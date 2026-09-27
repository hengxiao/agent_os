"""OnlineDebugSource + live 回流(docs/TUI-DEBUG.md §3/§4;D2)。

不经真服务:FakeClient(统一信封脚本化)+ FakeSse(线程不跑,事件经
feed_event 注入)——queue drain/断线回落/错误翻译全在无头下断言。
live 端到端(真 uvicorn + 真 SSE 线程)在 test_debugger_live.py。
"""

from __future__ import annotations

import pytest

from agent_os.host.tui.apps.debugger.app import build_app
from agent_os.host.tui.apps.debugger.model import OnlineDebugSource, _fib_script
from agent_os.host.tui.tui.cells import CellBuffer, Region

W, H = 72, 20


class FakeSse:
    """SseClient 形 stub:不跑线程;回调赋值即接线证明(线程只入队的纪律面
    由 test_thread_callbacks_only_enqueue 钉死)。"""

    def __init__(self) -> None:
        self.event_received = None
        self.connection_changed = None
        self.started: tuple[str, str] | None = None
        self.stopped = False

    def start_stream(self, base: str, path: str = "/api/stream") -> None:
        self.started = (base, path)
        if self.connection_changed is not None:
            self.connection_changed(False)  # proto 语义:先未连上

    def stop_stream(self) -> None:
        self.stopped = True

    def run_forever(self) -> None:
        pass


class FakeClient:
    """AgentOsClient 形 stub:脚本化统一信封 {ok,status,json|error}。"""

    base_url = "http://127.0.0.1:8391"

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.snap_doc: dict = {"session_id": "s-1", "run_id": "r-1",
                               "state": "running", "pause_point": None,
                               "breakpoints": [], "frame_stack": [],
                               "rerunnable": True}
        self.open_resp: dict = {"ok": True, "status": 200,
                                "json": {"session_id": "s-1", "run_id": "r-1"}}
        self.command_resp: dict = {"ok": True, "status": 200, "json": {"ok": True}}
        self.modify_resp: dict = {"ok": True, "status": 200, "json": {"ok": True}}
        self.inject_resp: dict = {"ok": True, "status": 200,
                                  "json": {"ok": True, "frame_id": "f-fib001"}}
        self.rerun_resp: dict = {"ok": True, "status": 200,
                                 "json": {"session_id": "s-2", "run_id": "r-2"}}
        #: 非 None 时 snapshot 直返回该信封(404 detach 后场景)
        self.snapshot_resp: dict | None = None
        self.skills_resp: dict = {"ok": True, "status": 200,
                                  "json": [{"name": "demo.fib"}]}
        self.run_status = "done"

    # -- 信封面(与 AgentOsClient 方法同签名) --
    def list_skills(self):
        return self.skills_resp

    def debug_open_session(self, skill, run_input, breakpoints,
                           replay_run_id=None, until_step=None):
        self.calls.append(("open", skill, run_input, list(breakpoints),
                           replay_run_id, until_step))
        return self.open_resp

    def debug_snapshot(self, sid):
        self.calls.append(("snapshot", sid))
        if self.snapshot_resp is not None:
            return self.snapshot_resp
        return {"ok": True, "status": 200, "json": dict(self.snap_doc)}

    def debug_close(self, sid):
        self.calls.append(("close", sid))
        return {"ok": True, "status": 200, "json": {"ok": True}}

    def debug_rerun(self, sid):
        self.calls.append(("rerun", sid))
        return self.rerun_resp

    def debug_add_breakpoint(self, sid, kind, match="*"):
        self.calls.append(("add_bp", sid, kind, match))
        return {"ok": True, "status": 200,
                "json": {"id": "bp-new", "kind": kind, "match": match,
                         "enabled": True, "hits": 0}}

    def debug_remove_breakpoint(self, sid, bp_id):
        self.calls.append(("del_bp", sid, bp_id))
        return {"ok": True, "status": 200, "json": {"ok": True}}

    def debug_command(self, sid, cmd):
        self.calls.append(("cmd", sid, cmd))
        return self.command_resp

    def debug_frame(self, sid, fid):
        self.calls.append(("frame", sid, fid))
        return {"ok": True, "status": 200, "json": {"frame_id": fid, "messages": [],
                                                    "working": {}, "usage": {}}}

    def debug_modify(self, sid, patch):
        self.calls.append(("modify", sid, dict(patch)))
        return self.modify_resp

    def debug_inject(self, sid, text, frame_id=None):
        self.calls.append(("inject", sid, text, frame_id))
        return self.inject_resp

    def run_signals(self, run_id):
        return {"ok": True, "status": 200, "json": _fib_script()[:5]}

    def run_detail(self, run_id):
        return {"ok": True, "status": 200, "json": {"status": self.run_status}}


