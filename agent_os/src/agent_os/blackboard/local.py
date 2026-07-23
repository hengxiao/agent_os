"""LocalBlackboard(DESIGN.md §12.2 baseline;M5)。

进程内 dict + asyncio.Queue,单 run 作用域。
StatusBoard 模式(§12.2):子帧每步把进度摘要写入约定命名空间(append-only +
最新值视图),父帧按需读取——后台帧(§3.4 spawn)的滚动状态通道。
并发控制:``put`` 带 CAS 版本号(乐观锁),read-before-write 约束内建。
读写走信号(``blackboard.publish`` / ``blackboard.write``),可审计、可被 sidecar 否决。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from agent_os.api.v1 import (
    BLACKBOARD_PUBLISH,
    BLACKBOARD_WRITE,
    BlackboardConflict,
    Envelope,
    Signal,
)

__all__ = ["LocalBlackboard"]


def _matches(pattern: str, type_: str) -> bool:
    """订阅 pattern 对 Envelope.type 的匹配,语义同信号总线:``"*"``/精确/``"prefix.*"``。"""
    if pattern == "*" or pattern == type_:
        return True
    if pattern.endswith(".*"):
        return type_.startswith(pattern[:-1])
    return False


class LocalBlackboard:
    """``agent_os.api.v1.Blackboard`` 协议实现(M5)。

    存储 ``dict[ns][key] -> (value, version)``;订阅经 asyncio.Queue 驱动,
    投递条件 = ``env.target`` 精确匹配订阅 frame_id 或 ``"*"`` 广播,且
    订阅 pattern 匹配 ``env.type``。``bus`` 为可选信号总线(读写审计,§12.2)。
    """

    def __init__(self, bus: Any = None) -> None:
        self._bus = bus
        self._kv: dict[str, dict[str, tuple[Any, int]]] = {}
        self._subs: list[tuple[str, str, asyncio.Queue[Envelope]]] = []

    async def put(self, ns: str, key: str, value: Any, cas_version: int | None = None) -> int:
        """CAS 乐观锁:读记版本、写时校验、失败重读重做。返回新版本号。"""
        cell = self._kv.setdefault(ns, {})
        _, version = cell.get(key, (None, 0))
        if cas_version is not None and (key not in cell or version != cas_version):
            raise BlackboardConflict(
                f"put({ns}/{key}) CAS 冲突:cas_version={cas_version},当前版本={version}"
            )
        version += 1
        cell[key] = (value, version)
        await self._emit(BLACKBOARD_WRITE, {"ns": ns, "key": key, "version": version})
        return version

    async def get(self, ns: str, key: str) -> tuple[Any, int]:
        """返回 ``(value, version)``;不存在返回 ``(None, 0)``。"""
        return self._kv.get(ns, {}).get(key, (None, 0))

    async def publish(self, env: Envelope) -> None:
        """帧→帧寻址消息:投递给全部匹配订阅者(target 精确或 ``"*"`` 广播)。"""
        for frame_id, pattern, queue in list(self._subs):
            if env.target != "*" and env.target != frame_id:
                continue
            if not _matches(pattern, env.type):
                continue
            queue.put_nowait(env)
        await self._emit(
            BLACKBOARD_PUBLISH,
            {"sender": env.sender, "target": env.target, "type": env.type},
        )

    def subscribe(self, frame_id: str, pattern: str) -> AsyncIterator[Envelope]:
        """登记订阅并返回异步迭代器(``async for`` 直接消费,无需 await)。

        订阅在调用时即生效(不等到首次迭代);迭代器关闭/取消时清理订阅。
        """
        queue: asyncio.Queue[Envelope] = asyncio.Queue()
        sub = (frame_id, pattern, queue)
        self._subs.append(sub)
        return self._drain(sub)

    async def _drain(self, sub: tuple[str, str, asyncio.Queue[Envelope]]) -> AsyncIterator[Envelope]:
        try:
            while True:
                yield await sub[2].get()
        finally:
            # 迭代器关闭(break→GC/aclose)或任务取消时清理订阅
            if sub in self._subs:
                self._subs.remove(sub)

    async def _emit(self, name: str, payload: dict[str, Any]) -> None:
        """读写审计信号(§12.2);未挂总线时静默跳过。"""
        if self._bus is not None:
            await self._bus.emit(Signal(name=name, payload=payload))
