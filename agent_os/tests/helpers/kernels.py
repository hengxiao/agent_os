"""内核组装快捷方式:各测试文件自建 RunConfig(约束留在测试里可见),组装链共享。"""

from __future__ import annotations

from pathlib import Path

from agent_os.api.v1 import Permission, RunConfig, Signal, ToolPolicy
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.telemetry import JsonlTelemetrySink
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIB_SKILLS_YAML = PROJECT_ROOT / "skills" / "skills.yaml"


def sandbox_tools(*, builtins: bool = False) -> LocalPythonToolRegistry:
    """注册了 python_exec(子进程沙箱)的工具注册表。"""
    reg = LocalPythonToolRegistry.with_builtins() if builtins else LocalPythonToolRegistry()
    reg.register(python_exec_tool(PythonSandboxLogicKernel()))
    return reg


def assemble(
    config: RunConfig,
    brain,
    skills: str | Path,
    *,
    tools: LocalPythonToolRegistry | None = None,
    sidecars=(),
    blackboard=None,
    telemetry_dir: str | Path | None = None,
):
    """标准组装链:MockProvider(brain) + sandbox 工具 + 双 Logic Kernel。

    ``brain`` 可以是应答函数,也可以是现成 Provider 实例。
    """
    provider = brain if hasattr(brain, "chat") else MockProvider(brain)
    builder = (
        KernelBuilder(config)
        .providers(provider)
        .tools(tools if tools is not None else sandbox_tools())
        .skills(LocalFileSkillRegistry(str(skills)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if sidecars:
        builder = builder.sidecars(*sidecars)
    if blackboard is not None:
        builder = builder.blackboard(blackboard)
    if telemetry_dir is not None:
        builder = builder.telemetry(JsonlTelemetrySink(str(telemetry_dir)))
    return builder.build()


def fib_kernel(brain=None, *, max_depth: int = 8, telemetry_dir=None):
    """跑仓库 ``skills/skills.yaml`` fib 技能的最小内核。"""
    from tests.helpers.brains import fib_brain

    config = RunConfig(
        model="mock/fib",
        max_depth=max_depth,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),  # python_exec 是 EXEC 级
        compression="off",
    )
    return assemble(config, brain or fib_brain, FIB_SKILLS_YAML, telemetry_dir=telemetry_dir)


def record_all(kernel) -> list[Signal]:
    """订阅全部信号,返回随运行增长的列表。"""
    seen: list[Signal] = []

    async def rec(sig: Signal) -> None:
        seen.append(sig)

    kernel.signals.subscribe("*", rec)
    return seen
