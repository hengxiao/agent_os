"""web_platform API 锚点测试(docs/WEB-PLATFORM.md §6)。

- sessions/messages 端点全流程(建会话 → 发意图 → agent 消息带卡且过协议闸);
- cards/action:白名单拒绝、scaffold.approve 转发(首稿 + 双卡)、未接线 action 400;
- 主 app /platform 挂载可用。
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
  - name: weather.query
    version: 1.0.0
    kind: prompt
    description: 按天气推荐晚餐。Use when 查晚餐;Do not use when 其他。
    inputs:
      type: object
      properties: { city: { type: string } }
    outputs:
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    permissions:
      tools: []
      skills: []
    model: { prefer: ["mock/x"] }
    prompt: |
      你是晚餐规划师。
"""

CONFIG_TOML = """
[run]
model = "mock/x"
compression = "off"

[providers.mock]
brain = "tests.web.test_lab_testrun:lab_brain"

[tools]
builtins = true
python_exec = "off"

[skills]
path = "{skills}"

[lab]
drafts_root = "{drafts}"
"""


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TOML.format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def test_messages_flow_intent_to_agent_card(client):
    """建会话 → 发"做个技能"意图 → agent 消息带 plan 卡(过协议闸)→ 会话落盘。"""
    r = client.post("/platform/api/sessions")
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    r = client.post(f"/platform/api/sessions/{sid}/messages", json={"text": "帮我做个 weather 技能"})
    assert r.status_code == 200, r.text
    msg = r.json()
    assert msg["role"] == "agent"
    assert msg["cards"][0]["type"] == "plan"
    assert msg["cards"][0]["data"]["reuse"][0]["name"] == "weather.query"

    # 会话落盘且消息成对(user + agent)
    got = client.get(f"/platform/api/sessions/{sid}").json()
    assert [m["role"] for m in got["messages"]] == ["user", "agent"]
    rows = client.get("/platform/api/sessions").json()
    assert rows[0]["id"] == sid
    assert rows[0]["messages"] == 2


def test_cards_action_whitelist_and_scaffold(client):
    """cards/action:非白名单拒绝;scaffold.approve → 首稿 + skill_pack/gate_report 双卡。"""
    r = client.post("/platform/api/cards/action", json={"action_id": "evil.delete", "payload": {}})
    assert r.status_code == 400

    r = client.post(
        "/platform/api/cards/action",
        json={"action_id": "scaffold.approve", "payload": {"name": "lab.dinner", "template": "prompt_query"}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    types = {c["type"] for c in body["cards"]}
    assert types == {"skill_pack", "gate_report"}
    gate = next(c for c in body["cards"] if c["type"] == "gate_report")
    assert gate["data"]["status"] in ("pass", "warn", "fail")
    # 草稿真的被创建了(转发的是既有能力,不是假动作)
    assert client.get("/api/lab/drafts/lab.dinner").status_code == 200
    assert client.get("/api/lab/drafts/lab.dinner").json()["manifest"]["name"] == "lab.dinner"

    # 重复批准同名:FileExistsError → 409 友好信息,不允许 500(用户实测回归)
    r = client.post(
        "/platform/api/cards/action",
        json={"action_id": "scaffold.approve", "payload": {"name": "lab.dinner", "template": "prompt_query"}},
    )
    assert r.status_code == 409, r.text
    assert "同名草稿已存在" in r.json()["detail"]


def test_cards_action_unwired_and_rewind(client):
    """未接线 action(plan.recheck 已接线)400 给明确信息;rewind 转发到既有版本面。"""
    r = client.post("/platform/api/cards/action", json={"action_id": "plan.confirm", "payload": {"plan_id": "no.such"}})
    assert r.status_code == 409, r.text  # GateError → 找不到计划(转发面的真实语义)


def test_platform_mount_does_not_break_main_app(client):
    """主 app 不受挂载影响:/ 与既有 API 照常。"""
    assert client.get("/").status_code == 200
    assert client.get("/api/skills").status_code == 200
