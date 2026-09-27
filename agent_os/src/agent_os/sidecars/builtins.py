"""内置 sidecar(docs/DESIGN.md §5.4 表格逐字;BudgetGuard/LoopDetector/StallDetector/ToolGuard

为 M4,CodeScanner 为 M5;DistillSidecar 为 §11.2 "蒸馏 sidecar" 写路径范式)。
TraceRecorder 不在此列——已升格为 Telemetry 子系统的 JSONL exporter(§10)。
自定义 sidecar 经 entry point ``agent_os.sidecars`` 注册。

输入最小化原则(§5.2):以下 sidecar 一律 ``needs_free_text = False``,只消费
结构化信号载荷(usage / 调用签名 / 工具名与参数),不接收主模型自由文本。
DistillSidecar 的转写素材不来自信号载荷,而是经装配期 bind 的 ``stack`` 句柄
读帧树(与 RunControl.get_frame_tree 同一数据源),信号本身仍只带结构化字段。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import time
from typing import Any, ClassVar

from agent_os.api.v1 import (
    RUN_ABORTED,
    RUN_FINISHED,
    Allow,
    ChatRequest,
    MemoryEntry,
    Message,
    Mode,
    Provenance,
    Role,
    RunControl,
    Signal,
    SignalPattern,
    Source,
    Verdict,
    Veto,
)

_log = logging.getLogger(__name__)


class BudgetGuard:
    """§5.4:订阅 ``post:llm.response``;累计成本/步数/时长,超限 → ``ctl.stop()``

    (长任务宿主可配 stop→pause 降级,§2.4)。按 run_id 独立累计。"""

    name: ClassVar[str] = "budget_guard"
    subscriptions: ClassVar[list[SignalPattern]] = ["post:llm.response"]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    def __init__(
        self,
        max_cost: float | None = None,
        max_steps: int | None = None,
        max_wall_time: float | None = None,
    ) -> None:
        self.max_cost = max_cost
        self.max_steps = max_steps
        self.max_wall_time = max_wall_time
        self._cost: dict[str, float] = {}
        self._steps: dict[str, int] = {}
        self._started: dict[str, float] = {}
        self._stopped: set[str] = set()

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        run_id = sig.run_id
        if run_id in self._stopped:
            return Allow()
        now = time.monotonic()
        started = self._started.setdefault(run_id, now)
        usage = sig.payload.get("usage") or {}
        cost = self._cost.get(run_id, 0.0) + float(usage.get("cost") or 0.0)
        self._cost[run_id] = cost
        steps = self._steps.get(run_id, 0) + 1
        self._steps[run_id] = steps
        reason = None
        if self.max_cost is not None and cost > self.max_cost:
            reason = f"BudgetGuard: 成本 {cost:.4f} 超上限 {self.max_cost}"
        elif self.max_steps is not None and steps > self.max_steps:
            reason = f"BudgetGuard: 步数 {steps} 超上限 {self.max_steps}"
        elif self.max_wall_time is not None and now - started > self.max_wall_time:
            reason = f"BudgetGuard: 挂钟时长 {now - started:.1f}s 超上限 {self.max_wall_time}s"
        if reason is not None:
            self._stopped.add(run_id)
            await ctl.stop(run_id, reason)
        return Allow()


class LoopDetector:
    """§5.4:订阅 ``post:step``;重复调用模式(同签名连续 N 次)→ ``inject_message``

    带操作策略纠偏,再犯 ``max_strikes`` 次 → ``stop``(防死亡螺旋,§3.2)。
    按 frame_id 维护最近签名序列;签名变化则重置该帧计数。"""

    name: ClassVar[str] = "loop_detector"
    subscriptions: ClassVar[list[SignalPattern]] = ["post:step"]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    def __init__(self, threshold: int = 3, max_strikes: int = 2) -> None:
        self.threshold = threshold
        self.max_strikes = max_strikes
        # frame_id -> {"sig": 最近签名, "count": 连续重复数, "strikes": 纠偏后再犯数}
        self._frames: dict[str, dict[str, object]] = {}
        self._stopped: set[str] = set()

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        frame_id = sig.frame_id or ""
        if frame_id in self._stopped:
            return Allow()
        state = self._frames.setdefault(frame_id, {"sig": None, "count": 0, "strikes": 0})
        for call in sig.payload.get("calls") or []:
            call_sig = call.get("sig", "")
            if call_sig == state["sig"]:
                state["count"] = int(state["count"]) + 1
            else:
                state.update({"sig": call_sig, "count": 1, "strikes": 0})
            count = int(state["count"])
            if count == self.threshold:
                await ctl.inject_message(
                    frame_id,
                    Message(
                        role=Role.USER,
                        content=(
                            f"检测到同一调用已连续重复 {count} 次:"
                            f"停止重试,改用其他策略或宣告受阻。"
                        ),
                        source=Source.INJECTED,
                    ),
                )
            elif count > self.threshold:
                state["strikes"] = int(state["strikes"]) + 1
                if int(state["strikes"]) >= self.max_strikes:
                    self._stopped.add(frame_id)
                    await ctl.stop(sig.run_id, f"LoopDetector: 帧 {frame_id} 循环不止")
                    return Allow()
        return Allow()


class StallDetector:
    """§5.4:订阅 ``post:step``;帧级静默超时(相邻 step 间隔超 ``max_idle_seconds``)

    → inject 纠偏 → 再犯 → stop。``clock`` 可注入(测试用假钟)。"""

    name: ClassVar[str] = "stall_detector"
    subscriptions: ClassVar[list[SignalPattern]] = ["post:step"]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 100
    needs_free_text: ClassVar[bool] = False

    def __init__(self, max_idle_seconds: float = 600, clock=None) -> None:
        self.max_idle_seconds = max_idle_seconds
        self._clock = clock or time.monotonic
        self._last: dict[str, float] = {}
        self._warned: set[str] = set()
        self._stopped: set[str] = set()

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        frame_id = sig.frame_id or ""
        now = self._clock()
        last = self._last.get(frame_id)
        self._last[frame_id] = now
        if last is None or frame_id in self._stopped:
            return Allow()
        if now - last > self.max_idle_seconds:
            if frame_id in self._warned:
                self._stopped.add(frame_id)
                await ctl.stop(sig.run_id, f"StallDetector: 帧 {frame_id} 停滞")
            else:
                self._warned.add(frame_id)
                await ctl.inject_message(
                    frame_id,
                    Message(
                        role=Role.USER,
                        content="长时间无进展:请给出当前状态与下一步",
                        source=Source.INJECTED,
                    ),
                )
        return Allow()


class ToolGuard:
    """§5.4:订阅 ``pre:tool.call``;规则表(工具名/参数模式)→ ``Veto``。

    rules = ``list[(tool_name, arg_regex, reason)]``:``payload["tool"]`` 命中工具名
    且 ``arg_regex`` 在参数 JSON 中搜索命中 → ``Veto(reason)``,否则 ``Allow``。

    能力上限声明:正则/关键字对 shell 组合爆炸无效,``system.shell.exec`` 的防护主体是
    沙箱(§9.2)+ 权限(§8.2);语义解析器作为后续替换实现预留。
    """

    name: ClassVar[str] = "tool_guard"
    subscriptions: ClassVar[list[SignalPattern]] = ["pre:tool.call"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    def __init__(self, rules: list[tuple[str, str, str]] | None = None) -> None:
        self.rules = list(rules or [])

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        tool = sig.payload.get("tool", "")
        args = json.dumps(sig.payload.get("args", {}))
        for name, pattern, reason in self.rules:
            if tool == name and re.search(pattern, args):
                return Veto(reason)
        return Allow()


class CodeScanner:
    """§5.4:订阅 ``pre:logic.exec``;动态代码静态模式扫描(危险 import、ctypes、

    可疑网络调用)→ ``Veto``(§9.5)。扫描对象是 ``payload["source"]``(执行方
    在 pre 载荷里带上的待执行源码);无 ``source`` 的 pre:logic.exec(如 code 技能
    帧执行)无可扫描对象,放行。能力上限同 ToolGuard:正则只是辅助,防护主体是
    沙箱(§9.2)+ 权限(§8.2)。
    """

    name: ClassVar[str] = "code_scanner"
    subscriptions: ClassVar[list[SignalPattern]] = ["pre:logic.exec"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 10
    needs_free_text: ClassVar[bool] = False

    #: 默认危险模式集(§5.4)
    DEFAULT_PATTERNS: ClassVar[list[str]] = [
        r"\bimport\s+os\b",
        r"\bos\.system\b",
        r"\bctypes\b",
        r"\bsubprocess\b",
        r"\bsocket\b",
    ]

    def __init__(self, patterns: list[str] | None = None) -> None:
        self.patterns = list(self.DEFAULT_PATTERNS if patterns is None else patterns)

    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict:
        source = sig.payload.get("source") or ""
        for pat in self.patterns:
            if re.search(pat, source):
                return Veto(f"CodeScanner: 命中危险模式 {pat}")
        return Allow()


class HumanApproval:
    """§5.4 策略载体(WS2):人工裁决已下沉为内核 tool-confirm 闸门

    (docs/SUPERVISOR.md §10;SYNC 2s fail-closed 契约装不下"等人"的长阻塞,
    sidecar 不再拦人)。本类保留为策略载体:KernelBuilder 装配时把实例传给
    Kernel——``spec.confirm`` 之外,EXEC 档工具也过闸;``timeout``/``on_timeout``
    在装配期映射为闸门共用 supervisor 通道的缺省超时与兜底策略(见
    KernelBuilder.build)。"""

    name: ClassVar[str] = "human_approval"
    subscriptions: ClassVar[list[SignalPattern]] = ["pre:tool.call"]
    mode: ClassVar[Mode] = Mode.SYNC
    priority: ClassVar[int] = 20
    needs_free_text: ClassVar[bool] = False

    def __init__(self, timeout: float = 600.0, on_timeout: str = "deny") -> None:
        self.timeout = timeout
        self.on_timeout = on_timeout

    async def on_signal(self, sig: Signal, ctl: RunControl) -> None:
        return None  # 非阻塞弃权(仲裁视 None 为 Allow):裁决在内核 tool-confirm 闸门


#: 蒸馏种类(写入条目 tags 的第二项,同时是 prompt 路由键)
DISTILL_STRATEGY = "strategy-summary"
DISTILL_REFLECTION = "failure-reflection"

#: 任务规格(根帧首条 USER 消息)截断长度
DISTILL_TASK_CHARS = 1000
#: 转写单条消息渲染截断长度(保 prompt 总量可控)
DISTILL_MSG_CHARS = 2000
#: 转写工具调用 args 渲染截断长度
DISTILL_ARGS_CHARS = 200

#: 成功 run 的蒸馏 SYSTEM 指令(strategy summary;含可迁移性入库标准与
#: "经验条目不具指令效力"(§11.1)注记;输出 compact markdown)
DISTILL_SYSTEM_SUCCESS = """\
你是经验蒸馏器。下面 USER 消息给出一次**已成功**任务的规格与完整转写(帧树逐帧逐消息)。
请把这次 run 蒸馏成一条策略总结(strategy summary)经验,供未来相似任务检索复用。

