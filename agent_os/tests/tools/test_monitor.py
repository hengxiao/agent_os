"""system.monitor.set / system.channel.connect / MonitorService 锚点测试
(ch04 Event-Triggered 三件套之二三;docs/reports/ch04-tools.md monitor_shell/connect_channel)。

固定约定:

- 两工具面常驻(注册点在构造器,§6.1 闸门联动同 timer 先例;新工具无旧名,不设别名);
  system.monitor.set 为 EXEC 档(side_effect=irreversible,同 shell_exec),
  system.channel.connect 为 WRITE 档(data_domains=["fs.*"]);工具立即返回 id,监控在后台;
- 两源共用一张 MonitorService(同 TimerService 生命周期):按 run_id 分桶;命中经
  ``ctl.inject_message`` 注入 ``[monitor 命中] {line}`` / ``[channel 命中] {line}``
  (Role.USER/Source.INJECTED);帧/run 终态 → 静默弃(log);
- shell 源:子进程 ``start_new_session`` 独立进程组,stdout/stderr 合并逐行读;
  到顶(``max_fires``)注入封顶事件并杀进程组;进程退出注入退出事件(带 exit_code);
  杀进程复用 tools/builtins.py ``_kill`` 纪律;resume 重武装 = 从头重跑命令
  (输出重放可能重复命中,replay caveat);
- channel 源:轮询文件 size/mtime,从持久化 ``offset`` 续读新增完整行(末尾残行
  留到下轮);rotation(size < offset)从 0 续读;resume 从 offset 续读**不重放**(锚);
- run 收尾(registry ``release_run``)取消本 run 全部监控,规格**不标 done**;
  规格随帧持久化(``working["_monitors"]``,随 checkpoint 落档);
- ``sleep``/``spawn`` 可注入(本文件用假钟/假进程,同 test_timer.py 先例);
- fire 注入通道(ctl)由 KernelBuilder 装配时 bind(未 bind → NOT_FOUND,
  "monitor 服务未装配",同 timer 先例)。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import textwrap
import time

from agent_os.api.v1 import (
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
from agent_os.tools.monitor import (
    format_cap_text,
    format_exit_text,
    format_match_text,
)


class _FakeSleep:
    """立即返回的快进钟(每拍 ``asyncio.sleep(0)`` 让出循环——channel 轮询是无限循环,
    不让出会饿死事件循环;timer 的有界循环才用得起纯同步假钟)。"""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)
        await asyncio.sleep(0)


async def _hang(delay: float) -> None:
    """永不返回的睡眠(release 取消路径用:轮询永不醒,只剩取消一种结局)。"""
    await asyncio.Event().wait()


class _FakeProc:
    """假子进程:预置行喂完后可 EOF/挂起;``_kill`` 纪律的协作面(killpg 打到伪 pid 被吞)。"""

    def __init__(self, lines=(), returncode: int = 0, hang: bool = False) -> None:
        self._lines = list(lines)
        self._returncode_preset = returncode
        self._hang = hang
        self.returncode: int | None = None
        #: 超 pid_max 的伪 pid:os.killpg 必抛 ProcessLookupError(_kill 吞掉),绝不误杀真进程
        self.pid = 99_999_999
        self.killed = False

    @property
    def stdout(self):
        return self._aiter()

    async def _aiter(self):
        for line in self._lines:
            yield (line + "\n").encode()
            await asyncio.sleep(0)  # 让出循环,模拟逐行到达
        if self._hang:
            await asyncio.Event().wait()

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = self._returncode_preset
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.returncode = -9


class _FakeSpawn:
    """记录式假 spawn(断言 command/cwd/进程组参数;按序返回预置 _FakeProc)。"""

    def __init__(self, *procs: _FakeProc) -> None:
        self.procs = list(procs)
        self.calls: list[dict] = []

    async def __call__(self, command: str, **kw):
        self.calls.append({"command": command, **kw})
        return self.procs.pop(0)


def _kernel_with_frame(frame_id: str = "f1", run_id: str = "r1"):
    """真实 Kernel + 一个 RUNNING 帧(fire 注入的真实落点:frame.context.messages)。"""
    kernel = Kernel()
    frame = SkillFrame(frame_id=frame_id, run_id=run_id)
    kernel.stack.push(frame)
    return kernel, frame


def _ctx(allowed=("system.monitor.set", "system.channel.connect"), workdir=None, read_paths=()):
    return ToolDispatchContext(
        frame=SkillFrame(frame_id="f1", run_id="r1"),
        allowed_tools=list(allowed),
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        workdir=workdir,
        read_paths=list(read_paths),
    )


def _bound_registry(sleep=None, spawn=None):
    """with_builtins + 绑定 ctl 的 registry(替内核持有一个带 RUNNING 帧的 Kernel)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    if sleep is not None:
        reg._monitors._sleep = sleep  # 测试注入假钟(同 StallDetector clock 先例)
    if spawn is not None:
        reg._monitors._spawn = spawn  # 测试注入假子进程工厂
    kernel, frame = _kernel_with_frame()
    reg.bind_monitor(RunControlImpl(kernel))
    return reg, frame


