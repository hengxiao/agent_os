"""system.schedule.set 锚点测试(D3 跨 run 自动派生;§17-3 宿主调度器的工具喂入源)。

固定约定:

- 工具面常驻(注册点在构造器,同 timer 先例;新工具无旧名,不设别名),
  WRITE 档(登记持久条目是对 run 外的副作用)、``confirm=True``(派生新 run
  是开新工作单元,人工确认是增殖刹车,§6.2 默认不信任);
- 参数:``skill`` 必填非空;``delay_seconds``/``at`` 二选一且 > 0(缺/并给/
  非正 → INVALID_ARGS);``input`` 缺省 {}、``note`` 缺省 "";
- 服务未装配(未 ``bind_schedule``)→ NOT_FOUND 结构化错误(同 timer/
  skill_register 先例);CLI 宿主不绑 → 行为与引入前一致;
- 装配 store-backed 服务(``StoreScheduleService``):条目落 ScheduleStore
  (``source="tool"``、``via_run_id`` = 调用 run、``principal`` 随帧身份落档),
  返回 ``{schedule_id, fire_at}``,登记即持久(crash-safe)。
"""

from __future__ import annotations

import asyncio
import json
import time

from agent_os.api.v1 import (
    Permission,
    Principal,
    SkillFrame,
    ToolCall,
    ToolDispatchContext,
    ToolErrorKind,
    ToolPolicy,
)
from agent_os.host.shared.scheduler import ScheduleStore, StoreScheduleService
from agent_os.tools.local_registry import LocalPythonToolRegistry


def _ctx(allowed=("system.schedule.set",), principal=None):
    frame = SkillFrame(frame_id="f1", run_id="r1")
    frame.principal = principal
    return ToolDispatchContext(
        frame=frame,
        allowed_tools=list(allowed),
        tool_policy=ToolPolicy(max_permission=Permission.EXEC),
    )


def _dispatch(reg, args, ctx=None):
    async def main():
        return await reg.dispatch(
            ToolCall(id="s1", name="system.schedule.set", args=args), ctx or _ctx()
        )

    return asyncio.run(main())


def test_schedule_set_registered_in_builtins():
    """system.schedule.set 在默认工具面里(常驻),无旧名别名;WRITE·confirm=True。"""
    reg = LocalPythonToolRegistry.with_builtins()
    assert reg.has("system.schedule.set"), "system.schedule.set 必须在默认工具面里"
    assert not reg.has("schedule_set"), "新工具不设旧名别名"
    spec = reg.get("system.schedule.set").spec
    assert spec.permission is Permission.WRITE, "登记持久调度条目是副作用,应按 WRITE 档"
    assert spec.confirm is True, "派生新 run 是开新工作单元,必须过 confirm 闸门(§6.2)"
    assert not spec.cacheable and not spec.concurrent_safe
    props = set(spec.parameters["properties"])
    assert props == {"skill", "input", "delay_seconds", "at", "note"}
    assert spec.parameters["required"] == ["skill"], "仅 skill 必填;delay/at 二选一在工具内校验"
    assert "Use when" in spec.description and "Do not use when" in spec.description


def test_unbound_schedule_service_reports_not_found():
    """未 bind 调度服务(CLI 等宿主形态):按"schedule 服务未装配"报 NOT_FOUND。"""
    reg = LocalPythonToolRegistry.with_builtins()
    res = _dispatch(reg, {"skill": "demo.fib", "delay_seconds": 60})
    assert not res.ok and res.error.kind is ToolErrorKind.NOT_FOUND
    assert "未装配" in res.error.message
    assert res.error.hint, "错误须带可执行的下一步建议(§W0-3)"


