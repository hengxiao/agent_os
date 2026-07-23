"""MockProvider(DESIGN.md §4.4;M0,测试基石)。

脚本化应答(静态 list 或 callable)、请求录制供 golden-file 断言、
可注入故障(429/500/超时/流式停滞)。所有 loop 测试不碰真实 API(§16 测试策略)。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable

from agent_os.api.v1 import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
    ProviderCaps,
    ProviderError,
    ProviderErrorKind,
    Role,
)


class MockProvider:
    """``agent_os.api.v1.Provider`` 协议实现(M0)。

    ``script``:list 按序弹出,或 callable ``(ChatRequest) -> ChatResponse``
    (返回协程亦可)。每次 ``chat`` 录制请求到 ``recorded``。
    ``stream_scripts``:每项是一段 ``[(text, delay), ...]`` 流式脚本,
    每次 ``stream`` 调用按序弹出一段(``stream_attempts`` 计数)。
    """

    name: str = "mock"

    def __init__(
        self,
        script: list[ChatResponse] | Callable[[ChatRequest], ChatResponse] | None = None,
        *,
        name: str | None = None,
        stream_scripts: list[list[tuple[str, float]]] | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        self.script = script or []
        self.recorded: list[ChatRequest] = []
        self.stream_scripts = stream_scripts
        self.stream_attempts = 0

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(supports_tools=True)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.recorded.append(req)
        if callable(self.script):
            resp = self.script(req)
            if inspect.isawaitable(resp):
                resp = await resp
            return resp
        if not self.script:
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE,
                "MockProvider 脚本已耗尽(list 为空且非 callable)",
            )
        return self.script.pop(0)

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]:
        """流式脚本(M5):每段 ``[(text, delay), ...]`` 逐项 sleep 后 yield chunk。

        未配 ``stream_scripts`` 保持 NotImplementedError;脚本耗尽(列表取空)→
        ``ProviderError(UNAVAILABLE, retryable=True)``(与 chat 耗尽口径一致,可进重试)。
        """
        if self.stream_scripts is None:
            raise NotImplementedError("M0")
        self.stream_attempts += 1
        if not self.stream_scripts:
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE,
                "MockProvider 流式脚本已耗尽",
                retryable=True,
            )
        for text, delay in self.stream_scripts.pop(0):
            await asyncio.sleep(delay)
            yield ChatChunk(delta=Message(role=Role.ASSISTANT, content=text))
