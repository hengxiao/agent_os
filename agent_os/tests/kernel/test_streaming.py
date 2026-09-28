"""流式消费锚点测试(WS2;runner chunk 循环 + post:llm.chunk + ttft/total 记账)。

固定约定:

- ``RunConfig.stream`` 缺省开;provider caps 报 ``supports_streaming`` 时
  ``_frame_loop`` 走 stream chunk 循环,否则回落一次性 ``chat``(双保险);
- 每 chunk 发 ``post:llm.chunk``(``_sig`` 基底 + ``{model, seq, text}``,
  seq 从 0 递增;仅 ASYNC 观察,不影响主流程);
- 组装完成的 Message 与 chat 路径同形态(content/reasoning 拼接、tool_calls
  收齐、meta 合并),汇回同一条后段:``post:llm.response`` 仍恰好一次;
- ttft/total 经 ``ChatResponse.ttft_ms/total_ms`` 由 ``account()`` 帧/run
  两级累加;chat 路径恒 0;
- 流中取消与 safe point 同点语义:逐 chunk 查 ``_stop_flags``/``_pause_flags``/
  ``_frame_stop_flags``,命中抛 RunAborted/RunPaused/SubtreeCancelled,
  半截 assistant 消息不 append。
"""

from __future__ import annotations

import asyncio
import textwrap
from typing import Any, ClassVar

import pytest

from agent_os.api.v1 import (
    POST_LLM_CHUNK,
    POST_LLM_RESPONSE,
    Allow,
    ChatChunk,
    ChatResponse,
    ChatUsage,
    FrameStatus,
    Message,
    Mode,
    Permission,
    Role,
    RunConfig,
    RunStatus,
    Signal,
    ToolPolicy,
)
from agent_os.kernel.errors import RunAborted, RunPaused, SubtreeCancelled
from agent_os.providers.mock import MockProvider
from tests.helpers.kernels import assemble, record_all

STREAM_YAML = """
skills:
  - name: test.greet
    version: 1.0.0
    kind: prompt
    inputs:
      type: object
      properties: { who: { type: string } }
      required: [who]
    outputs:
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 4 }
    prompt: GREET
"""

ANSWER = '{"answer": "hello"}'


def _script(answer: str = ANSWER) -> tuple[list, list[str]]:
    """一段流式脚本:两段文本(delay 使 ttft 可测)+ 尾随终 chunk(usage 记账锚点)。

    返回 ``(脚本, 各 chunk 应发信号的 text 序列)``——终 chunk 无 delta,text 为 ""。
    """
    mid = len(answer) // 2
    parts = [answer[:mid], answer[mid:]]
    script = [
        (parts[0], 0.01),
        (parts[1], 0.01),
        ChatChunk(finish_reason="stop", usage=ChatUsage(prompt=3, completion=2)),
    ]
    return script, [*parts, ""]


def _chat_resp(answer: str = ANSWER) -> ChatResponse:
    """与流式脚本同答案/同 usage 的 chat 回包(等价性对照)。"""
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=answer),
        finish_reason="stop",
        usage=ChatUsage(prompt=3, completion=2),
    )


def _kernel(provider, tmp_path, *, sidecars=(), stream: bool = True):
    """一次性 prompt 技能的最小内核(stream 开关经 RunConfig 传入)。"""
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
        stream=stream,
    )
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(STREAM_YAML), encoding="utf-8")
    return assemble(config, provider, p, sidecars=sidecars)


def _root(kernel):
    """run 结束后取根帧(本文件技能不递归,帧树仅此一帧)。"""
    return next(f for f in kernel.stack.tree() if f.parent_id is None)


class _Probe:
    """流中动作探针(同 test_run_control 的 sidecar 模式):chunk 信号上执行一次动作。"""

    name: ClassVar[str] = "stream-probe"
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self, action, *, once: bool = True) -> None:
        self.subscriptions = [POST_LLM_CHUNK]
        self._action = action
        self._once = once
        self.fired = 0

    async def on_signal(self, sig: Signal, ctl) -> Allow:
        if not self._once or self.fired == 0:
            self.fired += 1
            await self._action(sig, ctl)
        return Allow()


# ---------------------------------------------------------------------------
# chunk 信号与组装
# ---------------------------------------------------------------------------


