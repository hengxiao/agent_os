"""RunManager(RUNNERS.md §4.2;R3):Web runner 的进程内 run 管理器。

- 每个 run **独立内核**(``build_kernel(config_path)`` 重新装配),在独立线程中经
  host/shared 的 :func:`execute_run` 推进——execute_run 内部 ``asyncio.run`` 自起
  事件循环,与 FastAPI/uvicorn(及 TestClient 每请求一个 portal)的请求循环彻底
  解耦;run 终态在 worker 线程内就地写入 ``_active``,不依赖请求循环存活;
- 信号扇出(§4.2):装配时向该内核总线订阅 ``"*"``,信号序列化为 JSON 安全 dict
  (与 trace.jsonl 行同形,不可序列化值 ``repr`` 兜底)推入 per-run hub;
- hub = ``collections.deque(maxlen=2000)`` 环形缓冲 + 订阅者 ``asyncio.Queue``
  集合;订阅者事件循环在订阅时捕获,worker 线程经 ``loop.call_soon_threadsafe``
  投递;SSE 客户端先收回放缓冲,后收实时(``subscribe`` 在同一把锁内完成登记与
  快照,回放与实时之间无遗漏、无重复)。

R4 增量(§4.3/§4.4):

- 每个内核装配时附带 :class:`_StopBridge`(no-op ASYNC sidecar),保证
  ``kernel.ctl`` 恒存在 → ``stop_run`` 经 ``ctl.stop`` 在下一个 safe point 中止;
- ``resume_run``:用同一 config 新建内核,经 host/shared 的 ``execute_resume``
  从该 run 的 checkpoint.json 恢复,产物与内存态写回原 run;resume 期间换一只
  新 hub(原 hub 已在首次 run 结束时关闭),SSE 可继续观察;
- ``reload_skills``:共享一份 skills registry(惰性装配,仅供查询/reload;
  每 run 的内核仍各自重建 registry,reload 只影响后续新建的 run,§6.1)。

D3 增量(WEB-UI.md §4.3/§6.2):

- ``start_run(..., overrides=...)``:``{model?, max_cost?, max_steps?}`` 合并进
  本次 run 的 RunConfig——每次重新 ``load_config`` 读文件,改动只落在该 run
  私有的 config dict 副本上,不污染共享配置(后续 run 与 reload 路径不受影响);
- ``skills_manifests`` / ``skill_manifest``:共享 registry 的只读查询
  (``GET /api/skills`` 数据源),与 ``reload_skills`` 共用惰性装配路径。
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from agent_os.api.v1 import RUN_STARTED, Allow, Mode, RunControl, Signal
from agent_os.host.shared.artifacts import execute_resume, execute_run
from agent_os.host.shared.runrecord import STATUS_FAILED
from agent_os.runtime.config import build_kernel, load_config

#: hub 关闭时投递给订阅者的哨兵(§4.2:run 结束后 SSE 发终止事件并关闭)
HUB_CLOSED: Any = object()

#: 环形缓冲容量(§4.2 默认 2000 条)
BUFFER_MAXLEN = 2000

#: ``POST /api/runs`` 的 ``overrides`` 允许覆盖的 RunConfig 字段(WEB-UI.md §4.3 高级区)
OVERRIDE_FIELDS = ("model", "max_cost", "max_steps")


class _StopBridge:
    """内部 no-op ASYNC sidecar(R4 stop 通道的装配占位)。

    M4 起 KernelBuilder **有 sidecar 才**装配 ``kernel.ctl``(RunControlImpl);
    Web 的 stop(``POST /api/runs/{id}/stop`` → ``ctl.stop``)要求每个 run 的内核
    都有 ctl,与配置文件是否声明 sidecar 无关。本 sidecar 不订阅任何信号,
    唯一作用是让 builder 走 sidecar 装配路径。
    """

    name: ClassVar[str] = "_stop_bridge"
    subscriptions: ClassVar[list[str]] = []
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Allow:
        return Allow()


class RunValidationError(RuntimeError):
    """run 未开始即失败的校验类错误(技能不存在/输入不合 schema/装配失败)。

    与 CLI 退出码 2 同类(§3.3);Web 侧由路由层归 ``200 + {"status": "failed"}``。
    """


class ResumeConflictError(RuntimeError):
    """resume 与在途 run 冲突(§4.3:在跑的 run 不能 resume,先 stop);路由层归 409。"""


def _jsonable(value: Any) -> Any:
    """任意值 → JSON 安全结构(不可序列化值 ``repr`` 兜底,与 sink 同策略)。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=repr))


def signal_row(sig: Signal) -> dict[str, Any]:
    """Signal → JSON 安全 dict(与 trace.jsonl 的 signal 行同形,SSE/缓冲/trace 三处一致)。"""
    return {
        "type": "signal",
        "name": sig.name,
        "run_id": sig.run_id,
        "frame_id": sig.frame_id,
        "ts": sig.ts,
        "payload": _jsonable(sig.payload),
    }


