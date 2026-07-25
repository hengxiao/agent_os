"""Inline skill(merge)Runner 整合锚点测试(SKILL-INLINING.md §4.3/§9)。

固定约定:

- ``GET /api/skills/{name}`` 与 ``GET /api/skills`` 摘要都暴露 ``inline`` 字段
  (Skills 浏览器才能给 inline 技能打标);
- ``POST /api/runs`` 的 ``overrides`` 接受 ``inline: "on" | "off"``
  (消融开关经 API 可达,§9):off 档 merge 技能恢复伪工具与压帧路径,
  on 档伪工具被过滤、无子帧。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SKILLS_YAML = """
skills:
  - name: date_style
    version: 1.0.0
    kind: prompt
    inline: true
    description: 日期规范能力。Use when 输出含日期。
    inputs:
      type: object
      properties: { text: { type: string } }
      required: [text]
    outputs:
      type: object
      properties: { styled: { type: string } }
      required: [styled]
    permissions: { tools: [], skills: [] }
    model: { prefer: ["mock/x"] }
    prompt: |
      输出中出现日期时,一律规范为 ISO 8601(YYYY-MM-DD)。
  - name: report_writer
    version: 1.0.0
    kind: prompt
    description: 写简报。Use when 需要产出带日期的简报。
    inputs:
      type: object
      properties: { topic: { type: string } }
      required: [topic]
    outputs:
      type: object
      properties: { report: { type: string } }
      required: [report]
    permissions: { tools: [], skills: [date_style] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      你是简报撰写器,就输入主题写一句简报。
"""

CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "brains:writer_brain"

[tools]
builtins = false
python_exec = "off"

[skills]
path = "{skills}"

[telemetry]
dir = "{telemetry}"
"""

BRAIN = '''
import json
from agent_os.api.v1 import ChatResponse, ChatUsage, Message, Role, ToolCall


def writer_brain(req):
    user = next((json.loads(m.content) for m in req.messages if m.role is Role.USER), {})
    if "text" in user:  # date_style 帧(只在 off 档作为伪工具被调用时出现)
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"styled": "2026-07-24"})),
            finish_reason="stop", usage=ChatUsage(prompt=1, completion=1))
    tools = {t["name"] for t in req.tools}
    called = any(
        tc.name == "skill__date_style"
        for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
    )
    if not called and "skill__date_style" in tools:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT,
                            tool_calls=[ToolCall(id="c1", name="skill__date_style", args={"text": "x"})]),
            finish_reason="tool_calls", usage=ChatUsage(prompt=1, completion=1))
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"report": "ok"})),
        finish_reason="stop", usage=ChatUsage(prompt=1, completion=1))
'''


def _client(tmp_path: Path) -> TestClient:
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    (tmp_path / "brains.py").write_text(BRAIN, encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TOML.format(skills=tmp_path / "skills.yaml", telemetry=tmp_path / "traces"),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def test_skill_detail_and_summary_expose_inline(tmp_path):
    client = _client(tmp_path)
    detail = client.get("/api/skills/date_style").json()
    assert detail["inline"] is True
    summary = client.get("/api/skills").json()
    assert next(s for s in summary if s["name"] == "date_style")["inline"] is True


def test_overrides_inline_on_off_ablation(tmp_path):
    """§9 消融:on 档无子帧,off 档恢复伪工具并压出真实子帧。"""
    client = _client(tmp_path)

    r_on = client.post(
        "/api/runs",
        json={"skill": "report_writer", "input": {"topic": "t"}, "wait": True},
    )
    assert r_on.json()["status"] == "done"
    frames_on = client.get(f"/api/runs/{r_on.json()['run_id']}").json()["frames"]
    assert len(frames_on) == 1, "on 档 merge 不得产生子帧"

    r_off = client.post(
        "/api/runs",
        json={"skill": "report_writer", "input": {"topic": "t"},
              "wait": True, "overrides": {"inline": "off"}},
    )
    assert r_off.json()["status"] == "done"
    frames_off = client.get(f"/api/runs/{r_off.json()['run_id']}").json()["frames"]
    assert len(frames_off) == 2, "off 档退化为压帧,应有 date_style 子帧"
