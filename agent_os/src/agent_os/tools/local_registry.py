"""LocalPythonToolRegistry(docs/DESIGN.md §8.4;M1;STDLIB-CATALOG §W0-1 workdir 分区)。

函数即工具:decorator 注册,schema 从签名推导(str/int/float/bool → JSON 基本型;
list[X]/dict[str, X] → array/object;有默认值 → 非 required;docstring 首段 → description)。
sync 函数包 ``asyncio.to_thread``;§8.1 分发流水线全量实现(契约本体,不是可简化项)。

§W0-1:``ToolDispatchContext.workdir``/``read_paths`` 把"每 run 临时目录"升级为可配置
三分区(read_paths 只读 / workdir 读写 / 缺省临时目录);``resolve_work_path`` 是
system.file.read/system.file.write/system.file.edit/system.shell.exec 共用的统一路径解析器(三段判定,逃逸检查保留)。

§W1-3 replayable 回放:``ToolDispatchContext.replay_records`` 有记录时,``spec.replayable``
工具按调用序弹出记录值返回而不执行(host replay 的接线留后续里程碑);§W1-4:
``_run_states`` 是 run 级工具状态表(todo 清单等,同 run_id 跨帧共享,checkpoint
随档持久见 kernel.checkpoint);§W1-5:``bind_skills`` 注入 SkillRegistry 引用,
作 skill_search 的技能数据源(装配钩子,同 bind_signals 先例);M6:``bind_memory``
注入 MemoryService,作 memory_search/memory_write 数据源(同 bind_skills 先例)。WS1:
``bind_credentials`` 注入凭证作用域解析器(签名 ``(principal, declared_keys)``),
dispatch 按 ``spec.credentials`` 声明键现解析注入 ``ToolContext.credentials``
(未声明/未 bind → 空 dict;泄露纪律:值不进帧/checkpoint,错误消息只带键名)。
K2(docs/STDLIB.md §3.3):run 级工具状态分两类生命周期——**逻辑状态**用
``_run_states``(checkpoint 随附,resume 原样恢复;todo 清单先例;``release_run``
故意**不清**——run 收尾快照在回收之后才经 kernel.checkpoint 落盘,清了会丢档);
**进程态/易失状态**用 ``_proc_states``(shell 会话 cwd/env 等,**不进 checkpoint**,
``release_run`` 统一回收,resume 按需重建、幂等——与内核进程本地状态
``_stop_flags``/``_branch_budgets`` 同语义)。
D2(docs/DATA-AUTHZ.md §3/§6):``bind_data_policy`` 注入 [data] 策略后,数据闸
按域族分派(fs=路径前缀/net=URL 前缀,``register_net_domain`` 最长前缀优先),
"未配置域 = confidential" 生效,per-subject 域白名单并入 ``allow()`` 第二判据;
放行/拒绝经留存的信号总线发 ``data.access.granted``/``data.access.denied``
审计信号,放行判据回写 ``ToolContext.credentials["_authz"]``。
D3(docs/DATA-AUTHZ.md 派生链;WS1):``principal.attrs["via"]`` = 上游 principal
dict 列表(近端在前,元素 ``{subject, issuer, attrs}`` 可递归再含 via;引擎不
伪造链,宿主声明)——链在场时当前数据访问判定对**链上每一环**(含当前
principal)都过才放行(最弱一环判定,任一环拒 = 拒,理由注明哪一环);
链形状坏/深度超限 fail-closed 拒绝;无 via → 现状逐字不动(``_expand_via_chain``)。
§W4-3:构造器注册 ``fetch_page``(std/web 工具面,实现与注册时机说明见
tools/std_web.py)。
MCP(tools/mcp.py):``_mcp_clients`` 是 ``[mcp.servers]`` eager 装配的 stdio
client 列表(``connect_and_register`` 装配钩子注入;kernel 寿命——Kernel/registry
均无 close 钩子,进程清理由各 client 自带 close()/atexit 兜底)。
D4:``sensitive_domains`` 是确认卡片(升权/tool-confirm)的"敏感"判定公开口,
与数据闸共用 ``_resolve_declared_domains`` 解析(判定不另写第二份)。
D3(§17-3):``bind_schedule`` 注入宿主调度服务(store-backed),作
``system.schedule.set`` 跨 run 自动派生的登记面(注册点在构造器,同 timer
先例;未 bind → "schedule 服务未装配"结构化错误)。
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import shutil
import tempfile
import traceback
import types
import typing
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

import jsonschema

from agent_os.api.v1 import (
    CONFIDENTIAL,
    DATA_ACCESS_DENIED,
    DATA_ACCESS_GRANTED,
    PUBLIC,
    DataDomain,
    Permission,
    Principal,
    Signal,
    Tool,
    ToolCall,
    ToolContext,
    ToolDispatchContext,
    ToolError,
    ToolErrorKind,
    ToolResult,
    ToolSpec,
    allow,
    clearance_of,
)
from agent_os.tools.blob import InMemoryBlobStore


class _FunctionTool:
    """decorator 产物:把普通 Python 函数包装成 ``agent_os.api.v1.Tool``。

    约定:签名中名为 ``ctx`` 的参数由分发方注入 ``ToolContext``(不进 schema);
    函数返回 ``ToolResult`` 时直接透传(工具自行构造结构化错误),否则包成 ``ok=True``。
    """

    def __init__(self, func: Callable, spec: ToolSpec, aliases: Iterable[str] | None = None) -> None:
        self.spec = spec
        self._func = func
        self._wants_ctx = "ctx" in inspect.signature(func).parameters
        self.aliases = tuple(aliases or ())

    def with_alias(self, alias: str) -> _FunctionTool:
        """Return a copy of this tool registered under a different name."""
        from dataclasses import replace
        return _FunctionTool(self._func, replace(self.spec, name=alias), self.aliases)

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        kwargs = {**args, "ctx": ctx} if self._wants_ctx else dict(args)
        if inspect.iscoroutinefunction(self._func):
            value = await self._func(**kwargs)
        else:
            value = await asyncio.to_thread(self._func, **kwargs)
        if isinstance(value, ToolResult):
            return value
        return ToolResult(ok=True, value=value)


class LocalPythonToolRegistry:
    """本地 Python 函数工具表(M1)。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._blob = InMemoryBlobStore()
        self._workdirs: dict[str, str] = {}
        #: §W1-4 run 级工具状态(todo 清单等;同 run_id 的帧共享,checkpoint 随档持久)
        self._run_states: dict[str, dict[str, Any]] = {}
        #: K2 run 级**进程态**工具状态(shell 会话 cwd/env 等;同 run_states 按 run_id
        #: 分桶,但**不进 checkpoint**,``release_run`` 统一回收;两类生命周期见模块 docstring)
        self._proc_states: dict[str, dict[str, Any]] = {}
        #: §W1-5 skill_search 的技能数据源(KernelBuilder 装配时经 bind_skills 注入)
        self._skills: Any = None
        #: M6 memory_search/memory_write 的 MemoryService 数据源(装配时经 bind_memory 注入)
        self._memory: Any = None
        #: M1 system.user.ask/system.user.notify 的宿主回调通道(装配时经 bind_user_channel 注入)
        self._user_channel: Any = None
        #: 数据域边界表(docs/DATA-AUTHZ.md §3.1;D1):(路径前缀, 域),最长前缀优先;
        #: 空表 = 只有内置默认域 fs.workdir(public),其余路径按现状沙箱不拦截
        self._fs_domains: list[tuple[Path, DataDomain]] = []
        #: net 数据域边界表(D2):(URL 前缀, 域),最长前缀优先,同 fs
        self._net_domains: list[tuple[str, DataDomain]] = []
        #: [data] 配置策略(D2;``bind_data_policy`` 装配钩子注入):
        #: None = D1 语义(未配置不拦截);在场 = "解析失败/未命中按 confidential"
        #: + per-subject 域白名单并入 allow() 第二判据(§3.1/§3.2)
        self._data_policy: Any = None
        #: 信号总线引用(D2 审计信号 emit 用;``bind_signals`` 装配时留存,None = 跳过发射)
        self._signals_bus: Any = None
        #: 凭证作用域解析器(WS1;``bind_credentials`` 装配钩子注入;
        #: None = 空作用域,dispatch 注入空 credentials,零破坏)
        self._credentials_resolver: Any = None
        #: MCP stdio client 列表(tools/mcp.py ``connect_and_register`` 装配钩子注入;
        #: kernel 寿命;进程清理由 client 自带 close()/atexit 兜底)
        self._mcp_clients: list[Any] = []
        #: WS1 system.timer.set 的计时器调度表(fire 注入通道 ctl 由 KernelBuilder
        #: 经 bind_timer 注入;未 bind 时调用报"timer 服务未装配"结构化错误)
        # §W4-3 std/web:fetch_page 构造器注册(为什么不在 with_builtins:
        # 见 tools/std_web.py 模块 docstring 的 §6.1 闸门联动说明)
        from agent_os.tools.std_web import fetch_page_tool

        self.register(fetch_page_tool(registry=self))
        self.register_alias("fetch_page", "common.web.fetch_page")
        # M1/WS1 构造器注册(同一 §6.1 闸门联动,理由同上):std/user·std/task 技能的
        # permissions.tools 声明 system.user.ask/system.timer.set,而既有锚点
        # (test_std_transform/test_std_nlp 等)用空工具表加载同一批 std 域文件——
        # 挂在 with_builtins 会让那些装配红掉。宿主通道/计时服务未 bind 时调用
        # 报"未装配"结构化错误(NOT_FOUND),语义与挂在 with_builtins 逐字一致
        from agent_os.tools.builtins import ask_user_tool, notify_user_tool
        from agent_os.tools.timer import TimerService, timer_set_tool

        self._timers = TimerService()
        self.register(ask_user_tool(name="system.user.ask", registry=self))
        self.register(notify_user_tool(name="system.user.notify", registry=self))
        self.register(timer_set_tool(name="system.timer.set", registry=self))
        #: D3(§17-3)system.schedule.set 的宿主调度服务(``bind_schedule`` 装配钩子
        #: 注入;web 宿主在 [schedule] 段启用时绑 store-backed 服务)。注册点同在
        #: 构造器(与 timer 同一先例);未 bind 时调用报"schedule 服务未装配"
        #: 结构化错误(NOT_FOUND),CLI 等未绑宿主行为与引入前一致
        from agent_os.tools.schedule import schedule_set_tool

        self._schedule: Any = None
        self.register(schedule_set_tool(name="system.schedule.set", registry=self))
        #: ch04 Event-Triggered(monitor_shell/connect_channel):system.monitor.set 与
        #: system.channel.connect 共用一张 MonitorService 事件监控表(fire 注入通道
        #: ctl 由 KernelBuilder 经 bind_monitor 注入;注册点同在构造器,与 timer 同一
        #: §6.1 闸门联动先例;未 bind 时调用报"monitor 服务未装配"结构化错误)
        from agent_os.tools.monitor import (
            MonitorService,
            channel_connect_tool,
            monitor_set_tool,
        )

        self._monitors = MonitorService()
        self.register(monitor_set_tool(name="system.monitor.set", registry=self))
        self.register(channel_connect_tool(name="system.channel.connect", registry=self))

    def tool(
        self, *, name: str | None = None, permission: Permission = Permission.READ, timeout: float = 30.0, **spec_kw: Any
    ) -> Callable:
        """§8.4 decorator:``@registry.tool(permission=Permission.NET, timeout=30)``。

        ``spec_kw`` 透传 ToolSpec 预留字段(examples/cacheable/confirm/concurrency_safe 等)。
        """

        def decorator(func: Callable) -> Callable:
            spec = derive_spec(func, name=name, permission=permission, timeout=timeout, **spec_kw)
            self.register(_FunctionTool(func, spec))
            return func

        return decorator

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool
        for alias in getattr(tool, "aliases", ()):
            self._tools[alias] = tool.with_alias(alias)

    def register_alias(self, alias: str, canonical: str) -> None:
        """Register ``alias`` as an alternate name for an already-registered tool.

        Used during the hierarchical naming migration to keep flat legacy names
        working while emitting deprecation warnings.
        """
        target = self._tools.get(canonical)
        if target is None:
            raise KeyError(f"Cannot alias {alias!r}: canonical tool {canonical!r} not registered")
        if isinstance(target, _FunctionTool):
            self._tools[alias] = target.with_alias(alias)
        else:
            self._tools[alias] = target

    def has(self, name: str) -> bool:
        return name in self._tools

    def specs(self) -> list[ToolSpec]:
        """全部已注册工具的 ToolSpec(注册序;docs/WEB-UI.md §6.2 Tools 浏览器数据源)。"""
        return [tool.spec for tool in self._tools.values()]

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"未注册的工具: {name}") from None

    def schemas_for(self, names: list[str]) -> list[dict[str, Any]]:
        """按给定名字顺序生成注入请求的 tool schema(顺序固定保前缀稳定,§7.4 不变量 5)。"""
        return [
            {"name": t.spec.name, "description": t.spec.description, "parameters": t.spec.parameters}
            for name in names
            if (t := self._tools.get(name)) is not None
        ]

    def schemas(self, allowed: list[str] | None = None) -> list[dict[str, Any]]:
        """生成注入请求的 tool schema 列表(顺序固定保前缀稳定,§7.4 不变量 5)。"""
        return self.schemas_for(allowed if allowed is not None else list(self._tools))

    @property
    def run_states(self) -> dict[str, dict[str, Any]]:
        """run 级工具状态表(§W1-4;key = run_id,同 run 的帧共享;checkpoint 随档持久)。

        K2 两类生命周期的**逻辑状态**类:resume 要原样恢复的状态放这里(todo 清单
        先例)。``release_run`` 故意不清理——run 收尾的 checkpoint 快照在回收之后
        才落盘(kernel/checkpoint.py dump 读本表),清了会丢档。
        """
        return self._run_states

    @property
    def proc_states(self) -> dict[str, dict[str, Any]]:
        """run 级**进程态**工具状态表(K2;key = run_id;docs/STDLIB.md §3.3)。

        K2 两类生命周期的**易失状态**类:shell 会话 cwd/env 等进程本地事实放这里
        ——**不进 checkpoint**(dump 只读 ``run_states``,本表结构性排除),resume
        按需重建、幂等(与内核 ``_stop_flags``/``_branch_budgets`` 同语义);
        ``release_run`` 统一回收。只存用户命令引入的**增量**(如 env delta),
        进程环境可能含 API key,整体落存既泄露又陈旧。
        """
        return self._proc_states

    def bind_signals(self, bus: Any) -> None:
        """KernelBuilder 装配钩子:给提供 ``bind()`` 的工具(如 system.python.exec)接信号总线。

        总线引用同时留存给 D2 数据层审计信号(``_check_data_access`` 拒绝/放行时
        emit;未走本钩子的嵌入方 = None,跳过发射,零破坏)。
        """
        self._signals_bus = bus
        for tool in self._tools.values():
            bind = getattr(tool, "bind", None)
            if callable(bind):
                bind(bus)

    def bind_data_policy(self, policy: Any) -> None:
        """装配钩子(D2,同 bind_signals/bind_credentials 先例):注入 [data] 数据策略。

        注入后数据闸语义切换(docs/DATA-AUTHZ.md §3.1/§3.3):目标解析失败/未命中
        已配置域 → 按 confidential(fail closed);per-subject 域白名单并入
        ``allow()`` 第二判据。缺省(未 bind)= D1 逐字语义:未配置不拦截。
        """
        self._data_policy = policy

    def bind_skills(self, skills: Any) -> None:
        """KernelBuilder 装配钩子(§W1-5):注入 SkillRegistry 引用,作 skill_search 数据源。"""
        self._skills = skills

    def bind_memory(self, service: Any) -> None:
        """装配钩子(M6,同 bind_skills 先例):注入 MemoryService,作 memory_search/memory_write 数据源。

        缺省(未 bind)= 工具在场但按"未装配 memory 子系统"报结构化错误,行为与引入前一致。
        """
        self._memory = service

    def bind_user_channel(self, channel: Any) -> None:
        """装配钩子(M1,同 bind_memory 先例):注入宿主用户通道,作 system.user.ask/notify 的回调。

        channel 形态:带 ``ask(question: str)`` 与 ``notify(message: str)`` 方法的宿主对象
        (同步/async 均可;CLI 宿主可用 stdin/stderr 协议形态,同 _cli_supervisor 先例)。
        缺省(未 bind)= 工具在场但按"user 通道未装配"报结构化错误,行为与引入前一致。
        """
        self._user_channel = channel

    def bind_timer(self, ctl: Any) -> None:
        """装配钩子(WS1,同 bind_user_channel 先例):给 TimerService 注入 fire 的帧消息注入通道。

        ctl 形态:``agent_os.api.v1.RunControl`` 实现(内核 ``RunControlImpl``);
        KernelBuilder 在 build 后(kernel/ctl 就位)调用。缺省(未 bind)=
        工具在场但按"timer 服务未装配"报结构化错误,行为与引入前一致。
        """
        self._timers.bind(ctl)

    def bind_monitor(self, ctl: Any) -> None:
        """装配钩子(ch04 Event-Triggered,同 bind_timer 先例):给 MonitorService 注入 fire 的帧消息注入通道。

        ctl 形态:``agent_os.api.v1.RunControl`` 实现(内核 ``RunControlImpl``);
        KernelBuilder 在 build 后(kernel/ctl 就位)调用。缺省(未 bind)=
        工具在场但按"monitor 服务未装配"报结构化错误,行为与引入前一致。
        """
        self._monitors.bind(ctl)

    def bind_schedule(self, service: Any) -> None:
        """装配钩子(D3,同 bind_timer 先例):注入 store-backed 调度服务,作
        ``system.schedule.set`` 的登记面(§17-3 宿主调度器的工具喂入源)。

        service 形态:带 ``set(*, skill, input, fire_at, note, via_run_id, principal)``
        方法的宿主对象(web 宿主 = host/shared/scheduler.py 的
        ``StoreScheduleService``,``[schedule]`` 段启用时由
        ``RunManager.start_scheduler`` 装配)。缺省(未 bind)= 工具在场但按
        "schedule 服务未装配"报结构化错误,行为与引入前一致。
        """
        self._schedule = service

    def bind_blob(self, store: Any) -> None:
        """装配钩子(M3,同 bind_memory 先例):替换 spill 的 blob store(如文件版 FileBlobStore)。

        缺省 = 进程内 InMemoryBlobStore(run 结束即弃,§8.4)。
        """
        self._blob = store

    def bind_credentials(self, resolver: Any) -> None:
        """装配钩子(WS1,同 bind_signals/bind_skills 先例):注入凭证作用域解析器。

        resolver 签名 ``(principal, declared_keys) -> dict[str, str]``;
        ``principal`` 为 D3 per-principal 凭证留协议面,v1 实现可忽略。
        缺省(未 bind)= 空作用域:dispatch 恒注入空 ``credentials``,行为与引入前一致。
        """
        self._credentials_resolver = resolver

    async def dispatch(self, call: ToolCall, frame_ctx: ToolDispatchContext) -> ToolResult:
        """§8.1 分发流水线(本切片实现到超时执行为止;信号由内核 runner 收发):

        schema 校验(fail fast,禁止"智能纠正")→ 数据层 authZ(docs/DATA-AUTHZ.md §3.3,
        未配置不拦截)→ 三层权限(帧白名单 ∩ RunConfig 上限;
        工具自报等级随 spec;§W0-1 起 READ 档不占帧白名单)→ 构造 ToolContext
        (§W0-1 workdir/read_paths 分区注入;principal 随帧透传;
        WS1 按 spec.credentials 声明键注入凭证)→ ``wait_for`` 超时执行 → 结果归一化。
        """
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.NOT_FOUND,
                    message=f"未注册的工具: {call.name}",
                    retryable=False,
                ),
            )
        spec = tool.spec
        try:
            jsonschema.validate(call.args, spec.parameters)
        except jsonschema.ValidationError as e:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INVALID_ARGS,
                    message=f"工具 {call.name} 参数不合 schema: {e.message}",
                    retryable=False,
                ),
            )
        # 数据层 authZ(docs/DATA-AUTHZ.md §5.2 双闸串联):在三层权限交集**之前**——
        # 数据闸管"碰不碰得到",权限闸管"允不允许",各自独立失败
        denied, authz = await self._check_data_access(call, spec, frame_ctx)
        if denied is not None:
            return denied
        # §W0-1:READ 档工具不占帧白名单(只读无副作用,fs 边界由 resolve_work_path 分区保证);
        # 帧白名单约束 WRITE 及以上档。内核 runner 在分发前另有 manifest 白名单闸门(§8.2),
        # 此豁免只影响直接使用 registry 的嵌入方,生产路径权限语义不变。
        if (
            spec.permission > Permission.READ
            and call.name not in frame_ctx.allowed_tools
        ) or spec.permission > frame_ctx.tool_policy.max_permission:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.PERMISSION_DENIED,
                    message=(
                        f"工具 {call.name}(权限 {spec.permission.name})越权:不在帧白名单, "
                        f"或超过 RunConfig 上限 {frame_ctx.tool_policy.max_permission.name}"
                    ),
                    retryable=False,
                    hint=(
                        f"该工具不在本技能白名单;可用的有 "
                        f"{', '.join(frame_ctx.allowed_tools) or '(空)'}"
                        f"(READ 档工具不受白名单限制)"
                    ),
                ),
            )
        # §W1-3 回放:replayable 工具在 replay 模式按调用序弹出记录值返回,不执行
        # (记录源是 host trace,接线留后续里程碑;无记录或记录耗尽 → 正常执行)。
        # 位置在 schema 校验与三层权限之后:回放不放宽任何安全闸门,只替换"执行"这一步
        records = frame_ctx.replay_records
        if spec.replayable and records is not None:
            queue = records.get(call.name)
            if queue:
                return ToolResult(ok=True, value=queue.pop(0))
        workdir = (
            str(frame_ctx.workdir.expanduser().resolve())
            if frame_ctx.workdir is not None
            else self._workdir(frame_ctx.frame.run_id)
        )
        # WS1 凭证注入:按 spec.credentials 声明键从作用域现解析(resolver 每次
        # 现读 env,防 token 过期);只注入声明的键,作用域缺该键/env 缺席 → 键不出现;
        # 未声明或未 bind resolver → 空 dict(零破坏)。泄露纪律:值只进 ToolContext,
        # 不进帧上下文/checkpoint/telemetry;工具错误消息只带键名,不回显值
        credentials = (
            self._credentials_resolver(frame_ctx.frame.principal, spec.credentials)
            if self._credentials_resolver is not None and spec.credentials
            else {}
        )
        if authz is not None:
            # D2 判据回写(docs/DATA-AUTHZ.md §3.2):authZ 判定结果(判定的域/敏感度/
            # 放行与否)随 credentials 给工具自省;"_" 前缀命名空间与 WS1 用户凭证键防撞名
            credentials = {**credentials, "_authz": authz}
        ctx = ToolContext(
            run_id=frame_ctx.frame.run_id,
            frame_id=frame_ctx.frame.frame_id,
            principal=frame_ctx.frame.principal,  # docs/DATA-AUTHZ.md §2.3:身份随帧透传(预留变实填)
            workdir=workdir,
            read_paths=[str(p.expanduser().resolve()) for p in frame_ctx.read_paths],
            blob=self._blob,
            credentials=credentials,
        )
        try:
            result = await asyncio.wait_for(tool(call.args, ctx), timeout=spec.timeout)
        except TimeoutError:
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.TIMEOUT,
                    message=f"工具 {call.name} 执行超过 {spec.timeout}s",
                    retryable=True,
                ),
            )
        except asyncio.CancelledError:
            raise  # 取消传播,占位配对由内核 runner 负责(§3.1/§7.4)
        except Exception as e:  # noqa: BLE001 — 分发边界故意兜底:工具异常归一化为结构化错误(§8.1)
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INTERNAL,
                    message=f"{type(e).__name__}: {e}",
                    retryable=False,
                    hint=traceback.format_exc()[-500:],
                ),
            )
        if isinstance(result, ToolResult):
            return result
        return ToolResult(ok=True, value=result)

    def register_fs_domain(self, domain: DataDomain, path_prefix: str | Path) -> None:
        """注册 fs 数据域边界(宿主 API;agent-os.toml ``[data]`` 配置段接线见
        runtime/config.py ``build_kernel``,D2)。

        最长前缀优先(注册即排序),嵌套域(如 fs.shared ⊂ workdir)按更具体者判。
        """
        self._fs_domains.append((Path(path_prefix).expanduser().resolve(), domain))
        self._fs_domains.sort(key=lambda item: len(str(item[0])), reverse=True)

    def register_net_domain(self, domain: DataDomain, url_prefix: str) -> None:
        """注册 net 数据域边界(D2;URL 前缀,最长前缀优先,同 :meth:`register_fs_domain`)。"""
        self._net_domains.append((url_prefix, domain))
        self._net_domains.sort(key=lambda item: len(item[0]), reverse=True)

    def sensitive_domains(self, declared: Iterable[str]) -> list[str]:
        """声明数据域中按 [data] policy 判 confidential 的子集(保序;确认卡片
        "敏感"标注数据源,升权/tool-confirm 请求共用)。

        与 ``_check_data_access`` 共用同一份解析(``_resolve_declared_domains``,
        不另写判定):模式命中的已注册域含 confidential,或声明整体落空按 §3.3
        合成 confidential 占位 → 该模式敏感。policy 未 bind(D1 语义,数据层
        未启用)→ 恒 []。
        """
        policy = self._data_policy
        if policy is None:
            return []
        patterns = [str(d) for d in declared]
        if not patterns:
            return []
        conf = [
            domain
            for domain in _resolve_declared_domains(policy, patterns)
            if domain.sensitivity == CONFIDENTIAL
        ]
        return [p for p in patterns if any(fnmatch.fnmatchcase(d.name, p) for d in conf)]

    def _resolve_fs_domain(self, path: Path, workdir: Path) -> DataDomain | None:
        """路径 → 数据域:宿主注册域(已按最长前缀排序)优先;其后内置默认域
        ``fs.workdir``(public,D1 内置);都不沾 → None(未配置,D1 不拦截)。"""
        for prefix, domain in self._fs_domains:
            if path.is_relative_to(prefix):
                return domain
        if path.is_relative_to(workdir):
            return DataDomain(name="fs.workdir", sensitivity=PUBLIC)
        return None

    def _resolve_net_domain(self, url: str) -> DataDomain | None:
        """URL → 数据域(D2):宿主注册域按最长前缀匹配;未命中 → None。"""
        for prefix, domain in self._net_domains:
            if url.startswith(prefix):
                return domain
        return None

    async def _emit_data_signal(
        self, name: str, frame_ctx: ToolDispatchContext, payload: dict[str, Any]
    ) -> None:
        """数据层审计信号(docs/DATA-AUTHZ.md §6;D2):总线未装配(未走 bind_signals)跳过。"""
        bus = self._signals_bus
        if bus is None:
            return
        await bus.emit(
            Signal(
                name=name,
                run_id=frame_ctx.frame.run_id,
                frame_id=frame_ctx.frame.frame_id,
                payload=payload,
            )
        )

    async def _check_data_access(
        self, call: ToolCall, spec: ToolSpec, frame_ctx: ToolDispatchContext
    ) -> tuple[ToolResult | None, dict[str, Any] | None]:
        """数据层 authZ(docs/DATA-AUTHZ.md §3.3):``(拒绝 ToolResult, None)`` 或
        ``(None, 判据回写 dict)``;检查不触及本调用 → ``(None, None)``。

        D1 兼容策略(§8 D1 实现注;``_data_policy`` 未 bind 时逐字保持):工具未声明
        ``data_domains``、principal 未注入(v1 单用户语义)、目标落不进任何已配置域
        ——三种情况都不拦截,行为与引入本系统前完全一致("未配置 = 不启用数据层拦截");
        **默认拒绝只作用于已配置域**。D2(``bind_data_policy`` 注入 [data] 策略后)
        恢复原文语义:**解析失败/未命中的域按 confidential**(§3.1/§3.3),且
        per-subject 域白名单并入 ``allow()`` 第二判据(§3.2,clearance 够但域不在
        白名单同样拒绝)。拒绝消息只带域名/敏感度/clearance,不回显路径/URL 与
        域内内容(不泄漏);放行/拒绝各发一条审计信号(§6,总线未装配则跳过)。
        D3(WS1):``principal.attrs["via"]`` 派生链在场时按最弱一环判定——
        链上每一环(含当前 principal)都过 ``allow`` 才放行,被拒环在消息与
        ``data.access.denied`` payload(``via_link`` 键,additive)里注明;
        链形状坏/深度超限 fail-closed 拒绝(payload 加 ``via_error`` 键)。
        """
        if not spec.data_domains:
            return None, None
        principal = frame_ctx.frame.principal
        if principal is None:
            return None, None
        policy = self._data_policy
        # 按声明域族分派目标解析:fs.* 走路径前缀,net.* 走 URL 前缀;其余族
        # (db.* 等,D2 机制就位)在 policy 在场时按声明模式匹配已注册域,过同一 allow 判定
        domains: list[DataDomain] = []
        if "fs.*" in spec.data_domains:
            raw = call.args.get("path")
            if not isinstance(raw, str):
                return None, None  # 无路径参数可解析(D1:按未配置语义,不拦截)
            workdir = (
                str(frame_ctx.workdir.expanduser().resolve())
                if frame_ctx.workdir is not None
                else self._workdir(frame_ctx.frame.run_id)
            )
            resolved = resolve_work_path(
                workdir, [str(p.expanduser().resolve()) for p in frame_ctx.read_paths], raw
            )
            if isinstance(resolved, ToolResult):
                return None, None  # 越界路径由工具自身按沙箱语义报错(resolve_work_path),数据层不重复判
            domain = self._resolve_fs_domain(resolved, Path(workdir))
            if domain is None:
                if policy is None:
                    return None, None  # D1:未配置域不拦截
                # D2(§3.1):未配置域按 confidential——忘了配 = 最严,不是最松
                domain = DataDomain(name="fs.unconfigured", sensitivity=CONFIDENTIAL)
            domains.append(domain)
        if "net.*" in spec.data_domains and policy is not None:
            url = call.args.get("url")
            if not isinstance(url, str):
                return None, None  # 无 URL 参数可解析(同 fs,不拦截)
            domain = self._resolve_net_domain(url)
            if domain is None:
                # D2(§3.1):未命中已配置域按 confidential——忘了配 = 最严,不是最松
                domain = DataDomain(name="net.unconfigured", sensitivity=CONFIDENTIAL)
            domains.append(domain)
        # policy 缺席时 net 声明不判(D1 逐字:net 判定属 D2,net 工具行为与引入前一致)
        declared_rest = [d for d in spec.data_domains if d not in ("fs.*", "net.*")]
        if declared_rest and policy is not None:
            domains.extend(_resolve_declared_domains(policy, declared_rest))
        # policy 缺席时其余族声明同样不判(D1 只判 fs.*;skills.*/drafts.* 等平台声明维持现状)
        if not domains:
            return None, None
        # D3 派生链最弱一环(docs/DATA-AUTHZ.md;WS1):attrs["via"] 在场时,
        # 链上每一环(含当前 principal)都要过 allow 判定;无 via → chain 恰为
        # [principal],判定与信号 payload 逐字保持 D1/D2 现状
        chain, via_error = _expand_via_chain(principal)
        if via_error is not None:
            # fail-closed:宿主声明的链非法(形状坏/深度超限)等于身份没说清,
            # 与 clearance 缺省按最低档同旨——拒,理由只带原因不带域内内容
            await self._emit_data_signal(
                DATA_ACCESS_DENIED,
                frame_ctx,
                {
                    "subject": principal.subject,
                    "domain": domains[0].name,
                    "sensitivity": domains[0].sensitivity,
                    "tool": call.name,
                    "via_error": via_error,  # additive:链非法原因(审计面板定位用)
                },
            )
            return (
                ToolResult(
                    ok=False,
                    error=ToolError(
                        kind=ToolErrorKind.DATA_ACCESS_DENIED,
                        message=(
                            f"via 派生链非法({via_error}),fail-closed 拒绝 "
                            f"{principal.subject} 访问数据域 {domains[0].name}"
                        ),
                        retryable=False,
                        hint="数据层 authZ 拒绝:via 链须为 [{subject, issuer, attrs}] 列表,"
                        "深度不超过 8 层(本消息不含域内任何内容)",
                    ),
                ),
                None,
            )
        via_mode = len(chain) > 1
        chain_whitelists = (
            {link.subject: policy.whitelist_for(link.subject) for link in chain}
            if policy is not None
            else {}
        )
        for domain in domains:
            for link_index, link in enumerate(chain):
                if allow(link, domain, whitelist=chain_whitelists.get(link.subject)):
                    continue
                payload: dict[str, Any] = {
                    "subject": principal.subject,
                    "domain": domain.name,
                    "sensitivity": domain.sensitivity,
                    "tool": call.name,
                }
                suffix = ""
                if via_mode:
                    # additive:加注被拒环节(链下标 + 该环 subject;#0 = 当前 principal)
                    payload["via_link"] = {"index": link_index, "subject": link.subject}
                    suffix = f"(via 派生链环 #{link_index})"
                await self._emit_data_signal(DATA_ACCESS_DENIED, frame_ctx, payload)
                return (
                    ToolResult(
                        ok=False,
                        error=ToolError(
                            kind=ToolErrorKind.DATA_ACCESS_DENIED,
                            message=(
                                f"数据域 {domain.name}(敏感度 {domain.sensitivity})拒绝 "
                                f"{link.subject}(clearance {clearance_of(link)})访问{suffix}"
                            ),
                            retryable=False,
                            hint="数据层 authZ 拒绝:需要更高 clearance 的 principal(本消息不含域内任何内容)",
                        ),
                    ),
                    None,
                )
        await self._emit_data_signal(
            DATA_ACCESS_GRANTED,
            frame_ctx,
            {
                "subject": principal.subject,
                "domains": [d.name for d in domains],
                "tool": call.name,
            },
        )
        record = {
            "allowed": True,
            "domains": [{"name": d.name, "sensitivity": d.sensitivity} for d in domains],
        }
        return None, record

    def release_run(self, run_id: str) -> None:
        """run 收尾:删掉该 run 的临时工作目录并忘掉登记。

        不删的话 ``mkdtemp`` 的结果只增不减——长驻宿主会把 /tmp 塞满
        (审计发现:全仓原先无任何 rmtree)。已配置 workdir(§W0-1 分区)时
        不属本注册表所有,不动。WS1:顺带取消该 run 全部在册计时器(进程任务
        取消;规格已随帧 working 持久化,resume 重武装,见 tools/timer.py)。
        ch04:顺带取消该 run 全部在册监控(shell 源进程组同步 SIGKILL;
        规格同样留帧 working 供 resume 重武装,见 tools/monitor.py)。
        K2:顺带回收该 run 的进程态工具状态(``_proc_states``;逻辑状态
        ``_run_states`` 不在此清——收尾 checkpoint 快照在回收后才落盘)。
        """
        self._timers.release_run(run_id)
        self._monitors.release_run(run_id)
        self._proc_states.pop(run_id, None)
        wd = self._workdirs.pop(run_id, None)
        if wd:
            shutil.rmtree(wd, ignore_errors=True)

    def _workdir(self, run_id: str) -> str:
        """每 run 一个临时工作目录(限定 fs 工具范围,§2.2;§W0-1 缺省档,配置 workdir 时不走这里)。"""
        wd = self._workdirs.get(run_id)
        if wd is None:
            wd = tempfile.mkdtemp(prefix=f"agent_os-run-{run_id[:8]}-")
            self._workdirs[run_id] = wd
        return wd

    @classmethod
    def with_builtins(
        cls, http_transport: httpx.AsyncBaseTransport | None = None
    ) -> LocalPythonToolRegistry:
        """§14.2 组装示例入口:注册 §8.3 内置工具(system.file.read/system.file.write/system.file.edit/system.shell.exec/system.net.http_fetch)
        与 §W1 核心工具(system.file.list/system.file.search/system.time.now/system.task.todo_write/system.task.todo_update/system.skill.search)。

        何时用:单技能 agent 起步与测试的默认工具面;边界:fs 工具限定 run 工作目录(§2.2;
        §W0-1 起可配 workdir/read_paths 分区),system.shell.exec 缺省为一次性子进程(K2 起
        可选 ``session_id`` 会话化:同 (run, session) 跨调用保 cwd/env 增量,状态存进程态
        载体 ``proc_states``,不进 checkpoint,见模块 docstring 两类生命周期段),
        system.net.http_fetch 结果标记 ``untrusted_source``(§2.2 来源标记,注入防御);
        ``http_transport`` 供测试注入 httpx MockTransport,不碰真实网络。
        §W0-2:READ 档 system.file.read 声明 idempotent/cacheable/concurrent_safe(两个拼写一并置位),
        各工具 cost_hint 写量级(声明不强制)。
        §W1(STDLIB-CATALOG):system.time.now 声明 ``replayable``(replay 语义见 dispatch);system.task.todo_* 双工具
        持 run 级状态(``run_states``);system.skill.search 的 skills 数据源由 KernelBuilder
        经 ``bind_skills`` 注入(未装配时只检索工具面);M6:system.memory.search/system.memory.write
        的 MemoryService 经 ``bind_memory`` 注入(未装配时调用报"未配置 memory 子系统"结构化错误);
        WS-C:system.skill.register(§6.2 运行期注册,WRITE·confirm=True)的目标 skills registry
        同样经 ``bind_skills`` 注入(未 bind 或不支持 register 时调用报"未装配"结构化错误)。
        M1:system.user.ask/system.user.notify(§8.3 User Communication)的宿主回调经
        ``bind_user_channel`` 注入(未 bind 调用报"user 通道未装配"结构化错误,同 memory 先例)。
        WS1:system.timer.set(§8.3 扩展,一次性/周期计时器)的 fire 注入通道经
        ``bind_timer`` 注入(未 bind 调用报"timer 服务未装配"结构化错误,同 user 通道先例)。
        D3(§17-3):system.schedule.set(跨 run 自动派生,WRITE·confirm=True)的调度
        服务经 ``bind_schedule`` 注入(未 bind 调用报"schedule 服务未装配"结构化错误)。
        ch04(Event-Triggered):system.monitor.set/system.channel.connect(后台输出
        监控/事件文件接入,共用 MonitorService)的 fire 注入通道经 ``bind_monitor``
        注入(未 bind 调用报"monitor 服务未装配"结构化错误,同 timer 先例)。
        这四个工具的**注册点在构造器**(不在本方法):std 域文件声明了 ask/timer 的
        permissions.tools,§6.1 闸门联动要求空工具表也能装配(同 fetch_page 先例,
        见 __init__ 注释);notify/schedule.set 挂同一注册点与各自的服务绑定先例
        对齐;本方法拿到的是构造器已装好的面,不再重复注册。
        Phase 3(library-design-plan §4.2/§4.4):system.file.stat(读/写决策前探查)/system.file.delete
        (高危,confirm=True,仅文件与空目录)/system.file.mkdir(parents/exist_ok,幂等)/
        system.net.http_request(非 GET 通用 HTTP,与 http_fetch 共用执行体);新工具无旧名,不设别名。
        """
        from agent_os.tools.builtins import (
            blob_get,
            fs_edit,
            fs_read,
            fs_write,
            http_fetch_tool,
            http_request_tool,
            shell_exec_tool,
        )
        from agent_os.tools.std import (
            fs_delete,
            fs_list,
            fs_mkdir,
            fs_search,
            fs_stat,
            memory_search_tool,
            memory_write_tool,
            now,
            skill_register_tool,
            skill_search_tool,
            todo_read_tool,
            todo_update_tool,
            todo_write_tool,
        )

        reg = cls()
        reg.tool(
            name="system.file.read",
            data_domains=["fs.*"],
            permission=Permission.READ,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~10ms",
        )(fs_read)
        reg.register_alias("fs_read", "system.file.read")
        reg.tool(
            name="system.file.write",
            data_domains=["fs.*"],
            permission=Permission.WRITE,
            cost_hint="~10ms",
        )(fs_write)
        reg.register_alias("fs_write", "system.file.write")
        reg.tool(
            name="system.file.edit",
            data_domains=["fs.*"],
            permission=Permission.WRITE,
            timeout=10.0,
            cost_hint="~10ms",
        )(fs_edit)
        reg.register_alias("fs_edit", "system.file.edit")
        # K2:session_id 会话化(cwd/env_delta 存 proc_states 进程态载体,
        # 见 tools/builtins.py shell_exec_tool 与本模块 docstring 两类生命周期段)
        reg.register(shell_exec_tool(name="system.shell.exec", proc_states=reg.proc_states))
        reg.register_alias("shell_exec", "system.shell.exec")
        reg.register(http_fetch_tool(name="system.net.http_fetch", transport=http_transport))
        reg.register_alias("http_fetch", "system.net.http_fetch")
        # spill 的读取端(§8.3):没有它,所有返回 spill_ref 的工具都是死胡同——
        # 模型被告知"用 blob_get 取全文"却调不到该工具
        reg.tool(
            name="system.blob.get",
            permission=Permission.READ,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~1ms(进程内 blob)",
        )(blob_get)
        reg.register_alias("blob_get", "system.blob.get")
        # —— §W1 核心工具(契约字段同 READ 档统一声明,门槛见 tests/test_std_gate.py)——
        reg.tool(
            name="system.file.list",
            data_domains=["fs.*"],
            permission=Permission.READ,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~20ms,取决于目录规模",
        )(fs_list)
        reg.register_alias("fs_list", "system.file.list")
        reg.tool(
            name="system.file.search",
            data_domains=["fs.*"],
            permission=Permission.READ,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            timeout=60.0,  # 大目录树 + 单文件 2s 正则超时兜底,默认 30s 可能偏紧
            cost_hint="~50ms 起,取决于树规模与正则",
        )(fs_search)
        reg.register_alias("fs_search", "system.file.search")
        # now 的 cacheable 是门槛统一声明(READ⇒cacheable);复现机制是 replayable,不是缓存
        reg.tool(
            name="system.time.now",
            permission=Permission.READ,
            replayable=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~1ms",
        )(now)
        reg.register_alias("now", "system.time.now")
        reg.register(todo_write_tool(name="system.task.todo_write", run_states=reg.run_states))
        reg.register_alias("todo_write", "system.task.todo_write")
        reg.register(todo_update_tool(name="system.task.todo_update", run_states=reg.run_states))
        reg.register_alias("todo_update", "system.task.todo_update")
        reg.register(todo_read_tool(name="system.task.todo_read", run_states=reg.run_states))
        reg.register_alias("todo_read", "system.task.todo_read")
        reg.register(skill_search_tool(name="system.skill.search", registry=reg))
        reg.register_alias("skill_search", "system.skill.search")
        # M6(§11.2):工具面常驻,MemoryService 由 KernelBuilder 经 bind_memory 注入
        # (未装配时调用报结构化错误,同 skill_search 未 bind 只检索工具面的先例)
        reg.register(memory_search_tool(name="system.memory.search", registry=reg))
        reg.register_alias("memory_search", "system.memory.search")
        reg.register(memory_write_tool(name="system.memory.write", registry=reg))
        reg.register_alias("memory_write", "system.memory.write")
        # §6.2(WS-C):工具面常驻,confirm=True 过内核 tool-confirm 闸门;目标 skills
        # registry 由 KernelBuilder 经 bind_skills 注入(与 skill_search 同一数据源;
        # 未 bind 或 registry 不支持 register 时调用报"未装配"结构化错误,同 memory
        # 工具先例);新工具无旧名,不设别名(Phase 3 先例)
        reg.register(skill_register_tool(name="system.skill.register", registry=reg))
        # M1(§8.3 User Communication)/WS1(timer):system.user.ask/system.user.notify/
        # system.timer.set 的注册点在**构造器**(§6.1 闸门联动,见 __init__ 注释与
        # with_builtins docstring);通道/服务仍由 KernelBuilder 经 bind_user_channel/
        # bind_timer 注入(未 bind 调用报"未装配"结构化错误);均无旧名,不设别名
        # —— Phase 3 补齐(library-design-plan §4.2/§4.4;新工具无旧名,不设别名)——
        reg.tool(
            name="system.file.stat",
            data_domains=["fs.*"],
            permission=Permission.READ,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~5ms",
        )(fs_stat)
        # 高危删除:声明 confirm(两阶段语义),WRITE 档受帧白名单约束;
        # TIER-STANDARDS §1 命名规则:delete/kill/stop/remove 类必须显式标
        # irreversible(WRITE 默认推导只是 reversible——删除不可挽回,属漏标)
        reg.tool(
            name="system.file.delete",
            data_domains=["fs.*"],
            permission=Permission.WRITE,
            side_effect="irreversible",
            confirm=True,
            cost_hint="~5ms",
        )(fs_delete)
        # exist_ok 语义天然幂等
        reg.tool(
            name="system.file.mkdir",
            data_domains=["fs.*"],
            permission=Permission.WRITE,
            idempotent=True,
            cost_hint="~5ms",
        )(fs_mkdir)
        reg.register(http_request_tool(name="system.net.http_request", transport=http_transport))
        return reg