async def _wait_for(cond, timeout: float = 5.0) -> None:
    """轮询等条件为真(后台监控任务异步落地的断言辅助;超时 fail 不死等)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("等待条件超时(监控任务未按预期落地)")


def _contents(frame) -> list[str]:
    return [m.content for m in frame.context.messages]


# ---------------------------------------------------------------------------
# 注册 / 装配
# ---------------------------------------------------------------------------


def test_monitors_registered_in_builtins():
    """两工具在默认工具面里(常驻),无旧名别名;分档与 schema 形状固定。"""
    reg = LocalPythonToolRegistry.with_builtins()
    assert reg.has("system.monitor.set") and reg.has("system.channel.connect")
    for legacy in ("monitor_set", "channel_connect", "monitor_shell", "connect_channel"):
        assert not reg.has(legacy), "新工具不设旧名别名"
    mset = reg.get("system.monitor.set").spec
    assert mset.permission is Permission.EXEC, "起任意子进程,同 shell_exec 按 EXEC 档"
    assert mset.side_effect == "irreversible", "TIER-STANDARDS §1:EXEC 显式标 irreversible"
    assert not mset.cacheable and not mset.concurrent_safe
    assert set(mset.parameters["properties"]) == {"command", "pattern", "note", "max_fires"}
    assert mset.parameters["required"] == ["command"]
    conn = reg.get("system.channel.connect").spec
    assert conn.permission is Permission.WRITE, "登记后台轮询是副作用;读语义靠路径分区"
    assert conn.data_domains == ["fs.*"], "fs 数据域声明(数据层 authZ 解析目标)"
    assert set(conn.parameters["properties"]) == {"path", "pattern", "note", "interval", "max_fires"}
    assert conn.parameters["required"] == ["path"]


def test_unbound_monitor_service_reports_not_found():
    """未 bind fire 注入通道:两工具都按"monitor 服务未装配"报 NOT_FOUND(同 timer 先例)。"""
    reg = LocalPythonToolRegistry.with_builtins()

    async def main():
        mset = await reg.dispatch(
            ToolCall(id="1", name="system.monitor.set", args={"command": "tail -f x"}), _ctx()
        )
        conn = await reg.dispatch(
            ToolCall(id="2", name="system.channel.connect", args={"path": "events.log"}), _ctx()
        )
        return mset, conn

    for res in asyncio.run(main()):
        assert not res.ok and res.error.kind is ToolErrorKind.NOT_FOUND
        assert "monitor 服务未装配" in res.error.message
        assert res.error.hint, "错误须带可执行的下一步建议(§W0-3)"


def test_kernel_builder_binds_monitor_ctl():
    """KernelBuilder 装配链:无 sidecar 时也补装 ctl 并 bind 进 MonitorService(CLI 平价)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    kernel = KernelBuilder(RunConfig()).tools(reg).build()
    assert kernel.ctl is not None, "monitor fire 注入通道需要 ctl(无 sidecar 时补装)"
    assert reg._monitors.bound, "build 应把 ctl 经 bind_monitor 注入 MonitorService"


# ---------------------------------------------------------------------------
# shell 源:命中 / 到顶 / 退出 / 帧终态 / release
# ---------------------------------------------------------------------------


