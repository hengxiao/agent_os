"""RunControl 锚点测试(docs/DESIGN.md §5.2;sidecar 操控运行的唯一通道)。

固定约定:

- ``pause(run_id, reason)``:v1 语义同 stop,理由带 ``"paused: "`` 前缀,
  下一个 ``pre:step`` safe point 抛 ``RunAborted``;
- ``inject_message(frame_id, msg)``:向指定帧上下文追加消息;非 Message 入参
  包装为 ``role=USER, source=INJECTED``;帧不存在时丢弃并记日志,不崩 run;
- ``force_compress(frame_id)``:在帧工作内存置 ``_force_compress`` 标志
  (runner 在 maintain 前消费);帧不存在时丢弃,不崩 run;
- ``get_frame_tree(run_id)``:嵌套 dict(frame_id/skill/depth/status/children),
  只含该 run 的帧;``get_usage(run_id)``:内核记账快照。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from typing import Any, ClassVar

import pytest

from agent_os.api.v1 import (
    Allow,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    FrameStatus,
    Message,
    Mode,
    Role,
    Signal,
    Source,
    ToolCall,
)
from agent_os.kernel.control import FORCE_COMPRESS_KEY
from agent_os.kernel.errors import RunAborted, SubtreeCancelled
from agent_os.providers.mock import MockProvider
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import FIB_SKILLS_YAML, assemble


class _Probe:
    """通用探针 sidecar:在指定信号上执行一次动作(经 ctl,即 RunControl)。"""

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


def _kernel_with(probe: _Probe, brain=fib_brain):
    """带探针 sidecar 的 fib 内核(sidecars 非空 → builder 装配 RunControl)。"""
    from agent_os.api.v1 import Permission, RunConfig, ToolPolicy

    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    provider = brain if hasattr(brain, "chat") else MockProvider(brain)
    return assemble(config, provider, FIB_SKILLS_YAML, sidecars=(probe,)), provider


# ---------------------------------------------------------------------------
# pause
# ---------------------------------------------------------------------------


def test_pause_aborts_with_prefixed_reason():
    """pause 在下一个 safe point 中止 run,理由带 "paused: " 前缀(§5.2)。"""

    async def act(sig, ctl, notes):
        await ctl.pause(sig.run_id, "人工暂停排查")

    probe = _Probe("post:llm.response", act)
    kernel, _ = _kernel_with(probe)
    with pytest.raises(RunAborted, match="paused: 人工暂停排查"):
        asyncio.run(kernel.run("demo.fib", {"n": 3}))


# ---------------------------------------------------------------------------
# inject_message
# ---------------------------------------------------------------------------


def test_inject_message_wraps_and_reaches_context():
    """非 Message 入参包装为 USER/INJECTED,进入帧上下文并出现在后续 LLM 请求里。"""

    async def act(sig, ctl, notes):
        await ctl.inject_message(sig.frame_id, "注入的补充要求")

    probe = _Probe("post:llm.response", act)
    kernel, mock = _kernel_with(probe)
    result = asyncio.run(kernel.run("demo.fib", {"n": 3}))
    assert result == {"seq": [0, 1, 1]}

    injected = [
        m
        for req in mock.recorded
        for m in req.messages
        if m.content == "注入的补充要求"
    ]
    assert injected, "注入消息未进入后续请求"
    assert injected[0].role is Role.USER
    assert injected[0].source is Source.INJECTED


def test_inject_message_missing_frame_dropped():
    """帧不存在:消息丢弃并记日志,run 不受影响(§5.2 防御式)。"""

    async def act(sig, ctl, notes):
        await ctl.inject_message("no-such-frame", "应被丢弃")

    probe = _Probe("post:llm.response", act)
    kernel, mock = _kernel_with(probe)
    result = asyncio.run(kernel.run("demo.fib", {"n": 2}))
    assert result == {"seq": [0, 1]}
    assert not any(
        m.content == "应被丢弃" for req in mock.recorded for m in req.messages
    )


# ---------------------------------------------------------------------------
# force_compress
# ---------------------------------------------------------------------------


def test_force_compress_sets_frame_flag():
    """置帧工作内存标志,runner 在 maintain 前消费(§7.1 外部强制触发)。"""

    async def act(sig, ctl, notes):
        await ctl.force_compress(sig.frame_id)
        frame = kernel.stack.get(sig.frame_id)
        notes.append(frame.context.working.get(FORCE_COMPRESS_KEY))

    probe = _Probe("post:llm.response", act)
    kernel, _ = _kernel_with(probe)
    result = asyncio.run(kernel.run("demo.fib", {"n": 2}))
    assert result == {"seq": [0, 1]}
    assert probe.notes == [True], "标志未写入帧工作内存"


def test_force_compress_missing_frame_dropped():
    async def act(sig, ctl, notes):
        await ctl.force_compress("no-such-frame")

    probe = _Probe("post:llm.response", act)
    kernel, _ = _kernel_with(probe)
    result = asyncio.run(kernel.run("demo.fib", {"n": 2}))
    assert result == {"seq": [0, 1]}


# ---------------------------------------------------------------------------
# get_frame_tree / get_usage
# ---------------------------------------------------------------------------


def test_frame_tree_nested_shape():
    """fib(3) 递归压栈时,深度 2 帧入栈后 get_frame_tree 呈现父子嵌套。"""

    async def act(sig, ctl, notes):
        if sig.payload.get("depth") == 2:
            notes.append(await ctl.get_frame_tree(sig.run_id))

    probe = _Probe("post:frame.push", act, once=False)
    kernel, _ = _kernel_with(probe)
    asyncio.run(kernel.run("demo.fib", {"n": 3}))

    assert probe.notes, "未捕获深度 2 的帧树快照"
    tree = probe.notes[0]
    assert len(tree) == 1, "根节点应只有一个(fib 根帧)"
    root = tree[0]
    assert root["depth"] == 1 and "demo.fib" in root["skill"]
    assert root["status"] == "running"
    assert len(root["children"]) == 1
    child = root["children"][0]
    assert child["depth"] == 2 and child["children"] == []


def test_get_usage_snapshot():
    async def act(sig, ctl, notes):
        notes.append(await ctl.get_usage(sig.run_id))

    probe = _Probe("post:llm.response", act)
    kernel, _ = _kernel_with(probe)
    asyncio.run(kernel.run("demo.fib", {"n": 2}))
    assert probe.notes and probe.notes[0].steps >= 1


# ---------------------------------------------------------------------------
# cancel_frame(子树级联取消,WS1)
# ---------------------------------------------------------------------------

CHAIN_YAML = """
skills:
  - name: test.chain
    version: 1.0.0
    kind: prompt
    inputs:
      type: object
      properties: { k: { type: integer, minimum: 1 } }
      required: [k]
    outputs:
      type: object
      properties: { k: { type: integer } }
      required: [k]
    permissions: { tools: [], skills: [test.chain] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 10 }
    prompt: CHAIN
