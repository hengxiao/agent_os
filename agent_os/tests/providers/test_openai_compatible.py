"""OpenAICompatibleProvider 锚点测试(docs/DESIGN.md §4.3;经 httpx.MockTransport,不碰真实网络)。

固定约定:自身不重试;错误映射 429→RATE_LIMIT(读 Retry-After)、401/403→AUTH、
400 且 context length→CONTEXT_OVERFLOW、5xx/超时→UNAVAILABLE(retryable)。
"""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from agent_os.api.v1 import (
    ChatRequest,
    ContentPart,
    Message,
    ProviderError,
    ProviderErrorKind,
    Role,
    ToolCall,
)
from agent_os.providers.openai_compatible import OpenAICompatibleProvider
from agent_os.tools.blob import InMemoryBlobStore


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


# ---------------------------------------------------------------------------
# 流式(SSE):data: 逐行解至 [DONE];tool_calls 分片缓冲组装;usage 末 chunk
# ---------------------------------------------------------------------------


def _sse_payload(*events) -> bytes:
    """事件 dict 序列 → SSE 字节流(字面量 ``"[DONE]"`` 原样发)。"""
    return "".join(
        f"data: {'[DONE]' if ev == '[DONE]' else json.dumps(ev, ensure_ascii=False)}\n\n"
        for ev in events
    ).encode()


def _sse_client(payload: bytes, *, seen: dict | None = None) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stream_req() -> ChatRequest:
    return ChatRequest(model="openai/gpt-x", messages=[Message(role=Role.USER, content="hi")])


async def _collect(p: OpenAICompatibleProvider, req: ChatRequest):
    return [c async for c in p.stream(req)]


def test_stream_text_deltas_usage_and_done():
    """文本 delta 按序透传;末 usage chunk(choices 空)携带 finish_reason;请求体带 stream 标记。"""
    events = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "你"}}]},
        {"choices": [{"index": 0, "delta": {"content": "好"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}},
        "[DONE]",
    ]
    seen: dict = {}
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1", api_key="k", client=_sse_client(_sse_payload(*events), seen=seen)
    )
    chunks = asyncio.run(_collect(p, _stream_req()))

    assert [c.delta.content for c in chunks if c.delta and c.delta.content] == ["你", "好"]
    assert all(c.delta.role is Role.ASSISTANT for c in chunks if c.delta)
    final = chunks[-1]
    assert final.finish_reason == "stop"
    assert final.usage.prompt == 3 and final.usage.completion == 2
    body = seen["body"]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


def test_stream_reasoning_content_deltas():
    """reasoning_content 分片进 delta.reasoning;无 usage chunk 时 finish_reason 在 [DONE] 前兜底交付。"""
    events = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "先想"}}]},
        {"choices": [{"index": 0, "delta": {"reasoning_content": "再想"}}]},
        {"choices": [{"index": 0, "delta": {"content": "答"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        "[DONE]",
    ]
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1", api_key="k", client=_sse_client(_sse_payload(*events))
    )
    chunks = asyncio.run(_collect(p, _stream_req()))

    assert [c.delta.reasoning for c in chunks if c.delta and c.delta.reasoning] == ["先想", "再想"]
    assert [c.delta.content for c in chunks if c.delta and c.delta.content] == ["答"]
    assert chunks[-1].finish_reason == "stop"  # 端点未回 usage chunk 的兜底
    assert chunks[-1].usage is None


def test_stream_tool_calls_shards_assembled_and_unmangled():
    """tool_calls 分片按 index 缓冲(id/name 首帧、arguments JSON 分片),
    只在末 chunk 交付完整 ToolCall;``__`` → ``.`` unmangle。"""
    events = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "c1", "type": "function",
             "function": {"name": "system__file__read", "arguments": '{"pa'}},
            {"index": 1, "id": "c2", "type": "function",
             "function": {"name": "skill__demo__fib", "arguments": '{"n"'}},
        ]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 1, "function": {"arguments": ": 5}"}},
            {"index": 0, "function": {"arguments": 'th": "a.txt"}'}},
        ]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        "[DONE]",
    ]
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1", api_key="k", client=_sse_client(_sse_payload(*events))
    )
    chunks = asyncio.run(_collect(p, _stream_req()))

    tool_chunks = [c for c in chunks if c.delta and c.delta.tool_calls]
    assert len(tool_chunks) == 1, "分片只在末 chunk 交付一次,流式途中不透传"
    tcs = tool_chunks[0].delta.tool_calls
    assert [t.id for t in tcs] == ["c1", "c2"]
    assert [t.name for t in tcs] == ["system.file.read", "skill.demo.fib"]
    assert tcs[0].args == {"path": "a.txt"}
    assert tcs[1].args == {"n": 5}
    final = chunks[-1]
    assert final.finish_reason == "tool_calls"
    assert final.usage.prompt == 1 and final.usage.completion == 1


