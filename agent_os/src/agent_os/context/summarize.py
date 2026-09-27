"""SummarizeCompressor(docs/DESIGN.md §7.2 summarize;hierarchical 链尾承接)。

经 ProviderManager 用廉价模型把被逐区间摘要成一条 compact note(**context-aware**:
压缩 prompt 含帧任务规格与 pinned 常驻约束,保留契约逐字写进 SYSTEM 指令)。
连败熔断(默认 3 次)后退化纯 truncate——宁可丢信息也不阻塞主循环。

note 是普通 SYSTEM 消息、**非 pinned**,可被后续压缩再逐——有意为之:摘要本身
也会过时,随窗口前移最终被 rolling 兜底收编,不该永驻。
"""

from __future__ import annotations

import json

from agent_os.api.v1 import (
    ChatRequest,
    CompressionReport,
    FrameContext,
    KernelServices,
    Message,
    Role,
)
from agent_os.context.rolling_window import (
    MARKER,
    atomic_groups,
    group_is_pinned,
    hard_truncate_group,
    select_eviction_groups,
)

#: 退化 truncate 标记:无 providers/未配模型/熔断打开或本轮 LLM 失败时整组丢弃、
#: 不生成 note;与 MARKER 区分,便于责任链聚合与遥测识别降级
TRUNCATE_MARKER = "[COMPRESSED:truncate]"

#: 帧任务规格(首条 USER 消息)截断长度
TASK_SPEC_CHARS = 1000
#: pinned 常驻约束单条截断长度
PINNED_CHARS = 500
#: 被逐区间单条渲染截断长度(保 prompt 总量可控)
EVICT_RENDER_CHARS = 2000

#: context-aware SYSTEM 指令(§7.2:保留契约逐字写入,输出紧凑纯文本笔记)
SYSTEM_INSTRUCTION = """\
你是上下文压缩器。下面 USER 消息给出当前任务规格、常驻约束,以及一段将被移除的
历史消息区间;请把该区间压缩成一份紧凑的纯文本笔记,供后续步骤继续任务。

保留契约(以下内容必须逐字保留,不可摘要、不可改写):
1. 架构决策与关键约束——逐字保留,不可摘要;
2. 已修改文件清单——逐字保留文件路径;
3. 验证状态——哪些测试通过/未通过,逐字保留;
4. 未完成 TODO——逐字保留;
5. 标识符逐字保留:UUID、hash、URL、文件路径、技能名。

输出要求:直接输出紧凑的纯文本笔记正文;不要寒暄、不要解释、不要任何包装。"""

#: USER 正文分隔线(被逐区间的起点)
_DIVIDER = "—————— 以下为将被摘要移除的消息区间 ——————"


def _render_message(m: Message) -> str:
    """被逐区间单条渲染:``role`` + content;tool_calls 渲染为 ``name(args-json)``。"""
    head = f"[{m.role.value}]"
    if m.name:
        head += f" {m.name}"
    body = m.content or ""
    if m.tool_calls:
        calls = "; ".join(
            f"{c.name}({json.dumps(c.args, ensure_ascii=False, sort_keys=True)})"
            for c in m.tool_calls
        )
        body = f"{body}\n调用: {calls}" if body else f"调用: {calls}"
    text = f"{head} {body}"
    if len(text) > EVICT_RENDER_CHARS:
        text = text[:EVICT_RENDER_CHARS] + " …[截断]"
    return text


def _build_prompt(ctx: FrameContext, evict: list[list[Message]]) -> str:
    """拼装 USER 正文:帧任务规格 + pinned 常驻约束 + 分隔线 + 被逐区间逐条渲染。"""
    task = next((m.content or "" for m in ctx.messages if m.role is Role.USER), "")
    parts = [f"帧任务规格:\n{task[:TASK_SPEC_CHARS]}"]
    pinned_msgs = [m for m in ctx.messages if m.meta.get("id") in set(ctx.pinned)]
    if pinned_msgs:
        parts.append("常驻约束(pinned 消息,保留契约对其同样适用):")
        parts.extend(f"- {(m.content or '')[:PINNED_CHARS]}" for m in pinned_msgs)
    parts.append(_DIVIDER)
    for group in evict:
        parts.extend(_render_message(m) for m in group)
    return "\n\n".join(parts)


