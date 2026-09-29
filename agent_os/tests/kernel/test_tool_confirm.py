"""WS2 tool-confirm 两阶段闸门锚点测试(docs/DESIGN.md §8.2;docs/SUPERVISOR.md §10)。

固定约定:

- 触发条件:工具 ``spec.confirm is True``,或(HumanApproval 策略在场且 EXEC 档);
  闸门在 ``pre:tool.call`` 仲裁(含 Modify 改参)之后、分发之前——人审的就是要执行的;
- 确认走 supervisor 闭环:``Question.kind == "tool-confirm"``(不加新信号名),
  options 按 ``derive_side_effect(spec)``:reversible →
  ``["approve-once", "approve-run", "deny"]``;irreversible/none →
  ``["approve-once", "deny"]``(L3 永不批量授权);
- approve-once → 放行本次(不登记 Grant,重试必再撞闸);approve-run → 登记 run 档
  Grant(复用升权台账,粒度按工具名,checkpoint 持久)后放行,第二次同工具调用直通;
- deny → PERMISSION_DENIED 错误观察(与白名单拒绝同形),工具不执行;
- 无 supervisor 通道 → fail-closed PERMISSION_DENIED(高危操作无人可审=无人把关);
- pending 以 ``frame.context.working["_pending_tool_confirm"]`` 入 checkpoint,
  resume 重走闸门(不落 interrupted 占位);
- 编排 syscall 路径(ctx.call_tool)与同一条 ``_dispatch_call`` 闸门,同样过闸。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    ORCHESTRATE_TOOL,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    ToolCall,
    ToolPolicy,
)
from agent_os.kernel.errors import RunAborted
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.sidecars import HumanApproval
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

SKILLS_YAML = """
skills:
  - name: root_user
    version: 1.0.0
    kind: prompt
    description: 根调用方。Use when 测试 tool-confirm 闸门;Do not use when 其他。
    inputs:
      type: object
      properties: { task: { type: string } }
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions:
      tools: [confirm_tool, danger_tool, exec_tool, plain_tool, data_confirm_tool]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      你是根调用方,按需调用工具并汇报结果。
"""

ORCH_YAML = """
skills:
  - name: orch_driver
    version: 1.0.0
    kind: prompt
    description: 编排驱动。Use when 测试编排 syscall 过闸;Do not use when 其他。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { ok: { type: boolean } }
      required: [ok]
    permissions:
      tools: [python_orchestrate, confirm_tool]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6, max_tool_calls: 10 }
    prompt: |
      你是编排驱动,先跑编排脚本再原样汇报。