class SignalHub:
    """per-run 信号扇出:环形缓冲 + 订阅者队列集合(§4.2)。

    发布方是 run worker 线程(内核事件循环内),订阅方是各 SSE 请求的事件
    循环;两侧经 ``threading.Lock`` 与 ``loop.call_soon_threadsafe`` 交接。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: deque[dict[str, Any]] = deque(maxlen=BUFFER_MAXLEN)
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._closed = False

    def publish(self, row: dict[str, Any]) -> None:
        """缓冲 + 广播;订阅者循环已关闭的静默摘除(订阅者故障不得拖垮 run,§5.3)。"""
        with self._lock:
            self._buffer.append(row)
            subs = list(self._subscribers)
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, row)
            except RuntimeError:
                with self._lock:
                    self._subscribers.discard((loop, queue))

    def subscribe(self) -> tuple[asyncio.Queue, list[dict[str, Any]], bool]:
        """登记订阅者并原子快照缓冲 → ``(queue, replay, closed)``。

        先回放 ``replay`` 再从 ``queue`` 读实时;``closed=True`` 表示 run 已结束,
        回放完即可发终止事件。
        """
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.add((loop, queue))
            replay = list(self._buffer)
            closed = self._closed
        return queue, replay, closed

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = {(loop, q) for loop, q in self._subscribers if q is not queue}

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer)

    def close(self) -> None:
        """run 结束:向全部订阅者投哨兵(之后 publish 仍只进缓冲,容忍迟到信号)。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subs = list(self._subscribers)
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, HUB_CLOSED)
            except RuntimeError:
                pass


