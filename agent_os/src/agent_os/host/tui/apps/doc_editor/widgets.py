"""信箱 index / 信纸 letter / 输入框 input 三个子 widget(docs/TUI-DOC.md §4)。

- 信箱:每封信一行(标题 / [注×N] / 版本号 / 日期);j/k 或 C-n/C-p 移动,
  Enter 打开(emit "open",widget 永不出海)。
- 信纸(T2 用户裁决:**注释精度到字符,光标到字符**):cursor = 全文字符
  偏移(0-based),mark = 选区锚点偏移(None = 无选区),选区 = [min, max)
  半开区间;渲染:光标格反色、选区 ``select`` 底色、已批注 span 下划线 +
  warn 色、span 末字符后紧跟 ``[注×N]``(双编码)。
- 输入框:批注坞的撰写控件(state {text, cursor} 可序列化);编辑 intent
  面(self_insert 由 app 直接喂字符,editing 键走 intent)。

游标/选区收在可序列化 state(§3 末段);版面缓存是渲染期瞬态,不进 state。
"""

from __future__ import annotations

import unicodedata
from typing import Any

from agent_os.host.tui.apps.doc_editor.model import classify_lines, paragraph_blocks
from agent_os.host.tui.kernel.widget import Widget
from agent_os.host.tui.kernel.widget_def import WidgetDef
from agent_os.host.tui.tui import sty
from agent_os.host.tui.tui.cells import (
    CellBuffer,
    Region,
    Style,
    char_width,
)

# ---------------------------------------------------------------------------
# 信箱(card surface 摘要行;§4 "信箱"行)
# ---------------------------------------------------------------------------


