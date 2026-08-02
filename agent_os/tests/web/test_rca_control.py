"""R4 锚点测试:Web RCA(docs/RUNNERS.md §4.3/§4.4;失败定位 + usage 面板 + stop/resume/reload)。

固定约定:

- ``GET /api/runs/{id}/rca``:首个错误的结构化定位——
  ``first_error: {"kind": "vetoed"|"tool_error"|"aborted", "frame_id", "skill",
  "call": {"name","args"}|null, "message"}``;veto/工具错误从 checkpoint 帧上下文的
  错误观察(ok=false 工具结果,按帧入栈序)定位,aborted 取 result.json 的 error;
- ``GET /api/runs/{id}/usage``:run 汇总 + 按帧分列(九字段 Usage 子集);
- ``POST /api/runs/{id}/stop``:RunControl.stop,run 在下一个 safe point 中止;
- ``POST /api/runs/{id}/resume``:从该 run 的 checkpoint.json 恢复;
- ``POST /api/skills/reload``:热重载技能文件;
- 配置新增 ``[sidecars] tool_guard_rules = [[tool, pattern, reason], ...]`` → ToolGuard。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.helpers.brains import reset_cut_brain
from tests.helpers.config import write_config
from tests.helpers.kernels import FIB_SKILLS_YAML as SKILLS_YAML
from tests.helpers.web import wait_status as _wait_status

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

DANGER_YAML = """
skills:
  - name: test.danger
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [system.shell.exec], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 10 }
    prompt: 测试用 danger 技能。
"""


def _client(tmp_path: Path, **kw) -> TestClient:
    """本文件的 run 需要 system.shell.exec 等内置工具与宽松预算(ToolGuard/stop 场景)。"""
    from agent_os.host.web.app import create_app

    cfg = write_config(tmp_path, builtins=True, max_cost=100.0, **kw)
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


# ---------------------------------------------------------------------------
# RCA:veto 定位
# ---------------------------------------------------------------------------


def test_rca_locates_veto(tmp_path):
    """ToolGuard veto 的 run(最终 done)也能定位到首个 veto(§4.4 失败定位)。"""
    extra = (
        "\n[sidecars]\n"
        'tool_guard_rules = [["system.shell.exec", "rm -rf", "禁止危险命令 rm -rf"]]\n'
    )
    skills = tmp_path / "danger.yaml"
    skills.write_text(DANGER_YAML, encoding="utf-8")
    client = _client(tmp_path, brain="tests.helpers.brains:danger_brain", skills=skills, extra=extra)

    r = client.post("/api/runs", json={"skill": "test.danger", "input": {}, "wait": True})
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    rca = client.get(f"/api/runs/{run_id}/rca").json()
    err = rca["first_error"]
    assert err is not None and err["kind"] == "vetoed"
    assert err["call"]["name"] == "system.shell.exec"
    assert "禁止危险命令" in err["message"]
    assert err["frame_id"]


def test_rca_aborted_run(tmp_path):
    """fib(10) 深度超限中止 → rca 定位 aborted 与最深帧。"""
    client = _client(tmp_path, brain="tests.helpers.brains:fib_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 10}, "wait": True})
    run_id = r.json()["run_id"]
    assert r.json()["status"] in ("failed", "aborted")

    rca = client.get(f"/api/runs/{run_id}/rca").json()
    err = rca["first_error"]
    assert err is not None and err["kind"] == "aborted"
    assert "MaxDepthExceeded" in err["message"]


# ---------------------------------------------------------------------------
# usage 面板
# ---------------------------------------------------------------------------


def test_usage_endpoint_per_frame(tmp_path):
    client = _client(tmp_path, brain="tests.helpers.brains:fib_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 3}, "wait": True})
    run_id = r.json()["run_id"]
    usage = client.get(f"/api/runs/{run_id}/usage").json()
    assert usage["run"]["steps"] == 4
    assert len(usage["frames"]) == 2
    by_depth = sorted(usage["frames"], key=lambda f: f.get("depth", 0))
    assert by_depth[0]["steps"] == 3 and by_depth[1]["steps"] == 1


# ---------------------------------------------------------------------------
# stop / resume / reload
# ---------------------------------------------------------------------------


def test_stop_run(tmp_path):
    """无限循环 run:POST stop → 下一个 safe point 中止(§4.3)。"""
    client = _client(tmp_path, brain="tests.helpers.brains:loop_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 2}})
    run_id = r.json()["run_id"]
    time.sleep(0.3)
    r = client.post(f"/api/runs/{run_id}/stop")
    assert r.status_code == 200
    detail = _wait_status(client, run_id)
    assert detail["status"] == "aborted"


def test_resume_run(tmp_path):
    """断电 run 经 POST resume 恢复完成(§4.3)。"""
    reset_cut_brain()
    client = _client(tmp_path, brain="tests.helpers.brains:cut_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 5}, "wait": True})
    run_id = r.json()["run_id"]
    assert r.json()["status"] in ("failed", "aborted")

    r = client.post(f"/api/runs/{run_id}/resume")
    assert r.status_code == 200
    assert r.json()["result"] == {"seq": [0, 1, 1, 2, 3]}


def test_skills_reload(tmp_path):
    skills = tmp_path / "skills.yaml"
    skills.write_text(SKILLS_YAML.read_text(encoding="utf-8"), encoding="utf-8")
    client = _client(tmp_path, brain="tests.helpers.brains:fib_brain", skills=skills)

    r = client.post("/api/skills/reload")
    assert r.status_code == 200 and r.json()["reloaded"] is False  # mtime 未变

    skills.write_text(
        skills.read_text(encoding="utf-8").replace('version: 1.0.0', 'version: 1.0.1', 1),
        encoding="utf-8",
    )
    bumped = skills.stat().st_mtime + 2  # 粗粒度文件系统上也能区分
    os.utime(skills, (bumped, bumped))
    r = client.post("/api/skills/reload")
    assert r.status_code == 200 and r.json()["reloaded"] is True
