"""ProviderManager(DESIGN.md §4.2/§4.4;M1 路由/重试/限流,M5 流式看门狗 + fallback 链)。

前缀路由(``"anthropic/claude-sonnet-4"`` → provider 前缀)+ 指数退避(仅 retryable,
上限 ``max_attempts`` 次,尊重 ``retry_after``)+ 每 provider 令牌桶限流
(``rate_limits={name: (rate_per_sec, burst)}``,无配置不限流)。
流式带 idle watchdog(``stream_idle_timeout`` 秒无新 chunk → 杀流重试);
``fallbacks={model: [backup, ...]}`` 在某 model 重试耗尽后按链切换,
切换时请求归一化(剥离前一家专有内容,§4.2)。
预留:usage 细分记账发信号(``post:llm.response``)。
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
    """内核侧门面(§4.2):模型路由 + 弹性(重试/限流/看门狗/fallback)+ 记账 + token 估算口径。

    本切片:前缀路由 + 指数退避重试 + 令牌桶限流 + 流式 idle watchdog + fallback 链;
    usage 记账发信号后续里程碑(见模块 docstring)。
    """

    def __init__(
        self,
        providers: list[Provider] | None = None,
        *,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        rate_limits: dict[str, tuple[float, int]] | None = None,
        stream_idle_timeout: float = 30.0,
        fallbacks: dict[str, list[str]] | None = None,
        prices: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self.providers: dict[str, Provider] = {}
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.stream_idle_timeout = stream_idle_timeout
        self.fallbacks = {model: list(chain) for model, chain in (fallbacks or {}).items()}
        #: 每百万 token 单价表(§4.2 记账职责在 manager)。键为完整 model 串或
        #: provider 前缀(精确优先);值取 ``{input, output, cache_read?, cache_write?}``。
        #: **价格属配置不属代码**——硬编码的价目表必然过期,且各家各档差异大。
        self.prices = {k: dict(v) for k, v in (prices or {}).items()}
        self._buckets = {
            name: _TokenBucket(rate, burst) for name, (rate, burst) in (rate_limits or {}).items()
        }
        for p in providers or []:
            self.register(p)

    def price_for(self, model: str) -> dict[str, float] | None:
        """取该 model 的单价:完整串精确匹配优先,回落 provider 前缀。"""
        if model in self.prices:
            return self.prices[model]
        prefix, _, _ = model.partition("/")
        return self.prices.get(prefix)

    def _apply_cost(self, model: str, resp: ChatResponse) -> ChatResponse:
        """按单价表折算 ``usage.cost``(§4.2)。

        provider 自报 cost 时不覆盖(有些端点直接给金额);无单价表则保持 0.0——
        此时 ``RunConfig.max_cost`` 与 BudgetGuard 形同虚设,故装配期会告警
        (见 ``runtime.config``),不在此静默假装有护栏。
        """
        usage = resp.usage
        if usage is None or usage.cost:
            return resp
        price = self.price_for(model)
        if not price:
            return resp
        per_mtok = 1_000_000.0
        usage.cost = round(
            (usage.prompt * price.get("input", 0.0)
             + usage.completion * price.get("output", 0.0)
             + usage.cache_read * price.get("cache_read", price.get("input", 0.0))
             + usage.cache_write * price.get("cache_write", price.get("input", 0.0))
             + usage.thinking * price.get("output", 0.0))
            / per_mtok,
            8,
        )
        return resp

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

    def _chain(self, model: str) -> list[str]:
        """fallback 链(§4.2):主 model 在前,备选依序在后;无配置则只有主 model。"""
        return [model, *self.fallbacks.get(model, [])]

    @staticmethod
    def _fallback_request(req: ChatRequest, model: str) -> ChatRequest:
        """切换备选 model 的请求归一化:只保留契约层字段,``extra`` 清空

        (剥离前一家专有格式块,§4.2);messages/tools 已是契约层归一形态,原样透传。
        """
        return ChatRequest(
            model=model,
            messages=req.messages,
            tools=req.tools,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            response_format=req.response_format,
            extra={},
        )

    async def chat(self, req: ChatRequest) -> ChatResponse:
        """路由 → (限流)→ 调用;``ProviderError`` 仅 ``retryable`` 进指数退避(§4.2)。

        退避 ``backoff_base * 2**(attempt-1)``,有 ``retry_after`` 取两者较大;
        非 retryable 直接上抛;单 model 尝试达 ``max_attempts`` 后按 fallback 链
        切下一 model 重走"路由 + 重试",链尾仍失败上抛最后一次错误。
        每次实际调用前取一次令牌(重试也计入限流)。
        """
        chain = self._chain(req.model)
        for i, model in enumerate(chain):
            attempt_req = req if i == 0 else self._fallback_request(req, model)
            try:
                return self._apply_cost(model, await self._chat_with_retries(attempt_req))
            except ProviderError as e:
                if not e.retryable or i == len(chain) - 1:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def _chat_with_retries(self, req: ChatRequest) -> ChatResponse:
        """单 model 的"路由 + 限流 + 指数退避重试"(重试语义见 :meth:`chat`)。"""
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
        """流式 + idle watchdog(N 秒无新 chunk 判定停滞 → 杀流重试,§4.2)。

        逐 chunk ``wait_for`` 取:间隔超 ``stream_idle_timeout`` → 取消该流并记
        ``ProviderError(UNAVAILABLE, retryable=True)``,与 provider 显式抛出的
        retryable 错误走同一套退避/``max_attempts``;非 retryable 直接上抛;
        重试耗尽按 fallback 链切换(同 chat)。单次尝试的 chunk 先缓冲,流完整
        走通才向下游产出——中途停滞重试不会把半截流泄给消费方。
        """
        chain = self._chain(req.model)
        for i, model in enumerate(chain):
            attempt_req = req if i == 0 else self._fallback_request(req, model)
            provider = self.resolve(attempt_req.model)
            bucket = self._buckets.get(provider.name)
            exhausted: ProviderError | None = None
            for attempt in range(1, self.max_attempts + 1):
                if bucket is not None:
                    await bucket.acquire()
                try:
                    chunks = await self._stream_collect(provider, attempt_req)
                except ProviderError as e:
                    if not e.retryable:
                        raise
                    if attempt >= self.max_attempts:
                        exhausted = e
                        break
                    delay = self.backoff_base * 2 ** (attempt - 1)
                    if e.retry_after is not None:
                        delay = max(delay, e.retry_after)
                    await asyncio.sleep(delay)
                else:
                    for chunk in chunks:
                        yield chunk
                    return
            if exhausted is not None and i == len(chain) - 1:
                raise exhausted

    async def _stream_collect(
        self, provider: Provider, req: ChatRequest
    ) -> list[ChatChunk]:
        """单次流式尝试:缓冲消费 provider.stream;idle 超时杀流并抛 UNAVAILABLE(retryable)。"""
        chunks: list[ChatChunk] = []
        ait = provider.stream(req)
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        ait.__anext__(), timeout=self.stream_idle_timeout
                    )
                except StopAsyncIteration:
                    return chunks
                chunks.append(chunk)
        except TimeoutError:
            aclose = getattr(ait, "aclose", None)
            if aclose is not None:
                await aclose()  # 杀流:通知生成器收尾,不泄漏悬挂任务
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE,
                f"stream stalled after {self.stream_idle_timeout}s idle",
                retryable=True,
            ) from None
