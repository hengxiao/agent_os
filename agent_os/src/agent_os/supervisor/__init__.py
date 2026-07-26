"""supervisor 子系统(SUPERVISOR.md v2;S1 内核机制 + handler 通道)。

面向 agent 调用方的裁决路由:接收 ``ask_supervisor`` 请求,经注入的 handler
通道送达本 run 的调用方,带超时与兜底策略;帧的就地挂起/恢复机制在内核
(``kernel/runner.py``),本子系统只承载策略(§1.3 边界)。
"""

from .manager import SupervisorManager

__all__ = [
    "SupervisorManager",
]
