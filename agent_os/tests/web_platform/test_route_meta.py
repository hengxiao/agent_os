"""N6 路由可观测(O6,B6;docs/FLOWS-OPTIMIZATION.md 循环 3)。

agent 消息 meta 与 plan 卡 data.route_meta 标注路由来源:
LLM 命中 → {"route": "llm"};故障回落 → {"route": "rule", reason} —
reason 是机器码(llm_unavailable/llm_bad_schema),LLM 失败率可按其统计;
人话文案在前端 copy(六主题),后端不出中文。
"""

from __future__ import annotations

from agent_os.api.v1 import ChatResponse, Message, Role
from agent_os.host.web_platform.orchestrator import Orchestrator
from agent_os.providers.manager import ProviderManager
from agent_os.providers.mock import MockProvider


def _resp(text: str) -> ChatResponse:
    return ChatResponse(message=Message(role=Role.ASSISTANT, content=text))


def _orch(script: list[ChatResponse]) -> Orchestrator:
    pm = ProviderManager([MockProvider(script=list(script))])
    return Orchestrator(runs_provider=list, provider=pm, model="mock/x")


def test_llm_hit_marks_llm():
    """LLM 命中:消息 meta 与 plan 卡 route_meta 都标 llm(无 reason)。"""
    orch = _orch([_resp('{"intent": "create_skill", "goal": "weather", "name": "dinner.planner"}')])
    msg = orch.handle({"messages": []}, "帮我做个晚餐技能")
    assert msg["meta"] == {"route": "llm"}
    assert msg["cards"][0]["data"]["route_meta"] == {"route": "llm"}
    assert msg["cards"][0]["data"]["create"][0]["name"] == "dinner.planner"


def test_llm_failure_marks_rule_with_reason():
    """LLM 不可用:route=rule + reason=llm_unavailable(可统计的机器码)。"""
    orch = _orch([])  # 空脚本:chat 即抛 UNAVAILABLE
    msg = orch.handle({"messages": []}, "帮我做个 weather 技能")
    assert msg["meta"]["route"] == "rule"
    assert msg["meta"]["reason"] == "llm_unavailable"
    assert msg["cards"][0]["data"]["route_meta"]["reason"] == "llm_unavailable"


def test_llm_bad_schema_marks_rule_with_reason():
    """LLM 输出不合 schema:route=rule + reason=llm_bad_schema。"""
    orch = _orch([_resp("not json at all")])
    msg = orch.handle({"messages": []}, "帮我做个 weather 技能")
    assert msg["meta"]["route"] == "rule"
    assert msg["meta"]["reason"] == "llm_bad_schema"


def test_pure_rule_no_reason():
    """未装配 provider:route=rule,无 reason(这是正常态不是降级)。"""
    orch = Orchestrator(runs_provider=list)
    msg = orch.handle({"messages": []}, "你好")
    assert msg["meta"] == {"route": "rule"}


def test_route_meta_only_on_plan_card():
    """route_meta 只进 plan 卡 data(其它卡不带——标注的是命名/分解的来源)。"""
    orch = _orch([_resp('{"intent": "browse"}')])
    msg = orch.handle({"messages": []}, "最近有哪些 run")
    assert msg["meta"]["route"] == "llm"
    assert "route_meta" not in msg["cards"][0]["data"], "table 卡不带 route_meta"
