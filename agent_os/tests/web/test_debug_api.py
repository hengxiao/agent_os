"""P3 锚点测试:Web 调试 API(Agent OS Debugger;host/web/app.py ``/api/debug/*``)。

固定约定:

- ``POST /api/debug/sessions {skill, input, breakpoints?}`` 开调试会话并起 run
  (注入 DebugController 装配内核),返回 ``{session_id, run_id}``;
  ``breakpoints`` 在 run 启动前注册——本文件的停点断言都靠它保证确定性
  (mock brain 毫秒级推进,起 run 后再加断点会竞态错过早期信号);
  每 run 至多一个活跃会话,冲突 409;
- 断点命中即暂停:``GET`` 快照 ``state=paused`` + ``pause_point``(signal/
  frame_id/step/payload 等);断点列表带 ``hits``;
- 命令桥跨线程:command/modify/inject 经 run worker 事件循环执行(REST 线程
  直接碰 ``asyncio.Event`` 会炸线程检查)——本文件全流程即该桥的回归;
- 干预:modify 改 pre:tool.call 的参数(结果随之改变);inject 缺省 frame_id
  =暂停帧,消息以 ``source=injected`` 落进帧上下文;
- run 结束(finished/aborted)会话自动 detached;显式 ``DELETE`` 会话 detach
  放行,run 继续跑完;
- SSE ``/stream``:``state``(连接快照)→ ``bp_hit``/``paused``/``resumed`` →
  ``run_end``;已暂停的会话连接后立即补发 ``paused``。

停点断言的事实依据(n=3 的信号序列,与 tests/kernel/test_debug.py 同源):
F1 step1 → invoke F2(base,一步即弹)→ F1 step2 内 pre:tool.call
(system.python.exec,``code = "result = 0 + 1\\nprint(result)"``)→
F1 step3(最终答案)→ F1 pop,run done。
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from tests.helpers.web import make_client as _client
from tests.helpers.web import run_and_wait, wait_status

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

#: 轮询等暂停的兜底(秒):调试器悬挂时测试应失败而不是卡死
PAUSE_TIMEOUT = 15.0


def _open_session(
    client: TestClient, n: int = 3, breakpoints: list[dict] | None = None
) -> tuple[str, str]:
    body: dict = {"skill": "demo.fib", "input": {"n": n}}
    if breakpoints:
        body["breakpoints"] = breakpoints
    r = client.post("/api/debug/sessions", json=body)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert "error" not in doc, doc
    return doc["session_id"], doc["run_id"]


def _add_breakpoint(client: TestClient, sid: str, kind: str, match: str = "*") -> dict:
    r = client.post(f"/api/debug/sessions/{sid}/breakpoints", json={"kind": kind, "match": match})
    assert r.status_code == 200, r.text
    return r.json()


def _command(client: TestClient, sid: str, cmd: str) -> None:
    r = client.post(f"/api/debug/sessions/{sid}/command", json={"cmd": cmd})
    assert r.status_code == 200, r.text


def _wait_pause(client: TestClient, sid: str, prev: dict | None = None) -> dict:
    """轮询等到下一次暂停(pause_point 与 prev 不同),返回 pause_point。"""
    deadline = time.monotonic() + PAUSE_TIMEOUT
    while time.monotonic() < deadline:
        doc = client.get(f"/api/debug/sessions/{sid}").json()
        if doc["state"] == "paused" and doc["pause_point"] != prev:
            return doc["pause_point"]
        time.sleep(0.05)
    raise AssertionError(f"会话 {sid} 未在 {PAUSE_TIMEOUT}s 内暂停")


# ---------------------------------------------------------------------------
# 全流程:开会话(启动即断)→ 步进 into/out → tool_call 断点 → modify → done
# ---------------------------------------------------------------------------


def test_debug_full_flow(tmp_path):
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 3, breakpoints=[{"kind": "step"}])

    # 启动即断:F1 第一步;hits 累计、帧栈非空
    doc = client.get(f"/api/debug/sessions/{sid}").json()
    assert doc["run_id"] == run_id and doc["state"] == "paused"
    point = doc["pause_point"]
    bp_step = doc["breakpoints"][0]
    assert point["signal"] == "pre:step"
    assert point["depth"] == 1 and point["step"] == 1
    assert point["breakpoint_ids"] == [bp_step["id"]]
    assert bp_step["hits"] == 1
    assert doc["frame_stack"], "暂停时帧栈不应为空"

    # 暂停中加 tool_call 断点、删 step 断点
    _add_breakpoint(client, sid, "tool_call", "system.python.exec")
    r = client.delete(f"/api/debug/sessions/{sid}/breakpoints/{bp_step['id']}")
    assert r.status_code == 200

    # step_into:进入子帧 F2 的第一步
    _command(client, sid, "step_into")
    point2 = _wait_pause(client, sid, prev=point)
    assert point2["signal"] == "pre:step"
    assert point2["reason"] == "step:into"
    assert point2["depth"] == 2
    assert point2["frame_id"] != point["frame_id"]

    # step_out:停在子帧 F2 弹栈
    _command(client, sid, "step_out")
    point3 = _wait_pause(client, sid, prev=point2)
    assert point3["signal"] == "pre:frame.pop"
    assert point3["reason"] == "step:out"
    assert point3["frame_id"] == point2["frame_id"]

    # continue:命中 tool_call 断点(F1 step2 内的 pre:tool.call)
    _command(client, sid, "continue")
    point4 = _wait_pause(client, sid, prev=point3)
    assert point4["signal"] == "pre:tool.call"
    assert point4["reason"] == "breakpoint"
    assert point4["tool"] == "system.python.exec"
    assert point4["frame_id"] == point["frame_id"]
    assert "result = 0 + 1" in point4["payload"]["args"]["code"]

    # live 帧检视:暂停时 checkpoint 未落盘,走内核内存态
    fr = client.get(f"/api/debug/sessions/{sid}/frames/{point4['frame_id']}")
    assert fr.status_code == 200
    assert fr.json()["messages"], "帧上下文消息为空"

    # modify:改工具参数(改完即放行)→ run 跑完,结果随改后参数变化
    r = client.post(
        f"/api/debug/sessions/{sid}/modify",
        json={"patch": {"code": "result = 41\nprint(result)"}},
    )
    assert r.status_code == 200, r.text
    detail = wait_status(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1, 41]}

    # run 结束自动 detach
    doc = client.get(f"/api/debug/sessions/{sid}").json()
    assert doc["state"] == "detached"


def test_debug_step_over(tmp_path):
    """step_over:同帧下一条 pre:step 停(跨过子帧不停);帧尾则停在 pre:frame.pop。"""
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 3, breakpoints=[{"kind": "step"}])
    point = _wait_pause(client, sid)  # F1 step1
    bp_id = point["breakpoint_ids"][0]
    client.delete(f"/api/debug/sessions/{sid}/breakpoints/{bp_id}")

    _command(client, sid, "step_over")
    point2 = _wait_pause(client, sid, prev=point)
    assert point2["signal"] == "pre:step"
    assert point2["reason"] == "step:over"
    assert point2["frame_id"] == point["frame_id"], "step_over 应停在同一帧"
    assert point2["step"] == point["step"] + 1, "中间跨过整个子帧(F2 的 step 不停)"

    _command(client, sid, "step_over")  # F1 step3(最终答案步):帧内无下一条 step
    _command_pending = _wait_pause(client, sid, prev=point2)
    assert _command_pending["signal"] == "pre:step" and _command_pending["step"] == 3
    _command(client, sid, "step_over")
    point4 = _wait_pause(client, sid, prev=_command_pending)
    assert point4["signal"] == "pre:frame.pop", "帧尾 step_over 应停在 pre:frame.pop"
    assert point4["frame_id"] == point["frame_id"]

    _command(client, sid, "continue")
    detail = wait_status(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1, 1]}


# ---------------------------------------------------------------------------
# inject / stop / DELETE
# ---------------------------------------------------------------------------


def test_debug_inject_defaults_to_paused_frame(tmp_path):
    client = _client(tmp_path)
    sid, run_id = _open_session(
        client, 3, breakpoints=[{"kind": "tool_call", "match": "system.python.exec"}]
    )
    point = _wait_pause(client, sid)
    assert point["signal"] == "pre:tool.call"

    # frame_id 缺省 = 暂停帧
    r = client.post(f"/api/debug/sessions/{sid}/inject", json={"text": "调试注入的补充说明"})
    assert r.status_code == 200, r.text
    assert r.json()["frame_id"] == point["frame_id"]

    detail = wait_status(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1, 1]}, "注入不应改变确定性 mock 的结果"

    # 注入消息落进帧上下文(source=injected)
    fr = client.get(f"/api/debug/sessions/{sid}/frames/{point['frame_id']}").json()
    injected = [m for m in fr["messages"] if m["content"] == "调试注入的补充说明"]
    assert injected, "注入消息未出现在帧上下文"
    assert injected[0]["role"] == "user"
    assert injected[0]["source"] == "injected"


def test_debug_stop_aborts_run(tmp_path):
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 3, breakpoints=[{"kind": "step"}])
    _wait_pause(client, sid)

    _command(client, sid, "stop")
    detail = wait_status(client, run_id)
    assert detail["status"] == "aborted"
    doc = client.get(f"/api/debug/sessions/{sid}").json()
    assert doc["state"] == "detached"


def test_debug_delete_detaches_and_run_finishes(tmp_path):
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 3, breakpoints=[{"kind": "step"}])
    _wait_pause(client, sid)

    r = client.delete(f"/api/debug/sessions/{sid}")
    assert r.status_code == 200
    assert client.get(f"/api/debug/sessions/{sid}").status_code == 404
    detail = wait_status(client, run_id)
    assert detail["status"] == "done", "DELETE 应放行让 run 跑完"
    assert detail["result"] == {"seq": [0, 1, 1]}


# ---------------------------------------------------------------------------
# rerun:以创建参数(skill/input/启动断点)重开新会话
# ---------------------------------------------------------------------------


def test_debug_rerun_paused_stops_old_and_restarts(tmp_path):
    """paused 会话 rerun:旧 run 走 stop 中止、旧会话清出注册表;新会话启动即断。"""
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 3, breakpoints=[{"kind": "step"}])
    _wait_pause(client, sid)
    assert client.get(f"/api/debug/sessions/{sid}").json()["rerunnable"] is True

    r = client.post(f"/api/debug/sessions/{sid}/rerun")
    assert r.status_code == 200, r.text
    doc = r.json()
    new_sid, new_run = doc["session_id"], doc["run_id"]
    assert new_sid != sid and new_run != run_id

    # 旧会话已结束并清出注册表;旧 run 走中止路径(stop 命令语义)
    assert client.get(f"/api/debug/sessions/{sid}").status_code == 404
    assert wait_status(client, run_id)["status"] == "aborted"

    # 新会话带 origin:启动断点生效(启动即断),快照仍 rerunable
    point = _wait_pause(client, new_sid)
    assert point["signal"] == "pre:step"
    new_doc = client.get(f"/api/debug/sessions/{new_sid}").json()
    assert new_doc["rerunnable"] is True
    assert any(bp["kind"] == "step" for bp in new_doc["breakpoints"]), "启动断点随 origin 重开"
    _command(client, new_sid, "stop")
    wait_status(client, new_run)


def test_debug_rerun_finished_session(tmp_path):
    """detached(run 已结束)会话 rerun:无需收尾,直接重开并跑完。"""
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 1)  # 无断点:run 直接跑完
    detail = wait_status(client, run_id)
    assert detail["status"] == "done"

    r = client.post(f"/api/debug/sessions/{sid}/rerun")
    assert r.status_code == 200, r.text
    new_run = r.json()["run_id"]
    assert new_run != run_id
    detail = wait_status(client, new_run)
    assert detail["status"] == "done" and detail["result"] == {"seq": [0]}


def test_debug_rerun_error_semantics(tmp_path):
    """404(未知会话)/ 400(replay 会话无 origin)。"""
    client = _client(tmp_path)
    assert client.post("/api/debug/sessions/dbg-nope/rerun").status_code == 404
    src_run = run_and_wait(client, "demo.fib", {"n": 1})
    r = client.post("/api/debug/sessions", json={"replay_run_id": src_run})
    assert r.status_code == 200, r.text
    sid, run_id = r.json()["session_id"], r.json()["run_id"]
    wait_status(client, run_id)
    assert client.get(f"/api/debug/sessions/{sid}").json()["rerunnable"] is False
    assert client.post(f"/api/debug/sessions/{sid}/rerun").status_code == 400


# ---------------------------------------------------------------------------
# 随时暂停(GDB SIGINT 语义):command pause,仅 running 可发
# ---------------------------------------------------------------------------


def test_debug_pause_suspends_free_run(tmp_path):
    """无断点自由运行中发 pause:会话在下一条可仲裁信号挂起,reason="pause"。

    竞态说明:run 若在 pause 到达前已跑完(会话 detached),pause 归 409——
    两个分支都是正确语义(fib 走真实 subprocess,窗口足够,200 为主路径)。
    """
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 3)  # 无断点:自由运行
    r = client.post(f"/api/debug/sessions/{sid}/command", json={"cmd": "pause"})
    if r.status_code == 200:
        point = _wait_pause(client, sid)
        assert point["reason"] == "pause"
        assert point["signal"] in ("pre:step", "pre:tool.call")
        _command(client, sid, "stop")
        assert wait_status(client, run_id)["status"] == "aborted"
    else:
        assert r.status_code == 409, r.text
        assert wait_status(client, run_id)["status"] == "done"


def test_debug_pause_error_semantics(tmp_path):
    """pause 的状态/存在性约束:paused 会话 409;未知会话 404。"""
    client = _client(tmp_path)
    assert (
        client.post("/api/debug/sessions/dbg-nope/command", json={"cmd": "pause"}).status_code
        == 404
    )
    sid, _run_id = _open_session(client, 3, breakpoints=[{"kind": "step"}])
    _wait_pause(client, sid)
    r = client.post(f"/api/debug/sessions/{sid}/command", json={"cmd": "pause"})
    assert r.status_code == 409, "paused 会话不能再 pause"
    _command(client, sid, "stop")
    wait_status(client, _run_id)


# ---------------------------------------------------------------------------
# 错误语义:404 / 400 / 409 / 200+failed
# ---------------------------------------------------------------------------


def test_debug_error_semantics(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/debug/sessions/dbg-nope").status_code == 404
    assert (
        client.post("/api/debug/sessions/dbg-nope/command", json={"cmd": "continue"}).status_code
        == 404
    )
    assert (
        client.post("/api/debug/sessions/dbg-nope/breakpoints", json={"kind": "step"}).status_code
        == 404
    )

    # 启动即断,保证 paused 态可断言
    sid, run_id = _open_session(client, 3, breakpoints=[{"kind": "step"}])
    assert _wait_pause(client, sid)["signal"] == "pre:step"
    # 未知断点 kind → 400;未知 cmd → 422(pydantic Literal)
    assert (
        client.post(f"/api/debug/sessions/{sid}/breakpoints", json={"kind": "bogus"}).status_code
        == 400
    )
    assert (
        client.post(f"/api/debug/sessions/{sid}/command", json={"cmd": "bogus"}).status_code
        == 422
    )
    # modify 仅暂停在 pre:tool.call 有效 → 409
    assert (
        client.post(f"/api/debug/sessions/{sid}/modify", json={"patch": {}}).status_code == 409
    )
    # 每 run 至多一个活跃会话 → 409
    assert (
        client.post("/api/debug/sessions", json={"skill": "demo.fib", "input": {"n": 1}}).status_code
        == 409
    )
    # 清理:stop → aborted;会话自动 detached 后可以再开会话
    _command(client, sid, "stop")
    assert wait_status(client, run_id)["status"] == "aborted"
    sid2, run_id2 = _open_session(client, 1)
    client.delete(f"/api/debug/sessions/{sid2}")
    wait_status(client, run_id2)


def test_debug_command_requires_paused(tmp_path):
    """running(未暂停)状态下发 command/modify/inject → 409。"""
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 1)  # 无断点:running(n=1 很快跑完,也算非 paused)
    assert (
        client.post(f"/api/debug/sessions/{sid}/command", json={"cmd": "continue"}).status_code
        == 409
    )
    assert (
        client.post(f"/api/debug/sessions/{sid}/modify", json={"patch": {}}).status_code == 409
    )
    assert (
        client.post(f"/api/debug/sessions/{sid}/inject", json={"text": "x"}).status_code == 409
    )
    wait_status(client, run_id)


def test_debug_session_validation_error(tmp_path):
    """run 未开始的校验错与 POST /api/runs 同归类:200 + {"status": "failed"}。"""
    client = _client(tmp_path)
    r = client.post("/api/debug/sessions", json={"skill": "demo.fib", "input": {"n": "x"}})
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    # 失败的会话已收回:不影响后续开会话
    sid, run_id = _open_session(client, 1)
    client.delete(f"/api/debug/sessions/{sid}")
    wait_status(client, run_id)


# ---------------------------------------------------------------------------
# SSE(TestClient 会缓冲整个响应直到 generator 结束——starlette 1.3 行为;
# 故用消费线程收流、主线程驱动会话到 run_end,再断言全量事件流)
# ---------------------------------------------------------------------------


def _consume_stream(client: TestClient, sid: str, holder: dict) -> threading.Thread:
    """后台线程收完整个调试 SSE 流(响应在 run_end 后完成),body 存入 holder。"""

    def consume() -> None:
        r = client.get(f"/api/debug/sessions/{sid}/stream")
        holder["status"] = r.status_code
        holder["body"] = r.text

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    return t


def _wait_stream_attached(client: TestClient, sid: str, timeout: float = 5.0) -> None:
    """确定性等 SSE 生成器启动(替代盲睡):轮询快照的 stream_attached。

    差分/跳变类事件(bp_hit/paused/resumed)只在生成器运行期间可观测;
    生成器启动时刻此前只能靠 time.sleep 猜——高负载下猜不中就是 flake。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc = client.get(f"/api/debug/sessions/{sid}").json()
        if doc.get("stream_attached"):
            return
        time.sleep(0.05)
    raise AssertionError("SSE 生成器 5s 内未启动(stream_attached 未置位)")


