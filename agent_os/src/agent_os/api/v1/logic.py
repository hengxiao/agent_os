"""Logic Kernel 契约(DESIGN.md §9.1/§9.3):逻辑代码的唯一执行点(类比 CPU/ALU)。

两类代码都从这个咽喉点过:code 技能(§2.1)与 LLM 动态代码(``python_exec``,§9.4)。
动态代码永远走 SANDBOX,无配置项可关闭(§9.2)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .tools import BlobStore

__all__ = [
    "ExecError",
    "ExecRequest",
    "ExecResult",
    "ExecUsage",
    "LogicContext",
    "LogicError",
    "LogicKernel",
    "NetworkPolicy",
    "ResourceLimits",
    "TrustLevel",
]


class TrustLevel(Enum):
    """§9.2:实例自报隔离等级。"""

    TRUSTED = "trusted"
    SANDBOX = "sandbox"


@dataclass
class ResourceLimits:
    """§9.1 ``limits``:``wall_time / cpu_time / memory_mb / stdout_bytes``。"""

    wall_time: float | None = None  # 秒
    cpu_time: float | None = None  # 秒
    memory_mb: int | None = None
    stdout_bytes: int | None = None  # 超限截断,全文 spill 至 blob store


@dataclass
class NetworkPolicy:
    """§9.1 ``network``:开启需 NET 权限;目的级白名单。"""

    allowed_hosts: list[str] = field(default_factory=list)


class LogicError(Enum):
    """§9.1:``RUNTIME_ERROR | LIMIT_EXCEEDED | REJECTED``。"""

    RUNTIME_ERROR = "runtime_error"
    LIMIT_EXCEEDED = "limit_exceeded"
    REJECTED = "rejected"


@dataclass
class ExecError:
    """§9.1 ``error``:``{ kind, message, traceback }``。"""

    kind: LogicError = LogicError.RUNTIME_ERROR
    message: str = ""
    traceback: str = ""


@dataclass
class ExecUsage:
    """§9.1 ``usage``:``{ cpu_ms, mem_peak_mb, wall_ms }``。入帧 usage(§9.5)。"""

    cpu_ms: int = 0
    mem_peak_mb: int = 0
    wall_ms: int = 0


@dataclass
class ExecRequest:
    """§9.1(逐字):``{ source, language, entry, args, ctx, limits, network }``。"""

    source: str = ""  # 源码文本,或已加载 code 技能的入口引用
    language: str = "python"  # 预留多语言路由(§9.6)
    entry: str = "run"  # code 技能入口函数名
    args: dict[str, Any] = field(default_factory=dict)  # 帧 input
    ctx: LogicContext | None = None  # 仅 TRUSTED 模式注入(§9.3)
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    network: bool | NetworkPolicy = False


@dataclass
class ExecResult:
    """§9.1(逐字):``{ value, error, stdout, stderr, usage }``。"""

    value: Any = None  # 入口返回值(须可 JSON 序列化)
    error: ExecError | None = None
    stdout: str = ""
    stderr: str = ""
    usage: ExecUsage = field(default_factory=ExecUsage)


@runtime_checkable
class LogicKernel(Protocol):
    """§9.1。同语言多后端按信任等级择优(§9.6,entry point ``agent_os.logic_kernels``)。"""

    name: str
    trust: TrustLevel

    async def execute(self, req: ExecRequest) -> ExecResult: ...


@runtime_checkable
class LogicContext(Protocol):
    """§9.3:TRUSTED 模式下 code 技能的组合能力(编排者)。

    ``invoke`` / ``call_tool`` 全部回到内核分发路径:白名单、信号、记账一样不少。
    """

    frame_id: str
    blob: BlobStore
    log: logging.Logger

    async def invoke(self, skill: str, input: dict[str, Any]) -> Any:
        """调子技能 → 压栈。"""
        ...

    async def call_tool(self, tool: str, args: dict[str, Any]) -> Any:
        """调工具 → 走 Tool Registry。"""
        ...
