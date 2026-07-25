"""Run 契约(DESIGN.md §2.4;§14.1 冻结清单:``compression: "off"`` 消融档)。

Run 是预算、权限、信号的作用域边界;一个 Run 一棵帧树。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .frames import Usage
from .tools import Permission

__all__ = ["LogicPolicy", "Run", "RunConfig", "RunStatus", "ToolPolicy"]


@dataclass
class ToolPolicy:
    """§2.4:工具权限全局上限(三层权限交集之一,§8.2)。"""

    max_permission: Permission = Permission.WRITE


@dataclass
class LogicPolicy:
    """§2.4:``force_sandbox = True`` 时一切逻辑代码强制沙箱(多租户宿主场景,§9.2)。"""

    force_sandbox: bool = False


@dataclass
class RunConfig:
    """§2.4(逐字冻结,含 ``seed`` / ``temperature`` 复现性钉死)。"""

    model: str = ""  # 可被 Skill 的 model.prefer 覆盖
    max_depth: int = 8
    max_steps: int = 200  # 全 run 总步数
    max_cost: float = 2.0  # 美元
    max_wall_time: float = 1800.0
    compression: str = "hierarchical"  # "off" = 消融档(裸模型基线)
    orchestrate: bool = False  # 编排伪工具开关(Fail-Safe Default:显式开启,CODE-ORCHESTRATION.md)
    inline: str = "on"  # "off" = 内联消融档:merge 技能退化为普通压帧调用(SKILL-INLINING.md §9)
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    logic_policy: LogicPolicy = field(default_factory=LogicPolicy)
    seed: int | None = None
    temperature: float | None = None


class RunStatus(Enum):
    """Run 生命周期(§2.4 / §5.2 控制通道)。"""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class Run:
    """Run 运行时状态:配置 + 记账 + 状态。"""

    run_id: str = ""
    config: RunConfig = field(default_factory=RunConfig)
    status: RunStatus = RunStatus.PENDING
    usage: Usage = field(default_factory=Usage)
    result: Any = None
    error: str | None = None
