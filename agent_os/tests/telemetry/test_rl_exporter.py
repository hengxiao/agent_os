"""RL 轨迹 exporter 锚点测试(telemetry/rl_exporter.py;docs/DESIGN.md §10.2 训练就绪导出)。

固定约定:

- 行格式 schema ``agent_os.rl-trace/1``:首行版本头;``llm_exchange`` 行 =
  ``{v, type, run_id, frame_id, seq, model, request: {messages}, response: {message},
  usage, ts}``;消息 dict 与 checkpoint ``_message_to_dict`` 同形状(WAL 行 ↔
  checkpoint 消息逐字可对照);
- 配对:pre:llm.request(带 ``messages`` 键)按 (run_id, frame_id) FIFO 缓冲;
  post:llm.response(非 compress 源且带 ``message`` 键)弹出出行;``seq`` 每 run
  单调;压缩链补发(``source="compress"``)一律过滤(同 replay/otlp 口径);
  未配对/乱序 → ``_skipped`` 计数,绝不抛;
- 内核捕获开关:builder 见 sink 上注册了 ``rl_trajectory`` exporter 即置
  ``kernel._rl_capture``(duck-typed);**未配置时 pre/post:llm.* payload 与
  legacy 逐字节一致**(无 messages/message 键);
- 保真边界:``request.messages`` 是 build 后实发线报(压缩后主循环请求)——
  模型真实所见即训练所见;压缩前原始上下文不在行内,由 WAL pre/post:compress
  信号与 checkpoint 帧上下文重建;loss masking 角色维 = 消息自带 role/source
  (assistant 可训练,system/user/tool/injected 全 mask);
- redact 开时 WAL 行与 RL 行收同一份脱敏副本(sink.record 入口替换,
  train-on-redacted 语义)。

对端纪律照 test_otlp_exporter.py 先例:脚本化 fib_kernel / build_kernel +
register_exporter + record_all + telemetry.close 排干。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

from agent_os.api.v1 import (
    POST_LLM_RESPONSE,
    PRE_LLM_REQUEST,
    RUN_ABORTED,
    RUN_STARTED,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Role,
    Signal,
    ToolCall,
)
from agent_os.runtime.config import build_kernel
from agent_os.telemetry import RlTrajectoryExporter
from tests.helpers.kernels import FIB_SKILLS_YAML, fib_kernel, record_all

#: 主循环与摘要调用两档 usage(区分主循环/压缩链调用,同 test_compress_usage.py 先例)
MAIN_USAGE = ChatUsage(prompt=50, completion=20, cost=0.01)
SUMMARIZE_USAGE = ChatUsage(prompt=100, completion=10, cost=0.001)

_CHATTY_STATE = {"main_calls": 0}


def reset_rl_chatty_brain() -> None:
    _CHATTY_STATE["main_calls"] = 0


def rl_chatty_brain(req: ChatRequest) -> ChatResponse:
    """双路 mock 大脑(模块级:[providers.mock] brain 按 dotted path 加载)。

    摘要请求(SYSTEM 含压缩器指令)回笔记;主循环前 3 步回大输出工具调用
    (撑爆 300 token cap 触发压缩),第 4 步回最终答案。模块级状态跨请求存活
    (同 tests/helpers/brains.py cut_brain 先例),每个用例前先 reset。
    """
    first = (req.messages[0].content or "") if req.messages else ""
    if "上下文压缩器" in first:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content="压缩笔记:早前工具往返摘要"),
            finish_reason="stop",
            usage=SUMMARIZE_USAGE,
        )
    _CHATTY_STATE["main_calls"] += 1
    if _CHATTY_STATE["main_calls"] >= 4:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"answer": "done"})),
            finish_reason="stop",
            usage=MAIN_USAGE,
        )
    return ChatResponse(
        message=Message(
            role=Role.ASSISTANT,
            tool_calls=[
                ToolCall(
                    id=f"c{_CHATTY_STATE['main_calls']}",
                    name="system.python.exec",
                    args={"code": "print('x' * 1200)"},
                )
            ],
        ),
        finish_reason="tool_calls",
        usage=MAIN_USAGE,
    )


def rl_pii_brain(req: ChatRequest) -> ChatResponse:
    """一步到位最终答案(模块级 dotted path;redact 用例的最小 run)。"""
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"answer": "done"})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


CHATTY_YAML = """
skills:
  - name: chatty
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    permissions: { tools: [system.python.exec], skills: [] }
    context_policy: { max_tokens: 300, compress: summarize }
    limits: { max_steps: 12, timeout: 60 }
    model: { prefer: ["mock/chatty"] }
    prompt: "闲聊任务:按需调工具,最后输出 answer 字段。"
