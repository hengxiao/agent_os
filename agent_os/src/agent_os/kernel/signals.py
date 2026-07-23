"""信号总线(DESIGN.md §5.1;M0 InProcessSignalBus)。

命名 ``<阶段>:<事件>``;``pre:`` 同步可否决、``post:`` 异步观察(§5.1)。
SYNC sidecar 在关键路径有超时、默认 fail-closed;ASYNC 永不拖垮 run(§5.3)。
Telemetry 是总线的特权订阅者,不算 sidecar(§5.1)。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_os.api.v1 import Signal

_log = logging.getLogger("agent_os.signals")


def _matches(pattern: str, name: str) -> bool:
    """订阅模式匹配:``"*"`` 全量、精确名、``"prefix.*"`` 后缀通配。"""
    if pattern == "*" or pattern == name:
        return True
    if pattern.endswith(".*"):
        return name.startswith(pattern[:-1])
    return False


class InProcessSignalBus:
    """进程内信号总线(M0 地基)。

    handler 异常只记日志不抛出——信号是观察通道,订阅者故障不得拖垮 run(§5.3)。
    """

    def __init__(self) -> None:
        self._subscribers: list[tuple[str, Callable[[Signal], Awaitable[Any]]]] = []

    def subscribe(self, pattern: str, handler: Callable[[Signal], Awaitable[Any]]) -> None:
        """按订阅顺序登记;``pattern`` 语义见 :func:`_matches`。"""
        self._subscribers.append((pattern, handler))

    async def emit(self, sig: Signal) -> list[Any]:
        """广播信号;按订阅顺序 await 全部匹配 handler,返回结果 list。

        ``pre:*`` 收集 SYNC 判定(按 priority 确定性裁决,§5.2)——裁决在 sidecar
        监督层(M4),总线只负责忠实收集返回值。
        """
        results: list[Any] = []
        for pattern, handler in list(self._subscribers):
            if not _matches(pattern, sig.name):
                continue
            try:
                results.append(await handler(sig))
            except Exception:  # noqa: BLE001 — 总线故意兜底:订阅者故障不得拖垮 run(§5.3)
                _log.exception(
                    "信号 handler 异常(吞掉,不阻断 emit):pattern=%r signal=%r", pattern, sig.name
                )
        return results
