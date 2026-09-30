"""E5 锚点测试:持久调度 web 面(§17-3/§8.3/D3;host/shared/scheduler.py + RunManager 接线)。

固定约定:

- ``POST /api/events`` 带 ``delay_seconds``/``at``:互斥、须为正、坏类型全归 400
  (与 EventBody 的 400-over-422 语义一致,字段 deliberately Any);合法 →
  ``{"ok", "action": "scheduled", schedule_id, fire_at}``,条目落
  ``<artifacts_root>/schedule.json``(source="api",crash-safe);
- 调度器未启用(``[schedule]`` 段缺席)→ 停车 409,``GET /api/schedule`` 归空表;
- 启用时 ``create_app`` 起 ``Scheduler`` 守护(token refresher 先例);本文件用
  ``interval_seconds = 3600`` 钉住自动拍,手工 ``tick(未来 now)`` 驱动确定性;
- 到点经 ``dispatch_event`` 同路由派发(无 target → start_run);重启恢复:
  新 app 重载同一 schedule.json,过期条目恰好派发一次;
- **双火锚点(web 面)**:paused run 的 checkpoint 里过期计时器,调度器
  settle-in-file 后 resume——内核 rearm 不补火,恰好一条 [timer 到点] 消息。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_os.host.web.app import create_app
from tests.helpers.config import write_config
from tests.helpers.web import wait_status

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

#: [schedule] 段在场(启动调度器)但节拍钉死在 1 小时——自动拍在测试内永不触发,
#: 一切派发由手工 tick 驱动(确定性)
SCHEDULE_SECTION = "[schedule]\ninterval_seconds = 3600\n"


def _client(tmp_path: Path, **kw) -> TestClient:
    """本文件的 run 需要内置工具与宽松预算(照 test_events_api 先例)。"""
    cfg = write_config(tmp_path, builtins=True, max_cost=100.0, **kw)
    return TestClient(create_app(cfg, artifacts_root=tmp_path / "runs"))


def _manager(client: TestClient):
    """app.state.manager(create_app 装配时钉的 programmatic 入口)。"""
    return client.app.state.manager


def _tick(client: TestClient, now: float | None = None) -> dict:
    """手工驱动一拍并 join 全部 resume 线程(测试确定性面)。"""
    scheduler = _manager(client)._scheduler
    stats = scheduler.tick(now if now is not None else time.time())
    for t in scheduler._spawned:
        t.join(timeout=30)
    return stats


# ---------------------------------------------------------------------------
# 定时停车:POST /api/events + delay_seconds/at
# ---------------------------------------------------------------------------


def test_park_event_with_delay_seconds_persists(tmp_path):
    """带 delay_seconds → scheduled 响应;条目落 schedule.json;GET /api/schedule 可见。"""
    client = _client(tmp_path, extra=SCHEDULE_SECTION)
    before = time.time()
    r = client.post(
        "/api/events",
        json={
            "type": "cron.tick",
            "payload": {"at": "09:00"},
            "skill": "demo.fib",
            "input": {"n": 2},
            "delay_seconds": 3600,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["action"] == "scheduled"
    assert body["schedule_id"]
    assert before + 3600 <= body["fire_at"] <= time.time() + 3600

    # 持久化:文件在,条目形状全(source=api;principal 缺省 None;target None)
    doc = json.loads((tmp_path / "runs" / "schedule.json").read_text(encoding="utf-8"))
    assert doc["v"] == 1
    (entry,) = doc["pending"]
    assert entry["id"] == body["schedule_id"] and entry["fire_at"] == body["fire_at"]
    assert entry["type"] == "cron.tick" and entry["skill"] == "demo.fib"
    assert entry["input"] == {"n": 2} and entry["source"] == "api"
    assert entry["target"] is None and entry["principal"] is None

    r = client.get("/api/schedule")
    assert r.status_code == 200
    (row,) = r.json()["pending"]
    assert row == {
        "id": entry["id"],
        "type": "cron.tick",
        "fire_at": entry["fire_at"],
        "target": None,
        "skill": "demo.fib",
        "source": "api",
    }


def test_park_event_with_at_epoch(tmp_path):
    """at 绝对时刻:fire_at 原样取 at。"""
    client = _client(tmp_path, extra=SCHEDULE_SECTION)
    at = time.time() + 7200
    r = client.post(
        "/api/events",
        json={"type": "cron.tick", "skill": "demo.fib", "input": {"n": 1}, "at": at},
    )
    assert r.status_code == 200 and r.json()["fire_at"] == at


def test_park_validation_400_matrix(tmp_path):
    """互斥/非正/坏类型/缺 type 全归 400(不走 pydantic 422);合法形态不受影响。"""
    client = _client(tmp_path, extra=SCHEDULE_SECTION)
    base = {"type": "cron.tick", "skill": "demo.fib", "input": {"n": 1}}
    future = time.time() + 60
    bad_bodies = [
        {**base, "delay_seconds": 60, "at": future},  # 互斥
        {**base, "delay_seconds": 0},  # 非正
        {**base, "delay_seconds": -1},
        {**base, "delay_seconds": "soon"},  # 坏类型(400 而非 422)
        {**base, "at": 0},
        {**base, "at": -1},
        {**base, "at": "明天"},
        {**base, "delay_seconds": True},  # bool 不算数字
        {"skill": "demo.fib", "delay_seconds": 60},  # 缺 type(先撞 type 校验)
    ]
    for body in bad_bodies:
        r = client.post("/api/events", json=body)
        assert r.status_code == 400, (body, r.status_code, r.text)
    assert client.get("/api/schedule").json()["pending"] == [], "坏请求不得留下条目"


def test_park_requires_scheduler_enabled_409(tmp_path):
    """[schedule] 段缺席:停车 409(fail-closed 不静默收下);GET /api/schedule 归空表。"""
    client = _client(tmp_path)
    r = client.post(
        "/api/events",
        json={"type": "cron.tick", "skill": "demo.fib", "input": {"n": 1}, "delay_seconds": 60},
    )
    assert r.status_code == 409
    assert client.get("/api/schedule").json() == {"pending": []}
    assert not (tmp_path / "runs" / "schedule.json").exists(), "未启用不得建存储文件"


def test_immediate_event_path_unchanged_without_delay(tmp_path):
    """回归:不带 delay/at 的事件走原即时通道([schedule] 在场也不变)。"""
    client = _client(tmp_path, extra=SCHEDULE_SECTION)
    r = client.post(
        "/api/events",
        json={"type": "cron.tick", "skill": "demo.fib", "input": {"n": 2}, "wait": True},
    )
    assert r.status_code == 200 and r.json()["action"] == "started"
    assert client.get("/api/schedule").json()["pending"] == [], "即时通道不停车"


def test_web_kernel_binds_schedule_service_only_when_enabled(tmp_path):
    """绑定接线:[schedule] 在场 → 装配的 run 内核绑上 store-backed 服务
    (system.schedule.set 可用);缺席 → 不绑(工具报"schedule 服务未装配")。"""
    enabled_dir = tmp_path / "enabled"
    disabled_dir = tmp_path / "disabled"
    enabled_dir.mkdir()
    disabled_dir.mkdir()
    client_on = _client(enabled_dir, extra=SCHEDULE_SECTION)
    client_off = _client(disabled_dir)
    assert getattr(_manager(client_on)._assemble_kernel().tools, "_schedule", None) is not None
    assert getattr(_manager(client_off)._assemble_kernel().tools, "_schedule", None) is None


# ---------------------------------------------------------------------------
# 到点派发(手工 tick)与重启恢复
# ---------------------------------------------------------------------------


def test_due_entry_dispatched_via_start_run_on_tick(tmp_path):
    """停车(1 小时后)→ 手工 tick(未来 now)→ 无 target 通道 start_run 起新 run 跑完;
    条目摘除,再 tick 不重发。"""
    client = _client(tmp_path, extra=SCHEDULE_SECTION)
    r = client.post(
        "/api/events",
        json={"type": "cron.tick", "skill": "demo.fib", "input": {"n": 2}, "delay_seconds": 3600},
    )
    assert r.json()["action"] == "scheduled"

    stats = _tick(client, now=time.time() + 7200)
    assert stats["dispatched"] == 1 and stats["dropped"] == 0

    # 新 run 起来并跑完(fib(2) 终态 done,结果正确)
    runs = client.get("/api/runs").json()
    assert len(runs) == 1
    detail = wait_status(client, runs[0]["run_id"])
    assert detail["status"] == "done" and detail["result"] == {"seq": [0, 1]}

    assert client.get("/api/schedule").json()["pending"] == []
    stats = _tick(client, now=time.time() + 7200)
    assert stats["dispatched"] == 0, "已派发条目不得重发"


def test_restart_recovery_fires_overdue_exactly_once(tmp_path):
    """重启恢复:app1 停车后"进程重启"(同 artifacts_root 起 app2)→ 过期条目
    恰好派发一次(第二拍空);schedule.json 跨"重启"持久。"""
    cfg = write_config(tmp_path, builtins=True, max_cost=100.0, extra=SCHEDULE_SECTION)
    artifacts = tmp_path / "runs"
    client1 = TestClient(create_app(cfg, artifacts_root=artifacts))
    r = client1.post(
        "/api/events",
        json={"type": "cron.tick", "skill": "demo.fib", "input": {"n": 3}, "delay_seconds": 60},
    )
    assert r.json()["action"] == "scheduled"

    # "重启":全新 app/manager/调度器,同一 artifacts_root(schedule.json 重载)
    client2 = TestClient(create_app(cfg, artifacts_root=artifacts))
    assert len(client2.get("/api/schedule").json()["pending"]) == 1, "重启后挂起条目重载"

    stats = _tick(client2, now=time.time() + 120)
    assert stats["dispatched"] == 1, "过期条目恢复后恰好派发一次"
    runs = client2.get("/api/runs").json()
    assert len(runs) == 1
    detail = wait_status(client2, runs[0]["run_id"])
    assert detail["status"] == "done" and detail["result"] == {"seq": [0, 1, 1]}

    stats = _tick(client2, now=time.time() + 120)
    assert stats["dispatched"] == 0 and len(client2.get("/api/runs").json()) == 1


def test_due_entry_targeting_terminal_run_dropped(tmp_path):
    """定向终态 run 的到点条目:dispatch 冲突 → 丢弃(v1 无死信),tick 不炸。"""
    client = _client(tmp_path, extra=SCHEDULE_SECTION)
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 2}, "wait": True})
    run_id = r.json()["run_id"]
    r = client.post(
        "/api/events",
        json={"type": "user.ping", "target": {"run_id": run_id}, "delay_seconds": 30},
    )
    assert r.json()["action"] == "scheduled"

    stats = _tick(client, now=time.time() + 60)
    assert stats["dropped"] == 1 and stats["dispatched"] == 0
    assert client.get("/api/schedule").json()["pending"] == []


# ---------------------------------------------------------------------------
# 双火锚点(web 面):paused run 的过期计时器由调度器 settle + resume,恰好一次
# ---------------------------------------------------------------------------


def test_scheduler_wakes_paused_run_overdue_timer_exactly_once(tmp_path):
    """paused run(slow_fib,暂停期间计时器到点)→ 调度器 tick:settle-in-file(规格
    done + 文本追加根帧)→ 独立线程 resume 跑完;终态 checkpoint 恰好一条
    [timer 到点](内核 rearm 见已结算规格不补火)。"""
    client = _client(tmp_path, brain="tests.helpers.brains:slow_fib_brain", extra=SCHEDULE_SECTION)
    r = client.post("/api/runs", json={"skill": "demo.fib", "input": {"n": 3}})
    run_id = r.json()["run_id"]
    assert client.post(f"/api/runs/{run_id}/pause").status_code == 200
    detail = wait_status(client, run_id, statuses=("paused",))
    assert detail["status"] == "paused"

    # 植入过期 one-shot 规格(模拟暂停期间到点;形状同 tools/timer.py 落档)
    ckpt_path = tmp_path / "runs" / "runs" / run_id / "checkpoint.json"
    doc = json.loads(ckpt_path.read_text(encoding="utf-8"))
    root = min(doc["frames"], key=lambda f: f.get("depth", 0))
    now = time.time()
    root["context"]["working"]["_timers"] = [
        {
            "timer_id": "planted-1shot",
            "run_id": run_id,
            "frame_id": root["frame_id"],
            "delay_seconds": 3600.0,
            "interval_seconds": None,
            "count": None,
            "fired": 0,
            "note": "断电前安排的复查",
            "created_at": now - 3610,
            "next_fire_at": now - 5,
        }
    ]
    ckpt_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    stats = _tick(client)
    assert stats["resumed"] == 1 and stats["timer_fires"] == 1

    detail = wait_status(client, run_id)
    assert detail["status"] == "done" and detail["result"] == {"seq": [0, 1, 1]}

    # 恰好一次:终态 checkpoint 根帧只一条 [timer 到点](settle 追加的那条)
    doc = json.loads(ckpt_path.read_text(encoding="utf-8"))
    root = min(doc["frames"], key=lambda f: f.get("depth", 0))
    timer_msgs = [
        m for m in root["context"]["messages"] if m["content"].startswith("[timer 到点]")
    ]
    assert len(timer_msgs) == 1, f"双火锚点:恰好一次,得到 {len(timer_msgs)}"
    assert "planted-1shot" in timer_msgs[0]["content"] and "第 1 次" in timer_msgs[0]["content"]
    assert timer_msgs[0]["source"] == "injected" and timer_msgs[0]["role"] == "user"
    (spec,) = root["context"]["working"]["_timers"]
    assert spec["done"] is True and spec["fired"] == 1

    # 再一拍:规格已 done、run 已 done → 不再结算/唤醒
    stats = _tick(client)
    assert stats["resumed"] == 0 and stats["timer_fires"] == 0
