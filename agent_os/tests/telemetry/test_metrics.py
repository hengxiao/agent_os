"""MetricsCollector 锚点测试(telemetry/metrics.py;docs/DESIGN.md §10.2 metrics v1)。

固定约定:

- ``Exporter`` 契约:``export`` 纯内存计数(绝不 await IO);run 终态信号
  (run.finished/run.aborted/run.paused)finalize 并原子落盘
  ``<traces_dir>/<run_id>.metrics.json``(tmp + os.replace);
- 指标口径(全信号 derivable;详见 metrics.py 模块 docstring):
  ``llm.*`` 只计非 compress 源的 post:llm.response;``tools.*`` 是 post:tool.call
  口径(Veto/白名单拒绝不进分母);``legality_rate`` 零调用时为 None;
  ``steps`` = pre:step 计数;``frames`` = pre:frame.push 计数;
  ``escalations.granted`` 只计 decision != "deny"(拒绝由 skill.escalation.denied
  单边计数,防双计);
- 窗口语义:run.paused 也 finalize;未见本 run 任何信号的终态(resume 结算路径)
  不落盘,防零值报告覆盖 pause 窗口;
- 宿主归档:``_finalize_run`` 把 metrics.json 拷进 run 产物目录(opt-in,缺则
  静默跳过,同 _archive_trace 形但不写空文件)。

测试形态照 test_otlp_exporter.py 先例:脚本化内核 + register_exporter +
record_all + telemetry.close 排干。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    CONFIDENTIAL,
    DATA_ACCESS_DENIED,
    POST_COMPRESS,
    POST_LLM_RESPONSE,
    POST_TOOL_CALL,
    RUN_ABORTED,
    RUN_FINISHED,
    RUN_STARTED,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    DataDomain,
    Message,
    Permission,
    Principal,
    Role,
    RunConfig,
    Signal,
    ToolCall,
    ToolPolicy,
)
from agent_os.host.shared.artifacts import execute_run
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.telemetry import MetricsCollector
from agent_os.tools.builtins import python_exec_tool
from agent_os.tools.local_registry import LocalPythonToolRegistry
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import assemble, fib_kernel, record_all

MAIN_USAGE = ChatUsage(prompt=50, completion=20, cost=0.01)
SUMMARIZE_USAGE = ChatUsage(prompt=100, completion=10, cost=0.001)

METRICS_YAML = """
skills:
  - name: metrics_chatty
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { answer: { type: string } }
      required: [answer]
    permissions: { tools: [system.file.read, system.python.exec], skills: [] }
    context_policy: { max_tokens: 300, compress: summarize }
    limits: { max_steps: 12, timeout: 60 }
    model: { prefer: ["mock/metrics"] }
    prompt: "指标演练:先读文件,再跑代码,最后输出 answer 字段。"
