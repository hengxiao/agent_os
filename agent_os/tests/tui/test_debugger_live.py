"""live 端到端冒烟(docs/TUI-DEBUG.md §10 D2 验收:打真服务全流程;
停止行正确;断线回落轮询)。

真 uvicorn(线程内 ephemeral port)+ 真 OnlineDebugSource(httpx REST +
真 SSE 线程 → queue);``app.tick()`` 由测试驱动(主循环等价物,唯一 state
写者纪律不变)。数据基座 = tests/web/test_debug_api.py:21-24 同锚点
(demo.fib n=3,mock brain)。
"""

from __future__ import annotations

import threading
import time

import pytest
import uvicorn

from agent_os.host.tui.apps.debugger.app import build_app
from agent_os.host.tui.apps.debugger.model import OnlineDebugSource
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.web.app import create_app
from tests.helpers.config import write_config

W, H = 72, 20
TIMEOUT = 20.0

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def live_base(tmp_path):
    """真服务(ephemeral port;测试结束关停)。"""
    cfg = write_config(tmp_path)
    app = create_app(cfg, artifacts_root=tmp_path / "runs")
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error",
                       # SSE 长连接会拖住优雅关停(断线用例要求连接真断)
                       timeout_graceful_shutdown=0))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn 10s 内未启动"
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", server
    server.should_exit = True
    thread.join(timeout=5)


def _build(base_url: str, store=None):
    src = OnlineDebugSource(base_url, store=store)
    _tree, app, _engine, _motion = build_app(src, keymap_id="vim")

    def frame() -> CellBuffer:
        buf = CellBuffer(W, H)
        app.render_into(buf, Region(0, 0, W, H))
        return buf

    def drive(pred, timeout: float = TIMEOUT) -> bool:
        """主循环等价物:tick 驱动 drain/轮询直到 pred 或超时。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.tick()
            if pred():
                return True
            time.sleep(0.05)
        return False

    return src, app, frame, drive


def _out(app) -> list[str]:
    return app.cmd_widget().state["lines"]


def test_live_full_flow(live_base):
    """run(入口即停)→ c 停 tool_call 断点(停止行正确)→ c 跑完 run_end。"""
    base_url, _server = live_base
    src, app, frame, drive = _build(base_url)
    # fail-fast 探针 + 开会话(入口断点 + tool_call glob 断点,run 前注册)
    assert src.ping() is None
    src.open_live("demo.fib", {"n": 3}, [("tool_call", "system.*")])
    # SSE state/paused 回流:停止行 + ▶ + PAUSED 徽标
    assert drive(lambda: any(ln.startswith("Breakpoint 1, pre:step") and "step 1"
                             in ln for ln in _out(app)))
    assert "■ PAUSED" in frame().line_text(0)
    assert frame().line_text(H - 1).count("conn● SSE") == 1
    # 入口断点首停即删(pdb 语义;服务端 bp 列表只剩 system.*)
    assert drive(lambda: len(app.bps_widget().state["bps"]) == 1)
    # c → 停在 pre:tool.call(system.python.exec),停止行断点号/工具名正确
    app.run_command("c")
    assert _out(app)[-1] == "resumed (continue)"  # 异步源:先 echo
    # 真内核 pre:tool.call 负载无 step 顶层字段(runner.py:715;demo 脚本才有),
    # 停止行不带 ", step N" 后缀
    assert drive(lambda: any(ln.startswith("Breakpoint 2, pre:tool.call "
                                           "system.python.exec")
                             for ln in _out(app)))
    assert app.bps_widget().state["bps"][0]["hits"] == 1  # bp_hit 差分回流
    # 再 c → run 跑完:run_end 终态行 + 横幅 + 停流
    app.run_command("c")
    assert drive(lambda: "Run finished: done" in _out(app))
    assert src.ended
    assert "✓ ENDED done" in frame().line_text(0)
    assert "conn─ off" in frame().line_text(H - 1)


def test_live_disconnect_falls_back_to_poll(live_base):
    """断线回落:服务关停 → SSE 线程死亡 → ○ poll + 命令窗人话。"""
    base_url, server = live_base
    src, app, frame, drive = _build(base_url)
    src.open_live("demo.fib", {"n": 3}, [])
    assert drive(lambda: any(ln.startswith("Breakpoint 1, pre:step")
                             for ln in _out(app)))
    assert "conn● SSE" in frame().line_text(H - 1)
    # 真断线:服务关停,SSE 流中断(连接态由 SSE 线程上报,queue 回流)
    server.should_exit = True
    assert drive(lambda: "conn○ poll" in frame().line_text(H - 1))
    assert "调试实时连接中断,回退轮询" in _out(app)
    # 轮询路径激活但服务已死:单次失败静默,不炸不刷错误
    app.tick(now=time.monotonic() + 3.0)
    app.tick(now=time.monotonic() + 6.0)
    assert _out(app).count("调试实时连接中断,回退轮询") == 1
    src.close()


def test_live_set_args_reproduces_41(live_base):
    """D3 live 冒烟:set args 真落地——改 code 后 run 结果复现 web 锚点
    {"seq": [0, 1, 41]}(tests/web/test_debug_api.py:134-142 同款 patch)。"""
    base_url, _server = live_base
    src, app, _frame, drive = _build(base_url)
    src.open_live("demo.fib", {"n": 3}, [("tool_call", "system.python.exec")])
    assert drive(lambda: any(ln.startswith("Breakpoint 1, pre:step")
                             for ln in _out(app)))
    app.run_command("c")
    assert drive(lambda: any(ln.startswith("Breakpoint 2, pre:tool.call "
                                           "system.python.exec")
                             for ln in _out(app)))
    # 停在 pre:tool.call → set args 出海(POST …/modify,改完即放行)
    app.run_command('set args {"code":"result = 41\\nprint(result)"}')
    assert any(ln.startswith("args patched:") and "提交即放行" in ln
               for ln in _out(app))
    assert drive(lambda: "Run finished: done" in _out(app))
    # 结果复现:run detail 的 result 随改后参数变化
    detail = src._client.run_detail(src._run_id)
    assert detail["ok"] and detail["json"]["result"] == {"seq": [0, 1, 41]}


def test_live_rerun_and_detach(live_base):
    """D4 live 冒烟:rerun 跳新会话(SSE 换绑,入口断点重生再停)→ detach
    放行 run 跑完;本机账本记满两会话。"""
    from agent_os.host.tui.apps.debugger.model import SessionStore

    base_url, _server = live_base
    src, app, _frame, drive = _build(base_url, store=SessionStore())
    src.open_live("demo.fib", {"n": 3}, [])
    assert drive(lambda: any(ln.startswith("Breakpoint 1, pre:step")
                             for ln in _out(app)))
    sid1 = src.session_id
    # rerun:新会话首停(停止行二次出现;复位不误伤)
    app.run_command("rerun")
    assert any(ln.startswith("rerun -> 新会话") for ln in _out(app))
    assert src.session_id != sid1
    assert drive(lambda: sum(1 for ln in _out(app)
                             if ln.startswith("Breakpoint 1, pre:step")) == 2)
    # detach:放行,run 跑完(DELETE 后 SSE 仍送 run_end)
    app.run_command("detach")
    assert any(ln.startswith("detached(") for ln in _out(app))
    assert drive(lambda: "Run finished: done" in _out(app))
    assert len(src.session_store.list()) >= 2  # open + rerun 各记一笔
