"""命令方言(docs/TUI-DEBUG.md §5):解析(纯函数)+ 执行路由(经 DebugSource 出海)。

逐字学 GDB:命令名/缩写/参数语法对齐 GDB 真实习惯;**唯一前缀缩写可省**
(``fin`` = finish,``i b`` = info b);歧义报错列出候选;``help`` 文本从命令表
生成,不硬编码。容错面:

- ``parse(line)`` 是纯函数(不碰主题/数据源),返回 ``Command`` 或抛
  ``ParseError``(带 kind/candidates,文案由 app 层经 copy 键格式化);
- 裸 Enter 重复由 app 层驱动:``REPEATABLE`` 白名单(c/s/n/finish/until/
  up/down,GDB 同款;kill/run/detach/set args/inject 永不因空行重复);
- 执行路由 ``execute(env, cmd)``:只读命令(bt/info b/info frame/info args/
  info sessions/p/x/frame/up/down)+ 会话命令(run/b/delete/c/s/n/finish/
  until/kill 两段确认)+ D3 干预(set args/inject)+ D4 会话管理(detach/
  rerun/session 切换);enable/disable 照规格诚实回人话(后端缺口)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent_os.host.tui.apps.debugger.trace_rows import short_json, short_skill
from agent_os.host.tui.tui import sty

# ---------------------------------------------------------------------------
# 命令表(help/apropos 从本表生成,§5.1;alias 精确匹配,全名允许唯一前缀)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...]
    usage: str
    summary: str
    ms: str = "D1"  # 落地里程碑(记账用)


COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("run", (), "<skill> [--input <json>] [-b <spec>]… | --replay <rid> [--until N]",
                "起调试会话并开跑(GDB run)", "D1"),
    CommandSpec("break", ("b",), "<spec>",
                "加断点:fs_*(缺省 tool_call)/ skill:fib / step / error / *N(步数断点)", "D1"),
    CommandSpec("info", ("i",), "b|frame|args|sessions", "info 族:断点表 / 帧摘要 / 调用参数 / 会话", "D1"),
    CommandSpec("delete", (), "<N>", "删断点(N = info b 的 Num 列)", "D1"),
    CommandSpec("enable", (), "<N>", "启用断点(后端缺口,诚实拒绝)", "—"),
    CommandSpec("disable", (), "<N>", "禁用断点(后端缺口,诚实拒绝)", "—"),
    CommandSpec("continue", ("c",), "", "继续到下一断点", "D1"),
    CommandSpec("step", ("s",), "", "单步步入(step_into)", "D1"),
    CommandSpec("next", ("n",), "", "单步步过(step_over)", "D1"),
    CommandSpec("finish", ("fin",), "", "步出当前帧(step_out)", "D1"),
    CommandSpec("until", (), "<N>", "直达第 N 条 pre:step(步数断点 + continue)", "D1"),
    CommandSpec("backtrace", ("bt",), "", "调用栈(GDB bt)", "D1"),
    CommandSpec("frame", ("f",), "<N>", "选帧(#0 = 栈顶;选帧即改检视上下文)", "D1"),
    CommandSpec("up", (), "", "向调用者一帧(GDB up)", "D1"),
    CommandSpec("down", (), "", "向被调者一帧(GDB down)", "D1"),
    CommandSpec("print", ("p",), "[<key>]", "暂停点 payload(全量或子键,客户端取值零裁决)", "D1"),
    CommandSpec("x", (), "messages|working", "检视帧 messages / working 全文", "D1"),
    CommandSpec("set", (), "args <json>", "改本次调用参数(仅停在 pre:tool.call;提交即放行)", "D3"),
    CommandSpec("inject", (), "<text>", "注入一条 user 消息(注入即放行)", "D3"),
    CommandSpec("kill", (), "", "中止 run(两段确认:Kill the run being debugged? (y or n))", "D1"),
    CommandSpec("detach", (), "", "放行,run 继续跑完(会话摘下)", "D4"),
    CommandSpec("rerun", (), "", "以同参数重开新会话", "D4"),
    CommandSpec("session", (), "[<SID>]", "已知会话列表(本机账本)/ session <SID> 切换(attach)", "D4"),
    CommandSpec("quit", ("q",), "", "退 TUI(会话保留,不 detach)", "D1"),
    CommandSpec("help", ("h",), "[<cmd>]", "帮助(从命令表生成)", "D1"),
    CommandSpec("apropos", (), "<词>", "按词搜命令", "D1"),
)

_SPEC_BY_NAME = {s.name: s for s in COMMAND_SPECS}
_ALIAS = {a: s.name for s in COMMAND_SPECS for a in s.aliases}

#: info 子命令(alias 精确 + 全名唯一前缀)
_INFO_SUBS: dict[str, tuple[str, ...]] = {
    "breakpoints": ("b",),
    "frame": (),
    "args": (),
    "sessions": (),
}

#: 裸 Enter 重复白名单(§5.2,GDB 同款:步进系可重复,危险命令不重复)
REPEATABLE: frozenset[str] = frozenset({"continue", "step", "next", "finish", "until", "up", "down"})

#: 断点 kind 全集(与 kernel/debug.py BREAKPOINT_KINDS 对齐,不改内核)
_BP_KINDS = ("step", "tool_call", "skill_invoke", "error")


# ---------------------------------------------------------------------------
# 解析(纯函数)
# ---------------------------------------------------------------------------


@dataclass
class Command:
    """解析产物。name = 全名(空串 = 裸 Enter,app 层按 REPEATABLE 白名单处理)。"""
    name: str
    args: list[str] = field(default_factory=list)
    raw: str = ""


class ParseError(Exception):
    """解析失败(机器面;文案由 app 层经 copy 键格式化)。
    kind ∈ unknown / ambiguous / usage。"""

    def __init__(self, kind: str, cmd: str, candidates: list[str] | None = None,
                 usage: str = "") -> None:
        super().__init__(cmd)
        self.kind = kind
        self.cmd = cmd
        self.candidates = candidates or []
        self.usage = usage


@dataclass
class BpSpec:
    """``b <spec>`` 的解析结果(§5.1 spec 语法)。"""
    kind: str
    match: str = "*"
    until: int | None = None


def _resolve_word(word: str, names: list[str], aliases: dict[str, str]) -> str:
    """命令词解析:alias 精确 > 全名精确 > 全名唯一前缀;歧义/未知抛 ParseError。"""
    if word in aliases:
        return aliases[word]
    if word in names:
        return word
    cands = [n for n in names if n.startswith(word)]
    if len(cands) == 1:
        return cands[0]
    if cands:
        raise ParseError("ambiguous", word, sorted(cands))
    raise ParseError("unknown", word)


def parse_bp_spec(spec: str) -> BpSpec:
    """``b <spec>`` spec 语法(§5.1,学 GDB 位置规范):

    - ``b step`` / ``b error``:kind 直给(match 忽略);
    - ``b *N``:until 步数断点(仅 step;GDB ``b *addr`` 的最近邻);
    - ``b skill:<glob>``:skill_invoke 断点;
    - 其余(``b fs_*`` 等):缺省 tool_call,match = 名字 glob。
    """
    text = spec.strip()
    if not text:
        raise ParseError("usage", "break", usage="b <spec>")
    if text == "step":
        return BpSpec("step")
    if text == "error":
        return BpSpec("error")
    if text.startswith("*"):
        num = text[1:]
        if not num.isdigit() or int(num) < 1:
            raise ParseError("usage", "break", usage="b *N(N ≥ 1 的步数断点)")
        return BpSpec("step", until=int(num))
    if text.startswith("skill:"):
        match = text[len("skill:"):]
        if not match:
            raise ParseError("usage", "break", usage="b skill:<glob>")
        return BpSpec("skill_invoke", match)
    return BpSpec("tool_call", text)


def parse(line: str) -> Command:
    """命令行 → Command(纯函数;空行 → Command("") 由 app 层按白名单裁决)。"""
    raw = line.strip()
    if not raw:
        return Command("", [], "")
    words = raw.split()
    name = _resolve_word(words[0], list(_SPEC_BY_NAME), _ALIAS)
    args = words[1:]
    if name == "info":
        if not args:
            raise ParseError("usage", "info", usage="info b|frame|args|sessions")
        sub_alias = {a: n for n, aliases in _INFO_SUBS.items() for a in aliases}
        sub = _resolve_word(args[0], list(_INFO_SUBS), sub_alias)
        return Command("info", [sub, *args[1:]], raw)
    if name == "set" and (not args or args[0] != "args"):
        raise ParseError("usage", "set", usage="set args <json>")
    if name == "x" and (not args or args[0] not in ("messages", "working")):
        raise ParseError("usage", "x", usage="x messages|working")
    return Command(name, args, raw)


# ---------------------------------------------------------------------------
# 执行路由(D1:只读命令 + Demo 脚本化会话命令;其余诚实人话)
# ---------------------------------------------------------------------------


@dataclass
class ExecEnv:
    """执行环境(app 层持有;selected_frame 跨命令持续,GDB 选帧语义)。"""
    source: Any
    selected_frame: int = 0


@dataclass
class ExecResult:
    lines: list[str] = field(default_factory=list)
    quit: bool = False
    kill_confirm: bool = False  # kill 两段确认:置 armed(由 app 层管状态)
    restarted: bool = False  # rerun/session 切换:会话换绑,app 层复位终态簿记


def _copy(key: str, **kw: Any) -> str:
    return sty.copy(key).format(**kw) if kw else sty.copy(key)


def _snapshot(env: ExecEnv) -> dict[str, Any]:
    try:
        return env.source.snapshot()
    except Exception:  # noqa: BLE001 — 数据源故障归命令窗人话(不炸命令窗)
        return {"state": "none", "pause_point": None, "breakpoints": [],
                "frame_stack": [], "end_status": None}


def _stop_line(snap: dict[str, Any]) -> str:
    """停止行(§6;每次暂停自动打印,学 `Breakpoint 1, main () at main.c:4`)。"""
    pp = snap["pause_point"] or {}
    reason = str(pp.get("reason") or "")
    frame = str(pp.get("frame_id") or "-")
    skill = short_skill(pp.get("skill"))
    step_suffix = f", step {pp['step']}" if pp.get("step") is not None else ""
    signal = str(pp.get("signal") or "")
    if pp.get("tool"):
        signal = f"{signal} {pp['tool']}"
    if reason == "breakpoint":
        nums = pp.get("breakpoint_nums") or []
        num = nums[0] if nums else _bp_num(snap, (pp.get("breakpoint_ids") or [None])[0])
        return _copy("dbg.stop.bp", num=num, signal=signal, frame=frame,
                     skill=skill, step_suffix=step_suffix)
    if reason == "pause":
        return _copy("dbg.stop.pause", frame=frame, step_suffix=step_suffix)
    return _copy("dbg.stop.step", signal=signal, frame=frame, skill=skill,
                 step_suffix=step_suffix)


def _bp_num(snap: dict[str, Any], bp_id: Any) -> Any:
    for bp in snap.get("breakpoints") or []:
        if bp.get("id") == bp_id:
            return bp.get("num", "?")
    return "?"


def _advance_lines(env: ExecEnv, before_pp: Any) -> list[str]:
    """会话推进后的汇报(§6):新暂停 → 停止行;run 终结 → 终态行。"""
    snap = _snapshot(env)
    if snap.get("state") == "paused" and snap.get("pause_point") \
            and snap.get("pause_point") != before_pp:
        return [_stop_line(snap)]
    if snap.get("state") != "paused" and snap.get("end_status"):
        lines = [_copy("dbg.end.run", status=snap["end_status"])]
        path_fn = getattr(env.source, "artifacts_path", None)
        path = path_fn() if callable(path_fn) else ""
        if path:
            lines.append(_copy("dbg.end.artifacts", path=path))
        return lines
    return []


def _resume(env: ExecEnv, cmd: str) -> list[str]:
    snap = _snapshot(env)
    if snap.get("state") == "none" or not snap.get("run_id"):
        return [_copy("dbg.err.no_session")]
    if snap.get("state") != "paused":
        return [_copy("dbg.err.not_paused")]
    before = snap.get("pause_point")
    try:
        env.source.command(cmd)
    except PermissionError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001 — 409/404 等统一翻人话(§6)
        return [str(e)]
    echo = _copy("dbg.echo.resumed", cmd=cmd)
    if not getattr(env.source, "sync_control", True):
        return [echo]  # 异步源(live):停点/终态经 SSE·轮询回流(docs/TUI-DEBUG.md §4)
    return [echo, *_advance_lines(env, before)]


def _frames(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """栈帧(顶→底序;#0 = 栈顶,GDB 编号习惯)。"""
    return list(reversed(snap.get("frame_stack") or []))


def _cmd_run(env: ExecEnv, args: list[str]) -> list[str]:
    skill: str | None = None
    run_input: dict[str, Any] = {}
    bps: list[BpSpec] = []
    replay: str | None = None
    until: int | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--input" and i + 1 < len(args):
            try:
                value = json.loads(args[i + 1])
            except json.JSONDecodeError as e:
                return [_copy("dbg.err.usage", usage=f"run --input <json>(JSON 解析错: {e})")]
            if not isinstance(value, dict):
                return [_copy("dbg.err.usage", usage="run --input <json>(须为对象)")]
            run_input = value
            i += 2
        elif a == "-b" and i + 1 < len(args):
            try:
                bps.append(parse_bp_spec(args[i + 1]))
            except ParseError as e:
                return [_copy("dbg.err.usage", usage=e.usage)]
            i += 2
        elif a == "--replay" and i + 1 < len(args):
            replay = args[i + 1]
            i += 2
        elif a == "--until" and i + 1 < len(args) and args[i + 1].isdigit():
            until = int(args[i + 1])
            i += 2
        elif a.startswith("-"):
            return [_copy("dbg.err.usage", usage=_SPEC_BY_NAME["run"].usage)]
        else:
            skill = a
            i += 1
    if not getattr(env.source, "sync_control", True) \
            and any(b.until is not None for b in bps):
        # live REST 断点无 until 字段(DebugBreakpointBody 只有 kind/match)
        return [_copy("dbg.err.usage",
                      usage="run -b *N 在 live 会话不可用(--replay --until N 或 demo 可用)")]
    try:
        if replay is not None:
            env.source.open_replay(replay, until, [(b.kind, b.match) for b in bps])
        else:
            env.source.open_live(skill, run_input, [(b.kind, b.match) for b in bps])
    except PermissionError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001 — 校验失败/冲突等原样转述 error(§6)
        return [str(e)]
    if not getattr(env.source, "sync_control", True):
        return []  # 异步源(live):入口首停经 SSE paused 回流打停止行(§4)
    return _advance_lines(env, None)


def _cmd_break(env: ExecEnv, args: list[str]) -> list[str]:
    if not args:
        return [_copy("dbg.err.usage", usage="b <spec>")]
    try:
        spec = parse_bp_spec(args[0])
    except ParseError as e:
        return [_copy("dbg.err.usage", usage=e.usage)]
    try:
        bp = env.source.add_breakpoint(spec.kind, spec.match, until=spec.until)
    except PermissionError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    match = f"*{spec.until}" if spec.until is not None else spec.match
    return [_copy("dbg.echo.bp_set", num=bp.get("num", "?"), kind=spec.kind, match=match)]


def _cmd_delete(env: ExecEnv, args: list[str]) -> list[str]:
    if not args or not args[0].isdigit():
        return [_copy("dbg.err.usage", usage="delete <N>")]
    num = int(args[0])
    try:
        ok = env.source.remove_breakpoint(num)
    except PermissionError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    if not ok:
        return [_copy("dbg.err.no_bp", num=num)]
    return [_copy("dbg.echo.bp_del", num=num)]


def _cmd_info_b(env: ExecEnv) -> list[str]:
    bps = _snapshot(env).get("breakpoints") or []
    if not bps:
        return [_copy("dbg.empty.bps")]
    lines = [_copy("dbg.info_b.header")]
    for bp in bps:
        enb = "y" if bp.get("enabled", True) else "n"
        lines.append(f"{bp.get('num', '?'):<4}{bp.get('kind', ''):<13}{bp.get('match', ''):<17}"
                     f"{enb:<5}{bp.get('hits', 0)}")
    return lines


def _cmd_bt(env: ExecEnv) -> list[str]:
    snap = _snapshot(env)
    frames = _frames(snap)
    if not frames:
        return [_copy("dbg.empty.stack")]
    pp = snap.get("pause_point") or {}
    paused_fid = pp.get("frame_id") if snap.get("state") == "paused" else None
    lines = []
    for n, f in enumerate(frames):
        fid = str(f.get("frame_id") or "")
        at_suffix = ""
        if fid == paused_fid:
            at = str(pp.get("signal") or "")
            if pp.get("step") is not None:
                at += f" step {pp['step']}"
            at_suffix = f" at {at}"
        lines.append(_copy("dbg.bt.row", n=n, skill=short_skill(f.get("skill")),
                           fid=fid, at_suffix=at_suffix))
    return lines


def _cmd_frame(env: ExecEnv, n: int) -> list[str]:
    frames = _frames(_snapshot(env))
    if not frames:
        return [_copy("dbg.err.no_stack")]
    if not (0 <= n < len(frames)):
        return [_copy("dbg.err.no_frame", num=n)]
    env.selected_frame = n
    f = frames[n]
    return [_copy("dbg.frame.row", n=n, skill=short_skill(f.get("skill")),
                  fid=f.get("frame_id"))]


def _selected_frame_doc(env: ExecEnv) -> dict[str, Any] | None:
    frames = _frames(_snapshot(env))
    if not frames:
        return None
    n = min(max(env.selected_frame, 0), len(frames) - 1)
    env.selected_frame = n
    return frames[n]


def _cmd_print(env: ExecEnv, args: list[str]) -> list[str]:
    snap = _snapshot(env)
    if snap.get("state") != "paused" or not snap.get("pause_point"):
        return [_copy("dbg.err.not_paused")]
    payload = (snap["pause_point"] or {}).get("payload") or {}
    value: Any = payload
    if args:
        for key in args[0].split("."):
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return [_copy("dbg.err.usage", usage=f"p <key>(payload 无此键: {args[0]})")]
    pretty = json.dumps(value, ensure_ascii=False, indent=2, default=repr)
    return pretty.splitlines()


def _frame_full_doc(env: ExecEnv) -> tuple[dict[str, Any] | None, list[str] | None]:
    """选中帧的完整 FrameDoc(source.frame;找不到给命令窗人话)。"""
    entry = _selected_frame_doc(env)
    if entry is None:
        return None, [_copy("dbg.err.no_stack")]
    fid = str(entry.get("frame_id") or "")
    try:
        doc = env.source.frame(fid)
    except Exception as e:  # noqa: BLE001
        return None, [str(e)]
    if doc is None:
        return None, [_copy("dbg.err.no_frame", num=env.selected_frame)]
    return doc, None


def _cmd_x(env: ExecEnv, args: list[str]) -> list[str]:
    doc, err = _frame_full_doc(env)
    if err is not None:
        return err
    assert doc is not None
    if args[0] == "working":
        return json.dumps(doc.get("working"), ensure_ascii=False, indent=2, default=repr).splitlines()
    lines = []
    for m in doc.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "?")
        if m.get("content"):
            lines.append(f"[{role}] {m['content']}")
        elif m.get("tool_calls"):
            for tc in m.get("tool_calls") or []:
                lines.append(f"[{role}] tool_call {tc.get('name')}({short_json(tc.get('args'))})")
        else:
            lines.append(f"[{role}] (empty)")
    return lines or [_copy("dbg.empty.stack")]


