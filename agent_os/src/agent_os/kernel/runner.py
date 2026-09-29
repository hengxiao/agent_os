"""内核 runner(docs/DESIGN.md §3.1 语义伪码的落点;M0 单帧 runner,M2 调用栈/压栈挂起,
M4 verdict 仲裁与 sidecar 接线,M5a checkpoint/resume 断电恢复,
S1 ask_supervisor 拦截/就地挂起/回答注入与 resume 重问,docs/SUPERVISOR.md v2 §2/§4;
WS2 tool-confirm 两阶段闸门,docs/DESIGN.md §8.2 + docs/SUPERVISOR.md §10;
WS3 parallel_invoke fork/join,docs/DESIGN.md §3.4 第三原语)。

agent loop 顺序:safe point(run 中止/挂起标志:stop→RunAborted、pause→RunPaused,
同置时 stop 优先)→ pre:step 检查点(verdict 仲裁,§5.2)
→ 强制压缩检查 → context.maintain/build → providers.chat(RunConfig.stream 开且
provider caps 支持流式时改走 stream chunk 循环:逐 chunk 发 post:llm.chunk、
safe point 同款取消查表、ttft/total 计时入账;否则回落 chat 原路径)→
终止判断(outputs 校验 + verifier)→ 分发(pre:tool.call 可 Veto/Modify/Stop;
confirm/EXEC 工具过 tool-confirm 闸门挂起等人审;工具走 Tool Registry,
``skill.*`` 压栈)→ post:step(调用签名列表)→
记账与预算检查。弹栈前 ``pre:frame.pop`` 同步可否决(§3.1 pop())。

硬失败传播边界(§3.2):MaxDepthExceeded / RunAborted(含 BudgetExceeded,含
RunPaused——Run 边界特判落 PAUSED 而非 ABORTED,docs/DESIGN.md :940)不可被
单帧吞掉,沿调用栈弹到 Run 边界;其余子帧异常折叠为父帧的错误观察。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import inspect
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import jsonschema

from agent_os.api.v1 import (
    ASK_SUPERVISOR_TOOL,
    BUDGET_EXCEEDED,
    ORCHESTRATE_TOOL,
    POST_FRAME_POP,
    POST_FRAME_PUSH,
    POST_LLM_CHUNK,
    POST_LLM_RESPONSE,
    POST_LOGIC_EXEC,
    POST_SKILL_ESCALATE,
    POST_SKILL_INVOKE,
    POST_STEP,
    POST_TOOL_CALL,
    PRE_FRAME_POP,
    PRE_FRAME_PUSH,
    PRE_LLM_REQUEST,
    PRE_LOGIC_EXEC,
    PRE_SKILL_ESCALATE,
    PRE_SKILL_INVOKE,
    PRE_STEP,
    PRE_TOOL_CALL,
    RUN_ABORTED,
    RUN_FINISHED,
    RUN_PAUSED,
    RUN_STARTED,
    SKILL_ESCALATION_DENIED,
    TIER_IRREVERSIBLE,
    TIER_REVERSIBLE,
    Allow,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    EscalationRequest,
    ExecRequest,
    ForceCompress,
    FrameContext,
    FrameStatus,
    Grant,
    InjectMessage,
    LogicError,
    Message,
    Modify,
    Pause,
    Permission,
    Role,
    RunConfig,
    RunStatus,
    Signal,
    Skill,
    SkillCall,
    SkillFrame,
    SkillKind,
    SkillManifest,
    SkillRef,
    Source,
    Stop,
    ToolCall,
    ToolErrorKind,
    ToolResult,
    ToolSpec,
    TrustLevel,
    Usage,
    Veto,
    derive_side_effect,
    derive_skill_tier,
    derive_tools_tier,
    tier_exceeds,
)
from agent_os.kernel.checkpoint import (
    _last_tool_message,
    dump_checkpoint,
    resume_from_checkpoint,
)
from agent_os.kernel.control import FORCE_COMPRESS_KEY
from agent_os.kernel.errors import (
    BudgetExceeded,
    MaxDepthExceeded,
    OutputValidationError,
    RunAborted,
    RunPaused,
    SkillLoadError,
    SubtreeCancelled,
    ToolDispatchError,
)
from agent_os.kernel.logic_context import KernelLogicContext
from agent_os.kernel.run import Run
from agent_os.kernel.stack import FrameStack
from agent_os.logic.limits import exec_limits
from agent_os.tools.local_registry import ToolDispatchContext

_log = logging.getLogger("agent_os.kernel")

#: 最终答案 outputs 校验连败上限(§3.2 恢复环路语义:输出修复循环独立熔断)
_OUTPUT_VALIDATION_MAX_FAILURES = 2

#: parallel_invoke 分支协程返回哨兵:depends_on 前置分支失败,本分支未启动
_DEP_FAILED = object()

#: 单次沙箱执行的 syscall 默认上限(docs/CODE-ORCHESTRATION.md §4;manifest
#: ``limits.max_tool_calls`` 可覆盖)。限额是内核策略,故在此计数与拒绝——
#: 传输层只管传输,跑飞脚本由 wall_time 兜底
DEFAULT_MAX_TOOL_CALLS = 50

def _check_output(manifest: SkillManifest, content: str) -> tuple[Any, str | None]:
    """最终答案终止判断(§3.1 步骤 5):``(result, None)`` 或 ``(None, 错误说明)``。"""
    try:
        result = json.loads(content)
    except json.JSONDecodeError as e:
        return None, f"最终答案不是合法 JSON: {e}"
    if manifest.outputs:
        try:
            jsonschema.validate(result, manifest.outputs)
        except jsonschema.ValidationError as e:
            return None, f"最终答案不合 outputs schema: {e.message}"
    return result, None


def _error_payload(kind: ToolErrorKind, message: str, hint: str | None = None) -> dict[str, Any]:
    return {"kind": kind.value, "message": message, "retryable": False, "hint": hint or ""}


def _escalated_marker(name: str, target_manifest: SkillManifest) -> str:
    """升权溯源标记值(WS1,docs/ESCALATION.md §5):``"{name}@{version}"``;
    manifest version 缺省/空串时只写 name。"""
    version = target_manifest.version or ""
    return f"{name}@{version}" if version else name


def _cancelled_payload(message: str, hint: str | None = None) -> dict[str, Any]:
    """分支取消的结构化错误载荷(parallel_invoke,§3.4)。

    kind="cancelled" 是批级终态(first_success 败方/depends_on 未启动/子树取消),
    不是工具分发失败,故不占 ToolErrorKind 枚举——与 interrupted 的"分发中断"
    语义区分(后者是 §3.1 中断配对占位)。
    """
    return {"kind": "cancelled", "message": message, "retryable": False, "hint": hint or ""}


def _result_payload(result: ToolResult) -> dict[str, Any]:
    error = None
    if result.error is not None:
        error = {
            "kind": result.error.kind.value,
            "message": result.error.message,
            "retryable": result.error.retryable,
            "hint": result.error.hint,
        }
    return {"ok": result.ok, "value": result.value, "error": error}


def _call_sig(call: ToolCall) -> str:
    """调用签名(post:step 载荷,§5.4 LoopDetector 观察面):

    ``sha1(name + json.dumps(args, sort_keys=True))`` 前 12 位。
    """
    raw = call.name + json.dumps(call.args, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


class Kernel:
    """微内核本体:持有九个子系统的契约句柄,驱动 agent loop。

    构造参数即 §14.2 KernelBuilder 收集的全部子系统(均为契约 Protocol 类型)。
    """

    def __init__(
        self,
        *,
        config: RunConfig | None = None,
        providers: Any = None,  # ProviderManager(§4.2)
        tools: Any = None,  # Tool Registry(§8)
        skills: Any = None,  # SkillRegistry(§6)
        context: Any = None,  # ContextManager(§7.5)
        logic: Any = None,  # LogicKernel 路由(§9)
        sidecars: Any = None,  # SidecarSupervisor(§5.3)
        signals: Any = None,  # 信号总线(§5.1)
        telemetry: Any = None,  # TelemetrySink(§10)
        memory: Any = None,  # MemoryService(§11)
        blackboard: Any = None,  # Blackboard(§12)
        supervisor: Any = None,  # SupervisorManager(docs/SUPERVISOR.md §2.3;S1 handler 通道)
        stack: FrameStack | None = None,
        human_approval: Any = None,  # HumanApproval 策略载体(WS2 下沉,docs/SUPERVISOR.md §10;None=关)
    ) -> None:
        self.config = config or RunConfig()
        self.providers = providers
        self.tools = tools
        self.skills = skills
        self.context = context
        self.logic = logic
        self.sidecars = sidecars
        self.signals = signals
        self.telemetry = telemetry
        self.memory = memory
        self.blackboard = blackboard
        self.supervisor = supervisor
        self.stack = stack or FrameStack(max_depth=self.config.max_depth)
        #: HumanApproval 策略载体(WS2):在场时 EXEC 档工具也过 tool-confirm 闸门
        #: (``_confirm_tool_call``);其 timeout/on_timeout 已在装配期映射为
        #: supervisor 通道的缺省策略(见 KernelBuilder.build)
        self.human_approval = human_approval
        self._runs: dict[str, Run] = {}
        #: run 中止标志表(dict[run_id, reason];RunControl.stop 置位,
        #: runner 在 pre:step safe point 检查并抛 RunAborted,§3.1/§5.2)
        self._stop_flags: dict[str, str] = {}
        #: run 挂起标志表(dict[run_id, reason];RunControl.pause 置位,
        #: safe point 同点检查抛 RunPaused——Run 边界落 PAUSED、可 resume,
        #: docs/DESIGN.md :940;同 stop 同置时 stop 优先(更硬,落 ABORTED);
        #: run 收尾由 _release_run 清理,同进程 resume 不会立即再挂起)
        self._pause_flags: dict[str, str] = {}
        #: 帧级 stop 标志表(dict[frame_id, reason];RunControl.cancel_frame 经
        #: cancel_subtree 置位;safe point 同点检查,命中抛 SubtreeCancelled——
        #: 子树终态,不杀 run;仅 prompt 帧在 safe point 消费,code 帧不检查)
        self._frame_stop_flags: dict[str, str] = {}
        #: 帧/子树级预算防重表(frame_id 集合;manifest limits.max_cost/max_steps
        #: 触发即登记,信号恰好一次、取消不重复发)。与 _frame_stop_flags 同先例:
        #: 帧 id 全局唯一、按触发帧数有界,无清理点;进程态不随 checkpoint 持久化
        #: (新进程 resume 会再触发一次:cancel ack 幂等 + BudgetExceeded 重抛同语义)
        self._budget_tripped: set[str] = set()
        #: RunControl 句柄(装配 sidecars 时由 KernelBuilder 注入;pre:step 的
        #: InjectMessage/ForceCompress verdict 经它落地)
        self.ctl: Any = None
        #: spawn 后台帧登记表(§3.4):run_id → {frame_id → (parent_frame_id, 后台任务)};
        #: wait_frame 在此 join;按 run 分桶使 _release_run 只回收本 run(跨 run 隔离),
        #: parent_frame_id 供 cancel_subtree 补帧树压栈前的竞态窗口
        self._spawned: dict[str, dict[str, tuple[str, asyncio.Task[Any]]]] = {}

    # ------------------------------------------------------------------
    # §13 生命周期入口
    # ------------------------------------------------------------------

    async def run(self, skill: str, input: dict[str, Any], principal: Any = None) -> Any:
        """§13 生命周期入口:解析根技能 → 构建根帧 → 跑帧树 → usage 汇总返回。

        ``principal``(docs/DATA-AUTHZ.md §2.2):宿主认证后的调用方身份,存根帧并
        由子帧原样继承;缺省 None = v1 单用户语义(数据层不启用拦截)。
        """
        skill_obj = self.skills.get(SkillRef(name=skill))
        manifest = skill_obj.manifest
        if manifest.inputs:
            try:
                jsonschema.validate(input, manifest.inputs)
            except jsonschema.ValidationError as e:
                raise SkillLoadError(f"技能 {skill} 的输入不合 inputs schema: {e.message}") from e
        run = Run(run_id=uuid.uuid4().hex, config=self.config)
        run.state.status = RunStatus.RUNNING
        self._runs[run.run_id] = run
        root = SkillFrame(
            frame_id=uuid.uuid4().hex,
            run_id=run.run_id,
            skill=skill_obj.ref,
            input=dict(input),
            depth=1,
            principal=principal,
            context=FrameContext(
                messages=[
                    Message(role=Role.USER, content=json.dumps(input), source=Source.PARENT_INPUT)
                ]
            ),
            # 根帧 = 根 skill 的直接能力档(只看自己的 tools;docs/ESCALATION.md §2.2)。
            # 根技能由宿主直接启动不过闸——但启动只确认了根技能的直接能力面,
            # 经子技能够到更高档仍要过升权闸;若按完整推导档(含 skills 递归),
            # 白名单内调用恒不升权,闸门成为死代码
            tier=derive_tools_tier(manifest, self.tools),
        )
        await self.signals.emit(
            Signal(name=RUN_STARTED, run_id=run.run_id, payload={"skill": str(skill_obj.ref)})
        )
        try:
            try:
                result = await self.run_frame(root)
            except RunPaused as e:
                # 可恢复挂起(docs/DESIGN.md :940):落 PAUSED 而非 ABORTED,发
                # run.paused(不发 run.aborted);finally 照常收尾,宿主 finalize
                # 落 checkpoint 后可经 resume 恢复。注意必须放在通用
                # ``except Exception`` 之前——RunPaused 是 RunAborted 子类
                run.state.status = RunStatus.PAUSED
                run.state.error = f"{type(e).__name__}: {e}"
                await self.signals.emit(
                    Signal(name=RUN_PAUSED, run_id=run.run_id, payload={"reason": str(e)})
                )
                raise
            except Exception as e:
                run.state.status = (
                    RunStatus.ABORTED if isinstance(e, RunAborted) else RunStatus.FAILED
                )
                run.state.error = f"{type(e).__name__}: {e}"
                await self.signals.emit(
                    Signal(name=RUN_ABORTED, run_id=run.run_id, payload={"error": run.state.error})
                )
                raise
            run.state.status = RunStatus.DONE
            run.state.result = result
            await self.signals.emit(Signal(name=RUN_FINISHED, run_id=run.run_id, payload={}))
            return result
        finally:
            # run 收尾:取消在跑的 ASYNC sidecar 任务(§5.3)
            if self.sidecars is not None:
                await self.sidecars.close()
            await self._release_run(run.run_id)

    async def _release_run(self, run_id: str) -> None:
        """run 边界的资源回收:后台帧 → telemetry 句柄 → 工具临时目录。

        逐项都是"只增不减"的登记表(审计发现:全仓原先无任何回收点),长驻
        宿主跑够多 run 会 fd 耗尽 + /tmp 塞满。子系统未提供对应方法时静默跳过
        (契约层没强制这些方法,duck-typing 探测)。
        """
        # 后台帧(§3.4):run 已结算,残留任务不该继续记账进已结算的 usage;
        # 只回收本 run 桶——共享 kernel 的并发 run 不得跨 run 误杀
        for frame_id, (_parent_id, task) in list(self._spawned.pop(run_id, {}).items()):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        # 挂起标志一次性生效:run 收尾即清理,同进程 resume 不会立即再挂起
        # (跨进程 resume 本就看新内核的空表;标志本身不随 checkpoint 持久化)
        self._pause_flags.pop(run_id, None)
        for subsystem, method in ((self.telemetry, "close_run"), (self.tools, "release_run")):
            fn = getattr(subsystem, method, None)
            if fn is None:
                continue
            try:
                result = fn(run_id)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:  # noqa: BLE001 — 回收失败不该改变 run 的结果
                _log.warning("run %s 的 %s 回收失败: %r", run_id[:8], method, e)

    # ------------------------------------------------------------------
    # §10.2 检查点(M5a):WAL 原则——轨迹即全部状态,恢复 = 重入 loop 而非重跑
    # ------------------------------------------------------------------

    def checkpoint(self, run_id: str, path: str) -> None:
        """把 run 状态与全部帧(含上下文、usage、状态)序列化为 JSON 检查点(§10.2)。"""
        dump_checkpoint(self, run_id, path)

    async def resume(self, path: str) -> Any:
        """从检查点恢复:跳过 DONE 帧,最深的未完成帧带完整上下文重入 loop(§10.2)。"""
        return await resume_from_checkpoint(self, path)

    # ------------------------------------------------------------------
    # §3.1 单帧 agent loop
    # ------------------------------------------------------------------

    def _sig(self, name: str, frame: SkillFrame, extra: dict[str, Any] | None = None) -> Signal:
        payload = {"depth": frame.depth, "frame_id": frame.frame_id, "skill": str(frame.skill)}
        payload.update(extra or {})
        return Signal(name=name, run_id=frame.run_id, frame_id=frame.frame_id, payload=payload)

    # ------------------------------------------------------------------
    # §5.2/§5.3 verdict 仲裁(内核职责,§1 公理 3):策略在 sidecar,仲裁在内核
    # ------------------------------------------------------------------

    @staticmethod
    def _arbitrate_pre(verdicts: list[Any]) -> Any:
        """首个非 ``Allow`` verdict(emit 已按订阅/priority 序返回;None 视为 Allow)。"""
        for verdict in verdicts:
            if verdict is not None and not isinstance(verdict, Allow):
                return verdict
        return None

    async def _apply_pre_step(self, frame: SkillFrame, verdicts: list[Any]) -> None:
        """pre:step 检查点(§3.1 步骤 1):否决/暂停/强停即中止 run;注入/强压落地后继续。"""
        verdict = self._arbitrate_pre(verdicts)
        if verdict is None:
            return
        if isinstance(verdict, (Stop, Veto)):
            # pre:step 否决即中止(§5.2)
            raise RunAborted(verdict.reason)
        if isinstance(verdict, Pause):
            # 可恢复挂起(docs/DESIGN.md :940):理由原样上抛,Run 边界落 PAUSED
            raise RunPaused(verdict.reason)
        if isinstance(verdict, InjectMessage) and self.ctl is not None:
            await self.ctl.inject_message(verdict.frame_id, verdict.msg)
        elif isinstance(verdict, ForceCompress) and self.ctl is not None:
            await self.ctl.force_compress(verdict.frame_id)  # 置帧标志,maintain 前生效

    async def _force_compress(self, frame: SkillFrame) -> None:
        """强制压缩一次(§7.1 外部强制触发):无视 cap,走 context manager 压缩路径。"""
        force = getattr(self.context, "force_compress", None)
        if force is not None:
            await force(frame)

    async def _drain_compress_usage(self, frame: SkillFrame) -> None:
        """压缩链 LLM 用量排干入账(§7.2:summarize 等策略经 ProviderManager 的调用)。

        压缩器把每次 LLM 调用落 ``frame.context.working["_compress_llm_usage"]``
        (``{"model", "usage"}``;force_compress 与 maintain 共用该键,在本排干点
        一起收)。逐条:token 六项镜像 :meth:`account` 累加进帧/run 两级——
        **steps 不加**(压缩不是主循环步;run 级预算检查仍在下一步 account 统一做,
        帧/子树级预算在本排干点累加后即查,压缩花费同样受祖先预算约束)——
        并补发 ``post:llm.response``(``source="compress"``,与主循环发送点
        同形状 + additive),让 BudgetGuard/遥测看到完整成本。
        """
        entries = frame.context.working.pop("_compress_llm_usage", [])
        if not entries:
            return
        run = self._runs.get(frame.run_id)
        for entry in entries:
            usage = entry.get("usage")
            if usage is not None:
                targets = [frame.usage] + ([run.state.usage] if run is not None else [])
                for target in targets:
                    target.prompt_tokens += usage.prompt
                    target.completion_tokens += usage.completion
                    target.cache_read_tokens += usage.cache_read
                    target.cache_write_tokens += usage.cache_write
                    target.thinking_tokens += usage.thinking
                    target.cost += usage.cost
            await self.signals.emit(
                self._sig(
                    POST_LLM_RESPONSE,
                    frame,
                    {
                        "model": entry.get("model", ""),
                        "usage": self._usage_payload(usage),
                        "source": "compress",
                    },
                )
            )
        await self._check_subtree_budgets(frame)

    @staticmethod
    def _usage_payload(usage: ChatUsage | None) -> dict[str, Any]:
        """post:llm.response 的 usage 载荷(BudgetGuard 的记账数据源,§5.4)。"""
        if usage is None:
            return {"prompt": 0, "completion": 0, "cost": 0.0}
        return {"prompt": usage.prompt, "completion": usage.completion, "cost": usage.cost}

    async def run_frame(self, frame: SkillFrame) -> Any:
        """单帧 agent loop(§3.1):压栈 → loop → outputs 校验 → 弹栈。

        弹栈前 ``pre:frame.pop`` 同步可否决(§3.1 pop()):Veto → 纠偏观察写入
        帧上下文并回到 loop 继续(不弹栈);Stop → RunAborted;无否决才 pop_ok。
        """
        skill_obj = self.skills.get(frame.skill)
        await self.signals.emit(self._sig(PRE_FRAME_PUSH, frame))
        self.stack.push(frame)  # 深度兜底检查在 push 时(§3.2)
        await self.signals.emit(self._sig(POST_FRAME_PUSH, frame))
        while True:
            try:
                result = await self._execute_frame(frame, skill_obj)
            except BaseException as err:
                self.stack.pop_err(frame, err)
                await self._board_status(frame, {"status": "failed", "error": str(err)})
                raise
            verdicts = await self.signals.emit(
                self._sig(PRE_FRAME_POP, frame, {"result": result})
            )
            verdict = self._arbitrate_pre(verdicts)
            if isinstance(verdict, Stop):
                raise RunAborted(verdict.reason)
            if not isinstance(verdict, Veto):
                break  # 无否决,正常弹栈
            # reviewer 打回:纠偏观察入帧上下文,回到 loop 继续(§3.1)
            frame.context.messages.append(
                Message(
                    role=Role.USER,
                    content=f"reviewer 打回:{verdict.reason}。请继续完成任务。",
                    source=Source.INJECTED,
                )
            )
        self.stack.pop_ok(frame, result)
        await self._board_status(frame, {"status": "done", "result": result})
        await self.signals.emit(self._sig(POST_FRAME_POP, frame))
        return result

    async def _execute_frame(self, frame: SkillFrame, skill_obj: Skill) -> Any:
        """按技能形态执行一帧:code 技能交 Logic Kernel,prompt 技能跑 agent loop。"""
        if skill_obj.manifest.kind is SkillKind.CODE:
            return await self._run_code_frame(frame, skill_obj)
        return await self._frame_loop(frame, skill_obj)

    async def _frame_loop(self, frame: SkillFrame, skill_obj: Skill) -> Any:
        manifest = skill_obj.manifest
        output_failures = 0
        while True:
            # safe point(§3.1):先查 run 中止标志(RunControl.stop,§5.2)再查挂起
            # 标志(RunControl.pause)——同置时 stop 优先(更硬:ABORTED 不可恢复,
            # pause 落 PAUSED 可 resume,docs/DESIGN.md :940)
            reason = self._stop_flags.get(frame.run_id)
            if reason is not None:
                raise RunAborted(reason)
            pause_reason = self._pause_flags.get(frame.run_id)
            if pause_reason is not None:
                raise RunPaused(pause_reason)
            # 帧级 stop 标志(cancel_subtree 置位):子树终态,抛 SubtreeCancelled——
            # 分支取消不杀 run,与上面的 run 中止严格分界(§5.2 cancel_frame)
            frame_reason = self._frame_stop_flags.get(frame.frame_id)
            if frame_reason is not None:
                raise SubtreeCancelled(frame_reason)
            verdicts = await self.signals.emit(
                self._sig(PRE_STEP, frame, {"step": frame.usage.steps + 1})
            )
            await self._apply_pre_step(frame, verdicts)
            if frame.context.working.pop(FORCE_COMPRESS_KEY, False):
                await self._force_compress(frame)
            await self.context.maintain(frame)
            await self._drain_compress_usage(frame)
            req = await self.context.build(frame)
            await self.signals.emit(self._sig(PRE_LLM_REQUEST, frame, {"model": req.model}))
            resp = await self._llm_call(frame, req)
            # dict 形 tool_calls 归一化为 ToolCall(§4.1 契约形态;mock/第三方 provider
            # 可能回 dict)——在进帧上下文前统一,分发/调用签名/检查点只处理一种形态
            resp.message.tool_calls = [
                ToolCall(
                    id=str(tc.get("id", "")),
                    name=str(tc.get("name", "")),
                    args=dict(tc.get("args") or {}),
                )
                if isinstance(tc, dict)
                else tc
                for tc in resp.message.tool_calls
            ]
            await self.signals.emit(
                self._sig(
                    POST_LLM_RESPONSE,
                    frame,
                    {"model": req.model, "usage": self._usage_payload(resp.usage)},
                )
            )
            frame.context.messages.append(resp.message)
            await self.account(frame, resp.usage, ttft_ms=resp.ttft_ms, total_ms=resp.total_ms)
            if not resp.message.tool_calls:
                result, error = _check_output(manifest, resp.message.content)
                if error is None:
                    return result
                output_failures += 1
                if output_failures >= _OUTPUT_VALIDATION_MAX_FAILURES:
                    raise OutputValidationError(
                        f"技能 {manifest.name} 最终答案连续 {output_failures} 次未通过 "
                        f"outputs 校验: {error}"
                    )
                # 错误观察入上下文,允许重试一次(§3.2 输出修复循环)
                frame.context.messages.append(
                    Message(
                        role=Role.USER,
                        content=f"你的最终答案未通过 outputs 校验:{error}。"
                        f"请严格按 outputs schema 重新输出最终答案 JSON。",
                        source=Source.SYSTEM,
                    )
                )
                continue
            for call in resp.message.tool_calls:
                try:
                    payload = await self._dispatch_call(call, frame, manifest)
                except asyncio.CancelledError:
                    # 中断配对(§3.1 P0):占位 tool_result 保证配对原子性(§7.4 不变量 2)
                    payload = {
                        "ok": False,
                        "value": None,
                        "error": _error_payload(ToolErrorKind.INTERRUPTED, "interrupted"),
                    }
                    frame.context.messages.append(self._tool_message(call, payload))
                    raise
                except (RunAborted, MaxDepthExceeded, SubtreeCancelled):
                    # 硬失败(§3.2:预算/强停/深度兜底)与子树取消终态不可被单帧
                    # 吞成 INTERNAL 错误观察,照常弹栈
                    raise
                except Exception as e:  # noqa: BLE001 — 分发边界故意兜底:意外异常归一化为错误观察,run 存活
                    payload = {
                        "ok": False,
                        "value": None,
                        "error": _error_payload(
                            ToolErrorKind.INTERNAL, f"工具分发异常: {type(e).__name__}: {e}"
                        ),
                    }
                    frame.context.messages.append(self._tool_message(call, payload))
                    continue
                frame.context.messages.append(self._tool_message(call, payload))
            # post:step:本步调用签名列表(LoopDetector/StallDetector 的观察面,§5.4)
            await self.signals.emit(
                self._sig(
                    POST_STEP,
                    frame,
                    {
                        "step": frame.usage.steps,
                        "calls": [
                            {"name": c.name, "sig": _call_sig(c)}
                            for c in resp.message.tool_calls
                        ],
                    },
                )
            )
            # StatusBoard(§12.2):每步滚动状态,供父帧/外部按需读取
            await self._board_status(
                frame,
                {"status": "running", "step": frame.usage.steps, "skill": str(frame.skill)},
            )

    # ------------------------------------------------------------------
    # WS2 流式消费:chunk 循环 + post:llm.chunk + ttft/total 计时
    # ------------------------------------------------------------------

    async def _llm_call(self, frame: SkillFrame, req: ChatRequest) -> ChatResponse:
        """LLM 调用入口:流式优先,双保险回落一次性 ``chat``。

        ``RunConfig.stream`` 开(缺省)且 provider caps 报 ``supports_streaming``
        时走 :meth:`_stream_call`;否则(开关关闭,或 caps 不支持——如 MockProvider
        未配 ``stream_scripts``)回落 ``chat``,与原路径逐字一致。
        """
        provider = self.providers.resolve(req.model)
        if self.config.stream and provider.capabilities().supports_streaming:
            return await self._stream_call(frame, req)
        return await self.providers.chat(req)

    async def _stream_call(self, frame: SkillFrame, req: ChatRequest) -> ChatResponse:
        """流式 chunk 循环(§4.1 ``stream``;WS2 消费侧):组装成与 chat 同形态的回包。

        - 每 chunk 发 ``post:llm.chunk``(仅 ASYNC 观察;``_sig`` 基底 +
          ``{model, seq, text}``,text 取 ``delta.content``,seq 从 0 递增);
        - 取消与 safe point 同点语义:逐 chunk 查 ``_stop_flags``/``_pause_flags``/
          ``_frame_stop_flags``(纯 dict 读),命中抛 RunAborted/RunPaused/
          SubtreeCancelled——组装缓冲随之丢弃,
          半截 assistant 消息不 append(§7.4 配对不变量安全);
        - 计时:``time.monotonic()`` 记 t0,首 chunk 记 ttft、流尽记 total,
          落在 ``ChatResponse.ttft_ms/total_ms``,由 :meth:`account` 两级累加;
        - 组装:content/reasoning 逐 chunk 拼接,tool_calls 收齐(provider 侧已把
          分片缓冲成完整 ToolCall 一次交付),meta 合并,usage/finish_reason 取终
          chunk(终 chunk 缺席时 usage 归零值,与 chat 的缺省形态一致);
        - 中止/取消/异常经 finally ``aclose()`` 关流,生成器收尾不泄漏悬挂流
          (CancelledError 穿透时 WS1 provider 侧已关 response)。
        """
        t0 = time.monotonic()
        ttft_ms = 0
        seq = 0
        contents: list[str] = []
        reasonings: list[str] = []
        tool_calls: list[ToolCall] = []
        meta: dict[str, Any] = {}
        usage: ChatUsage | None = None
        finish_reason = ""
        stream = self.providers.stream(req)
        try:
            async for chunk in stream:
                # safe point 同款查表(纯 dict 读,§5.2):流中 stop/pause/cancel
                # 立即生效;同 pre:step 的顺序——stop 优先于 pause(更硬)
                reason = self._stop_flags.get(frame.run_id)
                if reason is not None:
                    raise RunAborted(reason)
                pause_reason = self._pause_flags.get(frame.run_id)
                if pause_reason is not None:
                    raise RunPaused(pause_reason)
                frame_reason = self._frame_stop_flags.get(frame.frame_id)
                if frame_reason is not None:
                    raise SubtreeCancelled(frame_reason)
                if seq == 0:
                    ttft_ms = int((time.monotonic() - t0) * 1000)
                delta = chunk.delta
                text = ""
                if delta is not None:
                    text = delta.content or ""
                    if text:
                        contents.append(text)
                    if delta.reasoning:
                        reasonings.append(delta.reasoning)
                    tool_calls.extend(delta.tool_calls)
                    meta.update(delta.meta)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                if chunk.usage is not None:
                    usage = chunk.usage
                await self.signals.emit(
                    self._sig(POST_LLM_CHUNK, frame, {"model": req.model, "seq": seq, "text": text})
                )
                seq += 1
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()
        total_ms = int((time.monotonic() - t0) * 1000)
        return ChatResponse(
            message=Message(
                role=Role.ASSISTANT,
                content="".join(contents),
                tool_calls=tool_calls,
                reasoning="".join(reasonings) if reasonings else None,
                meta=meta,
            ),
            finish_reason=finish_reason,
            usage=usage or ChatUsage(),
            ttft_ms=ttft_ms,
            total_ms=total_ms,
        )

    # ------------------------------------------------------------------
    # §9.4 code 技能帧:Logic Kernel 是唯一执行点(§9)
    # ------------------------------------------------------------------

    async def _run_code_frame(self, frame: SkillFrame, skill_obj: Skill) -> Any:
        """code 技能帧(§9.4):构造 ExecRequest 交 Logic Kernel 路由执行。

        默认 TRUSTED(注入 LogicContext,§9.3 编排者能力);``logic: {mode: sandbox}``
        或 ``RunConfig.logic_policy.force_sandbox`` → SANDBOX(§9.2,ctx=None 纯计算)。
        执行失败 → ToolDispatchError;返回值过 outputs schema 校验(无重试,§3.1 步骤 5)。
        """
        manifest = skill_obj.manifest
        module, _, entry = (manifest.handler or "").partition(":")
        if not module or not entry:
            raise SkillLoadError(
                f"code 技能 {manifest.name} 的 handler 应为 'pkg.mod:func' 形式,"
                f"得到: {manifest.handler!r}"
            )
        if self.logic is None:
            raise ToolDispatchError("未装配 Logic Kernel,code 技能无法执行(§9)")
        kernel = self.logic.route(manifest, self.config)
        trust = kernel.trust.value
        timeout = manifest.limits.timeout if manifest.limits and manifest.limits.timeout else 60
        trusted = kernel.trust is TrustLevel.TRUSTED
        req = ExecRequest(
            source=module,
            entry=entry,
            args=frame.input,
            ctx=KernelLogicContext(self, frame, manifest) if trusted else None,
            # SANDBOX 档:ctx 经 syscall 通道跨进程构造(docs/CODE-ORCHESTRATION.md §2.2),
            # 与 TRUSTED 档契约逐字一致——同一 handler 两档运行行为等价
            dispatch_fn=None if trusted else self._syscall_dispatcher(frame, manifest),
            limits=exec_limits(manifest_timeout=timeout),  # §9.1 三级取紧(调用方缺省)
        )
        await self.signals.emit(self._sig(PRE_LOGIC_EXEC, frame, {"trust": trust}))
        result = await kernel.execute(req)
        await self.signals.emit(
            self._sig(POST_LOGIC_EXEC, frame, {"trust": trust, "ok": result.error is None})
        )
        if result.error is not None:
            raise ToolDispatchError(
                f"code 技能 {manifest.name} 执行失败({result.error.kind.value}):"
                f" {result.error.message}",
                hint=getattr(result.error, "hint", ""),
            )
        if manifest.outputs:
            try:
                jsonschema.validate(result.value, manifest.outputs)
            except jsonschema.ValidationError as e:
                raise OutputValidationError(
                    f"code 技能 {manifest.name} 返回值不合 outputs schema: {e.message}"
                ) from e
        return result.value

    # ------------------------------------------------------------------
    # §3.1 步骤 6:分发(工具 vs 子技能,§3.3)
    # ------------------------------------------------------------------

    async def _dispatch_call(
        self, call: ToolCall, frame: SkillFrame, manifest: SkillManifest
    ) -> dict[str, Any]:
        if call.name.startswith("skill.") or call.name.startswith("skill__"):
            return await self._invoke_skill(call, frame, manifest)
        if call.name == ORCHESTRATE_TOOL:
            return await self._run_orchestration(call, frame, manifest)
        if call.name not in manifest.permissions.tools:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.PERMISSION_DENIED,
                    f"工具 {call.name} 不在技能 {manifest.name} 的 tools 白名单",
                ),
            }
        if call.name == ASK_SUPERVISOR_TOOL:
            # 内核拦截式伪工具(docs/SUPERVISOR.md §2.1,同 python_orchestrate 先例):
            # 白名单照常先查,再放行到 supervisor 通道
            return await self._ask_supervisor(call, frame)
        verdicts = await self.signals.emit(
            self._sig(PRE_TOOL_CALL, frame, {"tool": call.name, "args": dict(call.args)})
        )
        verdict = self._arbitrate_pre(verdicts)
        if isinstance(verdict, Veto):
            # 跳过分发:Veto 理由作为错误观察回写帧上下文(§5.2;不发 post:tool.call)
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(ToolErrorKind.VETOED, verdict.reason),
            }
        if isinstance(verdict, Stop):
            raise RunAborted(verdict.reason)
        if isinstance(verdict, Pause):
            # 可恢复挂起(docs/DESIGN.md :940):理由原样上抛,Run 边界落 PAUSED
            raise RunPaused(verdict.reason)
        if isinstance(verdict, Modify):
            call.args.update(verdict.patch)  # pre 可改参数(§5.1)
        # WS2 tool-confirm 闸门(docs/DESIGN.md §8.2 两阶段语义;docs/SUPERVISOR.md §10):
        # spec.confirm 或(HumanApproval 策略在场且 EXEC 档)→ 挂起等人工裁决。
        # 位置在白名单与 pre:tool.call 仲裁(含 Modify 改参)之后、分发之前——
        # 人审的就是要执行的(同 docs/ESCALATION.md §3 原则 1);fail-closed 靠显式
        # 返回错误载荷,不抛异常(帧 loop 的分发兜底会把异常吞成 INTERNAL)
        spec = self._tool_spec(call.name)
        if spec is not None and (
            spec.confirm
            or (self.human_approval is not None and spec.permission is Permission.EXEC)
        ):
            denied = await self._confirm_tool_call(call, frame, spec)
            if denied is not None:
                return denied
        result = await self.tools.dispatch(
            call,
            ToolDispatchContext(
                frame=frame,
                allowed_tools=manifest.permissions.tools,
                tool_policy=self.config.tool_policy,
                # §W0-1:RunConfig 的 workdir/read_paths 传入分发层(缺省 None → 每 run 临时目录)
                workdir=Path(self.config.workdir) if self.config.workdir else None,
                read_paths=[Path(p) for p in self.config.read_paths],
            ),
        )
        await self.signals.emit(
            self._sig(POST_TOOL_CALL, frame, {"tool": call.name, "ok": result.ok})
        )
        return _result_payload(result)

    # ------------------------------------------------------------------
    # docs/SUPERVISOR.md §2:ask_supervisor 伪工具——就地挂起,等本 run 调用方裁决(S1)
    # ------------------------------------------------------------------

    async def _ask_supervisor(self, call: ToolCall, frame: SkillFrame) -> dict[str, Any]:
        """``ask_supervisor`` 分发:pending 落盘 → supervisor.ask → 答案作 tool result。

        **就地挂起**(§2.2):``await supervisor.ask`` 自然阻塞本帧 loop——无需
        YIELD 机制,父帧照常停在自己的 await 点;**run 完成判定**因此天然安全:
        run 只在整棵 run_frame 树返回后才置 DONE,handler 未回答期间状态保持
        RUNNING(§7 runner 行"run 完成判定含'无 pending ask'"由 await 结构满足)。
        """
        if self.supervisor is None:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.NOT_FOUND,
                    "未装配 supervisor handler",
                    hint="用 KernelBuilder.supervisor(handler) 注入调用方通道(§2.3)",
                ),
            }
        question_id = f"q-{uuid.uuid4().hex[:12]}"
        # pending ask 先入 working(checkpoint 随帧序列化,§4;resume 凭此重新提问)
        frame.context.working["_pending_ask"] = {
            "question_id": question_id,
            "call_id": call.id,  # resume 结算时定位未配对的 ask 调用
            "question": str(call.args.get("question") or ""),
            "context": dict(call.args.get("context") or {}),
            "asked_at": time.time(),
        }
        ask_args = dict(call.args)
        ask_args["question_id"] = question_id  # Question 与落盘 pending 同一 id
        outcome = await self.supervisor.ask(frame, ask_args)
        # 仅正常闭环(含超时/兜底)才清 pending;异常(断电/取消)保留,供 resume 重问
        frame.context.working.pop("_pending_ask", None)
        if outcome.get("ok") is False:
            return {"ok": False, "value": None, "error": outcome["error"]}
        return {
            "ok": True,
            "value": {
                "answer": outcome["answer"],
                "decided_by": outcome["decided_by"],
                "question_id": question_id,
            },
            "error": None,
        }

    async def _settle_pending_ask(self, frame: SkillFrame) -> bool:
        """resume 结算 pending ask(§4):重新向调用方提问,结果写为该 call 的 tool result。

        在 ``_settle_unpaired_calls`` 之前调用:pending ask 的 ask 调用不是
        "分发到一半断电",不得落入 interrupted 占位——重新走 ``_ask_supervisor``
        (新 question_id/asked_at 落盘再清),配对原子性天然闭合(§7.4 不变量 2)。
        已配对的(如 stop 时 CancelledError 分支写入的 interrupted 占位)不重问,
        只清标志。返回 True 表示重新提问并结算了一条调用。
        """
        pending = frame.context.working.pop("_pending_ask", None)
        if pending is None:
            return False
        call_id = pending.get("call_id")
        messages = frame.context.messages
        for index, msg in enumerate(messages):
            if msg.role is not Role.ASSISTANT:
                continue
            for call in msg.tool_calls:
                if call.id != call_id or call.name != ASK_SUPERVISOR_TOOL:
                    continue
                if _last_tool_message(messages, index, call.id) is not None:
                    return False  # 已结算:只清 pending 标志
                payload = await self._ask_supervisor(call, frame)
                messages.append(self._tool_message(call, payload))
                return True
        return False

    def _syscall_dispatcher(
        self, frame: SkillFrame, manifest: SkillManifest, stats: dict[str, Any] | None = None
    ) -> Any:
        """构造 syscall 分发回调:沙箱内 ctx 的每次调用回到同一条 ``_dispatch_call`` 闸门。

        脚本以**调用帧的身份**执行——可调集合 = 该帧 manifest 白名单 ∩ RunConfig
        上限,ToolGuard/信号/记账全部沿用,**无权限提升**(docs/CODE-ORCHESTRATION.md §2.3)。
        帧控制面 syscall(kind=``cancel``/``frame_status``,W5-WS1)不是工具分发,
        不进 ``_dispatch_call``:直委托内核读/控视图(``cancel_subtree``/
        ``frame_status_payload``),寻址范围限于本 run 帧树,同样无权限提升。
        §3.4 编排原语(kind=``spawn``/``wait``/``parallel``)直委托内核后台帧
        管线(``spawn_frame``/``wait_frame``/``parallel_invoke``)——白名单/深度/
        升权闸与 invoke 同一条路径;可预见拒绝(白名单/深度/批形态/未知帧)与
        子树取消折叠为脚本可处置的错误观察,RunAborted(含 BudgetExceeded)穿透
        (``_serve_syscalls`` 硬失败边界,§3.2)。
        """
        counter = stats if stats is not None else {"calls": 0, "failed": []}
        limit = (
            manifest.limits.max_tool_calls
            if manifest.limits and manifest.limits.max_tool_calls
            else DEFAULT_MAX_TOOL_CALLS
        )

        async def dispatch(kind: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
            if counter["calls"] >= limit:
                # 限额是内核策略:以结构化错误回脚本(而非断管),脚本可自行收敛(§4.2)
                counter["limit_hit"] = True
                return {
                    "ok": False,
                    "value": None,
                    "error": _error_payload(
                        ToolErrorKind.INVALID_ARGS,
                        f"单次编排的调用上限 {limit} 已用尽(limits.max_tool_calls)",
                        hint="收敛脚本:减少循环次数或先聚合再调用",
                    ),
                }
            counter["calls"] += 1
            # 帧控制面(kind=cancel/frame_status):name 为目标 frame_id,args 带 reason
            if kind == "cancel":
                ids = await self.cancel_subtree(name, str(args.get("reason") or ""))
                return {"ok": True, "value": ids, "error": None}
            if kind == "frame_status":
                return {"ok": True, "value": self.frame_status_payload(name), "error": None}
            # §3.4 编排原语:spawn(name=技能名,args=input)/ wait(name=frame_id)/
            # parallel(args 带 branches/mode/max_concurrency/settle_timeout)
            if kind == "spawn":
                try:
                    fid = await self.spawn_frame(frame, name, dict(args))
                except (SkillLoadError, MaxDepthExceeded) as e:
                    # 白名单/升权拒绝与深度拒绝同形:脚本可预见的预检失败,折叠返回
                    return {
                        "ok": False,
                        "value": None,
                        "error": _error_payload(ToolErrorKind.PERMISSION_DENIED, str(e)),
                    }
                except RunAborted:
                    raise  # 硬失败穿透(_serve_syscalls 停服并弹到 Run 边界,§3.2)
                except Exception as e:  # noqa: BLE001 — 其余失败折叠为脚本可处置的错误观察
                    return {
                        "ok": False,
                        "value": None,
                        "error": _error_payload(ToolErrorKind.INTERNAL, f"{type(e).__name__}: {e}"),
                    }
                return {"ok": True, "value": fid, "error": None}
            if kind == "wait":
                try:
                    value = await self.wait_frame(name)
                except SubtreeCancelled as e:
                    # 子树取消是分支终态不是 run 中止:脚本可捕获继续结算
                    # (对齐 TRUSTED 档 race_first 的 catch 模式,§3.4)
                    return {"ok": False, "value": None, "error": _cancelled_payload(str(e))}
                except SkillLoadError as e:
                    return {
                        "ok": False,
                        "value": None,
                        "error": _error_payload(ToolErrorKind.INVALID_ARGS, str(e)),
                    }
                except (RunAborted, MaxDepthExceeded):
                    raise  # 硬失败穿透(§3.2)
                except Exception as e:  # noqa: BLE001 — 子帧失败原样回脚本(TRUSTED wait 上抛语义)
                    return {
                        "ok": False,
                        "value": None,
                        "error": _error_payload(ToolErrorKind.INTERNAL, f"{type(e).__name__}: {e}"),
                    }
                return {"ok": True, "value": value, "error": None}
            if kind == "parallel":
                branches = args.get("branches")
                if not isinstance(branches, list):
                    return {
                        "ok": False,
                        "value": None,
                        "error": _error_payload(
                            ToolErrorKind.INVALID_ARGS,
                            f"parallel 的 branches 应为 list,得到: {type(branches).__name__}",
                        ),
                    }
                try:
                    results = await self.parallel_invoke(
                        frame,
                        branches,
                        mode=args.get("mode", "all_settled"),
                        max_concurrency=args.get("max_concurrency"),
                        settle_timeout=args.get("settle_timeout", 5.0),
                    )
                except SkillLoadError as e:
                    # 批形态错/白名单外分支(预检):与 spawn 白名单拒绝同形折叠
                    return {
                        "ok": False,
                        "value": None,
                        "error": _error_payload(ToolErrorKind.PERMISSION_DENIED, str(e)),
                    }
                # MaxDepthExceeded/RunAborted/BudgetExceeded 不在此折叠(§3.2 穿透)
                return {"ok": True, "value": results, "error": None}
            target = f"skill.{name}" if kind == "skill" else name
            payload = await self._dispatch_call(
                ToolCall(id=f"{frame.frame_id[:8]}#{counter['calls']}", name=target, args=dict(args)),
                frame,
                manifest,
            )
            if not payload.get("ok"):
                counter["failed"].append(
                    {"name": name, "error": (payload.get("error") or {}).get("kind")}
                )
            return payload

        return dispatch

    async def _run_orchestration(
        self, call: ToolCall, frame: SkillFrame, manifest: SkillManifest
    ) -> dict[str, Any]:
        """``python_orchestrate``(docs/CODE-ORCHESTRATION.md):沙箱脚本 + 工具系统调用。

        脚本以**调用帧的身份**在 SANDBOX 执行;脚本内 ``ctx.call_tool``/``ctx.invoke``
        经 syscall 通道陷入内核,由 ``_dispatch_call`` 代为分发——白名单、ToolGuard
        veto、信号、记账全部沿用,**无权限提升**(可调集合 = 本帧 manifest 白名单)。
        中间变量留在沙箱,只有脚本的 ``result`` 回到帧上下文(§1.1 token 经济)。
        """
        if not self.config.orchestrate:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.PERMISSION_DENIED,
                    f"{ORCHESTRATE_TOOL} 未启用(Fail-Safe Default:需配置 [tools] "
                    f"python_orchestrate = true)",
                ),
            }
        if ORCHESTRATE_TOOL not in manifest.permissions.tools:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.PERMISSION_DENIED,
                    f"{ORCHESTRATE_TOOL} 不在技能 {manifest.name} 的 tools 白名单",
                ),
            }
        source = str(call.args.get("code") or "")
        if not source.strip():
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(ToolErrorKind.INVALID_ARGS, "code 参数为空"),
            }
        if self.logic is None:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(ToolErrorKind.INTERNAL, "未装配 Logic Kernel(§9)"),
            }
        kernel = self.logic.route_sandbox() if hasattr(self.logic, "route_sandbox") else None
        if kernel is None:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.INTERNAL, "未装配 SANDBOX Logic Kernel(编排脚本强制沙箱,§9.2)"
                ),
            }

        stats: dict[str, Any] = {"calls": 0, "failed": []}
        dispatch = self._syscall_dispatcher(frame, manifest, stats)

        timeout = call.args.get("timeout")
        try:
            wall = float(timeout) if timeout else None
        except (TypeError, ValueError):
            # 模型生成的 timeout 可能是非数值(如 "abc"),折为错误观察而非炸 run
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.INVALID_ARGS, f"timeout 参数无法解析为数值: {timeout!r}"
                ),
            }
        req = ExecRequest(
            source=source,
            args={},
            ctx=None,  # 沙箱侧 ctx 由驱动脚本经 syscall 通道构造(§2.2)
            dispatch_fn=dispatch,
            limits=exec_limits(
                manifest_timeout=(
                    manifest.limits.timeout if manifest.limits and manifest.limits.timeout else None
                ),
                caller_timeout=wall,
            ),  # §9.1 三级取紧:调用方不能放松 skill 声明的上限;未设字段回填内核默认
        )
        verdicts = await self.signals.emit(
            self._sig(
                PRE_LOGIC_EXEC,
                frame,
                {"trust": kernel.trust.value, "source": source, "language": "python",
                 "via": "orchestrate"},
            )
        )
        verdict = self._arbitrate_pre(verdicts)
        if isinstance(verdict, Veto):  # CodeScanner 等的否决点(§9.5)
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(ToolErrorKind.VETOED, verdict.reason),
            }
        if isinstance(verdict, Stop):
            raise RunAborted(verdict.reason)
        result = await kernel.execute(req)
        await self.signals.emit(
            self._sig(
                POST_LOGIC_EXEC,
                frame,
                {"trust": kernel.trust.value, "ok": result.error is None,
                 "via": "orchestrate", "calls": stats["calls"]},
            )
        )
        if result.error is not None:
            kind = (
                ToolErrorKind.TIMEOUT
                if result.error.kind is LogicError.LIMIT_EXCEEDED
                else ToolErrorKind.INTERNAL
            )
            # 崩溃/超限:已执行的 syscall 副作用已发生,回报清单供模型决定补偿(§4.3)
            return {
                "ok": False,
                "value": {"calls": stats["calls"], "failed": stats["failed"],
                          "limit_hit": bool(stats.get("limit_hit"))},
                "error": _error_payload(
                    kind,
                    f"编排脚本执行失败({result.error.kind.value}): {result.error.message}",
                    hint=(result.stderr or "")[-500:] or None,
                ),
            }
        return {
            "ok": True,
            "value": {
                "result": result.value,
                "calls": stats["calls"],
                "failed": stats["failed"],
                "limit_hit": bool(stats.get("limit_hit")),
                "stdout": result.stdout[-2000:] if result.stdout else "",
            },
            "error": None,
        }

    async def _invoke_skill(
        self, call: ToolCall, frame: SkillFrame, manifest: SkillManifest
    ) -> dict[str, Any]:
        """分发子技能调用:白名单 → 深度兜底 → 升权闸 → 压栈跑子帧 → 结果折叠回父帧。

        升权溯源(WS1,docs/ESCALATION.md §5):本次调用过了升权闸(目标档高于
        调用方档且被放行)时,成功 payload 加 ``"escalated": "{name}@{version}"``
        键(version 缺省/空只写 name)——设计原文是 TOOL content 文本前缀,偏差为
        payload 键:content 是 JSON,前缀会破坏 resume 结算的 ``json.loads``
        (kernel/checkpoint.py ``_payload_ok``/``_settle_unpaired_calls``);
        同档移动/降权直通不标。升权派生的子帧另在 ``context.working`` 落
        ``"_escalated_from": 父帧档``(working 随 checkpoint 序列化,且只被
        ContextManager 以具名键读取,不进 build 的消息面,不外泄进上下文组装)。
        """
        if call.name.startswith("skill."):
            name = call.name[len("skill."):]
        elif call.name.startswith("skill__"):
            name = call.name[len("skill__"):]
        else:
            name = call.name
        if name not in manifest.permissions.skills:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.PERMISSION_DENIED,
                    f"子技能 {name} 不在技能 {manifest.name} 的 skills 白名单",
                ),
            }
        await self.signals.emit(self._sig(PRE_SKILL_INVOKE, frame, {"skill": name}))
        if frame.depth + 1 > self.config.max_depth:
            raise MaxDepthExceeded(
                f"调用子技能 {name} 将达到 depth={frame.depth + 1},"
                f"超过 max_depth={self.config.max_depth}"
            )
        target = self.skills.get(SkillRef(name=name))
        target_tier = derive_skill_tier(target.manifest, self.tools, self.skills)
        escalated = ""  # 升权溯源标记:过闸且被放行后置 "{name}@{version}"(见 docstring)
        if tier_exceeds(target_tier, frame.tier):
            # 升权闸门(docs/ESCALATION.md §3):白名单检查后、make_frame 之前。
            # 原则 1 先校验后确认:参数不合被调方 inputs schema → INVALID_ARGS
            # 错误观察,**不产生确认请求**(审的就是要执行的,不存在审一套跑一套)
            try:
                jsonschema.validate(call.args, target.manifest.inputs)
            except jsonschema.ValidationError as e:
                return {
                    "ok": False,
                    "value": None,
                    "error": _error_payload(
                        ToolErrorKind.INVALID_ARGS,
                        f"子技能 {name} 的调用参数不合 inputs schema: {e.message}",
                    ),
                }
            denied = await self._confirm_escalation(call, frame, name, target.manifest, target_tier)
            if denied is not None:
                return denied
            escalated = _escalated_marker(name, target.manifest)
        try:
            child = self.skills.make_frame(
                SkillCall(name=name, args=dict(call.args), call_id=call.id), frame
            )
        except SkillLoadError as e:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(ToolErrorKind.INVALID_ARGS, str(e)),
            }
        # 登记触发子帧的父帧调用 id(checkpoint 恢复按 call_id 配对结算,§10.2;
        # LogicContext.invoke 经 _dispatch_call 委托到此,同一路径覆盖)
        child.call_id = call.id
        # 子帧继承被调 skill 推导档(§2.2:它在高层环境里跑,再调同档是同层移动)
        child.tier = target_tier
        if escalated:
            # 升权派生帧标记(父档快照;审计面板据此还原"此帧由升权创建")
            child.context.working["_escalated_from"] = frame.tier
        try:
            value = await self.run_frame(child)
        except (MaxDepthExceeded, RunAborted):
            raise  # 硬失败不可被帧吞掉,沿栈上抛(§3.2)
        except SubtreeCancelled as e:
            # 子树取消(§5.2 cancel_frame):被调子帧所在子树已终态——折叠为父帧的
            # interrupted 错误观察(分支取消不杀 run,恢复策略交父帧/模型);若父帧
            # 自身也在取消子树内,它自己的帧级 stop 标志会在下个 safe point 终结它
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.INTERRUPTED,
                    f"子技能 {name} 所在子树已取消: {e}",
                ),
            }
        except Exception as e:  # noqa: BLE001 — 帧边界故意兜底:任意子帧失败折叠为父帧错误观察(§3.2)
            _log.warning("子技能 %s 帧失败,转为父帧错误观察: %r", name, e)
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.INTERNAL,
                    f"子技能 {name} 失败: {type(e).__name__}: {e}",
                    # 结构化错误的最后一跳:SkillError 的 hint 续传到父帧观察(§W0-3)
                    hint=getattr(e, "hint", ""),
                ),
            }
        await self.signals.emit(self._sig(POST_SKILL_INVOKE, frame, {"skill": name, "ok": True}))
        payload: dict[str, Any] = {"ok": True, "value": value, "error": None}
        if escalated:
            # [ESCALATED] 溯源标记 = payload 键(非文本前缀,见 docstring 偏差说明)
            payload["escalated"] = escalated
        return payload

    # ------------------------------------------------------------------
    # docs/ESCALATION.md §3:升权确认——内核判定升权后强制挂起等裁决(原则 2,
    # 确认不由 LLM 发起);闭环复用 supervisor 通道(同 _ask_supervisor 先例)
    # ------------------------------------------------------------------

    async def _confirm_escalation(
        self,
        call: ToolCall,
        frame: SkillFrame,
        name: str,
        target_manifest: SkillManifest,
        target_tier: str,
    ) -> dict[str, Any] | None:
        """升权确认:Grant 命中直接放行;否则 pending 落盘 → supervisor 裁决 → 三态。

        - Grant 命中(§4 消费点:run 档 approve-run 仅 L2,once 档消费即焚)→
          发 post:skill.escalate(decision="grant-run")放行,不问第二次;
        - approve-once → 放行本次(不登记 Grant,重试必再撞闸,防"磨到批准");
          approve-run(仅 L2 提供此选项,L3 永不批量授权——三档模型最硬的规则)
          → 登记 run 档 Grant(tier 快照,随 run 死亡,checkpoint 持久)后放行;
        - deny → 父帧 PERMISSION_DENIED 错误观察(与白名单拒绝同形)。
        无 supervisor 通道且无 Grant 命中时 fail-closed:升权无人可审等于无人把关。
        """
        grant = self._consume_grant(frame, name, target_tier)
        if grant is not None:
            # Grant 命中(§4):无确认请求,故无 pre;post 记 decision="grant-run"
            await self.signals.emit(
                self._sig(
                    POST_SKILL_ESCALATE,
                    frame,
                    {
                        "skill": name,
                        "tier": target_tier,
                        "decision": "grant-run",
                        "decided_by": grant.decided_by,
                        "scope": grant.scope,
                    },
                )
            )
            return None
        if self.supervisor is None:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.PERMISSION_DENIED,
                    f"升权调用 {name}({frame.tier} → {target_tier})需要人审,"
                    "但未装配 supervisor 确认通道",
                    hint="用 KernelBuilder.supervisor(handler) 注入调用方通道(docs/SUPERVISOR.md §2.3)",
                ),
            }
        # §3 原则 2:approve-run 仅 L2 提供(本 run 内同 skill 后续调用放行);
        # L3 不可逆操作每一次都必须单独过人眼,options 里永不出现 approve-run
        options = (
            ["approve-once", "approve-run", "deny"]
            if target_tier == TIER_REVERSIBLE
            else ["approve-once", "deny"]
        )
        # 数据域面(确认卡片,D4):目标白名单工具的 data_domains 浅层并集(保序去重,
        # 与 derive_tools_tier 同口径——不沿 skills 递归);敏感子集走 registry 的
        # [data] policy 判定(未 bind → 空)
        domains: list[str] = []
        for tool_name in target_manifest.permissions.tools:
            tool_spec = self._tool_spec(tool_name)
            if tool_spec is None:
                continue  # 未注册/伪工具无声明可并入(未注册由分发层自行报错)
            for d in tool_spec.data_domains:
                if d not in domains:
                    domains.append(d)
        request = EscalationRequest(
            question_id=f"esc-{uuid.uuid4().hex[:12]}",
            run_id=frame.run_id,
            frame_id=frame.frame_id,
            skill=name,
            tier=target_tier,
            params=dict(call.args),
            requested={
                "tools": list(target_manifest.permissions.tools),
                "skills": list(target_manifest.permissions.skills),
            },
            reason_hint=f"{frame.tier} → {target_tier}",
            domains=domains,
            sensitive=self._sensitive_domains(domains),
            options=options,
        )
        # pending 升权入 working(checkpoint 随帧序列化;resume 凭此重走闸门,§3 原则 3)
        frame.context.working["_pending_escalation"] = request.to_pending(call.id)
        await self.signals.emit(
            self._sig(
                PRE_SKILL_ESCALATE,
                frame,
                {
                    "skill": name,
                    "tier": target_tier,
                    "params": dict(call.args),
                    "requested": request.requested,
                },
            )
        )
        outcome = await self.supervisor.ask(frame, request.to_supervisor_args(str(frame.skill.name)))
        # 仅正常闭环(含超时/兜底)才清 pending;异常(断电/取消)保留,供 resume 重问
        frame.context.working.pop("_pending_escalation", None)
        if outcome.get("ok") is False:
            return {"ok": False, "value": None, "error": outcome["error"]}
        answer = str(outcome["answer"])
        decided_by = str(outcome["decided_by"])
        if answer in ("approve-once", "approve-run"):
            scope = "run" if answer == "approve-run" else "once"
            if scope == "run":
                # 登记 run 档 Grant(§4):批准时的推导档快照;approve-once 不登记,
                # 下次同调用必须重新过人眼。question_id/frame_id 落台账(批准↔请求
                # 配对与发起帧溯源,审计面板用;checkpoint 落盘自动带上)
                self._register_run_grant(
                    frame,
                    Grant(
                        skill=name,
                        tier=target_tier,
                        scope="run",
                        decided_by=decided_by,
                        decided_at=time.time(),
                        question_id=request.question_id,
                        frame_id=frame.frame_id,
                    ),
                )
            await self.signals.emit(
                self._sig(
                    POST_SKILL_ESCALATE,
                    frame,
                    {
                        "skill": name,
                        "tier": target_tier,
                        "decision": answer,
                        "decided_by": decided_by,
                        "scope": scope,
                    },
                )
            )
            return None
        await self.signals.emit(
            self._sig(
                POST_SKILL_ESCALATE,
                frame,
                {
                    "skill": name,
                    "tier": target_tier,
                    "decision": "deny",
                    "decided_by": decided_by,
                    "scope": None,
                },
            )
        )
        await self.signals.emit(
            self._sig(
                SKILL_ESCALATION_DENIED,
                frame,
                {"skill": name, "tier": target_tier, "decided_by": decided_by},
            )
        )
        return {
            "ok": False,
            "value": None,
            "error": _error_payload(
                ToolErrorKind.PERMISSION_DENIED,
                f"升权调用 {name} 被拒绝(decided_by: {decided_by})",
                hint="可改道完成或请求用户改用高档技能直接启动",
            ),
        }

    def _consume_grant(self, frame: SkillFrame, name: str, target_tier: str) -> Grant | None:
        """Grant 消费点(§4):返回命中的 Grant(供信号载荷),无命中 → None。

        run 档双保险:即使有人手工构造 Grant/答案,也只对 ``reversible`` 目标档
        生效——L3 永不批量授权。once 档消费即焚(现状不产生 once 档 Grant:
        approve-once 不登记;此路径兜崩溃残留与手工构造)。
        """
        run = self._runs.get(frame.run_id)
        grants = getattr(run, "grants", None)
        if not grants:
            return None
        for grant in list(grants):
            if grant.skill != name:
                continue
            if grant.scope == "run":
                if target_tier == TIER_REVERSIBLE and grant.tier == TIER_REVERSIBLE:
                    return grant
                continue
            grants.remove(grant)  # scope="once":消费即焚
            return grant
        return None

    def _register_run_grant(self, frame: SkillFrame, grant: Grant) -> None:
        """登记 run 档 Grant(§4:存放于内核 Run,随 checkpoint 序列化,随 run 死亡)。"""
        run = self._runs.get(frame.run_id)
        if run is not None:
            run.grants.append(grant)

    async def _settle_pending_escalation(self, frame: SkillFrame) -> bool:
        """resume 结算 pending 升权(§3 原则 3):重走升权闸门,结果写为该 call 的 tool result。

        在 ``_settle_unpaired_calls`` 之前调用(与 ``_settle_pending_ask`` 同旨):
        挂起在确认闸门的 skill 调用不是"分发到一半断电",不得落入 interrupted
        占位——重入 ``_invoke_skill`` 会重新判定升权并再次挂起等裁决,批准后
        当场补建子帧跑完,配对原子性天然闭合。已配对的只清标志。
        """
        pending = frame.context.working.pop("_pending_escalation", None)
        if pending is None:
            return False
        call_id = pending.get("call_id")
        messages = frame.context.messages
        for index, msg in enumerate(messages):
            if msg.role is not Role.ASSISTANT:
                continue
            for call in msg.tool_calls:
                if call.id != call_id:
                    continue
                if not (call.name.startswith("skill.") or call.name.startswith("skill__")):
                    continue
                if _last_tool_message(messages, index, call.id) is not None:
                    return False  # 已结算:只清 pending 标志
                manifest = self.skills.get(frame.skill).manifest
                payload = await self._invoke_skill(call, frame, manifest)
                messages.append(self._tool_message(call, payload))
                return True
        return False

    # ------------------------------------------------------------------
    # docs/DESIGN.md §8.2 + docs/SUPERVISOR.md §10:tool-confirm 两阶段闸门(WS2)——
    # 不可幂等/高危工具(spec.confirm;HumanApproval 策略在场的 EXEC 档)强制挂起
    # 等人工裁决;闭环复用 supervisor 通道(同 _confirm_escalation 先例)
    # ------------------------------------------------------------------

    def _tool_spec(self, name: str) -> ToolSpec | None:
        """查工具 spec(闸门触发判定用);注册表不持该工具/不具 get 接口 → None
        (分发层自行报未注册错误,闸门不越俎代庖)。"""
        get = getattr(self.tools, "get", None)
        if get is None:
            return None
        try:
            return getattr(get(name), "spec", None)
        except KeyError:
            return None

    def _sensitive_domains(self, domains: list[str]) -> list[str]:
        """声明数据域中被 [data] policy 判 confidential 的子集(确认卡片"敏感"标注)。

        registry 不持判定口(自定义 registry 嵌入方)或无声明域 → [];policy 未
        bind 时 registry 侧同样返 [](D1 语义,数据层未启用)。
        """
        if not domains:
            return []
        judge = getattr(self.tools, "sensitive_domains", None)
        if judge is None:
            return []
        return list(judge(domains))

    async def _confirm_tool_call(
        self, call: ToolCall, frame: SkillFrame, spec: ToolSpec
    ) -> dict[str, Any] | None:
        """tool-confirm 闸门:Grant 命中直接放行;否则 pending 落盘 → supervisor 裁决 → 三态。

        与 ``_confirm_escalation`` 同构(确认由内核发起,不由 LLM):

        - approve-run 短路:run 档 Grant 命中(粒度按工具名,复用 docs/ESCALATION.md
          §4 台账与 ``_consume_grant`` 双保险:run 档只对 reversible 生效)→ 放行;
        - approve-once → 放行本次(不登记 Grant,重试必再撞闸,防"磨到批准");
          approve-run(仅 reversible 档提供,irreversible/none 每一次都必须过
          人眼——同 L3 永不批量授权)→ 登记 run 档 Grant(tier 快照,随 run 死亡,
          checkpoint 持久)后放行;
        - deny → PERMISSION_DENIED 错误观察(与白名单拒绝同形,模型看到"人拒绝了");
        - 无 supervisor 通道且无 Grant 命中 → fail-closed 拒绝(同升权先例:
          高危操作无人可审等于无人把关)。

        偏差说明(WS2 最小实现):内核批准即 confirmation token——批准后在同一
        调用上放行;不做字面"dry run 返回 token 注入 args 二次调用"(§8.2 契约
        未定义 token 格式)。
        """
        tier = derive_side_effect(spec)
        if self._consume_grant(frame, call.name, tier) is not None:
            return None  # approve-run 短路:本 run 内同工具已获批量授权
        if self.supervisor is None:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.PERMISSION_DENIED,
                    f"工具 {call.name} 需要人工确认,但未装配 supervisor 确认通道",
                    hint="用 KernelBuilder.supervisor(handler) 注入调用方通道(docs/SUPERVISOR.md §2.3)",
                ),
            }
        options = (
            ["approve-once", "approve-run", "deny"]
            if tier == TIER_REVERSIBLE
            else ["approve-once", "deny"]
        )
        question_id = f"tc-{uuid.uuid4().hex[:12]}"
        # 数据域面(确认卡片,D4):spec 声明原样直通;敏感子集与升权同一判定口径
        domains = list(spec.data_domains)
        # pending 落盘(checkpoint 随帧序列化;resume 凭 call_id 重走本闸门,§10.2)
        frame.context.working["_pending_tool_confirm"] = {
            "kind": "tool-confirm",
            "question_id": question_id,
            "call_id": call.id,
            "tool": call.name,
            "args": dict(call.args),
            "side_effect": tier,
            "options": list(options),
            "asked_at": time.time(),
        }
        outcome = await self.supervisor.ask(
            frame,
            {
                "question_id": question_id,
                # kind="tool-confirm":与 question/escalation 在信号流与收件箱里可区分
                # (additive 自由字符串,不加新信号名)
                "kind": "tool-confirm",
                "question": (
                    f"工具确认:{frame.skill.name} 帧请求执行 {call.name}"
                    f"(副作用档 {tier}),批准本次执行?"
                ),
                "context": {
                    "tool": call.name,
                    "args": dict(call.args),
                    "side_effect": tier,
                    "domains": domains,
                    "sensitive": self._sensitive_domains(domains),
                },
                "options": list(options),
                "urgency": "high" if tier == TIER_IRREVERSIBLE else "normal",
            },
        )
        # 仅正常闭环(含超时/兜底)才清 pending;异常(断电/取消)保留,供 resume 重问
        frame.context.working.pop("_pending_tool_confirm", None)
        if outcome.get("ok") is False:
            return {"ok": False, "value": None, "error": outcome["error"]}
        answer = str(outcome["answer"])
        decided_by = str(outcome["decided_by"])
        if answer in ("approve-once", "approve-run"):
            if answer == "approve-run":
                # 登记 run 档 Grant(复用升权台账:Grant.skill 字段承载工具名);
                # approve-once 不登记,下次同调用必须重新过人眼;question_id/frame_id
                # 与升权台账同义(批准↔请求配对与发起帧溯源,审计面板用)
                self._register_run_grant(
                    frame,
                    Grant(
                        skill=call.name,
                        tier=tier,
                        scope="run",
                        decided_by=decided_by,
                        decided_at=time.time(),
                        question_id=question_id,
                        frame_id=frame.frame_id,
                    ),
                )
            return None
        return {
            "ok": False,
            "value": None,
            "error": _error_payload(
                ToolErrorKind.PERMISSION_DENIED,
                f"工具 {call.name} 的执行被拒绝(decided_by: {decided_by})",
                hint="可改道完成,或请求用户批准后重试",
            ),
        }

    async def _settle_pending_tool_confirm(self, frame: SkillFrame) -> bool:
        """resume 结算 pending 工具确认(WS2):重走 tool-confirm 闸门(清标志重问)。

        与 ``_settle_pending_escalation`` 同旨,在 ``_settle_unpaired_calls``
        之前调用:挂起在确认闸门的工具调用不是"分发到一半断电",不得落入
        interrupted 占位——重入 ``_dispatch_call`` 会再次挂起等裁决,批准后当场
        分发,配对原子性闭合。已配对的只清标志。编排 syscall 路径的挂起(call_id
        是沙箱合成 id,不在消息流里)在此只清标志:resume 后编排调用本体落入
        interrupted 占位,由 LLM 重发编排时自然重过闸门(同 spawn 先例)。
        """
        pending = frame.context.working.pop("_pending_tool_confirm", None)
        if pending is None:
            return False
        call_id = pending.get("call_id")
        messages = frame.context.messages
        for index, msg in enumerate(messages):
            if msg.role is not Role.ASSISTANT:
                continue
            for call in msg.tool_calls:
                if call.id != call_id:
                    continue
                if call.name.startswith("skill.") or call.name.startswith("skill__"):
                    continue  # skill 调用归 _settle_pending_escalation
                if call.name in (ORCHESTRATE_TOOL, ASK_SUPERVISOR_TOOL):
                    continue  # 伪工具各有结算通道,不归本闸门
                if _last_tool_message(messages, index, call.id) is not None:
                    return False  # 已结算:只清 pending 标志
                manifest = self.skills.get(frame.skill).manifest
                payload = await self._dispatch_call(call, frame, manifest)
                messages.append(self._tool_message(call, payload))
                return True
        return False

    # ------------------------------------------------------------------
    # §3.4 spawn 后台帧:父帧不挂起,子帧独立预算后台运行;join 退化为读终态
    # ------------------------------------------------------------------

    def _spawn_whitelist_check(self, parent: SkillFrame, skill: str) -> None:
        """spawn 管线首段:白名单检查(parallel_invoke 逐分支复用;拒绝抛 SkillLoadError)。"""
        parent_manifest = self.skills.get(parent.skill).manifest
        if skill not in parent_manifest.permissions.skills:
            raise SkillLoadError(
                f"子技能 {skill} 不在技能 {parent_manifest.name} 的 skills 白名单"
            )

    async def _spawn_gate(self, parent: SkillFrame, skill: str, input: dict[str, Any]) -> tuple[str, str]:
        """spawn 管线前置段:PRE 信号 → 深度兜底 → 升权闸,返回
        ``(被调 skill 推导档, 升权溯源标记)``(未过闸时标记为空串)。

        深度超限抛 MaxDepthExceeded(硬失败);升权确认 await 发生在本调用点,
        拒绝/参数不合抛 SkillLoadError(与 spawn_frame 同形)。parallel_invoke
        起批前的逐分支串行预检复用本段。
        """
        await self.signals.emit(
            self._sig(PRE_SKILL_INVOKE, parent, {"skill": skill, "background": True})
        )
        if parent.depth + 1 > self.config.max_depth:
            raise MaxDepthExceeded(
                f"调用子技能 {skill} 将达到 depth={parent.depth + 1},"
                f"超过 max_depth={self.config.max_depth}"
            )
        target = self.skills.get(SkillRef(name=skill))
        target_tier = derive_skill_tier(target.manifest, self.tools, self.skills)
        escalated = ""  # 升权溯源标记(与 _invoke_skill 同旨):过闸且被放行后置位
        if tier_exceeds(target_tier, parent.tier):
            # 升权闸(docs/ESCALATION.md §3;E2 补 E1 遗留的绕道口子):语义与
            # _invoke_skill 一致——先校验后确认(失败不发确认),确认等待发生在
            # spawn 调用点本身(await 裁决后才 create_task),父帧不挂起的设计不变
            try:
                jsonschema.validate(input, target.manifest.inputs)
            except jsonschema.ValidationError as e:
                raise SkillLoadError(
                    f"子技能 {skill} 的调用参数不合 inputs schema: {e.message}"
                ) from e
            denied = await self._confirm_escalation(
                # spawn 由 code 技能发起,无对应的 LLM tool_call;合成 id 仅供
                # pending 落盘(resume 时 code 帧整体重跑,会重新走到本闸门)
                ToolCall(id=f"spawn-{uuid.uuid4().hex[:8]}", name=f"skill.{skill}", args=dict(input)),
                parent,
                skill,
                target.manifest,
                target_tier,
            )
            if denied is not None:
                # spawn 无 tool result 观察通道(不走 LLM 分发),与白名单拒绝同形上抛
                raise SkillLoadError((denied.get("error") or {}).get("message") or f"升权调用 {skill} 被拒绝")
            escalated = _escalated_marker(skill, target.manifest)
        return target_tier, escalated

    async def _spawn_register(
        self,
        parent: SkillFrame,
        child: SkillFrame,
        skill: str,
        task: asyncio.Task[Any],
        escalated: str = "",
    ) -> None:
        """spawn 管线登记段(§3.4):入 ``_spawned`` run 分桶 + POST 信号(parallel_invoke 复用)。

        ``escalated``(WS1 升权溯源标记):非空时并入 POST_SKILL_INVOKE payload
        ——spawn 的 wait 返回子帧原始结果值(无 ``{"ok", "value"}`` 信封,加键会
        污染被调方 outputs 契约),spawn 折叠路径的 [ESCALATED] 标记落点因此是
        本信号(payload 键,与 _invoke_skill 的 tool result 标注同值同义)。
        """
        # 按 run 分桶登记(含父帧 id):_release_run 只回收本 run,cancel_subtree
        # 经 parent_frame_id 补帧树压栈前的竞态窗口
        self._spawned.setdefault(child.run_id, {})[child.frame_id] = (parent.frame_id, task)
        # spawn-and-forget 的子帧异常由 StatusBoard 记录(failed);提前 retrieve,
        # 避免无人 wait 时事件循环 "exception was never retrieved" 噪音——
        # wait_frame 的 await 仍会原样上抛,语义不变
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        payload: dict[str, Any] = {"skill": skill, "ok": True, "background": True}
        if escalated:
            payload["escalated"] = escalated
        await self.signals.emit(self._sig(POST_SKILL_INVOKE, parent, payload))

    async def spawn_frame(self, parent: SkillFrame, skill: str, input: dict[str, Any]) -> str:
        """spawn 后台帧(§3.4):白名单/深度/升权检查与 invoke 一致,返回子帧 frame_id。

        子帧经 ``asyncio.create_task`` 后台运行并登记在 ``self._spawned``;
        PRE/POST_SKILL_INVOKE 信号与 invoke 一致,payload 加 ``"background": True``。
        升权(docs/ESCALATION.md §3):构成升权时在本调用点挂起等裁决(父帧不停),
        拒绝/参数不合以 SkillLoadError 上抛(与白名单拒绝同形,交 code 技能处理)。
        升权放行时:子帧 ``context.working["_escalated_from"]`` 落父档快照,
        POST_SKILL_INVOKE payload 带 ``escalated`` 标记(均与 _invoke_skill 同旨)。
        """
        self._spawn_whitelist_check(parent, skill)
        target_tier, escalated = await self._spawn_gate(parent, skill, input)
        child = self.skills.make_frame(SkillCall(name=skill, args=dict(input)), parent)
        # 子帧继承被调 skill 推导档(与 _invoke_skill 同旨),后代调用的升权判定才有基准
        child.tier = target_tier
        if escalated:
            # 升权派生帧标记(与 _invoke_skill 同旨;working 随 checkpoint 序列化)
            child.context.working["_escalated_from"] = parent.tier
        task = asyncio.create_task(self.run_frame(child))
        await self._spawn_register(parent, child, skill, task, escalated=escalated)
        return child.frame_id

    async def wait_frame(self, frame_id: str) -> Any:
        """join 退化为读终态(§3.4):子帧失败把异常原样上抛,交给 code 技能处理。

        子帧被级联取消(cancel_subtree,§5.2)时抛 :class:`SubtreeCancelled`——
        分支终态不是 run 中止,是否捕获恢复由 code 技能决定。
        """
        entry = self._spawned_entry(frame_id)
        if entry is None:
            raise SkillLoadError(f"未知的后台帧 {frame_id}(未 spawn 或已回收)")
        task = entry[1]
        try:
            return await task
        except asyncio.CancelledError:
            if task.cancelled():
                raise SubtreeCancelled(f"后台帧 {frame_id} 所属子树已取消") from None
            raise  # 是等待方自身被取消(等待中的帧被 cancel),原样上抛

    def _spawned_entry(self, frame_id: str) -> tuple[str, asyncio.Task[Any]] | None:
        """跨 run 桶查后台帧登记项(frame_id 全局唯一);不在册返回 None。"""
        for bucket in self._spawned.values():
            entry = bucket.get(frame_id)
            if entry is not None:
                return entry
        return None

    # ------------------------------------------------------------------
    # §3.4 parallel_invoke:fork/join 扇出(第三原语,WS3)——
    # 批内故障隔离 + first-success 锁定 + 级联取消 + 幂等结算一次
    # ------------------------------------------------------------------

    async def parallel_invoke(
        self,
        parent: SkillFrame,
        branches: list[dict[str, Any]],
        *,
        mode: str = "all_settled",
        max_concurrency: int | None = None,
        settle_timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        """parallel_invoke fork/join 扇出(§3.4 第三原语):按分支序返回
        ``[{"ok", "value", "error", "frame_id"}]``。

        - **起批前串行预检**:逐分支复用 spawn 管线前置段
          (``_spawn_whitelist_check`` + ``_spawn_gate``——白名单/PRE 信号/深度
          兜底/升权闸,升权确认 await 发生在本调用点)。批形态错(branches 非
          list、分支缺 skill、depends_on 越界/成环、mode/max_concurrency 非法)
          与白名单外分支 → 抛 SkillLoadError(与 spawn_frame 一致);深度超限抛
          MaxDepthExceeded(硬失败,不折叠);单分支参数不合 schema/升权被拒等
          **分支级**预检失败折叠为该分支 ``ok=False`` 条目(批内故障隔离);
        - **all_settled(默认)**:结构化 gather——普通分支异常折叠为该分支
          ``{"ok": False, "error": {...}}``,永不上抛;``depends_on`` 前置失败 →
          依赖分支标 ``{"ok": False, "error": {"kind": "cancelled"}}`` 且不启动;
        - **first_success**:done-flag 保证只赢一次;胜方锁定后对其余在跑分支
          ``cancel_subtree`` 级联取消,``asyncio.wait(timeout=settle_timeout)``
          等 ack;**幂等结算** = 父侧唯一 join 点——胜方结果取一次、败方丢弃
          (同批完成的胜者也丢弃,返回列表恰一个 ``ok=True``);分支 usage 已
          实时入 run 记账,不可回滚——与"进行中的副作用既成事实"的既定语义
          一致(§3.2/_release_run 同旨);全败 → 返回全部分支错误条目,不挂死;
        - **硬失败边界(§3.2)**:RunAborted/BudgetExceeded/MaxDepthExceeded
          不折叠——先取消所有在跑分支并等 ack,再沿栈上抛炸 run;批自身被取消
          (CancelledError)同样先取消所有分支任务、等 ack、再上抛;
        - **max_concurrency**:asyncio.Semaphore 包住每分支 run_frame;
        - **concurrency_safe 闸**(§3.4"工具须声明 concurrency_safe 才批内并发"
          的首个强制消费):code 分支的 manifest permissions.tools 含未声明
          ``concurrency_safe``/``concurrent_safe``(双拼写任一)的工具(注册表
          查无 spec 按未声明,fail-safe)→ 该分支**串行降级**(占满全部并发
          额度、独占执行窗;fail-safe 不拒绝,日志可观察);prompt 分支豁免
          (帧隔离);cacheable 缓存层不做;
        - **checkpoint/resume**:与 spawn 同形——批不做检查点配对,在跑批断电
          不恢复;code 父帧 resume 时整体重跑、批整体重发,调用方须保证幂等
          (同 spawn 的"resume 重走闸门重发"先例,§10.2);
        - **升权溯源(WS1)**:分支预检过了升权闸且被放行 → 该分支成功条目带
          ``"escalated": "{skill}@{version}"`` payload 键,子帧
          ``working["_escalated_from"]`` 落父档快照(与 _invoke_skill/spawn_frame
          同值同义);同档直通分支不标。
        """
        # ---- 批形态预检(非法即 SkillLoadError,与 spawn_frame 的白名单拒绝同形)----
        if mode not in ("all_settled", "first_success"):
            raise SkillLoadError(f"parallel_invoke 的 mode 非法: {mode!r}")
        if not isinstance(branches, list):
            raise SkillLoadError(
                f"parallel_invoke 的 branches 应为 list,得到: {type(branches).__name__}"
            )
        if max_concurrency is not None and (
            not isinstance(max_concurrency, int)
            or isinstance(max_concurrency, bool)
            or max_concurrency < 1
        ):
            raise SkillLoadError(f"max_concurrency 须为 >= 1 的整数,得到: {max_concurrency!r}")
        specs: list[dict[str, Any]] = []
        for index, branch in enumerate(branches):
            if not isinstance(branch, dict):
                raise SkillLoadError(
                    f"分支 {index} 形态错:应为 dict,得到 {type(branch).__name__}"
                )
            skill = branch.get("skill")
            if not isinstance(skill, str) or not skill:
                raise SkillLoadError(f"分支 {index} 形态错:缺 skill 名")
            raw_input = branch.get("input")
            branch_input = {} if raw_input is None else raw_input
            if not isinstance(branch_input, dict):
                raise SkillLoadError(f"分支 {index} 形态错:input 应为 dict")
            raw_deps = branch.get("depends_on")
            deps = [] if raw_deps is None else raw_deps
            if not isinstance(deps, list) or any(
                not isinstance(d, int)
                or isinstance(d, bool)
                or d < 0
                or d >= len(branches)
                or d == index
                for d in deps
            ):
                raise SkillLoadError(
                    f"分支 {index} 形态错:depends_on 应为 [0, {len(branches)}) 内非自身的下标列表"
                )
            specs.append(
                {
                    "skill": skill,
                    "input": dict(branch_input),
                    "depends_on": list(dict.fromkeys(deps)),
                }
            )
        # depends_on 成环即批形态错(Kahn:逐轮摘入度 0 节点,摘不完则有环)
        indegree = {i: set(spec["depends_on"]) for i, spec in enumerate(specs)}
        resolved = 0
        ready = [i for i, deps in indegree.items() if not deps]
        while ready:
            node = ready.pop()
            resolved += 1
            for i, deps in indegree.items():
                if node in deps:
                    deps.discard(node)
                    if not deps:
                        ready.append(i)
        if resolved != len(specs):
            raise SkillLoadError("parallel_invoke 的 depends_on 存在环")
        if not specs:
            return []

        # ---- 逐分支串行预检 + 统一 make_frame(spawn 管线前置段复用)----
        n = len(specs)
        results: list[dict[str, Any] | None] = [None] * n
        branch_frames: dict[int, SkillFrame] = {}
        branch_escalated: dict[int, str] = {}  # 分支下标 → 升权溯源标记(结算时并入分支条目)
        for index, spec in enumerate(specs):
            skill = spec["skill"]
            # 白名单外 → 抛 SkillLoadError(与 spawn_frame 一致,交 code 技能处理)
            self._spawn_whitelist_check(parent, skill)
            try:
                target_tier, escalated = await self._spawn_gate(parent, skill, spec["input"])
                child = self.skills.make_frame(
                    SkillCall(name=skill, args=dict(spec["input"])), parent
                )
            except SkillLoadError as e:
                # 批内故障隔离:分支级预检失败(参数不合 schema/升权被拒)折叠为该
                # 分支错误条目,不拖垮独立分支与父帧;MaxDepthExceeded 是硬失败,不在此列
                results[index] = {
                    "ok": False,
                    "value": None,
                    "error": _error_payload(ToolErrorKind.INVALID_ARGS, str(e)),
                    "frame_id": None,
                }
                continue
            # 子帧继承被调 skill 推导档(与 spawn_frame 同旨)
            child.tier = target_tier
            if escalated:
                # 升权派生帧标记(与 spawn_frame 同旨;随 working 入 checkpoint)
                child.context.working["_escalated_from"] = parent.tier
                branch_escalated[index] = escalated
            branch_frames[index] = child

        # ---- concurrency_safe 闸(§3.4):code 分支含未声明并发安全的工具 → 串行降级 ----
        cap = max_concurrency if max_concurrency is not None else max(1, len(branch_frames))
        semaphore = asyncio.Semaphore(cap)
        permits: dict[int, int] = {}
        for index, child in branch_frames.items():
            unsafe = self._parallel_unsafe_tool(child)
            if unsafe is None:
                permits[index] = 1
            else:
                # 占满全部并发额度 = 独占执行窗(串行降级):fail-safe 不拒绝,日志可观察
                permits[index] = cap
                _log.warning(
                    "parallel_invoke:分支 %d(%s)的工具 %s 未声明 concurrency_safe,"
                    "该分支串行降级(独占执行窗)",
                    index,
                    child.skill,
                    unsafe,
                )

        # ---- 统一 create_task + 登记 run 分桶(spawn 登记段复用);depends_on 走事件闸 ----
        events = [asyncio.Event() for _ in range(n)]
        for index, entry in enumerate(results):
            if entry is not None:
                events[index].set()  # 预检已折叠的分支:依赖者立即可见终态
        tasks: dict[int, asyncio.Task[Any]] = {}
        for index, child in branch_frames.items():
            task = asyncio.create_task(
                self._parallel_branch(
                    child,
                    semaphore,
                    permits[index],
                    specs[index]["depends_on"],
                    events,
                    results,
                )
            )
            await self._spawn_register(
                parent, child, specs[index]["skill"], task,
                escalated=branch_escalated.get(index, ""),
            )
            tasks[index] = task

        # ---- 父侧唯一 join 点:按完成序结算,结果按分支序落位 ----
        task_index = {task: index for index, task in tasks.items()}
        pending: set[asyncio.Task[Any]] = set(task_index)
        won: int | None = None  # first_success 胜方下标(done-flag,只赢一次)
        settle_deadline: float | None = None  # 胜方锁定后等败方 ack 的截止点
        try:
            while pending:
                wait_timeout = (
                    None
                    if settle_deadline is None
                    else max(0.0, settle_deadline - time.monotonic())
                )
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED, timeout=wait_timeout
                )
                if not done:
                    # settle_timeout:first_success 败方取消 ack 未到——超时后幂等
                    # 结算一次,不再等(§3.4);未 ack 任务弃置,由 _release_run 兜底
                    for task in pending:
                        index = task_index[task]
                        task.cancel()
                        results[index] = self._parallel_cancelled(
                            branch_frames[index], "settle_timeout 内未收到取消 ack,败方结果弃置"
                        )
                        events[index].set()
                    pending = set()
                    break
                for task in done:
                    index = task_index[task]
                    child = branch_frames[index]
                    try:
                        value = task.result()
                    except asyncio.CancelledError:
                        results[index] = self._parallel_cancelled(child, "分支子树已取消")
                    except SubtreeCancelled as e:
                        results[index] = self._parallel_cancelled(child, str(e))
                    except (RunAborted, MaxDepthExceeded):
                        raise  # 硬失败不折叠(§3.2):交外层取消整批后沿栈上抛
                    except Exception as e:  # noqa: BLE001 — 批内故障隔离:普通分支异常折叠(§3.4)
                        results[index] = {
                            "ok": False,
                            "value": None,
                            "error": _error_payload(
                                ToolErrorKind.INTERNAL,
                                f"{type(e).__name__}: {e}",
                                hint=getattr(e, "hint", ""),
                            ),
                            "frame_id": child.frame_id,
                        }
                    else:
                        if value is _DEP_FAILED:
                            results[index] = self._parallel_cancelled(
                                child, "depends_on 前置分支失败,本分支未启动"
                            )
                        elif won is not None:
                            # first_success 同批完成的败方:结果丢弃(幂等结算一次)
                            results[index] = self._parallel_cancelled(
                                child, "first_success 胜方已锁定,结果丢弃"
                            )
                        else:
                            entry: dict[str, Any] = {
                                "ok": True,
                                "value": value,
                                "error": None,
                                "frame_id": child.frame_id,
                            }
                            # [ESCALATED] 溯源标记(payload 键,与 _invoke_skill/spawn 同值同义):
                            # 分支过了升权闸才落键;同档直通分支不标
                            if index in branch_escalated:
                                entry["escalated"] = branch_escalated[index]
                            results[index] = entry
                    events[index].set()
                    if mode == "first_success" and won is None and results[index]["ok"]:
                        won = index
                        # 胜方锁定:其余在跑分支级联取消,settle_timeout 等 ack
                        cancels = [
                            asyncio.create_task(
                                self.cancel_subtree(
                                    branch_frames[loser].frame_id, "first_success 已锁定胜方"
                                )
                            )
                            for loser, loser_task in tasks.items()
                            if loser != index and not loser_task.done()
                        ]
                        if cancels:
                            _, cancels_pending = await asyncio.wait(
                                cancels, timeout=settle_timeout
                            )
                            for cancel_task in cancels_pending:
                                cancel_task.cancel()  # 超时弃等;分支 cancel 已发出
                            for cancel_task in cancels:
                                with contextlib.suppress(asyncio.CancelledError, Exception):
                                    await cancel_task  # retrieve,防 never-retrieved 噪音
                        settle_deadline = time.monotonic() + settle_timeout
        except asyncio.CancelledError:
            # 批自身被取消:先取消所有分支任务、等 ack、再上抛
            await self._parallel_cancel_all(branch_frames, tasks, "parallel_invoke 批被取消")
            raise
        except (RunAborted, MaxDepthExceeded):
            # 硬失败(§3.2):先取消所有在跑分支并等 ack,再沿栈上抛炸 run
            await self._parallel_cancel_all(branch_frames, tasks, "parallel_invoke 批内硬失败")
            raise
        # 结算收尾:所有路径都应已落位;未结算分支兜一个内部错误条目(理论不可达)
        settled: list[dict[str, Any]] = []
        for index, entry in enumerate(results):
            if entry is None:
                entry = {
                    "ok": False,
                    "value": None,
                    "error": _error_payload(ToolErrorKind.INTERNAL, "分支未结算"),
                    "frame_id": (
                        branch_frames[index].frame_id if index in branch_frames else None
                    ),
                }
            settled.append(entry)
        return settled

    async def _parallel_branch(
        self,
        child: SkillFrame,
        semaphore: asyncio.Semaphore,
        permits: int,
        depends_on: list[int],
        events: list[asyncio.Event],
        results: list[dict[str, Any] | None],
    ) -> Any:
        """单分支执行体:depends_on 事件闸 → 并发额度 → run_frame。

        前置分支任一失败 → 返回 ``_DEP_FAILED``(本分支不启动,join 点标 cancelled);
        串行降级分支占满全部额度(独占执行窗,concurrency_safe 闸);额度在 dep 闸
        之后获取——等待前置分支不占并发位。
        """
        for dep in depends_on:
            await events[dep].wait()
        if any(not results[dep]["ok"] for dep in depends_on):
            return _DEP_FAILED
        acquired = 0
        try:
            for _ in range(permits):
                await semaphore.acquire()
                acquired += 1
            return await self.run_frame(child)
        finally:
            for _ in range(acquired):
                semaphore.release()

    def _parallel_unsafe_tool(self, child: SkillFrame) -> str | None:
        """concurrency_safe 闸(§3.4):code 分支的 tools 白名单含未声明
        ``concurrency_safe``/``concurrent_safe``(双拼写任一)的工具 → 返回首个
        未声明工具名(调用方据此串行降级);prompt 分支豁免(帧隔离),返回 None。

        注册表查无 spec 按未声明处理(fail-safe)。
        """
        manifest = self.skills.get(child.skill).manifest
        if manifest.kind is not SkillKind.CODE:
            return None
        for name in manifest.permissions.tools:
            spec = self._tool_spec(name)
            if spec is None or not (spec.concurrency_safe or spec.concurrent_safe):
                return name
        return None

    @staticmethod
    def _parallel_cancelled(child: SkillFrame, message: str) -> dict[str, Any]:
        """分支取消条目(kind="cancelled":first_success 败方/depends_on 未启动/子树取消)。"""
        return {
            "ok": False,
            "value": None,
            "error": _cancelled_payload(message),
            "frame_id": child.frame_id,
        }

    async def _parallel_cancel_all(
        self,
        branch_frames: dict[int, SkillFrame],
        tasks: dict[int, asyncio.Task[Any]],
        reason: str,
    ) -> None:
        """取消整批在跑分支并等 ack;ack 阶段的异常一律吞掉——调用方正在上抛途中。"""
        for index, task in tasks.items():
            if task.done():
                continue
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.cancel_subtree(branch_frames[index].frame_id, reason)

    # ------------------------------------------------------------------
    # §5.2 cancel_frame:子树级联取消——目标帧及其后代进入终态,不中止 run
    # ------------------------------------------------------------------

    def _collect_subtree(self, frame_id: str) -> list[str]:
        """沿帧树邻接表 DFS 收集目标帧及全部后代的 frame_id(含目标自身)。

        邻接数据两源并集:``stack.children_of``(已压栈帧)+ ``_spawned`` 的
        parent_frame_id 登记(后台帧压栈前的竞态窗口,spawn 返回与 run_frame
        压栈之间没有同步点)。
        """
        seen = {frame_id}
        queue = [frame_id]
        while queue:
            fid = queue.pop()
            for child in self.stack.children_of(fid):
                if child.frame_id not in seen:
                    seen.add(child.frame_id)
                    queue.append(child.frame_id)
            for bucket in self._spawned.values():
                for child_id, (parent_id, _task) in bucket.items():
                    if parent_id == fid and child_id not in seen:
                        seen.add(child_id)
                        queue.append(child_id)
        return list(seen)

    async def cancel_subtree(self, frame_id: str, reason: str) -> list[str]:
        """子树级联取消(§5.2 RunControl.cancel_frame 的内核侧落地)。

        - 在册后台帧:``task.cancel()`` —— CancelledError 走 §3.1 中断配对路径
          (占位 tool_result 已保证 §7.4 配对原子性),随后 await 等终态(ack);
        - 调用链上的帧:置帧级 stop 标志,prompt 帧在 ``pre:step`` safe point 抛
          :class:`SubtreeCancelled`(code 帧不检查标志,经 wait/invoke 边界的
          子帧终态错误自然收尾);
        - 幂等:已终态的后台帧跳过 cancel,重复调用不炸;未知帧记日志并返回空表。

        返回纳入取消的 frame_id 列表(含目标自身,作 ack)。
        """
        if self.stack.get(frame_id) is None and self._spawned_entry(frame_id) is None:
            _log.warning("cancel_subtree:帧 %s 不存在,请求被丢弃", frame_id)
            return []
        ids = self._collect_subtree(frame_id)
        tasks: list[asyncio.Task[Any]] = []
        for fid in ids:
            entry = self._spawned_entry(fid)
            if entry is not None:
                task = entry[1]
                if not task.done():
                    task.cancel()
                tasks.append(task)
            else:
                # 非后台帧:帧级 stop 标志(仅 prompt 帧在 safe point 消费;对已
                # 终态帧置标志无害——frame_id 唯一,不会再被检查)
                self._frame_stop_flags[fid] = reason
        current = asyncio.current_task()
        for task in tasks:
            if task is current:
                continue  # 自取消(SYNC sidecar 在被取消帧的信号里发起):不在自身任务内 await 自身
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        return ids

    # ------------------------------------------------------------------
    # §12.2 StatusBoard:帧状态滚动写入 status 命名空间(后台帧的滚动状态通道)
    # ------------------------------------------------------------------

    async def _board_status(self, frame: SkillFrame, value: dict[str, Any]) -> None:
        """帧状态写入 ``status`` 命名空间;写失败只记日志,不阻断主流程。"""
        if self.blackboard is None:
            return
        try:
            await self.blackboard.put("status", frame.frame_id, value)
        except Exception:  # noqa: BLE001 — 状态通道故障不得拖垮 run(§5.3 同旨)
            _log.warning(
                "StatusBoard 写入失败(忽略):frame=%s value=%r",
                frame.frame_id,
                value,
                exc_info=True,
            )

    @staticmethod
    def _tool_message(call: ToolCall, payload: dict[str, Any]) -> Message:
        return Message(
            role=Role.TOOL,
            content=json.dumps(payload),
            tool_call_id=call.id,
            name=call.name,
            source=Source.TOOL_RESULT,
        )

    # ------------------------------------------------------------------
    # §3.1 步骤 7:记账与预算检查
    # ------------------------------------------------------------------

    async def account(
        self,
        frame: SkillFrame,
        usage: ChatUsage | None,
        *,
        ttft_ms: int = 0,
        total_ms: int = 0,
    ) -> None:
        """帧/run 两级记账与预算检查(§3.1 步骤 7;超预算抛 BudgetExceeded 沿栈上抛)。

        ``ttft_ms``/``total_ms``(WS2 流式计时):与 tokens 同点两级累加(求和口径
        见 :meth:`subtree_usage`);chat 路径恒 0,行为与引入前一致。

        run 级检查(max_steps → RunAborted、max_cost → BudgetExceeded)之后做
        帧/子树级预算检查(:meth:`_check_subtree_budgets`,manifest
        ``limits.max_cost``/``max_steps`` 执行点)。本方法因此是 async(祖先分档
        要 await cancel_subtree)——调用点:帧循环 :meth:`_frame_loop` 与
        :meth:`KernelLogicContext.chat`,两处本就 async。
        """
        frame.usage.steps += 1
        run = self._runs.get(frame.run_id)
        if run is not None:
            run.state.usage.steps += 1
        if usage is not None:
            targets = [frame.usage] + ([run.state.usage] if run is not None else [])
            for target in targets:
                target.prompt_tokens += usage.prompt
                target.completion_tokens += usage.completion
                target.cache_read_tokens += usage.cache_read
                target.cache_write_tokens += usage.cache_write
                target.thinking_tokens += usage.thinking
                target.cost += usage.cost
        if ttft_ms or total_ms:
            targets = [frame.usage] + ([run.state.usage] if run is not None else [])
            for target in targets:
                target.ttft_ms += ttft_ms
                target.total_ms += total_ms
        if run is not None and run.state.usage.steps > self.config.max_steps:
            raise RunAborted(
                f"run 总步数 {run.state.usage.steps} 超过 max_steps={self.config.max_steps}"
            )
        if run is not None and run.state.usage.cost > self.config.max_cost:
            raise BudgetExceeded(
                f"run 成本 {run.state.usage.cost:.4f} 超过 max_cost={self.config.max_cost}"
            )
        await self._check_subtree_budgets(frame)

    async def _check_subtree_budgets(self, frame: SkillFrame) -> None:
        """帧/子树级预算检查(manifest ``limits.max_cost``/``max_steps`` 执行点)。

        沿 ``parent_id`` 链收集祖先(含自身),逐预算帧(声明了 max_cost/max_steps
        的帧)判定;链上无预算声明直接返回(快路径,零预算 run 零开销)。判定口径:

        - ``max_cost``:**子树求和**——该帧及全部后代的 cost 合计 > max_cost
          (``_subtree_usage_sum``;花费沿子树累积,预算是"这棵子树总共花多少");
        - ``max_steps``:**帧自身**——该帧 ``usage.steps`` > max_steps(该帧自身
          agent loop 的迭代上限,与 RunConfig.max_steps"全 run 总步数"对仗;
          既有 manifest 均按此口径声明)。

        触发分档(先登记 ``_budget_tripped`` 防重 + 发 ``budget.exceeded`` 信号,
        信号恰好一次;一帧触发即返回——链上多预算同时超,最老/近根的先):
        - 预算帧是根帧(parent_id None)→ raise BudgetExceeded(§3.1 步骤 7
          同语义,炸 run);
        - 预算帧是当前帧 → raise SubtreeCancelled(本子树终态,invoke 边界折叠为
          父帧 interrupted 错误观察,run 继续);
        - 预算帧是祖先 → ``await self.cancel_subtree(...)``:当前帧在其子树内,
          prompt 帧在下一个 safe point 终结;**边界**:调用链上的 code 帧不检查
          帧级 stop 标志(见 :meth:`cancel_subtree`),其终结随 invoke/wait 边界
          穿透(子帧终态错误观察/SubtreeCancelled 上抛,恢复策略交 code 技能);
          当前帧是 code 帧时(``ctx.chat`` 记账路径)同理。

        ``_budget_tripped`` 是进程态,不随 checkpoint 持久化:新进程 resume 时
        同一预算帧会再触发一次,幂等(cancel ack 幂等;BudgetExceeded 重抛同语义);
        **同进程** resume 则命中防重跳过(该帧级预算不再拦截,run 级检查仍逐次
        兜底)——与 ``_frame_stop_flags`` 不持久化同旨的进程态边界。
        """
        chain: list[SkillFrame] = []
        node: SkillFrame | None = frame
        while node is not None:
            chain.append(node)
            node = self.stack.get(node.parent_id) if node.parent_id is not None else None
        budgeted: list[tuple[SkillFrame, Any]] = []
        for ancestor in chain:
            limits = self.skills.get(ancestor.skill).manifest.limits
            if limits is not None and (limits.max_cost is not None or limits.max_steps is not None):
                budgeted.append((ancestor, limits))
        if not budgeted:
            return
        for budget_frame, limits in reversed(budgeted):  # 最老(近根)先查
            if budget_frame.frame_id in self._budget_tripped:
                continue  # 已触发过:子树已终态/在取消,信号不重复发
            over_steps = (
                limits.max_steps is not None and budget_frame.usage.steps > limits.max_steps
            )
            usage: Usage | None = None
            over_cost = False
            if limits.max_cost is not None:
                usage = self._subtree_usage_sum(budget_frame.frame_id)
                over_cost = usage.cost > limits.max_cost
            if not (over_cost or over_steps):
                continue
            if usage is None:
                usage = self._subtree_usage_sum(budget_frame.frame_id)  # 信号 payload 用
            reason = (
                f"子树成本 {usage.cost:.4f} 超过 limits.max_cost={limits.max_cost}"
                if over_cost
                else f"帧步数 {budget_frame.usage.steps} 超过 limits.max_steps={limits.max_steps}"
            )
            self._budget_tripped.add(budget_frame.frame_id)
            await self.signals.emit(
                self._sig(
                    BUDGET_EXCEEDED,
                    budget_frame,
                    {
                        "max_cost": limits.max_cost,
                        "max_steps": limits.max_steps,
                        "subtree_cost": usage.cost,
                        "subtree_steps": usage.steps,
                    },
                )
            )
            if budget_frame.parent_id is None:
                raise BudgetExceeded(f"budget: 根帧技能 {budget_frame.skill.name} {reason}")
            if budget_frame.frame_id == frame.frame_id:
                raise SubtreeCancelled(f"budget: {reason}")
            await self.cancel_subtree(budget_frame.frame_id, f"budget: {reason}")
            return

    def subtree_usage(self, frame_id: str) -> Usage:
        """子树记账读视图(WS2):目标帧及全部后代的九字段 Usage 求和。

        子树收集复用 :meth:`_collect_subtree`(WS1;栈邻接 + ``_spawned`` 竞态窗口
        并集);只读视图,不改 ``account()`` 写入路径——帧/run 双写语义不变,父帧
        usage 仍不含子帧,聚合发生在读取侧。

        聚合口径:steps/tokens/cost 求和(计费口径,与 run 级记账对账一致);时间
        字段 ttft_ms/total_ms 同样**求和**——语义是"子树资源占用累计"而非墙钟
        时长(嵌套帧的 total_ms 本就重叠,父帧墙钟含子帧,求和是资源消耗口径;
        ttft_ms 求和为首 token 延迟累计,取均值会随子树规模稀释、不利于定位慢帧)。

        未知 frame_id:记日志并返回零值 Usage(与 inject_message/cancel_subtree
        同旨的防御式语义——读视图不崩调用方)。

        帧/子树级预算强制已实现(:meth:`_check_subtree_budgets`,manifest
        ``limits.max_cost``/``max_steps`` 执行点):``max_cost`` 检查侧沿祖先链
        调本求和段(:meth:`_subtree_usage_sum`),写路径不动(父帧 usage 仍不含
        子帧)——与"聚合发生在读取侧"的既定语义一致;``max_steps`` 口径为帧自身
        步数,不经求和段。组合子 budget 参数工具面(race_first 软闸 watchdog)
        仍单列,与本机制正交。
        """
        if self.stack.get(frame_id) is None and self._spawned_entry(frame_id) is None:
            _log.warning("subtree_usage:帧 %s 不存在,返回零值 Usage", frame_id)
            return Usage()
        return self._subtree_usage_sum(frame_id)

    def _subtree_usage_sum(self, frame_id: str) -> Usage:
        """子树 usage 求和段(:meth:`subtree_usage` 与帧级预算检查共用):
        ``_collect_subtree`` 收集 + 九字段累加;不含未知帧防御(调用方保证在册)。
        """
        total = Usage()
        for fid in self._collect_subtree(frame_id):
            frame = self.stack.get(fid)
            if frame is None:
                # _spawned 竞态窗口内尚未压栈的后台帧:无 SkillFrame 可读,usage 为零
                continue
            u = frame.usage
            total.steps += u.steps
            total.prompt_tokens += u.prompt_tokens
            total.completion_tokens += u.completion_tokens
            total.cache_read_tokens += u.cache_read_tokens
            total.cache_write_tokens += u.cache_write_tokens
            total.thinking_tokens += u.thinking_tokens
            total.cost += u.cost
            total.ttft_ms += u.ttft_ms
            total.total_ms += u.total_ms
        return total

    def frame_status_payload(self, frame_id: str) -> dict[str, Any]:
        """帧状态读视图(W5-WS1):``LogicContext.frame_status`` 与 syscall 桥共用数据源。

        返回 ``{"frame_id", "status", "skill", "usage"}``:``status`` 取
        :class:`FrameStatus` 值(``"running"``/``"done"``/...),``usage`` 为
        :meth:`subtree_usage` 的九字段 dict(asdict)。防御式语义与
        ``cancel_subtree``/``subtree_usage`` 同旨——未知帧返回 ``status=None``
        (``skill=None``、usage 零值)的同形字典,不抛;spawn 竞态窗口(已登记
        未压栈的后台帧)视如 ``"pending"``,usage 为零值(尚无 SkillFrame 可读)。
        """
        frame = self.stack.get(frame_id)
        if frame is None:
            status = (
                FrameStatus.PENDING.value if self._spawned_entry(frame_id) is not None else None
            )
            return {
                "frame_id": frame_id,
                "status": status,
                "skill": None,
                "usage": dataclasses.asdict(Usage()),
            }
        return {
            "frame_id": frame_id,
            "status": frame.status.value,
            "skill": frame.skill.name,
            "usage": dataclasses.asdict(self.subtree_usage(frame_id)),
        }


async def run_frame(frame: SkillFrame, kernel: Kernel) -> Any:
    """§3.1 语义伪码对应的模块级协程(压栈 → loop → 弹栈)。"""
    return await kernel.run_frame(frame)
