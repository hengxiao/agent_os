"""R3 锚点测试:Web UI Runner 基础(docs/RUNNERS.md §4;FastAPI + RunManager + SSE + SPA)。

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
from tests.helpers.web import run_and_wait, wait_status

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _run_fib(client: TestClient, n: int = 3) -> str:
    return run_and_wait(client, "demo.fib", {"n": n})


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
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 2}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    detail = wait_status(client, run_id)  # 10s 预算 50ms 间隔,替代无 sleep 忙轮询
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1]}


def test_post_run_validation_error(tmp_path):
    client = _client(tmp_path)
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": "x"}, "wait": True})
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


def test_sse_stream_includes_llm_chunks(tmp_path, monkeypatch):
    """流式锚点(WS2):provider caps 支持流式时,SSE 回放出现 post:llm.chunk 行。

    配置装配的 MockProvider 只接 brain(无 stream_scripts);测试经 monkeypatch
    换成带流式脚本的 MockProvider,fib(2) 一次 LLM 调用即终态。
    """
    from agent_os.api.v1 import ChatChunk, ChatUsage
    from agent_os.providers.mock import MockProvider

    scripts = [
        [
            ('{"seq": ', 0.0),
            ("[0, 1]}", 0.0),
            ChatChunk(finish_reason="stop", usage=ChatUsage(prompt=1, completion=1)),
        ]
    ]
    real_mock = MockProvider

    def _factory(brain=None, **kw):
        return real_mock(brain, stream_scripts=[list(script) for script in scripts])

    monkeypatch.setattr("agent_os.runtime.config.MockProvider", _factory)
    client = _client(tmp_path)
    run_id = run_and_wait(client, "demo.fib", {"n": 2})
    body = ""
    with client.stream("GET", f"/api/runs/{run_id}/stream") as r:
        assert r.status_code == 200
        for chunk in r.iter_text():
            body += chunk
            if "event: end" in body:
                break
    assert "post:llm.chunk" in body
    assert "post:llm.response" in body  # 汇回后段:恰好一次的响应信号不受影响
    assert "event: end" in body


def test_index_page_served(tmp_path):
    client = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


# ---------------------------------------------------------------------------
# 回归:test_post_run_async_mode flake 根因(产物半写窗口 + 内存分支硬编码 result=None)
# ---------------------------------------------------------------------------


def test_detail_memory_branch_carries_record_result(tmp_path):
    """_detail 内存分支:终态且 record 在场时响应必须带 record.result(非 None)。

    原实现硬编码 ``"result": None``——result.json 重写窗口内产物分支读失败回落
    内存分支,轮询会看到 ``{"status": "done", "result": null}``(flake 根因)。
    """
    from agent_os.host.web.app import _detail

    class _Mgr:
        def state_of(self, run_id):
            return {
                "status": "done",
                "record": {"result": {"seq": [0, 1]}, "usage": {"steps": 3}},
            }

    detail = _detail(tmp_path, _Mgr(), "run-x")  # 产物目录空 → 必走内存分支
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1]}
    assert detail["usage"] == {"steps": 3}


def test_write_json_atomic_readers_never_torn(tmp_path):
    """_write_json 原子性:并发重写期间读者只读到完整旧/新 JSON(tmp + os.replace)。

    写完目标即完整可解析、无 .tmp 残留;并发读写交叉多轮,任何一次读都不得
    抛出 JSONDecodeError(旧实现 bare write_text 的 truncate-write 窗口会)。
    """
    import json as _json
    import threading

    from agent_os.host.shared.artifacts import _write_json

    target = tmp_path / "result.json"
    _write_json(target, {"seq": 0})
    assert _json.loads(target.read_text(encoding="utf-8")) == {"seq": 0}
    assert not (tmp_path / "result.json.tmp").exists()  # tmp 已被 replace 收走

    stop = threading.Event()
    errors: list[Exception] = []

    def _writer() -> None:
        for i in range(1, 300):
            _write_json(target, {"seq": i, "pad": "x" * 4000})
        stop.set()

    def _reader() -> None:
        while not stop.is_set():
            try:
                doc = _json.loads(target.read_text(encoding="utf-8"))
                assert isinstance(doc["seq"], int)
            except Exception as e:  # noqa: BLE001 — 收集一切撕裂读证据
                errors.append(e)
                stop.set()

    w = threading.Thread(target=_writer)
    r = threading.Thread(target=_reader)
    w.start()
    r.start()
    w.join()
    r.join()
    assert errors == []
    assert _json.loads(target.read_text(encoding="utf-8"))["seq"] == 299
