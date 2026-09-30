"""宿主调度器(§17-3 持久事件队列与调度 / §8.3 跨 run 计时器 / D3 跨 run 自动派生)。

三处开口共用同一机制——``Scheduler`` 守护线程按 ``[schedule].interval_seconds``
节拍结算两个喂入源:

1. **持久调度表**(``ScheduleStore``,``<artifacts_root>/schedule.json``):
   ``POST /api/events`` 带 ``delay_seconds``/``at`` 的停车条目(source="api")与
   ``system.schedule.set`` 的派生条目(source="tool",``StoreScheduleService``)
   到点后经 ``RunManager.dispatch_event`` 派发——与 ``POST /api/events`` 即时
   通道**同一份三通道路由**(running 注入 / paused resume / 无 target 起新 run);
2. **跨 run 计时器**:``scan_due_timers`` 扫描产物目录里 paused run 的
   checkpoint,过期规格先 **settle-in-file**(:func:`settle_run_checkpoint`
   纯结算 + fire 文本追加根帧消息 + 原子写回)再 resume——内核 resume 的
   ``rearm_from_working`` 见到已结算规格不再补火(双火协调 = settle 先行,
   折算语义与进程内 rearm 共用 tools/timer.py 的 :func:`settle_overdue`
   单一实现)。

边界(v1):派发失败(目标终态/消失/校验拒)的条目丢弃 + log,**无死信队列**;
``dispatch_event`` 的 paused 通道是阻塞语义(resume 到底),持久事件的定向
resume 会占住本拍(少数派路径;timer 补投通道已隔离到独立 daemon 线程)。
同 token refresher 先例:无 shutdown 钩子,daemon 线程随进程退场。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from agent_os.api.v1 import FrameStatus, Principal, Role, Source
from agent_os.host.shared.artifacts import _write_json
from agent_os.tools.timer import settle_overdue

_log = logging.getLogger("agent_os.host.scheduler")

#: ``system.schedule.set`` 派生条目的事件类型(注入文本 ``[event:<type>]`` 前缀与
#: ``event.received`` 观测面用;无 target 通道只进 event_doc,不进 run input)
_TOOL_EVENT_TYPE = "schedule.set"


def new_entry(
    *,
    event_type: str,
    payload: Any = None,
    target: dict[str, Any] | None = None,
    skill: str | None = None,
    input: dict[str, Any] | None = None,
    wait: bool = False,
    principal: Any = None,
    via_run_id: str | None = None,
    fire_at: float,
    source: str,
) -> dict[str, Any]:
    """调度条目构造(API 停车与 ``system.schedule.set`` 共用同一形状,防两份字面量漂移)。

    ``principal`` 落档为 ``dataclasses.asdict`` 的纯 dict(同 checkpoint 帧身份
    落盘先例),到点派发时经 :func:`entry_dispatch` 重建;非 dataclass → None
    (派发回落宿主单用户语义)。
    """
    return {
        "id": uuid.uuid4().hex[:12],
        "type": event_type,
        "payload": payload if payload is not None else {},
        "target": target,
        "skill": skill,
        "input": input,
        "wait": bool(wait),
        "principal": dataclasses.asdict(principal)
        if principal is not None and dataclasses.is_dataclass(principal)
        else None,
        "via_run_id": via_run_id,
        "fire_at": float(fire_at),
        "created_at": time.time(),
        "source": source,
    }


def entry_dispatch(entry: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """持久条目 → ``(dispatch_event body, principal)``。

    body 形状与 ``POST /api/events`` 请求体一致(type/payload/target?/skill?/
    input?/wait?);principal dict 重建 :class:`Principal`(畸形 → 构造异常上抛,
    调用方按条目隔离丢弃,fail-closed 不放大身份)。
    """
    body: dict[str, Any] = {
        "type": entry.get("type") or "",
        "payload": entry.get("payload", {}),
        "skill": entry.get("skill"),
        "input": entry.get("input"),
        "wait": bool(entry.get("wait", False)),
    }
    if entry.get("target"):
        body["target"] = entry["target"]
    raw = entry.get("principal")
    principal = Principal(**raw) if isinstance(raw, dict) else None
    return body, principal


class ScheduleStore:
    """持久调度表(``{"v": 1, "pending": [...]}``;写一律原子,``_write_json`` tmp+os.replace)。

    读容错:文件缺席 = 空表;畸形(不可解析/版本或形状非法)→ 记 log 按空表
    起步——不杀宿主启动,原文件不主动重写(留存人工核查,下一次 add/pop 才
    覆盖)。全方法持同一把锁:add/pop 随改随存,due/pending 返回快照。
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._pending: list[dict[str, Any]] = self._load()

    @property
    def path(self) -> Path:
        """存储文件路径(测试观测点)。"""
        return self._path

    def _load(self) -> list[dict[str, Any]]:
        try:
            doc = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("schedule 存储 %s 不可读(%s),按空表起步", self._path, e)
            return []
        pending = doc.get("pending") if isinstance(doc, dict) else None
        if not isinstance(doc, dict) or doc.get("v") != 1 or not isinstance(pending, list):
            _log.warning("schedule 存储 %s 版本/形状非法,按空表起步", self._path)
            return []
        return [e for e in pending if isinstance(e, dict) and e.get("id")]

    def _save_locked(self) -> None:
        _write_json(self._path, {"v": 1, "pending": self._pending})

    def add(self, entry: dict[str, Any]) -> dict[str, Any]:
        """登记条目并原子落盘(返回原条目)。"""
        with self._lock:
            self._pending.append(entry)
            self._save_locked()
        return entry

    def pop(self, entry_id: str) -> dict[str, Any] | None:
        """按 id 摘除并原子落盘;不在表 → ``None``(调用方按已派发/已丢弃处理)。"""
        with self._lock:
            for index, entry in enumerate(self._pending):
                if entry.get("id") == entry_id:
                    self._pending.pop(index)
                    self._save_locked()
                    return entry
        return None

    def due(self, now: float) -> list[dict[str, Any]]:
        """到点条目快照(``fire_at <= now``;**不摘除**——摘除与派发由调用方成对负责)。

        ``fire_at`` 畸形的条目跳过 + 记 log(留在表内不静默丢;下一拍再告警)。
        """
        with self._lock:
            out: list[dict[str, Any]] = []
            for entry in self._pending:
                try:
                    if float(entry.get("fire_at") or 0) <= now:
                        out.append(entry)
                except (TypeError, ValueError):
                    _log.warning(
                        "schedule 条目 %s 的 fire_at 畸形(%r),跳过",
                        entry.get("id"),
                        entry.get("fire_at"),
                    )
            return out

    def pending(self) -> list[dict[str, Any]]:
        """全量挂起条目快照(``GET /api/schedule`` 数据源)。"""
        with self._lock:
            return [dict(e) for e in self._pending]


