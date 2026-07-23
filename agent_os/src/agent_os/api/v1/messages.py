"""消息模型(DESIGN.md §4.1 ``Message`` 块;§14.1 冻结清单:``reasoning`` 与 ``source``)。

归一化不丢字段:``reasoning`` 原样保留,下次请求逐字回传。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = ["Message", "Role", "Source", "ToolCall"]


class Role(Enum):
    """消息角色。``TOOL`` = tool_result 消息(与 tool_call 配对,§7.4 不变量 2)。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Source(Enum):
    """来源标注(§4.1):``system | parent_input | tool_result | injected | external``。"""

    SYSTEM = "system"
    PARENT_INPUT = "parent_input"
    TOOL_RESULT = "tool_result"
    INJECTED = "injected"
    EXTERNAL = "external"


@dataclass
class ToolCall:
    """一次工具/子技能调用请求(assistant 消息的 ``tool_calls`` 元素)。"""

    id: str = ""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """契约层消息(§4.1 字段逐字):

    ``{ role, content, tool_calls, tool_call_id, name, reasoning, source, meta }``
    """

    role: Role = Role.USER
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    reasoning: str | None = None  # 思维链/签名 thinking block,归一化不丢字段
    source: Source = Source.SYSTEM
    meta: dict[str, Any] = field(default_factory=dict)
