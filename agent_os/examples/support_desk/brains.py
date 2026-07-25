"""support_desk 示例的数据驱动 mock 大脑(调试用途)。

``support_brain`` 与 research_pipeline 的 ``ops_brain`` 同套消息解析约定
(从 system 首行 ``# skill: <name>`` 识别技能;首条可解析 JSON 的 USER 消息
为帧输入;TOOL 结果消息为 ``{"ok","value","error"}``),差别在"思考"方式:

- 每个 prompt 技能都**读工具/子技能返回的真实数据再决策**,不是查表应答:
  - ``classify_ticket``:先调 get_ticket,按工单原文关键词分类
    (碎/破损 → damaged;退/退款 → refund_request;投诉 → complaint;其余 → inquiry);
  - ``assess_refund``:读 check_policy 的 eligible 与 compute_refund 的 amount——
    无资格 → reject;amount > 自动审批上限(经 search_policy 读到的真实值)→
    escalate;否则 approve;reasoning 引用真实数字(天数、金额、窗口);
  - ``draft_response``:按 decision 组装文案,含真实姓名/订单号/格式化金额;
  - ``review_response``:检查文案含订单号 → ok;
- ``handle_ticket`` 的编排状态机按工具结果推进分支(approve/reject/escalate/
  非退款),final 严格合 outputs schema;同输入 → 同调用树 → 同结果(确定性)。
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    ToolCall,
)

#: 退款审批类类目(走 assess 分支);其余走 draft + notify 简化路径
_REFUNDISH = ("refund_request", "damaged")

# ---------------------------------------------------------------------------
# 请求解析
# ---------------------------------------------------------------------------


def _skill_name(req: ChatRequest) -> str:
    """system 消息首行 ``# skill: <name>`` → 技能名。"""
    first_line = req.messages[0].content.splitlines()[0]
    return first_line.split(":", 1)[1].strip()


def _frame_input(req: ChatRequest) -> dict[str, Any]:
    """首条可解析为 JSON 对象的 USER 消息 = 帧输入(状态元消息等自动跳过)。"""
    for m in req.messages:
        if m.role is Role.USER:
            try:
                data = json.loads(m.content)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                return data
    raise AssertionError("帧上下文缺少输入消息")


def _completed_results(req: ChatRequest) -> list[dict[str, Any]]:
    """按序收集已完成的调用结果(``{"ok","value","error"}`` JSON)。"""
    call_ids: set[str] = set()
    results: list[dict[str, Any]] = []
    for m in req.messages:
        if m.role is Role.ASSISTANT:
            for tc in m.tool_calls:
                call_ids.add(tc.id)
        elif m.role is Role.TOOL and m.tool_call_id in call_ids:
            results.append(json.loads(m.content))
    for r in results:
        assert r["ok"], f"子调用失败,demo 大脑不予恢复: {r}"
    return results


# ---------------------------------------------------------------------------
# 应答构造
# ---------------------------------------------------------------------------


def _final(payload: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _call(tc: ToolCall) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, tool_calls=[tc]),
        finish_reason="tool_calls",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _next(
    skill: str, steps: list[tuple[str, dict[str, Any]]], results: list[dict[str, Any]]
) -> ChatResponse | None:
    """还有未执行的编排步骤 → 发下一个 tool_call;全部完成 → None(该 final 了)。"""
    if len(results) >= len(steps):
        return None
    tool, args = steps[len(results)]
    return _call(ToolCall(id=f"call-{skill}-{len(results)}", name=tool, args=args))


# ---------------------------------------------------------------------------
# classify_ticket:读真实工单文本,按关键词分类
# ---------------------------------------------------------------------------


def _classify_text(text: str) -> tuple[str, float]:
    if any(k in text for k in ("碎", "破损", "裂", "坏了")):
        return "damaged", 0.95
    if any(k in text for k in ("退款", "退钱", "退货", "退")):
        return "refund_request", 0.9
    if "投诉" in text:
        return "complaint", 0.85
    return "inquiry", 0.6


def _classify_ticket(inp: dict[str, Any], results: list[dict[str, Any]]) -> ChatResponse:
    steps = [("get_ticket", {"ticket_id": inp["ticket_id"]})]
    nxt = _next("classify_ticket", steps, results)
    if nxt is not None:
        return nxt
    ticket = results[0]["value"]
    category, confidence = _classify_text(ticket["subject"] + "\n" + ticket["body"])
    return _final({"category": category, "confidence": confidence})


# ---------------------------------------------------------------------------
# assess_refund:读政策事实与金额再决策;reasoning 引用真实数字
# ---------------------------------------------------------------------------