def test_shell_match_injects_into_calling_frame():
    """正则命中的行注入调用帧(USER/INJECTED,带计数文本);进程退出补退出事件;出表。"""
    spawn = _FakeSpawn(_FakeProc(["miss", "hit-1", "also miss", "hit-2"], returncode=3))
    reg, frame = _bound_registry(spawn=spawn)

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.monitor.set",
                args={"command": "some-build", "pattern": "hit", "note": "盯报错"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        monitor_id = res.value["monitor_id"]
        await asyncio.wait_for(reg._monitors.tasks[monitor_id], 5)
        return monitor_id

    monitor_id = asyncio.run(main())
    contents = _contents(frame)
    assert len(contents) == 3, f"2 命中 + 1 退出事件: {contents}"
    assert contents[0].startswith("[monitor 命中] hit-1")
    assert "第 1 次/上限 20" in contents[0] and monitor_id in contents[0]
    assert contents[1].startswith("[monitor 命中] hit-2") and "第 2 次/上限 20" in contents[1]
    assert contents[2].startswith("[monitor 退出]") and "exit_code=3" in contents[2]
    assert "共触发 2 次" in contents[2]
    for msg in frame.context.messages:
        assert msg.role is Role.USER and msg.source is Source.INJECTED
    assert not reg._monitors.tasks and not reg._monitors._by_run and not reg._monitors.procs
    (spec,) = frame.context.working["_monitors"]
    assert spec["done"] is True and spec["fired"] == 2
    call = spawn.calls[0]
    assert call["command"] == "some-build" and call["start_new_session"] is True
    assert call["stdout"] is asyncio.subprocess.PIPE and call["stderr"] is asyncio.subprocess.STDOUT
    assert call["cwd"], "子进程 cwd = 帧工作目录(同 shell_exec 语义)"


def test_shell_real_subprocess_two_hits_and_exit_event():
    """真子进程集成:``printf 'hit1\\nmiss\\nhit2\\n'; sleep 0.2`` + pattern hit → 恰好 2 命中 + 退出事件。"""
    reg, frame = _bound_registry()  # 不注入 spawn:走真 asyncio.create_subprocess_shell

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.monitor.set",
                args={"command": "printf 'hit1\\nmiss\\nhit2\\n'; sleep 0.2", "pattern": "hit"},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        await asyncio.wait_for(reg._monitors.tasks[res.value["monitor_id"]], 10)

    asyncio.run(main())
    hits = [c for c in _contents(frame) if c.startswith("[monitor 命中]")]
    exits = [c for c in _contents(frame) if c.startswith("[monitor 退出]")]
    assert len(hits) == 2, f"恰好 2 次命中注入: {_contents(frame)}"
    assert "hit1" in hits[0] and "hit2" in hits[1], "miss 行不得命中"
    assert len(exits) == 1 and "exit_code=0" in exits[0]
    assert not reg._monitors.tasks and not reg._monitors.procs


def test_shell_cap_injects_cap_event_and_kills():
    """到顶(max_fires=2):2 命中 + 封顶事件,进程组被杀,规格标 done 出表。"""
    proc = _FakeProc(["hit1", "hit2", "hit3", "hit4"])
    spawn = _FakeSpawn(proc)
    reg, frame = _bound_registry(spawn=spawn)

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.monitor.set",
                args={"command": "spammy", "pattern": "hit", "max_fires": 2},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        await asyncio.wait_for(reg._monitors.tasks[res.value["monitor_id"]], 5)

    asyncio.run(main())
    contents = _contents(frame)
    assert len(contents) == 3, f"2 命中 + 1 封顶: {contents}"
    assert contents[2].startswith("[monitor 达上限]") and "上限 2" in contents[2]
    assert proc.killed, "到顶须终止进程组(不留孤儿)"
    (spec,) = frame.context.working["_monitors"]
    assert spec["done"] is True and spec["fired"] == 2
    assert not reg._monitors.tasks and not reg._monitors.procs


def test_frame_terminal_drops_silently():
    """帧终态:命中消息静默弃(不进帧 context),监控停止并杀进程,不残留任务。"""
    proc = _FakeProc(["hit"])
    spawn = _FakeSpawn(proc)
    reg, frame = _bound_registry(spawn=spawn)
    reg._monitors._ctl._kernel.stack.pop_ok(frame, None)  # 帧进终态(DONE)

    async def main():
        res = await reg.dispatch(
            ToolCall(id="1", name="system.monitor.set", args={"command": "x", "pattern": "hit"}),
            _ctx(),
        )
        assert res.ok, res.error
        await asyncio.wait_for(reg._monitors.tasks[res.value["monitor_id"]], 5)

    asyncio.run(main())
    assert not frame.context.messages, "终态帧不得再收注入消息"
    assert proc.killed, "帧终态须终止进程组(监控目的已灭,不留孤儿)"
    assert not reg._monitors.tasks


def test_release_run_cancels_and_kills_process_group():
    """run 收尾:release_run 取消监控并**同步**杀进程组(真子进程断言进程组消失);
    规格不标 done(留给 resume 重武装)。"""
    reg, frame = _bound_registry()  # 真 spawn

    async def main():
        res = await reg.dispatch(
            ToolCall(id="1", name="system.monitor.set", args={"command": "sleep 30"}), _ctx()
        )
        assert res.ok, res.error
        monitor_id = res.value["monitor_id"]
        await _wait_for(lambda: monitor_id in reg._monitors.procs, 2)
        proc = reg._monitors.procs[monitor_id]
        pgid = proc.pid  # start_new_session:子进程自成进程组,pgid == pid
        task = reg._monitors.tasks[monitor_id]
        reg.release_run("r1")  # runner._release_run 的调用形态(registry 链)
        assert not reg._monitors.tasks and not reg._monitors.procs, "release 须同步清表"
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert task.cancelled
        return pgid

    pgid = asyncio.run(main())
    # SIGKILL 投递/收尸有极小窗口,轮询确认进程组真的没了
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        raise AssertionError(f"进程组 {pgid} 在 release_run 后仍然存在(kill 纪律失效)")
    (spec,) = frame.context.working["_monitors"]
    assert "done" not in spec, "取消不得标 done——pause 后 resume 要靠留下的规格重武装"
    assert not frame.context.messages, "无命中无注入(sleep 不出行)"


def test_args_validation():
    """校验矩阵(fail-closed):空命令/坏正则/坏 max_fires/越界路径 → INVALID_ARGS 且带 hint。"""
    reg, _frame = _bound_registry(spawn=_FakeSpawn(_FakeProc(hang=True)))

    async def main():
        empty_cmd = await reg.dispatch(
            ToolCall(id="1", name="system.monitor.set", args={"command": "  "}), _ctx()
        )
        bad_regex = await reg.dispatch(
            ToolCall(id="2", name="system.monitor.set", args={"command": "x", "pattern": "["}), _ctx()
        )
        zero_fires = await reg.dispatch(
            ToolCall(id="3", name="system.monitor.set", args={"command": "x", "max_fires": 0}), _ctx()
        )
        bool_fires = await reg.dispatch(
            ToolCall(id="4", name="system.monitor.set", args={"command": "x", "max_fires": True}),
            _ctx(),
        )
        int_pattern = await reg.dispatch(
            ToolCall(id="5", name="system.monitor.set", args={"command": "x", "pattern": 123}), _ctx()
        )
        escape = await reg.dispatch(
            ToolCall(id="6", name="system.channel.connect", args={"path": "../../etc/passwd"}), _ctx()
        )
        # schema 层之下服务自己的 fail-closed(bool 混 int;True 过不了 jsonschema 的 integer)
        service_bool = reg._monitors.set_shell(run_id="r1", frame_id="f1", command="x", max_fires=True)
        return empty_cmd, bad_regex, zero_fires, bool_fires, int_pattern, escape, service_bool

    results = asyncio.run(main())
    names = ("空命令", "坏正则", "max_fires=0", "max_fires=True(schema)", "pattern=int(schema)", "路径越界", "max_fires=True(服务层)")
    for name, res in zip(names, results, strict=True):
        assert not res.ok, f"{name} 应拒绝: {res}"
        assert res.error.kind is ToolErrorKind.INVALID_ARGS, (name, res.error)
    # schema 闸门拒的(bool/int 型错)不带 hint(分发层现状,同 timer);服务层 fail-closed 必须带
    for name, res in zip(names, results, strict=True):
        if "schema" in name:
            continue
        assert res.error.hint, f"{name}: INVALID_ARGS 须带下一步建议(§W0-3)"
    assert "路径越界" in results[5].error.message, "channel 路径逃逸与 fs 工具同一错误形状"
    assert not reg._monitors.tasks, "非法调用不得建任务"


def test_set_shell_spawns_task_and_persists_spec():
    """set 成功:任务在册、规格落帧 working["_monitors"](键形状/取值全断言);release 不标 done。"""
    spawn = _FakeSpawn(_FakeProc(hang=True))
    reg, frame = _bound_registry(spawn=spawn)

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.monitor.set",
                args={"command": "tail -f x.log", "pattern": "ERR", "note": "盯错", "max_fires": 5},
            ),
            _ctx(),
        )
        assert res.ok, res.error
        monitor_id = res.value["monitor_id"]
        (spec,) = frame.context.working["_monitors"]
        assert set(spec) == {
            "monitor_id", "kind", "run_id", "frame_id", "command", "pattern", "note",
            "max_fires", "fired", "workdir", "created_at",
        }, "shell 规格键形状(持久化契约;done 仅终结时补标)"
        assert spec["monitor_id"] == monitor_id and spec["kind"] == "shell"
        assert spec["run_id"] == "r1" and spec["frame_id"] == "f1"
        assert spec["command"] == "tail -f x.log" and spec["pattern"] == "ERR"
        assert spec["note"] == "盯错" and spec["max_fires"] == 5 and spec["fired"] == 0
        assert spec["workdir"], "workdir 落档(resume 重跑命令的 cwd)"
        reg.release_run("r1")
        await asyncio.sleep(0.05)
        return spec

    spec = asyncio.run(main())
    assert "done" not in spec, "取消不标 done(留给 resume 重武装)"
    assert not reg._monitors.tasks


