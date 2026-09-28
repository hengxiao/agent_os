"""Memory 子系统(docs/DESIGN.md §11;M6 baseline):跨 run 持久记忆与知识的契约层实现。"""

from typing import Any

from agent_os.api.v1 import MemoryPrincipal

from .local_file import LocalFileMemoryService

__all__ = ["LocalFileMemoryService", "to_memory_principal"]


def to_memory_principal(principal: Any) -> MemoryPrincipal | None:
    """数据层 Principal(subject/issuer/attrs 形态)→ memory principal(user=subject)。

    None(v1 单用户语义)原样透传 = 检索全通;身份在场时只映射 user,
    tenant 留待多租户里程碑(subject 形如 "user:hengxiao",原样进 source.user)。
    工具面(system.memory.search/write)与 context 经验检索共用本转换,口径唯一。
    """
    if principal is None:
        return None
    return MemoryPrincipal(user=getattr(principal, "subject", None) or None)