"""


def _yaml(tmp_path: Path, text: str = SKILLS_YAML) -> str:
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(p)


def _tools() -> tuple[LocalPythonToolRegistry, list[str]]:
    """注册测试工具;``executed`` 记录真执行了的调用(断言"未执行"用)。"""
    tools = LocalPythonToolRegistry()
    executed: list[str] = []

    @tools.tool(name="confirm_tool", permission=Permission.WRITE, confirm=True)
    def confirm_tool(path: str) -> str:
        """可逆写入工具。Use when 测试 confirm 闸门;Do not use when 其他。"""
        executed.append(f"confirm:{path}")
        return f"written:{path}"

    @tools.tool(
        name="danger_tool",
        permission=Permission.WRITE,
        confirm=True,
        side_effect="irreversible",
    )
    def danger_tool(path: str) -> str:
        """不可逆删除工具。Use when 测试 irreversible 档 options;Do not use when 其他。"""
        executed.append(f"danger:{path}")
        return f"deleted:{path}"

    @tools.tool(name="exec_tool", permission=Permission.EXEC)
    def exec_tool(cmd: str) -> str:
        """EXEC 档工具(confirm 未声明)。Use when 测试 HumanApproval 策略;Do not use when 其他。"""
        executed.append(f"exec:{cmd}")
        return f"ran:{cmd}"

    @tools.tool(name="plain_tool", permission=Permission.WRITE)
    def plain_tool(path: str) -> str:
        """普通写入工具(confirm 未声明)。Use when 回归锚:不挂起;Do not use when 其他。"""
        executed.append(f"plain:{path}")
        return f"plain:{path}"

    @tools.tool(
        name="data_confirm_tool",
        permission=Permission.WRITE,
        confirm=True,
        data_domains=["db.analytics", "fs.*"],
    )
    def data_confirm_tool(path: str) -> str:
        """带数据域声明的 confirm 工具。Use when 测试确认卡片数据域面;Do not use when 其他。"""
        executed.append(f"dataconfirm:{path}")
        return f"dataconfirm:{path}"

    return tools, executed


def _brain(call_spec: dict) -> callable:
    """首轮发指定工具调用;看到 tool 结果后汇报 decision(ok 或 error.kind)与 value。"""

    def brain(req: ChatRequest) -> ChatResponse:
        tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
        if not tool_msgs:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[
                        ToolCall(id="c1", name=call_spec["name"], args=call_spec["args"])
                    ],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        result = json.loads(tool_msgs[-1].content)
        decision = "ok" if result["ok"] else result["error"]["kind"]
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=json.dumps({"decision": decision, "value": result.get("value")}),
            ),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    return brain


def _build(tmp_path, handler, call_spec, *, brain=None, sidecars=(), orchestrate=False):
    """标准装配:MockProvider + 测试工具表 + supervisor 通道(handler 为 None 时不装)。"""
    config = RunConfig(
        model="mock/x",
        orchestrate=orchestrate,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    tools, executed = _tools()
    builder = (
        KernelBuilder(config)
        .providers(MockProvider(brain or _brain(call_spec)))
        .tools(tools)
        .skills(LocalFileSkillRegistry(_yaml(tmp_path, ORCH_YAML if orchestrate else SKILLS_YAML)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if handler is not None:
        builder = builder.supervisor(handler, timeout_s=5.0)
    if sidecars:
        builder = builder.sidecars(*sidecars)
    return builder.build(), executed


def _double_call_brain(call_spec: dict) -> callable:
    """连调两次同样的工具调用(各自等结果),然后汇报 done——重试/approve-run 用例用。"""

    def brain(req: ChatRequest) -> ChatResponse:
        calls = [
            tc for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
        ]
        if len(calls) < 2:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[
                        ToolCall(
                            id=f"c{len(calls)}",
                            name=call_spec["name"],
                            args=call_spec["args"],
                        )
                    ],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"decision": "done"})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    return brain


# ---------------------------------------------------------------------------
# 挂起 → approve-once / deny / 重试(§8.2)
# ---------------------------------------------------------------------------


def test_confirm_tool_approve_once_runs(tmp_path):
    """confirm 工具调用挂起 → approve-once → 放行执行,工具体真跑了。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, executed = _build(
        tmp_path, handler, {"name": "confirm_tool", "args": {"path": "a.txt"}}
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))

    assert result["decision"] == "ok"
    assert result["value"] == "written:a.txt"
    assert executed == ["confirm:a.txt"]
    assert len(asked) == 1, "确认必须恰好一次"
    q = asked[0]
    assert q.kind == "tool-confirm"
    assert q.options == ["approve-once", "approve-run", "deny"]  # WRITE → reversible 档
    assert q.urgency == "normal"
    assert q.context["tool"] == "confirm_tool"
    assert q.context["args"] == {"path": "a.txt"}
    assert q.context["side_effect"] == "reversible"


def test_deny_returns_permission_denied(tmp_path):
    """deny → PERMISSION_DENIED 错误观察(与白名单拒绝同形);工具体从未执行。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "deny", "decided_by": "user:test"}

    kernel, executed = _build(
        tmp_path, handler, {"name": "confirm_tool", "args": {"path": "a.txt"}}
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))

    assert result["decision"] == "permission_denied"
    assert executed == [], "被拒绝的确认调用不得执行工具体"
    assert len(asked) == 1


def test_retry_after_deny_suspends_again(tmp_path):
    """拒绝后模型重试同一调用 → 再次挂起(approve 不缓存,防"磨到批准")。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "deny", "decided_by": "user:test"}

    call_spec = {"name": "confirm_tool", "args": {"path": "a.txt"}}
    kernel, executed = _build(
        tmp_path, handler, call_spec, brain=_double_call_brain(call_spec)
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))

    assert result["decision"] == "done"
    assert len(asked) == 2, "同一调用重试必须再次挂起确认"
    assert executed == []


def test_approve_run_skips_second_ask(tmp_path):
    """approve-run → 登记 run 档 Grant;第二次同工具调用不再挂起(Grant 命中直通)。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-run", "decided_by": "user:test"}

    call_spec = {"name": "confirm_tool", "args": {"path": "a.txt"}}
    kernel, executed = _build(
        tmp_path, handler, call_spec, brain=_double_call_brain(call_spec)
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))

    assert result["decision"] == "done"
    assert len(asked) == 1, "approve-run 后同工具第二次调用不得再问"
    assert executed == ["confirm:a.txt", "confirm:a.txt"]
    run = next(iter(kernel._runs.values()))
    grants = [g for g in run.grants if g.skill == "confirm_tool" and g.scope == "run"]
    assert len(grants) == 1, "approve-run 必须登记 run 档 Grant(粒度按工具名)"
    assert grants[0].tier == "reversible"


def test_irreversible_options_exclude_approve_run(tmp_path):
    """irreversible 档(confirm + side_effect=irreversible)→ options 恰为
    ["approve-once", "deny"](L3 永不批量授权)。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, executed = _build(
        tmp_path, handler, {"name": "danger_tool", "args": {"path": "b.txt"}}
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))

    assert result["decision"] == "ok"
    assert executed == ["danger:b.txt"]
    assert len(asked) == 1
    assert asked[0].options == ["approve-once", "deny"]
    assert asked[0].urgency == "high"  # 不可逆操作收件箱优先呈现(同升权先例)
    assert asked[0].context["side_effect"] == "irreversible"


