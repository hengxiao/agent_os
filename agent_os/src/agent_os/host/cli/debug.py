"""agent-os debug:交互式调试前端(Agent OS Debugger P2;pdb 风格 REPL)。

形态::

    agent-os debug <skill> --input <json|@file> [--config ...] [--artifacts ...]
    agent-os debug --replay <run_id> [--until-step N]   # P5 时间旅行

装配与等待模型:

- 内核按 ``agent-os.toml`` 装配(同 ``run``),随后手动把
  :class:`~agent_os.kernel.debug.DebugController` 挂到信号总线(等价
  ``KernelBuilder.debug_controller`` 路径,见 builder.py;无 sidecar 时补装
  RunControlImpl 供 inject_message 落地)。**不注入 CLI supervisor 通道**:
  REPL 独占 stdin,``_cli_supervisor`` 的 stdin 作答会与命令读取抢输入。
- ``--replay``(P5 时间旅行):从产物目录读 meta/trace/checkpoint,
  ``build_mock_script`` + ``replace_providers``(host/shared/replay.py)把内核
  provider 面换成回放脚本,skill/input 取 meta.json——LLM Mock 回放、工具
  副作用真实重跑(replay.py 模块 docstring 的明示边界);随后进同一 REPL。
- ``--until-step N``(P5):入口断点换成一次性步数断点(kernel/debug.py
  ``Breakpoint.until``),run 启动后不停,直到第 N 条 ``pre:step`` 才暂停。
- run 在与 REPL **相同的事件循环**上以后台 task 跑(调试原语要求前端命令与
  run 同循环调用);读命令用 ``asyncio.to_thread(sys.stdin.readline)``
  (同 ``_cli_supervisor`` 模式),主线程 ``input()`` 会阻塞整个事件循环。
- 入口断点:pdb 语义——会话开出即加一次性 ``step`` 断点,run 启动后停在第一条
  ``pre:step``,给用户设断点的机会;首次暂停时自动删除。
- stdin EOF 等价 ``q``:摘下会话让 run 跑完(脚本化喂命令时无需显式收尾)。

退出码沿用 §3.3:0 成功 / 2 校验错 / 3 run 失败或中止(含调试器 ``stop``)/ 4 装配错。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_os.api.v1 import RUN_STARTED
from agent_os.host.shared.artifacts import _finalize_run
from agent_os.host.shared.replay import build_mock_script, replace_providers
from agent_os.host.shared.runrecord import STATUS_ABORTED, STATUS_DONE, STATUS_FAILED
from agent_os.kernel.control import RunControlImpl
from agent_os.kernel.debug import (
    CMD_CONTINUE,
    CMD_STEP_INTO,
    CMD_STEP_OUT,
    CMD_STEP_OVER,
    CMD_STOP,
    DebugController,
    DebugSession,
)
from agent_os.kernel.errors import AgentOSError, RunAborted, SkillLoadError
from agent_os.runtime.config import build_kernel

#: REPL 帮助文本(h / help)
HELP = """命令(pdb 风格):
  b tool <glob> | b skill <glob> | b step | b error   加断点(glob 匹配工具/技能名)
  del <bp_id>                                         删断点
  info b                                              断点列表(id/kind/match/hits)
  c                                                   继续到下一断点
  si / so / out                                       单步:步入 / 步过 / 步出
  stop                                                中止 run
  q                                                   摘下调试器,让 run 跑完并退出
  bt                                                  帧栈(* 标记暂停帧)
  i [--messages] [--working]                          检视暂停帧(上下文消息 / working)
  mod <json>                                          改本次工具调用参数(仅停在 pre:tool.call)
  inj <text>                                          向暂停帧注入一条用户消息
  h                                                   本帮助
