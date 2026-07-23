"""JSONL exporter(DESIGN.md §10.1/§10.2;M5)。

append-only 事件日志 + 版本头;快照可重建、可丢弃(§16 风险表)。
训练就绪导出预留:记录压缩前的请求级原始报文与消息 provenance(支撑 loss masking)。
"""

from __future__ import annotations

from agent_os.api.v1 import Signal


class JsonlExporter:
    """``agent_os.api.v1.Exporter`` 协议实现(M5):JSONL 行格式落盘。"""

    name: str = "jsonl"

    def __init__(self, path: str) -> None:
        self.path = path

    async def export(self, sig: Signal) -> None:
        raise NotImplementedError("M5")

    async def close(self) -> None:
        raise NotImplementedError("M5")
