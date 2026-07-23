"""LocalPythonToolRegistry(DESIGN.md §8.4;M1)。

函数即工具:decorator 注册,schema 从签名推导(str/int/float/bool → JSON 基本型;
list[X]/dict[str, X] → array/object;有默认值 → 非 required;docstring 首段 → description)。
sync 函数包 ``asyncio.to_thread``;§8.1 分发流水线全量实现(契约本体,不是可简化项)。
"""

from __future__ import annotations

import asyncio
import inspect
import tempfile
import traceback
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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
    """``dispatch`` 的帧侧上下文:§8.2 三层权限交集中帧白名单与 RunConfig 上限由内核传入。"""

    frame: SkillFrame
    allowed_tools: list[str] = field(default_factory=list)  # 帧 manifest.tools 白名单
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)  # RunConfig 全局上限


class _FunctionTool:
    """decorator 产物:把普通 Python 函数包装成 ``agent_os.api.v1.Tool``。"""

    def __init__(self, func: Callable, spec: ToolSpec) -> None:
        self.spec = spec
        self._func = func

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if inspect.iscoroutinefunction(self._func):
            value = await self._func(**args)
        else:
            value = await asyncio.to_thread(self._func, **args)
        return ToolResult(ok=True, value=value)


class LocalPythonToolRegistry:
    """本地 Python 函数工具表(M1)。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._blob = InMemoryBlobStore()
        self._workdirs: dict[str, str] = {}

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

    def bind_signals(self, bus: Any) -> None:
        """KernelBuilder 装配钩子:给提供 ``bind()`` 的工具(如 python_exec)接信号总线。"""
        for tool in self._tools.values():
            bind = getattr(tool, "bind", None)
            if callable(bind):
                bind(bus)

    async def dispatch(self, call: ToolCall, frame_ctx: ToolDispatchContext) -> ToolResult:
        """§8.1 分发流水线(本切片实现到超时执行为止;信号由内核 runner 收发):

        schema 校验(fail fast,禁止"智能纠正")→ 三层权限(帧白名单 ∩ RunConfig 上限;
        工具自报等级随 spec)→ 构造 ToolContext → ``wait_for`` 超时执行 → 结果归一化。
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
        if (
            call.name not in frame_ctx.allowed_tools
            or spec.permission > frame_ctx.tool_policy.max_permission
        ):
            return ToolResult(
                ok=False,
                error=ToolError(
                    kind=ToolErrorKind.PERMISSION_DENIED,
                    message=(
                        f"工具 {call.name}(权限 {spec.permission.name})越权:不在帧白名单, "
                        f"或超过 RunConfig 上限 {frame_ctx.tool_policy.max_permission.name}"
                    ),
                    retryable=False,
                ),
            )
        ctx = ToolContext(
            run_id=frame_ctx.frame.run_id,
            frame_id=frame_ctx.frame.frame_id,
            workdir=self._workdir(frame_ctx.frame.run_id),
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
        """每 run 一个临时工作目录(限定 fs 工具范围,§2.2)。"""
        wd = self._workdirs.get(run_id)
        if wd is None:
            wd = tempfile.mkdtemp(prefix=f"agent_os-run-{run_id[:8]}-")
            self._workdirs[run_id] = wd
        return wd

    @classmethod
    def with_builtins(cls) -> LocalPythonToolRegistry:
        """§14.2 组装示例入口:注册 §8.3 内置工具(fs_read/fs_write/shell_exec 等)。"""
        raise NotImplementedError("M1")


_BASIC_TYPES: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


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
    """从函数签名推导 ToolSpec(§8.4 推导规则)。"""
    hints = typing.get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(func).parameters.items():
        properties[name] = _json_schema(hints.get(name, str))
        if param.default is inspect.Parameter.empty:
            required.append(name)
    doc = inspect.getdoc(func) or ""
    description = doc.split("\n\n", maxsplit=1)[0]  # docstring 首段 → description
    return ToolSpec(
        name=func.__name__,
        description=description,
        parameters={"type": "object", "properties": properties, "required": required},
        permission=permission,
        timeout=timeout,
        **spec_kw,
    )
