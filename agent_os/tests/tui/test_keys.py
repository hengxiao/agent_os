"""键解码 + keymap 引擎测试(docs/TUI-DOC.md §5.1)。

KeyDecoder:字节流 → KeyEvent(C-/M-/转义序列/Esc 超时裁决),纯状态机
无头直喂;KeymapEngine:前缀/和弦(gg、C-x C-s)、context 回落、超时、
hint/帮助从活动包生成。
"""

from __future__ import annotations

import time

from agent_os.host.tui.tui.keymap import (
    RES_INTENT,
    RES_PREFIX,
    RES_SELF_INSERT,
    RES_UNBOUND,
    KeymapEngine,
    emacs_pack,
    vim_pack,
)
from agent_os.host.tui.tui.keys import KeyDecoder, KeyEvent

# ---------------------------------------------------------------------------
# KeyDecoder
# ---------------------------------------------------------------------------

def test_decode_printable_ascii():
    d = KeyDecoder()
    evs = d.feed(b"ja:")
    assert [(e.key, e.ctrl, e.meta) for e in evs] == [("j", False, False), ("a", False, False), (":", False, False)]


def test_decode_ctrl_keys():
    d = KeyDecoder()
    evs = d.feed(b"\x04\x15\x0e\x10")  # C-d C-u C-n C-p
    assert [(e.key, e.ctrl) for e in evs] == [("d", True), ("u", True), ("n", True), ("p", True)]


def test_decode_enter_backspace_tab():
    d = KeyDecoder()
    assert d.feed(b"\r")[0] == KeyEvent("enter")
    assert d.feed(b"\x7f")[0] == KeyEvent("backspace")
    assert d.feed(b"\t")[0] == KeyEvent("tab")


def test_decode_arrow_and_function_keys():
    d = KeyDecoder()
    assert d.feed(b"\x1b[A")[0] == KeyEvent("up")
    assert d.feed(b"\x1b[B")[0] == KeyEvent("down")
    assert d.feed(b"\x1bOC")[0] == KeyEvent("right")  # SS3 形态
    assert d.feed(b"\x1b[5~")[0] == KeyEvent("pageup")
    assert d.feed(b"\x1b[24~")[0] == KeyEvent("f12")


def test_decode_meta_prefix():
    d = KeyDecoder()
    evs = d.feed(b"\x1bx")  # M-x
    assert evs == [KeyEvent("x", meta=True)]


def test_decode_bare_esc_after_timeout():
    """Esc 与转义序列歧义:裸 Esc 挂起,超时裁决为 esc(§5.1)。"""
    d = KeyDecoder()
    assert d.feed(b"\x1b") == []
    assert d.pending_escape()
    assert d.flush_escape() == KeyEvent("esc")


def test_decode_ctrl_space():
    """0x00 = C-Space/C-@(NUL;emacs mark 键,§5.3)。"""
    d = KeyDecoder()
    assert d.feed(b"\x00") == [KeyEvent(" ", ctrl=True)]
    assert d.feed(b"\x00")[0].chord() == "C-space"


def test_decode_ctrl_enter_csi_u():
    """C-Enter(modifyOtherKeys 形态 \\x1b[13;5u;坞内"存"键)。"""
    d = KeyDecoder()
    assert d.feed(b"\x1b[13;5u") == [KeyEvent("enter", ctrl=True)]
    assert d.feed(b"\x1b[13;5u")[0].chord() == "C-enter"


def test_decode_esc_sequence_not_confused():
    """Esc 后紧跟序列字节 → 转义序列,不是裸 Esc。"""
    d = KeyDecoder()
    evs = d.feed(b"\x1b[D")
    assert evs == [KeyEvent("left")]
    assert not d.pending_escape()


def test_decode_utf8_text():
    d = KeyDecoder()
    assert d.feed_text("信纸") == [KeyEvent("信"), KeyEvent("纸")]


# ---------------------------------------------------------------------------
# KeymapEngine(前缀/和弦 + 回落 + 超时)
# ---------------------------------------------------------------------------

def test_engine_simple_binding():
    e = KeymapEngine(vim_pack())
    assert e.resolve(KeyEvent("j"), "letter") == (RES_INTENT, "move_down")


def test_engine_chord_gg():
    e = KeymapEngine(vim_pack())
    assert e.resolve(KeyEvent("g"), "letter") == (RES_PREFIX, None)
    assert e.pending_display == "g"  # 挂起前缀回显(状态行)
    assert e.resolve(KeyEvent("g"), "letter") == (RES_INTENT, "goto_top")
    assert not e.pending()


def test_engine_chord_cx_cs():
    e = KeymapEngine(emacs_pack())
    assert e.resolve(KeyEvent("x", ctrl=True), "letter") == (RES_PREFIX, None)
    assert e.resolve(KeyEvent("s", ctrl=True), "letter") == (RES_INTENT, "save")