def _wait_stream_seen(client: TestClient, sid: str, bp_id: str, hits: int, timeout: float = 5.0) -> None:
    """等流的 hits 差分基线推进到指定值(摘除断点前必须让流先看到)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc = client.get(f"/api/debug/sessions/{sid}").json()
        if (doc.get("stream_seen") or {}).get(bp_id, 0) >= hits:
            return
        time.sleep(0.05)
    raise AssertionError(f"流未观测到 hits={hits}(bp {bp_id})")


def test_debug_sse_connect_first_events(tmp_path):
    """连接与首个事件:state 快照;已暂停的会话立即补发 paused。"""
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 3, breakpoints=[{"kind": "step"}])
    _wait_pause(client, sid)

    holder: dict = {}
    t = _consume_stream(client, sid, holder)
    # 确定性等生成器启动(流可观测面;paused 期间无状态变化可丢)
    _wait_stream_attached(client, sid)
    # 主线程驱动到 run_end:stop → aborted,流随即完成
    _command(client, sid, "stop")
    assert wait_status(client, run_id)["status"] == "aborted"
    t.join(timeout=PAUSE_TIMEOUT)
    assert not t.is_alive(), "SSE 流未在 run 结束后关闭"

    body = holder["body"]
    assert holder["status"] == 200
    # 首个事件 = state 快照;已暂停的会话补发 paused;stop 后 run_end(aborted)
    assert body.index("event: state") < body.index("event: paused")
    assert '"state": "paused"' in body
    assert '"signal": "pre:step"' in body
    assert "event: run_end" in body
    assert '"status": "aborted"' in body


def test_debug_sse_bp_hit_resumed_run_end(tmp_path):
    """流上观察全过程:bp_hit(再命中)→ paused → resumed → run_end(done)。"""
    client = _client(tmp_path)
    sid, run_id = _open_session(client, 3, breakpoints=[{"kind": "step"}])
    point = _wait_pause(client, sid)  # F1 step1,bp 已命中一次
    bp_id = point["breakpoint_ids"][0]

    holder: dict = {}
    t = _consume_stream(client, sid, holder)
    # 确定性等生成器启动(流可观测面):bp_hit 是 hits 差分事件,若生成器在命中
    # 之后才启动,差分起点已是新值,该事件就永远发不出来——此前用 time.sleep
    # 猜启动时刻,高负载下猜不中即 flake
    _wait_stream_attached(client, sid)
    # continue:命中下一 pre:step(子帧 F2)→ bp_hit(hits=2)+ paused
    _command(client, sid, "continue")
    point2 = _wait_pause(client, sid, prev=point)
    assert point2["signal"] == "pre:step"
    # 摘断点前必须让流的差分基线推进到 hits=2,否则事件发不出来(同 flake 根因)
    _wait_stream_seen(client, sid, bp_id, 2)
    # 摘断点后 continue:resumed → run 跑完 → run_end(done)
    client.delete(f"/api/debug/sessions/{sid}/breakpoints/{bp_id}")
    _command(client, sid, "continue")
    assert wait_status(client, run_id)["status"] == "done"
    t.join(timeout=PAUSE_TIMEOUT)
    assert not t.is_alive(), "SSE 流未在 run 结束后关闭"

    body = holder["body"]
    assert "event: bp_hit" in body
    assert '"hits": 2' in body
    assert f'"breakpoint_id": "{bp_id}"' in body
    assert body.index("event: bp_hit") < body.index("event: resumed")
    assert "event: run_end" in body
    assert '"status": "done"' in body


# ---------------------------------------------------------------------------
# P5 时间旅行:{replay_run_id, until_step?} 会话形态(回放边界:LLM Mock
# 回放 + 工具真实重跑;响应带 mode="replay")
# ---------------------------------------------------------------------------


def test_debug_replay_session(tmp_path):
    """录制 fib(3) → replay 会话:停点序列与 live 一致,响应带 mode="replay"。"""
    client = _client(tmp_path)
    src_run = run_and_wait(client, "demo.fib", {"n": 3})

    r = client.post(
        "/api/debug/sessions",
        json={"replay_run_id": src_run, "breakpoints": [{"kind": "step"}]},
    )
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["mode"] == "replay"
    sid, run_id = doc["session_id"], doc["run_id"]
    assert run_id != src_run, "回放调试应是新 run"

    # 启动即断:F1 第一步(与 live 相同)
    point = _wait_pause(client, sid)
    assert point["signal"] == "pre:step"
    assert point["depth"] == 1 and point["step"] == 1

    # step_into:进入子帧 F2 的第一步
    _command(client, sid, "step_into")
    point2 = _wait_pause(client, sid, prev=point)
    assert point2["signal"] == "pre:step" and point2["depth"] == 2

    _command(client, sid, "stop")
    assert wait_status(client, run_id)["status"] == "aborted"


def test_debug_replay_until_step(tmp_path):
    """until_step=2:run 直达第 2 条 pre:step(子帧)才暂停(一次性步数断点)。"""
    client = _client(tmp_path)
    src_run = run_and_wait(client, "demo.fib", {"n": 3})
    r = client.post("/api/debug/sessions", json={"replay_run_id": src_run, "until_step": 2})
    assert r.status_code == 200, r.text
    sid, run_id = r.json()["session_id"], r.json()["run_id"]

    point = _wait_pause(client, sid)
    assert point["signal"] == "pre:step" and point["depth"] == 2

    client.delete(f"/api/debug/sessions/{sid}")
    detail = wait_status(client, run_id)
    assert detail["status"] == "done"
    assert detail["result"] == {"seq": [0, 1, 1]}, "回放结果应与原 run 一致"


def test_debug_replay_validation(tmp_path):
    """两形态互斥/缺失/产物不存在/非法 until_step 的 400/404 归类。"""
    client = _client(tmp_path)
    src_run = run_and_wait(client, "demo.fib", {"n": 1})
    # 两形态字段混给 → 400
    assert (
        client.post(
            "/api/debug/sessions",
            json={"skill": "demo.fib", "input": {"n": 1}, "replay_run_id": src_run},
        ).status_code
        == 400
    )
    # 都不给 → 400
    assert client.post("/api/debug/sessions", json={}).status_code == 400
    # live 形态带 until_step → 400
    assert (
        client.post(
            "/api/debug/sessions",
            json={"skill": "demo.fib", "input": {"n": 1}, "until_step": 2},
        ).status_code
        == 400
    )
    # 产物目录不存在 → 404
    assert (
        client.post("/api/debug/sessions", json={"replay_run_id": "run-不存在"}).status_code
        == 404
    )
    # until_step 非法(<1)→ 400
    assert (
        client.post(
            "/api/debug/sessions", json={"replay_run_id": src_run, "until_step": 0}
        ).status_code
        == 400
    )
