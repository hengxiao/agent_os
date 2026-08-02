"""KimiProvider 锚点测试(DESIGN.md §4.3;Moonshot Kimi 接口)。

固定约定:

- ``KimiProvider()``:``name == "kimi"``,默认 base_url = ``https://api.moonshot.ai/v1``;
  key 取构造参数,缺省回落 ``MOONSHOT_API_KEY``/``KIMI_API_KEY``;
- 模型路由:``kimi/kimi-k2-thinking`` → 请求体 ``model == "kimi-k2-thinking"``(去前缀);
- 走 OpenAI 兼容协议:URL = ``{base_url}/chat/completions``,``Authorization: Bearer <key>``;
- thinking 模型的 ``reasoning_content`` 映射到 ``Message.reasoning``,下次请求逐字回传;
- 真实接口冒烟测试:设置 ``MOONSHOT_API_KEY``(或 ``KIMI_API_KEY``)后自动启用,
  模型名可用 ``KIMI_SMOKE_MODEL`` 覆盖(默认 ``kimi-k2-thinking``)。
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

from agent_os.api.v1 import ChatRequest, Message, ProviderError, ProviderErrorKind, Role
from agent_os.providers.kimi import DEFAULT_BASE_URL, KimiProvider


def test_kimi_defaults():
    p = KimiProvider(api_key="k")
    assert p.name == "kimi"
    assert p.base_url == DEFAULT_BASE_URL.rstrip("/")
    assert p.api_key == "k"


def test_kimi_key_from_env(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "env-key")
    p = KimiProvider()
    assert p.api_key == "env-key"


def _kimi_payload():
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "答案是 42。",
                "reasoning_content": "用户问了一个问题,需要推理……",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "system__python__exec", "arguments": '{"code": "print(42)"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


def test_kimi_chat_request_and_response_mapping():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("Authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=_kimi_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = KimiProvider(api_key="test-key", client=client)
    req = ChatRequest(
        model="kimi/kimi-k2-thinking",
        messages=[Message(role=Role.USER, content="四十加二?")],
        tools=[{"name": "system.python.exec", "description": "执行代码", "parameters": {}}],
    )
    resp = asyncio.run(p.chat(req))

    assert seen["url"] == f"{DEFAULT_BASE_URL}/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "kimi-k2-thinking"
    # 线格式(providers/naming.py):发出时 mangle 为 __ 分隔,响应解析回点分
    assert seen["body"]["tools"][0]["function"]["name"] == "system__python__exec"

    assert resp.message.tool_calls[0].name == "system.python.exec"
    assert resp.message.tool_calls[0].args == {"code": "print(42)"}
    assert resp.message.reasoning == "用户问了一个问题,需要推理……"
    assert resp.usage.prompt == 11 and resp.usage.completion == 7


def test_kimi_reasoning_round_trip():
    """reasoning 随下次请求逐字回传(Kimi thinking 模型不回传会报错,§4.1)。"""
    bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=_kimi_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = KimiProvider(api_key="k", client=client)

    async def main():
        r1 = await p.chat(ChatRequest(model="kimi/m", messages=[Message(role=Role.USER, content="q")]))
        await p.chat(ChatRequest(model="kimi/m", messages=[r1.message, Message(role=Role.USER, content="q2")]))

    asyncio.run(main())
    assert bodies[1]["messages"][0]["reasoning_content"] == "用户问了一个问题,需要推理……"


def test_kimi_error_mapping():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(429, json={"error": {"message": "rate limit"}}, headers={"Retry-After": "3"})
        )
    )
    p = KimiProvider(api_key="k", client=client)
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(p.chat(ChatRequest(model="kimi/m", messages=[])))
    assert exc_info.value.kind is ProviderErrorKind.RATE_LIMIT
    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after == 3.0


# ---------------------------------------------------------------------------
# 真实接口冒烟(夜间锚点;无 key 自动 skip)
# ---------------------------------------------------------------------------

_API_KEY = os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY")
_SMOKE_MODEL = os.environ.get("KIMI_SMOKE_MODEL", "kimi-k2-thinking")


@pytest.mark.skipif(not _API_KEY, reason="未设置 MOONSHOT_API_KEY/KIMI_API_KEY")
def test_kimi_live_smoke():
    p = KimiProvider()

    async def main():
        return await p.chat(
            ChatRequest(
                model=f"kimi/{_SMOKE_MODEL}",
                messages=[Message(role=Role.USER, content="只回答一个数字:40+2=?")],
                max_tokens=200,
            )
        )

    resp = asyncio.run(main())
    assert "42" in resp.message.content
    assert resp.usage.prompt > 0