def _source(cli: FakeClient | None = None) -> tuple[OnlineDebugSource, FakeClient]:
    cli = cli or FakeClient()
    return OnlineDebugSource(client=cli, sse_factory=FakeSse), cli


def _paused_doc(pp: dict | None = None, bps: list | None = None) -> dict:
    return {"session_id": "s-1", "run_id": "r-1", "state": "paused",
            "pause_point": pp if pp is not None else {
                "signal": "pre:step", "run_id": "r-1", "frame_id": "f-fib001",
                "depth": 1, "step": 1, "skill": "demo.fib",
                "payload": {"step": 1, "depth": 1, "skill": "demo.fib"},
                "breakpoint_ids": ["bp-a"], "reason": "breakpoint"},
            "breakpoints": bps if bps is not None else [
                {"id": "bp-a", "kind": "step", "match": "*", "enabled": True,
                 "hits": 1}],
            "frame_stack": [{"frame_id": "f-fib001", "skill": "demo.fib",
                             "depth": 1}],
            "rerunnable": True}


# ---------------------------------------------------------------------------
# 会话开启/挂载/回放 + 入口断点 + fail-fast
# ---------------------------------------------------------------------------

def test_open_live_prepends_entry_breakpoint_and_starts_stream():
    src, cli = _source()
    out = src.open_live("demo.fib", {"n": 3}, [("tool_call", "system.*")])
    assert out == {"session_id": "s-1", "run_id": "r-1"}
    # 入口断点(CLI debug.py:348 pdb 语义):第一个注册,run 启动前
    assert cli.calls[0] == ("open", "demo.fib", {"n": 3},
                            [("step", "*"), ("tool_call", "system.*")], None, None)
    # SSE 流接调试路径(sse.py path 泛化),无 /platform 前缀
    assert src._sse.started == ("http://127.0.0.1:8391",
                                "/api/debug/sessions/s-1/stream")
    assert src.conn == "online" and src.writable and not src.sync_control


def test_attach_existing_session():
    src, cli = _source()
    cli.snap_doc = _paused_doc()
    out = src.attach("s-1")
    assert out == {"session_id": "s-1", "run_id": "r-1"}
    assert not src.ended
    assert src._sse.started is not None


def test_attach_detached_session_marks_ended():
    src, cli = _source()
    cli.snap_doc = {**_paused_doc(), "state": "detached", "pause_point": None,
                    "frame_stack": []}
    src.attach("s-1")
    assert src.ended


def test_attach_unknown_session_human_words():
    src, cli = _source()
    cli.snap_doc = {}
    cli_resp = {"ok": False, "status": 404, "error": "404: 找不到调试会话: s-x"}
    cli.debug_snapshot = lambda sid: cli_resp  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="404: 找不到调试会话"):
        src.attach("s-x")


def test_open_replay_until_passthrough():
    src, cli = _source()
    src.open_replay("r-old", 3, [])
    assert cli.calls[0][4] == "r-old" and cli.calls[0][5] == 3
    assert cli.calls[0][3] == []  # until 由服务端注册;不补客户端入口断点
    src2, cli2 = _source()
    src2.open_replay("r-old", None, [])
    assert cli2.calls[0][3] == [("step", "*")]  # 无 --until → 客户端补入口断点