def _cmd_info_frame(env: ExecEnv) -> list[str]:
    doc, err = _frame_full_doc(env)
    if err is not None:
        return err
    assert doc is not None
    usage = doc.get("usage") or {}
    lines = [
        f"frame_id: {doc.get('frame_id')}",
        f"skill: {doc.get('skill')}",
        f"status: {doc.get('status')}",
        f"input: {short_json(doc.get('input'))}",
        f"usage: steps={usage.get('steps', 0)} cost={usage.get('cost', 0)}",
    ]
    if doc.get("error"):
        lines.append(f"error: {doc['error']}")
    return lines


def _cmd_info_args(env: ExecEnv) -> list[str]:
    doc, err = _frame_full_doc(env)
    if err is not None:
        return err
    assert doc is not None
    return json.dumps(doc.get("input"), ensure_ascii=False, indent=2, default=repr).splitlines()


def _cmd_until(env: ExecEnv, args: list[str]) -> list[str]:
    if not args or not args[0].isdigit() or int(args[0]) < 1:
        return [_copy("dbg.err.usage", usage="until <N>(N ≥ 1)")]
    n = int(args[0])
    try:
        bp = env.source.add_breakpoint("step", "*", until=n)
    except PermissionError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    echo = _copy("dbg.echo.bp_set", num=bp.get("num", "?"), kind="step", match=f"*{n}")
    return [echo, *_resume(env, "continue")]


