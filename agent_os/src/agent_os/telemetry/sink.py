"""Telemetry sink(DESIGN.md §10;M5a)。

实现已落在 :mod:`agent_os.telemetry.jsonl_exporter`(JSONL WAL baseline);
本模块保留为兼容入口(契约层 ``TelemetrySink`` 协议的实现类 re-export)。
"""

from __future__ import annotations

from .jsonl_exporter import JsonlTelemetrySink

__all__ = ["JsonlTelemetrySink"]
