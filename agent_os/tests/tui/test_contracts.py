"""契约测试(docs/TUI-DOC.md §5.1/§6/§9):注册即校验,不合规拒注册。

覆盖三面:
- WidgetDef:缺 kind / v 越界 / action 缺 id·重复 / events 空项 / surface 越界
  / intent 不在活动 keymap 契约清单内 → 拒注册;
- ThemePack:色/copy/sizes 三族契约缺项 → 拒注册;内置两包全过;
- KeymapPack:必备 intent 缺绑定 → 拒注册;vim/emacs 两包契约全覆盖,
  且 §5.2/§5.3 设计表里的键位逐条在表。
"""

from __future__ import annotations

from typing import Any

from agent_os.host.tui.kernel.registry import WidgetRegistry
from agent_os.host.tui.kernel.widget import Widget
from agent_os.host.tui.kernel.widget_def import WidgetDef
from agent_os.host.tui.tui.keymap import (
    CONTEXTS,
    REQUIRED_INTENTS,
    KeymapRegistry,
    emacs_pack,
    parse_sequence,
    vim_pack,
)
from agent_os.host.tui.tui.theme import (
    ThemeRegistry,
    classic_pack,
    terminal_pack,
)


class _MiniDef(WidgetDef):
    """最小合规 def(测试桩;各用例覆盖声明面做反面例)。"""

    def __init__(self, **over: Any) -> None:
        self._over = over

    def get_kind(self) -> str:
        return self._over.get("kind", "mini")

    def get_v(self) -> int:
        return self._over.get("v", 1)

    def get_actions(self) -> list[dict[str, Any]]:
        return self._over.get("actions", [])

    def get_events(self) -> list[str]:
        return self._over.get("events", [])

    def get_surfaces(self) -> list[str]:
        return self._over.get("surfaces", ["card", "tab"])

    def get_intents(self) -> list[str]:
        return self._over.get("intents", [])

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return Widget(self, state)


# ---------------------------------------------------------------------------
# WidgetDef 注册校验(registry.py;对译 widget_registry.gd)
# ---------------------------------------------------------------------------

def test_widget_def_register_ok():
    reg = WidgetRegistry()
    assert reg.register(_MiniDef())
    assert reg.get_def("mini") is not None
    assert reg.create("mini", {}) is not None


def test_widget_def_missing_kind_rejected():
    assert not WidgetRegistry().register(_MiniDef(kind=""))


def test_widget_def_bad_v_rejected():
    assert not WidgetRegistry().register(_MiniDef(v=0))


def test_widget_def_action_without_id_rejected():
    assert not WidgetRegistry().register(_MiniDef(actions=[{"exec": "local"}]))


def test_widget_def_duplicate_action_id_rejected():
    assert not WidgetRegistry().register(
        _MiniDef(actions=[{"id": "a"}, {"id": "a"}]))


def test_widget_def_empty_event_rejected():
    assert not WidgetRegistry().register(_MiniDef(events=[""]))


def test_widget_def_surface_out_of_range_rejected():
    assert not WidgetRegistry().register(_MiniDef(surfaces=["holo"]))
    assert not WidgetRegistry().register(
        _MiniDef(actions=[{"id": "a", "surfaces": ["holo"]}]))


def test_unregistered_kind_refused_create():
    reg = WidgetRegistry()
    assert reg.create("ghost", {}) is None  # 未注册 kind 显式拒绝


def test_widget_def_intent_contract():
    """§5.1 新增面:声明的 intent 必须落在活动 keymap 契约清单内。"""
    reg = WidgetRegistry(required_intents=["move_up", "open"])
    assert reg.register(_MiniDef(intents=["move_up", "open"]))
    assert not reg.register(_MiniDef(kind="bad", intents=["teleport"]))
    # 不给契约清单 = 不校验(godot 原型语义)
    assert WidgetRegistry().register(_MiniDef(kind="any", intents=["teleport"]))