_BASIC_TYPES: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _resolve_declared_domains(policy: Any, patterns: Iterable[str]) -> list[DataDomain]:
    """声明的数据域模式 → [data] 已注册域(fnmatch 逐模式展开;数据闸与确认卡片
    "敏感"判定共用的同一份解析,勿另写)。

    一个已注册域可被多个模式命中;**全部模式都落空**时按 §3.3"解析失败 =
    confidential"为每个模式合成 confidential 占位域(忘了配 = 最严)。
    """
    patterns = list(patterns)
    matched = [
        domain
        for pattern in patterns
        for domain in policy.domains.values()
        if fnmatch.fnmatchcase(domain.name, pattern)
    ]
    return matched or [DataDomain(name=p, sensitivity=CONFIDENTIAL) for p in patterns]


#: via 派生链递归深度上限(D3 防呆;超限 fail-closed 拒绝,兼作环状链的终止兜底)
_VIA_MAX_DEPTH = 8


def _expand_via_chain(principal: Principal) -> tuple[list[Principal], str | None]:
    """展开 ``principal.attrs["via"]`` 派生链(D3,docs/DATA-AUTHZ.md;WS1)。

    约定:``attrs["via"]`` = 上游 principal dict 列表(**近端在前**,每元素
    ``{subject, issuer, attrs}``,attrs 递归可再含 via)——引擎不伪造链,
    宿主声明。返回 ``([当前 principal, *上游环...], None)``;无 via(键缺席/
    None/空表)→ ``([principal], None)``,调用方按现状语义只判当前 principal。

    fail-closed:via 值非 list、元素非 dict、缺 subject(或空串)、attrs 非 dict、
    嵌套深度超 ``_VIA_MAX_DEPTH`` → ``([], 原因)``,调用方拒绝访问(宿主声明的
    链非法 = 身份没说清,与 clearance 缺省按最低档同旨)。

    类型注:``Principal.attrs`` 声明为 ``Mapping[str, str]``,``via`` 承载结构化
    列表是约定性 deviation(WS1);checkpoint ``dataclasses.asdict`` 与
    ``Principal(**raw)`` 重建对纯 dict/list 载荷天然往返,不放大身份。
    """
    chain = [principal]
    if not (principal.attrs or {}).get("via"):
        return chain, None

    def _walk(node: Principal, depth: int) -> str | None:
        raw = (node.attrs or {}).get("via")
        if not raw:
            return None
        # 节点确有上游要展开才计深度(恰 8 层且底层无 via 的链合法,第 9 层才拒)
        if depth > _VIA_MAX_DEPTH:
            return f"嵌套深度超过 {_VIA_MAX_DEPTH} 层"
        if not isinstance(raw, (list, tuple)):
            return f"via 应为列表,得到 {type(raw).__name__}"
        for element in raw:
            if not isinstance(element, dict):
                return f"via 元素应为 dict,得到 {type(element).__name__}"
            subject = element.get("subject")
            if not isinstance(subject, str) or not subject:
                return "via 元素缺 subject(或非空字符串)"
            attrs = element.get("attrs") or {}
            if not isinstance(attrs, dict):
                return f"via 元素 attrs 应为 dict,得到 {type(attrs).__name__}"
            # 照 api/v1/principal.py 字段还原为 Principal 再递归判定
            link = Principal(
                subject=subject,
                issuer=str(element.get("issuer") or ""),
                attrs=dict(attrs),
            )
            chain.append(link)
            error = _walk(link, depth + 1)
            if error is not None:
                return error
        return None

    error = _walk(principal, 1)
    if error is not None:
        return [], error
    return chain, None