def test_ping_fail_fast_words():
    src, cli = _source()
    assert src.ping() is None
    cli.skills_resp = {"ok": False, "status": 0, "error": "HTTP 发起失败: refused"}
    assert "refused" in (src.ping() or "")


# ---------------------------------------------------------------------------
# 错误翻译(§3/§6:404/400/409/200+failed/连接层 → 人话 RuntimeError)
# ---------------------------------------------------------------------------

def test_error_translation_matrix():
    src, cli = _source()
    src.open_live("demo.fib", {}, [])
    # 200 + failed(run 校验失败,§3.3 同归类)
    cli.open_resp = {"ok": True, "status": 200,
                     "json": {"status": "failed", "error": "技能不存在: ghost"}}
    with pytest.raises(RuntimeError, match="技能不存在: ghost"):
        src.open_live("ghost", {}, [])
    # 409 状态冲突
    cli.command_resp = {"ok": False, "status": 409, "error": "409: 会话不在 paused 态"}
    with pytest.raises(RuntimeError, match="409: 会话不在 paused 态"):
        src.command("continue")
    # 连接层失败(status 0)
    cli.command_resp = {"ok": False, "status": 0, "error": "HTTP 发起失败: boom"}
    with pytest.raises(RuntimeError, match="连不上服务"):
        src.command("continue")


def test_live_until_honest_refusal():
    """REST 断点端点无 until 字段(DebugBreakpointBody 只有 kind/match)。"""
    src, _cli = _source()
    src.open_live("demo.fib", {}, [])
    with pytest.raises(RuntimeError, match="until 步数断点在 live 会话不可用"):
        src.add_breakpoint("step", "*", until=3)


# ---------------------------------------------------------------------------
# 断点界面编号(num 是客户端显示号;REST 只有 bp_id)
# ---------------------------------------------------------------------------

def test_breakpoint_num_reconcile():
    src, cli = _source()
    cli.snap_doc = _paused_doc(bps=[
        {"id": "bp-a", "kind": "step", "match": "*", "enabled": True, "hits": 1},
        {"id": "bp-b", "kind": "tool_call", "match": "system.*", "enabled": True,
         "hits": 0}])
    src.open_live("demo.fib", {}, [])
    snap = src.snapshot()
    assert [bp["num"] for bp in snap["breakpoints"]] == [1, 2]
    assert src.snapshot()["breakpoints"][0]["num"] == 1  # 映射稳定
    bp = src.add_breakpoint("tool_call", "fs_*")
    assert bp["num"] == 3
    assert src.remove_breakpoint(2) is True
    assert ("del_bp", "s-1", "bp-b") in cli.calls
    assert src.remove_breakpoint(99) is False  # 未知 num 诚实 False


def test_end_status_backfill_and_cache():
    src, cli = _source()
    cli.snap_doc = {**_paused_doc(), "state": "detached", "pause_point": None,
                    "frame_stack": []}
    src.open_live("demo.fib", {}, [])
    snap = src.snapshot()
    assert snap["end_status"] == "done"  # run detail 回填
    cli.run_status = "aborted"
    assert src.snapshot()["end_status"] == "done"  # 缓存,不随服务端变


# ---------------------------------------------------------------------------
# SSE queue → app drain(§4:state/bp_hit/paused/resumed/run_end/connection)
# ---------------------------------------------------------------------------

def _live_app(cli: FakeClient | None = None):
    src, cli = _source(cli)
    cli.snap_doc = _paused_doc()
    src.open_live("demo.fib", {"n": 3}, [])
    _tree, app, _engine, _motion = build_app(src, keymap_id="vim")

    def frame() -> CellBuffer:
        buf = CellBuffer(W, H)
        app.render_into(buf, Region(0, 0, W, H))
        return buf

    return src, cli, app, frame


