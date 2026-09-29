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
- Pause verdict 两入口(pre:step / pre:tool.call)同样真化;
- 计时器规格随帧 working 落档(tools/timer.py):resume 结算序列重武装——
  未到期按剩余时长重睡到点真 fire、已过期立即补投、recurring 续跑到 count 停、
  链式 resume 不双火、DONE run 不复活。
"""

from __future__ import annotations

import asyncio
import json
import time
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


# ---------------------------------------------------------------------------
# 计时器持久化 / resume 重武装(规格随帧 working["_timers"] 落 checkpoint)
# ---------------------------------------------------------------------------


class _FakeSleep:
    """立即返回的快进钟(记录每次 delay 供折算断言;同 tests/tools/test_timer.py 先例)。"""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


async def _slow_fib(req):
    """异步慢 fib brain(每次调用让出事件循环 10ms):给重武装的计时器任务运行窗口。"""
    await asyncio.sleep(0.01)
    return fib_brain(req)


def _plant_timers(ckpt, specs, *, deepest: bool = False) -> str:
    """把规格植入 checkpoint 帧 working(模拟 pause 前 set 的计时器随档落盘);返回目标帧 id。

    规格 ``frame_id=None`` 的由本函数补为目标帧 id。缺省挂根帧;``deepest=True``
    挂最深帧(链式挂起场景:resume 从最深帧开始结算,只有它的执行窗口能
    让重武装的计时器在再次挂起前到点)。
    """
    doc = json.loads(ckpt.read_text(encoding="utf-8"))
    if deepest:
        target = max(doc["frames"], key=lambda f: f["depth"])
    else:
        target = next(f for f in doc["frames"] if f["parent_id"] is None)
    for spec in specs:
        if spec.get("frame_id") is None:
            spec["frame_id"] = target["frame_id"]
    target["context"]["working"]["_timers"] = specs
    ckpt.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return target["frame_id"]


def _one_shot_spec(run_id: str, *, overdue: bool = False, note: str = "断电前安排的复查") -> dict:
    """手工构造的 one-shot 规格(形状与 tools/timer.py set() 落档的一致)。"""
    now = time.time()
    return {
        "timer_id": "planted-1shot",
        "run_id": run_id,
        "frame_id": None,
        "delay_seconds": 3600.0,
        "interval_seconds": None,
        "count": None,
        "fired": 0,
        "note": note,
        "created_at": now - 3610 if overdue else now,
        "next_fire_at": now - 5 if overdue else now + 3600,
    }


def _paused_fib3_ckpt(tmp_path, name: str = "ckpt.json"):
    """fib(3) 首次 LLM 调用后挂起并落 checkpoint(断电现场模板);返回 (kernel, run_id, ckpt 路径)。"""
    kernel, _ = _kernel(sidecars=(_PauseProbe(),))
    with pytest.raises(RunPaused, match="^人工暂停排查$"):
        asyncio.run(kernel.run("demo.fib", {"n": 3}))
    run_id = _only_run(kernel).run_id
    ckpt = tmp_path / name
    kernel.checkpoint(run_id, str(ckpt))
    return kernel, run_id, ckpt


def _timer_msgs(frame) -> list:
    return [m for m in frame.context.messages if m.content.startswith("[timer 到点]")]


def test_resume_rearms_pending_one_shot(tmp_path):
    """one-shot 未到期时 pause → checkpoint → 新内核 resume:规格重武装,按剩余时长
    重睡(假钟记录折算),到点真 fire(消息进根帧 context),timer_id 改写。"""
    _, run_id, ckpt = _paused_fib3_ckpt(tmp_path)
    root_id = _plant_timers(ckpt, [_one_shot_spec(run_id)])

    kernel2, mock2 = _kernel(brain=_slow_fib)
    fake = _FakeSleep()
    kernel2.tools._timers._sleep = fake
    result = asyncio.run(kernel2.resume(str(ckpt)))

    assert result == {"seq": [0, 1, 1]}
    assert len(mock2.recorded) == 3, "恢复不是重跑(计时器重武装不得干扰帧结算)"
    root = kernel2.stack.get(root_id)
    timer_msgs = _timer_msgs(root)
    assert len(timer_msgs) == 1, "重武装后到点真 fire 一次"
    (spec,) = root.context.working["_timers"]
    assert spec["timer_id"] != "planted-1shot", "重武装改写 timer_id(防同进程二次 resume 双火)"
    assert spec["timer_id"] in timer_msgs[0].content and "第 1 次" in timer_msgs[0].content
    assert spec["done"] is True and spec["fired"] == 1
    assert fake.calls and 3500 < fake.calls[0] <= 3600, f"按剩余时长重睡: {fake.calls}"
    assert not kernel2.tools._timers.tasks, "fire 后任务结算出表"


def test_resume_overdue_one_shot_fires_immediately(tmp_path):
    """one-shot 在暂停期间已过期:resume 立即补投一次(不重生任务、不睡),消息进恢复后的 LLM 上下文。"""
    _, run_id, ckpt = _paused_fib3_ckpt(tmp_path)
    root_id = _plant_timers(ckpt, [_one_shot_spec(run_id, overdue=True)])

    kernel2, mock2 = _kernel()
    fake = _FakeSleep()
    kernel2.tools._timers._sleep = fake
    result = asyncio.run(kernel2.resume(str(ckpt)))

    assert result == {"seq": [0, 1, 1]}
    root = kernel2.stack.get(root_id)
    timer_msgs = _timer_msgs(root)
    assert len(timer_msgs) == 1, "暂停中过期欠的一次须补还"
    assert "planted-1shot" in timer_msgs[0].content, "补投沿用原 timer_id(同一个逻辑计时器)"
    assert fake.calls == [], f"立即补投不得再睡: {fake.calls}"
    (spec,) = root.context.working["_timers"]
    assert spec["done"] is True and spec["fired"] == 1
    assert not kernel2.tools._timers.tasks, "补偿 fire 不重生后台任务"
    seen = any("[timer 到点]" in m.content for req in mock2.recorded for m in req.messages)
    assert seen, "补投发生在帧重入 loop 之前,恢复后的首次 LLM 调用即应看到"


def test_resume_rearms_recurring_until_count(tmp_path):
    """recurring(count=3,fired=1 时 pause):resume 后续跑到 3 停——注入文本计数连续
    (第 2/3 次,共 3 次),首睡 = 剩余时长、其后回 interval,规格 fired/done 回写正确。"""
    _, run_id, ckpt = _paused_fib3_ckpt(tmp_path)
    now = time.time()
    root_id = _plant_timers(
        ckpt,
        [
            {
                "timer_id": "planted-recur",
                "run_id": run_id,
                "frame_id": None,
                "delay_seconds": None,
                "interval_seconds": 2.0,
                "count": 3,
                "fired": 1,
                "note": "轮询构建",
                "created_at": now - 2,
                "next_fire_at": now + 1.0,
            }
        ],
    )

    kernel2, _ = _kernel(brain=_slow_fib)
    fake = _FakeSleep()
    kernel2.tools._timers._sleep = fake
    result = asyncio.run(kernel2.resume(str(ckpt)))

    assert result == {"seq": [0, 1, 1]}
    root = kernel2.stack.get(root_id)
    contents = [m.content for m in _timer_msgs(root)]
    assert len(contents) == 2, f"续跑 fired=1→3 须恰好 2 次: {contents}"
    assert "第 2 次/共 3 次" in contents[0] and "第 3 次/共 3 次" in contents[1]
    (spec,) = root.context.working["_timers"]
    assert spec["fired"] == 3 and spec["done"] is True
    assert spec["timer_id"] != "planted-recur"
    assert len(fake.calls) == 2
    assert 0.5 <= fake.calls[0] <= 1.0, f"首睡 = 剩余时长(≈1s): {fake.calls}"
    assert fake.calls[1] == 2.0, "其后回到 interval 节奏"


def test_pause_resume_chain_timer_no_double_fire(tmp_path):
    """同进程 pause→resume→pause→resume:计时器全程只 fire 一次(重武装改写
    timer_id + 已 done 规格跳过,新旧两代不并存双火)。

    计时器挂最深帧:resume 从最深帧开始结算/执行,它的 LLM 调用窗口让重武装的
    计时器在再次挂起前到点(挂根帧则根帧尚未执行就被 pre:step 的挂起标志拦下)。
    """
    _, run_id, ckpt1 = _paused_fib3_ckpt(tmp_path, "ckpt1.json")
    child_id = _plant_timers(ckpt1, [_one_shot_spec(run_id)], deepest=True)

    # resume1:最深帧重武装 → 其 LLM 调用窗口内到点 fire 一次,随后再次挂起
    kernel2, _ = _kernel(brain=_slow_fib, sidecars=(_PauseProbe("第二次挂起"),))
    fake = _FakeSleep()
    kernel2.tools._timers._sleep = fake
    with pytest.raises(RunPaused, match="^第二次挂起$"):
        asyncio.run(kernel2.resume(str(ckpt1)))
    assert kernel2._runs[run_id].state.status is RunStatus.PAUSED
    assert len(fake.calls) == 1, f"resume1 重武装恰好一次: {fake.calls}"
    assert 3500 < fake.calls[0] <= 3600, f"按剩余时长重睡: {fake.calls}"
    ckpt2 = tmp_path / "ckpt2.json"
    kernel2.checkpoint(run_id, str(ckpt2))

    # resume2:规格已 done → 不重武装、不再 fire
    kernel3, _ = _kernel()
    result = asyncio.run(kernel3.resume(str(ckpt2)))
    assert result == {"seq": [0, 1, 1]}
    child = kernel3.stack.get(child_id)
    timer_msgs = _timer_msgs(child)
    assert len(timer_msgs) == 1, f"链式 resume 全程不得双火: {[m.content for m in timer_msgs]}"
    (spec,) = child.context.working["_timers"]
    assert spec["done"] is True and spec["fired"] == 1
    assert spec["timer_id"] != "planted-1shot" and spec["timer_id"] in timer_msgs[0].content
    root = next(f for f in kernel3.stack.tree() if f.run_id == run_id and f.parent_id is None)
    assert not _timer_msgs(root), "fire 只落在持规格的帧"
    assert not kernel3.tools._timers.tasks


def test_done_run_resume_does_not_rearm(tmp_path):
    """DONE run 的 checkpoint(帧全 DONE):resume 不重武装规格、不注入、不建任务。"""
    kernel1, _ = _kernel()
    assert asyncio.run(kernel1.run("demo.fib", {"n": 1})) == {"seq": [0]}
    run_id = _only_run(kernel1).run_id
    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run_id, str(ckpt))
    root_id = _plant_timers(ckpt, [_one_shot_spec(run_id)])

    kernel2, _ = _kernel()
    fake = _FakeSleep()
    kernel2.tools._timers._sleep = fake
    assert asyncio.run(kernel2.resume(str(ckpt))) == {"seq": [0]}

    assert fake.calls == [], f"DONE 帧不重武装(不得再睡): {fake.calls}"
    assert not kernel2.tools._timers.tasks, "DONE 帧不建任务"
    root = kernel2.stack.get(root_id)
    assert not _timer_msgs(root), "DONE 帧不得再收注入消息"
    (spec,) = root.context.working["_timers"]
    assert spec["timer_id"] == "planted-1shot" and "done" not in spec, "规格原样保留(未被结算)"