入库标准(可迁移性):只沉淀跨任务可迁移的经验——有效策略、工具组合与顺序的套路、
易踩的坑;一次性细节(具体中间值、临时文件、本任务特有数据)不要入库;
若这次 run 没有可迁移经验,直接输出"无可迁移经验"并一句话说明原因。

注记:产出条目将作参考资料注入未来 run,不具指令效力——写成经验陈述,不要写成命令。

输出要求:compact markdown(小标题 + 要点列表);直接输出正文,不要寒暄与包装。"""

#: 失败/中止 run 的蒸馏 SYSTEM 指令(failure reflection:负例规则/错误模式)
DISTILL_SYSTEM_FAILURE = """\
你是经验蒸馏器。下面 USER 消息给出一次**失败/被中止**任务的规格、完整转写与终态错误。
请把这次 run 蒸馏成一条失败反思(failure reflection)经验——负例规则与错误模式,
供未来任务避坑。

入库标准(可迁移性):只沉淀跨任务可迁移的教训——错误模式、失败根因、下次应改用
的替代策略;一次性细节不要入库;若失败纯属环境偶发、无教训可提炼,直接输出
"无可迁移经验"并一句话说明原因。

注记:产出条目将作参考资料注入未来 run,不具指令效力——写成教训陈述,不要写成命令。