def _cmd_set_args(env: ExecEnv, raw: str) -> list[str]:
    """``set args <json>``(§5.1:仅停在 pre:tool.call;提交即放行)。

    JSON 从原始行取(值可含空格);非法/非对象本地拦,不打后端。停点位置
    本地预检(§6 语式两源统一);出海后 409 竞态由数据源翻人话。"""
    parts = raw.split(None, 2)
    if len(parts) < 3:
        return [_copy("dbg.err.usage", usage="set args <json>")]
    try:
        patch = json.loads(parts[2])
    except json.JSONDecodeError as e:
        return [_copy("dbg.err.usage", usage=f"set args <json>(JSON 解析错: {e})")]
    if not isinstance(patch, dict):
        return [_copy("dbg.err.usage", usage="set args <json>(须为对象)")]
    snap = _snapshot(env)
    if snap.get("state") == "none" or not snap.get("run_id"):
        return [_copy("dbg.err.no_session")]
    if snap.get("state") != "paused":
        return [_copy("dbg.err.not_paused")]
    before = snap.get("pause_point") or {}
    if before.get("signal") != "pre:tool.call":
        return [_copy("dbg.err.set_args_pos")]
    try:
        env.source.modify(patch)
    except PermissionError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001 — 409/404 等统一翻人话(§6)
        return [str(e)]
    echo = _copy("dbg.echo.patched", patch=json.dumps(patch, ensure_ascii=False))
    if not getattr(env.source, "sync_control", True):
        return [echo]  # 异步源(live):改后推进经 SSE·轮询回流(§4)
    return [echo, *_advance_lines(env, before)]


