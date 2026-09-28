"""KernelBuilder(docs/DESIGN.md §14.2;M0 组装)。

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

import importlib.metadata
import warnings
from dataclasses import dataclass
from typing import Any

from agent_os.api.v1 import (
    ASK_SUPERVISOR_TOOL,
    ORCHESTRATE_TOOL,
    RunConfig,
    derive_skill_tier,
)
from agent_os.context.manager import ContextManager
from agent_os.context.narrate import NarrateCompressor
from agent_os.context.rolling_window import RollingWindowCompressor
from agent_os.context.spill import SpillCompressor
from agent_os.context.summarize import SummarizeCompressor
from agent_os.kernel import Kernel
from agent_os.kernel.control import RunControlImpl
from agent_os.kernel.errors import SkillLoadError
from agent_os.kernel.logic_router import LogicKernelRouter
from agent_os.kernel.signals import InProcessSignalBus
from agent_os.kernel.stack import FrameStack
from agent_os.providers.manager import ProviderManager
from agent_os.sidecars.builtins import DistillSidecar, HumanApproval
from agent_os.sidecars.supervisor import SidecarSupervisor
from agent_os.skills.manifest import validate_escalation_gates
from agent_os.supervisor import SupervisorManager
from agent_os.tools.local_registry import LocalPythonToolRegistry


@dataclass
class ContextSection:
    """``[context]`` 段(docs/RUNNERS.md §2.1)的装配形态:§7.2 压缩链调参。

    只在装配默认 ContextManager 时生效;嵌入方经 ``.context()`` 自装 manager 时
    本段不生效(注册表由嵌入方自负)。字段缺省值与 TOML 段缺席时的全默认一致。
    """

    summarize_model: str | None = None  # summarize 摘要模型;None = 跟 RunConfig.model
    spill_threshold_chars: int = 4000  # spill 触发阈值(单条 TOOL 消息字符数)
    summarize_breaker: int = 3  # summarize 连败熔断次数(熔断后退化 truncate)
    summarize_temperature: float = 0.2  # 摘要采样温度


@dataclass
class MemorySection:
    """``[memory]`` 段(docs/DESIGN.md §11)的装配形态:经验参考段(recall)调参。

    只在装配默认 ContextManager 时生效(同 :class:`ContextSection` 先例);recall 段
    还需 manifest ``context_policy.recall: true`` 才启用(opt-in,缺省关)。
    字段缺省值与 TOML 段缺键时的全默认一致。
    """

    recall_k: int = 3  # 检索条目数上限
    recall_entry_chars: int = 800  # 单条内容截断字符数
    recall_total_chars: int = 2000  # 经验参考段总量截尾字符数


def _compressor_plugins() -> dict[str, Any]:
    """entry point ``agent_os.compressors`` 插件加载(docs/DESIGN.md §7.5/§14.3)。

    逐条 ``ep.load()``:是类则无参实例化,是实例直接用;按 ``.name`` 入注册表
    (允许覆盖内置);单条坏只 ``warnings.warn`` 跳过,不杀装配;零条目 no-op。
    """
    registry: dict[str, Any] = {}
    for ep in importlib.metadata.entry_points(group="agent_os.compressors"):
        try:
            obj = ep.load()
            instance = obj() if isinstance(obj, type) else obj
            name = getattr(instance, "name", None)
            if not isinstance(name, str) or not name:
                raise TypeError(f"entry point {ep.name!r} 的压缩器缺 name 属性")
            registry[name] = instance
        except Exception as e:  # noqa: BLE001 — 单条坏 EP 只警告,不杀 build
            warnings.warn(f"压缩器 entry point 加载失败,已跳过: {ep.name}: {e}", stacklevel=2)
    return registry


def _distill_trigger(sidecar: DistillSidecar, ctl: Any) -> Any:
    """DistillSidecar 的总线直连 handler(装配理由见 build 内注释):内联触发判定。"""

    async def handler(sig: Any) -> None:
        await sidecar.on_signal(sig, ctl)

    return handler


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
        self._user_channel: Any = None
        self._blob: Any = None
        self._supervisor: dict[str, Any] | None = None
        self._debug_controller: Any = None
        self._retry: dict[str, Any] = {}
        self._context_section: ContextSection | None = None
        self._memory_section: MemorySection | None = None

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

    def context_section(self, section: ContextSection) -> KernelBuilder:
        """``[context]`` 段(§7.2 压缩链调参):装配默认 ContextManager 的注册表时用。

        嵌入方自装 ContextManager(``.context()``)时本段不生效——不强塞注册表。
        """
        self._context_section = section
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

    def memory_section(self, section: MemorySection) -> KernelBuilder:
        """``[memory]`` 段的 recall 调参:装配默认 ContextManager 时传入(同 context_section 先例)。

        嵌入方自装 ContextManager(``.context()``)时本段不生效——recall 调参由嵌入方自负。
        """
        self._memory_section = section
        return self

    def user_channel(self, channel: Any) -> KernelBuilder:
        """注入宿主用户通道(M1,§8.3):``ask(question)``/``notify(message)`` 回调对象。

        build 时经 ``tools.bind_user_channel`` 接线(bind 模式,同 memory);
        缺省 = 工具在场但调用报"user 通道未装配"结构化错误。
        """
        self._user_channel = channel
        return self

    def blob(self, store: Any) -> KernelBuilder:
        """注入 spill 的 blob store(M3):如文件版 FileBlobStore;缺省 = 进程内 InMemoryBlobStore。"""
        self._blob = store
        return self

    def blackboard(self, blackboard: Any) -> KernelBuilder:
        self._blackboard = blackboard
        return self

    def supervisor(
        self,
        handler: Any = None,
        *,
        timeout_s: float | None = None,
        on_timeout: str | None = None,
        default_answer: str | None = None,
    ) -> KernelBuilder:
        """注入 supervisor 调用方通道(docs/SUPERVISOR.md §2.3 handler 通道 / §6 配置;S1)。

        ``handler``:``async def handler(question: Question) -> Answer``(契约见
        ``api/v1/supervisor.py``);``None`` 表示仅预置策略字段(TOML
        ``[supervisor]`` 段形态,handler 由宿主通道经同一 API 注入——S2:
        ``build_kernel(supervisor_handler=...)`` 的 Web 收件箱 / CLI 协议),
        此时 build 不装配 SupervisorManager。
        策略字段缺省 None = 未显式配置:装配时先由 HumanApproval 策略(WS2,
        闸门与 ask_supervisor 共用本通道)补缺,再落 SupervisorManager 默认值
        (120s/fail/"")。
        """
        self._supervisor = {
            "handler": handler,
            "timeout_s": timeout_s,
            "on_timeout": on_timeout,
            "default_answer": default_answer,
        }
        return self

    def prices(self, table: dict[str, dict[str, float]] | None) -> KernelBuilder:
        """模型单价表(docs/RUNNERS.md §2.1 ``[prices]``):每百万 token 美元价。

        没有它 ``usage.cost`` 恒为 0,``RunConfig.max_cost`` 与 BudgetGuard
        都不会触发(fail-open 在钱上),故 :func:`build` 会在缺表时告警。
        """
        if table:
            self._retry["prices"] = table
        return self

    def retry(
        self,
        *,
        max_attempts: int | None = None,
        backoff_base: float | None = None,
        stream_idle_timeout: float | None = None,
    ) -> KernelBuilder:
        """ProviderManager 重试参数(docs/RUNNERS.md §2.1 ``[retry]``;None 保持 Manager 默认)。"""
        if max_attempts is not None:
            self._retry["max_attempts"] = max_attempts
        if backoff_base is not None:
            self._retry["backoff_base"] = backoff_base
        if stream_idle_timeout is not None:
            self._retry["stream_idle_timeout"] = stream_idle_timeout
        return self

    def debug_controller(self, controller: Any) -> KernelBuilder:
        """注入调试控制器(kernel/debug.py;缺省 None 时零开销、零行为变化)。"""
        self._debug_controller = controller
        return self

    def build(self) -> Kernel:
        """组装 Kernel(注入信号总线 / FrameStack / RunControl 等内核件)。

        装配边界:缺省补 ContextManager
        (M3:§7.2 注册表装配 spill/truncate/narrate/summarize 四段 + ProviderManager 注入
        + entry point ``agent_os.compressors`` 插件按 name 覆盖 + 状态注入
        + pre/post:compress 信号,§7);
        logic_kernels 按 TrustLevel 索引装配为 LogicKernelRouter(§9.2);
        sidecars(M4)装配 RunControlImpl + SidecarSupervisor 并注册到总线(§5);
        telemetry(M5a)作为总线特权订阅者接入(§5.1:全量订阅,不算 sidecar);
        blackboard(M5b)接线到 kernel.blackboard(§12:StatusBoard 与帧间消息);
        memory(M6)接线到 kernel.memory 并经 bind_memory 注入工具 registry
        (§11.2:memory_search/memory_write 数据源,bind 模式同 bind_skills);
        user_channel(M1,§8.3)经 bind_user_channel 注入工具 registry
        (system.user.ask/notify 的宿主回调);
        blob(M3)经 bind_blob 替换工具 registry 的 spill store(缺省进程内),
        并注入声明了 blob 槽位的 provider 适配器(WS1 多模态 parts 解析通道);
        supervisor(S1)有 handler 才装配 SupervisorManager 挂到 kernel.supervisor
        (docs/SUPERVISOR.md §2.3;仅预置策略字段时不装配,运行时按"未装配"报 not_found);
        human_approval(WS2,docs/SUPERVISOR.md §10):sidecar 列表中的 HumanApproval
        实例作策略载体传给 Kernel(EXEC 档工具过内核 tool-confirm 闸门),
        其 timeout/on_timeout 补缺 supervisor 通道策略(显式配置优先);
        distill sidecar(§11.2 写路径范式):bind providers/memory/stack 句柄,
        model 缺省回落 run.model;终态信号直挂总线(supervisor 的 ASYNC wrapper
        在 run 收尾 close 时等不到运行,见下方装配注释);
        debug_controller(P1)给了就把它挂到信号总线(直接订阅,见 kernel/debug.py);
        timer(WS1,§8.3 扩展):tools 提供 ``bind_timer`` 时把 kernel.ctl 绑进
        TimerService(system.timer.set 的 fire 注入通道;ctl 未装时补装
        RunControlImpl,同 debug_controller 先例);
        装配期权限闸门(§6.1):manifest 声明的工具必须在注册表中,缺失即拒绝加载。
        """
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
                    # ask_supervisor,docs/SUPERVISOR.md §2.1)
                    if not tools.has(t) and t not in (ORCHESTRATE_TOOL, ASK_SUPERVISOR_TOOL)
                }
            )
            if missing:
                raise SkillLoadError(f"manifest 声明的工具未注册(§6.1 权限闸门): {missing}")
            # 升权分档硬闸门(docs/ESCALATION.md §2.1/§3.4):推导档 ≥L2 禁 inline、
            # L3 禁 confirm: first。推导需要 Tool Registry,loader 单跑时不经过——
            # 装配是 tools 与 skills 同时在场的唯一加载期检查点
            for m in skills.manifests():
                validate_escalation_gates(m, derive_skill_tier(m, tools, skills))
        sup_manager = None
        # WS2(docs/SUPERVISOR.md §10):HumanApproval 已下沉为内核 tool-confirm 闸门,
        # sidecar 列表里的实例仅作策略载体——取出传给 Kernel(EXEC 档工具过闸),
        # 其 timeout/on_timeout 作为闸门共用 supervisor 通道的缺省(显式配置优先)
        human_approval = next(
            (s for s in self._sidecars if isinstance(s, HumanApproval)), None
        )
        if self._supervisor is not None and self._supervisor["handler"] is not None:
            # docs/SUPERVISOR.md §2.3:装配级 handler 通道(S2 宿主通道——Web 收件箱 /
            # CLI 协议——经 build_kernel(supervisor_handler=...) 走同一注入入口)
            sup_policy = {
                k: self._supervisor[k] for k in ("timeout_s", "on_timeout", "default_answer")
            }
            if human_approval is not None:
                if sup_policy["timeout_s"] is None:
                    sup_policy["timeout_s"] = human_approval.timeout
                if sup_policy["on_timeout"] is None:
                    # on_timeout 映射到 Manager 既有两档语义:"deny"→"fail"(超时
                    # 结构化错误,帧可降级);"allow"→"default_answer" 且兜底答案
                    # "approve-once"(超时视为批准本次)
                    if human_approval.on_timeout == "allow":
                        sup_policy["on_timeout"] = "default_answer"
                        if sup_policy["default_answer"] is None:
                            sup_policy["default_answer"] = "approve-once"
                    else:
                        sup_policy["on_timeout"] = "fail"
            sup_manager = SupervisorManager(
                self._supervisor["handler"],
                signals=bus,
                # None = 未显式配置,落 SupervisorManager 默认值(120s/fail/"")
                **{k: v for k, v in sup_policy.items() if v is not None},
            )
        if self._context is not None:
            # 嵌入方自装 ContextManager:注册表/providers 由嵌入方自负,不强塞
            context = self._context
        else:
            ctx_cfg = self._context_section or ContextSection()
            mem_cfg = self._memory_section or MemorySection()
            compressors: dict[str, Any] = {
                "spill": SpillCompressor(threshold_chars=ctx_cfg.spill_threshold_chars),
                "truncate": RollingWindowCompressor(),
                # narrate 与 summarize 同模型档(§7.2 同为廉价文本生成);
                # 无可用 providers 时 narrate 自动退化占位(不静默丢)
                "narrate": NarrateCompressor(
                    model=ctx_cfg.summarize_model or self.config.model or None,
                    breaker_threshold=ctx_cfg.summarize_breaker,
                    temperature=ctx_cfg.summarize_temperature,
                ),
                "summarize": SummarizeCompressor(
                    # 缺省跟主模型走;无可用 providers 时 summarize 自动退化 truncate(§7.2)
                    model=ctx_cfg.summarize_model or self.config.model or None,
                    breaker_threshold=ctx_cfg.summarize_breaker,
                    temperature=ctx_cfg.summarize_temperature,
                ),
            }
            # §7.5/§14.3:entry point 插件按 name 入注册表,允许覆盖内置
            compressors.update(_compressor_plugins())
            context = ContextManager.default(
                compressors=compressors,
                providers=providers,
                skills=skills,
                tools=tools,
                config=self.config,
                signals=bus,
                # S2(docs/SUPERVISOR.md §2.1):ask_supervisor 伪工具 schema 只在装了
                # supervisor 通道时呈现给 LLM;嵌入方自带 context manager 时自行决定
                supervisor=sup_manager is not None,
                # 经验参考段(manifest context_policy.recall opt-in):memory 缺省 None
                # = recall 帧冻结 None 快照;三键来自 [memory] 段(缺省全默认)
                memory=self._memory,
                recall_k=mem_cfg.recall_k,
                recall_entry_chars=mem_cfg.recall_entry_chars,
                recall_total_chars=mem_cfg.recall_total_chars,
            )
        if hasattr(tools, "bind_signals"):
            tools.bind_signals(bus)
        if skills is not None and hasattr(tools, "bind_skills"):
            tools.bind_skills(skills)  # §W1-5:system.skill.search 的技能数据源(bind 模式,同 system.python.exec)
        if self._memory is not None and hasattr(tools, "bind_memory"):
            tools.bind_memory(self._memory)  # M6 §11.2:system.memory.search/write 的数据源(bind 模式,同 bind_skills)
        if self._user_channel is not None and hasattr(tools, "bind_user_channel"):
            tools.bind_user_channel(self._user_channel)  # M1 §8.3:system.user.ask/notify 的宿主回调(bind 模式,同 bind_memory)
        if self._blob is not None and hasattr(tools, "bind_blob"):
            tools.bind_blob(self._blob)  # M3:spill 的 blob store(缺省 = 进程内 InMemoryBlobStore)
        if self._blob is not None:
            # WS1:多模态 parts 序列化的 blob 通道——ProviderManager 只是门面,
            # 直接挂到实际适配器实例(声明了 blob 槽位的才接;缺省 None = 占位降级)
            for provider in self._providers:
                if hasattr(provider, "blob"):
                    provider.blob = self._blob
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
            memory=self._memory,
            supervisor=sup_manager,
            stack=FrameStack(max_depth=self.config.max_depth),
            human_approval=human_approval,
        )
        if self._sidecars:
            # §5.2/§5.3:RunControl 是 sidecar 操控运行的唯一通道;supervisor 统一托管
            ctl = RunControlImpl(kernel)
            supervisor = SidecarSupervisor(bus, ctl)
            for sidecar in self._sidecars:
                if isinstance(sidecar, DistillSidecar):
                    # §11.2 蒸馏写路径范式:bind 子系统句柄;model 缺省回落 run.model
                    # (照 :310-315 summarize_model 回落先例);未配 [memory] 段时
                    # memory=None → 实例休眠(on_signal 不触发)
                    if sidecar.model is None:
                        sidecar.model = self.config.model or None
                    sidecar.bind(
                        providers=kernel.providers, memory=kernel.memory, stack=kernel.stack
                    )
                    # 终态信号(run.finished/run.aborted)emit 后 Kernel.run 的 finally
                    # 立即 supervisor.close() 取消 ASYNC wrapper——emit→close 无让出点,
                    # wrapper 从未运行就被回收(实测)——蒸馏触发故直挂总线(emit 内联
                    # await,保证执行);on_signal 只做触发判定 + create_task,按 run_id
                    # 幂等(_seen),wrapper 万一运行也不双触发
                    for pattern in sidecar.subscriptions:
                        bus.subscribe(pattern, _distill_trigger(sidecar, ctl))
                supervisor.register(sidecar)
            kernel.sidecars = supervisor
            kernel.ctl = ctl
        if self._debug_controller is not None:
            # 调试原语(P1):直接订阅总线(不经 SidecarSupervisor,绕开 SYNC 2s
            # 超时 fail-closed);订阅在 Telemetry/sidecar 之后,暂停前信号已落
            # trace/SSE。无 sidecar 时补装 RunControlImpl,供 inject_message 落地
            if kernel.ctl is None:
                kernel.ctl = RunControlImpl(kernel)
            self._debug_controller.attach(bus, kernel.ctl)
        if hasattr(tools, "bind_timer"):
            # WS1:system.timer.set 的 fire 注入通道——无 sidecar/debug 时 ctl 未装,
            # 补装 RunControlImpl(同 debug_controller 先例),保证计时到点能注入
            if kernel.ctl is None:
                kernel.ctl = RunControlImpl(kernel)
            tools.bind_timer(kernel.ctl)
        return kernel
