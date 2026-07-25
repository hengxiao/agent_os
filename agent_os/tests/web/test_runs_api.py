"""R3 锚点测试:Web UI Runner 基础(RUNNERS.md §4;FastAPI + RunManager + SSE + SPA)。

固定约定:

- ``create_app(config_path, artifacts_root=...)`` 返回 FastAPI app;每 run 独立内核
  (build_kernel 装配);``POST /api/runs {skill, input, wait?}`` 启动 run,
  ``wait: true`` 时阻塞到 run 结束;
- API(§4.3):``GET /api/runs``(含产物目录重建的历史)、``GET /api/runs/{id}``
  (status/result/error/usage/frames 帧树)、``GET /api/runs/{id}/signals``、
  ``GET /api/runs/{id}/frames/{fid}``(帧完整上下文 messages);
- ``GET /api/runs/{id}/stream``:SSE——先回放缓冲信号,run 结束后发终止事件并关闭;
- ``GET /``:静态 SPA(无构建单页)。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.helpers.web import make_client as _client
from tests.helpers.web import run_and_wait

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _run_fib(client: TestClient, n: int = 3) -> str:
    return run_and_wait(client, "fib", {"n": n})


# ---------------------------------------------------------------------------
# 启动 run 与详情
# ---------------------------------------------------------------------------


def test_post_run_wait_and_get_detail(tmp_path):
    client = _client(tmp_path)
    run_id = _run_fib(client, 3)
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1, 1]}
    assert detail["usage"]["steps"] == 4
    assert [f["depth"] for f in detail["frames"]] == [1, 2]


def test_post_run_async_mode(tmp_path):
    """wait=false(缺省)时立即返回 run_id,run 在后台推进;轮询直到 done。"""
    client = _client(tmp_path)
    r = client.post("/api/runs", json={"skill": "fib", "input": {"n": 2}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    for _ in range(200):
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["status"] in ("done", "failed", "aborted"):
            break
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1]}


def test_post_run_validation_error(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/runs", json={"skill": "fib", "input": {"n": "x"}, "wait": True})
    assert r.status_code == 422 or (r.status_code == 200 and r.json().get("status") == "failed")


def test_runs_list_includes_history(tmp_path):
    client = _client(tmp_path)
    run_id = _run_fib(client, 3)
    runs = client.get("/api/runs").json()
    assert any(r["run_id"] == run_id and r["status"] == "done" for r in runs)


# ---------------------------------------------------------------------------
# signals / frames
# ---------------------------------------------------------------------------


def test_signals_endpoint(tmp_path):
    client = _client(tmp_path)
    run_id = _run_fib(client, 3)
    signals = client.get(f"/api/runs/{run_id}/signals").json()
    names = [s["name"] for s in signals]
    assert "run.started" in names and "run.finished" in names
    assert "post:llm.response" in names


def test_frame_context_endpoint(tmp_path):
    client = _client(tmp_path)
    run_id = _run_fib(client, 3)
    detail = client.get(f"/api/runs/{run_id}").json()
    fid = detail["frames"][0]["frame_id"]
    r = client.get(f"/api/runs/{run_id}/frames/{fid}")
    assert r.status_code == 200
    body = r.json()
    assert body["messages"], "帧上下文消息为空"
    assert any(m["role"] == "assistant" for m in body["messages"])


# ---------------------------------------------------------------------------
# SSE 与 SPA
# ---------------------------------------------------------------------------


def test_sse_stream_replays_and_closes(tmp_path):
    client = _client(tmp_path)
    run_id = _run_fib(client, 3)
    body = ""
    with client.stream("GET", f"/api/runs/{run_id}/stream") as r:
        assert r.status_code == 200
        for chunk in r.iter_text():
            body += chunk
            if "event: end" in body:
                break
    assert "run.started" in body
    assert "event: end" in body


def test_index_page_served(tmp_path):
    client = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()
