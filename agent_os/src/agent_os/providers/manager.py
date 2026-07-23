"""ProviderManager(DESIGN.md §4.2/§4.4;M1;fallback 链 M5)。

前缀路由(``"anthropic/claude-sonnet-4"`` → provider 前缀)+ 指数退避(仅 retryable,
上限 ``max_attempts`` 次,尊重 ``retry_after``)+ 每 provider 令牌桶限流
(``rate_limits={name: (rate_per_sec, burst)}``,无配置不限流)。
预留:usage 细分记账发信号(``post:llm.response``)、流式停滞 idle watchdog、fallback 链(M5)。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from agent_os.api.v1 import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Provider,
    ProviderError,
    ProviderErrorKind,
)


class _TokenBucket:
    """令牌桶(§4.2):按 ``rate`` 补 token,容量 ``burst``;取不到就 sleep 到补足。

    桶内串行(锁内 sleep):同一 provider 的并发调用按到达序放行,间隔自然拉开。
    """

    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.capacity = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self.tokens) / self.rate)


class ProviderManager:
    """内核侧门面(§4.2):模型路由 + 弹性(重试/限流)+ 记账 + token 估算口径。

    本切片:前缀路由 + 指数退避重试 + 令牌桶限流;usage 记账发信号、idle watchdog、
    fallback 链后续里程碑(见模块 docstring)。
    """

    def __init__(
        self,
        providers: list[Provider] | None = None,
        *,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        rate_limits: dict[str, tuple[float, int]] | None = None,
    ) -> None:
        self.providers: dict[str, Provider] = {}
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self._buckets = {
            name: _TokenBucket(rate, burst) for name, (rate, burst) in (rate_limits or {}).items()
        }
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
        """路由 → (限流)→ 调用;``ProviderError`` 仅 ``retryable`` 进指数退避(§4.2)。

        退避 ``backoff_base * 2**(attempt-1)``,有 ``retry_after`` 取两者较大;
        非 retryable 直接上抛;尝试达 ``max_attempts`` 上抛最后一次错误。
        每次实际调用前取一次令牌(重试也计入限流)。
        """
        provider = self.resolve(req.model)
        bucket = self._buckets.get(provider.name)
        for attempt in range(1, self.max_attempts + 1):
            if bucket is not None:
                await bucket.acquire()
            try:
                return await provider.chat(req)
            except ProviderError as e:
                if not e.retryable or attempt >= self.max_attempts:
                    raise
                delay = self.backoff_base * 2 ** (attempt - 1)
                if e.retry_after is not None:
                    delay = max(delay, e.retry_after)
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]:
        """流式 + idle watchdog(N 秒无新 chunk 判定停滞 → 杀流重试,§4.2)。"""
        raise NotImplementedError("M1")
