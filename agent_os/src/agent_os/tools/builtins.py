"""内置工具(DESIGN.md §8.3;M1 四个基础件已实现,M5 补 fs_edit/python_exec 等)。

docstring 倡导"何时用/边界/负例"(§8.4);签名即 schema 推导来源。fs/shell 工具经
约定参数 ``ctx`` 取 ``ToolContext``(见 ``local_registry._FunctionTool``),限定在
run 工作目录内操作(§2.2);需要结构化错误时函数直接返回 ``ToolResult``。
"""

from __future__ import annotations

import asyncio
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
)
from agent_os.tools.local_registry import _FunctionTool, derive_spec


def _resolve_in_workdir(ctx: ToolContext | None, path: str) -> Path | ToolResult:
    """把 ``path`` 解析到 run 工作目录内;逃逸(``../``、绝对路径)→ INVALID_ARGS(§2.2)。"""
    workdir = Path(ctx.workdir if ctx is not None else ".").resolve()
    target = Path(workdir, path).resolve()
    if not target.is_relative_to(workdir):
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.INVALID_ARGS,
                message=f"路径越界: {path}(fs 工具限定在工作目录内)",
                retryable=False,
            ),
        )
    return target


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
            ),
        )
    if not target.is_file():
        return ToolResult(
            ok=False,
            error=ToolError(
                kind=ToolErrorKind.NOT_FOUND,
                message=f"文件不存在: {path}",
                retryable=False,
            ),
        )
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    window = lines[offset - 1 : offset - 1 + limit]
    return "\n".join(f"{n}\t{line}" for n, line in enumerate(window, start=offset))


async def fs_write(path: str, content: str, ctx: ToolContext | None = None) -> str | ToolResult:
    """写文件(整体覆盖,自动建父目录,§8.3)。

    Use when 需要创建或整体覆盖文件;Do not use when 只需局部修改(用 fs_edit)。
    """
    target = _resolve_in_workdir(ctx, path)
    if isinstance(target, ToolResult):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"已写入 {path}({len(content)} 字符)"


async def fs_edit(path: str, old_string: str, new_string: str) -> str:
    """局部编辑(old_string→new_string 唯一匹配,否则报错,§8.3)。"""
    raise NotImplementedError("M5")


async def shell_exec(command: str, timeout: int = 30, ctx: ToolContext | None = None) -> str | ToolResult:
    """执行 shell 命令(EXEC 级;一次性子进程,在帧工作目录内运行,捕获 stdout/stderr)。

    持久会话形态(跨调用保持 cwd/env)留待后续里程碑;超时先由 ``timeout`` 参数
    杀进程并返回 TIMEOUT,注册表 ``spec.timeout`` 兜底。防护主体是沙箱(§9.2)+
    权限(§8.2);ToolGuard 正则仅为辅助(§5.4 能力上限)。
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
            ),
        )
    parts = [out.decode("utf-8", errors="replace")]
    if err:
        parts.append(f"[stderr]\n{err.decode('utf-8', errors='replace')}")
    if proc.returncode:
        parts.append(f"[exit code {proc.returncode}] 命令非零退出")
    return "\n".join(parts)


def http_fetch_tool(transport: httpx.AsyncBaseTransport | None = None) -> Tool:
    """构造 ``http_fetch`` 内置工具(NET 级,§8.3);``transport`` 供测试注入 MockTransport。

    HTTP 错误状态(4xx/5xx)不算工具错误,照常返回 ``status``;结果标记
    ``untrusted_source``(§2.2,归一化时包裹来源标记,注入防御)。
    """

    async def http_fetch(url: str, max_bytes: int = 100_000) -> dict[str, Any]:
        """抓取 URL,返回 ``{"status", "content", "truncated"}``(content 超 max_bytes 截断)。

        Use when 需要读取公开网页;Do not use when 需要登录态。
        """
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=True, timeout=30.0
        ) as client:
            resp = await client.get(url)
            raw = await resp.aread()
        truncated = len(raw) > max_bytes
        content = raw[:max_bytes].decode(resp.encoding or "utf-8", errors="replace")
        return {"status": resp.status_code, "content": content, "truncated": truncated}

    spec = derive_spec(http_fetch, permission=Permission.NET, timeout=30.0, untrusted_source=True)
    return _FunctionTool(http_fetch, spec)


async def blob_get(ref: str, offset: int = 0, limit: int | None = None) -> str:
    """分页读取 blob(§8.3 offset/limit)。"""
    raise NotImplementedError("M3")


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
    """构造 ``python_exec`` 内置工具(§9.4):薄壳,委托传入的 SANDBOX Logic Kernel。

    value 形状 ``{"stdout", "stderr", "result"}``;执行错误 → TIMEOUT(LIMIT_EXCEEDED)
    或 INTERNAL,hint 带 stderr 尾部。提供可选 ``bind(signals)``:KernelBuilder 装配时
    注入总线,执行前后发 ``pre:logic.exec`` / ``post:logic.exec``(§9.5)。
    """

    class PythonExecTool:
        spec = ToolSpec(
            name="python_exec",
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

        def __init__(self, logic_kernel: LogicKernel) -> None:
            self._kernel = logic_kernel
            self._signals: Any = None

        def bind(self, signals: Any) -> None:
            """KernelBuilder 装配钩子:注入信号总线(§9.5 监督信号)。"""
            self._signals = signals

        async def _emit(self, name: str, ctx: ToolContext, payload: dict[str, Any]) -> None:
            if self._signals is not None:
                await self._signals.emit(
                    Signal(name=name, run_id=ctx.run_id, frame_id=ctx.frame_id, payload=payload)
                )

        async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
            req = ExecRequest(
                source=args["code"],
                limits=ResourceLimits(wall_time=10, cpu_time=5, memory_mb=256, stdout_bytes=100_000),
            )
            await self._emit(PRE_LOGIC_EXEC, ctx, {"tool": "python_exec"})
            res = await self._kernel.execute(req)
            await self._emit(POST_LOGIC_EXEC, ctx, {"tool": "python_exec", "ok": res.error is None})
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