def test_spec_not_recorded_when_frame_unreachable():
    """帧不可达(不在 kernel 栈):set 仍返回 id,只记 log 不阻断(退回进程态旧行为)。"""
    spawn = _FakeSpawn(_FakeProc(hang=True))
    reg, frame = _bound_registry(spawn=spawn)

    async def main():
        monitor_id = reg._monitors.set_shell(run_id="r1", frame_id="ghost-frame", command="x")
        assert isinstance(monitor_id, str)
        assert monitor_id in reg._monitors.tasks
        reg.release_run("r1")
        await asyncio.sleep(0.05)

    asyncio.run(main())
    assert "_monitors" not in frame.context.working, "帧不可达时规格不落档,也不误写别的帧"


def test_rearm_shell_respawns_command_from_scratch():
    """shell 源重武装:从头重跑命令(spawn 二次调用、同参),fired 续计不重置,id 改写。"""
    spawn = _FakeSpawn(_FakeProc(["hit"], hang=True), _FakeProc(["hit"], hang=True))
    reg, frame = _bound_registry(spawn=spawn)

    async def main():
        res = await reg.dispatch(
            ToolCall(id="1", name="system.monitor.set", args={"command": "tail -f x", "pattern": "hit"}),
            _ctx(),
        )
        assert res.ok, res.error
        old_id = res.value["monitor_id"]
        await _wait_for(lambda: len(frame.context.messages) == 1)  # 首次命中后挂起(hang)
        reg.release_run("r1")  # pause:进程任务取消,规格留下
        await asyncio.sleep(0.05)
        (spec,) = frame.context.working["_monitors"]
        assert spec["fired"] == 1 and "done" not in spec
        n = await reg._monitors.rearm_from_working(frame, now=time.time())
        assert n == 1, "未 done 规格须重武装 1 条"
        assert spec["monitor_id"] != old_id, "重武装须改写 monitor_id(防双火)"
        await _wait_for(lambda: len(frame.context.messages) == 2)  # 输出重放再次命中(replay caveat)
        reg.release_run("r1")
        await asyncio.sleep(0.05)
        return spec

    spec = asyncio.run(main())
    assert len(spawn.calls) == 2, "resume 重武装 = 从头重跑命令"
    assert spawn.calls[0]["command"] == spawn.calls[1]["command"] == "tail -f x"
    assert spawn.calls[0]["cwd"] == spawn.calls[1]["cwd"], "重跑用落档 workdir"
    assert "第 1 次/上限 20" in _contents(frame)[0]
    assert "第 2 次/上限 20" in _contents(frame)[1], "fired 从规格续计(不重置)"
    assert spec["fired"] == 2


