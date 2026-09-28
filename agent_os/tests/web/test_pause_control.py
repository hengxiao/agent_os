"""WS2 锚点测试:Web pause 真语义(docs/DESIGN.md :940;pause 端点 + RunRecord paused 归口)。

固定约定:

- ``POST /api/runs/{id}/pause``:``RunControl.pause`` 置挂起标志,run 在下一个
  safe point 抛 ``RunPaused`` 挂起——status 落 ``paused``(RunRecord/result.json/
  内存态一致),发 ``run.paused``(**无** run.aborted/无 aborted 记录),
  checkpoint 照常落盘;reason 可带 body(缺省 ``"web pause"``),照 error 先例
  透传(``"RunPaused: <reason>"``);
- 挂起 run 经 ``POST /api/runs/{id}/resume`` 恢复完成(新内核无挂起标志,result 正确);
- 非 running 状态 pause → 409;未知 run → 404;
- host/shared/artifacts 归口(execute_run/execute_resume 同构):``RunPaused``
  先于 ``RunAborted`` 特判 → ``paused``;其余 ``RunAborted`` 仍 → ``aborted``(回归)。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import Allow, Mode, Permission, RunConfig, Signal, ToolPolicy
from agent_os.host.shared.artifacts import (
    execute_resume,
    execute_run,
    read_result,
    read_trace,
)
from agent_os.providers.mock import MockProvider
from tests.helpers.brains import fib_brain
from tests.helpers.config import write_config
from tests.helpers.kernels import FIB_SKILLS_YAML, assemble
from tests.helpers.web import wait_status as _wait_status

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _client(tmp_path: Path, **kw) -> TestClient:
    """本文件的 run 需要内置工具与宽松预算(loop/pause 场景,照 test_rca_control 先例)。"""
    from agent_os.host.web.app import create_app

    cfg = write_config(tmp_path, builtins=True, max_cost=100.0, **kw)
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def _wait_paused(client: TestClient, run_id: str, timeout: float = 10.0) -> dict:
    return _wait_status(client, run_id, timeout, statuses=("paused",))


# ---------------------------------------------------------------------------
# pause 端点
# ---------------------------------------------------------------------------


def test_pause_run_endpoint(tmp_path):
    """无限循环 run:POST pause(带 reason)→ 下一个 safe point 挂起——
    status=paused(RunRecord 归口),run.paused 信号带 reason,无 aborted 记录。"""
    client = _client(tmp_path, brain="tests.helpers.brains:loop_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 2}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    time.sleep(0.3)

    r = client.post(f"/api/runs/{run_id}/pause", json={"reason": "排查内存"})
    assert r.status_code == 200 and r.json() == {"ok": True}

    detail = _wait_paused(client, run_id)
    assert detail["status"] == "paused"
    assert detail["error"] == "RunPaused: 排查内存"  # reason 照 error 先例透传

    # RunRecord 归口终态:result.json 同口径;checkpoint 照常落盘(resume 数据源)
    run_dir = tmp_path / "runs" / "runs" / run_id
    result = read_result(run_dir)
    assert result["status"] == "paused"
    assert result["error"] == "RunPaused: 排查内存"
    assert (run_dir / "checkpoint.json").is_file()

    # 信号面:发 run.paused(payload reason),不发 run.aborted → 无 aborted 记录
    names = [row.get("name") for row in read_trace(run_dir) if row.get("type") == "signal"]
    assert names.count("run.paused") == 1
    assert "run.aborted" not in names
    paused = next(
        row
        for row in read_trace(run_dir)
        if row.get("type") == "signal" and row.get("name") == "run.paused"
    )
    assert paused["payload"] == {"reason": "排查内存"}


def test_pause_default_reason(tmp_path):
    """不带 body 的 pause:reason 缺省 "web pause"。"""
    client = _client(tmp_path, brain="tests.helpers.brains:loop_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 2}})
    run_id = r.json()["run_id"]
    time.sleep(0.3)

    r = client.post(f"/api/runs/{run_id}/pause")
    assert r.status_code == 200
    detail = _wait_paused(client, run_id)
    assert detail["error"] == "RunPaused: web pause"


def test_resume_paused_run(tmp_path):
    """挂起 run 经 POST resume 恢复完成(§4.3;照 test_resume_run 模式)。"""
    client = _client(tmp_path, brain="tests.helpers.brains:slow_fib_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 3}})
    run_id = r.json()["run_id"]

    r = client.post(f"/api/runs/{run_id}/pause")
    assert r.status_code == 200
    detail = _wait_paused(client, run_id)
    assert detail["status"] == "paused"

    r = client.post(f"/api/runs/{run_id}/resume")
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["result"] == {"seq": [0, 1, 1]}

    detail = _wait_status(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1, 1]}


def test_pause_not_running_conflict(tmp_path):
    """非 running 状态 pause → 409(stop_run 同口径);未知 run → 404。"""
    client = _client(tmp_path, brain="tests.helpers.brains:fib_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 1}, "wait": True})
    run_id = r.json()["run_id"]
    assert r.json()["status"] == "done"

    r = client.post(f"/api/runs/{run_id}/pause")
    assert r.status_code == 409

    r = client.post("/api/runs/run-不存在/pause")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# artifacts 归口单测(host/shared;execute_run 路径)
# ---------------------------------------------------------------------------


def _config() -> RunConfig:
    return RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )


class _PauseProbe:
    """post:llm.response 上一次 ``ctl.pause`` 的探针(内核 test_pause_resume 同形)。"""

    name: ClassVar[str] = "pause-probe"
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False
    subscriptions: ClassVar[list] = ["post:llm.response"]

    async def on_signal(self, sig: Signal, ctl: Any) -> Allow:
        await ctl.pause(sig.run_id, "探针挂起")
        return Allow()


class _StopProbe:
    """post:llm.response 上一次 ``ctl.stop`` 的探针(RunAborted 回归用)。"""

    name: ClassVar[str] = "stop-probe"
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False
    subscriptions: ClassVar[list] = ["post:llm.response"]

    async def on_signal(self, sig: Signal, ctl: Any) -> Allow:
        await ctl.stop(sig.run_id, "探针中止")
        return Allow()


def _execute_with_probe(tmp_path: Path, probe: Any) -> dict:
    kernel = assemble(_config(), MockProvider(fib_brain), FIB_SKILLS_YAML, sidecars=(probe,))
    return execute_run(kernel, "demo.fib", {"n": 3}, artifacts_root=tmp_path, host="test")


def test_artifacts_classify_paused(tmp_path):
    """RunPaused 路径 → RunRecord status "paused"(先于 RunAborted 特判),
    error 照 "Type: message" 先例透传 reason;产物 result.json 同口径。"""
    record = _execute_with_probe(tmp_path, _PauseProbe())
    assert record["status"] == "paused"
    assert record["error"] == "RunPaused: 探针挂起"
    result = read_result(tmp_path / "runs" / record["run_id"])
    assert result["status"] == "paused"
    assert result["error"] == "RunPaused: 探针挂起"


def test_artifacts_classify_aborted_regression(tmp_path):
    """RunAborted 仍归 "aborted"(回归:RunPaused 特判不影响其余子类)。"""
    record = _execute_with_probe(tmp_path, _StopProbe())
    assert record["status"] == "aborted"
    assert record["error"] == "RunAborted: 探针中止"


def test_artifacts_classify_paused_on_resume(tmp_path):
    """execute_resume 路径同归口:resume 中再次 pause(链式挂起)→ "paused"。"""
    record1 = _execute_with_probe(tmp_path, _PauseProbe())
    assert record1["status"] == "paused"
    ckpt = Path(record1["artifacts"]["checkpoint"])

    kernel2 = assemble(_config(), MockProvider(fib_brain), FIB_SKILLS_YAML, sidecars=(_PauseProbe(),))
    record2 = execute_resume(kernel2, ckpt, artifacts_root=tmp_path, host="test")
    assert record2["status"] == "paused"
    assert record2["error"] == "RunPaused: 探针挂起"
