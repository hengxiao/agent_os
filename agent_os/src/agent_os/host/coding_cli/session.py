"""SessionRunner(P2-M1):交互式 coding 宿主的会话生命周期核心,与 I/O 完全解耦。

渲染/输入在 M2 的 repl.py;本模块只交付逻辑层。线程模型(web RunManager 最小复刻,
docs/RUNNERS.md §4.2):

- run 在 **worker 线程**:``execute_run``/``execute_resume`` 是阻塞式 ``asyncio.run``
  (host/shared/artifacts.py),与 UI 线程彻底解耦;
- worker→UI:订阅 ``"*"`` 信号,行化为轻量事件 dict 放入 ``queue.Queue``
  (:meth:`SessionRunner.events`)。**信号订阅者在 run 循环上被内联 await**
  (kernel/signals.py),必须 O(1)——只行化 + ``put_nowait``,绝不渲染/阻塞
  (web ``_fan``/SignalHub 先例);
- UI→run:supervisor 答案经 ``InboxChannel.answer``(loop.call_soon_threadsafe
  跨线程结算);user.ask 答案走本模块 :class:`InboxUserChannel`(仿 web
  ``_InboxUserChannel``,ask 挂起等答复、notify 只进渲染队列);中途插话
  (:meth:`inject_user_message`)经 ``run_coroutine_threadsafe`` 桥进 run 循环
  写根帧 ``working[EVENT_QUEUE_KEY]``(纯内存写,与 runner 同循环无竞态;
  不用 ctl.inject_message——mid-step 破坏 tool_call/tool_result 配对)。

事件类型(M2 行式渲染器的消费契约,枚举稳定;所有事件都有 ``type`` 键)::

    run.start   {run_id}
    chunk       {skill, seq, text}              # post:llm.chunk 流式分片
    tool.start  {tool, args, depth, frame_id}   # args = ≤120 字符摘要
    tool.end    {tool, ok, depth, frame_id}
    frame.push  {skill, depth, frame_id}
    frame.pop   {skill, depth, frame_id}
    question    {channel, question_id, question, options, urgency, kind}
    # channel ∈ {"supervisor", "user"};问题进 pending 即推(pending_questions 拉取明细)
    notify      {text}                          # system.user.notify 单向通知
    run.end     {run_id, status, result, error, usage, summary}

pause/stop 语义(交互宿主补丁):除 ``ctl.pause``/``ctl.stop`` 置旗外,把
``RunPaused``/``RunAborted`` 注入两个收件箱的挂起等待点(``fail_all``)——
挂起期间未决问题的 ask 以 RunAborted 族异常收场,内核"异常保留 pending"
(runner.py ``_ask_supervisor`` 注;RunAborted 族不写 interrupted 占位),
checkpoint 留 ``_pending_ask``,resume 经 ``_settle_pending_ask`` 以
**新 question_id** 重问,UI 层重答即可。stock ``ctl.pause`` 单独用时,挂起
旗要等 answer 结算后的下一个 pre:step 才消费,那时 pending 已正常闭环弹出,
resume 不会重问——交互场景"暂停一个正在等答复的 run"因此必须走 fail_all。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar

from agent_os.api.v1 import (
    POST_FRAME_POP,
    POST_LLM_CHUNK,
    POST_TOOL_CALL,
    PRE_FRAME_PUSH,
    PRE_TOOL_CALL,
    RUN_STARTED,
    SUPERVISOR_ASK,
    Allow,
    Mode,
    Signal,
    cli_principal,
)
from agent_os.host.coding_cli.session_store import SessionStore
from agent_os.host.shared.artifacts import execute_resume, execute_run
from agent_os.kernel.control import EVENT_QUEUE_KEY
from agent_os.kernel.errors import RunAborted, RunPaused
from agent_os.runtime.config import build_kernel, load_config
from agent_os.runtime.overrides import apply_overrides
from agent_os.supervisor import InboxChannel

#: 工具参数摘要的截断上限(行式渲染单行约束;截断显式标记,不静默)
_TOOL_ARGS_MAX_CHARS = 120


def _brief(value: Any, max_chars: int = _TOOL_ARGS_MAX_CHARS) -> str:
    """工具参数 → 单行摘要(JSON 序列化 + 截断;不可序列化值 repr 兜底)。"""
    text = json.dumps(value, ensure_ascii=False, default=repr)
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


def _rowify(sig: Signal) -> dict[str, Any] | None:
    """信号 → 渲染事件 dict(纯函数,O(1);不在渲染契约内的信号 → None)。

    订阅者在 run 循环上被内联 await,本函数绝不渲染/阻塞(web ``_fan`` 先例)。
    """
    payload = sig.payload or {}
    name = sig.name
    if name == POST_LLM_CHUNK:
        return {
            "type": "chunk",
            "skill": payload.get("skill"),
            "seq": payload.get("seq"),
            "text": payload.get("text", ""),
        }
    if name == PRE_TOOL_CALL:
        return {
            "type": "tool.start",
            "tool": payload.get("tool"),
            "args": _brief(payload.get("args")),
            "depth": payload.get("depth"),
            "frame_id": payload.get("frame_id"),
        }
    if name == POST_TOOL_CALL:
        return {
            "type": "tool.end",
            "tool": payload.get("tool"),
            "ok": payload.get("ok"),
            "depth": payload.get("depth"),
            "frame_id": payload.get("frame_id"),
        }
    if name == PRE_FRAME_PUSH:
        return {
            "type": "frame.push",
            "skill": payload.get("skill"),
            "depth": payload.get("depth"),
            "frame_id": payload.get("frame_id"),
        }
    if name == POST_FRAME_POP:
        return {
            "type": "frame.pop",
            "skill": payload.get("skill"),
            "depth": payload.get("depth"),
            "frame_id": payload.get("frame_id"),
        }
    if name == SUPERVISOR_ASK:
        # 问题进 pending 的信号触发点(UI 据此转问答态;明细走 pending_questions)
        return {
            "type": "question",
            "channel": "supervisor",
            "question_id": payload.get("question_id"),
            "question": payload.get("question"),
            "options": payload.get("options"),
            "urgency": payload.get("urgency"),
            "kind": payload.get("kind"),
        }
    if name == RUN_STARTED:
        return {"type": "run.start", "run_id": sig.run_id}
    return None


def _root_frame_id(tree: list[dict[str, Any]]) -> str | None:
    """帧树(``ctl.get_frame_tree`` 的嵌套 dict)中 depth 最小节点的 frame_id;空树 → None。"""
    best: tuple[int, Any] | None = None
    stack = list(tree)
    while stack:
        node = stack.pop()
        depth = node.get("depth", 0)
        if best is None or depth < best[0]:
            best = (depth, node.get("frame_id"))
        stack.extend(node.get("children") or [])
    return best[1] if best else None


class _CtlBridge:
    """no-op ASYNC sidecar:让 builder 恒装配 ``kernel.ctl``(web ``_StopBridge`` 先例)。

    builder 只在有 sidecar 时装配 RunControlImpl(runtime/builder.py);本宿主的
    pause/stop/inject 全走 ctl,与配置是否声明 sidecar 无关。不订阅任何信号。
    """

    name: ClassVar[str] = "_ctl_bridge"
    subscriptions: ClassVar[list[str]] = []
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    async def on_signal(self, sig: Signal, ctl: Any) -> Allow:
        return Allow()


class InboxUserChannel:
    """``system.user.ask``/``system.user.notify`` 的会话宿主通道(M1 §8.3;仿 web
    ``_InboxUserChannel``,但 ask 走自制挂起 pending 而非复用 supervisor 收件箱)。

    ``ask``:问题进 pending 表(带 ``user-`` 前缀 question_id)并挂起等
    :meth:`answer` 跨线程结算(loop.call_soon_threadsafe,同 InboxChannel 先例);
    同时向渲染队列推 ``question`` 事件(UI 触发点)。

    ``notify``:单向,只往渲染队列推 ``notify`` 事件;可观测面另有工具层
    ``user.notify`` 信号兜底(trace;渲染契约不收它,防与通道侧重复)。
    """

    def __init__(self, events: queue.Queue) -> None:
        self._events = events
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, Any]] = {}

    async def ask(self, question: str) -> str:
        question_id = f"user-{uuid.uuid4().hex[:12]}"
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        with self._lock:
            self._pending[question_id] = {
                "question": question,
                "asked_at": time.time(),
                "future": future,
            }
        self._events.put_nowait(
            {"type": "question", "channel": "user", "question_id": question_id, "question": question}
        )
        try:
            return await future
        finally:
            with self._lock:
                self._pending.pop(question_id, None)

    async def notify(self, message: str) -> None:
        """单向通知:只进渲染队列(O(1) put_nowait,run 循环内联可调)。"""
        self._events.put_nowait({"type": "notify", "text": message})

    def pending(self) -> list[dict[str, Any]]:
        """挂起列表(``pending_questions`` 聚合源;行带 ``channel="user"``)。"""
        with self._lock:
            entries = list(self._pending.items())
        return [
            {
                "question_id": question_id,
                "question": entry["question"],
                "asked_at": entry["asked_at"],
                "options": None,
                "channel": "user",
            }
            for question_id, entry in entries
        ]

    def answer(self, question_id: str, text: str) -> bool:
        """结算挂起问题(任意线程可调);找不到(或 run 循环已关闭)→ False。"""
        with self._lock:
            entry = self._pending.get(question_id)
        if entry is None:
            return False
        future: asyncio.Future[str] = entry["future"]
        try:
            future.get_loop().call_soon_threadsafe(self._settle, future, text)
        except RuntimeError:  # 事件循环已关闭(run 已终结)
            with self._lock:
                self._pending.pop(question_id, None)
            return False
        return True

    def fail_all(self, exc: BaseException) -> int:
        """全部挂起 ask 以异常收场(SessionRunner pause/stop 语义,见 InboxChannel.fail_all)。"""
        with self._lock:
            entries = list(self._pending.values())
        injected = 0
        for entry in entries:
            future: asyncio.Future[str] = entry["future"]
            try:
                future.get_loop().call_soon_threadsafe(self._fail, future, exc)
            except RuntimeError:
                continue
            injected += 1
        return injected

    @staticmethod
    def _settle(future: asyncio.Future[str], text: str) -> None:
        if not future.done():  # 竞态先到先得(同 InboxChannel._settle)
            future.set_result(text)

    @staticmethod
    def _fail(future: asyncio.Future[str], exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)


class SessionRunner:
    """交互会话的 run 生命周期核心(P2-M1;docs/RUNNERS.md §2 宿主契约)。

    一个会话 = 长存 run + checkpoint resume(WS2):``start`` 起新一轮,
    ``resume`` 从 :class:`SessionStore` 最后一个 paused turn 的 checkpoint 恢复;
    run 终态把 ``{run_id, input, status, checkpoint_path, summary}`` 追加进
    会话文档(paused 时 checkpoint_path 非空,resume 数据侧锚点)。

    ``config``:配置文件路径或等价 dict(runtime/config.py ``build_kernel`` 契约);
    ``overrides`` 经 K1 注册表 ``apply_overrides`` 合并(docs/RUNNERS.md §2.5)。
    ``session_id=None`` = 纯内存会话(不写 SessionStore)。
    """

    def __init__(
        self,
        config: str | Path | dict[str, Any],
        *,
        artifacts_root: str | Path,
        session_store: SessionStore,
        session_id: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        self._config = config
        self._artifacts_root = Path(artifacts_root)
        self._store = session_store
        self._session_id = session_id
        self._overrides = dict(overrides or {})
        self._events: queue.Queue = queue.Queue()
        self._inbox = InboxChannel()
        self._user_channel = InboxUserChannel(self._events)
        self._lock = threading.Lock()
        self._kernel: Any = None
        self._thread: threading.Thread | None = None
        self._run_id: str | None = None
        self._run_loop: asyncio.AbstractEventLoop | None = None
        self._skill: str | None = None

    # ------------------------------------------------------------------
    # 公开面:run 生命周期
    # ------------------------------------------------------------------

    def start(self, skill: str, input: dict[str, Any]) -> None:
        """起 worker 线程跑 ``execute_run``(非阻塞;run 进展经 :meth:`events` 流出)。

        已有进行中的 run → ``RuntimeError``(先 stop/pause 再开新一轮)。
        """
        self._ensure_idle()
        self._skill = skill
        self._run_id = None
        self._ensure_session_doc(skill)
        self._spawn_worker(self._work, skill=skill, input=input)

    def resume(self) -> None:
        """从会话最后一个 paused turn 的 checkpoint 恢复(``execute_resume``;非阻塞)。

        run_id 从 checkpoint 读(resume 不重发 ``run.started``);无可恢复的
        paused turn → ``RuntimeError``;无 session_id 的纯内存会话不支持 resume。
        """
        self._ensure_idle()
        if self._session_id is None:
            raise RuntimeError("纯内存会话(session_id=None)不支持 resume;请 start 新一轮")
        doc = self._store.load(self._session_id)
        turn = next(
            (
                t
                for t in reversed(doc["turns"])
                if t.get("status") == "paused" and t.get("checkpoint_path")
            ),
            None,
        )
        if turn is None:
            raise RuntimeError(f"会话 {self._session_id} 没有可恢复的 paused turn")
        checkpoint_path = str(turn["checkpoint_path"])
        ck = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
        self._skill = doc.get("skill")
        self._run_id = ck["run"]["run_id"]  # 提前钉上:resume 期间 pause/inject 即可用
        self._spawn_worker(
            self._work, checkpoint_path=checkpoint_path, input=turn.get("input") or {}
        )

    def pause(self, reason: str = "人工暂停") -> bool:
        """``ctl.pause``(下一个 safe point 挂起,落 PAUSED + checkpoint,可 resume)。

        挂起期间未决问题以 ``RunPaused`` 收场(模块 docstring「pause/stop 语义」),
        resume 后以新 question_id 重问。无活跃 run / 内核无 ctl → ``False``(防御)。
        """
        if not self._ctl_available():
            return False
        asyncio.run(self._kernel.ctl.pause(self._run_id, reason))  # 纯写标志表,无循环亲和性
        self._inbox.fail_all(RunPaused(reason))
        self._user_channel.fail_all(RunPaused(reason))
        return True

    def stop(self, reason: str = "人工中止") -> bool:
        """``ctl.stop``(下一个 safe point 中止,ABORTED);未决问题以 RunAborted 收场。"""
        if not self._ctl_available():
            return False
        asyncio.run(self._kernel.ctl.stop(self._run_id, reason))
        self._inbox.fail_all(RunAborted(reason))
        self._user_channel.fail_all(RunAborted(reason))
        return True

    def inject_user_message(self, text: str) -> bool:
        """中途插话:桥协程把事件写根帧 ``working[EVENT_QUEUE_KEY]``(下一步 build
        前排干为批头消息,保 §7.4 配对原子性;web inject_event 先例)。

        不用 ``ctl.inject_message``(mid-step 直接 append 会破坏 tool_call/
        tool_result 配对)。找不到根帧 / 无活跃 run / 无 ctl → ``False``。
        """
        kernel, run_id, loop = self._kernel, self._run_id, self._run_loop
        ctl = getattr(kernel, "ctl", None)
        if (
            ctl is None
            or run_id is None
            or loop is None
            or not loop.is_running()
            or not self._alive()
        ):
            return False

        async def _enqueue() -> bool:
            frame_id = _root_frame_id(await ctl.get_frame_tree(run_id))
            if frame_id is None:
                return False
            frame = kernel.stack.get(frame_id)
            if frame is None:
                return False
            # 纯内存写:桥内与 runner 同循环,无竞态(web run_manager 先例)
            frame.context.working.setdefault(EVENT_QUEUE_KEY, []).append(
                {"type": "user", "text": text, "at": time.time()}
            )
            return True

        try:
            future = asyncio.run_coroutine_threadsafe(_enqueue(), loop)
        except RuntimeError:  # 循环已关闭(run 刚结束)
            return False
        try:
            return bool(future.result(timeout=5.0))
        except Exception:  # noqa: BLE001 — 桥内失败按无法注入归类,不崩 UI
            return False

    # ------------------------------------------------------------------
    # 公开面:UI 消费
    # ------------------------------------------------------------------

    def events(self) -> queue.Queue:
        """渲染事件队列(UI 侧 ``get(timeout=...)`` 消费;事件类型见模块 docstring)。"""
        return self._events

    def pending_questions(self) -> list[dict[str, Any]]:
        """聚合两通道的挂起问题(supervisor 收件箱 + user 通道;行带 ``channel`` 键)。"""
        rows = [{**row, "channel": "supervisor"} for row in self._inbox.pending()]
        rows.extend(self._user_channel.pending())
        return rows

    def answer(self, question_id: str, text: str) -> bool:
        """按 question_id 路由到正确通道结算;找不到(或 run 已终结)→ ``False``。

        options 不合的重问语义由 SupervisorManager 协议承担(带 previous_error
        重问,收件箱呈现最新一次),本层原样透传。
        """
        if self._inbox.get(question_id) is not None:
            return self._inbox.answer(
                question_id, {"answer": text, "decided_by": "host:coding-cli"}
            )
        return self._user_channel.answer(question_id, text)

    @property
    def run_id(self) -> str | None:
        """当前/最近一轮的 run_id(无 → None;resume 轮从 checkpoint 读得)。"""
        return self._run_id

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def is_active(self) -> bool:
        """是否有进行中的 run(worker 线程存活;P2-M2 的 /status 与输入分派用)。"""
        return self._alive()

    def frame_tree(self) -> list[dict[str, Any]] | None:
        """只读帧树(P2-M2 ``/status``;``ctl.get_frame_tree`` 桥读)。

        UI 线程经一次性 ``asyncio.run`` 调 ctl 协程(纯内存读,无循环亲和性,
        同 pause/stop 先例;web ``debug_frame`` 也从 REST 线程直读 kernel.stack)。
        无活跃 run / 无 ctl → ``None``。
        """
        if not self._ctl_available():
            return None
        return asyncio.run(self._kernel.ctl.get_frame_tree(self._run_id))

    def usage(self) -> dict[str, Any] | None:
        """只读记账快照(P2-M2 ``/status``;``ctl.get_usage`` → Usage asdict);无活跃 run → None。"""
        if not self._ctl_available():
            return None
        usage = asyncio.run(self._kernel.ctl.get_usage(self._run_id))
        return dataclasses.asdict(usage)

    # ------------------------------------------------------------------
    # worker 线程(以下全在 worker 侧;UI 线程只经上面的公开面进来)
    # ------------------------------------------------------------------

    def _ensure_idle(self) -> None:
        if self._alive():
            raise RuntimeError("已有进行中的 run(先 stop/pause 等其落幕,或 resume 接续)")

    def _alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def _ctl_available(self) -> bool:
        return (
            self._alive()
            and self._run_id is not None
            and getattr(self._kernel, "ctl", None) is not None
        )

    def _spawn_worker(self, target: Any, **kwargs: Any) -> None:
        thread = threading.Thread(
            target=target, kwargs=kwargs, daemon=True, name="coding-cli-run"
        )
        self._thread = thread
        thread.start()

    def _ensure_session_doc(self, skill: str) -> None:
        """会话文档惰性创建(resume 既有会话时文档已存在;纯内存会话跳过)。

        overrides 随文档落盘(P2-M3):``--resume`` 重启后由 SessionRunner 继承
        (会话的 workdir/模型等覆盖不丢;显式 flag 优先于存档,见 __main__)。
        """
        if self._session_id is None:
            return
        try:
            self._store.load(self._session_id)
        except FileNotFoundError:
            config_path = (
                str(self._config) if isinstance(self._config, (str, Path)) else ""
            )
            self._store.create(
                self._session_id, skill, config_path, overrides=self._overrides
            )

    def _assemble(self) -> Any:
        """装配本轮内核:load_config → K1 apply_overrides → build_kernel。

        恒塞 :class:`_CtlBridge` 保证 ctl 在场(pause/stop/inject 通道,web
        ``_StopBridge`` 先例);supervisor handler = InboxChannel(挂起式收件箱,
        ``timeout_s`` 配置是无人作答的兜底);user channel = InboxUserChannel。
        """
        cfg = (
            load_config(self._config)
            if isinstance(self._config, (str, Path))
            else dict(self._config)
        )
        if self._overrides:
            cfg = apply_overrides(cfg, self._overrides)  # K1(docs/RUNNERS.md §2.5)
        return build_kernel(
            cfg,
            extra_sidecars=[_CtlBridge()],
            supervisor_handler=self._inbox,
            user_channel=self._user_channel,
        )

    def _work(self, skill: str | None = None, input: dict[str, Any] | None = None,
              checkpoint_path: str | None = None) -> None:
        """worker 本体:装配 → 订阅扇出 → execute_run/execute_resume → turn 落盘 → run.end。"""
        kernel = None
        try:
            kernel = self._assemble()
            self._kernel = kernel
            kernel.signals.subscribe("*", self._fan)
            # inject 桥:worker 事件循环句柄(逐信号捕获;web _cap_run_loop 先例)
            kernel.signals.subscribe("*", self._cap_loop)
            kernel.signals.subscribe(RUN_STARTED, self._cap_run_id)
            if checkpoint_path is None:
                record = execute_run(
                    kernel,
                    str(skill),
                    dict(input or {}),
                    artifacts_root=self._artifacts_root,
                    host="coding-cli",
                    # 数据层身份(docs/DATA-AUTHZ.md §2.2):本机用户即身份(同 CLI)
                    principal=cli_principal(),
                )
            else:
                record = execute_resume(
                    kernel,
                    checkpoint_path,
                    artifacts_root=self._artifacts_root,
                    host="coding-cli",
                )
        except Exception as e:  # noqa: BLE001 — 装配/校验错归 run.end 事件,不炸 UI 线程
            self._events.put_nowait(
                {
                    "type": "run.end",
                    "run_id": self._run_id,
                    "status": "failed",
                    "result": None,
                    "error": f"{type(e).__name__}: {e}",
                    "usage": None,
                    "summary": f"{type(e).__name__}: {e}",
                }
            )
            return
        finally:
            self._run_loop = None  # 循环随本轮销毁,inject 桥摘除
            if kernel is not None:
                self._close_telemetry(kernel)
        self._finish_turn(record, dict(input or {}))

    def _finish_turn(self, record: dict[str, Any], turn_input: dict[str, Any]) -> None:
        """run 终态收尾:turn 追加进会话文档(paused 带 checkpoint_path)+ run.end 事件。

        ``turn_input``:本轮输入(RunRecord 不含 input 键;start 轮 = start 入参,
        resume 轮 = 被恢复 paused turn 的 input,由 :meth:`resume` 从会话文档取)。
        """
        run_id = record["run_id"]
        status = record["status"]
        run_dir = self._artifacts_root / "runs" / run_id
        checkpoint_path = (
            str(run_dir / "checkpoint.json") if status == "paused" else None
        )
        summary = self._summarize(record)
        turn = {
            "run_id": run_id,
            "input": turn_input,
            "status": status,
            "checkpoint_path": checkpoint_path,
            "summary": summary,
        }
        store_error = None
        if self._session_id is not None:
            try:
                self._store.append_turn(self._session_id, turn)
            except Exception as e:  # noqa: BLE001 — 落盘失败不篡改 run 的终态,事件里注明
                store_error = f"{type(e).__name__}: {e}"
        event: dict[str, Any] = {
            "type": "run.end",
            "run_id": run_id,
            "status": status,
            "result": record.get("result"),
            "error": record.get("error"),
            "usage": record.get("usage"),
            "summary": summary,
        }
        if store_error is not None:
            event["store_error"] = store_error
        self._events.put_nowait(event)

    @staticmethod
    def _summarize(record: dict[str, Any]) -> str:
        """turn 摘要:错误优先,否则 result repr(截 200 字符,行式展示约束)。"""
        if record.get("error"):
            return str(record["error"])[:200]
        return repr(record.get("result"))[:200]

    async def _fan(self, sig: Signal) -> None:
        """信号 → 渲染事件(O(1):行化 + put_nowait;在 run 循环上被内联 await)。"""
        row = _rowify(sig)
        if row is not None:
            self._events.put_nowait(row)

    async def _cap_loop(self, sig: Signal) -> None:
        """捕获 run worker 的事件循环(inject 桥投递目标;幂等重写)。"""
        self._run_loop = asyncio.get_running_loop()

    async def _cap_run_id(self, sig: Signal) -> None:
        """``run.started`` 捕获 run_id(resume 不重发,resume 路径从 checkpoint 读)。"""
        self._run_id = sig.run_id

    @staticmethod
    def _close_telemetry(kernel: Any) -> None:
        """宿主退出点排干遥测 sink(§10.2;同 CLI ``_close_telemetry`` 先例:close 是
        排干点,失败只告警——遥测是观察通道,收尾失败不该改变 run 结果)。

        duck-typed:telemetry 缺席/无 close 即 no-op;execute_run 的 asyncio.run
        已销毁 run loop,close 在新 loop 上跑(exporter 异步收尾不绑定旧 loop)。
        """
        close = getattr(getattr(kernel, "telemetry", None), "close", None)
        if close is None:
            return
        with contextlib.suppress(Exception):
            result = close()
            if inspect.isawaitable(result):
                asyncio.run(result)
