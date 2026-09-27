"""工具分发错误兜底锚点测试(WS4)。

固定约定:

- 模型生成的非法参数(如 ``python_orchestrate`` 的 ``timeout="abc"``)折为
  结构化错误观察(kind=invalid_args)回写帧上下文,**不炸 run**;
- 分发前置阶段的意外异常(如 workdir 解析抛错)被工具循环兜底归一化为
  kind=internal 的错误观察,run 存活、配对原子性不破坏(§7.4 不变量 2);
- 硬失败(RunAborted 及其子类/MaxDepthExceeded,§3.2)不被兜底吞掉,
  照常弹栈到 Run 边界。
"""

from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from agent_os.api.v1 import (
    ORCHESTRATE_TOOL,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    Message,
    Mode,
    Permission,
    Role,
    RunConfig,
    Stop,
    ToolCall,
    ToolPolicy,
)
from agent_os.kernel.errors import RunAborted
from agent_os.runtime.config import build_kernel
from tests.helpers.kernels import assemble, sandbox_tools

SKILLS_YAML = """
skills:
  - name: test.driver
    version: 1.0.0
    kind: prompt
    inputs: { type: object, properties: {} }
    outputs: { type: object }
    permissions: { tools: [python_orchestrate, system.file.read], skills: [] }
    model: { prefer: ["mock/x"] }
    limits: { max_steps: 10, max_tool_calls: 20 }
    prompt: DRIVER
"""


def _yaml(tmp_path, body: str = SKILLS_YAML):
    p = tmp_path / "skills.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def _final(payload) -> ChatResponse:
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps(payload)),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


def _tool_then_final(call: ToolCall):
    """首轮发 ``call``,之后把末条 tool result 的 ok/kind 作为最终答案回传。"""

    def brain(req: ChatRequest) -> ChatResponse:
        tool_msgs = [m for m in req.messages if m.role is Role.TOOL]
        if not tool_msgs:
            return ChatResponse(
                message=Message(role=Role.ASSISTANT, tool_calls=[call]),
                finish_reason="tool_calls",
                usage=ChatUsage(prompt=1, completion=1),
            )
        payload = json.loads(tool_msgs[-1].content)
        return _final({"ok": payload["ok"], "kind": payload["error"]["kind"]})

    return brain


#: build_kernel 的 dotted path 需可 import,故 brain 提到模块级
bad_timeout_brain = _tool_then_final(
    ToolCall(
        id="c1",
        name=ORCHESTRATE_TOOL,
        args={"code": "result = 1", "timeout": "abc"},
    )
)


def test_orchestrate_bad_timeout_is_error_observation(tmp_path):
    """``[tools] python_orchestrate`` 开启下,timeout="abc" → invalid_args,run 不炸。"""
    kernel = build_kernel(
        {
            "run": {"model": "mock/x", "compression": "off"},
            "providers": {"mock": {"brain": "tests.kernel.test_dispatch_errors:bad_timeout_brain"}},
            "tools": {"python_orchestrate": True, "python_exec": "subprocess", "builtins": True},
            "skills": {"path": str(_yaml(tmp_path))},
        }
    )
    out = asyncio.run(kernel.run("test.driver", {}))
    assert out == {"ok": False, "kind": "invalid_args"}

    mock = kernel.providers.providers["mock"]
    tool_msgs = [m for req in mock.recorded for m in req.messages if m.role is Role.TOOL]
    payload = json.loads(tool_msgs[-1].content)
    assert payload["ok"] is False and payload["error"]["kind"] == "invalid_args"
    assert "timeout" in payload["error"]["message"]


def test_dispatch_pre_stage_crash_becomes_internal_error(tmp_path):
    """workdir 解析抛错(ToolDispatchContext 构造前置阶段)→ internal 错误观察,run 存活。"""
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
        workdir=123,  # type: ignore[arg-type]  # Path(123) 抛 TypeError,模拟分发前置崩溃
    )
    kernel = assemble(
        config,
        _tool_then_final(ToolCall(id="c1", name="system.file.read", args={"path": "x.txt"})),
        _yaml(tmp_path),
        tools=sandbox_tools(builtins=True),
    )
    out = asyncio.run(kernel.run("test.driver", {}))
    assert out == {"ok": False, "kind": "internal"}


class _HardStop:
    """SYNC sidecar:pre:tool.call 返回 Stop → _dispatch_call 内部抛 RunAborted。"""

    name = "hardstop"
    subscriptions = ("pre:tool.call",)
    mode = Mode.SYNC
    priority = 10
    needs_free_text = False

    async def on_signal(self, sig, ctl):
        return Stop("测试:硬停止")


def test_hard_failure_not_swallowed_by_dispatch_fallback(tmp_path):
    """回归锚:兜底不得吞掉硬失败(§3.2)——Stop 仍弹栈到 Run 边界。"""
    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = assemble(
        config,
        _tool_then_final(ToolCall(id="c1", name="system.file.read", args={"path": "x.txt"})),
        _yaml(tmp_path),
        tools=sandbox_tools(builtins=True),
        sidecars=(_HardStop(),),
    )
    with pytest.raises(RunAborted, match="硬停止"):
        asyncio.run(kernel.run("test.driver", {}))


def test_subtree_cancelled_not_swallowed_nor_run_abort(tmp_path):
    """帧边界语义(WS1):SubtreeCancelled 不被工具循环兜底吞成 INTERNAL
    (在 re-raise 名单里),也不升级为 run 中止(不是 RunAborted,run 状态
    是 failed 而非 aborted)。

    直接锚定 runner 工具循环的 re-raise 分支:工具层抛出的 Exception 会被
    registry 归一化为结构化错误(§8.1),到达不了该分支;故在分发边界注入。
    """
    from agent_os.api.v1 import RunStatus
    from agent_os.kernel.errors import SubtreeCancelled

    config = RunConfig(
        model="mock/x",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = assemble(
        config,
        _tool_then_final(ToolCall(id="c1", name="system.file.read", args={"path": "x.txt"})),
        _yaml(tmp_path),
        tools=sandbox_tools(builtins=True),
    )

    async def _boom_dispatch(call, frame, manifest):
        raise SubtreeCancelled("测试:子树取消")

    kernel._dispatch_call = _boom_dispatch  # type: ignore[method-assign]
    with pytest.raises(SubtreeCancelled, match="子树取消"):
        # 若被兜底吞掉,run 会以 {"ok": False, "kind": "internal"} 正常收尾而不抛
        asyncio.run(kernel.run("test.driver", {}))
    run = next(iter(kernel._runs.values()))
    assert run.state.status is RunStatus.FAILED, "不得升级为 run 中止(ABORTED)"
