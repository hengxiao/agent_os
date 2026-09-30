"""E5 宿主调度器锚点测试(host/shared/scheduler.py;§17-3/§8.3/D3 统一调度)。

固定约定:

- ``ScheduleStore``:``{"v": 1, "pending": [...]}``,写一律原子(tmp+os.replace);
  文件缺席 = 空表,畸形文件不杀读取(记 log 按空表起步);
- ``scan_due_timers``:只扫 result.json ``status=="paused"`` 的 run,逐帧(跳 DONE)
  过 ``settle_overdue``;畸形文件跳过该 run(扫描永不杀节拍);
- ``settle_run_checkpoint``:settle-in-file + fire 文本追加根帧消息(逐字对齐
  ``_message_to_dict`` 落盘形状)+ 原子写回;**必须先于 resume**(双火协调);
- ``Scheduler.tick(now)`` 同步本体:持久事件到点条目 pop → ``dispatch_event``
  (同 POST /api/events 路由),失败丢弃无死信;paused run 过期计时器 settle 后
  逐 run 独立 daemon 线程 resume;逐条目/逐 run 异常隔离;
- **双火锚点**:调度器 settle 后的 checkpoint 被内核 resume 时,rearm 见到已
  结算规格不再补火——过期的一次由追加文本送达,恰好一次。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from agent_os.api.v1 import (
    Allow,
    Mode,
    Permission,
    Principal,
    RunConfig,
    Signal,
    ToolPolicy,
)
from agent_os.host.shared.artifacts import _write_json
from agent_os.host.shared.scheduler import (
    Scheduler,
    ScheduleStore,
    new_entry,
    scan_due_timers,
    settle_run_checkpoint,
)
from agent_os.host.web.run_manager import ResumeConflictError
from agent_os.kernel.errors import RunPaused
from tests.helpers.brains import fib_brain
from tests.helpers.kernels import FIB_SKILLS_YAML, assemble

# ---------------------------------------------------------------------------
# ScheduleStore
# ---------------------------------------------------------------------------


def _entry(**over) -> dict[str, Any]:
    base = new_entry(
        event_type=over.pop("event_type", "cron.tick"),
        payload={"msg": "整点"},
        skill="demo.fib",
        input={"n": 2},
        fire_at=1000.0,
        source="api",
    )
    base.update(over)
    return base


def test_store_roundtrip_and_persistence(tmp_path):
    """add/pop 随改随存(原子);新实例重载见到同一份表(crash-safe)。"""
    store = ScheduleStore(tmp_path / "schedule.json")
    assert store.pending() == [], "缺文件 = 空表"
    e1 = _entry(fire_at=1000.0)
    e2 = _entry(fire_at=2000.0)
    store.add(e1)
    store.add(e2)

    doc = json.loads((tmp_path / "schedule.json").read_text(encoding="utf-8"))
    assert doc["v"] == 1 and len(doc["pending"]) == 2, "文件形状 {v: 1, pending: [...]}"

    reloaded = ScheduleStore(tmp_path / "schedule.json")
    assert [e["id"] for e in reloaded.pending()] == [e1["id"], e2["id"]]

    popped = reloaded.pop(e1["id"])
    assert popped["id"] == e1["id"]
    assert reloaded.pop("不存在的 id") is None
    again = ScheduleStore(tmp_path / "schedule.json")
    assert [e["id"] for e in again.pending()] == [e2["id"]], "pop 后落盘须已摘除"


def test_store_due_query(tmp_path):
    """due(now):fire_at <= now 的条目;不摘除;fire_at 畸形跳过(留表不静默丢)。"""
    store = ScheduleStore(tmp_path / "schedule.json")
    past, future, broken = (
        _entry(fire_at=500.0),
        _entry(fire_at=1500.0),
        _entry(fire_at=" soon"),
    )
    for e in (past, future, broken):
        store.add(e)
    due = store.due(1000.0)
    assert [e["id"] for e in due] == [past["id"]]
    assert len(store.pending()) == 3, "due 只读快照,不摘除"


def test_store_malformed_file_tolerated(tmp_path):
    """畸形存储(JSON 坏/版本或形状非法)→ 记 log 按空表起步,不抛;下一次写才覆盖。"""
    path = tmp_path / "schedule.json"
    path.write_text("{oops", encoding="utf-8")
    assert ScheduleStore(path).pending() == []
    path.write_text(json.dumps({"v": 2, "pending": []}), encoding="utf-8")
    assert ScheduleStore(path).pending() == []
    path.write_text(json.dumps({"v": 1, "pending": [{"无 id": True}, _entry()]}), encoding="utf-8")
    store = ScheduleStore(path)
    assert len(store.pending()) == 1, "缺 id 的畸形条目被滤掉"


# ---------------------------------------------------------------------------
# scan_due_timers / settle_run_checkpoint
# ---------------------------------------------------------------------------


def _overdue_spec(run_id: str, frame_id: str = "root-frame", **over) -> dict[str, Any]:
    """过期 one-shot 规格(形状与 tools/timer.py set() 落档的一致)。"""
    now = time.time()
    base = {
        "timer_id": "planted-1shot",
        "run_id": run_id,
        "frame_id": frame_id,
        "delay_seconds": 3600.0,
        "interval_seconds": None,
        "count": None,
        "fired": 0,
        "note": "断电前安排的复查",
        "created_at": now - 3610,
        "next_fire_at": now - 5,
    }
    base.update(over)
    return base


def _paused_run_dir(
    root: Path,
    run_id: str,
    specs: list[dict],
    *,
    frame_status: str = "running",
    with_messages: bool = True,
) -> Path:
    """手工搭 paused run 产物目录(result.json + checkpoint.json)。"""
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True)
    _write_json(
        run_dir / "result.json",
        {"status": "paused", "result": None, "error": "RunPaused: x", "usage": {}},
    )
    context: dict[str, Any] = {"working": {"_timers": specs}}
    if with_messages:
        context["messages"] = []
    frame = {
        "frame_id": "root-frame",
        "parent_id": None,
        "depth": 0,
        "skill": "demo.fib",
        "status": frame_status,
        "context": context,
    }
    _write_json(
        run_dir / "checkpoint.json",
        {"v": 1, "run": {"run_id": run_id, "usage": {}}, "frames": [frame]},
    )
    return run_dir


def test_scan_due_timers_paused_only(tmp_path):
    """只报 paused run 的过期规格;done/无产物/未到期/DONE 帧/缺规格列一律不报。"""
    now = time.time()
    _paused_run_dir(tmp_path, "run-paused", [_overdue_spec("run-paused")])
    _paused_run_dir(tmp_path, "run-notdue", [_overdue_spec("run-notdue", next_fire_at=now + 3600)])
    _paused_run_dir(tmp_path, "run-done-frame", [_overdue_spec("run-done-frame")], frame_status="done")
    _paused_run_dir(tmp_path, "run-empty", [])
    # 非 paused 的 run(done 终态)
    done_dir = tmp_path / "runs" / "run-done"
    done_dir.mkdir(parents=True)
    _write_json(done_dir / "result.json", {"status": "done", "result": {}, "error": None, "usage": {}})
    _write_json(done_dir / "checkpoint.json", {"v": 1, "run": {"run_id": "run-done"}, "frames": []})

    hits = scan_due_timers(tmp_path, now)
    assert [(h["run_id"], h["frame_id"], h["spec_index"]) for h in hits] == [
        ("run-paused", "root-frame", 0)
    ]
    assert hits[0]["fire_text"].startswith("[timer 到点] 断电前安排的复查")
    assert "第 1 次" in hits[0]["fire_text"]


def test_scan_due_timers_malformed_files_skip(tmp_path):
    """result.json / checkpoint.json 畸形 → 跳过该 run,扫描不炸、其它 run 照常。"""
    now = time.time()
    bad = tmp_path / "runs" / "run-bad"
    bad.mkdir(parents=True)
    (bad / "result.json").write_text("{oops", encoding="utf-8")
    (bad / "checkpoint.json").write_text("{}", encoding="utf-8")
    bad2 = tmp_path / "runs" / "run-bad2"
    bad2.mkdir(parents=True)
    _write_json(bad2 / "result.json", {"status": "paused"})
    (bad2 / "checkpoint.json").write_text("{oops", encoding="utf-8")
    _paused_run_dir(tmp_path, "run-good", [_overdue_spec("run-good")])

    hits = scan_due_timers(tmp_path, now)
    assert [h["run_id"] for h in hits] == ["run-good"]


def test_settle_run_checkpoint_writes_settled_spec_and_message(tmp_path):
    """settle-in-file:规格标 done + fire 文本追加根帧消息(8 键落盘形状)+ 原子写回。"""
    now = time.time()
    run_dir = _paused_run_dir(tmp_path, "run-a", [_overdue_spec("run-a")])
    ckpt = run_dir / "checkpoint.json"
    before = json.loads(ckpt.read_text(encoding="utf-8"))

    texts = settle_run_checkpoint(ckpt, now)

    assert texts == ["[timer 到点] 断电前安排的复查(timer_id=planted-1shot,第 1 次)"]
    doc = json.loads(ckpt.read_text(encoding="utf-8"))
    (spec,) = doc["frames"][0]["context"]["working"]["_timers"]
    assert spec["done"] is True and spec["fired"] == 1 and spec["next_fire_at"] == now
    (msg,) = doc["frames"][0]["context"]["messages"]
    assert msg == {
        "role": "user",
        "content": texts[0],
        "tool_calls": [],
        "tool_call_id": None,
        "name": None,
        "reasoning": None,
        "source": "injected",
        "meta": {"event": {"type": "unknown"}},  # 计时器文本无 [event:] 前缀
    }
    assert len(json.dumps(doc)) >= len(json.dumps(before))
    # 幂等:同一 now 再结算 → 无新文本,文件不再动
    assert settle_run_checkpoint(ckpt, now) == []


def test_settle_run_checkpoint_fail_closed_without_messages(tmp_path):
    """根帧缺消息列 → 不结算不写回(fail-closed:fire 无处送达则本拍作废)。"""
    run_dir = _paused_run_dir(tmp_path, "run-a", [_overdue_spec("run-a")], with_messages=False)
    ckpt = run_dir / "checkpoint.json"
    before = ckpt.read_text(encoding="utf-8")
    assert settle_run_checkpoint(ckpt, time.time()) == []
    assert ckpt.read_text(encoding="utf-8") == before, "无处送达时文件须原样保留"


def test_settle_run_checkpoint_unreadable_returns_empty(tmp_path):
    """checkpoint 缺失/畸形 → [] 不抛(调用方放弃本拍,不杀节拍)。"""
    assert settle_run_checkpoint(tmp_path / "runs" / "ghost" / "checkpoint.json", time.time()) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert settle_run_checkpoint(bad, time.time()) == []


# ---------------------------------------------------------------------------
# Scheduler.tick(spy manager)
# ---------------------------------------------------------------------------


class _SpyManager:
    """dispatch_event / resume_run / state_of 的 spy(逐条记录;可编程失败/状态)。"""

    _MISSING: ClassVar[Any] = object()

    def __init__(self) -> None:
        self.dispatched: list[tuple[dict, Any]] = []
        self.resumed: list[str] = []
        self.dispatch_plan: dict[str, Any] = {}  # entry type → dict 结果(可 None)或 Exception
        self.states: dict[str, dict] = {}
        self.interval_value = 9.0

    def state_of(self, run_id: str):
        return self.states.get(run_id)

    def schedule_section(self):
        return SimpleNamespace(interval_seconds=self.interval_value)

    async def dispatch_event(self, body: dict, *, principal=None):
        self.dispatched.append((body, principal))
        planned = self.dispatch_plan.get(body.get("type"), self._MISSING)
        if isinstance(planned, Exception):
            raise planned
        if planned is self._MISSING:
            return {"ok": True, "action": "started", "run_id": "run-new"}
        return planned

    async def resume_run(self, run_id: str, inject: list[str] | None = None):
        self.resumed.append(run_id)
        return {"run_id": run_id, "status": "done"}


def _scheduler(tmp_path, manager=None) -> tuple[Scheduler, ScheduleStore, _SpyManager]:
    store = ScheduleStore(tmp_path / "schedule.json")
    manager = manager or _SpyManager()
    return Scheduler(manager, store, tmp_path), store, manager


def test_tick_dispatches_due_entries_and_pops(tmp_path):
    """到点条目:pop → dispatch_event(body 与 POST /api/events 同形;principal 重建);未到期不动。"""
    scheduler, store, manager = _scheduler(tmp_path)
    due = _entry(fire_at=500.0, principal={"subject": "user:alice", "issuer": "api-token", "attrs": {}})
    future = _entry(fire_at=1500.0)
    store.add(due)
    store.add(future)

    stats = scheduler.tick(1000.0)

    assert stats == {"dispatched": 1, "dropped": 0, "resumed": 0, "timer_fires": 0}
    (body, principal) = manager.dispatched[0]
    assert body == {
        "type": "cron.tick",
        "payload": {"msg": "整点"},
        "skill": "demo.fib",
        "input": {"n": 2},
        "wait": False,
    }
    assert isinstance(principal, Principal) and principal.subject == "user:alice"
    assert [e["id"] for e in store.pending()] == [future["id"]], "派发过的条目已摘除;未到期留表"


def test_tick_per_item_isolation(tmp_path):
    """逐条隔离:第一条派发抛错(目标终态/消失)→ 丢弃;第二条照常派发;tick 不炸。"""
    scheduler, store, manager = _scheduler(tmp_path)
    manager.dispatch_plan["gone"] = FileNotFoundError("找不到 run: run-x")
    manager.dispatch_plan["terminal"] = ResumeConflictError("已终态")
    store.add(_entry(event_type="gone", fire_at=100.0))
    store.add(_entry(event_type="ok", fire_at=100.0))

    stats = scheduler.tick(1000.0)

    assert stats["dispatched"] == 1 and stats["dropped"] == 1
    assert [b["type"] for b, _ in manager.dispatched] == ["gone", "ok"]
    assert store.pending() == [], "失败条目丢弃(v1 无死信)"


def test_tick_drops_on_unexpected_action(tmp_path):
    """dispatch_event 返回未知 action(如 None/空 dict)→ 丢弃 + 计数(v1 无死信)。"""
    scheduler, store, manager = _scheduler(tmp_path)
    manager.dispatch_plan["weird"] = None
    store.add(_entry(event_type="weird", fire_at=100.0))
    stats = scheduler.tick(1000.0)
    assert stats == {"dispatched": 0, "dropped": 1, "resumed": 0, "timer_fires": 0}
    assert store.pending() == []


def test_tick_settles_paused_run_and_resumes_in_thread(tmp_path):
    """paused run 过期计时器:settle-in-file(规格 done + 文本追加)后 spawn daemon 线程 resume。"""
    scheduler, _store, manager = _scheduler(tmp_path)
    run_dir = _paused_run_dir(tmp_path, "run-p1", [_overdue_spec("run-p1")])

    stats = scheduler.tick(time.time())

    assert stats["resumed"] == 1 and stats["timer_fires"] == 1
    assert len(scheduler._spawned) == 1
    for t in scheduler._spawned:
        t.join(timeout=10)
    assert manager.resumed == ["run-p1"]
    doc = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    (spec,) = doc["frames"][0]["context"]["working"]["_timers"]
    assert spec["done"] is True, "resume 前规格已结算(内核 rearm 不再补火)"
    (msg,) = doc["frames"][0]["context"]["messages"]
    assert msg["content"].startswith("[timer 到点]")


def test_tick_skips_run_whose_resume_in_flight(tmp_path):
    """manager 内存态 running(resume 在途/并发拍)→ 本拍跳过:settle/resume 都不做。"""
    scheduler, _, manager = _scheduler(tmp_path)
    run_dir = _paused_run_dir(tmp_path, "run-p1", [_overdue_spec("run-p1")])
    manager.states["run-p1"] = {"status": "running"}
    before = (run_dir / "checkpoint.json").read_text(encoding="utf-8")

    stats = scheduler.tick(time.time())

    assert stats == {"dispatched": 0, "dropped": 0, "resumed": 0, "timer_fires": 0}
    assert manager.resumed == []
    assert (run_dir / "checkpoint.json").read_text(encoding="utf-8") == before


def test_tick_timer_path_isolated_from_entry_path(tmp_path):
    """事件路径与计时器路径互不影响:一边失败另一边照常;畸形 run 目 录不杀节拍。"""
    scheduler, store, manager = _scheduler(tmp_path)
    manager.dispatch_plan["boom"] = RuntimeError("装配炸了")
    store.add(_entry(event_type="boom", fire_at=100.0))
    bad = tmp_path / "runs" / "run-bad"
    bad.mkdir(parents=True)
    (bad / "result.json").write_text("{oops", encoding="utf-8")
    _paused_run_dir(tmp_path, "run-good", [_overdue_spec("run-good")])

    stats = scheduler.tick(time.time())

    assert stats["dropped"] == 1 and stats["resumed"] == 1
    for t in scheduler._spawned:
        t.join(timeout=10)
    assert manager.resumed == ["run-good"]


def test_start_idempotent_and_interval_reread(tmp_path):
    """start() 幂等(同一线程,daemon);interval 每拍现读 manager.schedule_section(改动即生效)。"""
    scheduler, _, manager = _scheduler(tmp_path)
    scheduler.start()
    first = scheduler._thread
    scheduler.start()
    assert scheduler._thread is first, "幂等:重复 start 不起新线程"
    assert first is not None and first.daemon and first.is_alive()
    assert scheduler.interval() == 9.0
    manager.interval_value = 0.5
    assert scheduler.interval() == 0.5, "间隔每拍现读(config 改动即生效)"
    manager.interval_value = "坏值"
    assert scheduler.interval() == 5.0, "配置读坏回落构造值,不杀节拍"


# ---------------------------------------------------------------------------
# 双火锚点(内核集成):settle-in-file 后 resume,rearm 不补火——恰好一次
# ---------------------------------------------------------------------------


class _PauseProbe:
    """post:llm.response 上一次 ``ctl.pause`` 的探针(照 tests/kernel/test_pause_resume.py 先例)。"""

    name: ClassVar[str] = "pause-probe"
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False
    subscriptions: ClassVar[list] = ["post:llm.response"]

    def __init__(self) -> None:
        self.fired = 0

    async def on_signal(self, sig: Signal, ctl) -> Allow:
        if self.fired == 0:
            self.fired += 1
            await ctl.pause(sig.run_id, "人工暂停排查")
        return Allow()


def _kernel(brain=fib_brain, sidecars=()):
    config = RunConfig(
        model="mock/fib",
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
        compression="off",
    )
    return assemble(config, brain, FIB_SKILLS_YAML, sidecars=sidecars)


def _paused_fib3_ckpt(tmp_path: Path) -> tuple[str, Path]:
    """fib(3) 首次 LLM 调用后挂起并落 checkpoint(断电现场模板);返回 (run_id, ckpt 路径)。"""
    kernel = _kernel(sidecars=(_PauseProbe(),))
    with pytest.raises(RunPaused, match="^人工暂停排查$"):
        asyncio.run(kernel.run("demo.fib", {"n": 3}))
    run_id = next(iter(kernel._runs.values())).run_id
    ckpt = tmp_path / "ckpt.json"
    kernel.checkpoint(run_id, str(ckpt))
    return run_id, ckpt


def _plant_overdue_timer(ckpt: Path, run_id: str) -> str:
    """把过期 one-shot 规格植入根帧 working(模拟暂停期间到点);返回根帧 id。"""
    doc = json.loads(ckpt.read_text(encoding="utf-8"))
    root = next(f for f in doc["frames"] if f["parent_id"] is None)
    root["context"]["working"]["_timers"] = [_overdue_spec(run_id, frame_id=root["frame_id"])]
    ckpt.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return root["frame_id"]


def test_settle_then_kernel_resume_fires_exactly_once(tmp_path):
    """**双火锚点**:调度器 settle-in-file 后的 checkpoint 被内核 resume——
    rearm 见到已结算规格(done)不补火、不重生任务;过期的一次由 settle 追加进
    根帧的文本送达(恰好一条 [timer 到点] 消息),恢复后的 LLM 上下文可见。"""
    run_id, ckpt = _paused_fib3_ckpt(tmp_path)
    root_id = _plant_overdue_timer(ckpt, run_id)

    texts = settle_run_checkpoint(ckpt, time.time())
    assert len(texts) == 1 and "planted-1shot" in texts[0]

    async def _slow_fib(req):
        await asyncio.sleep(0.01)  # 给(本不该存在的)重武装任务运行窗口
        return fib_brain(req)

    kernel2 = _kernel(brain=_slow_fib)
    fake_sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        fake_sleeps.append(delay)

    kernel2.tools._timers._sleep = _fake_sleep
    result = asyncio.run(kernel2.resume(str(ckpt)))

    assert result == {"seq": [0, 1, 1]}, "恢复不是重跑"
    root = kernel2.stack.get(root_id)
    timer_msgs = [m for m in root.context.messages if m.content.startswith("[timer 到点]")]
    assert len(timer_msgs) == 1, (
        f"settle-in-file 先行:恰好一次(settle 追加的那条;rearm 不得再补火),得到 {len(timer_msgs)}"
    )
    assert timer_msgs[0].content == texts[0]
    assert fake_sleeps == [], "已结算规格不得再武装出后台任务"
    assert not kernel2.tools._timers.tasks, "无任务残留"
    (spec,) = root.context.working["_timers"]
    assert spec["done"] is True and spec["fired"] == 1