class StoreScheduleService:
    """``system.schedule.set`` 的 store-backed 宿主实现(web 宿主经 ``bind_schedule`` 注入)。

    条目 ``source="tool"``、``via_run_id`` = 派生发起 run、``principal`` 随调用帧
    身份落档(D3 派生:派生 run 以发起方身份起跑);登记即持久(crash-safe),
    到点由调度器经 ``dispatch_event`` 无 target 通道起新 run(``skill`` + ``input``)。
    """

    def __init__(self, store: ScheduleStore) -> None:
        self._store = store

    def set(
        self,
        *,
        skill: str,
        input: dict[str, Any],
        fire_at: float,
        note: str = "",
        via_run_id: str | None = None,
        principal: Any = None,
    ) -> dict[str, Any]:
        """登记派生条目,返回 ``{schedule_id, fire_at}``(工具返回值)。"""
        entry = new_entry(
            event_type=_TOOL_EVENT_TYPE,
            payload={"note": note} if note else {},
            skill=skill,
            input=dict(input),
            principal=principal,
            via_run_id=via_run_id,
            fire_at=fire_at,
            source="tool",
        )
        self._store.add(entry)
        return {"schedule_id": entry["id"], "fire_at": entry["fire_at"]}


def _event_type_of(text: str) -> str:
    """从 ``[event:<type>]`` 前缀解析事件类型;无前缀归 ``"unknown"``。

    与 ``host/web/run_manager._event_type_of`` 同逻辑——shared 层不反向 import
    web 层,保留一份(4 行,漂移风险自负:两侧都以注入文本前缀为准)。
    """
    if text.startswith("[event:") and "]" in text:
        return text[len("[event:"):text.index("]")]
    return "unknown"


