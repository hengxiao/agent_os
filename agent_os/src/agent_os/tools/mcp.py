"""MCP(Model Context Protocol)stdio 适配器(docs/DESIGN.md §8.3 供应链清单;工具侧适配)。

零新依赖:自实现极简 JSON-RPC 2.0 客户端(newline-delimited JSON over stdio),
方法面 ``initialize`` / ``notifications/initialized`` / ``tools/list`` / ``tools/call``,
外加 server 宣告对应 capability 时的 ``resources/list`` / ``prompts/list``
(握手期缓存)与 ``resources/read`` / ``prompts/get``(派生工具走它们;
未宣告的方法绝不上线)。Streamable HTTP 传输见 :mod:`agent_os.tools.mcp_http`
(同 client 接口,``McpTool``/派生工具/装配层整段复用)。

协议版本钉扎(定案):initialize 请求值即钉扎值(缺省 2024-11-05,
``spec.protocol_version`` 显式给覆盖),server 回应 ``protocolVersion`` ≠ 请求值
→ McpError 拒绝连接——拒绝静默降级:协议行为随版本漂移,静默接受不可审计,
确要换版本须显式配置(配置键语义即"换钉扎值",非"参与协商")。

派生工具(定案):握手时 server 宣告 ``resources`` capability → 缓存 resources/list
并注册单个 ``mcp.<server>.resource_read``(参数 ``uri``);宣告 ``prompts`` →
缓存 prompts/list 并注册 ``mcp.<server>.prompt_get``(参数 ``name``/``arguments``)。
清单描述逐条过注入扫描(命中该条整段占位)+ 截断(同 McpTool 先例);
二进制资源块 spill 进 blob store 回 ``blob://`` ref(同 std_web.py 的
``ctx.blob.put`` 先例,ctx/blob 缺席退化为显式占位)。

连接模型(eager,定案):装配期(``connect_and_register`` / ``build_kernel`` 的
``[mcp.servers]`` 接线)拉起 server 子进程、完成握手与 tools/list、把全部工具以
``mcp.<server>.<tool>`` 命名空间注册进 LocalPythonToolRegistry;连接失败快速失败
(装配层归 ConfigError)——坏 server 静默缺席会让技能白名单形同虚设(以为有闸,
实际工具根本没来)。不做懒连接。

IO 模型:阻塞管道 + ``select`` 截止读行,经 ``asyncio.to_thread`` 接入 async。
不选 ``asyncio.create_subprocess_exec``:其子进程管道绑定创建它的 event loop,
而 ``build_kernel`` 是同步函数、装配 loop 与 run 执行 loop 不是同一个(Web 宿主
每 run 独立线程装配),loop 亲和会把连接打死在装配 loop 上。阻塞 fd + to_thread
与 loop 完全无关:装配路径走纯同步 ``connect_sync``(不碰任何 loop,宿主在已跑
event loop 的线程里装配也安全),运行路径走 async 包装,共用同一阻塞核心。

进程生命周期(定案):连接随 registry(kernel 寿命);``close()`` 显式杀进程组
(``start_new_session`` + ``os.killpg`` SIGKILL,照 tools/builtins.py shell_exec
先例),atexit 兜底不留孤儿;断管/进程死/协议垃圾 → 本次调用重连一次、重试一次,
再失败抛 :class:`McpError`(dispatch 归一 INTERNAL);调用超时/被取消 → 杀连接
上抛(dispatch 归一 TIMEOUT retryable)——超时后的连接不可信(迟到的响应会与
下一请求错位),必须重建;旧尸首在重连前 wait() 收掉,不留 zombie。

供应链纪律(§8.3):工具描述做注入扫描(共用 :mod:`agent_os.injection` 正则集)——
命中**整段弃用**为占位(同 context/manager.py recall 命中即弃整条经验的"宁缺毋滥"
先例;截断保留前半段仍可能夹带指令铺垫),干净描述超 500 字符截断并标记;
``untrusted_source=True`` 强制;server 级默认 permission=READ(配置可升
write/net/exec);``concurrency_safe=False``(远端进程状态不可知,fail-safe);
``confirm`` 逐 server 可开;子进程**不继承宿主环境变量**,env 只传配置解析值
(``{env = "VAR"}`` 间接引用,连接/重连时现读 os.environ,不落盘明文,
同 [credentials] 动态解析先例);npx 末参是无版本后缀的包规格(``@scope/name``
或裸名,缺 ``@x.y.z``)→ 装配期 warning 引导钉版(版本漂移不可审计;warning 不阻断)。

生命周期锚点:Kernel/registry 均无 close 钩子,clients 挂
``registry._mcp_clients``(``connect_and_register`` 自动挂),进程清理由
各 client 的 ``close()`` / atexit 兜底。
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import contextlib
import json
import logging
import os
import re
import select
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent_os import __version__
from agent_os.api.v1 import (
    Permission,
    ToolContext,
    ToolError,
    ToolErrorKind,
    ToolResult,
    ToolSpec,
)
from agent_os.injection import looks_suspicious

if TYPE_CHECKING:
    # 仅注解用(运行期延迟导入见 _client_for;mcp_http 反向 import 本模块,模块级互引会循环)
    from agent_os.tools.mcp_http import McpHttpClient

_log = logging.getLogger("agent_os.tools.mcp")

__all__ = [
    "McpError",
    "McpServerSpec",
    "McpStdioClient",
    "McpTool",
    "connect_and_register",
    "connect_and_register_sync",
]

#: server/工具名合法字符(进 ``mcp.<server>.<tool>`` 命名空间;同 TOML 裸键字符集)
_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")

#: 工具描述长度上限(字符);超出截断并标记(描述进静态前缀,§7.4 不变量 5)
_DESCRIPTION_MAX_CHARS = 500

#: npx 包规格且无版本后缀的形态:@scope/name 或裸名(首字符非 ``-``,排除 -y/--yes
#: 等 flag;``pkg@1.2.3``/``@scope/pkg@1.2.3`` 已钉版,不匹配)
_NPX_UNPINNED_PKG_RE = re.compile(r"@[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+|(?!-)[A-Za-z0-9._~-]+")


class McpError(RuntimeError):
    """MCP server 启动/握手/协议/调用失败(装配层归 ConfigError;dispatch 归一 INTERNAL)。"""


class _McpTransportError(McpError):
    """传输层失败(协议垃圾等;与 EOFError/OSError 同归"可重连重试"类)。"""


@dataclass
class McpServerSpec:
    """单个 MCP server 的装配规格(``[mcp.servers.<name>]`` 段解析产物)。

    传输二选一:stdio(``command`` 非空)或 Streamable HTTP(``url`` 非空);
    ``transport`` = auto(缺省,按键自动判:url → http,否则 stdio)/stdio/http
    (显式给与键不符装配层拒,见 runtime/config.py ``_mcp_servers``)。
    ``env``(stdio 子进程环境)/``headers``(http 请求头)值两种形态:str 字面量
    直传;``{"env": "VAR"}`` 间接引用——连接时现读 ``os.environ``(不落盘明文;
    重连重新解析,token 轮换即生效),变量缺席抛 :class:`McpError`(eager 快速
    失败,防静默缺席)。``protocol_version`` 缺省随传输(stdio 2024-11-05 /
    http 2025-03-26),显式给覆盖 initialize 请求值——请求值即钉扎值:server
    回应 ≠ 请求 → McpError 拒连(换版本须显式配置,不静默降级)。
    """

    name: str
    command: list[str] = field(default_factory=list)
    env: dict[str, Any] = field(default_factory=dict)
    permission: Permission = Permission.READ
    timeout: float = 30.0
    confirm: bool = False
    connect_timeout: float = 10.0
    url: str = ""
    headers: dict[str, Any] = field(default_factory=dict)
    transport: str = "auto"
    protocol_version: str = ""


def _warn_unpinned_npx(spec: McpServerSpec) -> None:
    """npx 末参是无版本后缀的包规格 → 一条 warning 引导钉 ``@x.y.z``(§8.3 供应链清单)。

    npx 对未钉版包默认拉最新,版本漂移不可审计;warning 只引导不阻断。
    已钉版/非 npx 命令/末参是 flag 或路径 → 静默。
    """
    command = spec.command
    if not command or os.path.basename(command[0]).lower() not in ("npx", "npx.cmd"):
        return
    last = command[-1]
    if _NPX_UNPINNED_PKG_RE.fullmatch(last):
        _log.warning(
            "server %s 的 npx 包 %r 未钉版本(npx 默认拉最新,版本漂移不可审计;"
            "建议钉版 %s@x.y.z,§8.3 供应链清单)",
            spec.name,
            last,
            last,
        )


class _LineReader:
    """newline-delimited JSON 的截止读行器(``select`` 等可读 + ``os.read``,纯 fd)。

    不用 Popen 自带的 buffered reader:其 ``readline`` 无超时,服务端挂起会让
    to_thread 线程永久占住(线程取消不了);select 到截止时刻抛 TimeoutError,
    线程有界退出,且与 event loop 完全无关(装配同步路径/运行任意 loop 通用)。
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._buf = bytearray()

    def readline(self, timeout: float) -> bytes:
        """读一行(不含 ``\\n``);超时 TimeoutError,对端关闭 EOFError。"""
        deadline = time.monotonic() + timeout
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = bytes(self._buf[:nl])
                del self._buf[: nl + 1]
                return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("读 MCP server 响应超时")
            ready, _, _ = select.select([self._fd], [], [], remaining)
            if not ready:
                raise TimeoutError("读 MCP server 响应超时")
            chunk = os.read(self._fd, 65536)
            if not chunk:
                raise EOFError("MCP server stdout 已关闭(进程死亡或断管)")
            self._buf.extend(chunk)


