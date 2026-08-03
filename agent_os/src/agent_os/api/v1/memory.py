"""Memory 契约(docs/DESIGN.md §11.1):跨 run 持久记忆与知识的契约层(类比文件系统)。

内核不实现检索算法(§11 非目标);读写分离:读 = 检索(经工具调用暴露),
写 = 离线蒸馏或运行期经验追加。检索层权限过滤:数据离开存储层之前按 principal 过滤。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = ["EntryRef", "MemoryEntry", "MemoryService", "Principal", "Provenance"]


@dataclass
class Principal:
    """调用方身份(user/tenant)。与 :class:`~agent_os.api.v1.tools.ToolContext` 的

    ``principal`` 预留槽位呼应(§2.2,v1 恒 None)。
    """

    user: str | None = None
    tenant: str | None = None


@dataclass
class Provenance:
    """写入溯源(§11.1/§6.2):来源 run / 任务 / 说明。可溯源 + 可驱逐是安全底线。"""

    run_id: str | None = None
    task: str | None = None
    note: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class EntryRef:
    """条目引用(``write()`` 返回,``evict()`` 入参)。"""

    id: str = ""


@dataclass
class MemoryEntry:
    """§11.1(逐字):强制 source tagging;新鲜度一等属性;经验条目不具指令效力。"""

    content: Any = None
    tags: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)  # 来源 run_id / 任务 / 内容来源
    created_at: float = field(default_factory=time.time)
    freshness: dict[str, Any] = field(default_factory=dict)  # 失效条件 / TTL
    trust: str = "experience"


@runtime_checkable
class MemoryService(Protocol):
    """§11.1(逐字,§14.1 冻结 Protocol 骨架)。"""

    async def search(self, query: str, k: int, principal: Principal) -> list[MemoryEntry]: ...

    async def write(self, entry: MemoryEntry, provenance: Provenance) -> EntryRef: ...

    async def evict(self, ref: EntryRef, reason: str) -> None: ...
