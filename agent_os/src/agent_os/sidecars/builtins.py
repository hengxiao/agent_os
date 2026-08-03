"""内置 sidecar(docs/DESIGN.md §5.4 表格逐字;BudgetGuard/LoopDetector/StallDetector/ToolGuard

为 M4,CodeScanner 为 M5)。TraceRecorder 不在此列——已升格为 Telemetry 子系统的
JSONL exporter(§10)。自定义 sidecar 经 entry point ``agent_os.sidecars`` 注册。

输入最小化原则(§5.2):以下 sidecar 一律 ``needs_free_text = False``,只消费
结构化信号载荷(usage / 调用签名 / 工具名与参数),不接收主模型自由文本。
"""

from __future__ import annotations

import json
import re
import time
from typing import ClassVar

from agent_os.api.v1 import (
    Allow,
    Message,
    Mode,
    Role,
    RunControl,
    Signal,
    SignalPattern,
    Source,
    Verdict,
    Veto,
)


class BudgetGuard:
    """§5.4:订阅 ``post:llm.response``;累计成本/步数/时长,超限 → ``ctl.stop()``

    (长任务宿主可配 stop→pause 降级,§2.4)。按 run_id 独立累计。"""

    name: ClassVar[str] = "budget_guard"
    subscriptions: ClassVar[list[SignalPattern]] = ["post:llm.response"]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    def __init__(
        self,
        max_cost: float | None = None,
        max_steps: int | None = None,
        max_wall_time: float | None = None,
    ) -> None:
        self.max_cost = max_cost
        self.max_steps = max_steps
        self.max_wall_time = max_wall_time
        self._cost: dict[str, float] = {}
        self._steps: dict[str, int] = {}
        self._started: dict[str, float] = {}
        self._stopped: set[str] = set()

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        run_id = sig.run_id
        if run_id in self._stopped:
            return Allow()
        now = time.monotonic()
        started = self._started.setdefault(run_id, now)
        usage = sig.payload.get("usage") or {}
        cost = self._cost.get(run_id, 0.0) + float(usage.get("cost") or 0.0)
        self._cost[run_id] = cost
        steps = self._steps.get(run_id, 0) + 1
        self._steps[run_id] = steps
        reason = None
        if self.max_cost is not None and cost > self.max_cost:
            reason = f"BudgetGuard: 成本 {cost:.4f} 超上限 {self.max_cost}"
        elif self.max_steps is not None and steps > self.max_steps:
            reason = f"BudgetGuard: 步数 {steps} 超上限 {self.max_steps}"
        elif self.max_wall_time is not None and now - started > self.max_wall_time:
            reason = f"BudgetGuard: 挂钟时长 {now - started:.1f}s 超上限 {self.max_wall_time}s"
        if reason is not None:
            self._stopped.add(run_id)
            await ctl.stop(run_id, reason)
        return Allow()


class LoopDetector:
    """§5.4:订阅 ``post:step``;重复调用模式(同签名连续 N 次)→ ``inject_message``

    带操作策略纠偏,再犯 ``max_strikes`` 次 → ``stop``(防死亡螺旋,§3.2)。
    按 frame_id 维护最近签名序列;签名变化则重置该帧计数。"""

    name: ClassVar[str] = "loop_detector"
    subscriptions: ClassVar[list[SignalPattern]] = ["post:step"]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    def __init__(self, threshold: int = 3, max_strikes: int = 2) -> None:
        self.threshold = threshold
        self.max_strikes = max_strikes
        # frame_id -> {"sig": 最近签名, "count": 连续重复数, "strikes": 纠偏后再犯数}
        self._frames: dict[str, dict[str, object]] = {}
        self._stopped: set[str] = set()

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        frame_id = sig.frame_id or ""
        if frame_id in self._stopped:
            return Allow()
        state = self._frames.setdefault(frame_id, {"sig": None, "count": 0, "strikes": 0})
        for call in sig.payload.get("calls") or []:
            call_sig = call.get("sig", "")
            if call_sig == state["sig"]:
                state["count"] = int(state["count"]) + 1
            else:
                state.update({"sig": call_sig, "count": 1, "strikes": 0})
            count = int(state["count"])
            if count == self.threshold:
                await ctl.inject_message(
                    frame_id,
                    Message(
                        role=Role.USER,
                        content=(
                            f"检测到同一调用已连续重复 {count} 次:"
                            f"停止重试,改用其他策略或宣告受阻。"
                        ),
                        source=Source.INJECTED,
                    ),
                )
            elif count > self.threshold:
                state["strikes"] = int(state["strikes"]) + 1
                if int(state["strikes"]) >= self.max_strikes:
                    self._stopped.add(frame_id)
                    await ctl.stop(sig.run_id, f"LoopDetector: 帧 {frame_id} 循环不止")
                    return Allow()
        return Allow()


