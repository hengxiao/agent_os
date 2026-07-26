"""内核检查点(DESIGN.md §10.2 WAL 原则、§3.1 中断配对;M5a)。

WAL 原则:"trajectory 是 Agent 的全部状态"——帧 transcript 完整入档即检查点,
恢复 = 重建帧树 + 带着完整上下文重入 loop(**恢复不是重跑**:DONE 帧跳过,
已完成的工作不重复,剩余 LLM 调用数精确可数)。

检查点 JSON(schema v1)::

    {"v": 1,
     "run": {"run_id", "status", "usage": {...}, "result", "error",
             "run_state"(§W1-4 run 级工具状态,additive,schema v1 不变)},
     "frames": [{"frame_id", "skill", "parent_id", "input", "depth", "status",
                 "result", "error", "call_id", "usage": {...},
                 "context": {"messages": [...], "working", "pinned", "token_estimate"}}]}

``status`` 是检查点视角的进展标签:``"done"`` = 帧已完成,或一步 LLM 调用都未
完成(``usage.steps == 0``,不持有任何可保存的进展,恢复等价于从头重跑);
``"running"`` = 持有未完成进展、待恢复。恢复时不凭标签做跳过判断,而以
``result`` 是否已结算为准(已完成帧才有 result)。

断电模拟的遗留形态(测试以异常模拟断电,异常沿栈上抛时:在跑帧被 ``pop_err``
置 FAILED,父帧 ``_invoke_skill`` 把子帧异常折叠为错误观察写入上下文)意味着
恢复结算要面对三种配对状态:

1. 调用已有 ``ok=True`` 的工具结果 → 已结算,不动;
2. 调用只有断电崩出的错误观察,且以此调用创建的子帧(call_id 匹配)已 DONE
   → **就地改写**为子帧真实结果(严格保持 tool_call/tool_result 一一配对);
3. 调用完全无工具结果(分发到一半断电)且无子帧 → 追加中断占位结果
   (``ok=False, kind=interrupted``,§3.1 中断配对)。

例外(SUPERVISOR.md §4):``ask_supervisor`` 调用无工具结果且帧 ``working`` 含
``_pending_ask`` 时**不是**"分发到一半断电"——恢复时先经
``Kernel._settle_pending_ask`` 重新向调用方提问并写回真实答案,
再进入上面三条规则结算其余调用。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from agent_os.api.v1 import (
    POST_FRAME_POP,
    RUN_ABORTED,
    RUN_FINISHED,
    FrameContext,
    FrameStatus,
    Message,
    Role,
    RunStatus,
    Signal,
    SkillFrame,
    SkillRef,
    Source,
    ToolCall,
    ToolErrorKind,
    Usage,
)
from agent_os.kernel.errors import RunAborted
from agent_os.kernel.run import Run

#: checkpoint JSON schema 版本(§10.2 schema 版本化)
CHECKPOINT_VERSION = 1

_USAGE_KEYS = {f.name for f in dataclasses.fields(Usage)}


def _usage_to_dict(usage: Usage) -> dict[str, Any]:
    return {k: getattr(usage, k) for k in _USAGE_KEYS}


def _usage_from_dict(data: dict[str, Any]) -> Usage:
    return Usage(**{k: v for k, v in data.items() if k in _USAGE_KEYS})


def _message_to_dict(msg: Message) -> dict[str, Any]:
    return {
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


def _message_from_dict(data: dict[str, Any]) -> Message:
    return Message(
        role=Role(data["role"]),
        content=data.get("content", ""),
        tool_calls=[
            ToolCall(id=tc.get("id", ""), name=tc.get("name", ""), args=tc.get("args", {}))
            for tc in data.get("tool_calls", [])
        ],
        tool_call_id=data.get("tool_call_id"),
        name=data.get("name"),
        reasoning=data.get("reasoning"),
        source=Source(data.get("source", "system")),
        meta=data.get("meta", {}),
    )


def _checkpoint_status(frame: SkillFrame) -> str:
    """检查点视角的进展标签(见模块 docstring)。"""
    if frame.status is FrameStatus.DONE or frame.usage.steps == 0:
        return "done"
    return "running"


def dump_checkpoint(kernel: Any, run_id: str, path: str) -> None:
    """把 run 状态与全部帧(含上下文与记账)序列化为 JSON 检查点(§10.2)。"""
    run = kernel._runs[run_id]
    frames = [f for f in kernel.stack.tree() if f.run_id == run_id]
    doc = {
        "v": CHECKPOINT_VERSION,
        "run": {
            "run_id": run.run_id,
            "status": run.state.status.value,
            "usage": _usage_to_dict(run.state.usage),
            "result": run.state.result,
            "error": run.state.error,
            # §W1-4:run 级工具状态(todo 清单等)随 checkpoint 落盘(additive,schema v1 不变)
            "run_state": getattr(kernel.tools, "run_states", {}).get(run_id, {}),
        },
        "frames": [
            {
                "frame_id": f.frame_id,
                "skill": str(f.skill),
                "parent_id": f.parent_id,
                "input": f.input,
                "depth": f.depth,
                "status": _checkpoint_status(f),
                "result": f.result,
                "error": str(f.error) if f.error is not None else None,
                "call_id": f.call_id,
                "usage": _usage_to_dict(f.usage),
                "context": {
                    "messages": [_message_to_dict(m) for m in f.context.messages],
                    "working": f.context.working,
                    "pinned": f.context.pinned,
                    "token_estimate": f.context.token_estimate,
                },
            }
            for f in frames
        ],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, default=repr), encoding="utf-8"
    )


def _frame_from_dict(run_id: str, data: dict[str, Any]) -> SkillFrame:
    return SkillFrame(
        frame_id=data["frame_id"],
        run_id=run_id,
        skill=SkillRef.parse(data["skill"]),
        parent_id=data.get("parent_id"),
        input=data.get("input", {}),
        context=FrameContext(
            messages=[_message_from_dict(m) for m in data["context"]["messages"]],
            working=data["context"].get("working", {}),
            pinned=data["context"].get("pinned", []),
            token_estimate=data["context"].get("token_estimate", 0),
        ),
        depth=data["depth"],
        result=data.get("result"),
        error=data.get("error"),
        usage=_usage_from_dict(data.get("usage", {})),
        call_id=data.get("call_id"),
    )


def _payload_ok(tool_msg: Message) -> bool | None:
    """工具结果消息的 ``ok`` 字段;内容非 JSON payload 时返回 None。"""
    try:
        payload = json.loads(tool_msg.content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload.get("ok") is True


def _last_tool_message(messages: list[Message], after: int, call_id: str) -> Message | None:
    """``after`` 下标之后与 ``call_id`` 配对的最后一条 TOOL 消息。"""
    found = None
    for msg in messages[after + 1:]:
        if msg.role is Role.TOOL and msg.tool_call_id == call_id:
            found = msg
    return found


def _tool_result_message(call: ToolCall, payload: dict[str, Any]) -> Message:
    """与 runner ``_tool_message`` 同构的工具结果消息。"""
    return Message(
        role=Role.TOOL,
        content=json.dumps(payload),
        tool_call_id=call.id,
        name=call.name,
        source=Source.TOOL_RESULT,
    )


def _settle_unpaired_calls(kernel: Any, frame: SkillFrame) -> None:
    """结算帧上下文中未配对/错误配对的调用(恢复重入 loop 前;规则见模块 docstring)。"""
    messages = frame.context.messages
    appended: list[Message] = []
    for index, msg in enumerate(messages):
        if msg.role is not Role.ASSISTANT:
            continue
        for call in msg.tool_calls:
            tool_msg = _last_tool_message(messages, index, call.id)
            ok = _payload_ok(tool_msg) if tool_msg is not None else None
            if ok or (ok is None and tool_msg is not None):
                continue  # 已有成功结果(或非 JSON 结果,不触碰)
            child = next(
                (c for c in kernel.stack.children_of(frame.frame_id) if c.call_id == call.id),
                None,
            )
            if child is not None and child.status is FrameStatus.DONE:
                # 子帧已完成:改写断电崩出的错误观察 / 补记真实结果(配对原子性)
                payload = {"ok": True, "value": child.result, "error": None}
                if tool_msg is not None:
                    tool_msg.content = json.dumps(payload)
                else:
                    appended.append(_tool_result_message(call, payload))
            elif tool_msg is None:
                # 分发到一半断电(无子帧):中断占位(§3.1 中断配对)
                payload = {
                    "ok": False,
                    "value": None,
                    "error": {
                        "kind": ToolErrorKind.INTERRUPTED.value,
                        "message": "interrupted",
                        "retryable": False,
                        "hint": "",
                    },
                }
                appended.append(_tool_result_message(call, payload))
            # 其余(子帧真失败的错误观察):保留,恢复后的 LLM 可见并自行恢复(§3.2)
    messages.extend(appended)


async def resume_from_checkpoint(kernel: Any, path: str) -> Any:
    """从检查点恢复 run:重建 Run 与帧树,跳过 DONE 帧,深度从深到浅结算并重入 loop。

    恢复期间 signals 照常发射;根帧完成时结算 Run 并发射 ``run.finished``
    (不补发 ``run.started``);失败路径与 :meth:`Kernel.run` 同构
    (FAILED/ABORTED + ``run.aborted``)。
    """
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("v") != CHECKPOINT_VERSION:
        raise ValueError(f"不支持的 checkpoint 版本: {doc.get('v')!r}")
    frames = [_frame_from_dict(doc["run"]["run_id"], fd) for fd in doc["frames"]]
    if not frames:
        raise ValueError("checkpoint 不含任何帧")

    run = Run(run_id=doc["run"]["run_id"], config=kernel.config)
    run.state.usage = _usage_from_dict(doc["run"]["usage"])
    run.state.status = RunStatus.RUNNING
    kernel._runs[run.run_id] = run
    # §W1-4:恢复 run 级工具状态(todo 清单等),恢复后状态栏与工具读到同一份
    saved_run_state = doc["run"].get("run_state")
    run_states = getattr(kernel.tools, "run_states", None)
    if saved_run_state and run_states is not None:
        run_states[run.run_id] = saved_run_state

    frames.sort(key=lambda f: f.depth)
    for frame in frames:
        kernel.stack.push(frame)  # 登记帧树(深度兜底检查同 run_frame)
    for frame in frames:
        if frame.result is not None:
            kernel.stack.pop_ok(frame, frame.result)  # 已完成帧:恢复时跳过

    root = next(f for f in frames if f.parent_id is None)
    try:
        # 深度从深到浅:子帧先恢复 DONE,父帧结算时才能取到真实结果
        for frame in sorted(frames, key=lambda f: f.depth, reverse=True):
            if frame.status is FrameStatus.DONE:
                continue
            # pending ask(SUPERVISOR.md §4):重新向调用方提问结算,
            # 先于未配对结算——不得落入 interrupted 占位
            await kernel._settle_pending_ask(frame)
            _settle_unpaired_calls(kernel, frame)
            skill_obj = kernel.skills.get(frame.skill)
            try:
                result = await kernel._execute_frame(frame, skill_obj)
            except BaseException as err:
                kernel.stack.pop_err(frame, err)
                raise
            kernel.stack.pop_ok(frame, result)
            await kernel.signals.emit(kernel._sig(POST_FRAME_POP, frame))
        run.state.status = RunStatus.DONE
        run.state.result = root.result
        await kernel.signals.emit(Signal(name=RUN_FINISHED, run_id=run.run_id, payload={}))
        return root.result
    except Exception as e:
        run.state.status = RunStatus.ABORTED if isinstance(e, RunAborted) else RunStatus.FAILED
        run.state.error = f"{type(e).__name__}: {e}"
        await kernel.signals.emit(
            Signal(name=RUN_ABORTED, run_id=run.run_id, payload={"error": run.state.error})
        )
        raise
    finally:
        # run 收尾:取消在跑的 ASYNC sidecar 任务(§5.3,与 run() 同构)
        if kernel.sidecars is not None:
            await kernel.sidecars.close()
