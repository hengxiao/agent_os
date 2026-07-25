"""M1 收尾锚点测试:内置工具四件套 + Provider 弹性(DESIGN.md §4.2/§4.4/§8.3/§8.4)。

固定约定:

- ``LocalPythonToolRegistry.with_builtins()`` 注册 ``fs_read``(READ)/``fs_write``(WRITE)/
  ``shell_exec``(EXEC)/``http_fetch``(NET);
- fs 工具限定在 ``ctx.workdir`` 内,逃逸路径(``../``)返回 ``ok=False, kind=INVALID_ARGS``;
- ``fs_read`` 带行号前缀(``1\\t...``),支持 ``offset``/``limit``;
- ``http_fetch_tool(transport=...)`` 工厂支持注入 httpx transport(测试用 MockTransport);
- ProviderManager:仅 retryable 错误进指数退避(respect retry_after),默认上限 3 次;
  每 provider 令牌桶限流 ``rate_limits={name: (rate_per_sec, burst)}``;
- OpenAICompatibleProvider 自身不重试;错误映射 429→RATE_LIMIT(读 Retry-After)、
  401/403→AUTH、400 且 context length→CONTEXT_OVERFLOW、5xx/超时→UNAVAILABLE(retryable)。
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    Message,
    Permission,
    ProviderCaps,
    ProviderError,
    ProviderErrorKind,
    Role,
    SkillFrame,
    ToolCall,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.providers.manager import ProviderManager
from agent_os.providers.openai_compatible import OpenAICompatibleProvider
from agent_os.tools.builtins import http_fetch_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry, ToolDispatchContext

# ---------------------------------------------------------------------------
# 内置工具
# ---------------------------------------------------------------------------


def _dispatch_ctx(*, allowed: list[str] | None = None, max_perm: Permission = Permission.EXEC):
    frame = SkillFrame(frame_id="f1", run_id="r1")
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=allowed or ["fs_read", "fs_write", "shell_exec", "http_fetch"],
        tool_policy=ToolPolicy(max_permission=max_perm),
    )


def test_with_builtins_registers_four_tools():
    reg = LocalPythonToolRegistry.with_builtins()
    for name in ("fs_read", "fs_write", "shell_exec", "http_fetch"):
        assert reg.has(name), name
    assert reg.get("fs_read").spec.permission is Permission.READ
    assert reg.get("fs_write").spec.permission is Permission.WRITE
    assert reg.get("shell_exec").spec.permission is Permission.EXEC
    assert reg.get("http_fetch").spec.permission is Permission.NET


def test_fs_write_then_read_with_line_numbers():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        wr = await reg.dispatch(
            ToolCall(id="w1", name="fs_write", args={"path": "a.txt", "content": "hello\nworld\nfoo"}), ctx
        )
        assert wr.ok, wr.error
        rd = await reg.dispatch(ToolCall(id="r1", name="fs_read", args={"path": "a.txt"}), ctx)
        assert rd.ok, rd.error
        return rd.value

    content = asyncio.run(main())
    assert "hello" in content and "world" in content
    assert "1" in content and "\t" in content  # 行号前缀


def test_fs_read_offset_limit():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        await reg.dispatch(
            ToolCall(id="w1", name="fs_write", args={"path": "b.txt", "content": "l1\nl2\nl3\nl4\n"}), ctx
        )
        rd = await reg.dispatch(
            ToolCall(id="r1", name="fs_read", args={"path": "b.txt", "offset": 2, "limit": 2}), ctx
        )
        assert rd.ok, rd.error
        return rd.value

    content = asyncio.run(main())
    assert "l2" in content and "l3" in content
    assert "l1" not in content and "l4" not in content


def test_fs_path_traversal_rejected():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        return await reg.dispatch(
            ToolCall(id="w1", name="fs_write", args={"path": "../evil.txt", "content": "x"}), ctx
        )

    result = asyncio.run(main())
    assert not result.ok
    assert result.error is not None and result.error.kind is ToolErrorKind.INVALID_ARGS


def test_shell_exec_runs_in_workdir():
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx()

    async def main():
        return await reg.dispatch(
            ToolCall(id="s1", name="shell_exec", args={"command": "echo hi && pwd"}), ctx
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    assert "hi" in result.value


def test_http_fetch_via_mock_transport():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="PAGE-CONTENT"))
    tool = http_fetch_tool(transport=transport)
    reg = LocalPythonToolRegistry()
    reg.register(tool)
    ctx = _dispatch_ctx()

    async def main():
        return await reg.dispatch(
            ToolCall(id="h1", name="http_fetch", args={"url": "https://example.com/"}), ctx
        )

    result = asyncio.run(main())
    assert result.ok, result.error
    assert result.value["status"] == 200
    assert "PAGE-CONTENT" in result.value["content"]


def test_tool_policy_caps_permission():
    """三层权限:RunConfig 上限 WRITE 时,EXEC 级 shell_exec 被拒(§8.2)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    ctx = _dispatch_ctx(max_perm=Permission.WRITE)

    async def main():
        return await reg.dispatch(ToolCall(id="s1", name="shell_exec", args={"command": "echo x"}), ctx)

    result = asyncio.run(main())
    assert not result.ok
    assert result.error is not None and result.error.kind is ToolErrorKind.PERMISSION_DENIED


# ---------------------------------------------------------------------------
# ProviderManager:重试与限流
# ---------------------------------------------------------------------------


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


def _req() -> ChatRequest:
    return ChatRequest(model="flaky/test", messages=[Message(role=Role.USER, content="hi")])


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
# OpenAICompatibleProvider(经 httpx.MockTransport,不碰真实网络)
# ---------------------------------------------------------------------------


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
                    "function": {"name": "fs_read", "arguments": '{"path": "a.txt"}'},
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
        tools=[{"name": "fs_read", "description": "", "parameters": {}}],
    )
    resp = asyncio.run(p.chat(req))
    assert resp.message.tool_calls[0].name == "fs_read"
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