# ---------------------------------------------------------------------------
# ThemePack 契约(theme.py;对译 theme_registry.gd)
# ---------------------------------------------------------------------------

def test_builtin_themes_pass_contract():
    ThemeRegistry.reset()
    assert ThemeRegistry.register(classic_pack())
    assert ThemeRegistry.register(terminal_pack())
    assert ThemeRegistry.current() is not None


def test_theme_missing_color_rejected():
    pack = classic_pack()
    del pack.colors["bg-0"]
    ThemeRegistry.reset()
    assert not ThemeRegistry.register(pack)


def test_theme_missing_copy_rejected():
    pack = classic_pack()
    del pack.copy["doc.save"]
    assert ThemeRegistry.validate(pack) == ["copy:doc.save"]


def test_theme_missing_size_rejected():
    pack = terminal_pack()
    del pack.sizes["s3"]
    assert "size:s3" in ThemeRegistry.validate(pack)


def test_theme_terminal_palette_reference():
    """terminal 包配色参考 terminal.css(§6):磷光绿主文本必须绿系色号。"""
    pack = terminal_pack()
    # #33ff66 → ANSI 46(#00ff00 族);允许近似,但必须落在绿系(36-51/82-87 等)
    assert pack.colors["fg-0"] in range(34, 52) or pack.colors["fg-0"] in range(82, 88)


# ---------------------------------------------------------------------------
# KeymapPack 契约(keymap.py;§5.1/§5.2/§5.3)
# ---------------------------------------------------------------------------

def test_builtin_keymaps_cover_required_intents():
    """两 keymap 包必备 intent 全覆盖(bindings 或命令条别名)。"""
    KeymapRegistry.reset()
    assert KeymapRegistry.register(vim_pack())
    assert KeymapRegistry.register(emacs_pack())
    for pack in (vim_pack(), emacs_pack()):
        assert KeymapRegistry.validate(pack) == []
        covered = pack.intents()
        for intent in REQUIRED_INTENTS:
            assert intent in covered, f"{pack.id} 缺必备 intent: {intent}"


def test_keymap_missing_intent_rejected():
    pack = vim_pack()
    del pack.bindings["letter"]["a"]  # annotate 的唯一绑定
    assert "intent:annotate" in KeymapRegistry.validate(pack)


def test_keymap_missing_copy_label_rejected():
    pack = vim_pack()
    del pack.copy["move_up"]
    assert "copy:move_up" in KeymapRegistry.validate(pack)


def test_keymap_unknown_context_rejected():
    pack = vim_pack()
    pack.bindings["sidebar"] = {"x": "open"}
    assert any(m.startswith("context:sidebar") for m in KeymapRegistry.validate(pack))
    assert set(pack.bindings) - set(CONTEXTS)  # 桩确实越界


