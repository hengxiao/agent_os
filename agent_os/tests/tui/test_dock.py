"""批注坞(T2 首个写面)无头测试:开坞/预填/模式切换/存删出海/只读源诚实。

出海走 DocSource(假 client);widget 永不出海(坞内编辑只是本地 state,
保存由 app 层调 source)。vim 包 modal:进入即 INSERT,Esc→NORMAL,i 回。
"""

from __future__ import annotations

from typing import Any

from agent_os.host.tui.apps.doc_editor.app import build_app
from agent_os.host.tui.apps.doc_editor.model import DemoDocSource, OnlineDocSource
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keys import KeyEvent

W, H = 72, 20
TEXT = "# 题\n\n第一段正文甲乙丙。\n\n第二段丁戊。\n"


class FakeClient:
    """统一信封假 client(端点契约 = web_platform/app.py annotations 面)。"""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str, str, str]] = []
        self.deleted: list[str] = []
        self.anns: list[dict[str, Any]] = []
        self.fail_save = False

    def list_docs(self) -> dict[str, Any]:
        return {"ok": True, "status": 200,
                "json": [{"name": "a.b", "title": "远信", "savedAt": 1}]}

    def read_doc(self, name: str) -> dict[str, Any]:
        return {"ok": True, "status": 200, "json": {
            "name": name, "text": TEXT, "meta": {"title": "远信"}, "versions": ["v001"]}}

    def read_annotations(self, name: str) -> dict[str, Any]:
        return {"ok": True, "status": 200, "json": list(self.anns)}

    def save_annotation(self, name: str, anchor: str, quote: str, content: str) -> dict[str, Any]:
        if self.fail_save:
            return {"ok": False, "status": 500, "error": "500: boom"}
        self.saved.append((name, anchor, quote, content))
        self.anns = [a for a in self.anns if a["anchor"] != anchor]
        self.anns.append({"anchor": anchor, "quote": quote, "content": content,
                          "status": "pending"})
        return {"ok": True, "status": 200, "json": {}}

    def delete_annotation(self, name: str, anchor: str) -> dict[str, Any]:
        self.deleted.append(anchor)
        self.anns = [a for a in self.anns if a["anchor"] != anchor]
        return {"ok": True, "status": 200, "json": {}}


def _frame(app) -> CellBuffer:
    buf = CellBuffer(W, H)
    app.render_into(buf, Region(0, 0, W, H))
    return buf


def _open_letter(app) -> None:
    app.feed_key(KeyEvent("enter"))


def _select(app, start: int, n: int) -> None:
    """光标移到 start,开 mark,右移 n 字(选区 [start, start+n))。"""
    lw = app.letter_widget()
    lw.mutate_state(lambda st: st.update(cursor=start))
    app.feed_key(KeyEvent("v"))
    for _ in range(n):
        app.feed_key(KeyEvent("l"))


def test_dock_open_with_selection_prefills_quote():
    fc = FakeClient()
    _tree, app, _engine, _motion = build_app(OnlineDocSource(fc), keymap_id="vim")
    _open_letter(app)
    s = TEXT.index("正文甲乙")
    _select(app, s, 4)
    app.feed_key(KeyEvent("a"))
    dock = app.state["dock"]
    assert dock["active"] and dock["quote"] == "正文甲乙"
    assert dock["anchor"] is None and dock["range"] == [s, s + 4]
    # 坞渲染:引文 + 状态章 + keymap 生成的操作 hint
    text = _frame(app).plain_text()
    assert "引文:「正文甲乙」" in text
    assert "[待处理]" in text
    assert "C-Enter" in text
    # vim modal:进入即 INSERT
    assert "INSERT" in _frame(app).line_text(H - 1)