def _cmd_inject(env: ExecEnv, raw: str) -> list[str]:
    """``inject <text>``(注入一条 user 消息进暂停帧;注入即放行)。"""
    parts = raw.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        return [_copy("dbg.err.usage", usage="inject <text>")]
    text = parts[1].strip()
    snap = _snapshot(env)
    if snap.get("state") == "none" or not snap.get("run_id"):
        return [_copy("dbg.err.no_session")]
    if snap.get("state") != "paused":
        return [_copy("dbg.err.not_paused")]
    before = snap.get("pause_point") or {}
    try:
        fid = env.source.inject(text) or before.get("frame_id")
    except PermissionError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    echo = _copy("dbg.echo.injected", frame=fid or "-",
                 skill=short_skill(before.get("skill")))
    if not getattr(env.source, "sync_control", True):
        return [echo]
    return [echo, *_advance_lines(env, before)]


def _cmd_rerun(env: ExecEnv) -> tuple[list[str], bool]:
    """rerun(§5.1):以创建参数重开新会话。返回 (行, 是否真换绑)。"""
    snap = _snapshot(env)
    if snap.get("state") == "none" or not snap.get("run_id"):
        return [_copy("dbg.err.no_session")], False
    try:
        out = env.source.rerun()
    except PermissionError as e:
        return [str(e)], False
    except Exception as e:  # noqa: BLE001 — 400(无 origin)/409 等原样转述(§6)
        return [str(e)], False
    echo = _copy("dbg.echo.rerun", sid=out.get("session_id", "?"),
                 run_id=out.get("run_id", "?"))
    if not getattr(env.source, "sync_control", True):
        return [echo], True  # 异步源(live):新会话首停经 SSE 回流(§4)
    return [echo, *_advance_lines(env, None)], True


