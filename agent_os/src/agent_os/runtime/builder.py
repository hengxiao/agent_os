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

from agent_os.api.v1 import ASK_SUPERVISOR_TOOL, ORCHESTRATE_TOOL, RunConfig
from agent_os.context.manager import ContextManager
from agent_os.context.rolling_window import RollingWindowCompressor
from agent_os.kernel import Kernel
from agent_os.kernel.control import RunControlImpl
from agent_os.kernel.errors import SkillLoadError
from agent_os.kernel.logic_router import LogicKernelRouter
from agent_os.kernel.signals import InProcessSignalBus
from agent_os.kernel.stack import FrameStack
from agent_os.providers.manager import ProviderManager
from agent_os.sidecars.supervisor import SidecarSupervisor
from agent_os.supervisor import SupervisorManager
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
        self._supervisor: dict[str, Any] | None = None
        self._retry: dict[str, Any] = {}

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

    def supervisor(
        self,
        handler: Any = None,
        *,
        timeout_s: float = 120.0,
        on_timeout: str = "fail",
        default_answer: str = "",
    ) -> KernelBuilder:
        """注入 supervisor 调用方通道(SUPERVISOR.md §2.3 handler 通道 / §6 配置;S1)。

        ``handler``:``async def handler(question: Question) -> Answer``(契约见
        ``api/v1/supervisor.py``);``None`` 表示仅预置策略字段(TOML
        ``[supervisor]`` 段形态,handler 由宿主通道经同一 API 注入——S2:
        ``build_kernel(supervisor_handler=...)`` 的 Web 收件箱 / CLI 协议),
        此时 build 不装配 SupervisorManager。
        """
        self._supervisor = {
            "handler": handler,
            "timeout_s": timeout_s,
            "on_timeout": on_timeout,
            "default_answer": default_answer,
        }
        return self

    def prices(self, table: dict[str, dict[str, float]] | None) -> KernelBuilder:
        """模型单价表(RUNNERS.md §2.1 ``[prices]``):每百万 token 美元价。

        没有它 ``usage.cost`` 恒为 0,``RunConfig.max_cost`` 与 BudgetGuard
        都不会触发(fail-open 在钱上),故 :func:`build` 会在缺表时告警。
        """
        if table:
            self._retry["prices"] = table
        return self

    def retry(
        self, *, max_attempts: int | None = None, backoff_base: float | None = None
    ) -> KernelBuilder:
        """ProviderManager 重试参数(RUNNERS.md §2.1 ``[retry]``;None 保持 Manager 默认)。"""
        if max_attempts is not None:
            self._retry["max_attempts"] = max_attempts
        if backoff_base is not None:
            self._retry["backoff_base"] = backoff_base
        return self

    def build(self) -> Kernel:
        """组装 Kernel(注入信号总线 / FrameStack / Dispatcher / RunControl 等内核件)。

        本纵向切片的装配边界:memory 尚未接线,
        传入了为避免静默丢弃直接拒绝(各自里程碑再做);缺省补 ContextManager
        (M3:RollingWindowCompressor + 状态注入 + pre/post:compress 信号,§7);
        logic_kernels 按 TrustLevel 索引装配为 LogicKernelRouter(§9.2);
        sidecars(M4)装配 RunControlImpl + SidecarSupervisor 并注册到总线(§5);
        telemetry(M5a)作为总线特权订阅者接入(§5.1:全量订阅,不算 sidecar);
        blackboard(M5b)接线到 kernel.blackboard(§12:StatusBoard 与帧间消息);
        supervisor(S1)有 handler 才装配 SupervisorManager 挂到 kernel.supervisor
        (SUPERVISOR.md §2.3;仅预置策略字段时不装配,运行时按"未装配"报 not_found);
        装配期权限闸门(§6.1):manifest 声明的工具必须在注册表中,缺失即拒绝加载。
        """
        unsupported: list[str] = []
        if self._memory is not None:
            unsupported.append("memory")
        if unsupported:
            raise NotImplementedError(
                f"M0 纵向切片不接入 {', '.join(unsupported)}(后续里程碑);"
                f"传入了会被静默丢弃,故直接拒绝"
            )
        bus = InProcessSignalBus()
        providers = ProviderManager(list(self._providers), **self._retry)
        tools = self._tools if self._tools is not None else LocalPythonToolRegistry()
        skills = self._skills
        if skills is not None:
            skills.load()
            missing = sorted(
                {
                    t
                    for m in skills.manifests()
                    for t in m.permissions.tools
                    # 伪工具由内核拦截,不进 registry(python_orchestrate /
                    # ask_supervisor,SUPERVISOR.md §2.1)
                    if not tools.has(t) and t not in (ORCHESTRATE_TOOL, ASK_SUPERVISOR_TOOL)
                }
            )
            if missing:
                raise SkillLoadError(f"manifest 声明的工具未注册(§6.1 权限闸门): {missing}")
        sup_manager = None
        if self._supervisor is not None and self._supervisor["handler"] is not None:
            # SUPERVISOR.md §2.3:装配级 handler 通道(S2 宿主通道——Web 收件箱 /
            # CLI 协议——经 build_kernel(supervisor_handler=...) 走同一注入入口)
            sup_manager = SupervisorManager(self._supervisor["handler"], signals=bus, **{
                k: self._supervisor[k] for k in ("timeout_s", "on_timeout", "default_answer")
            })
        context = self._context or ContextManager.default(
            RollingWindowCompressor(),
            skills=skills,
            tools=tools,
            config=self.config,
            signals=bus,
            # S2(SUPERVISOR.md §2.1):ask_supervisor 伪工具 schema 只在装了
            # supervisor 通道时呈现给 LLM;嵌入方自带 context manager 时自行决定
            supervisor=sup_manager is not None,
        )
        if hasattr(tools, "bind_signals"):
            tools.bind_signals(bus)
        if skills is not None and hasattr(tools, "bind_skills"):
            tools.bind_skills(skills)  # §W1-5:skill_search 的技能数据源(bind 模式,同 python_exec)
        if self._telemetry is not None:
            # §5.1:Telemetry 是总线的特权订阅者(全量订阅),不算 sidecar
            bus.subscribe("*", self._telemetry.record)
        kernel = Kernel(
            config=self.config,
            providers=providers,
            tools=tools,
            skills=skills,
            context=context,
            logic=LogicKernelRouter(list(self._logic_kernels)),
            signals=bus,
            telemetry=self._telemetry,
            blackboard=self._blackboard,
            supervisor=sup_manager,
            stack=FrameStack(max_depth=self.config.max_depth),
        )
        if self._sidecars:
            # §5.2/§5.3:RunControl 是 sidecar 操控运行的唯一通道;supervisor 统一托管
            ctl = RunControlImpl(kernel)
            supervisor = SidecarSupervisor(bus, ctl)
            for sidecar in self._sidecars:
                supervisor.register(sidecar)
            kernel.sidecars = supervisor
            kernel.ctl = ctl
        return kernel
