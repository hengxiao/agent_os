"""RunManager(docs/RUNNERS.md §4.2;R3):Web runner 的进程内 run 管理器。

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
  ``kernel.ctl`` 恒存在 → ``stop_run`` 经 ``ctl.stop`` 在下一个 safe point 中止
  (WS2:``pause_run`` 经同一通道 ``ctl.pause`` 在下一个 safe point 挂起,
  RunRecord 归 ``paused``,可 resume);
- ``resume_run``:用同一 config 新建内核,经 host/shared 的 ``execute_resume``
  从该 run 的 checkpoint.json 恢复,产物与内存态写回原 run;resume 期间换一只
  新 hub(原 hub 已在首次 run 结束时关闭),SSE 可继续观察;
- ``reload_skills``:共享一份 skills registry(惰性装配,仅供查询/reload;
  每 run 的内核仍各自重建 registry,reload 只影响后续新建的 run,§6.1)。

D3 增量(docs/WEB-UI.md §4.3/§6.2):

- ``start_run(..., overrides=...)``:``{model?, max_cost?, max_steps?, inline?}`` 合并进
  本次 run 的 RunConfig——每次重新 ``load_config`` 读文件,改动只落在该 run
  私有的 config dict 副本上,不污染共享配置(后续 run 与 reload 路径不受影响);
- ``skills_manifests`` / ``skill_manifest``:共享 registry 的只读查询
  (``GET /api/skills`` 数据源),与 ``reload_skills`` 共用惰性装配路径。

D4 增量(docs/WEB-UI.md §4.7/§6.2):

- ``tools_specs``:共享 tools registry 的全量 ToolSpec(``GET /api/tools``
  数据源);与 skills 共享 registry 同一惰性装配路径,但 ``[tools].builtins``
  恒视为 True——浏览器展示宿主全量工具面,与单 run 的 ``[tools]`` 开关无关。

D6 增量(一站多 skill set;tests/test_skillsets.py 锚点):

- ``skillsets_dir``:`<root>/<set>/skills.yaml`(必须)+ 可选 set 级
  ``agent-os.toml``(与全局 config **深合并**,继承全局)+ 可选 py 模块;
  ``_assemble_kernel(skill_set=...)`` 按 set 装配内核(每 set 惰性,共享 registry
  与每 run 内核都走同一路径);
- 装配前把 set 目录与全局配置目录钉到 ``sys.path`` 前两位并逐出同名外来缓存
  模块(``_prepare_set_imports``):set 自己声明的 dotted path 解析到本 set,
  深合并继承来的解析到全局配置目录,都不再依赖 PYTHONPATH。已知限制:code
  技能 handler 是调用时惰性 import(§6.3),跨 set 同名 handler 模块不支持(改名规避);
- ``start_run(..., skill_set=...)``:显式 set 用该 set 装配;未指定时唯一 set
  自动生效,否则全局 config(向后兼容);run 记录(内存态 + meta.json/result.json)
  带 ``skill_set``(全局 run 记 ``"default"``)。

S2 增量(docs/SUPERVISOR.md v2 §2.3/§5;tests/web/test_supervisor_channel.py 锚点):

- **Web 收件箱即默认宿主通道**:``_assemble_kernel`` 经
  ``build_kernel(supervisor_handler=...)`` 注入 supervisor handler——通道选择
  顺序(§2.3):run 级注入(``start_run(supervisor_handler=...)``)> 装配级注入
  (``RunManager(supervisor_handler=...)``)> 进程共享 :class:`InboxChannel`;
- ``supervisor_pending`` / ``supervisor_answer``:收件箱查询与结算
  (``GET /api/supervisor/pending`` / ``POST /api/supervisor/{id}/answer``
  数据源);options 校验在结算前,不匹配归 ``ValueError``(路由 400,
  问题保持挂起,§3:格式错误返回调用方重答)。

P3 增量(Agent OS Debugger;tests/web/test_debug_api.py 锚点):

- 共享一个 :class:`DebugController`(kernel/debug.py;``_debug``),经
  ``start_run(..., debug_session=...)`` 挂到该 run 内核的总线上——订阅序在
  hub(``_fan``)之后、``_cap`` 之前:暂停前信号已落 hub/trace,且
  ``start_run`` 返回时会话已绑定 run;每 run 至多一个活跃会话
  (``start_debug_session`` 冲突归 :class:`DebugConflictError`,路由 409);
- **跨线程命令桥**:``DebugSession`` 的 ``asyncio.Event`` 绑在 run worker
  线程的事件循环上,REST 线程直接 ``set()`` 会撞 ``loop.call_soon`` 的
  线程检查;``_debug_loops`` 逐会话记录 worker 循环(run.started 时捕获),
  resume/modify/inject/detach 一律经 ``run_coroutine_threadsafe`` 投递过去
  执行并等结果(``_in_debug_loop``)。

P5 增量(时间旅行):``start_debug_replay_session``——``POST /api/debug/sessions``
的 ``{replay_run_id, until_step?}`` 形态;``start_run(..., replay_script=...)``
在装配后 ``replace_providers`` 换回放内核(host/shared/replay.py 边界:
LLM Mock 回放,工具真实重跑);``overrides`` 增 ``checkpoint_interval``
(周期 checkpoint,RunConfig 同名字段,0=关)。

M1 增量(§8.3 User Communication;tests/web/test_user_channel.py 锚点):

- 每个内核 post-build 接 :class:`_InboxUserChannel`(system.user.ask/notify
  的宿主回调):ask 复用 supervisor 收件箱(``kind="user-ask"``,同一
  pending/answer 管路);notify 回调 no-op——可观测面在工具层补发的
  ``user.notify`` 信号(带 run/frame 归因,经 per-run hub 进 SSE、经
  telemetry 落 trace.jsonl;收件箱无免答条目形态)。

E4 增量(§17 开放问题 4 外部事件唤醒的宿主层形态;tests/web/test_events_api.py 锚点):

- ``inject_event``:在跑 run 的事件注入——``run_coroutine_threadsafe`` 桥到
  run worker 循环内查 ``ctl.get_frame_tree`` 取根帧(depth 最小);``[events].batch``
  开(缺省)时条目入根帧工作内存队列 ``_event_queue``(runner 在下一步 build 前
  排干为批头消息,保 §7.4 配对原子性),关时 ``ctl.inject_message`` 立即追加
  USER/INJECTED 消息(E4 原行为);worker 循环句柄逐 run 在首个内核信号时捕获
  (``_run_loops``,与调试命令桥同构);
- ``resume_run(..., inject=...)``:paused 唤醒——resume 前向 checkpoint.json
  根帧的 ``context.messages`` 追加事件消息(dict 形状逐字对齐内核
  ``_message_to_dict`` 落盘形态),写回后走原 execute_resume 路径;
- ``emit_event`` + ``EVENT_RECEIVED``:事件到达与路由结果经 per-run hub 投
  ``event.received`` 行(宿主层信号,刻意不进 api/v1),SSE/回看可见。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from agent_os.api.v1 import (
    CONFIDENTIAL,
    PRE_TOOL_CALL,
    RUN_STARTED,
    Allow,
    Message,
    Mode,
    Principal,
    Question,
    Role,
    RunControl,
    Signal,
    Source,
    web_single_user_principal,
)
from agent_os.host.shared.artifacts import execute_resume, execute_run
from agent_os.host.shared.replay import build_mock_script, replace_providers
from agent_os.host.shared.runrecord import STATUS_FAILED
from agent_os.kernel.control import EVENT_QUEUE_KEY
from agent_os.kernel.debug import RESUME_COMMANDS as DEBUG_RESUME_COMMANDS
from agent_os.kernel.debug import DebugController
from agent_os.kernel.errors import AgentOSError
from agent_os.runtime.builder import EventsSection
from agent_os.runtime.config import (
    _events_section,
    build_kernel,
    load_config,
    load_skillsets,
    web_token_map,
)

#: rerun 等 stop verdict 落地的兜底(秒):超时按 best-effort 收尾,不悬挂
_RERUN_STOP_TIMEOUT = 10.0
from agent_os.supervisor import InboxChannel

#: hub 关闭时投递给订阅者的哨兵(§4.2:run 结束后 SSE 发终止事件并关闭)
HUB_CLOSED: Any = object()

#: 环形缓冲容量(§4.2 默认 2000 条)
BUFFER_MAXLEN = 2000

#: ``POST /api/runs`` 的 ``overrides`` 允许覆盖的 RunConfig 字段(docs/WEB-UI.md §4.3 高级区;
#: ``inline`` = docs/SKILL-INLINING.md §9 消融开关,写入 ``cfg["run"]["inline"]``;
#: ``checkpoint_interval`` = Debugger P5 周期 checkpoint,0=关)
OVERRIDE_FIELDS = ("model", "max_cost", "max_steps", "inline", "checkpoint_interval")


class _StopBridge:
    """内部 no-op ASYNC sidecar(R4 stop 通道的装配占位)。

    M4 起 KernelBuilder **有 sidecar 才**装配 ``kernel.ctl``(RunControlImpl);
    Web 的 stop(``POST /api/runs/{id}/stop`` → ``ctl.stop``)要求每个 run 的内核
    都有 ctl,与配置文件是否声明 sidecar 无关。本 sidecar 不订阅任何信号,
    唯一作用是让 builder 走 sidecar 装配路径。(WS1 起 builder 在 tools 提供
    ``bind_timer`` 时也会补装 ctl——timer fire 注入通道;本桥保留,覆盖嵌入方
    自装无 ``bind_timer`` 的 registry 形态,零破坏。)
    """

    name: ClassVar[str] = "_stop_bridge"
    subscriptions: ClassVar[list[str]] = []
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Allow:
        return Allow()


class _InboxUserChannel:
    """M1(§8.3)Web 宿主用户通道:``system.user.ask``/``system.user.notify`` 的回调。

    ``ask``:复用 supervisor 收件箱(:class:`InboxChannel`)闭环——问题包装成
    ``Question(kind="user-ask")`` 进 pending 挂起,前端经既有
    ``GET /api/supervisor/pending`` / ``POST /api/supervisor/{id}/answer``
    管路作答(同一收件箱,按 kind 区分渲染);options=None 即自由文本作答。
    run_id/frame_id 通道契约拿不到(bind_user_channel 的 ``ask(question: str)``
    形状),留空——收件箱行以 question_id 寻址,不依赖这两个字段。

    ``notify``:no-op。收件箱没有免答通知条目形态(每条 pending 都等回答),
    而单向通知的可观测面已由工具层兜底——``system.user.notify`` 落地后向内核
    总线补发 ``user.notify`` 信号(带 run/frame 归因;tools/builtins.py),
    Web 侧经 per-run hub(``"*"`` 订阅)进 SSE、经 telemetry 落 trace.jsonl,
    宿主回调无需再做什么(这是"最薄形态"的定案,docs 见工具 docstring)。
    """

    def __init__(self, inbox: InboxChannel) -> None:
        self._inbox = inbox

    async def ask(self, question: str) -> str:
        answer = await self._inbox(
            Question(
                question_id=f"user-{uuid.uuid4().hex[:12]}",
                question=question,
                kind="user-ask",
            )
        )
        return str(answer.get("answer", ""))

    async def notify(self, message: str) -> None:
        """单向通知:可观测面在工具层的 ``user.notify`` 信号(trace/SSE),此处无操作。"""



class RunValidationError(RuntimeError):
    """run 未开始即失败的校验类错误(技能不存在/输入不合 schema/装配失败)。

    与 CLI 退出码 2 同类(§3.3);Web 侧由路由层归 ``200 + {"status": "failed"}``。
    """


class ResumeConflictError(RuntimeError):
    """resume 与在途 run 冲突(§4.3:在跑的 run 不能 resume,先 stop);路由层归 409。"""


class DebugConflictError(RuntimeError):
    """已有活跃调试会话(P3:每 run 至多一个活跃会话);路由层归 409。"""


#: 宿主层事件信号名(§17 开放问题 4:``POST /api/events`` 的到达/路由可观测面)。
#: 刻意**不进 api/v1**——事件入口是宿主层形态,内核信号枚举零改动;行形状与
#: :func:`signal_row` 输出一致,SSE/回看消费面无感。
EVENT_RECEIVED = "event.received"


def _event_type_of(text: str) -> str:
    """从事件文本的 ``[event:<type>]`` 前缀解析事件类型;无前缀(调用方自定义文本)归 ``"unknown"``。"""
    if text.startswith("[event:") and "]" in text:
        return text[len("[event:"):text.index("]")]
    return "unknown"


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


def _jsonable(value: Any) -> Any:
    """任意值 → JSON 安全结构(不可序列化值 ``repr`` 兜底,与 sink 同策略)。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=repr))


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """配置深合并(D6 set 级 ``agent-os.toml`` 继承全局):table 递归合并,其余覆盖。"""
    out = dict(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _evict_foreign_module(py: Path) -> None:
    """``sys.modules`` 缓存的同名模块若不是 ``py`` 本身则逐出(让 importlib 重解析)。"""
    mod = sys.modules.get(py.stem)
    file = getattr(mod, "__file__", None)
    if file is not None and Path(file).resolve() != py.resolve():
        del sys.modules[py.stem]


def _prepare_set_imports(set_dir: Path, global_dir: Path) -> None:
    """把 set 目录与全局配置目录钉到 ``sys.path`` 前两位,并逐出同名外来缓存模块(D6)。

    - set 目录优先(position 0):set 自己声明的 dotted path(brain/handler/
      tools.custom)解析到本 set;全局配置目录其次(position 1):深合并**继承**
      来的 dotted path(如全局 ``[providers.mock].brain``)解析到全局配置所在目录;
    - 多 set 共进程时各 set 的顶层模块可能同名(如各自的 ``brains.py``):逐出
      ``sys.modules`` 里与"本次装配应解析到的文件"不一致的同名缓存,保证
      importlib 按上述顺序重新解析。已装配内核持有的 callable 是直接引用,不受影响。
    已知限制:code 技能的 handler 是调用时惰性 import(§6.3),跨 set 同名 handler
    模块不支持(改名规避)。
    """
    dirs = [set_dir] if set_dir == global_dir else [set_dir, global_dir]
    for d in dirs:
        entry = str(d)
        while entry in sys.path:
            sys.path.remove(entry)
    for d in reversed(dirs):
        sys.path.insert(0, str(d))
    for py in set_dir.glob("*.py"):
        _evict_foreign_module(py)
    for py in global_dir.glob("*.py"):
        if not (set_dir / py.name).is_file():  # set 目录同名文件优先(上面已按 set 处理)
            _evict_foreign_module(py)


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


def message_row(msg: Message) -> dict[str, Any]:
    """Message → JSON 安全 dict(与 checkpoint.json 的 messages 行同形,P3 帧检视用)。"""
    row: dict[str, Any] = {
        "role": msg.role.value,
        "content": msg.content,
        "tool_calls": [
            {"id": tc.id, "name": tc.name, "args": tc.args} for tc in msg.tool_calls
        ],
        "tool_call_id": msg.tool_call_id,
        "name": msg.name,
        "reasoning": msg.reasoning,
        "source": msg.source.value,
        "meta": msg.meta,
    }
    if msg.parts:
        # WS1 additive:与 checkpoint._message_to_dict 同口径(parts 非空才落键)
        row["parts"] = [{"type": p.type, "mime": p.mime, "ref": p.ref} for p in msg.parts]
    return _jsonable(row)


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

    def __init__(
        self,
        config_path: str | Path,
        artifacts_root: Path,
        skillsets_dir: str | Path | None = None,
        supervisor_handler: Any = None,
    ) -> None:
        self._config_path = config_path
        self._artifacts_root = Path(artifacts_root)
        self._lock = threading.Lock()
        self._active: dict[str, dict[str, Any]] = {}
        self._hubs: dict[str, SignalHub] = {}
        self._skills_registry: Any = None  # 惰性装配的共享 registry(reload/查询用)
        self._tools_registry: Any = None  # 惰性装配的共享 tools registry(D4 查询用)
        #: S2 supervisor 通道(docs/SUPERVISOR.md §2.3):装配级注入的 handler(优先),
        #: 与进程共享的 InboxChannel(缺省——Web 收件箱即默认宿主通道)
        self._supervisor_handler = supervisor_handler
        self._inbox = InboxChannel()
        #: D6 一站多 set:{set 名: set 目录(resolve 后,sys.path/模块逐出比较一致)}
        self._skillsets_root = Path(skillsets_dir) if skillsets_dir is not None else None
        self._sets: dict[str, Path] = (
            {name: d.resolve() for name, d in load_skillsets(skillsets_dir).items()}
            if skillsets_dir is not None
            else {}
        )
        self._set_registries: dict[str, Any] = {}  # 每 set 惰性装配的共享 registry
        #: 串行化"钉 sys.path + 逐出模块 + build_kernel"(跨 set 装配/并发 run 防串模块)
        self._assemble_lock = threading.Lock()
        #: P3 调试会话(Agent OS Debugger):共享一个 DebugController(每 run 至多一个
        #: 活跃会话);逐会话记录 run worker 的事件循环,REST 侧命令经
        #: run_coroutine_threadsafe 投递(asyncio.Event 无跨线程亲和性)
        self._debug = DebugController()
        self._debug_loops: dict[str, asyncio.AbstractEventLoop] = {}
        #: E4 事件注入桥:run_id → run worker 事件循环(逐 run 首个内核信号时捕获,
        #: run 收尾摘除);``inject_event`` 经 run_coroutine_threadsafe 投递过去
        #: (与调试命令桥 ``_debug_loops`` 同构,但不依赖调试会话)
        self._run_loops: dict[str, asyncio.AbstractEventLoop] = {}

    def skillsets(self) -> dict[str, Path]:
        """全部 set(name → 目录,按名字序;``GET /api/skillsets`` 数据源,D6)。"""
        return dict(self._sets)

    def skillsets_root(self) -> Path | None:
        """set 根目录(docs/SKILL-PACKAGES.md §4.4:包级提交的目录形态落盘面;未配置 → None)。"""
        return self._skillsets_root

    def refresh_skillsets(self) -> None:
        """重扫 set 目录(P4:包级提交落新 set 后,``/api/skillsets`` 与下拉立即可见)。"""
        if self._skillsets_root is not None:
            self._sets = {
                name: d.resolve() for name, d in load_skillsets(self._skillsets_root).items()
            }

    def _set_dir(self, name: str) -> Path:
        """set 名 → 目录;未知 set 抛 :class:`RunValidationError`(路由层归 400)。"""
        try:
            return self._sets[name]
        except KeyError:
            raise RunValidationError(
                f"未知 skill set: {name!r}(可用: {sorted(self._sets) or '无'})"
            ) from None

    def _resolve_set(self, skill_set: str | None) -> str | None:
        """生效 set(D6):显式指定(校验存在)> 唯一 set 自动 > ``None``(全局,向后兼容)。"""
        if skill_set is not None:
            self._set_dir(skill_set)
            return skill_set
        if len(self._sets) == 1:
            return next(iter(self._sets))
        return None

    def _base_config(self, skill_set: str | None) -> str | Path | dict[str, Any]:
        """run 内核装配的 base config(config 路径或等价 dict,见 :func:`build_kernel`)。

        ``skill_set=None`` → 全局 config;否则(D6):先 ``_prepare_set_imports``
        (dotted path 不依赖 PYTHONPATH),再按模型组配置——set 目录有
        ``agent-os.toml`` 则与全局 config 深合并(set 级装配继承全局),无则用全局;
        ``[skills].path`` 取 set toml 显式声明(相对路径相对 set 目录解析),
        未声明恒指向 ``<set>/skills.yaml``(模型钉死:每 set 必有自己的 skills.yaml)。
        """
        if skill_set is None:
            global_dir = Path(self._config_path).resolve().parent
            _prepare_set_imports(global_dir, global_dir)  # 全局配置的 dotted path 同样免 PYTHONPATH
            return self._config_path
        set_dir = self._set_dir(skill_set)
        _prepare_set_imports(set_dir, Path(self._config_path).resolve().parent)
        set_toml = set_dir / "agent-os.toml"
        set_cfg = load_config(set_toml) if set_toml.is_file() else {}
        cfg = _deep_merge(load_config(self._config_path), set_cfg)
        declared = (set_cfg.get("skills") or {}).get("path")
        skills = dict(cfg.get("skills") or {})
        skills["path"] = str(set_dir / declared) if declared else str(set_dir / "skills.yaml")
        cfg["skills"] = skills
        return cfg

    def _assemble_kernel(
        self,
        overrides: dict[str, Any] | None = None,
        skill_set: str | None = None,
        supervisor_handler: Any = None,
    ) -> Any:
        """按 config 装配一个 run 的内核;恒附带 _StopBridge 保证 ctl 存在(stop 通道)。

        ``overrides``(D3,docs/WEB-UI.md §4.3):``{model?, max_cost?, max_steps?, inline?}``
        合并进本次 run 的 ``[run]`` 配置。配置文件每次重新读取,改动只落在本 run 私有的
        dict 副本上——只影响本次 run,不泄漏到后续 run 或共享 registry(D3 锚点)。

        ``skill_set``(D6):用该 set 的装配(``_base_config``);全程持
        ``_assemble_lock``,与并发 run/其它 set 的装配串行化(sys.path 是进程全局)。

        ``supervisor_handler``(S2):run 级注入的 supervisor 通道;缺省回落
        装配级 handler,再缺省回落进程共享 InboxChannel(§2.3 选择顺序)。

        M1(§8.3):每个内核 post-build 经 ``bind_user_channel`` 接 Web 宿主
        用户通道(:class:`_InboxUserChannel`;ask 复用收件箱;notify 的
        可观测面在工具层 ``user.notify`` 信号,回调 no-op)。
        """
        handler = supervisor_handler
        if handler is None:
            handler = self._supervisor_handler
        if handler is None:
            handler = self._inbox
        with self._assemble_lock:
            base = self._base_config(skill_set)
            if not overrides:
                kernel = build_kernel(
                    base, extra_sidecars=[_StopBridge()], supervisor_handler=handler
                )
            else:
                cfg = load_config(base) if isinstance(base, (str, Path)) else dict(base)
                run_section = dict(cfg.get("run") or {})
                for key in OVERRIDE_FIELDS:
                    if key in overrides and overrides[key] is not None:
                        run_section[key] = overrides[key]
                cfg["run"] = run_section
                kernel = build_kernel(
                    cfg, extra_sidecars=[_StopBridge()], supervisor_handler=handler
                )
        # M1(§8.3):Web 宿主用户通道——ask 复用收件箱(kind="user-ask");
        # notify 的可观测面在工具层 user.notify 信号(trace/SSE),通道回调 no-op
        # (形态定案见 _InboxUserChannel docstring)
        if hasattr(kernel.tools, "bind_user_channel"):
            kernel.tools.bind_user_channel(_InboxUserChannel(self._inbox))
        return kernel

    def _principal(self) -> Any:
        """数据层身份(docs/DATA-AUTHZ.md §2.2):Web 单用户模式 = 部署者。

        登录名取宿主配置 ``[web].user``(缺省本机用户);配置读取失败不拖垮 run
        (退化为本机用户)。多用户会话映射(api-token / 逐会话身份)属 D3。
        """
        try:
            login = (load_config(self._config_path).get("web") or {}).get("user")
        except Exception:  # noqa: BLE001 — 身份构造失败退化为缺省,不阻断 run
            login = None
        return web_single_user_principal(login)

    def principal(self) -> Any:
        """本宿主的单用户 principal(docs/SKILL-DEV.md §1.2:promote 记录的 promoted_by)。"""
        return self._principal()

    def token_principal(self, token: str) -> Any:
        """D3-lite 多用户映射(docs/DATA-AUTHZ.md §2.2):``[web.tokens]`` token → subject。

        命中 → 该 subject 的 Principal(issuer=``api-token``;clearance 同单用户
        confidential——token 即认证凭证,逐域收束由 ``[data.principals]`` 白名单表达);
        未命中 → None(调用方回落单用户语义)。配置每次现读,token 表改动即生效,
        与 ``_assemble_kernel`` 每 run 重读同旨;读取/解析失败不拖垮请求
        (退化为无映射,同 :meth:`_principal` 先例)。
        """
        if not token:
            return None
        try:
            table = web_token_map(load_config(self._config_path))
        except Exception:  # noqa: BLE001 — 映射解析失败退化为无映射,不阻断请求
            return None
        subject = table.get(token)
        if subject is None:
            return None
        return Principal(subject=subject, issuer="api-token", attrs={"clearance": CONFIDENTIAL})

    def has_token_map(self) -> bool:
        """装配期一探:``[web.tokens]`` 是否配置(决定 app 是否装 Bearer 映射中间件)。"""
        try:
            return bool(web_token_map(load_config(self._config_path)))
        except Exception:  # noqa: BLE001 — 同上:失败按无映射,不阻断装配
            return False

    def events_section(self) -> EventsSection:
        """``[events]`` 段(E4 事件批处理三键):inject_event 分流与事件文本截断的配置源。

        配置每次现读(同 ``token_principal`` 先例,改动即生效);读取/解析失败退化
        全默认(``batch=True``),不阻断事件路由——段畸形时 run 装配早已
        ConfigError 快速失败,这里只覆盖"run 启动后配置被改坏"的窗口。
        """
        try:
            return _events_section(load_config(self._config_path).get("events") or {})
        except Exception:  # noqa: BLE001 — 同 token_principal 先例:失败退化默认,不阻断请求
            return EventsSection()

    def assemble_lab_kernel(self, overlay: Any) -> Any:
        """装配"生产 + 草稿层"内核(docs/SKILL-DEV.md §1.1;L3):Lab test-run/G4 专用。

        **生产 run 不受影响**——overlay 只活在返回的这个内核里(Lab 请求作用域,§1.1)。
        """
        kernel = self._assemble_kernel()
        self.swap_skills_overlay(kernel, overlay)
        return kernel

    @staticmethod
    def swap_skills_overlay(kernel: Any, overlay: Any) -> None:
        """把 overlay 接进内核的全部 skills 引用点(kernel.skills / ContextManager /
        tools.bind_skills 的 skill_search 数据源);start_run 的 kernel_patcher 同款接线。
        """
        kernel.skills = overlay
        if getattr(kernel.context, "_skills", None) is not None:
            kernel.context._skills = overlay
        if getattr(kernel.tools, "_skills", None) is not None:
            kernel.tools._skills = overlay

    async def start_run(
        self,
        skill: str,
        input: dict[str, Any],
        wait: bool = False,
        overrides: dict[str, Any] | None = None,
        skill_set: str | None = None,
        supervisor_handler: Any = None,
        principal: Any = None,
        debug_session: Any = None,
        replay_script: Any = None,
        kernel_patcher: Any = None,
    ) -> str:
        """启动一个 run 并返回 run_id;``wait=True`` 时阻塞到 run 结束。

        run_id 从 ``run.started`` 信号捕获:无论 wait 与否都先等到 run 真正开始
        (或开始即失败)再返回;run 未开始的校验错抛 :class:`RunValidationError`。
        ``overrides`` 见 :meth:`_assemble_kernel`(D3,只影响本次 run)。
        ``skill_set``(D6):用该 set 的装配;缺省见 :meth:`_resolve_set`;run 记录
        (内存态 + meta.json/result.json)带 ``skill_set``(全局 run 记 ``"default"``)。
        ``supervisor_handler``(S2):run 级注入的 supervisor 通道(§2.3 选择顺序
        第一级;REST 层传不了可调用,供嵌入方程序化调用),缺省见 :meth:`_assemble_kernel`。
        ``principal``(D3-lite,docs/DATA-AUTHZ.md §2.2):请求级身份(Bearer 映射的
        多用户 principal);缺省 None → :meth:`_principal` 单用户部署者,行为与引入前一致。
        ``debug_session``(P3):给了就把共享 DebugController 挂到本 run 内核的
        总线上(订阅序:hub ``_fan`` 之后、``_cap`` 之前——暂停前信号已落
        hub/trace,且本方法返回时会话已绑定 run)。
        ``replay_script``(P5 时间旅行):给了就 ``replace_providers`` 把内核
        provider 面换成回放脚本(LLM Mock 回放,工具真实重跑;replay.py 边界)。
        ``kernel_patcher``(docs/SKILL-DEV.md §1.1;L3):装配后调用 ``kernel_patcher(kernel)``
        (Lab 用它在生产内核上接 overlay / 替换 provider),run 管理/SSE/记录全复用。
        """
        effective = self._resolve_set(skill_set)
        tag = effective or "default"
        hub = SignalHub()
        started = threading.Event()
        done = threading.Event()
        holder: dict[str, str] = {}
        state: dict[str, Any] = {
            "task": None,
            "status": "running",
            "record": None,
            "skill": skill,
            "skill_set": tag,
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
                kernel = self._assemble_kernel(
                    overrides, skill_set=effective, supervisor_handler=supervisor_handler
                )
                if replay_script is not None:
                    # P5 时间旅行:回放内核——provider 面换成 Mock 脚本(§3.4 边界:
                    # LLM 按 trace 重放,工具副作用真实重跑)
                    replace_providers(kernel, replay_script)
                if kernel_patcher is not None:
                    # Lab(docs/SKILL-DEV.md §1.1;L3):overlay 接线等请求作用域改造
                    kernel_patcher(kernel)
                state["kernel"] = kernel

                async def _fan(sig: Signal) -> None:
                    hub.publish(signal_row(sig))

                async def _cap(sig: Signal) -> None:
                    _register(sig.run_id)

                async def _cap_run_loop(sig: Signal) -> None:
                    # E4 事件注入桥:worker 事件循环句柄(REST 侧跨线程投递用);
                    # 订 "*" 而非 RUN_STARTED——resume 路径不补发 run.started
                    self._run_loops[sig.run_id] = asyncio.get_running_loop()

                kernel.signals.subscribe("*", _fan)
                kernel.signals.subscribe("*", _cap_run_loop)
                if debug_session is not None:
                    # P3:调试控制器在 hub 之后订阅(暂停前信号已落 hub/trace),
                    # 在 _cap 之前订阅(start_run 返回时会话已绑定 run)
                    self._debug.attach(kernel.signals, kernel.ctl)

                    async def _cap_loop(sig: Signal) -> None:
                        # 调试命令桥:worker 事件循环句柄(REST 侧跨线程投递用)
                        self._debug_loops[debug_session.id] = asyncio.get_running_loop()

                    kernel.signals.subscribe(RUN_STARTED, _cap_loop)
                kernel.signals.subscribe(RUN_STARTED, _cap)
                record = execute_run(
                    kernel, skill, input, artifacts_root=self._artifacts_root, host="web",
                    # 数据层身份(docs/DATA-AUTHZ.md §2.2):D3-lite 请求级身份优先,
                    # 缺省回落单用户部署者(行为与引入前逐字一致)
                    principal=principal or self._principal(),
                )
                record["skill_set"] = tag
                self._tag_artifacts(record["run_id"], tag)
                state["status"] = record["status"]
                state["record"] = record
            except Exception as e:  # noqa: BLE001 — 与 execute_run 同旨:校验/装配错归 RunRecord,不炸宿主(§3.3)
                state["status"] = STATUS_FAILED
                state["error"] = f"{type(e).__name__}: {e}"
            finally:
                self._run_loops.pop(holder.get("run_id") or "", None)  # E4:循环随 run 收尾摘除
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

    def _tag_artifacts(self, run_id: str, tag: str) -> None:
        """把 ``skill_set`` 写进产物 meta.json/result.json(D6:列表/详情与冷数据重建)。"""
        run_dir = self._artifacts_root / "runs" / run_id
        for name in ("meta.json", "result.json"):
            path = run_dir / name
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # 产物落盘半写窗口:跳过,内存态仍带 tag
            doc["skill_set"] = tag
            path.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2, default=repr), encoding="utf-8"
            )

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

    async def pause_run(self, run_id: str, reason: str = "web pause") -> bool:
        """``POST pause``(WS2 pause 真语义):``RunControl.pause`` 置挂起标志,run 在
        下一个 safe point 抛 ``RunPaused`` 挂起(落 PAUSED + checkpoint,可 resume)。

        语义/形状同 :meth:`stop_run`:仅内存态 running 才置标志,否则 → ``False``
        (路由层归 409);``ctl.pause`` 只写挂起标志表,跨线程 await 无事件循环
        亲和性问题。reason 缺省 ``"web pause"``,原样进 ``run.paused`` payload 与
        RunRecord error(``"RunPaused: <reason>"``)。
        """
        state = self.state_of(run_id)
        if state is None or state.get("status") != "running":
            return False
        ctl = getattr(state.get("kernel"), "ctl", None)
        if ctl is None:
            return False
        await ctl.pause(run_id, reason)
        return True

    async def resume_run(self, run_id: str, inject: list[str] | None = None) -> dict[str, Any]:
        """``POST resume``(§4.3):从该 run 产物目录的 checkpoint.json 恢复 run。

        用同一 config 新建内核,经 ``execute_resume`` 恢复;产物(result/checkpoint)
        与内存态写回原 run。阻塞到恢复结束,返回 RunRecord dict。checkpoint 缺失
        抛 ``FileNotFoundError``(路由层归 404),畸形抛 ``ValueError``(归 400),
        在途 run 抛 :class:`ResumeConflictError`(归 409)。

        ``inject``(E4,§17 开放问题 4 的 paused 唤醒通道):在场时先向
        checkpoint.json **根帧**(depth 最小)的 ``context.messages`` 追加事件
        消息再写回,随后走原 execute_resume 路径——恢复出的根帧带着事件进
        loop。文件坏/无根帧抛 :class:`ResumeConflictError`(归 409:事件投递
        失败按目标状态冲突处理,调用方可重试)。
        """
        run_dir = self._artifacts_root / "runs" / run_id
        checkpoint_path = run_dir / "checkpoint.json"
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"找不到 checkpoint: {run_id}")
        state = self.state_of(run_id)
        if state is not None and state.get("status") == "running":
            raise ResumeConflictError(f"run {run_id} 仍在进行,不能 resume")
        if inject:
            self._inject_into_checkpoint(checkpoint_path, inject)
        hub = SignalHub()  # 原 hub 已随首次 run 关闭;resume 期间换新,SSE 可继续观察
        with self._lock:
            self._hubs[run_id] = hub
            if state is None:
                state = self._cold_state(run_dir)
                self._active[run_id] = state
            state["status"] = "running"
        # D6:resume 沿用原 run 的 set 装配("default"/缺失 → 全局 config)
        tag = state.get("skill_set")
        effective = tag if tag and tag != "default" else None

        def _work() -> None:
            try:
                kernel = self._assemble_kernel(skill_set=effective)
                state["kernel"] = kernel

                async def _fan(sig: Signal) -> None:
                    hub.publish(signal_row(sig))

                async def _cap_run_loop(sig: Signal) -> None:
                    # E4 事件注入桥:worker 循环句柄(resume 不补发 run.started,订 "*")
                    self._run_loops[sig.run_id] = asyncio.get_running_loop()

                kernel.signals.subscribe("*", _fan)
                kernel.signals.subscribe("*", _cap_run_loop)
                record = execute_resume(
                    kernel, checkpoint_path, artifacts_root=self._artifacts_root, host="web"
                )
                state["status"] = record["status"]
                state["record"] = record
            except Exception:
                state["status"] = STATUS_FAILED
                raise
            finally:
                self._run_loops.pop(run_id, None)  # E4:循环随 run 收尾摘除
                hub.close()

        await asyncio.to_thread(_work)
        return state["record"]

    @staticmethod
    def _inject_into_checkpoint(checkpoint_path: Path, inject: list[str]) -> None:
        """向 checkpoint.json 根帧(depth 最小)的 ``context.messages`` 追加事件消息并写回(E4)。

        消息 dict 逐字对齐内核 ``_message_to_dict`` 的落盘形状(role/source 落
        枚举值、空 parts 不落键);``meta.event.type`` 从事件文本的
        ``[event:<type>]`` 前缀解析(见 :func:`_event_type_of`)。文件不可读
        (JSON 坏)/无帧/根帧缺 ``context.messages`` → :class:`ResumeConflictError`
        (409:事件投递失败,与在途冲突同族,调用方可重试)。
        """
        try:
            doc = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ResumeConflictError(f"checkpoint 不可读,事件无法投递: {e}") from e
        frames = doc.get("frames") or []
        root = min(frames, key=lambda f: f.get("depth", 0), default=None)
        messages = ((root or {}).get("context") or {}).get("messages")
        if not isinstance(messages, list):
            raise ResumeConflictError("checkpoint 无根帧消息列,事件无法投递")
        for text in inject:
            messages.append(
                {
                    "role": Role.USER.value,
                    "content": text,
                    "tool_calls": [],
                    "tool_call_id": None,
                    "name": None,
                    "reasoning": None,
                    "source": Source.INJECTED.value,
                    "meta": {"event": {"type": _event_type_of(text)}},
                }
            )
        checkpoint_path.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2, default=repr), encoding="utf-8"
        )

    async def inject_event(self, run_id: str, text: str, event_type: str = "unknown") -> str | None:
        """``POST /api/events`` 的在跑注入通道(E4):按 ``[events].batch`` 分两路。

        - 批处理开(缺省):事件条目 ``{type, text, at}`` 入根帧工作内存队列
          ``_event_queue``,runner 在下一步 build 前排干为一条批头消息
          (``[event 批处理 N 条]``;只在 build 前并入,保 §7.4 配对原子性),
          返回 ``"queued"``;队列随 checkpoint 落盘(working 序列化),
          resume 零钩子自然排干;
        - 批处理关:维持 E4 原行为,立即向根帧追加 USER/INJECTED 消息,
          返回 ``"injected"``。

        根帧 = ``ctl.get_frame_tree`` 中 depth 最小者;查帧树与入队/追加经
        ``run_coroutine_threadsafe`` 桥到 run worker 的事件循环内完成(与调试
        inject 同桥,``_run_loops`` 在首个内核信号时捕获)——不与 runner 的
        消息组装竞态。run 不在跑 / 无 ctl / worker 循环句柄不在 / 帧树为空
        → ``None``(路由层归 409)。
        """
        state = self.state_of(run_id)
        if state is None or state.get("status") != "running":
            return None
        ctl = getattr(state.get("kernel"), "ctl", None)
        if ctl is None:
            return None
        loop = self._run_loops.get(run_id)
        if loop is None or not loop.is_running():
            return None
        batch = self.events_section().batch

        async def _inject() -> str | None:
            frame_id = _root_frame_id(await ctl.get_frame_tree(run_id))
            if frame_id is None:
                return None
            if batch:
                # 入队只写根帧 working(纯内存操作,桥内与 runner 同循环,无竞态);
                # 批头上限/丢弃在 runner 排干点统一处理([events] batch_max)
                frame = ctl._kernel.stack.get(frame_id)
                if frame is None:
                    return None
                frame.context.working.setdefault(EVENT_QUEUE_KEY, []).append(
                    {"type": event_type, "text": text, "at": time.time()}
                )
                return "queued"
            await ctl.inject_message(
                frame_id,
                Message(role=Role.USER, content=text, source=Source.INJECTED),
            )
            return "injected"

        try:
            fut = asyncio.run_coroutine_threadsafe(_inject(), loop)
        except RuntimeError:
            # 竞态:is_running 检查与投递之间 loop 已关闭(run 刚结束)——按无法注入归类
            return None
        return await asyncio.wrap_future(fut)

    def emit_event(self, run_id: str, payload: dict[str, Any]) -> None:
        """向 run 的 hub 投一条宿主层 ``event.received`` 行(E4 路由可观测面)。

        仅经 per-run hub 扇出(SSE 实时/回放可见;hub 已关闭仍入缓冲,容忍
        resume/wait 路径的迟到投递);刻意**不进 api/v1** 信号枚举、不写
        trace.jsonl——事件路由是宿主层形态,内核零改动。hub 不存在(未知
        run)静默丢弃。
        """
        hub = self.hub_of(run_id)
        if hub is None:
            return
        hub.publish(
            {
                "type": "signal",
                "name": EVENT_RECEIVED,
                "run_id": run_id,
                "frame_id": None,
                "ts": time.time(),
                "payload": _jsonable(payload),
            }
        )

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
            "skill_set": meta.get("skill_set") or "default",
            "error": None,
            "started_at": meta.get("started_at") or datetime.now(UTC).isoformat(),
            "kernel": None,
        }

    # ------------------------------------------------------------------
    # P3 调试会话(Agent OS Debugger;tests/web/test_debug_api.py 锚点)
    # ------------------------------------------------------------------

    async def start_debug_session(
        self,
        skill: str,
        input: dict[str, Any],
        skill_set: str | None = None,
        breakpoints: list[tuple[str, str]] | None = None,
    ) -> tuple[str, str]:
        """``POST /api/debug/sessions``:开调试会话并以其装配起 run,返回 ``(session_id, run_id)``。

        每 run 至多一个活跃会话:已有未结束(state != detached)的会话 →
        :class:`DebugConflictError`(路由层归 409)。``breakpoints``(kind, match)
        在 run 启动**之前**注册到待绑定会话——调试启动即断是确定性的(mock brain
        的 run 毫秒级推进,起 run 后再加断点会竞态错过早期信号);未知 kind →
        ``ValueError``(路由层归 400)。run 未开始的校验错照常抛
        :class:`RunValidationError`,并收回待绑定会话(不留 armed 残渣)。
        """
        if any(s.state != "detached" for s in self._debug.sessions):
            raise DebugConflictError(
                "已有活跃调试会话(每 run 至多一个;先 stop 或 DELETE 结束当前会话)"
            )
        session = self._debug.open_session()
        try:
            for kind, match in breakpoints or ():
                session.add_breakpoint(kind, match)  # ValueError(未知 kind)向外抛
            run_id = await self.start_run(
                skill, input, skill_set=skill_set, debug_session=session
            )
        except Exception:
            # close_session 已根治待绑定指针(P1):会话未绑定 run 就被收回时
            # _armed 一并摘除,后续 open_session 不受残留影响
            self._debug.close_session(session.id)
            self._debug_loops.pop(session.id, None)
            raise
        # rerun 依据:同参数重开(replay 形态无 origin——回放参数在产物 meta,
        # 语义上应走 replay 端点而非 live 重跑)
        session.origin = {
            "skill": skill,
            "input": input,
            "skill_set": skill_set,
            "breakpoints": [{"kind": k, "match": m} for k, m in breakpoints or ()],
        }
        return session.id, run_id

    async def rerun_debug_session(self, session_id: str) -> tuple[str, str]:
        """``POST .../rerun``:以创建参数(skill/input/启动断点)重开新会话。

        旧会话仍活跃时先收尾:paused → ``stop`` 命令(走正常中止路径,
        checkpoint 落盘);running → ``stop_run`` 置中止标志;随后 detach +
        清注册表(同 DELETE)。无 origin(replay/CLI 会话)→ ``ValueError``
        (路由层归 400);其余错误语义同 :meth:`start_debug_session`。
        """
        session = self.debug_session(session_id)  # KeyError → 404
        origin = session.origin
        if origin is None:
            raise ValueError("该会话没有创建参数(replay/CLI 会话),不支持 rerun")
        if session.state != "detached":
            if session.state == "paused":
                await self._in_debug_loop(session, lambda: session.resume("stop"))
                # 等 stop verdict 落地(run 中止 → controller 自动 detach)再收尾:
                # 立即 detach 会让 _resume_verdict 先见 DETACHED 吞掉 stop,
                # 旧 run 变成跑完而不是中止(时序竞争)
                deadline = time.monotonic() + _RERUN_STOP_TIMEOUT
                while session.state != "detached" and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
            elif session.run_id:
                await self.stop_run(session.run_id)  # best-effort:已结束则 False
            if session.state != "detached":
                await self._in_debug_loop(session, session.detach)
            self._debug.close_session(session.id)
            self._debug_loops.pop(session.id, None)
        return await self.start_debug_session(
            origin["skill"],
            origin["input"],
            skill_set=origin.get("skill_set"),
            breakpoints=[(bp["kind"], bp["match"]) for bp in origin.get("breakpoints", [])],
        )

    async def start_debug_replay_session(
        self,
        replay_run_id: str,
        until_step: int | None = None,
        breakpoints: list[tuple[str, str]] | None = None,
        skill_set: str | None = None,
    ) -> tuple[str, str]:
        """``POST /api/debug/sessions`` 的 replay 形态(P5 时间旅行):回放指定 run
        并挂调试会话,返回 ``(session_id, run_id)``。

        从产物目录读 meta/trace/checkpoint,``build_mock_script`` 重建 Mock 脚本
        (回放边界:LLM Mock 回放 + 工具真实重跑,replay.py 模块 docstring);
        skill/input 取 meta.json。``until_step`` 注册一次性步数断点
        (``Breakpoint.until``:run 直达第 N 条 ``pre:step`` 才暂停)。
        产物缺失 → ``FileNotFoundError``(路由 404);产物畸形/未知断点
        kind/非法 until_step → ``ValueError``(400);会话冲突同 live(409)。
        """
        if any(s.state != "detached" for s in self._debug.sessions):
            raise DebugConflictError(
                "已有活跃调试会话(每 run 至多一个;先 stop 或 DELETE 结束当前会话)"
            )
        run_dir = self._artifacts_root / "runs" / replay_run_id
        for name in ("meta.json", "trace.jsonl", "checkpoint.json"):
            if not (run_dir / name).is_file():
                raise FileNotFoundError(f"找不到 run 产物目录: {run_dir}")
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        script = build_mock_script(run_dir)  # ValueError(产物畸形)向外抛
        session = self._debug.open_session()
        try:
            for kind, match in breakpoints or ():
                session.add_breakpoint(kind, match)  # ValueError(未知 kind)向外抛
            if until_step is not None:
                session.add_breakpoint("step", until=until_step)
            run_id = await self.start_run(
                meta.get("skill"),
                meta.get("input") or {},
                skill_set=skill_set,
                debug_session=session,
                replay_script=script,
            )
        except Exception:
            self._debug.close_session(session.id)
            self._debug_loops.pop(session.id, None)
            raise
        return session.id, run_id

    def debug_session(self, session_id: str) -> Any:
        """按 id 取会话;不存在 → ``KeyError``(路由层归 404)。"""
        session = self._debug.get(session_id)
        if session is None:
            raise KeyError(f"找不到调试会话: {session_id}")
        return session

    def debug_add_breakpoint(self, session_id: str, kind: str, match: str = "*") -> Any:
        """加断点;会话已结束 → :class:`AgentOSError`(409),未知 kind → ``ValueError``(400)。"""
        session = self.debug_session(session_id)
        if session.state == "detached":
            raise AgentOSError(f"调试会话 {session_id} 已结束,不能加断点")
        return session.add_breakpoint(kind, match)

    def debug_remove_breakpoint(self, session_id: str, bp_id: str) -> None:
        """删断点;断点不存在 → ``KeyError``(路由层归 404)。"""
        session = self.debug_session(session_id)
        if not session.remove_breakpoint(bp_id):
            raise KeyError(f"找不到断点: {bp_id}")

    async def _in_debug_loop(self, session: Any, fn: Any) -> None:
        """把调试命令投递到 run worker 的事件循环执行并等它落地(跨线程命令桥)。

        ``DebugSession`` 的 ``asyncio.Event`` 绑在 worker 循环上,REST 线程直接
        ``set()`` 会在 ``loop.call_soon`` 的线程检查处炸掉;经
        ``run_coroutine_threadsafe`` 在 worker 循环内执行,异常原样带回。
        worker 循环已不在(run 结束)时直接调用:此时没有阻塞中的 wait,
        ``Event.set()`` 无 waiter 是纯内存操作,安全。
        """
        loop = self._debug_loops.get(session.id)
        if loop is None or not loop.is_running():
            fn()
            return

        async def _wrap() -> None:
            fn()

        try:
            fut = asyncio.run_coroutine_threadsafe(_wrap(), loop)
        except RuntimeError:
            # 竞态:``is_running`` 检查与投递之间 loop 已关闭(run 刚结束)——
            # 此时没有阻塞中的 wait,直接调用是纯内存操作,安全
            fn()
            return
        await asyncio.wrap_future(fut)

    async def debug_command(self, session_id: str, command: str) -> None:
        """恢复命令(仅 paused):continue/三种单步/stop;状态不对 → :class:`AgentOSError`(409)。"""
        session = self.debug_session(session_id)
        if command not in DEBUG_RESUME_COMMANDS:
            raise ValueError(f"未知恢复命令: {command!r}(可选 {DEBUG_RESUME_COMMANDS})")
        if session.state != "paused":
            raise AgentOSError(f"调试会话 {session_id} 不在 paused 状态,无法 resume")
        await self._in_debug_loop(session, lambda: session.resume(command))

    async def debug_pause(self, session_id: str) -> None:
        """随时暂停(仅 running;GDB SIGINT 语义):run 在下一个可仲裁信号挂起。"""
        session = self.debug_session(session_id)
        await self._in_debug_loop(session, session.pause)  # 非 running → AgentOSError(409)

    async def debug_modify(self, session_id: str, patch: dict[str, Any]) -> None:
        """改本次工具调用参数(仅暂停在 pre:tool.call 时有效;改完即放行)。"""
        session = self.debug_session(session_id)
        if (
            session.state != "paused"
            or not session.pause_point
            or session.pause_point["signal"] != PRE_TOOL_CALL
        ):
            raise AgentOSError("modify 仅在暂停于 pre:tool.call 时有效")
        await self._in_debug_loop(session, lambda: session.modify_tool_args(dict(patch)))

    async def debug_inject(
        self, session_id: str, frame_id: str | None, text: str
    ) -> str:
        """向指定帧注入 USER/INJECTED 消息(``frame_id=None`` 缺省=暂停帧;注入后放行)。

        返回实际注入的 frame_id。
        """
        session = self.debug_session(session_id)
        if session.state != "paused":
            raise AgentOSError(f"调试会话 {session_id} 不在 paused 状态,无法 inject")
        fid = frame_id or (session.pause_point or {}).get("frame_id")
        if fid is None:
            raise AgentOSError("无暂停帧,inject 需要显式 frame_id")
        loop = self._debug_loops.get(session.id)
        if loop is None or not loop.is_running():
            raise AgentOSError("run worker 已结束,无法 inject")
        # inject_message 是协程(经 ctl 落地 + 放行):直接在 worker 循环跑并等结果
        await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(session.inject_message(fid, text), loop)
        )
        return fid

    async def debug_close(self, session_id: str) -> None:
        """``DELETE`` 会话:detach 放行(run 继续跑完),摘除注册表与循环句柄。"""
        session = self.debug_session(session_id)
        if session.state != "detached":
            await self._in_debug_loop(session, session.detach)
        self._debug.close_session(session_id)  # detach 幂等;pop 注册表记录
        self._debug_loops.pop(session_id, None)

    def debug_frame(self, session_id: str, frame_id: str) -> dict[str, Any] | None:
        """live 帧检视(kernel.stack 内存态;调试暂停时 checkpoint 尚未落盘)。

        帧树全量登记(含已结束帧),run 结束后仍可读;找不到 → ``None``(路由层归 404)。
        """
        session = self.debug_session(session_id)
        state = self.state_of(session.run_id) if session.run_id else None
        kernel = (state or {}).get("kernel")
        frame = kernel.stack.get(frame_id) if kernel is not None else None
        if frame is None:
            return None
        return {
            "frame_id": frame.frame_id,
            "skill": str(frame.skill),
            "status": frame.status.value,
            "usage": dataclasses.asdict(frame.usage),
            "input": _jsonable(frame.input),
            "result": _jsonable(frame.result),
            "error": _jsonable(frame.error),
            # 帧上下文逐条:"模型那一步看到了什么"(与 checkpoint 行同形)
            "messages": [message_row(m) for m in frame.context.messages],
            "working": _jsonable(frame.context.working),
        }

    # ------------------------------------------------------------------
    # S2 supervisor 收件箱(docs/SUPERVISOR.md §2.3/§5):Web 收件箱即默认宿主通道
    # ------------------------------------------------------------------

    def supervisor_pending(self) -> list[dict[str, Any]]:
        """``GET /api/supervisor/pending`` 数据源:默认通道(InboxChannel)的挂起列表。"""
        return self._inbox.pending()

    def supervisor_answer(self, question_id: str, answer: str) -> None:
        """``POST /api/supervisor/{question_id}/answer``:校验 options 后结算收件箱。

        找不到 question_id → ``KeyError``(路由归 404);answer 不在 options 内 →
        ``ValueError``(路由归 400,问题保持挂起——§3:格式错误返回调用方重答,
        不重问子帧)。结算时 ``decided_by`` 标 ``"host:web-ui"``(§2.1 通道标注)。
        """
        question = self._inbox.get(question_id)
        if question is None:
            raise KeyError(f"找不到 supervisor 问题: {question_id}")
        if question.options is not None and answer not in question.options:
            raise ValueError(
                f"答案 {answer!r} 不在 options {question.options} 内,请从 options 中选择作答"
            )
        if not self._inbox.answer(
            question_id, {"answer": answer, "decided_by": "host:web-ui"}
        ):
            # 竞态:get 之后问题刚好超时/终结被摘除,按找不到归类
            raise KeyError(f"找不到 supervisor 问题: {question_id}")

    def _shared_registry(self, skill_set: str | None = None) -> Any:
        """惰性装配共享 skills registry(reload/只读查询用)。

        ``skill_set``(D6)指定 set 的装配(每 set 各自缓存);``None`` 用全局配置。
        配置未装配 skills → :class:`RunValidationError`。每 run 的内核仍各自重建
        registry(§6.1),本 registry 仅供 ``GET /api/skills`` 与 reload。
        """
        if skill_set is not None:
            self._set_dir(skill_set)  # 未知 set → RunValidationError(路由层归 400)
            registry = self._set_registries.get(skill_set)
            if registry is None:
                registry = self._assemble_kernel(skill_set=skill_set).skills
                if registry is None:  # [skills].path 恒被 set 装配钉上,此处纯防御
                    raise RunValidationError(f"set {skill_set!r} 未装配 skills registry")
                self._set_registries[skill_set] = registry
            return registry
        registry = self._skills_registry
        if registry is None:
            registry = self._assemble_kernel().skills
            if registry is None:
                raise RunValidationError("配置未装配 skills registry(缺 [skills].path)")
            self._skills_registry = registry
        return registry

    def reload_skills(self, skill_set: str | None = None) -> bool:
        """``POST /api/skills/reload``(§4.3):共享 registry 的热重载(mtime 检查,§6.1)。

        ``skill_set``(D6)限定重载哪个 set;缺省重载全部——有 sets 时重载所有
        **已装配**的共享 registry(全局 + 各 set,未装配的不值得装配了再重载),
        无 sets 时维持原行为(全局,未装配则装配)。返回是否真重载。reload 只影响
        后续新建的 run(每 run 独立内核、独立 registry),在跑的 run 钉住旧版。
        """
        if skill_set is not None:
            return bool(self._shared_registry(skill_set).reload())
        if self._sets:
            reloaded = False
            if self._skills_registry is not None:
                reloaded = bool(self._skills_registry.reload()) or reloaded
            for registry in self._set_registries.values():
                reloaded = bool(registry.reload()) or reloaded
            return reloaded
        return bool(self._shared_registry().reload())

    def skills_manifests(self, skill_set: str | None = None) -> list[Any]:
        """``GET /api/skills``(docs/WEB-UI.md §6.2):共享 registry 的 manifest 列表(拓扑序)。

        ``skill_set``(D6)限定某个 set 的 registry(``?skill_set=`` 过滤)。
        """
        return list(self._shared_registry(skill_set).manifests())

    def skill_manifest(self, name: str, skill_set: str | None = None) -> Any | None:
        """按名字取 manifest;不存在返回 ``None``(路由层归 404)。"""
        return next((m for m in self.skills_manifests(skill_set) if m.name == name), None)

    def shared_skills_registry(self) -> Any:
        """共享 skills registry(docs/SKILL-DEV.md §1.1:Lab 推导档的生产层数据源)。"""
        return self._shared_registry()

    def shared_tools_registry(self) -> Any:
        """共享 tools registry(docs/SKILL-DEV.md §1.1:Lab 推导档的工具档数据源)。"""
        return self._shared_tools()

    def _shared_tools(self) -> Any:
        """惰性装配共享 tools registry(``GET /api/tools`` 数据源;D4)。

        装配路径与 skills 共享 registry 相同(惰性 + 缓存),区别只在把
        ``[tools].builtins`` 视为 True:Tools 浏览器回答"宿主能装配哪些工具"
        (§4.7 注册表全量 ToolSpec),与单个 run 的 ``[tools]`` 开关无关。
        装配用查询专用内核:摘掉 skills/telemetry/sidecars,技能文件损坏或
        telemetry 目录异常不影响工具目录。
        """
        registry = self._tools_registry
        if registry is None:
            cfg = load_config(self._config_path)
            tools_cfg = dict(cfg.get("tools") or {})
            tools_cfg["builtins"] = True
            cfg["tools"] = tools_cfg
            for section in ("skills", "telemetry", "sidecars"):
                cfg.pop(section, None)
            registry = build_kernel(cfg).tools
            self._tools_registry = registry
        return registry

    def tools_specs(self) -> list[Any]:
        """``GET /api/tools``(docs/WEB-UI.md §6.2):共享 tools registry 的全量 ToolSpec(注册序)。"""
        return list(self._shared_tools().specs())

    def state_of(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._active.get(run_id)

    def hub_of(self, run_id: str) -> SignalHub | None:
        with self._lock:
            return self._hubs.get(run_id)

    def active_items(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            return list(self._active.items())
