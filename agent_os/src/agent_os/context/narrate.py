"""NarrateCompressor(docs/DESIGN.md §7.2 narrate;hierarchical 链中、summarize 之前)。

被逐区间(同 rolling 语义)内带 parts 的多模态消息**原地改道**:一次廉价 chat
批量生成旁白(全部多模态消息渲染进一个请求,标注序号,逐条一句),原消息
content 替换为旁白文本、parts 置 None、meta 记 ``narrated=True``——消息不删
(``evicted=0``),parts 折算消失(estimator ``IMAGE_PART_TOKENS``)带来降价;
非多模态消息不动(留给责任链下一棒)。

无 providers / 未配 model / 熔断打开 / 本轮调用失败(含输出畸形)→ 退化占位:
content 落 ``[多模态内容已逐出:{mime} ×N]`` + parts=None + meta
``narrated="fallback"``(不静默丢);连败熔断口径同 summarize(默认 3 次)。
"""

from __future__ import annotations

import json

from agent_os.api.v1 import (
    ChatRequest,
    CompressionReport,
    ContentPart,
    FrameContext,
    KernelServices,
    Message,
    Role,
)
from agent_os.context.rolling_window import atomic_groups, select_eviction_groups
from agent_os.context.summarize import TASK_SPEC_CHARS

#: 幂等标记(§7.5):本轮有多模态消息被改道(旁白或占位)时上报,供责任链聚合;
#: 结构幂等由 parts=None 保证(已改道消息不再入候选),content 不内嵌标记
MARKER = "[NARRATED]"

#: 退化占位模板(无 providers/未配模型/熔断/本轮失败时;不静默丢,meta 记 fallback)
_FALLBACK_TEMPLATE = "[多模态内容已逐出:{desc}]"

#: 单条消息文本投影进 prompt 的截断长度(保 prompt 总量可控,同 summarize 截断精神)
RENDER_CHARS = 1000

#: 旁白 SYSTEM 指令(§7.2:一句旁白留置;输出契约 = JSON 数组,长度与编号对号)
SYSTEM_INSTRUCTION = """\
你是多模态旁白器。USER 给出当前任务规格与若干条编号消息,每条带有图片
(vision 模型拿到真图;否则只见文本投影与图片占位行)。请为每条编号消息写
**一句**中文旁白,概括其中多模态内容对任务的关键信息——图片随后将从上下文
移除,旁白是它的唯一留痕。

要求:
1. 逐条一句,紧扣任务规格;标识符逐字保留(文件名、URL、错误码);
2. 输出 JSON 数组:["旁白0", "旁白1", ...],长度与编号条数一致,顺序与编号一致;
3. 不要寒暄、不要解释、不要代码围栏、不要任何包装。"""


def _fallback_text(parts: list[ContentPart]) -> str:
    """退化占位串:按 mime 聚合计数(插入序确定,同输入必得同串)。"""
    counts: dict[str, int] = {}
    for p in parts:
        counts[p.mime] = counts.get(p.mime, 0) + 1
    desc = ", ".join(f"{mime} ×{n}" for mime, n in counts.items())
    return _FALLBACK_TEMPLATE.format(desc=desc)


def _build_prompt(ctx: FrameContext, mm: list[Message]) -> list[Message]:
    """拼装批量旁白请求:任务规格头 + 每条多模态消息一条编号 USER(携带原 parts)。

    parts 原样随请求走:适配器按 vision 能力序列化(真图或显式占位行,WS1),
    本层不感知 blob / caps 差异;parts 列表逐条拷贝,防改道置 None 污染请求。
    """
    task = next((m.content or "" for m in ctx.messages if m.role is Role.USER), "")
    msgs = [
        Message(role=Role.SYSTEM, content=SYSTEM_INSTRUCTION),
        Message(
            role=Role.USER,
            content=(
                f"帧任务规格:\n{task[:TASK_SPEC_CHARS]}\n\n"
                f"以下 {len(mm)} 条编号消息各带多模态内容,请逐条写旁白。"
            ),
        ),
    ]
    for i, m in enumerate(mm):
        preview = (m.content or "")[:RENDER_CHARS]
        msgs.append(
            Message(
                role=Role.USER,
                content=f"【{i}】原消息 role={m.role.value};文本投影:\n{preview}",
                parts=list(m.parts),
            )
        )
    return msgs