def test_chunk_signals_sequence(tmp_path):
    """chunk 信号序列:seq 递增、text 逐段正确、_sig 基底字段齐备。"""
    script, texts = _script()
    kernel = _kernel(MockProvider(stream_scripts=[script]), tmp_path)
    seen = record_all(kernel)
    result = asyncio.run(kernel.run("test.greet", {"who": "世界"}))
    assert result == {"answer": "hello"}

    chunks = [s for s in seen if s.name == POST_LLM_CHUNK]
    assert [s.payload["seq"] for s in chunks] == [0, 1, 2]
    assert [s.payload["text"] for s in chunks] == texts
    for s in chunks:
        assert s.payload["model"] == "mock/x"
        assert s.payload["depth"] == 1
        assert s.payload["skill"].startswith("local:test.greet@")
        assert s.frame_id


def test_stream_assembly_matches_chat_shape(tmp_path):
    """组装等价:同答案 stream 与 chat 的最终 Message 同形态。"""
    chat_kernel = _kernel(MockProvider([_chat_resp()]), tmp_path)
    stream_script, _ = _script()
    stream_kernel = _kernel(MockProvider(stream_scripts=[stream_script]), tmp_path)
    assert asyncio.run(chat_kernel.run("test.greet", {"who": "世界"})) == {"answer": "hello"}
    assert asyncio.run(stream_kernel.run("test.greet", {"who": "世界"})) == {"answer": "hello"}

    chat_msg = next(m for m in _root(chat_kernel).context.messages if m.role is Role.ASSISTANT)
    stream_msg = next(m for m in _root(stream_kernel).context.messages if m.role is Role.ASSISTANT)
    assert stream_msg.role is chat_msg.role is Role.ASSISTANT
    assert stream_msg.content == chat_msg.content == ANSWER
    assert stream_msg.tool_calls == chat_msg.tool_calls == []
    assert stream_msg.reasoning == chat_msg.reasoning is None
    assert stream_msg.source == chat_msg.source  # 均为契约缺省
    assert stream_msg.meta == chat_msg.meta == {}


def test_ttft_and_total_accounted(tmp_path):
    """ttft/total 入账:帧级 >0 且 total>=ttft;run 级与帧级同点累加。"""
    script, _ = _script()
    kernel = _kernel(MockProvider(stream_scripts=[script]), tmp_path)
    asyncio.run(kernel.run("test.greet", {"who": "世界"}))

    root = _root(kernel)
    assert root.usage.ttft_ms > 0
    assert root.usage.total_ms >= root.usage.ttft_ms
    run_usage = kernel._runs[root.run_id].state.usage
    assert run_usage.ttft_ms == root.usage.ttft_ms  # 单帧 run:两级相等
    assert run_usage.total_ms == root.usage.total_ms


def test_post_llm_response_once_with_usage(tmp_path):
    """汇回后段:post:llm.response 仍恰好一次,终 chunk 的 usage 进 account。"""
    script, _ = _script()
    kernel = _kernel(MockProvider(stream_scripts=[script]), tmp_path)
    seen = record_all(kernel)
    asyncio.run(kernel.run("test.greet", {"who": "世界"}))

    responses = [s for s in seen if s.name == POST_LLM_RESPONSE]
    assert len(responses) == 1
    assert responses[0].payload["usage"] == {"prompt": 3, "completion": 2, "cost": 0.0}
    root = _root(kernel)
    assert (root.usage.prompt_tokens, root.usage.completion_tokens) == (3, 2)
    assert root.usage.steps == 1


# ---------------------------------------------------------------------------
# 取消语义
# ---------------------------------------------------------------------------


def test_stop_mid_stream_aborts_without_partial_message(tmp_path):
    """流中 ctl.stop:下一 chunk 查表抛 RunAborted;帧上下文无半截 assistant 消息。"""

    async def act(sig, ctl):
        await ctl.stop(sig.run_id, "流中强停")

    # 3 段文本:stop 于 chunk 0 落地,chunk 1 进场即抛,chunk 2 永远不被消费
    script = [("a", 0.01), ("b", 0.01), ("c", 0.01)]
    probe = _Probe(act)
    kernel = _kernel(MockProvider(stream_scripts=[script]), tmp_path, sidecars=(probe,))
    seen = record_all(kernel)
    with pytest.raises(RunAborted, match="流中强停"):
        asyncio.run(kernel.run("test.greet", {"who": "世界"}))

    assert probe.fired == 1
    chunks = [s for s in seen if s.name == POST_LLM_CHUNK]
    assert [s.payload["seq"] for s in chunks] == [0], "stop 后的 chunk 不得再发信号"
    root = _root(kernel)
    assert root.status is FrameStatus.FAILED
    assert isinstance(root.error, RunAborted)
    assert not [m for m in root.context.messages if m.role is Role.ASSISTANT]


