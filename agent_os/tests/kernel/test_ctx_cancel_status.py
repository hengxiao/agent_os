"""LogicContext 帧控制面锚点测试(W5-WS1:``ctx.cancel`` / ``ctx.frame_status``)。

固定约定:

- ``ctx.cancel(frame_id, reason="") -> list[str]``:§5.2 子树级联取消的 §3.4 编排面
  (委托 ``kernel.cancel_subtree``)——目标帧及后代进入终态,**不**杀 run;
  ack 幂等,返回含目标自身的全部取消 id,未知帧返回空表;
- ``ctx.frame_status(frame_id) -> dict``:``{"frame_id", "status", "skill", "usage"}``——
  status 为 FrameStatus 值,usage 为 ``kernel.subtree_usage`` 子树汇总九字段 dict;
  未知帧返回 ``status=None``(skill=None、usage 零值)的同形字典,不抛异常;
- SANDBOX 档经 syscall 桥(kind=``cancel``/``frame_status``)调到同两方法,
  桥接端到端测试见 tests/logic/test_orchestration.py。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import textwrap

from agent_os.api.v1 import (
    ChatRequest,
    ChatResponse,
    ChatUsage,
    FrameStatus,
    Message,
    Permission,
    Role,
    RunConfig,
    ToolCall,
    ToolPolicy,
    Usage,
)
from tests.helpers.kernels import assemble, auto_approve, sandbox_tools

SKILLS_YAML = """
skills:
  - name: test.cancel_probe
    version: 1.0.0
    kind: code
    handler: tests.helpers.code_skills:cancel_and_probe
    inputs:
      type: object
      properties: { skill: { type: string }, args: { type: object } }
      required: [skill]
    outputs: { type: object }
    permissions: { tools: [], skills: [test.slow_echo] }
  - name: test.slow_echo
    version: 1.0.0
    kind: prompt
    description: 调一个慢工具再给最终答案(ctx.cancel 命中在跑子帧的时间窗)。
    inputs: { type: object, properties: {} }
    outputs:
      type: object
      properties: { done: { type: boolean } }
      required: [done]
    permissions: { tools: [test.slow_tool], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 4, timeout: 60 }
    prompt: 先调 test.slow_tool,再回答。
"""

#: Usage 九字段(§14.1 冻结清单;frame_status 的 usage 子树汇总形态锚)
USAGE_FIELDS = {
    "steps",
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "thinking_tokens",
    "cost",
    "ttft_ms",
    "total_ms",
}


def slow_brain(req: ChatRequest) -> ChatResponse:
    """首步调 test.slow_tool(秒级睡眠),拿到工具结果后给最终答案。"""
    if not any(m.role is Role.TOOL for m in req.messages):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="slow-1", name="test.slow_tool", args={"seconds": 3})],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"done": True})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _slow_tools():
    """注册了 test.slow_tool(async 睡眠)的工具注册表。"""
    reg = sandbox_tools()

    @reg.tool(name="test.slow_tool", permission=Permission.READ, timeout=30)
    async def slow_tool(seconds: int) -> str:
        """睡眠 seconds 秒后返回(ctx.cancel 命中在跑子帧的时间窗)。"""
        await asyncio.sleep(seconds)
        return "slept"

    return reg


def _build(tmp_path):
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(SKILLS_YAML), encoding="utf-8")
    return assemble(
        config,
        slow_brain,
        p,
        tools=_slow_tools(),
        # spawn 升权闸(docs/ESCALATION.md §3):自动批准通道等价于生产宿主里人每次放行
        supervisor=auto_approve,
    )


def _root_frame(kernel):
    return next(f for f in kernel.stack.tree() if f.parent_id is None)


def test_ctx_cancel_spawned_child(tmp_path):
    """code 技能经 ``ctx.cancel`` 取消其 spawn 的子帧:wait 收到 SubtreeCancelled
    (捕获后优雅收尾,**run 存活**);子帧 FAILED 终态(CancelledError 配对路径);
    ack 含目标自身(子帧无后代,恰为单元表)。"""
    kernel = _build(tmp_path)
    result = asyncio.run(kernel.run("test.cancel_probe", {"skill": "test.slow_echo"}))

    assert result["cancelled"] is True, "wait_frame 应对被取消的子帧上抛 SubtreeCancelled"
    fid = result["frame_id"]
    assert result["ack"] == [fid]
    child = kernel.stack.get(fid)
    assert child is not None and child.status is FrameStatus.FAILED
    assert isinstance(child.error, asyncio.CancelledError), "后台子帧应被 task.cancel() 终结"
    # run 存活:结果正常返回,根帧 DONE 终态
    assert _root_frame(kernel).status is FrameStatus.DONE


def test_ctx_frame_status_three_shapes(tmp_path):
    """``ctx.frame_status`` 三形态:running(运行中)/failed(终态)/未知帧(防御式)。

    附内核侧锚:run 结束后根帧为 done 形态;usage 与 ``kernel.subtree_usage``
    逐字段一致(读视图委托关系),未知帧 usage 为零值九字段。
    """
    kernel = _build(tmp_path)
    result = asyncio.run(kernel.run("test.cancel_probe", {"skill": "test.slow_echo"}))
    fid = result["frame_id"]

    running = result["running"]
    assert running, "轮询应在取消前观察到 running 形态(slow_tool 3s 时间窗)"
    assert running["frame_id"] == fid and running["status"] == "running"
    assert running["skill"] == "test.slow_echo"
    assert set(running["usage"]) == USAGE_FIELDS

    terminal = result["terminal"]
    assert terminal["status"] == "failed" and terminal["skill"] == "test.slow_echo"
    assert terminal["usage"] == dataclasses.asdict(kernel.subtree_usage(fid))

    unknown = result["unknown"]
    assert unknown["frame_id"] == "no-such-frame"
    assert unknown["status"] is None and unknown["skill"] is None
    assert unknown["usage"] == dataclasses.asdict(Usage())

    # done 形态 + 内核侧读视图(frame_status_payload 是 syscall 桥共用数据源)
    root = _root_frame(kernel)
    payload = kernel.frame_status_payload(root.frame_id)
    assert payload["status"] == "done" and payload["skill"] == "test.cancel_probe"
    assert payload["usage"] == dataclasses.asdict(kernel.subtree_usage(root.frame_id))
    assert kernel.frame_status_payload("no-such-frame")["status"] is None
