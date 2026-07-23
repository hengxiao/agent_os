"""Provider 契约(DESIGN.md §4.1;§14.1 冻结清单:ProviderCaps 能力位、usage 细分)。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from .messages import Message
from .tools import ToolSchema

__all__ = [
    "ChatChunk",
    "ChatRequest",
    "ChatResponse",
    "ChatUsage",
    "CotProtocol",
    "ModelRouter",
    "Provider",
    "ProviderCaps",
    "ProviderError",
    "ProviderErrorKind",
]

#: 思维链回传协议(§4.1):``"none" | "reasoning_content" | "signed_thinking"``
CotProtocol = Literal["none", "reasoning_content", "signed_thinking"]


@dataclass
class ProviderCaps:
    """能力位(§4.1,逐字冻结)。"""

    supports_tools: bool = False
    supports_vision: bool = False
    supports_json_mode: bool = False
    supports_streaming: bool = False
    supports_logprobs: bool = False  # logprobs:蒸馏/拒绝采样预留
    supports_realtime: bool = False  # 全双工/Omni 类模型
    cot_protocol: CotProtocol = "none"
    cache_billing: bool = False  # 是否报告 cache_read/write 计费
    max_context_tokens: int = 0
    token_counter: Callable[..., int] | None = None  # 可选精确 tokenizer


@dataclass
class ChatUsage:
    """单次响应的 usage 细分(§4.1 ``usage{prompt, completion, cache_read, cache_write, thinking, cost}``)。

    由 ProviderManager 归一化后折算入帧/Run 级 :class:`~agent_os.api.v1.frames.Usage`。
    """

    prompt: int = 0
    completion: int = 0
    cache_read: int = 0
    cache_write: int = 0
    thinking: int = 0
    cost: float = 0.0


@dataclass
class ChatRequest:
    """§4.1:``{ model, messages, tools, temperature, max_tokens, response_format, extra }``。

    ``extra`` 预留"内部事件透传"字段(实时模型话轮事件转发到信号总线)。
    """

    model: str = ""
    messages: list[Message] = field(default_factory=list)
    tools: list[ToolSchema] = field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResponse:
    """§4.1:``{ message, finish_reason, usage, ttft_ms, total_ms, raw }``。

    ``message.reasoning`` 原样保留,下次请求逐字回传。
    """

    message: Message = field(default_factory=Message)
    finish_reason: str = ""
    usage: ChatUsage = field(default_factory=ChatUsage)
    ttft_ms: int = 0
    total_ms: int = 0
    raw: Any = None


@dataclass
class ChatChunk:
    """流式分片(§4.1 ``stream``;``post:llm.chunk`` 信号载体)。"""

    delta: Message | None = None
    finish_reason: str | None = None
    usage: ChatUsage | None = None


class ProviderErrorKind(Enum):
    """§4.1:``RATE_LIMIT | CONTEXT_OVERFLOW | AUTH | UNAVAILABLE | INVALID``。"""

    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    AUTH = "auth"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class ProviderError(Exception):
    """§4.1:``{ kind, retryable, retry_after }``。仅 ``retryable`` 的错误进指数退避。"""

    def __init__(
        self,
        kind: ProviderErrorKind,
        message: str = "",
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.retry_after = retry_after


@runtime_checkable
class Provider(Protocol):
    """§4.1:把"一次 LLM 对话请求"翻译成各家 API,归一化回包。"""

    name: str

    def capabilities(self) -> ProviderCaps: ...

    async def chat(self, req: ChatRequest) -> ChatResponse: ...

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]: ...


@runtime_checkable
class ModelRouter(Protocol):
    """§4.2 预留扩展点:输入任务特征,输出模型与参数。v1 以静态 ``prefer`` 为默认实现。"""

    async def route(
        self, req: ChatRequest, prefer: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        """返回 ``(model, params)``。"""
        ...