def _cmd_detach(env: ExecEnv) -> list[str]:
    """detach(§5.1,GDB 逐字对应):放行,run 继续跑完,会话摘下。"""
    snap = _snapshot(env)
    if snap.get("state") == "none" or not snap.get("run_id"):
        return [_copy("dbg.err.no_session")]
    try:
        env.source.detach()
    except PermissionError as e:
        return [str(e)]
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    echo = _copy("dbg.echo.detached")
    if not getattr(env.source, "sync_control", True):
        return [echo]  # 异步源(live):run_end 经 SSE/轮询回流(§4)
    return [echo, *_advance_lines(env, snap.get("pause_point"))]


def _session_lines(env: ExecEnv) -> list[str]:
    """已知会话列表(本机账本,§5.1;新 → 旧)。"""
    store = getattr(env.source, "session_store", None)
    rows = store.list() if store is not None else []
    if not rows:
        return [_copy("dbg.empty.sessions")]
    lines = [_copy("dbg.sessions.header")]
    for r in rows:
        lines.append(f"{r.get('session_id')!s:<14}{r.get('run_id')!s:<12}"
                     f"{r.get('label')!s:<22}{r.get('base_url')}")
    return lines


def _cmd_session(env: ExecEnv, args: list[str]) -> ExecResult:
    """``session``(列表)/ ``session <SID>``(切换 = attach;GDB 无对应,
    SID 直给无形参歧义)。"""
    if not args:
        return ExecResult(_session_lines(env))
    sid = args[0]
    attach = getattr(env.source, "attach", None)
    if not callable(attach):
        return ExecResult([_copy("dbg.err.usage",
                                 usage="session <SID>(切换是 live 会话面)")])
    try:
        out = attach(sid)
    except Exception as e:  # noqa: BLE001 — 404(会话已消逝)等原样转述
        return ExecResult([str(e)])
    return ExecResult([_copy("dbg.echo.attached", sid=out.get("session_id", sid),
                             run_id=out.get("run_id", "?"))], restarted=True)


