"""supervisor 契约(SUPERVISOR.md v2 §2.1/§2.3;S1 契约,S2 LLM 可见 schema)。

``ask_supervisor`` 是内核拦截式伪工具(同 ``python_orchestrate`` 的拦截理由:
仲裁需绑定调用帧与 manifest,不进 Tool Registry);``Question`` 是路由出 agent、
交本 run 调用方的裁决请求,``SupervisorHandler`` 是嵌入方注入的默认通道
(装配级 handler;Web 收件箱 / CLI 协议属 S2 宿主通道)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict, runtime_checkable

__all__ = [
    "ASK_SUPERVISOR_SCHEMA",
    "ASK_SUPERVISOR_TOOL",
    "Answer",
    "Question",
    "SupervisorHandler",
]

#: ``ask_supervisor`` 伪工具名(SUPERVISOR.md §2.1):分发阶段被内核拦截,
#: manifest 以 ``permissions.tools: [ask_supervisor]`` 声明(声明即授权)
ASK_SUPERVISOR_TOOL = "ask_supervisor"

#: ``ask_supervisor`` 伪工具对 LLM 呈现的参数 JSON Schema(§2.1;S2)。
#: ContextManager 在 manifest 声明且内核装了 supervisor 通道时,以其为
#: ``parameters`` 追加进可见工具面(同 ``ORCHESTRATE_SCHEMA`` 先例,不进 registry)
ASK_SUPERVISOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "给上级(本 run 调用方)的裁决请求,说清要批准什么",
        },
        "context": {
            "type": "object",
            "description": "给调用方的佐证材料(可选;如订单号、金额)",
        },
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "description": "可选,限定答案集合;上级须从中选择作答",
        },
        "urgency": {
            "type": "string",
            "enum": ["normal", "high"],
            "description": "紧急度(可选,缺省 normal);high 在收件箱优先呈现",
        },
    },
    "required": ["question"],
}


@dataclass
class Question:
    """发给本 run 调用方的裁决请求(SUPERVISOR.md §2.3 载荷)。

    ``previous_error``:答案不合 ``options`` 时由 subsystem 带回重问(§3),
    初问恒为 ``None``。
    """

    question_id: str = ""
    question: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    options: list[str] | None = None
    urgency: str = "normal"  # normal | high
    frame_id: str = ""
    run_id: str = ""
    previous_error: str | None = None
    #: 请求类别(additive;ESCALATION.md §3):"question" = LLM 主动提问(默认),
    #: "escalation" = 内核判定的升权确认——结构化载荷走 ``context``,宿主按 kind 区分渲染
    kind: str = "question"


class Answer(TypedDict, total=False):
    """调用方作答(§2.1 结果形态);``decided_by`` 可缺省(subsystem 补通道标注)。"""

    answer: str
    decided_by: str


@runtime_checkable
class SupervisorHandler(Protocol):
    """嵌入方注入的裁决通道(§2.3 默认通道):收 Question,返 Answer dict。"""

    async def __call__(self, question: Question) -> dict[str, Any]: ...
