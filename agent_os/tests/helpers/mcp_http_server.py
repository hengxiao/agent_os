"""极简 MCP Streamable HTTP 假服务器(tests/tools/test_mcp_http.py 的罐头对端;FastAPI)。

协议:MCP 官方 spec 2025-03-26 版族 "Streamable HTTP" 小节
(https://modelcontextprotocol.io/specification/2025-03-26/basic/transports):

- 单端点 ``POST /mcp``;initialize 回 serverInfo 并发 ``Mcp-Session-Id`` 响应头,
  后续请求缺/错 session → 400(照 spec §Session Management 的 SHOULD);
- 响应模式 ``state.mode``:``"json"`` 单包 / ``"sse"`` text/event-stream
  (帧形 ``: keepalive`` 注释帧 + ``event: message\\ndata: {json}\\n\\n``;
  两种模式客户端都必须支持,且 initialize 也可走 SSE);
- notifications/* → 202 无体;tools/list 罐头(见 ``TOOLS``);
  未知工具 → -32602,未知方法 → -32601;
- ``DELETE /mcp`` 终止会话并记账(``state.delete_calls``);``GET /mcp`` → 405
  (standalone SSE 流不做,spec 允许 405)。

罐头工具:

- ``echo``:text 回显 ``args.text``;
- ``fail``:``isError=true`` 的工具级失败;
- ``slow``:``await asyncio.sleep(state.slow_delay)`` 才回(超时测试用);
- ``echo_auth``:回显本请求的 ``X-Auth`` 头(headers env 间接测试用);
- ``expire``:``state.expire_armed`` 时首次调用作废当前会话并回 404(spec 的
  会话终止语义——客户端须重新 initialize 重试),之后正常回 text(重连一次测试用);
- ``expire_always``:恒作废会话回 404(重试只一次 → INTERNAL 测试用)。

``ServerState.requests`` 全量记账每次 POST 的 method/session_id/x_auth/
protocol_version,供测试断言客户端行为(带 session 头、协商版本头等)。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

TOOLS: list[dict] = [
    {
        "name": "echo",
        "description": "回显 args.text。Use when 测试连通性;Do not use when 其他。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "fail",
        "description": "恒失败(isError=true)。Use when 测试错误归一化;Do not use when 其他。",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "slow",
        "description": "睡 state.slow_delay 秒才应答。Use when 测试超时;Do not use when 其他。",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "echo_auth",
        "description": "回显本请求 X-Auth 头。Use when 测试 headers 传递;Do not use when 其他。",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "expire",
        "description": "首次调用作废会话回 404。Use when 测试重连重试;Do not use when 其他。",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "expire_always",
        "description": "恒作废会话回 404。Use when 测试重试只一次;Do not use when 其他。",
        "inputSchema": {"type": "object"},
    },
]


@dataclass
class ServerState:
    """假服务器可变状态(测试可就地改;全量记账供断言)。"""

    #: 响应模式:"json" 单包 | "sse" text/event-stream(逐请求生效,可中途切换)
    mode: str = "json"
    #: slow 工具的应答延迟秒数(超时测试用,须远大于客户端 timeout)
    slow_delay: float = 30.0
    #: 有效会话集合(initialize 发,DELETE/expire 作废)
    sessions: set[str] = field(default_factory=set)
    #: 每次 POST 的记账:{method, session_id, x_auth, protocol_version}
    requests: list[dict[str, Any]] = field(default_factory=list)
    #: DELETE /mcp 收到的 session id 依次记账
    delete_calls: list[str] = field(default_factory=list)
    #: initialize 计数(重连测试断言重握手)
    init_count: int = 0
    #: expire 工具引信:True 时首次调用 404 并解除(重连后重试须成功)
    expire_armed: bool = True


def _text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _respond(state: ServerState, req_id: Any, result: dict) -> Response:
    """按 state.mode 回 JSON-RPC result:json 单包 或 SSE 帧(前导注释帧练客户端跳过)。"""
    message = {"jsonrpc": "2.0", "id": req_id, "result": result}
    if state.mode == "sse":
        frame = ": keepalive\n\nevent: message\ndata: " + json.dumps(message, ensure_ascii=False) + "\n\n"
        return Response(content=frame, media_type="text/event-stream")
    return JSONResponse(message)


def _respond_error(state: ServerState, req_id: Any, code: int, message: str) -> Response:
    """按 state.mode 回 JSON-RPC error(id 保留;HTTP 仍 200,错误在协议层)。"""
    payload = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    if state.mode == "sse":
        frame = "event: message\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        return Response(content=frame, media_type="text/event-stream")
    return JSONResponse(payload)


def _initialize(state: ServerState, body: dict) -> Response:
    """initialize 握手:发新会话 id(Mcp-Session-Id 响应头),协议版本回显请求值
    (模拟协商:客户端可断言协商值被记录并随后续 MCP-Protocol-Version 头带回)。"""
    state.init_count += 1
    session_id = uuid.uuid4().hex
    state.sessions.add(session_id)
    params = body.get("params") or {}
    requested = params.get("protocolVersion")
    result = {
        "protocolVersion": requested if isinstance(requested, str) and requested else "2025-03-26",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "fake-mcp-http", "version": "0.0.1"},
    }
    resp = _respond(state, body.get("id"), result)
    resp.headers["Mcp-Session-Id"] = session_id
    return resp


async def _call(state: ServerState, session_id: str, req_id: Any, name: str, args: dict, request: Request) -> Response:
    """tools/call 按名分发(expire* 走 HTTP 层 404,不回 JSON-RPC 响应)。"""
    if name == "expire":
        if state.expire_armed:
            state.expire_armed = False
            state.sessions.discard(session_id)  # 会话作废:本请求 404,旧会话后续请求也 404
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "session expired"}},
                status_code=404,
            )
        return _respond(state, req_id, _text("复活"))
    if name == "expire_always":
        state.sessions.discard(session_id)
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "session expired"}},
            status_code=404,
        )
    if name == "echo":
        return _respond(state, req_id, _text(str(args.get("text", ""))))
    if name == "fail":
        return _respond(
            state, req_id, {"content": [{"type": "text", "text": "boom: 故意失败"}], "isError": True}
        )
    if name == "slow":
        await asyncio.sleep(state.slow_delay)
        return _respond(state, req_id, _text("ok"))
    if name == "echo_auth":
        return _respond(state, req_id, _text(request.headers.get("x-auth", "")))
    return _respond_error(state, req_id, -32602, f"unknown tool: {name}")


def create_app(state: ServerState) -> FastAPI:
    """假服务器 app(状态外置:测试经 ``state`` 切模式/改延迟/读记账)。"""
    app = FastAPI()

    @app.post("/mcp")
    async def post_mcp(request: Request) -> Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}},
                status_code=400,
            )
        if not isinstance(body, dict):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}},
                status_code=400,
            )
        method = body.get("method")
        session_id = request.headers.get("mcp-session-id", "")
        state.requests.append(
            {
                "method": method,
                "session_id": session_id,
                "x_auth": request.headers.get("x-auth", ""),
                "protocol_version": request.headers.get("mcp-protocol-version", ""),
            }
        )
        if method == "initialize":
            return _initialize(state, body)
        # initialize 之外的请求必须带有效会话(spec §Session Management:缺 → 400)
        if session_id not in state.sessions:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "missing/invalid Mcp-Session-Id"}},
                status_code=400,
            )
        if isinstance(method, str) and method.startswith("notifications/"):
            return Response(status_code=202)
        if method == "tools/list":
            return _respond(state, body.get("id"), {"tools": TOOLS})
        if method == "tools/call":
            params = body.get("params") or {}
            return await _call(
                state,
                session_id,
                body.get("id"),
                str(params.get("name")),
                params.get("arguments") or {},
                request,
            )
        if body.get("id") is not None:
            return _respond_error(state, body.get("id"), -32601, f"method not found: {method}")
        return Response(status_code=202)

    @app.delete("/mcp")
    async def delete_mcp(request: Request) -> Response:
        session_id = request.headers.get("mcp-session-id", "")
        state.delete_calls.append(session_id)
        if session_id in state.sessions:
            state.sessions.discard(session_id)
            return Response(status_code=200)
        return Response(status_code=404)

    @app.get("/mcp")
    async def get_mcp() -> Response:
        # standalone SSE 流不做(spec 允许 405 Method Not Allowed)
        return Response(status_code=405)

    return app
