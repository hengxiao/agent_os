"""OpenAICompatibleProvider 锚点测试(docs/DESIGN.md §4.3;经 httpx.MockTransport,不碰真实网络)。

固定约定:自身不重试;错误映射 429→RATE_LIMIT(读 Retry-After)、401/403→AUTH、
400 且 context length→CONTEXT_OVERFLOW、5xx/超时→UNAVAILABLE(retryable)。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_os.api.v1 import (
    ChatRequest,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    ToolCall,
)
from agent_os.providers.openai_compatible import OpenAICompatibleProvider


def _openai_response(payload, status: int = 200, headers: dict | None = None):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, headers=headers or {})

    return httpx.MockTransport(handler)


def test_openai_compatible_success_mapping():
    payload = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "hi",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "system.file.read", "arguments": '{"path": "a.txt"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    client = httpx.AsyncClient(transport=_openai_response(payload))
    p = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key="k", client=client)
    req = ChatRequest(
        model="openai/gpt-x",
        messages=[Message(role=Role.USER, content="hello")],
        tools=[{"name": "system.file.read", "description": "", "parameters": {}}],
    )
    resp = asyncio.run(p.chat(req))
    assert resp.message.tool_calls[0].name == "system.file.read"
    assert resp.message.tool_calls[0].args == {"path": "a.txt"}
    assert resp.usage.prompt == 3 and resp.usage.completion == 2


def test_openai_compatible_error_mapping():
    cases = [
        (429, {"error": {"message": "rate limited"}}, {"Retry-After": "7"},
         ProviderErrorKind.RATE_LIMIT, True, 7.0),
        (401, {"error": {"message": "bad key"}}, None, ProviderErrorKind.AUTH, False, None),
        (400, {"error": {"message": "This model's maximum context length is 8192 tokens"}}, None,
         ProviderErrorKind.CONTEXT_OVERFLOW, False, None),
        (500, {"error": {"message": "boom"}}, None, ProviderErrorKind.UNAVAILABLE, True, None),
    ]
    for status, payload, headers, kind, retryable, retry_after in cases:
        client = httpx.AsyncClient(transport=_openai_response(payload, status, headers))
        p = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key="k", client=client)
        with pytest.raises(ProviderError) as exc_info:
            asyncio.run(p.chat(ChatRequest(model="openai/gpt-x", messages=[])))
        err = exc_info.value
        assert err.kind is kind, (status, err.kind)
        assert err.retryable is retryable, (status, err.retryable)
        if retry_after is not None:
            assert err.retry_after == retry_after, (status, err.retry_after)


def test_tool_name_wire_format_mangle_round_trip():
    """线格式(providers/naming.py):tools schema 与 assistant 历史发出时 ``.``→``__``;
    响应里 mangled 名解析回点分 canonical。"""
    seen: dict = {}
    payload = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "skill__demo__fib", "arguments": '{"n": 5}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key="k", client=client)

    async def main():
        history = [
            Message(role=Role.USER, content="q"),
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[ToolCall(id="c0", name="system.file.read", args={"path": "a.txt"})],
            ),
            Message(role=Role.TOOL, content="A", tool_call_id="c0", name="system.file.read"),
        ]
        return await p.chat(
            ChatRequest(
                model="openai/gpt-x",
                messages=history,
                tools=[{"name": "skill.demo.fib", "description": "", "parameters": {}}],
            )
        )

    resp = asyncio.run(main())

    body = seen["body"]
    # 出方向:tools schema 与 assistant 历史 tool_calls 都是 mangled
    assert body["tools"][0]["function"]["name"] == "skill__demo__fib"
    assistant = body["messages"][1]
    assert assistant["tool_calls"][0]["function"]["name"] == "system__file__read"
    # 入方向:mangled 响应名解析回点分
    assert resp.message.tool_calls[0].name == "skill.demo.fib"
    assert resp.message.tool_calls[0].args == {"n": 5}


def test_openai_compat_dynamic_key_env(monkeypatch):
    """api_key_env 动态解析:每次调用现读 env(token_refresh 续期即生效);
    装配期快照会让长跑进程 15 分钟后 401(2026-08-24 实证)。"""
    monkeypatch.setenv("TEST_KEY_ENV", "t1")
    p = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key_env="TEST_KEY_ENV")
    assert p.api_key == "t1"
    monkeypatch.setenv("TEST_KEY_ENV", "t2")
    assert p.api_key == "t2", "env 续期后必须现读,不许吃装配期快照"


def test_openai_compat_pinned_key_wins(monkeypatch):
    """显式 api_key 钉死,env 不影响(既有语义)。"""
    monkeypatch.setenv("TEST_KEY_ENV", "env-key")
    p = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key="pinned")
    assert p.api_key == "pinned"
    monkeypatch.setenv("TEST_KEY_ENV", "other")
    assert p.api_key == "pinned"
