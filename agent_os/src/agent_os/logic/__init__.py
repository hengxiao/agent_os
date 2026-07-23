"""Logic Kernel 子系统(DESIGN.md §9):逻辑代码的唯一执行点(类比 CPU/ALU)。

内核负责调度与仲裁,但亲自不执行任何指令;code 技能与动态代码都从这个咽喉点过。
"""

from .inprocess import InProcessLogicKernel
from .python_sandbox import PythonSandboxLogicKernel

__all__ = ["InProcessLogicKernel", "PythonSandboxLogicKernel"]