class RunManager:
    """单进程、单管路的 run 管理器(§4.2):状态只留内存 + 产物目录。

    ``_active[run_id] = {"task": Thread, "status", "record", "skill", "error",
    "started_at", "kernel"}``;历史 run 重启后由路由层从产物目录重建(冷数据,§4.2)。
    """

    def __init__(self, config_path: str | Path, artifacts_root: Path) -> None:
        self._config_path = config_path
        self._artifacts_root = Path(artifacts_root)
        self._lock = threading.Lock()
        self._active: dict[str, dict[str, Any]] = {}
        self._hubs: dict[str, SignalHub] = {}
        self._skills_registry: Any = None  # 惰性装配的共享 registry(reload/查询用)

    def _assemble_kernel(self, overrides: dict[str, Any] | None = None) -> Any:
        """按 config 装配一个 run 的内核;恒附带 _StopBridge 保证 ctl 存在(stop 通道)。

        ``overrides``(D3,WEB-UI.md §4.3):``{model?, max_cost?, max_steps?}`` 合并进
        本次 run 的 ``[run]`` 配置。配置文件每次重新读取,改动只落在本 run 私有的
        dict 副本上——只影响本次 run,不泄漏到后续 run 或共享 registry(D3 锚点)。
        """
        if not overrides:
            return build_kernel(self._config_path, extra_sidecars=[_StopBridge()])
        cfg = load_config(self._config_path)
        run_section = dict(cfg.get("run") or {})
        for key in OVERRIDE_FIELDS:
            if key in overrides and overrides[key] is not None:
                run_section[key] = overrides[key]
        cfg["run"] = run_section
        return build_kernel(cfg, extra_sidecars=[_StopBridge()])

    async def start_run(
        self,
        skill: str,
        input: dict[str, Any],
        wait: bool = False,
        overrides: dict[str, Any] | None = None,
    ) -> str:
        """启动一个 run 并返回 run_id;``wait=True`` 时阻塞到 run 结束。

        run_id 从 ``run.started`` 信号捕获:无论 wait 与否都先等到 run 真正开始
        (或开始即失败)再返回;run 未开始的校验错抛 :class:`RunValidationError`。
        ``overrides`` 见 :meth:`_assemble_kernel`(D3,只影响本次 run)。
        """
        hub = SignalHub()
        started = threading.Event()
        done = threading.Event()
        holder: dict[str, str] = {}
        state: dict[str, Any] = {
            "task": None,
            "status": "running",
            "record": None,
            "skill": skill,
            "error": None,
            "started_at": datetime.now(UTC).isoformat(),
            "kernel": None,
        }

        def _register(run_id: str) -> None:
            with self._lock:
                self._active[run_id] = state
                self._hubs[run_id] = hub
            holder["run_id"] = run_id
            started.set()

        def _work() -> None:
            try:
                kernel = self._assemble_kernel(overrides)
                state["kernel"] = kernel

                async def _fan(sig: Signal) -> None:
                    hub.publish(signal_row(sig))

                async def _cap(sig: Signal) -> None:
                    _register(sig.run_id)

                kernel.signals.subscribe("*", _fan)
                kernel.signals.subscribe(RUN_STARTED, _cap)
                record = execute_run(
                    kernel, skill, input, artifacts_root=self._artifacts_root, host="web"
                )
                state["status"] = record["status"]
                state["record"] = record
            except Exception as e:  # noqa: BLE001 — 与 execute_run 同旨:校验/装配错归 RunRecord,不炸宿主(§3.3)
                state["status"] = STATUS_FAILED
                state["error"] = f"{type(e).__name__}: {e}"
            finally:
                started.set()  # run 未开始的失败也要唤醒 start_run
                done.set()
                hub.close()

        thread = threading.Thread(target=_work, name=f"agent-os-run-{skill}", daemon=True)
        state["task"] = thread
        thread.start()
        await asyncio.to_thread(started.wait)
        run_id = holder.get("run_id")
        if run_id is None:
            raise RunValidationError(state["error"] or "run 未开始")
        if wait:
            await asyncio.to_thread(done.wait)
        return run_id

    async def stop_run(self, run_id: str) -> bool:
        """``POST stop``(§4.3):``RunControl.stop`` 置中止标志,run 在下一个 safe point 中止。

        run 不在内存态或已结束 → ``False``(路由层归 409)。``ctl.stop`` 只写
        中止标志表,跨线程 await 无事件循环亲和性问题。
        """
        state = self.state_of(run_id)
        if state is None or state.get("status") != "running":
            return False
        ctl = getattr(state.get("kernel"), "ctl", None)
        if ctl is None:
            return False
        await ctl.stop(run_id, "web stop")
        return True

    async def resume_run(self, run_id: str) -> dict[str, Any]:
        """``POST resume``(§4.3):从该 run 产物目录的 checkpoint.json 恢复 run。

        用同一 config 新建内核,经 ``execute_resume`` 恢复;产物(result/checkpoint)
        与内存态写回原 run。阻塞到恢复结束,返回 RunRecord dict。checkpoint 缺失
        抛 ``FileNotFoundError``(路由层归 404),畸形抛 ``ValueError``(归 400),
        在途 run 抛 :class:`ResumeConflictError`(归 409)。
        """
        run_dir = self._artifacts_root / "runs" / run_id
        checkpoint_path = run_dir / "checkpoint.json"
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"找不到 checkpoint: {run_id}")
        state = self.state_of(run_id)
        if state is not None and state.get("status") == "running":
            raise ResumeConflictError(f"run {run_id} 仍在进行,不能 resume")
        hub = SignalHub()  # 原 hub 已随首次 run 关闭;resume 期间换新,SSE 可继续观察
        with self._lock:
            self._hubs[run_id] = hub
            if state is None:
                state = self._cold_state(run_dir)
                self._active[run_id] = state
            state["status"] = "running"

        def _work() -> None:
            try:
                kernel = self._assemble_kernel()
                state["kernel"] = kernel

                async def _fan(sig: Signal) -> None:
                    hub.publish(signal_row(sig))

                kernel.signals.subscribe("*", _fan)
                record = execute_resume(
                    kernel, checkpoint_path, artifacts_root=self._artifacts_root, host="web"
                )
                state["status"] = record["status"]
                state["record"] = record
            except Exception:
                state["status"] = STATUS_FAILED
                raise
            finally:
                hub.close()

        await asyncio.to_thread(_work)
        return state["record"]

    @staticmethod
    def _cold_state(run_dir: Path) -> dict[str, Any]:
        """冷数据 run(进程重启后无内存态)的 resume 用 state 骨架,skill 等取 meta.json。"""
        meta: dict[str, Any] = {}
        try:
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        return {
            "task": None,
            "status": "running",
            "record": None,
            "skill": meta.get("skill"),
            "error": None,
            "started_at": meta.get("started_at") or datetime.now(UTC).isoformat(),
            "kernel": None,
        }

    def _shared_registry(self) -> Any:
        """惰性装配共享 skills registry(reload/只读查询用)。

        配置未装配 skills → :class:`RunValidationError`。每 run 的内核仍各自重建
        registry(§6.1),本 registry 仅供 ``GET /api/skills`` 与 reload。
        """
        registry = self._skills_registry
        if registry is None:
            registry = self._assemble_kernel().skills
            if registry is None:
                raise RunValidationError("配置未装配 skills registry(缺 [skills].path)")
            self._skills_registry = registry
        return registry

    def reload_skills(self) -> bool:
        """``POST /api/skills/reload``(§4.3):共享 registry 的热重载(mtime 检查,§6.1)。

        返回是否真重载。reload 只影响后续新建的 run(每 run 独立内核、独立
        registry),在跑的 run 钉住旧版。
        """
        return bool(self._shared_registry().reload())

    def skills_manifests(self) -> list[Any]:
        """``GET /api/skills``(WEB-UI.md §6.2):共享 registry 的 manifest 列表(拓扑序)。"""
        return list(self._shared_registry().manifests())

    def skill_manifest(self, name: str) -> Any | None:
        """按名字取 manifest;不存在返回 ``None``(路由层归 404)。"""
        return next((m for m in self.skills_manifests() if m.name == name), None)

    def state_of(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._active.get(run_id)

    def hub_of(self, run_id: str) -> SignalHub | None:
        with self._lock:
            return self._hubs.get(run_id)

    def active_items(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return list(self._active.items())
