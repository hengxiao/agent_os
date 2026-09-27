"""run 边界资源回收锚点测试(审计 §7.3)。

固定约定:run 结束(成功/失败/中止)后,内核回收三类"只增不减"的登记:

- **后台帧**(§3.4 spawn):run 已结算,残留任务不得继续记账进已结算的 usage;
- **telemetry 句柄**:flush + fsync + close(不关则每 run 泄一个常驻 fd,
  且归档 trace 可能拷到半行);
- **工具临时工作目录**:``mkdtemp`` 的结果须删(不删则长驻宿主把 /tmp 塞满)。

回归的是审计实测的一组缺陷:全仓原先没有任何回收点,Web 长驻宿主跑够多
run 必然 fd 耗尽 + 磁盘塞满。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_os.api.v1 import Permission, RunConfig, ToolPolicy
from agent_os.kernel.errors import OutputValidationError
from agent_os.telemetry import JsonlTelemetrySink
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import FIB_SKILLS_YAML, assemble, sandbox_tools


def _kernel(tmp_path, telemetry_dir=None):
    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(
        config,
        fib_brain,
        FIB_SKILLS_YAML,
        tools=sandbox_tools(builtins=True),
        telemetry_dir=telemetry_dir,
    )


def test_tool_workdir_removed_after_run(tmp_path):
    """run 结束后,该 run 的临时工作目录被删除且登记被清空。"""
    kernel = _kernel(tmp_path)
    asyncio.run(kernel.run("demo.fib", {"n": 3}))

    registry = kernel.tools
    assert registry._workdirs == {}, "workdir 登记表未清空(只增不减 → /tmp 泄漏)"


def test_workdir_created_during_run_then_gone(tmp_path):
    """更强的断言:run 期间目录确实存在,收尾后确实不在了。"""
    kernel = _kernel(tmp_path)
    seen: list[Path] = []

    original = kernel.tools._workdir

    def spy(run_id: str) -> str:
        wd = original(run_id)
        seen.append(Path(wd))
        return wd

    kernel.tools._workdir = spy  # type: ignore[method-assign]
    asyncio.run(kernel.run("demo.fib", {"n": 3}))

    assert seen, "本 run 未用到工具工作目录,测试前提不成立"
    assert not seen[0].exists(), f"临时目录未被回收: {seen[0]}"


def test_telemetry_handle_closed_and_flushed(tmp_path):
    """telemetry:run 收尾后句柄关闭,且 trace 完整落盘(可直接读到终止信号)。"""
    tdir = tmp_path / "traces"
    sink = JsonlTelemetrySink(str(tdir))
    kernel = _kernel(tmp_path, telemetry_dir=None)
    kernel.telemetry = sink
    kernel.signals.subscribe("*", sink.record)

    asyncio.run(kernel.run("demo.fib", {"n": 2}))

    assert sink._files == {}, "run 收尾未关闭 telemetry 句柄(每 run 泄一个 fd)"
    traces = list(tdir.glob("*.jsonl"))
    assert traces, "trace 未落盘"
    lines = traces[0].read_text(encoding="utf-8").splitlines()
    assert any("run.finished" in line for line in lines), "尾部信号未 flush 到磁盘"


def test_release_is_idempotent_and_survives_failure(tmp_path):
    """失败 run 同样回收;重复回收不报错(收尾路径不该改变 run 结果)。"""
    from tests.helpers.brains import bad_brain

    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    kernel = assemble(config, bad_brain, FIB_SKILLS_YAML, tools=sandbox_tools(builtins=True))
    with pytest.raises(OutputValidationError):  # 输出校验连败 → 帧失败上抛,属预期
        asyncio.run(kernel.run("demo.fib", {"n": 1}))
    assert kernel.tools._workdirs == {}, "失败 run 也必须回收"

    # 二次回收(幂等)
    asyncio.run(kernel._release_run("nonexistent-run"))


def test_release_run_isolated_per_run(tmp_path):
    """跨 run 隔离(WS1):run A 的 _release_run 只取消本 run 桶的后台帧,
    不杀共享 kernel 上 run B 的在跑后台帧(并发 run 回归)。"""
    kernel = _kernel(tmp_path)

    async def main():
        task_a = asyncio.create_task(asyncio.sleep(60))
        task_b = asyncio.create_task(asyncio.sleep(60))
        kernel._spawned.setdefault("run-a", {})["frame-a"] = ("parent-a", task_a)
        kernel._spawned.setdefault("run-b", {})["frame-b"] = ("parent-b", task_b)

        await kernel._release_run("run-a")
        assert task_a.cancelled(), "本 run 桶的后台帧应被取消"
        assert not task_b.done(), "run A 收尾不得误杀 run B 的在跑后台帧"
        assert "run-a" not in kernel._spawned and "run-b" in kernel._spawned

        await kernel._release_run("run-b")
        assert task_b.cancelled()
        assert kernel._spawned == {}, "两个 run 桶都应已回收"

    asyncio.run(main())


def test_resume_releases_spawned_registrations(tmp_path):
    """resume 收尾与 run() 对称(WS3 缺口):finally 必须调 _release_run——
    resume 路径里 spawn/parallel 登记的后台帧在 run 结束后不得滞留 _spawned。"""
    from agent_os.api.v1 import RUN_STARTED, Signal
    from tests.helpers.brains import PowerCut, power_cut_brain
    from tests.helpers.kernels import fib_kernel

    kernel1 = fib_kernel(power_cut_brain(cut_at=4))
    started: list[Signal] = []

    async def rec(sig: Signal) -> None:
        started.append(sig)

    kernel1.signals.subscribe(RUN_STARTED, rec)
    with pytest.raises(PowerCut):
        asyncio.run(kernel1.run("demo.fib", {"n": 5}))
    run_id = started[0].run_id
    ckpt = tmp_path / "ckpt.json"
    kernel1.checkpoint(run_id, str(ckpt))

    kernel2 = fib_kernel(fib_brain)  # 新内核从 checkpoint 恢复

    async def main():
        # 模拟 resume 期间 spawn/parallel 登记的后台帧(run_id 与 checkpoint 相同)
        task = asyncio.create_task(asyncio.sleep(60))
        kernel2._spawned.setdefault(run_id, {})["frame-x"] = ("parent-x", task)
        result = await kernel2.resume(str(ckpt))
        assert result == {"seq": [0, 1, 1, 2, 3]}
        assert kernel2._spawned == {}, "resume 收尾未回收 _spawned(WS3 缺口)"
        assert task.cancelled(), "滞留的后台帧任务应被取消"

    asyncio.run(main())
