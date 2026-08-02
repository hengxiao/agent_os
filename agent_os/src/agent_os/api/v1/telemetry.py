"""Telemetry 契约(docs/DESIGN.md §10.1):信号流的可持久化汇聚点(类比 journald/auditd)。

非 sidecar:WAL/检查点要求保证落盘的 durability 语义(§10)。内核不等待 IO(内部队列)。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .signals import Signal

__all__ = ["Checkpoint", "Exporter", "TelemetrySink"]


@dataclass
class Checkpoint:
    """检查点快照(§10.1 ``snapshot()`` 返回)。

    WAL 原则:帧 transcript 实时 append-only 落盘 = 任何时刻持有完整检查点;
    恢复 = 重放轨迹 + 静态前缀。
    """

    run_id: str = ""
    seq: int = 0  # 已落盘信号序号
    state: dict[str, Any] = field(default_factory=dict)  # 帧树等可重建状态
    created_at: float = field(default_factory=time.time)


@runtime_checkable
class TelemetrySink(Protocol):
    """§10.1(逐字)。exporters 注册制:JSONL(baseline)/ OTLP / RL-trajectory。"""

    async def record(self, sig: Signal) -> None:
        """内核不等待 IO(内部队列)。"""
        ...

    async def flush(self) -> None: ...

    async def snapshot(self, run_id: str) -> Checkpoint: ...


@runtime_checkable
class Exporter(Protocol):
    """遥测导出器(entry point ``agent_os.telemetry``,§10.1/§14.3)。

    OTLP/OpenInference 映射:帧树 ≡ span 树(§10.2);训练就绪导出记录压缩前原始报文。
    """

    name: str

    async def export(self, sig: Signal) -> None: ...

    async def close(self) -> None: ...
