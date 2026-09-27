"""干预命令(D3,docs/TUI-DEBUG.md §5.1/§6):set args / inject。

demo 全流程复现 tests/web/test_debug_api.py::test_debug_full_flow 的
modify 锚点(``{"code":"result = 41\\nprint(result)"}`` → run 结果
``{"seq": [0, 1, 41]}``);错误语式(未暂停/停错点/非法 JSON)与 inject
留痕(source=injected,x messages 可见)逐键断言。
"""

from __future__ import annotations

from agent_os.host.tui.tui.keys import KeyEvent

W, H = 72, 20


def _type(app, text: str) -> None:
    for ch in text:
        app.feed_key(KeyEvent(ch))
    app.feed_key(KeyEvent("enter"))


def _out(app) -> list[str]:
    return app.cmd_widget().state["lines"]


# ---------------------------------------------------------------------------
# set args:demo 全流程复现 41(web 锚点同款 patch)
# ---------------------------------------------------------------------------

def test_demo_set_args_reproduces_41(demo_debugger):
    app, _e, _m, _f = demo_debugger
    _type(app, "run")
    _type(app, "b system.*")
    _type(app, "c")  # 停在 pre:tool.call(原 args code = "result = 0 + 1…")
    assert any("Breakpoint 2, pre:tool.call system.python.exec" in ln
               for ln in _out(app))
    # 干预:改本次调用 code → 提交即放行(回声明示),run 跑完
    _type(app, 'set args {"code":"result = 41\\nprint(result)"}')
    out = _out(app)
    assert any(ln.startswith('args patched: {"code": "result = 41')
               and "提交即放行" in ln for ln in out)
    assert "Run finished: done" in out
    # 结果复现 web 锚点:run.finished 与帧检视双双跟随
    fin = [s for s in app.source.signals() if s["name"] == "run.finished"]
    assert fin and fin[-1]["payload"]["result"] == {"seq": [0, 1, 41]}
    assert app.source.frame("f-fib001")["result"] == {"seq": [0, 1, 41]}
    # 干预不伪造历史:已发出的 pre:tool.call 行仍是原 args
    pre = [s for s in app.source.signals() if s["name"] == "pre:tool.call"]
    assert "result = 0 + 1" in pre[-1]["payload"]["args"]["code"]
    # 工具结果行带着改后的值(post:tool.call 是派生行,被重算)
    post = [s for s in app.source.signals() if s["name"] == "post:tool.call"]
    assert post[-1]["payload"]["result"]["result"] == 41


def test_demo_set_args_position_guard(demo_debugger):
    """停错点 → §6 语式;会话/run 状态不对 → 同款人话(本地拦,不推进)。"""
    app, _e, _m, _f = demo_debugger
    _type(app, 'set args {"n": 5}')
    assert "The run is not being debugged." in _out(app)
    _type(app, "run")  # 入口断点:停在 pre:step(非 pre:tool.call)
    _type(app, 'set args {"n": 5}')
    assert "Cannot set args: not paused at pre:tool.call." in _out(app)
    # 会话仍停在原处(本地预检,未出海推进)
    assert app.source.snapshot()["state"] == "paused"
    assert app.source.snapshot()["pause_point"]["signal"] == "pre:step"


def test_demo_set_args_bad_json(demo_debugger):
    app, _e, _m, _f = demo_debugger
    _type(app, "run")
    _type(app, "b system.*")
    _type(app, "c")
    _type(app, "set args {oops")  # 非法 JSON:本地拦
    assert any(ln.startswith("Usage: set args <json>(JSON 解析错")
               for ln in _out(app))
    _type(app, "set args [1]")  # 非对象:本地拦
    assert "Usage: set args <json>(须为对象)" in _out(app)
    _type(app, "set args")  # 缺参数
    assert "Usage: set args <json>" in _out(app)
    # 都没出海:会话仍停在 pre:tool.call
    assert app.source.snapshot()["pause_point"]["signal"] == "pre:tool.call"


def test_demo_set_args_tool_failure_keeps_answer(demo_debugger):
    """改后 code 执行失败 → post:tool.call ok=False;终答行不动(demo 简化,
    不模拟 brain 对工具报错的反应;docstring 见 model.py modify)。"""
    app, _e, _m, _f = demo_debugger
    _type(app, "run")
    _type(app, "b system.*")
    _type(app, "c")
    _type(app, 'set args {"code":"raise ValueError(\\"boom\\")"}')
    out = _out(app)
    assert any(ln.startswith("args patched:") for ln in out)
    assert "Run finished: done" in out
    post = [s for s in app.source.signals() if s["name"] == "post:tool.call"]
    assert post[-1]["payload"]["ok"] is False
    fin = [s for s in app.source.signals() if s["name"] == "run.finished"]
    assert fin[-1]["payload"]["result"] == {"seq": [0, 1, 1]}


# ---------------------------------------------------------------------------
# inject:回声带帧号 + source=injected 落帧上下文(x messages 可见)
# ---------------------------------------------------------------------------

def test_demo_inject_visible_in_x_messages(demo_debugger):
    app, _e, _m, _f = demo_debugger
    _type(app, "run")            # 停 F1 step1
    _type(app, "b system.*")     # 留下后续停点(inject 即放行后还会停)
    _type(app, "inject 调试补充说明")
    out = _out(app)
    assert any(ln.startswith("injected -> frame f-fib001 (fib)")
               and "注入即放行" in ln for ln in out)
    # 注入即放行:放行后命中 bp 2,再停 pre:tool.call
    assert any("Breakpoint 2, pre:tool.call" in ln for ln in out)
    # x messages:注入消息在暂停帧上下文可见
    _type(app, "x messages")
    assert any("[user] 调试补充说明" in ln for ln in _out(app))
    # 留痕语义与 web 一致:role=user, source=injected
    msgs = app.source.frame("f-fib001")["messages"]
    injected = [m for m in msgs if m.get("content") == "调试补充说明"]
    assert injected and injected[0]["role"] == "user"
    assert injected[0]["source"] == "injected"


def test_demo_inject_guards(demo_debugger):
    app, _e, _m, _f = demo_debugger
    _type(app, "inject hi")
    assert "The run is not being debugged." in _out(app)
    _type(app, "run")
    _type(app, "inject")  # 空文本 → usage
    assert "Usage: inject <text>" in _out(app)
    _type(app, "c")  # 无断点了(入口已删):直达终态
    _type(app, "inject hi")  # 终态后:非 paused
    assert "The run is not paused." in _out(app)
