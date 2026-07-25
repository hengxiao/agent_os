"""真实场景实例锚点测试:examples/support_desk(客服工单退款处理台)。

场景(数据 mock,flow 真实):
- mock 数据集(tickets/orders/users/policies)驱动分支:满意路径(退款)、
  政策拒绝、大额升级,三条路径的 skill 调用树不同;
- 场景工具(get_ticket/get_order/search_policy/issue_refund/send_notification 等)
  经配置 `[tools.custom] module = "support_tools:register"` 加载;
- mock 大脑 support_brain 是数据驱动的:分类看工单文本,审批看政策事实与金额,
  拒绝/升级走不同分支——同一套技能,不同输入不同 flow。
"""

from __future__ import annotations

import asyncio
import sys

from agent_os.api.v1 import POST_TOOL_CALL, Permission, RunConfig, Signal, ToolPolicy
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry
from tests.helpers.kernels import PROJECT_ROOT

DESK_DIR = PROJECT_ROOT / "examples" / "support_desk"


def _build(brain):
    config = RunConfig(
        model="mock/support",
        max_depth=10,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    tools = LocalPythonToolRegistry.with_builtins()
    sys.path.insert(0, str(DESK_DIR))
    try:
        import support_tools

        support_tools.register(tools)
    finally:
        sys.path.remove(str(DESK_DIR))
    return (
        KernelBuilder(config)
        .providers(MockProvider(brain))
        .tools(tools)
        .skills(LocalFileSkillRegistry(str(DESK_DIR / "skills.yaml")))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .build()
    )


def _load_brain():
    sys.path.insert(0, str(DESK_DIR))
    try:
        import brains

        return brains.support_brain
    finally:
        sys.path.remove(str(DESK_DIR))


def test_support_desk_loads_skills():
    reg = LocalFileSkillRegistry(str(DESK_DIR / "skills.yaml"))
    manifests = reg.manifests()
    assert len(manifests) == 17
    assert {m.name for m in manifests} >= {
        "handle_ticket", "classify_ticket", "gather_context", "assess_refund",
        "process_refund", "draft_response", "escalate_ticket",
    }


def test_happy_path_refund_issued():
    """T-1001:VIP 用户 40 天未收货(VIP 窗口 45 天)→ 退款 8900 分,通知送达。"""
    kernel = _build(_load_brain())
    seen: list[Signal] = []

    async def rec(sig: Signal) -> None:
        seen.append(sig)

    kernel.signals.subscribe("*", rec)
    result = asyncio.run(kernel.run("handle_ticket", {"ticket_id": "T-1001"}))

    assert result["status"] == "resolved_refunded"
    assert result["refund_cents"] == 8900
    tools_called = [s.payload["tool"] for s in seen if s.name == POST_TOOL_CALL]
    assert tools_called.count("issue_refund") == 1
    assert tools_called.count("send_notification") == 1
    frames = [s for s in seen if s.name == "post:frame.push"]
    assert len(frames) >= 8


def test_reject_path_policy_denies():
    """T-1002:普通用户 50 天前签收(窗口 30 天)→ 政策拒绝,不发退款。"""
    kernel = _build(_load_brain())
    seen: list[Signal] = []

    async def rec(sig: Signal) -> None:
        seen.append(sig)

    kernel.signals.subscribe("*", rec)
    result = asyncio.run(kernel.run("handle_ticket", {"ticket_id": "T-1002"}))

    assert result["status"] == "resolved_rejected"
    tools_called = [s.payload["tool"] for s in seen if s.name == POST_TOOL_CALL]
    assert "issue_refund" not in tools_called
    assert tools_called.count("send_notification") == 1


def test_escalate_path_high_amount():
    """T-1003:¥5000 破损(超 ¥1000 自动上限)→ 升级人工,不发退款。"""
    kernel = _build(_load_brain())
    seen: list[Signal] = []

    async def rec(sig: Signal) -> None:
        seen.append(sig)

    kernel.signals.subscribe("*", rec)
    result = asyncio.run(kernel.run("handle_ticket", {"ticket_id": "T-1003"}))

    assert result["status"] == "escalated"
    tools_called = [s.payload["tool"] for s in seen if s.name == POST_TOOL_CALL]
    assert "issue_refund" not in tools_called


def test_deterministic_across_runs():
    """数据驱动的 flow 是确定性的:同输入同输出(调试可复现)。"""
    r1 = asyncio.run(_build(_load_brain()).run("handle_ticket", {"ticket_id": "T-1001"}))
    r2 = asyncio.run(_build(_load_brain()).run("handle_ticket", {"ticket_id": "T-1001"}))
    assert r1 == r2
