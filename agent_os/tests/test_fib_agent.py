"""递归菲波拉契 example case —— 内核纵向切片的测试锚点(DESIGN.md §3.1/§3.3/§9)。

技能语义(见 ``skills/skills.yaml`` 的 ``fib`` 技能):

- base case:n == 1 → ``[0]``;n == 2 → ``[0, 1]``;
- 否则先 invoke 自己(伪工具 ``skill__fib``,n-1)拿到前 n-1 个数,
  再调 ``python_exec``(Logic Kernel 沙箱)计算最后两数之和,追加到数列末尾。

本文件同时固定以下运行时约定(纵向切片实现必须满足):

- 帧输入以首条 USER 消息进入帧上下文,``content == json.dumps(input)``;
- 子技能伪工具名 = ``skill__<name>``,parameters 即该技能 ``inputs``;
- 工具结果消息 ``role == TOOL``、``tool_call_id`` 与调用配对,
  ``content == json.dumps({"ok": ..., "value": ..., "error": ...})``;
- ``python_exec`` 的结果 ``value`` 形状 = ``{"stdout": str, "stderr": str, "result": Any}``,
  其中 ``result`` 为 stdout 最后一个非空行的 JSON 解析(不可解析则为 None);
- assistant 最终答案 ``content == json.dumps(outputs 对象)``,无 ``tool_calls``;
- 深度超限(``RunConfig.max_depth``)与输出校验连败属于硬失败,向调用方抛异常。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_os.api.v1 import (
    POST_FRAME_POP,
    POST_FRAME_PUSH,
    POST_LLM_RESPONSE,
    POST_LOGIC_EXEC,
    POST_TOOL_CALL,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    Signal,
    ToolCall,
    ToolPolicy,
)
from agent_os.kernel.errors import MaxDepthExceeded, OutputValidationError
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry

SKILLS_YAML = "skills/skills.yaml"


# ---------------------------------------------------------------------------
# Mock "fib 大脑":严格按技能指令行事的确定性 LLM
# ---------------------------------------------------------------------------


def fib_brain(req: ChatRequest) -> ChatResponse:
    """模拟一个完美遵循 fib 技能指令的模型。

    决策顺序:无调用 → base case 或 invoke 自己;有子技能结果 → 调 python_exec;
    有 python_exec 结果 → 给出最终答案。
    """
    n = None
    for m in req.messages:
        if m.role is Role.USER:
            n = json.loads(m.content)["n"]
            break
    assert n is not None, "帧上下文缺少输入消息"

    call_names: dict[str, str] = {}
    results: dict[str, dict] = {}
    for m in req.messages:
        if m.role is Role.ASSISTANT:
            for tc in m.tool_calls:
                call_names[tc.id] = tc.name
        elif m.role is Role.TOOL and m.tool_call_id:
            results[m.tool_call_id] = json.loads(m.content)

    def final(seq: list[int]) -> ChatResponse:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"seq": seq})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    def calls(tc: ToolCall) -> ChatResponse:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, tool_calls=[tc]),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )

    fib_calls = [cid for cid, name in call_names.items() if name == "skill__fib"]
    if not fib_calls:
        if n <= 2:
            return final([0] if n == 1 else [0, 1])
        return calls(ToolCall(id=f"call-fib-{n}", name="skill__fib", args={"n": n - 1}))

    fib_result = results[fib_calls[-1]]
    assert fib_result["ok"], fib_result
    seq = fib_result["value"]["seq"]

    py_calls = [cid for cid, name in call_names.items() if name == "python_exec"]
    if not py_calls:
        code = f"result = {seq[-2]} + {seq[-1]}\nprint(result)"
        return calls(ToolCall(id=f"call-py-{n}", name="python_exec", args={"code": code}))

    total = results[py_calls[-1]]["value"]["result"]
    return final(seq + [total])


def bad_brain(req: ChatRequest) -> ChatResponse:
    """永远返回不合 outputs schema 的答案,用于输出校验连败测试。"""
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"wrong": 1})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def build_kernel(brain=fib_brain, *, max_depth: int = 8):
    config = RunConfig(
        model="mock/fib",
        max_depth=max_depth,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),  # python_exec 是 EXEC 级
        compression="off",
    )
    tools = LocalPythonToolRegistry()
    tools.register(python_exec_tool(PythonSandboxLogicKernel()))
    kernel = (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(tools)
        .skills(LocalFileSkillRegistry(SKILLS_YAML))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )
    return kernel


def record_all(kernel) -> list[Signal]:
    seen: list[Signal] = []

    async def rec(sig: Signal) -> None:
        seen.append(sig)

    kernel.signals.subscribe("*", rec)
    return seen


def run(kernel, skill: str, input: dict):
    return asyncio.run(kernel.run(skill, input))


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_fib_5_returns_full_sequence():
    """端到端:递归 4 帧 + 每帧沙箱加法,数列正确。"""
    kernel = build_kernel()
    result = run(kernel, "fib", {"n": 5})
    assert result == {"seq": [0, 1, 1, 2, 3]}


def test_recursion_frame_tree_and_accounting():
    """帧树形状:fib(5) 压 4 帧(5/4/3/2);python_exec 恰好 3 次;LLM 调用 10 次。"""
    kernel = build_kernel()
    seen = record_all(kernel)
    result = run(kernel, "fib", {"n": 5})
    assert result == {"seq": [0, 1, 1, 2, 3]}

    pushes = [s for s in seen if s.name == POST_FRAME_PUSH]
    pops = [s for s in seen if s.name == POST_FRAME_POP]
    assert len(pushes) == 4 and len(pops) == 4
    depths = sorted(s.payload["depth"] for s in pushes)
    assert depths == [1, 2, 3, 4]

    py_calls = [s for s in seen if s.name == POST_TOOL_CALL and s.payload.get("tool") == "python_exec"]
    assert len(py_calls) == 3
    logic_execs = [s for s in seen if s.name == POST_LOGIC_EXEC]
    assert len(logic_execs) == 3  # 沙箱真实执行(经 tool 的 bind 信号)

    llm_calls = [s for s in seen if s.name == POST_LLM_RESPONSE]
    assert len(llm_calls) == 10  # 3 个非 base 帧 × 3 次调用 + base 帧 × 1 次


def test_context_isolation_between_frames():
    """帧隔离:fib(5) 帧的上下文只含自己的轨迹;子帧结果折叠为单条工具结果。"""
    mock = MockProvider(fib_brain)
    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    tools = LocalPythonToolRegistry()
    tools.register(python_exec_tool(PythonSandboxLogicKernel()))
    kernel = (
        KernelBuilder(config)
        .providers(mock)
        .tools(tools)
        .skills(LocalFileSkillRegistry(SKILLS_YAML))
        .logic_kernels(InProcessLogicKernel())
        .build()
    )
    run(kernel, "fib", {"n": 5})

    def n_of(req: ChatRequest) -> int:
        return json.loads(req.messages[1].content)["n"]

    reqs_5 = [r for r in mock.recorded if n_of(r) == 5]
    assert len(reqs_5) == 3  # invoke 自己 → python_exec → 最终答案

    second = reqs_5[1]
    # 第二次请求时,帧(5)上下文只有:SYSTEM 指令 + USER 输入 + assistant 调用 + 一条工具结果
    assert [m.role for m in second.messages] == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.TOOL]
    child_result = json.loads(second.messages[-1].content)
    assert child_result["ok"] is True
    assert child_result["value"] == {"seq": [0, 1, 1, 2]}  # fib(4) 的返回值,整段子帧轨迹已折叠

    # 前缀稳定性(§7.4 不变量 5):同一帧的相邻请求,系统指令逐字节一致
    assert reqs_5[0].messages[0].content == second.messages[0].content


def test_depth_limit_aborts_run():
    """递归深度兜底:fib(4) 需要 3 层递归,max_depth=2 → 硬失败上抛。"""
    kernel = build_kernel(max_depth=2)
    with pytest.raises(MaxDepthExceeded):
        run(kernel, "fib", {"n": 4})


def test_output_validation_failure_aborts_run():
    """outputs schema 校验:连败 N 次(=2)后判帧失败,根帧上抛。"""
    kernel = build_kernel(brain=bad_brain)
    with pytest.raises(OutputValidationError):
        run(kernel, "fib", {"n": 1})
