"""Debugger P5 锚点测试:周期 checkpoint(kernel/checkpoint.py PeriodicCheckpointer)。

固定约定:

- ``RunConfig.checkpoint_interval = N``(0=关):内核装配期挂载的订阅者每 N 条
  ``post:step`` 调 ``dump_checkpoint`` **覆盖写** ``<root>/runs/<run_id>/checkpoint.json``
  —— 文件语义是"最近现场"(不回溯保留历史快照);
- 接线在宿主产物路径(``execute_run``,host/shared/artifacts.py):interval=0
  不挂载(零行为变化),>0 装配期挂载;
- ``[run].checkpoint_interval`` 经 runtime/config 透传进 RunConfig。
"""

from __future__ import annotations

import asyncio
import json

from agent_os.api.v1 import POST_STEP, RUN_STARTED
from agent_os.host.shared import artifacts
from agent_os.host.shared.artifacts import execute_run
from agent_os.kernel.checkpoint import PeriodicCheckpointer
from agent_os.runtime.config import build_kernel
from tests.helpers.kernels import fib_kernel


def test_periodic_checkpoint_writes_latest_snapshot_mid_run(tmp_path):
    """interval=2:run 进行中 checkpoint.json 已落盘(第 2 步时即可见),终态有效。"""
    kernel = fib_kernel()
    PeriodicCheckpointer(kernel, 2, tmp_path).attach()
    seen_mid_run: list[bool] = []
    started: list[str] = []

    async def _cap(sig) -> None:
        started.append(sig.run_id)

    async def _spy(sig) -> None:
        # 订阅在 PeriodicCheckpointer 之后:第 2 步时快照应已写盘("最近现场")
        path = tmp_path / "runs" / sig.run_id / "checkpoint.json"
        seen_mid_run.append(path.is_file())

    kernel.signals.subscribe(RUN_STARTED, _cap)
    kernel.signals.subscribe(POST_STEP, _spy)
    result = asyncio.run(kernel.run("demo.fib", {"n": 3}))
    assert result == {"seq": [0, 1, 1]}

    run_id = started[0]
    assert seen_mid_run[0] is False, "第 1 步(interval 未到)不应落盘"
    assert seen_mid_run[1] is True, "第 2 步应已写入最近现场"
    checkpoint = json.loads(
        (tmp_path / "runs" / run_id / "checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint["v"] == 1
    assert checkpoint["frames"], "快照应含帧树"


def test_execute_run_mounts_only_when_interval_positive(tmp_path, monkeypatch):
    """接线(execute_run):interval=0 不挂载;>0 装配期挂载一次(0=关,零行为变化)。"""
    calls: list = []

    class _Spy:
        def __init__(self, kernel, interval, root):
            calls.append(("init", interval))

        def attach(self):
            calls.append("attach")

    monkeypatch.setattr(artifacts, "PeriodicCheckpointer", _Spy)

    kernel = fib_kernel()
    kernel.config.checkpoint_interval = 0
    record = execute_run(kernel, "demo.fib", {"n": 1}, artifacts_root=tmp_path, host="test")
    assert record["status"] == "done"
    assert calls == [], "interval=0 不应挂载周期 checkpoint"

    kernel2 = fib_kernel()
    kernel2.config.checkpoint_interval = 2
    record2 = execute_run(kernel2, "demo.fib", {"n": 1}, artifacts_root=tmp_path, host="test")
    assert record2["status"] == "done"
    assert calls == [("init", 2), "attach"]


def test_config_run_field_passthrough():
    """``[run].checkpoint_interval`` 经 build_kernel 透传进 RunConfig(配置流)。"""
    kernel = build_kernel({"run": {"checkpoint_interval": 5}})
    assert kernel.config.checkpoint_interval == 5
    assert build_kernel({}).config.checkpoint_interval == 0, "缺省 0=关"
