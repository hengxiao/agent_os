"""内核调试原语(Agent OS Debugger P1):断点 / 单步 / 暂停 / 干预。

设计要点:

- **断点钩子零 runner 改动**:DebugController 直接订阅
  :class:`~agent_os.kernel.signals.InProcessSignalBus`(不经 SidecarSupervisor,
  绕开其 SYNC 通道 2s 超时 fail-closed);命中断点时 ``await`` 一个
  ``asyncio.Event`` 阻塞 emit —— run 就地挂起,事件循环空转可供
  Web/SSE 使用(同 ask_supervisor 就地挂起模式,runner.py ``_ask_supervisor``)。
- **frame 边界信号只观察**:``pre:frame.push``/``pre:frame.pop`` 的 verdict
  有语义(Veto = 纠偏打回,§3.1 pop()),调试器在这些信号上永远返回 None;
  ``Stop`` verdict 只在 ``pre:step``/``pre:tool.call`` 返回(runner 在这两点
  仲裁 pre verdict;``post:*``/``pre:skill.invoke`` 的返回值 runner 不消费,
  在这些暂停点上收到 stop 会推迟到下一个可仲裁的 pre 信号落地)。
- **错误隔离**(§5.3 同总线语义):controller 自身异常捕获 + log + 放行,
  不拖垮 run;run 结束(run.finished/run.aborted)或显式 detach 自动放行
  所有阻塞的 wait,run 不悬挂。
- **装配零开销**:``KernelBuilder.debug_controller(controller)``;缺省 None
  时不订阅任何信号,零行为变化。订阅发生在 Telemetry/sidecar 之后
  (订阅顺序即调用顺序),保证暂停前信号已落 trace/SSE。
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from agent_os.api.v1 import (
    POST_TOOL_CALL,
    PRE_FRAME_POP,
    PRE_FRAME_PUSH,
    PRE_SKILL_INVOKE,
    PRE_STEP,
    PRE_TOOL_CALL,
    RUN_ABORTED,
    RUN_FINISHED,
    RUN_STARTED,
    Modify,
    Signal,
    Stop,
)
from agent_os.kernel.errors import AgentOSError

_log = logging.getLogger("agent_os.kernel.debug")

#: 断点 kind 全集:``step`` 每条 pre:step;``tool_call``/``skill_invoke``
#: 按名字 glob 匹配;``error`` 工具调用失败(post:tool.call payload ok=false)
BREAKPOINT_KINDS = ("step", "tool_call", "skill_invoke", "error")

#: 恢复命令全集
CMD_CONTINUE = "continue"
CMD_STEP_INTO = "step_into"
CMD_STEP_OVER = "step_over"
CMD_STEP_OUT = "step_out"
CMD_STOP = "stop"
RESUME_COMMANDS = (CMD_CONTINUE, CMD_STEP_INTO, CMD_STEP_OVER, CMD_STEP_OUT, CMD_STOP)

#: Stop verdict 可安全返回的信号(runner 在这两点仲裁 pre verdict);
#: 其余暂停点(post:tool.call / pre:frame.pop / pre:skill.invoke)的返回值
#: runner 不消费,stop 推迟到下一个 pre:step / pre:tool.call 落地
_STOP_VERDICT_SIGNALS = (PRE_STEP, PRE_TOOL_CALL)

#: 会话状态机:armed(待绑定 run)→ running ⇄ paused → detached(终态)
_STATE_ARMED = "armed"
_STATE_RUNNING = "running"
_STATE_PAUSED = "paused"
_STATE_DETACHED = "detached"


@dataclass
class Breakpoint:
    """一个断点:kind + 名字 glob 匹配 + 命中计数(条件断点 v1 不做)。

    ``until``(P5 时间旅行 ``--until-step``):仅 ``step`` kind 有效——前 N-1 次
    命中只计数不停下,第 N 条 ``pre:step`` 才暂停并自我禁用(一次性);``None``
    为普通断点。
    """

    id: str
    kind: str  # "step" | "tool_call" | "skill_invoke" | "error"
    match: str = "*"  # tool/skill 名(glob;"*" 全匹配);kind=step/error 时忽略
    enabled: bool = True
    hits: int = 0
    until: int | None = None  # 一次性步数断点:停在第 N 条 pre:step


class DebugSession:
    """一次调试会话:挂到某 run 的内核总线上,管理断点与暂停/放行。

    公开方法(resume / modify_tool_args / inject_message / detach)由调试前端
    (CLI REPL / Web API)在与 run **相同的事件循环**上调用;``_handle`` 由
    controller 在 emit 链路内调用,是 run 一侧的入口。
    """

    def __init__(self, session_id: str, controller: DebugController) -> None:
        self.id = session_id
        #: 绑定的 run(run.started 时由 controller 填入)
        self.run_id: str | None = None
        self.state: str = _STATE_ARMED
        #: 暂停点快照:{signal, run_id, frame_id, depth, step, tool, skill,
        #: payload, breakpoint_ids, reason};恢复后保留供前端检视,下次暂停覆盖
        self.pause_point: dict[str, Any] | None = None
        self._breakpoints: dict[str, Breakpoint] = {}
        self._controller = controller
        #: 帧栈簿记(pre:frame.push 进 / pre:frame.pop 出):step_over 在 pop
        #: 暂停点上定位"当前帧 = 父帧"用;P2/P3 的调用栈视图也读它
        self._frame_stack: list[dict[str, Any]] = []
        self._resume_event: asyncio.Event | None = None
        self._resume_command: str | None = None
        self._modify_patch: dict[str, Any] | None = None
        self._step_mode: str | None = None  # "into" | "over" | "out"
        self._step_frame_id: str | None = None
        #: 非可仲裁信号上收到的 stop:推迟到下一个 pre:step / pre:tool.call 落地
        self._pending_stop: str | None = None
        #: 随时暂停请求(GDB SIGINT 语义):running 时由前端 pause() 置位,
        #: run 一侧在下一个可仲裁信号(_STOP_VERDICT_SIGNALS)消费并挂起
        self._pause_requested: bool = False
        #: 创建参数(host 层回填;live 形态 {skill, input, skill_set, breakpoints}),
        #: 供 rerun 以同参数重开新会话;replay/CLI 会话为 None(不可 rerun)
        self.origin: dict[str, Any] | None = None
        #: 状态变更通知(wait_paused 的等待源)
        self._state_changed = asyncio.Event()

    # ------------------------------------------------------------------
    # 断点管理
    # ------------------------------------------------------------------

    @property
    def breakpoints(self) -> list[Breakpoint]:
        return list(self._breakpoints.values())

    def add_breakpoint(self, kind: str, match: str = "*", *, until: int | None = None) -> Breakpoint:
        """加断点;``kind`` 见 :data:`BREAKPOINT_KINDS`,``match`` 是名字 glob。

        ``until=N``(仅 ``step`` kind,P5):一次性步数断点,停在第 N 条
        ``pre:step``(前 N-1 次只计数),命中后自我禁用。
        """
        if kind not in BREAKPOINT_KINDS:
            raise ValueError(f"未知断点 kind: {kind!r}(可选 {BREAKPOINT_KINDS})")
        if until is not None and (kind != "step" or until < 1):
            raise ValueError(f"until 仅 step 断点有效且须 >= 1,得到: kind={kind!r} until={until!r}")
        bp = Breakpoint(id=f"bp-{uuid.uuid4().hex[:8]}", kind=kind, match=match, until=until)
        self._breakpoints[bp.id] = bp
        return bp

    def remove_breakpoint(self, bp_id: str) -> bool:
        """删断点;返回是否确实存在。"""
        return self._breakpoints.pop(bp_id, None) is not None

    # ------------------------------------------------------------------
    # 前端命令(仅 paused 状态可发;detach 任意状态可发)
    # ------------------------------------------------------------------

    def resume(self, command: str = CMD_CONTINUE) -> None:
        """恢复运行:continue 到下一断点 / 三种单步 / stop(中止 run,§5.2 Stop)。"""
        if command not in RESUME_COMMANDS:
            raise ValueError(f"未知恢复命令: {command!r}(可选 {RESUME_COMMANDS})")
        if self.state != _STATE_PAUSED or self._resume_event is None:
            raise AgentOSError(f"调试会话 {self.id} 不在 paused 状态,无法 resume")
        self._resume_command = command
        self._set_state(_STATE_RUNNING)
        self._resume_event.set()

    def modify_tool_args(self, patch: dict[str, Any]) -> None:
        """改本次工具调用参数(仅暂停在 pre:tool.call 时有效,§5.1 Modify)。

        生效方式:本次被阻塞的 emit 返回 ``Modify(patch)``,runner 把它落到
        ``call.args`` 上(runner.py ``_dispatch_call``);改完即放行。
        """
        if (
            self.state != _STATE_PAUSED
            or not self.pause_point
            or self.pause_point["signal"] != PRE_TOOL_CALL
        ):
            raise AgentOSError("modify_tool_args 仅在暂停于 pre:tool.call 时有效")
        self._modify_patch = dict(patch)
        self.resume(CMD_CONTINUE)

    async def inject_message(self, frame_id: str, text: str) -> None:
        """向指定帧注入 USER/INJECTED 消息(经 ctl,§5.2),注入后放行。"""
        if self.state != _STATE_PAUSED:
            raise AgentOSError(f"调试会话 {self.id} 不在 paused 状态,无法 inject_message")
        ctl = self._controller.ctl
        if ctl is None:
            raise AgentOSError("调试控制器未挂接 RunControl,无法 inject_message")
        await ctl.inject_message(frame_id, text)
        self.resume(CMD_CONTINUE)

    def detach(self) -> None:
        """摘下会话:放行所有阻塞点,断点不再生效,run 继续跑完(终态)。"""
        if self.state == _STATE_DETACHED:
            return
        self._step_mode = None
        self._step_frame_id = None
        self._pause_requested = False
        self._set_state(_STATE_DETACHED)
        if self._resume_event is not None:
            self._resume_event.set()

    def pause(self) -> None:
        """随时暂停(GDB 里发 signal 的语义):``running`` 时置暂停请求,
        run 在下一个可仲裁信号(pre:step / pre:tool.call)挂起,暂停原因
        ``"pause"``。非 running → :class:`AgentOSError`(路由层归 409)。

        注意:run 正在 LLM 调用/工具执行内部时没有信号发出,pause 落在
        该调用结束后的下一条 pre:step / pre:tool.call——与 GDB"下一个
        安全点停下"一致。
        """
        if self.state != _STATE_RUNNING:
            raise AgentOSError(f"调试会话 {self.id} 不在 running 状态,无法 pause")
        self._pause_requested = True

    async def wait_paused(self) -> None:
        """等到会话进入 paused(detached 时直接返回,前端轮询/测试的等待点)。"""
        while True:
            if self.state in (_STATE_PAUSED, _STATE_DETACHED):
                return
            self._state_changed.clear()
            if self.state in (_STATE_PAUSED, _STATE_DETACHED):
                return  # 清除后再查一次,防丢通知
            await self._state_changed.wait()

    @property
    def frame_stack(self) -> list[dict[str, Any]]:
        """当前帧栈快照(push 序 = 栈底→栈顶;pop 暂停点上该帧已视为弹出)。"""
        return list(self._frame_stack)

    # ------------------------------------------------------------------
    # run 一侧(controller 在 emit 链路内调用)
    # ------------------------------------------------------------------

    def _bind(self, run_id: str) -> None:
        self.run_id = run_id
        self._set_state(_STATE_RUNNING)

    def _set_state(self, state: str) -> None:
        self.state = state
        self._state_changed.set()

    async def _handle(self, sig: Signal) -> Any:
        """信号入口(controller 已按 run_id 过滤);返回值并入 emit 结果列表。"""
        if self.state == _STATE_DETACHED:
            return None
        # 帧栈簿记(pre:frame.pop 被 Veto 打回时帧仍在跑,保留在栈上恰好正确)
        if sig.name == PRE_FRAME_PUSH:
            self._frame_stack.append(
                {
                    "frame_id": sig.frame_id,
                    "skill": sig.payload.get("skill"),
                    "depth": sig.payload.get("depth"),
                }
            )
        elif sig.name == PRE_FRAME_POP:
            self._frame_stack = [
                f for f in self._frame_stack if f["frame_id"] != sig.frame_id
            ]
        # 推迟的 stop:在下一个 runner 会仲裁的 pre 信号上落地
        if self._pending_stop is not None and sig.name in _STOP_VERDICT_SIGNALS:
            reason, self._pending_stop = self._pending_stop, None
            return Stop(reason)
        # 随时暂停(GDB SIGINT 语义):前端 pause() 的请求在下一个可仲裁
        # 信号落地;用户显式请求的 reason("pause")优先于断点/步进
        if self._pause_requested and sig.name in _STOP_VERDICT_SIGNALS:
            self._pause_requested = False
            return await self._pause(sig, [], None, reason="pause")
        hits = self._match_breakpoints(sig)
        step_mode = self._step_mode  # _step_stop 命中即消费,先记下来作暂停原因
        step_hit = self._step_stop(sig)
        if not hits and not step_hit:
            return None
        return await self._pause(sig, hits, step_mode)

    def _match_breakpoints(self, sig: Signal) -> list[Breakpoint]:
        """命中判定:kind + 名字 glob(fnmatch);命中即累计 ``hits``。"""
        hits: list[Breakpoint] = []
        for bp in self._breakpoints.values():
            if not bp.enabled:
                continue
            if bp.kind == "step" and sig.name == PRE_STEP or (
                bp.kind == "tool_call"
                and sig.name == PRE_TOOL_CALL
                and fnmatch.fnmatch(str(sig.payload.get("tool", "")), bp.match)
            ) or (
                bp.kind == "skill_invoke"
                and sig.name == PRE_SKILL_INVOKE
                and fnmatch.fnmatch(str(sig.payload.get("skill", "")), bp.match)
            ) or (
                bp.kind == "error"
                and sig.name == POST_TOOL_CALL
                and sig.payload.get("ok") is False
            ):
                hits.append(bp)
        for bp in hits:
            bp.hits += 1
        pause_hits: list[Breakpoint] = []
        for bp in hits:
            if bp.until is not None:
                # 一次性步数断点(P5):前 N-1 次只计数不停;第 N 次命中后自我禁用
                if bp.hits < bp.until:
                    continue
                bp.enabled = False
            pause_hits.append(bp)
        return pause_hits

    def _step_stop(self, sig: Signal) -> bool:
        """步进模式的停点判定(与断点无关;命中后该次步进即消费掉)。

        - ``step_into``:任意帧的下一条 pre:step 即停;
        - ``step_over``:同帧(frame_id 相同)的下一条 pre:step、或该帧
          pre:frame.pop 时停(子帧内的 step 不停——frame_id 天然不同);
        - ``step_out``:当前帧的 pre:frame.pop 时停。
        """
        if self._step_mode is None:
            return False
        if self._step_mode == "into":
            stop = sig.name == PRE_STEP
        elif self._step_mode == "over":
            stop = (
                sig.name in (PRE_STEP, PRE_FRAME_POP)
                and sig.frame_id == self._step_frame_id
            )
        else:  # out
            stop = sig.name == PRE_FRAME_POP and sig.frame_id == self._step_frame_id
        if stop:
            self._step_mode = None
            self._step_frame_id = None
        return stop

    async def _pause(
        self, sig: Signal, hits: list[Breakpoint], step_mode: str | None,
        reason: str | None = None,
    ) -> Any:
        """阻塞 emit 直到前端 resume/detach —— run 就地挂起,事件循环空转。"""
        reason = reason or ("breakpoint" if hits else f"step:{step_mode}")
        self.pause_point = {
            "signal": sig.name,
            "run_id": sig.run_id,
            "frame_id": sig.frame_id,
            "depth": sig.payload.get("depth"),
            "step": sig.payload.get("step"),
            "tool": sig.payload.get("tool"),
            "skill": sig.payload.get("skill"),
            "payload": dict(sig.payload),
            "breakpoint_ids": [bp.id for bp in hits],
            "reason": reason,
        }
        self._resume_event = asyncio.Event()
        self._resume_command = None
        self._modify_patch = None
        self._set_state(_STATE_PAUSED)
        try:
            await self._resume_event.wait()
        finally:
            # 取消(如 run 被强杀)也要把暂停簿记收掉,不悬挂状态机
            if self.state == _STATE_PAUSED:
                self._set_state(_STATE_RUNNING)
        return self._resume_verdict(sig)

    def _resume_verdict(self, sig: Signal) -> Any:
        """按恢复命令计算本次 emit 的返回值(verdict 或 None 放行)。"""
        command, self._resume_command = self._resume_command, None
        patch, self._modify_patch = self._modify_patch, None
        if self.state == _STATE_DETACHED:
            return None
        if patch is not None:
            # modify_tool_args 已校验暂停点;runner 在 pre:tool.call 落地 Modify(§5.1)
            return Modify(patch) if sig.name == PRE_TOOL_CALL else None
        if command == CMD_STOP:
            if sig.name in _STOP_VERDICT_SIGNALS:
                return Stop("debugger: stop")
            # post:*/frame 边界/skill.invoke 的返回值 runner 不消费,推迟落地
            self._pending_stop = "debugger: stop"
            return None
        if command == CMD_STEP_INTO:
            self._step_mode = "into"
        elif command in (CMD_STEP_OVER, CMD_STEP_OUT):
            self._step_mode = "over" if command == CMD_STEP_OVER else "out"
            self._step_frame_id = self._current_frame_id(sig)
        return None

    def _current_frame_id(self, sig: Signal) -> str | None:
        """步进目标帧:暂停在 pre:frame.pop 时该帧已视为弹出,当前帧 = 栈顶父帧。"""
        if sig.name == PRE_FRAME_POP:
            return self._frame_stack[-1]["frame_id"] if self._frame_stack else None
        return sig.frame_id


class DebugController:
    """调试会话注册表 + 信号订阅者。每 run 至多一个活跃会话。

    KernelBuilder 装配期经 :meth:`attach` 挂到信号总线(**直接订阅**,不经
    SidecarSupervisor —— 其 SYNC 通道 2s 超时 fail-closed,阻塞式暂停会被
    判超时否决);订阅顺序在 Telemetry/sidecar 之后,暂停前信号已落
    trace.jsonl / SSE。
    """

    #: 订阅清单:断点四类 + 步进边界两条 + run 生命周期(绑定/自动放行)
    _PATTERNS = (
        RUN_STARTED,
        RUN_FINISHED,
        RUN_ABORTED,
        PRE_STEP,
        PRE_TOOL_CALL,
        PRE_SKILL_INVOKE,
        POST_TOOL_CALL,
        PRE_FRAME_PUSH,
        PRE_FRAME_POP,
    )

    def __init__(self) -> None:
        self._sessions: dict[str, DebugSession] = {}
        self._armed: DebugSession | None = None
        self._ctl: Any = None

    @property
    def ctl(self) -> Any:
        """装配期注入的 RunControl(inject_message 的落地通道)。"""
        return self._ctl

    def attach(self, bus: Any, ctl: Any = None) -> None:
        """挂到内核信号总线(builder 装配期调用;可对多条总线重复挂接)。"""
        if ctl is not None:
            self._ctl = ctl
        for pattern in self._PATTERNS:
            bus.subscribe(pattern, self._on_signal)

    # ------------------------------------------------------------------
    # 会话注册表
    # ------------------------------------------------------------------

    def open_session(self) -> DebugSession:
        """开调试会话:绑定本总线下一个启动的 run(同时至多一个待绑定会话)。"""
        if self._armed is not None:
            raise AgentOSError("已有待绑定的调试会话(每 run 至多一个活跃会话)")
        session = DebugSession(f"dbg-{uuid.uuid4().hex[:8]}", self)
        self._sessions[session.id] = session
        self._armed = session
        return session

    def get(self, session_id: str) -> DebugSession | None:
        return self._sessions.get(session_id)

    @property
    def sessions(self) -> list[DebugSession]:
        return list(self._sessions.values())

    def close_session(self, session_id: str) -> None:
        """移除会话记录(先 detach 放行;前端清理用);待绑定指针一并摘除。"""
        session = self._sessions.pop(session_id, None)
        if session is not None:
            if self._armed is session:
                # 会话未绑定 run 就被收回:摘 _armed,否则后续 open_session 恒冲突
                self._armed = None
            session.detach()

    # ------------------------------------------------------------------
    # 总线入口
    # ------------------------------------------------------------------

    def _active(self, run_id: str) -> DebugSession | None:
        for session in self._sessions.values():
            if session.run_id == run_id and session.state != _STATE_DETACHED:
                return session
        return None

    async def _on_signal(self, sig: Signal) -> Any:
        """总线入口:run 生命周期绑定/解绑 + 按 run_id 转发给活跃会话。

        错误隔离(§5.3 同总线语义):自身异常捕获 + log + 放行,不拖垮 run。
        """
        try:
            if sig.name == RUN_STARTED:
                if self._armed is not None:
                    session, self._armed = self._armed, None
                    session._bind(sig.run_id)
                return None
            if sig.name in (RUN_FINISHED, RUN_ABORTED):
                # run 结束自动摘下会话:放行所有阻塞的 wait,run 不悬挂
                session = self._active(sig.run_id)
                if session is not None:
                    session.detach()
                return None
            session = self._active(sig.run_id)
            if session is None:
                return None
            return await session._handle(sig)
        except Exception:  # noqa: BLE001 — 调试器故障不得拖垮 run(捕获 + log + 放行)
            _log.exception("DebugController 处理信号异常(放行):signal=%r", sig.name)
            return None
