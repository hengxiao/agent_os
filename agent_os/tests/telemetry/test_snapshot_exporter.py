"""snapshot() 与 JsonlExporter 锚点测试(docs/DESIGN.md §10.1;M5)。

固定约定:

- ``snapshot(run_id)`` 是 WAL 视角的快照:返回已 append 落盘的信号条数(seq),
  ``state`` 恒空——帧树等可重建状态在 ``kernel/checkpoint.py``(Kernel.checkpoint),
  sink 侧不重复实现(最小诚实决策,见 jsonl_exporter docstring);
- ``JsonlExporter`` 是 §10.1 注册制插件位的 baseline:全部 run 的信号汇聚追加到
  单个文件,行格式同 sink schema(版本头 + signal 行),close 幂等且 fsync。
"""

from __future__ import annotations

import asyncio
import json

from agent_os.api.v1 import Checkpoint, Signal
from agent_os.telemetry import JsonlExporter, JsonlTelemetrySink


def _sig(name: str, run_id: str = "r1") -> Signal:
    return Signal(name=name, run_id=run_id, frame_id="f1", payload={})


def test_snapshot_seq_counts_appended_signals(tmp_path):
    sink = JsonlTelemetrySink(str(tmp_path / "traces"))

    async def main():
        await sink.record(_sig("a"))
        await sink.record(_sig("b"))
        await sink.record(_sig("c", run_id="r2"))
        snap1 = await sink.snapshot("r1")
        snap2 = await sink.snapshot("r2")
        snap_unknown = await sink.snapshot("nope")
        return snap1, snap2, snap_unknown

    snap1, snap2, snap_unknown = asyncio.run(main())
    assert isinstance(snap1, Checkpoint)
    assert snap1.run_id == "r1" and snap1.seq == 2
    assert snap2.run_id == "r2" and snap2.seq == 1
    assert snap_unknown.seq == 0, "未知 run 的持久前缀长度为 0"


def test_snapshot_state_empty_by_design(tmp_path):
    """state 恒空:帧树重建状态在 kernel/checkpoint.py,不在 sink 侧(决策见 docstring)。"""
    sink = JsonlTelemetrySink(str(tmp_path / "traces"))

    async def main():
        await sink.record(_sig("a"))
        return await sink.snapshot("r1")

    snap = asyncio.run(main())
    assert snap.state == {}, "sink 快照不携带帧树状态(语义在 Kernel.checkpoint)"


def test_exporter_receives_forwarded_signals(tmp_path):
    """register_exporter 注册后,record 的信号被转发汇聚到 exporter 文件(含版本头)。"""
    path = tmp_path / "export" / "all.jsonl"
    exporter = JsonlExporter(str(path))
    sink = JsonlTelemetrySink(str(tmp_path / "traces"), exporters=[exporter])

    async def main():
        await sink.record(_sig("pre:step"))
        await sink.record(_sig("post:step", run_id="r2"))
        await sink.close()  # sink.close 会关闭注册的 exporters

    asyncio.run(main())
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["v"] == 1 and lines[0]["type"] == "header", "首行必须是版本头"
    assert lines[0]["schema"] == "agent_os.trace/1", "行格式与 sink 同 schema"
    bodies = lines[1:]
    assert [line["name"] for line in bodies] == ["pre:step", "post:step"]
    assert {line["run_id"] for line in bodies} == {"r1", "r2"}, "多 run 汇聚到同一文件"


def test_exporter_close_idempotent(tmp_path):
    exporter = JsonlExporter(str(tmp_path / "x.jsonl"))

    async def main():
        await exporter.export(_sig("a"))
        await exporter.close()
        await exporter.close()  # 重复关闭不报错(sink.close 可能对同一 exporter 重复调用)

    asyncio.run(main())