class McpStdioClient:
    """单个 MCP server 的 stdio 连接(进程生命周期 = kernel 寿命)。"""

    #: 握手钉死的协议版本(现行稳定版;不跟 server 协商漂移——回应 ≠ 请求 →
    #: McpError 拒连;``spec.protocol_version`` 显式给时覆盖请求值即换钉扎值)
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, spec: McpServerSpec) -> None:
        if not _NAME_RE.fullmatch(spec.name):
            raise McpError(
                f"server 名 {spec.name!r} 非法(只许 [A-Za-z0-9_-];进工具命名空间 mcp.<server>.<tool>)"
            )
        self._spec = spec
        self._proc: subprocess.Popen[bytes] | None = None
        self._reader: _LineReader | None = None
        self._next_id = 0
        #: tools/list 缓存(注册数据源;eager 语义:连接成功即已就位)
        self.tools: list[dict[str, Any]] = []
        #: initialize 回应的 server capabilities(派生工具面判据;eager 语义同上)
        self.capabilities: dict[str, Any] = {}
        #: resources/list / prompts/list 缓存(server 宣告对应 capability 才拉取,否则恒空)
        self.resources: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        #: 单飞:同一连接 in-flight 请求最多一个(读循环按 id 配对的正确性依赖此)
        self._call_lock = asyncio.Lock()
        #: _ensure_connected 单飞(并发断线重连只起一个新进程)
        self._connect_lock = asyncio.Lock()
        _warn_unpinned_npx(spec)  # 装配期引导钉版(warning 不阻断)
        # 兜底:宿主忘 close/异常退出不留孤儿(close() 之后为 no-op)
        atexit.register(self._atexit_kill)

    # ------------------------------------------------------------------
    # 连接(eager:拉起进程 + initialize 握手 + tools/list 缓存)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """拉起子进程 + 握手 + tools/list(``connect_timeout`` 全程包住)。

        失败清理进程再抛 :class:`McpError`(装配层包装为 ConfigError 快速失败)。
        """
        try:
            # 外层看门狗兜底(内层 select 截止先到,这层防病态卡死)
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

        Web 宿主装配入口可能在已跑 loop 的线程里,``asyncio.run`` 会直接炸——
        阻塞管道 + select 截止的 IO 核心本就与 loop 无关,同步路径零 loop 依赖)。
        """
        try:
            self._connect_blocking()
        except Exception as e:
            self._kill_blocking()
            if isinstance(e, McpError):
                raise
            raise McpError(
                f"server {self._spec.name!r} 连接失败: {type(e).__name__}: {e}"
            ) from e

    def _connect_blocking(self) -> None:
        """拉起子进程并握手(阻塞;截止由 select 保证)。失败由调用方清理进程。"""
        self._kill_blocking()  # 重连路径:收掉旧尸首(zombie/半死连接),再起新进程
        env = {key: self._resolve_env_value(key, raw) for key, raw in self._spec.env.items()}
        try:
            proc = subprocess.Popen(
                self._spec.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # stderr 继承宿主:MCP 允许 server 往 stderr 打日志,宿主日志里可见;
                # 不开 PIPE——长跑 server 写满管道缓冲区无人读会双双死锁
                env=env,  # 不继承宿主 env(供应链最小授权;PATH/HOME 等需显式配置)
                start_new_session=True,  # 独立进程组:killpg 整组端掉(同 shell_exec 先例)
            )
        except OSError as e:
            raise McpError(f"server {self._spec.name!r} 启动失败({self._spec.command[0]!r}): {e}") from e
        reader = _LineReader(proc.stdout.fileno())
        deadline = time.monotonic() + self._spec.connect_timeout
        requested = self._spec.protocol_version or self.PROTOCOL_VERSION
        try:
            init_result = self._rpc(
                proc,
                reader,
                "initialize",
                {
                    "protocolVersion": requested,
                    "capabilities": {},
                    "clientInfo": {"name": "agent-os", "version": __version__},
                },
                deadline,
            )
            negotiated = init_result.get("protocolVersion")
            if negotiated != requested:
                raise McpError(
                    f"server {self._spec.name!r} 协议版本协商 {negotiated!r} ≠ 钉扎 {requested!r},"
                    f"拒绝静默降级(协议行为随版本漂移,静默接受不可审计;确要换版本请显式配置 "
                    f'[mcp.servers."{self._spec.name}"] protocol_version)'
                )
            self._write(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            result = self._rpc(proc, reader, "tools/list", {}, deadline)
            tools = result.get("tools")
            if not isinstance(tools, list):
                raise McpError(f"server {self._spec.name!r} 的 tools/list 响应缺 tools 数组")
            capabilities = init_result.get("capabilities")
            capabilities = capabilities if isinstance(capabilities, dict) else {}
            # 只拉 server 宣告过的原语(未宣告的方法绝不上线;宣告了却给不出清单 → 快速失败)
            resources = (
                self._handshake_list(proc, reader, "resources/list", "resources", deadline)
                if "resources" in capabilities
                else []
            )
            prompts = (
                self._handshake_list(proc, reader, "prompts/list", "prompts", deadline)
                if "prompts" in capabilities
                else []
            )
        except BaseException:
            # 握手任何一步失败都收掉这个半连进程(调用方还会再 _kill_blocking 一次,幂等)
            self._kill_proc(proc)
            raise
        self._proc = proc
        self._reader = reader
        self.tools = tools
        self.capabilities = capabilities
        self.resources = resources
        self.prompts = prompts

    def _handshake_list(
        self,
        proc: subprocess.Popen[bytes],
        reader: _LineReader,
        method: str,
        key: str,
        deadline: float,
    ) -> list[dict[str, Any]]:
        """握手期 ``*/list`` 拉取(server 宣告了对应 capability 才被调用;
        响应缺数组 → McpError 快速失败,同 tools/list 先例)。"""
        result = self._rpc(proc, reader, method, {}, deadline)
        entries = result.get(key)
        if not isinstance(entries, list):
            raise McpError(f"server {self._spec.name!r} 的 {method} 响应缺 {key} 数组")
        return entries

    def _resolve_env_value(self, key: str, raw: Any) -> str:
        """env 值解析:str 字面量直传;``{"env": "VAR"}`` 现读 os.environ(缺席 → McpError)。"""
        if isinstance(raw, str):
            return raw
        var = str(raw.get("env", ""))
        value = os.environ.get(var)
        if value is None:
            raise McpError(
                f"server {self._spec.name!r} 的 env.{key} 间接引用的环境变量 {var} 不存在"
                f"(不落盘明文;请先 export {var}=...)"
            )
        return value

    # ------------------------------------------------------------------
    # JSON-RPC 读写(阻塞核心;截止时刻由调用方算好传入)
    # ------------------------------------------------------------------

    def _write(self, proc: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
        """写一行 JSON + flush;断管抛 BrokenPipeError/OSError(归可重连类)。"""
        assert proc.stdin is not None
        line = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        proc.stdin.write(line)
        proc.stdin.flush()

    def _rpc(
        self,
        proc: subprocess.Popen[bytes],
        reader: _LineReader,
        method: str,
        params: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        """发一个请求并读到 id 匹配的响应(跳过 notification 与未知 id 的行)。

        JSON-RPC error 响应抛 :class:`McpError`(服务端拒绝,重连无意义);
        非法 JSON 抛 :class:`_McpTransportError`(协议垃圾,归可重连类)。
        """
        self._next_id += 1
        req_id = self._next_id
        self._write(
            proc, {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"server {self._spec.name!r} 的 {method} 响应超时")
            raw = reader.readline(remaining)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as e:
                raise _McpTransportError(
                    f"server {self._spec.name!r} 返回非法 JSON: {raw[:200]!r}"
                ) from e
            if not isinstance(msg, dict) or msg.get("id") != req_id:
                continue  # notification(无 id)/迟到的异 id 响应:跳过(单飞保证 in-flight 只有一个)
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

    # ------------------------------------------------------------------
    # 调用(任意 JSON-RPC 方法;断管重连一次重试一次)
    # ------------------------------------------------------------------

    async def request(
        self, method: str, params: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        """发一个 JSON-RPC 请求,返回 result dict(``call_tool``/``read_resource``/
        ``get_prompt`` 为其特化;归一化由 :class:`McpTool` 与派生工具各自负责)。

        断管/进程死/协议垃圾 → 杀连接、重连一次、重试一次,再失败抛 :class:`McpError`;
        超时(内层 select 截止或外层看门狗)→ 杀连接抛 TimeoutError(dispatch 归一
        TIMEOUT retryable)——超时的连接不可信(迟到的响应会与下一请求错位),必须重建;
        被取消(注册表超时/ run 中止)→ 杀连接后传播(同 shell_exec 的 CancelledError 先例)。
        ``timeout`` 缺省随 server spec。
        """
        timeout = self._spec.timeout if timeout is None else timeout
        async with self._call_lock:
            for attempt in (1, 2):
                try:
                    await self._ensure_connected()
                    return await asyncio.wait_for(
                        asyncio.to_thread(self._request_blocking, method, params, timeout),
                        timeout=timeout + 1.0,  # 外层看门狗;内层 select 截止先到
                    )
                except TimeoutError:
                    await self._abort()
                    raise
                except asyncio.CancelledError:
                    await self._abort()  # 不能留孤儿,也不能留错位的连接
                    raise
                except (EOFError, OSError, _McpTransportError) as e:
                    await self._abort()
                    if attempt == 2:
                        raise McpError(
                            f"server {self._spec.name!r} 调用 {method!r} 重连后仍失败: "
                            f"{type(e).__name__}: {e}"
                        ) from e
                    _log.warning(
                        "server %s 调用 %s 传输失败(%s),重连重试一次",
                        self._spec.name, method, e,
                    )
        raise AssertionError("unreachable")  # pragma: no cover — 循环两途必 return/raise

    async def call_tool(self, name: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
        """``tools/call``(``request`` 的特化),返回 result dict。"""
        return await self.request("tools/call", {"name": name, "arguments": args}, timeout)

    async def read_resource(self, uri: str, timeout: float | None = None) -> dict[str, Any]:
        """``resources/read``(``request`` 的特化;server 须宣告 resources capability)。"""
        return await self.request("resources/read", {"uri": uri}, timeout)

    async def get_prompt(
        self, name: str, arguments: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        """``prompts/get``(``request`` 的特化;server 须宣告 prompts capability)。"""
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return await self.request("prompts/get", params, timeout)

    def _request_blocking(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        """阻塞版 JSON-RPC 调用(to_thread 线程内执行;select 截止保证线程有界退出)。"""
        proc, reader = self._proc, self._reader
        if proc is None or reader is None:
            raise _McpTransportError(f"server {self._spec.name!r} 未连接")
        deadline = time.monotonic() + timeout
        return self._rpc(proc, reader, method, params, deadline)

    async def _ensure_connected(self) -> None:
        """已连接(进程活着)直接返回;否则单飞重连(_connect_lock 防并发双连双进程)。"""
        if self._alive:
            return
        async with self._connect_lock:
            if not self._alive:
                await self.connect()

    @property
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # 关闭与清理(杀进程组;atexit 兜底)
    # ------------------------------------------------------------------

    async def _abort(self) -> None:
        """连接标记断开并杀进程(断管/超时/取消后的统一收尾;下次调用经 _ensure_connected 重连)。"""
        await asyncio.to_thread(self._kill_blocking)

    async def close(self) -> None:
        """显式关闭:杀进程组回收(kernel 收尾/测试清理);幂等。"""
        await asyncio.to_thread(self._kill_blocking)

    def _kill_blocking(self) -> None:
        """杀当前连接的进程组并收尸(幂等;to_thread 内或同步路径用)。"""
        proc, self._proc, self._reader = self._proc, None, None
        if proc is not None:
            self._kill_proc(proc)

    @staticmethod
    def _kill_proc(proc: subprocess.Popen[bytes]) -> None:
        """杀指定进程组 + wait 收尸(照 shell_exec ``_kill`` 先例:SIGKILL 整组,不等 graceful)。"""
        if proc.poll() is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)
        # 管道 fd 显式关:读侧若还有残留线程(next 调用前的旧 select),拿 EOF/OSError 有界退出
        for stream in (proc.stdin, proc.stdout):
            with contextlib.suppress(Exception):
                if stream is not None:
                    stream.close()

    def _atexit_kill(self) -> None:
        """atexit 兜底(宿主崩溃/忘 close 不留孤儿;close 之后是 no-op)。同步、绝不抛。"""
        with contextlib.suppress(Exception):
            self._kill_blocking()


class McpTool:
    """MCP server 工具 → :class:`agent_os.api.v1.Tool` 契约适配(走全量 dispatch 管线)。

    spec 转换规则(§8.3 供应链清单):name = ``mcp.<server>.<tool>``;description
    注入扫描命中 → 整段弃用占位,干净描述超 500 字符截断;parameters =
    ``inputSchema``(缺省 ``{"type": "object"}``);permission/timeout/confirm 随
    server spec;``untrusted_source=True`` 强制;``concurrency_safe=False``。
    """

    def __init__(self, client: McpStdioClient | McpHttpClient, tool_name: str, spec: ToolSpec) -> None:
        self._client = client
        self._tool_name = tool_name  # server 侧的原始工具名(调用时透传)
        self.spec = spec

    @classmethod
    def from_tool_def(cls, client: McpStdioClient | McpHttpClient, tool_def: Any) -> McpTool | None:
        """tools/list 条目 → McpTool;形态非法/名字含非法字符 → 记 warning 跳过(返回 None)。"""
        server = client._spec
        if not isinstance(tool_def, dict):
            _log.warning("server %s 的 tools/list 条目非表,跳过: %r", server.name, tool_def)
            return None
        raw_name = tool_def.get("name")
        if not isinstance(raw_name, str) or not _NAME_RE.fullmatch(raw_name):
            _log.warning("server %s 的工具名含非法字符,跳过注册: %r", server.name, raw_name)
            return None
        description = tool_def.get("description")
        if not isinstance(description, str):
            description = ""
        if looks_suspicious(description):
            # 整段弃用(宁缺毋滥,同 recall 先例):截断保留的前半段仍可能是注入铺垫
            description = f"{raw_name}(原描述已移除:含可疑注入内容)"
        elif len(description) > _DESCRIPTION_MAX_CHARS:
            description = description[:_DESCRIPTION_MAX_CHARS] + "…[截断]"
        schema = tool_def.get("inputSchema")
        spec = ToolSpec(
            name=f"mcp.{server.name}.{raw_name}",
            description=description,
            parameters=schema if isinstance(schema, dict) else {"type": "object"},
            permission=server.permission,
            timeout=server.timeout,
            confirm=server.confirm,
            untrusted_source=True,  # 第三方 server 产出恒按不可信内容标记(§8.3)
            concurrency_safe=False,  # 远端进程状态不可知,fail-safe 默认否
        )
        return cls(client, raw_name, spec)

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """透传 tools/call 并归一化:McpError/TimeoutError 上抛给 dispatch 归一化。"""
        result = await self._client.call_tool(self._tool_name, args, self.spec.timeout)
        content = result.get("content")
        if result.get("isError"):
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INTERNAL,
                    message=f"MCP 工具 {self.spec.name} 报错: {_content_text(content)}",
                    retryable=False,
                ),
            )
        structured = result.get("structuredContent")
        if structured is not None:
            return ToolResult(ok=True, value=structured)  # 结构化内容优先于 text 拼接
        return ToolResult(ok=True, value=_content_text(content))


def _derived_spec(
    server: McpServerSpec, leaf: str, description: str, parameters: dict[str, Any]
) -> ToolSpec:
    """派生工具 spec:name = ``mcp.<server>.<leaf>``;permission/timeout/confirm 随
    server spec;``untrusted_source=True`` 强制;``concurrency_safe=False``(同 McpTool)。"""
    return ToolSpec(
        name=f"mcp.{server.name}.{leaf}",
        description=description,
        parameters=parameters,
        permission=server.permission,
        timeout=server.timeout,
        confirm=server.confirm,
        untrusted_source=True,  # 第三方 server 产出恒按不可信内容标记(§8.3)
        concurrency_safe=False,  # 远端进程状态不可知,fail-safe 默认否
    )


def _inventory_description(prefix: str, entries: list[dict[str, Any]], *, key: str) -> str:
    """派生工具描述:固定前缀 + 清单逐条 ``uri/name — 描述``。

    每条描述与 McpTool 同纪律:注入扫描命中 → 该条描述整段弃用为占位(宁缺毋滥,
    不影响其余条目);干净描述超 500 字符截断并标记。
    """
    lines = [prefix]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ident = entry.get(key)
        if not isinstance(ident, str) or not ident:
            continue
        description = entry.get("description")
        if not isinstance(description, str):
            description = ""
        if looks_suspicious(description):
            # 该条整段弃用(同 McpTool 先例):截断保留的前半段仍可能是注入铺垫
            description = "(原描述已移除:含可疑注入内容)"
        elif len(description) > _DESCRIPTION_MAX_CHARS:
            description = description[:_DESCRIPTION_MAX_CHARS] + "…[截断]"
        lines.append(f"{ident} — {description}" if description else ident)
    return "\n".join(lines)


class McpResourceReadTool:
    """``resources/read`` → 单个派生工具 ``mcp.<server>.resource_read``(uri 参数透传)。

    归一化:``contents[]`` text 块逐字拼接;blob 块(base64)spill 进 blob store
    回 ``blob://`` ref(同 std_web.py fetch_page 的 ``ctx.blob.put`` 先例;ctx/blob
    缺席退化为显式占位);base64 非法/未知块形 → 显式占位(不静默丢,同
    ``_content_text`` 精神);``isError``/响应缺 contents 数组 → INTERNAL
    (同 McpTool 错误归一家族)。
    """

    def __init__(self, client: McpStdioClient | McpHttpClient) -> None:
        self._client = client
        server = client._spec
        self.spec = _derived_spec(
            server,
            "resource_read",
            _inventory_description(
                f"读取 MCP server {server.name} 的资源(内容为第三方产出,不可信)。"
                "Use when 需要读取清单中某一资源;Do not use when 其他。资源清单:",
                client.resources,
                key="uri",
            ),
            {
                "type": "object",
                "properties": {"uri": {"type": "string", "description": "清单中的资源 uri"}},
                "required": ["uri"],
            },
        )

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """透传 resources/read 并归一化:McpError/TimeoutError 上抛给 dispatch 归一化。"""
        result = await self._client.read_resource(str(args.get("uri", "")))
        if result.get("isError"):
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INTERNAL,
                    message=f"MCP 工具 {self.spec.name} 报错: {_content_text(result.get('content'))}",
                    retryable=False,
                ),
            )
        contents = result.get("contents")
        if not isinstance(contents, list):
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INTERNAL,
                    message=f"MCP 工具 {self.spec.name} 的 resources/read 响应缺 contents 数组",
                    retryable=False,
                ),
            )
        parts: list[str] = []
        for block in contents:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            blob = block.get("blob")
            if isinstance(blob, str):
                parts.append(await self._blob_part(block, blob, ctx))
                continue
            parts.append(f"[不支持的 MCP 资源内容: {str(block)[:100]}]")
        return ToolResult(ok=True, value="\n".join(parts))

    @staticmethod
    async def _blob_part(block: dict[str, Any], blob: str, ctx: ToolContext) -> str:
        """blob 块(base64)→ spill blob store 回 ref;base64 非法/ctx.blob 缺席 → 显式占位。"""
        mime = block.get("mimeType")
        mime = mime if isinstance(mime, str) and mime else "application/octet-stream"
        try:
            data = base64.b64decode(blob, validate=True)
        except ValueError:
            return f"[二进制资源 {mime}: base64 非法,内容已略]"
        if ctx is not None and ctx.blob is not None:
            ref = await ctx.blob.put(data, ctx.run_id)
            return f"[二进制资源 {mime}, {len(data)} bytes: {ref}]"
        return f"[二进制资源 {mime}, {len(data)} bytes]"


class McpPromptGetTool:
    """``prompts/get`` → 单个派生工具 ``mcp.<server>.prompt_get``(name/arguments 透传)。

    归一化:``messages[]`` 逐条投影为 ``role: text`` 行拼接(content 单块/数组
    两形态统一走 ``_content_text``:text 拼接,未知类型显式占位);
    ``isError``/响应缺 messages 数组 → INTERNAL(同 McpTool 错误归一家族)。
    """

    def __init__(self, client: McpStdioClient | McpHttpClient) -> None:
        self._client = client
        server = client._spec
        self.spec = _derived_spec(
            server,
            "prompt_get",
            _inventory_description(
                f"取 MCP server {server.name} 的提示词模板(内容为第三方产出,不可信)。"
                "Use when 需要清单中某一模板;Do not use when 其他。模板清单:",
                client.prompts,
                key="name",
            ),
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "清单中的模板名"},
                    "arguments": {"type": "object", "description": "模板实参(可选)"},
                },
                "required": ["name"],
            },
        )

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """透传 prompts/get 并归一化:McpError/TimeoutError 上抛给 dispatch 归一化。"""
        arguments = args.get("arguments")
        result = await self._client.get_prompt(
            str(args.get("name", "")), arguments if isinstance(arguments, dict) else None
        )
        if result.get("isError"):
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INTERNAL,
                    message=f"MCP 工具 {self.spec.name} 报错: {_content_text(result.get('content'))}",
                    retryable=False,
                ),
            )
        messages = result.get("messages")
        if not isinstance(messages, list):
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INTERNAL,
                    message=f"MCP 工具 {self.spec.name} 的 prompts/get 响应缺 messages 数组",
                    retryable=False,
                ),
            )
        lines: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            role = role if isinstance(role, str) and role else "?"
            content = message.get("content")
            if isinstance(content, dict):
                content = [content]  # spec 单块形态统一成数组走 _content_text
            lines.append(f"{role}: {_content_text(content)}")
        return ToolResult(ok=True, value="\n".join(lines))


