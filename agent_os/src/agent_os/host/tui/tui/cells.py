"""cell buffer(docs/TUI-DOC.md §3;终端唯一真实新坑,收口在这一个文件)。

2D 格子:每格 = (字符, 样式)。纪律:
- CJK 双宽用 ``unicodedata.east_asian_width`` 收口(宽字符占两格,第二格是
  延续占位;光标/截断都按格算);
- 宽字符在右缘截断时不写半格(字符放不下就不画,原样留白);
- ANSI 序列不跨入格子:样式只在格子上,输出时按样式段串行发 SGR。

样式 = (fg, bg, attrs):fg/bg 是 ANSI 256 色号(int)或 None(缺省),
attrs 是 {"bold","dim","reverse"} 子集。输出 = 全量重渲(T1 从简,§3:
"render() 全量重渲 cell buffer → 输出",diff 留后续优化口)。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

#: 属性白名单(SGR 子集;reverse 是 focus-pulse/光标的载体,underline 是
#: 批注 span 的载体——双编码纪律:符号/线 + 色)
_ATTRS = ("bold", "dim", "reverse", "underline")

#: 样式段终止:朴素 reset(每行末也发一次,防串色到下一段输出)
_SGR_RESET = "\x1b[0m"


@dataclass(frozen=True)
class Style:
    """(fg, bg, attrs) 语义 token 解析后的 ANSI 色面。None = 终端缺省色。"""

    fg: int | None = None
    bg: int | None = None
    attrs: frozenset[str] = frozenset()

    def sgr(self) -> str:
        codes: list[str] = []
        if "bold" in self.attrs:
            codes.append("1")
        if "dim" in self.attrs:
            codes.append("2")
        if "underline" in self.attrs:
            codes.append("4")
        if "reverse" in self.attrs:
            codes.append("7")
        if self.fg is not None:
            codes.append(f"38;5;{self.fg}")
        if self.bg is not None:
            codes.append(f"48;5;{self.bg}")
        return f"\x1b[{';'.join(codes)}m" if codes else ""

    def with_attrs(self, *attrs: str) -> Style:
        return Style(self.fg, self.bg, self.attrs | frozenset(a for a in attrs if a in _ATTRS))

    def reversed(self) -> Style:
        return self.with_attrs("reverse") if "reverse" not in self.attrs else self


DEFAULT_STYLE = Style()


def char_width(ch: str) -> int:
    """终端格宽:CJK 全宽/宽 = 2(W/F),其余 = 1;不可打印算 0(不画)。"""
    if not ch or unicodedata.category(ch) in ("Cc", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def wrap_text(text: str, width: int) -> list[str]:
    """按格宽折行(CJK 双宽收口在本文件):贪心断行,长词硬断;空串 → [""]。"""
    if width < 1:
        return [""]
    lines: list[str] = []
    cur: list[str] = []
    cur_w = 0
    for ch in text:
        w = char_width(ch)
        if w == 0:
            continue
        if cur_w + w > width:
            lines.append("".join(cur))
            cur = []
            cur_w = 0
        cur.append(ch)
        cur_w += w
    lines.append("".join(cur))
    return lines


@dataclass
class _Cell:
    ch: str = " "
    style: Style = DEFAULT_STYLE
    cont: bool = False  # 双宽字符的第二格(延续占位,不独立输出)


@dataclass
class Region:
    """cell buffer 上的一段区域(render_into 的目标;widget 协议不假设渲染层)。"""
    x: int
    y: int
    w: int
    h: int


class CellBuffer:
    """2D cell buffer:render() 的产物;先清后建、幂等可重入。"""

    def __init__(self, width: int, height: int) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self._grid: list[list[_Cell]] = []
        self.clear()

    # ------------------------------------------------------------------
    # 写面
    # ------------------------------------------------------------------

    def clear(self, style: Style = DEFAULT_STYLE) -> None:
        """全清(全量重渲语义:先清后建)。"""
        self._grid = [[_Cell(style=style) for _ in range(self.width)] for _ in range(self.height)]

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def put(self, x: int, y: int, ch: str, style: Style = DEFAULT_STYLE) -> int:
        """在 (x,y) 放单字符;返回占格数(0/1/2)。宽字符放不下右缘 → 不画返回 0。"""
        if not ch:
            return 0
        ch = ch[0]
        w = char_width(ch)
        if w == 0 or not self.in_bounds(x, y):
            return 0
        if w == 2 and x + 1 >= self.width:
            return 0  # 宽字符截断:不写半格
        # 覆盖写入:若踩到双宽字符的任一格,把邻格也清掉(防半格残影)
        cur = self._grid[y][x]
        if cur.cont and x > 0:
            self._grid[y][x - 1] = _Cell(style=cur.style)
        if cur.ch and char_width(cur.ch) == 2 and not cur.cont and x + 1 < self.width:
            self._grid[y][x + 1] = _Cell(style=cur.style)
        self._grid[y][x] = _Cell(ch, style)
        if w == 2:
            self._grid[y][x + 1] = _Cell("", style, cont=True)
        return w

    def write(self, x: int, y: int, text: str, style: Style = DEFAULT_STYLE,
              max_width: int | None = None) -> int:
        """从 (x,y) 起写串(按格走,自动处理双宽);返回写入格数。
        max_width = 本行可用格宽上限(超出截断,不写半格)。"""
        if y < 0 or y >= self.height:
            return 0
        limit = self.width - x if max_width is None else min(max_width, self.width - x)
        used = 0
        cx = x
        for ch in text:
            w = char_width(ch)
            if w == 0:
                continue
            if used + w > limit:
                break
            placed = self.put(cx, y, ch, style)
            if placed == 0:
                break
            used += placed
            cx += placed
        return used

    def fill(self, x: int, y: int, w: int, h: int, style: Style) -> None:
        """区域刷样式(不改字符;focus-pulse 反色、选中行铺底用)。"""
        for yy in range(max(0, y), min(self.height, y + h)):
            for xx in range(max(0, x), min(self.width, x + w)):
                cell = self._grid[yy][xx]
                self._grid[yy][xx] = _Cell(cell.ch, style, cell.cont)

    def blank(self, x: int, y: int, w: int, h: int, style: Style = DEFAULT_STYLE) -> None:
        """区域清字 + 刷样式(覆盖层面板用:先抹掉下层内容)。"""
        for yy in range(max(0, y), min(self.height, y + h)):
            for xx in range(max(0, x), min(self.width, x + w)):
                self._grid[yy][xx] = _Cell(" ", style)

    def reverse_region(self, x: int, y: int, w: int, h: int) -> None:
        """区域反色(focus-pulse 的瞬时帧;只翻 reverse 属性位)。"""
        for yy in range(max(0, y), min(self.height, y + h)):
            for xx in range(max(0, x), min(self.width, x + w)):
                cell = self._grid[yy][xx]
                attrs = cell.style.attrs ^ frozenset({"reverse"})
                self._grid[yy][xx] = _Cell(cell.ch, Style(cell.style.fg, cell.style.bg, attrs), cell.cont)

    # ------------------------------------------------------------------
    # 读面 / 输出
    # ------------------------------------------------------------------

    def line_text(self, y: int) -> str:
        """第 y 行的纯文本(宽字符只取首格;断言/快照用)。"""
        if y < 0 or y >= self.height:
            return ""
        return "".join(c.ch for c in self._grid[y] if not c.cont).rstrip()

    def plain_text(self) -> str:
        """整屏纯文本(行尾空白裁掉;无头冒烟的断言面)。"""
        return "\n".join(self.line_text(y) for y in range(self.height))

    def style_at(self, x: int, y: int) -> Style:
        if not self.in_bounds(x, y):
            return DEFAULT_STYLE
        return self._grid[y][x].style

    def to_ansi(self) -> str:
        """buffer → ANSI 字符串(全量;样式段串行发 SGR,行末 reset)。
        双宽延续格不发字符,也不发样式(样式随首格,序列不跨入格子)。
        行间必须显式 ``\\r\\n``:raw 模式已关 OPOST(ONLCR 不可依赖,keys.py
        TerminalSession),裸 ``\\n`` 换行不回车会阶梯漂移(实机 bug:只见框架);
        ``\\r`` 顺带清掉满宽行的行尾折行挂起态。"""
        lines: list[str] = []
        for row in self._grid:
            parts: list[str] = []
            cur: Style | None = None
            for cell in row:
                if cell.cont:
                    continue
                if cell.style != cur:
                    parts.append(cell.style.sgr() or _SGR_RESET)
                    cur = cell.style
                parts.append(cell.ch)
            parts.append(_SGR_RESET)
            lines.append("".join(parts))
        return "\r\n".join(lines)

    # ------------------------------------------------------------------
    # 测试/调试用直读
    # ------------------------------------------------------------------

    def grid_snapshot(self) -> dict[str, Any]:
        """可序列化快照(宽字符占格与样式;快照比对用)。"""
        return {
            "width": self.width,
            "height": self.height,
            "rows": [
                [{"ch": c.ch, "fg": c.style.fg, "bg": c.style.bg,
                  "attrs": sorted(c.style.attrs), "cont": c.cont} for c in row]
                for row in self._grid
            ],
        }


#: Region 构造便捷口(语义同 godot 侧 "cell buffer 上的一段区域")
def region(x: int, y: int, w: int, h: int) -> Region:
    return Region(x, y, w, h)