def test_pause_mid_stream_suspends_without_partial_message(tmp_path):
    """流中 ctl.pause:下一 chunk 查表抛 RunPaused;落 PAUSED 可 resume,无半截消息。"""

    async def act(sig, ctl):
        await ctl.pause(sig.run_id, "流中暂停")

    # 3 段文本:pause 于 chunk 0 落地,chunk 1 进场即抛,chunk 2 永远不被消费
    script = [("a", 0.01), ("b", 0.01), ("c", 0.01)]
    probe = _Probe(act)
    kernel = _kernel(MockProvider(stream_scripts=[script]), tmp_path, sidecars=(probe,))
    seen = record_all(kernel)
    with pytest.raises(RunPaused, match="流中暂停"):
        asyncio.run(kernel.run("test.greet", {"who": "世界"}))

    assert probe.fired == 1
    chunks = [s for s in seen if s.name == POST_LLM_CHUNK]
    assert [s.payload["seq"] for s in chunks] == [0], "pause 后的 chunk 不得再发信号"
    root = _root(kernel)
    assert root.status is FrameStatus.FAILED
    assert isinstance(root.error, RunPaused)
    assert not [m for m in root.context.messages if m.role is Role.ASSISTANT]
    run = next(iter(kernel._runs.values()))
    assert run.state.status is RunStatus.PAUSED
    names = [s.name for s in seen]
    assert "run.paused" in names and "run.aborted" not in names


def test_frame_cancel_mid_stream_raises_subtree_cancelled(tmp_path):
    """流中帧级取消:置 ``_frame_stop_flags`` → SubtreeCancelled(非 RunAborted)。"""
    box: dict[str, Any] = {}

    async def act(sig, ctl):
        box["kernel"]._frame_stop_flags[sig.frame_id] = "流中剪枝"

    script = [("a", 0.01), ("b", 0.01)]
    probe = _Probe(act)
    kernel = _kernel(MockProvider(stream_scripts=[script]), tmp_path, sidecars=(probe,))
    box["kernel"] = kernel
    with pytest.raises(SubtreeCancelled, match="流中剪枝"):
        asyncio.run(kernel.run("test.greet", {"who": "世界"}))

    assert probe.fired == 1
    root = _root(kernel)
    assert root.status is FrameStatus.FAILED
    assert isinstance(root.error, SubtreeCancelled)
    assert not isinstance(root.error, RunAborted)
    assert not [m for m in root.context.messages if m.role is Role.ASSISTANT]


# ---------------------------------------------------------------------------
# 回落路径(双保险)
# ---------------------------------------------------------------------------


def test_stream_disabled_falls_back_to_chat(tmp_path):
    """开关关闭(stream=False):provider 支持流式也走 chat,无 chunk 信号。"""
    script, _ = _script()
    provider = MockProvider([_chat_resp()], stream_scripts=[script])
    kernel = _kernel(provider, tmp_path, stream=False)
    seen = record_all(kernel)
    result = asyncio.run(kernel.run("test.greet", {"who": "世界"}))

    assert result == {"answer": "hello"}
    assert provider.stream_attempts == 0, "stream=False 不得触碰 stream"
    assert not [s for s in seen if s.name == POST_LLM_CHUNK]


def test_no_stream_scripts_falls_back_to_chat(tmp_path):
    """Mock 未配 stream_scripts(caps 不报流式):缺省 stream=True 也自动回落 chat。"""
    provider = MockProvider([_chat_resp()])  # caps.supports_streaming=False
    kernel = _kernel(provider, tmp_path)
    seen = record_all(kernel)
    result = asyncio.run(kernel.run("test.greet", {"who": "世界"}))

    assert result == {"answer": "hello"}
    assert provider.stream_attempts == 0
    assert not [s for s in seen if s.name == POST_LLM_CHUNK]
    # chat 路径不写时间字段(行为与引入流式前一致)
    assert _root(kernel).usage.ttft_ms == 0
