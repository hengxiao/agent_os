"""agent-os-tui 入口(docs/TUI-DOC.md §2/§5.4/§10;T1 里程碑)。

主循环:键解码 → keymap 解析为 intent → app/widget 响应 intent → render()
全量重渲 cell buffer → 输出(T1 全量;diff 输出留后续优化口)。

flags:位置参数 ``FILE...``(本地文件直查,只读)/ ``--base-url`` /
``--offline --docs-root <dir>`` / ``--demo`` / ``--keymap vim|emacs`` /
``--reduced-motion``。默认 keymap 探测
$EDITOR/$VISUAL(含 emacs → emacs,否则 vim),持久化到
``~/.config/agent-os/tui.toml``(最小 toml 读写,零依赖)。

终端纪律:raw 模式进出场必须可靠恢复(termios + 备用屏幕 + 光标,
atexit + signal 双通道);进 raw 关 IXON 放出 C-s/C-q(§5.1)。
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

from agent_os.host.tui.apps.debugger.app import build_app as build_debugger_app
from agent_os.host.tui.apps.debugger.commands import ParseError, parse_bp_spec
from agent_os.host.tui.apps.debugger.model import (
    DEBUG_DEFAULT_BASE_URL,
    SESSION_STORE_PATH,
    DemoDebugSource,
    OfflineDebugSource,
    OnlineDebugSource,
    SessionStore,
)
from agent_os.host.tui.apps.doc_editor.app import build_app
from agent_os.host.tui.apps.doc_editor.model import (
    DemoDocSource,
    DocSource,
    FileDocSource,
    OfflineDocSource,
    OnlineDocSource,
)
from agent_os.host.tui.kernel.client import DEFAULT_BASE_URL, AgentOsClient
from agent_os.host.tui.tui.cells import CellBuffer, Region
from agent_os.host.tui.tui.keymap import KeymapRegistry
from agent_os.host.tui.tui.keys import TerminalSession

#: 动效帧节奏(秒;focus-pulse 重绘周期)
FRAME_INTERVAL = 0.06
#: 空闲心跳(秒;SIGWINCH 等事件兜底重渲周期)
IDLE_INTERVAL = 0.5

CONFIG_PATH = Path.home() / ".config" / "agent-os" / "tui.toml"


# ---------------------------------------------------------------------------
# 用户配置(最小 toml 读写,不引依赖;持久化面 = keymap,语义同 godot user://)
# ---------------------------------------------------------------------------

def load_config(path: Path = CONFIG_PATH) -> dict[str, str]:
    """读 ``key = "value"`` 行(最小面;坏行跳过,文件不在 → {})。"""
    out: dict[str, str] = {}
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            key, _, value = ln.partition("=")
            value = value.strip().strip('"').strip("'")
            if key.strip() and value:
                out[key.strip()] = value
    except OSError:
        pass
    return out


def save_config_value(key: str, value: str, path: Path = CONFIG_PATH) -> None:
    """覆盖写单键(已有配置保留其余行;目录自动建)。"""
    lines: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        pass
    out: list[str] = []
    replaced = False
    for ln in lines:
        if ln.strip().startswith(f"{key} =") or ln.strip().startswith(f"{key}="):
            out.append(f'{key} = "{value}"')
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f'{key} = "{value}"')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except OSError:
        pass  # 配置写不进不挡运行(偏好持久化是便利面)


def detect_keymap() -> str:
    """$EDITOR/$VISUAL 含 emacs → emacs 包,否则 vim 包(§5.4)。"""
    editor = (os.environ.get("EDITOR") or os.environ.get("VISUAL") or "").lower()
    return "emacs" if "emacs" in editor else "vim"


def resolve_keymap(flag: str | None) -> str:
    """优先级:--keymap > 持久化 > 探测。"""
    if flag:
        return flag
    saved = load_config().get("keymap", "")
    if saved in ("vim", "emacs"):
        return saved
    return detect_keymap()


# ---------------------------------------------------------------------------
# 数据源装配(位置参数 files > --demo > --offline > 在线 client)
# ---------------------------------------------------------------------------

def build_source(args: argparse.Namespace) -> DocSource:
    if args.files:
        return FileDocSource(args.files)
    if args.demo:
        return DemoDocSource()
    if args.offline:
        if not args.docs_root:
            raise SystemExit("--offline 需要 --docs-root <dir>(离线只读回放的产物目录)")
        return OfflineDocSource(args.docs_root)
    return OnlineDocSource(AgentOsClient(args.base_url))


# ---------------------------------------------------------------------------
# 终端进出场(raw + 备用屏幕 + 光标;atexit + signal 双通道可靠恢复)
# ---------------------------------------------------------------------------

class TerminalGuard:
    """raw 模式外壳:enter 进,exit/信号/atexit 三路都归 restore(幂等)。"""

    def __init__(self) -> None:
        self.session: TerminalSession | None = None
        self._restored = False

    def __enter__(self) -> TerminalSession:
        self.session = TerminalSession()
        self.session.__enter__()
        out = sys.stdout
        out.write("\x1b[?1049h")  # 备用屏幕
        out.write("\x1b[?25l")    # 隐光标
        out.flush()
        atexit.register(self.restore)
        return self.session

    def __exit__(self, *_exc: object) -> None:
        self.restore()

    def restore(self) -> None:
        if self._restored:
            return
        self._restored = True
        try:
            sys.stdout.write("\x1b[?25h\x1b[?1049l")  # 出备用屏幕 + 还光标
            sys.stdout.flush()
        except OSError:
            pass
        if self.session is not None:
            self.session.restore()


# ---------------------------------------------------------------------------
# 主循环(§2:键解码 → intent → render 全量重渲 → 输出)
# ---------------------------------------------------------------------------

def run_loop(args: argparse.Namespace, builder=build_app,
             source: object | None = None) -> int:
    """主循环(§2:键解码 → intent → render 全量重渲 → 输出)。
    builder/source 可注入:doc editor 与 debugger(debug 子命令)共用本循环。"""
    if source is None:
        source = build_source(args)
    keymap_id = resolve_keymap(args.keymap)
    tree, app, engine, motion = builder(
        source, keymap_id=keymap_id, reduced_motion=args.reduced_motion)
    del tree  # 寻址/cascade 备 T5 agent 面;主循环只经 app
    KeymapRegistry.set_save_cb(lambda pack_id: save_config_value("keymap", pack_id))

    resized = {"flag": False}

    def on_winch(_sig: int, _frame: object) -> None:
        resized["flag"] = True  # 促醒:尺寸在 _render_once 现取,心跳兜底重渲

    def on_quit(_sig: int, _frame: object) -> None:
        app.mutate_state(lambda st: st.update(quit=True))

    guard = TerminalGuard()
    with guard as term:
        old_winch = signal.signal(signal.SIGWINCH, on_winch)
        old_int = signal.signal(signal.SIGINT, on_quit)
        old_term = signal.signal(signal.SIGTERM, on_quit)
        try:
            while not app.state.get("quit"):
                tick = getattr(app, "tick", None)
                if callable(tick):
                    tick()  # D2:调试器 SSE queue drain/断线回落(doc editor 无此钩子)
                resized["flag"] = False
                _render_once(app, motion)
                timeout = FRAME_INTERVAL if motion.busy() or engine.pending() else IDLE_INTERVAL
                events = term.read_events(timeout=timeout)
                if not events:
                    engine.feed_timeout()
                    motion.tick()
                    continue
                for ev in events:
                    app.feed_key(ev)
        finally:
            signal.signal(signal.SIGWINCH, old_winch)
            signal.signal(signal.SIGINT, old_int)
            signal.signal(signal.SIGTERM, old_term)
    return 0


def _render_once(app, motion) -> None:
    """全量重渲一帧:先清后建(§3);尺寸现取(SIGWINCH 友好)。"""
    size = shutil.get_terminal_size((80, 24))
    buf = CellBuffer(size.columns, size.lines)
    app.render_into(buf, Region(0, 0, buf.width, buf.height))
    out = sys.stdout
    out.write("\x1b[H")  # 光标归位(备用屏幕内全量覆盖,无需清屏序列)
    out.write(buf.to_ansi())
    out.flush()


# ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-os-tui",
        description="Agent OS 终端 TUI Doc Editor(docs/TUI-DOC.md;T1:信箱/信纸只读阅览)"
                    "(子命令: debug = TUI agent 调试器,docs/TUI-DEBUG.md)",
    )
    parser.add_argument("files", nargs="*", metavar="FILE",
                        help="直接查看的本地文件(markdown 最小处理,只读;"
                             "给文件即不看 --demo/--offline/在线源)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"web_platform base(含 /platform 前缀;缺省 {DEFAULT_BASE_URL})")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式:直读 --docs-root 产物目录(只读回放,无任何写面)")
    parser.add_argument("--docs-root", default="",
                        help="离线模式的文档根(DocStore 布局;<artifacts_root>/docs)")
    parser.add_argument("--demo", action="store_true",
                        help="内置两篇示例文档,无需服务即可体验")
    parser.add_argument("--keymap", choices=["vim", "emacs"], default=None,
                        help="键位包(缺省:持久化值 > $EDITOR/$VISUAL 探测)")
    parser.add_argument("--reduced-motion", action="store_true",
                        help="动效全局强制 instant(对齐 prefers-reduced-motion)")
    return parser


# ---------------------------------------------------------------------------
# debug 子命令(docs/TUI-DEBUG.md;D2:--demo / --offline --run-dir / live)
# ---------------------------------------------------------------------------

def _debug_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-os-tui debug",
        description="TUI agent 调试器(docs/TUI-DEBUG.md;GDB 命令语言,(adb) 提示符)",
    )
    parser.add_argument("--demo", action="store_true",
                        help="内置 demo.fib n=3 脚本化假会话,无服务可体验全交互")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式:直读 --run-dir 的 run 产物(只读回放,无控制面)")
    parser.add_argument("--run-dir", default="",
                        help="离线模式的 run 产物目录(<artifacts>/runs/<id>)")
    parser.add_argument("--skill", default=None, metavar="S",
                        help="live:开会话即调试技能 S(缺省进 TUI 后 (adb) run <skill>)")
    parser.add_argument("--input", default=None, metavar="JSON",
                        help="live:技能输入 JSON(对象;缺省 {})")
    parser.add_argument("-b", "--breakpoint", dest="breakpoints", action="append",
                        default=[], metavar="SPEC",
                        help="live:启动断点(run 前注册,可重复;spec 语法同 b 命令)")
    parser.add_argument("--session", default=None, metavar="SID",
                        help="live:挂既有调试会话 SID(attach)")
    parser.add_argument("--replay", default=None, metavar="RUN_ID",
                        help="replay:回放指定 run 并挂调试会话")
    parser.add_argument("--until", type=int, default=None, metavar="N",
                        help="replay:直达第 N 条 pre:step 才暂停(一次性步数断点)")
    parser.add_argument("--base-url", default=DEBUG_DEFAULT_BASE_URL,
                        help=f"agent-os-web base(/api/debug/* 无前缀;缺省 {DEBUG_DEFAULT_BASE_URL})")
    parser.add_argument("--keymap", choices=["vim", "emacs"], default=None,
                        help="键位包(缺省:持久化值 > $EDITOR/$VISUAL 探测)")
    parser.add_argument("--reduced-motion", action="store_true",
                        help="动效全局强制 instant(对齐 prefers-reduced-motion)")
    return parser


def build_debug_source(args: argparse.Namespace):
    """debug 子命令的数据源:--demo > --offline > live(OnlineDebugSource)。"""
    if args.demo:
        return DemoDebugSource()
    if args.offline:
        if not args.run_dir:
            raise SystemExit("--offline 需要 --run-dir <dir>(run 产物目录)")
        return OfflineDebugSource(args.run_dir)
    return OnlineDebugSource(args.base_url,
                             # 已知会话本机账本(§5.1 session/info sessions)
                             store=SessionStore(SESSION_STORE_PATH))


def _start_live(source: OnlineDebugSource, args: argparse.Namespace) -> None:
    """live 入口(§0/§3):连通性 fail-fast → 按旗标开会话/挂载/回放。
    服务不可达/参数非法 → RuntimeError 人话(_main_debug 转 stderr + exit 2),
    不卡在空白屏。"""
    err = source.ping()
    if err is not None:
        raise RuntimeError(f"连不上 agent-os-web({args.base_url}):{err}")
    bps: list[tuple[str, str]] = []
    for raw in args.breakpoints:
        try:
            spec = parse_bp_spec(raw)
        except ParseError as e:
            raise RuntimeError(f"-b 断点 spec 非法:{e.usage or raw}") from e
        if spec.until is not None:
            raise RuntimeError(f"-b *N 在 live 启动断点不可用({raw};"
                               "until 仅 --replay --until N)")
        bps.append((spec.kind, spec.match))
    if args.session:
        source.attach(args.session)
        return
    if args.replay:
        source.open_replay(args.replay, args.until, bps)
        return
    if args.skill:
        run_input: dict[str, Any] = {}
        if args.input:
            try:
                value = json.loads(args.input)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"--input JSON 解析错: {e}") from e
            if not isinstance(value, dict):
                raise RuntimeError("--input 须为 JSON 对象")
            run_input = value
        source.open_live(args.skill, run_input, bps)
    # 都没给:进 TUI 无会话,(adb) run <skill> 手动开


def _main_debug(argv: list[str]) -> int:
    args = _debug_parser().parse_args(argv)
    if not sys.stdin.isatty():
        print("agent-os-tui debug 需要终端(stdin 非 tty)", file=sys.stderr)
        return 2
    try:
        source = build_debug_source(args)
        if isinstance(source, OnlineDebugSource):
            _start_live(source, args)  # fail-fast:服务不可达不卡在空白屏
    except RuntimeError as e:
        print(f"agent-os-tui debug: {e}", file=sys.stderr)
        return 2
    try:
        return run_loop(args, builder=build_debugger_app, source=source)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    """入口(``agent-os-tui = agent_os.host.tui.__main__:main``)。
    首参为 ``debug`` 时进调试器子命令;否则行为与既有 doc editor 完全不变。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "debug":
        return _main_debug(argv[1:])
    args = _parser().parse_args(argv)
    if not sys.stdin.isatty():
        # 要终端(无头冒烟走 build_app,不经这里)
        print("agent-os-tui 需要终端(stdin 非 tty)", file=sys.stderr)
        return 2
    try:
        return run_loop(args)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
