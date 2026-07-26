"""supervisor 契约(SUPERVISOR.md v2 §2.1/§2.3;S1)。

``ask_supervisor`` 是内核拦截式伪工具(同 ``python_orchestrate`` 的拦截理由:
仲裁需绑定调用帧与 manifest,不进 Tool Registry);``Question`` 是路由出 agent、
交本 run 调用方的裁决请求,``SupervisorHandler`` 是嵌入方注入的默认通道
(装配级 handler;Web 收件箱 / CLI 协议属 S2 宿主通道)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict, runtime_checkable

__all__ = [
    "ASK_SUPERVISOR_TOOL",
    "Answer",
    "Question",
    "SupervisorHandler",
]

#: ``ask_supervisor`` 伪工具名(SUPERVISOR.md §2.1):分发阶段被内核拦截,
#: manifest 以 ``permissions.tools: [ask_supervisor]`` 声明(声明即授权)
ASK_SUPERVISOR_TOOL = "ask_supervisor"


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


class Answer(TypedDict, total=False):
    """调用方作答(§2.1 结果形态);``decided_by`` 可缺省(subsystem 补通道标注)。"""

    answer: str
    decided_by: str


@runtime_checkable
class SupervisorHandler(Protocol):
    """嵌入方注入的裁决通道(§2.3 默认通道):收 Question,返 Answer dict。"""

    async def __call__(self, question: Question) -> dict[str, Any]: ...
