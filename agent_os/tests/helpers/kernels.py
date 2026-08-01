"""内核组装快捷方式:各测试文件自建 RunConfig(约束留在测试里可见),组装链共享。"""

from __future__ import annotations

import sys
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


async def auto_approve(question) -> dict:
    """升权闸门的测试通道(ESCALATION.md §3;E1):一律 approve-once。

    生产宿主(Web 收件箱 / CLI 协议)恒有 supervisor 通道;在闸门出现前写成的
    用例装配本 handler,等价于"人每次都批准本次调用",原有断言语义不变。
    """
    return {"answer": "approve-once", "decided_by": "test:auto-approve"}


def sandbox_tools(*, builtins: bool = False) -> LocalPythonToolRegistry:
    """注册了 system.python.exec(子进程沙箱)的工具注册表。"""
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
    debug_controller=None,
    supervisor=None,
):
    """标准组装链:MockProvider(brain) + sandbox 工具 + 双 Logic Kernel。

    ``brain`` 可以是应答函数,也可以是现成 Provider 实例。
    ``supervisor`` 给定时装配 supervisor 通道(升权闸门需要确认通道,ESCALATION.md §3)。
    """
    provider = brain if hasattr(brain, "chat") else MockProvider(brain)
    builder = (
        KernelBuilder(config)
        .providers(provider)
        .tools(tools if tools is not None else sandbox_tools())
        .skills(LocalFileSkillRegistry(str(skills)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if supervisor is not None:
        builder = builder.supervisor(supervisor)
    if sidecars:
        builder = builder.sidecars(*sidecars)
    if blackboard is not None:
        builder = builder.blackboard(blackboard)
    if telemetry_dir is not None:
        builder = builder.telemetry(JsonlTelemetrySink(str(telemetry_dir)))
    if debug_controller is not None:
        builder = builder.debug_controller(debug_controller)
    return builder.build()


def fib_kernel(brain=None, *, max_depth: int = 8, telemetry_dir=None):
    """跑仓库 ``skills/skills.yaml`` fib 技能的最小内核。"""
    from tests.helpers.brains import fib_brain

    config = RunConfig(
        model="mock/fib",
        max_depth=max_depth,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),  # system.python.exec 是 EXEC 级
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


def load_example_module(example: str, filename: str = "brains.py"):
    """按文件路径加载 examples 下的模块,**用专属模块名**避免跨示例串扰。

    四个示例各有一个 ``brains.py``;若都以扁平名 ``import brains`` 加载,
    ``sys.modules["brains"]`` 先缓存者胜 —— 测试的通过与否取决于**执行顺序**
    (实测:排除任一文件即 4 个确定性失败),且单跑与全量跑走不同代码路径。
    专属模块名(``<example>_brains``)从根上消除这个耦合。
    """
    import importlib.util

    path = PROJECT_ROOT / "examples" / example / filename
    mod_name = f"{example}_{Path(filename).stem}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover — 路径写错才会到这
        raise ImportError(f"无法按路径加载示例模块: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module
