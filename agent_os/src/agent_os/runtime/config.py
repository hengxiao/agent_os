"""配置文件加载(RUNNERS.md §2.1;DESIGN.md §14.2 的 TOML 形态;R1)。

``agent-os.toml`` 等价完成 KernelBuilder 链式组装,供 CLI/Web 两个薄宿主共用::

    [run]        → RunConfig 各字段(model/max_depth/max_steps/max_cost/
                   max_wall_time/compression/seed/temperature,§2.4;
                   workdir/read_paths §W0-1 工作目录分区)
    [providers.*]→ kimi/anthropic/openai(兼容端点)/mock(dotted path 应答函数)
    [tools]      → builtins 内置工具;python_exec = docker|subprocess|off;
                   python_orchestrate = true|false(编排伪工具,缺省 false)
    [tools.custom] → module = "pkg.mod:func":宿主自定义工具注册钩子,
                   importlib 加载后调用 ``func(registry)``(加载/注册失败抛 ConfigError);
                   声明即授权,RunConfig 权限上限同步提到 EXEC(同 python_exec)
    [skills]     → LocalFileSkillRegistry
    [sidecars]   → BudgetGuard / LoopDetector / tool_guard_rules → ToolGuard(缺省不加)
    [telemetry]  → JsonlTelemetrySink(目录自动创建)
    [retry]      → ProviderManager 的 max_attempts / backoff_base

两个错误归类的锚点:配置文件缺失/畸形/provider 装配失败抛 :class:`ConfigError`
(宿主归退出码 4);技能清单/权限闸门问题由 KernelBuilder 抛 SkillLoadError(归 2)。
"""

from __future__ import annotations

import importlib
import logging
import os
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from agent_os.api.v1 import Permission, RunConfig, ToolPolicy
from agent_os.logic import (
    DockerPythonSandboxLogicKernel,
    DockerUnavailableError,
    InProcessLogicKernel,
    PythonSandboxLogicKernel,
)
from agent_os.providers.claude import ClaudeProvider
from agent_os.providers.kimi import KimiProvider
from agent_os.providers.mock import MockProvider
from agent_os.providers.openai_compatible import OpenAICompatibleProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.sidecars.builtins import BudgetGuard, LoopDetector, ToolGuard
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.telemetry.jsonl_exporter import JsonlTelemetrySink
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry

_log = logging.getLogger("agent_os.runtime.config")

#: ``[run]`` 支持的字段(逐字对齐 §2.4 RunConfig 标量字段;workdir/read_paths 见 §W0-1)
_RUN_FIELDS = (
    "model",
    "max_depth",
    "max_steps",
    "max_cost",
    "max_wall_time",
    "compression",
    "inline",
    "seed",
    "temperature",
    "workdir",
    "read_paths",
)


class ConfigError(RuntimeError):
    """配置缺失/畸形/装配失败(RUNNERS.md §3.3 退出码 4:宿主/基础设施错误)。"""


def load_config(path: str | Path) -> dict[str, Any]:
    """TOML → 原始配置 dict;文件缺失/畸形抛 :class:`ConfigError`。"""
    target = Path(path)
    if not target.is_file():
        raise ConfigError(f"配置文件不存在: {target}")
    try:
        with target.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"配置文件 TOML 畸形: {target}: {e}") from e


def load_skillsets(root: str | Path) -> dict[str, Path]:
    """一站多 skill set(``--skillsets <root>``/``[skillsets] dir``):扫描 set 目录。

    模型:``<root>/<set>/skills.yaml``(必须)+ 可选 ``agent-os.toml``(set 级装配,
    继承全局)+ 可选 py 模块(装配时注入 sys.path)。返回 ``{set 名: set 目录}``
    (按名字排序;root 缺失/非目录抛 :class:`ConfigError`)。
    """
    base = Path(root)
    if not base.is_dir():
        raise ConfigError(f"skillsets 目录不存在: {base}")
    return {
        p.name: p
        for p in sorted(base.iterdir(), key=lambda p: p.name)
        if p.is_dir() and (p / "skills.yaml").is_file()
    }