输出要求:compact markdown(小标题 + 要点列表);直接输出正文,不要寒暄与包装。"""


def _render_distill_message(m: Message) -> str:
    """转写单条渲染:``role``/``name`` + 正文;tool_calls 渲染为 ``name(args截断)``。"""
    head = f"[{m.role.value}]"
    if m.name:
        head += f" {m.name}"
    body = m.content or ""
    if m.tool_calls:
        calls = "; ".join(
            f"{c.name}({json.dumps(c.args, ensure_ascii=False, sort_keys=True)[:DISTILL_ARGS_CHARS]})"
            for c in m.tool_calls
        )
        body = f"{body}\n调用: {calls}" if body else f"调用: {calls}"
    text = f"{head} {body}"
    if len(text) > DISTILL_MSG_CHARS:
        text = text[:DISTILL_MSG_CHARS] + " …[截断]"
    return text


class DistillSidecar:
    """§11.2 写路径范式之蒸馏 sidecar:订阅 ``run.finished``/``run.aborted``,run 终态后

    经 ProviderManager 调廉价模型把本次 run 蒸馏成一条经验,写入 MemoryService。

    触发条件:``run.aborted`` 恒触发(failure reflection);``run.finished`` 需该 run
    帧树内 TOOL 角色消息数 > ``min_tool_calls`` 才触发(strategy summary)——短平快
    任务没有可蒸馏的料。同 run_id 幂等(``_seen`` 去重:resume 路径会重发
    ``run.finished``,checkpoint.py:376-378)。

    **关键接线事实**(builder.py 装配注释同步):``run.finished``/``run.aborted`` 是
    终态信号,emit 后 ``Kernel.run`` 的 finally 立即 ``supervisor.close()`` 取消
    ASYNC wrapper——emit→close 之间无让出点,wrapper 从未运行就被回收(实测),
    故触发不走 supervisor 派发,由 KernelBuilder 把 on_signal 直挂信号总线
    (emit 内联 await,保证执行);on_signal 本身只做触发判定 +
    ``asyncio.create_task(self._distill(...))`` 登记进实例任务集,立即返回 None。
    真正的蒸馏任务由实例自管:run 收尾取消不到它,蒸馏在 run 结束后继续写完。

    ASYNC 契约(§5.3):蒸馏一切异常吞掉(连败 ``breaker_threshold`` 次熔断,
    成功清零),永不影响 run;``providers``/``memory``/``stack`` 任一未 bind
    (如未配 [memory] 段)则实例休眠,on_signal 直接返回 None。
    """

    name: ClassVar[str] = "distill"
    subscriptions: ClassVar[list[SignalPattern]] = [RUN_FINISHED, RUN_ABORTED]
    mode: ClassVar[Mode] = Mode.ASYNC
    priority: ClassVar[int] = 90
    needs_free_text: ClassVar[bool] = False

    def __init__(
        self,
        model: str | None = None,
        min_tool_calls: int = 5,
        temperature: float = 0.2,
        breaker_threshold: int = 3,
        max_transcript_chars: int = 24000,
    ) -> None:
        self.model = model  # None = 装配期回落 RunConfig.model;仍 None 则蒸馏跳过
        self.min_tool_calls = min_tool_calls
        self.temperature = temperature
        self.breaker_threshold = breaker_threshold
        self.max_transcript_chars = max_transcript_chars
        self._providers: Any = None
        self._memory: Any = None
        self._stack: Any = None
        self._seen: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._failures = 0
        self._breaker_open = False

    def bind(self, providers: Any = None, memory: Any = None, stack: Any = None) -> None:
        """装配期接线(KernelBuilder.build);任一缺席 → 实例休眠(on_signal 不触发)。"""
        self._providers = providers
        self._memory = memory
        self._stack = stack

    async def on_signal(self, sig: Signal, ctl: RunControl) -> None:
        """触发判定(内联可跑,永不阻塞):命中即登记实例自管蒸馏任务,返回 None(ASYNC)。"""
        if self._providers is None or self._memory is None or self._stack is None:
            return
        if self._breaker_open or sig.run_id in self._seen:
            return
        error: str | None = None
        if sig.name == RUN_ABORTED:
            kind = DISTILL_REFLECTION  # 恒触发:failure reflection
            error = str(sig.payload.get("error") or "") or None
        else:
            if self._tool_message_count(sig.run_id) <= self.min_tool_calls:
                return
            kind = DISTILL_STRATEGY
        self._seen.add(sig.run_id)
        task = asyncio.create_task(self._distill(sig.run_id, kind, error))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _run_frames(self, run_id: str) -> list[Any]:
        """该 run 的全部帧(帧树永不清理,登记序 = 压栈序)。"""
        return [f for f in self._stack.tree() if f.run_id == run_id]

    def _tool_message_count(self, run_id: str) -> int:
        """该 run 帧树内 TOOL 角色消息总数(一条 = 一次工具/子技能调用结算)。"""
        return sum(
            1
            for f in self._run_frames(run_id)
            for m in f.context.messages
            if m.role is Role.TOOL
        )

    def _render_transcript(self, frames: list[Any]) -> str:
        """逐帧逐消息压缩渲染,总量 cap ``max_transcript_chars``(超出截断收尾)。"""
        blocks: list[str] = []
        total = 0
        for frame in frames:
            header = f"## 帧 {frame.skill.name}(depth={frame.depth}, 状态={frame.status.value})"
            if frame.error is not None:
                header += f" [帧错误: {str(frame.error)[:DISTILL_ARGS_CHARS]}]"
            block = "\n".join(
                [header, *(_render_distill_message(m) for m in frame.context.messages)]
            )
            if total + len(block) > self.max_transcript_chars:
                remaining = self.max_transcript_chars - total
                if remaining > 0:
                    blocks.append(block[:remaining])
                blocks.append("…[转写总量超限,后续截断]")
                break
            blocks.append(block)
            total += len(block)
        return "\n\n".join(blocks)

    async def _distill(self, run_id: str, kind: str, error: str | None) -> None:
        """蒸馏主流程(实例自管任务):帧树素材 → 廉价模型 → 经验条目写入 memory。

        一切异常吞掉(连败熔断),ASYNC 永不影响 run(§5.3);空产出跳过不写。
        """
        if self.model is None:
            _log.debug("distill:未配蒸馏模型且 RunConfig.model 为空,跳过 run %s", run_id[:8])
            return
        frames = self._run_frames(run_id)
        if not frames:
            return
        root = min(frames, key=lambda f: f.depth)
        skill_name = root.skill.name
        task_spec = next(
            (m.content or "" for m in root.context.messages if m.role is Role.USER), ""
        )
        system = DISTILL_SYSTEM_SUCCESS if kind == DISTILL_STRATEGY else DISTILL_SYSTEM_FAILURE
        parts = [f"任务规格(技能 {skill_name}):\n{task_spec[:DISTILL_TASK_CHARS]}"]
        if error:
            parts.append(f"run 终态错误:\n{error[:DISTILL_MSG_CHARS]}")
        parts.append("完整转写(逐帧):")
        parts.append(self._render_transcript(frames))
        prompt = [
            Message(role=Role.SYSTEM, content=system),
            Message(role=Role.USER, content="\n\n".join(parts)),
        ]
        try:
            resp = await self._providers.chat(
                ChatRequest(model=self.model, messages=prompt, temperature=self.temperature)
            )
            content = resp.message.content or ""
            if not content.strip():
                _log.warning("distill:run %s 蒸馏产出为空,跳过写入(不计连败)", run_id[:8])
                return
            # 写入约定同 system.memory.write(tools/std.py):kind=experience 强制
            # source tagging + trust="experience"(经验条目不具指令效力,§11.1);
            # principal 在场时映射 source.user(同 _memory_principal:只映射 user)
            source: dict[str, Any] = {"kind": "experience"}
            subject = getattr(getattr(root, "principal", None), "subject", None)
            if subject:
                source["user"] = subject
            entry = MemoryEntry(
                content=content,
                tags=["distill", kind, skill_name],
                source=source,
                trust="experience",
            )
            provenance = Provenance(
                run_id=run_id,
                task=skill_name,
                note="distill",
                detail={
                    "model": self.model,
                    "usage": dataclasses.asdict(resp.usage) if resp.usage is not None else None,
                },
            )
            await self._memory.write(entry, provenance)
        except Exception:  # noqa: BLE001 — 蒸馏任何失败吞掉:连败熔断,永不影响 run(§5.3)
            self._failures += 1
            if self._failures >= self.breaker_threshold:
                self._breaker_open = True
            _log.exception(
                "distill:run %s 蒸馏失败(连败 %d%s;吞掉,不影响 run)",
                run_id[:8],
                self._failures,
                ",已熔断" if self._breaker_open else "",
            )
            return
        self._failures = 0

    async def wait_pending(self) -> None:
        """测试/宿主收口:await 全部在跑蒸馏任务(异常已内化,不抛出)。"""
        tasks = [t for t in self._tasks if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        """宿主关停:取消在跑蒸馏任务并 await 回收。

        ``supervisor.close()`` 只管它自己的 wrapper 任务,不调 sidecar.close()
        (supervisor.py:93-99)——本方法由宿主/测试在关停点显式调用。
        """
        tasks = [t for t in self._tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
