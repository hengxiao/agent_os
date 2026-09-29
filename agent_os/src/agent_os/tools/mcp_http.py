"""MCP(Model Context Protocol)Streamable HTTP 传输适配器(tools/mcp.py 的 HTTP 侧兄弟)。

协议出处:MCP 官方 spec **2025-03-26** 版族 "Streamable HTTP" 小节
(https://modelcontextprotocol.io/specification/2025-03-26/basic/transports);
``MCP-Protocol-Version`` 请求头为 2025-06-18 版引入(旧版 server 收到未知头无害,
缺省即假设 2025-03-26),故协商后一并带上。仓内无任何协议细节文档,以官方 spec 为准。

与 :class:`agent_os.tools.mcp.McpStdioClient` 同接口(``tools`` 缓存 /
``call_tool(name, args, timeout)`` / ``close()`` / ``connect[_sync]``),
``McpTool`` 与 ``connect_and_register(+_sync)`` 整段复用;IO 模型同 stdio 先例:
阻塞 ``httpx.Client`` 核心 + 单飞锁 + ``asyncio.to_thread`` async 包装 + 纯同步
装配入口(``connect_sync`` 不碰 event loop,理由见 mcp.py 模块 docstring 的
"装配 loop 与 run loop 非同一个" 论证——阻塞核心与 loop 完全无关)。

协议面(只覆盖 initialize / notifications/initialized / tools/list / tools/call
四个方法,与 stdio 对齐):

- 单端点 POST(spec.url);每请求必带 ``Accept: application/json, text/event-stream``
  (spec 强制双 content-type);``spec.headers`` 平铺注入(值支持
  ``{"env": "VAR"}`` 间接引用,连接时现读 os.environ,不落盘明文,同 mcp.py
  env 先例);httpx 不读代理/netrc 环境变量(``trust_env=False``,行为确定性);
- 响应分流:``application/json`` → 单包 JSON-RPC 响应;``text/event-stream`` →
  SSE 帧逐条解析(帧形 ``data: {json}\\n\\n``,\\r\\n 兼容;event:/id:/``:``
  注释行忽略,坏帧丢弃——同 host/tui/kernel/sse.py 先例),取 id 匹配者,
  流尽未匹配归协议垃圾;
- 会话管理:initialize 响应的 ``Mcp-Session-Id`` 头捕获后,后续请求(含 DELETE)
  必带(spec 的 MUST);协商返回的 ``protocolVersion`` 记录,initialize 之后请求
  带 ``MCP-Protocol-Version`` 头;server 回 404(会话被终止)→ 按 spec 重新
  initialize 新会话(归可重连类);
- notifications/initialized → 期待 202 Accepted 无体(宽容:任意 2xx 皆可);
- ``close()``:best-effort HTTP DELETE 终止会话(spec 的 SHOULD;server 已死/
  回 405 都无碍,吞掉),再关 httpx client;幂等;atexit 兜底。

失败归一(与 stdio 对齐):连接错/断流/5xx/404/协议垃圾 → 本次调用关会话、
重连一次、重试一次,再失败抛 :class:`McpError`(dispatch 归一 INTERNAL);
其余 4xx 与 JSON-RPC error → 直接 McpError(服务端拒绝,重连无意义);
超时(httpx 截止/帧 deadline/外层看门狗)→ 上抛内置 TimeoutError(dispatch
归一 TIMEOUT retryable)。与 stdio 不同处:HTTP 每请求独立 POST,无共享流错位
风险,超时/取消**不**拆会话(迟到的响应随连接关闭,会话状态仍可信,下次调用
免重握手)。

GET standalone SSE 流(server→client 主动推送)与 resumability(Last-Event-ID)
不做:tools/call 请求-响应模型用不到(协议面留开口:响应分流对 event 来源无假设,
扩展只是新增 GET 流读循环)。
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from agent_os import __version__
from agent_os.tools.mcp import _NAME_RE, McpError, McpServerSpec, _McpTransportError

_log = logging.getLogger("agent_os.tools.mcp_http")

__all__ = ["McpHttpClient"]


@dataclass
class _HttpSession:
    """一次 HTTP 会话的全部连接态(initialize 握手成功才建立;重连整体换新)。"""

    client: httpx.Client
    #: spec.headers 解析产物(env 间接已现读)
    base_headers: dict[str, str]
    #: initialize 响应 Mcp-Session-Id 头(server 可不发,空 = 无会话态)
    session_id: str = ""
    #: 协商定的协议版本(initialize 之后随 MCP-Protocol-Version 请求头带)
    protocol_version: str = ""


def _resolve_indirect(server_name: str, key: str, raw: Any) -> str:
    """headers 值解析:str 字面量直传;``{"env": "VAR"}`` 现读 os.environ(缺席 → McpError)。

    与 mcp.py ``_resolve_env_value`` 同语义(不落盘明文;重连重新解析,token 轮换即生效)。
    形态合法性由装配层(runtime/config.py)保证;直造 spec 传入坏形态此处按间接引用
    取 ``env`` 键,取不到同样快速失败。
    """
    if isinstance(raw, str):
        return raw
    var = str(raw.get("env", ""))
    value = os.environ.get(var)
    if value is None:
        raise McpError(
            f"server {server_name!r} 的 headers.{key} 间接引用的环境变量 {var} 不存在"
            f"(不落盘明文;请先 export {var}=...)"
        )
    return value


class McpHttpClient:
    """单个 MCP server 的 Streamable HTTP 连接(会话生命周期 = kernel 寿命)。"""

    #: initialize 请求的缺省协议版本(spec 2025-03-26 版族;``spec.protocol_version``
    #: 可覆盖;server 协商返回值优先,随后续 MCP-Protocol-Version 请求头带)
    DEFAULT_PROTOCOL_VERSION = "2025-03-26"

    def __init__(self, spec: McpServerSpec) -> None:
        if not _NAME_RE.fullmatch(spec.name):
            raise McpError(
                f"server 名 {spec.name!r} 非法(只许 [A-Za-z0-9_-];进工具命名空间 mcp.<server>.<tool>)"
            )
        if not spec.url.startswith(("http://", "https://")):
            raise McpError(f"server {spec.name!r} 的 url 须为 http(s):// URL,得到: {spec.url!r}")
        self._spec = spec
        self._session: _HttpSession | None = None
        self._next_id = 0
        #: tools/list 缓存(注册数据源;eager 语义:连接成功即已就位)
        self.tools: list[dict[str, Any]] = []
        #: 单飞:同一会话 in-flight 请求最多一个(SSE 流按 id 配对的正确性依赖此)
        self._call_lock = asyncio.Lock()
        #: _ensure_connected 单飞(并发断线重连只握手一次)
        self._connect_lock = asyncio.Lock()
        # 兜底:宿主忘 close/异常退出也尽力终止会话、关连接池(close 之后为 no-op)
        atexit.register(self._atexit_close)

    # ------------------------------------------------------------------
    # 连接(eager:initialize 握手 + notifications/initialized + tools/list 缓存)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """建会话 + 握手 + tools/list(``connect_timeout`` 全程包住)。

        失败清理连接再抛 :class:`McpError`(装配层包装为 ConfigError 快速失败)。
        """
        try:
            # 外层看门狗兜底(内层 httpx 截止先到,这层防病态卡死)
            await asyncio.wait_for(
                asyncio.to_thread(self.connect_sync),
                timeout=self._spec.connect_timeout + 1.0,
            )
        except TimeoutError as e:
            raise McpError(
                f"server {self._spec.name!r} 握手超过 {self._spec.connect_timeout}s"
            ) from e

    def connect_sync(self) -> None:
        """同步版 :meth:`connect`(``build_kernel`` 是同步函数,装配路径不碰 event loop;

        阻塞 httpx 核心的 IO 与 loop 无关,同步路径零 loop 依赖,同 stdio connect_sync)。
        """
        try:
            self._connect_blocking()
        except Exception as e:
            self._close_blocking()
            if isinstance(e, McpError):
                raise
            raise McpError(
                f"server {self._spec.name!r} 连接失败: {type(e).__name__}: {e}"
            ) from e

    def _connect_blocking(self) -> None:
        """建会话并握手(阻塞;httpx 截止保证有界)。失败由调用方清理连接。"""
        self._close_blocking()  # 重连路径:收掉旧会话(best-effort DELETE + client close)
        base_headers = {
            key: _resolve_indirect(self._spec.name, key, raw)
            for key, raw in self._spec.headers.items()
        }
        session = _HttpSession(
            # trust_env=False:不读 HTTP_PROXY/netrc(行为确定性,同 stdio 不继承宿主 env 的精神)
            client=httpx.Client(trust_env=False),
            base_headers=base_headers,
        )
        requested_version = self._spec.protocol_version or self.DEFAULT_PROTOCOL_VERSION
        timeout = self._spec.connect_timeout
        try:
            result, headers = self._rpc(
                session,
                "initialize",
                {
                    "protocolVersion": requested_version,
                    "capabilities": {},
                    "clientInfo": {"name": "agent-os", "version": __version__},
                },
                timeout,
            )
            # 捕获会话 id(server 可不发;发了则后续请求必带,spec 的 MUST)
            session.session_id = headers.get("mcp-session-id", "")
            negotiated = result.get("protocolVersion")
            session.protocol_version = (
                negotiated if isinstance(negotiated, str) and negotiated else requested_version
            )
            self._notify(session, "notifications/initialized", timeout)
            result, _ = self._rpc(session, "tools/list", {}, timeout)
        except BaseException:
            # 握手任何一步失败都关掉这个半连会话(调用方还会再 _close_blocking 一次,幂等)
            with contextlib.suppress(Exception):
                session.client.close()
            raise
        tools = result.get("tools")
        if not isinstance(tools, list):
            with contextlib.suppress(Exception):
                session.client.close()
            raise McpError(f"server {self._spec.name!r} 的 tools/list 响应缺 tools 数组")
        self._session = session
        self.tools = tools

    # ------------------------------------------------------------------
    # JSON-RPC over HTTP(阻塞核心;每请求独立 POST)
    # ------------------------------------------------------------------

    def _request_headers(self, session: _HttpSession) -> dict[str, str]:
        """每请求头:Accept 双 content-type(spec 强制)+ spec.headers 平铺 + 会话头
        (initialize 之后才有:Mcp-Session-Id 必带,MCP-Protocol-Version 按协商值)。"""
        headers = {"Accept": "application/json, text/event-stream", **session.base_headers}
        if session.session_id:
            headers["Mcp-Session-Id"] = session.session_id
        if session.protocol_version:
            headers["MCP-Protocol-Version"] = session.protocol_version
        return headers

    def _rpc(
        self, session: _HttpSession, method: str, params: dict[str, Any], timeout: float
    ) -> tuple[dict[str, Any], httpx.Headers]:
        """发一个 JSON-RPC 请求并拿到 id 匹配的响应(响应头一并返回,initialize 要取会话头)。

        JSON-RPC error 响应与其余 4xx 抛 :class:`McpError`(服务端拒绝,重连无意义);
        非法 JSON/意外 content-type/SSE 流尽未匹配/5xx/404(会话被 server 终止,spec
        §Session Management 要求 client 重新 initialize)抛 :class:`_McpTransportError`
        (可重连类);httpx.TimeoutException 归内置 TimeoutError(dispatch 归一 TIMEOUT)。
        """
        self._next_id += 1
        req_id = self._next_id
        message = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        try:
            with session.client.stream(
                "POST",
                self._spec.url,
                json=message,
                headers=self._request_headers(session),
                timeout=httpx.Timeout(timeout),
            ) as resp:
                self._check_status(resp, method)
                content_type = resp.headers.get("content-type", "")
                if content_type.startswith("application/json"):
                    resp.read()
                    msg = self._parse_json(resp.content, method)
                    return self._handle_message(msg, method, req_id), resp.headers
                if content_type.startswith("text/event-stream"):
                    deadline = time.monotonic() + timeout
                    for msg in self._iter_sse(resp, deadline):
                        if not isinstance(msg, dict) or msg.get("id") != req_id:
                            continue  # 流内 notification/异 id(单飞保证 in-flight 只有一个):跳过
                        return self._handle_message(msg, method, req_id), resp.headers
                    raise _McpTransportError(
                        f"server {self._spec.name!r} 的 {method} SSE 流结束未见 id={req_id} 的响应"
                    )
                raise _McpTransportError(
                    f"server {self._spec.name!r} 的 {method} 返回意外 content-type: {content_type!r}"
                )
        except httpx.TimeoutException as e:
            raise TimeoutError(f"server {self._spec.name!r} 的 {method} 响应超时") from e

    def _notify(self, session: _HttpSession, method: str, timeout: float) -> None:
        """发 JSON-RPC notification(无 id):spec 规定 202 Accepted 无体;宽容任意 2xx
        (有 server 回 200)。错误状态归一同 :meth:`_check_status`。"""
        try:
            resp = session.client.post(
                self._spec.url,
                json={"jsonrpc": "2.0", "method": method},
                headers=self._request_headers(session),
                timeout=httpx.Timeout(timeout),
            )
        except httpx.TimeoutException as e:
            raise TimeoutError(f"server {self._spec.name!r} 的 {method} 超时") from e
        self._check_status(resp, method)

    def _check_status(self, resp: httpx.Response, method: str) -> None:
        """HTTP 状态归一:2xx 放行;404 → 可重连类(会话被 server 终止,spec 要求重新
        initialize);5xx → 可重连类(server 临时故障);其余 4xx → McpError(拒绝,重连无意义)。"""
        if resp.status_code < 400:
            return
        resp.read()
        snippet = resp.text[:200]
        if resp.status_code == 404:
            raise _McpTransportError(
                f"server {self._spec.name!r} 的 {method} 回 404(会话已终止,须重新 initialize): {snippet}"
            )
        if resp.status_code >= 500:
            raise _McpTransportError(
                f"server {self._spec.name!r} 的 {method} 回 {resp.status_code}: {snippet}"
            )
        raise McpError(
            f"server {self._spec.name!r} 的 {method} 被 HTTP {resp.status_code} 拒绝: {snippet}"
        )

    def _parse_json(self, content: bytes, method: str) -> Any:
        """单包 JSON 解析;非法 JSON 归协议垃圾(可重连类)。"""
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise _McpTransportError(
                f"server {self._spec.name!r} 的 {method} 返回非法 JSON: {content[:200]!r}"
            ) from e

    def _handle_message(self, msg: Any, method: str, req_id: int) -> dict[str, Any]:
        """id 匹配响应的归一(单包/SSE 帧共用):JSON-RPC error → McpError;
        响应非表或 id 不符 → 协议垃圾(可重连类);result 非表 → {}(同 stdio)。"""
        if not isinstance(msg, dict) or msg.get("id") != req_id:
            raise _McpTransportError(
                f"server {self._spec.name!r} 的 {method} 响应 id 不匹配"
                f"(期待 {req_id}): {str(msg)[:200]!r}"
            )
        error = msg.get("error")
        if error is not None:
            if isinstance(error, dict):
                raise McpError(
                    f"server {self._spec.name!r} 的 {method} 被拒: "
                    f"[{error.get('code', '?')}] {error.get('message', '')}"
                )
            raise McpError(f"server {self._spec.name!r} 的 {method} 被拒: {error}")
        result = msg.get("result")
        return result if isinstance(result, dict) else {}

    def _iter_sse(self, resp: httpx.Response, deadline: float) -> Iterator[Any]:
        """SSE 帧 → JSON 消息生成器(``data:`` 行按 spec 多行 \\n 拼接,空行成帧;
        \\r\\n 兼容;event:/id:/retry:/``:`` 注释行忽略;坏帧丢弃——与
        host/tui/kernel/sse.py 先例同策略)。

        超过 deadline 抛内置 TimeoutError:keepalive 注释帧会刷新 httpx 读截止,
        总时限由本 deadline 兜住(外层 wait_for 看门狗再兜一层)。
        """
        buf = ""
        data: list[str] = []  # 当前帧的 data 行累积
        for chunk in resp.iter_text():
            if time.monotonic() > deadline:
                raise TimeoutError(f"server {self._spec.name!r} 的响应超时(SSE 流未收尾)")
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.rstrip("\r")
                if not line:
                    if data:  # 空行 = 帧尾;纯注释/keepalive 帧(无 data)跳过
                        raw = "\n".join(data)
                        data = []
                        try:
                            yield json.loads(raw)
                        except json.JSONDecodeError:
                            continue  # 坏帧丢弃(容错优先,同 sse.py 先例)
                    continue
                if line.startswith(":"):
                    continue  # 注释/keepalive 行
                if line.startswith("data:"):
                    data.append(line[5:].removeprefix(" "))  # spec: 去单个前导空格
                # event:/id:/retry: 行忽略(只按 JSON-RPC id 配对,不关心 SSE event 名)
        # 流尽:残余未收尾帧丢弃(server 未按 spec 先回响应再关流 → 调用方按未匹配处理)

    # ------------------------------------------------------------------
    # 调用(tools/call;传输失败重建会话重连一次重试一次)
    # ------------------------------------------------------------------

    async def call_tool(self, name: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        """``tools/call``,返回 result dict(归一化由 :class:`McpTool` 负责)。

        连接错/断流/5xx/404/协议垃圾 → 关会话、重连一次、重试一次,再失败抛
        :class:`McpError`;超时(httpx 截止/帧 deadline/外层看门狗)→ 上抛内置
        TimeoutError(dispatch 归一 TIMEOUT retryable)——会话**保留**:HTTP 每请求
        独立 POST,无 stdio 那种共享流错位风险,迟到的响应随连接关闭即清;
        被取消 → 直接传播(to_thread 线程由 httpx 截止保证有界退出)。
        """
        async with self._call_lock:
            for attempt in (1, 2):
                try:
                    await self._ensure_connected()
                    return await asyncio.wait_for(
                        asyncio.to_thread(self._call_blocking, name, args, timeout),
                        timeout=timeout + 1.0,  # 外层看门狗;内层 httpx 截止先到
                    )
                except TimeoutError:
                    raise  # 会话保留(见 docstring)
                except asyncio.CancelledError:
                    raise
                except (httpx.HTTPError, _McpTransportError) as e:
                    await self._abort()
                    if attempt == 2:
                        raise McpError(
                            f"server {self._spec.name!r} 调用 {name!r} 重连后仍失败: "
                            f"{type(e).__name__}: {e}"
                        ) from e
                    _log.warning(
                        "server %s 调用 %s 传输失败(%s),重建会话重试一次",
                        self._spec.name, name, e,
                    )
        raise AssertionError("unreachable")  # pragma: no cover — 循环两途必 return/raise

    def _call_blocking(self, name: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        """阻塞版 tools/call(to_thread 线程内执行;httpx 截止保证线程有界退出)。"""
        session = self._session
        if session is None:
            raise _McpTransportError(f"server {self._spec.name!r} 未连接")
        result, _ = self._rpc(session, "tools/call", {"name": name, "arguments": args}, timeout)
        return result

    async def _ensure_connected(self) -> None:
        """已建会话直接返回;否则单飞重连(_connect_lock 防并发双连双会话)。"""
        if self._session is not None:
            return
        async with self._connect_lock:
            if self._session is None:
                await self.connect()

    @property
    def _alive(self) -> bool:
        return self._session is not None

    # ------------------------------------------------------------------
    # 关闭与清理(best-effort DELETE 终止会话;atexit 兜底)
    # ------------------------------------------------------------------

    async def _abort(self) -> None:
        """会话作废并关闭(传输失败后的统一收尾;下次调用经 _ensure_connected 重连)。"""
        await asyncio.to_thread(self._close_blocking)

    async def close(self) -> None:
        """显式关闭:best-effort DELETE 终止会话 + 关 httpx client(kernel 收尾/测试清理);幂等。"""
        await asyncio.to_thread(self._close_blocking)

    def _close_blocking(self) -> None:
        """关当前会话(幂等;to_thread 内或同步路径用)。

        DELETE 终止会话是 spec 的 SHOULD:best-effort,短截止,任何失败吞掉
        (server 已死/回 405 都无碍;会话 server 侧终会过期)。
        """
        session, self._session = self._session, None
        if session is None:
            return
        if session.session_id:
            headers = {**session.base_headers, "Mcp-Session-Id": session.session_id}
            if session.protocol_version:
                headers["MCP-Protocol-Version"] = session.protocol_version
            with contextlib.suppress(Exception):
                session.client.delete(
                    self._spec.url, headers=headers, timeout=httpx.Timeout(2.0)
                )
        with contextlib.suppress(Exception):
            session.client.close()

    def _atexit_close(self) -> None:
        """atexit 兜底(宿主崩溃/忘 close 也尽力终止会话;close 之后是 no-op)。同步、绝不抛。"""
        with contextlib.suppress(Exception):
            self._close_blocking()
