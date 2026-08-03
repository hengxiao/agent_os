"""RunControl 契约(docs/DESIGN.md §5.2):内核暴露给 sidecar 的特权接口。

仅此通道可操控运行;sidecar(代码)→内核的 pull 不耗 token。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .frames import SkillFrame, Usage
from .messages import Message

__all__ = ["FrameTree", "RunControl"]

#: 帧树快照(一个 Run 一棵帧树,§2.4)
FrameTree = list[SkillFrame]


@runtime_checkable
class RunControl(Protocol):
    """§5.2 控制通道。"""

    async def stop(self, run_id: str, reason: str) -> None: ...

    async def pause(self, run_id: str, reason: str) -> None: ...

    async def inject_message(self, frame_id: str, msg: Message) -> None: ...

    async def force_compress(self, frame_id: str) -> None: ...

    async def get_frame_tree(self, run_id: str) -> FrameTree: ...

    async def get_usage(self, run_id: str) -> Usage: ...
