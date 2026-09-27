"""ThemePack / ThemeRegistry(docs/TUI-DOC.md §3/§6;对译 godot/kernel/
theme_pack.gd + theme_registry.gd + built_in_themes.gd)。

主题包 = ANSI 256 调色板 + copy 文案表 + 动效档位表;契约清单(色/copy/动效
三族)不过不注册(缺项拒注册,与键位包同机制)。组件零主题分支:组件只消费
语义 token,不出现主题 id 字符串;sizes token 塌缩为格子间距(§6)。

与 godot 原型的差异:colors 值是 ANSI 256 色号(int)而非 Color;内置两包
= classic(契约参考,dark-first)与 terminal(配色参考
host/web/static/css/themes/terminal.css 逐字移植语义,hex → 最近 ANSI 256
色号收口在本文件)。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

# ---------------------------------------------------------------------------
# 动效档位(对译 ThemeRegistry.MOTION_*;reduced-motion 全局强制 INSTANT)
# ---------------------------------------------------------------------------

MOTION_FULL = 0
MOTION_SUBTLE = 1
MOTION_INSTANT = 2

MOTION_NAMES = ("focus-pulse", "change-flash", "text-reveal", "note-arrive",
                "stamp-press", "page-fan", "ui-press", "pop-in", "dock-in",
                "paper-shake", "status-in", "tick",
                # 调试器具名(docs/TUI-DEBUG.md §8,D5):断点命中闪烁 / 暂停脉冲 /
                # 终态定格章;档位归主题档案,组件只叫名字
                "bp-hit", "paused", "run-done")


def hex_to_ansi256(hex_color: str) -> int:
    """#rrggbb → 最近 ANSI 256 色号(6x6x6 立方 + 灰阶;近似即可,主题包是
    数据资产,值以 ANSI 256 为准)。"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def cube(v: int) -> int:
        return 0 if v < 48 else min(5, round((v - 55) / 40)) if v > 55 else 1

    cr, cg, cb = cube(r), cube(g), cube(b)
    cube_code = 16 + 36 * cr + 6 * cg + cb
    # 灰阶候选(232-255:8+10n)
    gray_level = round((r + g + b) / 3)
    gray_code = 232 + max(0, min(23, round((gray_level - 8) / 10))) if 8 <= gray_level <= 238 else None

    def dist(code: int) -> float:
        if code >= 232:
            v = 8 + 10 * (code - 232)
            return (r - v) ** 2 + (g - v) ** 2 + (b - v) ** 2
        i = code - 16
        cc = [0 if x == 0 else 55 + 40 * x for x in (i // 36, (i % 36) // 6, i % 6)]
        return (r - cc[0]) ** 2 + (g - cc[1]) ** 2 + (b - cc[2]) ** 2

    if gray_code is not None and dist(gray_code) < dist(cube_code):
        return gray_code
    return cube_code


class ThemePack:
    """主题包(docs/DEBUG-UI-THEMES.md §2 三层契约):可插拔数据包,组件代码
    永远只有一份。TUI 主题 = 代码贡献(§6 政策);token 值语义移植自
    host/web/static/css/themes/*.css。"""

    def __init__(self) -> None:
        self.id: str = ""
        self.display_name: str = ""
        self.colors: dict[str, int] = {}   # token 名 → ANSI 256 色号
        self.sizes: dict[str, int] = {}    # 排版/间距 token(格子数)
        self.copy: dict[str, str] = {}     # 文案键(翻译层)
        self.motion: dict[str, int] = {}   # 具名动效 → MOTION_*

    def motion_of(self, motion_name: str) -> int:
        return self.motion.get(motion_name, MOTION_SUBTLE)


class ThemeRegistry:
    """主题注册表(§2.5):声明式清单 + 契约校验(缺项 → 不注册)+ 切换持久化
    通道(宿主注入 save_cb;持久化文件归 __main__ 管,注册表不碰盘)+ 顺序循环。
    组件零分支:组件只消费 current 的 token/copy/motion,不出现主题 id 字符串。
    (godot 侧是 static 类;Python 侧模块级单例面,语义同。)"""

    #: 契约清单(§2.1 全集 + 信件模型视图层扩展 token;两主题同约)
    REQUIRED_COLORS: tuple[str, ...] = (
        "bg-0", "bg-1", "bg-2", "bg-3", "line", "line-strong",
        "fg-0", "fg-1", "fg-2",
        "ok", "warn", "danger", "aborted", "live",
        "sig-llm", "sig-tool", "sig-sidecar", "sig-compress", "sig-budget", "sig-frame",
        "perm-read", "perm-write", "perm-net", "perm-exec",
        "focus-ring",
        # 选区底色(字符级框选,T2;用户裁决:注释精度到字符)
        "select",
        # 纸张 token(信件模型视图层;DEBUG-UI-THEMES §2.1 之外的扩展,两主题同约)
        "paper-0", "paper-1", "paper-ink", "paper-ink-2", "paper-line", "seal",
    )

    #: sizes token(格子数;排版 token 在终端塌缩为行/列间距,契约仍守)
    REQUIRED_SIZES: tuple[str, ...] = (
        "text-xs", "text-sm", "text-md", "text-lg", "text-xl",
        "s1", "s2", "s3", "s4", "s6", "s8",
        "r-sm", "r-md", "r-lg",
    )

    #: Doc Editor 用到的 copy 键(doc.* 主题;技术原文豁免直读)
    REQUIRED_COPY: tuple[str, ...] = (
        "doc.list.empty", "doc.new", "doc.create",
        "doc.save", "doc.snapshot", "doc.rewind", "doc.rewind.confirm", "doc.review", "doc.export",
        "doc.status.dirty", "doc.status.saved",
        "doc.comment.placeholder", "doc.comment.send", "doc.comment.apply", "doc.comment.thinking",
        "doc.chat.placeholder", "doc.chat.send",
        "doc.view.edit", "doc.view.preview", "doc.view.split",
        # TUI 键位引导(docs/TUI-DOC.md §1.5:误触的反馈是引导不是报错;copy 走 keymap 键)
        "doc.readonly.hint", "doc.help.title",
        # T2 批注坞(写面纪律:先管道后盖章,状态行人话)
        "doc.intent.later",
        "doc.ann.need_selection", "doc.ann.readonly_source",
        "doc.ann.saved", "doc.ann.deleted", "doc.ann.too_long",
        # T3 版本条/回溯(两段确认;盖章回声;无版本面的诚实提示)
        "doc.rewind.armed", "doc.rewind.done", "doc.versions.none", "doc.diff.same",
        # 调试器 dbg.* 族(docs/TUI-DEBUG.md §6/§8;停止行/错误语式是文案契约,
        # 主题可译但 {signal}/{frame} 等占位符不可吞;技术原文豁免直读)
        "dbg.prompt",
        "dbg.pane.stack", "dbg.pane.bps", "dbg.pane.trace", "dbg.pane.cmd",
        "dbg.state.paused", "dbg.state.running", "dbg.state.ended", "dbg.state.none",
        "dbg.conn.demo", "dbg.conn.offline", "dbg.conn.online",
        "dbg.conn.poll", "dbg.conn.off", "dbg.conn.down_msg",
        "dbg.empty.trace", "dbg.empty.stack", "dbg.empty.bps",
        "dbg.stop.bp", "dbg.stop.step", "dbg.stop.pause",
        "dbg.end.run", "dbg.end.artifacts",
        "dbg.echo.bp_set", "dbg.echo.bp_del", "dbg.echo.resumed",
        "dbg.echo.patched", "dbg.echo.injected",
        "dbg.echo.rerun", "dbg.echo.detached", "dbg.echo.attached",
        "dbg.err.not_paused", "dbg.err.not_running", "dbg.err.no_session",
        "dbg.err.no_stack", "dbg.err.no_bp", "dbg.err.no_frame",
        "dbg.err.ambiguous", "dbg.err.unknown", "dbg.err.usage",
        "dbg.err.enable_disable", "dbg.err.no_bp_target", "dbg.err.set_args_pos",
        "dbg.empty.sessions",
        "dbg.kill.confirm", "dbg.quit.hint", "dbg.help.header",
        # D5 copy 收编:表头/行模板(栈帧行 {n}/{skill}/{fid}/{at_suffix} 占位符不可吞)
        "dbg.bps.header", "dbg.info_b.header", "dbg.sessions.header",
        "dbg.bt.row", "dbg.frame.row",
    )

    _packs: ClassVar[dict[str, ThemePack]] = {}
    _order: ClassVar[list[ThemePack]] = []
    _current: ClassVar[ThemePack | None] = None
    #: 对齐 prefers-reduced-motion:所有具名动效解析为 Instant,对所有主题生效
    reduced_motion: ClassVar[bool] = False
    _listeners: ClassVar[list[Callable[[], None]]] = []
    _save_cb: ClassVar[Callable[[str], None] | None] = None

    @classmethod
    def reset(cls) -> None:
        """清表(测试隔离用;godot 的 static 语义在 Python 进程内要显式复位)。"""
        cls._packs = {}
        cls._order = []
        cls._current = None
        cls.reduced_motion = False
        cls._listeners = []
        cls._save_cb = None

    @classmethod
    def current(cls) -> ThemePack | None:
        return cls._current

    @classmethod
    def validate(cls, pack: ThemePack) -> list[str]:
        """契约:返回缺失项清单;空 = 通过。"""
        missing: list[str] = []
        for c in cls.REQUIRED_COLORS:
            if c not in pack.colors:
                missing.append(f"color:{c}")
        for s in cls.REQUIRED_SIZES:
            if s not in pack.sizes:
                missing.append(f"size:{s}")
        for k in cls.REQUIRED_COPY:
            if k not in pack.copy:
                missing.append(f"copy:{k}")
        return missing

    @classmethod
    def register(cls, pack: ThemePack) -> bool:
        """契约不过不注册。"""
        from agent_os.host.tui.kernel.widget import push_error  # 延迟导入防环

        missing = cls.validate(pack)
        if missing:
            push_error(f"主题 {pack.id} 缺契约项,拒注册:{', '.join(missing)}")
            return False
        if pack.id not in cls._packs:
            cls._order.append(pack)
        cls._packs[pack.id] = pack
        if cls._current is None:
            cls._current = pack
        return True

    @classmethod
    def apply(cls, pack_id: str) -> None:
        if pack_id not in cls._packs:
            return
        cls._current = cls._packs[pack_id]
        if cls._save_cb is not None:
            cls._save_cb(pack_id)
        cls._notify()

    @classmethod
    def cycle(cls) -> str:
        """注册表顺序循环(与 web 托盘主题切换同一通道语义)。"""
        if not cls._order:
            return ""
        i = cls._order.index(cls._current)
        nxt = cls._order[(i + 1) % len(cls._order)]
        cls.apply(nxt.id)
        return nxt.id

    @classmethod
    def restore_saved(cls, saved: str) -> None:
        """启动恢复(宿主读持久化值传入;缺省第一个注册主题)。"""
        if saved and saved in cls._packs:
            cls._current = cls._packs[saved]
        if cls._current is None and cls._order:
            cls._current = cls._order[0]

    @classmethod
    def set_save_cb(cls, cb: Callable[[str], None] | None) -> None:
        cls._save_cb = cb

    @classmethod
    def add_listener(cls, cb: Callable[[], None]) -> None:
        if cb not in cls._listeners:
            cls._listeners.append(cb)

    @classmethod
    def remove_listener(cls, cb: Callable[[], None]) -> None:
        if cb in cls._listeners:
            cls._listeners.remove(cb)

    @classmethod
    def _notify(cls) -> None:
        for cb in cls._listeners:
            cb()


# ---------------------------------------------------------------------------
# 内置主题(classic = 契约参考实现;terminal = 90 年代主机房,terminal.css 移植)
# ---------------------------------------------------------------------------

def _paint(t: ThemePack, hex_map: dict[str, str]) -> None:
    for k, v in hex_map.items():
        t.colors[k] = hex_to_ansi256(v)


def _sizes(t: ThemePack) -> None:
    # 排版 token(终端等宽:数值保留契约,布局消费的是格子间距 s*)
    t.sizes.update({
        "text-xs": 11, "text-sm": 12, "text-md": 14, "text-lg": 16, "text-xl": 20,
        "s1": 1, "s2": 1, "s3": 1, "s4": 2, "s6": 3, "s8": 4,
        "r-sm": 0, "r-md": 0, "r-lg": 0,
    })


def classic_pack() -> ThemePack:
    """classic(dark-first 严肃工程;godot built_in_themes.gd classic 同值,
    hex → ANSI 256)。"""
    t = ThemePack()
    t.id = "classic"
    t.display_name = "严肃工程"
    _paint(t, {
        "bg-0": "#0b0e14", "bg-1": "#11151d", "bg-2": "#171d29", "bg-3": "#1f2837",
        "line": "#263043", "line-strong": "#33405a",
        "fg-0": "#e6ebf2", "fg-1": "#9aa7ba", "fg-2": "#5d6b82",
        "ok": "#3fb68b", "warn": "#d9a03f", "danger": "#e5534b", "aborted": "#9e6bde", "live": "#3b9eff",
        "sig-llm": "#6f9fff", "sig-tool": "#3fb68b", "sig-sidecar": "#d9a03f",
        "sig-compress": "#8b7cf6", "sig-budget": "#e5534b", "sig-frame": "#5d6b82",
        "perm-read": "#5d6b82", "perm-write": "#d9a03f", "perm-net": "#6f9fff", "perm-exec": "#e5534b",
        "focus-ring": "#3b9eff",
        "select": "#24405e",
        "paper-0": "#f5efdf", "paper-1": "#efe7d2", "paper-ink": "#3a352c",
        "paper-ink-2": "#7a7468", "paper-line": "#d8cdb4", "seal": "#c0392b",
    })
    _sizes(t)
    t.copy = {
        "doc.list.empty": "还没有文档",
        "doc.new": "+ 新建文档", "doc.create": "创建",
        "doc.save": "保存", "doc.snapshot": "快照",
        "doc.rewind": "回滚", "doc.rewind.confirm": "确认回滚?",
        "doc.review": "评审", "doc.export": "导出",
        "doc.status.dirty": "未保存", "doc.status.saved": "已保存",
        "doc.comment.placeholder": "就这一段提问…", "doc.comment.send": "发送",
        "doc.comment.apply": "应用此修改", "doc.comment.thinking": "思考中…",
        "doc.chat.placeholder": "告诉 agent 要怎么改这篇文档…", "doc.chat.send": "发送",
        "doc.view.edit": "编辑", "doc.view.preview": "预览", "doc.view.split": "分屏",
        "doc.readonly.hint": "信不可涂改——{annotate} 批注,{open} 回执",
        "doc.help.title": "键位帮助(按任意键关闭)",
        "doc.intent.later": "「{label}」属后续里程碑——T1 只读阅览",
        "doc.ann.need_selection": "先框选({mark})再批注;光标落在既有批注上可重编",
        "doc.ann.readonly_source": "本地/演示面只读,批注需在线服务",
        "doc.ann.saved": "批注已出海存讫",
        "doc.ann.deleted": "批注已删",
        "doc.ann.too_long": "批注超长(≤500 字),未发送",
        "doc.rewind.armed": "再按 {confirm} 确认回溯到 {version}(历史不动)",
        "doc.rewind.done": "已回溯到 {version}(版本历史未动)",
        "doc.versions.none": "此面无版本(本地文件/演示面)",
        "doc.diff.same": "与工作稿一致",
        **_DBG_COPY,
    }
    t.motion = {name: MOTION_SUBTLE for name in MOTION_NAMES}
    # 调试器三具名:暂停/终态在 classic 给足 FULL(严肃工程的确认感),
    # bp-hit 常驻事件保持 SUBTLE 短闪;terminal 全 SUBTLE(克制主题)
    t.motion.update({"paused": MOTION_FULL, "run-done": MOTION_FULL})
    return t


def terminal_pack() -> ThemePack:
    """terminal(磷光绿主机房;host/web/static/css/themes/terminal.css §3.3
    的 token 逐字移植,hex → ANSI 256;动效全 subtle,克制主题)。"""
    t = ThemePack()
    t.id = "terminal"
    t.display_name = "主机房"
    _paint(t, {
        # 基底:近黑深绿(磷光屏底)
        "bg-0": "#0a0f0a", "bg-1": "#101a12", "bg-2": "#15251a", "bg-3": "#1d3020",
        "line": "#1e3a26", "line-strong": "#2e5a3a",
        "fg-0": "#33ff66", "fg-1": "#7fe89a", "fg-2": "#4a8a56",
        "ok": "#33ff66", "warn": "#ffc857", "danger": "#ff5555", "aborted": "#c678dd", "live": "#40d0ff",
        "sig-llm": "#66aaff", "sig-tool": "#33ff66", "sig-sidecar": "#ffc857",
        "sig-compress": "#c678dd", "sig-budget": "#ff5555", "sig-frame": "#4a8a56",
        "perm-read": "#4a8a56", "perm-write": "#ffc857", "perm-net": "#66aaff", "perm-exec": "#ff5555",
        "focus-ring": "#40d0ff",
        # 选区底(磷光绿屏上的深绿块;= terminal.css 暂停行底色)
        "select": "#16301e",
        # 纸张 token(磷光屏上的信纸:深绿纸 + 亮绿墨;两主题同约的扩展面)
        "paper-0": "#0d140d", "paper-1": "#101a12", "paper-ink": "#33ff66",
        "paper-ink-2": "#4a8a56", "paper-line": "#1e3a26", "seal": "#ff5555",
    })
    _sizes(t)
    t.copy = {
        "doc.list.empty": "队列里还没有信件",
        "doc.new": "+ 新建文档", "doc.create": "创建",
        "doc.save": "保存", "doc.snapshot": "快照",
        "doc.rewind": "回滚", "doc.rewind.confirm": "确认回滚?",
        "doc.review": "评审", "doc.export": "导出",
        "doc.status.dirty": "未保存", "doc.status.saved": "已保存",
        "doc.comment.placeholder": "就这一段提问…", "doc.comment.send": "发送",
        "doc.comment.apply": "应用此修改", "doc.comment.thinking": "…",
        "doc.chat.placeholder": "写给编者…", "doc.chat.send": "发送",
        "doc.view.edit": "编辑", "doc.view.preview": "预览", "doc.view.split": "分屏",
        "doc.readonly.hint": "只读信纸——{annotate} 批注,{open} 回执",
        "doc.help.title": "键位一览(按任意键关闭)",
        "doc.intent.later": "「{label}」尚未接线——本期只读",
        "doc.ann.need_selection": "先框选({mark})再批注",
        "doc.ann.readonly_source": "本地/演示面只读,批注需在线服务",
        "doc.ann.saved": "批注已存",
        "doc.ann.deleted": "批注已删",
        "doc.ann.too_long": "批注超长(≤500 字),未发送",
        "doc.rewind.armed": "再按 {confirm} 确认回溯到 {version}",
        "doc.rewind.done": "已回溯到 {version}",
        "doc.versions.none": "此面无版本",
        "doc.diff.same": "与工作稿一致",
        **_DBG_COPY,
        **_DBG_COPY_TERMINAL,
    }
    t.motion = {name: MOTION_SUBTLE for name in MOTION_NAMES}
    return t


#: 调试器 copy(docs/TUI-DEBUG.md §6/§8;两内置主题同值——停止行/错误语式
#: 逐字学 GDB,是文案契约;占位符 {signal}/{frame}/{step_suffix} 等不可吞)
_DBG_COPY: dict[str, str] = {
    "dbg.prompt": "(adb) ",
    "dbg.pane.stack": "调用栈 (bt)",
    "dbg.pane.bps": "断点 (info b)",
    "dbg.pane.trace": "执行轨迹",
    "dbg.pane.cmd": "命令",
    "dbg.state.paused": "PAUSED",
    "dbg.state.running": "RUNNING",
    "dbg.state.ended": "ENDED",
    "dbg.state.none": "NO RUN",
    "dbg.conn.demo": "─ demo",
    "dbg.conn.offline": "─ offline",
    "dbg.conn.online": "● SSE",
    "dbg.conn.poll": "○ poll",
    "dbg.conn.off": "─ off",
    "dbg.conn.down_msg": "调试实时连接中断,回退轮询",
    "dbg.empty.trace": "(无信号——run 之后这里出轨迹)",
    "dbg.empty.stack": "(帧栈为空)",
    "dbg.empty.bps": "(无断点)",
    "dbg.stop.bp": "Breakpoint {num}, {signal}, frame {frame} ({skill}){step_suffix}",
    "dbg.stop.step": "Step finished, {signal}, frame {frame} ({skill}){step_suffix}",
    "dbg.stop.pause": "Run paused by user (pause), frame {frame}{step_suffix}",
    "dbg.end.run": "Run finished: {status}",
    "dbg.end.artifacts": "artifacts: {path}",
    "dbg.echo.bp_set": "Breakpoint {num} set: {kind} {match}",
    "dbg.echo.bp_del": "Breakpoint {num} deleted.",
    "dbg.echo.resumed": "resumed ({cmd})",
    "dbg.echo.patched": "args patched: {patch}(提交即放行)",
    "dbg.echo.injected": "injected -> frame {frame} ({skill})(注入即放行)",
    "dbg.echo.rerun": "rerun -> 新会话 {sid} · run {run_id}",
    "dbg.echo.detached": "detached(放行,run 继续跑完;会话已摘下)",
    "dbg.echo.attached": "attached -> {sid}(run {run_id})",
    "dbg.err.not_paused": "The run is not paused.",
    "dbg.err.not_running": "The run is not running.",
    "dbg.err.no_session": "The run is not being debugged.",
    "dbg.err.no_stack": "No stack.",
    "dbg.err.no_bp": "No breakpoint number {num}.",
    "dbg.err.no_frame": "No frame number {num}.",
    "dbg.err.ambiguous": "Ambiguous command \"{cmd}\": {candidates}.",
    "dbg.err.unknown": "Undefined command: \"{cmd}\". Try \"help\".",
    "dbg.err.usage": "Usage: {usage}",
    "dbg.err.enable_disable": "Not supported: delete and re-add.",
    "dbg.err.no_bp_target": "No breakpoint target on this line.",
    "dbg.err.set_args_pos": "Cannot set args: not paused at pre:tool.call.",
    "dbg.kill.confirm": "Kill the run being debugged? (y or n)",
    "dbg.quit.hint": "(退出不 detach;paused 会话保留给下次连接)",
    "dbg.empty.sessions": "(无已知会话——本机账本为空;run 过才会记)",
    "dbg.help.header": "命令(GDB 方言;唯一前缀可缩写;裸 Enter 重复步进类命令):",
    # D5 收编:断点表/会话表表头与栈帧行模板(列宽对译 GDB info b;
    # 命令窗与窗格两套表头列宽不同,各自一键)
    "dbg.bps.header": "Num  Kind         Match        Enb Hits",
    "dbg.info_b.header": "Num  Kind         Match            Enb  Hits",
    "dbg.sessions.header": "Session       Run         Label                 Base",
    "dbg.bt.row": "#{n}  {skill} ({fid}){at_suffix}",
    "dbg.frame.row": "#{n}  {skill} ({fid})",
}


#: terminal 包的 dbg 窗格标题差异面(磷光机房大写铭文;停止行/错误语式是
#: GDB 逐字契约两包同值,窗格标题/空态属可译文案——换肤要有可观测面)
_DBG_COPY_TERMINAL: dict[str, str] = {
    "dbg.pane.stack": "STACK (bt)",
    "dbg.pane.bps": "BREAKS (info b)",
    "dbg.pane.trace": "TRACE",
    "dbg.pane.cmd": "CMD",
    "dbg.bps.header": "NUM  KIND         MATCH        ENB HITS",
    "dbg.info_b.header": "NUM  KIND         MATCH            ENB  HITS",
    "dbg.sessions.header": "SESSION       RUN         LABEL                 BASE",
}


def register_built_in() -> None:
    """注册内置两包(classic 契约参考在前;terminal 证明可插拔)。"""
    ThemeRegistry.register(classic_pack())
    ThemeRegistry.register(terminal_pack())
