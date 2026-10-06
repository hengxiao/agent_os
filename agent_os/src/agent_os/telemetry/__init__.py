"""Telemetry 子系统(docs/DESIGN.md §10;M5 baseline):信号流的可持久化汇聚点。

类比 journald / auditd + 飞行记录仪;WAL 原则:恢复 = 重放轨迹 + 静态前缀。
§10.2 增量:PII 脱敏 hook(redact_payload,默认关)、OTLP exporter(best-effort
v1)、MetricsCollector(每 run 计数报告)与 RlTrajectoryExporter(训练就绪导出)。
"""

from .jsonl_exporter import JsonlExporter, JsonlTelemetrySink
from .metrics import MetricsCollector
from .otlp_exporter import OtlpExporter
from .redact import redact_payload
from .replay import build_mock_script, replace_providers
from .rl_exporter import RlTrajectoryExporter

__all__ = [
    "JsonlExporter",
    "JsonlTelemetrySink",
    "MetricsCollector",
    "OtlpExporter",
    "RlTrajectoryExporter",
    "build_mock_script",
    "redact_payload",
    "replace_providers",
]