class IndexDef(WidgetDef):
    def get_kind(self) -> str:
        return "tui.index"

    def get_events(self) -> list[str]:
        return ["open"]

    def get_intents(self) -> list[str]:
        return ["move_up", "move_down", "open", "quit", "command", "help"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return IndexWidget(self, state)


class IndexWidget(Widget):
    """state: {entries: [{name,title,version,date,annotations}], cursor, scroll}"""

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.state.setdefault("entries", [])
        self.state.setdefault("cursor", 0)
        self.state.setdefault("scroll", 0)
        self._cursor_row: Region | None = None  # 渲染期瞬态(pulse_region 数据源)

    def set_entries(self, entries: list[dict[str, Any]]) -> None:
        """整表替换(app 层喂数据;游标回收到界内)。"""
        self.patch_state(lambda st: st.update(
            entries=entries, cursor=min(st.get("cursor", 0), max(0, len(entries) - 1))))

    def handle_intent(self, intent: str) -> bool:
        """只响应 intent(组件零按键分支红线);未识别的交回 app。"""
        entries = self.state["entries"]
        cur = int(self.state.get("cursor", 0))
        if intent == "move_down" and entries:
            self.mutate_state(lambda st: st.update(cursor=min(cur + 1, len(entries) - 1)))
            return True
        if intent == "move_up" and entries:
            self.mutate_state(lambda st: st.update(cursor=max(cur - 1, 0)))
            return True
        if intent == "open" and entries:
            name = str(entries[cur].get("name") or "")
            if name:
                self.emit_event("open", {"name": name})
            return True
        return False

    def render_into(self, buf: CellBuffer, region: Region) -> None:
        entries = self.state["entries"]
        if not entries:
            buf.write(region.x + 2, region.y, sty.copy("doc.list.empty"), sty.text("fg-2"))
            self._cursor_row = None
            return
        cur = int(self.state.get("cursor", 0))
        scroll = int(self.state.get("scroll", 0))
        scroll = min(scroll, cur)
        if cur >= scroll + region.h:
            scroll = cur - region.h + 1
        self.mutate_state(lambda st: st.update(scroll=scroll))
        for row in range(region.h):
            idx = scroll + row
            if idx >= len(entries):
                break
            self._render_row(buf, region, row, entries[idx], idx == cur)

    def _render_row(self, buf: CellBuffer, region: Region, row: int,
                    entry: dict[str, Any], current: bool) -> None:
        y = region.y + row
        style = sty.selected() if current else sty.text("fg-0")
        dim = sty.text("fg-1")
        buf.fill(region.x, y, region.w, 1, style if current else Style(None, None))
        if current:
            buf.write(region.x, y, "▸", sty.focus_ring())
            self._cursor_row = Region(region.x, y, region.w, 1)
        title_w = max(8, region.w - 30)
        buf.write(region.x + 2, y, str(entry.get("title") or ""), style, max_width=title_w)
        col = region.x + 2 + title_w + 1
        n = int(entry.get("annotations") or 0)
        if n > 0:
            col += buf.write(col, y, f"[注×{n}]", sty.text("warn", bold=True)) + 2
        date = str(entry.get("date") or "--")
        version = str(entry.get("version") or "工作稿")
        buf.write(region.x + region.w - len(date) - 1, y, date, dim)
        buf.write(region.x + region.w - len(date) - len(version) - 3, y, version, dim)

    def pulse_region(self) -> Region | None:
        return self._cursor_row

    def summary(self) -> str:
        return f"信箱({len(self.state['entries'])} 封)"


# ---------------------------------------------------------------------------
# 信纸(只读投影 + 字符级光标/框选;§1 原则 1:没有 INSERT 进正文的路径)
# ---------------------------------------------------------------------------


class LetterDef(WidgetDef):
    def get_kind(self) -> str:
        return "tui.letter"

    def get_events(self) -> list[str]:
        return ["close"]

    def get_intents(self) -> list[str]:
        return ["move_left", "move_right", "move_up", "move_down",
                "line_start", "line_end", "word_forward", "word_backward",
                "para_forward", "para_backward",
                "goto_top", "goto_bottom", "page_down", "page_up",
                "mark", "clear_mark", "annotate", "save", "generate",
                "review", "versions", "search", "quit", "command", "help"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return LetterWidget(self, state)


class _VLine:
    """一条视觉行:items = [(显示字符, 全文偏移|None, 是批注标记)];mark 项
    ([注×N] / 代码缩进)偏移为 None,不进选区/光标映射。
    归属区间:[line_off, vend);末段 vend = 行尾换行位(闭区间含该位)。
    ``cont`` = 折行后续段(格列 0 落段首而非行首)。"""

    __slots__ = ("cont", "items", "kind", "line_off", "vend", "vstart")

    def __init__(self, items: list[tuple[str, int | None, bool]], kind: str,
                 line_off: int, vstart: int, vend: int, cont: bool = False) -> None:
        self.items = items
        self.kind = kind
        self.line_off = line_off  # 源行起始(光标落在被剥修饰前缀上时归本行)
        self.vstart = vstart      # 本段首个实字符的全文偏移(空行 = 行偏移)
        self.vend = vend          # 段末的下一偏移(末段 = 行尾换行位)
        self.cont = cont


class LetterWidget(Widget):
    """state(全部可 JSON 序列化):name/title/version/text/spans/cursor/
    mark/goal_col/scroll。偏移以 ``text`` 全文为准(0-based;选区半开)。"""

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.state.setdefault("text", "")
        self.state.setdefault("spans", [])
        self.state.setdefault("cursor", 0)
        self.state.setdefault("mark", None)
        self.state.setdefault("goal_col", None)
        self.state.setdefault("scroll", 0)
        self._last_width = 76                 # 版面缓存键(渲染期瞬态)
        self._vlines: list[_VLine] = []
        self._cursor_region: Region | None = None
        self._cursor_cell: tuple[int, int] | None = None

    # ------------------------------------------------------------------
    # 派生面
    # ------------------------------------------------------------------

    def text(self) -> str:
        return str(self.state.get("text") or "")

    def selection(self) -> tuple[int, int] | None:
        """选区 [s, e) 半开;mark None 或 s == e → None。"""
        mark = self.state.get("mark")
        if mark is None:
            return None
        cur = int(self.state.get("cursor", 0))
        s, e = min(int(mark), cur), max(int(mark), cur)
        return (s, e) if s < e else None

    def clear_mark(self) -> None:
        self.mutate_state(lambda st: st.update(mark=None))

    def span_at(self, offset: int) -> dict[str, Any] | None:
        """光标下既有批注 span(start <= off < end;只认已解析的)。"""
        for sp in self.state["spans"]:
            if sp.get("start") is None:
                continue
            if int(sp["start"]) <= offset < int(sp["end"]):
                return sp
        return None

    def cursor_line_col(self) -> tuple[int, int]:
        """状态栏 行:列(1-based)。"""
        text = self.text()
        cur = max(0, min(int(self.state.get("cursor", 0)), len(text)))
        line = text.count("\n", 0, cur) + 1
        line_start = text.rfind("\n", 0, cur) + 1
        return line, cur - line_start + 1

    # ------------------------------------------------------------------
    # 移动(intent 面;组件零按键分支)
    # ------------------------------------------------------------------

    def handle_intent(self, intent: str, page_rows: int = 10) -> bool:
        text = self.text()
        if not text and intent not in ("mark",):
            return False
        cur = max(0, min(int(self.state.get("cursor", 0)), len(text)))
        new: int | None = None
        vertical = False
        if intent == "move_left":
            new = cur - 1
        elif intent == "move_right":
            new = cur + 1
        elif intent == "move_up":
            new = self._vertical(cur, -1)
            vertical = True
        elif intent == "move_down":
            new = self._vertical(cur, +1)
            vertical = True
        elif intent == "page_down":
            new = self._vertical(cur, +max(1, page_rows // 2))
            vertical = True
        elif intent == "page_up":
            new = self._vertical(cur, -max(1, page_rows // 2))
            vertical = True
        elif intent == "line_start":
            new = text.rfind("\n", 0, cur) + 1
        elif intent == "line_end":
            nl = text.find("\n", cur)
            new = nl if nl >= 0 else len(text)
        elif intent == "word_forward":
            new = self._word(cur, +1)
        elif intent == "word_backward":
            new = self._word(cur, -1)
        elif intent == "para_forward":
            new = self._para(cur, +1)
        elif intent == "para_backward":
            new = self._para(cur, -1)
        elif intent == "goto_top":
            new = 0
        elif intent == "goto_bottom":
            new = len(text)
        elif intent == "mark":
            mark = self.state.get("mark")
            self.mutate_state(lambda st: st.update(
                mark=None if mark is not None else cur))
            return True
        elif intent == "clear_mark":
            self.clear_mark()
            return True
        else:
            return False
        new = max(0, min(new, len(text)))
        goal = self.state.get("goal_col") if vertical else None
        if vertical and goal is None:
            goal = self._cursor_cell_col(cur)
        self.mutate_state(lambda st: st.update(cursor=new, goal_col=goal))
        return True

    @staticmethod
    def _is_word(ch: str) -> bool:
        return ch.isalnum() or ch == "_" or unicodedata.east_asian_width(ch) in ("W", "F")

    def _word(self, cur: int, direction: int) -> int:
        text = self.text()
        n = len(text)
        if direction > 0:
            i = cur
            while i < n and self._is_word(text[i]):
                i += 1
            while i < n and not self._is_word(text[i]):
                i += 1
            return i
        i = cur
        while i > 0 and not self._is_word(text[i - 1]):
            i -= 1
        while i > 0 and self._is_word(text[i - 1]):
            i -= 1
        return i

    def _para(self, cur: int, direction: int) -> int:
        blocks = paragraph_blocks(self.text())
        if not blocks:
            return 0
        # 所在段:最后一个段首 <= cur 的段(段首 = 首内容字符,行前缀归该段)
        cur_block = 0
        for i, (s, _e) in enumerate(blocks):
            if s <= cur:
                cur_block = i
        if direction > 0:
            if cur_block + 1 < len(blocks):
                return blocks[cur_block + 1][0]
            return len(self.text()) if cur >= blocks[-1][0] else blocks[-1][0]
        s, _e = blocks[cur_block]
        # vim {:先回当前段首,已在段首则回上一段首
        return blocks[cur_block - 1][0] if cur == s and cur_block > 0 else s

    def _vertical(self, cur: int, delta: int) -> int:
        """视觉行上下(记目标列 goal_col;列超行尾落行尾)。"""
        if not self._vlines:
            self._layout(self._last_width)
        idx = self._vline_of(cur)
        target = max(0, min(idx + delta, len(self._vlines) - 1))
        col = self.state.get("goal_col")
        if col is None:
            col = self._cursor_cell_col(cur)
        return self._offset_at_col(self._vlines[target], int(col))

    # ------------------------------------------------------------------
    # 版面(渲染期瞬态;偏移 ↔ 视觉行的唯一映射面)
    # ------------------------------------------------------------------

    def _layout(self, width: int) -> list[_VLine]:
        """全文 → 视觉行列表(贪心折行,宽字符按格;批注标记随 span 末字符)。
        分段区间:非末段 [vstart, vend) 半开;末段含行尾换行位(光标"行尾"
        落点)。空行 vstart == vend == 行偏移。"""
        text = self.text()
        markers: dict[int, int] = {}
        for sp in self.state["spans"]:
            if sp.get("end") is not None:
                end = int(sp["end"])
                markers[end] = markers.get(end, 0) + 1
        vlines: list[_VLine] = []
        for src in classify_lines(text):
            items: list[tuple[str, int | None, bool]] = []
            if src.kind == "code":
                items += [(" ", None, False), (" ", None, False)]
            for i, ch in enumerate(src.text):
                items.append((ch, src.disp_off + i, False))
                off = src.disp_off + i + 1
                if off in markers:
                    for mch in f"[注×{markers[off]}]":
                        items.append((mch, None, True))
            line_end = src.end_off
            if not any(off is not None for _c, off, _m in items):
                vlines.append(_VLine(items, src.kind if items else "blank",
                                     src.line_off, src.disp_off, line_end))
                continue
            cur_items: list[tuple[str, int | None, bool]] = []
            cur_w = 0
            seg_no = 0

            def flush(is_last: bool, src: Any = src, line_end: int = line_end) -> None:
                nonlocal cur_items, cur_w, seg_no
                real = [o for _c, o, _m in cur_items if o is not None]
                vstart = real[0] if real else src.disp_off
                vend = line_end if is_last else (real[-1] + 1 if real else src.disp_off)
                vlines.append(_VLine(cur_items, src.kind, src.line_off, vstart, vend,
                                     cont=seg_no > 0))
                cur_items = []
                cur_w = 0
                seg_no += 1

            for ch, off, is_mark in items:
                w = char_width(ch)
                if cur_w + w > width and cur_items:
                    flush(False)
                cur_items.append((ch, off, is_mark))
                cur_w += w
            flush(True)
        self._vlines = vlines
        self._last_width = width
        return vlines

    def _vline_of(self, offset: int) -> int:
        """偏移 → 视觉行序(行前缀/行尾换行位都归本行末段)。"""
        for i, vl in enumerate(self._vlines):
            if vl.line_off <= offset < vl.vend:
                return i
            if offset == vl.vend and i + 1 < len(self._vlines) \
                    and self._vlines[i + 1].line_off > offset:
                return i  # 行尾换行位:归本行(下一行起点更靠后)
        if self._vlines and offset >= self._vlines[-1].vend:
            return len(self._vlines) - 1
        return 0

    def _cursor_cell_col(self, offset: int) -> int:
        """偏移 → 所在视觉行内的格列(行尾 = 末字符后一格)。"""
        idx = self._vline_of(offset)
        vl = self._vlines[idx] if self._vlines else None
        if vl is None:
            return 0
        col = 0
        for ch, off, _m in vl.items:
            if off is not None and off >= offset:
                break
            col += char_width(ch)
        return col

    def _offset_at_col(self, vl: _VLine, col: int) -> int:
        """视觉行 + 格列 → 偏移(列超行尾 → 行尾偏移;格列 0:首段落行首
        ——含被剥修饰的前缀位,后续段落段首)。"""
        if col <= 0:
            return vl.vstart if vl.cont else vl.line_off
        acc = 0
        last_off = vl.vend
        for ch, off, _m in vl.items:
            w = char_width(ch)
            if acc + w > col:
                return off if off is not None else last_off
            acc += w
            if off is not None:
                last_off = off + 1
        return vl.vend

    # ------------------------------------------------------------------
    # 渲染(纯渲染:state → cell buffer;光标反色 / 选区底色 / span 下划线)
    # ------------------------------------------------------------------

    def render_into(self, buf: CellBuffer, region: Region) -> None:
        text = self.text()
        width = max(4, region.w)
        vlines = self._layout(width)
        cur = max(0, min(int(self.state.get("cursor", 0)), len(text)))
        sel = self.selection()
        span_ranges = [(int(sp["start"]), int(sp["end"]))
                       for sp in self.state["spans"] if sp.get("start") is not None]
        cursor_vl = self._vline_of(cur)
        scroll = int(self.state.get("scroll", 0))
        scroll = min(scroll, cursor_vl)
        if cursor_vl >= scroll + region.h:
            scroll = cursor_vl - region.h + 1
        scroll = max(0, scroll)
        self.mutate_state(lambda st: st.update(scroll=scroll, cursor=cur))
        self._cursor_region = None
        self._cursor_cell = None
        for row in range(region.h):
            vi = scroll + row
            if vi >= len(vlines):
                break
            self._render_vline(buf, region, region.y + row, vlines[vi],
                               cur, sel, span_ranges, vi == cursor_vl)

    def _render_vline(self, buf: CellBuffer, region: Region, y: int, vl: _VLine,
                      cursor: int, sel: tuple[int, int] | None,
                      spans: list[tuple[int, int]], is_cursor_line: bool) -> None:
        base = self._kind_style(vl.kind)
        x = region.x
        cursor_drawn = False
        for ch, off, is_mark in vl.items:
            # 批注标记 [注×N]:warn 加粗,不带下划线(下划线只属于 span 本体)
            style = Style(sty.c("warn"), base.bg, frozenset({"bold"})) if is_mark else base
            if off is not None:
                if any(s <= off < e for s, e in spans):
                    style = Style(sty.c("warn"), base.bg, base.attrs | frozenset({"underline"}))
                if sel is not None and sel[0] <= off < sel[1]:
                    style = Style(style.fg, sty.c("select"), style.attrs)
                if off == cursor:
                    style = style.with_attrs("reverse")
                    self._cursor_cell = (x, y)
                    cursor_drawn = True
            x += buf.write(x, y, ch, style, max_width=max(0, region.x + region.w - x))
        # 光标在行尾(换行位/空行/EOF):落在末字符后的空格;
        # 光标在被剥修饰的行前缀(如标题的 "# ")上:反色落在行首字符本体——
        # 不能在行首格写空白,否则吞掉第一个真字符(实锤:标题"信"被吃掉)
        if is_cursor_line and not cursor_drawn:
            first_real = next(((c, o) for c, o, _m in vl.items if o is not None), None)
            if first_real is not None and cursor < first_real[1]:
                buf.write(region.x, y, first_real[0], base.with_attrs("reverse"),
                          max_width=max(1, region.w))
                self._cursor_cell = (region.x, y)
            else:
                buf.write(x, y, " ", base.with_attrs("reverse"),
                          max_width=max(1, region.x + region.w - x))
                self._cursor_cell = (x, y)
        if is_cursor_line:
            self._cursor_region = Region(region.x, y, region.w, 1)

    @staticmethod
    def _kind_style(kind: str) -> Style:
        if kind == "heading":
            return sty.heading()
        if kind == "code":
            return sty.paper(dim=True)
        return sty.paper()

    def pulse_region(self) -> Region | None:
        return self._cursor_region

    def summary(self) -> str:
        line, col = self.cursor_line_col()
        sel = self.selection()
        mark = f"·选 {sel[1] - sel[0]} 字" if sel else ""
        return (f"{self.state.get('title', '')}({self.state.get('version', '')}"
                f"·{line}:{col}{mark})")


# ---------------------------------------------------------------------------
# 输入框(批注坞/命令条的撰写控件;§5.2 modal 只存在于这里,正文永远不进 INSERT)
# ---------------------------------------------------------------------------


class InputDef(WidgetDef):
    def get_kind(self) -> str:
        return "tui.input"

    def get_events(self) -> list[str]:
        return []

    def get_intents(self) -> list[str]:
        return ["edit_back", "edit_delete", "newline",
                "move_left", "move_right", "line_start", "line_end",
                "confirm", "cancel"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return InputWidget(self, state)


class InputWidget(Widget):
    """state: {text, cursor}(cursor = 字符序 0..len;self-insert 字符由 app
    直接调 insert_char——可打印键是文本输入不是绑定,§5.2 自插入面)。"""

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.state.setdefault("text", "")
        self.state.setdefault("cursor", 0)
        self._cursor_cell: tuple[int, int] | None = None

    def insert_char(self, ch: str) -> None:
        def _ins(st: dict[str, Any]) -> None:
            cur = max(0, min(int(st.get("cursor", 0)), len(st["text"])))
            st["text"] = st["text"][:cur] + ch + st["text"][cur:]
            st["cursor"] = cur + len(ch)
        self.mutate_state(_ins)

    def handle_intent(self, intent: str) -> bool:
        cur = max(0, min(int(self.state.get("cursor", 0)), len(self.state["text"])))
        text = self.state["text"]
        if intent == "edit_back" and cur > 0:
            self.mutate_state(lambda st: st.update(
                text=st["text"][: cur - 1] + st["text"][cur:], cursor=cur - 1))
            return True
        if intent == "edit_delete" and cur < len(text):
            self.mutate_state(lambda st: st.update(
                text=st["text"][:cur] + st["text"][cur + 1:]))
            return True
        if intent == "newline":
            self.mutate_state(lambda st: st.update(
                text=st["text"][:cur] + "\n" + st["text"][cur:], cursor=cur + 1))
            return True
        if intent == "move_left":
            self.mutate_state(lambda st: st.update(cursor=max(0, cur - 1)))
            return True
        if intent == "move_right":
            self.mutate_state(lambda st: st.update(cursor=min(len(st["text"]), cur + 1)))
            return True
        if intent == "line_start":
            self.mutate_state(lambda st: st.update(cursor=text.rfind("\n", 0, cur) + 1))
            return True
        if intent == "line_end":
            nl = text.find("\n", cur)
            self.mutate_state(lambda st: st.update(cursor=nl if nl >= 0 else len(st["text"])))
            return True
        return False

    def render_into(self, buf: CellBuffer, region: Region) -> None:
        """多行编辑区:折行绘制,光标格反色(行尾/空行落空白格)。"""
        text = str(self.state.get("text") or "")
        cur = max(0, min(int(self.state.get("cursor", 0)), len(text)))
        width = max(4, region.w)
        x, y = region.x, region.y
        drawn = False
        offset = 0
        for raw in text.split("\n"):
            for piece in _wrap_offsets(raw, width):
                for ch in piece:
                    style = sty.text("fg-0")
                    if offset == cur:
                        style = style.with_attrs("reverse")
                        self._cursor_cell = (x, y)
                        drawn = True
                    x += buf.write(x, y, ch, style, max_width=max(0, region.x + region.w - x))
                    offset += 1
                if offset == cur and not drawn:
                    buf.write(x, y, " ", sty.text("fg-0").with_attrs("reverse"),
                              max_width=max(1, region.x + region.w - x))
                    self._cursor_cell = (x, y)
                    drawn = True
                x = region.x
                y += 1
                if y >= region.y + region.h:
                    break
            if y >= region.y + region.h:
                break
            offset += 1  # 换行符位
            if offset == cur and not drawn and y < region.y + region.h:
                buf.write(x, y, " ", sty.text("fg-0").with_attrs("reverse"))
                self._cursor_cell = (x, y)
                drawn = True
        if not text and not drawn:
            buf.write(region.x, region.y, " ", sty.text("fg-0").with_attrs("reverse"))
            self._cursor_cell = (region.x, region.y)

    def summary(self) -> str:
        return f"输入({len(self.state.get('text') or '')} 字)"


def _wrap_offsets(line: str, width: int) -> list[str]:
    """按格宽折行(保空行;与 cells.wrap_text 同贪心,但空串 → [""])。"""
    if not line:
        return [""]
    out: list[str] = []
    cur: list[str] = []
    cur_w = 0
    for ch in line:
        w = char_width(ch)
        if cur_w + w > width and cur:
            out.append("".join(cur))
            cur = []
            cur_w = 0
        cur.append(ch)
        cur_w += w
    out.append("".join(cur))
    return out
