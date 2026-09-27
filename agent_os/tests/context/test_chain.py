"""ChainCompressor 单元测试(docs/DESIGN.md §7.2 hierarchical 责任链)。

固定约定:

- 链按序执行(spill → summarize),每阶段前估算 <= target 即短路;
- report 聚合:evicted 累加,before/after 为链级首末估算,非空 marker 以 ``+`` 连接;
- ``name`` 为各阶段 name 的 ``+`` 连接。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_os.api.v1 import Message, Role, ToolCall
from agent_os.context.chain import ChainCompressor
from agent_os.context.estimator import TokenEstimator
from agent_os.context.spill import SpillCompressor
from agent_os.context.summarize import SummarizeCompressor
from agent_os.tools.blob import InMemoryBlobStore
from tests.context.test_context import _frame, _group, _pairing_ok, _sys_msg


class _FakeProviders:
    """记录调用的假 ProviderManager(同 test_summarize 的形状约定)。"""

    def __init__(self, *, note: str = "摘要:链尾笔记") -> None:
        self.note = note
        self.calls = []

    async def chat(self, req):
        from agent_os.api.v1 import ChatResponse, ChatUsage

        self.calls.append(req)
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=self.note),
            finish_reason="stop",
            usage=ChatUsage(prompt=12, completion=5),
        )


def _svc(blob=None, providers=None, run_id: str = "r1"):
    return SimpleNamespace(
        estimator=TokenEstimator(), providers=providers, blob=blob, run_id=run_id
    )


#: 首尾可区分的大输出(SpillCompressor 阈值 500 之上)
BIG = "HEAD-" + "x" * 3000 + "-TAIL"


def _big_tail_group() -> list[Message]:
    """末尾原子组:assistant + 一条大 TOOL 输出(spill 的对象;最新的组,不该被逐)。"""
    call = ToolCall(id="big-1", name="fetch_page", args={"url": "https://x"})
    return [
        Message(role=Role.ASSISTANT, content="", tool_calls=[call]),
        Message(role=Role.TOOL, content=BIG, tool_call_id="big-1", name="fetch_page"),
    ]


def _chain() -> tuple[ChainCompressor, _FakeProviders, InMemoryBlobStore]:
    providers = _FakeProviders()
    blob = InMemoryBlobStore()
    chain = ChainCompressor(
        [SpillCompressor(threshold_chars=500), SummarizeCompressor(model="mock/cheap")]
    )
    return chain, providers, blob


def test_chain_spill_then_summarize_hierarchical():
    """hierarchical 场景:大 TOOL 输出变 ref,旧组被摘要为 note,最新组保留。"""
    chain, providers, blob = _chain()
    assert chain.name == "spill+summarize"
    msgs = [_sys_msg(), Message(role=Role.USER, content="hierarchical 场景任务")]
    for i in range(6):
        msgs.extend(_group(i, size=300))
    msgs.extend(_big_tail_group())
    ctx = _frame(msgs, pinned=["sys-0"]).context
    before = TokenEstimator().estimate(ctx.messages)

    report = asyncio.run(chain.compress(ctx, before // 3, _svc(blob=blob, providers=providers)))

    # spill 生效:大输出变冻结 ref(原文可从 blob 逐字节取回)
    spilled = [m for m in ctx.messages if "[SPILLED]" in m.content]
    assert len(spilled) == 1
    assert "HEAD-" in spilled[0].content and "-TAIL" in spilled[0].content
    refs = [line.split("ref: ", 1)[1].rstrip(")") for line in spilled[0].content.splitlines()
            if "ref: " in line]
    assert asyncio.run(blob.get(refs[0])) == BIG.encode("utf-8")
    # summarize 生效:旧组消失、留下 note、最新组(含 spilled ref)保留
    assert providers.calls, "spill 后仍超限,summarize 应执行"
    assert any(m.meta.get("compressed") for m in ctx.messages)
    remaining = {c.id for m in ctx.messages for c in m.tool_calls}
    assert "c0-0" not in remaining
    assert "big-1" in remaining
    # 链级 report 聚合
    assert report.marker == "[SPILLED]+[COMPRESSED]"
    assert report.evicted > 0
    assert report.before_tokens == before
    assert report.after_tokens < before
    assert report.cache_invalidation_estimate == before - report.after_tokens
    assert _pairing_ok(ctx.messages)


def test_chain_short_circuits_when_spill_suffices():
    """只大 TOOL 输出:spill 后达标,summarize 短路(不调 LLM)。"""
    chain, providers, blob = _chain()
    msgs = [_sys_msg(), *_big_tail_group()]
    ctx = _frame(msgs, pinned=["sys-0"]).context
    before = TokenEstimator().estimate(ctx.messages)

    report = asyncio.run(chain.compress(ctx, before // 2, _svc(blob=blob, providers=providers)))

    assert providers.calls == []
    assert report.marker == "[SPILLED]"
    assert report.evicted == 0
    assert report.after_tokens <= before // 2
    assert _pairing_ok(ctx.messages)


def test_chain_already_below_target_is_noop():
    chain, providers, _ = _chain()
    ctx = _frame([_sys_msg(), Message(role=Role.USER, content="短")], pinned=["sys-0"]).context
    before = TokenEstimator().estimate(ctx.messages)

    report = asyncio.run(chain.compress(ctx, before + 100, _svc(providers=providers)))

    assert providers.calls == []
    assert report.marker == ""
    assert report.evicted == 0
    assert report.before_tokens == report.after_tokens == before
