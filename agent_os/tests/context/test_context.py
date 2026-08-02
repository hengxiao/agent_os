"""M3 锚点测试:Context 子系统(docs/DESIGN.md §7;估算器 + RollingWindowCompressor + 状态注入 + 前缀稳定性)。

固定约定:

- ``TokenEstimator``:``estimate(messages) -> int``(char/4 + 每消息开销),与 ProviderManager 同一口径;
- ``RollingWindowCompressor.compress(ctx, target_tokens, svc)``:原子组整组驱逐(assistant
  带 tool_calls 与其全部 tool result 同进同出),pinned(``m.meta["id"]`` 在 ``ctx.pinned`` 中)
  永不移除;单组超限 → 硬截断并标注 ``[truncated]``;CompressionReport.marker == ``"[COMPRESSED]"``;
- ContextManager.maintain:cap = manifest ``context_policy.max_tokens`` 或默认值;超 cap 压到
  ``int(cap * target_ratio)``;``compress: "off"``(manifest 或 RunConfig)时短路;
- 状态注入(§7.3):build 时尾部追加一条 ``role=USER, meta={"kind": "status"}`` 的 key-value
  元消息(含 step/tokens/cost/budget_remaining/hint);**ephemeral**——不进 frame.context.messages;
  预算剩余 < 20% 时 hint 为收敛策略;
- 前缀稳定性(§7.4 不变量 5):两次连续 build,非 status 消息逐字节一致。
"""

from __future__ import annotations

import asyncio
import textwrap

from hypothesis import given, settings
from hypothesis import strategies as st

from agent_os.api.v1 import (
    POST_COMPRESS,
    PRE_COMPRESS,
    FrameContext,
    Message,
    Role,
    RunConfig,
    Signal,
    SkillFrame,
    SkillRef,
    ToolCall,
)
from agent_os.context.estimator import TokenEstimator
from agent_os.context.manager import ContextManager
from agent_os.context.rolling_window import RollingWindowCompressor
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

CHATTY_YAML = """
skills:
  - name: chatty
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    permissions: { tools: [], skills: [] }
    context_policy: { max_tokens: 4000, compress: truncate }
    model: { prefer: ["mock/x"] }
    prompt: 闲聊
"""


def _registry(tmp_path) -> LocalFileSkillRegistry:
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(CHATTY_YAML), encoding="utf-8")
    return LocalFileSkillRegistry(str(tmp_path / "skills.yaml"))


def _frame(messages: list[Message], pinned: list[str] | None = None) -> SkillFrame:
    return SkillFrame(
        frame_id="f1",
        run_id="r1",
        skill=SkillRef(name="chatty"),
        input={},
        context=FrameContext(messages=list(messages), pinned=pinned or []),
    )


def _sys_msg() -> Message:
    return Message(role=Role.SYSTEM, content="指令", meta={"id": "sys-0"})


def _group(idx: int, k: int = 1, size: int = 400) -> list[Message]:
    """一个原子组:assistant(k 个 tool_calls)+ k 条 tool result。"""
    calls = [ToolCall(id=f"c{idx}-{j}", name="t", args={}) for j in range(k)]
    msgs = [Message(role=Role.ASSISTANT, content="", tool_calls=calls)]
    msgs.extend(
        Message(role=Role.TOOL, content="x" * size, tool_call_id=c.id, name="t") for c in calls
    )
    return msgs


def _pairing_ok(messages: list[Message]) -> bool:
    """不变量 2:无孤儿 tool result,无缺失 result 的 tool_call。"""
    call_ids: set[str] = set()
    result_ids: set[str] = set()
    for m in messages:
        if m.role is Role.ASSISTANT:
            call_ids.update(c.id for c in m.tool_calls)
        elif m.role is Role.TOOL and m.tool_call_id:
            result_ids.add(m.tool_call_id)
    return call_ids == result_ids


def _svc():
    from types import SimpleNamespace

    return SimpleNamespace(estimator=TokenEstimator(), providers=None, blob=None)


# ---------------------------------------------------------------------------
# TokenEstimator
# ---------------------------------------------------------------------------


def test_estimator_scales_with_content():
    est = TokenEstimator()
    small = est.estimate([Message(role=Role.USER, content="hi")])
    big = est.estimate([Message(role=Role.USER, content="x" * 4000)])
    assert big > small
    assert est.estimate_text("abcd" * 100) >= 100
    assert est.estimate([]) == 0


# ---------------------------------------------------------------------------
# RollingWindowCompressor 单元
# ---------------------------------------------------------------------------


