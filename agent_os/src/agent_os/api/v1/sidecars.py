"""Sidecar 契约(DESIGN.md §5.2):被信号触发的监督者(中断处理程序 + 看门狗 + seccomp)。

输入最小化原则:默认只收结构化调用数据,不接收主模型自由文本;
``Veto(reason)`` 的 reason 作为错误观察回写帧上下文(``retryable: false``)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .control import RunControl
from .messages import Message
from .signals import Signal

__all__ = [
    "Allow",
    "ForceCompress",
    "InjectMessage",
    "Mode",
    "Modify",
    "Pause",
    "Sidecar",
    "SignalPattern",
    "Stop",
    "Verdict",
    "Veto",
]

#: 订阅模式(§5.2):如 ``"pre:tool.call"``、``"budget.*"``
SignalPattern = str


class Mode(Enum):
    """SYNC 在关键路径可否决(超时默认 2s,默认 fail-closed);ASYNC 纯观察,永不拖垮 run(§5.3)。"""

    SYNC = "sync"
    ASYNC = "async"


# —— Verdict 类型族(§5.2,逐字)——


@dataclass
class Allow:
    """放行。"""


@dataclass
class Modify:
    """修改调用参数。"""

    patch: dict[str, Any] = field(default_factory=dict)


@dataclass
class Veto:
    """否决;reason 回写帧上下文(错误观察语义,§5.2)。"""

    reason: str = ""


@dataclass
class InjectMessage:
    """向指定帧注入消息(纠偏消息必须带操作指令,§7.3)。"""

    frame_id: str = ""
    msg: Message = field(default_factory=Message)


@dataclass
class Pause:
    """暂停 run(BudgetGuard 的 stop 可降级为 pause + 通知,§2.4)。"""

    reason: str = ""


@dataclass
class Stop:
    """强停 run。"""

    reason: str = ""


@dataclass
class ForceCompress:
    """强制压缩指定帧(§7.1 外部强制触发)。"""

    frame_id: str = ""


#: 判定联合体(§5.2,逐字)
Verdict = Allow | Modify | Veto | InjectMessage | Pause | Stop | ForceCompress


@runtime_checkable
class Sidecar(Protocol):
    """§5.2 Sidecar 契约。"""

    name: str
    subscriptions: list[SignalPattern]
    mode: Mode
    priority: int  # 多个 SYNC sidecar 的确定性裁决顺序
    needs_free_text: bool  # 默认 False:只收结构化调用数据;True 须在文档标注注入风险

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict: ...
