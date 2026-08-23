"""OpenAICompatibleProvider(docs/DESIGN.md §4.4;M1,唯一必须的真适配器)。

一个类覆盖所有 OpenAI 兼容端点(官方/Azure/vLLM/Ollama/网关);依赖仅 httpx。
错误映射:429→RATE_LIMIT(读 Retry-After)、401/403→AUTH、400 且 context length→
CONTEXT_OVERFLOW、5xx/超时→UNAVAILABLE;自身不重试(重试在 ProviderManager);
reasoning 字段 round-trip(请求带 ``reasoning_content``,响应映射回 ``Message.reasoning``)。
线格式:工具/函数名发出时经 ``naming.mangle_name``(``.``→``__``)编码,响应解析时反向解码——
OpenAI 兼容端点不接受点分函数名。
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


class OpenAICompatibleProvider:
    """``agent_os.api.v1.Provider`` 协议实现(M1)。

    ``client`` 可注入自带 transport 的 ``httpx.AsyncClient``(测试用 MockTransport);
    缺省时每次请求临时建 client。``base_url`` 不带尾斜杠亦可,端点 = ``{base_url}/chat/completions``。
    """

    name: str = "openai"

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        *,
        name: str | None = None,
        client: httpx.AsyncClient | None = None,
        api_key_env: str | None = None,
    ) -> None:
        if name is not None:
            self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client
        # 动态 key(15 分钟 OAuth token 教训):给了 env 名就每次调用现读,
        # token_refresh 续期 os.environ 后下一调用即生效;显式 api_key 仍钉死
        self._api_key_env = api_key_env

    @property
    def api_key(self) -> str | None:
        env = getattr(self, "_api_key_env", None)
        if env:
            return os.environ.get(env)
        return getattr(self, "_pinned_key", None)

    @api_key.setter
    def api_key(self, v: str | None) -> None:
        self._pinned_key = v

    def capabilities(self) -> ProviderCaps:
        return ProviderCaps(
            supports_tools=True,
            supports_streaming=True,
            cot_protocol="reasoning_content",
        )

    async def chat(self, req: ChatRequest) -> ChatResponse:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = self._request_body(req)
        try:
            if self._client is not None:
                resp = await self._client.post(
                    f"{self.base_url}/chat/completions", json=body, headers=headers
                )
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions", json=body, headers=headers
                    )
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE,
                f"{type(e).__name__}: {e}",
                retryable=True,
            ) from e
        if resp.status_code >= 400:
            raise self._map_error(resp)
        return self._map_response(resp.json())

    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]:
        raise NotImplementedError("M1")

    # ------------------------------------------------------------------
    # 请求序列化(契约 → OpenAI 格式)
    # ------------------------------------------------------------------

    def _request_body(self, req: ChatRequest) -> dict[str, Any]:
        prefix = f"{self.name}/"
        model = req.model.removeprefix(prefix)
        body: dict[str, Any] = {
            "model": model,
            "messages": [self._message_to_openai(m) for m in req.messages],
        }
        if req.tools:
            body["tools"] = [
                {"type": "function", "function": {**t, "name": mangle_name(t.get("name", ""))}}
                for t in req.tools
            ]
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        return body

    @staticmethod
    def _message_to_openai(m: Message) -> dict[str, Any]:
        if m.role is Role.TOOL:
            return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
        out: dict[str, Any] = {"role": m.role.value, "content": m.content}
        if m.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": mangle_name(tc.name), "arguments": json.dumps(tc.args, ensure_ascii=False)},
                }
                for tc in m.tool_calls
            ]
        if m.reasoning:
            out["reasoning_content"] = m.reasoning  # round-trip:下次请求逐字回传(§4.1)
        return out

    # ------------------------------------------------------------------
    # 响应 / 错误映射(OpenAI 格式 → 契约)
    # ------------------------------------------------------------------

    @staticmethod
    def _map_response(data: dict[str, Any]) -> ChatResponse:
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        tool_calls = [
            ToolCall(
                id=tc.get("id", ""),
                name=unmangle_name((tc.get("function") or {}).get("name", "")),
                args=_parse_arguments((tc.get("function") or {}).get("arguments")),
            )
            for tc in msg.get("tool_calls") or []
        ]
        usage = data.get("usage") or {}
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                content=msg.get("content") or "",
                tool_calls=tool_calls,
                reasoning=msg.get("reasoning_content"),
            ),
            finish_reason=choice.get("finish_reason") or "",
            usage=ChatUsage(
                prompt=usage.get("prompt_tokens", 0),
                completion=usage.get("completion_tokens", 0),
            ),
            raw=data,
        )

    @staticmethod
    def _map_error(resp: httpx.Response) -> ProviderError:
        message = _error_message(resp)
        status = resp.status_code
        if status == 429:
            retry_after: float | None = None
            raw = resp.headers.get("Retry-After")
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
        if status == 400 and "context" in message.lower() and (
            "length" in message.lower() or "token" in message.lower()
        ):
            return ProviderError(ProviderErrorKind.CONTEXT_OVERFLOW, message, retryable=False)
        if status >= 500:
            return ProviderError(ProviderErrorKind.UNAVAILABLE, message, retryable=True)
        return ProviderError(ProviderErrorKind.INVALID, f"HTTP {status}: {message}", retryable=False)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """``function.arguments`` 是 JSON 字符串;解析失败按空调用处理(错误观察交由 schema 校验)。"""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error_message(resp: httpx.Response) -> str:
    """从 OpenAI 错误体取 ``error.message``;非 JSON 回退为正文截断。"""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:200]
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message", ""))
    return str(err if err is not None else data)[:200]
