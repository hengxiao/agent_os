"""帧模型(DESIGN.md §2.3;§14.1 冻结清单:Usage 细分字段)。

SkillFrame = 调用栈帧;FrameContext = 帧私有上下文(其他帧不可见);
Usage 为帧与 run 两级记账的最小数据前提。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .messages import Message

__all__ = ["FrameContext", "FrameStatus", "MsgId", "SkillFrame", "SkillRef", "Usage"]

#: pinned 消息标识类型(§2.3 FrameContext.pinned)
MsgId = str


@dataclass(frozen=True)
class SkillRef:
    """技能寻址:``<namespace>:<name>@<semver>``(§6.1)。"""

    name: str = ""
    version: str = ""
    namespace: str = "local"

    def __str__(self) -> str:
        return f"{self.namespace}:{self.name}@{self.version}"

    @classmethod
    def parse(cls, text: str) -> SkillRef:
        """解析 ``<namespace>:<name>@<version>``;缺省 namespace=``local``。"""
        namespace, _, rest = text.partition(":")
        if not rest:  # 无 namespace 段:"name@version"
            namespace, rest = "local", text
        name, _, version = rest.partition("@")
        return cls(name=name, version=version, namespace=namespace)


class FrameStatus(Enum):
    """§2.3:``PENDING | RUNNING | SUSPENDED | DONE | FAILED``。"""

    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Usage:
    """记账细分(§2.3,逐字冻结)。缓存读约 1/10 价;thinking 不可见但计费。"""

    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0
    cost: float = 0.0
    ttft_ms: int = 0
    total_ms: int = 0


@dataclass
class FrameContext:
    """帧私有上下文(§2.3,逐字冻结)。

    ``pinned`` 永不压缩(§7.4 不变量 1);``working`` 压缩时优先保留。
    """

    messages: list[Message] = field(default_factory=list)
    working: dict[str, Any] = field(default_factory=dict)
    pinned: list[MsgId] = field(default_factory=list)
    token_estimate: int = 0


@dataclass
class SkillFrame:
    """调用栈帧(§2.3,逐字冻结)。``parent_id = None`` 表示根帧。"""

    frame_id: str = ""
    run_id: str = ""
    skill: SkillRef = field(default_factory=SkillRef)
    parent_id: str | None = None
    input: dict[str, Any] = field(default_factory=dict)
    context: FrameContext = field(default_factory=FrameContext)
    status: FrameStatus = FrameStatus.PENDING
    depth: int = 0
    result: Any = None
    error: Any = None
    usage: Usage = field(default_factory=Usage)
    #: 触发本帧的父帧调用 id(子帧创建时登记;checkpoint 恢复按它配对结算,§10.2)
    call_id: str | None = None
