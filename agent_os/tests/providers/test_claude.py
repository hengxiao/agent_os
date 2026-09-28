"""ClaudeProvider 锚点测试(docs/DESIGN.md §4.3;Anthropic Messages API 独立适配器)。

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
import base64
import json
import os

import httpx
import pytest

from agent_os.api.v1 import (
    ChatRequest,
    ContentPart,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
)
from agent_os.providers.claude import ClaudeProvider
from agent_os.tools.blob import InMemoryBlobStore


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
    # 线格式:点分 canonical 名发出时 mangle 为 __ 分隔(providers/naming.py)
    assert body["tools"][0]["name"] == "system__file__read"


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
    """tool_use → ToolCall;assistant 回传 tool_use 块;连续 TOOL 合并为一条 user(§4 契约)。

    线格式:API 返回 mangled 名(``__`` 分隔),解析回点分;assistant 历史回传时再 mangle。
    """
    blocks = [
        {"type": "text", "text": "调用工具"},
        {"type": "tool_use", "id": "tu_1", "name": "system__file__read", "input": {"path": "a.txt"}},
        {"type": "tool_use", "id": "tu_2", "name": "system__file__read", "input": {"path": "b.txt"}},
    ]
    bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=_payload(blocks=blocks, stop_reason="tool_use"))

    p = ClaudeProvider(api_key="k", client=_client(handler))

    async def main():
        r1 = await p.chat(ChatRequest(model="anthropic/m", messages=[Message(role=Role.USER, content="q")]))
        assert [tc.name for tc in r1.message.tool_calls] == ["system.file.read", "system.file.read"]
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
    assert [b["name"] for b in tool_use_blocks] == ["system__file__read", "system__file__read"]
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
# 流式(SSE):Anthropic 事件序列;thinking+signature ⇄ reasoning+meta["signature"];
# tool_use input_json 分片按块累计、stop 时解析;usage 汇聚进终 chunk
# ---------------------------------------------------------------------------


def _sse_payload(*events) -> bytes:
    """事件 dict 序列 → SSE 字节流(Anthropic 无 [DONE],message_stop 收尾)。"""
    return "".join(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n" for ev in events).encode()


def _sse_client(payload: bytes, *, seen: dict | None = None) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})

    return _client(handler)


def _stream_req() -> ChatRequest:
    return ChatRequest(model="anthropic/m", messages=[Message(role=Role.USER, content="q")])


async def _collect(p: ClaudeProvider, req: ChatRequest):
    return [c async for c in p.stream(req)]


_MESSAGE_START = {
    "type": "message_start",
    "message": {"id": "msg_1", "usage": {
        "input_tokens": 20, "output_tokens": 1,
        "cache_read_input_tokens": 15, "cache_creation_input_tokens": 4,
    }},
}


def test_stream_event_sequence_text_and_usage():
    """message_start(input usage)→ text delta 按序透传 → message_delta(stop+output usage)
    → message_stop 交付终 chunk;ping/error 旁路;请求体带 stream=True。"""
    events = [
        _MESSAGE_START,
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "ping"},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "答案"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "是 42"}},
        {"type": "error", "error": {"type": "ping_missing", "message": "旁路不炸"}},  # error 事件旁路
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    seen: dict = {}
    p = ClaudeProvider(api_key="k", client=_sse_client(_sse_payload(*events), seen=seen))
    chunks = asyncio.run(_collect(p, _stream_req()))

    assert [c.delta.content for c in chunks if c.delta and c.delta.content] == ["答案", "是 42"]
    final = chunks[-1]
    assert final.delta is None
    assert final.finish_reason == "end_turn"
    # usage 从 message_start/message_delta 汇聚(与 _map_response 同口径)
    assert final.usage.prompt == 20 and final.usage.completion == 9
    assert final.usage.cache_read == 15 and final.usage.cache_write == 4
    assert seen["body"]["stream"] is True


def test_stream_thinking_signature_block():
    """thinking_delta → delta.reasoning 按序透传;signature_delta 按块累计,
    块 stop 时经 delta.meta["signature"] 交付(与 _map_response 同形态,供 round-trip)。"""
    events = [
        _MESSAGE_START,
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "先推"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "理"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig-"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "abc"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    p = ClaudeProvider(api_key="k", client=_sse_client(_sse_payload(*events)))
    chunks = asyncio.run(_collect(p, _stream_req()))

    assert [c.delta.reasoning for c in chunks if c.delta and c.delta.reasoning] == ["先推", "理"]
    sig_chunks = [c for c in chunks if c.delta and c.delta.meta.get("signature")]
    assert len(sig_chunks) == 1
    assert sig_chunks[0].delta.meta["signature"] == "sig-abc"
    # 推理文本分片不携带 signature(形态对齐 _map_response:reasoning 与 meta 分离)
    assert all(not c.delta.meta for c in chunks if c.delta and c.delta.reasoning)


def test_stream_tool_use_input_json_assembly():
    """tool_use 的 input_json 分片按块累计,content_block_stop 时解析 + unmangle,
    完整 ToolCall 一次交付;stop_reason=tool_use → finish_reason。"""
    events = [
        _MESSAGE_START,
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu_1", "name": "system__file__read"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"path":'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": ' "a.txt"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]
    p = ClaudeProvider(api_key="k", client=_sse_client(_sse_payload(*events)))
    chunks = asyncio.run(_collect(p, _stream_req()))

    tool_chunks = [c for c in chunks if c.delta and c.delta.tool_calls]
    assert len(tool_chunks) == 1, "分片途中不透传,stop 时一次交付"
    tc = tool_chunks[0].delta.tool_calls[0]
    assert tc.id == "tu_1"
    assert tc.name == "system.file.read"
    assert tc.args == {"path": "a.txt"}
    assert chunks[-1].finish_reason == "tool_use"
    assert chunks[-1].usage.completion == 7


def test_stream_error_status_529_maps_unavailable():
    """HTTP ≥400 与 chat 同口径(_map_error):529(overloaded)→ UNAVAILABLE(retryable)。"""
    client = _client(lambda req: httpx.Response(529, json={"error": {"message": "overloaded"}}))
    p = ClaudeProvider(api_key="k", client=client)
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_collect(p, _stream_req()))
    assert exc_info.value.kind is ProviderErrorKind.UNAVAILABLE
    assert exc_info.value.retryable is True


def test_stream_truncated_without_message_stop_raises_unavailable():
    """中途断流(未见 message_stop 流即结束)→ UNAVAILABLE(retryable),半截结果不可信。"""
    events = [
        _MESSAGE_START,
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "半"}},
        # 无 message_stop:连接中途断开
    ]
    p = ClaudeProvider(api_key="k", client=_sse_client(_sse_payload(*events)))
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_collect(p, _stream_req()))
    assert exc_info.value.kind is ProviderErrorKind.UNAVAILABLE
    assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# WS1 多模态 parts:image base64 block 与降级占位
# ---------------------------------------------------------------------------


def test_vision_parts_as_base64_image_block():
    """caps 恒 vision;blob 接线后 USER parts → content 块数组(text + image base64)。"""
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=_payload(blocks=[{"type": "text", "text": "好"}]))

    async def main():
        blob = InMemoryBlobStore()
        ref = await blob.put(b"jpeg-bytes", "run-1")
        p = ClaudeProvider(api_key="k", client=_client(handler), blob=blob)
        assert p.capabilities().supports_vision is True
        await p.chat(ChatRequest(
            model="anthropic/m",
            messages=[Message(
                role=Role.USER, content="这是什么",
                parts=[ContentPart(mime="image/jpeg", ref=ref)],
            )],
        ))

    asyncio.run(main())
    content = seen["body"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "这是什么"}
    assert content[1] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(b"jpeg-bytes").decode("ascii"),
        },
    }


def test_system_parts_placeholder_in_system_string():
    """system 消息不进 messages(仅文本):其 parts 一律降级为 system 文本尾的占位行。"""
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=_payload(blocks=[{"type": "text", "text": "好"}]))

    async def main():
        blob = InMemoryBlobStore()
        ref = await blob.put(b"img", "run-1")
        p = ClaudeProvider(api_key="k", client=_client(handler), blob=blob)
        await p.chat(ChatRequest(
            model="anthropic/m",
            messages=[
                Message(role=Role.SYSTEM, content="你是助手。", parts=[ContentPart(ref=ref)]),
                Message(role=Role.USER, content="你好"),
            ],
        ))
        return ref

    ref = asyncio.run(main())
    assert seen["body"]["system"] == f"你是助手。\n[图片 image/png ref={ref} 未随请求发送]"
    assert all(m["role"] != "system" for m in seen["body"]["messages"])


def test_claude_missing_blob_ref_placeholder_not_crash():
    """blob 缺 ref / 未接线:part 落显式占位行(纯文本),请求不炸。"""
    bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=_payload(blocks=[{"type": "text", "text": "好"}]))

    msg = Message(role=Role.USER, content="图", parts=[ContentPart(ref="blob://run-1/missing")])
    # blob 接线但缺 ref
    p = ClaudeProvider(api_key="k", client=_client(handler), blob=InMemoryBlobStore())
    asyncio.run(p.chat(ChatRequest(model="anthropic/m", messages=[msg])))
    # blob 未接线
    p2 = ClaudeProvider(api_key="k", client=_client(handler))
    asyncio.run(p2.chat(ChatRequest(model="anthropic/m", messages=[msg])))

    expected = "图\n[图片 image/png ref=blob://run-1/missing 未随请求发送]"
    for body in bodies:
        assert body["messages"][0]["content"] == expected


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
