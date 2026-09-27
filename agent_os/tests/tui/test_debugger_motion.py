"""调试器具名动效(docs/TUI-DEBUG.md §8,D5):bp-hit / paused / run-done。

- ``paused`` = 暂停行脉冲(先打停止行后播;档位归主题,classic FULL /
  terminal SUBTLE),区域向 TraceWidget 惰性要(跟布局走)。
- ``run-done`` = 终态定格章(章文走 ``dbg.end.run`` copy 键;先管道后盖章)。
- ``bp-hit`` = 断点行短闪(先原地更新计数后播;SUBTLE 一亮一灭 / FULL 两回合)。
- reduced-motion 全局强制 instant:三具名一律不记帧。
"""

from __future__ import annotations

from agent_os.host.tui.apps.debugger.app import build_app
from agent_os.host.tui.apps.debugger.model import DemoDebugSource
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keys import KeyEvent
from agent_os.host.tui.tui.motion import PULSE_TICKS
from agent_os.host.tui.tui.theme import (
    MOTION_INSTANT,
    MOTION_SUBTLE,
    ThemeRegistry,
)

W, H = 72, 20


def _type(app, text: str) -> None:
    for ch in text:
        app.feed_key(KeyEvent(ch))
    app.feed_key(KeyEvent("enter"))


def _frame(app) -> CellBuffer:
    buf = CellBuffer(W, H)
    app.render_into(buf, Region(0, 0, W, H))
    return buf


def _reverse_cells(buf: CellBuffer) -> int:
    return sum(
        1 for row in buf.grid_snapshot()["rows"] for c in row
        if "reverse" in c["attrs"] and not c["cont"]
    )


def _drain(motion) -> None:
    while motion.busy():
        motion.tick()


def _ticks_until_idle(motion) -> int:
    n = 0
    while motion.busy():
        motion.tick()
        n += 1
    return n


# ---------------------------------------------------------------------------
# paused:停止行先上屏,暂停行脉冲后播(先管道后动效)
# ---------------------------------------------------------------------------

def test_paused_pulse_after_stop_line(demo_debugger):
    app, _engine, motion, _frame_fn = demo_debugger
    _type(app, "run")
    out = app.cmd_widget().state["lines"]
    assert "Breakpoint 1, pre:step, frame f-fib001 (fib), step 1" in out  # 先管道
    assert motion.busy()  # 后动效:paused 脉冲记上帧(classic FULL:3 回合)
    buf = _frame(app)
    regions = motion.active_regions()
    assert regions and regions[0][0] is app.trace_widget()
    # 脉冲"亮"期:暂停行整行反色(区域 = TraceWidget 渲染期记录的暂停行)
    assert _reverse_cells(buf) > 0
    _drain(motion)
    assert motion.active_regions() == []


def test_paused_level_from_theme(demo_debugger):
    """档位归主题:classic paused=FULL(3 回合),terminal=SUBTLE(2 回合)。"""
    app, _engine, motion, _frame_fn = demo_debugger
    assert ThemeRegistry.current().id == "classic"
    _type(app, "run")
    assert _ticks_until_idle(motion) == 3 * 2 * PULSE_TICKS
    ThemeRegistry.apply("terminal")
    _type(app, "c")  # 无用户断点:run 直达终态
    _drain(motion)  # 排空 run-done 章
    _type(app, "rerun")  # 新会话再停一次,量 terminal 档
    assert motion.busy()
    assert _ticks_until_idle(motion) == 2 * 2 * PULSE_TICKS


# ---------------------------------------------------------------------------
# run-done:终态定格章(章文走 copy 键;定格期状态行恒亮)
# ---------------------------------------------------------------------------

def test_run_done_stamp(demo_debugger):
    app, _engine, motion, _frame_fn = demo_debugger
    _type(app, "run")
    _type(app, "c")  # 直达终态
    assert "Run finished: done" in app.cmd_widget().state["lines"]  # 先管道
    assert motion.active_stamps() == ["[ Run finished: done ]"]  # 后盖章
    _drain(motion)
    assert motion.active_stamps() == []


# ---------------------------------------------------------------------------
# bp-hit:计数原地更新 + 断点行短闪(SUBTLE 一亮一灭)
# ---------------------------------------------------------------------------

def test_bp_hit_flash(demo_debugger):
    app, _engine, motion, _frame_fn = demo_debugger
    _type(app, "run")
    _drain(motion)  # 排空 paused 脉冲,隔离 bp-hit 断言面
    _type(app, "b system.*")
    bp = app.bps_widget().state["bps"][0]
    app._on_bp_hit({"breakpoint_id": bp["id"], "hits": 7})
    # 先管道:断点表与 gutter 计数原地更新
    assert app.bps_widget().state["bps"][0]["hits"] == 7
    assert app.trace_widget().state["breakpoints"][0]["hits"] == 7
    # 后动效:断点行短闪(SUBTLE 档 bp-hit = 1 回合)
    assert app.bps_widget().hit_num == bp["num"]
    assert motion.busy()
    buf = _frame(app)
    regions = motion.active_regions()
    assert regions and regions[0][0] is app.bps_widget()
    assert _reverse_cells(buf) > 0
    assert _ticks_until_idle(motion) == 1 * 2 * PULSE_TICKS


def test_bp_hit_full_level_two_rounds(demo_debugger):
    """档位归主题:bp-hit 提到 FULL → 两回合短闪。"""
    app, _engine, motion, _frame_fn = demo_debugger
    ThemeRegistry.current().motion["bp-hit"] = 0  # MOTION_FULL
    _type(app, "run")
    _drain(motion)
    _type(app, "b system.*")
    bp = app.bps_widget().state["bps"][0]
    app._on_bp_hit({"breakpoint_id": bp["id"], "hits": 1})
    assert _ticks_until_idle(motion) == 2 * 2 * PULSE_TICKS


# ---------------------------------------------------------------------------
# reduced-motion:全局强制 instant,三具名一律不记帧
# ---------------------------------------------------------------------------

def test_reduced_motion_forces_instant_debugger():
    _tree, app, _engine, motion = build_app(
        DemoDebugSource(), keymap_id="vim", reduced_motion=True)
    assert ThemeRegistry.reduced_motion
    _type(app, "run")
    assert not motion.busy()  # paused 被闸
    lines = app.cmd_widget().state["lines"]
    assert any("Breakpoint 1, pre:step" in ln for ln in lines)  # 管道照走
    _type(app, "b system.*")
    bp = app.bps_widget().state["bps"][0]
    app._on_bp_hit({"breakpoint_id": bp["id"], "hits": 3})
    assert not motion.busy()  # bp-hit 被闸
    assert app.bps_widget().state["bps"][0]["hits"] == 3  # 计数照更
    _type(app, "c")
    assert not motion.busy()  # 停在 bp 2:paused 仍被闸
    _type(app, "c")  # 直达终态
    assert motion.active_stamps() == []  # run-done 被闸
    assert "Run finished: done" in app.cmd_widget().state["lines"]


def test_theme_instant_level_blocks_play(demo_debugger):
    """主题档案给 INSTANT 档 = 该具名不播(档位归主题的另一极)。"""
    app, _engine, motion, _frame_fn = demo_debugger
    ThemeRegistry.current().motion["paused"] = MOTION_INSTANT
    _type(app, "run")
    assert not motion.busy()
    ThemeRegistry.current().motion["paused"] = MOTION_SUBTLE
    _type(app, "s")
    assert motion.busy()