def resolve_work_path(
    workdir: str | Path,
    read_paths: Iterable[str | Path] = (),
    path: str = ".",
    *,
    write: bool = False,
) -> Path | ToolResult:
    """§W0-1 统一路径解析器(system.file.read/system.file.write/system.file.edit/system.shell.exec 共用),三段判定:

    ①路径在任一 read_path 下 → 只读区:读允许(**可在 workdir 之外**),
    ``write=True`` 拒绝(INVALID_ARGS);②路径在 workdir 下 → 读写;
    ③其他 → INVALID_ARGS(§2.2 逃逸检查保留)。hint 带运行期事实(现取,不硬编码)。
    """
    base = Path(workdir).resolve()
    target = Path(base, path).resolve()  # path 为绝对路径时 joinpath 自动丢弃 base
    for ro in read_paths:
        ro_resolved = Path(ro).resolve()
        if target.is_relative_to(ro_resolved):
            if not write:
                return target
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.INVALID_ARGS,
                    message=f"路径位于只读区,拒绝写入: {path}",
                    retryable=False,
                    hint=f"只读区({ro_resolved})来自 read_paths,不可写;产出请写入 workdir({base})内路径",
                ),
            )
    if target.is_relative_to(base):
        return target
    zones = f"workdir({base})" + (
        f" 或 read_paths({', '.join(str(Path(p).resolve()) for p in read_paths)})"
        if read_paths
        else ""
    )
    return ToolResult(
        ok=False,
        error=ToolError(
            kind=ToolErrorKind.INVALID_ARGS,
            message=f"路径越界: {path}(fs 工具限定在工作目录内)",
            retryable=False,
            hint=f"路径须在 {zones} 内;用相对路径或先确认目录结构",
        ),
    )