def _run_config(cfg: dict[str, Any]) -> RunConfig:
    unknown = sorted(set(cfg) - set(_RUN_FIELDS))
    if unknown:
        raise ConfigError(f"[run] 含未知字段: {unknown}(支持: {list(_RUN_FIELDS)})")
    return RunConfig(**{k: cfg[k] for k in _RUN_FIELDS if k in cfg})


def _load_dotted(dotted: str) -> Callable[..., Any]:
    """``"pkg.mod:func"`` dotted path → callable(mock brain 等,§2.1)。"""
    module, _, entry = dotted.partition(":")
    if not module or not entry:
        raise ConfigError(f"dotted path 应为 'pkg.mod:func' 形式,得到: {dotted!r}")
    try:
        func = getattr(importlib.import_module(module), entry)
    except (ImportError, AttributeError) as e:
        raise ConfigError(f"无法加载 dotted path {dotted!r}: {e}") from e
    if not callable(func):
        raise ConfigError(f"dotted path {dotted!r} 指向的对象不可调用")
    return func


def _providers(cfg: dict[str, Any]) -> list[Any]:
    providers: list[Any] = []
    unknown = sorted(set(cfg) - {"kimi", "anthropic", "openai", "mock"})
    if unknown:
        raise ConfigError(f"[providers] 含未知 provider: {unknown}")
    if "kimi" in cfg:
        providers.append(KimiProvider())
    if "anthropic" in cfg:
        providers.append(ClaudeProvider())
    if "openai" in cfg:
        oc = cfg["openai"] or {}
        api_key = os.environ.get(oc.get("api_key_env", "")) if oc.get("api_key_env") else None
        providers.append(
            OpenAICompatibleProvider(
                base_url=oc.get("base_url", "https://api.openai.com/v1"),
                api_key=api_key,
            )
        )
    if "mock" in cfg:
        brain = _load_dotted((cfg["mock"] or {}).get("brain", ""))
        providers.append(MockProvider(brain))
    return providers


def _sandbox_kernel(mode: str) -> Any | None:
    """``tools.python_exec`` 后端(§2.1);docker 不可用回退 subprocess 并记 warning。"""
    if mode == "docker":
        try:
            return DockerPythonSandboxLogicKernel()
        except DockerUnavailableError as e:
            _log.warning("docker 不可用,python_exec 回退 subprocess 沙箱: %s", e)
            return PythonSandboxLogicKernel()
    if mode == "subprocess":
        return PythonSandboxLogicKernel()
    if mode == "off":
        return None
    raise ConfigError(f"未知的 tools.python_exec 后端: {mode!r}(docker|subprocess|off)")


def _sidecars(cfg: dict[str, Any]) -> list[Any]:
    sidecars: list[Any] = []
    unknown = sorted(set(cfg) - {"budget_guard", "loop_detector", "tool_guard_rules"})
    if unknown:
        raise ConfigError(f"[sidecars] 含未知 sidecar: {unknown}")
    if "budget_guard" in cfg:
        bg = cfg["budget_guard"] or {}
        sidecars.append(
            BudgetGuard(
                max_cost=bg.get("max_cost"),
                max_steps=bg.get("max_steps"),
                max_wall_time=bg.get("max_wall_time"),
            )
        )
    if "loop_detector" in cfg:
        ld = cfg["loop_detector"] or {}
        sidecars.append(
            LoopDetector(
                threshold=ld.get("threshold", 3),
                max_strikes=ld.get("max_strikes", 2),
            )
        )
    if "tool_guard_rules" in cfg:
        rules: list[tuple[str, str, str]] = []
        for rule in cfg["tool_guard_rules"] or []:
            if len(rule) != 3:
                raise ConfigError(
                    f"[sidecars] tool_guard_rules 每项应为 [tool, pattern, reason],得到: {rule!r}"
                )
            rules.append((str(rule[0]), str(rule[1]), str(rule[2])))
        sidecars.append(ToolGuard(rules=rules))
    return sidecars


