"""D3 后端锚点测试:skills 列表端点 + run overrides(RUNNERS.md §4.3/WEB-UI.md §6.2)。

固定约定:

- ``GET /api/skills``:返回共享 registry 的 manifest 摘要列表
  (name/version/kind/description/permissions),供 Launch Modal 技能下拉;
- ``POST /api/runs`` 接受 ``overrides: {"model"?, "max_cost"?, "max_steps"?}``,
  按 run 合并进 RunConfig(只影响本次 run,不污染共享配置)。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from tests.test_cli_r1 import _write_config

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(_write_config(tmp_path), artifacts_root=tmp_path / "runs"))


def test_skills_endpoint_lists_manifest_summaries(tmp_path):
    client = _client(tmp_path)
    r = client.get("/api/skills")
    assert r.status_code == 200
    skills = r.json()
    fib = next(s for s in skills if s["name"] == "fib")
    assert fib["kind"] == "prompt"
    assert fib["version"]
    assert "菲波拉契" in fib["description"]
    assert "python_exec" in fib["permissions"]["tools"]


def test_post_run_overrides_max_steps(tmp_path):
    """overrides.max_steps=1 → fib(3) 在第 2 步即超预算中止(只影响本次 run)。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "fib", "input": {"n": 3}, "wait": True, "overrides": {"max_steps": 1}},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "aborted"


def test_overrides_do_not_leak_into_next_run(tmp_path):
    client = _client(tmp_path)
    r1 = client.post(
        "/api/runs",
        json={"skill": "fib", "input": {"n": 3}, "wait": True, "overrides": {"max_steps": 1}},
    )
    assert r1.json()["status"] == "aborted"
    r2 = client.post("/api/runs", json={"skill": "fib", "input": {"n": 3}, "wait": True})
    assert r2.json()["status"] == "done"
