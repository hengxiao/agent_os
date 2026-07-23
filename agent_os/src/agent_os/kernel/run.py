"""Run 运行时(DESIGN.md §2.4;M0)。

Run 是预算、权限、信号的作用域边界;一个 Run 一棵帧树。
故障致死性分层:max_steps / max_cost / max_wall_time 才是长任务熔断器(§2.4)。
"""

from __future__ import annotations

from agent_os.api.v1 import Run as RunContract
from agent_os.api.v1 import RunConfig, RunStatus, Usage


class Run:
    """内核侧 Run 句柄:持有契约层 :class:`Run` 状态 + 控制标志(safe point,§3.1)。"""

    def __init__(self, run_id: str = "", config: RunConfig | None = None) -> None:
        self.state = RunContract(run_id=run_id, config=config or RunConfig())

    @property
    def run_id(self) -> str:
        return self.state.run_id

    @property
    def status(self) -> RunStatus:
        return self.state.status

    @property
    def usage(self) -> Usage:
        return self.state.usage

    def check_control_flags(self) -> None:
        """每步循环开头的 safe point 检查(§3.1):pause/stop 标志 + 取消传播。"""
        raise NotImplementedError("M0")