# ---------------------------------------------------------------------------
# channel 源:追加 / offset 持久化(不重放)/ rotation / 到顶 / 路径分区
# ---------------------------------------------------------------------------


def test_channel_append_fires(tmp_path):
    """追加的新增行命中注入;offset 随读推进;非命中行不注入。"""
    events = tmp_path / "events.log"
    events.write_text("", encoding="utf-8")
    reg, frame = _bound_registry(sleep=_FakeSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(id="1", name="system.channel.connect", args={"path": "events.log", "pattern": "hit"}),
            _ctx(workdir=tmp_path),
        )
        assert res.ok, res.error
        assert res.value["path"] == str(events.resolve()), "回显解析后的绝对路径"
        with events.open("a", encoding="utf-8") as f:
            f.write("hit-1\nmiss\n")
        await _wait_for(lambda: len(frame.context.messages) == 1)
        channel_id = res.value["channel_id"]
        (spec,) = frame.context.working["_monitors"]
        assert spec["kind"] == "channel" and spec["monitor_id"] == channel_id
        assert spec["offset"] == len("hit-1\nmiss\n"), "offset 随读推进(含未命中行)"
        assert spec["fired"] == 1
        reg.release_run("r1")
        await asyncio.sleep(0.05)

    asyncio.run(main())
    (content,) = _contents(frame)
    assert content.startswith("[channel 命中] hit-1") and "第 1 次/上限 20" in content
    assert not reg._monitors.tasks