def test_thread_callbacks_only_enqueue():
    """§4 纪律: SSE 回调的唯一动作是入队(drain 前 source/app 零变化)。"""
    src, _cli, app, _frame = _live_app()
    base = src._events.qsize()  # start_stream 的首次"未连上"已在队里
    before_lines = list(app.cmd_widget().state["lines"])
    before_bps = [dict(bp) for bp in app.bps_widget().state["bps"]]
    # 直接调 SSE 回调(等价线程发射):只入队,不碰任何 state
    src._sse.event_received("paused", {"session_id": "s-1"})
    src._sse.connection_changed(True)
    assert app.cmd_widget().state["lines"] == before_lines
    assert app.bps_widget().state["bps"] == before_bps
    assert src._events.qsize() == base + 2


def test_paused_event_prints_stop_line_once_and_acks_entry_bp():
    src, cli, app, frame = _live_app()
    src.feed_event("connection", {"ok": True})
    app.tick(now=0.1)
    assert app.state["conn"] == "online"
    src.feed_event("paused", {"session_id": "s-1",
                              "pause_point": dict(cli.snap_doc["pause_point"])})
    app.tick(now=0.2)
    lines = app.cmd_widget().state["lines"]
    assert any(ln.startswith("Breakpoint 1, pre:step, frame f-fib001 (fib), step 1")
               for ln in lines)
    assert ("del_bp", "s-1", "bp-a") in cli.calls  # 入口断点首停后删
    assert "■ PAUSED" in frame().line_text(0)
    assert "▶" in frame().plain_text()  # ▶ 定位(先管道后动效)
    # 同一暂停点再来(轮询路径/补发)不重打
    app._poll_transitions()
    assert len([ln for ln in app.cmd_widget().state["lines"]
                if ln.startswith("Breakpoint 1")]) == 1


def test_bp_hit_updates_in_place():
    src, _cli, app, _frame = _live_app()
    app.refresh()
    src.feed_event("bp_hit", {"session_id": "s-1", "breakpoint_id": "bp-a",
                              "kind": "step", "match": "*", "hits": 3})
    app.tick(now=0.1)
    assert app.bps_widget().state["bps"][0]["hits"] == 3
    assert app.trace_widget().state["breakpoints"][0]["hits"] == 3


def test_connection_first_false_silent_then_message_after_loss():
    src, _cli, app, frame = _live_app()
    app.tick(now=0.1)  # start_stream 的首次 False(尚未连上)→ 静默 poll 态
    assert app.state["conn"] == "poll"
    assert not app.cmd_widget().state["lines"]
    src.feed_event("connection", {"ok": True})
    app.tick(now=0.2)
    assert app.state["conn"] == "online"
    src.feed_event("connection", {"ok": False})  # 真断线 → 人话
    app.tick(now=0.3)
    assert app.state["conn"] == "poll"
    assert "调试实时连接中断,回退轮询" in app.cmd_widget().state["lines"]
    assert "conn○ poll" in frame().line_text(H - 1)


def test_fallback_poll_discovers_pause_and_end():
    """断线回落(§4;web pollTick 对译):sse_down 时快照走 2s 轮询,
    paused/detached 转换在轮询里发现。"""
    src, cli, app, _frame = _live_app()
    cli.snap_doc = {**_paused_doc(), "state": "running", "pause_point": None}
    src.feed_event("connection", {"ok": True})
    app.tick(now=0.1)
    src.feed_event("connection", {"ok": False})  # 断线 → 回落
    app.tick(now=0.2)
    assert src.sse_down
    # 轮询发现新暂停(服务端推进,TUI 无事件)
    cli.snap_doc = _paused_doc(pp={"signal": "pre:tool.call", "run_id": "r-1",
                                   "frame_id": "f-fib001", "depth": 1, "step": 2,
                                   "tool": "system.python.exec", "skill": "demo.fib",
                                   "payload": {"step": 2, "tool": "system.python.exec"},
                                   "breakpoint_ids": [], "reason": "step:over"})
    app.tick(now=1.0)  # 未到 2s 不轮询
    assert not any("Step finished" in ln for ln in app.cmd_widget().state["lines"])
    app.tick(now=3.0)  # 到点 → 轮询发现
    lines = app.cmd_widget().state["lines"]
    assert any(ln.startswith("Step finished, pre:tool.call system.python.exec")
               for ln in lines)
    # 轮询发现 detached → 终态行(run detail 回填 status)
    cli.snap_doc = {**cli.snap_doc, "state": "detached", "pause_point": None,
                    "frame_stack": []}
    app.tick(now=6.0)
    assert "Run finished: done" in app.cmd_widget().state["lines"]
    assert app.state["conn"] == "off"


