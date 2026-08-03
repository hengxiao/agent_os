"""RollingWindowCompressor(docs/DESIGN.md §7.6;M3)。

保留 pinned + 最近若干原子组,超目标即从最旧整组驱逐;atomic_groups 把
assistant(带 tool_calls)与其全部 tool result 绑成原子组——§7.4 不变量 2 由此
结构性保证。纯函数、无 LLM/blob 依赖,是 hypothesis 不变量测试的载体。

定位警告(§7.6):裸 rolling window 是已知循环诱因,只是 hierarchical 链的中间层
基座,链尾必须有 summarize 或 spill 承接——不得读作"推荐做法"。
"""

from __future__ import annotations

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


class RollingWindowCompressor:
    """``agent_os.api.v1.Compressor`` 协议实现(M3)。"""

    name: str = "rolling_window"

    def atomic_groups(self, messages: list[Message]) -> list[list[Message]]:
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
        pinned = set(ctx.pinned)

        def is_pinned(group: list[Message]) -> bool:
            return any(m.meta.get("id") in pinned for m in group)

        remaining = self.atomic_groups(ctx.messages)
        total = before
        evicted = 0
        while total > target_tokens:
            non_pinned = [i for i, g in enumerate(remaining) if not is_pinned(g)]
            if len(non_pinned) <= 1:
                break  # 最后一个非 pinned 组留给硬截断兜底
            victim = remaining.pop(non_pinned[0])
            evicted += len(victim)
            total -= estimator.estimate(victim)

        if total > target_tokens:
            idx = next((i for i, g in enumerate(remaining) if not is_pinned(g)), None)
            if idx is not None:
                self._hard_truncate(remaining[idx], excess_chars=(total - target_tokens) * 4)

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
