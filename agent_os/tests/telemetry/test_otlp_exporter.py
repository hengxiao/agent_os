"""OTLP exporter 锚点测试(telemetry/otlp_exporter.py;docs/DESIGN.md §10.1/§10.2)。

固定约定:

- 帧树 ≡ span 树(§10.2):``run.started`` 开 run 根 span(终态信号关闭,
  ``agent_os.run.status`` 属性;aborted → OTLP ERROR + message);
  ``pre:frame.push``/``pre:tool.call``/``pre:llm.request`` 开子 span(parent =
  当前开放栈顶),``pre:frame.pop``/``post:tool.call``(帧内 LIFO)/
  ``post:llm.response`` 关闭;其余信号 = 顶 span 事件(payload 拍平为属性);
- ``trace_id = sha256(run_id)`` 前 32 hex,全 run 稳定;span_id 自增 16-hex;
  时间戳 unix 纳秒字符串;resource 恒 ``service.name="agent-os"``;
- llm span 带 OpenInference 风格用量属性(``llm.token_count.prompt`` 等,
  ch06-evaluation.md:83 的语义约定);
- ``export`` 绝不 await IO:有界队列(queue_max 满丢最旧,``_dropped`` 计数)
  + 后台 drainer(batch_max/flush_interval 触发 flush);POST 失败丢批
  (``_failed`` 计数)不重试(best-effort v1);``close()`` 是排干点且幂等;
- 未配对/乱序信号 → ``_skipped`` 计数跳过,绝不抛。

对端:tests/helpers/otlp_server.py(FastAPI 罐头端点;真 uvicorn + ephemeral
port,fixture 照 tests/tools/test_mcp_http.py:55-72 先例)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from typing import Any

import pytest
import uvicorn

from agent_os.api.v1 import (
    POST_LLM_RESPONSE,
    POST_TOOL_CALL,
    PRE_FRAME_POP,
    PRE_FRAME_PUSH,
    PRE_LLM_REQUEST,
    PRE_TOOL_CALL,
    RUN_ABORTED,
    RUN_FINISHED,
    RUN_STARTED,
    ChatRequest,
    ChatResponse,
    Signal,
)
from agent_os.runtime.config import build_kernel
from agent_os.telemetry import OtlpExporter
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import FIB_SKILLS_YAML, fib_kernel, record_all
from tests.helpers.otlp_server import ServerState, create_app


@pytest.fixture
def otlp_server():
    """真 OTLP 端点(ephemeral port;测试结束关停)——照 test_mcp_http.py 先例。"""
    state = ServerState()
    app = create_app(state)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error",
                       timeout_graceful_shutdown=0))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn 10s 内未启动"
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", state, server
    server.should_exit = True
    thread.join(timeout=5)


def _dead_port_url() -> str:
    """拿一个保证无人监听的环回端口(bind 后即关,连接必 ECONNREFUSED)。"""
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def _sig(name: str, run_id: str = "r1", frame_id: str | None = "f1",
         payload: dict | None = None) -> Signal:
    return Signal(name=name, run_id=run_id, frame_id=frame_id, payload=payload or {})


def _all_spans(state: ServerState) -> list[dict[str, Any]]:
    """全部已收 body 的 span 平铺(发送顺序 = 各 span 关闭顺序)。"""
    return [
        span
        for body in state.bodies
        for rs in body["resourceSpans"]
        for ss in rs["scopeSpans"]
        for span in ss["spans"]
    ]


def _attrs(span: dict[str, Any]) -> dict[str, Any]:
    """OTLP attributes 数组 → 平 dict(取 AnyValue 的原始值)。"""
    return {a["key"]: next(iter(a["value"].values())) for a in span.get("attributes", [])}


# ---------------------------------------------------------------------------
# 端到端 span 树(脚本化 fib run)
# ---------------------------------------------------------------------------


def test_fib_run_span_tree_end_to_end(otlp_server, tmp_path):
    """fib(3) 全程:run 根 + 2 skill + 1 tool + 4 llm span 的树形与用量属性。"""
    url, state, _server = otlp_server
    kernel = fib_kernel(fib_brain, telemetry_dir=tmp_path / "traces")
    otlp = OtlpExporter(url, flush_interval=0.05)
    kernel.telemetry.register_exporter(otlp)
    seen = record_all(kernel)

    assert asyncio.run(kernel.run("demo.fib", {"n": 3})) == {"seq": [0, 1, 1]}
    asyncio.run(kernel.telemetry.close())  # 排干点:close 才保证余量送达(§10.2)

    run_id = next(s for s in seen if s.name == RUN_STARTED).run_id
    spans = _all_spans(state)
    assert spans, "罐头端点未收到任何 span"
    # resource 属性:每批都带 service.name(OTLP 资源语义)
    for body in state.bodies:
        resource_attrs = _attrs(body["resourceSpans"][0]["resource"])
        assert resource_attrs["service.name"] == "agent-os"
    # trace_id 全 run 稳定 = sha256(run_id)[:32]
    trace_id = hashlib.sha256(run_id.encode()).hexdigest()[:32]
    assert {s["traceId"] for s in spans} == {trace_id}
    span_ids = [s["spanId"] for s in spans]
    assert len(span_ids) == len(set(span_ids)), "span_id 须 trace 内唯一"
    assert all(len(sid) == 16 for sid in span_ids), "span_id 为 16-hex"

    by_name: dict[str, list[dict]] = {}
    for span in spans:
        by_name.setdefault(span["name"], []).append(span)
    # run 根 span:无 parent,终态属性 + OTLP status OK
    (root,) = by_name["run"]
    assert "parentSpanId" not in root
    root_attrs = _attrs(root)
    assert root_attrs["agent_os.run_id"] == run_id
    assert root_attrs["agent_os.run.status"] == "done"
    assert root["status"] == {"code": 1}
    assert root["startTimeUnixNano"].isdigit() and root["endTimeUnixNano"].isdigit()
    # skill span ×2(根帧 + 子帧),parent 链见下方断言(伪工具不开 tool span)
    skills = [s for s in spans if s["name"].startswith("skill:")]
    assert len(skills) == 2
    assert all(s["name"].startswith("skill:local:demo.fib") for s in skills)
    tools = {s["name"]: s for s in spans if s["name"].startswith("tool:")}
    # skill.* 是内核拦截的伪工具(runner.py:_dispatch_call 直转 _invoke_skill,
    # 只发 pre/post:skill.invoke,不发 pre/post:tool.call)→ 唯一真工具是沙箱
    assert set(tools) == {"tool:system.python.exec"}
    assert all(_attrs(s)["agent_os.tool.ok"] is True for s in tools.values())
    # 子帧 skill span 的 parent = 父帧 skill span(伪工具不开 tool span)
    root_skill = next(s for s in skills if s.get("parentSpanId") == root["spanId"])
    child_skill = next(s for s in skills if s is not root_skill)
    assert child_skill["parentSpanId"] == root_skill["spanId"]
    assert _attrs(child_skill)["agent_os.depth"] == "2"
    assert tools["tool:system.python.exec"]["parentSpanId"] == root_skill["spanId"]
    # 子技能调用伪工具的信号落为父帧 skill span 的事件
    skill_events = [e["name"] for s in skills for e in s.get("events", [])]
    assert "pre:skill.invoke" in skill_events and "post:skill.invoke" in skill_events
    # llm span ×4(根帧:invoke 自己 → python.exec → 最终答案;子帧 base case ×1)
    llms = [s for s in spans if s["name"].startswith("llm:")]
    assert len(llms) == 4, "fib(3):根帧 3 次 + 子帧 base case 1 次 LLM 调用"
    for span in llms:
        attrs = _attrs(span)
        assert attrs["llm.model_name"] == "mock/fib"
        assert attrs["llm.token_count.prompt"] == "1"  # intValue 的 OTLP JSON string 编码
        assert attrs["llm.token_count.completion"] == "1"
        assert "llm.token_count.cache_read" in attrs  # additive 新维度在场(§10.2 增量)
        assert "llm.ttft_ms" in attrs and "llm.total_ms" in attrs
    # 其余信号(如 pre:step/post:step)落为顶 span 事件
    events = [e for s in spans for e in s.get("events", [])]
    assert any(e["name"] == "pre:step" for e in events)
    assert all(e["timeUnixNano"].isdigit() for e in events)
    # fib 无压缩(compression="off")、无流式:无未配对跳过
    assert otlp._skipped == 0
    assert otlp._failed == 0 and otlp._dropped == 0


# ---------------------------------------------------------------------------
# redact 接线:端点与 WAL 同见 [EMAIL]
# ---------------------------------------------------------------------------


def pii_brain(req: ChatRequest) -> ChatResponse:
    """首调用即抛带邮箱的 RuntimeError(模块级:[providers.mock] brain 按 dotted path 加载)。"""
    raise RuntimeError("联系方式 admin@corp.com 外泄")


def test_redact_on_masks_endpoint_and_wal(otlp_server, tmp_path):
    """redact=True(配置接线):run.aborted 的 error 在 WAL 行与端点 span status 同为脱敏副本。"""
    url, state, _server = otlp_server
    tdir = tmp_path / "traces"
    kernel = build_kernel(
        {
            "run": {"model": "mock/fib", "compression": "off"},
            "providers": {"mock": {"brain": "tests.telemetry.test_otlp_exporter:pii_brain"}},
            "tools": {"python_exec": "subprocess"},
            "skills": {"path": str(FIB_SKILLS_YAML)},
            "telemetry": {"dir": str(tdir), "redact": True},
        }
    )
    kernel.telemetry.register_exporter(OtlpExporter(url, flush_interval=0.05))
    seen = record_all(kernel)

    with pytest.raises(RuntimeError, match="admin@corp.com"):
        asyncio.run(kernel.run("demo.fib", {"n": 1}))
    asyncio.run(kernel.telemetry.close())

    run_id = next(s for s in seen if s.name == RUN_STARTED).run_id
    wal = (tdir / f"{run_id}.jsonl").read_text(encoding="utf-8")
    assert "[EMAIL]" in wal and "admin@corp.com" not in wal, "WAL 落盘脱敏副本"
    aborted = [json.loads(line) for line in wal.splitlines() if '"run.aborted"' in line]
    assert aborted[0]["payload"]["error"] == "RuntimeError: 联系方式 [EMAIL] 外泄"

    spans = _all_spans(state)
    (root,) = [s for s in spans if s["name"] == "run"]
    assert root["status"]["code"] == 2, "aborted → OTLP STATUS_CODE_ERROR"
    assert root["status"]["message"] == "RuntimeError: 联系方式 [EMAIL] 外泄"
    assert "admin@corp.com" not in json.dumps(state.bodies, ensure_ascii=False)
    assert _attrs(root)["agent_os.run.status"] == "aborted"


# ---------------------------------------------------------------------------
# best-effort 可靠性形态
# ---------------------------------------------------------------------------


def test_endpoint_down_run_still_done(tmp_path):
    """端点不可达:run 照常 DONE;失败丢批计数(不重试);close 不抛。"""
    kernel = fib_kernel(fib_brain, telemetry_dir=tmp_path / "traces")
    otlp = OtlpExporter(_dead_port_url(), flush_interval=0.05, timeout=1.0)
    kernel.telemetry.register_exporter(otlp)

    assert asyncio.run(kernel.run("demo.fib", {"n": 3})) == {"seq": [0, 1, 1]}
    asyncio.run(kernel.telemetry.close())  # 端点 down:末次排干同样丢批,不抛
    assert otlp._failed > 0, "POST 失败的 span 须计数"
    assert otlp._skipped == 0


def test_endpoint_500_drops_batch(otlp_server, tmp_path):
    """端点回 500:丢批计数,不重试(收到的 body 形态不被记账为成功)。"""
    url, state, _server = otlp_server
    state.status = 500
    otlp = OtlpExporter(url, flush_interval=60.0)  # drainer 不 firing,close 排干

    async def main():
        await otlp.export(_sig(RUN_STARTED, frame_id=None, payload={"skill": "s"}))
        await otlp.export(_sig(RUN_FINISHED, frame_id=None))
        await otlp.close()

    asyncio.run(main())
    assert otlp._failed == 1, "根 span 所在批被 500 丢弃并计数"
    assert len(state.bodies) == 1, "端点记账:请求到了但按失败处理(不重试 → 恰好 1 次)"


def test_queue_overflow_drops_oldest(otlp_server):
    """queue_max=2 + drainer 不排空(慢端点等价形态):丢最旧 + 计数;close 送出存活者。

    drainer 的 flush_interval 拉到 60s(测试窗口内不 firing)= 慢端点/背压的
    确定性等价:队列只进不出,溢出路径与计数被精确锚定,无时序竞态。
    """
    url, state, _server = otlp_server
    state.delay = 0.2  # 慢端点(close 排干的两批各睡一拍)
    otlp = OtlpExporter(url, batch_max=1, flush_interval=60.0, queue_max=2)

    async def main():
        await otlp.export(_sig(RUN_STARTED, frame_id=None, payload={"skill": "s"}))
        for i in range(5):
            await otlp.export(_sig(PRE_TOOL_CALL, payload={"tool": f"t{i}"}))
            await otlp.export(_sig(POST_TOOL_CALL, payload={"tool": f"t{i}", "ok": True}))
        await otlp.export(_sig(RUN_FINISHED, frame_id=None))
        await otlp.close()

    asyncio.run(main())
    # 6 条完成 span(5 tool + 根)过容量 2 的队列:丢最旧 4 条,存活 t4 与根
    assert otlp._dropped == 4
    names = [s["name"] for s in _all_spans(state)]
    assert names == ["tool:t4", "run"], "丢最旧:仅最后两条存活并在 close 排干送达"


def test_close_drains_then_idempotent(otlp_server):
    """close() 是排干点(drainer 未 firing 也全量送出);重复 close 无副作用。"""
    url, state, _server = otlp_server
    otlp = OtlpExporter(url, flush_interval=60.0)

    async def main():
        await otlp.export(_sig(RUN_STARTED, frame_id=None, payload={"skill": "s"}))
        await otlp.export(_sig(PRE_FRAME_PUSH, payload={"skill": "s", "depth": 1}))
        await otlp.export(_sig(PRE_FRAME_POP, payload={}))
        await otlp.export(_sig(RUN_FINISHED, frame_id=None))
        await otlp.close()
        await otlp.close()  # 幂等:不再发送、不报错
        await otlp.export(_sig(RUN_STARTED, run_id="r2", frame_id=None))  # close 后 export 丢弃

    asyncio.run(main())
    names = [s["name"] for s in _all_spans(state)]
    assert names == ["skill:s", "run"], "两 span 恰好各送一次(close 排干;重复 close 无重发)"


# ---------------------------------------------------------------------------
# 未配对/乱序:计数跳过,绝不抛
# ---------------------------------------------------------------------------


def test_unpaired_signals_skipped_never_raise(otlp_server):
    url, state, _server = otlp_server
    otlp = OtlpExporter(url, flush_interval=60.0)

    async def main():
        # 未知 run 的信号(run.started 缺席)
        await otlp.export(_sig(POST_TOOL_CALL, run_id="ghost", payload={"tool": "t", "ok": True}))
        # 孤儿 post:tool.call(无 pre 配对;payload 无 call id,帧内 LIFO 无从配)
        await otlp.export(_sig(RUN_STARTED, frame_id=None, payload={"skill": "s"}))
        await otlp.export(_sig(POST_TOOL_CALL, payload={"tool": "t", "ok": True}))
        # 孤儿 post:llm.response(压缩链补发的 source="compress" 同归此路径)
        await otlp.export(_sig(POST_LLM_RESPONSE, payload={"model": "m", "source": "compress"}))
        # 孤儿 pre:frame.pop(栈空)
        await otlp.export(_sig(PRE_FRAME_POP, payload={}))
        await otlp.export(_sig(RUN_ABORTED, frame_id=None, payload={"error": "boom"}))
        await otlp.close()

    asyncio.run(main())  # 全程不抛
    assert otlp._skipped == 4
    (root,) = _all_spans(state)
    assert root["name"] == "run" and root["status"]["code"] == 2
    assert root["status"]["message"] == "boom"


def test_aborted_run_force_closes_open_spans(otlp_server):
    """中断路径(无 post 配对):终态信号强关仍开放的 llm/tool/帧 span,不滞留。"""
    url, state, _server = otlp_server
    otlp = OtlpExporter(url, flush_interval=60.0)

    async def main():
        await otlp.export(_sig(RUN_STARTED, frame_id=None, payload={"skill": "s"}))
        await otlp.export(_sig(PRE_FRAME_PUSH, payload={"skill": "s", "depth": 1}))
        await otlp.export(_sig(PRE_LLM_REQUEST, payload={"model": "m"}))
        await otlp.export(_sig(RUN_ABORTED, frame_id=None, payload={"error": "断电"}))
        await otlp.close()

    asyncio.run(main())
    names = [s["name"] for s in _all_spans(state)]
    assert names == ["llm:m", "skill:s", "run"], "内层残留先强关,根最后"
    assert otlp._skipped == 0


# ---------------------------------------------------------------------------
# rl_capture 守卫:报文键永不进 span 事件体
# ---------------------------------------------------------------------------


def test_flatten_skips_rl_message_keys():
    """_flatten 顶层 messages/message 键一律跳过(rl_capture 报文会撑爆事件体);
    嵌套同名键(如 error.message)与其他键不受影响。"""
    from agent_os.telemetry.otlp_exporter import _flatten

    payload = {
        "model": "m",
        "messages": [{"role": "system", "content": "x" * 5000}],
        "message": {"role": "assistant", "content": "y" * 5000},
        "usage": {"prompt": 1},
        "error": {"message": "嵌套保留"},
    }
    assert _flatten(payload) == {
        "model": "m",
        "usage.prompt": 1,
        "error.message": "嵌套保留",
    }


def test_rl_capture_payloads_never_reach_span_events(otlp_server, tmp_path):
    """rl_capture 开(配置接线 rl_export)的 run:pre/post:llm.* payload 带报文键,
    全部 span 事件的属性键无 messages/message(守卫端到端锚;RL 行照常出产)。"""
    url, state, _server = otlp_server
    tdir = tmp_path / "traces"
    rl_path = tmp_path / "rl.jsonl"
    kernel = build_kernel(
        {
            "run": {"model": "mock/fib", "compression": "off"},
            "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
            "tools": {"python_exec": "subprocess"},
            "skills": {"path": str(FIB_SKILLS_YAML)},
            "telemetry": {"dir": str(tdir), "rl_export": {"path": str(rl_path)}},
        }
    )
    assert kernel._rl_capture is True
    kernel.telemetry.register_exporter(OtlpExporter(url, flush_interval=0.05))

    assert asyncio.run(kernel.run("demo.fib", {"n": 1})) == {"seq": [0]}
    asyncio.run(kernel.telemetry.close())

    spans = _all_spans(state)
    assert spans, "罐头端点未收到任何 span"
    event_keys = {
        a["key"] for s in spans for e in s.get("events", []) for a in e["attributes"]
    }
    assert "messages" not in event_keys and "message" not in event_keys
    assert not any(k.startswith(("messages.", "message.")) for k in event_keys)
    # RL 侧照常成行(捕获确实开着):fib(1) base case 恰好 1 次主循环交换
    rows = [
        json.loads(line)
        for line in rl_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and '"header"' not in line
    ]
    assert len(rows) == 1 and rows[0]["request"]["messages"]
