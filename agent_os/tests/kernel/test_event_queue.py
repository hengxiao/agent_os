"""事件批处理排干锚点测试(E4 增量;[events] 段 + ``Kernel._drain_event_queue``)。

固定约定:

- 在跑 run 的事件先入(根)帧工作内存队列 ``working["_event_queue"]``(条目
  ``{type, text, at}``,JSON 安全;host/web ``inject_event`` 是生产者,这里用
  probe sidecar 直接写——同 test_run_control.py 的 _Probe 模式);
- runner 在 **build 前** 排干(FORCE_COMPRESS 消费/maintain 同区):整个队列
  并入 **一条** 批头消息 ``Message(role=USER, source=INJECTED,
  content="[event 批处理 N 条]\\n1. ...", meta={"kind": "event-batch", "count": N})``
  ——步内并入会插在 assistant tool_calls 与 TOOL 结果之间,破 §7.4 不变量 2;
- 批上限 ``Kernel(events_batch_max=...)``(KernelBuilder 从 [events] batch_max
  透传):超出丢最旧,``meta.dropped`` 记丢弃数;无队列零操作;
- 队列随 checkpoint 落盘(working 序列化),resume 后第一次 build 前自然
  排干,零钩子。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

import pytest

from agent_os.api.v1 import (
    Allow,
    Mode,
    Permission,
    Role,
    RunConfig,
    Signal,
    Source,
    ToolPolicy,
)
from agent_os.kernel.control import EVENT_QUEUE_KEY
from agent_os.kernel.errors import RunPaused
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import EventsSection, KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import FIB_SKILLS_YAML, sandbox_tools


class _Probe:
    """通用探针 sidecar:在指定信号上执行动作(经 ctl,即 RunControl;照 test_run_control 先例)。"""

    name: ClassVar[str] = "probe"
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self, signal: str, action, *, once: bool = True) -> None:
        self.subscriptions = [signal]
        self._action = action
        self._once = once
        self.fired = 0
        self.notes: list[Any] = []

    async def on_signal(self, sig: Signal, ctl) -> Allow:
        if not self._once or self.fired == 0:
            self.fired += 1
            await self._action(sig, ctl, self.notes)
        return Allow()


def _kernel_with(probe: _Probe | None = None, *, batch_max: int = 50):
    """带探针 sidecar 的 fib 内核;events_section 透传批上限(照 test_run_control 先例)。

    返回 (kernel, MockProvider);``probe=None`` 时不挂 sidecar(无 ctl,零操作锚用)。
    """
    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    provider = MockProvider(fib_brain)
    builder = (
        KernelBuilder(config)
        .providers(provider)
        .tools(sandbox_tools())
        .skills(LocalFileSkillRegistry(str(FIB_SKILLS_YAML)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .events_section(EventsSection(batch_max=batch_max))
    )
    if probe is not None:
        builder = builder.sidecars(probe)
    return builder.build(), provider


def _queue_two(frame) -> None:
    """向帧工作内存队列写两条事件(模拟 host/web inject_event 的入队形态)。"""
    frame.context.working.setdefault(EVENT_QUEUE_KEY, []).append(
        {"type": "user.ping", "text": "[event:user.ping] 在吗", "at": 1.0}
    )
    frame.context.working[EVENT_QUEUE_KEY].append(
        {"type": "deploy.finished", "text": "[event:deploy.finished] v2 上线", "at": 2.0}
    )


def _batch_messages(mock) -> list:
    """mock.recorded 里全部 event-batch 批头消息(排干的观测面)。"""
    return [
        m
        for req in mock.recorded
        for m in req.messages
        if m.meta.get("kind") == "event-batch"
    ]


def _assert_pairing(messages: list) -> None:
    """§7.4 不变量 2:assistant tool_calls 之后紧邻对应 TOOL 结果(批头不得插入其间)。"""
    for i, m in enumerate(messages):
        if m.role is Role.ASSISTANT and m.tool_calls:
            follow = messages[i + 1 : i + 1 + len(m.tool_calls)]
            assert all(t.role is Role.TOOL for t in follow), f"配对被打断: {messages}"
            assert {t.tool_call_id for t in follow} == {tc.id for tc in m.tool_calls}


# ---------------------------------------------------------------------------
# 排干形态:单条批头 + 配对不破
# ---------------------------------------------------------------------------


def test_event_queue_drained_into_single_batch_header():
    """post:step 写根帧队列两条 → 下一步 build 前排干:请求 messages 含 **一条**
    批头("[event 批处理 2 条]" + 两条编号文本,meta.count==2);批头只在 build 前
    并入,assistant tool_calls 与 TOOL 结果的配对不被打断(§7.4 不变量 2)。"""

    async def act(sig, ctl, notes):
        if sig.payload.get("depth") == 1:  # 只写根帧(事件队列约定落在根帧)
            _queue_two(kernel.stack.get(sig.frame_id))

    probe = _Probe("post:step", act)
    kernel, mock = _kernel_with(probe)
    result = asyncio.run(kernel.run("demo.fib", {"n": 3}))
    assert result == {"seq": [0, 1, 1]}

    # 全部请求都过一遍配对不变量(排干发生在根帧 step2 build 前)
    for req in mock.recorded:
        _assert_pairing(req.messages)
    batch_reqs = [
        req for req in mock.recorded if any(m.meta.get("kind") == "event-batch" for m in req.messages)
    ]
    assert batch_reqs, "批头消息未进入任何请求"
    batch_msgs = [m for m in batch_reqs[0].messages if m.meta.get("kind") == "event-batch"]
    assert len(batch_msgs) == 1, "整个队列只许并入一条批头"
    (batch,) = batch_msgs
    assert batch.role is Role.USER
    assert batch.source is Source.INJECTED
    assert batch.meta == {"kind": "event-batch", "count": 2}
    assert batch.content.startswith("[event 批处理 2 条]\n")
    assert "1. [event:user.ping] 在吗" in batch.content
    assert "2. [event:deploy.finished] v2 上线" in batch.content
    # 批头并入点在 build 前:排在上一帧 assistant tool_calls 的 TOOL 结果之后
    # (build 期 ContextManager 还会追加 status 消息,故批头未必是请求最末条)
    msgs = batch_reqs[0].messages
    idx_batch = msgs.index(batch)
    idx_last_tool = max(i for i, m in enumerate(msgs) if m.role is Role.TOOL)
    assert idx_last_tool < idx_batch, "批头应排在既有轨迹 tool 结果之后(build 前并入点)"


def test_event_queue_drained_leaves_working_empty():
    """排干 pop 整个队列键:排干后的 post:step 观察 working 已无 ``_event_queue``。"""

    async def act(sig, ctl, notes):
        if sig.payload.get("depth") != 1:
            return
        frame = kernel.stack.get(sig.frame_id)
        if not notes:
            # 根帧首个 post:step:写两条;其后的 post:step 观察队列键
            _queue_two(frame)
            notes.append("queued")
        else:
            notes.append(frame.context.working.get(EVENT_QUEUE_KEY))

    probe = _Probe("post:step", act, once=False)
    kernel, _ = _kernel_with(probe)
    result = asyncio.run(kernel.run("demo.fib", {"n": 3}))
    assert result == {"seq": [0, 1, 1]}
    assert probe.notes == ["queued", None], "排干后 working 不得残留队列键"


# ---------------------------------------------------------------------------
# 批上限:丢最旧 + dropped 计数
# ---------------------------------------------------------------------------


def test_event_queue_batch_max_drops_oldest():
    """batch_max=1:排干只留最新一条,最旧的被丢弃,meta.dropped==1。"""

    async def act(sig, ctl, notes):
        if sig.payload.get("depth") != 1:
            return
        frame = kernel.stack.get(sig.frame_id)
        frame.context.working.setdefault(EVENT_QUEUE_KEY, []).append(
            {"type": "old", "text": "[event:old] 最旧", "at": 1.0}
        )
        frame.context.working[EVENT_QUEUE_KEY].append(
            {"type": "new", "text": "[event:new] 最新", "at": 2.0}
        )

    probe = _Probe("post:step", act)
    kernel, mock = _kernel_with(probe, batch_max=1)
    result = asyncio.run(kernel.run("demo.fib", {"n": 3}))
    assert result == {"seq": [0, 1, 1]}

    batch = next(iter(_batch_messages(mock)), None)
    assert batch is not None, "批头消息未进入任何请求"
    assert batch.meta == {"kind": "event-batch", "count": 1, "dropped": 1}
    assert batch.content.startswith("[event 批处理 1 条]\n")
    assert "1. [event:new] 最新" in batch.content
    assert "[event:old] 最旧" not in batch.content


def test_no_event_queue_is_noop():
    """无队列:排干零操作(显式锚;现全量回归隐式覆盖)——无任何 event-batch 消息。"""
    kernel, mock = _kernel_with()
    result = asyncio.run(kernel.run("demo.fib", {"n": 2}))
    assert result == {"seq": [0, 1]}
    assert _batch_messages(mock) == []


# ---------------------------------------------------------------------------
# checkpoint 往返:队列随档落盘,resume 自然排干
# ---------------------------------------------------------------------------


def test_event_queue_survives_checkpoint_resume(tmp_path):
    """未排干队列随 checkpoint 序列化:pause 在排干前生效(根帧 working 留队列)
    → checkpoint 含 ``_event_queue`` → 新内核 resume 首个请求即含批头(零钩子)。"""

    async def act(sig, ctl, notes):
        if sig.payload.get("depth") != 1:
            return
        _queue_two(kernel.stack.get(sig.frame_id))
        # 同点挂起:下一个 safe point(step2 pre:step)抛 RunPaused,先于排干点
        await ctl.pause(sig.run_id, "人工暂停排查")

    probe = _Probe("post:step", act)
    kernel, mock = _kernel_with(probe)
    with pytest.raises(RunPaused, match="^人工暂停排查$"):
        asyncio.run(kernel.run("demo.fib", {"n": 3}))
    # pause 先于排干:队列未被消费,任何请求都还没有批头
    assert _batch_messages(mock) == []

    run = next(iter(kernel._runs.values()))
    ckpt = tmp_path / "ckpt.json"
    kernel.checkpoint(run.run_id, str(ckpt))
    doc = json.loads(ckpt.read_text(encoding="utf-8"))
    root_doc = min(doc["frames"], key=lambda f: f.get("depth", 0))
    assert [item["text"] for item in root_doc["context"]["working"][EVENT_QUEUE_KEY]] == [
        "[event:user.ping] 在吗",
        "[event:deploy.finished] v2 上线",
    ], "未排干队列应随 checkpoint 落盘(working 序列化)"

    # 新内核 resume(无探针;照 test_pause_resume 的 pause→checkpoint→新内核模板)
    kernel2, mock2 = _kernel_with()
    result = asyncio.run(kernel2.resume(str(ckpt)))
    assert result == {"seq": [0, 1, 1]}
    first = mock2.recorded[0]
    _assert_pairing(first.messages)
    batch = next(m for m in first.messages if m.meta.get("kind") == "event-batch")
    assert batch.meta == {"kind": "event-batch", "count": 2}
    assert "1. [event:user.ping] 在吗" in batch.content
    assert "2. [event:deploy.finished] v2 上线" in batch.content
    # 恰好排干一次:后续请求里的批头都是同一条消息(轨迹累积,不重排)
    assert len({id(m) for m in _batch_messages(mock2)}) == 1