def test_vim_pack_binding_table():
    """§5.2 设计表逐条在案(T2 行:字符级移动/框选/坞双模态)。"""
    p = vim_pack()
    b = p.bindings
    assert b["global"][":"] == "command"
    assert b["global"]["?"] == "help"
    assert b["global"]["q"] == "quit"
    # 信纸:字符级光标 + v 框选(T2 裁决:v 给框选,版本条挪 <leader>v)
    assert b["letter"]["h"] == "move_left"
    assert b["letter"]["l"] == "move_right"
    assert b["letter"]["j"] == "move_down"
    assert b["letter"]["k"] == "move_up"
    assert b["letter"]["0"] == "line_start"
    assert b["letter"]["$"] == "line_end"
    assert b["letter"]["w"] == "word_forward"
    assert b["letter"]["b"] == "word_backward"
    assert b["letter"]["{"] == "para_backward"
    assert b["letter"]["}"] == "para_forward"
    assert b["letter"]["g g"] == "goto_top"
    assert b["letter"]["G"] == "goto_bottom"
    assert b["letter"]["C-d"] == "page_down"
    assert b["letter"]["C-u"] == "page_up"
    assert b["letter"]["v"] == "mark"
    assert b["letter"]["esc"] == "clear_mark"
    assert b["letter"]["a"] == "annotate"
    assert b["letter"]["\\ v"] == "versions"
    assert b["letter"]["r"] == "review"
    # 裸 g 已给 gg → generate 走 <leader>g(§5.4 裁决)
    assert b["letter"]["\\ g"] == "generate"
    # 坞(撰写控件,§5.2):共享面 C-Enter 存 / Enter 换行;INSERT Esc→NORMAL;
    # NORMAL 控件级 h/l/x,i 回 INSERT,q 弃,d 删
    assert b["dock"]["C-enter"] == "confirm"
    assert b["dock"]["enter"] == "newline"
    assert b["dock"]["backspace"] == "edit_back"
    assert b["dock:insert"]["esc"] == "mode_normal"
    assert b["dock:normal"]["i"] == "mode_insert"
    assert b["dock:normal"]["q"] == "cancel"
    assert b["dock:normal"]["d"] == "delete"
    assert b["dock:normal"]["x"] == "edit_delete"
    assert b["command"]["enter"] == "confirm"
    assert b["command"]["esc"] == "cancel"
    assert p.commands["w"] == "save"  # :w = 保存(盖章)命令条别名
    assert p.modal


def test_emacs_pack_binding_table():
    """§5.3 设计表逐条在案(T2:字符级移动全靠 Ctrl/Meta,C-Space 框选)。"""
    p = emacs_pack()
    b = p.bindings
    assert b["global"]["M-x"] == "command"
    assert b["global"]["C-h"] == "help"
    assert b["global"]["C-g"] == "quit"
    assert b["global"]["q"] == "quit"  # 只读 context q 同样退出(magit 传统)
    assert b["letter"]["C-f"] == "move_right"
    assert b["letter"]["C-b"] == "move_left"
    assert b["letter"]["C-n"] == "move_down"
    assert b["letter"]["C-p"] == "move_up"
    assert b["letter"]["C-a"] == "line_start"
    assert b["letter"]["C-e"] == "line_end"
    assert b["letter"]["M-f"] == "word_forward"
    assert b["letter"]["M-b"] == "word_backward"
    assert b["letter"]["M-}"] == "para_forward"
    assert b["letter"]["M-{"] == "para_backward"
    assert b["letter"]["C-space"] == "mark"
    assert b["letter"]["C-g"] == "clear_mark"
    assert b["letter"]["M-<"] == "goto_top"
    assert b["letter"]["M->"] == "goto_bottom"
    assert b["letter"]["C-v"] == "page_down"
    assert b["letter"]["M-v"] == "page_up"
    assert b["letter"]["C-c a"] == "annotate"
    assert b["letter"]["C-c v"] == "versions"
    assert b["letter"]["C-c r"] == "review"
    assert b["letter"]["C-c g"] == "generate"
    assert b["letter"]["C-x C-s"] == "save"
    # 坞(自插入):C-c C-c 存(org 惯例)/ C-g 弃 / C-c C-k 删;行内编辑
    assert b["dock"]["C-c C-c"] == "confirm"
    assert b["dock"]["C-g"] == "cancel"
    assert b["dock"]["C-c C-k"] == "delete"
    assert b["dock"]["C-a"] == "line_start"
    assert b["dock"]["C-d"] == "edit_delete"
    assert not p.modal


def test_key_sequence_parse_roundtrip():
    """键序记法规范化:C-x C-s / gg / <leader> 物理面。"""
    seq = parse_sequence("C-x C-s")
    assert seq[0].ctrl and seq[0].key == "x"
    assert seq[1].ctrl and seq[1].key == "s"
    seq = parse_sequence("g g")
    assert len(seq) == 2 and all(e.key == "g" for e in seq)


