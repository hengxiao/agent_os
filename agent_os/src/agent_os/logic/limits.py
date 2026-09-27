"""ResourceLimits 应用辅助(docs/DESIGN.md §9.1/§9.2;M5 沙箱限额)。

setrlimit 映射:cpu_time → RLIMIT_CPU、memory_mb → RLIMIT_AS、
stdout_bytes → RLIMIT_FSIZE;wall_time 由父进程 watchdog 兜底。
"""

from __future__ import annotations

import math

from agent_os.api.v1 import ResourceLimits

#: 子进程文件写上限(防日志炸弹);网络隔离 v1 不做,M5 用 unshare/nsjail 加固(§9.2)
_FSIZE_BYTES = 1024 * 1024
_NOFILE = 32


def apply_limits(limits: ResourceLimits) -> None:
    """在子进程中应用 rlimits(``preexec_fn`` 语义,fork 后 exec 前调用)。"""
    import resource

    if limits.cpu_time:
        cpu = max(1, math.ceil(limits.cpu_time))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    if limits.memory_mb:
        mem = int(limits.memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_FSIZE, (_FSIZE_BYTES, _FSIZE_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (_NOFILE, _NOFILE))


def merge_limits(base: ResourceLimits, override: ResourceLimits) -> ResourceLimits:
    """两级限额取紧(§9.1):逐字段取最严——``None`` = 该级未设(不约束,取另一级),
    两级都设取更小值。

    RunConfig / manifest / 调用方三级取紧即链式调用:
    ``merge_limits(merge_limits(run_cfg, manifest), caller)``。
    """

    def _tighter(a: float | None, b: float | None) -> float | None:
        if a is None:
            return b
        if b is None:
            return a
        return min(a, b)

    return ResourceLimits(
        wall_time=_tighter(base.wall_time, override.wall_time),
        cpu_time=_tighter(base.cpu_time, override.cpu_time),
        memory_mb=_tighter(base.memory_mb, override.memory_mb),
        stdout_bytes=_tighter(base.stdout_bytes, override.stdout_bytes),
    )


#: 沙箱执行的内核默认限额(§9.1):仅在 manifest 与调用方都未设时回填,不作上限
DEFAULT_EXEC_LIMITS = ResourceLimits(
    wall_time=60.0, cpu_time=60.0, memory_mb=256, stdout_bytes=100_000
)


def exec_limits(
    manifest_timeout: float | None = None, caller_timeout: float | None = None
) -> ResourceLimits:
    """沙箱执行限额的合成(§9.1 三级取紧的 v1 形态):manifest ``limits.timeout`` 与
    调用方 timeout 经 :func:`merge_limits` 取紧(调用方不能放松 skill 声明的上限),
    未设字段回填 :data:`DEFAULT_EXEC_LIMITS`(默认是回填而非上限,不收紧既有行为)。
    """
    merged = merge_limits(
        ResourceLimits(
            wall_time=manifest_timeout,
            cpu_time=manifest_timeout,
            memory_mb=None,
            stdout_bytes=None,
        ),
        ResourceLimits(
            wall_time=caller_timeout,
            cpu_time=caller_timeout,
            memory_mb=None,
            stdout_bytes=None,
        ),
    )
    return ResourceLimits(
        wall_time=(
            merged.wall_time if merged.wall_time is not None else DEFAULT_EXEC_LIMITS.wall_time
        ),
        cpu_time=merged.cpu_time if merged.cpu_time is not None else DEFAULT_EXEC_LIMITS.cpu_time,
        memory_mb=(
            merged.memory_mb if merged.memory_mb is not None else DEFAULT_EXEC_LIMITS.memory_mb
        ),
        stdout_bytes=(
            merged.stdout_bytes
            if merged.stdout_bytes is not None
            else DEFAULT_EXEC_LIMITS.stdout_bytes
        ),
    )
