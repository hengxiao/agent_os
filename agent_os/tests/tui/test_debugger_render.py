"""调试器渲染快照(docs/TUI-DEBUG.md §7/§9;D1 三窗 + 命令窗,72×20 无头)。

断言行语言/gutter/暂停行/状态行按规格渲染;断点 gutter 的 ● 语义对译 web
debug-view.js:544(精确 kind+match 才亮——glob 断点不加 ●,诚实不装)。
命令逐键喂入的冒烟在 test_debugger_smoke.py。
"""

from __future__ import annotations

import json

from agent_os.host.tui.apps.debugger.app import build_app
from agent_os.host.tui.apps.debugger.model import OfflineDebugSource, _fib_script
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keys import KeyEvent

W, H = 72, 20


def _lines(buf: CellBuffer) -> list[str]:
    return [buf.line_text(y) for y in range(H)]


def _find(lines: list[str], *subs: str) -> str:
    for ln in lines:
        if all(s in ln for s in subs):
            return ln
    raise AssertionError(f"找不到含 {subs} 的行:\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# 初始 NO RUN 面(§7 布局:header + 左栏两窗 + 轨迹 + 命令窗 + 状态行)
# ---------------------------------------------------------------------------

def test_initial_layout_no_run(demo_debugger):
    _app, _engine, _motion, frame = demo_debugger
    buf = frame()
    lines = _lines(buf)
    # header:NO RUN 双编码(─ 符号 + 文本)
    assert "─ NO RUN" in lines[0]
    assert "demo.fib" in lines[0]
    # 四窗标题(文案契约 dbg.pane.*)
    text = buf.plain_text()
    for title in ("调用栈 (bt)", "断点 (info b)", "执行轨迹", "命令"):
        assert title in text
    # 断点表常驻表头 + 三窗空态占位
    assert "Num  Kind" in text
    assert "(帧栈为空)" in text
    assert "(无断点)" in text
    assert "(无信号" in text
    # 状态行:焦点窗 + 连接态点 + 帮助键(键名从活动 keymap 生成)
    status = lines[H - 1]
    assert "foc=cmd" in status
    assert "conn─ demo" in status
    assert "C-x o 切换" in status
    # 命令窗提示符(plain_text 去尾空格,按 "(adb)" 断)
    assert "(adb)" in text


# ---------------------------------------------------------------------------
# run → 入口断点首停(pdb 语义;停止行/暂停行/栈帧三通道一致)
# ---------------------------------------------------------------------------

def test_run_pauses_at_entry_step(demo_debugger):
    app, _engine, _motion, frame = demo_debugger
    app.run_command("run")
    buf = frame()
    lines = _lines(buf)
    assert "■ PAUSED" in lines[0]
    assert "#s-demo-01" in lines[0]
    # 调用栈:#0 = 栈顶,▶ 当前(暂停)帧
    assert "▶#0 fib f-fib001" in buf.plain_text()
    # 停止行(§6 文案契约;入口断点 num 记账后删除,不停留在断点表)
    out = app.cmd_widget().state["lines"]
    assert "Breakpoint 1, pre:step, frame f-fib001 (fib), step 1" in out
    assert "(无断点)" in buf.plain_text()  # 一次性入口断点首停即删
    # 轨迹:行号零填充 + ▶ 落在暂停行(call 行,row_for_signal 最近可见前行)
    row = _find(lines, "0001", "run started fib")
    assert "●" not in row
    assert "▶" in _find(lines, "0002", "call skill.fib")
    # 无断点 → gutter 无 ●
    assert not any("●" in ln for ln in lines)


# ---------------------------------------------------------------------------
# 精确名断点:gutter ● + ▶ 同落 tool 行(web gutter 语义对译)
# ---------------------------------------------------------------------------

def test_exact_match_bp_gutter_and_paused_row(demo_debugger):
    app, _engine, _motion, frame = demo_debugger
    app.run_command("run")
    app.run_command("b system.python.exec")
    app.run_command("c")
    out = app.cmd_widget().state["lines"]
    assert "Breakpoint 2 set: tool_call system.python.exec" in out
    assert "resumed (continue)" in out
    assert any(ln.startswith("Breakpoint 2, pre:tool.call system.python.exec")
               for ln in out)
    lines = _lines(frame())
    row = _find(lines, "tool system.python.exec")
    assert "●" in row  # gutter:精确 kind+match 命中(debug-view.js:544 同语义)
    assert "▶" in row  # 暂停行 = tool 行(sig 区间精确覆盖)
    # 断点表窗 + info b 都见该断点(子串断言:列宽窄,不钉整行)
    assert "tool_call" in _find(lines, "Num  Kind") or "tool_call" in \
        frame().plain_text()


# ---------------------------------------------------------------------------
# glob 断点(b fs_*):加了但不命中,gutter 诚实不亮,c 直达 run 终态
# ---------------------------------------------------------------------------

def test_glob_bp_sets_but_never_hits(demo_debugger):
    app, _engine, _motion, frame = demo_debugger
    app.run_command("run")
    app.run_command("b fs_*")
    out = app.cmd_widget().state["lines"]
    assert "Breakpoint 2 set: tool_call fs_*" in out
    # gutter:bp match=glob ≠ 行目标原名 → 不亮(与 web 一致,不装熟)
    assert not any("●" in ln for ln in _lines(frame()))
    app.run_command("c")
    out = app.cmd_widget().state["lines"]
    assert "Run finished: done" in out  # fs_* 不命中 system.python.exec,直达终态
    assert "✓ ENDED done" in _lines(frame())[0]


# ---------------------------------------------------------------------------
# gutter 的键盘等价物:trace 窗 b = bp_toggle(加/删同名断点)
# ---------------------------------------------------------------------------

def test_bp_toggle_keyboard_on_trace(demo_debugger):
    app, _engine, _motion, frame = demo_debugger
    app.run_command("run")
    app.feed_key(KeyEvent("x", ctrl=True))  # C-x o:cmd → trace
    app.feed_key(KeyEvent("o"))
    assert app.state["focus"] == "trace"
    app.feed_key(KeyEvent("b"))  # 光标在暂停行(call 行)→ skill_invoke 断点
    out = app.cmd_widget().state["lines"]
    assert "Breakpoint 2 set: skill_invoke demo.fib" in out
    assert any("●" in ln for ln in _lines(frame()))
    app.feed_key(KeyEvent("b"))  # 再按 = 删
    assert "Breakpoint 2 deleted." in app.cmd_widget().state["lines"]
    assert not any("●" in ln for ln in _lines(frame()))


# ---------------------------------------------------------------------------
# offline 源(§3 只读回放):轨迹/帧检视可用,控制命令回人话
# ---------------------------------------------------------------------------

def _make_run_dir(tmp_path):
    root = tmp_path / "r-off1"
    root.mkdir()
    (root / "meta.json").write_text(
        json.dumps({"run_id": "r-off1", "skill": "demo.fib"}), encoding="utf-8")
    (root / "trace.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in _fib_script()) + "\n",
        encoding="utf-8")
    (root / "checkpoint.json").write_text(json.dumps({"frames": [
        {"frame_id": "f-fib001", "skill": "demo.fib", "depth": 1,
         "status": "done", "input": {"n": 3}, "result": {"seq": [0, 1, 1]},
         "usage": {"steps": 3}, "context": {"messages": []}}]}), encoding="utf-8")
    (root / "result.json").write_text(json.dumps({"status": "done"}), encoding="utf-8")
    return root


def test_offline_source_readonly(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    _tree, app, _engine, _motion = build_app(
        OfflineDebugSource(run_dir), keymap_id="vim")

    def frame() -> CellBuffer:
        buf = CellBuffer(W, H)
        app.render_into(buf, Region(0, 0, W, H))
        return buf

    lines = _lines(frame())
    assert "ENDED" in lines[0]
    assert "conn─ offline" in lines[H - 1]
    # 只读面可用:轨迹回放 + bt
    assert "run started fib" in frame().plain_text()
    app.run_command("bt")
    assert any("f-fib001" in ln for ln in app.cmd_widget().state["lines"])
    # 控制面:一律 §3 人话(不做假功能)
    app.run_command("run")
    app.run_command("b step")
    out = app.cmd_widget().state["lines"]
    assert out.count("The run is not being debugged (offline replay).") == 2
