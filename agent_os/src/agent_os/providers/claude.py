"""ClaudeProvider(docs/DESIGN.md §4.3;Anthropic Messages API 独立适配器)。

Anthropic Messages API 与 OpenAI 格式差异较大,故独立实现而非复用
OpenAICompatibleProvider(§4.3"值得独立实现"):

- 端点 ``POST {base_url}/v1/messages``;请求头 ``x-api-key`` + ``anthropic-version``;
- **system 顶层独立**(不进 messages);``max_tokens`` 必填(缺省 4096);
- 工具 ``{name, description, input_schema}``;assistant 的 tool_calls → ``tool_use`` 块;
  TOOL 消息 → ``tool_result`` 块,连续 TOOL 消息合并进同一条 user 消息;
- thinking 块(text + ``signature``)⇄ ``Message.reasoning`` + ``meta["signature"]``
  round-trip(``cot_protocol = "signed_thinking"``,§4.1);
- usage 含 ``cache_read_input_tokens``/``cache_creation_input_tokens`` → cache_read/write;
- 错误映射:429 → RATE_LIMIT(读 retry-after);401/403 → AUTH;400 且 prompt too long →
  CONTEXT_OVERFLOW;529(overloaded)/5xx → UNAVAILABLE(retryable);自身不重试(重试在 Manager)。
- 线格式:工具名发出时经 ``naming.mangle_name``(``.``→``__``)编码,响应解析时反向解码——
  Anthropic 函数名仅允许 ``[a-zA-Z0-9_-]``。
- WS1 多模态(``supports_vision=True``):消息 ``parts`` 序列化为 image block
  (``source.type="base64"``);blob 未接线/解析失败的 part 落显式占位行
  (``providers/parts.py``,blob.get 是 async,chat/stream 开头预解析);
  system 消息不进 messages,其 parts 一律降级为 system 文本尾的占位行。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agent_os.api.v1 import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    ProviderCaps,
    ProviderError,
    ProviderErrorKind,
    Role,
    ToolCall,
)

from .naming import mangle_name, unmangle_name
from .parts import image_blocks, placeholders_for, resolve_image_parts

DEFAULT_BASE_URL = "https://api.anthropic.com"
_DEFAULT_MAX_TOKENS = 4096


class ClaudeProvider:
    """``agent_os.api.v1.Provider`` 协议实现(Anthropic Messages API)。"""

    name: str = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        *,
        name: str | None = None,
        client: httpx.AsyncClient | None = None,
        anthropic_version: str = "2023-06-01",
        default_max_tokens: int = _DEFAULT_MAX_TOKENS,
        blob: Any | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.anthropic_version = anthropic_version
        self.default_max_tokens = default_max_tokens
        self._client = client
        # WS1:多模态 parts 的 blob 通道(builder 装配注入;缺省 None = parts 占位降级)
        self.blob = blob

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(
            supports_tools=True,
            supports_vision=True,
            supports_streaming=True,
            cot_protocol="signed_thinking",
            cache_billing=True,
        )

    async def chat(self, req: ChatRequest) -> ChatResponse:
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }
        body = self._request_body(req, await self._resolve_parts(req.messages))
        try:
            if self._client is not None:
                resp = await self._client.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                    resp = await client.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE, f"{type(e).__name__}: {e}", retryable=True
            ) from e
        if resp.status_code >= 400:
            raise self._map_error(resp)
        return self._map_response(resp.json())

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]:
        """Anthropic SSE 流式(§4.1):``stream=True`` + Messages 事件序列。

        ``message_start``(input usage)→ ``content_block_start/delta/stop``(text/
        thinking 即时透传;signature_delta 按块累计进 ``meta["signature"]``;tool_use 的
        input_json 分片按块累计、stop 时解析为完整 ToolCall(unmangle)一次交付)→
        ``message_delta``(stop_reason + output usage)→ ``message_stop``(交付终 chunk)。
        ``ping``/``error`` 旁路;流提前结束(未见 ``message_stop``)/传输层错误 →
        UNAVAILABLE(retryable)。
        """
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }
        body = {**self._request_body(req, await self._resolve_parts(req.messages)), "stream": True}
        try:
            if self._client is not None:
                async with self._client.stream(
                    "POST", f"{self.base_url}/v1/messages", json=body, headers=headers
                ) as resp:
                    async for chunk in self._stream_chunks(resp):
                        yield chunk
            else:
                async with (
                    httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client,
                    client.stream(
                        "POST", f"{self.base_url}/v1/messages", json=body, headers=headers
                    ) as resp,
                ):
                    async for chunk in self._stream_chunks(resp):
                        yield chunk
        except httpx.TransportError as e:
            # 连接/读超时、中途断流(ReadError/RemoteProtocolError 均归此类)
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE, f"{type(e).__name__}: {e}", retryable=True
            ) from e

    async def _stream_chunks(self, resp: httpx.Response) -> AsyncIterator[ChatChunk]:
        """单个 SSE 响应 → ChatChunk 序列(``async with client.stream`` 内消费)。"""
        if resp.status_code >= 400:
            await resp.aread()  # 流式响应须先读体,_map_error 才能取 error.message
            raise self._map_error(resp)
        stopped = False
        stop_reason: str | None = None
        blocks: dict[int, dict[str, Any]] = {}  # index → 块级累计(type/id/name/signature/json)
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue  # event:/注释/空行旁路(每事件单行 data,类型在 payload 内)
            try:
                data = json.loads(line[5:].strip())
            except ValueError:
                continue  # 畸形行跳过,不中断整流
            kind = data.get("type")
            if kind == "message_start":
                u = ((data.get("message") or {}).get("usage")) or {}
                usage["input"] = u.get("input_tokens", 0)
                usage["output"] = u.get("output_tokens", 0)
                usage["cache_read"] = u.get("cache_read_input_tokens", 0)
                usage["cache_write"] = u.get("cache_creation_input_tokens", 0)
            elif kind == "content_block_start":
                block = data.get("content_block") or {}
                btype = block.get("type")
                if btype in ("text", "thinking", "tool_use"):
                    blocks[data.get("index", 0)] = {
                        "type": btype,
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "signature": "",
                        "json": "",
                    }
            elif kind == "content_block_delta":
                block = blocks.get(data.get("index", 0))
                if block is None:
                    continue
                delta = data.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "text_delta":
                    yield ChatChunk(delta=Message(role=Role.ASSISTANT, content=delta.get("text", "")))
                elif dtype == "thinking_delta":
                    yield ChatChunk(delta=Message(role=Role.ASSISTANT, reasoning=delta.get("thinking", "")))
                elif dtype == "signature_delta":
                    block["signature"] += delta.get("signature", "")
                elif dtype == "input_json_delta":
                    block["json"] += delta.get("partial_json", "")
            elif kind == "content_block_stop":
                block = blocks.pop(data.get("index", 0), None)
                if block is None:
                    continue
                if block["type"] == "tool_use":
                    # 分片累计的 input_json 在 stop 时解析;unmangle 回点分 canonical
                    yield ChatChunk(
                        delta=Message(
                            role=Role.ASSISTANT,
                            tool_calls=[
                                ToolCall(
                                    id=block["id"],
                                    name=unmangle_name(block["name"]),
                                    args=_parse_partial_json(block["json"]),
                                )
                            ],
                        )
                    )
                elif block["type"] == "thinking" and block["signature"]:
                    # 与 _map_response 同形态:signature 存 meta["signature"] 供 round-trip
                    yield ChatChunk(
                        delta=Message(role=Role.ASSISTANT, meta={"signature": block["signature"]})
                    )
            elif kind == "message_delta":
                delta = data.get("delta") or {}
                if delta.get("stop_reason"):
                    stop_reason = delta["stop_reason"]
                u = data.get("usage") or {}
                if u.get("output_tokens") is not None:
                    usage["output"] = u["output_tokens"]
            elif kind == "message_stop":
                stopped = True
                break
            # ping/error 等其余事件旁路
        if not stopped:
            # 流提前结束(未见 message_stop)= 截断:半截结果不可信,按可重试故障上抛
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE, "SSE 流截断(未收到 message_stop)", retryable=True
            )
        # usage 从 message_start/message_delta 汇聚进终 ChatChunk
        yield ChatChunk(
            finish_reason=stop_reason,
            usage=ChatUsage(
                prompt=usage["input"],
                completion=usage["output"],
                cache_read=usage["cache_read"],
                cache_write=usage["cache_write"],
            ),
        )

    # ------------------------------------------------------------------
    # 请求序列化(契约 → Anthropic 格式)
    # ------------------------------------------------------------------

    async def _resolve_parts(
        self, messages: list[Message]
    ) -> dict[int, list[dict[str, Any] | None]]:
        """多模态 parts 预解析(WS1):blob.get 是 async,请求序列化保持同步。

        caps 恒 vision(类级声明);blob 未接线时返回空表(序列化侧逐 part 落占位行)。
        """
        if not self.capabilities().supports_vision or self.blob is None:
            return {}
        return await resolve_image_parts(messages, self.blob, _claude_image_block)

    def _request_body(
        self,
        req: ChatRequest,
        resolved: dict[int, list[dict[str, Any] | None]] | None = None,
    ) -> dict[str, Any]:
        model = req.model.removeprefix(f"{self.name}/")
        # system 顶层独立(仅文本):system 消息一般无 parts,有也一律落占位行附尾
        system_parts = [
            m.content + (placeholders_for(m.parts, None) if m.parts else "")
            for m in req.messages
            if m.role is Role.SYSTEM and (m.content or m.parts)
        ]
        messages = self._convert_messages(
            [m for m in req.messages if m.role is not Role.SYSTEM], resolved
        )
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": req.max_tokens or self.default_max_tokens,
            "messages": messages,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if req.tools:
            body["tools"] = [
                {
                    "name": mangle_name(t.get("name", "")),
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {}),
                }
                for t in req.tools
            ]
        if req.temperature is not None:
            body["temperature"] = req.temperature
        return body

    def _convert_messages(
        self,
        messages: list[Message],
        resolved: dict[int, list[dict[str, Any] | None]] | None = None,
    ) -> list[dict[str, Any]]:
        """连续 TOOL 消息合并进同一条 user 消息(Anthropic 要求 tool_result 在 user 内)。

        WS1:带 parts 的 USER 消息 → content 块数组(text + image base64 block);
        解析失败/未接线的 part 落显式占位行;ASSISTANT 的 parts 不回传图片
        (响应侧恒文本),占位行附其文本块尾。
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role is Role.TOOL:
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
                continue
            if m.role is Role.ASSISTANT:
                blocks: list[dict[str, Any]] = []
                if m.reasoning:
                    blocks.append({
                        "type": "thinking",
                        "thinking": m.reasoning,
                        "signature": m.meta.get("signature", ""),
                    })
                text = m.content
                if m.parts:
                    text += placeholders_for(m.parts, None)
                if text:
                    blocks.append({"type": "text", "text": text})
                blocks.extend(
                    {"type": "tool_use", "id": tc.id, "name": mangle_name(tc.name), "input": tc.args}
                    for tc in m.tool_calls
                )
                out.append({"role": "assistant", "content": blocks})
                continue
            if m.parts:
                part_blocks = (resolved or {}).get(id(m))
                text = m.content + placeholders_for(m.parts, part_blocks)
                images = image_blocks(part_blocks)
                content: Any = [{"type": "text", "text": text}, *images] if images else text
                out.append({"role": m.role.value, "content": content})
                continue
            out.append({"role": m.role.value, "content": m.content})
        return out

    # ------------------------------------------------------------------
    # 响应 / 错误映射(Anthropic 格式 → 契约)
    # ------------------------------------------------------------------

    @staticmethod
    def _map_response(data: dict[str, Any]) -> ChatResponse:
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        thinking = "".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking")
        signature = next(
            (b.get("signature") for b in blocks if b.get("type") == "thinking" and b.get("signature")),
            None,
        )
        tool_calls = [
            ToolCall(id=b.get("id", ""), name=unmangle_name(b.get("name", "")), args=b.get("input") or {})
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        usage = data.get("usage") or {}
        meta: dict[str, Any] = {"signature": signature} if signature else {}
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=text,
                tool_calls=tool_calls,
                reasoning=thinking or None,
                meta=meta,
            ),
            finish_reason=data.get("stop_reason") or "",
            usage=ChatUsage(
                prompt=usage.get("input_tokens", 0),
                completion=usage.get("output_tokens", 0),
                cache_read=usage.get("cache_read_input_tokens", 0),
                cache_write=usage.get("cache_creation_input_tokens", 0),
            ),
            raw=data,
        )

    @staticmethod
    def _map_error(resp: httpx.Response) -> ProviderError:
        try:
            data = resp.json()
            message = str((data.get("error") or {}).get("message", ""))
        except ValueError:
            message = resp.text[:200]
        status = resp.status_code
        if status == 429:
            retry_after: float | None = None
            raw = resp.headers.get("retry-after")
            if raw is not None:
                try:
                    retry_after = float(raw)
                except ValueError:
                    retry_after = None
            return ProviderError(
                ProviderErrorKind.RATE_LIMIT, message, retryable=True, retry_after=retry_after
            )
        if status in (401, 403):
            return ProviderError(ProviderErrorKind.AUTH, message, retryable=False)
        if status == 400 and "prompt is too long" in message.lower():
            return ProviderError(ProviderErrorKind.CONTEXT_OVERFLOW, message, retryable=False)
        if status == 529 or status >= 500:
            return ProviderError(ProviderErrorKind.UNAVAILABLE, message, retryable=True)
        return ProviderError(ProviderErrorKind.INVALID, f"HTTP {status}: {message}", retryable=False)


def _claude_image_block(mime: str, b64: str) -> dict[str, Any]:
    """单个 image part 的 Anthropic wire block(base64 source)。"""
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}


def _parse_partial_json(raw: str) -> dict[str, Any]:
    """流式 tool_use 的 ``input_json`` 分片累计串 → args;解析失败按空调用处理

    (与 openai_compatible._parse_arguments 同一容忍口径,错误观察交由 schema 校验)。
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["DEFAULT_BASE_URL", "ClaudeProvider"]
