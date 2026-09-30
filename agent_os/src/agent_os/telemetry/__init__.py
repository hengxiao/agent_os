"""Telemetry 子系统(docs/DESIGN.md §10;M5 baseline):信号流的可持久化汇聚点。

类比 journald / auditd + 飞行记录仪;WAL 原则:恢复 = 重放轨迹 + 静态前缀。
§10.2 增量:PII 脱敏 hook(redact_payload,默认关)与 OTLP exporter(best-effort v1)。
"""

from .jsonl_exporter import JsonlExporter, JsonlTelemetrySink
from .otlp_exporter import OtlpExporter
from .redact import redact_payload

__all__ = ["JsonlExporter", "JsonlTelemetrySink", "OtlpExporter", "redact_payload"]
