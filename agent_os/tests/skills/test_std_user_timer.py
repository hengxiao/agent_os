"""WS1 锚点测试:std/user · std/task 用户交互与定时技能(§8.3 + system.timer.set)。

固定约定:

- ``common.user.ask_human``(code):``system.user.ask`` 的薄适配——context 拼进
  问题文本;未装配 user 通道 → 工具 NOT_FOUND → handler 上抛(run failed);
- ``common.task.set_timer``(code):``system.timer.set`` 的薄适配——参数透传,
  返回 ``{timer_id}``;run 收尾自动取消在册计时器(进程态,不持久化);
- 两技能的 description 门槛(Use when / Do not use when)与 schema 结构由
  tests/test_std_gate.py 自动遍历覆盖,此处只验端到端行为。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_os.api.v1 import Permission, RunConfig, ToolPolicy
from agent_os.kernel.errors import ToolDispatchError
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.logic.python_sandbox import PythonSandboxLogicKernel
from agent_os.providers.mock import MockProvider
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry

STD_DIR = Path(__file__).resolve().parents[2] / "std"


class _RecordingChannel:
    """记录式宿主用户通道(ask 返回固定答案)。"""

    def __init__(self, answer: str = "方案 B") -> None:
        self.answer = answer
        self.questions: list[str] = []
        self.notifications: list[str] = []

    async def ask(self, question: str) -> str:
        self.questions.append(question)
        return self.answer

    async def notify(self, message: str) -> None:
        self.notifications.append(message)


def _kernel(user_channel=None):
    sys.path.insert(0, str(STD_DIR))
    tools = LocalPythonToolRegistry.with_builtins()
    builder = (
        KernelBuilder(
            RunConfig(
                model="mock/x",
                tool_policy=ToolPolicy(max_permission=Permission.EXEC),
                compression="off",
            )
        )
        .providers(MockProvider(lambda req: None))
        .tools(tools)
        .skills(LocalFileSkillRegistry(str(STD_DIR)))
        .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
    )
    if user_channel is not None:
        builder.user_channel(user_channel)
    return builder.build(), tools


def test_ask_human_roundtrip_with_mock_channel():
    """ask_human 走通:问题(含 context 拼接)到达宿主通道,回答作为技能输出返回。"""
    channel = _RecordingChannel(answer="继续,选方案 B")
    kernel, _tools = _kernel(user_channel=channel)
    result = asyncio.run(
        kernel.run("common.user.ask_human", {"question": "继续吗?", "context": "已连续失败 3 次"})
    )
    assert result == {"answer": "继续,选方案 B"}
    assert len(channel.questions) == 1
    assert "继续吗?" in channel.questions[0] and "已连续失败 3 次" in channel.questions[0], (
        "context 须拼进问题文本一起呈现"
    )


def test_ask_human_without_channel_fails_structured():
    """未装配 user 通道:工具面报 NOT_FOUND,handler 上抛 → run 失败(不静默吞)。"""
    kernel, _tools = _kernel(user_channel=None)
    # 内核把 handler 异常归一为 ToolDispatchError;原文(含 not_found 归因)保留在消息里
    with pytest.raises(ToolDispatchError, match="ask_human 调用失败"):
        asyncio.run(kernel.run("common.user.ask_human", {"question": "q?"}))


def test_set_timer_returns_timer_id_and_run_teardown_cancels():
    """set_timer 走通:立即返回 timer_id;run 收尾后任务表清空(计时器不泄漏)。"""
    kernel, tools = _kernel()
    result = asyncio.run(
        kernel.run("common.task.set_timer", {"delay_seconds": 60, "note": "复查进度"})
    )
    assert result["timer_id"], "须立即返回 timer_id(计时在后台)"
    assert not tools._timers.tasks and not tools._timers._by_run, (
        "run 收尾须取消本 run 全部计时器(WS1 生命周期语义)"
    )


def test_set_timer_invalid_args_surface_as_failure():
    """缺 delay/interval:工具 INVALID_ARGS → handler 上抛 → run 失败(校验在工具层一份)。"""
    kernel, _tools = _kernel()
    with pytest.raises(ToolDispatchError, match="set_timer 调用失败"):
        asyncio.run(kernel.run("common.task.set_timer", {"note": "x"}))