def _json_schema(annotation: Any) -> dict[str, Any]:
    """类型注解 → JSON Schema(§8.4 推导规则)。"""
    if annotation in _BASIC_TYPES:
        return {"type": _BASIC_TYPES[annotation]}
    origin = typing.get_origin(annotation)
    if origin is list:
        args = typing.get_args(annotation)
        return {"type": "array", "items": _json_schema(args[0]) if args else {}}
    if origin is dict:
        return {"type": "object"}
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return _json_schema(non_none[0])
    return {}


def derive_spec(func: Callable, *, name: str | None = None, permission: Permission, timeout: float, **spec_kw: Any) -> ToolSpec:
    """从函数签名推导 ToolSpec(§8.4 推导规则)。

    约定:名为 ``ctx`` 的参数视为 ``ToolContext`` 注入点,不进 schema(见 ``_FunctionTool``)。
    """
    hints = typing.get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in inspect.signature(func).parameters.items():
        if param_name == "ctx":
            continue
        properties[param_name] = _json_schema(hints.get(param_name, str))
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    # 整段 docstring → description(§8.4/§8.1):首段是"做什么",而 "Use when /
    # Do not use when" 与错误语义写在后续段落——只取首段会把路由指引丢掉,
    # 模型据以选工具的正是后者(书 Ch4:选错工具时先查工具描述)。
    # 描述进静态前缀,长一点不破 KV cache(§7.4 不变量 5)。
    description = inspect.getdoc(func) or ""
    return ToolSpec(
        name=name or func.__name__,
        description=description,
        parameters={"type": "object", "properties": properties, "required": required},
        permission=permission,
        timeout=timeout,
        **spec_kw,
    )