"""


def _chain_brain(req: ChatRequest) -> ChatResponse:
    """k>1 且尚无工具结果 → 调 skill.test.chain(k-1);否则给最终答案(容错错误观察)。"""
    k = None
    for m in req.messages:
        if m.role is Role.USER:
            k = json.loads(m.content)["k"]
            break
    tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
    if k and k > 1 and not tool_msgs:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(id=f"chain-{k}", name="skill.test.chain", args={"k": k - 1})
                ],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"k": k})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _chain_kernel(probe: _Probe, tmp_path):
    """三级递归链(k=3 → 2 → 1)的最小内核。"""
    from agent_os.api.v1 import Permission, RunConfig, ToolPolicy

    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(CHAIN_YAML), encoding="utf-8")
    return assemble(config, MockProvider(_chain_brain), p, sidecars=(probe,))


def test_cancel_frame_collects_subtree_and_run_survives(tmp_path):
    """cancel_frame 置帧级 stop 标志、DFS 收齐子树;子树终态(SubtreeCancelled)
    不杀 run——根帧拿到 interrupted 错误观察后正常跑到终态。"""
    box: dict[str, Any] = {}

    async def act(sig, ctl, notes):
        if sig.payload.get("depth") == 3:
            leaf_id = sig.payload["frame_id"]
            mid_id = box["kernel"].stack.get(leaf_id).parent_id
            notes.append((mid_id, leaf_id, await ctl.cancel_frame(mid_id, "剪枝测试")))

    probe = _Probe("post:frame.push", act, once=False)
    kernel = _chain_kernel(probe, tmp_path)
    box["kernel"] = kernel
    result = asyncio.run(kernel.run("test.chain", {"k": 3}))

    # run 到正常终态:根帧未被取消,子树取消折叠为 interrupted 错误观察
    assert result == {"k": 3}
    assert kernel._stop_flags == {}, "子树取消不得升级成 run 级 stop"

    assert probe.notes, "未在 depth-3 压栈时触发取消"
    mid_id, leaf_id, ack = probe.notes[0]
    assert set(ack) == {mid_id, leaf_id}, "子树 DFS 应收齐目标帧及其全部后代"
    # 帧级 stop 标志:同点检查抛 SubtreeCancelled 的数据源
    assert kernel._frame_stop_flags[mid_id] == "剪枝测试"
    assert kernel._frame_stop_flags[leaf_id] == "剪枝测试"
    # 子树终态:两帧均以 SubtreeCancelled 失败弹栈(不是 RunAborted)
    for fid in (mid_id, leaf_id):
        frame = kernel.stack.get(fid)
        assert frame is not None and frame.status is FrameStatus.FAILED
        assert isinstance(frame.error, SubtreeCancelled)
        assert not isinstance(frame.error, RunAborted)
    roots = [f for f in kernel.stack.tree() if f.parent_id is None]
    assert len(roots) == 1 and roots[0].status is FrameStatus.DONE
