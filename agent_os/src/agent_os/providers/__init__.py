"""Providers 子系统(docs/DESIGN.md §4):把"一次 LLM 对话请求"翻译成各家 API,归一化回包。"""

from .claude import ClaudeProvider
from .kimi import KimiProvider
from .manager import ProviderManager
from .mock import MockProvider
from .openai_compatible import OpenAICompatibleProvider

__all__ = [
    "ClaudeProvider",
    "KimiProvider",
    "MockProvider",
    "OpenAICompatibleProvider",
    "ProviderManager",
]
