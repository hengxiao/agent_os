"""Context 子系统(docs/DESIGN.md §7;M3):帧上下文的全权管理——组装 + 压缩 + 前缀缓存稳定性。"""

from .chain import ChainCompressor
from .estimator import TokenEstimator
from .manager import ContextManager, ContextOverflowError
from .rolling_window import RollingWindowCompressor
from .spill import SpillCompressor
from .summarize import SummarizeCompressor

__all__ = [
    "ChainCompressor",
    "ContextManager",
    "ContextOverflowError",
    "RollingWindowCompressor",
    "SpillCompressor",
    "SummarizeCompressor",
    "TokenEstimator",
]
