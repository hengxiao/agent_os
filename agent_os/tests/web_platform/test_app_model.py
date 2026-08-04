"""M1 App 协议面锚点测试(docs/APP-MODEL.md §2/§4/§9/§10)。

- 协议:manifest 非法拒绝(双表面不齐/skill 未注册/args_from 越界);
  首批八 kind(conversation + 六卡型 + escalation)全部合法注册;
- action 管道:点击 → manifest 校验(动作存在/表面合法/参数绑定)→ 既有
  handler(与旧 cards/action 同源)→ state 回写 → 结果卡登记新 instance;
- spawn:kind+ref 去重;未注册 kind 拒绝;
- 兼容:旧 /api/cards/action 不回归(同 handler 映射面)。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from agent_os.host.web_platform.app import create_platform_app
from agent_os.host.web_platform.apps import (
    AppInstanceStore,
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
    # M3:run/debug/lab-draft 三 kind 的绑定键
    "platform.run.stop", "platform.run.resume", "platform.run.rerun",
    "platform.debug.command", "platform.draft.check",
    # M4a:发起面(run 态)
    "platform.run.launch",
    # M5:shell 的 endpoint 动作(§13.1)
    "platform.shell.theme.set", "platform.shell.session.create",
    # D1:doc 的 endpoint 动作(docs/DOC-EDITOR.md §3)
    "platform.doc.save", "platform.doc.snapshot", "platform.doc.rewind", "platform.doc.export",
}


def _manifest(**over):
    base = {
        "kind": "test.app",
        "v": 1,
        "title": "{name}",
        "surfaces": {"card": "test.card", "tab": "test.tab"},
        "state_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
        "actions": [
            {"id": "a1", "label": "x", "exec": {"mode": "endpoint", "ref": "platform.plan.confirm"},
             "args_from": ["state.name"], "surface": ["card", "tab"]}
        ],
    }
    return base | over


# ---------------------------------------------------------------------------
# 协议:manifest 注册校验(§9)
# ---------------------------------------------------------------------------


def test_default_manifests_all_valid():
    """首批 kind 全部合法(M1 八个 + M3 三个 + M4b 五个 + M5 shell;
    shell 与普通 manifest 过同一协议校验——无特例代码路径,§13.4)。"""
    reg = AppRegistry(known_skills=KNOWN)
    for m in default_manifests():
        reg.register(m)
    assert set(reg.kinds()) == {
        "shell",
        "conversation", "plan", "skill_pack", "gate_report", "diff", "publish", "table", "escalation",
        "run", "debug", "lab-draft",
        "skills", "runs", "tools", "lab", "debug-old",
        "doc",  # D1(docs/DOC-EDITOR.md §2)
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
    """与 test_platform_w2 同形:supervisor 收件箱 + 装配失败(LLM 关)的安全态;
    M3 增补 run/debug 的 async 能力面(stop/resume/debug_command,记录调用)。"""

    def __init__(self, pending: list[dict[str, Any]] | None = None) -> None:
        self._pending = list(pending or [])
        self.answered: list[tuple[str, str]] = []
        self.stopped: list[str] = []
        self.resumed: list[str] = []
        self.debug_cmds: list[tuple[str, str]] = []
        self.runs_started: list[dict[str, Any]] = []

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

    # ── M3:run/debug 能力面(async,与 RunManager 同签名) ──
    async def start_run(self, skill: str, input: dict[str, Any], **kw: Any) -> str:
        self.runs_started.append({"skill": skill, "input": input})
        return "new-run-1"

    async def stop_run(self, run_id: str) -> bool:
        if run_id == "running-1":
            self.stopped.append(run_id)
            return True
        return False

    async def resume_run(self, run_id: str) -> dict[str, Any]:
        if run_id == "no-ckpt":
            raise FileNotFoundError(f"找不到 checkpoint: {run_id}")
        self.resumed.append(run_id)
        return {"status": "done"}

    async def debug_command(self, session_id: str, command: str) -> None:
        if session_id == "missing":
            raise KeyError(f"找不到调试会话: {session_id}")
        self.debug_cmds.append((session_id, command))


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
# M2 app.state 持久化(docs/APP-MODEL.md §10):写/读/坏文件隔离/重启解析
# ---------------------------------------------------------------------------


def test_instance_store_persistence(tmp_path):
    """写穿透 → 重启(新 store 同 root)可解析;kind+ref 去重跨重启;坏文件隔离;id 防穿越。"""
    root = tmp_path / "platform_apps"
    store = AppInstanceStore(root)
    inst, opened = store.register(kind="plan", ref="lab.x", state={"name": "lab.x"})
    assert opened
    assert (root / f"{inst['id']}.json").is_file(), "register 即落盘"
    store.update_state(inst["id"], {"resolved": "yes"})

    store2 = AppInstanceStore(root)  # 模拟进程重启
    got = store2.get(inst["id"])
    assert got is not None, "重启后 instance 可解析(M1 旧卡 404 缺口关闭)"
    assert got["state"]["resolved"] == "yes", "update_state 回写也落盘"
    again, opened2 = store2.register(kind="plan", ref="lab.x")
    assert not opened2 and again["id"] == inst["id"], "kind+ref 去重索引随加载重建"

    (root / "broken.json").write_text("{bad json", encoding="utf-8")  # 坏 JSON
    (root / "evil.json").write_text("{}", encoding="utf-8")  # 非法文件名(id 面外)
    store3 = AppInstanceStore(root)
    assert store3.get(inst["id"]) is not None, "坏文件不拖垮加载"
    assert store3.get("../../etc/passwd") is None, "id 防穿越"
    assert store3.get("app-zzzzzzzz") is None, "非法 id 查无"


def test_instance_resolvable_after_restart(tmp_path):
    """端点级:卡 dict 上的 instance id 在新 app(同 artifacts_root)仍可 GET。"""
    manager = _FakeManager()
    app1 = create_platform_app(manager=manager, lab_store=None, artifacts_root=tmp_path)
    c1 = TestClient(app1)
    sid = c1.post("/api/sessions").json()["id"]
    msg = c1.post(f"/api/sessions/{sid}/messages", json={"text": "你好"}).json()
    # help 卡(table)也登记 instance
    inst_id = msg["cards"][0]["instance"]
    assert (tmp_path / "platform_apps" / f"{inst_id}.json").is_file()

    app2 = create_platform_app(manager=manager, lab_store=None, artifacts_root=tmp_path)  # "重启"
    c2 = TestClient(app2)
    r = c2.get(f"/api/apps/{inst_id}")
    assert r.status_code == 200, "重启后旧卡 instance 可解析"
    assert r.json()["kind"] == "table"


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


# ---------------------------------------------------------------------------
# M3:run/debug/lab-draft 三 kind 的 action 转发(docs/APP-MODEL.md §8)
# ---------------------------------------------------------------------------


def _spawn(client, kind, ref, state):
    r = client.post("/api/apps/spawn", json={"kind": kind, "ref": ref, "state": state})
    assert r.status_code == 201, r.text
    return r.json()["instance"]["id"]


def test_m3_run_actions(client):
    """run.stop:在途 → 200 + state 回写;不在途 → 409。run.resume:无 checkpoint → 404。"""
    inst = _spawn(client, "run", "running-1", {"run_id": "running-1", "status": "running"})
    r = client.post(f"/api/apps/{inst}/actions/run.stop", json={"surface": "tab"})
    assert r.status_code == 200, r.text
    assert client.manager.stopped == ["running-1"], "薄 handler 转发既有 stop_run"
    assert r.json()["instance"]["state"]["status"] == "stopping", "state 回写"

    inst2 = _spawn(client, "run", "done-1", {"run_id": "done-1", "status": "done"})
    r409 = client.post(f"/api/apps/{inst2}/actions/run.stop", json={"surface": "tab"})
    assert r409.status_code == 409, "不在途 → 409(与旧 web stop 同归类)"

    inst3 = _spawn(client, "run", "no-ckpt", {"run_id": "no-ckpt", "status": "failed"})
    r404 = client.post(f"/api/apps/{inst3}/actions/run.resume", json={"surface": "tab"})
    assert r404.status_code == 404


def test_m3_debug_actions(client):
    """debug.continue/stop:command 由管道按 action id 注入;会话不存在 → 404。"""
    inst = _spawn(client, "debug", "s-1", {"session_id": "s-1", "run_id": "r1"})
    r = client.post(f"/api/apps/{inst}/actions/debug.continue", json={"surface": "tab"})
    assert r.status_code == 200, r.text
    r2 = client.post(f"/api/apps/{inst}/actions/debug.stop", json={"surface": "tab"})
    assert r2.status_code == 200, r2.text
    assert client.manager.debug_cmds == [("s-1", "continue"), ("s-1", "stop")]

    inst2 = _spawn(client, "debug", "missing", {"session_id": "missing"})
    r404 = client.post(f"/api/apps/{inst2}/actions/debug.continue", json={"surface": "tab"})
    assert r404.status_code == 404


def test_m3_draft_check_and_promote_surface(full_client):
    """lab-draft:draft.check 五关(与 Lab validate 同逻辑,返回 gate_report 卡);
    draft.promote 仅 tab 面(manifest 表面裁决,卡面调用 → 400)。"""
    client = full_client
    client.post("/api/lab/drafts", json={"name": "lab.m3"})
    # full_client 走旧 web 挂载:平台端点在 /platform 前缀下
    r0 = client.post("/platform/api/apps/spawn",
                     json={"kind": "lab-draft", "ref": "lab.m3", "state": {"name": "lab.m3", "root": "lab.m3"}})
    assert r0.status_code == 201, r0.text
    inst = r0.json()["instance"]["id"]
    r = client.post(f"/platform/api/apps/{inst}/actions/draft.check", json={"surface": "tab"})
    assert r.status_code == 200, r.text
    cards = r.json()["cards"]
    assert cards and cards[0]["type"] == "gate_report", "检查产出 gate_report 卡(同源逻辑)"

    r400 = client.post(f"/platform/api/apps/{inst}/actions/draft.promote", json={"surface": "card"})
    assert r400.status_code == 400 and "表面" in r400.json()["detail"], "promote 仅全面(重动作不上卡面)"


# ---------------------------------------------------------------------------
# M3.5 exec 三态同构迁移(docs/APP-MODEL.md v0.2 §4/§7/§9/§12)
# ---------------------------------------------------------------------------


def test_exec_required_and_modes():
    """协议:exec 缺省拒;mode 非法拒;endpoint/run 的 ref 未注册拒;local 带 ref 拒。"""
    reg = AppRegistry(known_skills=KNOWN)
    with pytest.raises(ValueError, match="缺 exec"):
        reg.register(_manifest(actions=[{"id": "a1", "label": "x", "args_from": [], "surface": ["card"]}]))
    with pytest.raises(ValueError, match="exec.mode 非法"):
        reg.register(_manifest(actions=[{"id": "a1", "label": "x",
                                         "exec": {"mode": "magic", "ref": "platform.plan.confirm"},
                                         "args_from": [], "surface": ["card"]}]))
    with pytest.raises(ValueError, match="未注册"):
        reg.register(_manifest(actions=[{"id": "a1", "label": "x",
                                         "exec": {"mode": "endpoint", "ref": "platform.evil"},
                                         "args_from": [], "surface": ["card"]}]))
    with pytest.raises(ValueError, match="local 态无 ref"):
        reg.register(_manifest(actions=[{"id": "a1", "label": "x",
                                         "exec": {"mode": "local", "ref": "platform.plan.confirm"},
                                         "args_from": [], "surface": ["card"]}]))
    # args_input 形态:必须 {name: schema}
    with pytest.raises(ValueError, match="args_input"):
        reg.register(_manifest(actions=[{"id": "a1", "label": "x",
                                         "exec": {"mode": "endpoint", "ref": "platform.plan.confirm"},
                                         "args_from": [], "args_input": ["warnings_ack"],
                                         "surface": ["card"]}]))


def test_legacy_skill_key_compat(caplog):
    """迁移期兼容:旧 skill 键归一为 exec.endpoint 并 warn(一个版本期)。"""
    reg = AppRegistry(known_skills=KNOWN)
    legacy = _manifest(actions=[{"id": "a1", "label": "x", "skill": "platform.plan.confirm",
                                 "args_from": [], "surface": ["card"]}])
    with caplog.at_level("WARNING", logger="agent_os.platform.apps"):
        reg.register(legacy)
    action = reg.action_of("test.app", "a1")
    assert action["exec"] == {"mode": "endpoint", "ref": "platform.plan.confirm"}, "归一为 exec"
    assert any("skill" in r.message and "exec" in r.message for r in caplog.records), "warn 在"


def test_conversation_local_actions_registered():
    """conversation 的 spawn/pin/close 声明为 local(归态在 manifest 上一眼可见)。"""
    reg = AppRegistry(known_skills=KNOWN)
    for m in default_manifests():
        reg.register(m)
    for aid in ("spawn", "pin", "close"):
        assert reg.action_of("conversation", aid)["exec"] == {"mode": "local"}


def test_authz_forged_args_from_rejected(client):
    """授权①:客户端伪装 args_from 字段(args 里塞 plan_id)→ 400 未声明。"""
    inst = _spawn(client, "publish", "plan-1", {"plan_id": "plan-1", "root": "lab.x"})
    r = client.post(f"/api/apps/{inst}/actions/plan.confirm",
                    json={"surface": "card", "args": {"plan_id": "evil-plan", "warnings_ack": True}})
    assert r.status_code == 400
    assert "args_input" in r.json()["detail"], "伪装 args_from 字段被 args_input 面拒"


def test_authz_state_is_server_authoritative(client):
    """授权②:args_from 绑定服务端 state(客户端 args 给空也是服务端值);
    args_input 不合 schema → 400。"""
    inst = _spawn(client, "run", "running-1", {"run_id": "running-1", "status": "running"})
    r = client.post(f"/api/apps/{inst}/actions/run.stop", json={"surface": "tab", "args": {}})
    assert r.status_code == 200
    assert client.manager.stopped == ["running-1"], "绑定的是服务端 state,不是客户端载荷"

    inst2 = _spawn(client, "publish", "plan-2", {"plan_id": "plan-2", "root": "lab.x"})
    r400 = client.post(f"/api/apps/{inst2}/actions/plan.confirm",
                       json={"surface": "card", "args": {"warnings_ack": "yes"}})
    assert r400.status_code == 400, "warnings_ack 须 boolean(schema 拒)"
    assert "不合 schema" in r400.json()["detail"]


def test_authz_exec_modes(client):
    """授权③:endpoint 动作无 run 副作用;local 动作经管道被拒且零调用。"""
    sid = client.post("/api/sessions").json()["id"]
    client.post(f"/api/sessions/{sid}/decisions/present")
    session = client.get(f"/api/sessions/{sid}").json()
    inst_id = next(c for m in session["messages"] for c in m["cards"] if c.get("instance"))["instance"]
    r = client.post(f"/api/apps/{inst_id}/actions/approve-once", json={"surface": "card"})
    assert r.status_code == 200
    assert client.manager.runs_started == [], "endpoint 动作不起 run(无 run 副作用)"

    # local:conversation 的 close 调到管道 → 400;manager 面零调用(不出海)
    # conversation instance 在 create_session 时登记,经 spawn 去重拿到它:
    conv_spawn = client.post("/api/apps/spawn", json={"kind": "conversation", "ref": sid})
    conv_id = conv_spawn.json()["instance"]["id"]
    before = (list(client.manager.stopped), list(client.manager.answered),
              list(client.manager.debug_cmds), list(client.manager.runs_started))
    r400 = client.post(f"/api/apps/{conv_id}/actions/close", json={"surface": "card"})
    assert r400.status_code == 400 and "local" in r400.json()["detail"]
    after = (list(client.manager.stopped), list(client.manager.answered),
             list(client.manager.debug_cmds), list(client.manager.runs_started))
    assert before == after, "local 动作零出海(无任何 handler/manager 调用)"


# ---------------------------------------------------------------------------
# M4a:run 真通道 + 发起面归一 + spawn state_schema 校验(docs/APP-MODEL.md §10)
# ---------------------------------------------------------------------------


def test_m4a_spawn_state_schema_validation(client):
    """spawn 初始 state 按 manifest state_schema 校验:不合 → 400(登记即伪造关闭)。"""
    bad = client.post("/api/apps/spawn", json={"kind": "run", "ref": "r1", "state": {"run_id": 123}})
    assert bad.status_code == 400
    assert "state_schema" in bad.json()["detail"]
    good = client.post("/api/apps/spawn",
                       json={"kind": "run", "ref": "r1", "state": {"run_id": "r1", "skill": "demo.fib"}})
    assert good.status_code == 201


@pytest.fixture()
def iterate_client(tmp_path: Path) -> TestClient:
    """真装配 + iterator brain(候选真写盘;run 通道的 run_id 有真实来源)。"""
    (tmp_path / "skills.yaml").write_text("skills: []\n", encoding="utf-8")
    cfg = tmp_path / "agent-os.toml"
    cfg.write_text(
        """
