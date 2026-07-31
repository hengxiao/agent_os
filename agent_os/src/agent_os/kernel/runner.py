"""内核 runner(DESIGN.md §3.1 语义伪码的落点;M0 单帧 runner,M2 调用栈/压栈挂起,
M4 verdict 仲裁与 sidecar 接线,M5a checkpoint/resume 断电恢复,
S1 ask_supervisor 拦截/就地挂起/回答注入与 resume 重问,SUPERVISOR.md v2 §2/§4)。

agent loop 顺序:safe point(run 中止标志)→ pre:step 检查点(verdict 仲裁,§5.2)
→ 强制压缩检查 → context.maintain/build → providers.chat →
终止判断(outputs 校验 + verifier)→ 分发(pre:tool.call 可 Veto/Modify/Stop;
工具走 Tool Registry,``skill.*`` 压栈)→ post:step(调用签名列表)→
记账与预算检查。弹栈前 ``pre:frame.pop`` 同步可否决(§3.1 pop())。

硬失败传播边界(§3.2):MaxDepthExceeded / RunAborted(含 BudgetExceeded)不可被
单帧吞掉,沿调用栈弹到 Run 边界;其余子帧异常折叠为父帧的错误观察。
"""

from __future__ import annotations

import asyncio
import contextlib
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
    ORCHESTRATE_TOOL,
    POST_FRAME_POP,
    POST_FRAME_PUSH,
    POST_LLM_RESPONSE,
    POST_LOGIC_EXEC,
    POST_SKILL_INVOKE,
    POST_STEP,
    POST_TOOL_CALL,
    PRE_FRAME_POP,
    PRE_FRAME_PUSH,
    PRE_LLM_REQUEST,
    PRE_LOGIC_EXEC,
    PRE_SKILL_INVOKE,
    PRE_STEP,
    PRE_TOOL_CALL,
    RUN_ABORTED,
    RUN_FINISHED,
    RUN_STARTED,
    Allow,
    ChatUsage,
    ExecRequest,
    ForceCompress,
    FrameContext,
    InjectMessage,
    LogicError,
    Message,
    Modify,
    Pause,
    ResourceLimits,
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
    TrustLevel,
    Veto,
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
    SkillLoadError,
    ToolDispatchError,
)
from agent_os.kernel.logic_context import KernelLogicContext
from agent_os.kernel.run import Run
from agent_os.kernel.stack import FrameStack
from agent_os.tools.local_registry import ToolDispatchContext

_log = logging.getLogger("agent_os.kernel")

#: 最终答案 outputs 校验连败上限(§3.2 恢复环路语义:输出修复循环独立熔断)
_OUTPUT_VALIDATION_MAX_FAILURES = 2

