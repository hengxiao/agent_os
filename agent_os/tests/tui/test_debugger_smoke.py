"""调试器无头冒烟(docs/TUI-DEBUG.md §9 验收序列;逐键喂入,不绕过键面)。

序列照规格:run → b <spec> → c → s + 裸 Enter×3 → bt/frame/info b → kill
两段确认 → q。命令一律经 feed_key 逐字键入(命令窗是第一界面;焦点默认
cmd)。规格 §9 的 ``b fs_*`` 与 demo 锚点工具名不符(tests/web/
test_debug_api.py:21-24 唯一工具是 system.python.exec),冒烟改用
``b system.*``;``b fs_*`` 的诚实不命中案例在 test_debugger_render.py。
"""

from __future__ import annotations

from agent_os.host.tui.apps.debugger.app import build_app
from agent_os.host.tui.apps.debugger.model import DemoDebugSource
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keys import KeyEvent

W, H = 72, 20


def _type(app, text: str) -> None:
    """逐键键入 + Enter 提交(text="" = 裸 Enter)。"""
    for ch in text:
        app.feed_key(KeyEvent(ch))
    app.feed_key(KeyEvent("enter"))


def _out(app) -> list[str]:
    return app.cmd_widget().state["lines"]


# ---------------------------------------------------------------------------
# 主序列(vim 包;§9 冒烟的 D1 形)
# ---------------------------------------------------------------------------

def test_smoke_full_session_vim(demo_debugger):
    app, _engine, _motion, frame = demo_debugger
    # 未开会话先 c:诚实人话(不炸)
    _type(app, "c")
    assert "The run is not being debugged." in _out(app)
    # run:入口断点首停(pdb 语义)
    _type(app, "run")
    assert "Breakpoint 1, pre:step, frame f-fib001 (fib), step 1" in _out(app)
    assert "■ PAUSED" in frame().line_text(0)
    # b system.*:fnmatch 命中 demo 锚点唯一工具 system.python.exec
    _type(app, "b system.*")
    assert "Breakpoint 2 set: tool_call system.*" in _out(app)
    # c:停在 pre:tool.call
    _type(app, "c")
    out = _out(app)
    assert "resumed (continue)" in out
    assert any("Breakpoint 2, pre:tool.call system.python.exec" in ln for ln in out)
    # bt / frame:栈帧检视(#0 = 栈顶;暂停帧带 at 定位)
    _type(app, "bt")
    assert any("#0" in ln and "f-fib001" in ln and "at pre:tool.call step 2" in ln
               for ln in _out(app))
    _type(app, "frame 0")
    assert any("#0" in ln and "f-fib001" in ln for ln in _out(app))
    # info b:hits 计数(断点 2 命中 1 次)
    _type(app, "info b")
    assert any("system.*" in ln and ln.rstrip().endswith("1") for ln in _out(app))
    assert app.source.snapshot()["breakpoints"][0]["hits"] == 1
    # 诚实人话(enable 后端缺口)+ set args(D3 已落地):停在 pre:tool.call,
    # patch 合入本次调用并提交即放行(无 code 键 → 工具结果不变,run 跑完)
    _type(app, "enable 1")
    assert "Not supported: delete and re-add." in _out(app)
    _type(app, 'set args {"n": 5}')
    assert any(ln.startswith('args patched: {"n": 5}') for ln in _out(app))
    assert "Run finished: done" in _out(app)
    # s + 裸 Enter×3:run 已终态,步进与重复一律 not_paused(GDB 同款白名单)
    _type(app, "s")
    assert "The run is not paused." in _out(app)
    _type(app, "")
    _type(app, "")
    _type(app, "")
    out = _out(app)
    assert out.count("Run finished: done") == 1
    assert out.count("The run is not paused.") >= 2
    assert "✓ ENDED done" in frame().line_text(0)
    # q:退出(paused 已终,quit 事件)
    _type(app, "q")
    assert app.state["quit"] is True


# ---------------------------------------------------------------------------
# kill 两段确认(逐字学 GDB;n 静默回提示符,y 落地 Stop verdict)
# ---------------------------------------------------------------------------