[run]
model = "mock/x"
compression = "off"
[providers.mock]
brain = "tests.web.test_lab_iterate:iterator_brain"
[tools]
builtins = true
python_exec = "off"
[skills]
path = "{skills}"
[lab]
drafts_root = "{drafts}"
""".format(skills=tmp_path / "skills.yaml", drafts=tmp_path / "drafts"),
        encoding="utf-8",
    )
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def test_m4a_iterate_run_channel(iterate_client):
    """exec.mode=run:iterate.generate → spawn run app 持 run_id(可进 run tab);
    running 态 = 持 run_id 且未终态(状态来自真实 run 记录,不发明标志位)。"""
    client = iterate_client
    client.post("/api/lab/drafts", json={"name": "lab.it"})
    inst = client.post("/platform/api/apps/spawn",
                       json={"kind": "diff", "ref": "lab.it", "state": {"name": "lab.it"}}).json()["instance"]["id"]
    r = client.post(f"/platform/api/apps/{inst}/actions/iterate.generate",
                    json={"surface": "tab", "args": {"note": ""}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"], "handler 透传 run_id(run_iterate 真 run)"
    run_inst = body.get("run_instance")
    assert run_inst and run_inst["kind"] == "run", "管道 spawn run app"
    assert run_inst["state"]["run_id"] == body["run_id"], "run app 持 run_id(v0.2 §4)"
    assert run_inst["state"]["status"] in ("done", "failed", "running"), "状态来自真实 run 记录"
    got = client.get(f"/platform/api/apps/{run_inst['id']}").json()
    assert got["state"]["run_id"] == body["run_id"], "run instance 持久化可解析(进 run tab 的锚)"


def test_m4a_run_launch(full_client, tmp_path):
    """发起面归一:run.launch 缺省骨架 → start_run → 新 run instance;
    客户端改参不合 schema → 400。"""
    client = full_client
    inst = client.post("/platform/api/apps/spawn",
                       json={"kind": "run", "ref": "seed", "state": {"run_id": "seed", "skill": "weather.query"}}
                       ).json()["instance"]["id"]
    r = client.post(f"/platform/api/apps/{inst}/actions/run.launch",
                    json={"surface": "tab", "args": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] and body.get("run_instance"), "起 run + spawn run app 持 run_id"
    # 产物面:runs/<new_id>/meta.json(start_run 异步起线程,短轮询等落盘)
    import time as _time

    metas = []
    for _ in range(50):
        metas = list((tmp_path / "runs").rglob("meta.json"))
        if any(body["skill"].encode() in m.read_bytes() for m in metas):
            break
        _time.sleep(0.1)
    assert any(body["skill"].encode() in m.read_bytes() for m in metas), "新 run 落了产物"

    bad = client.post(f"/platform/api/apps/{inst}/actions/run.launch",
                      json={"surface": "tab", "args": {"input": {"city": 123}}})
    assert bad.status_code == 400
    assert "inputs schema" in bad.json()["detail"], "改参不合 schema 被拒"

    good = client.post(f"/platform/api/apps/{inst}/actions/run.launch",
                       json={"surface": "tab", "args": {"input": {"city": "北京"}}})
    assert good.status_code == 200, "合法改参放行(用户可改的落点)"


# ---------------------------------------------------------------------------
# M4b:主动汇报 + SSE transport + legacy kinds(docs/APP-MODEL.md §10)
# ---------------------------------------------------------------------------


def test_m4b_runs_present_reports_to_owner_session(client, tmp_path):
    """主动汇报:本会话发起的 run(created_by 链回溯)到终态 → agent 消息 + 卡;
    幂等(游标随会话持久化);别会话发起的 run 不报。"""
    sid = client.post("/api/sessions").json()["id"]
    other = client.post("/api/sessions").json()["id"]
    # 产物层落一个失败 run(meta/result)
    run_dir = tmp_path / "runs" / "rep-1"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps({"run_id": "rep-1", "skill": "demo.fib",
                                                    "started_at": "2026-08-03T10:00:00+00:00"}))
    (run_dir / "result.json").write_text(json.dumps(
        {"status": "failed", "result": None, "error": "ProviderError: 401 token expired", "usage": {}}))
    # 本会话的发起链:tab spawn 的 run instance(created_by=sid);
    # 另挂一个别会话的(created_by=other)对照
    client.post("/api/apps/spawn", json={"kind": "run", "ref": "rep-1", "created_by": sid,
                                          "state": {"run_id": "rep-1", "skill": "demo.fib"}})
    client.post("/api/apps/spawn", json={"kind": "run", "ref": "rep-2", "created_by": other,
                                          "state": {"run_id": "rep-2", "skill": "demo.fib"}})
    r = client.post(f"/api/sessions/{sid}/runs/present")
    presented = r.json()["presented"]
    assert len(presented) == 1, "只报本会话发起的"
    msg = presented[0]
    assert "demo.fib" in msg["text"] and "失败" in msg["text"]
    assert "ProviderError" not in msg["text"], "人话摘要(N1 纪律)"
    assert msg["cards"][0]["data"]["ref"] == {"kind": "run", "id": "rep-1"}, "结果卡带 run 锚"
    assert msg["cards"][0]["instance"], "结果卡登记 instance(M1 同轨)"

    assert client.post(f"/api/sessions/{sid}/runs/present").json()["presented"] == [], "幂等"
    session = client.get(f"/api/sessions/{sid}").json()
    assert session.get("presented_runs") == ["rep-1"], "游标随会话持久化"

    # 管道 spawn 的二级 created_by 链(run app 的父级是 instance,父级的父级才是会话)
    parent = client.post("/api/apps/spawn", json={"kind": "skill_pack", "ref": "lab.chain",
                                                   "created_by": sid}).json()["instance"]["id"]
    run_dir2 = tmp_path / "runs" / "rep-3"
    run_dir2.mkdir(parents=True)
    (run_dir2 / "meta.json").write_text(json.dumps({"run_id": "rep-3", "skill": "demo.fib",
                                                     "started_at": "2026-08-03T11:00:00+00:00"}))
    (run_dir2 / "result.json").write_text(json.dumps({"status": "done", "result": {"x": 1},
                                                       "error": "", "usage": {}}))
    child = client.post("/api/apps/spawn", json={"kind": "run", "ref": "rep-3",
                                                  "created_by": parent,
                                                  "state": {"run_id": "rep-3", "skill": "demo.fib"}})
    assert child.status_code == 201
    r2 = client.post(f"/api/sessions/{sid}/runs/present").json()["presented"]
    assert len(r2) == 1 and "跑完了" in r2[0]["text"], "created_by 链回溯到会话(二级)"


def test_m4b_sse_stream(client):
    """SSE:路由注册在案 + 帧语义(_stream_diff 纯函数)。

    TestClient 会把响应体收完才返回(实测:无限 SSE 流连 headers 都拿不到),
    无限流不能走 TestClient——端点存在性用 openapi 面钉,帧语义用纯函数守。
    """
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/stream" in paths, "SSE 端点注册在案"

    from agent_os.host.web_platform.app import _stream_diff

    seen: dict[str, set[str] | None] = {"decisions": None, "terminal": None}
    # 首周期建基线,不补报存量
    assert _stream_diff(["esc-0"], [{"run_id": "r0", "skill": "a", "status": "done"}], seen) == []
    # pending 新增 → decision.new;终态新增 → run.finished;消失/重复不出帧
    events = _stream_diff(
        ["esc-0", "esc-1"],
        [{"run_id": "r0", "skill": "a", "status": "done"},
         {"run_id": "r1", "skill": "demo.fib", "status": "failed"}],
        seen,
    )
    assert ("decision.new", {"question_ids": ["esc-1"]}) in events
    assert ("run.finished", {"run_id": "r1", "skill": "demo.fib", "status": "failed"}) in events
    assert _stream_diff(["esc-0"], [{"run_id": "r0", "skill": "a", "status": "done"}], seen) == []


# ---------------------------------------------------------------------------
# M5:shell app 化 + widget 寻址(docs/APP-MODEL.md §13/§14/§15)
# ---------------------------------------------------------------------------


def _shell(client):
    return client.get("/api/shell").json()


def _shell_act(client, shell_id, action, args=None):
    return client.post(f"/api/apps/{shell_id}/actions/{action}",
                       json={"surface": "tab", "args": args or {}})


def test_m5_shell_bootstrap_and_tab_actions(client):
    """bootstrap 实例化(conv 恒首);tab.open 去重聚焦/focus/close 回落;
    全部经管道(local mutator 仅改 state,不出海)。"""
    shell = _shell(client)
    assert shell["kind"] == "shell"
    assert shell["state"]["tabs"][0]["id"] == "conv", "bootstrap:conv 恒首"
    assert shell["state"]["active_tab"] == "conv"
    sid = shell["id"]

    r = _shell_act(client, sid, "shell.tab.open",
                   {"id": "d:run:r1", "instance_id": "app-r1", "kind": "run", "ref": "r1", "title": "运行详情"})
    assert r.status_code == 200, r.text
    state = _shell(client)["state"]
    assert state["active_tab"] == "d:run:r1"
    assert len(state["tabs"]) == 2
    # kind+ref 去重(不同 id 同 ref → 聚焦不重复开)
    _shell_act(client, sid, "shell.tab.open",
               {"id": "d:run:r1-b", "kind": "run", "ref": "r1", "title": "运行详情"})
    state = _shell(client)["state"]
    assert len(state["tabs"]) == 2, "同 kind+ref 去重"
    assert state["active_tab"] == "d:run:r1", "聚焦已有 tab"
    # focus/close
    _shell_act(client, sid, "shell.tab.focus", {"tab": "conv"})
    assert _shell(client)["state"]["active_tab"] == "conv"
    _shell_act(client, sid, "shell.tab.focus", {"tab": "d:run:r1"})
    _shell_act(client, sid, "shell.tab.close", {"tab": "d:run:r1"})
    state = _shell(client)["state"]
    assert [t["id"] for t in state["tabs"]] == ["conv"]
    assert state["active_tab"] == "conv", "关闭回落 conversation(关闭≠销毁语义)"


def test_m5_shell_layout_persistence(tmp_path):
    """布局持久化:move_tab 重排 + icon_mode 开关 → 新 app(同 root)重启恢复。"""
    manager = _FakeManager()
    c1 = TestClient(create_platform_app(manager=manager, lab_store=None, artifacts_root=tmp_path))
    sid = _shell(c1)["id"]
    for tid, ref in (("d:run:r1", "r1"), ("d:gate:g1", "g1")):
        _shell_act(c1, sid, "shell.tab.open",
                   {"id": tid, "kind": tid.split(":")[1], "ref": ref, "title": tid})
    _shell_act(c1, sid, "shell.layout.move_tab", {"tab": "d:gate:g1", "before": "d:run:r1"})
    _shell_act(c1, sid, "shell.layout.set", {"icon_mode": True})
    _shell_act(c1, sid, "shell.theme.set", {"theme": "ink"})

    c2 = TestClient(create_platform_app(manager=manager, lab_store=None, artifacts_root=tmp_path))
    state = _shell(c2)["state"]
    assert [t["id"] for t in state["tabs"]] == ["conv", "d:gate:g1", "d:run:r1"], "重排持久化"
    assert state["layout"]["icon_mode"] is True, "图标列开关持久化"
    assert state["theme"] == "ink", "主题偏好持久化(endpoint 态)"


def test_m5_shell_session_create(client):
    """shell.session.create(endpoint):新会话 + conversation instance 登记。"""
    sid = _shell(client)["id"]
    r = _shell_act(client, sid, "shell.session.create")
    assert r.status_code == 200, r.text
    session = r.json()["session"]
    assert client.get(f"/api/sessions/{session['id']}").status_code == 200


def test_m5_widget_registry(client):
    """widget 注册/解析(read 人话摘要)/focus 同源/注销后 404。"""
    path = "/shell/tab/app-r1/surface/tab/section/findings"
    assert client.post("/api/widgets/register",
                       json={"path": path, "kind": "gate_report", "summary_hint": "4 项通过,1 个建议"}
                       ).status_code == 200
    got = client.get(f"/api/widgets{path}").json()
    assert got["summary"] == "4 项通过,1 个建议", "read = 人话摘要(摘要层文字)"
    assert client.post("/api/widgets/read", json={"path": path}).json()["path"] == path
    assert client.post("/api/widgets/focus", json={"path": path}).json()["ok"] is True
    assert client.get("/api/widgets/shell/tab/nope").status_code == 404, "未注册 404"
    assert client.post("/api/widgets/focus", json={"path": "/nope"}).status_code == 404, "focus 与 read 同源 404"
    client.post("/api/widgets/unregister", json={"path": path})
    assert client.get(f"/api/widgets{path}").status_code == 404, "注销后 404"


def test_m5_desktop_minimize_and_wallpaper(tmp_path):
    """桌面化 root widget(M5 增补):desktop 默认键在;bootstrap 后最小化 =
    无激活 tab(active_tab "",tab 保留);壁纸开关持久化(重启恢复)。"""
    manager = _FakeManager()
    c1 = TestClient(create_platform_app(manager=manager, lab_store=None, artifacts_root=tmp_path))
    shell = _shell(c1)
    assert shell["state"]["desktop"] == {"pinned": [], "wallpaper": True}, "desktop 默认键"
    sid = shell["id"]
    _shell_act(c1, sid, "shell.tab.open",
               {"id": "d:run:r1", "kind": "run", "ref": "r1", "title": "运行详情"})
    r = _shell_act(c1, sid, "shell.tab.minimize")
    assert r.status_code == 200, r.text
    state = _shell(c1)["state"]
    assert state["active_tab"] == "", "最小化 = 无激活 tab(桌面主屏)"
    assert [t["id"] for t in state["tabs"]] == ["conv", "d:run:r1"], "最小化≠关闭(tab 保留)"
    # 壁纸开关:local 态,参数过 args_input schema(非 bool 拒)
    assert _shell_act(c1, sid, "shell.desktop.set", {"wallpaper": "x"}).status_code == 400
    _shell_act(c1, sid, "shell.desktop.set", {"wallpaper": False})
    assert _shell(c1)["state"]["desktop"]["wallpaper"] is False

    c2 = TestClient(create_platform_app(manager=manager, lab_store=None, artifacts_root=tmp_path))
    state = _shell(c2)["state"]
    assert state["desktop"]["wallpaper"] is False, "壁纸开关持久化(重启恢复)"
    assert state["active_tab"] == "", "桌面态持久化"
