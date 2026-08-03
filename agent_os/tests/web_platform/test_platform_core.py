"""web_platform 后端骨架锚点测试(docs/WEB-PLATFORM.md §3-§6)。

- sessions:创建/读取/追加/列表(标题回填、摘要卡数、坏文件隔离);
- artifacts:六卡型 schema 校验、action 白名单拒绝(非白名单 id / 端点不符);
- orchestrator:三意图(做技能 plan 卡 + 复用检索、为什么挂 table 卡、help 卡)。
"""

from __future__ import annotations

import pytest

from agent_os.host.web_platform.artifacts import (
    ACTION_WHITELIST,
    build_diff_card,
    build_gate_report_card,
    build_plan_card,
    build_publish_card,
    build_skill_pack_card,
    build_table_card,
    validate_card,
)
from agent_os.host.web_platform.orchestrator import Orchestrator
from agent_os.host.web_platform.sessions import SessionStore, new_message
from agent_os.skills.local_file import LocalFileSkillRegistry

# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


def test_sessions_create_append_list(tmp_path):
    """创建 → 追加(user/agent)→ 读取一致;标题取首条用户消息;摘要带卡数。"""
    store = SessionStore(tmp_path / "platform_sessions")
    session = store.create()
    sid = session["id"]

    store.append(sid, new_message("user", text="帮我做个按天气推荐晚餐的技能"))
    agent_msg = new_message("agent", text="计划如下", cards=[{"type": "plan", "v": 1, "data": {}, "actions": []}])
    store.append(sid, agent_msg)

    got = store.get(sid)
    assert len(got["messages"]) == 2
    assert got["title"].startswith("帮我做")
    assert got["messages"][1]["cards"][0]["type"] == "plan"

    rows = store.list()
    assert len(rows) == 1
    assert rows[0]["messages"] == 2
    assert rows[0]["cards"] == 1

    # 坏 id 防穿越;坏文件隔离
    with pytest.raises(FileNotFoundError):
        store.get("../../etc/passwd")
    (tmp_path / "platform_sessions" / "broken.json").write_text("{bad json", encoding="utf-8")
    assert len(store.list()) == 1, "坏文件不拖垮列表"


# ---------------------------------------------------------------------------
# artifacts:六卡型 + 白名单
# ---------------------------------------------------------------------------


def test_all_card_types_build_and_validate():
    """六卡型 build 均过 schema 校验(出服务端前的协议闸)。"""
    cards = [
        build_plan_card(goal="做个晚餐技能", reuse=[], create=[{"name": "lab.custom", "template": "prompt_query", "reason": "r"}], approve_payload={"name": "lab.custom"}),
        build_skill_pack_card(name="lab.custom", tier="reversible", members=["lab.custom"]),
        build_gate_report_card(draft="lab.custom", status="pass", gates={}, plan_payload={"root": "lab.custom"}),
        build_diff_card(name="lab.custom", diff={"has_changes": True, "members": []}),
        build_publish_card(root="lab.custom", members=[], plan_id="plan-1"),
        build_table_card(title="t", columns=["a"], rows=[["b"]]),
    ]
    assert {c["type"] for c in cards} == {"plan", "skill_pack", "gate_report", "diff", "publish", "table"}
    for card in cards:
        validate_card(card)  # 不抛即过


def test_action_whitelist_rejects(tmp_path):
    """白名单裁决:非白名单 action id / 端点与白名单不符 / 坏卡型 全部拒绝。"""
    card = build_plan_card(goal="g", reuse=[], create=[{"name": "lab.x", "template": "t", "reason": "r"}], approve_payload={})
    card["actions"].append({"id": "evil.delete", "label": "x", "method": "POST", "endpoint": "/api/evil"})
    with pytest.raises(ValueError, match="白名单"):
        validate_card(card)

    good = ACTION_WHITELIST["scaffold.approve"]
    bad = build_plan_card(goal="g", reuse=[], create=[{"name": "lab.x", "template": "t", "reason": "r"}], approve_payload={})
    bad["actions"][0]["endpoint"] = "/api/lab/evil"
    with pytest.raises(ValueError, match="端点应为"):
        validate_card(bad)
    assert good == ("POST", "/api/lab/drafts")

    with pytest.raises(ValueError, match="未知卡型"):
        validate_card({"type": "evil", "v": 1, "data": {}, "actions": []})


# ---------------------------------------------------------------------------
# orchestrator 三意图
# ---------------------------------------------------------------------------


@pytest.fixture()
def production(tmp_path):
    path = tmp_path / "skills.yaml"
    path.write_text(
        "skills:\n"
        "  - name: weather.query\n"
        "    version: 1.0.0\n"
        "    kind: prompt\n"
        "    description: 按天气推荐晚餐。Use when 查晚餐;Do not use when 其他。\n"
        "    permissions: { tools: [], skills: [] }\n"
        "    prompt: 查。\n",
        encoding="utf-8",
    )
    return LocalFileSkillRegistry(str(path))


def test_intent_make_skill_plan_card(production):
    """意图①:plan 卡含复用(主题词命中生产技能)+ 新建建议 + 批准动作。"""
    orch = Orchestrator(skills=production)
    msg = orch.handle({"messages": []}, "帮我做个 weather 相关的技能")
    assert msg["role"] == "agent"
    card = msg["cards"][0]
    assert card["type"] == "plan"
    assert card["data"]["reuse"], "主题词应命中 weather.query"
    assert card["data"]["reuse"][0]["name"] == "weather.query"
    assert card["data"]["create"]
    approve = card["actions"][0]
    assert approve["id"] == "scaffold.approve"
    assert approve["endpoint"] == "/api/lab/drafts"


def test_intent_why_failed_table_card(production):
    """意图②:最近失败 run 的摘要 table 卡;无失败时明确说明。"""
    runs = [
        {"run_id": "a1b2c3d4", "skill": "weather.query", "status": "done", "error": ""},
        {"run_id": "e5f6a7b8", "skill": "ops.janitor", "status": "failed", "error": "outputs 校验失败: 缺 answer"},
    ]
    orch = Orchestrator(skills=production, runs_provider=lambda: runs)
    msg = orch.handle({"messages": []}, "刚才那个 run 为什么挂了")
    card = msg["cards"][0]
    assert card["type"] == "table"
    assert card["data"]["rows"][0][1] == "ops.janitor"
    assert "outputs" in card["data"]["rows"][0][2]

    orch2 = Orchestrator(skills=production, runs_provider=list)
    msg2 = orch2.handle({"messages": []}, "为什么失败了")
    assert "没有失败" in msg2["text"]


def test_intent_help_card(production):
    """意图③:其他 → help 卡(三句引导)。"""
    orch = Orchestrator(skills=production)
    msg = orch.handle({"messages": []}, "你好")
    assert msg["cards"][0]["type"] == "table"
    assert len(msg["cards"][0]["data"]["rows"]) == 3
    # LLM 路由接口:注入 provider 走 _route_llm,返回面一致
    orch2 = Orchestrator(skills=production, provider=object())
    assert orch2.handle({"messages": []}, "帮我做个 weather 技能")["cards"][0]["type"] == "plan"
