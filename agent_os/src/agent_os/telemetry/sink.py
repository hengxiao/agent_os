"""JsonlTelemetrySink(DESIGN.md §10.2 baseline;M5)。

``traces/<run_id>.jsonl``:版本头 + 队列批量落盘(WAL);schema 版本化——JSONL 行格式
为带版本头的稳定契约,字段足以重建完整 tool-calling 过程。
PII 脱敏 hook:落盘前可插拔清洗(默认关闭)。MetricsCollector:在线汇聚过程指标
(action legality rate、path efficiency、回溯频率)。
"""

from __future__ import annotations

from agent_os.api.v1 import Checkpoint, Exporter, Signal


class JsonlTelemetrySink:
    """``agent_os.api.v1.TelemetrySink`` 协议实现(M5)。exporters 注册制。"""

    SCHEMA_VERSION: str = "1"

    def __init__(self, traces_dir: str = "./traces", exporters: list[Exporter] | None = None) -> None:
        self.traces_dir = traces_dir
        self.exporters: list[Exporter] = list(exporters or [])

    def register_exporter(self, exporter: Exporter) -> None:
        """JSONL(baseline)/ OTLP / RL-trajectory(§10.1)。"""
        raise NotImplementedError("M5")

    async def record(self, sig: Signal) -> None:
        """内核不等待 IO(内部队列)。"""
        raise NotImplementedError("M5")

    async def flush(self) -> None:
        raise NotImplementedError("M5")

    async def snapshot(self, run_id: str) -> Checkpoint:
        raise NotImplementedError("M5")
