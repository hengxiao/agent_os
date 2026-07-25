"""工具契约(DESIGN.md §2.2;§14.1 冻结清单:ToolSpec 全部预留字段、

ToolContext.principal 与 credentials、结构化 ToolResult 含 retryable)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "BlobStore",
    "Permission",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolErrorKind",
    "ToolResult",
    "ToolSchema",
    "ToolSpec",
]


class Permission(IntEnum):
    """权限等级(§8.2):``READ < WRITE < NET < EXEC``,粒度递增。"""

    READ = 1
    WRITE = 2
    NET = 3
    EXEC = 4


#: 工具的 JSON Schema 类型别名(§4.1 ChatRequest.tools 元素)
ToolSchema = dict[str, Any]


class ToolErrorKind(Enum):
    """结构化错误类别(§3.2 / §8.1);供模型与 LoopDetector 区分恢复策略。"""

    INVALID_ARGS = "invalid_args"
    PERMISSION_DENIED = "permission_denied"
    VETOED = "vetoed"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"
    INTERNAL = "internal"


@dataclass
class ToolError:
    """§2.2:``{ kind, message, retryable, hint }``。

    ``retryable`` 区分"该重试"与"该换策略";``hint`` = 修复建议。
    """

    kind: ToolErrorKind = ToolErrorKind.INTERNAL
    message: str = ""
    retryable: bool = False
    hint: str = ""


@dataclass
class ToolResult:
    """§2.2:``{ ok, value, error }``。"""

    ok: bool = True
    value: Any = None
    error: ToolError | None = None


@dataclass
class ToolSpec:
    """工具规格(§2.2,逐字冻结,含 v1 全部预留字段——baseline 可先不实现检查逻辑)。

    STDLIB-CATALOG §W0-2 additive 增列:``cost_hint``/``replayable``/``concurrent_safe``
    (声明不强制——只落库暴露,强制检查留到有真实违规案例再加)。
    """

    name: str = ""
    description: str = ""  # 写 "when to use" + 边界 + 负例,不写 "what it does"
    parameters: ToolSchema = field(default_factory=dict)  # JSON Schema
    permission: Permission = Permission.READ
    idempotent: bool = False  # 供重试与 sidecar 判断
    timeout: float = 30.0
    # —— v1 契约预留字段(§14.1)——
    examples: list[dict[str, Any]] = field(default_factory=list)  # 1-5 个真实调用示例,随 schema 注入
    cacheable: bool = False  # 只读工具结果可缓存
    confirm: bool = False  # 不可幂等操作的两阶段语义(dry run + confirmation token)
    concurrency_safe: bool = False  # 默认否(fail-safe),parallel_invoke 依赖
    cost: float | None = None  # 成本注解(静态分析/路由用)
    depends_on: list[str] = field(default_factory=list)  # 前置工具依赖
    conflicts_with: list[str] = field(default_factory=list)  # 互斥约束
    untrusted_source: bool = False  # 结果是否来自不可信内容(触发 source tagging)
    # —— §W0-2 契约字段(additive,声明不强制)——
    cost_hint: str = ""  # 成本量级("~10ms"/"~5s,大文件更久" 形式,不写绝对秒数依赖)
    replayable: bool = False  # 可回放:replay/崩溃恢复重跑时可直接返回记录值(如 now)
    concurrent_safe: bool = False  # §W0-2 命名;与 §14.1 预留 concurrency_safe 同义,声明时一并置位


@dataclass
class ToolContext:
    """run 作用域资源注入(§2.2,逐字冻结;§W0-1 additive 增列 ``read_paths``)。工具不碰全局状态。"""

    run_id: str = ""
    frame_id: str = ""
    principal: Any = None  # caller identity(user/tenant),v1 恒 None,契约预留
    workdir: str = ""  # 帧工作目录(限定 fs 工具范围)
    read_paths: list[str] = field(default_factory=list)  # §W0-1 只读挂载(可在 workdir 之外)
    blob: BlobStore | None = None
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("agent_os.tools"))
    credentials: dict[str, Any] = field(default_factory=dict)  # 按工具声明注入,不碰全局环境


@runtime_checkable
class Tool(Protocol):
    """§2.2:原子能力——无调用栈、无 LLM 循环、单次进出。"""

    spec: ToolSpec

    async def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...


@runtime_checkable
class BlobStore(Protocol):
    """blob 存储(§7.2 spill / §8.4)。ref 采用 ``blob://<run_id>/<sha>`` URI 形态。"""

    async def put(self, data: bytes, run_id: str) -> str:
        """写入并返回 blob ref。"""
        ...

    async def get(self, ref: str, offset: int = 0, limit: int | None = None) -> bytes:
        """分页读取(§8.3 ``blob_get`` offset/limit)。"""
        ...