def test_confirm_without_supervisor_denied(tmp_path):
    """无 supervisor 通道 → fail-closed PERMISSION_DENIED(无人可审 = 无人把关)。"""
    kernel, executed = _build(
        tmp_path, None, {"name": "confirm_tool", "args": {"path": "a.txt"}}
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))
    assert result["decision"] == "permission_denied"
    assert executed == []


def test_plain_tool_not_gated(tmp_path):
    """回归锚:confirm 未声明的普通工具不挂起(无策略在场时 EXEC 工具也不过闸)。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "deny"}

    kernel, executed = _build(
        tmp_path, handler, {"name": "plain_tool", "args": {"path": "a.txt"}}
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))
    assert result["decision"] == "ok"
    assert executed == ["plain:a.txt"]
    assert not asked

    kernel2, executed2 = _build(
        tmp_path, handler, {"name": "exec_tool", "args": {"cmd": "ls"}}
    )
    result2 = asyncio.run(kernel2.run("root_user", {"task": "t"}))
    assert result2["decision"] == "ok"
    assert executed2 == ["exec:ls"]
    assert not asked, "无 HumanApproval 策略时 EXEC 工具不得过闸"


# ---------------------------------------------------------------------------
# checkpoint/resume:挂起中序列化恢复后重问(§10.2,同升权 §3 原则 3)
# ---------------------------------------------------------------------------


def test_checkpoint_resume_pending_tool_confirm(tmp_path):
    """挂起确认期间断电 → checkpoint 含 _pending_tool_confirm → resume 重问 → 批准后完成。"""
    calls = {"n": 0}

    async def crashing_handler(question):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RunAborted("模拟断电")
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel1, _ = _build(
        tmp_path, crashing_handler, {"name": "confirm_tool", "args": {"path": "a.txt"}}
    )
    seen = []

    async def rec(sig):
        seen.append(sig)

    kernel1.signals.subscribe("run.started", rec)

    async def first_run():
        with pytest.raises(RunAborted):
            await kernel1.run("root_user", {"task": "t"})

    asyncio.run(first_run())
    run_id = seen[0].run_id

    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run_id, str(ckpt))
    data = json.loads(ckpt.read_text(encoding="utf-8"))
    pending = [
        f["context"].get("working", {}).get("_pending_tool_confirm") for f in data["frames"]
    ]
    pending = [p for p in pending if p]
    assert pending, "checkpoint 必须含 _pending_tool_confirm"
    assert pending[0]["tool"] == "confirm_tool"
    assert pending[0]["args"] == {"path": "a.txt"}
    assert pending[0]["side_effect"] == "reversible"
    assert pending[0]["options"] == ["approve-once", "approve-run", "deny"]

    kernel2, executed2 = _build(
        tmp_path, crashing_handler, {"name": "confirm_tool", "args": {"path": "a.txt"}}
    )
    result = asyncio.run(kernel2.resume(str(ckpt)))
    assert result["decision"] == "ok"
    assert result["value"] == "written:a.txt"
    assert calls["n"] == 2, "resume 必须重新向调用方发起工具确认"
    assert executed2 == ["confirm:a.txt"], "批准后工具在恢复的内核里真执行"


# ---------------------------------------------------------------------------
# 编排 syscall 路径(docs/CODE-ORCHESTRATION.md §2.3:同一条 _dispatch_call 闸门)
# ---------------------------------------------------------------------------


def test_orchestration_syscall_gated(tmp_path):
    """python_orchestrate 脚本内 ctx.call_tool 调 confirm 工具 → 同样挂起过闸。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    script = (
        'r = ctx.call_tool("confirm_tool", {"path": "orch.txt"})\n'
        "result = r"
    )

    def brain(req: ChatRequest) -> ChatResponse:
        tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
        if not tool_msgs:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name=ORCHESTRATE_TOOL,
                            args={"code": script, "timeout": 30},
                        )
                    ],
                ),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        # 编排载荷原样作最终答案(outputs 只需 ok 布尔)
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=tool_msgs[-1].content),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )

    kernel, executed = _build(
        tmp_path, handler, {}, brain=brain, orchestrate=True
    )
    result = asyncio.run(kernel.run("orch_driver", {}))

    assert result["ok"] is True
    out = result["value"]  # 编排载荷:{"result": 脚本 result, "calls": N, "failed": [...]}
    assert out["result"]["ok"] is True, "脚本内 syscall 应拿到 ok 载荷"
    assert out["result"]["value"] == "written:orch.txt"
    assert out["calls"] == 1 and out["failed"] == []
    assert executed == ["confirm:orch.txt"]
    assert len(asked) == 1, "编排 syscall 路径同样过 tool-confirm 闸"
    assert asked[0].kind == "tool-confirm"
    assert asked[0].context["tool"] == "confirm_tool"


