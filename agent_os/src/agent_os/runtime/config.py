"""配置文件加载(docs/RUNNERS.md §2.1;docs/DESIGN.md §14.2 的 TOML 形态;R1)。

``agent-os.toml`` 等价完成 KernelBuilder 链式组装,供 CLI/Web 两个薄宿主共用::

    [run]        → RunConfig 各字段(model/max_depth/max_steps/max_cost/
                   max_wall_time/compression/seed/temperature,§2.4;
                   workdir/read_paths §W0-1 工作目录分区;
                   checkpoint_interval Debugger P5 周期 checkpoint,0=关)
    [providers.*]→ kimi/anthropic/openai(兼容端点)/mock(dotted path 应答函数);
                   [providers] router = "pkg.mod:Class"(§4.2 ModelRouter 扩展点,
                   无参实例化,加载/实例化失败 ConfigError;缺省 DefaultModelRouter:
                   静态 prefer 链 + caps 探测,fail-open)
    [tools]      → builtins 内置工具;python_exec = docker|subprocess|off;
                   python_orchestrate = true|false(编排伪工具,缺省 false)
    [tools.custom] → module = "pkg.mod:func":宿主自定义工具注册钩子,
                   importlib 加载后调用 ``func(registry)``(加载/注册失败抛 ConfigError);
                   声明即授权,RunConfig 权限上限同步提到 EXEC(同 system.python.exec)
    [mcp.servers.*] → MCP server 工具面(tools/mcp.py stdio + tools/mcp_http.py
                   Streamable HTTP;DESIGN §8.3 供应链清单):command(stdio)与
                   url(http)恰居其一,transport = auto(缺省按键判)|stdio|http;
                   headers(http 请求头)与 env 同规则(字面量或 {env = "VAR"} 间接);
                   protocol_version 缺省随传输(stdio 2024-11-05 / http 2025-03-26)。
                   eager 装配——装配期拉起连接 initialize 握手 +
                   tools/list 并注册全部工具(mcp.<server>.<tool> 命名空间,走全量
                   dispatch 管线),连接失败 ConfigError 快速失败(防坏 server 静默
                   缺席致白名单形同虚设);段存在才接线,缺段零破坏
    [skills]     → LocalFileSkillRegistry:path 技能源(单文件/目录/列表);
                   watch_interval = 秒数(缺省 0 = 不看):>0 时装配轮询热重载
                   看门狗(daemon 线程,reload 失败保留旧表记 log);
                   register_smoke = "default"(默认重放验证门:草稿
                   drafts/<name>/tests/*.json 用例 + expected 深比较 / expect
                   LLM 裁判,见 skills/register_smoke.py;草稿根取
                   [lab].drafts_root,缺省 skills 路径同级 drafts/)或
                   "pkg.mod:func" 自定义回调(加载失败 ConfigError,同
                   [tools.custom] 先例;缺省不跑);
                   register_judge_model = 默认验证门 expect 判定的裁判模型
                   (缺省跟 run.model)
    [sidecars]   → BudgetGuard / LoopDetector / tool_guard_rules → ToolGuard(缺省不加);
                   human_approval(WS2)= true 或 { timeout, on_timeout }:
                   装配 HumanApproval 策略,EXEC 档工具过内核 tool-confirm 闸门;
                   distill(docs/DESIGN.md §11.2 写路径范式)= true 或 { model?,
                   min_tool_calls?, temperature?, breaker_threshold?,
                   max_transcript_chars?, verify? }:run 终态后廉价模型蒸馏经验写入 [memory]
                   (缺省关闭;无 [memory] 段时装配出来也是休眠实例;verify = 入库前
                   内容审查档,缺省 true)
    [supervisor] → timeout_s / on_timeout / default_answer(docs/SUPERVISOR.md §6;TOML
                   写不了可调用 handler——此处只加载策略字段,handler 由宿主经
                   build_kernel(supervisor_handler=...) 注入,S2:Web 收件箱
                   默认通道 / CLI stderr 协议)
    [telemetry]  → JsonlTelemetrySink(目录自动创建);redact = true 开 PII 脱敏 hook
                   (§10.2,默认关闭,regex 快筛,开启后 WAL 不再逐字保真);
                   [telemetry.otlp] 子表接 OTLP exporter(endpoint 必填;
                   headers 支持 {env = "VAR"} 间接;batch_max/flush_interval/
                   queue_max/timeout 调参;best-effort:失败丢批不重试)
    [memory]     → M6 记忆子系统(docs/DESIGN.md §11.2):dir = "./memory"
                   (LocalFileMemoryService 根目录);段存在才接线,缺段完全不 bind
                   (同 [credentials] 先例;接线后 system.memory.search/write 可用)
    [blob]       → M3 spill 文件存储(§8.4/§7.2):dir = "./blobs"(FileBlobStore
                   根目录);段存在才接线,缺段 = 进程内 InMemoryBlobStore(零破坏)
    [context]    → §7.2 压缩链调参(WS2):summarize_model(摘要模型,缺省跟 run.model)/
                   spill_threshold_chars(spill 触发字符阈值,默认 4000)/
                   summarize_breaker(摘要连败熔断次数,默认 3)/
                   summarize_temperature(摘要采样温度,默认 0.2);缺段 = 全默认
    [retry]      → ProviderManager 的 max_attempts / backoff_base / stream_idle_timeout
                   (流式 idle 看门狗秒数,缺段保持 Manager 默认 30s)
    [prices]     → 每模型每百万 token 单价({input, output, cache_read?});
                   缺它则 usage.cost 恒 0,max_cost/BudgetGuard 不会触发(装配期告警)
    [credentials]→ WS1 凭证作用域:{凭证名 = {env = "VAR_NAME"}},只存 env 变量名
                   不落盘明文;段存在才 bind 到工具 registry(dispatch 按工具声明注入)
    [data]       → D2 数据层 authZ(docs/DATA-AUTHZ.md §3):domains = [{name,
                   sensitivity?(缺省 confidential), path_prefix|url_prefix 恰一}];
                   [data.principals."<subject>"] domains = [glob 域名] 白名单。
                   段存在才 bind_data_policy + 注册域边界;缺席完全不 bind
                   (D1"未配置不拦截"语义逐字不动)
    [web.tokens] → D3-lite 多用户映射:{ "<token>" = "user:<login>" }(host/web
                   Bearer 门命中映射 → 逐用户 Principal;解析见 web_token_map)
    [events]     → E4 事件批处理(``POST /api/events`` 在跑通道):batch(在跑事件先入
                   根帧队列、下一步批头并入,缺省 true;false = 立即注入)/batch_max
                   (单批条数上限,默认 50,超出丢最旧)/event_text_max(事件文本
                   截断,默认 2000);缺段 = 全默认

两个错误归类的锚点:配置文件缺失/畸形/provider 装配失败抛 :class:`ConfigError`
(宿主归退出码 4);技能清单/权限闸门问题由 KernelBuilder 抛 SkillLoadError(归 2)。
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import tomllib
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from agent_os.api.v1 import (
    CONFIDENTIAL,
    DataDomain,
    DataPolicy,
    Permission,
    RunConfig,
    ToolPolicy,
)
from agent_os.logic import (
    DockerPythonSandboxLogicKernel,
    DockerUnavailableError,
    InProcessLogicKernel,
    PythonSandboxLogicKernel,
)
from agent_os.memory.local_file import LocalFileMemoryService
from agent_os.providers.claude import ClaudeProvider
from agent_os.providers.kimi import KimiProvider
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider
from agent_os.providers.openai_compatible import OpenAICompatibleProvider
from agent_os.runtime.builder import (
    ContextSection,
    EventsSection,
    KernelBuilder,
    MemorySection,
)
from agent_os.sidecars.builtins import (
    BudgetGuard,
    DistillSidecar,
    HumanApproval,
    LoopDetector,
    ToolGuard,
)
from agent_os.skills.draft_store import DraftStore
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.telemetry.jsonl_exporter import JsonlTelemetrySink
from agent_os.telemetry.otlp_exporter import OtlpExporter
from agent_os.tools.blob import FileBlobStore
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry
from agent_os.tools.mcp import McpError, McpServerSpec, connect_and_register_sync

_log = logging.getLogger("agent_os.runtime.config")

#: ``[run]`` 支持的字段(逐字对齐 §2.4 RunConfig 标量字段;workdir/read_paths 见 §W0-1;
#: stream = WS2 流式消费开关,缺省开,caps 不支持自动回落 chat)
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
    "checkpoint_interval",
    "stream",
)


class ConfigError(RuntimeError):
    """配置缺失/畸形/装配失败(docs/RUNNERS.md §3.3 退出码 4:宿主/基础设施错误)。"""


#: ``[context]`` 支持的字段(§7.2 压缩链调参,WS2;缺段 = 全默认,见 ContextSection)
_CONTEXT_FIELDS = (
    "summarize_model",
    "spill_threshold_chars",
    "summarize_breaker",
    "summarize_temperature",
)

#: ``[memory]`` 支持的字段(§11;dir = 存储接线,recall_* = 经验参考段调参,缺键 = 全默认)
_MEMORY_FIELDS = ("dir", "recall_k", "recall_entry_chars", "recall_total_chars")

#: ``[events]`` 支持的字段(E4 事件批处理;缺段 = 全默认,见 EventsSection)
_EVENTS_FIELDS = ("batch", "batch_max", "event_text_max")


class CredentialScope:
    """凭证作用域(WS1):``[credentials]`` 段解析产物,凭证名 → env 变量名。

    不落盘明文:配置只存 env 变量名;动态解析——每次调用现读 ``os.environ``,
    不做装配期快照(15 分钟 OAuth token 续期后下一调用即生效,同
    providers/openai_compatible.py 动态 key 先例)。可作 resolver 直接传给
    ``LocalPythonToolRegistry.bind_credentials``。
    """

    def __init__(self, table: dict[str, str]) -> None:
        self._table = dict(table)

    def __call__(self, principal: Any, declared_keys: Iterable[str]) -> dict[str, str]:
        """按工具声明键现读 env,返回 ``{凭证名: 值}``。

        ``principal`` 为 D3 per-principal 凭证留的协议面,v1 忽略。
        作用域未配置的声明键、或 env 变量缺席 → 该键不出现(工具按
        ``ctx.credentials.get(key)`` 判缺凭证;泄露纪律:错误消息只带键名,不回显值)。
        """
        out: dict[str, str] = {}
        for key in declared_keys:
            env = self._table.get(key)
            if env is None:
                continue  # 作用域未配置该键
            value = os.environ.get(env)
            if value is not None:
                out[key] = value
        return out


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

def _skills_section(cfg: Any) -> dict[str, Any]:
    """``[skills]`` 段 strict 校验(同 ``_memory_section``/``_retry`` 先例:拼错的键

    会静默落空,必须装配期报出)。支持键:path(技能源)/ watch_interval(轮询
    热重载看门狗间隔秒数,缺省 0 = 不看;须 >= 0 的数值)/ register_smoke
    (register() 验证门冒烟回调:``"default"`` 哨兵 = 装配 skills/register_smoke.py
    的默认重放实现;否则须为 "pkg.mod:func" 字符串,加载在 build_kernel 装配点做,
    同 [tools.custom] ``_load_dotted`` 先例)/ register_judge_model(默认实现
    expect 判定的裁判模型,None 或字符串,缺省跟 run.model)。
    """
    if not isinstance(cfg, dict):
        raise ConfigError(f"[skills] 段应为表,得到: {cfg!r}")
    known = {"path", "watch_interval", "register_smoke", "register_judge_model"}
    unknown = sorted(set(cfg) - known)
    if unknown:
        raise ConfigError(
            f"[skills] 含未知字段: {unknown}"
            f"(支持: ['path', 'register_judge_model', 'register_smoke', 'watch_interval'])"
        )
    section = dict(cfg)
    watch = section.get("watch_interval", 0)
    if isinstance(watch, bool) or not isinstance(watch, (int, float)) or watch < 0:
        raise ConfigError(f"[skills] watch_interval 须为 >= 0 的数值(0 = 不看),得到: {watch!r}")
    section["watch_interval"] = float(watch)
    smoke = section.get("register_smoke")
    if smoke is not None and (
        not isinstance(smoke, str) or (smoke != "default" and ":" not in smoke)
    ):
        raise ConfigError(
            f"[skills] register_smoke 须为 'default' 或 'pkg.mod:func' 字符串,得到: {smoke!r}"
        )
    judge_model = section.get("register_judge_model")
    if judge_model is not None and not isinstance(judge_model, str):
        raise ConfigError(f"[skills] register_judge_model 须为字符串,得到: {judge_model!r}")
    return section


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
    unknown = sorted(set(cfg) - {"kimi", "anthropic", "openai", "mock", "router"})
    if unknown:
        raise ConfigError(f"[providers] 含未知 provider: {unknown}")
    if "kimi" in cfg:
        kc = cfg["kimi"] or {}
        unknown = sorted(set(kc) - {"base_url", "api_key"})
        if unknown:
            raise ConfigError(f"[providers.kimi] 含未知字段: {unknown}(支持: ['api_key', 'base_url'])")
        providers.append(
            # api_key 缺省不传:保持 KimiProvider 的动态 env 解析(token_refresh 续期即生效),
            # 显式给才钉死(见 kimi.py 类注)
            KimiProvider(**{k: kc[k] for k in ("base_url", "api_key") if k in kc})
        )
    if "anthropic" in cfg:
        ac = cfg["anthropic"] or {}
        known = {"api_key", "base_url", "default_max_tokens", "anthropic_version"}
        unknown = sorted(set(ac) - known)
        if unknown:
            raise ConfigError(f"[providers.anthropic] 含未知字段: {unknown}(支持: {sorted(known)})")
        providers.append(ClaudeProvider(**{k: ac[k] for k in known if k in ac}))
    if "openai" in cfg:
        oc = cfg["openai"] or {}
        # api_key_env 交给 provider 动态解析(每次调用现读;token_refresh 续期即生效),
        # 不在装配期快照——15 分钟 OAuth token 会过期(2026-08-24 实证)
        providers.append(
            OpenAICompatibleProvider(
                base_url=oc.get("base_url", "https://api.openai.com/v1"),
                api_key_env=oc.get("api_key_env") or None,
            )
        )
    if "mock" in cfg:
        brain = _load_dotted((cfg["mock"] or {}).get("brain", ""))
        providers.append(MockProvider(brain))
    return providers


def _providers_router(cfg: dict[str, Any]) -> Any | None:
    """``[providers] router = "pkg.mod:Class"`` → 自定义 ModelRouter 实例(§4.2 扩展点)。

    dotted path 加载后**无参实例化**(同 ``[tools.custom]`` 的 ``_load_dotted`` 先例,
    但目标是类不是注册钩子);缺键 → None(装配层落 DefaultModelRouter);
    非字符串/加载失败/实例化失败 → ConfigError——router 拼错会静默落默认实现,
    宁可装配期炸掉(strict,同 ``_prices``/``_credentials`` 先例)。
    """
    dotted = cfg.get("router")
    if dotted is None:
        return None
    if not isinstance(dotted, str) or not dotted:
        raise ConfigError(f"[providers] router 须为 'pkg.mod:Class' 字符串,得到: {dotted!r}")
    cls = _load_dotted(dotted)
    try:
        return cls()
    except Exception as e:  # 实例化失败归配置装配错误(退出码 4)
        raise ConfigError(f"[providers] router {dotted!r} 无参实例化失败: {e}") from e


def _sandbox_kernel(mode: str) -> Any | None:
    """``tools.python_exec`` 后端(§2.1);docker 不可用回退 subprocess 并记 warning。

    注意:配置键仍为 ``python_exec``,但注册到工具表时 canonical 名为 ``system.python.exec``。"""
    if mode == "docker":
        try:
            return DockerPythonSandboxLogicKernel()
        except DockerUnavailableError as e:
            _log.warning("docker 不可用,system.python.exec 回退 subprocess 沙箱: %s", e)
            return PythonSandboxLogicKernel()
    if mode == "subprocess":
        return PythonSandboxLogicKernel()
    if mode == "off":
        return None
    raise ConfigError(f"未知的 tools.python_exec 后端: {mode!r}(docker|subprocess|off);对应工具 canonical 名为 system.python.exec")


def _prices(cfg: dict[str, Any]) -> dict[str, dict[str, float]]:
    """``[prices]`` → ``{model_or_prefix: {input, output, ...}}``(每百万 token 美元)。

    键可为完整 model 串(``"openai/kimi-k2.7"``)或 provider 前缀(``"openai"``),
    精确匹配优先。值里的未知字段直接报错——单价拼错会静默失去成本护栏。
    """
    known = {"input", "output", "cache_read", "cache_write"}
    table: dict[str, dict[str, float]] = {}
    for model, raw in cfg.items():
        if not isinstance(raw, dict):
            raise ConfigError(f"[prices] {model!r} 应为表(如 {{ input = 3.0, output = 15.0 }})")
        unknown = sorted(set(raw) - known)
        if unknown:
            raise ConfigError(f"[prices] {model!r} 含未知字段: {unknown}(支持: {sorted(known)})")
        try:
            table[model] = {k: float(v) for k, v in raw.items()}
        except (TypeError, ValueError) as e:
            raise ConfigError(f"[prices] {model!r} 的单价须为数字: {e}") from None
    return table


def _credentials(cfg: dict[str, Any]) -> CredentialScope:
    """``[credentials]`` → :class:`CredentialScope`(凭证名 → env 变量名;不落盘明文)。

    值必须是 ``{ env = "VAR_NAME" }`` 形态;非法形态/未知键直接报错——
    凭证名拼错会静默拿不到注入,宁可装配期炸掉(同 ``_prices`` 严格先例)。
    """
    table: dict[str, str] = {}
    for name, raw in cfg.items():
        if not isinstance(raw, dict):
            raise ConfigError(f"[credentials] {name!r} 应为表(如 {{ env = \"GITHUB_TOKEN\" }})")
        unknown = sorted(set(raw) - {"env"})
        if unknown:
            raise ConfigError(f"[credentials] {name!r} 含未知字段: {unknown}(支持: ['env'])")
        env = raw.get("env")
        if not isinstance(env, str) or not env:
            raise ConfigError(f"[credentials] {name!r} 的 env 须为非空字符串(env 变量名,不落盘明文)")
        table[name] = env
    return CredentialScope(table)


#: ``[mcp.servers.<name>]`` 支持的字段(tools/mcp.py McpServerSpec 逐字对齐)
_MCP_SERVER_FIELDS = (
    "command",
    "connect_timeout",
    "confirm",
    "env",
    "headers",
    "permission",
    "protocol_version",
    "timeout",
    "transport",
    "url",
)

#: transport 合法值(auto = 按键自动判:url → http,command → stdio)
_MCP_TRANSPORTS = ("auto", "stdio", "http")

#: permission 字符串 → Permission(缺省 read,最小授权;升档须显式配置)
_MCP_PERMISSIONS = {
    "read": Permission.READ,
    "write": Permission.WRITE,
    "net": Permission.NET,
    "exec": Permission.EXEC,
}

#: server 名合法字符(进工具命名空间 ``mcp.<server>.<tool>``;同 TOML 裸键字符集)
_MCP_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")


def _mcp_servers(cfg: dict[str, Any]) -> list[McpServerSpec]:
    """``[mcp.servers.<name>]`` 段 → :class:`McpServerSpec` 列表(DESIGN §8.3 供应链清单)。

    严格校验(同 ``_credentials``/``_prices`` 先例——server 名/权限拼错会静默
    缺席或错档,宁可装配期炸掉):command(stdio)与 url(Streamable HTTP)
    **恰居其一**(都缺/都给 → ConfigError);command 非空 list[str];url 必须
    http(s)://;transport ∈ auto|stdio|http,显式给与键不符(transport="http"
    但无 url 等)→ ConfigError;env/headers 值为 str 字面量或 ``{env = "VAR"}``
    间接引用(连接时现读 os.environ,不落盘明文);headers 仅 http 传输有意义,
    stdio server 配了直接拒(防静默忽略);permission ∈ read|write|net|exec
    (缺省 read);timeout/connect_timeout 正数;confirm 布尔;protocol_version
    非空 str(缺省随传输);server 名只许 ``[A-Za-z0-9_-]``。
    """
    unknown = sorted(set(cfg) - {"servers"})
    if unknown:
        raise ConfigError(f"[mcp] 含未知字段: {unknown}(支持: ['servers'])")
    servers = cfg.get("servers") or {}
    if not isinstance(servers, dict):
        raise ConfigError(f"[mcp.servers] 应为表(<name> = {{ command = [...] }}),得到: {servers!r}")
    specs: list[McpServerSpec] = []
    for name, raw in servers.items():
        if not _MCP_NAME_RE.fullmatch(str(name)):
            raise ConfigError(
                f"[mcp.servers] server 名 {name!r} 非法"
                f"(只许 [A-Za-z0-9_-];进工具命名空间 mcp.<server>.<tool>)"
            )
        if not isinstance(raw, dict):
            raise ConfigError(f'[mcp.servers."{name}"] 应为表(如 {{ command = [...] }}),得到: {raw!r}')
        unknown = sorted(set(raw) - set(_MCP_SERVER_FIELDS))
        if unknown:
            raise ConfigError(
                f'[mcp.servers."{name}"] 含未知字段: {unknown}'
                f"(支持: {sorted(_MCP_SERVER_FIELDS)})"
            )
        transport = raw.get("transport", "auto")
        if not isinstance(transport, str) or transport not in _MCP_TRANSPORTS:
            raise ConfigError(
                f'[mcp.servers."{name}"] transport 须为 auto|stdio|http,得到: {transport!r}'
            )
        command = raw.get("command")
        url = raw.get("url")
        has_command = command is not None
        has_url = url is not None
        if has_command and has_url:
            raise ConfigError(
                f'[mcp.servers."{name}"] command 与 url 恰居其一,但都给了'
                f"(stdio 传输给 command,Streamable HTTP 传输给 url)"
            )
        if not has_command and not has_url:
            raise ConfigError(
                f'[mcp.servers."{name}"] command/url 恰居其一,但都缺'
                f'(stdio 传输给 command = [...],Streamable HTTP 传输给 url = "https://...")'
            )
        if has_command:
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(c, str) and c for c in command)
            ):
                raise ConfigError(
                    f'[mcp.servers."{name}"] command 必填,须为非空字符串数组,得到: {command!r}'
                )
        elif not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ConfigError(
                f'[mcp.servers."{name}"] url 须为 http(s):// URL,得到: {url!r}'
            )
        if transport == "stdio" and not has_command:
            raise ConfigError(
                f'[mcp.servers."{name}"] transport = "stdio" 与键不符:须给 command(实际给了 url)'
            )
        if transport == "http" and not has_url:
            raise ConfigError(
                f'[mcp.servers."{name}"] transport = "http" 与键不符:须给 url(实际给了 command)'
            )
        env = raw.get("env") or {}
        if not isinstance(env, dict):
            raise ConfigError(f'[mcp.servers."{name}"] env 应为表,得到: {env!r}')
        for key, value in env.items():
            ok = isinstance(value, str) or (
                isinstance(value, dict)
                and set(value) == {"env"}
                and isinstance(value["env"], str)
                and value["env"]
            )
            if not ok:
                raise ConfigError(
                    f'[mcp.servers."{name}"] env.{key} 须为字符串字面量或 '
                    f'{{ env = "VAR" }} 间接引用,得到: {value!r}'
                )
        headers = raw.get("headers") or {}
        if not isinstance(headers, dict):
            raise ConfigError(f'[mcp.servers."{name}"] headers 应为表,得到: {headers!r}')
        if headers and not has_url:
            raise ConfigError(
                f'[mcp.servers."{name}"] headers 仅 Streamable HTTP(url)传输有意义,'
                f"stdio server 配了会被静默忽略,直接拒"
            )
        for key, value in headers.items():
            ok = isinstance(value, str) or (
                isinstance(value, dict)
                and set(value) == {"env"}
                and isinstance(value["env"], str)
                and value["env"]
            )
            if not ok:
                raise ConfigError(
                    f'[mcp.servers."{name}"] headers.{key} 须为字符串字面量或 '
                    f'{{ env = "VAR" }} 间接引用,得到: {value!r}'
                )
        protocol_version = raw.get("protocol_version", "")
        if not isinstance(protocol_version, str) or ("protocol_version" in raw and not protocol_version):
            raise ConfigError(
                f'[mcp.servers."{name}"] protocol_version 须为非空字符串'
                f'(如 "2025-03-26";缺省随传输:stdio 2024-11-05 / http 2025-03-26),'
                f"得到: {raw.get('protocol_version')!r}"
            )
        permission_raw = raw.get("permission", "read")
        if not isinstance(permission_raw, str) or permission_raw.lower() not in _MCP_PERMISSIONS:
            raise ConfigError(
                f'[mcp.servers."{name}"] permission 须为 read|write|net|exec,'
                f"得到: {permission_raw!r}"
            )
        timeout = raw.get("timeout", 30.0)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ConfigError(f'[mcp.servers."{name}"] timeout 须为正数,得到: {timeout!r}')
        connect_timeout = raw.get("connect_timeout", 10.0)
        if (
            not isinstance(connect_timeout, (int, float))
            or isinstance(connect_timeout, bool)
            or connect_timeout <= 0
        ):
            raise ConfigError(
                f'[mcp.servers."{name}"] connect_timeout 须为正数,得到: {connect_timeout!r}'
            )
        confirm = raw.get("confirm", False)
        if not isinstance(confirm, bool):
            raise ConfigError(f'[mcp.servers."{name}"] confirm 须为布尔,得到: {confirm!r}')
        specs.append(
            McpServerSpec(
                name=str(name),
                command=list(command) if has_command else [],
                env=dict(env),
                permission=_MCP_PERMISSIONS[permission_raw.lower()],
                timeout=float(timeout),
                confirm=confirm,
                connect_timeout=float(connect_timeout),
                url=str(url) if has_url else "",
                headers=dict(headers),
                transport=transport,
                protocol_version=protocol_version,
            )
        )
    return specs


def _context_section(cfg: dict[str, Any]) -> ContextSection:
    """``[context]`` 段 → :class:`ContextSection`(§7.2 压缩链调参;缺段 = 全默认)。

    严格未知字段 + 类型校验(同 ``_prices``/``_credentials``/``[blob]`` 先例):
    键拼错会静默落默认值——spill 阈值/熔断参数错位不痛不痒地失效,宁可装配期炸掉。
    """
    unknown = sorted(set(cfg) - set(_CONTEXT_FIELDS))
    if unknown:
        raise ConfigError(f"[context] 含未知字段: {unknown}(支持: {list(_CONTEXT_FIELDS)})")
    model = cfg.get("summarize_model")
    if model is not None and (not isinstance(model, str) or not model):
        raise ConfigError(f"[context] summarize_model 须为非空字符串,得到: {model!r}")
    threshold = cfg.get("spill_threshold_chars", 4000)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
        raise ConfigError(f"[context] spill_threshold_chars 须为正整数,得到: {threshold!r}")
    breaker = cfg.get("summarize_breaker", 3)
    if not isinstance(breaker, int) or isinstance(breaker, bool) or breaker < 1:
        raise ConfigError(f"[context] summarize_breaker 须为 >= 1 的整数,得到: {breaker!r}")
    temperature = cfg.get("summarize_temperature", 0.2)
    if not isinstance(temperature, (int, float)) or isinstance(temperature, bool) or temperature < 0:
        raise ConfigError(f"[context] summarize_temperature 须为非负数字,得到: {temperature!r}")
    return ContextSection(
        summarize_model=model,
        spill_threshold_chars=threshold,
        summarize_breaker=breaker,
        summarize_temperature=float(temperature),
    )


def _memory_section(cfg: dict[str, Any]) -> MemorySection:
    """``[memory]`` 段 → :class:`MemorySection`(经验参考段 recall 调参;缺键 = 全默认)。

    严格未知字段 + 类型校验(同 ``_context_section`` 先例):recall 三键拼错会静默
    落默认值——检索条数/截断口径错位不痛不痒地失效,宁可装配期炸掉。
    recall 段还需 manifest ``context_policy.recall: true`` 才启用(opt-in)。
    """
    unknown = sorted(set(cfg) - set(_MEMORY_FIELDS))
    if unknown:
        raise ConfigError(f"[memory] 含未知字段: {unknown}(支持: {list(_MEMORY_FIELDS)})")
    recall_k = cfg.get("recall_k", 3)
    if not isinstance(recall_k, int) or isinstance(recall_k, bool) or recall_k < 1:
        raise ConfigError(f"[memory] recall_k 须为 >= 1 的整数,得到: {recall_k!r}")
    entry_chars = cfg.get("recall_entry_chars", 800)
    if not isinstance(entry_chars, int) or isinstance(entry_chars, bool) or entry_chars < 1:
        raise ConfigError(f"[memory] recall_entry_chars 须为 >= 1 的整数,得到: {entry_chars!r}")
    total_chars = cfg.get("recall_total_chars", 2000)
    if not isinstance(total_chars, int) or isinstance(total_chars, bool) or total_chars < 1:
        raise ConfigError(f"[memory] recall_total_chars 须为 >= 1 的整数,得到: {total_chars!r}")
    return MemorySection(
        recall_k=recall_k,
        recall_entry_chars=entry_chars,
        recall_total_chars=total_chars,
    )


def _events_section(cfg: dict[str, Any]) -> EventsSection:
    """``[events]`` 段 → :class:`EventsSection`(E4 事件批处理三键;缺段 = 全默认)。

    严格未知字段 + 类型校验(同 ``_context_section``/``_memory_section`` 先例):
    开关/上限拼错会静默落默认值——批处理被关掉或截断口径错位不痛不痒地失效,
    宁可装配期炸掉。
    """
    unknown = sorted(set(cfg) - set(_EVENTS_FIELDS))
    if unknown:
        raise ConfigError(f"[events] 含未知字段: {unknown}(支持: {list(_EVENTS_FIELDS)})")
    batch = cfg.get("batch", True)
    if not isinstance(batch, bool):
        raise ConfigError(f"[events] batch 须为布尔,得到: {batch!r}")
    batch_max = cfg.get("batch_max", 50)
    if not isinstance(batch_max, int) or isinstance(batch_max, bool) or batch_max < 1:
        raise ConfigError(f"[events] batch_max 须为 >= 1 的整数,得到: {batch_max!r}")
    event_text_max = cfg.get("event_text_max", 2000)
    if (
        not isinstance(event_text_max, int)
        or isinstance(event_text_max, bool)
        or event_text_max < 1
    ):
        raise ConfigError(f"[events] event_text_max 须为 >= 1 的整数,得到: {event_text_max!r}")
    return EventsSection(batch=batch, batch_max=batch_max, event_text_max=event_text_max)


#: ``[telemetry]`` 支持的字段(§10.2;dir = WAL 目录,redact = PII 脱敏 hook,otlp = OTLP exporter 子表)
_TELEMETRY_FIELDS = ("dir", "redact", "otlp")

#: ``[telemetry.otlp]`` 支持的字段(endpoint 必填;其余为 OtlpExporter 调参,缺省随其默认值)
_TELEMETRY_OTLP_FIELDS = ("endpoint", "headers", "batch_max", "flush_interval", "queue_max", "timeout")


def _telemetry_section(cfg: dict[str, Any]) -> dict[str, Any]:
    """``[telemetry]`` 段严格解析(§10.2)→ 装配形态 dict(``dir``/``redact``/``otlp``)。

    严格未知字段 + 类型校验(同 ``_context_section`` 先例;闭合本段此前是全仓
    唯一非严格段的缺口——键拼错会静默不接线,best-effort exporter 静默丢数据,
    宁可装配期炸掉):

    - ``dir`` 非空字符串(WAL 目录;OTLP 是 exporter 不是 sink 替代);
    - ``redact`` 布尔(§10.2 逐字"默认关闭";开启后 WAL 不再逐字保真,合规取舍);
    - ``otlp`` 子表:``endpoint`` 必填 http(s):// 字符串;``headers`` 值为
      字符串字面量或 ``{env = "VAR"}`` 间接引用(``os.environ`` **装配时**现读,
      不落盘明文——同 ``_mcp_servers`` headers 先例,唯解析时点不同:mcp 连
      接时现读,otlp 一次性解析;变量缺席 → ConfigError);
      ``batch_max``/``flush_interval``/``queue_max``/``timeout`` 正数。
    """
    unknown = sorted(set(cfg) - set(_TELEMETRY_FIELDS))
    if unknown:
        raise ConfigError(f"[telemetry] 含未知字段: {unknown}(支持: {list(_TELEMETRY_FIELDS)})")
    section: dict[str, Any] = {"dir": None, "redact": False, "otlp": None}
    trace_dir = cfg.get("dir")
    if trace_dir is not None:
        if not isinstance(trace_dir, str) or not trace_dir:
            raise ConfigError(f"[telemetry] dir 须为非空字符串路径,得到: {trace_dir!r}")
        section["dir"] = trace_dir
    redact = cfg.get("redact", False)
    if not isinstance(redact, bool):
        raise ConfigError(f"[telemetry] redact 须为布尔,得到: {redact!r}")
    section["redact"] = redact
    otlp = cfg.get("otlp")
    if otlp is not None:
        if not isinstance(otlp, dict):
            raise ConfigError(f"[telemetry.otlp] 应为表({{ endpoint = \"http://...\" }}),得到: {otlp!r}")
        unknown = sorted(set(otlp) - set(_TELEMETRY_OTLP_FIELDS))
        if unknown:
            raise ConfigError(
                f"[telemetry.otlp] 含未知字段: {unknown}(支持: {list(_TELEMETRY_OTLP_FIELDS)})"
            )
        endpoint = otlp.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            raise ConfigError(f"[telemetry.otlp] endpoint 必填,须为 http(s):// URL,得到: {endpoint!r}")
        headers: dict[str, str] = {}
        raw_headers = otlp.get("headers") or {}
        if not isinstance(raw_headers, dict):
            raise ConfigError(f"[telemetry.otlp] headers 应为表,得到: {raw_headers!r}")
        for key, value in raw_headers.items():
            if isinstance(value, str):
                headers[key] = value
                continue
            ok = (
                isinstance(value, dict)
                and set(value) == {"env"}
                and isinstance(value["env"], str)
                and value["env"]
            )
            if not ok:
                raise ConfigError(
                    f'[telemetry.otlp] headers.{key} 须为字符串字面量或 '
                    f'{{ env = "VAR" }} 间接引用,得到: {value!r}'
                )
            env_value = os.environ.get(value["env"])
            if env_value is None:
                raise ConfigError(
                    f'[telemetry.otlp] headers.{key} 间接引用的环境变量 {value["env"]} 不存在'
                    f'(不落盘明文;请先 export {value["env"]}=...)'
                )
            headers[key] = env_value
        kwargs: dict[str, Any] = {"endpoint": endpoint, "headers": headers}
        for key in ("batch_max", "flush_interval", "queue_max", "timeout"):
            raw = otlp.get(key)
            if raw is None:
                continue
            if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw <= 0:
                raise ConfigError(f"[telemetry.otlp] {key} 须为正数,得到: {raw!r}")
            kwargs[key] = raw
        section["otlp"] = kwargs
    return section


def _data_policy(cfg: dict[str, Any]) -> DataPolicy:
    """``[data]`` 段 → :class:`DataPolicy`(D2;docs/DATA-AUTHZ.md §3.1/§3.2)。

    形态::

        [data]
        domains = [ { name, sensitivity?, path_prefix?|url_prefix? }, ... ]
        [data.principals."<subject>"]
        domains = ["fs.*", "net.public"]

    ``name`` 必填;``sensitivity`` 缺省 confidential(忘了配 = 最严,§3.1);
    ``path_prefix``/``url_prefix`` 恰居其一(域边界二选一);白名单值为 glob
    域名模式表。非法形态/未知键 → ConfigError——域边界拼错会静默失去拦截,
    宁可装配期炸掉(同 ``_prices``/``_credentials`` 严格先例)。
    """
    unknown = sorted(set(cfg) - {"domains", "principals"})
    if unknown:
        raise ConfigError(f"[data] 含未知字段: {unknown}(支持: ['domains', 'principals'])")
    domains: dict[str, DataDomain] = {}
    boundaries: dict[str, tuple[str, str]] = {}
    raw_domains = cfg.get("domains") or []
    if not isinstance(raw_domains, list):
        raise ConfigError(f"[data] domains 应为表数组,得到: {raw_domains!r}")
    for raw in raw_domains:
        if not isinstance(raw, dict):
            raise ConfigError(f"[data] domains 每项应为表,得到: {raw!r}")
        unknown = sorted(set(raw) - {"name", "sensitivity", "path_prefix", "url_prefix"})
        if unknown:
            raise ConfigError(
                f"[data] domains 项含未知字段: {unknown}"
                f"(支持: ['name', 'path_prefix', 'sensitivity', 'url_prefix'])"
            )
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"[data] domains 项的 name 须为非空字符串,得到: {name!r}")
        if name in domains:
            raise ConfigError(f"[data] domains 域名重复: {name!r}")
        sensitivity = raw.get("sensitivity", CONFIDENTIAL)
        if not isinstance(sensitivity, str) or not sensitivity:
            raise ConfigError(f"[data] 域 {name!r} 的 sensitivity 须为非空字符串")
        path_prefix, url_prefix = raw.get("path_prefix"), raw.get("url_prefix")
        if (path_prefix is None) == (url_prefix is None):
            raise ConfigError(f"[data] 域 {name!r} 的 path_prefix/url_prefix 须恰居其一")
        prefix = path_prefix if path_prefix is not None else url_prefix
        if not isinstance(prefix, str) or not prefix:
            raise ConfigError(f"[data] 域 {name!r} 的边界前缀须为非空字符串")
        domains[name] = DataDomain(name=name, sensitivity=sensitivity)
        boundaries[name] = ("fs", path_prefix) if path_prefix is not None else ("net", url_prefix)
    whitelists: dict[str, tuple[str, ...]] = {}
    raw_principals = cfg.get("principals") or {}
    if not isinstance(raw_principals, dict):
        raise ConfigError(f"[data.principals] 应为表,得到: {raw_principals!r}")
    for subject, raw in raw_principals.items():
        if not isinstance(raw, dict):
            raise ConfigError(f'[data.principals."{subject}"] 应为表(如 {{ domains = ["fs.*"] }})')
        unknown = sorted(set(raw) - {"domains"})
        if unknown:
            raise ConfigError(
                f'[data.principals."{subject}"] 含未知字段: {unknown}(支持: [\'domains\'])'
            )
        patterns = raw.get("domains")
        if not isinstance(patterns, list) or not all(isinstance(p, str) and p for p in patterns):
            raise ConfigError(
                f'[data.principals."{subject}"] 的 domains 须为非空字符串数组(glob 域名模式)'
            )
        whitelists[str(subject)] = tuple(patterns)
    return DataPolicy(domains=domains, whitelists=whitelists, boundaries=boundaries)


def web_token_map(cfg: dict[str, Any]) -> dict[str, str]:
    """``[web.tokens]`` → ``{token: subject}``(D3-lite 多用户映射;docs/DATA-AUTHZ.md §2.2)。

    值须为非空字符串 subject(如 ``"user:alice"``);非表/非法形态 → ConfigError
    (token 表拼错会静默退回单用户——认证面宁可装配期炸掉,同 ``_credentials`` 先例)。
    """
    raw = (cfg.get("web") or {}).get("tokens") or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"[web.tokens] 应为表({{ \"<token>\" = \"user:<login>\" }}),得到: {raw!r}")
    table: dict[str, str] = {}
    for token, subject in raw.items():
        if not isinstance(subject, str) or not subject:
            raise ConfigError(
                f"[web.tokens] {token!r} 的映射值须为非空字符串 subject(如 \"user:alice\")"
            )
        table[str(token)] = subject
    return table


def _sidecars(cfg: dict[str, Any]) -> list[Any]:
    sidecars: list[Any] = []
    unknown = sorted(
        set(cfg) - {"budget_guard", "loop_detector", "tool_guard_rules", "human_approval", "distill"}
    )
    if unknown:
        raise ConfigError(f"[sidecars] 含未知 sidecar: {unknown}")
    if "budget_guard" in cfg:
        bg = cfg["budget_guard"] or {}
        action = bg.get("action", "stop")
        if action not in ("stop", "pause"):
            raise ConfigError(
                f'[sidecars] budget_guard.action 应为 "stop" 或 "pause"(§2.4 '
                f"stop→pause 降级),得到: {action!r}"
            )
        sidecars.append(
            BudgetGuard(
                max_cost=bg.get("max_cost"),
                max_steps=bg.get("max_steps"),
                max_wall_time=bg.get("max_wall_time"),
                action=action,
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
    if "human_approval" in cfg:
        # WS2(docs/SUPERVISOR.md §10):人工裁决已下沉为内核 tool-confirm 闸门;
        # 本键装配 HumanApproval 策略载体(EXEC 档工具也过闸)。值 = true(缺省
        # 策略)或表;on_timeout 语义:deny=超时拒绝(policy fail),allow=超时
        # 兜底批准本次(default_answer=approve-once;装配期映射见 KernelBuilder.build)
        ha = cfg["human_approval"]
        if ha is True:
            ha = {}
        if not isinstance(ha, dict):
            raise ConfigError(
                f"[sidecars] human_approval 应为 true 或表"
                f'(如 {{ timeout = 600, on_timeout = "deny" }}),得到: {ha!r}'
            )
        unknown_ha = sorted(set(ha) - {"timeout", "on_timeout"})
        if unknown_ha:
            raise ConfigError(
                f"[sidecars] human_approval 含未知字段: {unknown_ha}"
                f"(支持: ['on_timeout', 'timeout'])"
            )
        on_timeout = ha.get("on_timeout", "deny")
        if on_timeout not in ("deny", "allow"):
            raise ConfigError(
                f"[sidecars] human_approval.on_timeout 应为 'deny' | 'allow',"
                f"得到: {on_timeout!r}"
            )
        sidecars.append(
            HumanApproval(timeout=float(ha.get("timeout", 600)), on_timeout=on_timeout)
        )
    if "distill" in cfg:
        # §11.2 写路径范式:蒸馏 sidecar。true = 全默认;表形态 strict 校验(照
        # human_approval 先例:未知键/类型错 ConfigError)。需要 [memory] 段配合:
        # 无 [memory] 时装配出的实例 bind 不到 memory,休眠不触发
        d = cfg["distill"]
        if d is True:
            d = {}
        if not isinstance(d, dict):
            raise ConfigError(
                f"[sidecars] distill 应为 true 或表"
                f'(如 {{ model = "kimi/cheap", min_tool_calls = 5 }}),得到: {d!r}'
            )
        unknown_d = sorted(
            set(d)
            - {"model", "min_tool_calls", "temperature", "breaker_threshold", "max_transcript_chars", "verify"}
        )
        if unknown_d:
            raise ConfigError(
                f"[sidecars] distill 含未知字段: {unknown_d}"
                f"(支持: ['breaker_threshold', 'max_transcript_chars', 'min_tool_calls', 'model', 'temperature', 'verify'])"
            )
        model = d.get("model")
        if model is not None and (not isinstance(model, str) or not model):
            raise ConfigError(f"[sidecars] distill.model 须为非空字符串,得到: {model!r}")
        ints: dict[str, Any] = {}
        for key, default in (
            ("min_tool_calls", 5),
            ("breaker_threshold", 3),
            ("max_transcript_chars", 24000),
        ):
            val = d.get(key, default)
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                raise ConfigError(
                    f"[sidecars] distill.{key} 须为 >= 0 的整数,得到: {val!r}"
                )
            ints[key] = val
        temperature = d.get("temperature", 0.2)
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            raise ConfigError(f"[sidecars] distill.temperature 须为数值,得到: {temperature!r}")
        verify = d.get("verify", True)
        if not isinstance(verify, bool):
            raise ConfigError(f"[sidecars] distill.verify 须为布尔,得到: {verify!r}")
        sidecars.append(
            DistillSidecar(
                model=model,
                min_tool_calls=ints["min_tool_calls"],
                temperature=float(temperature),
                breaker_threshold=ints["breaker_threshold"],
                max_transcript_chars=ints["max_transcript_chars"],
                verify=verify,
            )
        )
    return sidecars


def _smoke_kernel_factory(cfg: dict[str, Any]) -> Callable[[], Any]:
    """默认验证门(register_smoke="default")的按例内核工厂。

    剥离 watcher/MCP/sidecars 防按例累积泄漏与蒸馏副作用:每个重放用例都重建
    一次内核,看门狗 daemon 线程与 MCP stdio 子进程不剥会按例泄漏;DistillSidecar
    会在 run 终态蒸馏写 memory(真实副作用,冒烟 run 绝不能触发)。
    ``register_smoke`` 键一并移除:冒烟内核自身没有 register 需求,重绑无意义。
    代价(文档注明):候选技能引用 mcp.* 工具时冒烟 run 必然 fail-closed。

    cfg2 只构建一次(装配期配置不变),工厂本身按例调用 build_kernel;
    user_channel/supervisor handler 等宿主通道不注入(零参调用)。
    """
    cfg2 = dict(cfg)
    skills2 = dict(cfg.get("skills") or {})
    skills2.pop("register_smoke", None)
    skills2["watch_interval"] = 0
    cfg2["skills"] = skills2
    cfg2.pop("mcp", None)
    cfg2.pop("sidecars", None)
    return lambda: build_kernel(cfg2)


def _register_drafts_root(cfg: dict[str, Any], skills_path: Any) -> str:
    """默认验证门的草稿根:[lab].drafts_root 优先(与 Web Lab 同一配置键);
    缺省 = skills 路径同级的 ``drafts/``(文件与目录形态都取**同级**——目录形态下
    drafts 放进技能目录会被 loader 当技能包加载)。
    """
    return (cfg.get("lab") or {}).get("drafts_root") or str(Path(skills_path).parent / "drafts")


def build_kernel(
    config: str | Path | dict[str, Any],
    *,
    extra_sidecars: Iterable[Any] = (),
    supervisor_handler: Any = None,
    user_channel: Any = None,
) -> Any:
    """按 docs/RUNNERS.md §2.1 把 ``agent-os.toml``(或等价 dict)装配为 Kernel。

    缺省:无 ``[run]`` 用 RunConfig 默认;无 ``[providers]`` → 空 Manager
    (运行时才报"provider 前缀未注册");无 ``[skills]``/``[telemetry]``/``[sidecars]``
    对应子系统不接线;无 ``[mcp]`` 不拉起任何 MCP server(零破坏)。
    启用 ``python_exec``(配置键)即把 RunConfig 全局权限上限提到 EXEC
    (实际工具名 ``system.python.exec``)。
    (§8.2:工具自报 EXEC 级,不提上限必被分发层拒绝,配置即授权)。

    ``extra_sidecars``:配置文件之外由宿主追加的 sidecar(Web runner 的 stop
    通道占位 sidecar;M4 起仅有 sidecar 时 builder 才装配 ``kernel.ctl``)。

    ``supervisor_handler``(S2,docs/SUPERVISOR.md §2.3):宿主注入的调用方通道
    (``async def handler(question) -> Answer``),与 TOML ``[supervisor]``
    段的策略字段(timeout_s/on_timeout/default_answer)合并装配
    SupervisorManager;不传则维持 S1 行为(仅预置策略,运行时 ask 报
    not_found,fail-closed)。Web 宿主传 InboxChannel(默认通道),
    CLI 传 stderr/stdin 协议 handler。

    ``user_channel``(M1,§8.3):宿主注入的用户通道(带 ``ask(question)`` /
    ``notify(message)`` 方法的对象,同步/async 均可),经
    ``KernelBuilder.user_channel`` → ``tools.bind_user_channel`` 接线;
    不传则 system.user.ask/notify 调用报"user 通道未装配"结构化错误。
    """
    cfg = load_config(config) if isinstance(config, (str, Path)) else dict(config)
    run_cfg = _run_config(cfg.get("run") or {})

    tools_cfg = cfg.get("tools") or {}
    registry = (
        LocalPythonToolRegistry.with_builtins()
        if tools_cfg.get("builtins", False)
        else LocalPythonToolRegistry()
    )
    cred_cfg = cfg.get("credentials")
    if cred_cfg is not None:
        # WS1:[credentials] 段存在才接线;缺席完全不 bind(dispatch 注入空 credentials,零破坏)
        registry.bind_credentials(_credentials(cred_cfg))
    data_cfg = cfg.get("data")
    if data_cfg is not None:
        # D2:[data] 段存在才接线(bind policy + 注册域边界);缺席完全不 bind,
        # registry 维持 D1"未配置不拦截"语义逐字不动(docs/DATA-AUTHZ.md §8 实现注 1)
        policy = _data_policy(data_cfg)
        if not policy.domains and not policy.whitelists:
            # 空 [data] 段 = 绑空策略:fail-closed 语义下,带 principal 的调用对声明了
            # data_domains 的工具全被拒(域未配置按 confidential + subject 白名单空表)。
            # 行为不变,装配期提示是否有意(空段更可能是漏配)
            _log.warning(
                "[data] 段为空(无 domains 无 principals):数据策略按 fail-closed 绑定,"
                "带身份的调用对全部声明 data_domains 的工具访问都会被拒;"
                "若属漏配请补 domains/principals,若无意启用数据层请删除空 [data] 段"
            )
        registry.bind_data_policy(policy)
        for name, (kind, prefix) in policy.boundaries.items():
            if kind == "fs":
                registry.register_fs_domain(policy.domains[name], prefix)
            else:
                registry.register_net_domain(policy.domains[name], prefix)
    if tools_cfg.get("python_orchestrate", False):
        # 编排伪工具(docs/CODE-ORCHESTRATION.md):显式开启;声明即授权,权限上限提到 EXEC
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
            # 与 system.python.exec 同理(§8.2 配置即授权):宿主显式装配自定义工具,
            # 工具自报等级可能达 EXEC,不提上限必被分发层拒绝
            run_cfg.tool_policy = ToolPolicy(max_permission=Permission.EXEC)

    mcp_cfg = cfg.get("mcp")
    if mcp_cfg is not None:
        # [mcp] 段存在才接线(同 [credentials]/[memory] 先例;缺段零破坏)。
        # eager(定案):装配期建立连接(stdio 子进程 / HTTP 会话)+ initialize 握手
        # + tools/list,工具以 mcp.<server>.<tool> 注册进 registry(走全量 dispatch 管线);
        # 连接失败 ConfigError 快速失败——坏 server 静默缺席会让技能白名单形同虚设
        mcp_specs = _mcp_servers(mcp_cfg)
        if mcp_specs:
            try:
                connect_and_register_sync(registry, mcp_specs)
            except McpError as e:
                raise ConfigError(f"[mcp] server 装配失败(eager 连接): {e}") from e

    builder = KernelBuilder(run_cfg).tools(registry).logic_kernels(*logic)
    providers_cfg = cfg.get("providers") or {}
    providers = _providers(providers_cfg)
    if providers:
        builder.providers(*providers)
    router = _providers_router(providers_cfg)
    if router is not None:
        # §4.2 ModelRouter 扩展点:[providers] router 自定义实现;缺省由 builder 落 DefaultModelRouter
        builder.router(router)
    skills_cfg = _skills_section(cfg.get("skills") or {})
    skills_path = skills_cfg.get("path")
    if skills_path:
        skills_registry = LocalFileSkillRegistry(skills_path)
        smoke_spec = skills_cfg.get("register_smoke")
        if smoke_spec == "default":
            # §6.2 验证门默认实现:草稿重放 + expected/expect 双判定
            # (skills/register_smoke.py;延迟 import 防环——该模块依赖 skills.* 全链)
            from agent_os.skills.register_smoke import DefaultRegisterSmoke

            skills_registry.bind_register_smoke(
                DefaultRegisterSmoke(
                    production=skills_registry,
                    store=DraftStore(_register_drafts_root(cfg, skills_path)),
                    make_kernel=_smoke_kernel_factory(cfg),
                    judge_providers=ProviderManager(providers) if providers else None,
                    judge_model=skills_cfg.get("register_judge_model") or run_cfg.model,
                )
            )
        elif smoke_spec:
            # §6.2 验证门降级形态:dotted path 加载冒烟回调并 bind 进 register() 管线
            # (加载失败 ConfigError,同 [tools.custom] 先例)
            skills_registry.bind_register_smoke(_load_dotted(smoke_spec))
        if skills_cfg["watch_interval"] > 0:
            # 轮询热重载看门狗(daemon 线程,随进程;reload 失败保留旧表记 log)
            skills_registry.start_watching(skills_cfg["watch_interval"])
        builder.skills(skills_registry)
    elif (
        skills_cfg["watch_interval"] > 0
        or skills_cfg.get("register_smoke")
        or skills_cfg.get("register_judge_model")
    ):
        raise ConfigError(
            "[skills] 配置了 watch_interval/register_smoke/register_judge_model 但没有 path"
            "(无 registry 可接线;register_judge_model 离开默认验证门同样无意义)"
        )
    sidecars = [*_sidecars(cfg.get("sidecars") or {}), *extra_sidecars]
    if sidecars:
        builder.sidecars(*sidecars)
    sup_cfg = cfg.get("supervisor") or {}
    unknown = sorted(set(sup_cfg) - {"timeout_s", "on_timeout", "default_answer"})
    if unknown:
        raise ConfigError(
            f"[supervisor] 含未知字段: {unknown}"
            f"(支持: timeout_s/on_timeout/default_answer;handler 为可调用,"
            f"须走 build_kernel(supervisor_handler=...) / KernelBuilder.supervisor API 注入)"
        )
    if sup_cfg.get("on_timeout", "fail") not in ("fail", "default_answer"):
        raise ConfigError(
            f"[supervisor] on_timeout 应为 'fail' | 'default_answer',"
            f"得到: {sup_cfg.get('on_timeout')!r}"
        )
    if sup_cfg or supervisor_handler is not None:
        # handler 写不进 TOML:策略字段来自 [supervisor] 段,通道由宿主注入(§2.3);
        # 两者皆无则维持 S1 行为——build 不装配 Manager,运行时 ask 按
        # "未装配 supervisor handler" 报 not_found(fail-closed)
        builder.supervisor(
            supervisor_handler,
            **{k: sup_cfg[k] for k in ("timeout_s", "on_timeout", "default_answer") if k in sup_cfg},
        )
    if user_channel is not None:
        # M1 §8.3:system.user.ask/notify 的宿主回调通道(handler 同写不进 TOML,
        # 由宿主注入,同 supervisor_handler 先例)
        builder.user_channel(user_channel)
    telemetry_cfg = cfg.get("telemetry")
    if telemetry_cfg is not None:
        # §10.2:严格段解析(dir/redact/otlp 三键;未知字段 ConfigError,同各段先例)
        section = _telemetry_section(telemetry_cfg)
        if section["dir"] is None and (section["redact"] or section["otlp"] is not None):
            raise ConfigError(
                '[telemetry] 配置了 redact/otlp 但缺 dir(OTLP 是 exporter 不是 sink 替代——'
                'sink 需要 WAL 目录;请补 dir = "./traces")'
            )
        if section["dir"] is not None:
            sink = JsonlTelemetrySink(section["dir"], redact=section["redact"])
            if section["otlp"] is not None:
                sink.register_exporter(OtlpExporter(**section["otlp"]))
            builder.telemetry(sink)
    memory_cfg = cfg.get("memory")
    if memory_cfg is not None:
        # M6:[memory] 段存在才接线(同 [credentials] 先例);缺席完全不 bind。
        # 严格未知字段 + recall_* 类型校验在 _memory_section(同 _context_section
        # 先例):dir 拼错会静默写到别的目录,recall 调参拼错会静默落默认值
        section = _memory_section(memory_cfg)
        mem_dir = memory_cfg.get("dir") or "./memory"
        if not isinstance(mem_dir, str):
            raise ConfigError(f"[memory] dir 须为字符串路径,得到: {mem_dir!r}")
        builder.memory(LocalFileMemoryService(mem_dir))
        builder.memory_section(section)
    blob_cfg = cfg.get("blob")
    if blob_cfg is not None:
        # M3:[blob] 段存在才接线(同 [memory] 先例);缺段 = 进程内 InMemoryBlobStore(零破坏)。
        # 严格未知字段(同 _prices/_credentials):dir 拼错会静默落到内存版,spill 不持久
        unknown_blob = sorted(set(blob_cfg) - {"dir"})
        if unknown_blob:
            raise ConfigError(f"[blob] 含未知字段: {unknown_blob}(支持: ['dir'])")
        blob_dir = blob_cfg.get("dir")
        if not isinstance(blob_dir, str) or not blob_dir:
            raise ConfigError(f"[blob] dir 须为非空字符串路径,得到: {blob_dir!r}")
        builder.blob(FileBlobStore(blob_dir))
    # WS2:[context] 段(§7.2 压缩链调参)——缺段也过一遍校验函数,落全默认 ContextSection
    builder.context_section(_context_section(cfg.get("context") or {}))
    # [events] 段(E4 事件批处理)——缺段也过一遍校验函数,落全默认 EventsSection(同 [context] 先例)
    builder.events_section(_events_section(cfg.get("events") or {}))
    prices = _prices(cfg.get("prices") or {})
    builder.prices(prices)
    if not prices:
        # fail-open 警示:配了成本上限却没有价格源 = 没有护栏,必须让人知道
        _log.warning(
            "未配置 [prices] 模型单价表:usage.cost 将恒为 0,"
            "RunConfig.max_cost=%.2f 与 BudgetGuard 的成本上限**不会触发**"
            "(§4.2 记账;按量计费端点请补 [prices])",
            run_cfg.max_cost,
        )
    retry = cfg.get("retry") or {}
    if retry:
        # 严格未知字段(同 _prices/_credentials):超时时长拼错会静默失去看门狗调节
        unknown_retry = sorted(set(retry) - {"max_attempts", "backoff_base", "stream_idle_timeout"})
        if unknown_retry:
            raise ConfigError(
                f"[retry] 含未知字段: {unknown_retry}"
                f"(支持: ['backoff_base', 'max_attempts', 'stream_idle_timeout'])"
            )
        builder.retry(
            max_attempts=retry.get("max_attempts"),
            backoff_base=retry.get("backoff_base"),
            stream_idle_timeout=retry.get("stream_idle_timeout"),
        )
    return builder.build()
