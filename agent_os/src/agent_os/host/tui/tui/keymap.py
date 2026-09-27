"""KeymapPack / KeymapRegistry / KeymapEngine(docs/TUI-DOC.md §5)。

键位层与主题包同构:**组件绑定意图(intent),不绑定物理键**;"组件零按键
分支"与"组件零主题分支"是同一条红线。

- ``KeymapPack = {id, modal, bindings: {context → {键序 → intent}}, commands, copy}``
- context 分级:global / index / letter / dock / input / command;子级缺省向
  global 回落;
- **契约清单 = 必备 intent 集合**,缺绑定 = 拒注册(与主题"缺项拒注册"同机制);
  WidgetDef 声明自己响应的 intent 集合,注册时校验落在活动包的 intent 面内;
- hint 栏与 ``?`` 帮助面板**从活动 keymap 生成**,永不硬编码;
- 键引擎持前缀/和弦态 + 超时(``gg``、``C-x C-s``、``<leader>g``)。

键序记法:空格分隔的和弦序列;和弦 = ``C-x`` / ``M-x`` / ``C-M-x`` / 裸键
(单字符本体或 ``enter``/``esc``/``up`` 等具名键)。vim 的 ``<leader>`` 取
缺省 ``\\``(显示面翻译回 ``<leader>``)。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import ClassVar

from agent_os.host.tui.tui.keys import KeyEvent

#: context 分级(§5.1);子级缺省向 global 回落。
#: dbg-* 三个是调试器 app 的窗格 context(docs/TUI-DEBUG.md §5.3):
#: dbg-cmd = 命令窗(INSERT 常驻),dbg-trace / dbg-stack = 只读窗。
CONTEXTS = ("global", "index", "letter", "dock", "input", "command",
            "dbg-cmd", "dbg-trace", "dbg-stack")

#: 前缀/和弦超时(秒;vim timeoutlen 同款语义)
CHORD_TIMEOUT = 1.0

#: vim <leader> 的物理键(缺省 \\;显示面翻回 <leader>)
LEADER = "\\"

#: 契约清单 = 必备 intent 集合(§5.1;缺绑定 = 拒注册)。
#: 覆盖途径:bindings 直接或弦,或 commands 命令条别名(vim 的 :w 是后者)。
#: T2 扩员:字符级移动/词/段/框选(用户裁决:注释精度到字符,光标到字符)
#: + 撰写控件编辑面(edit_back/edit_delete/newline/confirm/cancel)。
REQUIRED_INTENTS: tuple[str, ...] = (
    "move_left", "move_right", "move_up", "move_down",
    "line_start", "line_end", "word_forward", "word_backward",
    "para_forward", "para_backward",
    "goto_top", "goto_bottom", "page_down", "page_up",
    "mark", "open", "quit", "command", "help",
    "annotate", "save", "generate", "review", "versions",
    "edit_back", "edit_delete", "newline", "confirm", "cancel",
    "tree",
    # 调试器(docs/TUI-DEBUG.md §5.3):C-x o 切焦点 / 焦点窗滚动 / 裸 Enter
    # 重复 / C-c = pause(SIGINT)/ 轨迹行 b = bp_toggle / C-l 重绘;
    # 命令窗 readline 基本键(C-u 删行 / C-w 删词 / ↑↓ 历史)
    "focus_next", "scroll_up", "scroll_down", "repeat_last", "interrupt",
    "bp_toggle", "redraw",
    "edit_kill_line", "edit_kill_word", "history_prev", "history_next",
)

#: 引擎级保留 intent(不占契约):input 族 context 里未绑定的可打印键
SELF_INSERT = "self_insert"


def parse_chord(token: str) -> KeyEvent:
    """和弦记法 → KeyEvent(规范化;bindings 表与 KeyEvent.chord() 的共同基)。"""
    ctrl = meta = False
    key = token
    while True:
        if key.startswith("C-M-"):
            ctrl = meta = True
            key = key[4:]
        elif key.startswith("C-"):
            ctrl = True
            key = key[2:]
        elif key.startswith("M-"):
            meta = True
            key = key[2:]
        else:
            break
    if key == "space":
        key = " "
    return KeyEvent(key, ctrl=ctrl, meta=meta)


def parse_sequence(spec: str) -> tuple[KeyEvent, ...]:
    """键序记法 → KeyEvent 元组("C-x C-s" / "g g" / "\\ g")。"""
    return tuple(parse_chord(tok) for tok in spec.split() if tok)


def display_chord(ev: KeyEvent) -> str:
    """KeyEvent → 人读和弦(hint/帮助面板用;\\ 翻回 <leader>,具名键首字母
    大写:C-enter → C-Enter,C-space → C-Space)。"""
    if ev.key == LEADER and not ev.ctrl and not ev.meta:
        return "<leader>"
    key = ev.key.capitalize() if len(ev.key) > 1 else ev.key
    mods = ("C-" if ev.ctrl else "") + ("M-" if ev.meta else "")
    return mods + key


def display_sequence(seq: tuple[KeyEvent, ...]) -> str:
    """键序 → 人读串:连续裸单字符并拢("g g"→"gg"),其余空格分隔。"""
    parts: list[str] = []
    mergeable: list[bool] = []
    for ev in seq:
        d = display_chord(ev)
        bare = len(d) == 1 and not ev.ctrl and not ev.meta
        if parts and bare and mergeable[-1]:
            parts[-1] += d
        else:
            parts.append(d)
            mergeable.append(bare)
    return " ".join(parts)


class KeymapPack:
    """键位包(§5.1):可插拔数据包;组件零按键分支。"""

    def __init__(self) -> None:
        self.id: str = ""
        self.display_name: str = ""
        self.modal: bool = False
        #: {context: {键序 spec: intent}}
        self.bindings: dict[str, dict[str, str]] = {}
        #: 命令条别名 {命令词: intent}(vim ":w" = save 盖章,§5.2)
        self.commands: dict[str, str] = {}
        #: intent → 人读标签(hint 栏/帮助面板的文案;翻译层)
        self.copy: dict[str, str] = {}
        #: 命令条专属文案 {key: text}(如 command.prompt)
        self.messages: dict[str, str] = {}

    def intents(self) -> set[str]:
        """本包声明的 intent 全集(bindings + commands;widget intent 校验面)。"""
        out = set(self.commands.values())
        for ctx in self.bindings.values():
            out.update(ctx.values())
        return out

    def parsed_bindings(self) -> dict[str, dict[tuple[KeyEvent, ...], str]]:
        """解析缓存:{context: {(KeyEvent,…): intent}}(spec 一次性归一化)。"""
        return {
            ctx: {parse_sequence(spec): intent for spec, intent in table.items()}
            for ctx, table in self.bindings.items()
        }


class KeymapRegistry:
    """键位注册表(§5.1):契约校验(必备 intent 缺绑定 → 不注册)+ 切换持久化
    通道(save_cb 归宿主;与 ThemeRegistry 同构)。"""

    _packs: ClassVar[dict[str, KeymapPack]] = {}
    _order: ClassVar[list[KeymapPack]] = []
    _current: ClassVar[KeymapPack | None] = None
    _save_cb: ClassVar[Callable[[str], None] | None] = None
    _listeners: ClassVar[list[Callable[[], None]]] = []

    @classmethod
    def reset(cls) -> None:
        cls._packs = {}
        cls._order = []
        cls._current = None
        cls._save_cb = None
        cls._listeners = []

    @classmethod
    def current(cls) -> KeymapPack | None:
        return cls._current

    @classmethod
    def validate(cls, pack: KeymapPack) -> list[str]:
        """契约:返回缺失项清单;空 = 通过。必备 intent 允许由 bindings 或
        commands 别名覆盖(§5.2 vim 保存 = :w 命令条别名)。"""
        covered = pack.intents()
        missing = [f"intent:{i}" for i in REQUIRED_INTENTS if i not in covered]
        for intent in REQUIRED_INTENTS:
            if intent in covered and intent not in pack.copy:
                missing.append(f"copy:{intent}")
        for ctx in pack.bindings:
            # 模式子表("dock:normal"/"dock:insert";modal 包撰写控件用)
            base = ctx.split(":", 1)[0]
            if base not in CONTEXTS:
                missing.append(f"context:{ctx}(越界,只允许 {'/'.join(CONTEXTS)}[:mode])")
        return missing

    @classmethod
    def register(cls, pack: KeymapPack) -> bool:
        from agent_os.host.tui.kernel.widget import push_error  # 延迟导入防环

        missing = cls.validate(pack)
        if missing:
            push_error(f"keymap {pack.id} 缺契约项,拒注册:{', '.join(missing)}")
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
    def restore_saved(cls, saved: str) -> None:
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
    def _notify(cls) -> None:
        for cb in cls._listeners:
            cb()

    @classmethod
    def pack_ids(cls) -> list[str]:
        return [p.id for p in cls._order]


# ---------------------------------------------------------------------------
# 键引擎(前缀/和弦状态机 + context 回落 + 超时)
# ---------------------------------------------------------------------------

#: resolve 的结果形态
RES_INTENT = "intent"          # 解析出 intent
RES_PREFIX = "prefix"          # 前缀命中,等下一键
RES_UNBOUND = "unbound"        # 无绑定
RES_SELF_INSERT = SELF_INSERT  # input 族 context 的可打印键自插入

#: 自插入面(§5.2/§5.3:dock/input 进入即 INSERT / 永远可输入;command 条同;
#: dbg-cmd = 调试器命令窗,常驻 INSERT,docs/TUI-DEBUG.md §5.3)
_INPUT_CONTEXTS = ("dock", "input", "command", "dbg-cmd")


class KeymapEngine:
    """活动 keymap 的运行时:KeyEvent 进,intent 出。
    前缀/和弦态(``gg`` / ``C-x C-s`` / ``\\ g``)挂起,超时由宿主调
    ``feed_timeout`` 裁决(主循环 poll 周期或测试显式驱动)。"""

    def __init__(self, pack: KeymapPack) -> None:
        self.pack = pack
        self._tables = pack.parsed_bindings()
        self._pending: list[KeyEvent] = []
        self._pending_exact: str | None = None  # 前缀同时是完整序列(vim 歧义面)
        self._pending_since: float | None = None

    # ------------------------------------------------------------------

    def reset(self) -> None:
        self._pending = []
        self._pending_exact = None
        self._pending_since = None

    def set_pack(self, pack: KeymapPack) -> None:
        """切换即重置(挂起前缀不跨包;同主题切换语义:切换即全量重渲 hint)。"""
        self.pack = pack
        self._tables = pack.parsed_bindings()
        self.reset()

    @property
    def pending_display(self) -> str:
        """挂起前缀的人读串(状态行回显;空 = 无挂起)。"""
        return display_sequence(tuple(self._pending)) if self._pending else ""

    def pending(self) -> bool:
        return bool(self._pending)

    # ------------------------------------------------------------------

    def _candidates(self, context: str, mode: str | None = None) -> dict[tuple[KeyEvent, ...], str]:
        """context 回落:模式子表 > 子级表 > global 表合并(后者先铺,前者覆盖)。"""
        merged: dict[tuple[KeyEvent, ...], str] = {}
        if context != "global":
            merged.update(self._tables.get("global", {}))
        merged.update(self._tables.get(context, {}))
        if mode and self.pack.modal:
            merged.update(self._tables.get(f"{context}:{mode}", {}))
        return merged

    def resolve(self, ev: KeyEvent, context: str, mode: str | None = None
                ) -> tuple[str, str | KeyEvent | None]:
        """喂一个键。返回 (结果形态, intent|KeyEvent|None)。

        ``mode``(modal 包撰写控件;"normal"/"insert"):INSERT 模式下 input 族
        context 的未绑定可打印键 = 自插入;NORMAL 模式不自插入(控件级键走绑定)。
        modeless 包忽略 mode(永远可输入,§5.3)。"""
        seq = tuple(self._pending + [ev])
        table = self._candidates(context, mode)
        exact = table.get(seq)
        longer = any(s[: len(seq)] == seq and len(s) > len(seq) for s in table)
        if exact is not None and not longer:
            self.reset()
            return RES_INTENT, exact
        if longer:
            self._pending.append(ev)
            self._pending_exact = exact  # 同时是完整序列:超时后按它落定
            self._pending_since = time.monotonic()
            return RES_PREFIX, None
        # 未命中:若有挂起,整串作废(vim 语义;不逐键重试)
        had_pending = bool(self._pending)
        self.reset()
        if had_pending:
            return RES_UNBOUND, None
        insert_ok = not self.pack.modal or mode in (None, "insert")
        if context in _INPUT_CONTEXTS and insert_ok and len(ev.key) == 1 \
                and ev.key.isprintable() and not ev.ctrl and not ev.meta:
            return RES_SELF_INSERT, ev
        return RES_UNBOUND, ev

    def feed_timeout(self) -> tuple[str, str | KeyEvent | None] | None:
        """前缀超时裁决(> CHORD_TIMEOUT):有完整序列缓冲按它落定,否则作废。"""
        if self._pending_since is None:
            return None
        if time.monotonic() - self._pending_since < CHORD_TIMEOUT:
            return None
        exact = self._pending_exact
        self.reset()
        if exact is not None:
            return RES_INTENT, exact
        return RES_UNBOUND, None

    # ------------------------------------------------------------------
    # 生成面(hint 栏 / ? 帮助面板:从活动 keymap 生成,永不硬编码 §5.1)
    # ------------------------------------------------------------------

    def binding_for(self, intent: str, context: str, mode: str | None = None) -> str:
        """intent 在当前 context(模式子表→子级→global 回落)下的首个绑定的
        人读串;无直接或弦时看命令条别名(显示为 ":w" 形态)。"""
        ctxs: list[str] = []
        if mode and self.pack.modal:
            ctxs.append(f"{context}:{mode}")
        ctxs.append(context)
        if context != "global":
            ctxs.append("global")
        for ctx in ctxs:
            for spec, it in self.pack.bindings.get(ctx, {}).items():
                if it == intent:
                    return display_sequence(parse_sequence(spec))
        for word, it in self.pack.commands.items():
            if it == intent:
                return f":{word}"
        return ""

    def hint_summary(self, context: str, intents: list[str], mode: str | None = None) -> str:
        """hint 栏摘要:"键:标签" 段,空格分隔;无绑定的 intent 跳过。"""
        parts: list[str] = []
        for intent in intents:
            key = self.binding_for(intent, context, mode)
            label = self.pack.copy.get(intent, intent)
            if key:
                parts.append(f"{key} {label}")
        return "  ".join(parts)

    def help_lines(self, context: str, mode: str | None = None) -> list[tuple[str, str]]:
        """? 帮助面板行(键序 → 标签;模式子表 + 当前 context + global 全量)。"""
        rows: list[tuple[str, str]] = []
        ctxs: list[str] = []
        if mode and self.pack.modal:
            ctxs.append(f"{context}:{mode}")
        ctxs.append(context)
        if context != "global":
            ctxs.append("global")
        for ctx in ctxs:
            for spec, intent in self.pack.bindings.get(ctx, {}).items():
                rows.append((display_sequence(parse_sequence(spec)),
                             self.pack.copy.get(intent, intent)))
        for word, intent in self.pack.commands.items():
            rows.append((f":{word}", self.pack.copy.get(intent, intent)))
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for key, label in rows:
            if key not in seen:
                seen.add(key)
                out.append((key, label))
        return out


# ---------------------------------------------------------------------------
# 内置两包(docs/TUI-DOC.md §5.2/§5.3;T1 行:global/index/letter/command)
# ---------------------------------------------------------------------------

_COMMON_COPY = {
    "move_left": "左移", "move_right": "右移",
    "move_up": "上一行", "move_down": "下一行",
    "line_start": "行首", "line_end": "行尾",
    "word_forward": "下一词", "word_backward": "上一词",
    "para_forward": "下一段", "para_backward": "上一段",
    "open": "打开", "quit": "退出", "command": "命令条", "help": "帮助",
    "goto_top": "文首", "goto_bottom": "文末",
    "page_down": "下半页", "page_up": "上半页",
    "mark": "框选", "annotate": "批注", "save": "盖章", "generate": "生成",
    "review": "评审", "versions": "版本条", "tree": "版本树",
    "search": "搜索", "search_next": "下一个", "search_prev": "上一个",
    "edit_back": "退格", "edit_delete": "删字", "newline": "换行",
    "confirm": "确认", "cancel": "取消", "delete": "删除", "clear_mark": "清选区",
    "mode_normal": "NORMAL", "mode_insert": "INSERT",
    "self_insert": "输入",
    # 调试器(docs/TUI-DEBUG.md §5.3)
    "focus_next": "切焦点", "scroll_up": "上滚", "scroll_down": "下滚",
    "repeat_last": "重复", "interrupt": "中断", "bp_toggle": "断点",
    "redraw": "重绘",
    "edit_kill_line": "删行", "edit_kill_word": "删词",
    "history_prev": "上条", "history_next": "下条",
}


def vim_pack() -> KeymapPack:
    """vim 包(§5.2;modal):信纸/信箱是只读缓冲心智,裸字母给 app 意图;
    modal 只存在于撰写控件(dock/input/command),正文永远不进 INSERT。
    T2(用户裁决):``v`` = 框选(视觉模式肌肉记忆优先),版本条挪 ``<leader>v``。"""
    p = KeymapPack()
    p.id = "vim"
    p.display_name = "Vim"
    p.modal = True
    p.bindings = {
        # global:命令条 / 帮助 / 返回;方向键/Enter 两包全局有效(§5.4)
        "global": {
            ":": "command", "?": "help", "q": "quit",
            "enter": "open",
            "up": "move_up", "down": "move_down",
            "left": "move_left", "right": "move_right",
        },
        # 信箱(只读列表;裸字母 = app 意图)
        "index": {
            "j": "move_down", "k": "move_up",
        },
        # 信纸(只读缓冲;字符级光标 + v 框选,less/mutt/magit/vim 传统)
        "letter": {
            "h": "move_left", "l": "move_right",
            "j": "move_down", "k": "move_up",
            "0": "line_start", "$": "line_end",
            "w": "word_forward", "b": "word_backward",
            "{": "para_backward", "}": "para_forward",
            "g g": "goto_top", "G": "goto_bottom",
            "C-d": "page_down", "C-u": "page_up",
            "v": "mark",
            "/": "search", "n": "search_next", "N": "search_prev",
            "a": "annotate", f"{LEADER} v": "versions", "r": "review",
            f"{LEADER} t": "tree",
            f"{LEADER} g": "generate",  # <leader>g(裸 g 已给 gg,§5.4 裁决)
            "esc": "clear_mark",        # 选区中 Esc 清选区(视觉模式惯例)
        },
        # 批注坞(撰写控件;共享面:C-Enter 存 / Enter 换行 / 退格)
        "dock": {
            "C-enter": "confirm", "enter": "newline", "backspace": "edit_back",
        },
        # 坞·INSERT(进入即此):Esc → NORMAL
        "dock:insert": {
            "esc": "mode_normal",
        },
        # 坞·NORMAL:控件级 h/l/x;i 回 INSERT;q 弃;d 删(既有批注)
        "dock:normal": {
            "i": "mode_insert", "q": "cancel", "d": "delete",
            "h": "move_left", "l": "move_right", "x": "edit_delete",
            "0": "line_start", "$": "line_end",
        },
        # 命令条(撰写控件:进入即 INSERT;Esc 回 NORMAL = 离开命令条)
        "command": {
            "enter": "confirm", "esc": "cancel", "C-c": "cancel",
            "backspace": "edit_back",
        },
        # 调试器命令窗(docs/TUI-DEBUG.md §5.3;常驻 INSERT):readline 基本键
        # 两包都收(C-a/C-e/C-u/C-w/↑↓历史);与 global 冲突的可打印键
        # (q/:/\?)显式绑回 self_insert——`q` 是命令文本,不是退出键
        "dbg-cmd": {
            "enter": "confirm", "backspace": "edit_back",
            "up": "history_prev", "down": "history_next",
            "C-a": "line_start", "C-e": "line_end",
            "C-u": "edit_kill_line", "C-w": "edit_kill_word",
            "C-l": "redraw", "C-c": "interrupt", "C-x o": "focus_next",
            "q": "self_insert", ":": "self_insert", "?": "self_insert",
        },
        # 调试器轨迹窗(只读;j/k 移光标行,↑↓/PgUp/PgDn 滚动,b = 行断点)
        "dbg-trace": {
            "j": "move_down", "k": "move_up",
            "g g": "goto_top", "G": "goto_bottom",
            "up": "scroll_up", "down": "scroll_down",
            "pageup": "page_up", "pagedown": "page_down",
            "C-u": "page_up", "C-d": "page_down",
            "b": "bp_toggle", "enter": "repeat_last",
            "C-x o": "focus_next", "C-l": "redraw", "C-c": "interrupt",
        },
        # 调试器调用栈窗(只读;j/k 选帧)
        "dbg-stack": {
            "j": "move_down", "k": "move_up",
            "g g": "goto_top", "G": "goto_bottom",
            "up": "scroll_up", "down": "scroll_down",
            "pageup": "page_up", "pagedown": "page_down",
            "enter": "repeat_last",
            "C-x o": "focus_next", "C-l": "redraw", "C-c": "interrupt",
        },
    }
    # 命令条别名(:w = 保存(盖章),§5.2;:q 退出)
    p.commands = {"w": "save", "q": "quit"}
    p.copy = dict(_COMMON_COPY)
    p.messages = {
        "mode.normal": "NORMAL", "mode.insert": "INSERT",
        "command.prompt": ":",
    }
    return p


def emacs_pack() -> KeymapPack:
    """emacs 包(§5.3;modeless):永远可输入;移动全靠 Ctrl/Meta;C-c 前缀给
    app 意图。``q`` 在只读 context 同样退出(magit/help buffer 传统,§5.4)。
    T2:``C-Space`` = mark 开关(0x00 解码);坞内 C-a/C-e/C-f/C-b 行内编辑。"""
    p = KeymapPack()
    p.id = "emacs"
    p.display_name = "Emacs"
    p.modal = False
    p.bindings = {
        # global:命令条 / 帮助 / 取消(C-g 万能取消)
        "global": {
            "M-x": "command", "C-h": "help", "C-g": "quit",
            "q": "quit",
            "enter": "open",
            "up": "move_up", "down": "move_down",
            "left": "move_left", "right": "move_right",
        },
        "index": {
            "C-n": "move_down", "C-p": "move_up",
        },
        # 信纸(字符级光标 + C-Space 框选)
        "letter": {
            "C-f": "move_right", "C-b": "move_left",
            "C-n": "move_down", "C-p": "move_up",
            "C-a": "line_start", "C-e": "line_end",
            "M-f": "word_forward", "M-b": "word_backward",
            "M-}": "para_forward", "M-{": "para_backward",
            "C-space": "mark",
            "C-g": "clear_mark",        # 选区中 C-g 清 mark(覆盖 global 万能取消)
            "M-<": "goto_top", "M->": "goto_bottom",
            "C-v": "page_down", "M-v": "page_up",
            "C-s": "search", "C-r": "search_prev",
            "C-c a": "annotate", "C-c v": "versions",
            "C-c r": "review", "C-c g": "generate", "C-c t": "tree",
            "C-x C-s": "save",
        },
        # 批注坞(自插入;C-c C-c 存(org 惯例)/ C-g 弃;C-c C-k 删既有)
        "dock": {
            "C-c C-c": "confirm", "C-g": "cancel", "C-c C-k": "delete",
            "enter": "newline", "backspace": "edit_back", "C-d": "edit_delete",
            "C-a": "line_start", "C-e": "line_end",
            "C-f": "move_right", "C-b": "move_left",
        },
        "command": {
            "enter": "confirm", "esc": "cancel", "C-g": "cancel",
            "backspace": "edit_back",
        },
        # 调试器命令窗(docs/TUI-DEBUG.md §5.3;modeless 永远可输入):readline
        # 基本键 + C-f/C-b/C-d;q 显式绑回 self_insert(命令文本,不是退出键)
        "dbg-cmd": {
            "enter": "confirm", "backspace": "edit_back", "C-d": "edit_delete",
            "C-f": "move_right", "C-b": "move_left",
            "up": "history_prev", "down": "history_next",
            "C-a": "line_start", "C-e": "line_end",
            "C-u": "edit_kill_line", "C-w": "edit_kill_word",
            "C-l": "redraw", "C-c": "interrupt", "C-x o": "focus_next",
            "C-g": "cancel", "q": "self_insert",
        },
        # 调试器轨迹窗(只读;C-n/C-p 移光标行,b = 行断点;q 仍退出,magit 传统)
        "dbg-trace": {
            "C-n": "move_down", "C-p": "move_up",
            "M-<": "goto_top", "M->": "goto_bottom",
            "up": "scroll_up", "down": "scroll_down",
            "pageup": "page_up", "pagedown": "page_down",
            "C-v": "page_down", "M-v": "page_up",
            "b": "bp_toggle", "enter": "repeat_last",
            "C-x o": "focus_next", "C-l": "redraw", "C-c": "interrupt",
        },
        # 调试器调用栈窗(只读;C-n/C-p 选帧)
        "dbg-stack": {
            "C-n": "move_down", "C-p": "move_up",
            "M-<": "goto_top", "M->": "goto_bottom",
            "up": "scroll_up", "down": "scroll_down",
            "pageup": "page_up", "pagedown": "page_down",
            "C-v": "page_down", "M-v": "page_up",
            "enter": "repeat_last",
            "C-x o": "focus_next", "C-l": "redraw", "C-c": "interrupt",
        },
    }
    p.commands = {"q": "quit"}
    p.copy = dict(_COMMON_COPY)
    p.messages = {"command.prompt": "M-x "}
    return p


def register_built_in() -> None:
    """注册内置两键位包(vim 在前为缺省;默认探测 $EDITOR/$VISUAL 归 __main__)。"""
    KeymapRegistry.register(vim_pack())
    KeymapRegistry.register(emacs_pack())
