"""E4 锚点测试:外部事件唤醒入口 ``POST /api/events``(§17 开放问题 4 宿主层形态)。

固定约定:

- body ``{type, payload?, target?, skill?, input?, wait?}``:``type`` 必填非空
  (缺/空 → 400);``payload`` dict 或字符串;
- 三通道路由(按目标 run 状态):running → 注入根帧(``action="injected"``);
  paused → checkpoint.json 根帧(depth 最小)追加事件消息后 resume
  (``action="resumed"``);无 target → ``skill`` 起新 run(``action="started"``,
  ``input`` 缺省 ``{"event": {"type", "payload"}}``,``wait`` 透传);
- 终态(done/failed/aborted)run → 409,未知 run → 404,无 target 缺 skill → 400;
- 事件文本 ``[event:<type>] <payload JSON>``(compact 分隔符,截 2000 字符);
  checkpoint 追加的消息 dict 逐字对齐内核 ``_message_to_dict`` 落盘形状;
- 事件到达与路由结果经 per-run hub 投 ``event.received``(宿主层信号,不进
  api/v1),SSE 回放可见;鉴权语义与全站中间件一致(静态门禁 401、
  ``[web.tokens]`` 映射 token → principal 透传)。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import Role, Source
from agent_os.host.web.app import create_app
from tests.helpers.config import write_config
from tests.helpers.web import run_and_wait, wait_status

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

TOKEN_MAP = '[web.tokens]\n"tok-alice" = "user:alice"\n'

#: 无 inputs 约束的回声技能(配 done_brain:任意 input 一步终态)——缺省 input 形态测试用
ECHO_SKILLS_YAML = """
skills:
- name: demo.echo
  version: 1.0.0
  kind: prompt
  description: 测试回声技能,任意输入直接给出 done。
  inputs:
    type: object
  outputs:
    type: object
    properties:
      done:
        type: boolean
  permissions:
    tools: []
    skills: []
  prompt: "收到事件后直接输出最终 JSON 答案,不要输出其他文字。"
"""


def _client(tmp_path: Path, **kw) -> TestClient:
    """本文件的 run 需要内置工具与宽松预算(照 test_pause_control 先例)。"""
    cfg = write_config(tmp_path, builtins=True, max_cost=100.0, **kw)
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def _spy_mock(monkeypatch) -> list:
    """捕获装配出的 MockProvider 实例(照 test_runs_api 流式锚点的 monkeypatch 先例)。

    每 run 独立内核 → 每 run 一个 mock;``recorded`` 是该 run 全部 LLM 请求
    (事件消息是否进根帧上下文的观测面)。
    """
    from agent_os.providers.mock import MockProvider

    made: list = []

    def _factory(brain=None, **kw):
        made.append(MockProvider(brain, **kw))
        return made[-1]

    monkeypatch.setattr("agent_os.runtime.config.MockProvider", _factory)
    return made


def _wait_recorded(made: list, timeout: float = 10.0) -> None:
    """等最新的 mock 录到至少一次 LLM 请求(run 真跑起来的确定性判定)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if made and made[-1].recorded:
            return
        time.sleep(0.02)
    raise AssertionError("run 未在超时内发出首个 LLM 请求")


def _event_messages(mock) -> list[str]:
    """mock.recorded 里全部 USER/INJECTED 消息文本(事件注入的观测面)。"""
    return [
        m.content
        for req in mock.recorded
        for m in req.messages
        if m.role is Role.USER and m.source is Source.INJECTED
    ]


def _sse_body(client: TestClient, run_id: str) -> str:
    """读完 SSE(含回放)到终止事件,返回全文(照 test_runs_api 先例)。"""
    body = ""
    with client.stream("GET", f"/api/runs/{run_id}/stream") as r:
        assert r.status_code == 200
        for chunk in r.iter_text():
            body += chunk
            if "event: end" in body:
                break
    return body


# ---------------------------------------------------------------------------
# 通道 1:running → 注入根帧
# ---------------------------------------------------------------------------


def test_event_injected_into_running_run(tmp_path, monkeypatch):
    """在跑 run:POST events(target.run_id)→ action=injected;后续 LLM 请求的
    根帧 messages 含事件文本(USER/INJECTED)。"""
    made = _spy_mock(monkeypatch)
    client = _client(tmp_path, brain="tests.helpers.brains:slow_fib_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 6}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    _wait_recorded(made)

    r = client.post(
        "/api/events",
        json={"type": "user.ping", "payload": {"msg": "在吗"}, "target": {"run_id": run_id}},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "action": "injected", "run_id": run_id}

    detail = wait_status(client, run_id)
    assert detail["status"] == "done"
    texts = _event_messages(made[-1])
    assert any(
        t.startswith("[event:user.ping]") and '"msg":"在吗"' in t for t in texts
    ), f"注入后请求未见事件文本: {texts}"


# ---------------------------------------------------------------------------
# 通道 2:paused → checkpoint 追加后 resume
# ---------------------------------------------------------------------------


def test_event_resumes_paused_run(tmp_path, monkeypatch):
    """PAUSED run:POST events → action=resumed → 恢复跑完(done);恢复后请求
    含事件文本;终态 checkpoint 根帧的注入消息 dict 逐字对齐落盘形状。"""
    made = _spy_mock(monkeypatch)
    client = _client(tmp_path, brain="tests.helpers.brains:slow_fib_brain")
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 3}})
    run_id = r.json()["run_id"]
    assert client.post(f"/api/runs/{run_id}/pause").status_code == 200
    detail = wait_status(client, run_id, statuses=("paused",))
    assert detail["status"] == "paused"

    mocks_before = len(made)
    r = client.post(
        "/api/events",
        json={
            "type": "deploy.finished",
            "payload": {"version": "v2"},
            "target": {"run_id": run_id},
        },
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "action": "resumed", "run_id": run_id}

    detail = wait_status(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1, 1]}

    # resume 用新内核(mock +1):恢复后的 LLM 请求带事件文本
    assert len(made) == mocks_before + 1
    assert any("[event:deploy.finished]" in t for t in _event_messages(made[-1]))

    # 终态 checkpoint 根帧:注入消息 dict 逐字对齐 _message_to_dict 落盘形状
    ckpt = json.loads((tmp_path / "runs" / "runs" / run_id / "checkpoint.json").read_text())
    root = min(ckpt["frames"], key=lambda f: f.get("depth", 0))
    injected = [m for m in root["context"]["messages"] if m.get("source") == "injected"]
    assert injected == [
        {
            "role": "user",
            "content": '[event:deploy.finished] {"version":"v2"}',
            "tool_calls": [],
            "tool_call_id": None,
            "name": None,
            "reasoning": None,
            "source": "injected",
            "meta": {"event": {"type": "deploy.finished"}},
        }
    ]

    # 路由结果经信号面可见(resume 换过新 hub 且已关闭:emit 入缓冲,SSE 回放可见)
    body = _sse_body(client, run_id)
    assert "event.received" in body and '"resumed"' in body