def test_rolling_window_evicts_oldest_groups_first():
    est = TokenEstimator()
    comp = RollingWindowCompressor()
    msgs = [_sys_msg()]
    for i in range(6):
        msgs.extend(_group(i, size=400))
    ctx = _frame(msgs, pinned=["sys-0"]).context
    before = est.estimate(ctx.messages)
    target = before // 3

    report = asyncio.run(comp.compress(ctx, target, _svc()))

    assert report.marker == "[COMPRESSED]"
    assert report.after_tokens < report.before_tokens
    assert ctx.messages[0].meta.get("id") == "sys-0"  # pinned 永驻
    assert _pairing_ok(ctx.messages)
    # 最旧的组先走:剩下的都是较新的组
    remaining_call_ids = {c.id for m in ctx.messages for c in m.tool_calls}
    assert "c5-0" in remaining_call_ids
    assert "c0-0" not in remaining_call_ids


def test_rolling_window_never_splits_atomic_group():
    est = TokenEstimator()
    comp = RollingWindowCompressor()
    msgs = [_sys_msg(), Message(role=Role.USER, content="u" * 200)]
    msgs.extend(_group(0, k=3, size=800))  # 一个大组:1 assistant + 3 results
    ctx = _frame(msgs, pinned=["sys-0"]).context
    before = est.estimate(ctx.messages)

    asyncio.run(comp.compress(ctx, before // 2, _svc()))

    assert _pairing_ok(ctx.messages)


def test_hard_truncation_when_single_group_too_big():
    comp = RollingWindowCompressor()
    msgs = [_sys_msg()]
    msgs.extend(_group(0, size=20_000))  # 单组远超 target
    ctx = _frame(msgs, pinned=["sys-0"]).context

    report = asyncio.run(comp.compress(ctx, 500, _svc()))

    assert _pairing_ok(ctx.messages)
    assert any("[truncated]" in m.content for m in ctx.messages)
    assert report.after_tokens < report.before_tokens


# ---------------------------------------------------------------------------
# 不变量 property 测试(hypothesis)
# ---------------------------------------------------------------------------


@settings(max_examples=40, deadline=None)
@given(
    segments=st.lists(st.integers(min_value=0, max_value=3), min_size=1, max_size=24),
    size=st.integers(min_value=50, max_value=600),
)
def test_compress_invariants_random_history(segments, size):
    """随机消息历史:压缩后配对完好、pinned 永驻、估算单调下降(§7.4)。"""
    est = TokenEstimator()
    comp = RollingWindowCompressor()
    msgs = [_sys_msg()]
    for i, k in enumerate(segments):
        if k == 0:
            msgs.append(Message(role=Role.USER, content="u" * size))
        else:
            msgs.extend(_group(i, k=k, size=size))
    ctx = _frame(msgs, pinned=["sys-0"]).context
    before = est.estimate(ctx.messages)
    target = max(200, before // 4)

    asyncio.run(comp.compress(ctx, target, _svc()))

    assert _pairing_ok(ctx.messages), "tool_call/tool_result 配对被破坏"
    assert any(m.meta.get("id") == "sys-0" for m in ctx.messages), "pinned 被移除"
    after = est.estimate(ctx.messages)
    assert after <= before


# ---------------------------------------------------------------------------
# ContextManager:maintain 触发压缩 / off 短路
# ---------------------------------------------------------------------------


def _manager(reg, *, compress: str = "truncate", status_bar: bool = True):
    bus = _RecordingBus()
    mgr = ContextManager(
        skills=reg,
        tools=LocalPythonToolRegistry(),
        config=RunConfig(compression=compress, max_cost=2.0),
        compressor=RollingWindowCompressor(),
        estimator=TokenEstimator(),
        signals=bus,
        status_bar=status_bar,
        default_max_tokens=4000,
        target_ratio=0.5,
    )
    return mgr, bus


class _RecordingBus:
    def __init__(self) -> None:
        self.seen: list[Signal] = []

    def subscribe(self, pattern, handler) -> None: ...

    async def emit(self, sig: Signal):
        self.seen.append(sig)
        return []


def test_maintain_compresses_oversized_context(tmp_path):
    reg = _registry(tmp_path)
    mgr, bus = _manager(reg)
    msgs = [_sys_msg()]
    for i in range(40):
        msgs.extend(_group(i, size=400))  # ≈ 16k+ tokens,远超 cap 4000
    frame = _frame(msgs, pinned=["sys-0"])

    asyncio.run(mgr.maintain(frame))

    est = TokenEstimator()
    after = est.estimate(frame.context.messages)
    assert after <= 4000 * 0.5 + 400  # 目标水位 + 单组粒度容差
    assert _pairing_ok(frame.context.messages)
    assert any(m.meta.get("id") == "sys-0" for m in frame.context.messages)
    kinds = [s.name for s in bus.seen]
    assert kinds.count(PRE_COMPRESS) == 1 and kinds.count(POST_COMPRESS) == 1


def test_maintain_respects_compression_off(tmp_path):
    reg = _registry(tmp_path)
    mgr, bus = _manager(reg, compress="off")
    msgs = [_sys_msg()]
    for i in range(40):
        msgs.extend(_group(i, size=400))
    frame = _frame(msgs, pinned=["sys-0"])
    before = len(frame.context.messages)

    asyncio.run(mgr.maintain(frame))

    assert len(frame.context.messages) == before
    assert not any(s.name == PRE_COMPRESS for s in bus.seen)


# ---------------------------------------------------------------------------
# 状态注入与前缀稳定性
# ---------------------------------------------------------------------------


def test_status_bar_appended_at_build_and_ephemeral(tmp_path):
    reg = _registry(tmp_path)
    mgr, _ = _manager(reg)
    frame = _frame([Message(role=Role.USER, content="{}")], pinned=[])
    frame.usage.steps = 3
    frame.usage.cost = 0.1

    req = asyncio.run(mgr.build(frame))

    last = req.messages[-1]
    assert last.meta.get("kind") == "status"
    assert "step" in last.content and "3" in last.content
    assert "budget_remaining" in last.content
    # ephemeral:状态消息不写入帧上下文
    assert all(m.meta.get("kind") != "status" for m in frame.context.messages)


def test_status_bar_low_budget_hint(tmp_path):
    reg = _registry(tmp_path)
    mgr, _ = _manager(reg)
    frame = _frame([Message(role=Role.USER, content="{}")])
    frame.usage.cost = 1.9  # 预算 2.0,剩余 5%

    req = asyncio.run(mgr.build(frame))

    last = req.messages[-1]
    assert "收敛" in last.content


def test_prefix_stability_between_builds(tmp_path):
    reg = _registry(tmp_path)
    mgr, _ = _manager(reg)
    frame = _frame([Message(role=Role.USER, content="{}")])

    req1 = asyncio.run(mgr.build(frame))
    req2 = asyncio.run(mgr.build(frame))

    strip = lambda r: [(m.role, m.content) for m in r.messages if m.meta.get("kind") != "status"]
    assert strip(req1) == strip(req2)


# ---------------------------------------------------------------------------
# force_compress(§7.1 外部强制触发)与无 compressor 短路
# ---------------------------------------------------------------------------


def test_force_compress_runs_below_cap(tmp_path):
    """未超 cap 也强制压缩一次;pre:compress 载荷带 forced=True。"""
    reg = _registry(tmp_path)
    mgr, bus = _manager(reg)
    msgs = [_sys_msg(), *_group(0, size=400)]  # 远低于 cap 4000
    frame = _frame(msgs, pinned=["sys-0"])

    asyncio.run(mgr.maintain(frame))
    assert not any(s.name == PRE_COMPRESS for s in bus.seen), "未超限 maintain 不应压缩"

    asyncio.run(mgr.force_compress(frame))
    pre = [s for s in bus.seen if s.name == PRE_COMPRESS]
    post = [s for s in bus.seen if s.name == POST_COMPRESS]
    assert len(pre) == 1 and len(post) == 1
    assert pre[0].payload.get("forced") is True
    assert _pairing_ok(frame.context.messages)


def test_force_compress_respects_compression_off(tmp_path):
    """消融档(compression off)下强制触发仍短路(§7.1)。"""
    reg = _registry(tmp_path)
    mgr, bus = _manager(reg, compress="off")
    frame = _frame([_sys_msg(), *_group(0, size=400)], pinned=["sys-0"])

    asyncio.run(mgr.force_compress(frame))
    assert not any(s.name == PRE_COMPRESS for s in bus.seen)


def test_maintain_without_compressor_updates_estimate_only(tmp_path):
    """无 compressor:静默跳过压缩,但 token 估算照常更新。"""
    reg = _registry(tmp_path)
    mgr = ContextManager(
        skills=reg,
        tools=LocalPythonToolRegistry(),
        config=RunConfig(compression="truncate", max_cost=2.0),
        compressor=None,
        estimator=TokenEstimator(),
        default_max_tokens=100,  # 极低 cap,必然超限
    )
    msgs = [_sys_msg(), *_group(0, size=4000)]
    frame = _frame(msgs)
    before = len(frame.context.messages)

    asyncio.run(mgr.maintain(frame))

    assert len(frame.context.messages) == before
    assert frame.context.token_estimate > 100


# ---------------------------------------------------------------------------
# MinimalContextManager(M0 纵向切片最小实现)
# ---------------------------------------------------------------------------


def test_minimal_manager_builds_same_shape_without_status(tmp_path):
    """组装逻辑与 ContextManager 一致,但无状态注入、maintain 为 no-op。"""
    from agent_os.context.manager import MinimalContextManager

    reg = _registry(tmp_path)
    mgr = MinimalContextManager(
        skills=reg, tools=LocalPythonToolRegistry(), config=RunConfig(model="mock/fallback")
    )
    frame = _frame([Message(role=Role.USER, content="{}")])

    req = asyncio.run(mgr.build(frame))

    assert req.messages[0].role is Role.SYSTEM and req.messages[0].content == "闲聊"
    assert req.model == "mock/x"  # manifest model.prefer 优先于 RunConfig
    assert all(m.meta.get("kind") != "status" for m in req.messages)

    before = list(frame.context.messages)
    asyncio.run(mgr.maintain(frame))  # no-op
    assert frame.context.messages == before
