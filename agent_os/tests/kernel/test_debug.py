"""内核调试原语测试(Agent OS Debugger P1;kernel/debug.py)。

固定约定:

- 断点命中即暂停:run 任务不完成,``pause_point`` 记录信号/帧/工具等快照;
  ``resume("continue")`` 放行后 run 跑完,结果与无调试一致;
- 四种步进语义在 fib 递归嵌套帧上:step_into 进子帧、step_over 不停子帧
  (同帧下一条 pre:step 或该帧 pre:frame.pop 停)、step_out 停在当前帧
  pre:frame.pop、continue 到下一断点;
- 干预:``modify_tool_args`` 经 Modify verdict 改工具参数(pre:tool.call);
  ``inject_message`` 经 ctl 注入 USER/INJECTED 消息;
- error 断点 = post:tool.call payload ok=false;stop → RunAborted;
  detach → 阻塞点自动放行,run 不悬挂。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_os.api.v1 import (
    PRE_FRAME_POP,
    PRE_STEP,
    PRE_TOOL_CALL,
    POST_TOOL_CALL,
    ChatResponse,
    ChatUsage,
    Message,
    Permission,
    Role,
    RunConfig,
    RunStatus,
    ToolCall,
    ToolPolicy,
)
from agent_os.kernel.debug import DebugController
from agent_os.kernel.errors import RunAborted
from agent_os.providers.mock import MockProvider
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import FIB_SKILLS_YAML, assemble

#: 暂停/收尾的兜底超时(秒):调试器若悬挂,测试应失败而不是卡死
PAUSE_TIMEOUT = 10
RUN_TIMEOUT = 30


def _debug_kernel(brain=fib_brain):
    """带调试控制器的 fib 内核;返回 ``(kernel, controller, mock)``。"""
    config = RunConfig(
        model="mock/fib",
        max_depth=8,
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),  # system.python.exec 是 EXEC 级
        compression="off",
    )
    controller = DebugController()
    mock = MockProvider(brain)
    kernel = assemble(config, mock, FIB_SKILLS_YAML, debug_controller=controller)
    return kernel, controller, mock


def _err_brain(req) -> ChatResponse:
    """第一次调用发一个会失败的 system.python.exec,之后给合法最终答案。"""
    if not any(m.role is Role.TOOL for m in req.messages):
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                tool_calls=[
                    ToolCall(
                        id="c-err",
                        name="system.python.exec",
                        args={"code": "raise RuntimeError('boom')"},
                    )
                ],
            ),
            finish_reason="tool_calls",
            usage=ChatUsage(prompt=1, completion=1),
        )
    return ChatResponse(
        message=Message(role=Role.ASSISTANT, content=json.dumps({"seq": [0]})),
        finish_reason="stop",
        usage=ChatUsage(prompt=1, completion=1),
    )


async def _pause_point(session) -> dict:
    """等到暂停并返回 pause_point(超时兜底:悬挂即测试失败)。"""
    await asyncio.wait_for(session.wait_paused(), PAUSE_TIMEOUT)
    assert session.state == "paused", f"会话未暂停(state={session.state})"
    return session.pause_point


async def _finish(task):
    return await asyncio.wait_for(task, RUN_TIMEOUT)


# ---------------------------------------------------------------------------
# 断点命中 / continue
# ---------------------------------------------------------------------------


def test_breakpoint_pauses_then_continue_matches_plain_run():
    """tool_call 断点命中即暂停(run 不完成、pause_point 正确);continue 后结果与无调试一致。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        bp = session.add_breakpoint("tool_call", "system.python.exec")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        point = await _pause_point(session)
        assert not task.done(), "断点命中时 run 不应完成"
        assert point["signal"] == PRE_TOOL_CALL
        assert point["tool"] == "system.python.exec"
        assert point["reason"] == "breakpoint"
        assert point["breakpoint_ids"] == [bp.id]
        assert bp.hits == 1

        session.resume("continue")
        result = await _finish(task)
        assert result == {"seq": [0, 1, 1]}, "调试放行后的结果应与无调试一致"
        return session

    session = asyncio.run(drive())
    assert session.state == "detached", "run 结束应自动摘下会话"