def _injected_message(text: str) -> dict[str, Any]:
    """注入消息 dict:逐字对齐内核 ``_message_to_dict`` 落盘形状(同
    ``RunManager._inject_into_checkpoint``——8 键齐全,空 parts 不落键)。"""
    return {
        "role": Role.USER.value,
        "content": text,
        "tool_calls": [],
        "tool_call_id": None,
        "name": None,
        "reasoning": None,
        "source": Source.INJECTED.value,
        "meta": {"event": {"type": _event_type_of(text)}},
    }


def scan_due_timers(artifacts_root: str | Path, now: float) -> list[dict[str, Any]]:
    """扫描 ``<artifacts_root>/runs/*``:paused run 的 checkpoint 里过期计时器规格。

    ``result.json`` ``status=="paused"`` → 解析 ``checkpoint.json`` → 逐帧(跳过
    DONE,同内核 resume 结算口径)对 ``context.working._timers`` 每条规格过
    :func:`settle_overdue`。纯**发现**通道:settle 只作用在本次解析出的内存副本
    (持久化由 :func:`settle_run_checkpoint` 重读重写完成——同一 ``now`` 下两次
    结算结果确定一致)。畸形 result/checkpoint/规格 → 跳过该 run/该条 + 记 log
    (扫描永不杀节拍)。

    返回 ``[{run_id, frame_id, spec_index, fire_text}]``(fire_text 已按
    ``format_fire_text`` 折算,供观测/对账)。
    """
    hits: list[dict[str, Any]] = []
    runs_root = Path(artifacts_root) / "runs"
    try:
        run_dirs = sorted(p for p in runs_root.iterdir() if p.is_dir())
    except OSError:
        return hits
    for run_dir in run_dirs:
        result_path = run_dir / "result.json"
        checkpoint_path = run_dir / "checkpoint.json"
        if not result_path.is_file() or not checkpoint_path.is_file():
            continue
        try:
            status = json.loads(result_path.read_text(encoding="utf-8")).get("status")
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("run %s 的 result.json 不可读,跳过本 run 计时器扫描: %s", run_dir.name, e)
            continue
        if status != "paused":
            continue
        try:
            doc = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            _log.warning("run %s 的 checkpoint.json 不可读,跳过本 run 计时器扫描: %s", run_dir.name, e)
            continue
        for frame in doc.get("frames") or []:
            if frame.get("status") == FrameStatus.DONE.value:
                continue
            specs = ((frame.get("context") or {}).get("working") or {}).get("_timers")
            if not isinstance(specs, list):
                continue
            for index, spec in enumerate(specs):
                if not isinstance(spec, dict):
                    continue
                try:
                    text = settle_overdue(spec, now)
                except (TypeError, ValueError) as e:
                    _log.warning(
                        "run %s 帧 %s 第 %d 条计时器规格畸形(%s),跳过该条",
                        run_dir.name,
                        frame.get("frame_id"),
                        index,
                        e,
                    )
                    continue
                if text is not None:
                    hits.append(
                        {
                            "run_id": run_dir.name,
                            "frame_id": frame.get("frame_id"),
                            "spec_index": index,
                            "fire_text": text,
                        }
                    )
    return hits


