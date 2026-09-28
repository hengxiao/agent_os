"""WS2 锚点测试:升权审计面板(docs/ESCALATION.md §5;``GET /api/runs/{id}/escalations``)。

固定约定:

- 面板数据源 = trace.jsonl 三条升权信号 + checkpoint.json ``run.grants`` 台账
  (approve-run 登记,含 question_id/frame_id 配对键;approve-once 不登记);
- pre ↔ post 按 (frame_id, skill, tier) 时间序配对并一行;question_id 由
  ``supervisor.ask``(kind="escalation")信号补上,grant-run 从台账匹配;
- grant-run 命中无 pre(Grant 直接放行,§5 实现注),单列;
- 空 run(无升权)→ 200 空面板;未知 run → 404;
- ``?kind=escalation`` 具名筛选精确命中三条升权信号。
"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import ChatResponse, ChatUsage, Message, Permission, Role, ToolCall
from agent_os.host.web.app import create_app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SKILLS_YAML = """
skills:
  - name: root_caller
    version: 1.0.0
    kind: prompt
    description: 根调用方。Use when 测试升权审计;Do not use when 其他。
    inputs:
      type: object
      properties: { task: { type: string } }
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions:
      tools: []
      skills: [child_write, child_exec]
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      你是根调用方,按需调用子技能并汇报结果。
  - name: child_write
    version: 1.0.0
    kind: prompt
    description: 中档子技能。Use when 需要写入;Do not use when 只读或不可逆。
    inputs:
      type: object
      properties: { cmd: { type: string } }
      required: [cmd]
    outputs:
      type: object
      properties: { ran: { type: boolean } }
      required: [ran]
    permissions:
      tools: [write_tool]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 3 }
    prompt: |
      CHILD_WRITE_MARK 你是写入员,按 cmd 写入并汇报。
  - name: child_exec
    version: 1.0.0
    kind: prompt
    description: 高档子技能。Use when 需要执行命令;Do not use when 只读。
    inputs:
      type: object
      properties: { cmd: { type: string } }
      required: [cmd]
    outputs:
      type: object
      properties: { ran: { type: boolean } }
      required: [ran]
    permissions:
      tools: [exec_tool]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 3 }
    prompt: |
      CHILD_EXEC_MARK 你是命令执行员,按 cmd 执行并汇报。
"""

CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "{brain}"

[tools]
builtins = false
python_exec = "off"

[tools.custom]
module = "tests.web.test_escalations_api:register_tools"

[skills]
path = "{skills}"

[telemetry]
dir = "{telemetry}"

[supervisor]
timeout_s = 30
"""


def register_tools(registry: Any) -> None:
    """``[tools.custom]`` 注册钩子(runtime/config.py):WRITE/EXEC 工具各一,
    供 child_write/child_exec 推导 reversible/irreversible 档。"""

    @registry.tool(name="write_tool", permission=Permission.WRITE)
    def write_tool() -> str:
        """写入工具。Use when 测试 L2。"""
        return "w"

    @registry.tool(name="exec_tool", permission=Permission.EXEC)
    def exec_tool() -> str:
        """执行工具。Use when 测试 L3。"""
        return "x"


def _child_or_none(req: Any) -> ChatResponse | None:
    """子帧(SYSTEM 带 CHILD_ 标记)直接交付;根帧返回 None 由调用方续写。"""
    system = req.messages[0].content if req.messages else ""
    if "CHILD_" in system:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"ran": True})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return None


def _skill_calls(req: Any) -> list[Any]:
    return [tc for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls]


def escalation_twice_brain(req: Any) -> ChatResponse:
    """根帧连发两次 skill.child_write 再汇总(首调 approve-run 后,次调 Grant 命中)。"""
    child = _child_or_none(req)
    if child is not None:
        return child
    calls = _skill_calls(req)
    if len(calls) < 2:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(id=f"c{len(calls)}", name="skill.child_write", args={"cmd": "w"})
                ],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"decision": "ok"})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def escalation_deny_brain(req: Any) -> ChatResponse:
    """根帧发一次 skill.child_exec(L3 升权),拿到结果(被拒)后汇报。"""
    child = _child_or_none(req)
    if child is not None:
        return child
    if not _skill_calls(req):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c0", name="skill.child_exec", args={"cmd": "rm"})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    result = json.loads(next(m.content for m in reversed(req.messages) if m.role is Role.TOOL))
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT,
            content=json.dumps({"decision": "ok" if result["ok"] else result["error"]["kind"]}),
        ),
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


def _wait_pending(client: TestClient) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pending = client.get("/api/supervisor/pending").json()
        if pending:
            return pending[0]
        time.sleep(0.1)
    raise AssertionError("pending 必须列出挂起中的升权确认")