def _assess_refund(inp: dict[str, Any], results: list[dict[str, Any]]) -> ChatResponse:
    steps: list[tuple[str, dict[str, Any]]] = [
        ("skill__check_policy", {"category": inp["category"], "facts": inp["facts"]})
    ]
    if results and results[0]["value"]["eligible"]:
        steps.append(("skill__compute_refund", {"order": inp["order"]}))
        if len(results) >= 2:
            steps.append(("search_policy", {"query": "auto approve limit"}))
    nxt = _next("assess_refund", steps, results)
    if nxt is not None:
        return nxt
    check = results[0]["value"]
    policy_text = ";".join(check["reasons"])
    if not check["eligible"]:
        return _final(
            {"decision": "reject", "reasoning": f"{policy_text}。不符合退款政策,拒绝退款。"}
        )
    amount = results[1]["value"]["amount_cents"]
    limit = results[2]["value"]["policies"]["auto_approve_limit_cents"]
    if amount > limit:
        return _final(
            {
                "decision": "escalate",
                "reasoning": (
                    f"{policy_text};但退款金额 {amount} 分超过自动审批上限 "
                    f"{limit} 分,超出自动处理权限,升级人工审核。"
                ),
            }
        )
    return _final(
        {
            "decision": "approve",
            "reasoning": (
                f"{policy_text};退款金额 {amount} 分未超自动审批上限 {limit} 分,自动批准。"
            ),
        }
    )


# ---------------------------------------------------------------------------
# draft_response:按 decision 组装文案(真实姓名/单号/金额),过 review
# ---------------------------------------------------------------------------


def _compose_response(inp: dict[str, Any], amount_text: str | None) -> str:
    name = inp["user"]["name"]
    order_id = inp["order"]["order_id"]
    decision = inp["decision"]
    if decision == "approve":
        return (
            f"{name} 您好,关于订单 {order_id} 的退款申请已审核通过:"
            f"退款 {amount_text} 将按原支付路径退回,预计 1-3 个工作日到账。"
            "给您带来不便,非常抱歉。"
        )
    if decision == "reject":
        return (
            f"{name} 您好,关于订单 {order_id} 的退款申请,经审核:{inp['reasoning']} "
            "很抱歉本次无法为您办理退款;如有新的凭证,欢迎回复本工单补充说明。"
        )
    if inp["category"] == "complaint":
        return (
            f"{name} 您好,关于订单 {order_id} 您反馈的服务体验问题我们已如实记录,"
            "并转交客服主管跟进,将在 24 小时内回复处理结果。非常抱歉给您带来不好的体验。"
        )
    return (
        f"{name} 您好,关于订单 {order_id} 的咨询:会员积分按实付金额每 1 元累计 1 分,"
        "订单完成后 24 小时内自动到账。如有其他问题欢迎随时联系。"
    )


def _draft_response(inp: dict[str, Any], results: list[dict[str, Any]]) -> ChatResponse:
    needs_format = inp["amount_cents"] > 0
    steps: list[tuple[str, dict[str, Any]]] = []
    if needs_format:
        steps.append(("skill__format_currency", {"amount_cents": inp["amount_cents"]}))
    if len(results) >= len(steps):
        amount_text = results[0]["value"]["text"] if needs_format else None
        steps.append(("skill__review_response", {"response": _compose_response(inp, amount_text)}))
    nxt = _next("draft_response", steps, results)
    if nxt is not None:
        return nxt
    amount_text = results[0]["value"]["text"] if needs_format else None
    return _final({"response": _compose_response(inp, amount_text)})


# ---------------------------------------------------------------------------
# handle_ticket:编排状态机——按工具结果推进分支,final 严格合 outputs schema
# ---------------------------------------------------------------------------


def _notify_args(ticket_id: str, ctx: dict[str, Any], message: str) -> dict[str, Any]:
    return {"ticket_id": ticket_id, "user_id": ctx["user"]["user_id"], "message": message}


def _summary_args(
    ticket_id: str,
    cat: str,
    decision: str,
    refund_cents: int,
    priority: str,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ticket_id": ticket_id,
        "category": cat,
        "decision": decision,
        "refund_cents": refund_cents,
        "priority": priority,
        "user": ctx["user"],
    }


