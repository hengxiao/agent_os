"""契约层冒烟测试(docs/DESIGN.md §14.1 冻结清单的物理形态)。

不依赖任何实现:只验证 api/v1 契约可导入、可默认实例化、冻结字段在位、
Protocol 全部 @runtime_checkable。
"""

from __future__ import annotations

import importlib

import pytest

CONTRACT_MODULES = [
    "agent_os.api.v1.messages",
    "agent_os.api.v1.frames",
    "agent_os.api.v1.providers",
    "agent_os.api.v1.tools",
    "agent_os.api.v1.skills",
    "agent_os.api.v1.sidecars",
    "agent_os.api.v1.signals",
    "agent_os.api.v1.context",
    "agent_os.api.v1.logic",
    "agent_os.api.v1.telemetry",
    "agent_os.api.v1.memory",
    "agent_os.api.v1.blackboard",
    "agent_os.api.v1.run",
    "agent_os.api.v1.control",
]

SKELETON_MODULES = [
    "agent_os.kernel",
    "agent_os.kernel.runner",
    "agent_os.kernel.stack",
    "agent_os.kernel.signals",
    "agent_os.kernel.control",
    "agent_os.kernel.run",
    "agent_os.context",
    "agent_os.context.manager",
    "agent_os.context.estimator",
    "agent_os.context.rolling_window",
    "agent_os.providers",
    "agent_os.providers.manager",
    "agent_os.providers.openai_compatible",
    "agent_os.providers.mock",
    "agent_os.tools",
    "agent_os.tools.local_registry",
    "agent_os.tools.builtins",
    "agent_os.tools.blob",
    "agent_os.skills",
    "agent_os.skills.manifest",
    "agent_os.skills.local_file",
    "agent_os.skills.loader",
    "agent_os.sidecars",
    "agent_os.sidecars.supervisor",
    "agent_os.sidecars.builtins",
    "agent_os.logic",
    "agent_os.logic.inprocess",
    "agent_os.logic.python_sandbox",
    "agent_os.logic.limits",
    "agent_os.telemetry",
    "agent_os.telemetry.sink",
    "agent_os.telemetry.jsonl_exporter",
    "agent_os.memory",
    "agent_os.memory.local_file",
    "agent_os.blackboard",
    "agent_os.blackboard.local",
    "agent_os.runtime",
    "agent_os.runtime.builder",
    "agent_os.runtime.config",
]


@pytest.mark.parametrize("mod", CONTRACT_MODULES + SKELETON_MODULES)
def test_importable(mod: str) -> None:
    importlib.import_module(mod)


def test_flat_reexport() -> None:
    from agent_os.api import v1
    from agent_os.api.v1 import memory as memory_mod

    for name in (
        "Message", "SkillFrame", "FrameContext", "Usage", "Provider", "ChatRequest",
        "Tool", "ToolSpec", "ToolContext", "ToolResult", "SkillManifest", "Sidecar",
        "Signal", "RunControl", "ContextManager", "Compressor", "LogicKernel",
        "TelemetrySink", "MemoryService", "Blackboard", "Envelope", "RunConfig",
        "MemoryPrincipal",
    ):
        assert hasattr(v1, name), f"agent_os.api.v1 缺少 re-export: {name}"
    # 同名消歧(M6):平铺命名空间的 Principal 是数据层形态(subject/issuer/attrs);
    # memory 形态(user/tenant)只经 MemoryPrincipal 别名到达
    assert v1.Principal is not v1.MemoryPrincipal
    assert v1.MemoryPrincipal is memory_mod.Principal


def test_default_instantiation() -> None:
    from agent_os.api.v1 import (
        Envelope,
        Message,
        RunConfig,
        SkillManifest,
        ToolSpec,
        Usage,
    )

    assert ToolSpec() is not None
    assert Usage() is not None
    assert Message() is not None
    assert RunConfig() is not None
    assert Envelope() is not None
    assert SkillManifest() is not None


