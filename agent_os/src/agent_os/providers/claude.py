"""ClaudeProvider(DESIGN.md §4.3;Anthropic Messages API 独立适配器)。

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
"""

from __future__ import annotations

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
    ) -> None:
        if name is not None:
            self.name = name
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.anthropic_version = anthropic_version
        self.default_max_tokens = default_max_tokens
        self._client = client

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
        body = self._request_body(req)
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
        raise NotImplementedError("M5")

    # ------------------------------------------------------------------
    # 请求序列化(契约 → Anthropic 格式)
    # ------------------------------------------------------------------

    def _request_body(self, req: ChatRequest) -> dict[str, Any]:
        model = req.model.removeprefix(f"{self.name}/")
        system_parts = [m.content for m in req.messages if m.role is Role.SYSTEM and m.content]
        messages = self._convert_messages([m for m in req.messages if m.role is not Role.SYSTEM])
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

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """连续 TOOL 消息合并进同一条 user 消息(Anthropic 要求 tool_result 在 user 内)。"""
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
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                blocks.extend(
                    {"type": "tool_use", "id": tc.id, "name": mangle_name(tc.name), "input": tc.args}
                    for tc in m.tool_calls
                )
                out.append({"role": "assistant", "content": blocks})
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


__all__ = ["DEFAULT_BASE_URL", "ClaudeProvider"]
