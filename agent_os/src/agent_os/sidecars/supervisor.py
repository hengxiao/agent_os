"""SidecarSupervisor(DESIGN.md §5.3;M4)。

sidecar 统一托管:注册、关停(ASYNC 任务取消)。
监督语义:SYNC 在关键路径、独立超时(默认 2s)、自身异常或超时一律 fail-closed
(视为 Veto);ASYNC 以任务派发,异常只记日志,永远不允许拖垮 run。
多个 SYNC sidecar 订阅同一 pattern 时按 priority 顺序逐个裁决(注册时排序),
首个非 ``Allow`` verdict 生效(§5.2 确定性裁决顺序)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_os.api.v1 import Allow, Mode, RunControl, Sidecar, Signal, Verdict, Veto

_log = logging.getLogger("agent_os.sidecars")


class SidecarSupervisor:
    """sidecar 生命周期与裁决编排(M4)。

    - ``register``:SYNC sidecar 按订阅 pattern 向总线注册共享 handler(同 pattern
      只注册一次,handler 内按 priority 序逐个 ``wait_for`` 裁决);ASYNC sidecar
      每个 (pattern, sidecar) 注册一个 fire-and-forget handler;
    - ``close``:run 收尾时取消全部在跑的 ASYNC 任务。
    """

    def __init__(self, bus: Any, ctl: RunControl, *, sync_timeout: float = 2.0) -> None:
        self._bus = bus
        self._ctl = ctl
        self.sync_timeout = sync_timeout
        self.sidecars: list[Sidecar] = []
        self._sync: list[Sidecar] = []  # 按 priority 升序(注册时排序)
        self._sync_bound: set[str] = set()
        self._tasks: set[asyncio.Task[Any]] = set()

    def register(self, sidecar: Sidecar) -> None:
        """登记 sidecar 并把它的订阅接到总线上。"""
        self.sidecars.append(sidecar)
        if sidecar.mode is Mode.SYNC:
            self._sync.append(sidecar)
            self._sync.sort(key=lambda s: s.priority)  # 稳定排序:同 priority 保持注册序
            for pattern in sidecar.subscriptions:
                if pattern not in self._sync_bound:
                    self._sync_bound.add(pattern)
                    self._bus.subscribe(pattern, self._sync_handler(pattern))
        else:
            for pattern in sidecar.subscriptions:
                self._bus.subscribe(pattern, self._async_handler(sidecar))

    def _sync_handler(self, pattern: str) -> Callable[[Signal], Awaitable[Verdict]]:
        """某 pattern 的关键路径 handler:按 priority 序逐个裁决,首个非 Allow 生效。

        超时或异常 → fail-closed:``Veto("sidecar <name> fail-closed: <原因>")``(§5.3)。
        """

        async def handler(sig: Signal) -> Verdict:
            for sidecar in self._sync:
                if pattern not in sidecar.subscriptions:
                    continue
                try:
                    verdict = await asyncio.wait_for(
                        sidecar.on_signal(sig, self._ctl), self.sync_timeout
                    )
                except Exception as e:  # noqa: BLE001 — 含 TimeoutError:fail-closed(§5.3)
                    reason = str(e) or type(e).__name__
                    verdict = Veto(f"sidecar {sidecar.name} fail-closed: {reason}")
                if not isinstance(verdict, Allow):
                    return verdict
            return Allow()

        return handler

    def _async_handler(self, sidecar: Sidecar) -> Callable[[Signal], Awaitable[None]]:
        """ASYNC 观察 handler:create_task 派发后立即返回 None,永不拖垮 run(§5.3)。"""

        async def handler(sig: Signal) -> None:
            task = asyncio.create_task(self._observe(sidecar, sig))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        return handler

    async def _observe(self, sidecar: Sidecar, sig: Signal) -> None:
        try:
            await sidecar.on_signal(sig, self._ctl)
        except Exception:  # noqa: BLE001 — ASYNC sidecar 故障只记日志(§5.3)
            _log.exception("ASYNC sidecar %s 处理 %s 异常(吞掉,不影响 run)", sidecar.name, sig.name)

    async def close(self) -> None:
        """run 收尾清理:取消全部在跑的 ASYNC 任务。"""
        tasks = [t for t in self._tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
