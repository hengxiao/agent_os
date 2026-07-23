"""RunControl 实现(DESIGN.md §5.2;M4 sidecar 与信号)。

内核暴露给 sidecar 的特权接口的唯一实现——仅此通道可操控运行;
``get_frame_tree`` 是 sidecar(代码)→内核的 pull,不耗 token。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import FrameTree, Message, Usage


class KernelRunControl:
    """``agent_os.api.v1.RunControl`` 协议的内核侧实现(M4)。"""

    def __init__(self, kernel: Any = None) -> None:
        self._kernel = kernel

    async def stop(self, run_id: str, reason: str) -> None:
        raise NotImplementedError("M4")

    async def pause(self, run_id: str, reason: str) -> None:
        raise NotImplementedError("M4")

    async def inject_message(self, frame_id: str, msg: Message) -> None:
        raise NotImplementedError("M4")

    async def force_compress(self, frame_id: str) -> None:
        raise NotImplementedError("M4")

    async def get_frame_tree(self, run_id: str) -> FrameTree:
        raise NotImplementedError("M4")

    async def get_usage(self, run_id: str) -> Usage:
        raise NotImplementedError("M4")
