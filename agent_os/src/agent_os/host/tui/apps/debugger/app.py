"""DebuggerApp(docs/TUI-DEBUG.md §2/§5/§7;D1:命令窗 + 只读骨架)。

compound 组装:调用栈(stack)+ 断点表(bps)+ 信号轨迹(trace)+ 命令窗
(cmd)四个子 widget;屏幕态(焦点窗/kill 两段确认/上一条命令/选中帧)全在
app 层,全量可 JSON 序列化。widget 层零网络面——唯一出海在 app 层经
DebugSource(Demo 脚本化模拟 / Offline 只读回放 / Online D1 stub)。

使用原则(§0):**命令窗是第一界面**——所有调试动作都有命令形式,快捷键只是
命令的回声;键位逐字学 GDB(裸 Enter 白名单重复、C-c = pause、C-x o 切焦点、
C-l 重绘);命令方言逐字学 GDB(commands.py)。键纪律:物理键一律先过
KeymapEngine 翻成 intent(组件零按键分支);色/文案全走 sty + copy 键
(组件零主题分支);状态双编码(色 + 符号/文本)。
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import Any

from agent_os.host.tui.apps.debugger.commands import (
    REPEATABLE,
    Command,
    ExecEnv,
    ParseError,
    _stop_line,
    execute,
    execute_stop,
    parse,
)
from agent_os.host.tui.apps.debugger.model import DebugSource
from agent_os.host.tui.apps.debugger.trace_rows import (
    bp_target_for_row,
    build_trace_rows,
    paused_signal_index,
    row_for_signal,
)
from agent_os.host.tui.apps.debugger.widgets import (
    BpDef,
    BpWidget,
    CmdDef,
    CmdWidget,
    StackDef,
    StackWidget,
    TraceDef,
    TraceWidget,
)
from agent_os.host.tui.kernel.compound import CompoundWidget
from agent_os.host.tui.kernel.registry import WidgetRegistry
from agent_os.host.tui.kernel.tree import WidgetTree
from agent_os.host.tui.kernel.widget import Widget
from agent_os.host.tui.kernel.widget_def import WidgetDef
from agent_os.host.tui.tui import sty
from agent_os.host.tui.tui.cells import CellBuffer, Region, text_width
from agent_os.host.tui.tui.keymap import (
    RES_INTENT,
    RES_PREFIX,
    RES_SELF_INSERT,
    SELF_INSERT,
    KeymapEngine,
    KeymapRegistry,
)
from agent_os.host.tui.tui.keymap import register_built_in as register_keymaps
from agent_os.host.tui.tui.keys import KeyEvent
from agent_os.host.tui.tui.motion import MotionPlayer
from agent_os.host.tui.tui.theme import ThemeRegistry
from agent_os.host.tui.tui.theme import register_built_in as register_themes

#: 焦点窗 → keymap context(§5.3;默认焦点 = 命令窗,命令窗是第一界面)
_FOCUS_CONTEXT = {"cmd": "dbg-cmd", "trace": "dbg-trace", "stack": "dbg-stack"}
_FOCUS_ORDER = ("cmd", "trace", "stack")

#: 会话状态 → (符号, 色 token, copy 键)(双编码:符号 + 文本 + 色)
_STATE_BADGE = {
    "paused": ("■", "warn", "dbg.state.paused"),
    "running": ("▶", "live", "dbg.state.running"),
    "detached": ("✓", "ok", "dbg.state.ended"),
    "armed": ("…", "fg-2", "dbg.state.running"),
    "none": ("─", "fg-2", "dbg.state.none"),
}


class DebuggerDef(WidgetDef):
    def get_kind(self) -> str:
        return "tui.dbg.debugger"

    def get_events(self) -> list[str]:
        return ["quit"]

    def get_intents(self) -> list[str]:
        return ["focus_next", "scroll_up", "scroll_down", "page_up", "page_down",
                "move_up", "move_down", "goto_top", "goto_bottom",
                "repeat_last", "interrupt", "bp_toggle", "redraw",
                "quit", "help", "confirm", "cancel"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return DebuggerApp(self, state)


class DebuggerApp(CompoundWidget):
    """state(全部可序列化):focus / status / quit / kill_armed /
    last_raw / last_name / selected_frame / conn。数据源与引擎由装配注入。"""

    CMD_ROWS = 8    # 命令窗高(分隔 + 滚动输出 + (adb) 输入行)
    LEFT_COL_W = 26  # 左栏宽(调用栈 + 断点表)
    POLL_INTERVAL = 2.0  # 轮询周期(秒;web debug-view.js POLL_MS 对译)

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.state.setdefault("focus", "cmd")  # 命令窗是第一界面(§0.1)
        self.state.setdefault("status", "")
        self.state.setdefault("quit", False)
        self.state.setdefault("kill_armed", False)  # kill 两段确认(app 层管,§5.1)
        self.state.setdefault("last_raw", "")
        self.state.setdefault("last_name", "")
        self.state.setdefault("selected_frame", 0)
        self.state.setdefault("conn", "demo")
        #: 装配注入(host 接线,不进 state)
        self.source: DebugSource | None = None
        self.engine: KeymapEngine | None = None
        self.motion: MotionPlayer | None = None
        self._env: ExecEnv | None = None
        #: 渲染路径快照缓存(render 不打数据源;refresh/tick 是唯一更新口)
        self._snap: dict[str, Any] = {}
        #: D2 异步回流簿记(瞬态,不进 state):已打停止行的暂停点/终态/轮询节奏
        self._pp_reported = ""
        self._ended_reported = False
        self._last_poll = 0.0
        self._conn_was_ok = False  # 首次 conn False = 尚未连上,不是断线

    # ------------------------------------------------------------------
    # 组合面
    # ------------------------------------------------------------------

    def get_slots(self) -> list[dict[str, Any]]:
        return [
            {"id": "stack", "kind": "tui.dbg.stack"},
            {"id": "bps", "kind": "tui.dbg.bps"},
            {"id": "trace", "kind": "tui.dbg.trace"},
            {"id": "cmd", "kind": "tui.dbg.cmd"},
        ]

    def get_dynamic_allow(self) -> list[str]:
        return ["tui.dbg.stack", "tui.dbg.bps", "tui.dbg.trace", "tui.dbg.cmd"]

    def stack_widget(self) -> StackWidget:
        return self.find_child("stack")  # type: ignore[return-value]

    def bps_widget(self) -> BpWidget:
        return self.find_child("bps")  # type: ignore[return-value]

    def trace_widget(self) -> TraceWidget:
        return self.find_child("trace")  # type: ignore[return-value]

    def cmd_widget(self) -> CmdWidget:
        return self.find_child("cmd")  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 数据面(app 层唯一出海;快照 → 三窗 state 整态替换)
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """快照 → 三窗(先管道后盖章:数据先行,▶ 定位/动效随后)。"""
        if self.source is None:
            return
        try:
            snap = self.source.snapshot()
            signals = self.source.signals()
        except Exception:  # noqa: BLE001 — 数据源故障给空面,不炸渲染
            return
        self._apply_snapshot(snap, signals)

    def _apply_snapshot(self, snap: dict[str, Any],
                        signals: list[dict[str, Any]]) -> None:
        self._snap = snap  # 渲染路径(header/状态行)只读本缓存,不打数据源
        rows = build_trace_rows(signals)
        bps = snap.get("breakpoints") or []
        paused_line = None
        if snap.get("state") == "paused" and snap.get("pause_point"):
            pidx = paused_signal_index(signals, snap["pause_point"])
            if pidx is not None:
                row = row_for_signal(rows, pidx)
                paused_line = row["line"] if row else None
        trace = self.trace_widget()
        trace.mutate_state(lambda st: st.update(rows=rows, breakpoints=bps,
                                                paused_line=paused_line))
        if paused_line is not None:
            trace.scroll_to_line(paused_line)
        frames = list(reversed(snap.get("frame_stack") or []))
        pp = snap.get("pause_point") or {}
        paused_fid = pp.get("frame_id") if snap.get("state") == "paused" else None
        env = self._env
        sel = min(env.selected_frame if env else 0, max(0, len(frames) - 1))
        self.stack_widget().mutate_state(lambda st: st.update(
            frames=frames, paused_fid=paused_fid, sel=sel))
        if env is not None:
            env.selected_frame = sel
        self.bps_widget().mutate_state(lambda st: st.update(bps=bps))

    # ------------------------------------------------------------------
    # 键面(物理键 → intent → 路由;焦点窗决定 context)
    # ------------------------------------------------------------------

    def feed_key(self, ev: KeyEvent) -> None:
        if self.engine is None:
            return
        focus = str(self.state.get("focus", "cmd"))
        context = _FOCUS_CONTEXT.get(focus, "dbg-cmd")
        # 命令窗常驻 INSERT(doc_editor InputWidget/模式感知先例;modal 包传 insert)
        mode = "insert" if (focus == "cmd" and self.engine.pack.modal) else None
        res, payload = self.engine.resolve(ev, context, mode)
        if res == RES_PREFIX:
            return
        if res == RES_SELF_INSERT and isinstance(payload, KeyEvent) and focus == "cmd":
            self.cmd_widget().insert_char(payload.key)
            return
        if res != RES_INTENT:
            return
        intent = str(payload)
        # 数据化自插入(dbg-cmd 把与 global 冲突的可打印键绑回 self_insert)
        if intent == SELF_INSERT and focus == "cmd":
            self.cmd_widget().insert_char(ev.key)
            return
        self._dispatch(intent, focus)

    def _dispatch(self, intent: str, focus: str) -> None:
        if intent == "focus_next":
            order = _FOCUS_ORDER
            nxt = order[(order.index(focus) + 1) % len(order)]
            self.mutate_state(lambda st: st.update(focus=nxt, status=""))
            return
        if intent == "quit":
            self._quit()
            return
        if intent == "help":
            self.run_command("help")
            return
        if intent == "redraw":
            self.mutate_state(lambda st: st.update(status=""))  # 全量重渲本就每帧
            return
        if intent == "interrupt":
            self._interrupt()
            return
        if intent == "repeat_last":
            self._repeat_last()
            return
        if intent == "confirm" and focus == "cmd":
            self.run_command(self.cmd_widget().take_input())
            return
        if intent == "bp_toggle" and focus == "trace":
            self._toggle_bp()
            return
        target: Widget = {"cmd": self.cmd_widget(), "trace": self.trace_widget(),
                          "stack": self.stack_widget()}[focus]
        handled = target.handle_intent(intent)  # type: ignore[attr-defined]
        if handled:
            if target is self.stack_widget():
                # 栈窗光标 = 选中帧(GDB 选帧即改检视上下文)
                sel = int(self.stack_widget().state.get("sel", 0))
                if self._env is not None:
                    self._env.selected_frame = sel
                self.mutate_state(lambda st: st.update(selected_frame=sel, status=""))
            else:
                self.mutate_state(lambda st: st.update(status=""))
            return
        if intent == "cancel":
            return  # emacs C-g 在命令窗 = 万能取消(命令窗无瞬态可取消,no-op)
        self.mutate_state(lambda st: st.update(
            status=sty.copy("dbg.err.unknown").format(cmd=intent)))

    def _quit(self) -> None:
        """q(只读窗):退 TUI 不 detach(§4;paused 会话给一行提示)。"""
        if self.source is not None:
            with suppress(Exception):  # 数据源故障不挡退出
                if self.source.snapshot().get("state") == "paused":
                    self.cmd_widget().write_lines([sty.copy("dbg.quit.hint")])
        self.mutate_state(lambda st: st.update(quit=True))
        self.emit_event("quit", {})

    def _interrupt(self) -> None:
        """C-c = pause(SIGINT 习惯;落在下一个可仲裁信号,精度边界见帮助)。"""
        if self.source is None:
            return
        try:
            snap = self.source.snapshot()
            if snap.get("state") == "running":
                self.source.command("pause")
                self.refresh()
                self._motion_for_transition("running")
            else:
                self.mutate_state(lambda st: st.update(status=sty.copy("dbg.err.not_running")))
        except Exception as e:  # noqa: BLE001 — 409 等翻状态行人话(§6)
            msg = str(e)
            self.mutate_state(lambda st, m=msg: st.update(status=m))

    # ------------------------------------------------------------------
    # 命令窗(GDB 命令语言;解析/执行在 commands.py,裸 Enter 白名单在这层)
    # ------------------------------------------------------------------

    def run_command(self, text: str) -> None:
        """提交一行(kill 两段确认拦截优先;空行 = 按白名单重复上一命令)。"""
        stripped = text.strip()
        if self.state.get("kill_armed"):
            self._kill_answer(stripped)
            return
        if not stripped:
            self._repeat_last()
            return
        self._echo_and_execute(text, store_last=True)

    def _repeat_last(self) -> None:
        """裸 Enter 重复(§5.2 白名单;危险命令 kill/run/detach/set/inject 永不重复)。"""
        raw = str(self.state.get("last_raw") or "")
        if str(self.state.get("last_name") or "") not in REPEATABLE or not raw:
            return
        self._echo_and_execute(raw, store_last=False)

    def _echo_and_execute(self, text: str, store_last: bool) -> None:
        cmd_w = self.cmd_widget()
        cmd_w.write_lines([f"{sty.copy('dbg.prompt')}{text.strip()}"])
        try:
            cmd = parse(text)
        except ParseError as e:
            cmd_w.write_lines(self._parse_error_lines(e))
            return
        assert self._env is not None
        prev_state = str(self._snap.get("state") or "")
        result = execute(self._env, cmd)
        cmd_w.write_lines(result.lines)
        if result.restarted:
            self._on_session_restarted()
        if result.kill_confirm:
            self.mutate_state(lambda st: st.update(kill_armed=True))
        if store_last:
            self.mutate_state(lambda st: st.update(last_raw=text.strip(),
                                                   last_name=cmd.name))
        if result.quit:
            self.mutate_state(lambda st: st.update(quit=True))
            self.emit_event("quit", {})
        if self._env is not None:
            self.mutate_state(lambda st: st.update(
                selected_frame=self._env.selected_frame))
        self.refresh()
        self._motion_for_transition(prev_state)

    def _motion_for_transition(self, prev_state: str) -> None:
        """命令路径动效钩(§8 先管道后动效:行已上屏/快照已刷,按状态迁移播具名)。
        同步源(demo/offline)的停点/终态在命令返回内落定;异步源(live)经
        SSE/轮询回流播(§4)。两边同过 _pp_reported/_ended_reported 去重,
        同一停点/同一终态只播一次。"""
        if self.motion is None or self.source is None:
            return
        if not getattr(self.source, "sync_control", True):
            return  # 异步源:转换未到,SSE/轮询路径播
        state = str(self._snap.get("state") or "")
        if state == "paused":
            pp = self._snap.get("pause_point") or {}
            key = self._pp_key(pp)
            if pp and key != self._pp_reported:
                self._pp_reported = key
                self.motion.play("paused", self.trace_widget())
        elif state == "detached" and prev_state != "detached" \
                and not self._ended_reported:
            self._ended_reported = True
            self._play_run_done(str(self._snap.get("end_status") or "unknown"))

    def _play_run_done(self, status: str) -> None:
        """终态定格章(章文走 dbg.end.run copy 键,与终态行同源)。"""
        if self.motion is not None:
            stamp = sty.copy("dbg.end.run").format(status=status)
            self.motion.play("run-done", self, text=f"[ {stamp} ]")

    def _kill_answer(self, answer: str) -> None:
        """kill 两段确认第二击(逐字学 GDB:`Kill the run being debugged? (y or n)`)。"""
        cmd_w = self.cmd_widget()
        self.mutate_state(lambda st: st.update(kill_armed=False))
        cmd_w.write_lines([f"{sty.copy('dbg.prompt')}{answer}"])
        if answer.lower() not in ("y", "yes"):
            return  # n/其他:静默回到提示符(GDB 同款)
        assert self._env is not None
        prev_state = str(self._snap.get("state") or "")
        cmd_w.write_lines(execute_stop(self._env))
        self.refresh()
        self._motion_for_transition(prev_state)

    @staticmethod
    def _parse_error_lines(e: ParseError) -> list[str]:
        if e.kind == "ambiguous":
            return [sty.copy("dbg.err.ambiguous").format(
                cmd=e.cmd, candidates=", ".join(e.candidates))]
        if e.kind == "usage":
            return [sty.copy("dbg.err.usage").format(usage=e.usage)]
        return [sty.copy("dbg.err.unknown").format(cmd=e.cmd)]

    # ------------------------------------------------------------------
    # bp_toggle(web gutter 的键盘等价物;bp_target_for_row 决定该行可否打)
    # ------------------------------------------------------------------

    def _toggle_bp(self) -> None:
        if self.source is None:
            return
        row = self.trace_widget().cursor_row()
        target = bp_target_for_row(row)
        if target is None:
            self.mutate_state(lambda st: st.update(status=sty.copy("dbg.err.no_bp_target")))
            return
        try:
            snap = self.source.snapshot()
        except Exception:  # noqa: BLE001
            return
        assert self._env is not None
        cmd_w = self.cmd_widget()
        for bp in snap.get("breakpoints") or []:
            if bp.get("kind") == target["kind"] and bp.get("match") == target["match"]:
                result = execute(self._env, Command("delete", [str(bp.get("num"))], ""))
                cmd_w.write_lines(result.lines)
                self.refresh()
                return
        spec = f"skill:{target['match']}" if target["kind"] == "skill_invoke" else target["match"]
        result = execute(self._env, Command("break", [spec], ""))
        cmd_w.write_lines(result.lines)
        self.refresh()

    # ------------------------------------------------------------------
    # tick(D2 live 回流,docs/TUI-DEBUG.md §4;__main__ 主循环每帧调,
    # 主循环是唯一 state 写者;无 drain_events 的源(demo/offline)早退)
    # ------------------------------------------------------------------

    def tick(self, now: float | None = None) -> None:
        """主循环心跳钩子:轮询(若到期)→ SSE queue drain → 事件路由。
        顺序有意:事件比任何轮询快照新,bp_hit 的原地更新不被同 tick 的
        全量快照覆盖。会话在期间隔 POLL_INTERVAL 刷轨迹(SSE 模式)/
        快照+轨迹(断线回落模式,web pollTick 对译:paused/detached 转换
        在此发现)。"""
        src = self.source
        if src is None:
            return
        if getattr(src, "session_id", "") and not getattr(src, "ended", False):
            now = time.monotonic() if now is None else now
            if now - self._last_poll >= self.POLL_INTERVAL:
                self._last_poll = now
                if getattr(src, "sse_down", False):
                    self._poll_transitions()
                else:
                    self._refresh_trace()  # SSE 模式:ticker 只补轨迹(web 同)
        drain = getattr(src, "drain_events", None)
        if callable(drain):
            for evt, data in drain():
                self._on_stream_event(evt, data)

    def _on_stream_event(self, evt: str, data: dict[str, Any]) -> None:
        """SSE 事件路由(state/bp_hit/paused/resumed/run_end/connection)。"""
        if evt == "connection":
            self._on_connection(bool(data.get("ok")))
            return
        if evt == "state":
            self.refresh()  # 连接快照 → 全量换 doc
            return
        if evt == "bp_hit":
            self._on_bp_hit(data)
            return
        if evt == "paused":
            # paused 只带 pause_point → 拉全量快照 + signals 再打停止行(§4)
            if self.source is None:
                return
            try:
                snap = self.source.snapshot()
                signals = self.source.signals()
            except Exception:  # noqa: BLE001 — 拉取失败下轮再来
                return
            self._apply_snapshot(snap, signals)
            self._report_pause(snap)
            return
        if evt == "resumed":
            self._pp_reported = ""
            self.refresh()
            return
        if evt == "run_end":
            self._report_run_end(str(data.get("status") or "") or None)

    def _on_session_restarted(self) -> None:
        """rerun/session 切换后的复位(§4 换绑纪律):终态/停止行/连接簿记
        清零——旧会话的 ended/off 不能罩住新会话;conn 回 poll 等 SSE 首连。"""
        self._pp_reported = ""
        self._ended_reported = False
        self._conn_was_ok = False
        self.mutate_state(lambda st: st.update(conn="poll", kill_armed=False))
        self.refresh()

    def _on_connection(self, ok: bool) -> None:
        """连接态点:SSE 断 → ○ poll + 命令窗人话(回落轮询);恢复静默切回。
        首次 False 是"尚未连上"(start_stream 同步上报,proto 语义),不是
        断线——只有真连上过再断才打人话(web es.onerror 同语义)。"""
        if self._ended_reported:
            return  # run_end 后服务关流是正常收尾,不算断线
        if ok:
            self._conn_was_ok = True
            self.mutate_state(lambda st: st.update(conn="online"))
            return
        was = self._conn_was_ok
        self.mutate_state(lambda st: st.update(conn="poll"))
        if was:
            self.cmd_widget().write_lines([sty.copy("dbg.conn.down_msg")])

    def _on_bp_hit(self, data: dict[str, Any]) -> None:
        """hits 差分事件:断点表 + gutter 原地更新计数(web bp_hit 同语义)。"""
        bid = data.get("breakpoint_id")
        hits = data.get("hits")

        def _patch(key: str):
            def _do(st: dict[str, Any]) -> None:
                for bp in st.get(key) or []:
                    if isinstance(bp, dict) and bp.get("id") == bid:
                        bp["hits"] = hits
            return _do

        self.bps_widget().mutate_state(_patch("bps"))
        self.trace_widget().mutate_state(_patch("breakpoints"))
        if self.motion is not None:  # 先管道后动效:计数已落,断点行短闪
            self.bps_widget().hit_num = next(
                (bp.get("num") for bp in self.bps_widget().state.get("bps") or []
                 if isinstance(bp, dict) and bp.get("id") == bid), None)
            self.motion.play("bp-hit", self.bps_widget())

    def _report_pause(self, snap: dict[str, Any]) -> None:
        """停止行(按暂停点去重:命令路径与轮询路径可能先后看到同一停点)。"""
        pp = snap.get("pause_point") or {}
        key = self._pp_key(pp)
        if not pp or key == self._pp_reported:
            return
        self._pp_reported = key
        self.cmd_widget().write_lines([_stop_line(snap)])
        ack = getattr(self.source, "ack_entry_breakpoint", None)
        if callable(ack):
            with suppress(Exception):  # 入口断点删除失败不挡流程
                if ack(pp):
                    self.refresh()  # 入口断点已删:重拉快照刷断点表
        if self.motion is not None:  # 先打印后动效(先管道后盖章)
            self.motion.play("paused", self.trace_widget())

    def _report_run_end(self, status: str | None) -> None:
        """终态行 + 横幅 + 停流(GDB [Inferior 1 exited …] 的对应物)。"""
        if self._ended_reported:
            return
        self._ended_reported = True
        src = self.source
        if status is None and src is not None:
            getter = getattr(src, "end_status", None)
            status = getter() if callable(getter) else None
        status = status or "unknown"
        self.refresh()  # 终态快照(breakpoints 带最终 hits)+ 轨迹补全
        lines = [sty.copy("dbg.end.run").format(status=status)]
        if src is not None:
            path_fn = getattr(src, "artifacts_path", None)
            path = path_fn() if callable(path_fn) else ""
            if path:
                lines.append(sty.copy("dbg.end.artifacts").format(path=path))
        self.cmd_widget().write_lines(lines)
        self.mutate_state(lambda st: st.update(conn="off"))
        self._snap = dict(self._snap, state="detached", end_status=status)
        self._play_run_done(status)
        close = getattr(src, "close", None)
        if callable(close):
            close()

    def _poll_transitions(self) -> None:
        """断线回落轮询(§4;sse_down 时快照+signals 同周期;单次失败静默)。"""
        if self.source is None:
            return
        try:
            snap = self.source.snapshot()
            signals = self.source.signals()
        except Exception:  # noqa: BLE001 — 单次轮询失败静默,下周期重试(web 同)
            return
        self._apply_snapshot(snap, signals)
        if snap.get("state") == "detached":
            self._report_run_end(None)
        elif snap.get("state") == "paused":
            self._report_pause(snap)

    def _refresh_trace(self) -> None:
        """轨迹常驻轮刷(SSE 模式;snapshot 由事件驱动,不整表重拉)。"""
        if self.source is None:
            return
        try:
            signals = self.source.signals()
        except Exception:  # noqa: BLE001
            return
        rows = build_trace_rows(signals)
        self.trace_widget().mutate_state(lambda st: st.update(rows=rows))

    @staticmethod
    def _pp_key(pp: dict[str, Any]) -> str:
        """暂停点身份(signal/frame/step/tool;停止行去重键)。"""
        if not pp:
            return ""
        return json.dumps({k: pp.get(k) for k in ("signal", "frame_id", "step", "tool")},
                          sort_keys=True, default=repr)

    # ------------------------------------------------------------------
    # 渲染(§7 三窗布局;全量重渲,先清后建)
    # ------------------------------------------------------------------

    def render_into(self, buf: CellBuffer, region: Region) -> None:
        w, h = region.w, region.h
        focus = str(self.state.get("focus", "cmd"))
        cmd_h = max(6, min(self.CMD_ROWS, h - 6))
        body_h = max(2, h - 2 - cmd_h)
        left_w = max(18, min(self.LEFT_COL_W, w // 3)) if w >= 48 else max(12, w // 3)
        for fid, widget in (("cmd", self.cmd_widget()), ("trace", self.trace_widget()),
                            ("stack", self.stack_widget())):
            widget.mutate_state(lambda st, on=(fid == focus): st.update(focused=on))
        self._render_header(buf, region)
        body = Region(region.x, region.y + 1, w, body_h)
        stack_h = max(3, body_h // 2)
        self.stack_widget().render_into(buf, Region(body.x, body.y, left_w, stack_h))
        self.bps_widget().render_into(
            buf, Region(body.x, body.y + stack_h, left_w, body_h - stack_h))
        for row in range(body_h):  # 左右栏分隔竖线
            buf.write(region.x + left_w, body.y + row, "│", sty.text("line"))
        self.trace_widget().render_into(
            buf, Region(body.x + left_w + 1, body.y, w - left_w - 1, body_h))
        self.cmd_widget().render_into(buf, Region(region.x, region.y + 1 + body_h, w, cmd_h))
        self._render_status(buf, region)
        if self.motion is not None:
            for _target, reg in self.motion.active_regions():
                buf.reverse_region(reg.x, reg.y, reg.w, reg.h)

    def _state_badge(self) -> tuple[str, str, str]:
        """会话状态 → (符号, 色 token, 文本)(双编码;ENDED 带终态词)。
        读渲染路径快照缓存(refresh/tick 更新),渲染不打数据源。"""
        snap = self._snap
        state = str(snap.get("state") or "none")
        sym, token, copy_key = _STATE_BADGE.get(state, _STATE_BADGE["none"])
        text = sty.copy(copy_key)
        if state == "detached" and snap.get("end_status"):
            status = str(snap["end_status"])
            text = f"{text} {status}"
            if status == "failed":
                sym, token = "✗", "danger"
            elif status == "aborted":
                sym, token = "⊘", "aborted"
        return sym, token, text

    def _render_header(self, buf: CellBuffer, region: Region) -> None:
        label = self.source.label() if self.source is not None else ""
        sid = str(self._snap.get("session_id") or "")
        sym, token, state_text = self._state_badge()
        title = f"─ #{sid or 'adb'} · {label} · "
        buf.write(region.x, region.y, title, sty.text("fg-2"), max_width=region.w)
        x = text_width(title)
        badge = f"{sym} {state_text}"
        buf.write(x, region.y, badge, sty.text(token, bold=True),
                  max_width=max(0, region.w - x))
        x += text_width(badge) + 1
        buf.write(x, region.y, "─" * max(0, region.w - x), sty.text("fg-2"),
                  max_width=max(0, region.w - x))

    def _render_status(self, buf: CellBuffer, region: Region) -> None:
        """状态行:会话状态(双编码)+ 焦点窗(切换键从活动 keymap 生成)+
        连接态点(dbg.conn.* copy 键:demo/offline/online/poll/off)+ 帮助键;
        run_end 定格章(run-done 动效)覆盖左段。"""
        y = region.y + region.h - 1
        style = sty.status_bar()
        buf.fill(region.x, y, region.w, 1, style)
        sym, token, state_text = self._state_badge()
        buf.write(region.x, y, f" {sym} ", sty.text(token, bold=True))
        left = f" {state_text}"
        status = str(self.state.get("status") or "")
        if status:
            left += f" · {status}"
        if self.motion is not None:
            stamps = self.motion.active_stamps()
            if stamps:  # 终态横幅(定格期恒亮;run-done 章文)
                left = f" {stamps[-1]}"
        focus = str(self.state.get("focus", "cmd"))
        right = ""
        if self.engine is not None:
            ctx = _FOCUS_CONTEXT.get(focus, "dbg-cmd")
            switch = self.engine.binding_for("focus_next", ctx) or "C-x o"
            help_key = self.engine.binding_for("help", ctx) or "?"
            conn = sty.copy(f"dbg.conn.{self.state.get('conn', 'demo')}")
            right = f"foc={focus} ({switch} 切换) · conn{conn} · {help_key} 帮助"
            pending = self.engine.pending_display
            if pending:
                right = f"{pending}…  " + right
        right_w = text_width(right)
        left_max = max(0, region.w - right_w - 2) if right else region.w - 1
        buf.write(region.x + 4, y, left, style, max_width=left_max)
        if right:
            buf.write(region.x + max(0, region.w - right_w - 1), y, right, style)

    # ------------------------------------------------------------------

    def context_fragment(self) -> dict[str, Any]:
        return {"kind": self.get_kind(), "app": "debugger",
                "focus": self.state.get("focus", "cmd")}

    def summary(self) -> str:
        sym, _token, text = self._state_badge()
        return f"调试器({sym} {text})"


# ---------------------------------------------------------------------------
# 装配(host 接线:数据源/键位/动效注入;__main__ 与无头测试共用)
# ---------------------------------------------------------------------------

def build_registries() -> None:
    """内置主题/键位包注册(幂等;契约校验在注册面)。"""
    if ThemeRegistry.current() is None:
        register_themes()
    if KeymapRegistry.current() is None:
        register_keymaps()


def build_app(
    source: DebugSource,
    *,
    keymap_id: str = "",
    reduced_motion: bool = False,
    tree: WidgetTree | None = None,
) -> tuple[WidgetTree, DebuggerApp, KeymapEngine, MotionPlayer]:
    """装配一棵可跑的树(与 doc_editor 同签名四元组):
    注册表 → tree → app(slots)→ 数据源/引擎接线。"""
    build_registries()
    pack = KeymapRegistry.current()
    if keymap_id:
        KeymapRegistry.apply(keymap_id)
        pack = KeymapRegistry.current()
    assert pack is not None
    ThemeRegistry.reduced_motion = reduced_motion
    engine = KeymapEngine(pack)
    motion = MotionPlayer()
    registry = WidgetRegistry(required_intents=list(pack.intents()))
    for def_ in (StackDef(), BpDef(), TraceDef(), CmdDef(), DebuggerDef()):
        registry.register(def_)
    if tree is None:
        tree = WidgetTree(motion=motion)
    app = registry.create("tui.dbg.debugger", {})
    assert isinstance(app, DebuggerApp)
    tree.root.add_child_widget(app, "debugger")
    app.create_slots(registry)
    app.source = source
    app.engine = engine
    app.motion = motion
    app._env = ExecEnv(source)
    app.mutate_state(lambda st: st.update(conn=str(getattr(source, "conn", "demo"))))
    app.refresh()
    tree.focus_target = lambda _w: None  # focus 动词面留 D2+(寻址已通)
    return tree, app, engine, motion
