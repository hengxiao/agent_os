"""Run 契约(docs/DESIGN.md §2.4;§14.1 冻结清单:``compression: "off"`` 消融档)。

Run 是预算、权限、信号的作用域边界;一个 Run 一棵帧树。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .frames import Usage
from .tools import ToolPolicy  # ToolPolicy 契约本体在 tools.py(工具层),此处 re-export

__all__ = ["LogicPolicy", "Run", "RunConfig", "RunStatus", "ToolPolicy"]


@dataclass
class LogicPolicy:
    """§2.4:``force_sandbox = True`` 时一切逻辑代码强制沙箱(多租户宿主场景,§9.2)。

    ``trusted_builtin_prefixes``(docs/APP-MODEL.md §17.9):force_sandbox 下的
    **显式**可信内置白名单——命中的内置技能(默认 ``platform.*``,UI 动作技能,
    与用户提供技能不同源)仍走 TRUSTED 进程内执行;豁免写在这里是显式声明,
    不靠"恰好没被覆盖"。空元组 = 不豁免任何技能。
    """

    force_sandbox: bool = False
    trusted_builtin_prefixes: tuple[str, ...] = ("platform.",)


@dataclass
class RunConfig:
    """§2.4(逐字冻结,含 ``seed`` / ``temperature`` 复现性钉死;§W0-1 additive 增列 workdir 分区)。"""

    model: str = ""  # 可被 Skill 的 model.prefer 覆盖
    max_depth: int = 8
    max_steps: int = 200  # 全 run 总步数
    max_cost: float = 2.0  # 美元
    max_wall_time: float = 1800.0
    compression: str = "hierarchical"  # "off" = 消融档(裸模型基线)
    orchestrate: bool = False  # 编排伪工具开关(Fail-Safe Default:显式开启,docs/CODE-ORCHESTRATION.md)
    inline: str = "on"  # "off" = 内联消融档:merge 技能退化为普通压帧调用(docs/SKILL-INLINING.md §9)
    tool_policy: ToolPolicy = field(default_factory=ToolPolicy)
    logic_policy: LogicPolicy = field(default_factory=LogicPolicy)
    seed: int | None = None
    temperature: float | None = None
    # —— §W0-1 workdir 可配置(additive;缺省 None → 每 run 临时目录,安全边界不静默放宽)——
    workdir: str | None = None  # run 工作目录(fs/shell 共用解析点,可写产出区)
    read_paths: list[str] = field(default_factory=list)  # 只读挂载(如源码目录,可在 workdir 之外)
    # —— Debugger P5 周期 checkpoint(additive;0=关,N=每 N 步覆盖写"最近现场")——
    checkpoint_interval: int = 0


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
