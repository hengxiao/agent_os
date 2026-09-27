"""信纸字符光标 / 框选 / 渲染层测试(T2 用户裁决:光标到字符,选区半开)。

移动语义逐 intent 锚定(左/右 ±1 字、行首/行尾、词、段、视觉行上下记目标列、
半屏翻页、文首/文末);渲染断言走 cell 快照(选区 select 底 / span 下划线 /
光标反色 / [注×N] 标记)。
"""

from __future__ import annotations

from agent_os.host.tui.apps.doc_editor.app import build_app
from agent_os.host.tui.apps.doc_editor.model import (
    DemoDocSource,
    anchor_from_range,
    resolve_spans,
)
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keys import KeyEvent

W, H = 72, 20
TEXT = "# 标题行\n\nfirst second 词三\n\n第三段收尾。\n"
#           0123 4567 ... 偏移基准见各用例


def _letter_app(text: str = TEXT, keymap: str = "vim"):
    """最小装配:直通 LetterWidget(绕过信箱,喂自定义全文)。"""
    _tree, app, _engine, _motion = build_app(DemoDocSource(), keymap_id=keymap)
    spans = resolve_spans(text, [])
    app.letter_widget().set_state({
        "name": "t.d", "title": "测", "version": "v001", "text": text,
        "spans": [sp.__dict__ for sp in spans], "cursor": 0, "mark": None,
        "goal_col": None, "scroll": 0,
    })
    app.mutate_state(lambda st: st.update(screen="letter"))
    return app, app.letter_widget()


def _frame(app) -> CellBuffer:
    buf = CellBuffer(W, H)
    app.render_into(buf, Region(0, 0, W, H))
    return buf


def _feed(app, ev: KeyEvent) -> None:
    app.feed_key(ev)


# ---------------------------------------------------------------------------
# 移动
# ---------------------------------------------------------------------------

def test_move_left_right_clamped():
    app, lw = _letter_app()
    _feed(app, KeyEvent("l"))
    _feed(app, KeyEvent("l"))
    assert lw.state["cursor"] == 1 + 1
    _feed(app, KeyEvent("h"))
    assert lw.state["cursor"] == 1
    lw.mutate_state(lambda st: st.update(cursor=0))
    _feed(app, KeyEvent("h"))
    assert lw.state["cursor"] == 0  # 左缘夹住
    lw.mutate_state(lambda st: st.update(cursor=len(TEXT)))
    _feed(app, KeyEvent("l"))
    assert lw.state["cursor"] == len(TEXT)  # 右缘夹住


def test_line_start_end():
    app, lw = _letter_app()
    cur = TEXT.index("second")
    lw.mutate_state(lambda st: st.update(cursor=cur))
    _feed(app, KeyEvent("0"))
    assert lw.state["cursor"] == TEXT.index("first")  # 行首(源行)
    _feed(app, KeyEvent("$"))
    assert lw.state["cursor"] == TEXT.index("词三") + 2  # 行尾(换行位)


def test_word_motions():
    app, lw = _letter_app()
    lw.mutate_state(lambda st: st.update(cursor=TEXT.index("first")))
    _feed(app, KeyEvent("w"))
    assert lw.state["cursor"] == TEXT.index("second")
    _feed(app, KeyEvent("w"))
    assert lw.state["cursor"] == TEXT.index("词三")
    _feed(app, KeyEvent("b"))
    assert lw.state["cursor"] == TEXT.index("second")


def test_para_motions():
    app, lw = _letter_app()
    _feed(app, KeyEvent("}"))
    assert lw.state["cursor"] == TEXT.index("first")  # 下一段首
    _feed(app, KeyEvent("}"))
    assert lw.state["cursor"] == TEXT.index("第三段")
    _feed(app, KeyEvent("{"))
    assert lw.state["cursor"] == TEXT.index("first")


def test_goto_top_bottom_and_page():
    app, lw = _letter_app()
    _feed(app, KeyEvent("G"))
    assert lw.state["cursor"] == len(TEXT)
    _feed(app, KeyEvent("g"))
    _feed(app, KeyEvent("g"))
    assert lw.state["cursor"] == 0
    _feed(app, KeyEvent("d", ctrl=True))  # C-d 半屏(视觉行)
    assert lw.state["cursor"] > 0
    _feed(app, KeyEvent("u", ctrl=True))
    assert lw.state["cursor"] == 0


def test_visual_line_up_down_keeps_goal_col():
    """j/k = 视觉行同列(记目标列;列超行尾落行尾,回长行恢复目标列)。"""
    text = "长行一甲乙丙丁戊\n\n短\n长行四庚辛壬癸子\n"
    app, lw = _letter_app(text)
    lw.mutate_state(lambda st: st.update(cursor=4))  # "乙"(格列 8,CJK 双宽)
    _frame(app)  # 建版面
    _feed(app, KeyEvent("j"))  # → 空行(落行偏移 9)
    assert lw.state["cursor"] == 9
    _feed(app, KeyEvent("j"))  # → "短"行尾(目标列 8 超行宽)
    assert lw.state["cursor"] == text.index("短") + 1
    _feed(app, KeyEvent("j"))  # → 长行四,恢复格列 8(= 4 字)
    assert lw.state["cursor"] == text.index("长行四") + 4


def test_up_down_visual_wrap_line():
    """长行折成两条视觉行:j 从第一视觉行到第二视觉行(同一源行内)。"""
    text = "甲" * 100
    app, lw = _letter_app(text)
    _frame(app)
    _feed(app, KeyEvent("j"))
    # CJK 双宽:72 格列 → 36 字/视觉行
    assert lw.state["cursor"] == 36


