"""S1 锚点测试:supervisor 内核机制(docs/SUPERVISOR.md v2 §2/§9)。

固定约定:

- `ask_supervisor` 是内核拦截式伪工具;manifest 须声明 `permissions.tools: [ask_supervisor]`;
- `KernelBuilder.supervisor(handler)` 注入调用方 handler
  (``async def handler(question: Question) -> Answer``;Question 含
  question_id/question/context/options/urgency/frame_id/run_id;
  Answer 含 answer/decided_by);
- 提问帧就地挂起直到回答或超时;回答作为该 ask 的 tool result 写回,帧重入 loop;
- 超时策略:`on_timeout = "fail" | "default_answer"`;
- options 不匹配:subsystem 以 `previous_error` 重问调用方;
- pending ask 以 `frame.context.working["_pending_ask"]` 入 checkpoint。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path

import pytest

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    ToolCall,
    ToolPolicy,
)
from agent_os.kernel.errors import RunAborted
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

SKILLS_YAML = """
skills:
  - name: expense_report
    version: 1.0.0
    kind: prompt
    description: 报销审批。Use when 需要上级批准。
    inputs:
      type: object
      properties: { amount: { type: integer } }
      required: [amount]
    outputs:
      type: object
      properties: { decision: { type: string } }
      required: [decision]
    permissions:
      tools: [ask_supervisor]
      skills: []
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 6 }
    prompt: |
      你是报销审批员。大额报销必须经 ask_supervisor 请求上级裁决,并按裁决回答。
