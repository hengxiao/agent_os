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
    """RunConfig / manifest / 调用方三级限额取紧(§9.1)。"""
    raise NotImplementedError("M5")