# ---------------------------------------------------------------------------
# 调试器契约(docs/TUI-DEBUG.md §5.3/§8;D1 三张 dbg-* context + dbg.* copy 族)
# ---------------------------------------------------------------------------

def test_debugger_contexts_registered():
    """dbg-cmd/dbg-trace/dbg-stack 三张 context 在契约清单(越界拒注册面)。"""
    for ctx in ("dbg-cmd", "dbg-trace", "dbg-stack"):
        assert ctx in CONTEXTS


def test_vim_pack_debugger_bindings():
    """vim 包 dbg-* 绑定抽查(§5.3 设计表;q 在命令窗是文本不是退出键)。"""
    b = vim_pack().bindings
    assert b["dbg-cmd"]["C-x o"] == "focus_next"
    assert b["dbg-cmd"]["enter"] == "confirm"
    assert b["dbg-cmd"]["up"] == "history_prev"
    assert b["dbg-cmd"]["down"] == "history_next"
    assert b["dbg-cmd"]["C-u"] == "edit_kill_line"
    assert b["dbg-cmd"]["C-w"] == "edit_kill_word"
    assert b["dbg-cmd"]["C-c"] == "interrupt"
    assert b["dbg-cmd"]["q"] == "self_insert"  # 与 global quit 冲突 → 显式绑回
    assert b["dbg-cmd"][":"] == "self_insert"
    assert b["dbg-cmd"]["?"] == "self_insert"
    assert b["dbg-trace"]["b"] == "bp_toggle"
    assert b["dbg-trace"]["enter"] == "repeat_last"
    assert b["dbg-trace"]["j"] == "move_down"
    assert b["dbg-trace"]["k"] == "move_up"
    assert b["dbg-stack"]["j"] == "move_down"
    assert b["dbg-stack"]["C-l"] == "redraw"


def test_emacs_pack_debugger_bindings():
    """emacs 包 dbg-* 绑定抽查(modeless;C-n/C-p 移动,q 绑回 self_insert)。"""
    b = emacs_pack().bindings
    assert b["dbg-cmd"]["C-x o"] == "focus_next"
    assert b["dbg-cmd"]["enter"] == "confirm"
    assert b["dbg-cmd"]["C-f"] == "move_right"
    assert b["dbg-cmd"]["C-b"] == "move_left"
    assert b["dbg-cmd"]["C-g"] == "cancel"
    assert b["dbg-cmd"]["q"] == "self_insert"
    assert b["dbg-trace"]["C-n"] == "move_down"
    assert b["dbg-trace"]["C-p"] == "move_up"
    assert b["dbg-trace"]["b"] == "bp_toggle"
    assert b["dbg-stack"]["C-n"] == "move_down"