def _handle_ticket(inp: dict[str, Any], results: list[dict[str, Any]]) -> ChatResponse:
    ticket_id = inp["ticket_id"]
    steps: list[tuple[str, dict[str, Any]]] = [("skill__classify_ticket", {"ticket_id": ticket_id})]
    if len(results) >= 1:
        steps.append(("skill__gather_context", {"ticket_id": ticket_id}))
    if len(results) >= 2:
        cat = results[0]["value"]["category"]
        steps.append(
            ("skill__estimate_priority", {"category": cat, "facts": results[1]["value"]["facts"]})
        )
    if len(results) >= 3:
        cat = results[0]["value"]["category"]
        ctx = results[1]["value"]
        priority = results[2]["value"]["priority"]
        if cat in _REFUNDISH:
            steps.append(
                (
                    "skill__assess_refund",
                    {"category": cat, "facts": ctx["facts"], "order": ctx["order"]},
                )
            )
            if len(results) >= 4:
                decision = results[3]["value"]["decision"]
                reasoning = results[3]["value"]["reasoning"]
                draft = {
                    "category": cat,
                    "decision": decision,
                    "user": ctx["user"],
                    "order": ctx["order"],
                    "reasoning": reasoning,
                }
                if decision == "approve":
                    steps.append(
                        (
                            "skill__process_refund",
                            {"ticket_id": ticket_id, "order": ctx["order"], "user": ctx["user"]},
                        )
                    )
                    if len(results) >= 5:
                        amount = results[4]["value"]["amount_cents"]
                        steps.append(("skill__draft_response", {**draft, "amount_cents": amount}))
                        if len(results) >= 6:
                            response = results[5]["value"]["response"]
                            steps.append(
                                ("skill__notify_customer", _notify_args(ticket_id, ctx, response))
                            )
                            if len(results) >= 7:
                                steps.append(
                                    (
                                        "skill__write_case_summary",
                                        _summary_args(
                                            ticket_id, cat, decision, amount, priority, ctx
                                        ),
                                    )
                                )
                elif decision == "reject":
                    steps.append(("skill__draft_response", {**draft, "amount_cents": 0}))
                    if len(results) >= 5:
                        response = results[4]["value"]["response"]
                        steps.append(
                            ("skill__notify_customer", _notify_args(ticket_id, ctx, response))
                        )
                        if len(results) >= 6:
                            steps.append(
                                (
                                    "skill__write_case_summary",
                                    _summary_args(ticket_id, cat, decision, 0, priority, ctx),
                                )
                            )
                else:  # escalate
                    steps.append(
                        (
                            "skill__escalate_ticket",
                            {"ticket_id": ticket_id, "reason": reasoning, "priority": priority},
                        )
                    )
                    if len(results) >= 5:
                        steps.append(
                            (
                                "skill__write_case_summary",
                                _summary_args(ticket_id, cat, decision, 0, priority, ctx),
                            )
                        )
        else:
            steps.append(
                (
                    "skill__draft_response",
                    {
                        "category": cat,
                        "decision": "none",
                        "user": ctx["user"],
                        "order": ctx["order"],
                        "amount_cents": 0,
                        "reasoning": "",
                    },
                )
            )
            if len(results) >= 4:
                response = results[3]["value"]["response"]
                steps.append(("skill__notify_customer", _notify_args(ticket_id, ctx, response)))
    nxt = _next("handle_ticket", steps, results)
    if nxt is not None:
        return nxt
    return _finalize_ticket(ticket_id, results)


def _finalize_ticket(ticket_id: str, results: list[dict[str, Any]]) -> ChatResponse:
    cat = results[0]["value"]["category"]
    if cat in _REFUNDISH:
        decision = results[3]["value"]["decision"]
        if decision == "approve":
            return _final(
                {
                    "status": "resolved_refunded",
                    "ticket_id": ticket_id,
                    "refund_cents": results[4]["value"]["amount_cents"],
                    "response": results[5]["value"]["response"],
                }
            )
        if decision == "reject":
            return _final(
                {
                    "status": "resolved_rejected",
                    "ticket_id": ticket_id,
                    "refund_cents": 0,
                    "response": results[4]["value"]["response"],
                }
            )
        esc = results[4]["value"]
        return _final(
            {
                "status": "escalated",
                "ticket_id": ticket_id,
                "refund_cents": 0,
                "response": (
                    f"您的问题已升级人工客服(升级单 {esc['escalation_id']}),"
                    f"专项组将在 24 小时内与您联系。工单号 {ticket_id}。非常抱歉给您带来不便。"
                ),
            }
        )
    status = "answered" if cat == "inquiry" else "acknowledged"
    return _final(
        {
            "status": status,
            "ticket_id": ticket_id,
            "refund_cents": 0,
            "response": results[3]["value"]["response"],
        }
    )


# ---------------------------------------------------------------------------
# 叶 prompt 技能
# ---------------------------------------------------------------------------


def _leaf_review_response(inp: dict[str, Any]) -> dict[str, Any]:
    ok = re.search(r"\b[a-zA-Z]+\d+\b", inp["response"]) is not None
    return {"ok": ok, "issues": [] if ok else ["回复缺少订单号"]}


def _leaf_translate_en(inp: dict[str, Any]) -> dict[str, Any]:
    return {"translation": f"[EN] {inp['text']}"}


_ORCHESTRATORS = {
    "handle_ticket": _handle_ticket,
    "classify_ticket": _classify_ticket,
    "assess_refund": _assess_refund,
    "draft_response": _draft_response,
}

_LEAF_FINALS = {
    "review_response": _leaf_review_response,
    "translate_en": _leaf_translate_en,
}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def support_brain(req: ChatRequest) -> ChatResponse:
    """数据驱动 mock 大脑:按 system 首行的技能名分派到编排/叶行为。"""
    name = _skill_name(req)
    if name in _LEAF_FINALS:
        return _final(_LEAF_FINALS[name](_frame_input(req)))
    if name in _ORCHESTRATORS:
        return _ORCHESTRATORS[name](_frame_input(req), _completed_results(req))
    raise AssertionError(f"support_brain 未覆盖的技能: {name}")