def test_run_end_event_stops_stream_and_banner():
    src, _cli, app, frame = _live_app()
    src.feed_event("connection", {"ok": True})
    app.tick(now=0.1)
    src.feed_event("run_end", {"session_id": "s-1", "run_id": "r-1",
                               "status": "aborted"})
    app.tick(now=0.2)
    lines = app.cmd_widget().state["lines"]
    assert "Run finished: aborted" in lines
    assert src.ended and src._sse.stopped
    assert "⊘ ENDED aborted" in frame().line_text(0)
    assert "conn─ off" in frame().line_text(H - 1)
    # run_end 后服务关流的 connection(False) 不算断线
    src.feed_event("connection", {"ok": False})
    app.tick(now=0.3)
    assert "调试实时连接中断,回退轮询" not in app.cmd_widget().state["lines"]


def test_async_command_path_echo_only():
    """异步源(sync_control=False):c/s/n 只回 echo,停点经事件回流(§4)。"""
    src, cli, app, _frame = _live_app()
    app.refresh()
    app.run_command("c")
    assert app.cmd_widget().state["lines"][-1] == "resumed (continue)"
    assert ("cmd", "s-1", "continue") in cli.calls
    # 停点不双打印:命令路径不打,SSE paused 路径打一次
    src.feed_event("paused", {"session_id": "s-1",
                              "pause_point": dict(cli.snap_doc["pause_point"])})
    app.tick(now=0.2)
    assert len([ln for ln in app.cmd_widget().state["lines"]
                if ln.startswith("Breakpoint 1")]) == 1


# ---------------------------------------------------------------------------
# D3 干预:set args / inject(假传输层;停点预检本地,出海经信封)
# ---------------------------------------------------------------------------

def _tool_call_doc() -> dict:
    """停在 pre:tool.call 的快照(set args 的合法位置)。"""
    return _paused_doc(pp={"signal": "pre:tool.call", "run_id": "r-1",
                           "frame_id": "f-fib001", "depth": 1, "step": None,
                           "tool": "system.python.exec", "skill": "demo.fib",
                           "payload": {"tool": "system.python.exec",
                                       "args": {"code": "result = 0 + 1"}},
                           "breakpoint_ids": ["bp-b"], "reason": "breakpoint"},
                       bps=[{"id": "bp-b", "kind": "tool_call",
                             "match": "system.*", "enabled": True, "hits": 1}])


def test_online_set_args_posts_modify():
    _src, cli, app, _frame = _live_app()
    cli.snap_doc = _tool_call_doc()
    app.run_command('set args {"code": "result = 41"}')
    lines = app.cmd_widget().state["lines"]
    assert any(ln.startswith('args patched: {"code": "result = 41"}')
               and "提交即放行" in ln for ln in lines)
    assert ("modify", "s-1", {"code": "result = 41"}) in cli.calls


def test_online_set_args_local_guards_no_http():
    """本地预检(§6 语式统一):停错点/非法 JSON 不打后端。"""
    _src, cli, app, _frame = _live_app()  # snap_doc = paused @ pre:step
    app.run_command('set args {"n": 5}')
    assert "Cannot set args: not paused at pre:tool.call." in \
        app.cmd_widget().state["lines"]
    cli.snap_doc = _tool_call_doc()
    app.run_command("set args {oops")
    assert any("JSON 解析错" in ln for ln in app.cmd_widget().state["lines"])
    assert not [c for c in cli.calls if c[0] == "modify"]


