"""调试器四窗(docs/TUI-DEBUG.md §2/§7;GDB TUI 式布局的窗格件)。

- ``StackWidget``(调用栈,bt 行):``#N skill f-id`` + ▶ 当前(暂停)帧 +
  选中帧反色(焦点时);#0 = 栈顶(GDB 编号习惯)。
- ``BpWidget``(断点,info b 表):常驻表头走 ``dbg.bps.header`` copy 键;
  bp_hit 计数由 app 刷新(原地更新)+ bp-hit 短闪(hit_num 行)。
- ``TraceWidget``(执行轨迹,源码窗等价物):gutter ``●`` 断点 + 行号 +
  ``▶`` 暂停行(反色);行语言 = trace_rows 对译文本;光标行给 bp_toggle
  (web gutter 的键盘等价物,bp_target_for_row 决定该行可否打)。
- ``CmdWidget``(命令窗,第一界面):多行滚动输出(停止行/回声/info 输出全进
  此窗)+ ``(adb)`` 输入行 + ↑↓ 历史。

三铁律不变:state 全可 JSON 序列化;组件零按键分支(只响应 intent);
组件零主题分支(色/文案全走 sty + copy 键)。focused 由 app 写进 state
(选中行反色只在焦点窗;状态行 foc= 是双编码的文字通道)。
"""

from __future__ import annotations

from typing import Any

from agent_os.host.tui.apps.debugger.trace_rows import (
    body_text,
    bp_target_for_row,
    fmt_dur,
    short_skill,
)
from agent_os.host.tui.kernel.widget import Widget
from agent_os.host.tui.kernel.widget_def import WidgetDef
from agent_os.host.tui.tui import sty
from agent_os.host.tui.tui.cells import CellBuffer, Region, Style, text_width

#: 轨迹行 kind → sig-* 色 token(色 token 零新增,§8)
_KIND_TOKEN = {
    "run": "fg-0", "call": "sig-frame", "ret": "sig-frame",
    "llm": "sig-llm", "tool": "sig-tool", "exec": "sig-sidecar",
    "inline": "fg-2", "obs": "warn",
}


def _title(buf: CellBuffer, region: Region, text: str) -> None:
    """窗格标题行(─ 标题 ─── 形;标题文案走 copy 键,零主题分支)。"""
    head = f"─ {text} "
    rule = head + "─" * max(0, region.w - text_width(head))
    buf.write(region.x, region.y, rule, sty.text("fg-2"), max_width=region.w)


def _clamp_scroll(scroll: int, total: int, height: int, keep: int) -> int:
    """滚动回收:keep(光标/暂停行)必须入窗。"""
    scroll = max(0, min(scroll, keep))
    if keep >= scroll + height:
        scroll = keep - height + 1
    return max(0, scroll)


# ---------------------------------------------------------------------------
# 调用栈(bt 行;#0 = 栈顶)
# ---------------------------------------------------------------------------


