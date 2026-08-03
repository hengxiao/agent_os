"""M4 锚点测试:Sidecar 子系统(docs/DESIGN.md §5;supervisor + RunControl + 内置 sidecar)。

固定约定:

- SidecarSupervisor:SYNC sidecar 按 priority 顺序在关键路径 await,超时(默认 2s)或异常
  → **fail-closed**(视为 Veto);ASYNC sidecar 以任务派发,异常只记日志,永不拖垮 run;
- 裁决(内核职责,§1 公理 3):首个 Stop/Veto 生效;``pre:step`` 的 Veto 视为中止 run;
- **Veto 理由回写**(§5.2):``pre:tool.call`` 被 Veto 时跳过分发,理由以
  ``{"ok": False, "error": {"kind": "vetoed", "message": reason, "retryable": False}}``
  写入帧上下文;``pre:frame.pop`` 被 Veto 时,把理由作为纠偏观察写入帧上下文并继续 loop;
- RunControl:stop(置中止标志,下一个 safe point 生效)/pause(v1 同 stop,理由带 "paused:")/
  inject_message(向指定帧上下文追加 USER 消息)/force_compress/get_frame_tree/get_usage;
- 信号载荷扩充:``post:llm.response`` 带 usage;``post:step`` 带本步调用签名列表
  ``calls: [{"name", "sig"}]``(sig = name+args 哈希);
- BudgetGuard(ASYNC, post:llm.response):累计成本超限 → ctl.stop;
- LoopDetector(ASYNC, post:step):同签名连续 threshold 次 → inject_message 纠偏(带操作指令),
  再犯 max_strikes 次 → ctl.stop;
- StallDetector(ASYNC, post:step):相邻 step 间隔超 max_idle(clock 可注入)→ 先纠偏再 stop;
- ToolGuard(SYNC, pre:tool.call):规则表(工具名 + 参数正则)命中 → Veto。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from typing import ClassVar

import pytest

from agent_os.api.v1 import (
    POST_TOOL_CALL,
    Allow,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Mode,
    Permission,
    Role,
    RunConfig,
    Signal,
    ToolCall,
    ToolPolicy,
    Veto,
)
from agent_os.kernel.errors import RunAborted
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.sidecars import BudgetGuard, LoopDetector, StallDetector, ToolGuard
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry

LOOPER_YAML = """
skills:
  - name: test.looper
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [system.python.exec, system.shell.exec], skills: [] }
    model: { prefer: ["mock/loop"] }
    limits: { max_steps: 50 }
    prompt: 测试用 test.looper 技能。