def test_channel_rearm_resumes_from_offset_no_replay(tmp_path):
    """offset 持久化锚:release 后 append,resume 从 offset 续读——只补新行,**不重放**旧行。"""
    events = tmp_path / "events.log"
    events.write_text("hit1\n", encoding="utf-8")
    reg, frame = _bound_registry(sleep=_FakeSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(id="1", name="system.channel.connect", args={"path": "events.log", "pattern": "hit"}),
            _ctx(workdir=tmp_path),
        )
        assert res.ok, res.error
        old_id = res.value["channel_id"]
        await _wait_for(lambda: len(frame.context.messages) == 1)  # hit1 已读
        reg.release_run("r1")  # pause:offset 已落档(5),任务取消,规格不标 done
        await asyncio.sleep(0.05)
        (spec,) = frame.context.working["_monitors"]
        assert spec["offset"] == len("hit1\n") and spec["fired"] == 1 and "done" not in spec
        with events.open("a", encoding="utf-8") as f:
            f.write("hit2\n")  # 暂停期间的追加 = 新行
        n = await reg._monitors.rearm_from_working(frame, now=time.time())
        assert n == 1 and spec["monitor_id"] != old_id
        await _wait_for(lambda: len(frame.context.messages) == 2)  # 只补 hit2
        reg.release_run("r1")
        await asyncio.sleep(0.05)
        return spec

    spec = asyncio.run(main())
    contents = _contents(frame)
    assert len(contents) == 2, f"不重放:全程恰好 2 条(hit1 + hit2): {contents}"
    assert "hit1" in contents[0] and "hit2" in contents[1], "旧行不得二次注入"
    assert "第 2 次/上限 20" in contents[1], "fired 续计"
    assert spec["offset"] == len("hit1\nhit2\n") and spec["fired"] == 2


def test_channel_rotation_continues_from_zero(tmp_path):
    """rotation(截断重写,size < offset):记 log 一次并从文件头续读,新内容照常命中。"""
    events = tmp_path / "events.log"
    events.write_text("aaa\nbbb\n", encoding="utf-8")
    reg, frame = _bound_registry(sleep=_FakeSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(id="1", name="system.channel.connect", args={"path": "events.log"}),  # 空 pattern = 每行
            _ctx(workdir=tmp_path),
        )
        assert res.ok, res.error
        await _wait_for(lambda: len(frame.context.messages) == 2)  # aaa/bbb 已读(offset=8)
        (spec,) = frame.context.working["_monitors"]
        assert spec["offset"] == len("aaa\nbbb\n")
        events.write_text("x\n", encoding="utf-8")  # 截断重写:size 2 < offset 8
        await _wait_for(lambda: len(frame.context.messages) == 3)
        reg.release_run("r1")
        await asyncio.sleep(0.05)
        return spec

    spec = asyncio.run(main())
    contents = _contents(frame)
    assert contents[2].startswith("[channel 命中] x"), f"rotation 后从 0 续读: {contents}"
    assert spec["offset"] == len("x\n") and spec["fired"] == 3


def test_channel_cap_stops(tmp_path):
    """channel 到顶:2 命中 + 封顶事件,任务终结出表,规格标 done。"""
    events = tmp_path / "events.log"
    events.write_text("a\nb\nc\n", encoding="utf-8")
    reg, frame = _bound_registry(sleep=_FakeSleep())

    async def main():
        res = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.channel.connect",
                args={"path": "events.log", "max_fires": 2},
            ),
            _ctx(workdir=tmp_path),
        )
        assert res.ok, res.error
        channel_id = res.value["channel_id"]
        await asyncio.wait_for(reg._monitors.tasks[channel_id], 5)

    asyncio.run(main())
    contents = _contents(frame)
    assert len(contents) == 3, f"2 命中 + 1 封顶(c 行不再注入): {contents}"
    assert contents[2].startswith("[channel 达上限]") and "上限 2" in contents[2]
    (spec,) = frame.context.working["_monitors"]
    assert spec["done"] is True and spec["fired"] == 2
    assert not reg._monitors.tasks