def test_stream_error_status_reuses_map_error():
    """HTTP ≥400 与 chat 同口径(_map_error):429→RATE_LIMIT(读 Retry-After)、5xx→UNAVAILABLE。"""
    cases = [
        (429, {"error": {"message": "rate limited"}}, {"Retry-After": "7"},
         ProviderErrorKind.RATE_LIMIT, True, 7.0),
        (500, {"error": {"message": "boom"}}, None, ProviderErrorKind.UNAVAILABLE, True, None),
    ]
    for status, payload, headers, kind, retryable, retry_after in cases:
        client = httpx.AsyncClient(transport=_openai_response(payload, status, headers))
        p = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key="k", client=client)
        with pytest.raises(ProviderError) as exc_info:
            asyncio.run(_collect(p, _stream_req()))
        err = exc_info.value
        assert err.kind is kind, (status, err.kind)
        assert err.retryable is retryable, (status, err.retryable)
        if retry_after is not None:
            assert err.retry_after == retry_after, (status, err.retry_after)


def test_stream_truncated_without_done_raises_unavailable():
    """中途断流(未见 [DONE] 流即结束)→ UNAVAILABLE(retryable),半截结果不可信。"""
    events = [
        {"choices": [{"index": 0, "delta": {"content": "半"}}]},
        # 无 [DONE]:连接中途断开
    ]
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1", api_key="k", client=_sse_client(_sse_payload(*events))
    )
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_collect(p, _stream_req()))
    assert exc_info.value.kind is ProviderErrorKind.UNAVAILABLE
    assert exc_info.value.retryable is True


def test_stream_transport_error_maps_unavailable():
    """连接层故障与 chat 同口径:Timeout/ConnectError → UNAVAILABLE(retryable)。"""
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key="k", client=client)
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(_collect(p, _stream_req()))
    assert exc_info.value.kind is ProviderErrorKind.UNAVAILABLE
    assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# WS1 多模态 parts:vision 序列化(image_url data URI)与降级占位
# ---------------------------------------------------------------------------


def _capture_client(seen: dict) -> httpx.AsyncClient:
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_vision_parts_serialized_as_image_url():
    """supports_vision + blob 接线:parts → content parts 数组(text + image_url data URI)。"""
    seen: dict = {}

    async def main():
        blob = InMemoryBlobStore()
        ref = await blob.put(b"\x89PNG-bytes", "run-1")
        p = OpenAICompatibleProvider(
            base_url="https://api.example.com/v1", api_key="k",
            client=_capture_client(seen), blob=blob, supports_vision=True,
        )
        assert p.capabilities().supports_vision is True
        await p.chat(ChatRequest(
            model="openai/gpt-v",
            messages=[Message(role=Role.USER, content="这是什么", parts=[ContentPart(ref=ref)])],
        ))

    asyncio.run(main())
    content = seen["body"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "这是什么"}
    b64 = base64.b64encode(b"\x89PNG-bytes").decode("ascii")
    assert content[1] == {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}


