"""OpenAICompatibleProvider(DESIGN.md §4.4;M1,唯一必须的真适配器)。

一个类覆盖所有 OpenAI 兼容端点(官方/Azure/vLLM/Ollama/网关);依赖仅 httpx。
错误映射:429→RATE_LIMIT(读 Retry-After)、401/403→AUTH、400 且 context length→
CONTEXT_OVERFLOW、5xx/超时→UNAVAILABLE;自身不重试;reasoning 字段 round-trip。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_os.api.v1 import ChatChunk, ChatRequest, ChatResponse, ProviderCaps


class OpenAICompatibleProvider:
    """``agent_os.api.v1.Provider`` 协议实现(M1)。"""

    name: str = "openai"

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        *,
        name: str | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        self.base_url = base_url
        self.api_key = api_key

    def capabilities(self) -> ProviderCaps:
        raise NotImplementedError("M1")

    async def chat(self, req: ChatRequest) -> ChatResponse:
        raise NotImplementedError("M1")

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]:
        raise NotImplementedError("M1")
