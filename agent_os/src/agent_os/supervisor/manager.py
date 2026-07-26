"""SupervisorManager(SUPERVISOR.md v2 §2.3 handler 通道 / §3 超时与兜底 / §5 信号;S1)。

内核在 ``_dispatch_call`` 拦截 ``ask_supervisor`` 后调用 :meth:`SupervisorManager.ask`;
本类负责:构造 ``Question`` → 发 ``supervisor.ask`` 信号 → 调调用方 handler
(``asyncio.wait_for`` 超时)→ options 校验(不合法以 ``previous_error`` 重问,
至多 2 次 handler 调用)→ 发 ``supervisor.answer`` 信号 → 返回裁决。

:meth:`ask` 的返回形态(内核据 ``ok is False`` 区分):

- 成功:``{"answer": ..., "decided_by": ...}``(handler 缺省 decided_by 时补
  ``"handler"`` 通道标注);
- 超时 + ``on_timeout="default_answer"``:同成功形态,``decided_by =
  "policy:default"``(§3 兜底);
- 超时 + ``on_timeout="fail"`` / 重问后答案仍不合法:
  ``{"ok": False, "error": {...}}`` 结构化错误(``retryable: True``,帧可降级)。

嵌套监督(§2.5)对内核透明:handler 内部可再起 run 或级联上报,这里只看到
一次 handler 调用的往返。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from typing import Any

from agent_os.api.v1 import (
    SUPERVISOR_ANSWER,
    SUPERVISOR_ASK,
    SUPERVISOR_TIMEOUT,
    Question,
    Signal,
)

_log = logging.getLogger("agent_os.supervisor")

#: 答案不合 options 的重问上限(§3):初问 + 1 次重问 = 至多 2 次 handler 调用
_MAX_ASK_ATTEMPTS = 2

#: 超时 fail 的错误观察(SUPERVISOR.md §3,逐字 hint)
_TIMEOUT_HINT = "上级未回答,可降级处理或重新询问"


class SupervisorManager:
    """S1 的调用方通道:装配级 handler + 超时/兜底 policy(§2.3 通道选择顺序的第一级)。"""

    def __init__(
        self,
        handler: Any,  # SupervisorHandler(契约 Protocol,§2.3)
        *,
        signals: Any,  # 信号总线(§5.1)
        timeout_s: float = 120.0,
        on_timeout: str = "fail",
        default_answer: str = "",
    ) -> None:
        if on_timeout not in ("fail", "default_answer"):
            raise ValueError(
                f"on_timeout 应为 'fail' | 'default_answer',得到: {on_timeout!r}"
            )
        self._handler = handler
        self._signals = signals
        self._timeout_s = float(timeout_s)
        self._on_timeout = on_timeout
        self._default_answer = default_answer

    async def ask(self, frame: Any, args: dict[str, Any]) -> dict[str, Any]:
        """向调用方提问并等待裁决(返回形态见模块 docstring)。

        ``args`` 即 ``ask_supervisor`` 的调用参数;内核可预置 ``question_id``
        (pending ask 落盘与 Question 同一 id,§4),缺省由本方法生成。
        handler 抛出的异常(含 RunAborted 断电模拟)原样上抛,不在此兜底。
        """
        question = Question(
            question_id=str(args.get("question_id") or f"q-{uuid.uuid4().hex[:12]}"),
            question=str(args.get("question") or ""),
            context=dict(args.get("context") or {}),
            options=[str(o) for o in args["options"]] if args.get("options") else None,
            urgency=str(args.get("urgency") or "normal"),
            frame_id=frame.frame_id,
            run_id=frame.run_id,
        )
        await self._signals.emit(
            Signal(
                name=SUPERVISOR_ASK,
                run_id=question.run_id,
                frame_id=question.frame_id,
                payload={
                    "question_id": question.question_id,
                    "question": question.question,
                    "context": question.context,
                    "options": question.options,
                    "urgency": question.urgency,
                    "channel": "handler",  # §2.3:S1 仅嵌入方 handler 通道
                },
            )
        )
        previous_error: str | None = None
        for _ in range(_MAX_ASK_ATTEMPTS):
            q = question if previous_error is None else replace(question, previous_error=previous_error)
            try:
                raw = await asyncio.wait_for(self._handler(q), timeout=self._timeout_s)
            except TimeoutError:  # asyncio.wait_for 超时(3.11+ 内建别名)
                return await self._timeout_outcome(question)
            answer, decided_by, error = self._validate(raw, question)
            if error is None:
                await self._emit_answer(question, decided_by, answer)
                return {"answer": answer, "decided_by": decided_by}
            previous_error = error  # 格式错误返回给调用方重答(§3),不重问子帧
        _log.warning("supervisor 答案连续 %d 次不合法,按 fail 处理", _MAX_ASK_ATTEMPTS)
        return {
            "ok": False,
            "error": {
                "kind": "supervisor_invalid_answer",
                "message": f"上级答案连续 {_MAX_ASK_ATTEMPTS} 次不合法: {previous_error}",
                "retryable": True,
                "hint": "上级须按 options 作答,帧可降级处理或重新询问",
            },
        }

    async def _timeout_outcome(self, question: Question) -> dict[str, Any]:
        """超时(§3):发 ``supervisor.timeout`` 信号,按 on_timeout 策略闭环或报错。"""
        await self._signals.emit(
            Signal(
                name=SUPERVISOR_TIMEOUT,
                run_id=question.run_id,
                frame_id=question.frame_id,
                payload={"question_id": question.question_id, "after_s": self._timeout_s},
            )
        )
        if self._on_timeout == "default_answer":
            await self._emit_answer(question, "policy:default", self._default_answer)
            return {"answer": self._default_answer, "decided_by": "policy:default"}
        return {
            "ok": False,
            "error": {
                "kind": "supervisor_timeout",
                "message": f"上级未在 {self._timeout_s}s 内回答",
                "retryable": True,
                "hint": _TIMEOUT_HINT,
            },
        }

    @staticmethod
    def _validate(
        raw: Any, question: Question
    ) -> tuple[Any, str | None, str | None]:
        """校验 handler 返回:``(answer, decided_by, None)`` 或 ``(None, None, 重问理由)``。"""
        if not isinstance(raw, dict) or "answer" not in raw:
            return None, None, (
                "handler 须返回 {'answer': ..., 'decided_by': ...} 形态的 dict,"
                f"得到: {raw!r}"
            )
        answer = raw["answer"]
        if question.options is not None and answer not in question.options:
            return None, None, (
                f"答案 {answer!r} 不在 options {question.options} 内,请从 options 中选择作答"
            )
        return answer, str(raw.get("decided_by") or "handler"), None

    async def _emit_answer(self, question: Question, decided_by: Any, answer: Any) -> None:
        """``supervisor.answer`` 信号(§5;trace 中 ask/answer 成对,replay 据此回放)。"""
        await self._signals.emit(
            Signal(
                name=SUPERVISOR_ANSWER,
                run_id=question.run_id,
                frame_id=question.frame_id,
                payload={
                    "question_id": question.question_id,
                    "decided_by": decided_by,
                    "answer": str(answer)[:200],  # §5 摘要:答案可长,信号只留截断形态
                },
            )
        )
