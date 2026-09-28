"""消息模型(docs/DESIGN.md §4.1 ``Message`` 块;§14.1 冻结清单:``reasoning`` 与 ``source``)。

归一化不丢字段:``reasoning`` 原样保留,下次请求逐字回传。
WS1 additive(2026-09-28):``ContentPart`` 多模态内容块 + ``Message.parts``
(``content`` 仍是纯文本投影,parts 缺省 None,位置参数兼容)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = ["ContentPart", "Message", "Role", "Source", "ToolCall"]


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
class ContentPart:
    """多模态内容块(§4.1 additive 扩展,2026-09-28)。

    ``Message.content`` 仍是纯文本投影(可为空串);parts 元素生成后不可变
    (同 §7.2 spill 替换串冻结精神)。v1 仅 ``type="image"``;``ref`` 为
    ``blob://<run_id>/<sha>``;``mime`` 随 part 走(blob store 不存 mime)。
    """

    type: str = "image"
    mime: str = "image/png"
    ref: str = ""


@dataclass
class Message:
    """契约层消息(§4.1 字段逐字):

    ``{ role, content, tool_calls, tool_call_id, name, reasoning, source, meta }``

    WS1 additive:``parts`` 多模态内容块(缺省 None;放最后,位置参数兼容)。
    """

    role: Role = Role.USER
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    reasoning: str | None = None  # 思维链/签名 thinking block,归一化不丢字段
    source: Source = Source.SYSTEM
    meta: dict[str, Any] = field(default_factory=dict)
    parts: list[ContentPart] | None = None  # WS1 additive:多模态内容块(§4.1)