#: 单次沙箱执行的 syscall 默认上限(CODE-ORCHESTRATION.md §4;manifest
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
        supervisor: Any = None,  # SupervisorManager(SUPERVISOR.md §2.3;S1 handler 通道)
        stack: FrameStack | None = None,
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
        self._runs: dict[str, Run] = {}
        #: run 中止标志表(dict[run_id, reason];RunControl.stop/pause 置位,
        #: runner 在 pre:step safe point 检查并抛 RunAborted,§3.1/§5.2)
        self._stop_flags: dict[str, str] = {}
        #: RunControl 句柄(装配 sidecars 时由 KernelBuilder 注入;pre:step 的
        #: InjectMessage/ForceCompress verdict 经它落地)
        self.ctl: Any = None
        #: spawn 后台帧登记表(§3.4):frame_id → 子帧后台任务;wait_frame 在此 join
        self._spawned: dict[str, asyncio.Task[Any]] = {}

    # ------------------------------------------------------------------
    # §13 生命周期入口
    # ------------------------------------------------------------------

    async def run(self, skill: str, input: dict[str, Any]) -> Any:
        """§13 生命周期入口:解析根技能 → 构建根帧 → 跑帧树 → usage 汇总返回。"""
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
            context=FrameContext(
                messages=[
                    Message(role=Role.USER, content=json.dumps(input), source=Source.PARENT_INPUT)
                ]
            ),
        )
        await self.signals.emit(
            Signal(name=RUN_STARTED, run_id=run.run_id, payload={"skill": str(skill_obj.ref)})
        )
        try:
            try:
                result = await self.run_frame(root)
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
        # 后台帧(§3.4):run 已结算,残留任务不该继续记账进已结算的 usage
        for frame_id, task in list(self._spawned.items()):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._spawned.pop(frame_id, None)
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
            raise RunAborted(f"paused: {verdict.reason}")
        if isinstance(verdict, InjectMessage) and self.ctl is not None:
            await self.ctl.inject_message(verdict.frame_id, verdict.msg)
        elif isinstance(verdict, ForceCompress) and self.ctl is not None:
            await self.ctl.force_compress(verdict.frame_id)  # 置帧标志,maintain 前生效

    async def _force_compress(self, frame: SkillFrame) -> None:
        """强制压缩一次(§7.1 外部强制触发):无视 cap,走 context manager 压缩路径。"""
        force = getattr(self.context, "force_compress", None)
        if force is not None:
            await force(frame)

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
            # safe point(§3.1):先查 run 中止标志(RunControl.stop/pause,§5.2)
            reason = self._stop_flags.get(frame.run_id)
            if reason is not None:
                raise RunAborted(reason)
            verdicts = await self.signals.emit(
                self._sig(PRE_STEP, frame, {"step": frame.usage.steps + 1})
            )
            await self._apply_pre_step(frame, verdicts)
            if frame.context.working.pop(FORCE_COMPRESS_KEY, False):
                await self._force_compress(frame)
            await self.context.maintain(frame)
            req = await self.context.build(frame)
            await self.signals.emit(self._sig(PRE_LLM_REQUEST, frame, {"model": req.model}))
            resp = await self.providers.chat(req)
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
            self.account(frame, resp.usage)
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
            # SANDBOX 档:ctx 经 syscall 通道跨进程构造(CODE-ORCHESTRATION.md §2.2),
            # 与 TRUSTED 档契约逐字一致——同一 handler 两档运行行为等价
            dispatch_fn=None if trusted else self._syscall_dispatcher(frame, manifest),
            limits=ResourceLimits(
                wall_time=timeout, cpu_time=timeout, memory_mb=256, stdout_bytes=100_000
            ),
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
            # 内核拦截式伪工具(SUPERVISOR.md §2.1,同 python_orchestrate 先例):
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
            raise RunAborted(f"paused: {verdict.reason}")
        if isinstance(verdict, Modify):
            call.args.update(verdict.patch)  # pre 可改参数(§5.1)
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
    # SUPERVISOR.md §2:ask_supervisor 伪工具——就地挂起,等本 run 调用方裁决(S1)
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
        上限,ToolGuard/信号/记账全部沿用,**无权限提升**(CODE-ORCHESTRATION.md §2.3)。
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
        """``python_orchestrate``(CODE-ORCHESTRATION.md):沙箱脚本 + 工具系统调用。

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
        wall = float(timeout) if timeout else 60.0
        req = ExecRequest(
            source=source,
            args={},
            ctx=None,  # 沙箱侧 ctx 由驱动脚本经 syscall 通道构造(§2.2)
            dispatch_fn=dispatch,
            limits=ResourceLimits(
                wall_time=wall, cpu_time=wall, memory_mb=256, stdout_bytes=100_000
            ),
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
        try:
            value = await self.run_frame(child)
        except (MaxDepthExceeded, RunAborted):
            raise  # 硬失败不可被帧吞掉,沿栈上抛(§3.2)
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
        return {"ok": True, "value": value, "error": None}

    # ------------------------------------------------------------------
    # §3.4 spawn 后台帧:父帧不挂起,子帧独立预算后台运行;join 退化为读终态
    # ------------------------------------------------------------------

    async def spawn_frame(self, parent: SkillFrame, skill: str, input: dict[str, Any]) -> str:
        """spawn 后台帧(§3.4):白名单/深度检查与 invoke 一致,返回子帧 frame_id。

        子帧经 ``asyncio.create_task`` 后台运行并登记在 ``self._spawned``;
        PRE/POST_SKILL_INVOKE 信号与 invoke 一致,payload 加 ``"background": True``。
        """
        parent_manifest = self.skills.get(parent.skill).manifest
        if skill not in parent_manifest.permissions.skills:
            raise SkillLoadError(
                f"子技能 {skill} 不在技能 {parent_manifest.name} 的 skills 白名单"
            )
        await self.signals.emit(
            self._sig(PRE_SKILL_INVOKE, parent, {"skill": skill, "background": True})
        )
        if parent.depth + 1 > self.config.max_depth:
            raise MaxDepthExceeded(
                f"调用子技能 {skill} 将达到 depth={parent.depth + 1},"
                f"超过 max_depth={self.config.max_depth}"
            )
        child = self.skills.make_frame(SkillCall(name=skill, args=dict(input)), parent)
        task = asyncio.create_task(self.run_frame(child))
        self._spawned[child.frame_id] = task
        # spawn-and-forget 的子帧异常由 StatusBoard 记录(failed);提前 retrieve,
        # 避免无人 wait 时事件循环 "exception was never retrieved" 噪音——
        # wait_frame 的 await 仍会原样上抛,语义不变
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        await self.signals.emit(
            self._sig(
                POST_SKILL_INVOKE, parent, {"skill": skill, "ok": True, "background": True}
            )
        )
        return child.frame_id

    async def wait_frame(self, frame_id: str) -> Any:
        """join 退化为读终态(§3.4):子帧失败把异常原样上抛,交给 code 技能处理。"""
        task = self._spawned.get(frame_id)
        if task is None:
            raise SkillLoadError(f"未知的后台帧 {frame_id}(未 spawn 或已回收)")
        return await task

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

    def account(self, frame: SkillFrame, usage: ChatUsage | None) -> None:
        """帧/run 两级记账与预算检查(§3.1 步骤 7;超预算抛 BudgetExceeded 沿栈上抛)。"""
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
        if run is not None and run.state.usage.steps > self.config.max_steps:
            raise RunAborted(
                f"run 总步数 {run.state.usage.steps} 超过 max_steps={self.config.max_steps}"
            )
        if run is not None and run.state.usage.cost > self.config.max_cost:
            raise BudgetExceeded(
                f"run 成本 {run.state.usage.cost:.4f} 超过 max_cost={self.config.max_cost}"
            )


async def run_frame(frame: SkillFrame, kernel: Kernel) -> Any:
    """§3.1 语义伪码对应的模块级协程(压栈 → loop → 弹栈)。"""
    return await kernel.run_frame(frame)