# ---------------------------------------------------------------------------
# HumanApproval 策略(WS2 下沉):EXEC 档工具也过闸
# ---------------------------------------------------------------------------


def test_human_approval_policy_gates_exec_tool(tmp_path):
    """HumanApproval 策略在场 + EXEC 档工具(confirm 未声明)→ 挂起走 tool-confirm。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, executed = _build(
        tmp_path,
        handler,
        {"name": "exec_tool", "args": {"cmd": "ls"}},
        sidecars=(HumanApproval(),),
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))

    assert result["decision"] == "ok"
    assert executed == ["exec:ls"]
    assert kernel.human_approval is not None, "策略载体必须传给 Kernel"
    assert len(asked) == 1
    q = asked[0]
    assert q.kind == "tool-confirm"
    # EXEC 缺省推导 irreversible → 两选项(永不批量授权),高 urgency
    assert q.options == ["approve-once", "deny"]
    assert q.urgency == "high"
    assert q.context["tool"] == "exec_tool"


def test_human_approval_policy_ignores_plain_write_tool(tmp_path):
    """策略在场时,非 EXEC 且未声明 confirm 的工具不过闸(策略不扩大打击面)。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "deny"}

    kernel, executed = _build(
        tmp_path,
        handler,
        {"name": "plain_tool", "args": {"path": "a.txt"}},
        sidecars=(HumanApproval(),),
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))
    assert result["decision"] == "ok"
    assert executed == ["plain:a.txt"]
    assert not asked


# ---------------------------------------------------------------------------
# D4:tool-confirm 请求数据域面(context.domains = spec.data_domains 原样直通;
# context.sensitive = 其中 [data] policy 判 confidential 的子集)
# ---------------------------------------------------------------------------


def test_confirm_context_carries_data_domains(tmp_path):
    """confirm 工具声明 data_domains → 确认请求 context 原样携带;
    未 bind [data] policy → sensitive 恒空。无声明的工具(confirm_tool)→ 空表。"""
    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, executed = _build(
        tmp_path, handler, {"name": "data_confirm_tool", "args": {"path": "a.txt"}}
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))

    assert result["decision"] == "ok"
    assert executed == ["dataconfirm:a.txt"]
    q = asked[0]
    assert q.kind == "tool-confirm"
    assert q.context["domains"] == ["db.analytics", "fs.*"]
    assert q.context["sensitive"] == [], "policy 未配置 → 敏感子集为空(D1 语义)"

    # 对照:无 data_domains 声明的 confirm 工具 → 空表(卡片不渲染该区)
    asked.clear()
    kernel2, _ = _build(
        tmp_path, handler, {"name": "confirm_tool", "args": {"path": "b.txt"}}
    )
    asyncio.run(kernel2.run("root_user", {"task": "t"}))
    assert asked[0].context["domains"] == []
    assert asked[0].context["sensitive"] == []


def test_confirm_context_sensitive_subset_with_data_policy(tmp_path):
    """[data] policy 在场:sensitive = domains 中判 confidential 的子集
    (命中 confidential 已注册域的模式入选;只命中 public 域的不入选)。"""
    from agent_os.api.v1 import CONFIDENTIAL, PUBLIC, DataDomain, DataPolicy

    asked = []

    async def handler(question):
        asked.append(question)
        return {"answer": "approve-once", "decided_by": "user:test"}

    kernel, _ = _build(
        tmp_path, handler, {"name": "data_confirm_tool", "args": {"path": "a.txt"}}
    )
    kernel.tools.bind_data_policy(
        DataPolicy(
            domains={
                "db.analytics": DataDomain(name="db.analytics", sensitivity=CONFIDENTIAL),
                "fs.shared": DataDomain(name="fs.shared", sensitivity=PUBLIC),
            }
        )
    )
    result = asyncio.run(kernel.run("root_user", {"task": "t"}))

    assert result["decision"] == "ok"
    q = asked[0]
    assert q.context["domains"] == ["db.analytics", "fs.*"]
    assert q.context["sensitive"] == ["db.analytics"]