def test_step_breakpoint_glob_matching():
    """skill_invoke 断点按名字 glob 匹配(fnmatch);step 断点每条 pre:step 都停。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        bp_skill = session.add_breakpoint("skill_invoke", "demo.*")
        bp_step = session.add_breakpoint("step")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        point = await _pause_point(session)
        assert point["signal"] == PRE_STEP  # 根帧第一步先停

        session.remove_breakpoint(bp_step.id)
        session.resume("continue")
        point = await _pause_point(session)
        assert point["signal"] == "pre:skill.invoke"
        assert point["skill"] == "demo.fib"
        assert bp_skill.hits == 1

        session.resume("continue")
        assert await _finish(task) == {"seq": [0, 1, 1]}

    asyncio.run(drive())


# ---------------------------------------------------------------------------
# 四种步进语义(fib 递归嵌套帧:F1=fib(3) 根帧,F2=fib(2) 子帧 base case)
# ---------------------------------------------------------------------------


def test_step_into_enters_child_frame():
    """step_into:任意帧的下一条 pre:step 即停——F1 第一步后进入子帧 F2。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        bp = session.add_breakpoint("step")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        first = await _pause_point(session)
        assert first["signal"] == PRE_STEP and first["depth"] == 1

        session.remove_breakpoint(bp.id)  # 摘掉断点,只验步进语义
        session.resume("step_into")
        point = await _pause_point(session)
        assert point["signal"] == PRE_STEP
        assert point["reason"] == "step:into"
        assert point["depth"] == 2, "step_into 应停在子帧的第一步"
        assert point["frame_id"] != first["frame_id"]

        session.resume("continue")
        assert await _finish(task) == {"seq": [0, 1, 1]}

    asyncio.run(drive())


def test_step_over_skips_child_frame_steps():
    """step_over:子帧内的 step 不停,同帧下一条 pre:step 停。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        bp = session.add_breakpoint("step")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        first = await _pause_point(session)
        session.remove_breakpoint(bp.id)
        session.resume("step_over")
        point = await _pause_point(session)
        assert point["signal"] == PRE_STEP
        assert point["reason"] == "step:over"
        assert point["frame_id"] == first["frame_id"], "step_over 应停在同一帧"
        assert point["step"] == first["step"] + 1, "中间跨过整个子帧(F2 的 step 不停)"

        session.resume("continue")
        assert await _finish(task) == {"seq": [0, 1, 1]}

    asyncio.run(drive())


def test_step_over_stops_at_frame_pop_when_no_more_steps():
    """step_over:本帧没有下一条 step(给出最终答案)时,停在该帧 pre:frame.pop。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        bp = session.add_breakpoint("step")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        first = await _pause_point(session)
        session.remove_breakpoint(bp.id)
        session.resume("step_over")
        second = await _pause_point(session)  # F1 step 2
        session.resume("step_over")
        third = await _pause_point(session)  # F1 step 3(最终答案前)
        assert third["frame_id"] == first["frame_id"] and third["step"] == 3
        session.resume("step_over")
        point = await _pause_point(session)
        assert point["signal"] == PRE_FRAME_POP, "帧尾 step_over 应停在 pre:frame.pop"
        assert point["frame_id"] == first["frame_id"]

        session.resume("continue")
        assert await _finish(task) == {"seq": [0, 1, 1]}

    asyncio.run(drive())