def test_online_modify_409_human():
    """竞态 409(本地快照旧,服务端已非 pre:tool.call)→ 人话原样转述。"""
    _src, cli, app, _frame = _live_app()
    cli.snap_doc = _tool_call_doc()
    cli.modify_resp = {"ok": False, "status": 409,
                       "error": "modify 仅在暂停于 pre:tool.call 时有效"}
    app.run_command('set args {"code": "x"}')
    assert "modify 仅在暂停于 pre:tool.call 时有效" in \
        app.cmd_widget().state["lines"]


def test_online_inject_posts_and_echoes_frame():
    _src, cli, app, _frame = _live_app()
    app.run_command("inject 补充说明 带空格")
    lines = app.cmd_widget().state["lines"]
    assert ("inject", "s-1", "补充说明 带空格", None) in cli.calls
    assert any(ln.startswith("injected -> frame f-fib001 (fib)")
               and "注入即放行" in ln for ln in lines)


def test_online_inject_404_human():
    _src, cli, app, _frame = _live_app()
    cli.inject_resp = {"ok": False, "status": 404, "error": "找不到调试会话: s-1"}
    app.run_command("inject hi")
    assert "找不到调试会话: s-1" in app.cmd_widget().state["lines"]


# ---------------------------------------------------------------------------
# D4 会话管理:rerun(换绑)/ detach / session(本机账本)
# ---------------------------------------------------------------------------

def test_online_rerun_rebinds_stream_and_resets_app():
    """rerun:POST 跳新会话——旧 SSE 停、新流换绑、app 终态簿记复位;
    旧会话尾巴(run_end s-1)被 sid 过滤不落地。"""
    src, cli, app, _frame = _live_app()
    src.feed_event("connection", {"ok": True})
    app.tick(now=0.1)
    src.feed_event("run_end", {"session_id": "s-1", "run_id": "r-1",
                               "status": "done"})
    app.tick(now=0.2)
    assert app.state["conn"] == "off"
    old_sse = src._sse
    app.run_command("rerun")
    lines = app.cmd_widget().state["lines"]
    assert any(ln.startswith("rerun -> 新会话 s-2 · run r-2") for ln in lines)
    assert ("rerun", "s-1") in cli.calls
    assert src.session_id == "s-2"
    assert old_sse.stopped and src._sse is not old_sse
    assert src._sse.started[1].endswith("/api/debug/sessions/s-2/stream")
    assert app.state["conn"] == "poll"  # 等 SSE 首连(复位后)
    # 新会话 paused 回流:停止行照打(复位不误伤)
    src.feed_event("connection", {"ok": True})
    src.feed_event("paused", {"session_id": "s-2",
                              "pause_point": dict(_paused_doc()["pause_point"])})
    app.tick(now=0.3)
    assert any(ln.startswith("Breakpoint 1, pre:step")
               for ln in app.cmd_widget().state["lines"])
    # 旧会话尾巴:被过滤,不端掉新会话
    src.feed_event("run_end", {"session_id": "s-1", "run_id": "r-1",
                               "status": "aborted"})
    app.tick(now=0.4)
    assert not src.ended
    assert app.state["conn"] == "online"


def test_online_rerun_400_no_origin_human():
    """replay/CLI 会话无 origin:服务端 400 → 人话原样转述(rerunnable=false)。"""
    _src, cli, app, _frame = _live_app()
    cli.rerun_resp = {"ok": False, "status": 400,
                      "error": "该会话没有创建参数(replay/CLI 会话),不支持 rerun"}
    app.run_command("rerun")
    assert "该会话没有创建参数(replay/CLI 会话),不支持 rerun" in \
        app.cmd_widget().state["lines"]