def test_vision_disabled_falls_back_to_placeholder():
    """缺省 supports_vision=False:parts 不进 wire,content 尾部逐 part 显式占位行。"""
    seen: dict = {}
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1", api_key="k", client=_capture_client(seen)
    )
    assert p.capabilities().supports_vision is False
    msg = Message(
        role=Role.USER, content="这是什么",
        parts=[ContentPart(mime="image/jpeg", ref="blob://run-1/deadbeef")],
    )
    asyncio.run(p.chat(ChatRequest(model="openai/gpt-x", messages=[msg])))
    content = seen["body"]["messages"][0]["content"]
    assert content == "这是什么\n[图片 image/jpeg ref=blob://run-1/deadbeef 未随请求发送]"


def test_vision_without_blob_falls_back_to_placeholder():
    """supports_vision=True 但 blob 未接线:全部 part 占位,content 保持纯文本。"""
    seen: dict = {}
    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1", api_key="k",
        client=_capture_client(seen), supports_vision=True,
    )
    msg = Message(role=Role.USER, content="图", parts=[ContentPart(ref="blob://run-1/ab")])
    asyncio.run(p.chat(ChatRequest(model="openai/gpt-v", messages=[msg])))
    content = seen["body"]["messages"][0]["content"]
    assert content == "图\n[图片 image/png ref=blob://run-1/ab 未随请求发送]"


