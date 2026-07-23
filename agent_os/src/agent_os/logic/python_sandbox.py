"""PythonSandboxLogicKernel(DESIGN.md §9.2/§9.7;M5)。

子进程 + ``setrlimit``(CPU/内存/文件大小)+ 临时只读工作目录 + 默认断网
(目的级白名单可配)。用于 LLM 动态代码(强制,无配置项可关闭)与声明
``logic: {mode: sandbox}`` 的 code 技能。
警示(§9.2):venv 不是沙箱——只隔离包依赖,文件系统/网络/进程全无约束。
隔离阶梯(后端替换,契约不变):subprocess+rlimits(v1)→ OS 级(seccomp/nsjail)→
容器 → microVM。

v1 极简版接受偏差:**网络隔离不做**——M5 用 ``unshare``/nsjail 加固;
文件系统仅靠 rlimits 与 ``-I`` 隔离,不是安全边界。
"""

from __future__ import annotations

import asyncio
import sys
import time

from agent_os.api.v1 import (
    ExecError,
    ExecRequest,
    ExecResult,
    ExecUsage,
    LogicError,
    TrustLevel,
)
from agent_os.logic.limits import apply_limits

#: 默认兜底墙钟(调用方未给 limits.wall_time 时)
_DEFAULT_WALL_TIME = 30.0


def _truncate(text: str, max_bytes: int | None) -> str:
    if max_bytes is None:
        return text
    return text.encode("utf-8", errors="replace")[:max_bytes].decode("utf-8", errors="replace")


class PythonSandboxLogicKernel:
    """``agent_os.api.v1.LogicKernel`` 协议实现(M5),进程级隔离。

    ``sys.executable -I -c <source>`` 起子进程;``preexec_fn`` 设 rlimits;
    父进程 ``wait_for`` 兜底 wall_time,超时杀进程 → LIMIT_EXCEEDED。
    """

    name: str = "python_sandbox"
    trust: TrustLevel = TrustLevel.SANDBOX

    async def execute(self, req: ExecRequest) -> ExecResult:
        limits = req.limits
        wall = limits.wall_time if limits.wall_time else _DEFAULT_WALL_TIME

        def preexec() -> None:
            apply_limits(limits)

        start = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            req.source,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=preexec,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=wall)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            ms = int((time.perf_counter() - start) * 1000)
            return ExecResult(
                error=ExecError(
                    kind=LogicError.LIMIT_EXCEEDED,
                    message=f"超过 wall_time={wall}s,子进程已杀",
                ),
                usage=ExecUsage(cpu_ms=ms, wall_ms=ms),
            )
        ms = int((time.perf_counter() - start) * 1000)
        stdout = _truncate(stdout_b.decode("utf-8", errors="replace"), limits.stdout_bytes)
        stderr = _truncate(stderr_b.decode("utf-8", errors="replace"), limits.stdout_bytes)
        # time.process_time 不含子进程;v1 记 wall_ms,cpu_ms 同 wall_ms(§9.7 usage 口径简化)
        usage = ExecUsage(cpu_ms=ms, wall_ms=ms)
        if proc.returncode != 0:
            lines = stderr.strip().splitlines()
            tail = lines[-1] if lines else f"退出码 {proc.returncode}"
            return ExecResult(
                stdout=stdout,
                stderr=stderr,
                error=ExecError(kind=LogicError.RUNTIME_ERROR, message=tail[-500:], traceback=stderr),
                usage=usage,
            )
        # result 解析(stdout 最后一行 JSON)由 python_exec 工具层做(§9.4)
        return ExecResult(value=stdout, stdout=stdout, stderr=stderr, usage=usage)