def test_args_validation():
    """skill 空 / delay·at 缺或并给 / 非正 → INVALID_ARGS(带 hint)。"""
    reg = LocalPythonToolRegistry.with_builtins()
    reg.bind_schedule(StoreScheduleService(ScheduleStore("/nonexistent/schedule.json")))
    cases = [
        {"skill": "", "delay_seconds": 60},
        {"skill": "demo.fib"},  # 两者都缺
        {"skill": "demo.fib", "delay_seconds": 60, "at": time.time() + 60},  # 并给
        {"skill": "demo.fib", "delay_seconds": 0},
        {"skill": "demo.fib", "delay_seconds": -5},
        {"skill": "demo.fib", "at": 0},
        {"skill": "demo.fib", "at": -1},
    ]
    for args in cases:
        res = _dispatch(reg, args)
        assert not res.ok and res.error.kind is ToolErrorKind.INVALID_ARGS, (args, res.error)
        assert res.error.hint, "INVALID_ARGS 须带下一步建议(§W0-3)"


def test_bound_service_lands_entry_in_store(tmp_path):
    """装配 store-backed 服务:条目落 ScheduleStore(source=tool、via_run_id、principal
    随帧身份),返回 {schedule_id, fire_at};文件持久化可重载(crash-safe)。"""
    store = ScheduleStore(tmp_path / "schedule.json")
    reg = LocalPythonToolRegistry.with_builtins()
    reg.bind_schedule(StoreScheduleService(store))
    principal = Principal(subject="user:alice", issuer="api-token", attrs={"clearance": "confidential"})
    before = time.time()

    res = _dispatch(
        reg,
        {"skill": "demo.fib", "input": {"n": 4}, "delay_seconds": 600, "note": "稍后复查"},
        _ctx(principal=principal),
    )

    assert res.ok, res.error
    assert res.value["schedule_id"], "须返回 schedule_id"
    fire_at = res.value["fire_at"]
    assert before + 600 <= fire_at <= time.time() + 600, "fire_at = now + delay_seconds"

    # 落档形状(持久化契约):source=tool / via_run_id=调用 run / principal 纯 dict / input 原样
    reloaded = ScheduleStore(tmp_path / "schedule.json")
    (entry,) = reloaded.pending()
    assert entry["id"] == res.value["schedule_id"]
    assert entry["source"] == "tool" and entry["via_run_id"] == "r1"
    assert entry["skill"] == "demo.fib" and entry["input"] == {"n": 4}
    assert entry["payload"] == {"note": "稍后复查"}
    assert entry["principal"] == {
        "subject": "user:alice",
        "issuer": "api-token",
        "attrs": {"clearance": "confidential"},
    }
    assert entry["target"] is None and entry["wait"] is False
    assert entry["fire_at"] == fire_at and entry["created_at"] >= before


def test_at_absolute_moment(tmp_path):
    """at 绝对时刻:fire_at 原样取 at(不加 now)。"""
    store = ScheduleStore(tmp_path / "schedule.json")
    reg = LocalPythonToolRegistry.with_builtins()
    reg.bind_schedule(StoreScheduleService(store))
    at = time.time() + 3600
    res = _dispatch(reg, {"skill": "demo.fib", "at": at})
    assert res.ok and res.value["fire_at"] == at
    (entry,) = store.pending()
    assert entry["input"] == {}, "input 缺省 {}"
    assert entry["payload"] == {}, "note 缺省 → payload 空"


def test_entry_dispatches_via_run_manager_shape(tmp_path):
    """工具落档的条目经 entry_dispatch 还原为 dispatch_event body(无 target → skill 通道)。"""
    from agent_os.host.shared.scheduler import entry_dispatch

    store = ScheduleStore(tmp_path / "schedule.json")
    reg = LocalPythonToolRegistry.with_builtins()
    reg.bind_schedule(StoreScheduleService(store))
    principal = Principal(subject="user:bob", issuer="web-session")
    _dispatch(reg, {"skill": "demo.fib", "input": {"n": 2}, "delay_seconds": 60}, _ctx(principal=principal))
    (entry,) = store.pending()

    body, rebuilt = entry_dispatch(json.loads(json.dumps(entry)))
    assert body == {
        "type": "schedule.set",
        "payload": {},
        "skill": "demo.fib",
        "input": {"n": 2},
        "wait": False,
    }
    assert isinstance(rebuilt, Principal) and rebuilt.subject == "user:bob"
    assert rebuilt.issuer == "web-session"
