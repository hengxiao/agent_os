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

mock "fib 大脑"本体在 ``tests.helpers.brains.fib_brain``(多处共用)。
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
    Permission,
    Role,
    RunConfig,
    ToolPolicy,
)
from agent_os.kernel.errors import MaxDepthExceeded, OutputValidationError
from agent_os.providers.mock import MockProvider
from tests.helpers.brains import bad_brain, fib_brain
from tests.helpers.kernels import FIB_SKILLS_YAML, assemble, fib_kernel, record_all


def run(kernel, skill: str, input: dict):
    return asyncio.run(kernel.run(skill, input))


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_fib_5_returns_full_sequence():
    """端到端:递归 4 帧 + 每帧沙箱加法,数列正确。"""
    kernel = fib_kernel()
    result = run(kernel, "fib", {"n": 5})
    assert result == {"seq": [0, 1, 1, 2, 3]}


def test_recursion_frame_tree_and_accounting():
    """帧树形状:fib(5) 压 4 帧(5/4/3/2);python_exec 恰好 3 次;LLM 调用 10 次。"""
    kernel = fib_kernel()
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
    kernel = assemble(config, mock, FIB_SKILLS_YAML)
    run(kernel, "fib", {"n": 5})

    def n_of(req: ChatRequest) -> int:
        return json.loads(req.messages[1].content)["n"]

    reqs_5 = [r for r in mock.recorded if n_of(r) == 5]
    assert len(reqs_5) == 3  # invoke 自己 → python_exec → 最终答案

    second = reqs_5[1]
    # 第二次请求时,帧(5)上下文只有:SYSTEM 指令 + USER 输入 + assistant 调用 + 一条工具结果
    # (M3 起尾部可能追加 status 元消息,meta["kind"]=="status",§7.3;不参与业务语义)
    roles = [m.role for m in second.messages]
    assert roles[:4] == [Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.TOOL]
    assert all(m.meta.get("kind") == "status" for m in second.messages[4:])
    tool_msgs = [m for m in second.messages if m.role is Role.TOOL]
    child_result = json.loads(tool_msgs[-1].content)
    assert child_result["ok"] is True
    assert child_result["value"] == {"seq": [0, 1, 1, 2]}  # fib(4) 的返回值,整段子帧轨迹已折叠

    # 前缀稳定性(§7.4 不变量 5):同一帧的相邻请求,系统指令逐字节一致
    assert reqs_5[0].messages[0].content == second.messages[0].content


def test_depth_limit_aborts_run():
    """递归深度兜底:fib(4) 需要 3 层递归,max_depth=2 → 硬失败上抛。"""
    kernel = fib_kernel(max_depth=2)
    with pytest.raises(MaxDepthExceeded):
        run(kernel, "fib", {"n": 4})


def test_output_validation_failure_aborts_run():
    """outputs schema 校验:连败 N 次(=2)后判帧失败,根帧上抛。"""
    kernel = fib_kernel(bad_brain)
    with pytest.raises(OutputValidationError):
        run(kernel, "fib", {"n": 1})
