"""JSONL exporter 与 JsonlTelemetrySink(DESIGN.md §10.1/§10.2;M5a baseline)。

append-only 事件日志 + 版本头;快照可重建、可丢弃(§16 风险表)。
WAL 原则:帧 transcript 实时 append-only 落盘 = 任何时刻持有完整检查点;
schema 版本化——JSONL 行格式为带版本头的稳定契约,字段足以重建完整
tool-calling 过程。训练就绪导出预留:记录压缩前的请求级原始报文与消息
provenance(支撑 loss masking)。

行格式(schema ``agent_os.trace/1``)::

    {"v": 1, "type": "header", "schema": "agent_os.trace/1"}        # 每文件首行
    {"v": 1, "type": "signal", "name", "run_id", "frame_id", "ts", "payload"}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO

from agent_os.api.v1 import Checkpoint, Exporter, Signal


class JsonlTelemetrySink:
    """``agent_os.api.v1.TelemetrySink`` 协议实现(M5a baseline,§10.2)。

    全部信号 append 到 ``<traces_dir>/<run_id>.jsonl``(追加模式,行缓冲);
    每个文件首行为版本头。payload 遇不可 JSON 序列化值用 ``repr`` 兜底。
    """

    SCHEMA: str = "agent_os.trace/1"

    def __init__(self, traces_dir: str = "./traces", exporters: list[Exporter] | None = None) -> None:
        self.traces_dir = traces_dir
        self.exporters: list[Exporter] = list(exporters or [])
        Path(traces_dir).mkdir(parents=True, exist_ok=True)
        #: run_id → 行缓冲文件句柄(懒打开,首行写版本头)
        self._files: dict[str, IO[str]] = {}

    def register_exporter(self, exporter: Exporter) -> None:
        """JSONL(baseline)/ OTLP / RL-trajectory(§10.1)。"""
        self.exporters.append(exporter)

    def _file_for(self, run_id: str) -> IO[str]:
        fh = self._files.get(run_id)
        if fh is None:
            fh = open(  # noqa: SIM115 — sink 生命周期随内核,句柄常驻并提供显式 flush
                Path(self.traces_dir) / f"{run_id}.jsonl",
                "a",
                encoding="utf-8",
                buffering=1,  # 行缓冲:每行即落盘(WAL)
            )
            self._files[run_id] = fh
            if fh.tell() == 0:
                fh.write(
                    json.dumps({"v": 1, "type": "header", "schema": self.SCHEMA}) + "\n"
                )
        return fh

    async def record(self, sig: Signal) -> None:
        """追加一行信号(总线特权订阅者入口,§5.1);随后转发注册的 exporters。"""
        line = {
            "v": 1,
            "type": "signal",
            "name": sig.name,
            "run_id": sig.run_id,
            "frame_id": sig.frame_id,
            "ts": sig.ts,
            "payload": sig.payload,
        }
        self._file_for(sig.run_id).write(
            json.dumps(line, ensure_ascii=False, default=repr) + "\n"
        )
        for exporter in self.exporters:
            await exporter.export(sig)

    async def flush(self) -> None:
        for fh in self._files.values():
            fh.flush()

    async def snapshot(self, run_id: str) -> Checkpoint:
        """检查点快照语义在 ``Kernel.checkpoint``(M5a 不实现于此)。"""
        raise NotImplementedError("M5")


class JsonlExporter:
    """``agent_os.api.v1.Exporter`` 协议实现(M5):JSONL 行格式落盘。"""

    name: str = "jsonl"

    def __init__(self, path: str) -> None:
        self.path = path

    async def export(self, sig: Signal) -> None:
        raise NotImplementedError("M5")

    async def close(self) -> None:
        raise NotImplementedError("M5")
