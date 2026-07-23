"""InProcessLogicKernel(DESIGN.md §9.7;M2)。

``trust = TRUSTED``;进程内 ``await asyncio.wait_for(handler(args, ctx), wall_time)``;
stdout/stderr 捕获(``contextlib.redirect_stdout/stderr`` → StringIO);
返回值 JSON 序列化检查(不可序列化 → RUNTIME_ERROR);usage:wall_ms + cpu_ms,
mem_peak_mb 不支持记 0;超时 → LIMIT_EXCEEDED。
明确不做:内存限额、隔离、网络管控——那是 PythonSandboxLogicKernel(M5)存在的意义。
"""

from __future__ import annotations

import asyncio
import contextlib
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
    TrustLevel,
)

_DEFAULT_WALL_TIME = 30.0


class InProcessLogicKernel:
    """``agent_os.api.v1.LogicKernel`` 协议实现(M2),用于可信 code 技能(默认)与调试。"""

    name: str = "inprocess"
    trust: TrustLevel = TrustLevel.TRUSTED

    async def execute(self, req: ExecRequest) -> ExecResult:
        wall = req.limits.wall_time if req.limits.wall_time else _DEFAULT_WALL_TIME
        stdout_io, stderr_io = io.StringIO(), io.StringIO()
        ns: dict[str, Any] = {"__name__": "__logic_inprocess__"}
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        value: Any = None
        try:
            with contextlib.redirect_stdout(stdout_io), contextlib.redirect_stderr(stderr_io):
                exec(compile(req.source, "<inprocess-logic>", "exec"), ns)  # noqa: S102
                entry = ns.get(req.entry)
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
