"""OpenAICompatibleProvider(docs/DESIGN.md §4.4;M1,唯一必须的真适配器)。

一个类覆盖所有 OpenAI 兼容端点(官方/Azure/vLLM/Ollama/网关);依赖仅 httpx。
错误映射:429→RATE_LIMIT(读 Retry-After)、401/403→AUTH、400 且 context length→
CONTEXT_OVERFLOW、5xx/超时→UNAVAILABLE;自身不重试(重试在 ProviderManager);
reasoning 字段 round-trip(请求带 ``reasoning_content``,响应映射回 ``Message.reasoning``)。
线格式:工具/函数名发出时经 ``naming.mangle_name``(``.``→``__``)编码,响应解析时反向解码——
OpenAI 兼容端点不接受点分函数名。
WS1 多模态:``supports_vision=True`` 且 blob 接线时,消息 ``parts`` 序列化为
OpenAI parts 数组(text + ``image_url`` data URI);否则逐 part 落显式占位行
(``providers/parts.py``,blob.get 是 async,chat/stream 开头预解析)。
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


class OpenAICompatibleProvider:
    """``agent_os.api.v1.Provider`` 协议实现(M1)。

    ``client`` 可注入自带 transport 的 ``httpx.AsyncClient``(测试用 MockTransport);
    缺省时每次请求临时建 client。``base_url`` 不带尾斜杠亦可,端点 = ``{base_url}/chat/completions``。
    ``supports_vision`` 按后端能力声明(缺省 False);``blob`` 为 parts 解析的
    blob store(builder 装配注入,缺省 None = parts 一律占位降级)。
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
        blob: Any | None = None,
        supports_vision: bool = False,
    ) -> None:
        if name is not None:
            self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client
        # 动态 key(15 分钟 OAuth token 教训):给了 env 名就每次调用现读,
        # token_refresh 续期 os.environ 后下一调用即生效;显式 api_key 仍钉死
        self._api_key_env = api_key_env
        # WS1:多模态 parts 的 blob 通道与 vision 能力位(缺省关,按后端声明)
        self.blob = blob
        self._supports_vision = supports_vision

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
            supports_vision=self._supports_vision,
            supports_streaming=True,
            cot_protocol="reasoning_content",
        )

    async def chat(self, req: ChatRequest) -> ChatResponse:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = self._request_body(req, await self._resolve_parts(req.messages))
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
        """SSE 流式(§4.1):``stream=True`` + ``stream_options.include_usage``。

        逐行解 ``data:`` 至 ``[DONE]``:content/reasoning_content delta 即时透传;
        tool_calls 分片按 index 缓冲,finish 时组装为完整 ToolCall(unmangle)一次交付——
        流式只透传文本/推理;usage 末 chunk(choices 空)连同暂存的 finish_reason 交付。
        流提前结束(未见 ``[DONE]``)/传输层错误 → UNAVAILABLE(retryable)。
        """
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {
            **self._request_body(req, await self._resolve_parts(req.messages)),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            if self._client is not None:
                async with self._client.stream(
                    "POST", f"{self.base_url}/chat/completions", json=body, headers=headers
                ) as resp:
                    async for chunk in self._stream_chunks(resp):
                        yield chunk
            else:
                async with (
                    httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client,
                    client.stream(
                        "POST", f"{self.base_url}/chat/completions", json=body, headers=headers
                    ) as resp,
                ):
                    async for chunk in self._stream_chunks(resp):
                        yield chunk
        except httpx.TransportError as e:
            # 连接/读超时、中途断流(ReadError/RemoteProtocolError 均归此类)
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE,
                f"{type(e).__name__}: {e}",
                retryable=True,
            ) from e

    async def _stream_chunks(self, resp: httpx.Response) -> AsyncIterator[ChatChunk]:
        """单个 SSE 响应 → ChatChunk 序列(``async with client.stream`` 内消费)。"""
        if resp.status_code >= 400:
            await resp.aread()  # 流式响应须先读体,_map_error 才能取 error.message
            raise self._map_error(resp)
        done = False
        finish_reason: str | None = None
        tool_parts: dict[int, dict[str, str]] = {}  # index → {id, name, arguments 分片}
        tool_delivered = False
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue  # event:/注释/空行旁路(OpenAI 每事件单行 data)
            payload = line[5:].strip()
            if payload == "[DONE]":
                done = True
                break
            try:
                data = json.loads(payload)
            except ValueError:
                continue  # 畸形行跳过,不中断整流
            usage = data.get("usage")
            if usage:
                # 末 chunk(choices 空):usage + 暂存的 finish_reason 一并交付
                yield ChatChunk(
                    finish_reason=finish_reason,
                    usage=ChatUsage(
                        prompt=usage.get("prompt_tokens", 0),
                        completion=usage.get("completion_tokens", 0),
                    ),
                )
                finish_reason = None
            for choice in data.get("choices") or []:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    yield ChatChunk(delta=Message(role=Role.ASSISTANT, content=content))
                reasoning = delta.get("reasoning_content")
                if reasoning:
                    yield ChatChunk(delta=Message(role=Role.ASSISTANT, reasoning=reasoning))
                for tc in delta.get("tool_calls") or []:
                    part = tool_parts.setdefault(tc.get("index") or 0, {"id": "", "name": "", "arguments": ""})
                    part["id"] = part["id"] or (tc.get("id") or "")
                    func = tc.get("function") or {}
                    part["name"] = part["name"] or (func.get("name") or "")
                    part["arguments"] += func.get("arguments") or ""
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                    if tool_parts and not tool_delivered:
                        # 只在末 chunk 交付完整 ToolCall(分片缓冲组装 + unmangle 回点分 canonical)
                        yield self._tool_calls_chunk(tool_parts)
                        tool_delivered = True
        if tool_parts and not tool_delivered:
            yield self._tool_calls_chunk(tool_parts)  # finish 帧缺席的兜底(正常不触发)
        if finish_reason is not None:
            # 端点未回 usage chunk(include_usage 未兑现)时,finish_reason 在此兜底交付
            yield ChatChunk(finish_reason=finish_reason)
        if not done:
            # 流提前结束(未见 [DONE])= 截断:半截结果不可信,按可重试故障上抛
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE, "SSE 流截断(未收到 [DONE])", retryable=True
            )

    @staticmethod
    def _tool_calls_chunk(tool_parts: dict[int, dict[str, str]]) -> ChatChunk:
        """缓冲的 tool_calls 分片 → 完整 ToolCall 序列的单个 delta chunk。"""
        return ChatChunk(
            delta=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(
                        id=part["id"],
                        name=unmangle_name(part["name"]),
                        args=_parse_arguments(part["arguments"]),
                    )
                    for _, part in sorted(tool_parts.items())
                ],
            )
        )

    # ------------------------------------------------------------------
    # 请求序列化(契约 → OpenAI 格式)
    # ------------------------------------------------------------------

    async def _resolve_parts(
        self, messages: list[Message]
    ) -> dict[int, list[dict[str, Any] | None]]:
        """多模态 parts 预解析(WS1):blob.get 是 async,请求序列化保持同步。

        vision caps + blob 接线齐备才解析;否则空表(序列化侧逐 part 落占位行)。
        """
        if not self.capabilities().supports_vision or self.blob is None:
            return {}
        return await resolve_image_parts(messages, self.blob, _openai_image_block)

    def _request_body(
        self,
        req: ChatRequest,
        resolved: dict[int, list[dict[str, Any] | None]] | None = None,
    ) -> dict[str, Any]:
        prefix = f"{self.name}/"
        model = req.model.removeprefix(prefix)
        body: dict[str, Any] = {
            "model": model,
            "messages": [self._message_to_openai(m, resolved) for m in req.messages],
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

    def _message_to_openai(
        self,
        m: Message,
        resolved: dict[int, list[dict[str, Any] | None]] | None = None,
    ) -> dict[str, Any]:
        if m.role is Role.TOOL:
            return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
        content: Any = m.content
        if m.parts:
            # WS1:有解析成功的 part → content 变 parts 数组(text + image_url);
            # 失败/未解析的 part 落显式占位行附在文本尾(逐 part 一行)
            blocks = (resolved or {}).get(id(m))
            text = m.content + placeholders_for(m.parts, blocks)
            images = image_blocks(blocks)
            content = [{"type": "text", "text": text}, *images] if images else text
        out: dict[str, Any] = {"role": m.role.value, "content": content}
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


def _openai_image_block(mime: str, b64: str) -> dict[str, Any]:
    """单个 image part 的 OpenAI wire block(data URI 内嵌 base64)。"""
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


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
