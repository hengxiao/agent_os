"""内核 runner(DESIGN.md §3.1 语义伪码的落点;M0 单帧 runner,M2 调用栈/压栈挂起)。

agent loop 顺序:pre:step 检查点 → context.maintain/build → providers.chat →
终止判断(outputs 校验 + verifier)→ 分发(工具走 Tool Registry,``skill__*`` 压栈)→
记账与预算检查。弹栈前 ``pre:frame.pop`` 同步可否决(§3.1 pop())。

硬失败传播边界(§3.2):MaxDepthExceeded / RunAborted(含 BudgetExceeded)不可被
单帧吞掉,沿调用栈弹到 Run 边界;其余子帧异常折叠为父帧的错误观察。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import jsonschema

from agent_os.api.v1 import (
    POST_FRAME_POP,
    POST_FRAME_PUSH,
    POST_LLM_RESPONSE,
    POST_SKILL_INVOKE,
    POST_TOOL_CALL,
    PRE_FRAME_POP,
    PRE_FRAME_PUSH,
    PRE_LLM_REQUEST,
    PRE_SKILL_INVOKE,
    PRE_STEP,
    PRE_TOOL_CALL,
    RUN_ABORTED,
    RUN_FINISHED,
    RUN_STARTED,
    ChatUsage,
    FrameContext,
    Message,
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
    ToolCall,
    ToolErrorKind,
    ToolResult,
)
from agent_os.kernel.errors import (
    BudgetExceeded,
    MaxDepthExceeded,
    OutputValidationError,
    RunAborted,
    SkillLoadError,
)
from agent_os.kernel.run import Run
from agent_os.kernel.stack import FrameStack
from agent_os.tools.local_registry import ToolDispatchContext

_log = logging.getLogger("agent_os.kernel")

#: 最终答案 outputs 校验连败上限(§3.2 恢复环路语义:输出修复循环独立熔断)
_OUTPUT_VALIDATION_MAX_FAILURES = 2


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


def _error_payload(kind: ToolErrorKind, message: str) -> dict[str, Any]:
    return {"kind": kind.value, "message": message, "retryable": False, "hint": ""}


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
        self.stack = stack or FrameStack(max_depth=self.config.max_depth)
        self._runs: dict[str, Run] = {}

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
            result = await self.run_frame(root)
        except Exception as e:
            run.state.status = RunStatus.ABORTED if isinstance(e, RunAborted) else RunStatus.FAILED
            run.state.error = f"{type(e).__name__}: {e}"
            await self.signals.emit(
                Signal(name=RUN_ABORTED, run_id=run.run_id, payload={"error": run.state.error})
            )
            raise
        run.state.status = RunStatus.DONE
        run.state.result = result
        await self.signals.emit(Signal(name=RUN_FINISHED, run_id=run.run_id, payload={}))
        return result

    # ------------------------------------------------------------------
    # §3.1 单帧 agent loop
    # ------------------------------------------------------------------

    def _sig(self, name: str, frame: SkillFrame, extra: dict[str, Any] | None = None) -> Signal:
        payload = {"depth": frame.depth, "frame_id": frame.frame_id, "skill": str(frame.skill)}
        payload.update(extra or {})
        return Signal(name=name, run_id=frame.run_id, frame_id=frame.frame_id, payload=payload)

    async def run_frame(self, frame: SkillFrame) -> Any:
        """单帧 agent loop(§3.1):压栈 → loop → outputs 校验 → 弹栈。"""
        skill_obj = self.skills.get(frame.skill)
        await self.signals.emit(self._sig(PRE_FRAME_PUSH, frame))
        self.stack.push(frame)  # 深度兜底检查在 push 时(§3.2)
        await self.signals.emit(self._sig(POST_FRAME_PUSH, frame))
        try:
            if skill_obj.manifest.kind is SkillKind.CODE:
                raise NotImplementedError("M2: code 技能帧执行(本切片不覆盖)")
            result = await self._frame_loop(frame, skill_obj)
        except BaseException as err:
            self.stack.pop_err(frame, err)
            raise
        await self.signals.emit(self._sig(PRE_FRAME_POP, frame))
        self.stack.pop_ok(frame, result)
        await self.signals.emit(self._sig(POST_FRAME_POP, frame))
        return result

    async def _frame_loop(self, frame: SkillFrame, skill_obj: Skill) -> Any:
        manifest = skill_obj.manifest
        output_failures = 0
        while True:
            await self.signals.emit(self._sig(PRE_STEP, frame, {"step": frame.usage.steps + 1}))
            await self.context.maintain(frame)
            req = await self.context.build(frame)
            await self.signals.emit(self._sig(PRE_LLM_REQUEST, frame, {"model": req.model}))
            resp = await self.providers.chat(req)
            await self.signals.emit(self._sig(POST_LLM_RESPONSE, frame, {"model": req.model}))
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

    # ------------------------------------------------------------------
    # §3.1 步骤 6:分发(工具 vs 子技能,§3.3)
    # ------------------------------------------------------------------

    async def _dispatch_call(
        self, call: ToolCall, frame: SkillFrame, manifest: SkillManifest
    ) -> dict[str, Any]:
        if call.name.startswith("skill__"):
            return await self._invoke_skill(call, frame, manifest)
        if call.name not in manifest.permissions.tools:
            return {
                "ok": False,
                "value": None,
                "error": _error_payload(
                    ToolErrorKind.PERMISSION_DENIED,
                    f"工具 {call.name} 不在技能 {manifest.name} 的 tools 白名单",
                ),
            }
        await self.signals.emit(self._sig(PRE_TOOL_CALL, frame, {"tool": call.name}))
        result = await self.tools.dispatch(
            call,
            ToolDispatchContext(
                frame=frame,
                allowed_tools=manifest.permissions.tools,
                tool_policy=self.config.tool_policy,
            ),
        )
        await self.signals.emit(
            self._sig(POST_TOOL_CALL, frame, {"tool": call.name, "ok": result.ok})
        )
        return _result_payload(result)

    async def _invoke_skill(
        self, call: ToolCall, frame: SkillFrame, manifest: SkillManifest
    ) -> dict[str, Any]:
        name = call.name[len("skill__"):]
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
                    ToolErrorKind.INTERNAL, f"子技能 {name} 失败: {type(e).__name__}: {e}"
                ),
            }
        await self.signals.emit(self._sig(POST_SKILL_INVOKE, frame, {"skill": name, "ok": True}))
        return {"ok": True, "value": value, "error": None}

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
