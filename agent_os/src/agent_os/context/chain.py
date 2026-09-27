"""ChainCompressor(docs/DESIGN.md §7.2 hierarchical):压缩器责任链。

按序组合多档策略(manifest 默认 ``spill → truncate → summarize``);逐阶段执行,
当前估算 <= target 即短路;report 聚合各阶段(evicted 累加、非空 marker 以
``+`` 连接,链名同形)。
"""

from __future__ import annotations

from agent_os.api.v1 import (
    CompressionReport,
    Compressor,
    FrameContext,
    KernelServices,
)


class ChainCompressor:
    """``agent_os.api.v1.Compressor`` 协议实现(§7.2 hierarchical 责任链)。"""

    def __init__(self, stages: list[Compressor]) -> None:
        self.stages = list(stages)
        self.name = "+".join(s.name for s in self.stages)

    async def compress(
        self, ctx: FrameContext, target_tokens: int, svc: KernelServices
    ) -> CompressionReport:
        """逐阶段压缩:达标即短路,后续阶段不再执行。"""
        estimator = svc.estimator
        before = estimator.estimate(ctx.messages)
        evicted = 0
        markers: list[str] = []
        for stage in self.stages:
            if estimator.estimate(ctx.messages) <= target_tokens:
                break
            r = await stage.compress(ctx, target_tokens, svc)
            evicted += r.evicted
            if r.marker:
                markers.append(r.marker)
        after = estimator.estimate(ctx.messages)
        return CompressionReport(
            evicted=evicted,
            before_tokens=before,
            after_tokens=after,
            cache_invalidation_estimate=before - after,
            marker="+".join(markers),
        )
