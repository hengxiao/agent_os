"""Web 测试共用:TestClient 工厂与终态轮询。"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from tests.helpers.config import write_config


def make_client(tmp_path: Path, **config_kw) -> TestClient:
    """写一份 agent-os.toml 并起 app;关键字参数透传 ``write_config``。"""
    cfg = write_config(tmp_path, **config_kw)
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def run_and_wait(client: TestClient, skill: str, input: dict) -> str:
    r = client.post("/api/runs", json={"skill": skill, "input": input, "wait": True})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def wait_status(client: TestClient, run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["status"] in ("done", "failed", "aborted"):
            return detail
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内结束")