class StackDef(WidgetDef):
    def get_kind(self) -> str:
        return "tui.dbg.stack"

    def get_intents(self) -> list[str]:
        return ["move_up", "move_down", "goto_top", "goto_bottom",
                "scroll_up", "scroll_down", "page_up", "page_down"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return StackWidget(self, state)


class StackWidget(Widget):
    """state: {frames: [{frame_id, skill, depth}](顶→底), paused_fid, sel,
    scroll, focused}。sel = 选中帧(#N,app 的检视上下文,与 frame N 联动)。"""

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.state.setdefault("frames", [])
        self.state.setdefault("paused_fid", None)
        self.state.setdefault("sel", 0)
        self.state.setdefault("scroll", 0)
        self.state.setdefault("focused", False)

    def handle_intent(self, intent: str, page_rows: int = 4) -> bool:
        frames = self.state["frames"]
        sel = int(self.state.get("sel", 0))
        if intent == "move_down" and frames:
            self.mutate_state(lambda st: st.update(sel=min(sel + 1, len(frames) - 1)))
            return True
        if intent == "move_up" and frames:
            self.mutate_state(lambda st: st.update(sel=max(sel - 1, 0)))
            return True
        if intent == "goto_top" and frames:
            self.mutate_state(lambda st: st.update(sel=0))
            return True
        if intent == "goto_bottom" and frames:
            self.mutate_state(lambda st: st.update(sel=len(frames) - 1))
            return True
        if intent == "scroll_up":
            self.mutate_state(lambda st: st.update(scroll=max(0, int(st.get("scroll", 0)) - 1)))
            return True
        if intent == "scroll_down":
            self.mutate_state(lambda st: st.update(scroll=int(st.get("scroll", 0)) + 1))
            return True
        if intent == "page_up":
            self.mutate_state(lambda st: st.update(scroll=max(0, int(st.get("scroll", 0)) - page_rows)))
            return True
        if intent == "page_down":
            self.mutate_state(lambda st: st.update(scroll=int(st.get("scroll", 0)) + page_rows))
            return True
        return False

    def render_into(self, buf: CellBuffer, region: Region) -> None:
        _title(buf, region, sty.copy("dbg.pane.stack"))
        frames = self.state["frames"]
        body_h = region.h - 1
        if body_h <= 0:
            return
        if not frames:
            buf.write(region.x + 1, region.y + 1, sty.copy("dbg.empty.stack"),
                      sty.text("fg-2"), max_width=region.w - 1)
            return
        paused_fid = self.state.get("paused_fid")
        sel = min(int(self.state.get("sel", 0)), len(frames) - 1)
        focused = bool(self.state.get("focused"))
        scroll = _clamp_scroll(int(self.state.get("scroll", 0)), len(frames), body_h, sel)
        self.mutate_state(lambda st: st.update(scroll=scroll, sel=sel))
        for row in range(body_h):
            idx = scroll + row
            if idx >= len(frames):
                break
            f = frames[idx]
            fid = str(f.get("frame_id") or "")
            current = fid and fid == paused_fid
            mark = "▶" if current else " "
            text = f"{mark}#{idx} {short_skill(f.get('skill'))} {fid}"
            style: Style = sty.text("fg-0")
            if idx == sel and focused:
                style = sty.selected()
                buf.fill(region.x, region.y + 1 + row, region.w, 1, style)
            elif current:
                style = sty.text("live", bold=True)
            buf.write(region.x, region.y + 1 + row, text, style, max_width=region.w)

    def summary(self) -> str:
        return f"调用栈({len(self.state['frames'])} 帧)"


# ---------------------------------------------------------------------------
# 断点表(info b;Num/Kind/Match/Enb/Hits 常驻)
# ---------------------------------------------------------------------------


class BpDef(WidgetDef):
    def get_kind(self) -> str:
        return "tui.dbg.bps"

    def get_intents(self) -> list[str]:
        return ["scroll_up", "scroll_down"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return BpWidget(self, state)


class BpWidget(Widget):
    """state: {bps: [{num, kind, match, enabled, hits}], scroll}。
    hit_num = 最近一次 bp_hit 事件的断点号(bp-hit 短闪目标;渲染期记行区,
    pulse_region 惰性回报,跟布局走)。"""

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.state.setdefault("bps", [])
        self.state.setdefault("scroll", 0)
        self.hit_num: Any = None
        self._hit_region: Region | None = None  # 渲染期瞬态(pulse_region 数据源)

    def handle_intent(self, intent: str) -> bool:
        if intent == "scroll_up":
            self.mutate_state(lambda st: st.update(scroll=max(0, int(st.get("scroll", 0)) - 1)))
            return True
        if intent == "scroll_down":
            self.mutate_state(lambda st: st.update(scroll=int(st.get("scroll", 0)) + 1))
            return True
        return False

    def render_into(self, buf: CellBuffer, region: Region) -> None:
        _title(buf, region, sty.copy("dbg.pane.bps"))
        if region.h <= 1:
            return
        buf.write(region.x, region.y + 1, sty.copy("dbg.bps.header"),
                  sty.text("fg-2"), max_width=region.w)
        bps = self.state["bps"]
        if not bps:
            self._hit_region = None
            if region.h > 2:
                buf.write(region.x + 1, region.y + 2, sty.copy("dbg.empty.bps"),
                          sty.text("fg-2"), max_width=region.w - 1)
            return
        scroll = min(int(self.state.get("scroll", 0)), max(0, len(bps) - 1))
        self._hit_region = None
        for row in range(region.h - 2):
            idx = scroll + row
            if idx >= len(bps):
                break
            bp = bps[idx]
            y = region.y + 2 + row
            if self.hit_num is not None and bp.get("num") == self.hit_num:
                self._hit_region = Region(region.x, y, region.w, 1)
            enb = "y" if bp.get("enabled", True) else "n"
            hits = int(bp.get("hits") or 0)
            hit_text = f"×{hits}" if hits else "0"
            text = (f"{bp.get('num', '?'):<4}{bp.get('kind') or ''!s:<13}"
                    f"{bp.get('match') or ''!s:<13}{enb:<4}{hit_text}")
            buf.write(region.x, y, text, sty.text("fg-0"),
                      max_width=region.w)

    def pulse_region(self) -> Region | None:
        return self._hit_region

    def summary(self) -> str:
        return f"断点({len(self.state['bps'])})"


# ---------------------------------------------------------------------------
# 执行轨迹(源码窗等价物;gutter ● + ▶ 暂停行)
# ---------------------------------------------------------------------------


class TraceDef(WidgetDef):
    def get_kind(self) -> str:
        return "tui.dbg.trace"

    def get_intents(self) -> list[str]:
        return ["move_up", "move_down", "goto_top", "goto_bottom",
                "scroll_up", "scroll_down", "page_up", "page_down", "bp_toggle"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return TraceWidget(self, state)


class TraceWidget(Widget):
    """state: {rows: [trace_rows 行], breakpoints: [{kind, match, enabled}],
    paused_line, cursor, scroll, focused}。cursor = 光标行(bp_toggle 目标)。"""

    GUTTER_W = 2   # "● "
    NUM_W = 5      # "0003 "
    MARK_W = 2     # "▶ "

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.state.setdefault("rows", [])
        self.state.setdefault("breakpoints", [])
        self.state.setdefault("paused_line", None)
        self.state.setdefault("cursor", 0)
        self.state.setdefault("scroll", 0)
        self.state.setdefault("focused", False)
        self._paused_region: Region | None = None  # 渲染期瞬态(focus-pulse 用)

    def cursor_row(self) -> dict[str, Any] | None:
        rows = self.state["rows"]
        if not rows:
            return None
        cur = min(max(0, int(self.state.get("cursor", 0))), len(rows) - 1)
        return rows[cur]

    def scroll_to_line(self, line: int | None) -> None:
        """光标定位到某行号(暂停行跟随;line = 1 起)。"""
        rows = self.state["rows"]
        if line is None or not rows:
            return
        idx = next((i for i, r in enumerate(rows) if r.get("line") == line), None)
        if idx is not None:
            self.mutate_state(lambda st: st.update(cursor=idx))

    def handle_intent(self, intent: str, page_rows: int = 8) -> bool:
        rows = self.state["rows"]
        cur = int(self.state.get("cursor", 0))
        if intent == "move_down" and rows:
            self.mutate_state(lambda st: st.update(cursor=min(cur + 1, len(rows) - 1)))
            return True
        if intent == "move_up" and rows:
            self.mutate_state(lambda st: st.update(cursor=max(cur - 1, 0)))
            return True
        if intent == "goto_top" and rows:
            self.mutate_state(lambda st: st.update(cursor=0))
            return True
        if intent == "goto_bottom" and rows:
            self.mutate_state(lambda st: st.update(cursor=len(rows) - 1))
            return True
        if intent == "scroll_up":
            self.mutate_state(lambda st: st.update(scroll=max(0, int(st.get("scroll", 0)) - 1)))
            return True
        if intent == "scroll_down":
            self.mutate_state(lambda st: st.update(scroll=int(st.get("scroll", 0)) + 1))
            return True
        if intent == "page_up":
            self.mutate_state(lambda st: st.update(
                scroll=max(0, int(st.get("scroll", 0)) - page_rows)))
            return True
        if intent == "page_down":
            self.mutate_state(lambda st: st.update(scroll=int(st.get("scroll", 0)) + page_rows))
            return True
        return False

    def bp_on_row(self, row: dict[str, Any]) -> bool:
        """该行是否有生效断点(gutter ●;目标对译 bp_target_for_row)。"""
        target = bp_target_for_row(row)
        if target is None:
            return False
        return any(
            bp.get("kind") == target["kind"] and bp.get("match") == target["match"]
            and bp.get("enabled", True)
            for bp in self.state["breakpoints"]
        )

    def render_into(self, buf: CellBuffer, region: Region) -> None:
        _title(buf, region, sty.copy("dbg.pane.trace"))
        rows = self.state["rows"]
        body_h = region.h - 1
        self._paused_region = None
        if body_h <= 0:
            return
        if not rows:
            buf.write(region.x + 1, region.y + 1, sty.copy("dbg.empty.trace"),
                      sty.text("fg-2"), max_width=region.w - 1)
            return
        cur = min(int(self.state.get("cursor", 0)), len(rows) - 1)
        paused_line = self.state.get("paused_line")
        focused = bool(self.state.get("focused"))
        keep = cur
        if paused_line is not None:
            pidx = next((i for i, r in enumerate(rows) if r.get("line") == paused_line), None)
            if pidx is not None:
                keep = pidx
        scroll = _clamp_scroll(int(self.state.get("scroll", 0)), len(rows), body_h, keep)
        self.mutate_state(lambda st: st.update(scroll=scroll, cursor=cur))
        for row in range(body_h):
            idx = scroll + row
            if idx >= len(rows):
                break
            self._render_row(buf, region, region.y + 1 + row, rows[idx],
                             idx == cur and focused)

    def _render_row(self, buf: CellBuffer, region: Region, y: int,
                    row: dict[str, Any], is_cursor: bool) -> None:
        paused = row.get("line") == self.state.get("paused_line")
        token = _KIND_TOKEN.get(str(row.get("kind")), "fg-0")
        failed = row.get("status") in ("failed", "vetoed", "aborted")
        style = sty.text("danger" if failed else token)
        if str(row.get("kind")) == "run":
            style = style.with_attrs("bold")
        if paused:
            # 暂停行反色(▶ + reverse,双编码;GDB 源码窗 PC 行)
            style = Style(style.fg, style.bg, style.attrs | frozenset({"reverse"}))
            buf.fill(region.x, y, region.w, 1, style)
            self._paused_region = Region(region.x, y, region.w, 1)
        elif is_cursor:
            buf.fill(region.x, y, region.w, 1, sty.selected())
            style = sty.selected()
        x = region.x
        x += buf.write(x, y, "●" if self.bp_on_row(row) else " ",
                       sty.text("danger", bold=True), max_width=self.GUTTER_W)
        x = region.x + self.GUTTER_W
        x += buf.write(x, y, f"{int(row.get('line') or 0):04d}", sty.text("fg-2"),
                       max_width=self.NUM_W - 1)
        x = region.x + self.GUTTER_W + self.NUM_W
        x += buf.write(x, y, "▶" if paused else " ",
                       sty.text("warn", bold=True), max_width=self.MARK_W - 1)
        x = region.x + self.GUTTER_W + self.NUM_W + self.MARK_W
        depth = max(0, int(row.get("depth") or 0))
        indent = "  " * depth
        x += buf.write(x, y, indent, style)
        x += buf.write(x, y, body_text(row), style,
                       max_width=max(0, region.x + region.w - x))
        dur = fmt_dur(row.get("dur_ms"))
        if dur and x + text_width(dur) + 1 < region.x + region.w:
            buf.write(region.x + region.w - text_width(dur) - 1, y, dur, sty.text("fg-2"))

    def pulse_region(self) -> Region | None:
        return self._paused_region

    def summary(self) -> str:
        return f"轨迹({len(self.state['rows'])} 行)"


# ---------------------------------------------------------------------------
# 命令窗(第一界面;滚动输出 + (adb) 输入行 + ↑↓ 历史)
# ---------------------------------------------------------------------------


class CmdDef(WidgetDef):
    def get_kind(self) -> str:
        return "tui.dbg.cmd"

    def get_intents(self) -> list[str]:
        return ["move_left", "move_right", "line_start", "line_end",
                "edit_back", "edit_delete", "edit_kill_line", "edit_kill_word",
                "history_prev", "history_next", "scroll_up", "scroll_down",
                "confirm", "cancel"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return CmdWidget(self, state)


class CmdWidget(Widget):
    """state: {lines(滚动输出), input, cursor, history, hist_idx, stash,
    scroll(0 = 跟随尾)}。输入行单行;可打印键由 app 直接喂 insert_char。"""

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.state.setdefault("lines", [])
        self.state.setdefault("input", "")
        self.state.setdefault("cursor", 0)
        self.state.setdefault("history", [])
        self.state.setdefault("hist_idx", None)
        self.state.setdefault("stash", "")
        self.state.setdefault("scroll", 0)
        self._cursor_cell: tuple[int, int] | None = None

    # ------------------------------------------------------------------
    # 输出面(app 层写;输出即历史,GDB 习惯)
    # ------------------------------------------------------------------

    def write_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        def _w(st: dict[str, Any]) -> None:
            st["lines"].extend(str(ln) for ln in lines)
            st["scroll"] = 0  # 新输出跟随尾(显式滚动后被新输出拉回,GDB 同)
        self.mutate_state(_w)

    def set_input(self, text: str) -> None:
        self.mutate_state(lambda st: st.update(
            input=text, cursor=len(text), hist_idx=None, stash=""))

    def take_input(self) -> str:
        """取走当前输入(提交;非空行进历史)。"""
        text = str(self.state.get("input") or "")
        def _t(st: dict[str, Any]) -> None:
            if text.strip():
                st["history"].append(text)
            st.update(input="", cursor=0, hist_idx=None, stash="")
        self.mutate_state(_t)
        return text

    # ------------------------------------------------------------------
    # 编辑(intent 面;readline 基本键,§5.3)
    # ------------------------------------------------------------------

    def insert_char(self, ch: str) -> None:
        def _ins(st: dict[str, Any]) -> None:
            cur = max(0, min(int(st.get("cursor", 0)), len(st["input"])))
            st["input"] = st["input"][:cur] + ch + st["input"][cur:]
            st["cursor"] = cur + len(ch)
        self.mutate_state(_ins)

    def handle_intent(self, intent: str) -> bool:
        text = str(self.state.get("input") or "")
        cur = max(0, min(int(self.state.get("cursor", 0)), len(text)))
        if intent == "move_left":
            self.mutate_state(lambda st: st.update(cursor=max(0, cur - 1)))
            return True
        if intent == "move_right":
            self.mutate_state(lambda st: st.update(cursor=min(len(st["input"]), cur + 1)))
            return True
        if intent == "line_start":
            self.mutate_state(lambda st: st.update(cursor=0))
            return True
        if intent == "line_end":
            self.mutate_state(lambda st: st.update(cursor=len(st["input"])))
            return True
        if intent == "edit_back" and cur > 0:
            self.mutate_state(lambda st: st.update(
                input=st["input"][: cur - 1] + st["input"][cur:], cursor=cur - 1))
            return True
        if intent == "edit_delete" and cur < len(text):
            self.mutate_state(lambda st: st.update(
                input=st["input"][:cur] + st["input"][cur + 1:]))
            return True
        if intent == "edit_kill_line":  # C-u:删整行(readline unix-line-discard 近似)
            self.mutate_state(lambda st: st.update(input="", cursor=0))
            return True
        if intent == "edit_kill_word" and cur > 0:  # C-w:删前一词(readline)
            i = cur
            while i > 0 and text[i - 1] == " ":
                i -= 1
            while i > 0 and text[i - 1] != " ":
                i -= 1
            self.mutate_state(lambda st: st.update(
                input=st["input"][:i] + st["input"][cur:], cursor=i))
            return True
        if intent == "history_prev":
            history = self.state["history"]
            if not history:
                return True
            idx = self.state.get("hist_idx")
            if idx is None:
                self.mutate_state(lambda st: st.update(stash=st["input"]))
                idx = len(history)
            idx = max(0, int(idx) - 1)
            text_new = str(history[idx])
            self.mutate_state(lambda st: st.update(
                hist_idx=idx, input=text_new, cursor=len(text_new)))
            return True
        if intent == "history_next":
            idx = self.state.get("hist_idx")
            if idx is None:
                return True
            idx = int(idx) + 1
            history = self.state["history"]
            if idx >= len(history):
                stash = str(self.state.get("stash") or "")
                self.mutate_state(lambda st: st.update(
                    hist_idx=None, input=stash, cursor=len(stash)))
            else:
                text_new = str(history[idx])
                self.mutate_state(lambda st: st.update(
                    hist_idx=idx, input=text_new, cursor=len(text_new)))
            return True
        if intent == "scroll_up":
            self.mutate_state(lambda st: st.update(scroll=int(st.get("scroll", 0)) + 1))
            return True
        if intent == "scroll_down":
            self.mutate_state(lambda st: st.update(scroll=max(0, int(st.get("scroll", 0)) - 1)))
            return True
        return False

    # ------------------------------------------------------------------
    # 渲染(分隔行 + 滚动输出 + (adb) 输入行;光标格反色)
    # ------------------------------------------------------------------

    def render_into(self, buf: CellBuffer, region: Region) -> None:
        _title(buf, region, sty.copy("dbg.pane.cmd"))
        if region.h <= 1:
            return
        out_h = region.h - 2
        lines = self.state["lines"]
        scroll = min(int(self.state.get("scroll", 0)), max(0, len(lines) - out_h))
        tail = len(lines) - scroll
        start = max(0, tail - out_h)
        for row in range(out_h):
            idx = start + row
            if idx >= tail:
                break
            buf.write(region.x, region.y + 1 + row, str(lines[idx]), sty.text("fg-0"),
                      max_width=region.w)
        if scroll > 0:  # 回看指示(双编码:符号 + 计数)
            buf.write(region.x + region.w - 8, region.y,
                      f" ↑{scroll}", sty.text("warn", bold=True), max_width=8)
        # 输入行:(adb) 提示符 + 文本 + 光标反色(超宽左裁,光标恒可见)
        y = region.y + region.h - 1
        prompt = sty.copy("dbg.prompt")
        buf.write(region.x, y, prompt, sty.text("live", bold=True))
        avail = max(4, region.w - text_width(prompt))
        text = str(self.state.get("input") or "")
        cur = max(0, min(int(self.state.get("cursor", 0)), len(text)))
        start_ch = max(0, cur - avail + 1)
        x = region.x + text_width(prompt)
        self._cursor_cell = None
        drawn = False
        for i in range(start_ch, len(text)):
            style = sty.text("fg-0")
            if i == cur:
                style = style.with_attrs("reverse")
                self._cursor_cell = (x, y)
                drawn = True
            x += buf.write(x, y, text[i], style, max_width=max(0, region.x + region.w - x))
        if not drawn:
            buf.write(x, y, " ", sty.text("fg-0").with_attrs("reverse"),
                      max_width=max(1, region.x + region.w - x))
            self._cursor_cell = (x, y)

    def summary(self) -> str:
        return f"命令窗({len(self.state['lines'])} 行输出)"
