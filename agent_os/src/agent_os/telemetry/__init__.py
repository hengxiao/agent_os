"""Telemetry 子系统(DESIGN.md §10;M5 baseline):信号流的可持久化汇聚点。

类比 journald / auditd + 飞行记录仪;WAL 原则:恢复 = 重放轨迹 + 静态前缀。
"""

from .jsonl_exporter import JsonlExporter, JsonlTelemetrySink

__all__ = ["JsonlExporter", "JsonlTelemetrySink"]