def _parse_narrations(raw: str, n: int) -> list[str] | None:
    """解析旁白输出:JSON 字符串数组、长度对号、逐条非空;畸形一律 ``None``。

    容忍一层代码围栏包装(廉价模型常见),其余包装不猜——宁落占位不错配。
    """
    text = (raw or "").strip()
    if text.startswith("```") and text.endswith("```"):
        inner = text[3:-3]
        inner = inner.removeprefix("json")  # 围栏语言标注行
        text = inner.strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list) or len(data) != n:
        return None
    if not all(isinstance(x, str) and x.strip() for x in data):
        return None
    return [x.strip() for x in data]


class NarrateCompressor:
    """``agent_os.api.v1.Compressor`` 协议实现(§7.2 narrate;hierarchical 链中)。

    ``breaker_threshold`` 次连败后熔断(``_breaker_open``),此后恒退化占位;
    任一轮成功(调用返回且输出合法)即清零连败。输出畸形(非 JSON / 数量不符 /
    空串)视同本轮调用失败——计连败、全区间落占位,口径同 summarize 的 except;
    但已返回响应的 usage 仍落账(tokens 真实花出,预算记账不谎报)。
    """

    name: str = "narrate"

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

    async def compress(
        self, ctx: FrameContext, target_tokens: int, svc: KernelServices
    ) -> CompressionReport:
        """被逐区间内的多模态消息原地改道为文本旁白(不删消息,``evicted=0``)。

        - 候选 = ``select_eviction_groups``(同 rolling 语义)被逐区间内的 parts
          消息;pinned 组永不入被逐区间,天然豁免;已改道(``parts=None``)的消息
          不再入候选(结构幂等);
        - 无候选 → 零值 report、空 marker(责任链聚合不带本段);
        - 改道只降 parts 折算,文本消息不删;仍超目标由链下一棒(truncate /
          summarize)承接——hierarchical 中摘要器拿到的被逐区间已是旁白文本。
        """
        estimator = svc.estimator
        before = estimator.estimate(ctx.messages)
        if before <= target_tokens:
            return CompressionReport(before_tokens=before, after_tokens=before)

        evict, _keep = select_eviction_groups(
            atomic_groups(ctx.messages), ctx.pinned, estimator, target_tokens
        )
        mm = [m for g in evict for m in g if m.parts]
        if not mm:
            return CompressionReport(before_tokens=before, after_tokens=before)

        narrations = await self._try_narrate(ctx, mm, svc)
        if narrations is not None:
            for m, text in zip(mm, narrations, strict=True):
                m.content = text
                m.parts = None
                m.meta["narrated"] = True
        else:
            for m in mm:
                m.content = _fallback_text(m.parts)
                m.parts = None
                m.meta["narrated"] = "fallback"

        after = estimator.estimate(ctx.messages)
        return CompressionReport(
            evicted=0,
            before_tokens=before,
            after_tokens=after,
            cache_invalidation_estimate=before - after,
            marker=MARKER,
        )

    async def _try_narrate(
        self, ctx: FrameContext, mm: list[Message], svc: KernelServices
    ) -> list[str] | None:
        """LLM 旁白路径:不可用(无 providers/未配模型/熔断打开)或本轮失败返回 ``None``。"""
        if svc.providers is None or self._model is None or self._breaker_open:
            return None
        try:
            resp = await svc.providers.chat(
                ChatRequest(
                    model=self._model,
                    messages=_build_prompt(ctx, mm),
                    temperature=self._temperature,
                )
            )
        except Exception:  # noqa: BLE001 — 旁白调用任何失败都退化占位,不阻塞主循环(§7.2)
            self._record_failure()
            return None
        # 响应已返回:usage 先落账(tokens 真实花出);输出畸形另计连败(见类 docstring)
        ctx.working.setdefault("_compress_llm_usage", []).append(
            {"model": self._model, "usage": resp.usage}
        )
        narrations = _parse_narrations(resp.message.content or "", len(mm))
        if narrations is None:
            self._record_failure()
            return None
        self._failures = 0
        return narrations

    def _record_failure(self) -> None:
        """连败计数 + 熔断(口径同 summarize;任一轮成功由 ``_try_narrate`` 清零)。"""
        self._failures += 1
        if self._failures >= self._breaker_threshold:
            self._breaker_open = True
