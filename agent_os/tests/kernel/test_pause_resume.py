"""pause 真语义锚点测试(docs/DESIGN.md :940;可恢复挂起 → checkpoint → resume)。

固定约定:

- ``RunControl.pause`` / Pause verdict → 下一个 safe point 抛 ``RunPaused``
  (继承 ``RunAborted``,沿调用栈弹到 Run 边界不被单帧吞掉)→ Run 边界落
  ``RunStatus.PAUSED``、发 ``run.paused``(payload ``{"reason": ...}``,
  **不发** ``run.aborted``),理由原样(不拼 ``"paused: "`` 前缀);
- 挂起后宿主 finalize 照常 ``kernel.checkpoint(run_id, path)`` 落档,新内核
  ``resume`` 恢复:**恢复不是重跑**(checkpoint 前已完成的 LLM 调用不重复),
  usage 继承 checkpoint 累计继续记账;
- resume 边界同语义:恢复中再次 pause 仍落 PAUSED + ``run.paused``,
  可链式"挂起 → 恢复 → 再挂起 → 再恢复"直至 DONE;
- Pause verdict 两入口(pre:step / pre:tool.call)同样真化。
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar

import pytest

from agent_os.api.v1 import (
    RUN_FINISHED,
    RUN_PAUSED,
    RUN_STARTED,
    Allow,
    Mode,
    Pause,
    Permission,
    RunConfig,
    RunStatus,
    Signal,
    ToolPolicy,
)
from agent_os.kernel.errors import RunPaused
from agent_os.providers.mock import MockProvider
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import FIB_SKILLS_YAML, assemble, record_all


def _config() -> RunConfig:
    return RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )


def _kernel(brain=fib_brain, sidecars=()):
    """fib 内核(sidecars 非空 → builder 装配 RunControl);返回 (kernel, MockProvider)。"""
    provider = brain if hasattr(brain, "chat") else MockProvider(brain)
    return assemble(_config(), provider, FIB_SKILLS_YAML, sidecars=sidecars), provider


def _only_run(kernel):
    return next(iter(kernel._runs.values()))


class _PauseProbe:
    """post:llm.response 上一次 ``ctl.pause`` 的探针(经 RunControl 通道)。"""

    name: ClassVar[str] = "pause-probe"
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False
    subscriptions: ClassVar[list] = ["post:llm.response"]

    def __init__(self, reason: str = "人工暂停排查") -> None:
        self.reason = reason
        self.fired = 0

    async def on_signal(self, sig: Signal, ctl) -> Allow:
        if self.fired == 0:
            self.fired += 1
            await ctl.pause(sig.run_id, self.reason)
        return Allow()


class _VerdictProbe:
    """在指定 pre 信号上回一次 Pause verdict 的 sidecar(内核仲裁路径)。"""

    name: ClassVar[str] = "verdict-probe"
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self, signal: str, reason: str) -> None:
        self.subscriptions = [signal]
        self._reason = reason
        self.fired = 0

    async def on_signal(self, sig: Signal, ctl) -> Any:
        if self.fired == 0:
            self.fired += 1
            return Pause(self._reason)
        return Allow()


# ---------------------------------------------------------------------------
# 挂起 → checkpoint → 新内核 resume(断电恢复模板,test_trace_checkpoint 同构)
# ---------------------------------------------------------------------------


def test_pause_checkpoint_resume_no_rerun(tmp_path):
    """fib(3) 首次 LLM 调用后挂起 → checkpoint → 新内核 resume 到 DONE:
    结果正确、恢复只补 3 次调用(不重跑已完成的 1 次)、usage 继承累计。"""
    kernel1, mock1 = _kernel(sidecars=(_PauseProbe(),))
    seen1 = record_all(kernel1)
    with pytest.raises(RunPaused, match="^人工暂停排查$"):
        asyncio.run(kernel1.run("demo.fib", {"n": 3}))

    run = _only_run(kernel1)
    assert run.state.status is RunStatus.PAUSED
    names1 = [s.name for s in seen1]
    assert names1.count(RUN_PAUSED) == 1 and "run.aborted" not in names1
    usage_before = run.state.usage
    done_calls = len(mock1.recorded)
    assert done_calls == 1, "挂起点:根帧首次调用已结算,子帧尚未起步"

    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run.run_id, str(ckpt))
    assert ckpt.exists()

    kernel2, mock2 = _kernel()
    seen2 = record_all(kernel2)
    result = asyncio.run(kernel2.resume(str(ckpt)))
    assert result == {"seq": [0, 1, 1]}
    # 恢复不是重跑:fib(3) 全程 4 次调用,checkpoint 前已完成 1 次,只补 3 次
    assert len(mock2.recorded) == 3
    # usage 继承:checkpoint 累计 + 恢复新增
    usage_after = kernel2._runs[run.run_id].state.usage
    assert usage_after.steps == usage_before.steps + 3
    assert usage_after.prompt_tokens == usage_before.prompt_tokens + 3
    names2 = [s.name for s in seen2]
    assert RUN_FINISHED in names2, "resume 到 DONE 应发 run.finished"
    assert RUN_STARTED not in names2, "resume 不补发 run.started(§10.2)"
    assert "run.aborted" not in names2 and RUN_PAUSED not in names2


def test_pause_resume_chain(tmp_path):
    """链式:挂起 → resume 中二次挂起(仍落 PAUSED)→ 再 resume 到 DONE。"""
    kernel1, _ = _kernel(sidecars=(_PauseProbe("第一次挂起"),))
    with pytest.raises(RunPaused, match="^第一次挂起$"):
        asyncio.run(kernel1.run("demo.fib", {"n": 3}))
    run_id = _only_run(kernel1).run_id
    ckpt1 = tmp_path / "ckpt1.json"
    kernel1.checkpoint(run_id, str(ckpt1))

    # resume 边界同语义:恢复中再次 pause 落 PAUSED + run.paused(不发 run.aborted)
    kernel2, _ = _kernel(sidecars=(_PauseProbe("第二次挂起"),))
    seen2 = record_all(kernel2)
    with pytest.raises(RunPaused, match="^第二次挂起$"):
        asyncio.run(kernel2.resume(str(ckpt1)))
    assert kernel2._runs[run_id].state.status is RunStatus.PAUSED
    names2 = [s.name for s in seen2]
    assert names2.count(RUN_PAUSED) == 1 and "run.aborted" not in names2
    ckpt2 = tmp_path / "ckpt2.json"
    kernel2.checkpoint(run_id, str(ckpt2))

    # 再 resume:根帧带着子帧真实结果继续,两次调用到 DONE
    kernel3, mock3 = _kernel()
    result = asyncio.run(kernel3.resume(str(ckpt2)))
    assert result == {"seq": [0, 1, 1]}
    assert len(mock3.recorded) == 2
    assert kernel3._runs[run_id].state.status is RunStatus.DONE


# ---------------------------------------------------------------------------
# Pause verdict 两入口真化(§5.2 仲裁在内核)
# ---------------------------------------------------------------------------


def test_pause_verdict_pre_step_suspends():
    """pre:step Pause verdict:首个 safe point 后即挂起(未发任何 LLM 调用),
    理由原样上抛、落 PAUSED、发 run.paused。"""
    probe = _VerdictProbe("pre:step", "sidecar 要求暂停")
    kernel, mock = _kernel(sidecars=(probe,))
    seen = record_all(kernel)
    with pytest.raises(RunPaused, match="^sidecar 要求暂停$"):
        asyncio.run(kernel.run("demo.fib", {"n": 3}))

    assert not mock.recorded, "pre:step 否决于首次 LLM 调用之前"
    assert _only_run(kernel).state.status is RunStatus.PAUSED
    paused = [s for s in seen if s.name == RUN_PAUSED]
    assert len(paused) == 1 and paused[0].payload == {"reason": "sidecar 要求暂停"}
    assert not [s for s in seen if s.name == "run.aborted"]


def test_pause_verdict_pre_tool_call_suspends():
    """pre:tool.call Pause verdict:工具分发前挂起,落 PAUSED 可恢复。"""
    probe = _VerdictProbe("pre:tool.call", "工具调用前暂停")
    kernel, mock = _kernel(sidecars=(probe,))
    seen = record_all(kernel)
    with pytest.raises(RunPaused, match="^工具调用前暂停$"):
        asyncio.run(kernel.run("demo.fib", {"n": 3}))

    # fib(3) 全程 4 次调用:根帧首调(skill.* 不过 pre:tool.call)+ 子帧终答
    # + 根帧第二调(响应已结算)——挂起于该次 system.python.exec 的分发前
    assert len(mock.recorded) == 3
    assert _only_run(kernel).state.status is RunStatus.PAUSED
    paused = [s for s in seen if s.name == RUN_PAUSED]
    assert len(paused) == 1 and paused[0].payload == {"reason": "工具调用前暂停"}
    assert not [s for s in seen if s.name == "run.aborted"]