def _content_text(content: Any) -> str:
    """content 块数组 → 文本:text 块拼接;其余类型占位标记(显式,不静默丢)。"""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        else:
            parts.append(f"[不支持的 MCP 内容类型: {block.get('type', '?')}]")
    return "\n".join(parts)


def _register_tools(registry: Any, client: McpStdioClient | McpHttpClient) -> None:
    """把 server 的工具面注册进 registry(命名空间 ``mcp.<server>.<tool>``;撞名拒覆盖)。

    tools/list 逐条注册;握手缓存了 resources/prompts 清单(server 宣告过对应
    capability)时再各注册一个派生工具 ``mcp.<server>.resource_read`` /
    ``mcp.<server>.prompt_get``。
    """
    for tool_def in client.tools:
        tool = McpTool.from_tool_def(client, tool_def)
        if tool is None:
            continue  # 非法条目/工具名:from_tool_def 已记 warning
        if registry.has(tool.spec.name):
            raise McpError(f"MCP 工具名撞已有注册项,拒绝覆盖: {tool.spec.name}")
        registry.register(tool)
    derived: list[Any] = []
    if client.resources:
        derived.append(McpResourceReadTool(client))
    if client.prompts:
        derived.append(McpPromptGetTool(client))
    for tool in derived:
        if registry.has(tool.spec.name):
            raise McpError(f"MCP 工具名撞已有注册项,拒绝覆盖: {tool.spec.name}")
        registry.register(tool)