def test_channel_read_paths_zone_allowed(tmp_path):
    """read_paths 只读区内的文件可监控(write=False 读语义);区外越界拒绝。"""
    wd = tmp_path / "wd"
    ro = tmp_path / "ro"
    wd.mkdir()
    ro.mkdir()
    events = ro / "events.log"
    events.write_text("", encoding="utf-8")
    reg, frame = _bound_registry(sleep=_hang)

    async def main():
        res = await reg.dispatch(
            ToolCall(id="1", name="system.channel.connect", args={"path": str(events)}),
            _ctx(workdir=wd, read_paths=[ro]),
        )
        assert res.ok, res.error
        assert res.value["path"] == str(events.resolve())
        reg.release_run("r1")
        await asyncio.sleep(0.05)

    asyncio.run(main())
    (spec,) = frame.context.working["_monitors"]
    assert spec["kind"] == "channel" and spec["path"] == str(events.resolve())
    assert not reg._monitors.tasks


# ---------------------------------------------------------------------------
# 规格持久化 / resume 结算序列
# ---------------------------------------------------------------------------


def test_monitor_spec_checkpoint_json_roundtrip(tmp_path):
    """两种规格都是 JSON 纯 dict:checkpoint 序列化/反序列化往返后 working 整 dict 形状不变。"""
    events = tmp_path / "events.log"
    events.write_text("", encoding="utf-8")
    spawn = _FakeSpawn(_FakeProc(hang=True))
    reg, frame = _bound_registry(sleep=_hang, spawn=spawn)

    async def main():
        mset = await reg.dispatch(
            ToolCall(
                id="1",
                name="system.monitor.set",
                args={"command": "tail -f x", "pattern": "ERR", "note": "盯错", "max_fires": 5},
            ),
            _ctx(),
        )
        conn = await reg.dispatch(
            ToolCall(
                id="2",
                name="system.channel.connect",
                args={"path": "events.log", "pattern": "hit", "interval": 0.01},
            ),
            _ctx(workdir=tmp_path),
        )
        assert mset.ok and conn.ok
        assert conn.value["interval"] == 0.5, "interval 下限钳制 0.5s(防抖)"
        reg.release_run("r1")
        await asyncio.sleep(0.05)

    asyncio.run(main())
    working = frame.context.working
    restored = json.loads(json.dumps(working, ensure_ascii=False))
    assert restored == working, "working 整 dict 须 JSON 往返不变(规格随 checkpoint 落档的前提)"
    shell_spec, channel_spec = restored["_monitors"]
    assert set(shell_spec) == {
        "monitor_id", "kind", "run_id", "frame_id", "command", "pattern", "note",
        "max_fires", "fired", "workdir", "created_at",
    }
    assert set(channel_spec) == {
        "monitor_id", "kind", "run_id", "frame_id", "path", "pattern", "note",
        "interval", "max_fires", "fired", "offset", "created_at",
    }
    assert channel_spec["interval"] == 0.5 and channel_spec["offset"] == 0


def test_kernel_settle_pending_monitors_without_service():
    """runner 侧防御:registry 未持有 monitor 服务(裸 Kernel,tools=None)→ 静默跳过,不阻断 resume。"""
    kernel = Kernel()
    frame = SkillFrame(frame_id="f1", run_id="r1")
    frame.context.working["_monitors"] = [
        {
            "monitor_id": "m1", "kind": "channel", "run_id": "r1", "frame_id": "f1",
            "path": "/tmp/x", "pattern": "", "note": "", "interval": 0.5,
            "max_fires": 20, "fired": 0, "offset": 0, "created_at": time.time(),
        }
    ]

    async def main():
        return await kernel._settle_pending_monitors(frame)

    assert asyncio.run(main()) == 0


