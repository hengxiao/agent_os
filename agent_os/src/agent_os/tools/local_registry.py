"""LocalPythonToolRegistry(DESIGN.md §8.4;M1;STDLIB-CATALOG §W0-1 workdir 分区)。

函数即工具:decorator 注册,schema 从签名推导(str/int/float/bool → JSON 基本型;
list[X]/dict[str, X] → array/object;有默认值 → 非 required;docstring 首段 → description)。
sync 函数包 ``asyncio.to_thread``;§8.1 分发流水线全量实现(契约本体,不是可简化项)。

§W0-1:``ToolDispatchContext.workdir``/``read_paths`` 把"每 run 临时目录"升级为可配置
三分区(read_paths 只读 / workdir 读写 / 缺省临时目录);``resolve_work_path`` 是
fs_read/fs_write/fs_edit/shell_exec 共用的统一路径解析器(三段判定,逃逸检查保留)。

§W1-3 replayable 回放:``ToolDispatchContext.replay_records`` 有记录时,``spec.replayable``
工具按调用序弹出记录值返回而不执行(host replay 的接线留后续里程碑);§W1-4:
``_run_states`` 是 run 级工具状态表(todo 清单等,同 run_id 跨帧共享,checkpoint
随档持久见 kernel.checkpoint);§W1-5:``bind_skills`` 注入 SkillRegistry 引用,
作 skill_search 的技能数据源(装配钩子,同 bind_signals 先例)。
"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
import traceback
import types
import typing
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

import jsonschema

from agent_os.api.v1 import (
    Permission,
    SkillFrame,
    Tool,
    ToolCall,
    ToolContext,
    ToolError,
    ToolErrorKind,
    ToolPolicy,
    ToolResult,
    ToolSpec,
)
from agent_os.tools.blob import InMemoryBlobStore


@dataclass
class ToolDispatchContext:
    """``dispatch`` 的帧侧上下文:§8.2 三层权限交集中帧白名单与 RunConfig 上限由内核传入。

    §W0-1 additive:``workdir``/``read_paths`` 由 runner 从 RunConfig 传入;
    缺省 ``None`` → 保持现状(每 run 临时目录,安全边界不静默放宽)。
    §W1-3 additive:``replay_records`` 由 host replay 注入(trace 记录值);
    缺省 ``None`` → 正常执行(锚点直接构造 ctx 验证回放语义)。
    """

    frame: SkillFrame
    allowed_tools: list[str] = field(default_factory=list)  # 帧 manifest.tools 白名单
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)  # RunConfig 全局上限
    workdir: Path | None = None  # §W0-1 run 工作目录(fs/shell 共用解析点)
    read_paths: list[Path] = field(default_factory=list)  # §W0-1 只读挂载(可在 workdir 之外)
    replay_records: dict[str, list] | None = None  # §W1-3 replayable 工具按调用序弹出的记录值


class _FunctionTool:
    """decorator 产物:把普通 Python 函数包装成 ``agent_os.api.v1.Tool``。

    约定:签名中名为 ``ctx`` 的参数由分发方注入 ``ToolContext``(不进 schema);
    函数返回 ``ToolResult`` 时直接透传(工具自行构造结构化错误),否则包成 ``ok=True``。
    """

    def __init__(self, func: Callable, spec: ToolSpec) -> None:
        self.spec = spec
        self._func = func
        self._wants_ctx = "ctx" in inspect.signature(func).parameters

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
        #: §W1-5 skill_search 的技能数据源(KernelBuilder 装配时经 bind_skills 注入)
        self._skills: Any = None

    def tool(
        self, *, permission: Permission = Permission.READ, timeout: float = 30.0, **spec_kw: Any
    ) -> Callable:
        """§8.4 decorator:``@registry.tool(permission=Permission.NET, timeout=30)``。

        ``spec_kw`` 透传 ToolSpec 预留字段(examples/cacheable/confirm/concurrency_safe 等)。
        """

        def decorator(func: Callable) -> Callable:
            spec = derive_spec(func, permission=permission, timeout=timeout, **spec_kw)
            self.register(_FunctionTool(func, spec))
            return func

        return decorator

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def specs(self) -> list[ToolSpec]:
        """全部已注册工具的 ToolSpec(注册序;WEB-UI.md §6.2 Tools 浏览器数据源)。"""
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
        """run 级工具状态表(§W1-4;key = run_id,同 run 的帧共享;checkpoint 随档持久)。"""
        return self._run_states

    def bind_signals(self, bus: Any) -> None:
        """KernelBuilder 装配钩子:给提供 ``bind()`` 的工具(如 python_exec)接信号总线。"""
        for tool in self._tools.values():
            bind = getattr(tool, "bind", None)
            if callable(bind):
                bind(bus)

    def bind_skills(self, skills: Any) -> None:
        """KernelBuilder 装配钩子(§W1-5):注入 SkillRegistry 引用,作 skill_search 数据源。"""
        self._skills = skills

    async def dispatch(self, call: ToolCall, frame_ctx: ToolDispatchContext) -> ToolResult:
        """§8.1 分发流水线(本切片实现到超时执行为止;信号由内核 runner 收发):

        schema 校验(fail fast,禁止"智能纠正")→ 三层权限(帧白名单 ∩ RunConfig 上限;
        工具自报等级随 spec;§W0-1 起 READ 档不占帧白名单)→ 构造 ToolContext
        (§W0-1 workdir/read_paths 分区注入)→ ``wait_for`` 超时执行 → 结果归一化。
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
        ctx = ToolContext(
            run_id=frame_ctx.frame.run_id,
            frame_id=frame_ctx.frame.frame_id,
            workdir=workdir,
            read_paths=[str(p.expanduser().resolve()) for p in frame_ctx.read_paths],
            blob=self._blob,
            credentials={},
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
        """§14.2 组装示例入口:注册 §8.3 内置工具(fs_read/fs_write/fs_edit/shell_exec/http_fetch)
        与 §W1 核心工具(fs_list/fs_search/now/todo_write/todo_update/skill_search)。

        何时用:单技能 agent 起步与测试的默认工具面;边界:fs 工具限定 run 工作目录(§2.2;
        §W0-1 起可配 workdir/read_paths 分区),shell_exec 为一次性子进程(持久会话形态
        后续里程碑),http_fetch 结果标记 ``untrusted_source``(§2.2 来源标记,注入防御);
        ``http_transport`` 供测试注入 httpx MockTransport,不碰真实网络。
        §W0-2:READ 档 fs_read 声明 idempotent/cacheable/concurrent_safe(两个拼写一并置位),
        各工具 cost_hint 写量级(声明不强制)。
        §W1(STDLIB-CATALOG):now 声明 ``replayable``(replay 语义见 dispatch);todo 双工具
        持 run 级状态(``run_states``);skill_search 的 skills 数据源由 KernelBuilder
        经 ``bind_skills`` 注入(未装配时只检索工具面)。
        """
        from agent_os.tools.builtins import (
            fs_edit,
            fs_read,
            fs_write,
            http_fetch_tool,
            shell_exec,
        )
        from agent_os.tools.std import (
            fs_list,
            fs_search,
            now,
            skill_search_tool,
            todo_update_tool,
            todo_write_tool,
        )

        reg = cls()
        reg.tool(
            permission=Permission.READ,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~10ms",
        )(fs_read)
        reg.tool(permission=Permission.WRITE, cost_hint="~10ms")(fs_write)
        reg.tool(permission=Permission.WRITE, timeout=10.0, cost_hint="~10ms")(fs_edit)
        reg.tool(permission=Permission.EXEC, cost_hint="~100ms 起,取决于命令")(shell_exec)
        reg.register(http_fetch_tool(transport=http_transport))
        # —— §W1 核心工具(契约字段同 READ 档统一声明,门槛见 tests/test_std_gate.py)——
        reg.tool(
            permission=Permission.READ,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~20ms,取决于目录规模",
        )(fs_list)
        reg.tool(
            permission=Permission.READ,
            idempotent=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            timeout=60.0,  # 大目录树 + 单文件 2s 正则超时兜底,默认 30s 可能偏紧
            cost_hint="~50ms 起,取决于树规模与正则",
        )(fs_search)
        # now 的 cacheable 是门槛统一声明(READ⇒cacheable);复现机制是 replayable,不是缓存
        reg.tool(
            permission=Permission.READ,
            replayable=True,
            cacheable=True,
            concurrent_safe=True,
            concurrency_safe=True,
            cost_hint="~1ms",
        )(now)
        reg.register(todo_write_tool(reg.run_states))
        reg.register(todo_update_tool(reg.run_states))
        reg.register(skill_search_tool(reg))
        return reg


_BASIC_TYPES: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


def resolve_work_path(
    workdir: str | Path,
    read_paths: Iterable[str | Path] = (),
    path: str = ".",
    *,
    write: bool = False,
) -> Path | ToolResult:
    """§W0-1 统一路径解析器(fs_read/fs_write/fs_edit/shell_exec 共用),三段判定:

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


def derive_spec(func: Callable, *, permission: Permission, timeout: float, **spec_kw: Any) -> ToolSpec:
    """从函数签名推导 ToolSpec(§8.4 推导规则)。

    约定:名为 ``ctx`` 的参数视为 ``ToolContext`` 注入点,不进 schema(见 ``_FunctionTool``)。
    """
    hints = typing.get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(func).parameters.items():
        if name == "ctx":
            continue
        properties[name] = _json_schema(hints.get(name, str))
        if param.default is inspect.Parameter.empty:
            required.append(name)
    # 整段 docstring → description(§8.4/§8.1):首段是"做什么",而 "Use when /
    # Do not use when" 与错误语义写在后续段落——只取首段会把路由指引丢掉,
    # 模型据以选工具的正是后者(书 Ch4:选错工具时先查工具描述)。
    # 描述进静态前缀,长一点不破 KV cache(§7.4 不变量 5)。
    description = inspect.getdoc(func) or ""
    return ToolSpec(
        name=func.__name__,
        description=description,
        parameters={"type": "object", "properties": properties, "required": required},
        permission=permission,
        timeout=timeout,
        **spec_kw,
    )
