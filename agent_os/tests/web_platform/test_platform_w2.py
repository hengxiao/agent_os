"""web_platform W2 锚点测试(docs/WEB-PLATFORM.md §11)。

- escalation 卡协议:注册/校验/options 按档原样携带(L2 三枚、L3 两枚);
- decisions:聚合(tier_human 人话字段、普通 question 过滤、收件箱异常降级空表)、
  作答转发(ok / 404 / 400 归类与旧 web 收件箱一致);
- present 轮询汇聚:新 pending → agent 消息 + escalation 卡落会话(持久化)+ 幂等;
- LLM 意图路由(scripted MockProvider,不碰真 LLM):命中各意图 /
  故障回落规则 / schema 不合回落;browse 意图(时间窗过滤 + 逐行详情锚)。
"""

from __future__ import annotations

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
    """作答转发:ok 落 run_manager;missing → 404;answer 不合 options → 400。"""
    r = client.post("/api/decisions/esc-1", json={"answer": "approve-once"})
    assert r.status_code == 200
    assert client.manager.answered == [("esc-1", "approve-once")]

    r404 = client.post("/api/decisions/missing", json={"answer": "approve-once"})
    assert r404.status_code == 404
    r400 = client.post("/api/decisions/esc-1", json={"answer": "bad-answer"})
    assert r400.status_code == 400


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
    # 失败行给错误摘要,成功行人话
    assert card["data"]["rows"][0][2] == "运行成功"
    assert "ProviderError" in card["data"]["rows"][1][2]


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
