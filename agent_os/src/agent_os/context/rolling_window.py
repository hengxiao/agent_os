"""RollingWindowCompressor(docs/DESIGN.md §7.6;M3)。

保留 pinned + 最近若干原子组,超目标即从最旧整组驱逐;atomic_groups 把
assistant(带 tool_calls)与其全部 tool result 绑成原子组——§7.4 不变量 2 由此
结构性保证。纯函数、无 LLM/blob 依赖,是 hypothesis 不变量测试的载体。

驱逐选择(:func:`select_eviction_groups`)与硬截断(:func:`hard_truncate_group`)
为模块级纯函数,供 summarize 等高级策略复用(§7.2 hierarchical 链的中间层基座)。

定位警告(§7.6):裸 rolling window 是已知循环诱因,只是 hierarchical 链的中间层
基座,链尾必须有 summarize 或 spill 承接——不得读作"推荐做法"。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agent_os.api.v1 import (
    CompressionReport,
    FrameContext,
    KernelServices,
    Message,
    Role,
)

#: 硬截断标注(§7.6 步骤 3)
TRUNCATED = "[truncated]"

#: 幂等标记(§7.5):防责任链多策略重复处理
MARKER = "[COMPRESSED]"


def atomic_groups(messages: list[Message]) -> list[list[Message]]:
    """assistant(带 tool_calls)与其全部 tool result 绑成原子组(§7.6 步骤 1)。

    assistant 消息后续**连续**且 ``tool_call_id`` 匹配的 TOOL 消息入同组;
    其他消息各自成组(裸露的 TOOL 消息也单独成组——防御性,正常路径由
    内核配对写入结构性杜绝,§3.1/§7.4 不变量 2)。
    """
    groups: list[list[Message]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if msg.role is Role.ASSISTANT and msg.tool_calls:
            call_ids = {c.id for c in msg.tool_calls}
            group = [msg]
            i += 1
            while (
                i < n
                and messages[i].role is Role.TOOL
                and messages[i].tool_call_id in call_ids
            ):
                group.append(messages[i])
                i += 1
            groups.append(group)
        else:
            groups.append([msg])
            i += 1
    return groups


def group_is_pinned(group: list[Message], pinned_ids: Iterable[str]) -> bool:
    """组内含 pinned 消息(``m.meta["id"] in pinned``)即视为 pinned 组(§7.4 不变量 1)。"""
    pinned = set(pinned_ids)
    return any(m.meta.get("id") in pinned for m in group)


def select_eviction_groups(
    groups: list[list[Message]],
    pinned_ids: Iterable[str],
    estimator: Any,
    target_tokens: int,
) -> tuple[list[list[Message]], list[list[Message]]]:
    """整组驱逐选择(§7.6 步骤 2):返回 ``(被逐组, 保留组)``,入参 ``groups`` 不被修改。

    从最旧的非 pinned 组起整组驱逐,直到保留估算 <= ``target_tokens`` 或只剩一个
    非 pinned 组——**始终保留最后一个非 pinned 组**,它是硬截断兜底的对象
    (全弹光会让帧丢失全部近期上下文)。
    """
    pinned = set(pinned_ids)
    keep = list(groups)
    evict: list[list[Message]] = []
    total = estimator.estimate([m for g in keep for m in g])
    while total > target_tokens:
        non_pinned = [i for i, g in enumerate(keep) if not group_is_pinned(g, pinned)]
        if len(non_pinned) <= 1:
            break  # 最后一个非 pinned 组留给硬截断兜底
        victim = keep.pop(non_pinned[0])
        evict.append(victim)
        total -= estimator.estimate(victim)
    return evict, keep


def hard_truncate_group(group: list[Message], *, excess_chars: int) -> None:
    """对组内各消息 content 做字符级截断(§7.6 步骤 3),标注 ``[truncated]``。

    按 char/4 口径把 token 超出量折算成字符,从首条有足够内容的消息起依次削;
    截断长度预留标注串,使截断后整体落回目标水位。
    """
    for msg in group:
        if excess_chars <= 0:
            break
        content = msg.content or ""
        if len(content) <= len(TRUNCATED):
            continue  # 截它比留着更贵,跳过(标注本身占字符)
        keep = max(0, len(content) - excess_chars - len(TRUNCATED))
        new = content[:keep] + TRUNCATED
        excess_chars -= len(content) - len(new)
        msg.content = new


class RollingWindowCompressor:
    """``agent_os.api.v1.Compressor`` 协议实现(M3;§7.2 ``truncate`` 策略的基座实现)。"""

    #: §7.2 策略名对齐:注册表键与 post:compress 的 strategy 遥测都以 "truncate" 出现
    name: str = "truncate"

    def atomic_groups(self, messages: list[Message]) -> list[list[Message]]:
        """模块级 :func:`atomic_groups` 的类方法别名(向后兼容既有调用点)。"""
        return atomic_groups(messages)

    async def compress(
        self, ctx: FrameContext, target_tokens: int, svc: KernelServices
    ) -> CompressionReport:
        """整组驱逐 + 硬截断兜底(§7.6 步骤 2/3)。

        - 不变量 1:含 pinned 消息(``m.meta["id"] in ctx.pinned``)的组永不弹;
        - 驱逐从最旧的非 pinned 组开始,但**始终保留最后一个非 pinned 组**——
          它是硬截断兜底的对象(全弹光会让帧丢失全部近期上下文);
        - 驱逐后仍超限 → 对最早保留的非 pinned 组逐消息字符级截断,标注
          ``[truncated]``;只动 content,结构不动,配对不破。
        """
        estimator = svc.estimator
        before = estimator.estimate(ctx.messages)

        evict, remaining = select_eviction_groups(
            atomic_groups(ctx.messages), ctx.pinned, estimator, target_tokens
        )
        evicted = sum(len(g) for g in evict)
        total = estimator.estimate([m for g in remaining for m in g])

        if total > target_tokens:
            idx = next(
                (i for i, g in enumerate(remaining) if not group_is_pinned(g, ctx.pinned)),
                None,
            )
            if idx is not None:
                hard_truncate_group(remaining[idx], excess_chars=(total - target_tokens) * 4)

        ctx.messages[:] = [m for group in remaining for m in group]
        after = estimator.estimate(ctx.messages)
        return CompressionReport(
            evicted=evicted,
            before_tokens=before,
            after_tokens=after,
            cache_invalidation_estimate=before - after,
            marker=MARKER,
        )

    def _hard_truncate(self, group: list[Message], *, excess_chars: int) -> None:
        """向后兼容别名:实现已提为模块级 :func:`hard_truncate_group`。"""
        hard_truncate_group(group, excess_chars=excess_chars)