def _help_lines(args: list[str]) -> list[str]:
    if args:
        try:
            name = _resolve_word(args[0], list(_SPEC_BY_NAME), _ALIAS)
        except ParseError as e:
            return [_copy("dbg.err.unknown", cmd=e.cmd)]
        spec = _SPEC_BY_NAME[name]
        aliases = f"(别名: {', '.join(spec.aliases)})" if spec.aliases else ""
        return [f"{spec.name} {spec.usage}".rstrip() + f"  {aliases}", f"  {spec.summary}"]
    width = max(len(s.name) for s in COMMAND_SPECS)
    lines = [_copy("dbg.help.header")]
    for s in COMMAND_SPECS:
        lines.append(f"  {s.name:<{width}}  {s.usage:<28} {s.summary}".rstrip())
    return lines


def _apropos_lines(args: list[str]) -> list[str]:
    if not args:
        return [_copy("dbg.err.usage", usage="apropos <词>")]
    word = args[0].lower()
    hits = [s for s in COMMAND_SPECS
            if word in s.name.lower() or word in s.summary.lower() or word in s.usage.lower()]
    if not hits:
        return [_copy("dbg.err.unknown", cmd=args[0])]
    return [f"{s.name} {s.usage}  — {s.summary}".rstrip() for s in hits]


