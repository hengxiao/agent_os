"""RunControl 实现(docs/DESIGN.md §5.2;M4 sidecar 与信号)。

内核暴露给 sidecar 的特权接口的唯一实现——仅此通道可操控运行;
``get_frame_tree`` 是 sidecar(代码)→内核的 pull,不耗 token。

语义:stop 置 run 中止标志(runner 在 ``pre:step`` safe point 检查并抛
``RunAborted``);pause 在 v1 同 stop,理由带 ``"paused: "`` 前缀;
inject_message 向指定帧上下文追加 USER/INJECTED 消息;force_compress 在帧上
置标志,runner 在 maintain 前检查并强制执行一次压缩(§7.1 外部强制触发)。
"""

from __future__ import annotations

import logging
from typing import Any

from agent_os.api.v1 import Message, Role, Source, Usage

_log = logging.getLogger("agent_os.kernel")

#: force_compress 在帧工作内存上的标志键(runner 在 maintain 前检查并清除)
FORCE_COMPRESS_KEY = "_force_compress"


class RunControlImpl:
    """``agent_os.api.v1.RunControl`` 协议的内核侧实现(M4)。

    持有 kernel 引用;所有操控都落到 kernel 的运行时状态(中止标志表 / FrameStack),
    在 runner 的 safe point 生效,sidecar 自身不直接打断 loop。
    """

    def __init__(self, kernel: Any = None) -> None:
        self._kernel = kernel

    async def stop(self, run_id: str, reason: str) -> None:
        """置 run 中止标志;下一个 ``pre:step`` safe point 抛 ``RunAborted``。"""
        self._kernel._stop_flags[run_id] = reason

    async def pause(self, run_id: str, reason: str) -> None:
        """v1 语义同 stop,理由带 ``"paused: "`` 前缀(§5.2)。"""
        await self.stop(run_id, f"paused: {reason}")

    async def inject_message(self, frame_id: str, msg: Message) -> None:
        """向指定帧上下文追加消息;非 Message 入参包装为 ``USER``/``INJECTED``。"""
        frame = self._kernel.stack.get(frame_id)
        if frame is None:
            _log.warning("inject_message:帧 %s 不存在,消息被丢弃", frame_id)
            return
        if not isinstance(msg, Message):
            msg = Message(role=Role.USER, content=str(msg), source=Source.INJECTED)
        frame.context.messages.append(msg)

    async def force_compress(self, frame_id: str) -> None:
        """置帧强制压缩标志;runner 在该帧 maintain 前强制执行一次压缩(§7.1)。"""
        frame = self._kernel.stack.get(frame_id)
        if frame is None:
            _log.warning("force_compress:帧 %s 不存在,请求被丢弃", frame_id)
            return
        frame.context.working[FORCE_COMPRESS_KEY] = True

    async def get_frame_tree(self, run_id: str) -> list[dict[str, Any]]:
        """该 run 帧树的嵌套 dict(frame_id/skill/depth/status/children)。"""
        frames = [f for f in self._kernel.stack.tree() if f.run_id == run_id]
        ids = {f.frame_id for f in frames}

        def node(frame: Any) -> dict[str, Any]:
            return {
                "frame_id": frame.frame_id,
                "skill": str(frame.skill),
                "depth": frame.depth,
                "status": frame.status.value,
                "children": [
                    node(c)
                    for c in self._kernel.stack.children_of(frame.frame_id)
                    if c.frame_id in ids
                ],
            }

        return [node(f) for f in frames if f.parent_id not in ids]

    async def get_usage(self, run_id: str) -> Usage:
        """该 run 的记账快照(§2.3 Usage)。"""
        return self._kernel._runs[run_id].state.usage


#: 兼容别名(M0 骨架时期的类名,``agent_os.kernel`` 包导出路径不变)
KernelRunControl = RunControlImpl
