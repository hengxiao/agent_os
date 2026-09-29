"""system.timer.set / TimerService 锚点测试(WS1;docs/DESIGN.md §8.3 扩展)。

固定约定:

- 工具面常驻(``with_builtins`` 默认注册;新工具无旧名,不设别名),WRITE 档
  (登记后台任务是对 run 的副作用);工具立即返回 timer_id,计时在后台;
- 参数:one-shot = ``delay_seconds``,recurring = ``interval_seconds``
  (``count`` 缺省无限);两者二选一,缺/并给 → INVALID_ARGS;下限钳制 0.5s(防抖);
- TimerService 按 run_id 分桶;fire 经 ``ctl.inject_message`` 注入
  ``[timer 到点] {note}``(Role.USER/Source.INJECTED);帧/run 终态 → 静默弃(log);
- run 收尾(runner ``_release_run`` → registry ``release_run``)取消本 run 全部
  计时器(**只取消进程任务,规格不标 done**——留给 resume 重武装);
- 规格随帧持久化(``frame.context.working["_timers"]``,随 checkpoint 落档;
  帧不可达只记 log 不阻断);resume 经 ``Kernel._settle_pending_timers`` →
  ``rearm_from_working`` 折算重武装(过期 one-shot 立即补投;recurring 错过的
  中间触发不逐次补账,节奏从 resume 起算;timer_id 改写防双火);
- ``sleep`` 可注入(本文件用假钟快进/悬挂,同 StallDetector 注入 clock 先例);
- fire 注入通道(ctl)由 KernelBuilder 装配时 bind(未 bind → NOT_FOUND,
  同 user 通道先例;builder 恒装配,所以正常路径总有)。
"""

from __future__ import annotations

import asyncio
import json
import textwrap
import time

from agent_os.api.v1 import (
    Message,
    Permission,
    Role,
    RunConfig,
    SkillFrame,
    Source,
    ToolCall,
    ToolDispatchContext,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.kernel.control import RunControlImpl
from agent_os.kernel.runner import Kernel
from agent_os.logic.inprocess import InProcessLogicKernel
from agent_os.runtime.builder import KernelBuilder
from agent_os.skills.local_file import LocalFileSkillRegistry
from agent_os.tools.local_registry import LocalPythonToolRegistry
from agent_os.tools.timer import TimerService


class _FakeSleep:
    """立即返回的快进钟(记录每次 delay,供钳制断言)。"""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


async def _hang(delay: float) -> None:
    """永不返回的睡眠(release 取消路径用:到点永不到达,只剩取消一种结局)。"""
    await asyncio.Event().wait()


def _kernel_with_frame(frame_id: str = "f1", run_id: str = "r1"):
    """真实 Kernel + 一个 RUNNING 帧(fire 注入的真实落点:frame.context.messages)。"""
    kernel = Kernel()
    frame = SkillFrame(frame_id=frame_id, run_id=run_id)
    kernel.stack.push(frame)
    return kernel, frame


def _ctx(allowed=("system.timer.set",)):
    return ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1"),
        allowed_tools=list(allowed),
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )


def _bound_registry(sleep=None):
    """with_builtins + 绑定 ctl 的 registry(替内核持有一个带 RUNNING 帧的 Kernel)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    if sleep is not None:
        reg._timers._sleep = sleep  # 测试注入假钟(同 StallDetector clock 先例)
    kernel, frame = _kernel_with_frame()
    reg.bind_timer(RunControlImpl(kernel))
    return reg, frame


def test_timer_registered_in_builtins():
    """system.timer.set 在默认工具面里(常驻),无旧名别名;WRITE 档、不可缓存/并行。"""
    reg = LocalPythonToolRegistry.with_builtins()
    assert reg.has("system.timer.set"), "system.timer.set 必须在默认工具面里"
    assert not reg.has("timer_set") and not reg.has("set_timer"), "新工具不设旧名别名"
    spec = reg.get("system.timer.set").spec
    assert spec.permission is Permission.WRITE, "登记后台计时是副作用,应按 WRITE 档"
    assert not spec.cacheable and not spec.concurrent_safe
    props = set(spec.parameters["properties"])
    assert props == {"delay_seconds", "interval_seconds", "count", "note"}
    assert spec.parameters["required"] == [], "四个参数全部可选(缺/并给的校验在工具内)"


def test_unbound_timer_service_reports_not_found():
    """未 bind fire 注入通道:按"timer 服务未装配"报 NOT_FOUND(同 user 通道先例)。"""
    reg = LocalPythonToolRegistry.with_builtins()

    async def main():
        return await reg.dispatch(
            ToolCall(id="1", name="system.timer.set", args={"delay_seconds": 1}), _ctx()
        )

    res = asyncio.run(main())
    assert not res.ok and res.error.kind is ToolErrorKind.NOT_FOUND
    assert res.error.hint, "错误须带可执行的下一步建议(§W0-3)"


def test_one_shot_fires_and_injects_into_frame():
    """one-shot 到点:向目标帧注入 [timer 到点] 消息(USER/INJECTED),任务出表。"""
    reg, frame = _bound_registry(sleep=_FakeSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.timer.set",
                args={"delay_seconds": 5, "note": "复查进度"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        timer_id = res.value["timer_id"]
        await reg._timers.tasks[timer_id]  # 假钟立即到点
        return timer_id

    timer_id = asyncio.run(main())
    assert len(frame.context.messages) == 1, "到点须注入恰好一条消息"
    msg = frame.context.messages[0]
    assert msg.role is Role.USER and msg.source is Source.INJECTED
    assert msg.content.startswith("[timer 到点] 复查进度")
    assert timer_id in msg.content and "第 1 次" in msg.content
    assert not reg._timers.tasks and not reg._timers._by_run, "one-shot 结算后须出表"


def test_recurring_count_settles():
    """recurring + count=2:恰好触发两次(带计数文本),然后计时器自然结算出表。"""
    reg, frame = _bound_registry(sleep=_FakeSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.timer.set",
                args={"interval_seconds": 2, "count": 2, "note": "轮询构建"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        await reg._timers.tasks[res.value["timer_id"]]

    asyncio.run(main())
    contents = [m.content for m in frame.context.messages]
    assert len(contents) == 2, f"count=2 须恰好两次,得到 {contents}"
    assert "第 1 次/共 2 次" in contents[0] and "第 2 次/共 2 次" in contents[1]
    assert not reg._timers.tasks, "count 用尽后计时器须结算出表"


def test_frame_terminal_drops_silently():
    """帧终态:到点消息静默弃(不进帧 context),recurring 一并停止后续触发。"""
    reg, frame = _bound_registry(sleep=_FakeSleep())
    reg._timers._ctl._kernel.stack.pop_ok(frame, None)  # 帧进终态(DONE)

    async def main():
        one_shot = await reg.dispatch(
            ToolCall(id="1", name="system.timer.set", args={"delay_seconds": 1}), _ctx()
        )
        recurring = await reg.dispatch(
            ToolCall(
                id="2",
                name="system.timer.set",
                args={"interval_seconds": 1},  # count 缺省 = 无限
            ),
            _ctx(),
        )
        assert one_shot.ok and recurring.ok
        one_task = reg._timers.tasks[one_shot.value["timer_id"]]
        recur_task = reg._timers.tasks[recurring.value["timer_id"]]
        await one_task
        await recur_task

    asyncio.run(main())
    assert not frame.context.messages, "终态帧不得再收注入消息"
    assert not reg._timers.tasks, "帧终态后 recurring 也须停止并出表"


def test_release_run_cancels_run_timers():
    """run 收尾:release_run 取消本 run 全部计时器并清空分桶;其它 run 不受影响。"""
    reg, _frame = _bound_registry(sleep=_hang)

    async def main():
        t1 = reg._timers.set(run_id="r1", frame_id="f1", delay_seconds=60)
        t2 = reg._timers.set(run_id="r1", frame_id="f1", interval_seconds=60)
        t3 = reg._timers.set(run_id="r2", frame_id="f1", delay_seconds=60)
        assert isinstance(t1, str) and isinstance(t2, str) and isinstance(t3, str)
        task1 = reg._timers.tasks[t1]
        assert set(reg._timers.tasks) == {t1, t2, t3}
        reg.release_run("r1")  # runner._release_run 的调用形态
        assert set(reg._timers.tasks) == {t3}, "只回收本 run 桶,跨 run 不得误杀"
        assert "r1" not in reg._timers._by_run
        await asyncio.sleep(0)  # 让取消落地
        assert task1.cancelled, "被取消的计时任务须以 CancelledError 结算"
        reg.release_run("r2")  # 收尾清场,防悬挂任务泄漏出测试
        await asyncio.sleep(0)

    asyncio.run(main())
    assert not reg._timers.tasks and not reg._timers._by_run


def test_args_validation_and_clamp():
    """缺参/并给/count<1 → INVALID_ARGS;delay/interval 下限钳制 0.5s(防抖)。"""
    sleep = _FakeSleep()
    reg, _frame = _bound_registry(sleep=sleep)

    async def main():
        missing = await reg.dispatch(
            ToolCall(id="1", name="system.timer.set", args={"note": "x"}), _ctx()
        )
        both = await reg.dispatch(
            ToolCall(
                id="2",
                name="system.timer.set",
                args={"delay_seconds": 1, "interval_seconds": 2},
            ),
            _ctx(),
        )
        bad_count = await reg.dispatch(
            ToolCall(
                id="3", name="system.timer.set", args={"interval_seconds": 2, "count": 0}
            ),
            _ctx(),
        )
        clamped = await reg.dispatch(
            ToolCall(id="4", name="system.timer.set", args={"delay_seconds": 0.01}), _ctx()
        )
        assert clamped.ok, clamped.error
        await reg._timers.tasks[clamped.value["timer_id"]]
        return missing, both, bad_count, clamped

    missing, both, bad_count, clamped = asyncio.run(main())
    for res in (missing, both, bad_count):
        assert not res.ok and res.error.kind is ToolErrorKind.INVALID_ARGS, res.error
        assert res.error.hint, "INVALID_ARGS 须带下一步建议(§W0-3)"
    assert clamped.value["delay_seconds"] == 0.5, "下限须钳到 0.5s"
    assert sleep.calls == [0.5], "实际调度须用钳制后的值"


def test_kernel_run_release_cancels_timers(tmp_path):
    """端到端接线:run 内 code 技能调 system.timer.set;run 收尾后任务表清空。"""
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text(
        "skills:\n"
        + textwrap.dedent(
            """
            - name: test.set_timer
              version: 1.0.0
              kind: code
              handler: tests.helpers.code_skills:set_timer_probe
              inputs:
                type: object
                properties: { args: { type: object } }
              outputs:
                type: object
                properties: { timer_id: { type: string } }
                required: [timer_id]
              permissions: { tools: [system.timer.set], skills: [] }
            """
        ),
        encoding="utf-8",
    )
    reg = LocalPythonToolRegistry.with_builtins()
    kernel = (
        KernelBuilder(
            RunConfig(
                model="mock/fib",
                tool_policy=ToolPolicy(max_permission=Permission.EXEC),
                compression="off",
            )
        )
        .tools(reg)
        .skills(LocalFileSkillRegistry(str(skills_yaml)))
        .logic_kernels(InProcessLogicKernel())
        .build()
    )

    async def main():
        # run() 的 finally 在返回前完成 _release_run:返回点计时器已取消
        return await kernel.run(
            "test.set_timer", {"args": {"delay_seconds": 60, "note": "长跑提醒"}}
        )

    result = asyncio.run(main())
    assert result["timer_id"], "工具须立即返回 timer_id(计时在后台)"
    assert not reg._timers.tasks and not reg._timers._by_run, (
        "run 收尾(_release_run → registry.release_run)须取消本 run 全部计时器"
    )


def test_kernel_builder_binds_timer_ctl():
    """KernelBuilder 装配链:无 sidecar 时也补装 ctl 并 bind 进 TimerService。"""
    reg = LocalPythonToolRegistry.with_builtins()
    kernel = KernelBuilder(RunConfig()).tools(reg).build()
    assert kernel.ctl is not None, "timer fire 注入通道需要 ctl(无 sidecar 时补装)"
    assert reg._timers.bound, "build 应把 ctl 经 bind_timer 注入 TimerService"


def test_injected_message_shape_recurring_note():
    """注入文本形状:note 为空时也带 timer_id 与计数(便于模型寻址/对账)。"""
    sleep = _FakeSleep()
    reg, frame = _bound_registry(sleep=sleep)

    async def main():
        res = await reg.dispatch(
            ToolCall(id="1", name="system.timer.set", args={"delay_seconds": 1}), _ctx()
        )
        assert res.ok, res.error
        await reg._timers.tasks[res.value["timer_id"]]
        return res.value["timer_id"]

    timer_id = asyncio.run(main())
    msg: Message = frame.context.messages[0]
    assert msg.content == f"[timer 到点] (timer_id={timer_id},第 1 次)"


# ---------------------------------------------------------------------------
# 规格持久化 / resume 重武装(working["_timers"] 随 checkpoint 落档)
# ---------------------------------------------------------------------------


class _GateSleep:
    """首次立即返回、其后悬挂的门控钟(让 recurring 停在 fired=1,模拟 pause 时点)。"""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)
        if len(self.calls) > 1:
            await asyncio.Event().wait()


def test_set_persists_spec_in_frame_working():
    """set 成功:规格落帧 working["_timers"](随 checkpoint 持久化),键形状/取值全断言。"""
    reg, frame = _bound_registry(sleep=_FakeSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.timer.set",
                args={"delay_seconds": 5, "note": "复查进度"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        timer_id = res.value["timer_id"]
        (spec,) = frame.context.working["_timers"]
        assert set(spec) == {
            "timer_id", "run_id", "frame_id", "delay_seconds", "interval_seconds",
            "count", "fired", "note", "created_at", "next_fire_at",
        }, "规格键形状(持久化契约;done 仅终结时补标)"
        assert spec["timer_id"] == timer_id
        assert spec["run_id"] == "r1" and spec["frame_id"] == "f1"
        assert spec["delay_seconds"] == 5.0 and spec["interval_seconds"] is None
        assert spec["count"] is None and spec["fired"] == 0 and spec["note"] == "复查进度"
        assert spec["next_fire_at"] - spec["created_at"] == 5.0, "next_fire_at = created_at + delay"
        await reg._timers.tasks[timer_id]  # 假钟立即到点
        return spec

    spec = asyncio.run(main())
    assert spec["done"] is True and spec["fired"] == 1, "fire/任务终结须回写规格"


def test_recurring_spec_rolls_with_fire():
    """recurring 规格随 fire 滚动:fired 逐次 +1、next_fire_at 推进,count 耗尽标 done。"""
    reg, frame = _bound_registry(sleep=_FakeSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.timer.set",
                args={"interval_seconds": 2, "count": 2},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        timer_id = res.value["timer_id"]
        (spec,) = frame.context.working["_timers"]
        first_next = spec["next_fire_at"]
        await reg._timers.tasks[timer_id]
        return spec, first_next

    spec, first_next = asyncio.run(main())
    assert spec["fired"] == 2 and spec["done"] is True
    assert spec["next_fire_at"] >= first_next, "next_fire_at 随 fire 滚动推进"


def test_release_run_keeps_spec_for_rearm():
    """run 收尾取消不标 done:规格留在帧 working,供 resume 重武装(pause/收尾统一)。"""
    reg, frame = _bound_registry(sleep=_hang)

    async def main():
        res = await reg.dispatch(
            ToolCall(id="1", name="system.timer.set", args={"delay_seconds": 60}), _ctx()
        )
        assert res.ok, res.error
        reg.release_run("r1")
        await asyncio.sleep(0)

    asyncio.run(main())
    (spec,) = frame.context.working["_timers"]
    assert "done" not in spec, "取消不得标 done——pause 后 resume 要靠留下的规格重武装"
    assert not reg._timers.tasks


def test_spec_not_recorded_when_frame_unreachable():
    """帧不可达(不在 kernel 栈):set 仍返回 timer_id,只记 log 不阻断(退回进程态旧行为)。"""
    reg, frame = _bound_registry(sleep=_hang)

    async def main():
        timer_id = reg._timers.set(run_id="r1", frame_id="ghost-frame", delay_seconds=60)
        assert isinstance(timer_id, str)
        assert timer_id in reg._timers.tasks
        reg.release_run("r1")
        await asyncio.sleep(0)

    asyncio.run(main())
    assert "_timers" not in frame.context.working, "帧不可达时规格不落档,也不误写别的帧"


def test_rearm_one_shot_pending_refires():
    """重武装(one-shot 未到期):按剩余时长重睡,timer_id 改写,到点真 fire 后标 done。"""
    reg, frame = _bound_registry(sleep=_hang)
    fake = _FakeSleep()

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.timer.set",
                args={"delay_seconds": 60, "note": "回看"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        old_id = res.value["timer_id"]
        reg.release_run("r1")  # 模拟 pause:run 收尾取消进程任务,规格留下
        await asyncio.sleep(0)
        assert not reg._timers.tasks
        reg._timers._sleep = fake  # resume:换假钟(等价于新内核的 service)
        n = await reg._timers.rearm_from_working(frame, now=time.time())
        assert n == 1, "未 done 规格须重武装 1 条"
        (spec,) = frame.context.working["_timers"]
        new_id = spec["timer_id"]
        assert new_id != old_id, "重武装须改写 timer_id(防同进程二次 resume 双火)"
        await reg._timers.tasks[new_id]  # 假钟立即到点
        return new_id, spec

    new_id, spec = asyncio.run(main())
    assert fake.calls and 55 < fake.calls[0] <= 60, f"须按剩余时长重睡: {fake.calls}"
    assert len(frame.context.messages) == 1, "到点真 fire 一次"
    msg = frame.context.messages[0]
    assert new_id in msg.content and "第 1 次" in msg.content and "回看" in msg.content
    assert spec["done"] is True and spec["fired"] == 1
    assert not reg._timers.tasks


def test_rearm_one_shot_overdue_fires_immediately():
    """重武装(one-shot 已过期):立即补投一次并标 done,不重生任务(欠的一次要还)。"""
    reg, frame = _bound_registry(sleep=_hang)
    fake = _FakeSleep()

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.timer.set",
                args={"delay_seconds": 5, "note": "到点提醒"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        reg.release_run("r1")
        await asyncio.sleep(0)
        (spec,) = frame.context.working["_timers"]
        spec["next_fire_at"] = time.time() - 1  # 暂停期间已超时
        reg._timers._sleep = fake
        n = await reg._timers.rearm_from_working(frame, now=time.time())
        assert n == 1
        return spec

    spec = asyncio.run(main())
    assert fake.calls == [], f"过期补投走立即 fire,不得再睡: {fake.calls}"
    assert not reg._timers.tasks, "补偿 fire 不重生后台任务"
    assert len(frame.context.messages) == 1
    assert "[timer 到点] 到点提醒" in frame.context.messages[0].content
    assert spec["done"] is True and spec["fired"] == 1


def test_rearm_recurring_overdue_compensates_once_then_continues():
    """重武装(recurring 已过期):只补最近一次(错过的中间触发不逐次补账),其后按 interval 续到 count 停。"""
    reg, frame = _bound_registry(sleep=_GateSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.timer.set",
                args={"interval_seconds": 2, "count": 3, "note": "轮询"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        old_id = res.value["timer_id"]
        task = reg._timers.tasks[old_id]
        while len(frame.context.messages) < 1:  # 等首次 fire 落地(门控钟放行第一次)
            await asyncio.sleep(0)
        reg.release_run("r1")  # pause:fired=1,第二次睡眠悬挂中被取消
        await asyncio.sleep(0)
        assert task.cancelled
        (spec,) = frame.context.working["_timers"]
        assert spec["fired"] == 1 and "done" not in spec
        spec["next_fire_at"] = time.time() - 100  # 断电 100s(错过约 50 次到点)
        fake = _FakeSleep()
        reg._timers._sleep = fake
        n = await reg._timers.rearm_from_working(frame, now=time.time())
        assert n == 1
        new_id = spec["timer_id"]
        if new_id in reg._timers.tasks:  # 补偿后未到 count:续睡任务在册
            await reg._timers.tasks[new_id]
        return old_id, new_id, fake, spec

    old_id, new_id, fake, spec = asyncio.run(main())
    contents = [m.content for m in frame.context.messages]
    assert len(contents) == 3, f"fired=1 + 补 1 次 + 续 1 次 = 3,得到 {contents}"
    assert "第 1 次/共 3 次" in contents[0]
    assert "第 2 次/共 3 次" in contents[1] and old_id in contents[1], "过期只补最近一次(沿用原 id)"
    assert "第 3 次/共 3 次" in contents[2] and new_id in contents[2]
    assert fake.calls == [2.0], f"补偿后只续睡一次 interval(不逐次补账): {fake.calls}"
    assert spec["done"] is True and spec["fired"] == 3


def test_rearm_recurring_pending_sleeps_remaining_first():
    """重武装(recurring 未到期):首睡 = 剩余时长(睡到原到点),其后回 interval 节奏。"""
    reg, frame = _bound_registry(sleep=_GateSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.timer.set",
                args={"interval_seconds": 2, "count": 3, "note": "轮询"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        while len(frame.context.messages) < 1:
            await asyncio.sleep(0)
        reg.release_run("r1")
        await asyncio.sleep(0)
        (spec,) = frame.context.working["_timers"]
        # 首次 fire 已把 next_fire_at 滚动为 触发时刻+2(未到期,remaining ≈ 2)
        fake = _FakeSleep()
        reg._timers._sleep = fake
        n = await reg._timers.rearm_from_working(frame, now=time.time())
        assert n == 1
        await reg._timers.tasks[spec["timer_id"]]
        return fake, spec

    fake, spec = asyncio.run(main())
    assert len(fake.calls) == 2
    assert 1.0 < fake.calls[0] <= 2.0, f"首睡须是剩余时长(≈2): {fake.calls}"
    assert fake.calls[1] == 2.0, "其后回到 interval 节奏"
    contents = [m.content for m in frame.context.messages]
    assert len(contents) == 3
    assert "第 2 次/共 3 次" in contents[1] and "第 3 次/共 3 次" in contents[2]
    assert spec["done"] is True and spec["fired"] == 3


def test_rearm_skips_done_and_empty():
    """重武装快路径:无 _timers 键 / 规格已 done → 0 条,不建任务、不注入。"""
    reg, frame = _bound_registry(sleep=_FakeSleep())

    async def main():
        assert await reg._timers.rearm_from_working(frame, now=time.time()) == 0
        now = time.time()
        frame.context.working["_timers"] = [
            {
                "timer_id": "t-done", "run_id": "r1", "frame_id": "f1",
                "delay_seconds": 1.0, "interval_seconds": None, "count": None,
                "fired": 1, "note": "", "created_at": now, "next_fire_at": now + 1,
                "done": True,
            }
        ]
        assert await reg._timers.rearm_from_working(frame, now=time.time()) == 0

    asyncio.run(main())
    assert not reg._timers.tasks
    assert not frame.context.messages


def test_rearm_unbound_service_no_crash():
    """未 bind ctl 的裸 TimerService:重武装静默不炸(fire 防御式丢弃,任务照常结算)。"""
    service = TimerService()
    service._sleep = _FakeSleep()
    frame = SkillFrame(frame_id="f1", run_id="r1")
    now = time.time()
    frame.context.working["_timers"] = [
        {  # 过期 one-shot:补偿 fire 因 ctl 缺位被丢弃,仍标 done 结算
            "timer_id": "t1", "run_id": "r1", "frame_id": "f1",
            "delay_seconds": 5.0, "interval_seconds": None, "count": None,
            "fired": 0, "note": "过期", "created_at": now - 10, "next_fire_at": now - 5,
        },
        {  # 未到期 recurring:重武装建任务,到点 fire 丢弃后正常终结
            "timer_id": "t2", "run_id": "r1", "frame_id": "f1",
            "delay_seconds": None, "interval_seconds": 2.0, "count": 1,
            "fired": 0, "note": "未到期", "created_at": now, "next_fire_at": now + 2,
        },
    ]

    async def main():
        n = await service.rearm_from_working(frame, now=time.time())
        assert n == 2
        specs = frame.context.working["_timers"]
        assert specs[0]["done"] is True, "补偿路径 rearm 持规格引用,直接标 done(不经 ctl)"
        new_id = specs[1]["timer_id"]
        assert new_id != "t2"
        await service.tasks[new_id]  # 假钟到点:fire 丢弃,任务正常终结出表
        return specs

    specs = asyncio.run(main())
    # 无 ctl 时 _run 侧的 fired/done 回写无处可达(规格经 ctl 反查帧),维持重武装时
    # 状态——真实 resume 路径 ctl 恒在(KernelBuilder 装配),此处只保证静默不炸
    assert "done" not in specs[1] and specs[1]["fired"] == 0
    assert not service.tasks


def test_kernel_settle_pending_timers_without_service():
    """runner 侧防御:registry 未持有 timer 服务(裸 Kernel,tools=None)→ 静默跳过,不阻断 resume。"""
    kernel = Kernel()
    frame = SkillFrame(frame_id="f1", run_id="r1")
    now = time.time()
    frame.context.working["_timers"] = [
        {
            "timer_id": "t1", "run_id": "r1", "frame_id": "f1",
            "delay_seconds": 5.0, "interval_seconds": None, "count": None,
            "fired": 0, "note": "", "created_at": now, "next_fire_at": now + 5,
        }
    ]

    async def main():
        return await kernel._settle_pending_timers(frame)

    assert asyncio.run(main()) == 0


def test_timer_spec_checkpoint_json_roundtrip():
    """规格是 JSON 纯 dict:checkpoint 序列化/反序列化往返后 working 整 dict 形状不变。"""
    reg, frame = _bound_registry(sleep=_hang)

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.timer.set",
                args={"interval_seconds": 2, "count": 3, "note": "轮询构建"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        reg.release_run("r1")
        await asyncio.sleep(0)

    asyncio.run(main())
    working = frame.context.working
    restored = json.loads(json.dumps(working, ensure_ascii=False))
    assert restored == working, "working 整 dict 须 JSON 往返不变(规格随 checkpoint 落档的前提)"
    (spec,) = restored["_timers"]
    assert set(spec) == {
        "timer_id", "run_id", "frame_id", "delay_seconds", "interval_seconds",
        "count", "fired", "note", "created_at", "next_fire_at",
    }
    assert spec["interval_seconds"] == 2.0 and spec["count"] == 3 and spec["fired"] == 0
    assert spec["note"] == "轮询构建" and spec["delay_seconds"] is None
