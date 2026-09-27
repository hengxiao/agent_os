"""SpillCompressor 单元测试(docs/DESIGN.md §7.2 spill;hierarchical 链首)。

固定约定:

- 超长(> threshold_chars)非 pinned TOOL 消息原地改写为**冻结替换串**:
  ``[SPILLED]`` 标记行(原始字节数 + ref)+ head/tail 片段 + blob_get 取回提示;
  替换串确定性生成,同输入必得同串(§7.2/§7.4 前缀稳定性);
- blob 缺失或 run_id 为空 → no-op(零值 report,不抛错);
- 小消息 / pinned 消息 / 非 TOOL 消息不动;二次压缩幂等(不重复 put);
- report:evicted=0;命中时 marker=``"[SPILLED]"``,无命中 marker=``""``。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_os.api.v1 import Message, Role, ToolCall
from agent_os.context.estimator import TokenEstimator
from agent_os.context.spill import MARKER, SpillCompressor
from agent_os.tools.blob import InMemoryBlobStore
from tests.context.test_context import _frame, _pairing_ok, _sys_msg


class _CountingBlob(InMemoryBlobStore):
    """记录 put 次数与 ref 的内存 blob(store 行为与生产实现一致)。"""

    def __init__(self) -> None:
        super().__init__()
        self.puts: list[str] = []

    async def put(self, data: bytes, run_id: str) -> str:
        ref = await super().put(data, run_id)
        self.puts.append(ref)
        return ref


def _svc(blob=None, providers=None, run_id: str = "r1"):
    return SimpleNamespace(
        estimator=TokenEstimator(), providers=providers, blob=blob, run_id=run_id
    )


def _big_tool_msgs(big: str, *, meta: dict | None = None) -> list[Message]:
    call = ToolCall(id="big-1", name="fetch_page", args={"url": "https://x"})
    return [
        Message(role=Role.ASSISTANT, content="", tool_calls=[call]),
        Message(
            role=Role.TOOL,
            content=big,
            tool_call_id="big-1",
            name="fetch_page",
            meta=meta or {},
        ),
    ]


#: 首尾可区分的大输出(默认阈值 4000 之上)
BIG = "HEAD-" + "x" * 4000 + "y" * 4000 + "-TAIL"


def test_spill_replaces_big_tool_message():
    blob = _CountingBlob()
    comp = SpillCompressor()  # 默认 threshold 4000 / head 500 / tail 500
    ctx = _frame([_sys_msg(), *_big_tool_msgs(BIG)], pinned=["sys-0"]).context
    before = TokenEstimator().estimate(ctx.messages)

    report = asyncio.run(comp.compress(ctx, 10**9, _svc(blob=blob)))

    m = ctx.messages[-1]
    assert MARKER in m.content
    assert f"原始 {len(BIG.encode('utf-8'))} 字节" in m.content
    assert BIG[:500] in m.content  # head 片段
    assert BIG[-500:] in m.content  # tail 片段
    assert "blob_get" in m.content  # 取回提示
    # blob 回读原文逐字节一致
    assert len(blob.puts) == 1
    assert blob.puts[0].startswith("blob://r1/")
    assert asyncio.run(blob.get(blob.puts[0])) == BIG.encode("utf-8")
    assert blob.puts[0] in m.content
    # report 字段
    assert report.evicted == 0
    assert report.before_tokens == before
    assert report.after_tokens < before
    assert report.cache_invalidation_estimate == before - report.after_tokens
    assert report.marker == MARKER
    assert _pairing_ok(ctx.messages)  # 原地改写不破坏配对


def test_spill_replacement_is_frozen_and_deterministic():
    """同一原文两次独立压缩,替换串逐字节一致(§7.2 冻结;无时间戳/随机量)。"""
    comp = SpillCompressor()
    ctx1 = _frame(_big_tool_msgs(BIG)).context
    ctx2 = _frame(_big_tool_msgs(BIG)).context
    asyncio.run(comp.compress(ctx1, 0, _svc(blob=_CountingBlob())))
    asyncio.run(comp.compress(ctx2, 0, _svc(blob=_CountingBlob())))
    assert ctx1.messages[-1].content == ctx2.messages[-1].content


def test_spill_skips_small_tool_message():
    blob = _CountingBlob()
    comp = SpillCompressor()
    msgs = _big_tool_msgs("小输出")
    ctx = _frame(msgs).context

    report = asyncio.run(comp.compress(ctx, 0, _svc(blob=blob)))

    assert ctx.messages[-1].content == "小输出"
    assert blob.puts == []
    assert report.marker == ""
    assert report.before_tokens == report.after_tokens


def test_spill_skips_pinned_tool_message():
    blob = _CountingBlob()
    comp = SpillCompressor()
    msgs = [_sys_msg(), *_big_tool_msgs(BIG, meta={"id": "big-0"})]
    ctx = _frame(msgs, pinned=["sys-0", "big-0"]).context

    report = asyncio.run(comp.compress(ctx, 0, _svc(blob=blob)))

    assert ctx.messages[-1].content == BIG
    assert blob.puts == []
    assert report.marker == ""


def test_spill_second_run_is_idempotent():
    blob = _CountingBlob()
    comp = SpillCompressor()
    ctx = _frame(_big_tool_msgs(BIG)).context

    asyncio.run(comp.compress(ctx, 0, _svc(blob=blob)))
    after_first = ctx.messages[-1].content
    asyncio.run(comp.compress(ctx, 0, _svc(blob=blob)))

    assert ctx.messages[-1].content == after_first  # 内容不变
    assert len(blob.puts) == 1  # 不重复 put


def test_spill_without_blob_is_noop():
    comp = SpillCompressor()
    ctx = _frame(_big_tool_msgs(BIG)).context
    before = TokenEstimator().estimate(ctx.messages)

    report = asyncio.run(comp.compress(ctx, 0, _svc(blob=None)))

    assert ctx.messages[-1].content == BIG
    assert report.marker == ""
    assert report.before_tokens == report.after_tokens == before


def test_spill_without_run_id_is_noop():
    blob = _CountingBlob()
    comp = SpillCompressor()
    ctx = _frame(_big_tool_msgs(BIG)).context

    report = asyncio.run(comp.compress(ctx, 0, _svc(blob=blob, run_id="")))

    assert ctx.messages[-1].content == BIG
    assert blob.puts == []
    assert report.marker == ""


def test_spill_ignores_user_and_assistant_messages():
    blob = _CountingBlob()
    comp = SpillCompressor()
    msgs = [
        Message(role=Role.USER, content=BIG),
        Message(role=Role.ASSISTANT, content=BIG),
    ]
    ctx = _frame(msgs).context

    report = asyncio.run(comp.compress(ctx, 0, _svc(blob=blob)))

    assert [m.content for m in ctx.messages] == [BIG, BIG]
    assert blob.puts == []
    assert report.marker == ""
