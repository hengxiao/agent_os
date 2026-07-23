"""KernelBuilder(DESIGN.md §14.2;M0 组装)。

链式组装内核与九个子系统::

    kernel = (KernelBuilder(config)
              .providers(OpenAICompatibleProvider(base_url=..., api_key=...))
              .tools(LocalPythonToolRegistry.with_builtins())
              .skills(LocalFileSkillRegistry("./skills.yaml"))
              .context(ContextManager.default(RollingWindowCompressor()))
              .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
              .sidecars(BudgetGuard(max_cost=2.0), LoopDetector())
              .telemetry(JsonlTelemetrySink("./traces"))
              .memory(LocalFileMemoryService("./memory"))
              .blackboard(LocalBlackboard())
              .build())

消融测试(§14.2):不挂任何子系统(仅 MockProvider + 空工具表)时裸 loop 仍能跑通——
微内核纯粹性的验收。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import RunConfig
from agent_os.context.manager import MinimalContextManager
from agent_os.kernel import Kernel
from agent_os.kernel.errors import SkillLoadError
from agent_os.kernel.logic_router import LogicKernelRouter
from agent_os.kernel.signals import InProcessSignalBus
from agent_os.kernel.stack import FrameStack
from agent_os.providers.manager import ProviderManager
from agent_os.tools.local_registry import LocalPythonToolRegistry


class KernelBuilder:
    """按 §14.2 收集子系统并构建 Kernel。"""

    def __init__(self, config: RunConfig | None = None) -> None:
        self.config = config or RunConfig()
        self._providers: list[Any] = []
        self._tools: Any = None
        self._skills: Any = None
        self._context: Any = None
        self._logic_kernels: list[Any] = []
        self._sidecars: list[Any] = []
        self._telemetry: Any = None
        self._memory: Any = None
        self._blackboard: Any = None

    def providers(self, *providers: Any) -> KernelBuilder:
        self._providers.extend(providers)
        return self

    def tools(self, registry: Any) -> KernelBuilder:
        self._tools = registry
        return self

    def skills(self, registry: Any) -> KernelBuilder:
        self._skills = registry
        return self

    def context(self, manager: Any) -> KernelBuilder:
        self._context = manager
        return self

    def logic_kernels(self, *kernels: Any) -> KernelBuilder:
        self._logic_kernels.extend(kernels)
        return self

    def sidecars(self, *sidecars: Any) -> KernelBuilder:
        self._sidecars.extend(sidecars)
        return self

    def telemetry(self, sink: Any) -> KernelBuilder:
        self._telemetry = sink
        return self

    def memory(self, service: Any) -> KernelBuilder:
        self._memory = service
        return self

    def blackboard(self, blackboard: Any) -> KernelBuilder:
        self._blackboard = blackboard
        return self

    def build(self) -> Kernel:
        """组装 Kernel(注入信号总线 / FrameStack / Dispatcher / RunControl 等内核件)。

        本纵向切片的装配边界:sidecars/telemetry/memory/blackboard 尚未接线,
        传入了为避免静默丢弃直接拒绝(各自里程碑再做);缺省补 MinimalContextManager;
        logic_kernels 按 TrustLevel 索引装配为 LogicKernelRouter(§9.2);
        装配期校验各技能 manifest.permissions.tools 都在工具注册表中(§6.1 权限闸门)。
        """
        unsupported: list[str] = []
        if self._sidecars:
            unsupported.append("sidecars")
        if self._telemetry is not None:
            unsupported.append("telemetry")
        if self._memory is not None:
            unsupported.append("memory")
        if self._blackboard is not None:
            unsupported.append("blackboard")
        if unsupported:
            raise NotImplementedError(
                f"M0 纵向切片不接入 {', '.join(unsupported)}(后续里程碑);"
                f"传入了会被静默丢弃,故直接拒绝"
            )
        bus = InProcessSignalBus()
        providers = ProviderManager(list(self._providers))
        tools = self._tools if self._tools is not None else LocalPythonToolRegistry()
        skills = self._skills
        if skills is not None:
            skills.load()
            for m in skills.manifests():
                missing = [t for t in m.permissions.tools if not tools.has(t)]
                if missing:
                    raise SkillLoadError(
                        f"技能 {m.name} 声明的工具未在工具注册表中注册: {missing}(§6.1 权限闸门)"
                    )
        context = self._context or MinimalContextManager(skills=skills, tools=tools, config=self.config)
        if hasattr(tools, "bind_signals"):
            tools.bind_signals(bus)
        return Kernel(
            config=self.config,
            providers=providers,
            tools=tools,
            skills=skills,
            context=context,
            logic=LogicKernelRouter(list(self._logic_kernels)),
            signals=bus,
            stack=FrameStack(max_depth=self.config.max_depth),
        )
