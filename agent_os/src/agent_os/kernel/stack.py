"""帧栈(DESIGN.md §2.3/§3.1;M2 技能与调用栈)。

隔离语义严格对齐函数调用:父帧只能经 ``input`` 传参,子帧只能经 ``result``/``error``
返回;子帧 transcript 弹栈时折叠为返回值(§2.3,"isolation over compression")。
"""

from __future__ import annotations

from typing import Any

from agent_os.api.v1 import FrameStatus, SkillFrame
from agent_os.kernel.errors import MaxDepthExceeded


class FrameStack:
    """一个 Run 一棵帧树的显式栈,供 sidecar 检视与 RunControl 操控(§3.1)。

    活动帧在 ``_frames``(栈序);全部登记过的帧按 frame_id 入 ``_by_id``,
    parent_id → children 的邻接表维护帧树。深度检查在 push 时兜底
    (runner 在压栈前先做同样检查,见 §3.1 子技能分发)。
    """

    def __init__(self, max_depth: int = 8) -> None:
        self.max_depth = max_depth
        self._frames: list[SkillFrame] = []
        self._by_id: dict[str, SkillFrame] = {}
        self._children: dict[str | None, list[SkillFrame]] = {}

    def push(self, frame: SkillFrame) -> None:
        """压栈并登记帧树;``frame.depth > max_depth`` → :class:`MaxDepthExceeded`。"""
        if frame.depth > self.max_depth:
            raise MaxDepthExceeded(
                f"帧 {frame.frame_id}(技能 {frame.skill.name})depth={frame.depth} "
                f"超过 max_depth={self.max_depth}"
            )
        frame.status = FrameStatus.RUNNING
        self._frames.append(frame)
        self._by_id[frame.frame_id] = frame
        self._children.setdefault(frame.parent_id, []).append(frame)

    def pop_ok(self, frame: SkillFrame, result: Any) -> Any:
        """``pre:frame.pop`` 放行后的正常弹栈(§3.1)。"""
        frame.result = result
        frame.status = FrameStatus.DONE
        self._remove(frame)
        return result

    def pop_err(self, frame: SkillFrame, error: Any) -> Any:
        """失败弹栈:错误记入帧,沿栈上抛由 runner 负责。"""
        frame.error = error
        frame.status = FrameStatus.FAILED
        self._remove(frame)
        return error

    def _remove(self, frame: SkillFrame) -> None:
        if frame in self._frames:
            self._frames.remove(frame)

    def get(self, frame_id: str) -> SkillFrame | None:
        """按 frame_id 查帧(RunControl 寻址,§5.2);不存在返回 None。"""
        return self._by_id.get(frame_id)

    @property
    def depth(self) -> int:
        """活动帧数(当前栈深)。"""
        return len(self._frames)

    def children_of(self, frame_id: str | None) -> list[SkillFrame]:
        """某帧的直接子帧(帧树邻接表查询)。"""
        return list(self._children.get(frame_id, []))

    def tree(self) -> list[SkillFrame]:
        """帧树快照(RunControl.get_frame_tree 的数据源,§5.2):全部登记过的帧。"""
        return list(self._by_id.values())