def test_dock_save_sends_char_precise_anchor():
    fc = FakeClient()
    _tree, app, _engine, _motion = build_app(OnlineDocSource(fc), keymap_id="vim")
    _open_letter(app)
    s = TEXT.index("正文甲乙")
    _select(app, s, 4)
    app.feed_key(KeyEvent("a"))
    for ch in "改写这段":
        app.feed_key(KeyEvent(ch))
    app.feed_key(KeyEvent("enter", ctrl=True))  # C-Enter 存
    # 同行锚点:ec = 选区长度(quote_at 互逆约定;L3:C4 起长 4)
    assert fc.saved == [("a.b", "doc.md#L3:C4-L3:C4", "正文甲乙", "改写这段")]
    assert app.state["status"] == "批注已出海存讫"
    assert not app.state["dock"]["active"]
    # 重拉后 span 上屏(下划线 + [注×1])
    buf = _frame(app)
    assert "[注×1]" in buf.plain_text()
    underlined = [c for row in buf.grid_snapshot()["rows"] for c in row
                  if "underline" in c["attrs"]]
    assert "".join(c["ch"] for c in underlined) == "正文甲乙"


def test_dock_edit_existing_and_delete():
    """光标落在既有 span 内 → 同锚点重编(upsert);坞内可删。"""
    fc = FakeClient()
    from agent_os.host.tui.apps.doc_editor.model import anchor_from_range
    s, e = TEXT.index("第二段"), TEXT.index("第二段") + 3
    fc.anns.append({"anchor": anchor_from_range(TEXT, s, e), "quote": TEXT[s:e],
                    "content": "旧批注", "status": "pending"})
    _tree, app, _engine, _motion = build_app(OnlineDocSource(fc), keymap_id="vim")
    _open_letter(app)
    lw = app.letter_widget()
    lw.mutate_state(lambda st: st.update(cursor=s + 1))
    app.feed_key(KeyEvent("a"))
    dock = app.state["dock"]
    assert dock["active"] and dock["anchor"] is not None
    assert app.dock_widget().state["text"] == "旧批注"  # 预填旧内容
    # 删:vims NORMAL 下 d(Esc 回 NORMAL 再按 d)
    app.feed_key(KeyEvent("esc"))
    assert app.state["mode"] == "normal"
    app.feed_key(KeyEvent("d"))
    assert fc.deleted == [dock["anchor"]]
    assert app.state["status"] == "批注已删"
    assert not app.state["dock"]["active"]


def test_dock_vim_insert_normal_mode_switch():
    """vim 坞:INSERT 自插入;Esc→NORMAL 后可打印键不插入;i 回 INSERT。"""
    fc = FakeClient()
    _tree, app, _engine, _motion = build_app(OnlineDocSource(fc), keymap_id="vim")
    _open_letter(app)
    _select(app, TEXT.index("正文"), 2)
    app.feed_key(KeyEvent("a"))
    app.feed_key(KeyEvent("x"))
    assert app.dock_widget().state["text"] == "x"
    app.feed_key(KeyEvent("esc"))  # → NORMAL
    app.feed_key(KeyEvent("y"))    # NORMAL 下可打印键不插入
    assert app.dock_widget().state["text"] == "x"
    app.feed_key(KeyEvent("h"))    # 控件级左移
    assert app.dock_widget().state["cursor"] == 0
    app.feed_key(KeyEvent("i"))    # 回 INSERT
    app.feed_key(KeyEvent("y"))
    assert app.dock_widget().state["text"] == "yx"
    # Enter 换行(两包同)
    app.feed_key(KeyEvent("enter"))
    assert "\n" in app.dock_widget().state["text"]
    # q 弃(NORMAL 下;先回 NORMAL)
    app.feed_key(KeyEvent("esc"))
    app.feed_key(KeyEvent("q"))
    assert not app.state["dock"]["active"]