def settle_run_checkpoint(checkpoint_path: str | Path, now: float) -> list[str]:
    """settle-in-file:把 checkpoint 里过期计时器规格就地结算并原子写回(双火协调本体)。

    逐帧(跳过 DONE)过 :func:`settle_overdue`;有到点的把 fire 文本追加进**根帧**
    (depth 最小)``context.messages``(:func:`_injected_message` 形状),整档经
    ``_write_json`` 原子写回。返回本次结算出的 fire 文本列表;无到点 → ``[]``
    (不写文件)。

    调用时序约束(恰好一次的根据):本函数必须先于 resume 完成——resume 的内核
    ``_settle_pending_timers`` → ``rearm_from_working`` 见到已结算规格(one-shot
    已 done / recurring 的 next_fire_at 已推进)不再补火,过期欠的一次由追加进
    根帧的文本随恢复送达。根帧消息列缺失(无法追加)→ 不结算不写回,记 log 归
    ``[]``(fail-closed:宁可本拍作废下拍重试,不消费了 fire 却无处送达);
    写回失败同样作废本拍(不启动 resume,防"文件未结算却被唤醒"的双火窗口)。
    """
    path = Path(checkpoint_path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _log.warning("checkpoint %s 不可读,跳过本拍结算: %s", path, e)
        return []
    frames = doc.get("frames") or []
    root = min(frames, key=lambda f: f.get("depth", 0), default=None)
    messages = ((root or {}).get("context") or {}).get("messages")
    if not isinstance(messages, list):
        _log.warning("checkpoint %s 无根帧消息列,跳过本拍结算(fire 无处送达)", path)
        return []
    texts: list[str] = []
    for frame in frames:
        if frame.get("status") == FrameStatus.DONE.value:
            continue
        specs = ((frame.get("context") or {}).get("working") or {}).get("_timers")
        if not isinstance(specs, list):
            continue
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            try:
                text = settle_overdue(spec, now)
            except (TypeError, ValueError) as e:
                _log.warning(
                    "checkpoint %s 帧 %s 的计时器规格畸形(%s),跳过该条",
                    path,
                    frame.get("frame_id"),
                    e,
                )
                continue
            if text is None:
                continue
            texts.append(text)
            messages.append(_injected_message(text))
    if not texts:
        return []
    try:
        _write_json(path, doc)
    except OSError as e:
        _log.warning("checkpoint %s 结算写回失败(%s),本拍作废(不启动 resume 防双火)", path, e)
        return []
    return texts


class Scheduler:
    """宿主调度守护:持久事件表 + 跨 run 计时器补投的统一节拍器。

    ``tick(now)`` 是同步、可直接测试的结算本体(守护线程只是它的驱动):

    1. ``store.due(now)`` 逐条 pop → ``asyncio.run(manager.dispatch_event(...))``
       (与 ``POST /api/events`` 同路由);目标终态/消失/校验拒 → 丢弃 + log
       (v1 无死信);paused 定向阻塞本拍(见模块 docstring 边界);
    2. :func:`scan_due_timers` 发现的 run 逐个 :func:`settle_run_checkpoint`
       settle-in-file 后,**各自一个 daemon 线程**跑
       ``asyncio.run(manager.resume_run(run_id))``(resume 阻塞语义隔离,
       不拖住节拍);manager 内存态显示 running(resume 在途)的 run 本拍跳过;
    3. 逐条目/逐 run 异常隔离:单点失败只记 log,不杀本拍。

    ``start()`` 幂等启动 daemon 驱动:``threading.Event.wait(interval)`` 循环,
    间隔每拍现读 ``manager.schedule_section()``(config 改动即生效;manager
    无此方法/读取失败 → 构造值)。``_tick_lock`` 互斥守护拍与手工拍(settle-
    in-file 与 store pop 各自原子,但"扫描→结算→resume"序列不并发更稳)。
    """

    def __init__(
        self,
        manager: Any,
        store: ScheduleStore,
        artifacts_root: str | Path,
        *,
        interval_seconds: float = 5.0,
    ) -> None:
        self._manager = manager
        self._store = store
        self._artifacts_root = Path(artifacts_root)
        self._interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._start_lock = threading.Lock()
        self._tick_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        #: 本调度器 spawn 的 resume 线程(测试观测点:join 等 resume 落地)
        self._spawned: list[threading.Thread] = []

    def interval(self) -> float:
        """本拍间隔(秒):manager 暴露 ``schedule_section`` 则现读(每拍生效),否则构造值。"""
        section_fn = getattr(self._manager, "schedule_section", None)
        if callable(section_fn):
            try:
                return float(section_fn().interval_seconds)
            except Exception as e:  # noqa: BLE001 — 配置读坏不杀节拍,回落构造值
                _log.warning("schedule_section 读取失败(%s),本拍用构造间隔", e)
        return self._interval_seconds

    # ------------------------------------------------------------------
    # 结算本体(同步;守护线程与测试共用)
    # ------------------------------------------------------------------

    def tick(self, now: float | None = None) -> dict[str, int]:
        """结算一拍,返回 ``{"dispatched", "dropped", "resumed", "timer_fires"}`` 计数。"""
        now = time.time() if now is None else now
        stats = {"dispatched": 0, "dropped": 0, "resumed": 0, "timer_fires": 0}
        with self._tick_lock:
            self._dispatch_due_entries(now, stats)
            self._settle_due_timers(now, stats)
        return stats

    def _dispatch_due_entries(self, now: float, stats: dict[str, int]) -> None:
        """持久事件表到点条目:pop → dispatch_event;逐条隔离(单条失败不杀节拍)。"""
        for due in self._store.due(now):
            entry = self._store.pop(due.get("id") or "")
            if entry is None:
                continue  # 已被并发摘除(防御;正常路径 tick 互斥不会撞)
            try:
                body, principal = entry_dispatch(entry)
                result = asyncio.run(self._manager.dispatch_event(body, principal=principal))
            except Exception as e:  # noqa: BLE001 — 派发失败(目标终态/消失/校验拒):丢弃 + log
                stats["dropped"] += 1
                _log.warning("定时事件 %s 派发失败,条目丢弃(v1 无死信): %s", entry.get("id"), e)
                continue
            action = (result or {}).get("action")
            if action in ("queued", "injected", "resumed", "started"):
                stats["dispatched"] += 1
            else:
                stats["dropped"] += 1
                _log.warning("定时事件 %s 目标不可达(%s),条目丢弃", entry.get("id"), result)

    def _settle_due_timers(self, now: float, stats: dict[str, int]) -> None:
        """跨 run 计时器:扫描 → settle-in-file → 逐 run 独立 daemon 线程 resume。"""
        run_ids = sorted({hit["run_id"] for hit in scan_due_timers(self._artifacts_root, now)})
        for run_id in run_ids:
            try:
                state = self._manager.state_of(run_id)
            except Exception:  # noqa: BLE001 — 状态查询失败按可结算处理(resume 冲突自会拒)
                state = None
            if state is not None and state.get("status") == "running":
                continue  # resume 在途(内存态 running):本拍跳过,下拍重扫
            try:
                texts = settle_run_checkpoint(
                    self._artifacts_root / "runs" / run_id / "checkpoint.json", now
                )
            except Exception:  # noqa: BLE001 — 逐 run 隔离:单 run 失败不杀节拍
                _log.exception("调度器结算 run %s 的过期计时器失败(已隔离)", run_id)
                continue
            if not texts:
                continue
            stats["timer_fires"] += len(texts)
            thread = threading.Thread(
                target=self._resume_blocking,
                args=(run_id,),
                daemon=True,
                name=f"agent-os-sched-resume-{run_id[:8]}",
            )
            self._spawned.append(thread)
            thread.start()
            stats["resumed"] += 1

    def _resume_blocking(self, run_id: str) -> None:
        """resume 阻塞语义隔离:独立 daemon 线程里 ``asyncio.run`` 直到底(不拖住节拍)。"""
        try:
            asyncio.run(self._manager.resume_run(run_id))
        except Exception:  # noqa: BLE001 — resume 失败(竞态冲突/产物畸形)不杀节拍,下拍重扫
            _log.exception("调度器 resume run %s 失败", run_id)

    # ------------------------------------------------------------------
    # 守护驱动
    # ------------------------------------------------------------------

    def start(self) -> None:
        """幂等启动 daemon 驱动线程(同 token refresher 先例:无 shutdown 钩子,随进程退场)。"""
        with self._start_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._loop, name="agent-os-scheduler", daemon=True
            )
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval()):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 — tick 内已逐项隔离,此处是节拍不死的最后兜底
                _log.exception("调度器 tick 未捕获异常(已兜底,下一拍继续)")
