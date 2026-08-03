"""M1 App 协议面锚点测试(docs/APP-MODEL.md §2/§4/§9/§10)。

- 协议:manifest 非法拒绝(双表面不齐/skill 未注册/args_from 越界);
  首批八 kind(conversation + 六卡型 + escalation)全部合法注册;
- action 管道:点击 → manifest 校验(动作存在/表面合法/参数绑定)→ 既有
  handler(与旧 cards/action 同源)→ state 回写 → 结果卡登记新 instance;
- spawn:kind+ref 去重;未注册 kind 拒绝;
- 兼容:旧 /api/cards/action 不回归(同 handler 映射面)。
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from agent_os.host.web_platform.app import create_platform_app
from agent_os.host.web_platform.apps import (
    AppRegistry,
    bind_args,
    default_manifests,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

KNOWN = {
    "platform.scaffold.approve", "platform.iterate.generate",
    "platform.candidate.accept", "platform.candidate.discard",
    "platform.version.rewind", "platform.plan.recheck",
    "platform.plan.confirm", "platform.decision.answer",
}


def _manifest(**over):
    base = {
        "kind": "test.app",
        "v": 1,
        "title": "{name}",
        "surfaces": {"card": "test.card", "tab": "test.tab"},
        "state_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
        "actions": [
            {"id": "a1", "label": "x", "skill": "platform.plan.confirm",
             "args_from": ["state.name"], "surface": ["card", "tab"]}
        ],
    }
    return base | over


# ---------------------------------------------------------------------------
# 协议:manifest 注册校验(§9)
# ---------------------------------------------------------------------------


def test_default_manifests_all_valid():
    """首批八 kind 全部合法(conversation + 六卡型 + escalation)。"""
    reg = AppRegistry(known_skills=KNOWN)
    for m in default_manifests():
        reg.register(m)
    assert set(reg.kinds()) == {
        "conversation", "plan", "skill_pack", "gate_report", "diff", "publish", "table", "escalation",
    }


def test_manifest_rejects_missing_surface():
    """双表面不齐(缺 tab)→ 拒绝注册(不变量 1:每个 app 必须有两张面孔)。"""
    reg = AppRegistry(known_skills=KNOWN)
    with pytest.raises(ValueError, match="双表面"):
        reg.register(_manifest(surfaces={"card": "only.card"}))


def test_manifest_rejects_unknown_skill():
    """action 的 skill 不在绑定表 → 拒绝(防声明没有实现的调用)。"""
    reg = AppRegistry(known_skills=KNOWN)
    bad = _manifest(actions=[{"id": "a1", "label": "x", "skill": "platform.evil",
                              "args_from": [], "surface": ["card"]}])
    with pytest.raises(ValueError, match="未注册"):
        reg.register(bad)


def test_manifest_rejects_args_from_out_of_schema():
    """args_from 越出 state_schema → 拒绝(参数来源必须落在 state 内)。"""
    reg = AppRegistry(known_skills=KNOWN)
    bad = _manifest(actions=[{"id": "a1", "label": "x", "skill": "platform.plan.confirm",
                              "args_from": ["state.nope"], "surface": ["card"]}])
    with pytest.raises(ValueError, match="越出 state_schema"):
        reg.register(bad)
    bad2 = _manifest(actions=[{"id": "a1", "label": "x", "skill": "platform.plan.confirm",
                               "args_from": ["query.name"], "surface": ["card"]}])
    with pytest.raises(ValueError, match="state"):
        reg.register(bad2)


def test_bind_args_from_state():
    """参数绑定:args_from 逐条从 state 取(键 = 末段);缺源 → KeyError。"""
    action = {"args_from": ["state.name", "state.create.0.template"]}
    state = {"name": "lab.x", "create": [{"template": "prompt_query"}]}
    assert bind_args(action, state) == {"name": "lab.x", "template": "prompt_query"}
    with pytest.raises(KeyError):
        bind_args({"args_from": ["state.missing"]}, state)


# ---------------------------------------------------------------------------
# action 管道(假 manager 面:决策作答 + state 回写 + spawn 去重)
# ---------------------------------------------------------------------------


class _FakeManager:
    """与 test_platform_w2 同形:supervisor 收件箱 + 装配失败(LLM 关)的安全态。"""

    def __init__(self, pending: list[dict[str, Any]] | None = None) -> None:
        self._pending = list(pending or [])
        self.answered: list[tuple[str, str]] = []

    def supervisor_pending(self) -> list[dict[str, Any]]:
        return self._pending

    def supervisor_answer(self, question_id: str, answer: str) -> None:
        if question_id == "missing" or any(q == question_id for q, _ in self.answered):
            # 找不到 / 已结算(与 run_manager 同归类:KeyError → 404)
            raise KeyError(f"找不到 supervisor 问题: {question_id}")
        self.answered.append((question_id, answer))

    def shared_skills_registry(self) -> Any:
        return None

    def assemble_lab_kernel(self, overlay: Any) -> Any:
        raise RuntimeError("无内核(测试面)")


ESC_ROW: dict[str, Any] = {
    "question_id": "esc-1", "run_id": "r1", "frame_id": "f0",
    "question": "升权确认",
    "context": {"skill": "ops.janitor", "tier": "irreversible", "params": {},
                "requested": {}, "reason_hint": "none → irreversible"},
    "options": ["approve-once", "deny"], "urgency": "high",
    "previous_error": None, "asked_at": 1, "kind": "escalation",
}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    manager = _FakeManager(pending=[ESC_ROW])
    app = create_platform_app(manager=manager, lab_store=None, artifacts_root=tmp_path)
    c = TestClient(app)
    c.manager = manager  # type: ignore[attr-defined]
    return c


def test_pipeline_decision_answer_and_state_writeback(client):
    """决策卡登记 instance → 管道作答(参数绑定 question_id)→ state 回写 resolved
    → GET instance 两表面同源一致。"""
    sid = client.post("/api/sessions").json()["id"]
    presented = client.post(f"/api/sessions/{sid}/decisions/present").json()["presented"]
    card = presented[0]["cards"][0]
    inst_id = card["instance"]
    assert inst_id, "决策卡创建即登记 instance(卡上带 id)"

    r = client.post(f"/api/apps/{inst_id}/actions/approve-once", json={"surface": "card"})
    assert r.status_code == 200, r.text
    assert client.manager.answered == [("esc-1", "approve-once")], "绑定参数 + action id 作答"

    inst = client.get(f"/api/apps/{inst_id}").json()
    assert inst["state"]["resolved"] == "approve-once", "结果写回 app.state(§4)"
    assert inst["state"]["question_id"] == "esc-1", "state 与卡数据同源(两表面一致)"

    r404 = client.post(f"/api/apps/{inst_id}/actions/approve-run", json={"surface": "card"})
    assert r404.status_code == 404, "已结算的问题再答 → 404 归类"


def test_pipeline_manifest_adjudication(client):
    """manifest 裁决:instance 不存在/action 不存在/表面非法/参数缺源 → 4xx。"""
    assert client.post("/api/apps/app-nope/actions/x", json={}).status_code == 404
    sid = client.post("/api/sessions").json()["id"]
    client.post(f"/api/sessions/{sid}/decisions/present")
    session = client.get(f"/api/sessions/{sid}").json()
    inst_id = next(c for m in session["messages"] for c in m["cards"] if c.get("instance"))["instance"]
    assert client.post(f"/api/apps/{inst_id}/actions/evil.act", json={}).status_code == 404
    # 表面裁决:注册一个仅 tab 面的测试 kind(经 app.state 内省面),card 面调用 → 400
    registry = client.app.state.platform_registry
    registry.register({
        "kind": "test.tabonly", "v": 1, "title": "t",
        "surfaces": {"card": "t.card", "tab": "t.tab"},
        "state_schema": {"type": "object", "properties": {"question_id": {"type": "string"}}},
        "actions": [{"id": "deny", "label": "x", "skill": "platform.decision.answer",
                     "args_from": ["state.question_id"], "surface": ["tab"]}],
    })
    inst2 = client.post("/api/apps/spawn",
                        json={"kind": "test.tabonly", "ref": "r1", "state": {"question_id": "esc-1"}})
    assert inst2.status_code == 201
    r = client.post(f"/api/apps/{inst2.json()['instance']['id']}/actions/deny", json={"surface": "card"})
    assert r.status_code == 400 and "表面" in r.json()["detail"]


def test_spawn_dedupe_and_unknown_kind(client):
    """spawn:kind+ref 去重(返回既有,opened=False);未注册 kind → 400。"""
    r1 = client.post("/api/apps/spawn", json={"kind": "skill_pack", "ref": "lab.x"})
    assert r1.status_code == 201 and r1.json()["opened"] is True
    r2 = client.post("/api/apps/spawn", json={"kind": "skill_pack", "ref": "lab.x"})
    assert r2.json()["opened"] is False
    assert r2.json()["instance"]["id"] == r1.json()["instance"]["id"], "kind+ref 去重"
    assert client.post("/api/apps/spawn", json={"kind": "evil", "ref": "x"}).status_code == 400


# ---------------------------------------------------------------------------
# 管道全链路(真装配):plan 卡 → scaffold.approve(参数绑定正确)
# ---------------------------------------------------------------------------

SKILLS_YAML = """
skills:
  - name: weather.query
    version: 1.0.0
    kind: prompt
    description: 按天气推荐晚餐。Use when 查晚餐;Do not use when 其他。
    inputs: { type: object, properties: { city: { type: string } } }
    outputs: { type: object, properties: { answer: { type: string } }, required: [answer] }
    permissions: { tools: [], skills: [] }
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
def full_client(tmp_path: Path) -> TestClient:
    (tmp_path / "skills.yaml").write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        CONFIG_TOML.format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def test_pipeline_scaffold_approve_end_to_end(full_client):
    """plan 卡 instance → 管道批准:服务端从 state 绑定 name/template(前端不给)
    → 首稿落盘 → 结果卡也登记 instance;旧 cards/action 同源不回归。"""
    client = full_client
    sid = client.post("/platform/api/sessions").json()["id"]
    msg = client.post(f"/platform/api/sessions/{sid}/messages",
                      json={"text": "帮我做个 weather 技能"}).json()
    card = msg["cards"][0]
    inst_id = card["instance"]
    create_name = card["data"]["create"][0]["name"]

    r = client.post(f"/platform/api/apps/{inst_id}/actions/scaffold.approve",
                    json={"surface": "card", "session_id": sid})
    assert r.status_code == 200, r.text
    draft = client.get(f"/api/lab/drafts/{create_name}").json()
    assert draft["name"] == create_name, "参数绑定正确(state.name/template → 既有 handler)"
    assert "smoke.json" in draft["tests"], "与旧管道同一 handler(N3 用例也在)"
    result_cards = r.json()["cards"]
    assert all("instance" in c for c in result_cards), "结果卡登记新 instance(可选 spawn)"

    # 兼容:旧 cards/action 同 handler 不回归(同名草稿 → 409 人话,证明同一条路径)
    old = client.post("/platform/api/cards/action",
                      json={"action_id": "scaffold.approve",
                            "payload": {"name": create_name, "template": "prompt_query"}})
    assert old.status_code == 409, "旧入口同 handler(重名撞同一 FileExistsError 归类)"