class SummarizeCompressor:
    """``agent_os.api.v1.Compressor`` 协议实现(§7.2 summarize;hierarchical 链尾)。

    ``breaker_threshold`` 次连败后熔断(``_breaker_open``),此后恒退化 truncate;
    任一轮成功即清零连败计数。note id 取实例级自增序号(``compress-note-<n>``),
    实例内唯一、不与 pinned 冲突。
    """

    name: str = "summarize"

    def __init__(
        self,
        model: str | None = None,
        breaker_threshold: int = 3,
        temperature: float = 0.2,
    ) -> None:
        self._model = model
        self._breaker_threshold = breaker_threshold
        self._temperature = temperature
        self._failures = 0
        self._breaker_open = False
        self._note_seq = 0

    async def compress(
        self, ctx: FrameContext, target_tokens: int, svc: KernelServices
    ) -> CompressionReport:
        """被逐区间 → LLM 摘要 note;不可用时退化整组丢弃(纯 truncate)。"""
        estimator = svc.estimator
        before = estimator.estimate(ctx.messages)
        if before <= target_tokens:
            return CompressionReport(before_tokens=before, after_tokens=before)

        groups = atomic_groups(ctx.messages)
        evict, keep = select_eviction_groups(groups, ctx.pinned, estimator, target_tokens)
        evicted = sum(len(g) for g in evict)

        if not evict:
            # 无可逐组(只剩一个非 pinned 组):硬截断兜底,同 rolling
            _hard_truncate_fallback(keep, ctx.pinned, before, target_tokens)
            ctx.messages[:] = [m for g in keep for m in g]
            after = estimator.estimate(ctx.messages)
            return CompressionReport(
                before_tokens=before,
                after_tokens=after,
                cache_invalidation_estimate=before - after,
                marker=MARKER,
            )

        note = await self._try_summarize(ctx, evict, svc)
        marker = MARKER if note is not None else TRUNCATE_MARKER

        # 重组:整组删除被逐区间;LLM 成功时在首个被逐组的下标位置插入 note
        evict_ids = {id(g) for g in evict}
        new_groups: list[list[Message]] = []
        note_inserted = False
        for g in groups:
            if id(g) in evict_ids:
                if note is not None and not note_inserted:
                    new_groups.append([self._note_message(note, evicted)])
                    note_inserted = True
            else:
                new_groups.append(g)

        total = estimator.estimate([m for g in new_groups for m in g])
        if total > target_tokens:
            # 保留组仍超限:对最早非 pinned 保留组硬截断兜底(同 rolling)
            _hard_truncate_fallback(keep, ctx.pinned, total, target_tokens)

        ctx.messages[:] = [m for g in new_groups for m in g]
        after = estimator.estimate(ctx.messages)
        return CompressionReport(
            evicted=evicted,
            before_tokens=before,
            after_tokens=after,
            cache_invalidation_estimate=before - after,
            marker=marker,
        )

    async def _try_summarize(
        self, ctx: FrameContext, evict: list[list[Message]], svc: KernelServices
    ) -> str | None:
        """LLM 摘要路径:不可用(无 providers/未配模型/熔断打开)或失败返回 ``None``。"""
        if svc.providers is None or self._model is None or self._breaker_open:
            return None
        prompt = [
            Message(role=Role.SYSTEM, content=SYSTEM_INSTRUCTION),
            Message(role=Role.USER, content=_build_prompt(ctx, evict)),
        ]
        try:
            resp = await svc.providers.chat(
                ChatRequest(
                    model=self._model, messages=prompt, temperature=self._temperature
                )
            )
        except Exception:  # noqa: BLE001 — 摘要调用任何失败都退化 truncate,不阻塞主循环(§7.2)
            self._failures += 1
            if self._failures >= self._breaker_threshold:
                self._breaker_open = True
            return None
        self._failures = 0
        ctx.working.setdefault("_compress_llm_usage", []).append(
            {"model": self._model, "usage": resp.usage}
        )
        return resp.message.content

    def _note_message(self, note: str, evicted: int) -> Message:
        """摘要 note:非 pinned 普通 SYSTEM 消息(可被后续压缩再逐,有意为之)。"""
        msg = Message(
            role=Role.SYSTEM,
            content=f"{MARKER} 早前 {evicted} 条消息的摘要:\n{note}",
            meta={"id": f"compress-note-{self._note_seq}", "compressed": True},
        )
        self._note_seq += 1
        return msg


def _hard_truncate_fallback(
    keep: list[list[Message]], pinned_ids: list[str], total: int, target_tokens: int
) -> None:
    """对最早保留的非 pinned 组硬截断兜底(同 rolling §7.6 步骤 3)。"""
    if total <= target_tokens:
        return
    idx = next((i for i, g in enumerate(keep) if not group_is_pinned(g, pinned_ids)), None)
    if idx is not None:
        hard_truncate_group(keep[idx], excess_chars=(total - target_tokens) * 4)