def test_settle_timers_and_monitors_on_same_frame_independently(tmp_path):
    """结算序列:同一帧挂计时器+监控两种规格,``_settle_pending_timers`` 与
    ``_settle_pending_monitors`` 各自重武装、互不干扰(计时器悬挂不到点,监控照常命中)。"""
    events = tmp_path / "events.log"
    events.write_text("init\n", encoding="utf-8")
    reg = LocalPythonToolRegistry.with_builtins()
    kernel = Kernel(tools=reg)
    frame = SkillFrame(frame_id="f1", run_id="r1")
    kernel.stack.push(frame)
    ctl = RunControlImpl(kernel)
    reg.bind_timer(ctl)
    reg.bind_monitor(ctl)
    now = time.time()
    frame.context.working["_timers"] = [
        {
            "timer_id": "t-planted", "run_id": "r1", "frame_id": "f1",
            "delay_seconds": 3600.0, "interval_seconds": None, "count": None,
            "fired": 0, "note": "长跑提醒", "created_at": now, "next_fire_at": now + 3600,
        }
    ]
    frame.context.working["_monitors"] = [
        {
            "monitor_id": "m-planted", "kind": "channel", "run_id": "r1", "frame_id": "f1",
            "path": str(events.resolve()), "pattern": "", "note": "", "interval": 0.5,
            "max_fires": 20, "fired": 0, "offset": 0, "created_at": now,
        }
    ]

    async def main():
        reg._timers._sleep = _hang  # 重武装后悬挂(不到点,停在在册态)
        reg._monitors._sleep = _FakeSleep()
        n_timers = await kernel._settle_pending_timers(frame)
        n_monitors = await kernel._settle_pending_monitors(frame)
        assert n_timers == 1 and n_monitors == 1, "两条 settle 各重武装 1 条"
        await _wait_for(lambda: len(frame.context.messages) == 1)
        reg.release_run("r1")
        await asyncio.sleep(0.05)

    asyncio.run(main())
    (tspec,) = frame.context.working["_timers"]
    (mspec,) = frame.context.working["_monitors"]
    assert tspec["timer_id"] != "t-planted" and "done" not in tspec, "计时器重武装悬挂,未被监控结算干扰"
    assert mspec["monitor_id"] != "m-planted" and mspec["fired"] == 1
    assert "done" not in mspec, "release 取消不标 done(留给 resume)"
    (content,) = _contents(frame)
    assert content.startswith("[channel 命中] init"), "计时器未 fire,唯一注入来自监控"
    assert not reg._timers.tasks and not reg._monitors.tasks


# ---------------------------------------------------------------------------
# 纯函数出口 / 端到端接线
# ---------------------------------------------------------------------------


def test_format_texts_are_the_single_shape():
    """注入文本唯一构造点:kind 定标签;行截断 200;退出/封顶文本形状固定。"""
    assert (
        format_match_text("shell", "m1", "build failed", 1, 20)
        == "[monitor 命中] build failed(monitor_id=m1,第 1 次/上限 20)"
    )
    assert (
        format_match_text("channel", "c2", "done", 3, 5)
        == "[channel 命中] done(monitor_id=c2,第 3 次/上限 5)"
    )
    long_text = format_match_text("shell", "m1", "x" * 250, 1, 20)
    assert long_text == f"[monitor 命中] {'x' * 200}(monitor_id=m1,第 1 次/上限 20)", "行截断 200"
    assert format_exit_text("m1", 3, 2) == "[monitor 退出] 进程结束(exit_code=3)(monitor_id=m1,共触发 2 次)"
    assert format_cap_text("shell", "m1", 2) == "[monitor 达上限] 触发次数达上限 2,监控终止(monitor_id=m1)"
    assert format_cap_text("channel", "c2", 2) == "[channel 达上限] 触发次数达上限 2,监控终止(monitor_id=c2)"


def test_kernel_run_release_cancels_monitors(tmp_path):
    """端到端接线:run 内 code 技能调 system.monitor.set;run 收尾后任务/进程表清空。"""
    skills_yaml = tmp_path / "skills.yaml"
    skills_yaml.write_text(
        "skills:\n"
        + textwrap.dedent(
            """
            - name: test.set_monitor
              version: 1.0.0
              kind: code
              handler: tests.helpers.code_skills:monitor_probe
              inputs:
                type: object
                properties: { args: { type: object } }
              outputs:
                type: object
                properties: { monitor_id: { type: string } }
                required: [monitor_id]
              permissions: { tools: [system.monitor.set], skills: [] }
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
        # run() 的 finally 在返回前完成 _release_run:返回点监控已取消、进程组已杀
        return await kernel.run("test.set_monitor", {"args": {"command": "sleep 30"}})

    result = asyncio.run(main())
    assert result["monitor_id"], "工具须立即返回 monitor_id(监控在后台)"
    assert not reg._monitors.tasks and not reg._monitors._by_run and not reg._monitors.procs, (
        "run 收尾(_release_run → registry.release_run)须取消本 run 全部监控"
    )