def _client_for(spec: McpServerSpec) -> McpStdioClient | McpHttpClient:
    """按传输分流建 client:auto 按键判(``url`` 非空 → http,否则 stdio)。

    显式 ``transport`` 与键不符(stdio 无 command / http 无 url)由装配层
    (runtime/config.py ``_mcp_servers``)拒;直造 spec 绕过时,http 侧由
    ``McpHttpClient.__init__`` 的 url 校验兜住,stdio 侧空 command 在 Popen
    启动失败时归 :class:`McpError`。
    """
    transport = spec.transport
    if transport == "auto":
        transport = "http" if spec.url else "stdio"
    if transport == "http":
        # 运行期延迟导入:mcp_http 反向 import 本模块的 McpError/spec,模块级互引会循环
        from agent_os.tools.mcp_http import McpHttpClient

        return McpHttpClient(spec)
    return McpStdioClient(spec)


async def connect_and_register(
    registry: Any, specs: list[McpServerSpec]
) -> list[McpStdioClient | McpHttpClient]:
    """装配入口(async):逐 server 连接 + 注册全部工具,返回 client 列表(kernel 寿命)。

    任一 server 失败 → 已起连接全部清理,抛 :class:`McpError`(装配层包装为
    ConfigError 快速失败)。clients 同时挂 ``registry._mcp_clients``(生命周期
    锚点:Kernel/registry 无 close 钩子,连接清理由 client.close()/atexit 兜底)。
    """
    clients: list[McpStdioClient | McpHttpClient] = []
    try:
        for spec in specs:
            client = _client_for(spec)
            clients.append(client)  # 先入列:连接/注册失败也要清理这个已起连接
            await client.connect()
            _register_tools(registry, client)
    except BaseException:
        for client in clients:
            await client.close()
        raise
    registry._mcp_clients.extend(clients)
    return clients


def connect_and_register_sync(
    registry: Any, specs: list[McpServerSpec]
) -> list[McpStdioClient | McpHttpClient]:
    """同步版装配入口(``build_kernel`` 是同步函数;为什么不 asyncio.run 见 connect_sync)。"""
    clients: list[McpStdioClient | McpHttpClient] = []
    try:
        for spec in specs:
            client = _client_for(spec)
            clients.append(client)  # 先入列(同上:失败路径也要清理)
            client.connect_sync()
            _register_tools(registry, client)
    except BaseException:
        for client in clients:
            with contextlib.suppress(Exception):
                if isinstance(client, McpStdioClient):
                    client._kill_blocking()
                else:
                    client._close_blocking()
        raise
    registry._mcp_clients.extend(clients)
    return clients