# ---------------------------------------------------------------------------
# 路由归类:终态 409 / 未知 404 / 校验 400
# ---------------------------------------------------------------------------


def test_event_to_terminal_run_conflict_and_unknown_404(tmp_path):
    """终态(done)run → 409;未知 run → 404。"""
    client = _client(tmp_path)
    run_id = run_and_wait(client, "demo.fib", {"n": 2})

    r = client.post("/api/events", json={"type": "x.y", "target": {"run_id": run_id}})
    assert r.status_code == 409

    r = client.post("/api/events", json={"type": "x.y", "target": {"run_id": "run-不存在"}})
    assert r.status_code == 404


def test_event_validation_400(tmp_path):
    """缺 type / 空 type / 无 target 缺 skill → 400(不走 pydantic 422)。"""
    client = _client(tmp_path)
    r = client.post("/api/events", json={"skill": "demo.fib", "input": {"n": 2}})
    assert r.status_code == 400
    r = client.post("/api/events", json={"type": "", "skill": "demo.fib", "input": {"n": 2}})
    assert r.status_code == 400
    r = client.post("/api/events", json={"type": "x.y"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 通道 3:无 target → 起新 run
# ---------------------------------------------------------------------------


def test_event_without_target_starts_run_with_explicit_input(tmp_path):
    """无 target:skill + 显式 input + wait=true → action=started,run 跑完(done);
    事件到达/路由结果经 SSE 可见。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/events",
        json={
            "type": "cron.tick",
            "payload": "整点",
            "skill": "demo.fib",
            "input": {"n": 2},
            "wait": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["action"] == "started"

    detail = client.get(f"/api/runs/{body['run_id']}").json()
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1]}

    sse = _sse_body(client, body["run_id"])
    assert "event.received" in sse and '"started"' in sse


def test_event_without_target_default_input_shape(tmp_path):
    """无 target 且 input 缺省:run input 取 ``{"event": {"type", "payload"}}``
    (观测面:产物 meta.json 的 input)。"""
    skills = tmp_path / "echo_skills.yaml"
    skills.write_text(ECHO_SKILLS_YAML, encoding="utf-8")
    client = _client(tmp_path, brain="tests.helpers.brains:done_brain", skills=skills)

    r = client.post(
        "/api/events",
        json={"type": "cron.tick", "payload": {"at": "09:00"}, "skill": "demo.echo", "wait": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["action"] == "started"

    detail = client.get(f"/api/runs/{body['run_id']}").json()
    assert detail["status"] == "done"
    meta = json.loads((tmp_path / "runs" / "runs" / body["run_id"] / "meta.json").read_text())
    assert meta["input"] == {"event": {"type": "cron.tick", "payload": {"at": "09:00"}}}


# ---------------------------------------------------------------------------
# 鉴权(全站中间件语义不变)
# ---------------------------------------------------------------------------


def test_events_static_token_gate(tmp_path):
    """静态 token 门禁下:无 token → 401(照 test_auth 先例)。"""
    cfg = write_config(tmp_path)
    client = TestClient(create_app(cfg, artifacts_root=tmp_path / "runs", token="s3cr3t-token"))
    r = client.post(
        "/api/events", json={"type": "cron.tick", "skill": "demo.fib", "input": {"n": 2}}
    )
    assert r.status_code == 401


def test_events_mapped_token_principal(tmp_path):
    """[web.tokens] 映射 token 起新 run:principal 按映射透传(观测面:
    checkpoint 帧 principal;照 test_data_authz 先例)。"""
    cfg = write_config(tmp_path, extra=TOKEN_MAP)
    client = TestClient(create_app(cfg, artifacts_root=tmp_path / "runs", token="s3cr3t-token"))
    r = client.post(
        "/api/events",
        json={"type": "cron.tick", "skill": "demo.fib", "input": {"n": 2}, "wait": True},
        headers={"Authorization": "Bearer tok-alice"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "started"

    ckpt = json.loads((tmp_path / "runs" / "runs" / body["run_id"] / "checkpoint.json").read_text())
    principals = {f["principal"]["subject"] for f in ckpt["frames"]}
    assert principals == {"user:alice"}
    assert {f["principal"]["issuer"] for f in ckpt["frames"]} == {"api-token"}
