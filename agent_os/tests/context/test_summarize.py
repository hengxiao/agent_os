"""SummarizeCompressor 单元测试(docs/DESIGN.md §7.2 summarize;hierarchical 链尾)。

固定约定:

- 驱逐选择复用 rolling 语义(最旧非 pinned 组起逐,始终保留最后一个非 pinned 组);
- LLM 成功:被逐区间整组删除,首个被逐组下标处插入 SYSTEM note
  (``meta={"id": "compress-note-<n>", "compressed": True}``,非 pinned);
- 无 providers / 未配 model / 熔断打开 / 本轮 LLM 异常 → 退化纯 truncate
  (整组丢弃、无 note),marker=``"[COMPRESSED:truncate]"``;
- 连败 ``breaker_threshold`` 次后熔断(此后不再调 chat);任一轮成功清零连败;
- 每次成功调用把 ``{"model", "usage"}`` 落 ``ctx.working["_compress_llm_usage"]``;
- 无可逐组(只剩一个非 pinned 组)→ 硬截断兜底,标注 ``[truncated]``。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_os.api.v1 import ChatRequest, ChatResponse, ChatUsage, Message, Role
from agent_os.context.estimator import TokenEstimator
from agent_os.context.rolling_window import MARKER
from agent_os.context.summarize import TRUNCATE_MARKER, SummarizeCompressor
from tests.context.test_context import _frame, _group, _pairing_ok, _sys_msg

TASK_TEXT = "修复登录 bug 的任务规格"


class _FakeProviders:
    """记录调用的假 ProviderManager(形状对齐 tests/providers/test_manager.py 的 FlakyProvider)。"""

    def __init__(self, *, note: str = "摘要:早前工具往返的压缩笔记", failures=None) -> None:
        self.note = note
        self.failures = list(failures or [])
        self.calls: list[ChatRequest] = []

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.calls.append(req)
        if self.failures:
            raise self.failures.pop(0)
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=self.note),
            finish_reason="stop",
            usage=ChatUsage(prompt=12, completion=5, cost=0.001),
        )


def _svc(providers=None, blob=None, run_id: str = "r1"):
    return SimpleNamespace(
        estimator=TokenEstimator(), providers=providers, blob=blob, run_id=run_id
    )


def _oversized_ctx(n_groups: int = 6, size: int = 400):
    """pinned 系统消息 + USER 任务规格 + n 个原子组(远超 target)。"""
    msgs = [_sys_msg(), Message(role=Role.USER, content=TASK_TEXT)]
    for i in range(n_groups):
        msgs.extend(_group(i, size=size))
    return _frame(msgs, pinned=["sys-0"]).context


def _target(ctx) -> int:
    return TokenEstimator().estimate(ctx.messages) // 3


def test_summarize_evicts_old_groups_and_inserts_note():
    providers = _FakeProviders()
    comp = SummarizeCompressor(model="mock/cheap")
    ctx = _oversized_ctx()

    report = asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    assert report.marker == MARKER
    assert report.evicted > 0
    assert report.after_tokens < report.before_tokens
    assert report.cache_invalidation_estimate == report.before_tokens - report.after_tokens
    # note 插入在首个被逐组的下标位置:0 号是 pinned 系统消息,1 号原为首逐组(USER 任务)
    assert ctx.messages[0].meta.get("id") == "sys-0"
    note = ctx.messages[1]
    assert note.role is Role.SYSTEM
    assert note.meta.get("compressed") is True
    assert note.meta.get("id") == "compress-note-0"
    assert note.content.startswith(MARKER)
    assert providers.note in note.content
    # note 非 pinned(可被后续压缩再逐,有意为之)
    assert note.meta.get("id") not in ctx.pinned
    # 旧组消失、最后一个非 pinned 组始终保留
    remaining = {c.id for m in ctx.messages for c in m.tool_calls}
    assert "c0-0" not in remaining
    assert "c5-0" in remaining
    assert _pairing_ok(ctx.messages)


def test_summarize_note_ids_are_unique_across_rounds():
    providers = _FakeProviders()
    comp = SummarizeCompressor(model="mock/cheap")
    for _ in range(2):
        ctx = _oversized_ctx()
        asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))
    assert comp._note_seq == 2  # 实例级自增,不撞 pinned


def test_summarize_prompt_is_context_aware():
    providers = _FakeProviders()
    comp = SummarizeCompressor(model="mock/cheap")
    ctx = _oversized_ctx()

    asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    req = providers.calls[0]
    assert req.model == "mock/cheap"
    assert req.temperature == 0.2
    sys_msg, user_msg = req.messages
    assert sys_msg.role is Role.SYSTEM
    # 保留契约关键词(§7.2)
    assert "逐字保留" in sys_msg.content
    assert "架构决策" in sys_msg.content
    assert "验证状态" in sys_msg.content
    assert "TODO" in sys_msg.content
    # USER 正文:帧任务规格 + pinned 常驻约束 + 被逐区间渲染
    assert user_msg.role is Role.USER
    assert TASK_TEXT in user_msg.content
    assert "常驻约束" in user_msg.content
    assert "指令" in user_msg.content  # pinned 系统消息内容(_sys_msg)
    assert "[tool]" in user_msg.content  # 被逐区间逐条渲染(role 标注)


def test_summarize_degrades_to_truncate_without_providers():
    comp = SummarizeCompressor(model="mock/cheap")
    ctx = _oversized_ctx()

    report = asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=None)))

    assert report.marker == TRUNCATE_MARKER
    assert report.evicted > 0
    assert not any(m.meta.get("compressed") for m in ctx.messages)
    assert "_compress_llm_usage" not in ctx.working
    assert _pairing_ok(ctx.messages)


def test_summarize_degrades_when_model_unset():
    providers = _FakeProviders()
    comp = SummarizeCompressor(model=None)
    ctx = _oversized_ctx()

    report = asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    assert providers.calls == []  # 未配模型不调 LLM
    assert report.marker == TRUNCATE_MARKER
    assert not any(m.meta.get("compressed") for m in ctx.messages)


def test_summarize_llm_failure_degrades_to_truncate():
    providers = _FakeProviders(failures=[RuntimeError("boom")])
    comp = SummarizeCompressor(model="mock/cheap")
    ctx = _oversized_ctx()

    report = asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    assert len(providers.calls) == 1
    assert report.marker == TRUNCATE_MARKER
    assert not any(m.meta.get("compressed") for m in ctx.messages)
    assert "_compress_llm_usage" not in ctx.working
    assert _pairing_ok(ctx.messages)


def test_summarize_breaker_opens_after_consecutive_failures():
    providers = _FakeProviders(failures=[RuntimeError("x"), RuntimeError("y")])
    comp = SummarizeCompressor(model="mock/cheap", breaker_threshold=2)

    for _ in range(3):
        ctx = _oversized_ctx()  # 每轮独立超限上下文(上轮已压缩的会短路)
        asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    assert len(providers.calls) == 2  # 第三次熔断已开,不再调 chat
    assert comp._breaker_open is True


def test_summarize_success_resets_failure_count():
    providers = _FakeProviders(failures=[RuntimeError("x")])
    comp = SummarizeCompressor(model="mock/cheap", breaker_threshold=2)

    ctx = _oversized_ctx()  # 第 1 轮:失败(连败 1)
    asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))
    ctx = _oversized_ctx()  # 第 2 轮:成功,连败清零
    asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))
    assert comp._failures == 0
    providers.failures.append(RuntimeError("z"))
    ctx = _oversized_ctx()  # 第 3 轮:再败 1 次,熔断未开
    asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    assert len(providers.calls) == 3
    assert comp._breaker_open is False


def test_summarize_records_llm_usage_in_working():
    providers = _FakeProviders()
    comp = SummarizeCompressor(model="mock/cheap")
    ctx = _oversized_ctx()

    asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    entries = ctx.working["_compress_llm_usage"]
    assert len(entries) == 1
    assert entries[0]["model"] == "mock/cheap"
    assert entries[0]["usage"].prompt == 12
    assert entries[0]["usage"].completion == 5


def test_summarize_below_target_is_noop():
    providers = _FakeProviders()
    comp = SummarizeCompressor(model="mock/cheap")
    ctx = _frame([_sys_msg(), Message(role=Role.USER, content="短")], pinned=["sys-0"]).context
    before = TokenEstimator().estimate(ctx.messages)

    report = asyncio.run(comp.compress(ctx, before + 100, _svc(providers=providers)))

    assert providers.calls == []
    assert report.evicted == 0
    assert report.marker == ""
    assert report.before_tokens == report.after_tokens == before


def test_summarize_single_huge_group_hard_truncated():
    """只剩一个非 pinned 组(无组可逐)→ 硬截断兜底,不调 LLM。"""
    providers = _FakeProviders()
    comp = SummarizeCompressor(model="mock/cheap")
    msgs = [_sys_msg(), *_group(0, size=20_000)]
    ctx = _frame(msgs, pinned=["sys-0"]).context

    report = asyncio.run(comp.compress(ctx, 500, _svc(providers=providers)))

    assert providers.calls == []
    assert report.evicted == 0
    assert report.marker == MARKER
    assert any("[truncated]" in m.content for m in ctx.messages)
    assert report.after_tokens < report.before_tokens
    assert _pairing_ok(ctx.messages)
