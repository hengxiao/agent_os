"""内置工具(docs/DESIGN.md §8.3;M1 四个基础件已实现,M5 补 system.file.edit/system.python.exec 等;
STDLIB-CATALOG §W0:统一路径解析器/错误 hint/if_match 乐观锁/system.shell.exec 结构化返回)。

docstring 倡导"何时用/边界/负例"(§8.4);签名即 schema 推导来源。fs/shell 工具经
约定参数 ``ctx`` 取 ``ToolContext``(见 ``local_registry._FunctionTool``),限定在
run 工作目录内操作(§2.2;§W0-1 起 read_paths 只读区可在 workdir 之外);
需要结构化错误时函数直接返回 ``ToolResult``;错误 ``hint`` 是给模型的下一步
动作建议(§W0-3),运行期事实(路径)现取,不硬编码。
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import inspect
import json
import os
import signal
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

#: shell_exec 在注册表里的硬超时(§8.1 spec.timeout);内层 wait_for 必须钳在它以内,
#: 否则外层先触发 → CancelledError 绕过内层 except → 子进程成孤儿
_SHELL_SPEC_TIMEOUT = 30.0

#: 留给内层 kill + 收尸的余量(秒)
_SHELL_KILL_GRACE = 1.0

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
    窗口没读完时末尾附一行 ``[已显示 a-b 行,共 N 行;续读 system.file.read(offset=b+1)]``
    (§3.2 契约 2):**截断必须显式**,否则模型会把前 2000 行当全文继续推理。
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
    total = len(lines)
    window = lines[offset - 1 : offset - 1 + limit]
    if not window:
        # 空窗口不能返回空串:模型分不清"文件是空的"和"offset 翻过头了"
        return (
            f"[{path} 共 {total} 行;offset={offset} 已越过末尾,无内容]"
            if total
            else f"[{path} 是空文件]"
        )
    body = "\n".join(f"{n}\t{line}" for n, line in enumerate(window, start=offset))
    end = offset + len(window) - 1
    if end < total:
        # §3.2 契约 2:截断必须显式告知,否则模型把局部当全文往下推理
        body += f"\n[已显示 {offset}-{end} 行,共 {total} 行;续读 system.file.read(offset={end + 1})]"
    return body


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


def _kill(proc: asyncio.subprocess.Process) -> None:
    """杀掉整个进程组;已退出则是 no-op(竞态下 ProcessLookupError 属正常)。

    只 ``proc.kill()`` 不够:``sh -c`` 未必 exec 掉自己(带管道/多命令时必然 fork),
    杀 shell 留下的孙子进程仍在跑——实测 ``sleep 60`` 在 shell 被 SIGKILL 后
    照活不误。子进程建在独立会话里(``start_new_session``),故可整组端掉。
    """
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


async def _reap(proc: asyncio.subprocess.Process, comm: asyncio.Future) -> None:
    """超时收尸:先杀进程,再等 communicate 收管道(顺序不能反,见 shell_exec 注释)。"""
    _kill(proc)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(comm, timeout=_SHELL_KILL_GRACE)


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
        # 独立会话 = 独立进程组:超时才杀得干净(见 _kill);顺带切断 tty,
        # 命令拿不到终端也就抢不走宿主的前台/信号
        start_new_session=True,
    )
    # 双超时竞态:注册表 spec.timeout 是外层硬闸门,内层再等下去就没意义了——
    # 模型传 timeout=120 而 spec 是 30 时,外层先到并抛 CancelledError,本函数的
    # except 走不到,proc 永不被 kill(留孤儿子进程)。故把内层钳到 spec 以内,
    # 留一点余量给杀进程收尸。
    effective = min(float(timeout), _SHELL_SPEC_TIMEOUT - _SHELL_KILL_GRACE)
    # shield:超时**不取消** communicate。直接取消它会让子进程的管道传输永不关闭,
    # 随后的 proc.wait() 等不到 connection_lost 而死锁(挂起比留孤儿更糟)。
    # 正确次序是先杀进程 → 管道 EOF → communicate 自然收尾。
    comm = asyncio.ensure_future(proc.communicate())
    try:
        out, err = await asyncio.wait_for(asyncio.shield(comm), timeout=effective)
    except TimeoutError:
        await _reap(proc, comm)
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.TIMEOUT,
                message=f"命令超过 {effective:g}s 未结束,已终止",
                retryable=True,
                hint=(
                    f"缩短命令运行时间;timeout 参数上限受注册表约束"
                    f"(spec.timeout={_SHELL_SPEC_TIMEOUT:g}s),更长的活儿请拆分或转后台"
                ),
            ),
        )
    except asyncio.CancelledError:
        # 外层(注册表超时 / run 中止)取消:同样不能留孤儿,杀完再把取消传上去
        _kill(proc)
        comm.cancel()
        raise
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
        data_domains=["net.*"],  # D2 数据层声明(docs/DATA-AUTHZ.md §3.1);未配置 [data] 时语义不变
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
        data_domains=["net.*"],  # D2 数据层声明(docs/DATA-AUTHZ.md §3.1);未配置 [data] 时语义不变
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


def _user_channel_not_assembled() -> ToolResult:
    """未装配 user 通道的结构化错误(同 memory 工具"未装配"报 NOT_FOUND 先例,§2.3)。"""
    return ToolResult(
        ok=False,
        error=ToolError(
            kind=ToolErrorKind.NOT_FOUND,
            message="user 通道未装配",
            retryable=False,
            hint="宿主经 LocalPythonToolRegistry.bind_user_channel(channel) 注入回调后再用",
        ),
    )


def ask_user_tool(*, name: str = "system.user.ask", registry: Any) -> Tool:
    """构造 ``system.user.ask``(User Communication 类,§8.3):经宿主回调向用户提问并等待回答。

    与 ask_supervisor 的分工:ask_supervisor 是**内核通道**(伪工具,内核拦截、
    就地挂起、pending 落盘、resume 重问,docs/SUPERVISOR.md §2),技能主动请示用;
    本工具是**工具面**形态——普通分发路径上的宿主回调(装配时经
    ``bind_user_channel`` 注入,bind 模式同 bind_memory 先例;CLI 宿主可用
    stdin/stderr 协议形态,同 _cli_supervisor 先例),无内核闸门/pending 语义,
    断电后按未配对调用的通用规则结算(§3.1 中断配对)。未 bind → NOT_FOUND。
    """

    async def ask_user(question: str, ctx: ToolContext | None = None) -> str | ToolResult:
        """向宿主用户提问并等待回答,返回回答文本。

        Use when 需要用户提供信息或做选择;Do not use when 请示权限/升级裁决
        (走 ask_supervisor 内核通道)。宿主未装配 user 通道 → NOT_FOUND。
        """
        channel = getattr(registry, "_user_channel", None)
        ask = getattr(channel, "ask", None) if channel is not None else None
        if not callable(ask):
            return _user_channel_not_assembled()
        answer = ask(question)
        if inspect.isawaitable(answer):  # 回调可同步可 async(同 registry 对 sync/async 函数的态度)
            answer = await answer
        return str(answer)

    spec = derive_spec(
        ask_user,
        name=name,
        # WRITE 档(不取 READ):等用户输入的交互既不可缓存也不可并行(STDLIB §8
        # 门槛的 READ⇒cacheable+concurrent_safe 红利对它不成立);WRITE 起占帧
        # 白名单(§W0-1)——能和用户对话的技能应在 manifest 里声明
        permission=Permission.WRITE,
        # 等用户输入远超常规工具耗时:注册表 spec.timeout 是硬闸门(§8.1),给足 1h;
        # 宿主侧的超时/取消经 run 控制通道(stop/cancel)到达
        timeout=3600.0,
        cost_hint="取决于人(秒到分钟级)",
    )
    return _FunctionTool(ask_user, spec)


def notify_user_tool(*, name: str = "system.user.notify", registry: Any) -> Tool:
    """构造 ``system.user.notify``(User Communication 类,§8.3):经宿主回调单向通知用户。

    单向语义:不等回答、无回答载荷;回调通道与 ``system.user.ask`` 同源
    (``bind_user_channel`` 装配,未 bind → NOT_FOUND)。
    """

    async def notify_user(message: str, ctx: ToolContext | None = None) -> str | ToolResult:
        """向宿主用户发一条单向通知(不等回答),返回确认串。

        Use when 需要汇报进展/结果而不需要回答;Do not use when 需要用户作答
        (用 system.user.ask)。宿主未装配 user 通道 → NOT_FOUND。
        """
        channel = getattr(registry, "_user_channel", None)
        notify = getattr(channel, "notify", None) if channel is not None else None
        if not callable(notify):
            return _user_channel_not_assembled()
        done = notify(message)
        if inspect.isawaitable(done):
            await done
        return "已通知用户"

    spec = derive_spec(
        notify_user,
        name=name,
        # WRITE 档:对用户可见的单向副作用(同 system.user.ask 的档位判定)
        permission=Permission.WRITE,
        timeout=30.0,
        cost_hint="~10ms(单向,不等回答)",
    )
    return _FunctionTool(notify_user, spec)


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

        def with_alias(self, alias: str) -> PythonExecTool:
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
