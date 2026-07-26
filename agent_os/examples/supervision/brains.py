"""supervision 示例的 mock 大脑(数据驱动、确定性,无需 API key)。

- :func:`clerk_brain`:内层 junior_clerk——先 ask_supervisor(大额 urgency=high),
  再按裁决输出 {decision, decided_by}(decided_by 照抄裁决来源,链路可见);
- :func:`lead_brain`:外层 team_lead——读裁决请求,按政策
  (amount_cents ≤ 300000 批准)输出 {answer}。
"""

from __future__ import annotations

import json

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    ToolCall,
)

#: team_lead 的自动批准上限(分)
AUTO_APPROVE_LIMIT_CENTS = 300000


def clerk_brain(req: ChatRequest) -> ChatResponse:
    """内层大脑:先 ask_supervisor,再按裁决回答(decided_by 透传,链路可观察)。"""
    asked = any(
        tc.name == "ask_supervisor"
        for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
    )
    if not asked:
        amount = json.loads(next(m.content for m in req.messages if m.role is Role.USER))["amount"]
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="ask_supervisor", args={
                    "question": f"批准 ¥{amount} 报销吗?",
                    "context": {"amount_cents": amount * 100},
                    "options": ["approve", "reject"],
                    "urgency": "high" if amount >= 1000 else "normal",
                })],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    result = json.loads(
        next(m.content for m in reversed(req.messages) if m.role is Role.TOOL)
    )
    if not result["ok"]:  # 超时/答案不合法:文员降级为暂缓(§3 retryable 语义)
        payload = {"decision": "defer", "decided_by": "none"}
    else:
        payload = {
            "decision": result["value"]["answer"],
            "decided_by": result["value"]["decided_by"],
        }
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload, ensure_ascii=False)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def lead_brain(req: ChatRequest) -> ChatResponse:
    """外层大脑:按政策裁决(amount_cents ≤ 上限 → approve,否则 reject)。"""
    ask = json.loads(next(m.content for m in req.messages if m.role is Role.USER))
    cents = int((ask.get("context") or {}).get("amount_cents") or 0)
    answer = "approve" if cents <= AUTO_APPROVE_LIMIT_CENTS else "reject"
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT,
            content=json.dumps({"answer": answer}, ensure_ascii=False),
        ),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )
