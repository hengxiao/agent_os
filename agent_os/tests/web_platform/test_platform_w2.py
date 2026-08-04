"""web_platform W2 锚点测试(docs/WEB-PLATFORM.md §11)。

- escalation 卡协议:注册/校验/options 按档原样携带(L2 三枚、L3 两枚);
- decisions:聚合(tier_human 人话字段、普通 question 过滤、收件箱异常降级空表)、
  作答经 action 管道收编(§17.7-4:ok / 404 归类与旧 web 收件箱一致;专属端点退役);
- present 轮询汇聚:新 pending → agent 消息 + escalation 卡落会话(持久化)+ 幂等;
- LLM 意图路由(scripted MockProvider,不碰真 LLM):命中各意图 /
  故障回落规则 / schema 不合回落;browse 意图(时间窗过滤 + 逐行详情锚)。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_os.api.v1 import ChatResponse, Message, Role
from agent_os.host.web_platform.app import create_platform_app
from agent_os.host.web_platform.artifacts import (
    CARD_TYPES,
    build_escalation_card,
    validate_card,
)
from agent_os.host.web_platform.orchestrator import Orchestrator
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

#: 升权 pending 行(InboxChannel.pending() 形态;context 原样直通升权载荷)
ESC_ROW_L3: dict[str, Any] = {
    "question_id": "esc-1",
    "run_id": "r1",
    "frame_id": "f0",
    "question": "升权确认:root 帧(none → irreversible)请求调用 ops.janitor,批准本次执行?",
    "context": {
        "skill": "ops.janitor",
        "tier": "irreversible",
        "params": {"path": "/tmp/x"},
        "requested": {"tools": ["fs.write"], "skills": []},
        "reason_hint": "none → irreversible",
    },
    "options": ["approve-once", "deny"],
    "urgency": "high",
    "previous_error": None,
    "asked_at": 1730000000,
    "kind": "escalation",
}
QUESTION_ROW: dict[str, Any] = {
    "question_id": "q-1",
    "run_id": "r1",
    "frame_id": "f0",
    "question": "普通提问",
    "context": {},
    "options": None,
    "urgency": "normal",
    "previous_error": None,
    "asked_at": 1730000001,
}


class _FakeManager:
    """decisions 面的假 run_manager:只实现 supervisor 收件箱两个方法。"""

    def __init__(self, pending: list[dict[str, Any]] | None = None) -> None:
        self._pending = list(pending or [])
        self.answered: list[tuple[str, str]] = []

    def supervisor_pending(self) -> list[dict[str, Any]]:
        return self._pending

    def supervisor_answer(self, question_id: str, answer: str) -> None:
        if question_id == "missing":
            raise KeyError(f"找不到 supervisor 问题: {question_id}")
        if answer == "bad-answer":
            raise ValueError(f"答案 {answer!r} 不在 options 内")
        self.answered.append((question_id, answer))

    # create_platform_app 装配期触碰的面(LLM 后端装配失败 → 纯规则,正是默认安全态)
    def shared_skills_registry(self) -> Any:
        return None

    def assemble_lab_kernel(self, overlay: Any) -> Any:
        raise RuntimeError("无内核(测试面)")


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    manager = _FakeManager(pending=[ESC_ROW_L3, QUESTION_ROW])
    app = create_platform_app(manager=manager, lab_store=None, artifacts_root=tmp_path)
    client = TestClient(app)
    client.manager = manager  # type: ignore[attr-defined] — 测试断言用
    return client


# ---------------------------------------------------------------------------
# escalation 卡协议
# ---------------------------------------------------------------------------


def test_escalation_card_protocol():
    """卡型注册;options 按档原样携带(L2 含 approve-run,L3 无);缺锚即拒。"""
    assert "escalation" in CARD_TYPES
    l2 = build_escalation_card(
        question_id="esc-2", skill="ops.janitor", tier="reversible",
        options=["approve-once", "approve-run", "deny"],
    )
    l3 = build_escalation_card(
        question_id="esc-3", skill="ops.janitor", tier="irreversible",
        options=["approve-once", "deny"],
    )
    validate_card(l2)
    validate_card(l3)
    assert "approve-run" in l2["data"]["options"], "L2 三枚"
    assert "approve-run" not in l3["data"]["options"], "L3 无 approve-run(ESCALATION §3)"
    with pytest.raises(ValueError, match="question_id"):
        build_escalation_card(question_id="", skill="ops.janitor", tier="none")


# ---------------------------------------------------------------------------
# decisions 聚合与作答
# ---------------------------------------------------------------------------


def test_decisions_aggregate(client):
    """聚合:只收 kind=escalation(普通 question 不进对话中枢);tier_human 人话随行。"""
    rows = client.get("/api/decisions").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["question_id"] == "esc-1"
    assert row["skill"] == "ops.janitor"
    assert row["tier"] == "irreversible"
    assert row["tier_human"] == "不可逆需审批"
    assert row["params"] == {"path": "/tmp/x"}
    assert row["requested"] == {"tools": ["fs.write"], "skills": []}
    assert row["options"] == ["approve-once", "deny"]


def test_decisions_aggregate_degrades(tmp_path):
    """收件箱面异常 → 空列表(决策轮询不能打断对话)。"""
    class _Broken(_FakeManager):
        def supervisor_pending(self) -> list[dict[str, Any]]:
            raise RuntimeError("inbox down")

    app = create_platform_app(manager=_Broken(), lab_store=None, artifacts_root=tmp_path)
    assert TestClient(app).get("/api/decisions").json() == []


def test_decisions_answer_forwarding(client):
    """作答收编(§17.7-4):经 escalation app 的 action 管道(platform.decision.answer
    技能)——ok 落 run_manager;missing → 404(已被处理,归类保留);答案非法 →
    manifest 裁决 404(比专属端点的 manager 400 更早一层);专属端点退役。"""
    def _answer(qid: str, action: str):
        inst = client.post("/api/apps/spawn", json={
            "kind": "escalation", "ref": qid,
            "state": {"question_id": qid, "skill": "ops.janitor"},
        }).json()["instance"]["id"]
        return client.post(f"/api/apps/{inst}/actions/{action}", json={"surface": "card"})

    r = _answer("esc-1", "approve-once")
    assert r.status_code == 200
    assert client.manager.answered == [("esc-1", "approve-once")]

    r404 = _answer("missing", "approve-once")
    assert r404.status_code == 404, "已被处理/不存在 → 404(与旧收件箱同归类)"
    rbad = _answer("esc-1", "bad-answer")
    assert rbad.status_code == 404 and "无 action" in rbad.json()["detail"], "manifest 裁决先于 manager"
    assert (
        client.post("/api/decisions/esc-1", json={"answer": "approve-once"}).status_code == 404
    ), "作答专属端点已退役(§17.7-4)"


# ---------------------------------------------------------------------------
# present 轮询汇聚
# ---------------------------------------------------------------------------


def test_present_inserts_and_is_idempotent(client):
    """新 pending → agent 消息 + escalation 卡落会话并持久化;再轮询幂等(不重复)。"""
    sid = client.post("/api/sessions").json()["id"]
    r = client.post(f"/api/sessions/{sid}/decisions/present")
    presented = r.json()["presented"]
    assert len(presented) == 1
    card = presented[0]["cards"][0]
    assert card["type"] == "escalation"
    assert card["data"]["question_id"] == "esc-1"
    assert card["data"]["reason_hint"] == "none → irreversible", "技术面随卡(详情层)"

    session = client.get(f"/api/sessions/{sid}").json()
    assert any(c.get("type") == "escalation" for m in session["messages"] for c in m["cards"]), "持久化"

    again = client.post(f"/api/sessions/{sid}/decisions/present").json()["presented"]
    assert again == [], "同一 pending 不重复呈现"

    r404 = client.post("/api/sessions/deadbeef/decisions/present")
    assert r404.status_code == 404


# ---------------------------------------------------------------------------
# LLM 意图路由(scripted MockProvider)
# ---------------------------------------------------------------------------


def _resp(text: str) -> ChatResponse:
    return ChatResponse(message=Message(role=Role.ASSISTANT, content=text))


def _llm_orch(script: list[ChatResponse], runs: list[dict[str, Any]] | None = None) -> Orchestrator:
    pm = ProviderManager([MockProvider(script=list(script))])
    return Orchestrator(runs_provider=lambda: list(runs or []), provider=pm, model="mock/x")


def test_llm_route_create_skill():
    """LLM 命中 create_skill:goal 用于命名(lab.<goal>)。"""
    orch = _llm_orch([_resp('{"intent": "create_skill", "goal": "weather"}')])
    msg = orch.handle({"messages": []}, "我想要一个能查天气的东西")
    card = msg["cards"][0]
    assert card["type"] == "plan"
    assert card["data"]["create"][0]["name"] == "lab.weather"


def test_llm_route_browse_with_timeframe():
    """LLM 命中 browse:timeframe=上周 → 近 7 天过滤;逐行 run 详情锚。"""
    now = time.time()
    runs = [
        {"run_id": "old12345", "skill": "ops.janitor", "status": "failed", "error": "x", "ts": now - 10 * 86400},
        {"run_id": "new12345", "skill": "weather.query", "status": "done", "error": "", "ts": now - 3600},
        {"run_id": "new67890", "skill": "demo.fib", "status": "failed", "error": "ProviderError: down", "ts": now - 60},
    ]
    orch = _llm_orch([_resp('{"intent": "browse", "timeframe": "上周"}')], runs)
    msg = orch.handle({"messages": []}, "上周哪些失败了")
    card = msg["cards"][0]
    assert card["type"] == "table"
    ids = [r[0] for r in card["data"]["rows"]]
    assert "old12345"[:8] not in ids, "10 天前的被时间窗滤掉"
    assert len(card["data"]["rows"]) == 2
    assert card["data"]["row_refs"][0] == {"kind": "run", "id": "new12345"}, "逐行详情锚"
    # 失败行给人话摘要(N1),成功行 status_human
    assert card["data"]["rows"][0][2] == "运行成功"
    assert card["data"]["rows"][1][2] == "模型服务不可用"
    assert "ProviderError" not in card["data"]["rows"][1][2], "行级摘要零类名泄漏(N1)"


def test_llm_route_failure_falls_back_to_rules():
    """LLM 不可用(脚本耗尽 → ProviderError)→ 规则路由接住,对话不中断。"""
    orch = _llm_orch([])  # 空脚本:chat 即抛 UNAVAILABLE
    msg = orch.handle({"messages": []}, "刚才那个 run 为什么挂了")
    assert msg["cards"][0]["type"] == "table", "规则 why_failed 接住"


def test_llm_route_bad_schema_falls_back():
    """LLM 输出非 JSON / intent 越界 → 都回落规则。"""
    orch = _llm_orch([_resp("I think you want a skill")])
    msg = orch.handle({"messages": []}, "帮我做个 weather 技能")
    assert msg["cards"][0]["type"] == "plan", "非 JSON → 规则 create_skill"

    orch2 = _llm_orch([_resp('{"intent": "evil.takeover"}')])
    msg2 = orch2.handle({"messages": []}, "你好")
    assert msg2["cards"][0]["data"]["title"] == "我能做什么", "intent 越界 → 规则 help"


def test_no_provider_is_pure_rules():
    """未装配 provider(装配失败安全态)→ 纯规则,不碰 LLM。"""
    orch = Orchestrator(runs_provider=list)
    assert orch.handle({"messages": []}, "最近有哪些 run")["cards"][0]["data"]["title"] == "最近的运行"


# ---------------------------------------------------------------------------
# 实测断点回归( orchestrator 层三个"根本不可用")
# ---------------------------------------------------------------------------


def _registry(tmp_path: Path, extra: str = ""):
    """两技能的生产 registry:weather.query(生产)+ 可选附加(如 lab.* 草稿名)。"""
    from agent_os.skills.local_file import LocalFileSkillRegistry

    (tmp_path / "skills.yaml").write_text(
        "skills:\n"
        "  - name: weather.query\n"
        "    version: 1.0.0\n"
        "    kind: prompt\n"
        "    description: 按天气推荐晚餐。Use when 查晚餐;Do not use when 其他。\n"
        "    permissions: { tools: [], skills: [] }\n"
        "    prompt: 查。\n"
        + extra,
        encoding="utf-8",
    )
    return LocalFileSkillRegistry(str(tmp_path / "skills.yaml"))


def test_plan_create_name_unique_and_mutex(tmp_path):
    """断点 1:create 名撞占用 → 自动唯一后缀;reuse 与 create 互斥。"""
    production = _registry(tmp_path)
    orch = Orchestrator(
        skills=production,
        name_taken=lambda n: n in {"lab.weather", "lab.weather2"},  # 草稿层已有两个
    )
    msg = orch.handle({"messages": []}, "帮我做个 weather 相关的技能")
    data = msg["cards"][0]["data"]
    create_name = data["create"][0]["name"]
    assert create_name == "lab.weather3", "跳过两个占用名"
    reuse_names = [r["name"] for r in data["reuse"]]
    assert create_name not in reuse_names, "reuse/create 互斥"
    approve = msg["cards"][0]["actions"][0]
    assert approve["payload"]["name"] == create_name, "批准载荷 = 唯一名"


def test_plan_llm_name_adopted(tmp_path):
    """断点 1:LLM 路由的 name 建议被采用(不合规 name 清洗后回落规则命名)。"""
    production = _registry(tmp_path)
    orch = Orchestrator(
        skills=production,
        provider=ProviderManager([MockProvider(script=[_resp('{"intent": "create_skill", "goal": "dinner", "name": "Dinner.Planner!"}')])]),
        model="mock/x",
    )
    msg = orch.handle({"messages": []}, "帮我规划晚餐")
    assert msg["cards"][0]["data"]["create"][0]["name"] == "dinner.planner", "清洗后采用"

    orch2 = Orchestrator(
        skills=production,
        provider=ProviderManager([MockProvider(script=[_resp('{"intent": "create_skill", "goal": "dinner", "name": "!!!"}')])]),
        model="mock/x",
    )
    msg2 = orch2.handle({"messages": []}, "帮我规划晚餐")
    assert msg2["cards"][0]["data"]["create"][0]["name"] == "lab.dinner", "不合规 name → 规则命名"


def test_plan_rule_fallback_unique_name(tmp_path):
    """断点 1:规则回落(无 LLM)也保唯一;topic 是兜底 custom 时 reuse 给空。"""
    production = _registry(tmp_path)
    orch = Orchestrator(skills=production, name_taken=lambda n: n == "lab.custom")
    msg = orch.handle({"messages": []}, "帮我做个技能")
    data = msg["cards"][0]["data"]
    assert data["create"][0]["name"] == "lab.custom2"
    assert data["reuse"] == [], "custom 无语义依据,不硬塞复用"


def test_reuse_only_production_and_semantic(tmp_path):
    """断点 2:草稿(lab.*)不进 reuse;生产技能须主题词命中名字/描述才进。"""
    production = _registry(
        tmp_path,
        extra=(
            "  - name: lab.weather\n"
            "    version: 0.1.0\n"
            "    kind: prompt\n"
            "    description: weather 草稿(未发布)。\n"
            "    permissions: { tools: [], skills: [] }\n"
            "    prompt: 草稿。\n"
            "  - name: ops.janitor\n"
            "    version: 1.0.0\n"
            "    kind: prompt\n"
            "    description: 清理日志。Use when 清理;Do not use when 其他。\n"
            "    permissions: { tools: [], skills: [] }\n"
            "    prompt: 清。\n"
        ),
    )
    orch = Orchestrator(skills=production)
    data = orch.handle({"messages": []}, "帮我做个 weather 相关的技能")["cards"][0]["data"]
    names = [r["name"] for r in data["reuse"]]
    assert names == ["weather.query"], "草稿 lab.weather 不进;不相关的 ops.janitor 不进"


def test_browse_reads_artifacts_layer(tmp_path):
    """断点 3:runs 数据源 = 产物层(重启后内存空依然有数据),browse/why_failed 共用。"""
    from agent_os.host.web_platform.app import _recent_runs

    runs_dir = tmp_path / "runs"
    for run_id, skill, status, error, started in [
        ("aaa111", "demo.fib", "done", "", "2026-08-01T10:00:00+00:00"),
        ("bbb222", "demo.fib", "failed", "outputs 校验失败: 缺 answer", "2026-08-02T10:00:00+00:00"),
    ]:
        d = runs_dir / run_id
        d.mkdir(parents=True)
        (d / "meta.json").write_text(
            json.dumps({"run_id": run_id, "skill": skill, "started_at": started}), encoding="utf-8"
        )
        (d / "result.json").write_text(
            json.dumps({"status": status, "result": None, "error": error, "usage": {}}), encoding="utf-8"
        )

    class _EmptyManager:  # 内存态为空 = 进程重启后
        def active_items(self) -> list:
            return []

    runs = _recent_runs(_EmptyManager(), tmp_path)
    assert [r["run_id"] for r in runs] == ["aaa111", "bbb222"], "产物层枚举,ts 升序"
    assert runs[1]["status"] == "failed"
    assert "outputs" in runs[1]["error"], "失败行带错误摘要"

    # 经 orchestrator:browse 出非空行 + 逐行锚;why_failed 取最新失败
    orch = Orchestrator(runs_provider=lambda: _recent_runs(_EmptyManager(), tmp_path))
    browse = orch.handle({"messages": []}, "最近有哪些 run")["cards"][0]["data"]
    assert len(browse["rows"]) == 2
    assert browse["row_refs"][1] == {"kind": "run", "id": "bbb222"}
    why = orch.handle({"messages": []}, "为什么挂了")["cards"][0]["data"]
    assert why["rows"][0][1] == "demo.fib"
    assert why["ref"] == {"kind": "run", "id": "bbb222"}
