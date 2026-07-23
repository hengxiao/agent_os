"""Blackboard 子系统(DESIGN.md §12;M5 baseline):run 作用域的帧间共享状态与异步消息。"""

from .local import LocalBlackboard

__all__ = ["LocalBlackboard"]
