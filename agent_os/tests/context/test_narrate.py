"""NarrateCompressor 单元测试(docs/DESIGN.md §7.2 narrate;hierarchical 链中)。

固定约定:

- 候选 = 被逐区间(``select_eviction_groups`` 同 rolling 语义)内的 parts 消息,
  pinned 组天然豁免;非多模态消息不动(留给责任链下一棒);
- LLM 成功:一次廉价 chat 批量生成旁白(全部多模态消息渲染进一个请求,标注
  序号),原消息**原地替换**(content=旁白文本、parts=None、meta narrated=True),
  不删消息(``evicted=0``);parts 折算消失 → 估算下降;
- 无 providers / 未配 model / 熔断打开 / 本轮失败(LLM 异常或输出畸形:非 JSON /
  数量不符)→ 退化占位 ``[多模态内容已逐出:{mime} ×N]`` + parts=None +
  meta ``narrated="fallback"``(不静默丢);畸形输出计连败,但已返回响应的
  usage 仍落账(tokens 真实花出);
- 连败 ``breaker_threshold`` 次熔断(此后零调用);任一轮成功清零连败;
- report:``evicted=0``、marker=``"[NARRATED]"``(零动作时空 marker)。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from agent_os.api.v1 import (
    POST_COMPRESS,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    ContentPart,
    Message,
    Role,
    RunConfig,
    SkillFrame,
)
from agent_os.context.estimator import IMAGE_PART_TOKENS, TokenEstimator
from agent_os.context.manager import ContextManager
from agent_os.context.narrate import MARKER, NarrateCompressor, _fallback_text
from agent_os.context.rolling_window import RollingWindowCompressor
from agent_os.context.spill import SpillCompressor
from agent_os.context.summarize import SummarizeCompressor
from agent_os.tools.local_registry import LocalPythonToolRegistry
from tests.context.test_context import (
    _frame,
    _group,
    _pairing_ok,
    _RecordingBus,
    _registry,
    _sys_msg,
)

TASK_TEXT = "分析 UI 截图并修复布局 bug 的任务规格"


class _FakeProviders:
    """记录调用的假 ProviderManager(形状对齐 test_summarize.py 的 _FakeProviders)。

    ``reply`` 缺省 None 时按请求中带 parts 的消息条数生成旁白 JSON 数组
    (``["第 0 条图片的旁白", ...]``);不带 parts 的请求(摘要器接力)回纯文本笔记。
    """

    def __init__(self, *, reply: str | None = None, failures=None) -> None:
        self.reply = reply
        self.failures = list(failures or [])
        self.calls: list[ChatRequest] = []

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.calls.append(req)
        if self.failures:
            raise self.failures.pop(0)
        n = sum(1 for m in req.messages if m.parts)
        if self.reply is not None:
            content = self.reply
        elif n:
            content = json.dumps(
                [f"第 {i} 条图片的旁白" for i in range(n)], ensure_ascii=False
            )
        else:
            content = "摘要:早前工具往返的压缩笔记"
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=content),
            finish_reason="stop",
            usage=ChatUsage(prompt=20, completion=8, cost=0.002),
        )


def _svc(providers=None, blob=None, run_id: str = "r1"):
    return SimpleNamespace(
        estimator=TokenEstimator(), providers=providers, blob=blob, run_id=run_id
    )


def _mm_msg(i: int, n_parts: int = 1) -> Message:
    """一条多模态 USER 消息(文本投影 + n 个 image part;每 part 折算 1024 token)。"""
    return Message(
        role=Role.USER,
        content=f"截图 {i}",
        parts=[
            ContentPart(mime="image/png", ref=f"blob://r1/img{i}-{j}")
            for j in range(n_parts)
        ],
    )


def _oversized_ctx(n_groups: int = 6, size: int = 400, mm_at=(1, 3), n_parts: int = 1):
    """pinned 系统消息 + USER 任务规格 + n 个原子组;mm_at 下标组前各塞一条多模态消息。

    target 取估算 1/3 时,两条多模态消息均落在被逐区间(最旧非 pinned 段)。
    """
    msgs = [_sys_msg(), Message(role=Role.USER, content=TASK_TEXT)]
    for i in range(n_groups):
        if i in mm_at:
            msgs.append(_mm_msg(i, n_parts=n_parts))
        msgs.extend(_group(i, size=size))
    return _frame(msgs, pinned=["sys-0"]).context


def _target(ctx) -> int:
    return TokenEstimator().estimate(ctx.messages) // 3


# ---------------------------------------------------------------------------
# 改道留置(原地替换)
# ---------------------------------------------------------------------------


def test_narrate_diverts_multimodal_messages_in_place():
    providers = _FakeProviders()
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _oversized_ctx()
    before = TokenEstimator().estimate(ctx.messages)
    mm_index = [i for i, m in enumerate(ctx.messages) if m.parts]
    assert len(mm_index) == 2

    report = asyncio.run(comp.compress(ctx, before // 3, _svc(providers=providers)))

    assert report.marker == MARKER
    assert report.evicted == 0  # 原地改道不删消息
    assert report.before_tokens == before
    assert report.cache_invalidation_estimate == report.before_tokens - report.after_tokens
    # 原地:消息总数与位置不变,旁白按编号对号
    assert len(ctx.messages) == 16
    for k, i in enumerate(mm_index):
        m = ctx.messages[i]
        assert m.content == f"第 {k} 条图片的旁白"
        assert m.parts is None
        assert m.meta.get("narrated") is True
    # parts 折算消失 → 估算大幅下降(扣除旁白文本自身开销)
    assert report.after_tokens <= report.before_tokens - 2 * IMAGE_PART_TOKENS + 100
    # 非多模态消息不动(留给链下一棒);pinned 与配对不破
    assert ctx.messages[0].meta.get("id") == "sys-0"
    assert any(m.content == TASK_TEXT for m in ctx.messages)
    assert _pairing_ok(ctx.messages)


def test_narrate_leaves_kept_interval_untouched():
    """保留区间(未入被逐区间)的多模态消息不改道。"""
    providers = _FakeProviders()
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _oversized_ctx(n_groups=8, mm_at=(1, 7))  # 第二条 mm 足够新,落在保留区间
    before = TokenEstimator().estimate(ctx.messages)

    asyncio.run(comp.compress(ctx, before // 2, _svc(providers=providers)))

    narrated = [m for m in ctx.messages if m.meta.get("narrated") is True]
    kept_mm = [m for m in ctx.messages if m.parts]
    assert len(narrated) == 1 and len(kept_mm) == 1
    assert len(providers.calls[0].messages) == 3  # SYSTEM + 头 + 1 条编号消息


# ---------------------------------------------------------------------------
# 批量一次调用
# ---------------------------------------------------------------------------


def test_narrate_batches_all_multimodal_messages_into_one_call():
    providers = _FakeProviders()
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _oversized_ctx(mm_at=(0, 1, 2))  # 3 条多模态,同在被逐区间
    before = TokenEstimator().estimate(ctx.messages)

    asyncio.run(comp.compress(ctx, before // 3, _svc(providers=providers)))

    assert len(providers.calls) == 1  # 一次廉价调用批量生成
    req = providers.calls[0]
    assert req.model == "mock/cheap" and req.temperature == 0.2
    # parts 原样随请求走(适配器按 vision 能力序列化,本层不感知 blob/caps)
    assert sum(len(m.parts or []) for m in req.messages) == 3
    sys_msg, header, *numbered = req.messages
    assert sys_msg.role is Role.SYSTEM and "JSON" in sys_msg.content
    assert TASK_TEXT in header.content  # 帧任务规格进 prompt
    assert len(numbered) == 3
    for i, m in enumerate(numbered):
        assert f"【{i}】" in m.content and "role=user" in m.content


# ---------------------------------------------------------------------------
# 零动作与退化占位
# ---------------------------------------------------------------------------


def test_narrate_no_multimodal_is_zero_action():
    providers = _FakeProviders()
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _oversized_ctx(mm_at=())
    before = TokenEstimator().estimate(ctx.messages)
    snapshot = [(m.role, m.content) for m in ctx.messages]

    report = asyncio.run(comp.compress(ctx, before // 3, _svc(providers=providers)))

    assert providers.calls == []
    assert report.evicted == 0 and report.marker == ""
    assert report.before_tokens == report.after_tokens == before
    assert [(m.role, m.content) for m in ctx.messages] == snapshot


def test_narrate_below_target_is_noop():
    providers = _FakeProviders()
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _frame([_sys_msg(), _mm_msg(0)], pinned=["sys-0"]).context
    before = TokenEstimator().estimate(ctx.messages)

    report = asyncio.run(comp.compress(ctx, before + 100, _svc(providers=providers)))

    assert providers.calls == []
    assert report.evicted == 0 and report.marker == ""
    assert report.before_tokens == report.after_tokens == before


def test_narrate_fallback_placeholder_without_providers():
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _oversized_ctx(n_parts=2)  # 每条 2 个 part

    report = asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=None)))

    assert report.marker == MARKER  # 有改道动作(占位),非零动作
    fallback = [m for m in ctx.messages if m.meta.get("narrated") == "fallback"]
    assert len(fallback) == 2
    for m in fallback:
        assert m.content == "[多模态内容已逐出:image/png ×2]"  # 不静默丢
        assert m.parts is None
    assert "_compress_llm_usage" not in ctx.working
    assert _pairing_ok(ctx.messages)


def test_narrate_fallback_when_model_unset():
    providers = _FakeProviders()
    comp = NarrateCompressor(model=None)
    ctx = _oversized_ctx()

    report = asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    assert providers.calls == []  # 未配模型不调 LLM
    assert report.marker == MARKER
    fallback = [m for m in ctx.messages if m.meta.get("narrated") == "fallback"]
    assert len(fallback) == 2


def test_fallback_text_groups_by_mime():
    parts = [
        ContentPart(mime="image/png"),
        ContentPart(mime="image/jpeg"),
        ContentPart(mime="image/png"),
    ]
    assert _fallback_text(parts) == "[多模态内容已逐出:image/png ×2, image/jpeg ×1]"


# ---------------------------------------------------------------------------
# 熔断(口径同 summarize)
# ---------------------------------------------------------------------------


def test_narrate_breaker_opens_after_consecutive_failures():
    providers = _FakeProviders(failures=[RuntimeError("x"), RuntimeError("y")])
    comp = NarrateCompressor(model="mock/cheap", breaker_threshold=2)

    for _ in range(3):
        ctx = _oversized_ctx()  # 每轮独立超限上下文(上轮已改道的不再入候选)
        asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    assert len(providers.calls) == 2  # 第三次熔断已开,不再调 chat
    assert comp._breaker_open is True
    # 熔断开后仍退化占位(不静默丢)
    fallback = [m for m in ctx.messages if m.meta.get("narrated") == "fallback"]
    assert len(fallback) == 2


def test_narrate_success_resets_failure_count():
    providers = _FakeProviders(failures=[RuntimeError("x")])
    comp = NarrateCompressor(model="mock/cheap", breaker_threshold=2)

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


# ---------------------------------------------------------------------------
# 输出畸形(非 JSON / 数量不符)= 本轮失败
# ---------------------------------------------------------------------------


def test_narrate_malformed_output_falls_back_and_counts_failure():
    providers = _FakeProviders(reply="这不是 JSON")
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _oversized_ctx()

    report = asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    assert report.marker == MARKER
    fallback = [m for m in ctx.messages if m.meta.get("narrated") == "fallback"]
    assert len(fallback) == 2
    assert comp._failures == 1  # 输出畸形 = 本轮调用失败(口径同 summarize 的 except)
    # 响应已返回:usage 仍落账(tokens 真实花出,预算记账不谎报)
    assert len(ctx.working["_compress_llm_usage"]) == 1


def test_narrate_count_mismatch_falls_back():
    providers = _FakeProviders(reply='["只有一条旁白"]')  # 2 条多模态,数量不符
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _oversized_ctx()

    asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    assert [m for m in ctx.messages if m.meta.get("narrated") == "fallback"]
    assert comp._failures == 1


def test_narrate_tolerates_single_code_fence():
    providers = _FakeProviders(reply='```json\n["旁白甲", "旁白乙"]\n```')
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _oversized_ctx()

    asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    contents = [m.content for m in ctx.messages if m.meta.get("narrated") is True]
    assert contents == ["旁白甲", "旁白乙"]
    assert comp._failures == 0


# ---------------------------------------------------------------------------
# usage 落账
# ---------------------------------------------------------------------------


def test_narrate_records_llm_usage_in_working():
    providers = _FakeProviders()
    comp = NarrateCompressor(model="mock/cheap")
    ctx = _oversized_ctx()

    asyncio.run(comp.compress(ctx, _target(ctx), _svc(providers=providers)))

    entries = ctx.working["_compress_llm_usage"]
    assert len(entries) == 1
    assert entries[0]["model"] == "mock/cheap"
    assert entries[0]["usage"].prompt == 20
    assert entries[0]["usage"].completion == 8


# ---------------------------------------------------------------------------
# 模式表(经 ContextManager + manifest;strategy 遥测)
# ---------------------------------------------------------------------------


def _narrate_manager(reg, providers, *, mode: str = "narrate"):
    """注册表四段齐全的 manager(narrate/summarize 配模型 + fake providers)。

    spill 用默认阈值 4000:本文件测试消息不触发 spill,聚光灯在 narrate 段。
    """
    bus = _RecordingBus()
    mgr = ContextManager(
        skills=reg,
        tools=LocalPythonToolRegistry(),
        config=RunConfig(compression=mode, max_cost=2.0),
        compressors={
            "spill": SpillCompressor(),
            "truncate": RollingWindowCompressor(),
            "narrate": NarrateCompressor(model="mock/cheap"),
            "summarize": SummarizeCompressor(model="mock/cheap"),
        },
        providers=providers,
        estimator=TokenEstimator(),
        signals=bus,
        default_max_tokens=4000,
        target_ratio=0.5,
    )
    return mgr, bus


def _mm_heavy_frame(n_mm: int = 4) -> SkillFrame:
    """重负主要来自多模态 parts 的帧:narrate 一段即可压到目标(truncate 短路)。"""
    msgs = [_sys_msg(), Message(role=Role.USER, content=TASK_TEXT)]
    msgs.extend(_mm_msg(i) for i in range(n_mm))
    for i in range(3):
        msgs.extend(_group(i, size=50))
    return _frame(msgs, pinned=["sys-0"])


def test_narrate_mode_chain_via_manifest(tmp_path):
    """manifest compress="narrate" → 链 narrate+truncate;多模态消息经 manager 改道。"""
    reg = _registry(tmp_path, mode="narrate")
    providers = _FakeProviders()
    mgr, bus = _narrate_manager(reg, providers)
    frame = _mm_heavy_frame()

    asyncio.run(mgr.maintain(frame))

    post = [s for s in bus.seen if s.name == POST_COMPRESS]
    assert len(post) == 1 and post[0].payload["strategy"] == "narrate+truncate"
    assert len(providers.calls) == 1  # 批量一次;after 达标,truncate 短路
    narrated = [m for m in frame.context.messages if m.meta.get("narrated") is True]
    assert len(narrated) == 3 and all(m.parts is None for m in narrated)
    # 保留区间内的多模态消息(最新一条)不动
    assert sum(1 for m in frame.context.messages if m.parts) == 1
    assert _pairing_ok(frame.context.messages)


def test_hierarchical_narrate_feeds_summarize(tmp_path):
    """hierarchical 链 narrate 在 summarize 前:摘要器拿到的被逐区间已是旁白文本。"""
    reg = _registry(tmp_path, mode="hierarchical")
    providers = _FakeProviders()  # 同一 fake:带 parts 的请求回旁白 JSON,否则回摘要笔记
    mgr, bus = _narrate_manager(reg, providers, mode="hierarchical")
    msgs = [_sys_msg(), Message(role=Role.USER, content=TASK_TEXT)]
    msgs.extend([_mm_msg(0), _mm_msg(1)])
    for i in range(30):
        msgs.extend(_group(i, size=400))  # 重文本区间:narrate 后仍超目标,summarize 接力
    frame = _frame(msgs, pinned=["sys-0"])

    asyncio.run(mgr.maintain(frame))

    post = [s for s in bus.seen if s.name == POST_COMPRESS]
    assert len(post) == 1
    assert post[0].payload["strategy"] == "spill+narrate+summarize"
    assert len(providers.calls) == 2  # narrate 一次 + summarize 一次
    narrate_req, summarize_req = providers.calls
    assert any(m.parts for m in narrate_req.messages)
    # 摘要 prompt 渲染的被逐区间已是旁白文本(而非占位/原图)
    assert "旁白" in summarize_req.messages[-1].content
    assert _pairing_ok(frame.context.messages)