"""

PII_YAML = """
skills:
  - name: pii_demo
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    permissions: { tools: [], skills: [] }
    limits: { max_steps: 4, timeout: 30 }
    model: { prefer: ["mock/pii"] }
    prompt: "答复格式咨询 admin@corp.com 后即可获知;直接输出 answer 字段。"
"""


def _sig(name: str, run_id: str = "r1", frame_id: str | None = "f1", payload: dict | None = None) -> Signal:
    return Signal(name=name, run_id=run_id, frame_id=frame_id, payload=payload or {})


def _rows(path) -> list[dict]:
    """RL JSONL → llm_exchange 行(跳过版本头)。"""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("type") != "header":
                rows.append(row)
    return rows


def _fib_cfg(telemetry: dict) -> dict:
    return {
        "run": {"model": "mock/fib", "compression": "off"},
        "providers": {"mock": {"brain": "tests.helpers.brains:fib_brain"}},
        "tools": {"python_exec": "subprocess"},
        "skills": {"path": str(FIB_SKILLS_YAML)},
        "telemetry": telemetry,
    }


# ---------------------------------------------------------------------------
# 配置接线 run:全量交换行(请求报文 + 响应报文 + usage)
# ---------------------------------------------------------------------------


def test_configured_run_rows_full_exchange(tmp_path):
    """fib(3) 全程:4 次主循环交换成行;行含完整请求报文/响应报文/usage;seq 单调;
    报文与 WAL 信号 payload 逐字一致(checkpoint 同款形状的可对照性锚)。"""
    tdir = tmp_path / "traces"
    rl_path = tmp_path / "rl.jsonl"
    kernel = build_kernel(_fib_cfg({"dir": str(tdir), "rl_export": {"path": str(rl_path)}}))
    assert kernel._rl_capture is True, "builder 见 rl_trajectory exporter 即置捕获开关"
    seen = record_all(kernel)

    assert asyncio.run(kernel.run("demo.fib", {"n": 3})) == {"seq": [0, 1, 1]}
    asyncio.run(kernel.telemetry.close())

    run_id = next(s for s in seen if s.name == RUN_STARTED).run_id
    rows = _rows(rl_path)
    assert len(rows) == 4, "fib(3):根帧 3 次 + 子帧 base case 1 次主循环 LLM 调用"
    assert [r["seq"] for r in rows] == [1, 2, 3, 4], "seq 每 run 单调递增"
    for row in rows:
        assert row["v"] == 1 and row["type"] == "llm_exchange"
        assert row["run_id"] == run_id and row["frame_id"]
        assert row["model"] == "mock/fib"
        assert isinstance(row["ts"], (int, float))
        messages = row["request"]["messages"]
        assert messages and messages[0]["role"] == "system", "请求报文含完整消息列"
        # checkpoint _message_to_dict 同形状:8 键齐全,空 parts 不落键
        for msg in messages:
            assert set(msg) == {
                "role", "content", "tool_calls", "tool_call_id",
                "name", "reasoning", "source", "meta",
            }
        response = row["response"]["message"]
        assert response["role"] == "assistant"
        usage = row["usage"]
        assert usage["prompt"] == 1 and usage["completion"] == 1 and "cost" in usage

    # 与 WAL 逐字对照:每个 pre:llm.request 的 messages 与 RL 行的 request.messages 相等
    wal = [
        json.loads(line)
        for line in (tdir / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    wal_pres = [
        row["payload"]["messages"]
        for row in wal
        if row.get("type") == "signal" and row.get("name") == PRE_LLM_REQUEST
    ]
    assert wal_pres == [r["request"]["messages"] for r in rows]
    wal_posts = [
        row["payload"]["message"]
        for row in wal
        if row.get("type") == "signal" and row.get("name") == POST_LLM_RESPONSE
    ]
    assert wal_posts == [r["response"]["message"] for r in rows]
    # 配置接线下 pre/post payload 的键集 = legacy 键集 + messages/message(additive)
    pre = next(s for s in seen if s.name == PRE_LLM_REQUEST)
    assert set(pre.payload) == {"depth", "frame_id", "skill", "model", "messages"}
    post = next(s for s in seen if s.name == POST_LLM_RESPONSE)
    assert set(post.payload) == {"depth", "frame_id", "skill", "model", "usage", "message"}


# ---------------------------------------------------------------------------
# 压缩链 run:compress 源过滤(保真边界见模块 docstring)
# ---------------------------------------------------------------------------


def test_compress_chain_source_filtered(tmp_path):
    """压缩链真实触发的 run:压缩排干补发的 post:llm.response(source="compress")
    不成行;主循环交换照出,行内报文是压缩后实发线报(保真边界:压缩前原始上下文
    由 WAL pre/post:compress + checkpoint 重建,不在 RL 行内)。"""
    reset_rl_chatty_brain()
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text(textwrap.dedent(CHATTY_YAML), encoding="utf-8")
    tdir = tmp_path / "traces"
    rl_path = tmp_path / "rl.jsonl"
    kernel = build_kernel(
        {
            "run": {"model": "mock/chatty"},
            "providers": {
                "mock": {"brain": "tests.telemetry.test_rl_exporter:rl_chatty_brain"}
            },
            "tools": {"python_exec": "subprocess"},
            "skills": {"path": str(skills_yaml)},
            "telemetry": {"dir": str(tdir), "rl_export": {"path": str(rl_path)}},
        }
    )
    seen = record_all(kernel)

    assert asyncio.run(kernel.run("chatty", {})) == {"answer": "done"}
    asyncio.run(kernel.telemetry.close())

    responses = [s for s in seen if s.name == POST_LLM_RESPONSE]
    main = [s for s in responses if s.payload.get("source") != "compress"]
    compress = [s for s in responses if s.payload.get("source") == "compress"]
    assert len(main) == 4 and compress, "压缩真实触发:主循环 4 次 + 摘要补发 >= 1 次"

    rows = _rows(rl_path)
    assert len(rows) == 4, "compress 源补发不成行;行数恰为主循环交换数"
    assert [r["seq"] for r in rows] == [1, 2, 3, 4]
    for row in rows:
        # 行内是主循环请求的实发线报(pinned 系统提示永驻,压缩不变量 1),
        # 不是压缩器自己的摘要请求
        assert "上下文压缩器" not in row["request"]["messages"][0]["content"]
        assert "闲聊任务" in row["request"]["messages"][0]["content"]


# ---------------------------------------------------------------------------
# redact:RL 行与 WAL 同一份脱敏副本(train-on-redacted)
# ---------------------------------------------------------------------------


def test_redact_on_rows_redacted(tmp_path):
    """redact=True:技能 prompt 里的邮箱在 RL 行(与 WAL 同路径)脱敏为 [EMAIL]。"""
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text(textwrap.dedent(PII_YAML), encoding="utf-8")
    rl_path = tmp_path / "rl.jsonl"
    kernel = build_kernel(
        {
            "run": {"model": "mock/pii", "compression": "off"},
            "providers": {"mock": {"brain": "tests.telemetry.test_rl_exporter:rl_pii_brain"}},
            "skills": {"path": str(skills_yaml)},
            "telemetry": {
                "dir": str(tmp_path / "traces"),
                "redact": True,
                "rl_export": {"path": str(rl_path)},
            },
        }
    )
    asyncio.run(kernel.run("pii_demo", {}))
    asyncio.run(kernel.telemetry.close())

    blob = rl_path.read_text(encoding="utf-8")
    assert "[EMAIL]" in blob and "admin@corp.com" not in blob
    (row,) = _rows(rl_path)
    system = row["request"]["messages"][0]
    assert system["content"].count("[EMAIL]") == 1


# ---------------------------------------------------------------------------
# 未配对/乱序/legacy 流量:计数跳过,绝不抛
# ---------------------------------------------------------------------------


def test_unpaired_and_legacy_signals_never_raise(tmp_path):
    rl_path = tmp_path / "rl.jsonl"
    exporter = RlTrajectoryExporter(str(rl_path))

    async def main():
        # 孤儿 post(带 message 但无缓冲 pre)→ _skipped
        await exporter.export(_sig(POST_LLM_RESPONSE, payload={"model": "m", "message": {"role": "assistant"}}))
        # pre 缺 messages 键(_rl_capture 未开的 legacy 流量)→ 忽略,不计数
        await exporter.export(_sig(PRE_LLM_REQUEST, payload={"model": "m"}))
        # post 缺 message 键(legacy 流量)→ 忽略,不计数
        await exporter.export(_sig(POST_LLM_RESPONSE, payload={"model": "m", "usage": {}}))
        # compress 源补发(即便带 message 键) → 过滤,不计数
        await exporter.export(_sig(POST_LLM_RESPONSE, payload={
            "model": "m", "source": "compress", "message": {"role": "assistant"},
        }))
        # 正常配对 → 出行
        await exporter.export(_sig(PRE_LLM_REQUEST, payload={"model": "m", "messages": [{"role": "system", "content": "s"}]}))
        await exporter.export(_sig(POST_LLM_RESPONSE, payload={
            "model": "m", "usage": {"prompt": 1, "completion": 1, "cost": 0.0},
            "message": {"role": "assistant", "content": "a"},
        }))
        # 孤儿 pre 滞留 + run 终态 → 清收入 _skipped
        await exporter.export(_sig(PRE_LLM_REQUEST, payload={"model": "m", "messages": []}))
        await exporter.export(_sig(RUN_ABORTED, frame_id=None, payload={"error": "boom"}))
        await exporter.close()
        await exporter.close()  # 幂等

    asyncio.run(main())
    assert exporter._skipped == 2, "孤儿 post 1 + 终态清收孤儿 pre 1;legacy/compress 不计"
    rows = _rows(rl_path)
    assert len(rows) == 1
    assert rows[0]["seq"] == 1 and rows[0]["response"]["message"]["content"] == "a"


def test_version_header_and_close_idempotent(tmp_path):
    """首行版本头 schema agent_os.rl-trace/1;close 幂等;close 后 export 丢弃。"""
    rl_path = tmp_path / "rl.jsonl"
    exporter = RlTrajectoryExporter(str(rl_path))
    header = rl_path.read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(header) == {"v": 1, "type": "header", "schema": "agent_os.rl-trace/1"}

    async def main():
        await exporter.close()
        await exporter.close()
        await exporter.export(_sig(PRE_LLM_REQUEST, payload={"model": "m", "messages": []}))

    asyncio.run(main())
    assert _rows(rl_path) == [], "close 后的信号不落盘"


# ---------------------------------------------------------------------------
# 未配置 run:payload 与 legacy 逐字节一致(无 messages/message 键)
# ---------------------------------------------------------------------------


def test_unconfigured_run_payloads_byte_identical(tmp_path):
    """未配 rl_export 的内核:_rl_capture 缺省 False,pre/post:llm.* payload 键集
    与 legacy 逐字节一致(additive 守卫锚)。"""
    kernel = fib_kernel(telemetry_dir=tmp_path / "traces")
    assert getattr(kernel, "_rl_capture", False) is False
    seen = record_all(kernel)

    assert asyncio.run(kernel.run("demo.fib", {"n": 3})) == {"seq": [0, 1, 1]}

    pres = [s for s in seen if s.name == PRE_LLM_REQUEST]
    posts = [s for s in seen if s.name == POST_LLM_RESPONSE]
    assert len(pres) == 4 and len(posts) == 4
    for sig in pres:
        assert set(sig.payload) == {"depth", "frame_id", "skill", "model"}
        assert "messages" not in sig.payload
    for sig in posts:
        assert set(sig.payload) == {"depth", "frame_id", "skill", "model", "usage"}
        assert "message" not in sig.payload