# ---------------------------------------------------------------------------
# 框选(选区 = [min(cursor,mark), max) 半开)
# ---------------------------------------------------------------------------

def test_mark_and_extend_selection():
    app, lw = _letter_app()
    lw.mutate_state(lambda st: st.update(cursor=TEXT.index("first")))
    _feed(app, KeyEvent("v"))
    for _ in range(5):
        _feed(app, KeyEvent("l"))
    s = TEXT.index("first")
    assert lw.selection() == (s, s + 5)
    assert TEXT[s:s + 5] == "first"
    _feed(app, KeyEvent("esc"))  # vim Esc 清选区
    assert lw.selection() is None


def test_mark_backward_selection():
    app, lw = _letter_app()
    lw.mutate_state(lambda st: st.update(cursor=TEXT.index("second") + 6))
    _feed(app, KeyEvent("v"))
    for _ in range(6):
        _feed(app, KeyEvent("h"))
    s, e = lw.selection()
    assert TEXT[s:e] == "second"


# ---------------------------------------------------------------------------
# 渲染(选区底色 / span 下划线 / 光标反色 / 行:列)
# ---------------------------------------------------------------------------

def _snapshot(buf: CellBuffer):
    return buf.grid_snapshot()["rows"]


def test_cursor_cell_reversed():
    app, _lw = _letter_app()
    buf = _frame(app)
    rows = _snapshot(buf)
    rev = [(x, y) for y, row in enumerate(rows) for x, c in enumerate(row)
           if "reverse" in c["attrs"] and y < H - 1]
    assert rev  # 光标格反色
    x, y = rev[0]
    # cursor=0 在被剥前缀 "#" 上 → 反色落行首真字符本体(不吞字,见
    # test_cursor_on_stripped_prefix_keeps_first_glyph)
    assert rows[y][x]["ch"] in (TEXT[0], "标", " ")


def test_selection_uses_select_token_bg():
    from agent_os.host.tui.tui.theme import ThemeRegistry

    app, lw = _letter_app()
    lw.mutate_state(lambda st: st.update(cursor=TEXT.index("first"), mark=TEXT.index("first") + 5))
    buf = _frame(app)
    select_color = ThemeRegistry.current().colors["select"]
    rows = _snapshot(buf)
    sel_chars = "".join(c["ch"] for row in rows for c in row if c["bg"] == select_color)
    assert "first" in sel_chars
    assert "3:" in buf.plain_text() or ":" in buf.line_text(H - 1)  # 行:列在状态栏


def test_annotation_span_underline_and_marker():
    text = TEXT
    s, e = text.index("second"), text.index("second") + 6
    rec = {"anchor": anchor_from_range(text, s, e), "quote": text[s:e],
           "content": "注", "status": "pending"}
    _tree, app, _engine, _motion = build_app(DemoDocSource(), keymap_id="vim")
    spans = resolve_spans(text, [rec])
    app.letter_widget().set_state({
        "name": "t.d", "title": "测", "version": "v001", "text": text,
        "spans": [sp.__dict__ for sp in spans], "cursor": 0, "mark": None,
        "goal_col": None, "scroll": 0,
    })
    app.mutate_state(lambda st: st.update(screen="letter"))
    buf = _frame(app)
    rows = _snapshot(buf)
    under = "".join(c["ch"] for row in rows for c in row if "underline" in c["attrs"])
    assert "second" in under
    assert "[注×1]" in buf.plain_text()  # 标记紧跟 span 末字符


def test_cursor_line_col_in_status_bar():
    app, lw = _letter_app()
    lw.mutate_state(lambda st: st.update(cursor=TEXT.index("second")))
    _feed(app, KeyEvent("x"))  # 清状态消息让位……先清 status
    app.mutate_state(lambda st: st.update(status=""))
    line = _frame(app).line_text(H - 1)
    assert "3:7" in line  # second 在第 3 行第 7 列(1-based)


def test_paragraph_body_has_no_background_fill():
    """T2.1 用户裁决:段落不铺背景(纸底会淹没反色光标);正文区任何字符
    不得铺纸底色(paper-0),正文文字 bg 一律为 None。"""
    from agent_os.host.tui.tui.theme import ThemeRegistry
    app, _lw = _letter_app()
    buf = _frame(app)
    paper0 = ThemeRegistry.current().colors["paper-0"]
    for row in buf.grid_snapshot()["rows"][1:6]:  # 信纸正文区(0 行是顶栏)
        for cell in row:
            assert cell["bg"] != paper0, f"正文区出现纸底: {cell}"
    # 正文文字(如 "first" 的 f)无底色;标题栏(深色 heading 条)不在此裁决面
    f_cell = next(c for c in buf.grid_snapshot()["rows"][3] if c["ch"] == "f")
    assert f_cell["bg"] is None


def test_cursor_on_stripped_prefix_keeps_first_glyph():
    """光标在被剥修饰前缀(标题的 `# `)上:反色落行首字符本体,
    不得写空白吞掉首字(实锤:标题'标'被光标格吃掉)。"""
    app, _lw = _letter_app()  # cursor=0 → 在 "#" 上
    buf = _frame(app)
    row1 = buf.grid_snapshot()["rows"][1]
    assert row1[0]["ch"] == "标" and "reverse" in row1[0]["attrs"]
