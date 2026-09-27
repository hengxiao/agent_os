"""无头冒烟(docs/TUI-DOC.md §9;T2 验收:两包键位各自可读可翻可框选)。

demo 数据装配 app → 喂合成键事件序列 → 渲染帧字符串断言;focus-pulse 后
缓冲区含反色段。不经终端(无 pty),CellBuffer 直接当屏幕。
字符级光标/坞的细粒度用例在 test_letter.py / test_dock.py。
"""

from __future__ import annotations

from agent_os.host.tui.apps.doc_editor.app import build_app
from agent_os.host.tui.apps.doc_editor.model import DemoDocSource
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keymap import KeymapRegistry
from agent_os.host.tui.tui.keys import KeyEvent

W, H = 72, 20


def _reverse_cells(buf: CellBuffer) -> int:
    return sum(
        1 for row in buf.grid_snapshot()["rows"] for c in row
        if "reverse" in c["attrs"] and not c["cont"]  # 宽字符光标占两格,只数首格
    )


def _type_text(app, text: str) -> None:
    for ch in text:
        app.feed_key(KeyEvent(ch))


# ---------------------------------------------------------------------------
# vim 包:j/k/Enter/G/gg/q + 框选(§10 验收序列的 T2 形)
# ---------------------------------------------------------------------------

def test_vim_read_flow(demo_app):
    app, _engine, motion, frame = demo_app
    buf = frame()
    # 信箱:两行各一封信,首行 ▸ 光标列 + [注×N] badge
    assert "信箱" in buf.line_text(0)
    assert buf.line_text(1).startswith("▸")
    assert "信件模型(示例)" in buf.line_text(1)
    assert "[注×2]" in buf.line_text(1)
    assert "终端宿主(示例)" in buf.line_text(2)
    # 状态栏:vim 模式指示 + keymap 生成的 hint(不硬编码)
    assert "NORMAL" in buf.line_text(H - 1)
    assert "j 下一行" in buf.line_text(H - 1)

    app.feed_key(KeyEvent("j"))
    assert app.index_widget().state["cursor"] == 1
    assert frame().line_text(2).startswith("▸")
    app.feed_key(KeyEvent("k"))
    assert app.index_widget().state["cursor"] == 0

    # Enter 打开 → 信纸只读投影 + focus-pulse
    app.feed_key(KeyEvent("enter"))
    assert app.state["screen"] == "letter"
    assert motion.busy()
    buf = frame()
    assert _reverse_cells(buf) > 0
    assert "信件模型(示例)(v001)" in buf.line_text(0)
    body = buf.plain_text()
    assert "信纸不可直改" in body
    assert "[注×1]" in body  # span 末字符后紧跟标记(字符精度)
    while motion.busy():
        motion.tick()
    # 脉冲灭期后:只剩光标格一格反色
    assert _reverse_cells(frame()) == 1

    # G/gg:文首/文末(字符偏移)
    lw = app.letter_widget()
    app.feed_key(KeyEvent("G"))
    assert lw.state["cursor"] == len(lw.text())
    app.feed_key(KeyEvent("g"))
    app.feed_key(KeyEvent("g"))
    assert lw.state["cursor"] == 0

    # q 退回信箱,再 q 退出
    app.feed_key(KeyEvent("q"))
    assert app.state["screen"] == "index"
    app.feed_key(KeyEvent("q"))
    assert app.state["quit"] is True


def test_vim_mark_and_status_line_position(demo_app):
    app, _engine, _motion, frame = demo_app
    app.feed_key(KeyEvent("enter"))
    lw = app.letter_widget()
    app.feed_key(KeyEvent("v"))
    for _ in range(3):
        app.feed_key(KeyEvent("l"))
    assert lw.selection() == (0, 3)
    line = frame().line_text(H - 1)
    assert "选3字" in line and "1:4" in line  # 行:列 + 选区宽度
    app.feed_key(KeyEvent("esc"))  # Esc 清选区
    assert lw.selection() is None


def test_vim_page_and_printable_guidance(demo_app):
    app, _engine, _motion, _frame = demo_app
    app.feed_key(KeyEvent("enter"))
    app.feed_key(KeyEvent("d", ctrl=True))  # C-d 半屏(视觉行)
    assert app.letter_widget().state["cursor"] > 0
    app.feed_key(KeyEvent("u", ctrl=True))
    assert app.letter_widget().state["cursor"] == 0
    # 可打印键 → 状态行一句人话引导(§1.5;copy 键 + 键名随包)
    app.feed_key(KeyEvent("x"))
    assert "信不可涂改" in app.state["status"]
    assert "a 批注" in app.state["status"]


def test_vim_command_bar(demo_app):
    app, _engine, _motion, frame = demo_app
    app.feed_key(KeyEvent(":"))
    assert app.state["command"]["active"]
    assert frame().line_text(H - 1).startswith(":")
    _type_text(app, "keymap")
    app.feed_key(KeyEvent("enter"))
    assert "keymap = vim" in app.state["status"]
    # :set keymap emacs → 切换即 hint 全换(§5.4)
    app.feed_key(KeyEvent(":"))
    _type_text(app, "set keymap emacs")
    app.feed_key(KeyEvent("enter"))
    assert KeymapRegistry.current().id == "emacs"
    assert "keymap → emacs" in app.state["status"]
    assert "NORMAL" not in frame().line_text(H - 1)  # emacs 包无模式指示
    # :q 退出
    app.feed_key(KeyEvent(":"))
    _type_text(app, "q")
    app.feed_key(KeyEvent("enter"))
    assert app.state["quit"] is True


