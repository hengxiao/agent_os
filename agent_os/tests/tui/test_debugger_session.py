"""会话管理(D4,docs/TUI-DEBUG.md §5.1):rerun / detach / session。

demo 侧按内核语义同步模拟(rerun=以创建参数重开、入口断点重生;
detach=摘下放行 run 跑完);online 侧(假传输层/真 uvicorn)在
test_debugger_online.py / test_debugger_live.py。
"""

from __future__ import annotations

from agent_os.host.tui.tui.keys import KeyEvent


def _type(app, text: str) -> None:
    for ch in text:
        app.feed_key(KeyEvent(ch))
    app.feed_key(KeyEvent("enter"))


def _out(app) -> list[str]:
    return app.cmd_widget().state["lines"]


def test_demo_rerun_reopens_at_entry(demo_debugger):
    """rerun(§5.1 GDB 再敲一次 run):kill 后重开,入口断点重生,再停 step 1。"""
    app, _e, _m, _f = demo_debugger
    _type(app, "rerun")  # 从未 run:人话
    assert "The run is not being debugged." in _out(app)
    _type(app, "run")
    _type(app, "kill")
    _type(app, "y")
    assert "Run finished: aborted" in _out(app)
    _type(app, "rerun")
    out = _out(app)
    assert any(ln.startswith("rerun -> 新会话 s-demo-01 · run r-demo0001")
               for ln in out)
    # 新会话停在第一条 pre:step(入口断点随 origin 重生,编号重新从 1 起)
    assert out.count("Breakpoint 1, pre:step, frame f-fib001 (fib), step 1") == 2
    assert app.source.snapshot()["state"] == "paused"
    # 断点表也重置(demo 的入口断点在 _pause 自删,暂停时列表已空)
    assert len(app.source.snapshot()["breakpoints"]) == 0


def test_demo_detach_lets_run_finish(demo_debugger):
    """detach(GDB 逐字对应):摘下放行,run 继续跑完;控制命令随后诚实拒绝。"""
    app, _e, _m, _f = demo_debugger
    _type(app, "run")
    _type(app, "detach")
    out = _out(app)
    assert "detached(放行,run 继续跑完;会话已摘下)" in out
    assert "Run finished: done" in out
    assert app.source.snapshot()["state"] == "detached"
    _type(app, "c")
    assert "The run is not paused." in _out(app)
    _type(app, "q")  # 非 paused:无"不 detach"提示行
    assert "(退出不 detach;paused 会话保留给下次连接)" not in _out(app)
    assert app.state["quit"] is True


def test_demo_session_ledger_empty_and_switch_human(demo_debugger):
    """session(本机账本):demo 无账本 → 空态;切换是 live 面 → 诚实人话。"""
    app, _e, _m, _f = demo_debugger
    _type(app, "session")
    assert "(无已知会话——本机账本为空;run 过才会记)" in _out(app)
    _type(app, "info sessions")
    assert "(无已知会话——本机账本为空;run 过才会记)" in _out(app)
    _type(app, "session s-x")
    assert "Usage: session <SID>(切换是 live 会话面)" in _out(app)
