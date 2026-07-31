"""Web API 错误分支锚点测试(RUNNERS.md §4.3;未知资源一律 404,不 500)。"""

from __future__ import annotations

import pytest

from tests.helpers.web import make_client as _client
from tests.helpers.web import run_and_wait

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs/no-such-run",
        "/api/runs/no-such-run/signals",
        "/api/runs/no-such-run/rca",
        "/api/runs/no-such-run/usage",
    ],
)
def test_unknown_run_resources_404(tmp_path, path):
    client = _client(tmp_path)
    r = client.get(path)
    assert r.status_code == 404, (path, r.status_code, r.text)


def test_unknown_frame_404(tmp_path):
    client = _client(tmp_path)
    run_id = run_and_wait(client, "demo.fib", {"n": 2})
    r = client.get(f"/api/runs/{run_id}/frames/no-such-frame")
    assert r.status_code == 404


def test_stop_unknown_run_404(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/runs/no-such-run/stop")
    assert r.status_code == 404


def test_resume_unknown_run_404(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/runs/no-such-run/resume")
    assert r.status_code == 404


def test_unknown_skill_rejected(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/runs", json={"skill": "no_such_skill", "input": {}, "wait": True})
    assert r.status_code in (400, 404, 422) or (
        r.status_code == 200 and r.json().get("status") == "failed"
    ), r.text