def test_vim_help_panel_generated_from_keymap(demo_app):
    app, engine, _motion, frame = demo_app
    app.feed_key(KeyEvent("enter"))  # 信纸屏(letter context 行才进面板)
    app.feed_key(KeyEvent("?"))
    text = frame().plain_text()
    assert "gg" in text and "下一行" in text  # 面板行 = 活动包绑定
    assert "框选" in text  # T2:v = mark(裸 v 不再给版本条)
    # 版本条挪 <leader>v(面板行超屏高会截断,绑定点查引擎面)
    assert engine.binding_for("versions", "letter") == "<leader> v"
    assert engine.binding_for("generate", "letter") == "<leader> g"
    app.feed_key(KeyEvent("x"))  # 任意键关闭
    assert not app.state["help"]


def test_unsupported_intent_guidance(demo_app):
    """T2 未开放的 intent(generate 等)→ 状态行人话,不炸不直改(§1 红线)。"""
    app, _engine, _motion, _frame = demo_app
    app.feed_key(KeyEvent("enter"))
    app.feed_key(KeyEvent("\\"))
    app.feed_key(KeyEvent("g"))  # <leader>g = generate
    assert "尚未接线" in app.state["status"] or "里程碑" in app.state["status"]


# ---------------------------------------------------------------------------
# emacs 包:C-n/C-p/Enter/C-Space/M-x(§10 验收序列的 T2 形)
# ---------------------------------------------------------------------------

def test_emacs_read_flow():
    _tree, app, _engine, _motion = build_app(DemoDocSource(), keymap_id="emacs")

    def frame() -> CellBuffer:
        buf = CellBuffer(W, H)
        app.render_into(buf, Region(0, 0, W, H))
        return buf

    assert "NORMAL" not in frame().line_text(H - 1)  # modeless 无模式指示
    assert "C-n 下一行" in frame().line_text(H - 1)
    app.feed_key(KeyEvent("n", ctrl=True))
    assert app.index_widget().state["cursor"] == 1
    app.feed_key(KeyEvent("p", ctrl=True))
    assert app.index_widget().state["cursor"] == 0
    app.feed_key(KeyEvent("enter"))
    assert app.state["screen"] == "letter"
    lw = app.letter_widget()
    # M-> 文末 / M-< 文首(字符偏移)
    app.feed_key(KeyEvent(">", meta=True))
    assert lw.state["cursor"] == len(lw.text())
    app.feed_key(KeyEvent("<", meta=True))
    assert lw.state["cursor"] == 0
    # C-f/C-b 字符级
    app.feed_key(KeyEvent("f", ctrl=True))
    assert lw.state["cursor"] == 1
    app.feed_key(KeyEvent("b", ctrl=True))
    assert lw.state["cursor"] == 0
    # C-Space 框选 + 移动扩选
    app.feed_key(KeyEvent(" ", ctrl=True))
    app.feed_key(KeyEvent("f", ctrl=True))
    app.feed_key(KeyEvent("f", ctrl=True))
    assert lw.selection() == (0, 2)
    # C-g 清 mark(letter context 覆盖 global 万能取消)
    app.feed_key(KeyEvent("g", ctrl=True))
    assert lw.selection() is None
    assert app.state["screen"] == "letter"
    # C-v / M-v 翻页
    app.feed_key(KeyEvent("v", ctrl=True))
    assert lw.state["cursor"] > 0
    app.feed_key(KeyEvent("v", meta=True))
    assert lw.state["cursor"] == 0
    # q 退回信箱(只读 context 同样退出,magit 传统)
    app.feed_key(KeyEvent("q"))
    assert app.state["screen"] == "index"
    # M-x 命令条(emacs 面)
    app.feed_key(KeyEvent("x", meta=True))
    assert app.state["command"]["active"]
    _type_text(app, "set keymap vim")
    app.feed_key(KeyEvent("enter"))
    assert KeymapRegistry.current().id == "vim"
    assert "NORMAL" in frame().line_text(H - 1)


def test_emacs_printable_on_letter_is_guidance_not_insert():
    """modeless ≠ 可改正文:信纸上按可打印键同样只有引导(§1.1 红线)。"""
    _tree, app, _engine, _motion = build_app(DemoDocSource(), keymap_id="emacs")
    app.feed_key(KeyEvent("enter"))
    before = app.letter_widget().text()
    app.feed_key(KeyEvent("x"))
    assert app.letter_widget().text() == before  # 正文零改动
    assert "批注" in app.state["status"]


def test_letter_state_serializable(demo_app):
    """游标/选区收在可序列化 state(§3 末段)。"""
    import json

    app, _engine, _motion, _frame = demo_app
    app.feed_key(KeyEvent("enter"))
    app.feed_key(KeyEvent("v"))
    app.feed_key(KeyEvent("l"))
    json.dumps(app.state)
    json.dumps(app.letter_widget().state)
    json.dumps(app.index_widget().state)
    json.dumps(app.dock_widget().state)
