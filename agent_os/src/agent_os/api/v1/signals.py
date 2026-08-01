"""信号目录与信号结构(DESIGN.md §5.1;§14.1 冻结清单:``pre:frame.pop`` 信号名)。

命名 ``<阶段>:<事件>``;``pre:`` = 同步可否决,``post:`` = 异步观察。
信号是 Telemetry(§10)的持久化数据源。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "BLACKBOARD_PUBLISH",
    "BLACKBOARD_WRITE",
    "BUDGET_EXCEEDED",
    "BUDGET_WARNING",
    "POST_COMPRESS",
    "POST_CONTEXT_INLINE",
    "POST_FRAME_POP",
    "POST_FRAME_PUSH",
    "POST_LLM_CHUNK",
    "POST_LLM_RESPONSE",
    "POST_LOGIC_EXEC",
    "POST_SKILL_ESCALATE",
    "POST_SKILL_INVOKE",
    "POST_STEP",
    "POST_TOOL_CALL",
    "PRE_COMPRESS",
    "PRE_FRAME_POP",
    "PRE_FRAME_PUSH",
    "PRE_LLM_REQUEST",
    "PRE_LOGIC_EXEC",
    "PRE_SKILL_ESCALATE",
    "PRE_SKILL_INVOKE",
    "PRE_STEP",
    "PRE_TOOL_CALL",
    "RUN_ABORTED",
    "RUN_FINISHED",
    "RUN_STARTED",
    "SIGNAL_NAMES",
    "SKILL_ESCALATION_DENIED",
    "SUPERVISOR_ANSWER",
    "SUPERVISOR_ASK",
    "SUPERVISOR_TIMEOUT",
    "Signal",
]

# —— §5.1 信号目录(逐字)——
RUN_STARTED = "run.started"
RUN_FINISHED = "run.finished"
RUN_ABORTED = "run.aborted"

PRE_FRAME_PUSH = "pre:frame.push"
POST_FRAME_PUSH = "post:frame.push"
PRE_FRAME_POP = "pre:frame.pop"  # 弹栈前可否决(§3.1 pop())
POST_FRAME_POP = "post:frame.pop"

PRE_STEP = "pre:step"  # 每帧每步一次,sidecar 的主检查点
POST_STEP = "post:step"

PRE_LLM_REQUEST = "pre:llm.request"
POST_LLM_RESPONSE = "post:llm.response"
POST_LLM_CHUNK = "post:llm.chunk"  # 流式,仅 ASYNC

PRE_TOOL_CALL = "pre:tool.call"  # pre 可改参数/否决
POST_TOOL_CALL = "post:tool.call"

PRE_SKILL_INVOKE = "pre:skill.invoke"
POST_SKILL_INVOKE = "post:skill.invoke"

#: 升权确认(ESCALATION.md §5;E2):pre 在确认请求发出时,post 在收到裁决
#: (含 Grant 命中放行,decision="grant-run"),denied 专记拒绝——审计/重放可区分
PRE_SKILL_ESCALATE = "pre:skill.escalate"
POST_SKILL_ESCALATE = "post:skill.escalate"
SKILL_ESCALATION_DENIED = "skill.escalation.denied"

PRE_LOGIC_EXEC = "pre:logic.exec"  # 逻辑代码执行,pre 可否决
POST_LOGIC_EXEC = "post:logic.exec"

PRE_COMPRESS = "pre:compress"
POST_COMPRESS = "post:compress"

#: 内联能力段组装(帧首次 build 快照时一次性发射;SKILL-INLINING.md §7)
POST_CONTEXT_INLINE = "post:context.inline"

BLACKBOARD_PUBLISH = "blackboard.publish"  # 黑板读写可审计(§12)
BLACKBOARD_WRITE = "blackboard.write"

BUDGET_WARNING = "budget.warning"  # 80%
BUDGET_EXCEEDED = "budget.exceeded"

#: supervisor 裁决路由(SUPERVISOR.md v2 §5;S1):ask/answer 成对渲染,timeout 记等待时长
SUPERVISOR_ASK = "supervisor.ask"
SUPERVISOR_ANSWER = "supervisor.answer"
SUPERVISOR_TIMEOUT = "supervisor.timeout"

#: 目录全集(测试用)
SIGNAL_NAMES: tuple[str, ...] = (
    RUN_STARTED,
    RUN_FINISHED,
    RUN_ABORTED,
    PRE_FRAME_PUSH,
    POST_FRAME_PUSH,
    PRE_FRAME_POP,
    POST_FRAME_POP,
    PRE_STEP,
    POST_STEP,
    PRE_LLM_REQUEST,
    POST_LLM_RESPONSE,
    POST_LLM_CHUNK,
    PRE_TOOL_CALL,
    POST_TOOL_CALL,
    PRE_SKILL_INVOKE,
    POST_SKILL_INVOKE,
    PRE_SKILL_ESCALATE,
    POST_SKILL_ESCALATE,
    SKILL_ESCALATION_DENIED,
    PRE_LOGIC_EXEC,
    POST_LOGIC_EXEC,
    PRE_COMPRESS,
    POST_COMPRESS,
    POST_CONTEXT_INLINE,
    BLACKBOARD_PUBLISH,
    BLACKBOARD_WRITE,
    BUDGET_WARNING,
    BUDGET_EXCEEDED,
    SUPERVISOR_ASK,
    SUPERVISOR_ANSWER,
    SUPERVISOR_TIMEOUT,
)


@dataclass
class Signal:
    """内核生命周期事件(广播、生命周期语义,kernel IPC;与黑板消息的边界见 §12.2)。"""

    name: str = ""
    run_id: str = ""
    frame_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
