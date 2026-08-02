"""InProcessLogicKernel(docs/DESIGN.md §9.7;M2)。

``trust = TRUSTED``;进程内 ``await asyncio.wait_for(handler(args, ctx), wall_time)``;
stdout/stderr 捕获(``contextlib.redirect_stdout/stderr`` → StringIO);
返回值 JSON 序列化检查(不可序列化 → RUNTIME_ERROR);usage:wall_ms + cpu_ms,
mem_peak_mb 不支持记 0;超时 → LIMIT_EXCEEDED。
入口两种形态:``source`` 为源码文本 → exec 后取 ``entry``;为可 import 的模块路径
(code 技能,§6.3)→ ``importlib.import_module`` 后 ``getattr``。
硬失败(RunAborted/MaxDepthExceeded)与权限仲裁失败(SkillLoadError,如 spawn/invoke
白名单拒绝、board 命名空间拒绝)不折成 ExecResult,原样上抛(§3.2;fail-closed)。
明确不做:内存限额、隔离、网络管控——那是 PythonSandboxLogicKernel(M5)存在的意义。
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import inspect
import io
import json
import time
import traceback
from typing import Any

from agent_os.api.v1 import (
    ExecError,
    ExecRequest,
    ExecResult,
    ExecUsage,
    LogicError,
    SkillError,
    TrustLevel,
)
from agent_os.kernel.errors import MaxDepthExceeded, RunAborted, SkillLoadError

_DEFAULT_WALL_TIME = 30.0


def _is_module_path(source: str) -> bool:
    """``pkg.mod`` 形态且当前可 import(find_spec 只探测);否则按源码文本处理。"""
    try:
        return importlib.util.find_spec(source) is not None
    except Exception:  # noqa: BLE001 — 探测失败一律按源码文本处理
        return False


def _resolve_entry(req: ExecRequest) -> Any:
    """模块路径 → import 后 ``getattr``;源码文本 → exec 后从命名空间取 ``entry``。"""
    if "\n" not in req.source and _is_module_path(req.source):
        module = importlib.import_module(req.source)
        return getattr(module, req.entry)
    ns: dict[str, Any] = {"__name__": "__logic_inprocess__"}
    exec(compile(req.source, "<inprocess-logic>", "exec"), ns)  # noqa: S102
    return ns.get(req.entry)


class InProcessLogicKernel:
    """``agent_os.api.v1.LogicKernel`` 协议实现(M2),用于可信 code 技能(默认)与调试。"""

    name: str = "inprocess"
    trust: TrustLevel = TrustLevel.TRUSTED

    async def execute(self, req: ExecRequest) -> ExecResult:
        wall = req.limits.wall_time if req.limits.wall_time else _DEFAULT_WALL_TIME
        stdout_io, stderr_io = io.StringIO(), io.StringIO()
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        value: Any = None
        try:
            with contextlib.redirect_stdout(stdout_io), contextlib.redirect_stderr(stderr_io):
                entry = _resolve_entry(req)
                if callable(entry):
                    value = entry(req.args, req.ctx)
                    if inspect.isawaitable(value):
                        value = await asyncio.wait_for(value, timeout=wall)
        except TimeoutError:
            return ExecResult(
                stdout=stdout_io.getvalue(),
                stderr=stderr_io.getvalue(),
                error=ExecError(kind=LogicError.LIMIT_EXCEEDED, message=f"超过 wall_time={wall}s"),
                usage=self._usage(wall_start, cpu_start),
            )
        except (RunAborted, MaxDepthExceeded, SkillLoadError):
            raise  # 硬失败/权限仲裁失败不折成 ExecResult,沿调用栈弹到 Run 边界(§3.2)
        except SkillError as e:
            # 结构化错误保真(§W0-3):kind/hint/retryable 不被压扁成一句人话
            return ExecResult(
                stdout=stdout_io.getvalue(),
                stderr=stderr_io.getvalue(),
                error=ExecError(
                    kind=e.kind,
                    message=str(e)[:500],
                    traceback=traceback.format_exc(),
                    hint=e.hint,
                    retryable=e.retryable,
                ),
                usage=self._usage(wall_start, cpu_start),
            )
        except Exception:  # noqa: BLE001 — 执行边界故意兜底:任意异常归一化为 RUNTIME_ERROR(§9.7)
            tb = traceback.format_exc()
            return ExecResult(
                stdout=stdout_io.getvalue(),
                stderr=stderr_io.getvalue(),
                error=ExecError(
                    kind=LogicError.RUNTIME_ERROR,
                    message=tb.strip().splitlines()[-1][:500],
                    traceback=tb,
                ),
                usage=self._usage(wall_start, cpu_start),
            )
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            return ExecResult(
                stdout=stdout_io.getvalue(),
                stderr=stderr_io.getvalue(),
                error=ExecError(
                    kind=LogicError.RUNTIME_ERROR,
                    message=f"入口 {req.entry} 返回值不可 JSON 序列化: {type(value).__name__}",
                ),
                usage=self._usage(wall_start, cpu_start),
            )
        return ExecResult(
            value=value,
            stdout=stdout_io.getvalue(),
            stderr=stderr_io.getvalue(),
            usage=self._usage(wall_start, cpu_start),
        )

    @staticmethod
    def _usage(wall_start: float, cpu_start: float) -> ExecUsage:
        return ExecUsage(
            cpu_ms=int((time.process_time() - cpu_start) * 1000),
            wall_ms=int((time.perf_counter() - wall_start) * 1000),
        )
