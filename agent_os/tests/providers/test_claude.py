"""ClaudeProvider 锚点测试(DESIGN.md §4.3;Anthropic Messages API 独立适配器)。

固定约定(Anthropic Messages API 与 OpenAI 格式差异):

- ``ClaudeProvider()``:``name == "anthropic"``,默认 base_url = ``https://api.anthropic.com``,
  key 取构造参数或 ``ANTHROPIC_API_KEY``;端点 = ``{base_url}/v1/messages``;
  请求头 = ``x-api-key`` + ``anthropic-version``;
- 模型路由:``anthropic/claude-sonnet-4`` → 请求体 ``model == "claude-sonnet-4"``(去前缀);
- **system 顶层独立**:Role.SYSTEM 消息合成 body 顶层 ``system`` 字符串,不进 messages;
- ``max_tokens`` 必填:req.max_tokens 为 None 时用 provider 默认;
- 工具:``{name, description, input_schema}``;assistant 的 tool_calls → ``tool_use`` 块;
  TOOL 消息 → ``tool_result`` 块,**连续的 TOOL 消息合并进同一条 user 消息**;
- 响应:content blocks(text/thinking/tool_use)→ Message(content/tool_calls/reasoning),
  thinking 块的 ``signature`` 存 ``Message.meta["signature"]`` 并随请求回传;
  usage 含 ``cache_read_input_tokens``/``cache_creation_input_tokens`` → ChatUsage.cache_read/write;
  ``stop_reason`` → finish_reason;
- 错误:429 → RATE_LIMIT(读 retry-after);401/403 → AUTH;
  400 且 prompt too long → CONTEXT_OVERFLOW;529(overloaded)/5xx → UNAVAILABLE(retryable);
- 真实接口冒烟:``ANTHROPIC_API_KEY`` 存在时启用,模型用 ``CLAUDE_SMOKE_MODEL`` 覆盖。
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

from agent_os.api.v1 import (
    ChatRequest,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
)
from agent_os.providers.claude import ClaudeProvider


def _payload(*, blocks, stop_reason="end_turn", usage=None):
    return {
        "id": "msg_1",
        "role": "assistant",
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": usage or {
            "input_tokens": 20,
            "output_tokens": 9,
            "cache_read_input_tokens": 15,
            "cache_creation_input_tokens": 4,
        },
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_claude_defaults():
    p = ClaudeProvider(api_key="k")
    assert p.name == "anthropic"
    assert p.base_url == "https://api.anthropic.com"
    assert p.api_key == "k"


def test_claude_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert ClaudeProvider().api_key == "env-key"


def test_request_mapping_system_tools_max_tokens():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["headers"] = req.headers
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=_payload(blocks=[{"type": "text", "text": "好"}]))

    p = ClaudeProvider(api_key="sk-ant", client=_client(handler))
    req = ChatRequest(
        model="anthropic/claude-sonnet-4",
        messages=[
            Message(role=Role.SYSTEM, content="你是助手。"),
            Message(role=Role.USER, content="你好"),
        ],
        tools=[{"name": "system.file.read", "description": "读文件", "parameters": {"type": "object"}}],
    )
    asyncio.run(p.chat(req))

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant"
    assert seen["headers"]["anthropic-version"]
    body = seen["body"]
    assert body["model"] == "claude-sonnet-4"
    assert body["system"] == "你是助手。"
    assert all(m["role"] != "system" for m in body["messages"])
    assert body["max_tokens"] > 0  # 必填,缺省有默认
    assert body["tools"][0]["input_schema"] == {"type": "object"}
    assert body["tools"][0]["name"] == "system.file.read"


def test_response_mapping_thinking_usage_stop_reason():
    blocks = [
        {"type": "thinking", "thinking": "先推理一下……", "signature": "sig-abc"},
        {"type": "text", "text": "答案是 42。"},
    ]

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload(blocks=blocks))

    p = ClaudeProvider(api_key="k", client=_client(handler))
    resp = asyncio.run(p.chat(ChatRequest(model="anthropic/m", messages=[Message(role=Role.USER, content="q")])))

    assert resp.message.content == "答案是 42。"
    assert resp.message.reasoning == "先推理一下……"
    assert resp.message.meta.get("signature") == "sig-abc"
    assert resp.usage.prompt == 20 and resp.usage.completion == 9
    assert resp.usage.cache_read == 15 and resp.usage.cache_write == 4
    assert resp.finish_reason == "end_turn"


def test_tool_use_response_and_tool_result_round_trip():
    """tool_use → ToolCall;assistant 回传 tool_use 块;连续 TOOL 合并为一条 user(§4 契约)。"""
    blocks = [
        {"type": "text", "text": "调用工具"},
        {"type": "tool_use", "id": "tu_1", "name": "system.file.read", "input": {"path": "a.txt"}},
        {"type": "tool_use", "id": "tu_2", "name": "system.file.read", "input": {"path": "b.txt"}},
    ]
    bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=_payload(blocks=blocks, stop_reason="tool_use"))

    p = ClaudeProvider(api_key="k", client=_client(handler))

    async def main():
        r1 = await p.chat(ChatRequest(model="anthropic/m", messages=[Message(role=Role.USER, content="q")]))
        history = [
            Message(role=Role.USER, content="q"),
            r1.message,
            Message(role=Role.TOOL, content='{"ok": true, "value": "A"}', tool_call_id="tu_1", name="system.file.read"),
            Message(role=Role.TOOL, content='{"ok": true, "value": "B"}', tool_call_id="tu_2", name="system.file.read"),
        ]
        await p.chat(ChatRequest(model="anthropic/m", messages=history))

    asyncio.run(main())

    second = bodies[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    tool_use_blocks = [b for b in second[1]["content"] if b["type"] == "tool_use"]
    assert [b["name"] for b in tool_use_blocks] == ["system.file.read", "system.file.read"]
    assert tool_use_blocks[0]["input"] == {"path": "a.txt"}
    # 连续 TOOL 消息合并为一条 user 消息,内含两个 tool_result 块
    result_blocks = second[2]["content"]
    assert isinstance(result_blocks, list) and len(result_blocks) == 2
    assert all(b["type"] == "tool_result" for b in result_blocks)
    assert result_blocks[0]["tool_use_id"] == "tu_1"


def test_error_mapping():
    cases = [
        (429, {"error": {"message": "rate limited"}}, {"retry-after": "5"},
         ProviderErrorKind.RATE_LIMIT, True, 5.0),
        (401, {"error": {"message": "bad key"}}, None, ProviderErrorKind.AUTH, False, None),
        (400, {"error": {"message": "prompt is too long: 200001 tokens > 200000"}}, None,
         ProviderErrorKind.CONTEXT_OVERFLOW, False, None),
        (529, {"error": {"message": "overloaded"}}, None, ProviderErrorKind.UNAVAILABLE, True, None),
    ]
    for status, payload, headers, kind, retryable, retry_after in cases:
        client = _client(lambda req, s=status, p=payload, h=headers: httpx.Response(s, json=p, headers=h or {}))
        p = ClaudeProvider(api_key="k", client=client)
        with pytest.raises(ProviderError) as exc_info:
            asyncio.run(p.chat(ChatRequest(model="anthropic/m", messages=[])))
        err = exc_info.value
        assert err.kind is kind, (status, err.kind)
        assert err.retryable is retryable, (status, err.retryable)
        if retry_after is not None:
            assert err.retry_after == retry_after, (status, err.retry_after)


# ---------------------------------------------------------------------------
# 真实接口冒烟(无 key 自动 skip)
# ---------------------------------------------------------------------------

_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
_SMOKE_MODEL = os.environ.get("CLAUDE_SMOKE_MODEL", "claude-sonnet-4-20250514")


@pytest.mark.skipif(not _API_KEY, reason="未设置 ANTHROPIC_API_KEY")
def test_claude_live_smoke():
    p = ClaudeProvider()

    async def main():
        return await p.chat(
            ChatRequest(
                model=f"anthropic/{_SMOKE_MODEL}",
                messages=[Message(role=Role.USER, content="只回答一个数字:40+2=?")],
                max_tokens=64,
            )
        )

    resp = asyncio.run(main())
    assert "42" in resp.message.content
    assert resp.usage.prompt > 0