def test_frozen_fields_present() -> None:
    """§14.1 v1 契约冻结清单逐项落地。"""
    from agent_os.api.v1 import (
        ChatChunk,
        Envelope,
        Message,
        Permission,
        RunConfig,
        SkillManifest,
        ToolContext,
        ToolError,
        ToolResult,
        ToolSpec,
        Usage,
        signals,
    )

    # ToolContext.principal 与 credentials(WS1:未声明凭证/未 bind 作用域时仍恒空)
    ctx = ToolContext()
    assert hasattr(ctx, "principal") and ctx.principal is None
    assert hasattr(ctx, "credentials") and ctx.credentials == {}

    # Message.reasoning 与 source
    msg = Message()
    assert hasattr(msg, "reasoning") and msg.reasoning is None
    assert hasattr(msg, "source")

    # Usage 细分字段
    usage = Usage()
    for f in ("steps", "prompt_tokens", "completion_tokens", "cache_read_tokens",
              "cache_write_tokens", "thinking_tokens", "cost", "ttft_ms", "total_ms"):
        assert hasattr(usage, f), f"Usage 缺字段: {f}"
    assert usage.cache_read_tokens == 0

    # 结构化 ToolResult(含 retryable)
    err = ToolError(retryable=True)
    assert hasattr(err, "retryable") and err.retryable is True
    assert hasattr(err, "hint")
    res = ToolResult(ok=False, error=err)
    assert res.error is not None and res.error.retryable is True

    # ToolSpec 全部预留字段
    spec = ToolSpec()
    for f in ("examples", "cacheable", "confirm", "concurrency_safe", "cost",
              "depends_on", "conflicts_with", "untrusted_source"):
        assert hasattr(spec, f), f"ToolSpec 缺预留字段: {f}"
    assert spec.confirm is False
    assert spec.concurrency_safe is False  # 默认否(fail-safe)
    assert spec.credentials == []  # WS1:缺省不声明凭证,dispatch 注入空 dict

    # 权限粒度递增
    assert Permission.READ < Permission.WRITE < Permission.NET < Permission.EXEC

    # manifest verifier 槽位
    manifest = SkillManifest()
    assert hasattr(manifest, "verifier") and manifest.verifier is None
    assert hasattr(manifest, "permissions") and hasattr(manifest.permissions, "blackboard")

    # pre:frame.pop 信号名(弹栈前同步可否决)
    assert signals.PRE_FRAME_POP == "pre:frame.pop"
    assert signals.POST_LLM_CHUNK == "post:llm.chunk"
    # ChatChunk 流式分片字段冻结(§4.1;WS2 消费侧载体)
    chunk = ChatChunk()
    assert chunk.delta is None and chunk.finish_reason is None and chunk.usage is None
    assert signals.BLACKBOARD_PUBLISH == "blackboard.publish"
    assert signals.BLACKBOARD_WRITE == "blackboard.write"
    # 运行期注册信号(§6.2;WS-C):pre 可否决 / post 观察;目录全集 33 → 35 → 36
    assert signals.PRE_SKILL_REGISTER == "pre:skill.register"
    assert signals.POST_SKILL_REGISTER == "post:skill.register"
    assert signals.PRE_SKILL_REGISTER in signals.SIGNAL_NAMES
    assert signals.POST_SKILL_REGISTER in signals.SIGNAL_NAMES
    # 经验参考段组装信号(additive):帧首次 build 快照时一次性发射
    assert signals.POST_CONTEXT_RECALL == "post:context.recall"
    assert signals.POST_CONTEXT_RECALL in signals.SIGNAL_NAMES
    assert len(signals.SIGNAL_NAMES) == 36

    # compression: "off" 消融档存在(默认值是 hierarchical,档位语义见 §7.1)
    assert RunConfig(compression="off").compression == "off"
    # stream 流式消费开关(WS2 additive;缺省开,caps 不支持自动回落 chat)
    assert RunConfig().stream is True

    # Envelope 冻结
    env = Envelope()
    assert env.target == "*" and hasattr(env, "ts")


def test_protocols_runtime_checkable() -> None:
    from agent_os.api.v1 import (
        Blackboard,
        Compressor,
        ContextManager,
        Exporter,
        LogicKernel,
        MemoryService,
        ModelRouter,
        Provider,
        RunControl,
        Sidecar,
        SkillRegistry,
        TelemetrySink,
        Tool,
    )

    for proto in (Tool, Provider, ModelRouter, SkillRegistry, Sidecar, RunControl,
                  ContextManager, Compressor, LogicKernel, TelemetrySink, Exporter,
                  MemoryService, Blackboard):
        assert hasattr(proto, "_is_runtime_protocol") or proto.__dict__.get("_is_runtime_protocol"), \
            f"{proto.__name__} 未标记 @runtime_checkable"


class _FakeTool:
    spec = None  # runtime isinstance 检查数据成员存在性

    async def __call__(self, args, ctx): ...


class _FakeProvider:
    name = "fake"

    def capabilities(self): ...

    async def chat(self, req): ...

    async def stream(self, req): ...


def test_fake_structural_conformance() -> None:
    from agent_os.api.v1 import Provider, Tool

    assert isinstance(_FakeTool(), Tool)
    assert isinstance(_FakeProvider(), Provider)


def test_full_chain_builds_with_memory(tmp_path) -> None:
    """链式组装返回 builder 自身;build() 装配成功(M6 起 memory 接线,不再报里程碑号)。"""
    from agent_os.api.v1 import RunConfig
    from agent_os.blackboard import LocalBlackboard
    from agent_os.logic import InProcessLogicKernel, PythonSandboxLogicKernel
    from agent_os.memory import LocalFileMemoryService
    from agent_os.providers import MockProvider
    from agent_os.runtime import KernelBuilder
    from agent_os.sidecars import BudgetGuard, LoopDetector
    from agent_os.telemetry import JsonlTelemetrySink

    memory = LocalFileMemoryService(str(tmp_path / "memory"))
    builder = (
        KernelBuilder(RunConfig())
        .providers(MockProvider())
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .sidecars(BudgetGuard(max_cost=2.0), LoopDetector())
        .telemetry(JsonlTelemetrySink(str(tmp_path / "traces")))
        .memory(memory)
        .blackboard(LocalBlackboard())
    )
    kernel = builder.build()
    assert kernel.memory is memory, "memory(M6)应接线到 kernel.memory"
    assert kernel.tools._memory is memory, "memory 应经 bind_memory 注入工具 registry(§11.2)"