def _wait_terminal(client: TestClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["status"] in ("done", "failed", "aborted"):
            return detail
        time.sleep(0.1)
    raise AssertionError(f"run {run_id} 未在时限内到达终态")


def _start_and_answer(tmp_path: Path, brain: str, answer: str) -> tuple[TestClient, str, dict]:
    """起 root_caller run → 等升权确认挂起 → 作答 → 等终态 → (client, run_id, 问题行)。"""
    client = _client(tmp_path, brain)
    r = client.post("/api/runs", json={"skill": "root_caller", "input": {"task": "t"}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    question = _wait_pending(client)
    assert question["kind"] == "escalation"
    assert question["run_id"] == run_id
    r = client.post(f"/api/supervisor/{question['question_id']}/answer", json={"answer": answer})
    assert r.status_code == 200
    return client, run_id, question


def test_approve_run_flow_events_and_grant_pairing(tmp_path):
    """approve-run 全流程:事件行齐全(pre 并入 post;grant-run 单列),approve-run
    事件行的 question_id == checkpoint grants 台账的 question_id(批准↔请求配对)。"""
    client, run_id, question = _start_and_answer(
        tmp_path, "tests.web.test_escalations_api:escalation_twice_brain", "approve-run"
    )
    detail = _wait_terminal(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"decision": "ok"}

    r = client.get(f"/api/runs/{run_id}/escalations")
    assert r.status_code == 200
    panel = r.json()
    posts = [e for e in panel["events"] if e["kind"] == "post"]
    assert [e["decision"] for e in posts] == ["approve-run", "grant-run"]
    assert not [e for e in panel["events"] if e["kind"] == "pre"], "pre 已并入 post 行"

    approve = posts[0]
    assert approve["paired"] is True
    assert approve["asked_ts"] is not None
    assert approve["skill"] == "child_write"
    assert approve["tier"] == "reversible"
    assert approve["from_tier"] == "none"  # 发起帧档(checkpoint 帧段)
    assert approve["scope"] == "run"
    assert approve["decided_by"] == "host:web-ui"
    assert '"cmd": "w"' in approve["params"]  # 参数摘要来自配对 pre

    grants = panel["grants"]
    assert len(grants) == 1
    grant = grants[0]
    assert grant["skill"] == "child_write"
    assert grant["scope"] == "run"
    assert grant["question_id"] == question["question_id"]
    # 配对键:approve-run 事件行与台账同一 question_id;grant-run 命中从台账回补
    assert approve["question_id"] == grant["question_id"]
    assert posts[1]["question_id"] == grant["question_id"]
    assert posts[1]["paired"] is False, "grant-run 无 pre(§5 实现注)"
    assert grant["frame_id"] == approve["frame_id"]

    assert panel["summary"] == {"total": 2, "approved": 1, "denied": 0, "grant_run": 1}


def test_deny_run_denied_event_listed(tmp_path):
    """deny run:post(decision=deny)与 skill.escalation.denied 事件都在列,无台账。"""
    client, run_id, _ = _start_and_answer(
        tmp_path, "tests.web.test_escalations_api:escalation_deny_brain", "deny"
    )
    detail = _wait_terminal(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"decision": "permission_denied"}

    panel = client.get(f"/api/runs/{run_id}/escalations").json()
    posts = [e for e in panel["events"] if e["kind"] == "post"]
    assert len(posts) == 1
    post = posts[0]
    assert post["decision"] == "deny"
    assert post["paired"] is True
    assert post["decided_by"] == "host:web-ui"
    assert post["question_id"], "deny 的 question_id 由 supervisor.ask 配对补上"

    denied = [e for e in panel["events"] if e["kind"] == "denied"]
    assert len(denied) == 1
    assert denied[0]["skill"] == "child_exec"
    assert denied[0]["tier"] == "irreversible"
    assert denied[0]["decided_by"] == "host:web-ui"
    assert denied[0]["question_id"] == post["question_id"]

    assert panel["grants"] == [], "deny 不登记台账"
    assert panel["summary"] == {"total": 1, "approved": 0, "denied": 1, "grant_run": 0}


def test_empty_run_empty_panel_and_unknown_404(tmp_path):
    """空 run(无升权):200 + 空事件 + 汇总零 + 空台账;未知 run → 404(照邻端点)。"""
    client = _client(tmp_path, "tests.web.test_escalations_api:escalation_deny_brain")
    r = client.post("/api/runs", json={"skill": "child_write", "input": {"cmd": "w"}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    detail = _wait_terminal(client, run_id)
    assert detail["status"] == "done"

    panel = client.get(f"/api/runs/{run_id}/escalations").json()
    assert panel["events"] == []
    assert panel["grants"] == []
    assert panel["summary"] == {"total": 0, "approved": 0, "denied": 0, "grant_run": 0}

    r = client.get("/api/runs/no-such-run/escalations")
    assert r.status_code == 404


def test_kind_escalation_filter_hits_three_signals(tmp_path):
    """``?kind=escalation`` 具名筛选:精确命中三条升权信号,不夹带 supervisor.ask。"""
    client, run_id, _ = _start_and_answer(
        tmp_path, "tests.web.test_escalations_api:escalation_deny_brain", "deny"
    )
    _wait_terminal(client, run_id)

    rows = client.get(f"/api/runs/{run_id}/signals?kind=escalation").json()
    names = [r["name"] for r in rows]
    assert names == ["pre:skill.escalate", "post:skill.escalate", "skill.escalation.denied"]
    assert all(r["type"] == "signal" for r in rows)