def test_dock_emacs_flow():
    """emacs 坞:自插入;C-c C-c 存 / C-g 弃;C-Space 框选。"""
    fc = FakeClient()
    _tree, app, _engine, _motion = build_app(OnlineDocSource(fc), keymap_id="emacs")
    _open_letter(app)
    lw = app.letter_widget()
    s = TEXT.index("丁戊")
    lw.mutate_state(lambda st: st.update(cursor=s))
    app.feed_key(KeyEvent(" ", ctrl=True))  # C-Space 开 mark
    app.feed_key(KeyEvent("f", ctrl=True))
    app.feed_key(KeyEvent("f", ctrl=True))
    assert lw.selection() == (s, s + 2)
    app.feed_key(KeyEvent("c", ctrl=True))
    app.feed_key(KeyEvent("a"))  # C-c a = annotate
    assert app.state["dock"]["active"]
    for ch in "注":
        app.feed_key(KeyEvent(ch))
    app.feed_key(KeyEvent("c", ctrl=True))
    app.feed_key(KeyEvent("c", ctrl=True))  # C-c C-c 存
    assert fc.saved and fc.saved[0][1].startswith("doc.md#")
    assert app.state["status"] == "批注已出海存讫"


def test_dock_save_failure_keeps_dock():
    fc = FakeClient()
    fc.fail_save = True
    _tree, app, _engine, _motion = build_app(OnlineDocSource(fc), keymap_id="vim")
    _open_letter(app)
    _select(app, 0, 2)
    app.feed_key(KeyEvent("a"))
    app.feed_key(KeyEvent("z"))
    app.feed_key(KeyEvent("enter", ctrl=True))
    assert "批注存失败" in app.state["status"]
    assert app.state["dock"]["active"]  # 失败不关坞,内容不丢
    assert app.dock_widget().state["text"] == "z"


def test_dock_too_long_rejected():
    fc = FakeClient()
    _tree, app, _engine, _motion = build_app(OnlineDocSource(fc), keymap_id="vim")
    _open_letter(app)
    _select(app, 0, 2)
    app.feed_key(KeyEvent("a"))
    app.dock_widget().set_state({"text": "长" * 501, "cursor": 501})
    app.feed_key(KeyEvent("enter", ctrl=True))
    assert "超长" in app.state["status"]
    assert not fc.saved


def test_annotate_without_selection_guides():
    fc = FakeClient()
    _tree, app, _engine, _motion = build_app(OnlineDocSource(fc), keymap_id="vim")
    _open_letter(app)
    app.feed_key(KeyEvent("a"))  # 无选区、光标不在 span 上
    assert "先框选" in app.state["status"]
    assert not app.state["dock"]["active"]


def test_annotate_readonly_source_honest():
    """demo/离线面只读:annotate 直接报"需在线服务",不开坞不做假写面。"""
    _tree, app, _engine, _motion = build_app(DemoDocSource(), keymap_id="vim")
    _open_letter(app)
    lw = app.letter_widget()
    lw.mutate_state(lambda st: st.update(cursor=1, mark=3))
    app.feed_key(KeyEvent("a"))
    assert "只读" in app.state["status"] and "在线" in app.state["status"]
    assert not app.state["dock"]["active"]


def test_vim_esc_clears_selection_and_emacs_cg_clears_mark():
    fc = FakeClient()
    _tree, app, _engine, _motion = build_app(OnlineDocSource(fc), keymap_id="vim")
    _open_letter(app)
    lw = app.letter_widget()
    lw.mutate_state(lambda st: st.update(mark=0))
    app.feed_key(KeyEvent("esc"))
    assert lw.state["mark"] is None
    # emacs:C-g 清 mark(letter context 覆盖 global 万能取消)
    _t2, app2, _e2, _m2 = build_app(OnlineDocSource(FakeClient()), keymap_id="emacs")
    _open_letter(app2)
    lw2 = app2.letter_widget()
    lw2.mutate_state(lambda st: st.update(mark=1, cursor=3))
    app2.feed_key(KeyEvent("g", ctrl=True))
    assert lw2.state["mark"] is None
    assert app2.state["screen"] == "letter"  # 清 mark 不退屏
