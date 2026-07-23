"""ProviderManager 基础版(DESIGN.md §4.2/§4.4;M1;fallback 链 M5)。

前缀路由(``"anthropic/claude-sonnet-4"`` → provider 前缀)+ 指数退避(仅 retryable,
上限 3 次,长任务可配大)+ usage 细分记账发信号(``post:llm.response``)。
预留:每 provider 令牌桶限流、流式停滞 idle watchdog、fallback 链(M5)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent_os.api.v1 import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Provider,
    ProviderError,
    ProviderErrorKind,
)


class ProviderManager:
    """内核侧门面(§4.2):模型路由 + 弹性 + 记账 + token 估算口径。

    本切片只做前缀路由;重试/限流/fallback 属 M1+(见模块 docstring)。
    """

    def __init__(self, providers: list[Provider] | None = None) -> None:
        self.providers: dict[str, Provider] = {}
        for p in providers or []:
            self.register(p)

    def register(self, provider: Provider) -> None:
        """注册 provider(前缀路由键 = provider.name,§4.2)。"""
        self.providers[provider.name] = provider

    def resolve(self, model: str) -> Provider:
        """``"<provider>/<model>"`` 前缀路由。"""
        prefix, _, _ = model.partition("/")
        try:
            return self.providers[prefix]
        except KeyError:
            raise ProviderError(
                ProviderErrorKind.INVALID,
                f"模型 {model!r} 的 provider 前缀 {prefix!r} 未注册",
            ) from None

    async def chat(self, req: ChatRequest) -> ChatResponse:
        """内含重试、限流、记账(§3.1 步骤 4)——本切片无重试(M1 再做)。"""
        return await self.resolve(req.model).chat(req)

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]:
        """流式 + idle watchdog(N 秒无新 chunk 判定停滞 → 杀流重试,§4.2)。"""
        raise NotImplementedError("M1")
