"""MockProvider(DESIGN.md §4.4;M0,测试基石)。

脚本化应答(静态 list 或 callable)、请求录制供 golden-file 断言、
可注入故障(429/500/超时/流式停滞)。所有 loop 测试不碰真实 API(§16 测试策略)。
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Callable

from agent_os.api.v1 import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    ProviderCaps,
    ProviderError,
    ProviderErrorKind,
)


class MockProvider:
    """``agent_os.api.v1.Provider`` 协议实现(M0)。

    ``script``:list 按序弹出,或 callable ``(ChatRequest) -> ChatResponse``
    (返回协程亦可)。每次 ``chat`` 录制请求到 ``recorded``。
    """

    name: str = "mock"

    def __init__(
        self,
        script: list[ChatResponse] | Callable[[ChatRequest], ChatResponse] | None = None,
        *,
        name: str | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        self.script = script or []
        self.recorded: list[ChatRequest] = []

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
        raise NotImplementedError("M0")
