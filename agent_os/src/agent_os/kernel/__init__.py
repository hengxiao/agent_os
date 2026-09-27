"""微内核(docs/DESIGN.md §3):只做流控制 + 权限控制 + IPC。

async 调用链即 Skill 调用栈(§3.1)。:class:`Kernel`(:mod:`.runner`)为完整
agent loop 实现(工具分发/子技能压栈/safe point 检查均内化在 runner 内)。
"""

from .control import KernelRunControl, RunControlImpl
from .run import Run
from .runner import Kernel, run_frame
from .signals import InProcessSignalBus
from .stack import FrameStack

__all__ = [
    "FrameStack",
    "InProcessSignalBus",
    "Kernel",
    "KernelRunControl",
    "Run",
    "RunControlImpl",
    "run_frame",
]