class StallDetector:
    """§5.4:订阅 ``post:step``;帧级静默超时(相邻 step 间隔超 ``max_idle_seconds``)

    → inject 纠偏 → 再犯 → stop。``clock`` 可注入(测试用假钟)。"""

    name: ClassVar[str] = "stall_detector"
    subscriptions: ClassVar[list[SignalPattern]] = ["post:step"]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    def __init__(self, max_idle_seconds: float = 600, clock=None) -> None:
        self.max_idle_seconds = max_idle_seconds
        self._clock = clock or time.monotonic
        self._last: dict[str, float] = {}
        self._warned: set[str] = set()
        self._stopped: set[str] = set()

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        frame_id = sig.frame_id or ""
        now = self._clock()
        last = self._last.get(frame_id)
        self._last[frame_id] = now
        if last is None or frame_id in self._stopped:
            return Allow()
        if now - last > self.max_idle_seconds:
            if frame_id in self._warned:
                self._stopped.add(frame_id)
                await ctl.stop(sig.run_id, f"StallDetector: 帧 {frame_id} 停滞")
            else:
                self._warned.add(frame_id)
                await ctl.inject_message(
                    frame_id,
                    Message(
                        role=Role.USER,
                        content="长时间无进展:请给出当前状态与下一步",
                        source=Source.INJECTED,
                    ),
                )
        return Allow()


class ToolGuard:
    """§5.4:订阅 ``pre:tool.call``;规则表(工具名/参数模式)→ ``Veto``。

    rules = ``list[(tool_name, arg_regex, reason)]``:``payload["tool"]`` 命中工具名
    且 ``arg_regex`` 在参数 JSON 中搜索命中 → ``Veto(reason)``,否则 ``Allow``。

    能力上限声明:正则/关键字对 shell 组合爆炸无效,``system.shell.exec`` 的防护主体是
    沙箱(§9.2)+ 权限(§8.2);语义解析器作为后续替换实现预留。
    """

    name: ClassVar[str] = "tool_guard"
    subscriptions: ClassVar[list[SignalPattern]] = ["pre:tool.call"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self, rules: list[tuple[str, str, str]] | None = None) -> None:
        self.rules = list(rules or [])

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        tool = sig.payload.get("tool", "")
        args = json.dumps(sig.payload.get("args", {}))
        for name, pattern, reason in self.rules:
            if tool == name and re.search(pattern, args):
                return Veto(reason)
        return Allow()


class CodeScanner:
    """§5.4:订阅 ``pre:logic.exec``;动态代码静态模式扫描(危险 import、ctypes、

    可疑网络调用)→ ``Veto``(§9.5)。扫描对象是 ``payload["source"]``(执行方
    在 pre 载荷里带上的待执行源码);无 ``source`` 的 pre:logic.exec(如 code 技能
    帧执行)无可扫描对象,放行。能力上限同 ToolGuard:正则只是辅助,防护主体是
    沙箱(§9.2)+ 权限(§8.2)。
    """

    name: ClassVar[str] = "code_scanner"
    subscriptions: ClassVar[list[SignalPattern]] = ["pre:logic.exec"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    #: 默认危险模式集(§5.4)
    DEFAULT_PATTERNS: ClassVar[list[str]] = [
        r"\bimport\s+os\b",
        r"\bos\.system\b",
        r"\bctypes\b",
        r"\bsubprocess\b",
        r"\bsocket\b",
    ]

    def __init__(self, patterns: list[str] | None = None) -> None:
        self.patterns = list(self.DEFAULT_PATTERNS if patterns is None else patterns)

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        source = sig.payload.get("source") or ""
        for pat in self.patterns:
            if re.search(pat, source):
                return Veto(f"CodeScanner: 命中危险模式 {pat}")
        return Allow()


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
