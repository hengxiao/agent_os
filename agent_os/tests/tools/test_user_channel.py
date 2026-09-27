"""system.user.ask / system.user.notify 锚点测试(docs/DESIGN.md §8.3;M1 宿主回调形态)。

固定约定:

- 工具面常驻(``with_builtins`` 默认注册;新工具无旧名,不设别名);
- 宿主回调经 ``bind_user_channel`` 装配钩子注入(bind 模式,同 bind_memory 先例),
  channel 形态为带 ``ask(question)``/``notify(message)`` 方法的对象,同步/async 均可;
- 未 bind → NOT_FOUND 结构化错误(同 memory 工具"未装配"先例);
- 与 ask_supervisor 的分工:ask_supervisor = 内核通道(伪工具,pending 落盘,
  resume 重问);本工具 = 工具面宿主回调,无内核闸门/pending 语义。
"""

from __future__ import annotations

import asyncio

from agent_os.api.v1 import (
    Permission,
    RunConfig,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.runtime.builder import KernelBuilder
from agent_os.tools.local_registry import LocalPythonToolRegistry


def _ctx(allowed=("system.user.ask", "system.user.notify")):
    return ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1"),
        allowed_tools=list(allowed),
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )


class _AsyncChannel:
    """async 回调宿主通道(记录收到的问题/通知)。"""

    def __init__(self) -> None:
        self.questions: list[str] = []
        self.notifications: list[str] = []

    async def ask(self, question: str) -> str:
        self.questions.append(question)
        return "async 回答"

    async def notify(self, message: str) -> None:
        self.notifications.append(message)


class _SyncChannel:
    """同步回调宿主通道(宿主嵌入方不强制 async)。"""

    def __init__(self) -> None:
        self.questions: list[str] = []

    def ask(self, question: str) -> str:
        self.questions.append(question)
        return "sync 回答"

    def notify(self, message: str) -> None:
        pass


def test_user_tools_registered_in_builtins():
    """User Communication 工具在默认工具面里(§8.3),无旧名别名;WRITE 档——
    等用户输入的交互不可缓存/不可并行(READ⇒cacheable 红利不成立),且占帧白名单。"""
    reg = LocalPythonToolRegistry.with_builtins()
    assert reg.has("system.user.ask"), "system.user.ask 必须在默认工具面里"
    assert reg.has("system.user.notify"), "system.user.notify 必须在默认工具面里"
    assert not reg.has("ask_user") and not reg.has("notify_user"), "新工具不设旧名别名"
    for name in ("system.user.ask", "system.user.notify"):
        spec = reg.get(name).spec
        assert spec.permission is Permission.WRITE, f"{name}: 交互副作用应按 WRITE 档"
        assert not spec.cacheable and not spec.concurrent_safe


def test_unbound_channel_reports_not_found():
    """未 bind 宿主通道:两工具按"user 通道未装配"报 NOT_FOUND(行为与引入前一致)。"""
    reg = LocalPythonToolRegistry.with_builtins()

    async def main():
        ask = await reg.dispatch(
            ToolCall(id="1", name="system.user.ask", args={"question": "q?"}), _ctx()
        )
        notify = await reg.dispatch(
            ToolCall(id="2", name="system.user.notify", args={"message": "hi"}), _ctx()
        )
        return ask, notify

    ask, notify = asyncio.run(main())
    assert not ask.ok and ask.error.kind is ToolErrorKind.NOT_FOUND
    assert not notify.ok and notify.error.kind is ToolErrorKind.NOT_FOUND
    assert ask.error.hint, "错误须带可执行的下一步建议(§W0-3)"


def test_ask_user_invokes_bound_async_callback():
    """bind 后 ask 回调被调用(收到原样 question),回答文本作为工具值返回。"""
    reg = LocalPythonToolRegistry.with_builtins()
    channel = _AsyncChannel()
    reg.bind_user_channel(channel)

    async def main():
        return await reg.dispatch(
            ToolCall(id="1", name="system.user.ask", args={"question": "继续吗?"}), _ctx()
        )

    res = asyncio.run(main())
    assert res.ok, res.error
    assert res.value == "async 回答"
    assert channel.questions == ["继续吗?"], "宿主回调须收到原样问题"


def test_notify_user_one_way_bound_callback():
    """bind 后 notify 回调被调用(单向:不等回答,返回确认串)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    channel = _AsyncChannel()
    reg.bind_user_channel(channel)

    async def main():
        return await reg.dispatch(
            ToolCall(id="1", name="system.user.notify", args={"message": "已完成 3/5"}), _ctx()
        )

    res = asyncio.run(main())
    assert res.ok, res.error
    assert channel.notifications == ["已完成 3/5"]
    assert channel.questions == [], "notify 是单向语义,不得触发 ask 回调"


def test_sync_callbacks_supported():
    """同步宿主回调同样可用(同 registry 对 sync/async 工具函数的态度)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    channel = _SyncChannel()
    reg.bind_user_channel(channel)

    async def main():
        return await reg.dispatch(
            ToolCall(id="1", name="system.user.ask", args={"question": "q?"}), _ctx()
        )

    res = asyncio.run(main())
    assert res.ok and res.value == "sync 回答"
    assert channel.questions == ["q?"]


def test_kernel_builder_user_channel_wires_registry():
    """KernelBuilder.user_channel 装配链:build 时经 bind_user_channel 接线。"""
    reg = LocalPythonToolRegistry.with_builtins()
    channel = _AsyncChannel()
    KernelBuilder(RunConfig()).tools(reg).user_channel(channel).build()
    assert reg._user_channel is channel, "build 应把 user_channel 经 bind 注入 registry"

    async def main():
        return await reg.dispatch(
            ToolCall(id="1", name="system.user.ask", args={"question": "q?"}), _ctx()
        )

    res = asyncio.run(main())
    assert res.ok and res.value == "async 回答"
