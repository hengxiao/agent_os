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
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_os.api.v1 import RUN_STARTED, Signal
from agent_os.host.shared.artifacts import execute_run
from agent_os.host.shared.runrecord import STATUS_FAILED
from agent_os.runtime.config import build_kernel

#: hub 关闭时投递给订阅者的哨兵(§4.2:run 结束后 SSE 发终止事件并关闭)
HUB_CLOSED: Any = object()

#: 环形缓冲容量(§4.2 默认 2000 条)
BUFFER_MAXLEN = 2000


class RunValidationError(RuntimeError):
    """run 未开始即失败的校验类错误(技能不存在/输入不合 schema/装配失败)。

    与 CLI 退出码 2 同类(§3.3);Web 侧由路由层归 ``200 + {"status": "failed"}``。
    """


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
    "started_at"}``;历史 run 重启后由路由层从产物目录重建(冷数据,§4.2)。
    """

    def __init__(self, config_path: str | Path, artifacts_root: Path) -> None:
        self._config_path = config_path
        self._artifacts_root = Path(artifacts_root)
        self._lock = threading.Lock()
        self._active: dict[str, dict[str, Any]] = {}
        self._hubs: dict[str, SignalHub] = {}

    async def start_run(self, skill: str, input: dict[str, Any], wait: bool = False) -> str:
        """启动一个 run 并返回 run_id;``wait=True`` 时阻塞到 run 结束。

        run_id 从 ``run.started`` 信号捕获:无论 wait 与否都先等到 run 真正开始
        (或开始即失败)再返回;run 未开始的校验错抛 :class:`RunValidationError`。
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
        }

        def _register(run_id: str) -> None:
            with self._lock:
                self._active[run_id] = state
                self._hubs[run_id] = hub
            holder["run_id"] = run_id
            started.set()

        def _work() -> None:
            try:
                kernel = build_kernel(self._config_path)

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

    def state_of(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._active.get(run_id)

    def hub_of(self, run_id: str) -> SignalHub | None:
        with self._lock:
            return self._hubs.get(run_id)

    def active_items(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return list(self._active.items())
