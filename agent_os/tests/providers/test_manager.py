"""ProviderManager 弹性锚点测试(DESIGN.md §4.4/§8.4;重试、限流、流式看门狗、fallback 链)。

固定约定:

- 仅 retryable 错误进指数退避(respect retry_after),默认上限 3 次;
- 每 provider 令牌桶限流 ``rate_limits={name: (rate_per_sec, burst)}``;
- ``ProviderManager.stream(req)``:**idle watchdog**——超过 ``stream_idle_timeout``
  秒无新 chunk → 杀流并抛 ``ProviderError(UNAVAILABLE, retryable=True)``;
  重试语义与 chat 一致(仅 retryable、指数退避、max_attempts);
- ``MockProvider(stream_scripts=[[(text, delay), ...], ...])``:每次 stream 尝试按序取一段脚本;
- ``ProviderManager(fallbacks={model: [backup_model, ...]})``:重试耗尽后按链切换,
  请求归一化(只保留契约层字段,剥离前一家专有内容)。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    Message,
    ProviderCaps,
    ProviderError,
    ProviderErrorKind,
    Role,
)
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider


class FlakyProvider:
    """按脚本连续失败的 provider(测试重试语义)。"""

    def __init__(self, failures: list[ProviderError], *, name: str = "flaky") -> None:
        self.name = name
        self.failures = list(failures)
        self.attempts = 0

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(supports_tools=True)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)
        return ChatResponse(message=Message(role=Role.ASSISTANT, content="ok"), finish_reason="stop")

    async def stream(self, req: ChatRequest):
        raise NotImplementedError


class DownProvider:
    """永远 UNAVAILABLE 的 provider。"""

    def __init__(self, name: str = "down") -> None:
        self.name = name
        self.attempts = 0

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(supports_tools=True)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.attempts += 1
        raise ProviderError(ProviderErrorKind.UNAVAILABLE, "down", retryable=True)

    async def stream(self, req: ChatRequest):
        raise NotImplementedError


def _req() -> ChatRequest:
    return ChatRequest(model="flaky/test", messages=[Message(role=Role.USER, content="hi")])


# ---------------------------------------------------------------------------
# 重试与限流
# ---------------------------------------------------------------------------


def test_retry_recovers_from_retryable_errors():
    p = FlakyProvider([
        ProviderError(ProviderErrorKind.RATE_LIMIT, "slow", retryable=True, retry_after=0.01),
        ProviderError(ProviderErrorKind.UNAVAILABLE, "down", retryable=True),
    ])
    mgr = ProviderManager([p], max_attempts=3, backoff_base=0.01)
    resp = asyncio.run(mgr.chat(_req()))
    assert resp.message.content == "ok"
    assert p.attempts == 3


def test_no_retry_on_non_retryable_error():
    p = FlakyProvider([ProviderError(ProviderErrorKind.AUTH, "bad key", retryable=False)])
    mgr = ProviderManager([p], max_attempts=3, backoff_base=0.01)
    with pytest.raises(ProviderError):
        asyncio.run(mgr.chat(_req()))
    assert p.attempts == 1


def test_retry_exhausts_attempts():
    p = FlakyProvider([ProviderError(ProviderErrorKind.RATE_LIMIT, "429", retryable=True)] * 5)
    mgr = ProviderManager([p], max_attempts=3, backoff_base=0.01)
    with pytest.raises(ProviderError):
        asyncio.run(mgr.chat(_req()))
    assert p.attempts == 3


def test_rate_limit_spaces_calls():
    p = FlakyProvider([])
    mgr = ProviderManager([p], rate_limits={"flaky": (5.0, 1)})  # 5 req/s,burst 1

    async def main():
        start = time.monotonic()
        for _ in range(3):
            await mgr.chat(_req())
        return time.monotonic() - start

    elapsed = asyncio.run(main())
    assert elapsed >= 0.35  # 第 2/3 次调用被令牌桶拉开(间隔约 0.2s)


# ---------------------------------------------------------------------------
# 流式 idle watchdog
# ---------------------------------------------------------------------------


def test_stream_idle_watchdog_retries_and_recovers():
    """第一次尝试在第 2 个 chunk 前停滞 0.3s(> 0.1s 看门狗)→ 杀流重试 → 第二次顺畅。"""
    scripts = [
        [("a", 0.0), ("b", 0.3), ("c", 0.0)],
        [("a", 0.0), ("b", 0.0), ("c", 0.0)],
    ]
    mock = MockProvider(stream_scripts=scripts)
    mgr = ProviderManager([mock], max_attempts=2, backoff_base=0.01, stream_idle_timeout=0.1)

    async def main():
        return [c async for c in mgr.stream(ChatRequest(model="mock/x", messages=[]))]

    chunks = asyncio.run(main())
    text = "".join(c.delta.content for c in chunks if c.delta and c.delta.content)
    assert text == "abc"
    assert mock.stream_attempts == 2


def test_stream_idle_watchdog_gives_up_after_max_attempts():
    scripts = [[("a", 0.5)]] * 3
    mock = MockProvider(stream_scripts=scripts)
    mgr = ProviderManager([mock], max_attempts=2, backoff_base=0.01, stream_idle_timeout=0.1)

    async def main():
        return [c async for c in mgr.stream(ChatRequest(model="mock/x", messages=[]))]

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(main())
    assert exc_info.value.kind is ProviderErrorKind.UNAVAILABLE
    assert mock.stream_attempts == 2


# ---------------------------------------------------------------------------
# fallback 链
# ---------------------------------------------------------------------------


def test_provider_fallback_chain():
    """主模型重试耗尽 → 按 fallback 链切到备选 provider(§4.2)。"""
    down = DownProvider()
    backup = MockProvider([
        ChatResponse(message=Message(role=Role.ASSISTANT, content="from-backup"), finish_reason="stop")
    ])
    mgr = ProviderManager(
        [down, backup],
        max_attempts=2,
        backoff_base=0.01,
        fallbacks={"down/main": ["mock/backup"]},
    )
    resp = asyncio.run(mgr.chat(ChatRequest(model="down/main", messages=[Message(role=Role.USER, content="hi")])))
    assert resp.message.content == "from-backup"
    assert down.attempts == 2
    assert len(backup.recorded) == 1
