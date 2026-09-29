"""MCP Streamable HTTP 适配器锚点测试(tools/mcp_http.py;spec 2025-03-26 版族)。

固定约定(与 test_mcp.py 的 stdio 侧对齐):

- ``url`` 键触发 http 传输(transport 缺省 auto 按键判);eager 装配:initialize
  握手 + tools/list 注册进 LocalPythonToolRegistry,连接失败 ConfigError 快速失败;
- 响应分流:application/json 单包 / text/event-stream SSE 帧(两种都必须支持,
  逐请求分流,会话中途可切换);
- 会话管理:initialize 捕获 Mcp-Session-Id 后续请求必带;协商 protocolVersion
  记录,后续请求带 MCP-Protocol-Version;404(会话被 server 终止)→ 重建会话
  重试一次;close() best-effort DELETE 终止会话;
- 传输失败(连接错/断流/5xx/404/协议垃圾)→ 重建会话重连一次重试一次,再失败
  INTERNAL;超时 → TIMEOUT retryable,**会话保留**(HTTP 每请求独立 POST,无
  stdio 共享流错位风险——与 stdio"超时必杀连接"的语义差异点,测试锚死);
- headers 值支持 ``{"env": "VAR"}`` 间接引用(连接时现读 os.environ,不落盘明文;
  变量缺席 eager 快速失败)。

对端:tests/helpers/mcp_http_server.py(FastAPI 罐头服务器;真 uvicorn +
ephemeral port,fixture 照 tests/tui/test_debugger_live.py:30-48 先例)。
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest
import uvicorn

from agent_os.api.v1 import (
    Permission,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.runtime.config import ConfigError, build_kernel
from agent_os.tools.local_registry import LocalPythonToolRegistry
from agent_os.tools.mcp import (
    McpError,
    McpServerSpec,
    connect_and_register,
)
from agent_os.tools.mcp_http import McpHttpClient
from tests.helpers.mcp_http_server import ServerState, create_app

#: 测试专用 env 名/值(避免与真实环境变量撞名;值带可识别标记)
ENV_VAR = "AGENT_OS_TEST_MCP_HTTP_TOKEN"
ENV_VALUE = "TESTSECRET-MCP-HTTP-TOKEN"


@pytest.fixture
def http_server():
    """真服务(ephemeral port;测试结束关停)——照 test_debugger_live.py:30-48 先例。"""
    state = ServerState()
    app = create_app(state)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error",
                       timeout_graceful_shutdown=0))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn 10s 内未启动"
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/mcp", state, server
    server.should_exit = True
    thread.join(timeout=5)


def _spec(url: str, **kw) -> McpServerSpec:
    """默认装配规格:http 传输(url 触发 auto 判),缺省全默认(permission=READ/timeout=30)。"""
    return McpServerSpec(name="web", url=url, **kw)


def _dispatch_ctx(*, allowed: list[str] | None = None, max_perm: Permission = Permission.EXEC):
    frame = SkillFrame(frame_id="f1", run_id="r1")
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=allowed if allowed is not None else [],
        tool_policy=ToolPolicy(max_permission=max_perm),
    )


async def _assemble(spec: McpServerSpec, registry: LocalPythonToolRegistry | None = None):
    """连一个 server 并返回 (registry, clients);调用方负责 finally close。"""
    registry = registry or LocalPythonToolRegistry()
    clients = await connect_and_register(registry, [spec])
    return registry, clients


async def _close(clients) -> None:
    for client in clients:
        await client.close()


# ---------------------------------------------------------------------------
# 1. eager 装配:注册与 spec 转换(url 触发 http 传输)
# ---------------------------------------------------------------------------


def test_eager_registration_and_spec_fields(http_server):
    """url 键 → auto 判 http;装配期 initialize + tools/list 注册;spec 权限/标记正确。"""

    async def main():
        url, state, _server = http_server
        registry, clients = await _assemble(_spec(url))
        try:
            assert isinstance(clients[0], McpHttpClient)  # transport auto 按 url 判 http
            assert registry.has("mcp.web.echo")
            assert registry.has("mcp.web.fail")
            assert registry.has("mcp.web.slow")
            echo = registry.get("mcp.web.echo").spec
            assert echo.permission is Permission.READ  # server 级缺省最小授权
            assert echo.timeout == 30.0
            assert echo.untrusted_source is True  # 第三方产出恒标记(§8.3)
            assert echo.concurrency_safe is False
            assert echo.parameters["properties"]["text"]["type"] == "string"  # inputSchema 透传
            assert len(registry._mcp_clients) == 1  # 生命周期锚点挂上
            assert state.init_count == 1  # eager:装配期恰好握手一次
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 2. 调用归一化:echo 文本 / fail isError → INTERNAL
# ---------------------------------------------------------------------------


def test_echo_and_fail_normalization(http_server):
    """echo → 文本结果;fail(isError=true)→ INTERNAL 错误且消息带 text。"""

    async def main():
        url, _state, _server = http_server
        registry, clients = await _assemble(_spec(url))
        try:
            ok = await registry.dispatch(
                ToolCall(id="e1", name="mcp.web.echo", args={"text": "你好"}), _dispatch_ctx()
            )
            assert ok.ok, ok.error
            assert ok.value == "你好"
            fail = await registry.dispatch(
                ToolCall(id="e2", name="mcp.web.fail", args={}), _dispatch_ctx()
            )
            assert not fail.ok
            assert fail.error is not None and fail.error.kind is ToolErrorKind.INTERNAL
            assert "boom" in fail.error.message
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 3. 超时:TIMEOUT retryable;会话保留(HTTP 与 stdio 的语义差异点)
# ---------------------------------------------------------------------------


def test_timeout_retryable_and_session_kept(http_server):
    """slow + timeout=0.5 → TIMEOUT retryable;会话**保留**(HTTP 每请求独立 POST,
    无 stdio 迟到响应错位风险),后续 echo 在同会话上照常(不重握手)。"""

    async def main():
        url, state, _server = http_server
        state.slow_delay = 5.0  # 远大于 0.5s 客户端超时;测试收尾不等它睡完
        registry, clients = await _assemble(_spec(url, timeout=0.5))
        try:
            session_id = clients[0]._session.session_id
            result = await registry.dispatch(
                ToolCall(id="t1", name="mcp.web.slow", args={}), _dispatch_ctx()
            )
            assert not result.ok
            assert result.error is not None and result.error.kind is ToolErrorKind.TIMEOUT
            assert result.error.retryable is True
            assert clients[0]._alive, "HTTP 超时不拆会话(无共享流错位风险)"
            ok = await registry.dispatch(
                ToolCall(id="t2", name="mcp.web.echo", args={"text": "照常"}), _dispatch_ctx()
            )
            assert ok.ok and ok.value == "照常"
            assert state.init_count == 1, "会话保留:不重握手"
            assert clients[0]._session.session_id == session_id
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 4. SSE 模式:initialize/list/call 全程 SSE 帧;会话中途切换 JSON/SSE 逐请求分流
# ---------------------------------------------------------------------------


def test_sse_mode_end_to_end(http_server):
    """mode=sse 下装配(SSE 帧握手 + 会话头捕获)+ echo;中途切 json 再切回,逐请求分流。"""

    async def main():
        url, state, _server = http_server
        state.mode = "sse"
        registry, clients = await _assemble(_spec(url))
        try:
            assert clients[0]._session.session_id, "SSE 模式下也要捕获 Mcp-Session-Id"
            ok = await registry.dispatch(
                ToolCall(id="s1", name="mcp.web.echo", args={"text": "流式"}), _dispatch_ctx()
            )
            assert ok.ok and ok.value == "流式"
            state.mode = "json"  # 会话中途切换:客户端逐请求按 content-type 分流
            ok2 = await registry.dispatch(
                ToolCall(id="s2", name="mcp.web.echo", args={"text": "单包"}), _dispatch_ctx()
            )
            assert ok2.ok and ok2.value == "单包"
            state.mode = "sse"
            ok3 = await registry.dispatch(
                ToolCall(id="s3", name="mcp.web.echo", args={"text": "再流式"}), _dispatch_ctx()
            )
            assert ok3.ok and ok3.value == "再流式"
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 5. 会话头:server 对缺/错 session 回 400;客户端后续请求全带捕获的 session id
# ---------------------------------------------------------------------------


def test_session_header_required_and_sent(http_server):
    """直连 httpx 不带/乱带 Mcp-Session-Id → 400(spec §Session Management);
    客户端 initialize 之后每个请求都带捕获的 session id。"""
    url, state, _server = http_server

    # 缺 session → 400;错 session → 400(直接打 server,不经过客户端)
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream"}
    with httpx.Client(trust_env=False) as raw:
        missing = raw.post(url, json=rpc, headers=headers)
        assert missing.status_code == 400
        wrong = raw.post(url, json=rpc, headers={**headers, "Mcp-Session-Id": "bogus"})
        assert wrong.status_code == 400

    async def main():
        state.requests.clear()  # 清掉上面 400 探针的记账,只留客户端流量
        registry, clients = await _assemble(_spec(url))
        try:
            session_id = clients[0]._session.session_id
            assert session_id, "initialize 响应的 Mcp-Session-Id 必须捕获"
            await registry.dispatch(
                ToolCall(id="h1", name="mcp.web.echo", args={"text": "x"}), _dispatch_ctx()
            )
            followups = [r for r in state.requests if r["method"] != "initialize"]
            assert followups, "initialize 之后应有若干请求"
            assert all(r["session_id"] == session_id for r in followups), (
                f"后续请求必须全带捕获的 session id: {followups}"
            )
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 6. headers env 间接引用:连接时现读 os.environ;缺席 eager 快速失败
# ---------------------------------------------------------------------------


def test_headers_env_indirection(http_server, monkeypatch):
    """headers {"X-Auth": {"env": VAR}} 现读命中:echo_auth 逐字回显;字面量头同在。"""

    async def main():
        url, _state, _server = http_server
        monkeypatch.setenv(ENV_VAR, ENV_VALUE)
        registry, clients = await _assemble(
            _spec(url, headers={"X-Auth": {"env": ENV_VAR}, "X-Static": "literal"})
        )
        try:
            result = await registry.dispatch(
                ToolCall(id="a1", name="mcp.web.echo_auth", args={}), _dispatch_ctx()
            )
            assert result.ok, result.error
            assert result.value == ENV_VALUE  # 间接引用值随请求到达 server
        finally:
            await _close(clients)

    asyncio.run(main())


def test_headers_env_missing_fails_fast(http_server, monkeypatch):
    """间接引用的环境变量缺席 → eager 快速失败(McpError;不落盘明文)。"""

    async def main():
        url, _state, _server = http_server
        monkeypatch.delenv(ENV_VAR, raising=False)
        with pytest.raises(McpError, match="环境变量"):
            await connect_and_register(LocalPythonToolRegistry(), [
                _spec(url, headers={"X-Auth": {"env": ENV_VAR}})
            ])

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 7. close():best-effort DELETE 终止会话;幂等
# ---------------------------------------------------------------------------


def test_close_sends_delete(http_server):
    """close() → DELETE /mcp 带 session id(记账命中)且会话作废;重复 close 不再发。"""

    async def main():
        url, state, _server = http_server
        _registry, clients = await _assemble(_spec(url))
        session_id = clients[0]._session.session_id
        await _close(clients)
        assert state.delete_calls == [session_id]
        assert session_id not in state.sessions, "DELETE 后 server 侧会话已终止"
        assert not clients[0]._alive
        await _close(clients)  # 幂等:不再发 DELETE
        assert state.delete_calls == [session_id]

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 8. 重连一次:会话被 server 终止(404)→ 重新 initialize 重试成功;双断 → INTERNAL
# ---------------------------------------------------------------------------


def test_reconnect_after_session_expire(http_server):
    """expire 工具:首次 404 作废会话 → 客户端重 initialize(新 session)重试 → 成功。"""

    async def main():
        url, state, _server = http_server
        registry, clients = await _assemble(_spec(url))
        try:
            old_session = clients[0]._session.session_id
            result = await registry.dispatch(
                ToolCall(id="x1", name="mcp.web.expire", args={}), _dispatch_ctx()
            )
            assert result.ok, result.error
            assert result.value == "复活"  # 重连后重试命中(引信已解除)
            assert state.init_count == 2, "404 后必须重新 initialize 一次"
            new_session = clients[0]._session.session_id
            assert new_session and new_session != old_session, "重连须换新会话"
        finally:
            await _close(clients)

    asyncio.run(main())


def test_double_expire_gives_internal(http_server):
    """expire_always 恒 404:重连重试后又 404 → INTERNAL(重试只一次)。"""

    async def main():
        url, _state, _server = http_server
        registry, clients = await _assemble(_spec(url))
        try:
            result = await registry.dispatch(
                ToolCall(id="x2", name="mcp.web.expire_always", args={}), _dispatch_ctx()
            )
            assert not result.ok
            assert result.error is not None and result.error.kind is ToolErrorKind.INTERNAL
            assert "重连后仍失败" in result.error.message
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 9. 协议版本:缺省 2025-03-26;spec.protocol_version 覆盖,协商值随后续请求头带
# ---------------------------------------------------------------------------


def test_protocol_version_negotiation(http_server):
    """缺省请求 2025-03-26 且后续 MCP-Protocol-Version 头按协商值带;
    spec.protocol_version="2024-11-05" → server 回显协商,后续请求头按 2024-11-05 带。"""

    async def main():
        url, state, _server = http_server
        # 缺省:请求 2025-03-26,server 回显,后续头按协商值
        _registry, clients = await _assemble(_spec(url))
        try:
            assert clients[0]._session.protocol_version == "2025-03-26"
            initialize_req = next(r for r in state.requests if r["method"] == "initialize")
            assert initialize_req["protocol_version"] == "", "initialize 本身不带协议版本头"
            followups = [r for r in state.requests if r["method"] != "initialize"]
            assert all(r["protocol_version"] == "2025-03-26" for r in followups)
        finally:
            await _close(clients)
        # 覆盖:server 回显请求值(模拟协商),后续头按 2024-11-05
        state.requests.clear()
        registry2, clients2 = await _assemble(
            _spec(url, protocol_version="2024-11-05"), registry=LocalPythonToolRegistry()
        )
        try:
            assert clients2[0]._session.protocol_version == "2024-11-05"
            await registry2.dispatch(
                ToolCall(id="p1", name="mcp.web.echo", args={"text": "y"}), _dispatch_ctx()
            )
            calls = [r for r in state.requests if r["method"] == "tools/call"]
            assert calls and all(r["protocol_version"] == "2024-11-05" for r in calls)
        finally:
            await _close(clients2)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 10. JSON-RPC error(服务端拒绝):直接 McpError 上抛,不触发重连
# ---------------------------------------------------------------------------


def test_jsonrpc_error_no_reconnect(http_server):
    """未知工具 → -32602 JSON-RPC error → McpError(拒绝语义,重连无意义),init 计数不变。"""

    async def main():
        url, state, _server = http_server
        _registry, clients = await _assemble(_spec(url))
        try:
            with pytest.raises(McpError, match="-32602"):
                await clients[0].call_tool("nope", {}, 5.0)
            assert state.init_count == 1, "JSON-RPC error 不重连"
        finally:
            await _close(clients)

    asyncio.run(main())


# ---------------------------------------------------------------------------
# 11. config 接线:[mcp.servers] url/headers/transport 全键装配;eager 失败 ConfigError
# ---------------------------------------------------------------------------


def test_config_wiring_http(http_server, monkeypatch):
    """build_kernel 全键(url + headers 间接 + transport="http")装配进 kernel;
    连不上的 url → ConfigError 快速失败(eager)。"""

    url, _state, _server = http_server
    monkeypatch.setenv(ENV_VAR, ENV_VALUE)
    kernel = build_kernel(
        {
            "mcp": {
                "servers": {
                    "web": {
                        "url": url,
                        "transport": "http",
                        "headers": {"X-Auth": {"env": ENV_VAR}},
                        "permission": "read",
                        "timeout": 5,
                        "connect_timeout": 5,
                    }
                }
            }
        }
    )
    try:
        assert kernel.tools.has("mcp.web.echo")
        assert len(kernel.tools._mcp_clients) == 1
    finally:
        asyncio.run(_close(kernel.tools._mcp_clients))

    with pytest.raises(ConfigError, match="eager"):
        build_kernel({"mcp": {"servers": {"web": {"url": "http://127.0.0.1:1/mcp"}}}})
