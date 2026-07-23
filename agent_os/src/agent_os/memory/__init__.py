"""Memory 子系统(DESIGN.md §11;M6 baseline):跨 run 持久记忆与知识的契约层实现。"""

from .local_file import LocalFileMemoryService

__all__ = ["LocalFileMemoryService"]
