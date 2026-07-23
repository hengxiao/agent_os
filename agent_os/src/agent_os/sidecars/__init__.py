"""Sidecars 子系统(DESIGN.md §5;M4 supervisor 与基础 sidecar,M5 CodeScanner)。

被信号触发的监督者:观察运行、监督行为,必要时强行停止或控制上下文。
"""

from .builtins import (
    BudgetGuard,
    CodeScanner,
    HumanApproval,
    LoopDetector,
    StallDetector,
    ToolGuard,
)
from .supervisor import SidecarSupervisor

__all__ = [
    "BudgetGuard",
    "CodeScanner",
    "HumanApproval",
    "LoopDetector",
    "SidecarSupervisor",
    "StallDetector",
    "ToolGuard",
]
