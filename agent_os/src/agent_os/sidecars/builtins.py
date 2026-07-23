"""内置 sidecar(DESIGN.md §5.4 表格逐字;BudgetGuard/LoopDetector/StallDetector/ToolGuard/

HumanApproval 为 M4,CodeScanner 为 M5)。TraceRecorder 不在此列——已升格为 Telemetry
子系统的 JSONL exporter(§10)。自定义 sidecar 经 entry point ``agent_os.sidecars`` 注册。
"""

from __future__ import annotations

from typing import ClassVar

from agent_os.api.v1 import Mode, RunControl, Signal, SignalPattern, Verdict


class BudgetGuard:
    """§5.4:订阅 ``post:llm.response``;累计成本/步数/时长,超限 → ``ctl.stop()``

    (长任务宿主可配 stop→pause 降级,§2.4)。"""

    name: ClassVar[str] = "budget_guard"
    subscriptions: ClassVar[list[SignalPattern]] = ["post:llm.response"]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    def __init__(self, max_cost: float | None = None, max_steps: int | None = None, max_wall_time: float | None = None) -> None:
        self.max_cost = max_cost
        self.max_steps = max_steps
        self.max_wall_time = max_wall_time

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        raise NotImplementedError("M4")


class LoopDetector:
    """§5.4:订阅 ``post:step``;重复调用模式(同工具同参 N 次)→ ``inject_message``

    带操作策略纠偏,再犯 → ``stop``(防死亡螺旋,§3.2)。"""

    name: ClassVar[str] = "loop_detector"
    subscriptions: ClassVar[list[SignalPattern]] = ["post:step"]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        raise NotImplementedError("M4")


class StallDetector:
    """§5.4:订阅 ``post:step``;帧级静默超时(长期无进展)→ inject 纠偏 → stop。"""

    name: ClassVar[str] = "stall_detector"
    subscriptions: ClassVar[list[SignalPattern]] = ["post:step"]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    def __init__(self, stall_timeout: float = 300.0) -> None:
        self.stall_timeout = stall_timeout

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        raise NotImplementedError("M4")


class ToolGuard:
    """§5.4:订阅 ``pre:tool.call``;规则表(工具名/参数模式)→ ``Veto``。

    能力上限声明:正则/关键字对 shell 组合爆炸无效,``shell_exec`` 的防护主体是
    沙箱(§9.2)+ 权限(§8.2);语义解析器作为后续替换实现预留。
    """

    name: ClassVar[str] = "tool_guard"
    subscriptions: ClassVar[list[SignalPattern]] = ["pre:tool.call"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self, rules: list[dict] | None = None) -> None:
        self.rules = rules or []

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        raise NotImplementedError("M4")


class CodeScanner:
    """§5.4:订阅 ``pre:logic.exec``;动态代码静态模式扫描(危险 import、ctypes、

    可疑网络调用)→ ``Veto``(§9.5)。"""

    name: ClassVar[str] = "code_scanner"
    subscriptions: ClassVar[list[SignalPattern]] = ["pre:logic.exec"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        raise NotImplementedError("M5")


class HumanApproval:
    """§5.4:订阅 ``pre:tool.call``(EXEC 级);挂起等待人工批准,超时走可配默认

    (如"超时拒绝");高风险可叠加模型审批。"""

    name: ClassVar[str] = "human_approval"
    subscriptions: ClassVar[list[SignalPattern]] = ["pre:tool.call"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 20
    needs_free_text: ClassVar[bool] = False

    def __init__(self, timeout: float = 600.0, on_timeout: str = "deny") -> None:
        self.timeout = timeout
        self.on_timeout = on_timeout

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        raise NotImplementedError("M4")