def test_engine_chord_cc_prefix_emacs():
    e = KeymapEngine(emacs_pack())
    assert e.resolve(KeyEvent("c", ctrl=True), "letter") == (RES_PREFIX, None)
    assert e.resolve(KeyEvent("a"), "letter") == (RES_INTENT, "annotate")


def test_engine_leader_g_vim():
    e = KeymapEngine(vim_pack())
    assert e.resolve(KeyEvent("\\"), "letter") == (RES_PREFIX, None)
    assert e.resolve(KeyEvent("g"), "letter") == (RES_INTENT, "generate")


def test_engine_context_fallback_to_global():
    """子级缺省向 global 回落(§5.1):letter 里按 ? 走 global 帮助。"""
    e = KeymapEngine(vim_pack())
    assert e.resolve(KeyEvent("?"), "letter") == (RES_INTENT, "help")
    assert e.resolve(KeyEvent("enter"), "index") == (RES_INTENT, "open")


def test_engine_chord_timeout_flush():
    e = KeymapEngine(vim_pack())
    e.resolve(KeyEvent("g"), "letter")
    e._pending_since = time.monotonic() - 2.0  # 假钟:超时
    assert e.feed_timeout() == (RES_UNBOUND, None)
    assert not e.pending()


def test_engine_self_insert_in_command_context():
    """input 族 context:未绑定可打印键 = 自插入(§5.2/§5.3 撰写控件)。"""
    e = KeymapEngine(vim_pack())
    res, payload = e.resolve(KeyEvent("s"), "command", "insert")
    assert res == RES_SELF_INSERT
    assert isinstance(payload, KeyEvent) and payload.key == "s"
    # 阅览屏无自插入面
    assert e.resolve(KeyEvent("s"), "letter")[0] == RES_UNBOUND


def test_engine_modal_mode_awareness():
    """modal 包模式面(§5.2 坞):INSERT 自插入 + Esc→NORMAL;NORMAL 下可打印
    不插入,控件级键走模式子表;i 回 INSERT。modeless 包忽略 mode。"""
    e = KeymapEngine(vim_pack())
    # INSERT:可打印自插入,Esc 回 NORMAL,C-Enter 存(共享面)
    assert e.resolve(KeyEvent("s"), "dock", "insert")[0] == RES_SELF_INSERT
    assert e.resolve(KeyEvent("esc"), "dock", "insert") == (RES_INTENT, "mode_normal")
    assert e.resolve(KeyEvent("enter", ctrl=True), "dock", "insert") == (RES_INTENT, "confirm")
    # NORMAL:可打印不插入;i → INSERT;q 弃;x 删字(控件级)
    assert e.resolve(KeyEvent("s"), "dock", "normal")[0] == RES_UNBOUND
    assert e.resolve(KeyEvent("i"), "dock", "normal") == (RES_INTENT, "mode_insert")
    assert e.resolve(KeyEvent("q"), "dock", "normal") == (RES_INTENT, "cancel")
    assert e.resolve(KeyEvent("x"), "dock", "normal") == (RES_INTENT, "edit_delete")
    assert e.resolve(KeyEvent("enter", ctrl=True), "dock", "normal") == (RES_INTENT, "confirm")
    # emacs 忽略 mode:坞内永远自插入;C-c C-c 存 / C-g 弃
    em = KeymapEngine(emacs_pack())
    assert em.resolve(KeyEvent("s"), "dock")[0] == RES_SELF_INSERT
    assert em.resolve(KeyEvent("c", ctrl=True), "dock") == (RES_PREFIX, None)
    assert em.resolve(KeyEvent("c", ctrl=True), "dock") == (RES_INTENT, "confirm")
    assert em.resolve(KeyEvent("g", ctrl=True), "dock") == (RES_INTENT, "cancel")


def test_engine_unbound_broken_chord():
    e = KeymapEngine(vim_pack())
    e.resolve(KeyEvent("g"), "letter")
    assert e.resolve(KeyEvent("z"), "letter") == (RES_UNBOUND, None)  # 整串作废


def test_hint_and_help_generated_from_pack():
    """hint 栏与帮助面板从活动 keymap 生成(§5.1;两包产出不同即证不硬编码)。"""
    vim = KeymapEngine(vim_pack())
    emacs = KeymapEngine(emacs_pack())
    intents = ["move_down", "annotate", "quit"]
    v_hint = vim.hint_summary("letter", intents)
    e_hint = emacs.hint_summary("letter", intents)
    assert "j" in v_hint and "a" in v_hint and "q" in v_hint
    assert "C-n" in e_hint and "C-c a" in e_hint and "C-g" in e_hint
    assert v_hint != e_hint
    # 帮助面板行覆盖当前 context + global
    keys = [k for k, _label in vim.help_lines("letter")]
    assert "gg" in keys and "G" in keys and ":" in keys
    # vim 的 save 无直接或弦,显示为命令条别名 :w
    assert vim.binding_for("save", "letter") == ":w"
    assert emacs.binding_for("save", "letter") == "C-x C-s"
