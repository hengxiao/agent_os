"""InboxChannel(SUPERVISOR.md v2 §2.3 宿主默认通道 / §5 收件箱;S2)。

挂起式收件箱:``__call__`` 即 SupervisorHandler 契约——Question 进 pending 表
(``asked_at`` 由本通道打戳)并挂起一个 ``asyncio.Future``;宿主(Web 路由 /
嵌入方应用)调 :meth:`answer` 结算。Web 收件箱即默认宿主通道:RunManager 装配
内核时缺省注册本通道(通道选择顺序:run 级注入 > 装配级注入 > InboxChannel,
§2.3)。

跨线程安全:run worker 线程的事件循环创建 future,``answer`` 可来自任意线程
(Web 请求线程),经 ``loop.call_soon_threadsafe`` 结算;超时(``asyncio.wait_for``
取消 handler 协程)或被取消后,问题自动离开收件箱(``__call__`` 的 finally)。
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from agent_os.api.v1 import Question


class InboxChannel:
    """pending dict + Future 结算的宿主收件箱(§2.3 handler 接口;S2)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}

    async def __call__(self, question: Question) -> dict[str, Any]:
        """SupervisorHandler 契约:挂起直到 :meth:`answer` 结算(或被 wait_for 取消)。

        同一 question_id 的重问(options 不合时 SupervisorManager 带 previous_error
        再调,§3)覆盖旧条目:收件箱始终呈现最新一次提问。
        """
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        with self._lock:
            self._pending[question.question_id] = {
                "question": question,
                "asked_at": time.time(),
                "future": future,
            }
        try:
            return await future
        finally:
            with self._lock:
                self._pending.pop(question.question_id, None)

    def pending(self) -> list[dict[str, Any]]:
        """收件箱列表(§5 pending 行;``GET /api/supervisor/pending`` 数据源)。

        urgency=high 优先,其余先问先排;``previous_error`` 透传(重问语义)。
        """
        with self._lock:
            entries = list(self._pending.values())
        rows: list[dict[str, Any]] = []
        for entry in entries:
            q = entry["question"]
            rows.append(
                {
                    "question_id": q.question_id,
                    "run_id": q.run_id,
                    "frame_id": q.frame_id,
                    "question": q.question,
                    "context": q.context,
                    "options": q.options,
                    "urgency": q.urgency,
                    "previous_error": q.previous_error,
                    "asked_at": entry["asked_at"],
                }
            )
        rows.sort(key=lambda r: (r["urgency"] != "high", r["asked_at"]))
        return rows

    def get(self, question_id: str) -> Question | None:
        """按 id 取挂起中的 Question(路由层 options 校验用);找不到 → None。"""
        with self._lock:
            entry = self._pending.get(question_id)
        return entry["question"] if entry is not None else None

    def answer(self, question_id: str, answer: dict[str, Any]) -> bool:
        """结算挂起问题(任意线程可调);找不到(或 run 循环已关闭)→ False。"""
        with self._lock:
            entry = self._pending.get(question_id)
        if entry is None:
            return False
        future: asyncio.Future[dict[str, Any]] = entry["future"]
        try:
            future.get_loop().call_soon_threadsafe(self._settle, future, answer)
        except RuntimeError:  # 事件循环已关闭(run 已终结):摘除并视为找不到
            with self._lock:
                self._pending.pop(question_id, None)
            return False
        return True

    @staticmethod
    def _settle(future: asyncio.Future[dict[str, Any]], answer: dict[str, Any]) -> None:
        if not future.done():  # 竞态防御:超时取消与 answer 同时到达时先到先得
            future.set_result(answer)
