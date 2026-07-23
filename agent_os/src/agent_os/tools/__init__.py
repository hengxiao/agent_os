"""Tool Registry 子系统(DESIGN.md §8):工具的注册、校验、鉴权、分发与结果归一化。

类比 syscall 表 + seccomp + vfs;分发流水线见 §8.1,权限模型见 §8.2(三层取交集)。
"""

from .blob import FileBlobStore
from .local_registry import LocalPythonToolRegistry

__all__ = ["FileBlobStore", "LocalPythonToolRegistry"]