def test_theme_debugger_copy_contract():
    """dbg.* copy 是文案契约:删一键 → validate 点名;占位符可 format 不吞。"""
    pack = classic_pack()
    del pack.copy["dbg.err.not_paused"]
    assert "copy:dbg.err.not_paused" in ThemeRegistry.validate(pack)
    # 停止行/终态行模板:占位符 {signal}/{frame}/{skill}/{step_suffix}/{num}
    # 必须可 format(主题可译文案,但占位符不可吞)
    for pack in (classic_pack(), terminal_pack()):
        pack.copy["dbg.stop.bp"].format(
            num=1, signal="pre:step", frame="f-x", skill="fib", step_suffix="")
        pack.copy["dbg.stop.step"].format(
            signal="pre:step", frame="f-x", skill="fib", step_suffix=", step 1")
        pack.copy["dbg.stop.pause"].format(frame="f-x", step_suffix="")
        pack.copy["dbg.end.run"].format(status="done")
        pack.copy["dbg.end.artifacts"].format(path="/tmp/x")
        pack.copy["dbg.echo.bp_set"].format(num=2, kind="tool_call", match="fs_*")
        pack.copy["dbg.echo.bp_del"].format(num=2)
        pack.copy["dbg.echo.resumed"].format(cmd="continue")
        pack.copy["dbg.err.no_bp"].format(num=9)
        pack.copy["dbg.err.no_frame"].format(num=9)
        pack.copy["dbg.err.ambiguous"].format(cmd="f", candidates="frame, finish")
        pack.copy["dbg.err.unknown"].format(cmd="zzz")
        pack.copy["dbg.err.usage"].format(usage="b <spec>")
        pack.copy["dbg.echo.patched"].format(patch='{"code": "x"}')
        pack.copy["dbg.echo.injected"].format(frame="f-x", skill="fib")
        pack.copy["dbg.echo.rerun"].format(sid="s-2", run_id="r-2")
        pack.copy["dbg.echo.detached"].format()
        pack.copy["dbg.echo.attached"].format(sid="s-2", run_id="r-2")
        pack.copy["dbg.err.set_args_pos"].format()
        pack.copy["dbg.empty.sessions"].format()
        # D5 收编:表头/栈帧行模板(占位符 {n}/{skill}/{fid}/{at_suffix} 不可吞)
        pack.copy["dbg.bps.header"].format()
        pack.copy["dbg.info_b.header"].format()
        pack.copy["dbg.sessions.header"].format()
        pack.copy["dbg.bt.row"].format(n=0, skill="fib", fid="f-x", at_suffix="")
        pack.copy["dbg.bt.row"].format(
            n=0, skill="fib", fid="f-x", at_suffix=" at pre:step step 1")
        pack.copy["dbg.frame.row"].format(n=1, skill="fib", fid="f-y")


def test_debugger_motion_names_registered():
    """调试器三具名(bp-hit/paused/run-done)在 MOTION_NAMES 契约清单,
    两内置包的 motion 表自动齐(缺名 → validate 点名)。"""
    from agent_os.host.tui.tui.theme import MOTION_NAMES

    for name in ("bp-hit", "paused", "run-done"):
        assert name in MOTION_NAMES
    for pack in (classic_pack(), terminal_pack()):
        for name in MOTION_NAMES:
            assert name in pack.motion  # 缺名回落 SUBTLE 之外,两包显式给档
    # 档位归主题:classic 的 paused/run-done 给足 FULL,terminal 全 SUBTLE(克制)
    from agent_os.host.tui.tui.theme import MOTION_FULL, MOTION_SUBTLE
    assert classic_pack().motion["paused"] == MOTION_FULL
    assert classic_pack().motion["run-done"] == MOTION_FULL
    assert terminal_pack().motion["paused"] == MOTION_SUBTLE
    assert terminal_pack().motion["run-done"] == MOTION_SUBTLE


def test_theme_switch_dbg_copy_observable():
    """换肤可观测(D5):terminal 包的 dbg 窗格标题/表头差异面;停止行/
    错误语式是 GDB 逐字契约,两包同值不换。"""
    c, t = classic_pack(), terminal_pack()
    for key in ("dbg.pane.stack", "dbg.pane.bps", "dbg.pane.trace",
                "dbg.pane.cmd", "dbg.bps.header", "dbg.info_b.header",
                "dbg.sessions.header"):
        assert c.copy[key] != t.copy[key], key
    for key in ("dbg.stop.bp", "dbg.err.not_paused", "dbg.end.run",
                "dbg.echo.bp_set", "dbg.help.header"):
        assert c.copy[key] == t.copy[key], key


def test_debugger_zero_theme_branch():
    """换肤零组件分支(§8):debugger 组件源码不出现主题 id 字符串,
    只消费语义 token/copy/具名动效。"""
    from pathlib import Path

    root = Path(__file__).parents[1] / "src" / "agent_os" / "host" / "tui" \
        / "apps" / "debugger"
    for py in sorted(root.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        assert "classic" not in text, py.name
        assert "terminal" not in text, py.name