def test_step_out_stops_at_current_frame_pop():
    """step_out:停到当前帧的 pre:frame.pop。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        bp = session.add_breakpoint("step")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        await _pause_point(session)  # F1 step 1
        session.remove_breakpoint(bp.id)
        session.resume("step_into")
        child = await _pause_point(session)  # F2 step 1
        assert child["depth"] == 2

        session.resume("step_out")
        point = await _pause_point(session)
        assert point["signal"] == PRE_FRAME_POP
        assert point["reason"] == "step:out"
        assert point["frame_id"] == child["frame_id"], "step_out 应停在当前帧(F2)弹栈"

        session.resume("continue")
        assert await _finish(task) == {"seq": [0, 1, 1]}

    asyncio.run(drive())


def test_continue_runs_to_next_breakpoint():
    """continue:清步进模式放行,命中下一断点再停。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        bp = session.add_breakpoint("step")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        first = await _pause_point(session)
        session.resume("continue")  # 不是单步:应直接命中下一断点
        second = await _pause_point(session)
        assert second["signal"] == PRE_STEP
        assert second["frame_id"] != first["frame_id"] or second["step"] != first["step"]
        assert bp.hits == 2

        session.remove_breakpoint(bp.id)
        session.resume("continue")
        assert await _finish(task) == {"seq": [0, 1, 1]}
        assert bp.hits == 2, "摘掉的断点不再命中"

    asyncio.run(drive())


# ---------------------------------------------------------------------------
# 干预:Modify / inject_message
# ---------------------------------------------------------------------------


def test_modify_tool_args_patches_call():
    """tool_call 断点 + Modify patch:工具实际收到改后参数,结果随之改变。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        session.add_breakpoint("tool_call", "system.python.exec")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        point = await _pause_point(session)
        assert point["signal"] == PRE_TOOL_CALL
        assert "result = 0 + 1" in point["payload"]["args"]["code"]

        session.modify_tool_args({"code": "result = 41\nprint(result)"})
        result = await _finish(task)
        assert result == {"seq": [0, 1, 41]}, "工具应执行改后的代码(41 而非 1)"

    asyncio.run(drive())


def test_inject_message_reaches_frame_context():
    """inject_message:帧上下文出现 Source.INJECTED 消息,进入后续 LLM 请求。"""

    async def drive():
        kernel, controller, mock = _debug_kernel()
        session = controller.open_session()
        session.add_breakpoint("tool_call", "system.python.exec")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        point = await _pause_point(session)
        await session.inject_message(point["frame_id"], "调试注入的补充说明")
        assert await _finish(task) == {"seq": [0, 1, 1]}

        injected = [
            m
            for req in mock.recorded
            for m in req.messages
            if m.content == "调试注入的补充说明"
        ]
        assert injected, "注入消息未进入后续 LLM 请求"
        assert injected[0].role is Role.USER
        assert injected[0].source.name == "INJECTED"

    asyncio.run(drive())


# ---------------------------------------------------------------------------
# error 断点 / stop / detach
# ---------------------------------------------------------------------------


def test_error_breakpoint_on_failed_tool_call():
    """error 断点:post:tool.call payload ok=false 时命中。"""

    async def drive():
        kernel, controller, _ = _debug_kernel(_err_brain)
        session = controller.open_session()
        bp = session.add_breakpoint("error")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 1}))

        point = await _pause_point(session)
        assert point["signal"] == POST_TOOL_CALL
        assert point["tool"] == "system.python.exec"
        assert point["payload"]["ok"] is False
        assert bp.hits == 1

        session.resume("continue")
        assert await _finish(task) == {"seq": [0]}

    asyncio.run(drive())


def test_stop_aborts_run():
    """stop:返回 Stop verdict,run 以 RunAborted 收尾、状态 aborted。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        session.add_breakpoint("step")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        await _pause_point(session)
        session.resume("stop")
        with pytest.raises(RunAborted, match="debugger: stop"):
            await _finish(task)
        run = kernel._runs[session.run_id]
        assert run.state.status is RunStatus.ABORTED
        return session

    session = asyncio.run(drive())
    assert session.state == "detached", "run.aborted 应自动摘下会话"


def test_detach_releases_blocked_run():
    """detach:阻塞点自动放行,断点不再生效,run 跑完不悬挂(超时断言兜底)。"""

    async def drive():
        kernel, controller, _ = _debug_kernel()
        session = controller.open_session()
        bp = session.add_breakpoint("step")
        task = asyncio.create_task(kernel.run("demo.fib", {"n": 3}))

        await _pause_point(session)
        session.detach()
        assert session.state == "detached"
        result = await _finish(task)
        assert result == {"seq": [0, 1, 1]}
        assert bp.hits == 1, "detach 后断点不再命中"

    asyncio.run(drive())