def build_kernel(config: str | Path | dict[str, Any], *, extra_sidecars: Iterable[Any] = ()) -> Any:
    """按 RUNNERS.md §2.1 把 ``agent-os.toml``(或等价 dict)装配为 Kernel。

    缺省:无 ``[run]`` 用 RunConfig 默认;无 ``[providers]`` → 空 Manager
    (运行时才报"provider 前缀未注册");无 ``[skills]``/``[telemetry]``/``[sidecars]``
    对应子系统不接线。启用 ``python_exec`` 即把 RunConfig 全局权限上限提到 EXEC
    (§8.2:工具自报 EXEC 级,不提上限必被分发层拒绝,配置即授权)。

    ``extra_sidecars``:配置文件之外由宿主追加的 sidecar(Web runner 的 stop
    通道占位 sidecar;M4 起仅有 sidecar 时 builder 才装配 ``kernel.ctl``)。
    """
    cfg = load_config(config) if isinstance(config, (str, Path)) else dict(config)
    run_cfg = _run_config(cfg.get("run") or {})

    tools_cfg = cfg.get("tools") or {}
    registry = (
        LocalPythonToolRegistry.with_builtins()
        if tools_cfg.get("builtins", False)
        else LocalPythonToolRegistry()
    )
    if tools_cfg.get("python_orchestrate", False):
        # 编排伪工具(CODE-ORCHESTRATION.md):显式开启;声明即授权,权限上限提到 EXEC
        run_cfg.orchestrate = True
        if run_cfg.tool_policy.max_permission < Permission.EXEC:
            run_cfg.tool_policy = ToolPolicy(max_permission=Permission.EXEC)
    sandbox = _sandbox_kernel(tools_cfg.get("python_exec", "off"))
    logic: list[Any] = [InProcessLogicKernel()]
    if sandbox is not None:
        registry.register(python_exec_tool(sandbox))
        logic.append(sandbox)
        if run_cfg.tool_policy.max_permission < Permission.EXEC:
            run_cfg.tool_policy = ToolPolicy(max_permission=Permission.EXEC)

    custom = tools_cfg.get("custom") or {}
    if custom:
        register_fn = _load_dotted(str(custom.get("module", "")))
        try:
            register_fn(registry)
        except Exception as e:  # 宿主工具注册失败归配置装配错误(退出码 4)
            raise ConfigError(f"[tools.custom] 注册钩子执行失败: {e}") from e
        if run_cfg.tool_policy.max_permission < Permission.EXEC:
            # 与 python_exec 同理(§8.2 配置即授权):宿主显式装配自定义工具,
            # 工具自报等级可能达 EXEC,不提上限必被分发层拒绝
            run_cfg.tool_policy = ToolPolicy(max_permission=Permission.EXEC)

    builder = KernelBuilder(run_cfg).tools(registry).logic_kernels(*logic)
    providers = _providers(cfg.get("providers") or {})
    if providers:
        builder.providers(*providers)
    skills_path = (cfg.get("skills") or {}).get("path")
    if skills_path:
        builder.skills(LocalFileSkillRegistry(skills_path))
    sidecars = [*_sidecars(cfg.get("sidecars") or {}), *extra_sidecars]
    if sidecars:
        builder.sidecars(*sidecars)
    telemetry_dir = (cfg.get("telemetry") or {}).get("dir")
    if telemetry_dir:
        builder.telemetry(JsonlTelemetrySink(telemetry_dir))
    retry = cfg.get("retry") or {}
    if retry:
        builder.retry(
            max_attempts=retry.get("max_attempts"),
            backoff_base=retry.get("backoff_base"),
        )
    return builder.build()
