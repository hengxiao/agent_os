"""Blackboard 契约(DESIGN.md §12.1):run 作用域内的帧间共享状态与异步消息。

类比共享内存 + 消息队列。易失、run 作用域;与 Memory(持久、跨 run)的边界见 §12.2。
权限并入 manifest 声明(``permissions.blackboard: [ns]``),内核逐次仲裁;读写走信号可审计。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = ["Blackboard", "Envelope"]


@dataclass
class Envelope:
    """§12.1(逐字):``{ sender, target, type, payload, ts }``。

    ``type`` 约定:status_update / query / result / terminate …;``target = "*"`` 广播。
    """

    sender: str = ""  # frame_id
    target: str = "*"  # frame_id | "*"
    type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@runtime_checkable
class Blackboard(Protocol):
    """§12.1(逐字,§14.1 冻结 Protocol 骨架)。"""

    async def publish(self, env: Envelope) -> None:
        """帧→帧寻址消息。"""
        ...

    async def subscribe(self, frame_id: str, pattern: str) -> AsyncIterator[Envelope]: ...

    async def put(self, ns: str, key: str, value: Any, cas_version: int | None = None) -> int:
        """CAS 乐观锁:读记版本、写时校验、失败重读重做(§12.2)。返回新版本号。"""
        ...

    async def get(self, ns: str, key: str) -> tuple[Any, int]:
        """返回 ``(value, version)``。"""
        ...
