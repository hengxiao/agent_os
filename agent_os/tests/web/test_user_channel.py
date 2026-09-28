"""M1 锚点测试:Web 宿主用户通道(docs/DESIGN.md §8.3;system.user.ask/notify)。

固定约定:

- **ask 复用 supervisor 收件箱闭环**:``system.user.ask`` 经 ``_InboxUserChannel``
  包装成 ``Question(kind="user-ask")`` 进 ``GET /api/supervisor/pending``,
  ``POST /api/supervisor/{question_id}/answer`` 作答后 run 恢复(同一管路,
  按 kind 区分渲染;options=None 即自由文本作答);
- **notify 发 trace 可见信号**:收件箱无免答条目形态,``system.user.notify``
  经 ``_InboxUserChannel.notify`` 向内核总线发 ``user.notify`` 信号
  (telemetry 全量订阅 → 落 trace.jsonl);
- 无需注入 handler:``_assemble_kernel`` 装配即得(与 Web 收件箱同旨)。
"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    ToolCall,
)
from agent_os.host.web.app import create_app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SKILLS_YAML = """
skills:
  - name: user_consult
    version: 1.0.0
    kind: prompt
    description: 用户咨询。Use when 需要用户作答。Do not use when 常规决策。
    inputs:
      type: object
      properties: { topic: { type: string } }
      required: [topic]
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions:
      tools: [system.user.ask]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      你是咨询顾问。必须先经 system.user.ask 向用户提问,再按回答输出 decision。
  - name: user_ping
    version: 1.0.0
    kind: prompt
    description: 进度通知。Use when 要单向告知用户。Do not use when 需要回答。
    inputs:
      type: object
      properties: { note: { type: string } }
      required: [note]
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions:
      tools: [system.user.notify]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      你是通知员。先经 system.user.notify 发一条通知(note 内容),再输出 done=true。
"""

CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "{brain}"

[tools]
builtins = true
python_exec = "off"

[skills]
path = "{skills}"

[telemetry]
dir = "{telemetry}"
"""


def user_ask_brain(req: ChatRequest) -> ChatResponse:
    """先调 system.user.ask,再按回答输出 decision(dotted path 供 config 加载)。"""
    asked = any(
        tc.name == "system.user.ask"
        for m in req.messages
        if m.role is Role.ASSISTANT
        for tc in m.tool_calls
    )
    if not asked:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(id="c1", name="system.user.ask", args={"question": "挑哪个方案?"})
                ],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    result = json.loads(next(m.content for m in reversed(req.messages) if m.role is Role.TOOL))
    answer = result["value"] if result["ok"] else "未回答"
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"decision": answer})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def user_notify_brain(req: ChatRequest) -> ChatResponse:
    """先调 system.user.notify(单向),再输出 done(dotted path 供 config 加载)。"""
    notified = any(
        tc.name == "system.user.notify"
        for m in req.messages
        if m.role is Role.ASSISTANT
        for tc in m.tool_calls
    )
    if not notified:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="system.user.notify",
                        args={"message": "后台批处理已完成"},
                    )
                ],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _client(tmp_path: Path, brain: str) -> TestClient:
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TOML.format(
            brain=brain, skills=tmp_path / "skills.yaml", telemetry=tmp_path / "traces"
        ),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def _wait_pending(client: TestClient) -> list[dict]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pending = client.get("/api/supervisor/pending").json()
        if pending:
            return pending
        time.sleep(0.1)
    return []


def _wait_done(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["status"] in ("done", "failed", "aborted"):
            return detail
        time.sleep(0.1)
    return client.get(f"/api/runs/{run_id}").json()


def test_user_ask_roundtrip_via_inbox(tmp_path):
    """user.ask 经收件箱闭环:pending 行 kind="user-ask" → 作答 → run 以回答完成。"""
    client = _client(tmp_path, "tests.web.test_user_channel:user_ask_brain")
    r = client.post("/api/runs", json={"skill": "user_consult", "input": {"topic": "选型"}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    pending = _wait_pending(client)
    assert pending, "user.ask 的问题须进收件箱 pending"
    q = pending[0]
    assert q["question"] == "挑哪个方案?"
    assert q["kind"] == "user-ask", "用户通道问题按 kind='user-ask' 与 supervisor 裁决区分"
    assert q["options"] is None, "自由文本作答(无 options 约束)"

    r = client.post(f"/api/supervisor/{q['question_id']}/answer", json={"answer": "方案 B"})
    assert r.status_code == 200

    detail = _wait_done(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"decision": "方案 B"}, "用户回答须作为工具结果闭环进 run"


def test_user_notify_emits_trace_signal(tmp_path):
    """notify 取最薄形态:run 完成且 trace.jsonl 落 user.notify 信号(payload 带原文)。"""
    client = _client(tmp_path, "tests.web.test_user_channel:user_notify_brain")
    r = client.post("/api/runs", json={"skill": "user_ping", "input": {"note": "批处理"}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    detail = _wait_done(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"done": True}
    # 单向语义:全程不得有 pending 条目残留
    assert client.get("/api/supervisor/pending").json() == []

    trace = (tmp_path / "runs" / "runs" / run_id / "trace.jsonl").read_text(encoding="utf-8")
    signals = [
        json.loads(line) for line in trace.splitlines() if json.loads(line).get("name") == "user.notify"
    ]
    assert signals, "notify 须经信号总线落 trace(telemetry 全量订阅)"
    assert signals[0]["payload"]["message"] == "后台批处理已完成"
