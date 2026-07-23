"""Context 契约(DESIGN.md §7.5):帧上下文的全权管理——组装 + 压缩 + 前缀缓存稳定性。

组装与压缩必须一家管(§7);不变量见 §7.4(pinned 永驻、tool_call/tool_result
配对原子、压缩后 token 严格下降、压缩发信号、build 输出跨步逐字节稳定)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .frames import FrameContext, SkillFrame
from .providers import ChatRequest
from .tools import BlobStore

__all__ = ["CompressionReport", "Compressor", "ContextManager", "KernelServices"]


@runtime_checkable
class ContextManager(Protocol):
    """§7.5:内核 agent loop 步骤 2/3 的调用点(§3.1)。"""

    async def build(self, frame: SkillFrame) -> ChatRequest:
        """组装请求:指令 + 帧上下文 + 可见 schema + 状态注入 + 来源标注(§7.3)。"""
        ...

    async def maintain(self, frame: SkillFrame) -> None:
        """压缩维护 + 状态注入。"""
        ...


@runtime_checkable
class Compressor(Protocol):
    """压缩策略(责任链可组合,§7.2)。新策略经 entry point ``agent_os.compressors`` 注册。"""

    name: str

    async def compress(
        self, ctx: FrameContext, target_tokens: int, svc: KernelServices
    ) -> CompressionReport: ...


@dataclass
class CompressionReport:
    """§7.5:``{ evicted, before_tokens, after_tokens, cache_invalidation_estimate, marker }``。

    ``marker = "[COMPRESSED]"`` 幂等标记,防责任链多策略重复处理。
    """

    evicted: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    cache_invalidation_estimate: int = 0
    marker: str = ""


@runtime_checkable
class KernelServices(Protocol):
    """§7.5:压缩策略可用的内核服务——token 估算器、ProviderManager(摘要用)、blob store。

    ``estimator`` 与 ``providers`` 为内核侧实现类型(§4.2 / §7.6),
    契约层仅作结构占位,不落具体类以免契约层依赖子系统。
    """

    estimator: Any  # token 估算器(Context 子系统与 ProviderManager 共用同一口径,§4.2)
    providers: Any  # ProviderManager(summarize/narrate 策略的廉价模型调用通道,§7.2)
    blob: BlobStore