def test_smoke_kill_two_strike(demo_debugger):
    app, _engine, _motion, frame = demo_debugger
    _type(app, "run")
    _type(app, "kill")
    assert "Kill the run being debugged? (y or n)" in _out(app)
    assert app.state["kill_armed"] is True
    _type(app, "n")  # 第一击后答 n:静默回提示符,会话仍 paused
    assert app.state["kill_armed"] is False
    assert "■ PAUSED" in frame().line_text(0)
    assert not any("aborted" in ln for ln in _out(app))
    _type(app, "kill")
    _type(app, "y")
    out = _out(app)
    assert "resumed (stop)" in out
    assert "Run finished: aborted" in out
    assert "⊘ ENDED aborted" in frame().line_text(0)
    _type(app, "q")
    assert app.state["quit"] is True


# ---------------------------------------------------------------------------
# 步进/选帧:s 步入子帧 → bt 两帧 → frame 1 改检视上下文 → finish 步出 → p
# ---------------------------------------------------------------------------

def test_smoke_step_frames_finish(demo_debugger):
    app, _engine, _motion, _frame = demo_debugger
    _type(app, "run")
    _type(app, "s")  # 步入 F2(base case)
    assert "Step finished, pre:step, frame f-fib002 (fib), step 1" in _out(app)
    _type(app, "bt")
    out = _out(app)
    assert any("#0" in ln and "f-fib002" in ln for ln in out)
    assert any("#1" in ln and "f-fib001" in ln for ln in out)
    _type(app, "frame 1")  # 选帧即改检视上下文(GDB)
    assert app.state["selected_frame"] == 1
    _type(app, "info args")  # 选中帧(F1)的调用参数
    assert any('"n": 3' in ln for ln in _out(app))
    _type(app, "frame 0")
    _type(app, "finish")  # 步出 F2 → 停在 pre:frame.pop
    assert any(ln.startswith("Step finished, pre:frame.pop, frame f-fib002")
               for ln in _out(app))
    _type(app, "p")  # 暂停点 payload 全量(客户端取值零裁决)
    assert any('"seq"' in ln for ln in _out(app))


# ---------------------------------------------------------------------------
# 裸 Enter 不重复危险命令(REPEATABLE 白名单外:run/kill/set/inject…)
# ---------------------------------------------------------------------------

def test_smoke_bare_enter_never_repeats_dangerous(demo_debugger):
    app, _engine, _motion, _frame = demo_debugger
    _type(app, "run")
    before = len(_out(app))
    _type(app, "")  # last = run(不在白名单)→ 无回声无动作
    assert len(_out(app)) == before
    assert app.source.snapshot()["state"] == "paused"


# ---------------------------------------------------------------------------
# emacs 包:modeless 同样可跑;↑ 历史;C-u 清行;trace 窗 q 退出(magit 传统)
# ---------------------------------------------------------------------------

def test_smoke_emacs_pack():
    _tree, app, _engine, _motion = build_app(DemoDebugSource(), keymap_id="emacs")

    def frame() -> CellBuffer:
        buf = CellBuffer(W, H)
        app.render_into(buf, Region(0, 0, W, H))
        return buf

    _type(app, "run")
    assert "Breakpoint 1, pre:step, frame f-fib001 (fib), step 1" in _out(app)
    assert "■ PAUSED" in frame().line_text(0)
    # ↑ 历史召回上一命令;C-u 清行(readline unix-line-discard)
    app.feed_key(KeyEvent("up"))
    assert app.cmd_widget().state["input"] == "run"
    app.feed_key(KeyEvent("u", ctrl=True))
    assert app.cmd_widget().state["input"] == ""
    # C-x o 切到 trace;modeless 下 q 仍退出(emacs 包 global q = quit)
    app.feed_key(KeyEvent("x", ctrl=True))
    app.feed_key(KeyEvent("o"))
    assert app.state["focus"] == "trace"
    app.feed_key(KeyEvent("q"))
    assert app.state["quit"] is True
