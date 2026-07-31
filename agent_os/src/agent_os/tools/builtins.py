"""内置工具(DESIGN.md §8.3;M1 四个基础件已实现,M5 补 system.file.edit/system.python.exec 等;
STDLIB-CATALOG §W0:统一路径解析器/错误 hint/if_match 乐观锁/system.shell.exec 结构化返回)。

docstring 倡导"何时用/边界/负例"(§8.4);签名即 schema 推导来源。fs/shell 工具经
约定参数 ``ctx`` 取 ``ToolContext``(见 ``local_registry._FunctionTool``),限定在
run 工作目录内操作(§2.2;§W0-1 起 read_paths 只读区可在 workdir 之外);
需要结构化错误时函数直接返回 ``ToolResult``;错误 ``hint`` 是给模型的下一步
动作建议(§W0-3),运行期事实(路径)现取,不硬编码。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from agent_os.api.v1 import (
    POST_LOGIC_EXEC,
    PRE_LOGIC_EXEC,
    ExecRequest,
    LogicError,
    LogicKernel,
    Permission,
    ResourceLimits,
    Signal,
    Tool,
    ToolContext,
    ToolError,
    ToolErrorKind,
    ToolResult,
    ToolSpec,
    Veto,
)
from agent_os.tools.local_registry import _FunctionTool, derive_spec, resolve_work_path

#: system.shell.exec 单条流(stdout/stderr)截断上限(§W0-5;截断时 ``truncated=True`` 显式标记)
_SHELL_STREAM_MAX_CHARS = 100_000


def _resolve_in_workdir(
    ctx: ToolContext | None, path: str, *, write: bool = False
) -> Path | ToolResult:
    """把 ``path`` 按 §W0-1 三段判定解析(只读区/workdir 读写/越界 INVALID_ARGS);

    解析本体是 ``local_registry.resolve_work_path``(fs 与 shell 共用同一解析器)。
    """
    workdir = ctx.workdir if ctx is not None else "."
    read_paths = ctx.read_paths if ctx is not None else ()
    return resolve_work_path(workdir, read_paths, path, write=write)


def _check_if_match(target: Path, path: str, if_match: str) -> ToolResult | None:
    """§W0-4 乐观锁:``if_match`` 与当前文件内容(或其 sha256 十六进制)比对。

    不传(空串)= 不检查(向后兼容);不匹配拒写,error 带当前版本标识
    (hash 前缀 + 内容前 40 字符),hint "内容已被修改,重读后重试";
    文件不存在则无法比对 → NOT_FOUND。
    """
    if not if_match:
        return None
    if not target.is_file():
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.NOT_FOUND,
                message=f"文件不存在,无法比对 if_match: {path}",
                retryable=False,
                hint="去掉 if_match 可创建新文件;带锁写入前请先确认文件已存在",
            ),
        )
    current = target.read_text(encoding="utf-8")
    digest = hashlib.sha256(current.encode("utf-8")).hexdigest()
    if if_match in (current, digest):
        return None
    return ToolResult(
        ok=False,
        error=ToolError(
            kind=ToolErrorKind.INVALID_ARGS,
            message=(
                f"if_match 不匹配: {path}"
                f"(当前版本 sha256:{digest[:12]},内容前 40 字符 {current[:40]!r})"
            ),
            retryable=False,
            hint="内容已被修改,重读后重试",
        ),
    )


async def fs_read(
    path: str, offset: int = 1, limit: int = 2000, ctx: ToolContext | None = None
) -> str | ToolResult:
    """读文件(行号前缀 ``"{n}\\t{line}"``,offset/limit 分页,§8.3)。

    Use when 需要查看工作目录内文件;Do not use when 路径越出帧工作目录(会被拒)。
    文件不存在 → NOT_FOUND;``offset``/``limit`` 从 1 起计,非法 → INVALID_ARGS。
    """
    target = _resolve_in_workdir(ctx, path)
    if isinstance(target, ToolResult):
        return target
    if offset < 1 or limit < 1:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.INVALID_ARGS,
                message=f"offset/limit 必须 >= 1(收到 offset={offset}, limit={limit})",
                retryable=False,
                hint="offset 与 limit 均从 1 起计;读开头用 offset=1",
            ),
        )
    if not target.is_file():
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.NOT_FOUND,
                message=f"文件不存在: {path}",
                retryable=False,
                hint=f"检查工作目录(当前 {target.parent});或用 system.file.list 确认路径",
            ),
        )
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    window = lines[offset - 1 : offset - 1 + limit]
    return "\n".join(f"{n}\t{line}" for n, line in enumerate(window, start=offset))


async def fs_write(
    path: str, content: str, if_match: str = "", ctx: ToolContext | None = None
) -> str | ToolResult:
    """写文件(整体覆盖,自动建父目录,§8.3;``if_match`` 乐观锁见 §W0-4)。

    Use when 需要创建或整体覆盖文件;Do not use when 只需局部修改(用 system.file.edit)。
    ``if_match`` 为期望的当前内容(或其 sha256 十六进制):不匹配拒写,不传不检查。
    """
    target = _resolve_in_workdir(ctx, path, write=True)
    if isinstance(target, ToolResult):
        return target
    conflict = _check_if_match(target, path, if_match)
    if conflict is not None:
        return conflict
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"已写入 {path}({len(content)} 字符)"


async def fs_edit(
    path: str, old_string: str, new_string: str, if_match: str = "", ctx: ToolContext | None = None
) -> str | ToolResult:
    """局部编辑(old_string→new_string **唯一匹配**才替换,§8.3;``if_match`` 乐观锁见 §W0-4)。

    Use when 只需局部修改文件;Do not use when 整体覆盖(用 system.file.write)。
    未找到 / 多处匹配 → INVALID_ARGS(多处时请带更多上下文使匹配唯一);
    文件不存在 → NOT_FOUND;路径越出帧工作目录 → INVALID_ARGS(同 system.file.read)。
    """
    target = _resolve_in_workdir(ctx, path, write=True)
    if isinstance(target, ToolResult):
        return target
    if not target.is_file():
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.NOT_FOUND,
                message=f"文件不存在: {path}",
                retryable=False,
                hint=f"检查工作目录(当前 {target.parent});或用 system.file.list 确认路径",
            ),
        )
    conflict = _check_if_match(target, path, if_match)
    if conflict is not None:
        return conflict
    content = target.read_text(encoding="utf-8")
    count = content.count(old_string)
    if count == 0:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.INVALID_ARGS,
                message=f"未找到 old_string: {old_string[:80]!r}(文件 {path})",
                retryable=False,
                hint="先用 system.file.read 核对文件内容;old_string 须与文件完全一致(含空白与换行)",
            ),
        )
    if count > 1:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.INVALID_ARGS,
                message=f"old_string 不唯一,多处匹配(×{count}):请提供更多上下文使匹配唯一",
                retryable=False,
                hint="扩大 old_string 上下文(前后各带几行)使匹配唯一,或用 system.file.write 整体覆盖",
            ),
        )
    target.write_text(content.replace(old_string, new_string), encoding="utf-8")
    return f"已编辑 {path}(替换 1 处)"


async def shell_exec(
    command: str, timeout: int = 30, ctx: ToolContext | None = None
) -> dict[str, Any] | ToolResult:
    """执行 shell 命令(EXEC 级;一次性子进程,在帧工作目录内运行,捕获 stdout/stderr)。

    Use when 需要跑构建/测试/git 等外部命令;Do not use when 只是读写文件
    (用 system.file.read/system.file.write,权限更低且返回结构化)或做纯计算(用 system.python.exec)。
    每次调用是**独立子进程**:``cd``、venv 激活、环境变量都不跨调用保留。

    返回 ``{"stdout", "stderr", "exit_code", "truncated", "text"}``(§W0-5):
    结构化字段供编排脚本使用,``text`` 为拼接版供模型直读;单条流超
    100_000 字符截断并显式标记 ``truncated``(不静默截断);非零退出不算
    工具错误,``exit_code`` 照常返回。持久会话形态(跨调用保持 cwd/env)留待
    后续里程碑;超时先由 ``timeout`` 参数杀进程并返回 TIMEOUT,注册表
    ``spec.timeout`` 兜底。防护主体是沙箱(§9.2)+ 权限(§8.2);
    ToolGuard 正则仅为辅助(§5.4 能力上限)。
    """
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=ctx.workdir if ctx is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.TIMEOUT,
                message=f"命令超过 {timeout}s 未结束,已终止",
                retryable=True,
                hint=f"缩短命令运行时间,或用 timeout 参数放宽上限(当前 {timeout}s)",
            ),
        )
    stdout = out.decode("utf-8", errors="replace")
    stderr = err.decode("utf-8", errors="replace")
    truncated = len(stdout) > _SHELL_STREAM_MAX_CHARS or len(stderr) > _SHELL_STREAM_MAX_CHARS
    stdout = stdout[:_SHELL_STREAM_MAX_CHARS]
    stderr = stderr[:_SHELL_STREAM_MAX_CHARS]
    exit_code = proc.returncode if proc.returncode is not None else 0
    parts = [stdout]
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if exit_code:
        parts.append(f"[exit code {exit_code}] 命令非零退出")
    if truncated:
        parts.append(f"[truncated] 输出超过 {_SHELL_STREAM_MAX_CHARS} 字符,已截断")
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "truncated": truncated,
        "text": "\n".join(parts),
    }


async def _http_request(
    transport: httpx.AsyncBaseTransport | None,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: str = "",
    max_bytes: int = 100_000,
) -> dict[str, Any]:
    """system.net.http_fetch / system.net.http_request 共用执行体:发请求、按 max_bytes
    截断、统一返回 {status, content, truncated}(不静默截断,同 §W0-5)。"""
    async with httpx.AsyncClient(
        transport=transport, follow_redirects=True, timeout=30.0
    ) as client:
        resp = await client.request(method, url, headers=headers, content=body or None)
        raw = await resp.aread()
    truncated = len(raw) > max_bytes
    content = raw[:max_bytes].decode(resp.encoding or "utf-8", errors="replace")
    return {"status": resp.status_code, "content": content, "truncated": truncated}


def http_fetch_tool(*, name: str = "system.net.http_fetch", transport: httpx.AsyncBaseTransport | None = None) -> Tool:
    """构造 ``system.net.http_fetch`` 内置工具(NET 级,§8.3);``transport`` 供测试注入 MockTransport。

    HTTP 错误状态(4xx/5xx)不算工具错误,照常返回 ``status``;结果标记
    ``untrusted_source``(§2.2,归一化时包裹来源标记,注入防御)。
    """

    async def http_fetch(url: str, max_bytes: int = 100_000) -> dict[str, Any]:
        """抓取 URL,返回 ``{"status", "content", "truncated"}``(content 超 max_bytes 截断)。

        Use when 需要读取公开网页;Do not use when 需要登录态,或需要非 GET 方法
        /自定义 headers/body(用 system.net.http_request)。
        """
        return await _http_request(transport, "GET", url, max_bytes=max_bytes)

    spec = derive_spec(
        http_fetch,
        name=name,
        permission=Permission.NET,
        timeout=30.0,
        untrusted_source=True,
        cost_hint="~1s,取决于网络与页面大小",
    )
    return _FunctionTool(http_fetch, spec)


#: system.net.http_request 允许的方法集合(白名单,大小写不敏感)
_HTTP_REQUEST_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def http_request_tool(*, name: str = "system.net.http_request", transport: httpx.AsyncBaseTransport | None = None) -> Tool:
    """构造 ``system.net.http_request`` 内置工具(NET 级;Phase 3 通用 HTTP,library-design-plan §4.4)。

    执行体与 ``system.net.http_fetch`` 共用 ``_http_request``;错误语义一致
    (HTTP 错误状态照常返回 ``status``;``untrusted_source`` 标记注入防御)。
    """

    async def http_request(
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: str = "",
        max_bytes: int = 100_000,
    ) -> dict[str, Any] | ToolResult:
        """通用 HTTP 请求,返回 ``{"status", "content", "truncated"}``(content 超 max_bytes 截断)。

        Use when 需要 POST/PUT/PATCH/DELETE 等非 GET 方法,或自定义 headers/body
        (调 REST API、提交表单);Do not use when 只是读取公开网页
        (用 system.net.http_fetch,GET 便捷形态,参数更少)。method 大小写不敏感,
        白名单 GET/POST/PUT/PATCH/DELETE/HEAD/OPTIONS,其他 → INVALID_ARGS;
        HTTP 错误状态(4xx/5xx)不算工具错误,照常返回 ``status``。
        """
        verb = method.strip().upper()
        if verb not in _HTTP_REQUEST_METHODS:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INVALID_ARGS,
                    message=f"不支持的 HTTP 方法: {method!r}",
                    retryable=False,
                    hint=f"method 取值为 {'/'.join(sorted(_HTTP_REQUEST_METHODS))}(大小写不敏感)",
                ),
            )
        return await _http_request(transport, verb, url, headers=headers, body=body, max_bytes=max_bytes)

    spec = derive_spec(
        http_request,
        name=name,
        permission=Permission.NET,
        timeout=30.0,
        untrusted_source=True,
        cost_hint="~1s,取决于网络与页面大小",
    )
    return _FunctionTool(http_request, spec)


async def blob_get(
    ref: str, offset: int = 0, limit: int = 20_000, ctx: ToolContext | None = None
) -> dict[str, Any] | ToolResult:
    """分页取回 spill 出去的大结果(READ;§8.3 offset/limit)。

    Use when 某个工具返回里带 ``spill_ref``、需要看被截断掉的全文;
    Do not use when 手上没有 ref(它由产生大输出的工具给出,不能自己拼)。
    返回 ``{content, offset, limit, total_bytes, truncated}``——``truncated``
    为真表示还有后续,用 ``offset += limit`` 继续取。
    """
    if ctx is None or ctx.blob is None:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.INTERNAL,
                message="当前装配未提供 blob store",
                retryable=False,
                hint="spill 依赖 blob store;检查 KernelBuilder 的工具装配",
            ),
        )
    if offset < 0 or limit < 1:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.INVALID_ARGS,
                message=f"offset 须 >= 0、limit 须 >= 1(收到 offset={offset}, limit={limit})",
                retryable=False,
                hint="从头取用 offset=0;按 offset += limit 翻页",
            ),
        )
    try:
        chunk = await ctx.blob.get(ref, offset=offset, limit=limit)
        total = len(await ctx.blob.get(ref))
    except KeyError:
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.NOT_FOUND,
                message=f"blob 不存在: {ref}",
                retryable=False,
                hint="ref 只在本 run 内有效,且须来自工具返回的 spill_ref,不能自行构造",
            ),
        )
    return {
        "content": chunk.decode("utf-8", errors="replace"),
        "offset": offset,
        "limit": limit,
        "total_bytes": total,
        # 显式截断标记(§3.2 契约 2):模型据此知道自己还没看全
        "truncated": offset + len(chunk) < total,
    }


async def ask_user(question: str) -> str:
    """向用户提问(User Communication 类,宿主注入回调,§8.3)。"""
    raise NotImplementedError("M1")


async def notify_user(message: str) -> None:
    """通知用户(User Communication 类,宿主注入回调,§8.3)。"""
    raise NotImplementedError("M1")


async def python_exec(source: str, args: dict[str, Any] | None = None) -> Any:
    """LLM 的"代码解释器"(§9.4):薄壳——校验参数后委托 SANDBOX Logic Kernel 执行,

    结果走 §8.1 归一化流水线;``permission: EXEC``,受三层权限与 HumanApproval 闸门约束。
    """
    raise NotImplementedError("M5")


def _parse_result(stdout: str) -> Any:
    """stdout 最后一个非空行的 ``json.loads``;不可解析(或无输出)为 None(§9.4)。"""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except ValueError:
        return None


def python_exec_tool(kernel: LogicKernel) -> Tool:
    """构造 ``system.python.exec`` 内置工具(§9.4):薄壳,委托传入的 SANDBOX Logic Kernel。

    value 形状 ``{"stdout", "stderr", "result"}``;执行错误 → TIMEOUT(LIMIT_EXCEEDED)
    或 INTERNAL,hint 带 stderr 尾部。提供可选 ``bind(signals)``:KernelBuilder 装配时
    注入总线,执行前后发 ``pre:logic.exec`` / ``post:logic.exec``(§9.5);
    ``pre`` 载荷带 ``source``/``language`` 供 CodeScanner 扫描,任一 ``Veto`` →
    不执行,返回 ``ToolError(kind=VETOED, message=理由)``(与 veto 回写语义一致,§5.2)。
    """

    class PythonExecTool:
        spec = ToolSpec(
            name="system.python.exec",
            description=(
                "在沙箱子进程中执行 Python 代码。Use when 需要确定性计算或数据处理;"
                "Do not use when 需要网络、文件系统或长驻状态(沙箱极简,见 §9.2)。"
                "约定:把结果 print 出来,取 stdout 最后一个非空行按 JSON 解析。"
            ),
            parameters={
                "type": "object",
                "properties": {"code": {"type": "string", "description": "要执行的 Python 源码"}},
                "required": ["code"],
            },
            permission=Permission.EXEC,
            timeout=10.0,
        )
        aliases = ("python_exec",)

        def __init__(self, logic_kernel: LogicKernel) -> None:
            self._kernel = logic_kernel
            self._signals: Any = None

        def bind(self, signals: Any) -> None:
            """KernelBuilder 装配钩子:注入信号总线(§9.5 监督信号)。"""
            self._signals = signals

        def with_alias(self, alias: str) -> "PythonExecTool":
            """Return a copy registered under a different name (legacy alias support)."""
            from dataclasses import replace

            new = copy.copy(self)
            new.spec = replace(self.spec, name=alias)
            return new

        async def _emit(
            self, name: str, ctx: ToolContext, payload: dict[str, Any]
        ) -> list[Any]:
            if self._signals is None:
                return []
            return await self._signals.emit(
                Signal(name=name, run_id=ctx.run_id, frame_id=ctx.frame_id, payload=payload)
            )

        async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            req = ExecRequest(
                source=args["code"],
                limits=ResourceLimits(wall_time=10, cpu_time=5, memory_mb=256, stdout_bytes=100_000),
            )
            verdicts = await self._emit(
                PRE_LOGIC_EXEC,
                ctx,
                {"tool": "system.python.exec", "source": args["code"], "language": "python"},
            )
            for verdict in verdicts:
                if isinstance(verdict, Veto):
                    # pre:logic.exec 否决(如 CodeScanner):不执行,理由回写(§9.5/§5.2)
                    return ToolResult(
                        ok=False,
                        error=ToolError(
                            kind=ToolErrorKind.VETOED,
                            message=verdict.reason,
                            retryable=False,
                        ),
                    )
            res = await self._kernel.execute(req)
            await self._emit(POST_LOGIC_EXEC, ctx, {"tool": "system.python.exec", "ok": res.error is None})
            if res.error is not None:
                kind = (
                    ToolErrorKind.TIMEOUT
                    if res.error.kind is LogicError.LIMIT_EXCEEDED
                    else ToolErrorKind.INTERNAL
                )
                return ToolResult(
                    ok=False,
                    error=ToolError(kind=kind, message=res.error.message, hint=res.stderr[-500:]),
                )
            return ToolResult(
                ok=True,
                value={"stdout": res.stdout, "stderr": res.stderr, "result": _parse_result(res.stdout)},
            )

    return PythonExecTool(kernel)
