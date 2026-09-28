"""system.timer.set / TimerService 锚点测试(WS1;docs/DESIGN.md §8.3 扩展)。

固定约定:

- 工具面常驻(``with_builtins`` 默认注册;新工具无旧名,不设别名),WRITE 档
  (登记后台任务是对 run 的副作用);工具立即返回 timer_id,计时在后台;
- 参数:one-shot = ``delay_seconds``,recurring = ``interval_seconds``
  (``count`` 缺省无限);两者二选一,缺/并给 → INVALID_ARGS;下限钳制 0.5s(防抖);
- TimerService 按 run_id 分桶;fire 经 ``ctl.inject_message`` 注入
  ``[timer 到点] {note}``(Role.USER/Source.INJECTED);帧/run 终态 → 静默弃(log);
- run 收尾(runner ``_release_run`` → registry ``release_run``)取消本 run 全部
  计时器(进程态,不随 checkpoint 持久化,进程重启即丢);
- ``sleep`` 可注入(本文件用假钟快进/悬挂,同 StallDetector 注入 clock 先例);
- fire 注入通道(ctl)由 KernelBuilder 装配时 bind(未 bind → NOT_FOUND,
  同 user 通道先例;builder 恒装配,所以正常路径总有)。
"""

from __future__ import annotations

import asyncio
import textwrap

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