def test_vision_missing_blob_ref_placeholder_not_crash():
    """blob 缺 ref:该 part 落占位行(不炸请求),其余 part 照常进 wire。"""
    seen: dict = {}

    async def main():
        blob = InMemoryBlobStore()
        good = await blob.put(b"img", "run-1")
        p = OpenAICompatibleProvider(
            base_url="https://api.example.com/v1", api_key="k",
            client=_capture_client(seen), blob=blob, supports_vision=True,
        )
        msg = Message(
            role=Role.USER, content="两张",
            parts=[ContentPart(ref="blob://run-1/missing"), ContentPart(ref=good)],
        )
        await p.chat(ChatRequest(model="openai/gpt-v", messages=[msg]))

    asyncio.run(main())
    content = seen["body"]["messages"][0]["content"]
    assert content[0] == {
        "type": "text",
        "text": "两张\n[图片 image/png ref=blob://run-1/missing 未随请求发送]",
    }
    b64 = base64.b64encode(b"img").decode("ascii")
    assert content[1] == {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
    assert len(content) == 2, f"失败 part 只占位不进 wire:{content}"


def test_vision_parts_also_serialized_in_stream():
    """流式路径同一预解析(chat/stream 开头都调 _resolve_parts)。"""
    seen: dict = {}
    events = [
        {"choices": [{"index": 0, "delta": {"content": "好"}}]},
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        "[DONE]",
    ]

    async def main():
        blob = InMemoryBlobStore()
        ref = await blob.put(b"img", "run-1")
        p = OpenAICompatibleProvider(
            base_url="https://api.example.com/v1", api_key="k",
            client=_sse_client(_sse_payload(*events), seen=seen), blob=blob, supports_vision=True,
        )
        req = ChatRequest(
            model="openai/gpt-v",
            messages=[Message(role=Role.USER, content="看", parts=[ContentPart(ref=ref)])],
        )
        return [c async for c in p.stream(req)]

    chunks = asyncio.run(main())
    assert chunks, "流照常收尾"
    content = seen["body"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "看"}
    assert content[1]["type"] == "image_url"


# ---------------------------------------------------------------------------
# F3 回归(dogfood 实捕):中止/取消穿过流式响应时的生成器清理
# ---------------------------------------------------------------------------


def _slow_sse_client(texts: tuple[str, ...], *, tick: float = 0.05) -> httpx.AsyncClient:
    """慢流 SSE mock:逐段 sleep 后产出一行(中止落在字节途中的真实形态)。"""
    from httpx._content import AsyncIteratorByteStream

    async def body():
        for t in texts:
            yield _sse_payload({"choices": [{"index": 0, "delta": {"content": t}}]})
            await asyncio.sleep(tick)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=AsyncIteratorByteStream(body()),
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run_watched(main) -> list:
    """自建 loop 跑 ``main()`` 协程函数并捕获 loop 异常处理器的全部上下文。

    asyncgen 收尾错误(``an error occurred during closing of asynchronous
    generator`` 级联 / ``RuntimeError: generator didn't stop after athrow()``)
    经 loop 异常处理器上报——``asyncio.run`` 内部 loop 挂不上钩,故自建
    (收尾阶段显式 ``shutdown_asyncgens``,与 asyncio.run 的关闭序列一致)。
    """
    loop = asyncio.new_event_loop()
    errors: list = []
    loop.set_exception_handler(lambda lp, ctx: errors.append(ctx))
    try:
        loop.run_until_complete(main())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    return errors


def test_abort_mid_stream_closes_generators_cleanly():
    """F3(provider+Manager 层):loop 体抛 RunAborted + finally aclose(runner._stream_call
    形态)穿过慢流——RunAborted 干净传播,loop 异常处理器零上报。

    修复前(stream 跨 yield 的 ``async with client.stream``):aclose 掷入的
    GeneratorExit 经 contextlib ``__aexit__`` athrow 进 httpx 的 stream 生成器,
    级联终态下冒 ``RuntimeError: generator didn't stop after athrow()``。
    """
    from agent_os.providers.manager import ProviderManager

    class _RunAborted(Exception):
        pass

    seen: list[str] = []

    async def main():
        p = OpenAICompatibleProvider(client=_slow_sse_client(("a", "b", "c")))
        stream = ProviderManager([p]).stream(_stream_req())
        try:
            async for _chunk in stream:
                raise _RunAborted("流中强停")
        except _RunAborted as e:
            seen.append(str(e))
        finally:
            await stream.aclose()

    errors = _run_watched(main)
    assert seen == ["流中强停"], "中止须按原异常传播"
    assert errors == [], f"loop 异常处理器收到(生成器清理级联泄漏): {errors}"


def test_kernel_stop_mid_stream_aborts_clean(tmp_path):
    """F3(内核级):流中 ctl.stop → run 正常抛 RunAborted(ABORTED 语义),
    无生成器清理 RuntimeError(Loop 异常处理器零上报)。"""
    from typing import ClassVar

    from agent_os.api.v1 import (
        POST_LLM_CHUNK,
        Allow,
        Mode,
        Permission,
        RunConfig,
        Signal,
        ToolPolicy,
    )
    from agent_os.kernel.errors import RunAborted
    from agent_os.providers.openai_compatible import OpenAICompatibleProvider as _OCP
    from tests.helpers.kernels import assemble

    class _StopProbe:
        """首个 chunk 落地即 ctl.stop(test_streaming.py _Probe 同款形态)。"""

        name: ClassVar[str] = "f3-stop-probe"
        mode: ClassVar[Mode] = Mode.SYNC
        priority: ClassVar[int] = 10
        needs_free_text: ClassVar[bool] = False
        subscriptions: ClassVar[list] = [POST_LLM_CHUNK]
        fired = 0

        async def on_signal(self, sig: Signal, ctl) -> Allow:
            if self.fired == 0:
                self.fired += 1
                await ctl.stop(sig.run_id, "流中强停(F3 回归)")
            return Allow()

    skills = tmp_path / "skills.yaml"
    skills.write_text(
        """
skills:
  - name: test.greet
    version: 1.0.0
    kind: prompt
    inputs:
      type: object
      properties: { who: { type: string } }
      required: [who]
    outputs:
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["openai/gpt-x"] }
    limits: { max_steps: 4 }
    prompt: GREET
""",
        encoding="utf-8",
    )
    provider = _OCP(client=_slow_sse_client(('{"answer": "he', 'llo"}', '…')))
    probe = _StopProbe()
    kernel = assemble(
        RunConfig(
            model="openai/gpt-x",
            tool_policy=ToolPolicy(max_permission=Permission.EXEC),
            compression="off",
        ),
        provider,
        skills,
        sidecars=(probe,),
    )

    outcome: list[str] = []

    async def main():
        try:
            await kernel.run("test.greet", {"who": "世界"})
        except RunAborted:
            outcome.append("aborted")

    errors = _run_watched(main)
    assert outcome == ["aborted"], "流中 stop 须按 RunAborted 正常收场"
    assert probe.fired == 1
    assert errors == [], f"loop 异常处理器收到(生成器清理级联泄漏): {errors}"
