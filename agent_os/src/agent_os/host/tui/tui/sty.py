"""Sty(docs/TUI-DOC.md §3/§6;对译 godot/kernel/sty.gd)。

样式助手:组件只消费当前主题的语义 token(对齐"全部样式只消费契约 token,
不写散值"的纪律;WEB-UI.md §3 / DEBUG-UI-THEMES.md §4-6 组件无分支)。
godot 侧产 StyleBox/控件;TUI 侧产 (fg, bg, attrs) 的 Style 与文案——
cell buffer 的写面单元。缺 token 回显眼缺省(契约测试的肉眼版)。
"""

from __future__ import annotations

from agent_os.host.tui.tui.cells import Style
from agent_os.host.tui.tui.theme import ThemeRegistry

#: 缺 token 的显眼回退(godot 的 MAGENTA 对译;ANSI 256 的 201 ≈ magenta)
_FALLBACK_FG = 201


def c(token: str) -> int:
    """颜色 token → ANSI 256 色号(缺 token 显眼回退)。"""
    t = ThemeRegistry.current()
    if t is None:
        return _FALLBACK_FG
    return t.colors.get(token, _FALLBACK_FG)


def s(token: str) -> int:
    """间距 token → 格子数(缺 token 回 1)。"""
    t = ThemeRegistry.current()
    if t is None:
        return 1
    return t.sizes.get(token, 1)


def copy(key: str) -> str:
    """copy 键 → 文案(缺 key 上屏 key 本体,契约测试的肉眼版)。"""
    t = ThemeRegistry.current()
    if t is None:
        return key
    return t.copy.get(key, key)


# ---------------------------------------------------------------------------
# 基础件(语义 token → Style;组件的取色唯一通道)
# ---------------------------------------------------------------------------

def text(fg_token: str = "fg-0", *, bg_token: str | None = None,
         bold: bool = False, dim: bool = False) -> Style:
    """正文样式:fg/bg 语义 token + 属性位。"""
    attrs = frozenset(a for a, on in (("bold", bold), ("dim", dim)) if on)
    return Style(c(fg_token), c(bg_token) if bg_token else None, attrs)


def panel() -> Style:
    """面板底(bg-1)。"""
    return Style(c("fg-0"), c("bg-1"))


def selected() -> Style:
    """选中行(悬浮面 bg-2 + 强调;信箱当前信 / 信纸当前段共用)。"""
    return Style(c("fg-0"), c("bg-2"), frozenset({"bold"}))


def focus_ring() -> Style:
    """焦点环(WEB-A11Y §5.2;TUI 里用于当前项左缘光标列)。"""
    return Style(c("focus-ring"))


def status_bar() -> Style:
    """状态栏(反色底行;双编码纪律的颜色通道,符号通道由调用方另给)。"""
    return Style(c("bg-0"), c("fg-2"))


def heading() -> Style:
    """信纸标题行(反色 + 加粗;markdown 最小处理的 #-行,§4)。"""
    return Style(c("paper-0"), c("paper-ink"), frozenset({"bold"}))


def paper(dim: bool = False) -> Style:
    """信纸正文 = 终端原生:墨色 fg、**无纸底**(T2.1 用户裁决:段落不铺
    背景,否则光标(cell 反色)在纸面上不可辨;纸/印 token 只留给标题栏、
    印章回声等小面积件,阅读面不铺底)。"""
    return Style(c("fg-1" if dim else "fg-0"), None, frozenset({"dim"} if dim else ()))


def seal() -> Style:
    """印章色(盖章回声;双编码:色 + 文字章,符号通道由调用方给)。"""
    return Style(c("seal"), c("paper-0"), frozenset({"bold"}))


def selection() -> Style:
    """框选选区(select 底色;T2 字符级选区,双编码:底色 + 状态栏行:列)。"""
    return Style(c("fg-0"), c("select"))


def ann_span(base: Style | None = None) -> Style:
    """已批注 span(下划线 + warn 色,不铺底——同 T2.1 裁决;span 末字符后
    的 [注×N] 同款色)。"""
    bg = base.bg if base else None
    return Style(c("warn"), bg, frozenset({"underline"}))