"""


def _sig(name: str, run_id: str = "r1", frame_id: str | None = "f1", payload: dict | None = None) -> Signal:
    return Signal(name=name, run_id=run_id, frame_id=frame_id, payload=payload or {})


def _metrics_brain(state: dict[str, int]):
    """双路 mock 大脑:摘要请求(SYSTEM 含压缩器指令)回笔记;主循环第 1 步调
    system.file.read(数据域拒绝),第 2 步调 system.python.exec 大输出(撑爆
    300 token cap 触发压缩),第 3 步回最终答案。"""

    def brain(req: ChatRequest) -> ChatResponse:
        first = (req.messages[0].content or "") if req.messages else ""
        if "上下文压缩器" in first:
            state["summarize_calls"] += 1
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, content="压缩笔记:早前往返摘要"),
                finish_reason="stop",
                usage=SUMMARIZE_USAGE,
            )
        state["main_calls"] += 1
        n = state["main_calls"]
        if n == 1:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[
                        ToolCall(id="c1", name="system.file.read", args={"path": "zone/notes.txt"})
                    ],
                ),
                finish_reason="tool_calls",
                usage=MAIN_USAGE,
            )
        if n == 2:
            return ChatResponse(
                message=Message(
                    role=Role.ASSISTANT,
                    tool_calls=[
                        ToolCall(
                            id="c2",
                            name="system.python.exec",
                            args={"code": "print('x' * 1200)"},
                        )
                    ],
                ),
                finish_reason="tool_calls",
                usage=MAIN_USAGE,
            )
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"answer": "done"})),
            finish_reason="stop",
            usage=MAIN_USAGE,
        )

    return brain


# ---------------------------------------------------------------------------
# 脚本化 run:工具错误 + 数据拒绝 + 压缩 → 计数器逐项正确
# ---------------------------------------------------------------------------


def test_scripted_run_counters(tmp_path):
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text(textwrap.dedent(METRICS_YAML), encoding="utf-8")
    tools = LocalPythonToolRegistry.with_builtins()
    tools.register(python_exec_tool(PythonSandboxLogicKernel()))
    # confidential 数据域覆盖 workdir 下的 zone/;PUBLIC 身份的 file.read 必被拒
    tools.register_fs_domain(
        DataDomain(name="fs.zone", sensitivity=CONFIDENTIAL), tmp_path / "zone"
    )
    config = RunConfig(
        model="mock/metrics",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        # 不设 compression="off":RunConfig 消融档会短路一切压缩(_cap 返回 None),
        # 缺省档下 manifest context_policy.compress=summarize 生效
        workdir=str(tmp_path),
    )
    state = {"main_calls": 0, "summarize_calls": 0}
    tdir = tmp_path / "traces"
    kernel = assemble(config, _metrics_brain(state), str(skills_yaml), tools=tools, telemetry_dir=tdir)
    collector = MetricsCollector(str(tdir))
    kernel.telemetry.register_exporter(collector)
    seen = record_all(kernel)
    principal = Principal(subject="user:bob", issuer="test", attrs={"clearance": "public"})

    result = asyncio.run(kernel.run("metrics_chatty", {}, principal=principal))
    assert result == {"answer": "done"}
    asyncio.run(kernel.telemetry.close())

    run_id = next(s for s in seen if s.name == RUN_STARTED).run_id
    # 信号面 sanity:数据拒绝 1 次、工具调用 2 次(1 拒 1 成)、压缩 1 次、
    # post:llm.response 共 4 条(3 主循环 + 1 压缩补发)
    assert len([s for s in seen if s.name == DATA_ACCESS_DENIED]) == 1
    tool_posts = [s for s in seen if s.name == POST_TOOL_CALL]
    assert [s.payload["ok"] for s in tool_posts] == [False, True]
    assert len([s for s in seen if s.name == POST_COMPRESS]) == 1
    responses = [s for s in seen if s.name == POST_LLM_RESPONSE]
    assert len(responses) == 4
    assert len([s for s in responses if s.payload.get("source") == "compress"]) == 1

    path = tdir / f"{run_id}.metrics.json"
    assert path.is_file(), "终态信号即落盘(不等 sink.close)"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["v"] == 1 and report["run_id"] == run_id
    # llm.*:压缩补发不入账(3 次主循环 × MAIN_USAGE)
    assert report["llm"]["calls"] == 3
    assert report["llm"]["prompt_tokens"] == 150
    assert report["llm"]["completion_tokens"] == 60
    assert report["llm"]["cost"] == pytest.approx(0.03)
    # tools.*:post:tool.call 口径(数据拒绝进了分发层,计分母;legality 1/2)
    assert report["tools"] == {
        "calls": 2, "ok": 1, "errors": 1, "legality_rate": pytest.approx(0.5)
    }
    assert report["data_denied"] == 1
    assert report["steps"] == 3, "pre:step 口径:每帧每步恰好一次(压缩不加步)"
    assert report["frames"] == 1
    assert report["compress"]["count"] == 1
    assert report["compress"]["evicted"] >= 1
    assert report["compress"]["strategies"] == {"summarize": 1}
    assert report["escalations"] == {"granted": 0, "denied": 0}
    assert report["supervisor"] == {"asks": 0, "timeouts": 0}
    assert report["budget"] == {"warnings": 0, "exceeded": 0}
    assert report["run"] == {"status": "done"}
    # report() 访问器与落盘件一致
    assert collector.report(run_id) == report
    assert collector.report("ghost") is None


# ---------------------------------------------------------------------------
# 终态 finalize 语义:首终态落定;重复/零信号终态不重写;close 幂等
# ---------------------------------------------------------------------------


def test_terminal_finalize_semantics(tmp_path):
    collector = MetricsCollector(str(tmp_path))

    async def main():
        await collector.export(_sig(RUN_STARTED, frame_id=None, payload={"skill": "s"}))
        await collector.export(_sig(POST_LLM_RESPONSE, payload={
            "model": "m", "usage": {"prompt": 5, "completion": 3, "cost": 0.5},
        }))
        # compress 源补发:不计 llm.*
        await collector.export(_sig(POST_LLM_RESPONSE, payload={
            "model": "m", "source": "compress", "usage": {"prompt": 9, "completion": 9, "cost": 9.0},
        }))
        await collector.export(_sig(RUN_ABORTED, frame_id=None, payload={"error": "boom"}))
        # 重复终态:窗口已 finalize,不重写(首个终态落定)
        await collector.export(_sig(RUN_FINISHED, frame_id=None, payload={}))
        # 零信号 run 的终态(resume 结算路径):不落盘
        await collector.export(_sig(RUN_FINISHED, run_id="ghost", frame_id=None, payload={}))
        await collector.close()
        await collector.close()  # 幂等
        await collector.export(_sig(RUN_STARTED, run_id="after-close", frame_id=None))  # 丢弃

    asyncio.run(main())
    report = json.loads((tmp_path / "r1.metrics.json").read_text(encoding="utf-8"))
    assert report["run"]["status"] == "aborted", "首个终态落定,后续终态不覆盖"
    assert report["llm"] == {"calls": 1, "prompt_tokens": 5, "completion_tokens": 3, "cost": 0.5}
    assert report["tools"] == {"calls": 0, "ok": 0, "errors": 0, "legality_rate": None}
    assert collector.report("r1") == report
    assert not (tmp_path / "ghost.metrics.json").exists(), "零信号窗口不落盘"
    assert not (tmp_path / "after-close.metrics.json").exists(), "close 后信号丢弃"


# ---------------------------------------------------------------------------
# 宿主归档:execute_run → run 产物目录收 metrics.json(opt-in,缺则静默跳过)
# ---------------------------------------------------------------------------


def test_execute_run_archives_metrics(tmp_path):
    tdir = tmp_path / "traces"
    kernel = fib_kernel(fib_brain, telemetry_dir=tdir)
    kernel.telemetry.register_exporter(MetricsCollector(str(tdir)))
    record = execute_run(kernel, "demo.fib", {"n": 1}, artifacts_root=tmp_path / "arts", host="test")

    run_id = record["run_id"]
    run_dir = Path(record["artifacts"]["dir"])
    archived = run_dir / "metrics.json"
    assert archived.is_file(), "_finalize_run 把 metrics.json 归档进 run 目录"
    assert json.loads(archived.read_text(encoding="utf-8")) == json.loads(
        (tdir / f"{run_id}.metrics.json").read_text(encoding="utf-8")
    )
    report = json.loads(archived.read_text(encoding="utf-8"))
    assert report["run"]["status"] == "done" and report["llm"]["calls"] == 1


def test_execute_run_without_metrics_skips_archive(tmp_path):
    """未挂 MetricsCollector:run 目录无 metrics.json,归档静默跳过(不写空文件)。"""
    kernel = fib_kernel(fib_brain, telemetry_dir=tmp_path / "traces")
    record = execute_run(kernel, "demo.fib", {"n": 1}, artifacts_root=tmp_path / "arts", host="test")
    run_dir = Path(record["artifacts"]["dir"])
    assert not (run_dir / "metrics.json").exists()
    assert (run_dir / "trace.jsonl").is_file(), "trace 归档照旧(缺则空文件)"