def execute_stop(env: ExecEnv) -> list[str]:
    """kill 两段确认的第二击(app 层确认后调):Stop verdict → run aborted。"""
    snap = _snapshot(env)
    if snap.get("state") != "paused":
        return [_copy("dbg.err.not_paused")]
    before = snap.get("pause_point")
    try:
        env.source.command("stop")
    except Exception as e:  # noqa: BLE001
        return [str(e)]
    echo = _copy("dbg.echo.resumed", cmd="stop")
    if not getattr(env.source, "sync_control", True):
        return [echo]  # 异步源(live):run_end 经 SSE/轮询回流(§4)
    return [echo, *_advance_lines(env, before)]


def execute(env: ExecEnv, cmd: Command) -> ExecResult:
    """执行路由(会话命令经 DebugSource 出海;只读命令本地成文)。"""
    name, args = cmd.name, cmd.args
    if not name:
        return ExecResult()  # 裸 Enter 的重复裁决在 app 层(REPEATABLE 白名单)
    if name == "run":
        return ExecResult(_cmd_run(env, args))
    if name == "break":
        return ExecResult(_cmd_break(env, args))
    if name == "info":
        sub = args[0]
        if sub == "breakpoints":
            return ExecResult(_cmd_info_b(env))
        if sub == "frame":
            return ExecResult(_cmd_info_frame(env))
        if sub == "args":
            return ExecResult(_cmd_info_args(env))
        return ExecResult(_session_lines(env))  # info sessions(本机账本)
    if name == "delete":
        return ExecResult(_cmd_delete(env, args))
    if name in ("enable", "disable"):
        return ExecResult([_copy("dbg.err.enable_disable")])
    if name == "continue":
        return ExecResult(_resume(env, "continue"))
    if name == "step":
        return ExecResult(_resume(env, "step_into"))
    if name == "next":
        return ExecResult(_resume(env, "step_over"))
    if name == "finish":
        return ExecResult(_resume(env, "step_out"))
    if name == "until":
        return ExecResult(_cmd_until(env, args))
    if name == "backtrace":
        return ExecResult(_cmd_bt(env))
    if name == "frame":
        if not args or not args[0].lstrip("-").isdigit():
            return ExecResult([_copy("dbg.err.usage", usage="frame <N>")])
        return ExecResult(_cmd_frame(env, int(args[0])))
    if name == "up":
        return ExecResult(_cmd_frame(env, env.selected_frame + 1))
    if name == "down":
        if env.selected_frame <= 0:
            return ExecResult([_copy("dbg.err.no_frame", num=-1)])
        return ExecResult(_cmd_frame(env, env.selected_frame - 1))
    if name == "print":
        return ExecResult(_cmd_print(env, args))
    if name == "x":
        return ExecResult(_cmd_x(env, args))
    if name == "set":
        return ExecResult(_cmd_set_args(env, cmd.raw))
    if name == "inject":
        return ExecResult(_cmd_inject(env, cmd.raw))
    if name == "kill":
        snap = _snapshot(env)
        if snap.get("state") in ("paused", "running"):
            return ExecResult([_copy("dbg.kill.confirm")], kill_confirm=True)
        return ExecResult([_copy("dbg.err.no_session")])
    if name == "detach":
        return ExecResult(_cmd_detach(env))
    if name == "rerun":
        lines, restarted = _cmd_rerun(env)
        return ExecResult(lines, restarted=restarted)
    if name == "session":
        return _cmd_session(env, args)
    if name == "quit":
        snap = _snapshot(env)
        lines = [_copy("dbg.quit.hint")] if snap.get("state") == "paused" else []
        return ExecResult(lines, quit=True)
    if name == "help":
        return ExecResult(_help_lines(args))
    if name == "apropos":
        return ExecResult(_apropos_lines(args))
    return ExecResult([_copy("dbg.err.unknown", cmd=name)])
