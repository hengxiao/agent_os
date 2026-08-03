"""Logic Kernel 契约(docs/DESIGN.md §9.1/§9.3):逻辑代码的唯一执行点(类比 CPU/ALU)。

两类代码都从这个咽喉点过:code 技能(§2.1)与 LLM 动态代码(``system.python.exec``,§9.4)。
动态代码永远走 SANDBOX,无配置项可关闭(§9.2)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .tools import BlobStore

__all__ = [
    "ORCHESTRATE_SCHEMA",
    "ORCHESTRATE_TOOL",
    "SYSCALL_FD",
    "SYSCALL_PROTOCOL_VERSION",
    "ExecError",
    "ExecRequest",
    "ExecResult",
    "ExecUsage",
    "LogicContext",
    "LogicError",
    "LogicKernel",
    "NetworkPolicy",
    "ResourceLimits",
    "SkillError",
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
    """§9.1 ``error``:``{ kind, message, traceback }``;additive 增 hint/retryable。

    ``hint``/``retryable``(§W0-3 错误契约):code 技能经 :class:`SkillError`
    抛出的结构化错误在此保真——否则错误跨层传递时被压扁成一句人话,
    编排脚本无法按 kind 分支、模型也拿不到下一步动作建议。
    """

    kind: LogicError = LogicError.RUNTIME_ERROR
    message: str = ""
    traceback: str = ""
    hint: str = ""
    retryable: bool = False


class SkillError(Exception):
    """code 技能抛出的**结构化**错误(§W0-3;STDLIB §8 第 13 条编排友好性)。

    普通 ``ValueError("一句话")`` 会被执行层归一化成 RUNTIME_ERROR 并丢掉
    全部结构;抛本异常则 ``kind``/``hint``/``retryable`` 一路保真到模型与
    编排脚本。``hint`` 写**下一步动作**(运行期事实现取),不是错误描述的重复。
    """

    def __init__(
        self,
        message: str,
        *,
        kind: LogicError = LogicError.RUNTIME_ERROR,
        hint: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.hint = hint
        self.retryable = retryable


@dataclass
class ExecUsage:
    """§9.1 ``usage``:``{ cpu_ms, mem_peak_mb, wall_ms }``。入帧 usage(§9.5)。"""

    cpu_ms: int = 0
    mem_peak_mb: int = 0
    wall_ms: int = 0


#: 编排伪工具名(docs/CODE-ORCHESTRATION.md §2.1):分发阶段被内核拦截,不进 Tool Registry
#: ——syscall 仲裁需绑定调用帧与调用方 manifest,权限敏感的分发留在内核(同 ``skill.*``)
ORCHESTRATE_TOOL = "python_orchestrate"

#: 编排伪工具对 LLM 的呈现(ContextManager 在 manifest 声明且消融开关为 on 时追加)
ORCHESTRATE_SCHEMA: dict[str, Any] = {
    "name": ORCHESTRATE_TOOL,
    "description": (
        "在沙箱中执行编排脚本,脚本内可经 ctx 调用本技能白名单内的工具与子技能。"
        "Use when 需要循环/分支/批量调用工具(中间结果不占上下文,显著省 token);"
        "Do not use when 只需一两次调用(直接调工具更简单)或纯计算(用 system.python.exec)。"
        "脚本约定:同步直线代码;"
        "``ctx.call_tool(name, args) -> {'ok','value','error'}``、"
        "``ctx.invoke(skill, input) -> 结果``(失败抛异常);"
        "把最终结果赋给变量 ``result``(须可 JSON 序列化)。"
        "print() 输出会进返回值的 stdout 字段,是编排脚本的调试通道"
        "(可 print 中间量,出错时看得到;不占后续轮次上下文)。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "编排脚本源码(Python)"},
            "timeout": {"type": "number", "description": "墙钟上限秒数(默认 60)"},
        },
        "required": ["code"],
    },
}

#: 沙箱 syscall 协议版本(docs/CODE-ORCHESTRATION.md §2.2;首行版本头,同 telemetry 惯例)
SYSCALL_PROTOCOL_VERSION = 1

#: 沙箱内 syscall 通道的文件描述符(与业务 stdout 分流,避免混流)
SYSCALL_FD = 3


@dataclass
class ExecRequest:
    """§9.1(逐字):``{ source, language, entry, args, ctx, limits, network }``。

    ``dispatch_fn``(docs/CODE-ORCHESTRATION.md):SANDBOX 档的**工具系统调用**回调——
    非 None 时沙箱进入服务循环,脚本内 ``ctx.call_tool``/``ctx.invoke`` 经管道
    陷入内核,由本回调代为分发(白名单/veto/信号/记账全部沿用 ``_dispatch_call``)。
    None 则为纯计算档(``system.python.exec`` 与 v1 code 技能行为不变)。
    """

    source: str = ""  # 源码文本,或已加载 code 技能的入口引用
    language: str = "python"  # 预留多语言路由(§9.6)
    entry: str = "run"  # code 技能入口函数名
    args: dict[str, Any] = field(default_factory=dict)  # 帧 input
    ctx: LogicContext | None = None  # 仅 TRUSTED 模式注入(§9.3)
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    network: bool | NetworkPolicy = False
    #: SANDBOX 档 syscall 分发回调:``async (kind, name, args) -> {"ok","value","error"}``
    dispatch_fn: Any = None


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

    ``invoke`` / ``call_tool`` / ``spawn`` 全部回到内核分发路径:白名单、信号、
    记账一样不少;``spawn``/``wait`` 为 §3.4 后台帧原语(父帧不挂起,join 退化为
    读终态);``board`` 为黑板命名空间代理(§12,无黑板时为 None)。
    """

    frame_id: str
    blob: BlobStore
    log: logging.Logger
    board: Any  # 黑板命名空间代理(§12:按 manifest.permissions.blackboard 仲裁);None = 无黑板

    async def invoke(self, skill: str, input: dict[str, Any]) -> Any:
        """调子技能 → 压栈。"""
        ...

    async def call_tool(self, tool: str, args: dict[str, Any]) -> Any:
        """调工具 → 走 Tool Registry。"""
        ...

    async def spawn(self, skill: str, input: dict[str, Any]) -> str:
        """§3.4 后台帧:父帧不挂起,子帧独立后台运行;返回子帧 frame_id。"""
        ...

    async def wait(self, frame_id: str) -> Any:
        """§3.4:join 退化为读终态;子帧失败原样上抛。"""
        ...