def test_online_detach_echo_then_run_end_via_stream():
    src, cli, app, _frame = _live_app()
    app.run_command("detach")
    lines = app.cmd_widget().state["lines"]
    assert ("close", "s-1") in cli.calls
    assert "detached(放行,run 继续跑完;会话已摘下)" in lines
    # DELETE 后 SSE 流仍送 run_end(生成器持会话引用,见 web/app.py stream_debug)
    src.feed_event("run_end", {"session_id": "s-1", "run_id": "r-1",
                               "status": "done"})
    app.tick(now=0.1)
    assert "Run finished: done" in app.cmd_widget().state["lines"]


def test_online_detach_poll_mode_terminal_snapshot():
    """断线回落中 detach:快照 404 是真态(DELETE 摘注册表)——run 未终态时
    轮询静默,终态后合成 detached 快照,终态行照常落地。"""
    src, cli, app, _frame = _live_app()
    cli.snap_doc = {**_paused_doc(), "state": "running", "pause_point": None}
    src.feed_event("connection", {"ok": True})
    app.tick(now=0.1)
    src.feed_event("connection", {"ok": False})  # 断线 → 回落轮询
    app.tick(now=0.2)
    app.run_command("detach")
    assert src._detached
    cli.snapshot_resp = {"ok": False, "status": 404, "error": "找不到调试会话: s-1"}
    cli.run_status = "running"
    app.tick(now=3.0)  # 轮询:404 + 未终态 → 静默(不造假终态)
    assert not any("Run finished" in ln
                   for ln in app.cmd_widget().state["lines"])
    cli.run_status = "done"
    app.tick(now=6.0)  # run 终态 → 合成 detached 快照 → 终态行
    assert "Run finished: done" in app.cmd_widget().state["lines"]


def test_online_session_list_and_switch():
    """session/info sessions(本机账本,§5.1):记账 → 列表 → session <SID>
    切换(attach);消逝会话 404 → 人话。"""
    from agent_os.host.tui.apps.debugger.model import SessionStore

    src, cli = _source()
    src.session_store = SessionStore()  # 纯内存(无头不落盘)
    cli.snap_doc = _paused_doc()
    src.open_live("demo.fib", {"n": 3}, [])
    _tree, app, _engine, _motion = build_app(src, keymap_id="vim")
    app.run_command("session")
    lines = app.cmd_widget().state["lines"]
    assert any("s-1" in ln and "demo.fib" in ln and "r-1" in ln for ln in lines)
    app.run_command("info sessions")
    assert any("s-1" in ln for ln in app.cmd_widget().state["lines"])
    # 切换 = attach:换绑 + 记账 + app 复位
    app.run_command("session s-9")
    assert src.session_id == "s-9"
    assert any(ln.startswith("attached -> s-9(run r-1)")
               for ln in app.cmd_widget().state["lines"])
    assert [r["session_id"] for r in src.session_store.list()] == ["s-9", "s-1"]
    # 消逝会话(服务端已删):404 → 人话,不炸不换位
    cli.snapshot_resp = {"ok": False, "status": 404, "error": "找不到调试会话: s-0"}
    app.run_command("session s-0")
    assert "找不到调试会话: s-0" in app.cmd_widget().state["lines"]
    assert src.session_id == "s-9"


def test_session_store_disk_roundtrip(tmp_path):
    """账本落盘:写 → 新实例读回;sid 去重;坏文件 → 空账本不炸。"""
    from agent_os.host.tui.apps.debugger.model import SessionStore

    path = tmp_path / "debugger-sessions.json"
    store = SessionStore(path)
    store.record("s-1", "r-1", "demo.fib", "http://x")
    store.record("s-2", "r-2", "replay:r-0", "http://x")
    store.record("s-1", "r-1b", "demo.fib", "http://x")  # 去重(按 sid)
    again = SessionStore(path)
    rows = again.list()
    assert [r["session_id"] for r in rows] == ["s-1", "s-2"]  # 新→旧,去重后位
    assert rows[0]["run_id"] == "r-1b"
    path.write_text("{bad json", encoding="utf-8")
    assert SessionStore(path).list() == []