"""


def _yaml(tmp_path: Path) -> str:
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    return str(p)


def expense_brain(req: ChatRequest) -> ChatResponse:
    """大额报销先 ask_supervisor,再按裁决输出 decision。"""
    asked = any(
        tc.name == "ask_supervisor"
        for m in req.messages if m.role is Role.ASSISTANT for tc in m.tool_calls
    )
    if not asked:
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="c1", name="ask_supervisor", args={
                    "question": "批准 ¥5000 报销吗?",
                    "context": {"amount_cents": 500000},
                    "options": ["approve", "reject"],
                })],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    result = json.loads(
        next(m.content for m in reversed(req.messages) if m.role is Role.TOOL)
    )
    if not result["ok"]:
        return ChatResponse(
            message=Message(role=Role.ASSISTANT, content=json.dumps({"decision": "fallback_defer"})),
            finish_reason="stop",
            usage=ChatUsage(prompt=1, completion=1),
        )
    answer = result["value"]["answer"]
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"decision": answer})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _build(tmp_path, handler, *, on_timeout: str = "fail", timeout_s: float = 120.0):
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    builder = (
        KernelBuilder(config)
        .providers(MockProvider(expense_brain))
        .tools(LocalPythonToolRegistry())
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .supervisor(handler, timeout_s=timeout_s, on_timeout=on_timeout)
    )
    return builder.build()


# ---------------------------------------------------------------------------
# handler 闭环
# ---------------------------------------------------------------------------


def test_handler_receives_question_and_answer_flows_back(tmp_path):
    """子帧 ask → handler 收到完整问题 → 回答 → 子帧以答案为 tool result 恢复。"""
    received = []

    async def handler(question):
        received.append(question)
        return {"answer": "approve", "decided_by": "test-handler"}

    kernel = _build(tmp_path, handler)
    result = asyncio.run(kernel.run("expense_report", {"amount": 5000}))

    assert result == {"decision": "approve"}
    assert len(received) == 1
    q = received[0]
    assert q.question == "批准 ¥5000 报销吗?"
    assert q.context == {"amount_cents": 500000}
    assert q.options == ["approve", "reject"]
    assert q.frame_id and q.run_id


def test_frame_suspends_until_answer(tmp_path):
    """就地挂起:handler 延迟回答期间 run 不得完成;回答后才闭环。"""
    gate = asyncio.Event()

    async def handler(question):
        await gate.wait()
        return {"answer": "reject", "decided_by": "test-handler"}

    kernel = _build(tmp_path, handler)

    async def main():
        task = asyncio.create_task(kernel.run("expense_report", {"amount": 5000}))
        await asyncio.sleep(0.2)
        assert not task.done(), "handler 未回答前 run 必须挂起"
        gate.set()
        return await task

    result = asyncio.run(main())
    assert result == {"decision": "reject"}


def test_pending_ask_blocks_run_completion(tmp_path):
    """run 完成判定:pending ask 存在时 run 不得判完成(挂起期间状态非 done)。"""
    gate = asyncio.Event()
    statuses = []

    async def handler(question):
        await gate.wait()
        return {"answer": "approve", "decided_by": "test-handler"}

    kernel = _build(tmp_path, handler)

    async def main():
        task = asyncio.create_task(kernel.run("expense_report", {"amount": 5000}))
        await asyncio.sleep(0.2)
        for run in kernel._runs.values():
            statuses.append(run.state.status.value)
        gate.set()
        await task
        return statuses

    statuses = asyncio.run(main())
    assert statuses and statuses[0] != "done"


# ---------------------------------------------------------------------------
# 超时与兜底
# ---------------------------------------------------------------------------


def test_timeout_fail_returns_retryable_error(tmp_path):
    """on_timeout=fail:子帧收到结构化错误(retryable),可降级继续。"""

    async def slow_handler(question):
        await asyncio.sleep(5)
        return {"answer": "approve", "decided_by": "test-handler"}

    kernel = _build(tmp_path, slow_handler, on_timeout="fail", timeout_s=0.1)
    result = asyncio.run(kernel.run("expense_report", {"amount": 5000}))
    assert result == {"decision": "fallback_defer"}


def test_timeout_default_answer(tmp_path):
    """on_timeout=default_answer:超时以兜底答案闭环,decided_by 标 policy。"""
    seen_decided_by = []

    async def slow_handler(question):
        await asyncio.sleep(5)
        return {"answer": "approve", "decided_by": "test-handler"}

    class SpyBrain:
        def __call__(self, req):
            resp = expense_brain(req)
            for m in reversed(req.messages):
                if m.role is Role.TOOL:
                    payload = json.loads(m.content)
                    if payload.get("ok"):
                        seen_decided_by.append(payload["value"].get("decided_by"))
                    break
            return resp

    kernel = (
        KernelBuilder(RunConfig(model="mock/x", tool_policy=ToolPolicy(max_permission=Permission.EXEC), compression="off"))
        .providers(MockProvider(SpyBrain()))
        .tools(LocalPythonToolRegistry())
        .skills(LocalFileSkillRegistry(_yaml(tmp_path)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .supervisor(slow_handler, timeout_s=0.1, on_timeout="default_answer", default_answer="approve")
        .build()
    )
    result = asyncio.run(kernel.run("expense_report", {"amount": 5000}))
    assert result == {"decision": "approve"}
    assert seen_decided_by and seen_decided_by[0] == "policy:default"


# ---------------------------------------------------------------------------
# options 校验
# ---------------------------------------------------------------------------


def test_options_validation_reasks_caller(tmp_path):
    """答案不在 options 内:subsystem 带 previous_error 重问,直至合法。"""
    attempts = []

    async def flaky_handler(question):
        attempts.append(getattr(question, "previous_error", None))
        if len(attempts) == 1:
            return {"answer": "maybe"}
        return {"answer": "approve"}

    kernel = _build(tmp_path, flaky_handler)
    result = asyncio.run(kernel.run("expense_report", {"amount": 5000}))
    assert result == {"decision": "approve"}
    assert len(attempts) == 2
    assert attempts[0] is None and attempts[1], "第二次重问必须带 previous_error"


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------


def test_permission_denied_without_declaration(tmp_path):
    """manifest 未声明 ask_supervisor → PERMISSION_DENIED 错误观察。"""
    yaml_text = SKILLS_YAML.replace("tools: [ask_supervisor]", "tools: []")
    (tmp_path / "skills.yaml").write_text(yaml_text, encoding="utf-8")
    seen = []

    async def handler(question):
        return {"answer": "approve"}

    kernel = (
        KernelBuilder(RunConfig(model="mock/x", tool_policy=ToolPolicy(max_permission=Permission.EXEC), compression="off"))
        .providers(MockProvider(expense_brain))
        .tools(LocalPythonToolRegistry())
        .skills(LocalFileSkillRegistry(str(tmp_path / "skills.yaml")))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
        .supervisor(handler)
        .build()
    )
    mock = kernel.providers.providers["mock"]
    asyncio.run(kernel.run("expense_report", {"amount": 5000}))
    tool_msgs = [
        json.loads(m.content)
        for req in mock.recorded for m in req.messages if m.role is Role.TOOL
    ]
    assert tool_msgs and tool_msgs[0]["ok"] is False
    assert tool_msgs[0]["error"]["kind"] == "permission_denied"


# ---------------------------------------------------------------------------
# checkpoint:pending ask 入档与 resume 重新提问
# ---------------------------------------------------------------------------


def test_checkpoint_pending_ask_and_resume_reasks(tmp_path):
    """挂起期间断电 → checkpoint 含 _pending_ask → resume 重新提问 → 回答闭环。"""
    calls = {"n": 0}

    async def crashing_handler(question):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RunAborted("模拟断电")
        return {"answer": "approve", "decided_by": "test-handler"}

    kernel1 = _build(tmp_path, crashing_handler)
    seen = []

    async def rec(sig):
        seen.append(sig)

    kernel1.signals.subscribe("run.started", rec)

    async def first_run():
        with pytest.raises(RunAborted):
            await kernel1.run("expense_report", {"amount": 5000})

    asyncio.run(first_run())
    run_id = seen[0].run_id

    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run_id, str(ckpt))
    data = json.loads(ckpt.read_text(encoding="utf-8"))
    assert any(
        f["context"].get("working", {}).get("_pending_ask") for f in data["frames"]
    ), "checkpoint 必须含 _pending_ask"

    kernel2 = _build(tmp_path, crashing_handler)
    result = asyncio.run(kernel2.resume(str(ckpt)))
    assert result == {"decision": "approve"}
    assert calls["n"] == 2, "resume 必须重新向调用方提问"


# ---------------------------------------------------------------------------
# 配对原子性
# ---------------------------------------------------------------------------


def test_pairing_invariant_through_suspend_resume(tmp_path):
    """挂起/恢复全程无孤儿 tool result(§7.4 不变量 2)。"""

    async def handler(question):
        return {"answer": "approve", "decided_by": "test-handler"}

    kernel = _build(tmp_path, handler)
    mock = kernel.providers.providers["mock"]
    asyncio.run(kernel.run("expense_report", {"amount": 5000}))

    for req in mock.recorded:
        call_ids, result_ids = set(), set()
        for m in req.messages:
            if m.role is Role.ASSISTANT:
                call_ids.update(tc.id for tc in m.tool_calls)
            elif m.role is Role.TOOL and m.tool_call_id:
                result_ids.add(m.tool_call_id)
        assert call_ids == result_ids, f"配对阵损坏: {call_ids ^ result_ids}"


# ---------------------------------------------------------------------------
# 信号 channel 标签(S3,§5)
# ---------------------------------------------------------------------------


def test_signal_channel_label_default_handler(tmp_path):
    """supervisor.ask 信号 payload 带 channel:嵌入方 handler 缺省标 "handler"。"""
    asks = []

    async def rec(sig):
        asks.append(sig)

    async def handler(question):
        return {"answer": "approve", "decided_by": "test-handler"}

    kernel = _build(tmp_path, handler)
    kernel.signals.subscribe("supervisor.ask", rec)
    asyncio.run(kernel.run("expense_report", {"amount": 5000}))

    assert len(asks) == 1
    assert asks[0].payload["channel"] == "handler"


def test_signal_channel_label_from_handler_attribute(tmp_path):
    """handler 带 supervisor_channel 属性时按属性标注(inbox|cli 宿主通道的标记方式)。"""
    asks = []

    async def rec(sig):
        asks.append(sig)

    async def handler(question):
        return {"answer": "approve", "decided_by": "test-handler"}

    handler.supervisor_channel = "inbox"  # InboxChannel/CLI 协议的同款标法
    kernel = _build(tmp_path, handler)
    kernel.signals.subscribe("supervisor.ask", rec)
    asyncio.run(kernel.run("expense_report", {"amount": 5000}))

    assert len(asks) == 1
    assert asks[0].payload["channel"] == "inbox"


def test_host_channels_declare_labels():
    """宿主默认通道自报标签:Web 收件箱 = inbox,CLI 协议 = cli(S3,§5)。"""
    from agent_os.host.cli.main import _cli_supervisor
    from agent_os.supervisor import InboxChannel

    assert InboxChannel().supervisor_channel == "inbox"
    assert _cli_supervisor.supervisor_channel == "cli"
