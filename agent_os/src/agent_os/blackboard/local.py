"""LocalBlackboard(DESIGN.md §12.2 baseline;M5)。

进程内 dict + asyncio.Queue,单 run 作用域。
StatusBoard 模式(§12.2):子帧每步把进度摘要写入约定命名空间(append-only +
最新值视图),父帧按需读取——后台帧(§3.4 spawn)的滚动状态通道。
并发控制:``put`` 带 CAS 版本号(乐观锁),read-before-write 约束内建。
读写走信号(``blackboard.publish`` / ``blackboard.write``),可审计、可被 sidecar 否决。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agent_os.api.v1 import Envelope


class LocalBlackboard:
    """``agent_os.api.v1.Blackboard`` 协议实现(M5)。"""

    def __init__(self) -> None:
        self._kv: dict[tuple[str, str], tuple[Any, int]] = {}

    async def publish(self, env: Envelope) -> None:
        raise NotImplementedError("M5")

    async def subscribe(self, frame_id: str, pattern: str) -> AsyncIterator[Envelope]:
        raise NotImplementedError("M5")

    async def put(self, ns: str, key: str, value: Any, cas_version: int | None = None) -> int:
        """CAS 乐观锁:读记版本、写时校验、失败重读重做。返回新版本号。"""
        raise NotImplementedError("M5")

    async def get(self, ns: str, key: str) -> tuple[Any, int]:
        """返回 ``(value, version)``。"""
        raise NotImplementedError("M5")