"""


def _yaml(tmp_path) -> str:
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(LOOPER_YAML), encoding="utf-8")
    return str(p)


def _tools() -> LocalPythonToolRegistry:
    """注册 test.looper 声明的全部工具(§6.1 装配期权限闸门要求声明即存在)。"""
    tools = LocalPythonToolRegistry.with_builtins()
    tools.register(python_exec_tool(PythonSandboxLogicKernel()))
    return tools


def _build(tmp_path, *sidecars, brain, cost: float = 0.0):
    config = RunConfig(
        model="mock/loop",
        max_cost=100.0,  # 不让内核自身记账抢先触发,把预算留给 BudgetGuard
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    builder = (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(_tools())
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if sidecars:
        builder = builder.sidecars(*sidecars)
    return builder.build()


def run(kernel):
    return asyncio.run(kernel.run("test.looper", {}))


# ---------------------------------------------------------------------------
# 测试用 brains
# ---------------------------------------------------------------------------


def loop_brain(cost: float = 0.0):
    """永远以相同参数重复调 system.python.exec,永不给出最终答案。"""

    def brain(req: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="system.python.exec", args={"code": "print(1)"})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1, cost=cost),
        )

    return brain


def shell_then_final_brain(req: ChatRequest) -> ChatResponse:
    """先尝试危险命令;看到结果(被 veto)后给出最终答案。"""
    called = any(m.role is Role.TOOL for m in req.messages)
    if not called:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="system.shell.exec", args={"command": "rm -rf /"})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def always_final_brain(req: ChatRequest) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


# ---------------------------------------------------------------------------
# 内置 sidecar
# ---------------------------------------------------------------------------


def test_budget_guard_stops_run(tmp_path):
    """BudgetGuard(ASYNC):累计成本超限 → ctl.stop,下一个 safe point 中止(§5.4)。"""
    kernel = _build(tmp_path, BudgetGuard(max_cost=0.05), brain=loop_brain(cost=0.02))
    with pytest.raises(RunAborted, match="BudgetGuard"):
        run(kernel)


def test_loop_detector_injects_then_stops(tmp_path):
    """LoopDetector:同签名 3 次 → 注入纠偏消息;再犯 2 次 → stop(§5.4)。"""
    kernel = _build(tmp_path, LoopDetector(threshold=3, max_strikes=2), brain=loop_brain())
    with pytest.raises(RunAborted, match="LoopDetector"):
        run(kernel)


def test_loop_detector_injection_reaches_context(tmp_path):
    """纠偏消息以 USER 消息进入帧上下文,且带操作指令(§7.3)。"""
    mock = MockProvider(loop_brain())
    config = RunConfig(
        model="mock/loop",
        max_cost=100.0,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = (
        KernelBuilder(config)
        .providers(mock)
        .tools(_tools())
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel())
        .sidecars(LoopDetector(threshold=3, max_strikes=2))
        .build()
    )
    with pytest.raises(RunAborted):
        run(kernel)
    injected = [
        m.content
        for req in mock.recorded
        for m in req.messages
        if m.role is Role.USER and "停止重试" in m.content
    ]
    assert injected, "纠偏消息未进入帧上下文"


def test_tool_guard_veto_reason_written_back(tmp_path):
    """ToolGuard veto:跳过分发,理由作为错误观察(kind=vetoed)回写帧上下文(§5.2)。"""
    guard = ToolGuard(rules=[("system.shell.exec", r"rm\s+-rf", "禁止危险命令 rm -rf")])
    kernel = _build(tmp_path, guard, brain=shell_then_final_brain)
    result = run(kernel)
    assert result == {"done": True}

    # 从 mock 录制里找 veto 证据(上下文里的工具结果:ok=False, kind=vetoed, message 含理由)
    mock = kernel.providers.providers["mock"]
    tool_msgs = [
        json.loads(m.content)
        for req in mock.recorded
        for m in req.messages
        if m.role is Role.TOOL
    ]
    assert tool_msgs, "应有一条 veto 结果消息"
    assert tool_msgs[0]["ok"] is False
    assert tool_msgs[0]["error"]["kind"] == "vetoed"
    assert "禁止危险命令" in tool_msgs[0]["error"]["message"]
    assert tool_msgs[0]["error"]["retryable"] is False


def test_tool_guard_veto_skips_dispatch(tmp_path):
    """被 veto 的调用不产生 post:tool.call 信号(未执行)。"""
    seen = []

    async def rec(sig: Signal) -> None:
        seen.append(sig)

    guard = ToolGuard(rules=[("system.shell.exec", r"rm\s+-rf", "禁止")])
    kernel = _build(tmp_path, guard, brain=shell_then_final_brain)
    kernel.signals.subscribe("*", rec)
    run(kernel)
    dispatched = [s for s in seen if s.name == POST_TOOL_CALL and s.payload.get("tool") == "system.shell.exec"]
    assert dispatched == []


# ---------------------------------------------------------------------------
# reviewer(pre:frame.pop)与 fail-closed
# ---------------------------------------------------------------------------


class _Reviewer:
    """proposer-reviewer:第一次弹栈打回,第二次放行(§3.1 pre:frame.pop)。"""

    name: ClassVar[str] = "reviewer"
    subscriptions: ClassVar[list[str]] = ["pre:frame.pop"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self) -> None:
        self.calls = 0

    async def on_signal(self, sig: Signal, ctl) -> Veto | Allow:
        self.calls += 1
        if self.calls == 1:
            return Veto("报告缺少引用,继续完善")
        return Allow()


def test_reviewer_rejects_first_pop(tmp_path):
    """pre:frame.pop 否决:理由写入帧上下文,loop 继续;第二次弹栈放行。"""
    reviewer = _Reviewer()
    kernel = _build(tmp_path, reviewer, brain=always_final_brain)
    result = run(kernel)
    assert result == {"done": True}
    assert reviewer.calls == 2

    mock = kernel.providers.providers["mock"]
    assert len(mock.recorded) == 2  # 第一次被打回后重新给答案
    second_req_msgs = mock.recorded[1].messages
    assert any("报告缺少引用" in m.content for m in second_req_msgs)


class _CrashySidecar:
    """在 pre:step 抛异常,验证 fail-closed(§5.3)。"""

    name: ClassVar[str] = "crashy"
    subscriptions: ClassVar[list[str]] = ["pre:step"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    async def on_signal(self, sig: Signal, ctl):
        raise RuntimeError("boom")


def test_sync_sidecar_fail_closed(tmp_path):
    """SYNC sidecar 异常 → fail-closed 视为 Veto → pre:step 否决即中止 run。"""
    kernel = _build(tmp_path, _CrashySidecar(), brain=always_final_brain)
    with pytest.raises(RunAborted, match="fail-closed"):
        run(kernel)


# ---------------------------------------------------------------------------
# StallDetector(fake clock)
# ---------------------------------------------------------------------------


def test_stall_detector_with_fake_clock(tmp_path):
    """StallDetector:相邻 step 间隔超 idle → 先纠偏,再犯 → stop(§5.4)。"""
    t = [0.0]

    def clock() -> float:
        return t[0]

    def brain(req: ChatRequest) -> ChatResponse:
        t[0] += 700.0  # 每次 LLM 调用推进时钟,模拟长期无进展
        return loop_brain()(req)

    kernel = _build(
        tmp_path,
        StallDetector(max_idle_seconds=600, clock=clock),
        brain=brain,
    )
    with pytest.raises(RunAborted, match="StallDetector"):
        run(kernel)
