"""Web 目录端点锚点测试:skills/tools 列表 + run overrides(RUNNERS.md §4.3/WEB-UI.md §6.2)。

固定约定:

- ``GET /api/skills``:返回共享 registry 的 manifest 摘要列表
  (name/version/kind/description/permissions),供 Launch Modal 技能下拉;
- ``GET /api/tools``:返回共享 tools registry 的全量 ToolSpec 摘要
  (name/description/permission/parameters 及执行属性),供 Tools 浏览器;
- ``POST /api/runs`` 接受 ``overrides: {"model"?, "max_cost"?, "max_steps"?}``,
  按 run 合并进 RunConfig(只影响本次 run,不污染共享配置)。
"""

from __future__ import annotations

import pytest

from tests.helpers.web import make_client as _client

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_skills_endpoint_lists_manifest_summaries(tmp_path):
    client = _client(tmp_path)
    r = client.get("/api/skills")
    assert r.status_code == 200
    skills = r.json()
    fib = next(s for s in skills if s["name"] == "demo.fib")
    assert fib["kind"] == "prompt"
    assert fib["version"]
    assert "菲波拉契" in fib["description"]
    assert "system.python.exec" in fib["permissions"]["tools"]


def test_tools_endpoint_lists_tool_specs(tmp_path):
    client = _client(tmp_path)
    r = client.get("/api/tools")
    assert r.status_code == 200
    tools = {t["name"]: t for t in r.json()}
    assert "system.python.exec" in tools
    assert tools["system.python.exec"]["permission"] == "EXEC"
    assert tools["system.python.exec"]["parameters"]["type"] == "object"
    assert "system.file.read" in tools
    assert tools["system.file.read"]["permission"] == "READ"


def test_post_run_overrides_max_steps(tmp_path):
    """overrides.max_steps=1 → fib(3) 在第 2 步即超预算中止(只影响本次 run)。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 3}, "wait": True, "overrides": {"max_steps": 1}},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "aborted"


def test_overrides_do_not_leak_into_next_run(tmp_path):
    client = _client(tmp_path)
    r1 = client.post(
        "/api/runs",
        json={"skill": "demo.fib", "input": {"n": 3}, "wait": True, "overrides": {"max_steps": 1}},
    )
    assert r1.json()["status"] == "aborted"
    r2 = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 3}, "wait": True})
    assert r2.json()["status"] == "done"