空行忽略;stdin EOF 等价 q。"""

#: ``b`` 子命令的别名 → 断点 kind(kernel/debug.py BREAKPOINT_KINDS)
_BREAK_KINDS = {"tool": "tool_call", "skill": "skill_invoke", "step": "step", "error": "error"}


def _build_debug_kernel(config: str) -> tuple[Any, DebugController]:
    """装配内核并挂 DebugController;SkillLoadError → 2,其余装配失败 → 4(§3.3)。

    手动 attach 等价 ``KernelBuilder.debug_controller(controller)``(builder.py:
    订阅在 Telemetry/sidecar 之后;无 sidecar 时补装 RunControlImpl)。
    """
    from agent_os.host.cli.main import _InfraError  # 延迟导入:main 注册本模块,防循环

    try:
        # 不注入 _cli_supervisor:REPL 独占 stdin,问答案通道会与命令读取冲突
        kernel = build_kernel(config, supervisor_handler=None)
    except SkillLoadError:
        raise
    except Exception as e:
        raise _InfraError(f"{type(e).__name__}: {e}") from e
    controller = DebugController()
    if kernel.ctl is None:
        kernel.ctl = RunControlImpl(kernel)
    controller.attach(kernel.signals, kernel.ctl)
    return kernel, controller


# ----------------------------------------------------------------------
# 暂停展示与检视命令
# ----------------------------------------------------------------------


def _print_pause(session: DebugSession) -> None:
    """暂停点摘要:信号、原因、工具/技能、帧、步数、命中断点。"""
    pp = session.pause_point
    if pp is None:  # pragma: no cover — 仅在 paused 状态调用,防御
        return
    parts = [f"signal={pp['signal']}", f"reason={pp['reason']}"]
    if pp.get("tool"):
        parts.append(f"tool={pp['tool']}")
    if pp.get("skill"):
        parts.append(f"skill={pp['skill']}")
    parts.append(f"frame={pp['frame_id']}")
    if pp.get("step") is not None:
        parts.append(f"step={pp['step']}")
    if pp.get("depth") is not None:
        parts.append(f"depth={pp['depth']}")
    if pp["breakpoint_ids"]:
        parts.append(f"bp={','.join(pp['breakpoint_ids'])}")
    print("[paused] " + " ".join(parts))


def _do_break(session: DebugSession, rest: str) -> None:
    parts = rest.split()
    kind = _BREAK_KINDS.get(parts[0]) if parts else None
    if kind is None:
        print("*** 用法: b tool <glob> | b skill <glob> | b step | b error")
        return
    match = parts[1] if len(parts) > 1 else "*"
    bp = session.add_breakpoint(kind, match)
    print(f"断点 {bp.id}: kind={bp.kind} match={bp.match!r}")


def _do_delete(session: DebugSession, rest: str) -> None:
    bp_id = rest.split()[0] if rest.split() else ""
    if not bp_id:
        print("*** 用法: del <bp_id>")
    elif session.remove_breakpoint(bp_id):
        print(f"已删除断点 {bp_id}")
    else:
        print(f"*** 断点不存在: {bp_id}")


def _do_info_b(session: DebugSession) -> None:
    bps = session.breakpoints
    if not bps:
        print("无断点")
        return
    for bp in bps:
        print(
            f"{bp.id} kind={bp.kind} match={bp.match!r}"
            f" hits={bp.hits} enabled={bp.enabled}"
        )


def _do_backtrace(session: DebugSession) -> None:
    stack = session.frame_stack
    if not stack:
        print("(帧栈为空)")
        return
    paused_id = session.pause_point["frame_id"] if session.pause_point else None
    for f in stack:
        mark = "*" if f["frame_id"] == paused_id else " "
        print(f"{mark} depth={f['depth']} skill={f['skill']} frame={f['frame_id']}")


def _do_inspect(kernel: Any, session: DebugSession, rest: str) -> None:
    """检视暂停帧:缺省打印帧摘要;``--messages``/``--working`` 展开上下文。

    渲染对齐 CLI ``inspect --frame`` 子命令(cli/main.py ``_cmd_inspect``)。
    """
    pp = session.pause_point
    frame = kernel.stack.get(pp["frame_id"]) if pp else None
    if frame is None:
        print(f"*** 帧不在内核帧树中: {pp['frame_id'] if pp else '-'}")
        return
    flags = set(rest.split())
    print(f"frame_id: {frame.frame_id}")
    print(f"skill: {frame.skill.name}")
    print(f"depth: {frame.depth}")
    print(f"status: {getattr(frame.status, 'value', frame.status)}")
    print(f"input: {frame.input}")
    print(f"usage: steps={frame.usage.steps} cost={frame.usage.cost}")
    if frame.error:
        print(f"error: {frame.error}")
    if "--messages" in flags:
        for msg in frame.context.messages:
            role = getattr(msg.role, "value", msg.role)
            if msg.content:
                print(f"[{role}] {msg.content}")
            elif msg.tool_calls:
                for tc in msg.tool_calls:
                    args = json.dumps(tc.args, ensure_ascii=False, default=repr)
                    print(f"[{role}] tool_call {tc.name}({args})")
            else:
                print(f"[{role}] (empty)")
    if "--working" in flags:
        print(f"working: {frame.context.working}")


def _do_mod(session: DebugSession, rest: str) -> str | None:
    """``mod <json>``:改本次工具调用参数(modify_tool_args 生效即放行)。"""
    try:
        patch = json.loads(rest) if rest else None
    except json.JSONDecodeError as e:
        print(f"*** JSON 解析错: {e}")
        return None
    if not isinstance(patch, dict):
        print("*** 用法: mod <json 对象>(仅暂停于 pre:tool.call 时有效)")
        return None
    try:
        session.modify_tool_args(patch)
    except AgentOSError as e:
        print(f"*** {e}")
        return None
    print(f"已修改工具参数: {patch}")
    return "wait"


async def _do_inject(session: DebugSession, rest: str) -> str | None:
    """``inj <text>``:向暂停帧注入一条用户消息(注入后放行)。"""
    text = rest.strip()
    if not text:
        print("*** 用法: inj <text>")
        return None
    frame_id = session.pause_point["frame_id"]
    try:
        await session.inject_message(frame_id, text)
    except AgentOSError as e:
        print(f"*** {e}")
        return None
    print(f"已注入消息到帧 {frame_id}")
    return "wait"


# ----------------------------------------------------------------------
# REPL 主循环
# ----------------------------------------------------------------------


async def _read_line() -> str | None:
    """异步读一行命令(stdin EOF 返回 None;to_thread 同 _cli_supervisor 模式)。"""
    print("dbg> ", end="", flush=True)
    line = await asyncio.to_thread(sys.stdin.readline)
    return line.strip() if line else None


async def _wait_pause_or_done(session: DebugSession, run_task: asyncio.Task) -> str:
    """等下一次暂停或 run 结束,返回 ``"paused"`` | ``"done"``。"""
    if run_task.done():
        return "done"
    waiter = asyncio.ensure_future(session.wait_paused())
    try:
        await asyncio.wait({waiter, run_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
    if session.state == "paused":
        return "paused"
    return "done" if run_task.done() else await _wait_pause_or_done(session, run_task)


async def _command_loop(kernel: Any, session: DebugSession) -> str:
    """暂停时的命令子循环:返回 ``"wait"``(已恢复运行)或 ``"quit"``(摘下)。"""
    while True:
        line = await _read_line()
        if line is None:
            print("q(stdin EOF,摘下会话让 run 跑完)")
            return "quit"
        cmd, _, rest = line.partition(" ")
        cmd, rest = cmd.lower(), rest.strip()
        if not cmd:
            continue
        if cmd in ("h", "help", "?"):
            print(HELP)
        elif cmd in ("b", "break"):
            _do_break(session, rest)
        elif cmd == "del":
            _do_delete(session, rest)
        elif cmd == "info" and rest == "b":
            _do_info_b(session)
        elif cmd == "bt":
            _do_backtrace(session)
        elif cmd in ("i", "inspect"):
            _do_inspect(kernel, session, rest)
        elif cmd == "mod":
            action = _do_mod(session, rest)
            if action:
                return action
        elif cmd == "inj":
            action = await _do_inject(session, rest)
            if action:
                return action
        elif cmd in ("c", "continue"):
            session.resume(CMD_CONTINUE)
            return "wait"
        elif cmd in ("si", "step", "s"):
            session.resume(CMD_STEP_INTO)
            return "wait"
        elif cmd in ("so", "next", "n"):
            session.resume(CMD_STEP_OVER)
            return "wait"
        elif cmd in ("out", "step_out", "r"):
            session.resume(CMD_STEP_OUT)
            return "wait"
        elif cmd == "stop":
            session.resume(CMD_STOP)
            return "wait"
        elif cmd in ("q", "quit"):
            return "quit"
        else:
            print(f"*** 未知命令: {cmd}('h' 查看帮助)")


async def _repl(kernel: Any, session: DebugSession, run_task: asyncio.Task, entry_bp_id: str) -> None:
    """REPL 外层循环:等暂停 → 展示 → 命令子循环,直到 run 结束或用户 q。"""
    while True:
        state = await _wait_pause_or_done(session, run_task)
        if state != "paused":
            return
        if entry_bp_id is not None:
            # 入口断点(一次性):首次暂停即删,后续 c 不再每步停
            session.remove_breakpoint(entry_bp_id)
            entry_bp_id = None
        _print_pause(session)
        if await _command_loop(kernel, session) == "quit":
            session.detach()
            return


async def _debug_session(
    kernel: Any,
    controller: DebugController,
    skill: str,
    run_input: dict[str, Any],
    artifacts_root: Path,
    until_step: int | None = None,
) -> dict[str, Any]:
    """起 run + 跑 REPL,收尾落产物四件套并组 RunRecord(复用 ``_finalize_run``,§2.2)。

    ``until_step``(P5 ``--until-step``):入口断点换成一次性步数断点——run 启动后
    不停,直到第 N 条 ``pre:step`` 才暂停(``Breakpoint.until``,命中后自我禁用)。
    """
    session = controller.open_session()
    entry_bp = session.add_breakpoint("step", until=until_step)  # pdb 语义:入口即停(或第 N 步)
    started: list[str] = []

    async def _rec(sig: Any) -> None:
        started.append(sig.run_id)

    kernel.signals.subscribe(RUN_STARTED, _rec)
    started_at = datetime.now(UTC).isoformat()
    run_task = asyncio.create_task(kernel.run(skill, run_input))
    if until_step is None:
        print(f"调试会话 {session.id} 已开(run 启动即停在第一条 pre:step;'h' 查看命令)")
    else:
        print(f"调试会话 {session.id} 已开(直达第 {until_step} 条 pre:step 暂停;'h' 查看命令)")
    await _repl(kernel, session, run_task, entry_bp.id)
    # REPL 结束:run 已完成,或 q/EOF 摘下会话——等 run 收尾并归类 status(§3.3)
    status, result, error = STATUS_DONE, None, None
    try:
        result = await run_task
    except RunAborted as e:
        status, error = STATUS_ABORTED, f"{type(e).__name__}: {e}"
    except Exception as e:
        if not started:
            raise  # run 未开始(校验类):上抛由宿主归退出码 2
        status, error = STATUS_FAILED, f"{type(e).__name__}: {e}"
    run_id = started[0]
    controller.close_session(session.id)
    return _finalize_run(
        kernel,
        run_id,
        artifacts_root,
        meta={
            "run_id": run_id,
            "skill": skill,
            "input": run_input,
            "host": "cli",
            "started_at": started_at,
            "debug_session": session.id,
        },
        status=status,
        result=result,
        error=error,
    )


def cmd_debug(args: argparse.Namespace) -> int:
    """``agent-os debug`` 子命令入口(cli/main.py 注册)。

    live 形态:``<skill> --input`` 必填;``--replay <run_id>``(P5 时间旅行)形态:
    skill/input 从产物 meta.json 读取,内核 provider 面换成回放脚本。
    """
    from agent_os.host.cli.main import _emit_record, _InfraError, _parse_input  # 防循环

    if args.until_step is not None and args.until_step < 1:
        print(f"输入错误: --until-step 须 >= 1,得到 {args.until_step}", file=sys.stderr)
        return 2
    replay_script: list[Any] | None = None
    if args.replay:
        if args.skill is not None or args.input is not None:
            print("输入错误: --replay 与 <skill>/--input 互斥(skill/input 从产物 meta 读取)", file=sys.stderr)
            return 2
        run_dir = Path(args.artifacts) / "runs" / args.replay
        for name in ("meta.json", "trace.jsonl", "checkpoint.json"):
            if not (run_dir / name).is_file():
                print(f"找不到 run 产物目录: {run_dir}", file=sys.stderr)
                return 2
        try:
            meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
            replay_script = build_mock_script(run_dir)
        except (OSError, ValueError) as e:  # 产物畸形 / 信号与 checkpoint 对不齐
            print(f"replay 产物无效: {e}", file=sys.stderr)
            return 2
        skill, run_input = meta.get("skill"), meta.get("input") or {}
    else:
        if not args.skill or args.input is None:
            print("输入错误: live 调试需要 <skill> 与 --input(或用 --replay <run_id>)", file=sys.stderr)
            return 2
        try:
            run_input = _parse_input(args.input)
        except (OSError, json.JSONDecodeError) as e:
            print(f"输入错误: {e}", file=sys.stderr)
            return 2
        skill = args.skill
    try:
        kernel, controller = _build_debug_kernel(args.config)
    except SkillLoadError as e:
        print(f"技能校验错误: {e}", file=sys.stderr)
        return 2
    except _InfraError as e:
        print(f"基础设施错误: {e}", file=sys.stderr)
        return 4
    if replay_script is not None:
        # P5 时间旅行:回放按 trace 记录值走,不问第二次(§4;replay.py 边界)
        replace_providers(kernel, replay_script)
    try:
        record = asyncio.run(
            _debug_session(
                kernel, controller, skill, run_input, Path(args.artifacts),
                until_step=args.until_step,
            )
        )
    except SkillLoadError as e:
        print(f"校验错误: {e}", file=sys.stderr)
        return 2
    _emit_record(record, False)
    return 0 if record["status"] == STATUS_DONE else 3
