"""S2 锚点测试:supervisor 宿主通道(SUPERVISOR.md v2 §2.3/§5)。

固定约定:

- **LLM 可见 schema**:manifest 声明 `ask_supervisor` 后,ContextManager 组装的
  请求 tools 中含该伪工具 schema(真实 LLM 才能看到);
- **Web 收件箱**:`GET /api/supervisor/pending` 列出待答问题(run/帧/问题/options/
  urgency);`POST /api/supervisor/{question_id}/answer` 提交回答,
  回答后对应 run 恢复;
- Web 收件箱即默认宿主通道:无需注入 handler,装配即得。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from tests.kernel.test_supervisor import expense_brain  # noqa: F401  (复用其语义,brain 由 config 经 dotted path 加载)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SKILLS_YAML = """
skills:
  - name: expense_report
    version: 1.0.0
    kind: prompt
    description: 报销审批。Use when 需要上级批准。
    inputs:
      type: object
      properties: { amount: { type: integer } }
      required: [amount]
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions:
      tools: [ask_supervisor]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      你是报销审批员。大额报销必须经 ask_supervisor 请求上级裁决,并按裁决回答。
"""

CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "tests.kernel.test_supervisor:expense_brain"

[tools]
builtins = false
python_exec = "off"

[skills]
path = "{skills}"

[telemetry]
dir = "{telemetry}"

[supervisor]
timeout_s = 30
"""


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TOML.format(skills=tmp_path / "skills.yaml", telemetry=tmp_path / "traces"),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def test_web_inbox_pending_and_answer_resumes_run(tmp_path):
    """run 挂起 → pending 列出问题 → 提交回答 → run 恢复完成。"""
    client = _client(tmp_path)
    r = client.post("/api/runs", json={"skill": "expense_report", "input": {"amount": 5000}})
    assert r.status_code == 200
    run_id = r.json()["run_id"]

    deadline = time.monotonic() + 10
    pending = []
    while time.monotonic() < deadline:
        pending = client.get("/api/supervisor/pending").json()
        if pending:
            break
        time.sleep(0.1)
    assert pending, "pending 必须列出挂起中的问题"
    q = pending[0]
    assert q["question"] == "批准 ¥5000 报销吗?"
    assert q["run_id"] == run_id
    assert q["options"] == ["approve", "reject"]
    assert q["frame_id"]

    r = client.post(f"/api/supervisor/{q['question_id']}/answer", json={"answer": "approve"})
    assert r.status_code == 200

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail["status"] in ("done", "failed", "aborted"):
            break
        time.sleep(0.1)
    assert detail["status"] == "done"
    assert detail["result"] == {"decision": "approve"}


def test_answer_outside_options_rejected(tmp_path):
    """提交不匹配 options 的回答 → 400/422,问题仍在 pending。"""
    client = _client(tmp_path)
    client.post("/api/runs", json={"skill": "expense_report", "input": {"amount": 5000}})

    deadline = time.monotonic() + 10
    pending = []
    while time.monotonic() < deadline:
        pending = client.get("/api/supervisor/pending").json()
        if pending:
            break
        time.sleep(0.1)
    assert pending
    q = pending[0]
    r = client.post(f"/api/supervisor/{q['question_id']}/answer", json={"answer": "maybe"})
    assert r.status_code in (400, 422)
    assert client.get("/api/supervisor/pending").json(), "被拒回答后问题仍应挂起"


def test_llm_visible_schema_contains_ask_supervisor(tmp_path):
    """manifest 声明后,组装的请求 tools 必须含 ask_supervisor 伪工具 schema。"""
    client = _client(tmp_path)
    client.post("/api/runs", json={"skill": "expense_report", "input": {"amount": 5000}})

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pending = client.get("/api/supervisor/pending").json()
        if pending:
            break
        time.sleep(0.1)
    # 用 ContextManager 直接组装该技能的请求,验证伪工具 schema 可见
    import json as _json

    from agent_os.api.v1 import FrameContext, Message, Role, RunConfig, SkillFrame, SkillRef, Source
    from agent_os.context.manager import ContextManager
    from agent_os.context.rolling_window import RollingWindowCompressor
    from agent_os.skills.local_file import LocalFileSkillRegistry
    from agent_os.tools.local_registry import LocalPythonToolRegistry

    reg = LocalFileSkillRegistry(str(tmp_path / "skills.yaml"))
    mgr = ContextManager(
        skills=reg, tools=LocalPythonToolRegistry(), config=RunConfig(model="mock/x"),
        compressor=RollingWindowCompressor(), signals=None, status_bar=False,
    )
    frame = SkillFrame(
        frame_id="f0",
        run_id="r0",
        skill=SkillRef(name="expense_report"),
        input={"amount": 1},
        context=FrameContext(
            messages=[Message(role=Role.USER, content=_json.dumps({"amount": 1}), source=Source.PARENT_INPUT)]
        ),
    )
    req = asyncio.run(mgr.build(frame))
    names = [t["name"] for t in req.tools]
    assert "ask_supervisor" in names
