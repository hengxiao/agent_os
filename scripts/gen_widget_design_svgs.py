#!/usr/bin/env python3
"""生成 13 张 widget 高保真设计效果图 → docs/widgets/design/<kind>.svg。

验收基准配对 docs/WIDGET-DESIGN.md(§1 设计语言 / §3 逐控件效果要求):
- 画布 1080×700;顶部标题条;上部 tab 完整形态(含 1 个 hover + 1 个焦点环实例);
  左下 card 摘要形态(340px 宽);右下状态变体小样(2–3 个)。
- 色值逐字取自 agent_os/src/agent_os/host/web/static/css/themes/classic.css。
- 禁用 SVG filter:三级阴影用叠层圆角矩形模拟(--shadow-1/2/3,§1.2)。
- 4px 基网;圆角 输入/按钮 6、卡片/浮层 8、气泡 12、徽标 999;
  字号阶梯 20/15/13/12/11,mono 一律 12;字重 600/500/400。

用法:python3 scripts/gen_widget_design_svgs.py(幂等重写 13 张 SVG)。
"""

from __future__ import annotations

import calendar
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape as _esc

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "widgets" / "design"

# ── classic 主题真实色值(css/themes/classic.css 逐字)──────────────────────
BG0 = "#0b0e14"
BG1 = "#11151d"
BG2 = "#171d29"
BG3 = "#1f2837"
LINE = "#263043"
LINE_STRONG = "#33405a"
FG0 = "#e6ebf2"
FG1 = "#9aa7ba"
FG2 = "#5d6b82"
OK = "#3fb68b"
WARN = "#d9a03f"
DANGER = "#e5534b"
LIVE = "#3b9eff"
SIG_LLM = "#6f9fff"
SIG_TOOL = "#3fb68b"
SIG_SIDECAR = "#d9a03f"
SIG_COMPRESS = "#8b7cf6"
SIG_BUDGET = "#e5534b"
SIG_FRAME = "#5d6b82"
# --log-bg 为规格书 §3.10 指定的独立 token(tokens.css 尚未定义),此处给出建议值。
LOG_BG = "#0c1018"

SANS = "Inter,-apple-system,'PingFang SC','Microsoft YaHei','Noto Sans SC',sans-serif"
MONO = "ui-monospace,'JetBrains Mono','SF Mono',Consolas,monospace"

W, H = 1080, 700
WHITE = "#ffffff"

# 三级阴影(--shadow-1 卡静态 / --shadow-2 hover·浮层 / --shadow-3 弹层),
# 用叠层圆角矩形模拟:(纵向偏移, 黑透明度, 外扩)。
SHADOW = {
    1: [(1, 0.06, 0), (3, 0.04, 2)],
    2: [(2, 0.08, 1), (5, 0.05, 3)],
    3: [(4, 0.10, 2), (9, 0.06, 5)],
}


def _n(v: float) -> str:
    v = round(float(v), 1)
    return str(int(v)) if v == int(v) else str(v)


def esc(s) -> str:
    return _esc(str(s), {'"': "&quot;"})


def tw(s: str, size: float, mono: bool = False) -> float:
    """文本宽度估算(CJK ≈ 1.0em;mono 西文 0.6em;sans 西文按类别)。"""
    w = 0.0
    for ch in s:
        o = ord(ch)
        if o >= 0x2E80 or o in (0x2013, 0x2014, 0x2026):  # CJK / 破折号 / 省略号
            w += 1.0
        elif 0x2190 <= o <= 0x21FF or o in (0x00B7,):  # 箭头 / 间隔号
            w += 0.9 if o != 0x00B7 else 0.5
        elif mono:
            w += 0.6
        elif ch == " ":
            w += 0.3
        elif ch.isupper():
            w += 0.66
        elif ch.isdigit():
            w += 0.56
        else:
            w += 0.5
    return w * size


# ── 基础图元 ──────────────────────────────────────────────────────────────
def rs(x, y, w, h, r=0, fill="none", stroke=None, sw=1, op=None, dash=None):
    a = (f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}"'
         f' rx="{_n(r)}" fill="{fill}"')
    if stroke:
        a += f' stroke="{stroke}" stroke-width="{_n(sw)}"'
    if op is not None:
        a += f' fill-opacity="{op}"'
    if dash:
        a += f' stroke-dasharray="{dash}"'
    return a + "/>"


def ts(x, y, s, size=13, fill=FG0, wght=400, mono=False, anchor=None, op=None, it=False):
    a = (f'<text x="{_n(x)}" y="{_n(y)}" font-size="{_n(size)}" fill="{fill}"'
         f' font-weight="{wght}"')
    if mono:
        a += f' font-family="{MONO}"'
    if anchor:
        a += f' text-anchor="{anchor}"'
    if op is not None:
        a += f' fill-opacity="{op}"'
    if it:
        a += ' font-style="italic"'
    return a + f'>{esc(s)}</text>'


def ls(x1, y1, x2, y2, stroke=LINE, sw=1, dash=None, op=None):
    a = (f'<line x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}"'
         f' stroke="{stroke}" stroke-width="{_n(sw)}"')
    if dash:
        a += f' stroke-dasharray="{dash}"'
    if op is not None:
        a += f' stroke-opacity="{op}"'
    return a + "/>"


def cs(cx, cy, r, fill, op=None, stroke=None, sw=1):
    a = f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}" fill="{fill}"'
    if op is not None:
        a += f' fill-opacity="{op}"'
    if stroke:
        a += f' stroke="{stroke}" stroke-width="{_n(sw)}"'
    return a + "/>"


def ps(d, fill="none", stroke=None, sw=1, op=None):
    a = f'<path d="{d}" fill="{fill}"'
    if stroke:
        a += f' stroke="{stroke}" stroke-width="{_n(sw)}"'
    if op is not None:
        a += f' fill-opacity="{op}"'
    return a + "/>"


def shadow(x, y, w, h, r, level):
    return [rs(x - sp, y + dy - sp, w + 2 * sp, h + 2 * sp, r + sp, "#000000", op=op)
            for dy, op, sp in SHADOW[level]]


def ring(x, y, w, h, r=6):
    """焦点环:2px --live,offset 1px(§1.4)。"""
    return rs(x - 2, y - 2, w + 4, h + 4, r + 2, "none", LIVE, 2)


def spans(x, y, parts, size=12, mono=True, anchor=None):
    """连续多色文本;parts = [(text, fill[, wght]), ...];返回结束 x。"""
    out = []
    for p in parts:
        s, fill = p[0], p[1]
        wght = p[2] if len(p) > 2 else 400
        out.append(ts(x, y, s, size, fill, wght, mono, anchor))
        x += tw(s, size, mono)
    return x, "".join(out)


# ── 复合图元 ──────────────────────────────────────────────────────────────
TONE = {
    "neutral": (BG2, FG1, None),
    "live": (LIVE, LIVE, 0.12),
    "ok": (OK, OK, 0.12),
    "warn": (WARN, WARN, 0.14),
    "danger": (DANGER, DANGER, 0.14),
    "solid-live": (LIVE, WHITE, None),
}


def pill(x, y, s, tone="neutral", size=11, h=20, mono=False, wght=500):
    """徽标/chip 胶囊(圆角 999);返回 (svg, 宽度)。"""
    bg, fg, a = TONE[tone]
    w = tw(s, size, mono) + 16
    body = rs(x, y, w, h, h / 2, bg, op=a) if a else rs(x, y, w, h, h / 2, bg)
    body += ts(x + w / 2, y + h / 2 + size * 0.36, s, size, fg, wght, mono, "middle")
    return body, w


def chip(x, y, s, selected=False, hover=False, h=28, size=12):
    """可选择 chip(日期快捷项 / enum 单选);返回 (svg, 宽度)。"""
    w = tw(s, size) + 24
    if selected:
        body = rs(x, y, w, h, h / 2, LIVE, op=0.12) + rs(x, y, w, h, h / 2, "none", LIVE, 1, op=0.5)
        body += ts(x + w / 2, y + h / 2 + size * 0.36, s, size, LIVE, 500, anchor="middle")
    else:
        body = rs(x, y, w, h, h / 2, BG2 if hover else "none", None if hover else LINE)
        body += ts(x + w / 2, y + h / 2 + size * 0.36, s, size, FG1, 400, anchor="middle")
    return body, w


def button(x, y, s, kind="primary", h=28, size=12, disabled=False, hover=False):
    """按钮(圆角 6);返回 (svg, 宽度)。kind: primary / ghost。"""
    w = tw(s, size) + 24
    if kind == "primary":
        body = rs(x, y, w, h, 6, LIVE) + ts(x + w / 2, y + h / 2 + size * 0.36, s, size, WHITE, 500, anchor="middle")
    else:
        body = rs(x, y, w, h, 6, BG2 if hover else "none", LINE)
        body += ts(x + w / 2, y + h / 2 + size * 0.36, s, size, FG1, 500, anchor="middle")
    if disabled:
        body = f'<g opacity="0.45">{body}</g>'
    return body, w


def input_box(x, y, w, h=32, value="", ph="", focused=False, error=False,
              icon=None, mono=False, disabled=False):
    """输入框(圆角 6,bg-2 凹槽);focused=焦点环;error=红边。"""
    body = rs(x, y, w, h, 6, BG2, DANGER if error else LINE)
    tx = x + 12
    if icon:
        body += ts(tx, y + h / 2 + 4.5, icon, 12, FG2)
        tx += 22
    if value:
        cur = ""
        if value.endswith("|"):
            value = value[:-1]
            cx = tx + tw(value, 13, mono) + 1
            cur = rs(cx, y + h / 2 - 8, 2, 16, 0, LIVE)
        body += ts(tx, y + h / 2 + 4.5, value, 13, FG0, 400, mono) + cur
    elif ph:
        body += ts(tx, y + h / 2 + 4.5, ph, 13, FG2)
    if focused:
        body += ring(x, y, w, h, 6)
    if disabled:
        body = f'<g opacity="0.45">{body}</g>'
    return body


def segmented(x, y, options, active=0, h=26, focus_idx=None):
    """分段切换;返回 (svg, 总宽)。"""
    seg_w = max(tw(o, 11) for o in options) + 20
    w = seg_w * len(options) + 6
    body = rs(x, y, w, h, 6, BG2, LINE)
    for i, o in enumerate(options):
        sx = x + 3 + i * seg_w
        if i == active:
            body += rs(sx, y + 3, seg_w, h - 6, 4, BG3)
        body += ts(sx + seg_w / 2, y + h / 2 + 4, o, 11, FG0 if i == active else FG2,
                   500 if i == active else 400, anchor="middle")
        if focus_idx is not None and i == focus_idx:
            body += ring(sx, y + 3, seg_w, h - 6, 4)
    return body, w


def switch(x, y, on=True, w=36, h=20):
    """iOS 式 switch(36×20 胶囊轨道 + 内嵌圆拨钮):开=--live 底拨钮居右;关=--bg-3 底拨钮居左。"""
    body = rs(x, y, w, h, h / 2, LIVE if on else BG3)
    cx = x + w - h / 2 if on else x + h / 2
    body += cs(cx, y + h / 2, h / 2 - 3, WHITE)
    return body


def empty_state(cx, cy, icon, msg, btn=None, w=None):
    """§1.5 empty 通例:图标位 + 一句人话 + 主操作。"""
    out = ts(cx, cy - 22, icon, 20, FG2, anchor="middle")
    out += ts(cx, cy + 4, msg, 12, FG1, anchor="middle")
    if btn:
        bw = tw(btn, 12) + 24
        b, _ = button(cx - bw / 2, cy + 18, btn, "ghost", h=26)
        out += b
    return out


def skeleton_row(x, y, widths, h=10, gap=12):
    out, cx = "", x
    for i, w in enumerate(widths):
        out += rs(cx, y, w, h, h / 2, BG2 if i % 2 == 0 else BG3)
        cx += w + gap
    return out


# ── 页面骨架 ──────────────────────────────────────────────────────────────
def page(title, ref):
    """标题条 + TAB 面板 + 两个下区标签;返回 parts 列表。"""
    g = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="{SANS}">']
    g.append(rs(0, 0, W, H, 0, BG0))
    g.append(ts(24, 33, title, 15, FG0, 600))
    g.append(ts(1056, 33, f"参照 {ref}", 12, FG2, anchor="end"))
    g.append(ls(0, 48.5, W, 48.5, LINE))
    g.append(ts(24, 68, "TAB · 完整形态(含 hover 与焦点环实例)", 11, FG2, 500))
    g += shadow(24, 76, 1032, 400, 8, 1)
    g.append(rs(24, 76, 1032, 400, 8, BG1, LINE))
    g.append(ts(24, 500, "CARD · 摘要形态(340px)", 11, FG2, 500))
    g.append(ts(388, 500, "状态变体", 11, FG2, 500))
    return g


def tab_head(g, label, right=""):
    g.append(ts(40, 101, label, 12, FG1, 500))
    if right:
        g.append(right)
    g.append(ls(40, 116.5, 1040, 116.5, LINE))


def card(w=340, h=168, hover=False):
    """widget card 底(左下,340px 宽)。"""
    g = shadow(24, 508, w, h, 8, 2 if hover else 1)
    g.append(rs(24, 508, w, h, 8, BG1, LINE))
    return g


def vboxes(captions):
    """右下状态变体小样底框;返回每框内容原点 [(x, y)]。caption 置框内左上。"""
    n = len(captions)
    if n == 2:
        xs, ws = [388, 728], [328, 328]
    else:
        xs, ws = [388, 616, 844], [212, 212, 212]
    g, origins = [], []
    for x, w0, cap in zip(xs, ws, captions):
        g.append(rs(x, 508, w0, 168, 8, BG1, LINE))
        g.append(ts(x + 14, 528, cap, 11, FG2, 500))
        origins.append((x + 14, 544, w0 - 28))
    return g, origins


def svg_doc(parts):
    return "".join(parts) + "</svg>"


# ── 编辑器基座(W-text / W-json 共享)──────────────────────────────────────
def editor_rows(g, lines, cur=None, mono=True, size=12, x0=92, y0=124, pitch=20,
                gutter_x=76, rows=15, color=FG0):
    """行号槽 + 文本行;cur = 当前行(1-based,整行 bg-2 高亮,行号转正文色)。"""
    if cur:
        g.append(rs(40, y0 + (cur - 1) * pitch, 1000, pitch, 0, BG2))
    for i in range(rows):
        base = y0 + i * pitch + 15
        no = str(i + 1)
        g.append(ts(gutter_x, base, no, 11, FG0 if i + 1 == cur else FG2, mono=True, anchor="end"))
        if i < len(lines) and lines[i]:
            g.append(ts(x0, base, lines[i], size, color, 400, mono))


# ═══ 3.1 W-text ═══════════════════════════════════════════════════════════
def w_text():
    g = page("W-text · 文本编辑器", "VS Code / Notion")
    tab_head(g, "prompt · markdown")
    # 编辑区:focus(外圈焦点环)+ dirty 左缘竖条
    g.append(rs(40, 124, 2, 312, 0, LIVE))  # dirty 竖条
    lines = ["# 晚餐推荐助手", "", "你是晚餐推荐助手。按天气、日程与", "口味偏好给出建议，回答控制在",
             "三句话以内。", "", "## 约束", "- 不推荐生食", "- 预算人均 80 元以内", "",
             "输出:推荐 + 理由 + 备选"]
    editor_rows(g, lines, cur=9)
    # 选区(--live 18%)+ 2px 光标
    g.append(rs(106.4, 286, 48, 16, 0, LIVE, op=0.18))
    g.append(rs(155, 286, 2, 16, 0, LIVE))
    g.append(ring(40, 124, 1000, 312, 8))
    # 右下微标(hover 态:bg-2 + 详细 tooltip)+ 未保存圆点
    p, pw = pill(940, 444, "12 行 · 340 字", "neutral")
    g.append(rs(940, 444, pw, 20, 10, BG2, LINE))
    g.append(cs(952, 454, 3, LIVE))
    g.append(ts(960, 458, "12 行 · 340 字", 11, FG1))
    g += shadow(914, 406, 140, 30, 6, 2)
    g.append(rs(914, 406, 140, 30, 6, BG2, LINE))
    g.append(ts(984, 425, "第 9 行 · 第 14 列", 11, FG0, anchor="middle"))
    # card(hover:抬升 + 打开 →)
    g += card(hover=True)
    g.append(rs(40, 524, 11, 14, 2, "none", FG1))
    g.append(ls(40, 528, 51, 528, FG1))
    g.append(ts(60, 538, "晚餐推荐助手.md", 13, FG0, 500))
    g.append(cs(60 + tw("晚餐推荐助手.md", 13) + 10, 534, 3, LIVE))
    g.append(ts(350, 537, "打开 →", 11, LIVE, anchor="end"))
    g.append(ts(38, 570, "你是晚餐推荐助手。按天气、日程与", 13, FG1))
    g.append(ts(38, 594, "口味偏好给出建议，回答控制在", 13, FG1))
    g.append(ts(38, 618, "三句话以内。", 13, FG1, op=0.35))
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(38, 660, "12 行 · 340 字 · 3 分钟前", 11, FG2))
    # 状态变体:empty / readonly
    vb, (o1, o2) = vboxes(["EMPTY · 空态", "READONLY · 只读"])
    g += vb
    g.append(empty_state(o1[0] + 150, 584, "¶", "开始输入,或让助手帮你起草", "让助手起草"))
    g.append(rs(o2[0], o2[1], o2[2], 96, 6, BG2))
    g.append(ts(o2[0] + 12, o2[1] + 24, "你是晚餐推荐助手。按天气、", 12, FG1))
    g.append(ts(o2[0] + 12, o2[1] + 44, "日程与口味偏好给出建议。", 12, FG1))
    g.append(ts(o2[0] + 12, o2[1] + 64, "回答控制在三句话以内。", 12, FG1))
    g.append(ts(o2[0] + o2[2] - 12, o2[1] + 22, "🔒", 12, FG2, anchor="end"))
    g.append(ts(o2[0] + 12, o2[1] + 88, "只读 · 无光标 · 无脏条", 11, FG2))
    return svg_doc(g)


# ═══ 3.2 W-json ═══════════════════════════════════════════════════════════
def w_json():
    g = page("W-json · JSON 编辑器", "VS Code JSON / Postman")
    # format 钮 hover 实例(平时 40%,hover 显形:bg-2 + 全透明字)
    fb, fw = button(0, 0, "格式化", "ghost", h=24)
    g.append(f'<g transform="translate(964,88)">{fb}</g>')
    tab_head(g, "inputs · json")
    # 括号匹配浅底(第 1 行 { 与第 6 行 })
    g.append(rs(90, 126, 10, 16, 2, LIVE, op=0.12))
    g.append(rs(90, 226, 10, 16, 2, LIVE, op=0.12))
    g.append(rs(101, 226, 2, 16, 0, LIVE))  # 光标
    # 行号槽
    for i in range(15):
        g.append(ts(76, 124 + i * 20 + 15, str(i + 1), 11, FG2, mono=True, anchor="end"))
    g.append(cs(58, 194, 3.5, DANGER))  # 第 4 行槽红点
    # 语法着色:key live / string ok / number·bool warn / 标点弱色
    jl = [
        [("{", FG2)],
        [("  ", FG2), ('"name"', LIVE), (": ", FG2), ('"dinner-bot"', OK), (",", FG2)],
        [("  ", FG2), ('"budget"', LIVE), (": ", FG2), ("80", WARN), (",", FG2)],
        [("  ", FG2), ('"tags"', LIVE), (": ", FG2), ("[", FG2), ('"正餐"', OK), (", ", FG2), ('"轻食"', OK)],
        [("  ", FG2), ('"active"', LIVE), (": ", FG2), ("true", WARN)],
        [("}", FG2)],
    ]
    for i, line in enumerate(jl):
        _, svg = spans(92, 124 + i * 20 + 15, line)
        g.append(svg)
    # 第 4 行红波浪下划(缺右括号)
    wave = "M164 202 q3 -3 6 0" + " t6 0" * 12
    g.append(ps(wave, "none", DANGER, 1.2))
    g.append(ring(40, 124, 1000, 312, 8))
    # 底部错误条
    g.append(ls(40, 436.5, 1040, 436.5, LINE))
    g.append(ts(52, 457, "✕", 12, DANGER, 600))
    g.append(ts(68, 457, "第 4 行:缺右括号", 12, FG0, 500))
    g.append(ts(1040, 457, "点击跳转 →", 11, FG2, anchor="end"))
    # card(v2 · 用户验收反馈:错误态左边条 --danger;状态行 + 顶层键 chips + meta)
    g += card()
    g.append(rs(24, 516, 3, 152, 0, DANGER))
    g.append(ts(40, 536, "✕", 12, DANGER, 600))
    g.append(ts(56, 536, "第 4 行:缺右括号", 12, DANGER, 500))
    # v2:顶层键名 chips(前 4 个,溢出 +N,mono;撤掉首行原文预览——「{」没有信息量)
    kx = 40
    for k in ["name", "budget", "tags", "active"]:
        p, kw = pill(kx, 552, k, "neutral", size=11, mono=True)
        g.append(p)
        kx += kw + 6
    p, _ = pill(kx, 552, "+1", "neutral", size=11)
    g.append(p)
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(38, 660, "5 键 · 218 B · 3 分钟前", 11, FG2))
    # 状态变体:合法 / 空态
    vb, (o1, o2) = vboxes(["合法 · ✓ 徽标", "EMPTY · 空态"])
    g += vb
    p1, _ = pill(o1[0], o1[1], "✓ JSON 合法 · 12 键", "ok")
    g.append(p1)
    g.append(rs(o1[0], o1[1] + 32, o1[2], 76, 6, BG2))
    _, s1 = spans(o1[0] + 12, o1[1] + 54, [("{", FG2)], size=11)
    g.append(s1)
    _, s2 = spans(o1[0] + 12, o1[1] + 74, [("  ", FG2), ('"name"', LIVE), (": ", FG2), ('"dinner-bot"', OK), (",", FG2)], size=11)
    g.append(s2)
    _, s3 = spans(o1[0] + 12, o1[1] + 94, [("  ", FG2), ('"budget"', LIVE), (": ", FG2), ("80", WARN)], size=11)
    g.append(s3)
    g.append(empty_state(o2[0] + 150, 584, "{ }", "粘贴或输入 JSON", "插入示例"))
    return svg_doc(g)


# ═══ 3.3 W-table ══════════════════════════════════════════════════════════
def w_table():
    g = page("W-table · 表格编辑器", "Airtable / Notion database")
    tab_head(g, "trip.tasks · 6 行 × 4 列")
    cols = [("名称", "Aa", 68, 320, True), ("优先级", "#", 388, 160, False),
            ("状态", "≡", 548, 200, False), ("截止", None, 748, 260, False)]
    # 状态列头 hover(bg-2;v2:排序/⋯ 视觉槽已撤)
    g.append(rs(548, 124, 200, 32, 0, BG2))
    for name, icon, x, cw, req in cols:
        tx = x + 12
        if icon:
            g.append(ts(tx, 145, icon, 11, FG2))
            tx += 18
        elif name == "截止":
            g.append(rs(tx, 133, 12, 12, 2, "none", FG2))
            g.append(ls(tx, 137, tx + 12, 137, FG2))
            tx += 20
        g.append(ts(tx, 145, name, 12, FG1, 500))
        if req:
            g.append(ts(tx + tw(name, 12) + 3, 145, "*", 12, DANGER, 600))
    g.append(ls(40, 156.5, 1040, 156.5, LINE))
    # 行(v2 · 用户验收反馈:KV 式常驻可编辑——值即隐形 input;拖柄/排序槽全撤):
    # r2 hover / r3 选中 / r4 名称格 focus 显形(焦点环 = 「focus 才显」实例)
    rows = [("故宫门票预约", "1", "进行中", "live", "2026-08-06"),
            ("机票比价", "2", "待办", "neutral", "2026-08-08"),
            ("酒店确认单", "3", "已完成", "ok", "2026-08-05"),
            ("租车询价|", "4", "待办", "neutral", "2026-08-10"),
            ("保险购买", "5", "待办", "neutral", "2026-08-12"),
            ("签证材料复印", "6", "待办", "neutral", "2026-08-15")]
    pitch, top0 = 40, 156
    g.append(rs(40, top0 + pitch, 1000, pitch, 0, BG2))  # r2 hover
    g.append(rs(40, top0 + 2 * pitch, 1000, pitch, 0, LIVE, op=0.08))  # r3 选中
    g.append(rs(40, top0 + 2 * pitch, 2, pitch, 0, LIVE))
    for i, (name, n, st, tone, date) in enumerate(rows):
        top = top0 + i * pitch
        base = top + 25
        val, cur = (name[:-1], True) if name.endswith("|") else (name, False)
        g.append(ts(80, base, val, 13, FG0))
        if cur:
            g.append(rs(80 + tw(val, 13) + 1, top + 12, 2, 16, 0, LIVE))
        g.append(ts(400, base, n, 13, FG0))
        p, _ = pill(560, top + 9, st, tone, h=22, size=12)
        g.append(p)
        g.append(ts(780, base, date, 12, FG1, mono=True))
        if i == 1:
            g.append(ts(1024, base, "✕", 12, FG1, anchor="middle"))
        if i < 5:
            g.append(ls(40, top + pitch + 0.5, 1040, top + pitch + 0.5, LINE, op=0.6))
    # 常驻编辑器:平时隐形,focus 才显(框与行高齐,焦点环在框外 1px)
    g.append(rs(72, top0 + 3 * pitch + 4, 308, 32, 6, BG2))
    g.append(ring(72, top0 + 3 * pitch + 4, 308, 32, 6))
    # + 添加行(整宽虚线)
    g.append(rs(40, 400, 1000, 36, 6, "none", LINE, dash="5 4"))
    g.append(ts(540, 423, "+ 添加行", 13, FG2, anchor="middle"))
    # card
    g += card()
    g.append(ts(38, 536, "出行任务表", 13, FG0, 500))
    p, pw = pill(0, 0, "6 行", "neutral")
    g.append(f'<g transform="translate({350 - pw},522)">{p}</g>')
    g.append(ts(38, 562, "名称", 11, FG2, 500))
    g.append(ts(190, 562, "优先级", 11, FG2, 500))
    g.append(ts(250, 562, "状态", 11, FG2, 500))
    p2, _ = pill(322, 552, "+1", "neutral")
    g.append(p2)
    for j, (nm, nn, st, tone) in enumerate([("故宫门票预约", "1", "进行中", LIVE),
                                            ("机票比价", "2", "待办", FG2)]):
        y = 588 + j * 26
        g.append(ts(38, y, nm, 12, FG0))
        g.append(ts(190, y, nn, 12, FG1))
        g.append(cs(254, y - 4, 3, tone))
        g.append(ts(262, y, st, 12, FG1))
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(350, 660, "查看全部 →", 12, LIVE, anchor="end"))
    # 状态变体:空态 / 常编辑(隐形 input + focus 显形)
    vb, (o1, o2) = vboxes(["EMPTY · 空态", "常编辑 · 隐形 input"])
    g += vb
    g.append(ts(o1[0], o1[1] + 8, "名称", 11, FG2, 500))
    g.append(ts(o1[0] + 120, o1[1] + 8, "优先级", 11, FG2, 500))
    g.append(ls(o1[0], o1[1] + 16.5, o1[0] + o1[2], o1[1] + 16.5, LINE))
    g.append(skeleton_row(o1[0], o1[1] + 30, [90, 60]))
    b, _ = button(o1[0], o1[1] + 56, "添加第一行", "primary", h=26)
    g.append(b)
    rows2 = ["机票比价", "酒店确认单|", "保险购买"]
    for i, nm in enumerate(rows2):
        y = o2[1] + 4 + i * 34
        val, cur = (nm[:-1], True) if nm.endswith("|") else (nm, False)
        g.append(ts(o2[0] + 16, y + 17, val, 12, FG0))
        if cur:  # focus 才显的实例
            g.append(rs(o2[0] + 8, y - 1, o2[2] - 24, 32, 6, BG2))
            g.append(ring(o2[0] + 8, y - 1, o2[2] - 24, 32, 6))
            g.append(ts(o2[0] + 16, y + 17, val, 12, FG0))
            g.append(rs(o2[0] + 16 + tw(val, 12) + 1, y + 5, 2, 16, 0, LIVE))
        g.append(ls(o2[0], y + 26.5, o2[0] + o2[2], y + 26.5, LINE, op=0.6))
    return svg_doc(g)


# ═══ 3.4 W-kv ═════════════════════════════════════════════════════════════
def w_kv():
    g = page("W-kv · 键值编辑器", "Postman headers")
    tab_head(g, "headers · 5 条")
    g.append(ts(52, 140, "KEY", 11, FG2, 600))
    g.append(ts(452, 140, "VALUE", 11, FG2, 600))
    g.append(ls(40, 148.5, 1040, 148.5, LINE))
    rows = [("Host", "api.weather.cn", None),
            ("X-Api-Key", "wk_test_9f2c7e11a4|", "focus"),
            ("Accept", "application/json", "dup"),
            ("Accept", "text/html", "dup"),
            ("X-Trace-Id", "7f3a-9c21-44", "hover")]
    top0, pitch = 156, 34
    for i, (k, v, st) in enumerate(rows):
        top = top0 + i * pitch
        base = top + 22
        if st == "dup":
            g.append(rs(40, top, 1000, pitch, 0, WARN, op=0.08))
        if st == "hover":
            g.append(rs(40, top, 1000, pitch, 0, BG2))
        g.append(ts(52, base, k, 12, FG0, 500, mono=True))
        if st == "dup":
            g.append(ts(52 + tw(k, 12, True) + 8, base, "⚠", 11, WARN))
        cur = v.endswith("|")
        g.append(ts(452, base, v[:-1] if cur else v, 13, FG1))
        if cur:
            g.append(rs(452 + tw(v[:-1], 13) + 1, top + 9, 2, 16, 0, LIVE))
        if st == "hover":
            g.append(ts(1024, base, "✕", 12, FG1, anchor="middle"))
    g.append(ring(440, top0 + pitch, 572, pitch, 6))  # value 焦点环
    # 重复 key tooltip(hover ⚠ 出现)
    g += shadow(560, 261, 168, 26, 6, 2)
    g.append(rs(560, 261, 168, 26, 6, BG2, LINE_STRONG))
    g.append(ts(644, 278, "key 重复,后者覆盖前者", 11, FG0, anchor="middle"))
    # + 添加(虚线行)
    g.append(rs(40, 334, 1000, 32, 6, "none", LINE, dash="5 4"))
    g.append(ts(540, 355, "+ 添加", 12, FG2, anchor="middle"))
    # card
    g += card()
    p1, w1 = pill(38, 522, "5 键值", "neutral")
    g.append(p1)
    p2, _ = pill(38 + w1 + 8, 522, "⚠ 重复 ×1", "warn")
    g.append(p2)
    for j, line in enumerate(["Host = api.weather.cn", "X-Api-Key = wk_test_9f2c…",
                              "Accept = application/json"]):
        g.append(ts(38, 562 + j * 24, line, 11, FG1, mono=True))
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(38, 660, "共 5 条 · 1 处重复", 11, FG2))
    # 状态变体:空态 / 加载骨架
    vb, (o1, o2) = vboxes(["EMPTY · 空态", "LOADING · skeleton"])
    g += vb
    g.append(empty_state(o1[0] + 150, 584, "∷", "还没有键值对", "+ 添加第一条"))
    for i in range(3):
        y = o2[1] + 10 + i * 34
        g.append(rs(o2[0], y, 88, 10, 5, BG2))
        g.append(rs(o2[0] + 100, y, 188, 10, 5, BG3))
    return svg_doc(g)


# ═══ 3.5 W-form ═══════════════════════════════════════════════════════════
def w_form():
    g = page("W-form · schema 表单", "Stripe 结账 / Linear 设置")
    tab_head(g, "alert_rule · schema form")
    # v2 · 用户验收反馈:**单列为主**(Stripe 式整齐排版;两列网格撤掉)
    # 字段 1:规则名称(label 上置 → 控件(焦点)→ help 贴控件下)
    g.append(ts(40, 136, "规则名称", 12, FG0, 500))
    g.append(ts(40 + tw("规则名称", 12) + 3, 136, "*", 12, DANGER, 600))
    g.append(input_box(40, 142, 560, value="延迟告警", focused=True))
    g.append(ts(40, 190, "显示在告警列表与通知标题", 11, FG2))
    # 字段 2:阈值 stepper(错误:控件红边 + 行内 ⚠ 人话)
    g.append(ts(40, 226, "阈值(ms)", 12, FG0, 500))
    g.append(ts(40 + tw("阈值(ms)", 12) + 3, 226, "*", 12, DANGER, 600))
    g.append(rs(40, 232, 560, 32, 6, BG2, DANGER))
    g.append(ts(56, 253, "−", 14, FG1, anchor="middle"))
    g.append(ls(72, 240, 72, 256, LINE))
    g.append(ts(320, 253, "0", 12, FG0, mono=True, anchor="middle"))
    g.append(ls(584, 240, 584, 256, LINE))
    g.append(ts(592, 253, "+", 13, FG1, anchor="middle"))
    g.append(ts(40, 280, "⚠", 11, DANGER))
    g.append(ts(56, 280, "阈值需在 1–10000 之间", 11, DANGER))
    # 字段 3:级别 enum chips(中选中,高 hover)
    g.append(ts(40, 312, "级别", 12, FG0, 500))
    cx = 40
    for name, st in [("低", None), ("中", "sel"), ("高", "hover")]:
        c, cw = chip(cx, 318, name, selected=st == "sel", hover=st == "hover")
        g.append(c)
        cx += cw + 8
    g.append(ts(40, 364, "影响通知优先级与颜色", 11, FG2))
    # 字段 4:boolean switch(label 上置,switch 在下,help 贴控件下)
    g.append(ts(40, 396, "启用告警", 12, FG0, 500))
    g.append(switch(40, 402, on=True))
    g.append(ts(40, 438, "关闭后规则暂停评估", 11, FG2))
    # 嵌套 object:卡片化分组(标题 500 + 描述弱色 + 内边距)
    g.append(rs(40, 460, 1000, 96, 8, BG2, LINE))
    g.append(ts(56, 484, "通知对象", 13, FG0, 500))
    g.append(ts(56 + tw("通知对象", 13) + 10, 484, "alertmanager 接收方", 11, FG2))
    g.append(ts(56, 508, "邮箱", 11, FG2, 500))
    g.append(rs(56, 514, 460, 32, 6, BG1, LINE))
    g.append(ts(68, 535, "oncall@agent-os.dev", 13, FG0))
    # 数组项:虚线添加
    g.append(ts(40, 590, "通知渠道", 12, FG0, 500))
    g.append(rs(40, 596, 560, 32, 6, BG2, LINE))
    g.append(ts(52, 617, "邮件", 13, FG0))
    g.append(ts(588, 618, "▾", 12, FG2, anchor="end"))
    g.append(rs(40, 636, 560, 32, 6, "none", LINE, dash="5 4"))
    g.append(ts(320, 657, "+ 添加 Webhook", 12, FG2, anchor="middle"))
    # 底部操作行贴底:reset 居左 + live 圆点「有未保存改动」,主按钮居右
    b1, w1 = button(40, 684, "重置", "ghost", h=26)
    g.append(b1)
    g.append(cs(40 + w1 + 14, 697, 3, LIVE))
    g.append(ts(40 + w1 + 24, 701, "有未保存改动", 11, FG2))
    b2, w2 = button(0, 0, "保存规则", "primary", h=26)
    g.append(f'<g transform="translate({1040 - w2},684)">{b2}</g>')
    # card:必填完成度
    g += card()
    g.append(ts(38, 536, "告警规则 · form", 13, FG0, 500))
    g.append(rs(38, 548, 312, 4, 2, BG3))
    g.append(rs(38, 548, 234, 4, 2, LIVE))
    g.append(ts(38, 578, "必填 3/4", 12, FG0))
    p1, w1 = pill(0, 0, "Webhook URL", "neutral")
    g.append(f'<g transform="translate({322 - w1},566)">{p1}</g>')
    p2, _ = pill(326, 566, "+1", "neutral")
    g.append(p2)
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(38, 660, "schema · 6 字段 · 1 嵌套组", 11, FG2))
    # 状态变体:空 schema / 加载 / 未修改(reset 禁用)
    vb, (o1, o2, o3) = vboxes(["EMPTY · 空 schema", "LOADING · skeleton", "未修改 · reset 禁用"])
    g += vb
    g.append(empty_state(o1[0] + 92, 580, "✎", "schema 为空", "粘贴 schema"))
    g.append(rs(o2[0], o2[1] + 4, 60, 8, 4, BG2))
    g.append(rs(o2[0], o2[1] + 20, o2[2], 26, 6, BG3))
    g.append(rs(o2[0], o2[1] + 62, 48, 8, 4, BG2))
    g.append(rs(o2[0], o2[1] + 78, o2[2], 26, 6, BG3))
    b3, _ = button(o3[0], o3[1] + 12, "重置", "ghost", h=26, disabled=True)
    g.append(b3)
    b4, _ = button(o3[0] + 68, o3[1] + 12, "保存", "primary", h=26)
    g.append(b4)
    g.append(ts(o3[0], o3[1] + 64, "dirty=false", 11, FG2, mono=True))
    g.append(ts(o3[0], o3[1] + 84, "重置钮禁用", 11, FG2))
    return svg_doc(g)


# ═══ 3.6 W-list ═══════════════════════════════════════════════════════════
def w_list():
    g = page("W-list · 可选列表", "Linear ⌘K / VS Code quick pick")
    tab_head(g, "mcp.tools · 多选")
    # 过滤框(焦点环)
    g.append(input_box(40, 124, 1000, h=36, value="http", icon="🔍", mono=True, focused=True))
    g.append(ts(1028, 147, "Esc 清空", 11, FG2, anchor="end"))
    items = [("net.", "_fetch", "v2.1 · 工具", "sel"),
             ("net.", "_server", "v1.4 · 工具", "hover"),
             ("ops.", "_probe", "v0.9 · 工具", "sel"),
             ("data.", "_export", "v0.3 · 工具", None),
             ("legacy.", "_bridge", "v1.0 · 工具", None),
             ("dev.", "_debug", "v0.1 · 实验", None)]
    top0, pitch = 168, 36
    for i, (pre, post, meta, st) in enumerate(items):
        top = top0 + i * pitch
        base = top + 23
        if st == "sel":
            g.append(rs(40, top, 1000, pitch, 0, LIVE, op=0.08))
            g.append(rs(40, top, 2, pitch, 0, LIVE))
            g.append(ts(1020, base, "✓", 12, LIVE, anchor="middle"))
        elif st == "hover":
            g.append(rs(40, top, 1000, pitch, 0, BG2))
        _, svg = spans(56, base, [(pre, FG0, 500), ("http", LIVE, 500), (post, FG0, 500)], size=12)
        g.append(svg)
        g.append(ts(996, base, meta, 11, FG2, anchor="end"))
    # 多选浮条
    g += shadow(430, 400, 220, 32, 16, 2)
    g.append(rs(430, 400, 220, 32, 16, BG2, LINE))
    _, svg = spans(500, 421, [("已选 2", FG0), (" · ", FG2), ("清除", LIVE)], size=12, mono=False)
    g.append(svg)
    # card
    g += card()
    g.append(ts(38, 532, "当前选中", 11, FG2, 500))
    g.append(ts(38, 560, "net.http_fetch", 12, FG0, 500, mono=True))
    g.append(ts(38, 584, "v2.1 · 工具 · 共 24 项", 11, FG2))
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(350, 660, "更换 →", 12, LIVE, anchor="end"))
    # 状态变体:无结果 / 加载
    vb, (o1, o2) = vboxes(["EMPTY · 无结果", "LOADING · skeleton"])
    g += vb
    g.append(input_box(o1[0], o1[1], o1[2], h=30, value="http2", icon="🔍", mono=True))
    g.append(ts(o1[0] + o1[2] / 2, o1[1] + 62, "没有匹配『http2』的项", 12, FG1, anchor="middle"))
    bw = tw("清除搜索", 12) + 24
    b, _ = button(o1[0] + (o1[2] - bw) / 2, o1[1] + 78, "清除搜索", "ghost", h=26)
    g.append(b)
    g.append(rs(o2[0], o2[1], o2[2], 30, 6, BG2))
    for i in range(3):
        g.append(skeleton_row(o2[0], o2[1] + 46 + i * 26, [140, 80]))
    return svg_doc(g)


# ═══ 3.7 W-tree ═══════════════════════════════════════════════════════════
def w_tree():
    g = page("W-tree · 命名空间树", "VS Code 资源管理器")
    tab_head(g, "skills · namespace tree")
    # (chevron, 名称, 层级, 目录?, 徽标, 状态)
    nodes = [("▾", "system", 0, True, "5", "anc"),
             ("▾", "file", 1, True, "2", None),
             (None, "read", 2, False, None, None),
             (None, "write", 2, False, None, "hover"),
             (None, "net.http_fetch", 1, False, None, "cur"),
             ("▾", "weather", 0, True, "2", None),
             (None, "forecast", 1, False, None, None),
             (None, "query", 1, False, None, None),
             ("▸", "ops", 0, True, "1", None),
             ("▸", "data", 0, True, "4", None)]
    # v2 · 用户验收反馈:紧凑化(行高 24px、缩进 12px/级、chevron 10px;
    # 行内只留 图标 + 名称 + 计数胶囊弱)
    top0, pitch = 128, 24
    for i, (chev, name, lv, is_dir, badge, st) in enumerate(nodes):
        top = top0 + i * pitch
        base = top + 17
        if st == "hover":
            g.append(rs(40, top, 1000, pitch, 0, BG2))
        elif st == "cur":
            g.append(rs(40, top, 1000, pitch, 0, LIVE, op=0.08))
            g.append(rs(40, top, 2, pitch, 0, LIVE))
        cx = 48 + lv * 12
        if chev:
            g.append(ts(cx, base, chev, 10, FG2))
        nx = cx + 14
        if is_dir:
            fg = FG0 if st == "anc" else FG1  # 父链名称转正文色
            g.append(ts(nx, base, name, 13, fg, 500))
        else:
            g.append(ts(nx, base, name, 12, FG0 if st == "cur" else FG1, mono=True))
        if badge:
            bx = nx + tw(name, 13 if is_dir else 12, not is_dir) + 8
            p, _ = pill(bx, top + 3, badge, "neutral", h=18)
            g.append(p)
    g.append(ring(40, 124, 1000, 10 * pitch + 8, 6))
    # card:面包屑 + N 叶子
    g += card()
    g.append(ts(38, 532, "当前位置", 11, FG2, 500))
    _, svg = spans(38, 558, [("system", FG1), (" / ", FG2), ("net", FG1), (" / ", FG2),
                             ("http_fetch", FG0, 500)])
    g.append(svg)
    p, _ = pill(38, 576, "12 叶子", "neutral")
    g.append(p)
    p2, _ = pill(38 + 12 * 6 + 40, 576, "3 命名空间", "neutral")
    g.append(p2)
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(350, 660, "打开 →", 12, LIVE, anchor="end"))
    # 状态变体:过滤中 / 空态
    vb, (o1, o2) = vboxes(["过滤「fetch」· 非命中 50%", "EMPTY · 无结果"])
    g += vb
    g.append(input_box(o1[0], o1[1], o1[2], h=30, value="fetch", icon="🔍", mono=True))
    g.append(ts(o1[0] + 4, o1[1] + 52, "▾", 11, FG2))
    g.append(ts(o1[0] + 22, o1[1] + 52, "system", 12, FG0, 500))
    _, svg = spans(o1[0] + 34, o1[1] + 78, [("net.http_", FG1), ("fetch", LIVE)], size=12)
    g.append(svg)
    g.append(f'<g opacity="0.5">{ts(o1[0] + 4, o1[1] + 104, "▸", 11, FG2)}'
             f'{ts(o1[0] + 22, o1[1] + 104, "weather", 12, FG1, 500)}</g>')
    g.append(ts(o2[0] + o2[2] / 2, o2[1] + 44, "没有匹配『ssh』的项", 12, FG1, anchor="middle"))
    bw = tw("清除搜索", 12) + 24
    b, _ = button(o2[0] + (o2[2] - bw) / 2, o2[1] + 62, "清除搜索", "ghost", h=26)
    g.append(b)
    return svg_doc(g)


# ═══ 3.8 W-date ═══════════════════════════════════════════════════════════
import datetime as _dt

TODAY = _dt.date(2026, 8, 4)
RANGE = (_dt.date(2026, 8, 1), _dt.date(2026, 8, 4))
HOVER_DAY = _dt.date(2026, 8, 12)


def _month_grid(g, year, month, gx, gy, sel_range=False, hover=True):
    """月历格:28×28 圆角 6,pitch 32;今天 live 描边;区间为连续 12% live 色带
    (同行连续格一条带、端点之间无圆角),仅两端点实底圆角。"""
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(year, month)
    if sel_range:  # 色带先画(端点实底后画压在上面)
        for j, week in enumerate(weeks):
            run = None
            for i, d in enumerate(week):
                if RANGE[0] <= d <= RANGE[1]:
                    run = i if run is None else run
                elif run is not None:
                    g.append(rs(gx + run * 32, gy + j * 32, (i - run) * 32, 28, 0, LIVE, op=0.12))
                    run = None
            if run is not None:
                g.append(rs(gx + run * 32, gy + j * 32, (7 - run) * 32, 28, 0, LIVE, op=0.12))
    for j, week in enumerate(weeks):
        for i, d in enumerate(week):
            x, y = gx + i * 32, gy + j * 32
            in_month = d.month == month
            s = str(d.day)
            if sel_range and d in RANGE:
                g.append(rs(x, y, 28, 28, 6, LIVE))
                g.append(ts(x + 14, y + 19, s, 12, WHITE, 600, anchor="middle"))
                if d == TODAY:  # 今天恰逢端点:实底下加白点双编码
                    g.append(cs(x + 14, y + 23, 1.5, WHITE))
                continue
            if hover and d == HOVER_DAY:
                g.append(rs(x, y, 28, 28, 6, BG2))
            fg, op = FG0, None
            if not in_month:
                fg, op = FG2, 0.35
            g.append(ts(x + 14, y + 19, s, 12, fg, 400, anchor="middle", op=op))
            if d == TODAY:
                g.append(cs(x + 14, y + 14, 11, "none", stroke=LIVE, sw=1.5))


def w_date():
    g = page("W-date · 日期控件", "macOS 日历弹层 / Google Calendar")
    tab_head(g, "travel.dates · range")
    # range 双框(start 焦点)
    g.append(input_box(40, 124, 152, value="2026-08-01", icon="📅", mono=True, focused=True))
    g.append(ts(204, 145, "→", 13, FG2, anchor="middle"))
    g.append(input_box(216, 124, 152, value="2026-08-04", icon="📅", mono=True))
    # 快捷 chips(本周选中)
    cx = 40
    for name, sel in [("今天", False), ("昨天", False), ("本周", True), ("上周", False)]:
        c, cw = chip(cx, 172, name, selected=sel)
        g.append(c)
        cx += cw + 8
    g.append(ts(40, 224, "支持手输 ISO 8601 格式", 11, FG2))
    # 纠序示例(结束 < 开始 → 自动纠序并提示)
    g.append(rs(40, 248, 328, 64, 6, BG2, LINE))
    _, svg = spans(52, 272, [("2026-08-10", FG2), (" → ", FG2), ("2026-08-04", FG0)], size=12)
    g.append(svg)
    g.append(ts(52, 298, "↔ 结束早于开始 · 已自动调整顺序", 11, LIVE))
    g.append(ts(40, 452, "时区 Asia/Shanghai · UTC+8", 11, FG2))
    # 弹层(--shadow-3,双月 + 底部操作)
    g += shadow(384, 124, 656, 316, 8, 3)
    g.append(rs(384, 124, 656, 316, 8, BG1, LINE))
    for k, (year, month, gx) in enumerate([(2026, 8, 464), (2026, 9, 736)]):
        center = gx + 112
        g.append(ts(center, 152, f"{year} 年 {month} 月", 13, FG0, 500, anchor="middle"))
        for i, wd in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
            g.append(ts(gx + 14 + i * 32, 180, wd, 11, FG2, anchor="middle"))
        _month_grid(g, year, month, gx, 192, sel_range=(k == 0))
    g.append(ts(464, 152, "‹", 14, FG1, anchor="middle"))
    g.append(ts(960, 152, "›", 14, FG1, anchor="middle"))
    g.append(ls(712, 140, 712, 424, LINE))
    g.append(ls(400, 396.5, 1024, 396.5, LINE))
    _, svg = spans(400, 420, [("2026-08-01", FG1), (" → ", FG2), ("2026-08-04", FG1),
                              (" · 共 4 天", FG2)], size=11)
    g.append(svg)
    b1, w1 = button(0, 0, "取消", "ghost", h=26)
    g.append(f'<g transform="translate(912,404)">{b1}</g>')
    b2, w2 = button(0, 0, "确定", "primary", h=26)
    g.append(f'<g transform="translate({1024 - w2},404)">{b2}</g>')
    # card
    g += card()
    g.append(ts(38, 532, "行程日期", 11, FG2, 500))
    x_end, svg = spans(38, 560, [("2026-08-01", FG0), (" → ", FG2), ("2026-08-04", FG0)])
    g.append(svg)
    p, _ = pill(x_end + 12, 548, "本周", "live")
    g.append(p)
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(38, 660, "range · 共 4 天", 11, FG2))
    # 状态变体:非法输入 / 禁用
    vb, (o1, o2) = vboxes(["非法输入 · 红边行内提示", "DISABLED · 禁用"])
    g += vb
    g.append(input_box(o1[0], o1[1], o1[2], value="下周三上午", icon="📅", error=True))
    g.append(ts(o1[0], o1[1] + 52, "⚠", 11, DANGER))
    g.append(ts(o1[0] + 16, o1[1] + 52, "无法识别,示例:2026-08-04", 11, DANGER))
    g.append(input_box(o2[0], o2[1], o2[2], value="2026-08-04", icon="📅", mono=True, disabled=True))
    g.append(ts(o2[0], o2[1] + 52, "已锁定 · 由上游步骤提供", 11, FG2))
    return svg_doc(g)


# ═══ 3.9 W-chart ══════════════════════════════════════════════════════════
COST = [32.0, 41.5, 28.9, 52.3, 44.0, 61.8, 42.7, 48.2]
TOKENS = [21.0, 26.0, 18.0, 33.0, 27.0, 38.0, 30.0, 36.0]
DATES = ["07-28", "07-29", "07-30", "07-31", "08-01", "08-02", "08-03", "08-04"]
PX0, PX1, PY0, PY1, SCALE = 96, 1040, 408, 168, 3.0  # y:0–80


def _pt(i, v):
    return PX0 + i * (PX1 - PX0) / 7, PY0 - v * SCALE


def w_chart():
    g = page("W-chart · 图表", "Stripe Dashboard / Linear Insights")
    tab_head(g, "usage · line · 8 天")
    # 头部:标题 + 图例(budget 隐藏 40%)+ 抽稀提示 + segmented
    g.append(ts(40, 148, "每日花费", 13, FG0, 500))
    g.append(cs(146, 144, 4, LIVE))
    g.append(ts(156, 148, "cost", 12, FG1))
    g.append(cs(202, 144, 4, SIG_COMPRESS))
    g.append(ts(212, 148, "tokens", 12, FG1))
    g.append(f'<g opacity="0.4">{cs(268, 144, 4, SIG_SIDECAR)}{ts(278, 148, "budget", 12, FG1)}</g>')
    g.append(ts(900, 148, "已抽稀 12,000 → 500", 11, FG2, anchor="end"))
    seg, _ = segmented(928, 130, ["图表", "表格"], active=0, focus_idx=1)
    g.append(seg)
    # 网格(仅水平虚线)+ 轴标签
    for v in range(0, 81, 20):
        y = PY0 - v * SCALE
        g.append(ls(PX0, y, PX1, y, LINE, dash="2 5", op=0.8))
        g.append(ts(84, y + 4, str(v), 11, FG2, anchor="end", mono=True))
    for i, d in enumerate(DATES):
        x, _ = _pt(i, 0)
        g.append(ts(x, 428, d, 11, FG2, anchor="middle", mono=True))
    # 面积渐变(--live 10% → 0)+ 两序列折线
    g.append('<defs><linearGradient id="ag" x1="0" y1="0" x2="0" y2="1">'
             f'<stop offset="0" stop-color="{LIVE}" stop-opacity="0.1"/>'
             f'<stop offset="1" stop-color="{LIVE}" stop-opacity="0"/>'
             "</linearGradient></defs>")
    pts = [_pt(i, v) for i, v in enumerate(COST)]
    d_area = f"M{pts[0][0]:.1f} {PY0} " + " ".join(f"L{x:.1f} {y:.1f}" for x, y in pts) + f" L{pts[-1][0]:.1f} {PY0} Z"
    g.append(ps(d_area, "url(#ag)"))
    g.append(ps("M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts), "none", LIVE, 2))
    pts2 = [_pt(i, v) for i, v in enumerate(TOKENS)]
    g.append(ps("M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts2), "none", SIG_COMPRESS, 2))
    # hover:垂直参考线 + 序列点 + tooltip 卡
    hx, hy = _pt(5, COST[5])
    g.append(ls(hx, PY1, hx, PY0, LINE_STRONG))
    g.append(cs(hx, hy, 4, LIVE, stroke=BG1, sw=2))
    g.append(cs(hx, _pt(5, TOKENS[5])[1], 4, SIG_COMPRESS, stroke=BG1, sw=2))
    g += shadow(786, 196, 180, 88, 8, 2)
    g.append(rs(786, 196, 180, 88, 8, BG2, LINE))
    g.append(ts(798, 218, "8 月 2 日", 11, FG2))
    g.append(cs(804, 236, 3.5, LIVE))
    g.append(ts(814, 240, "cost", 12, FG1))
    g.append(ts(954, 240, "61.8", 12, FG0, mono=True, anchor="end"))
    g.append(cs(804, 262, 3.5, SIG_COMPRESS))
    g.append(ts(814, 266, "tokens", 12, FG1))
    g.append(ts(954, 266, "38.0", 12, FG0, mono=True, anchor="end"))
    # card:大读数 + 涨跌徽标 + 迷你折线
    g += card()
    g.append(ts(38, 532, "cost · 最新值", 11, FG2, 500))
    g.append(ts(38, 564, "¥48.20", 20, FG0, 600, mono=True))
    p, _ = pill(150, 548, "▲ 12.4%", "ok", h=22, size=12)
    g.append(p)
    sx0, sw_, sy0, sh_ = 224, 120, 528, 40
    vmin, vmax = min(COST), max(COST)
    spts = [(sx0 + i * sw_ / 7, sy0 + sh_ - (v - vmin) / (vmax - vmin) * (sh_ - 4) - 2)
            for i, v in enumerate(COST)]
    g.append(ps("M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in spts), "none", LIVE, 1.5))
    g.append(cs(spts[-1][0], spts[-1][1], 3, LIVE))
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(38, 660, "近 8 日 · 均值 ¥43.86", 11, FG2))
    # 状态变体:空态 / 表格视图
    vb, (o1, o2) = vboxes(["EMPTY · 空态", "表格视图 · 同数据等价"])
    g += vb
    cx0 = o1[0] + 150
    for i, hh in enumerate([18, 30, 24]):
        g.append(rs(cx0 - 34 + i * 24, 556 - hh, 14, hh, 2, FG2, op=0.5))
    g.append(ts(cx0, 584, "还没有数据", 12, FG1, anchor="middle"))
    bw = tw("接入数据源", 12) + 24
    b, _ = button(cx0 - bw / 2, 598, "接入数据源", "ghost", h=26)
    g.append(b)
    tw_ = o2[2]
    g.append(rs(o2[0], o2[1], tw_, 24, 4, BG2))
    for k, htxt in enumerate(["series", "x", "y"]):
        g.append(ts(o2[0] + 10 + k * 90, o2[1] + 16, htxt, 11, FG2, 500, mono=True))
    for j, (s_, x_, y_) in enumerate([("cost", "1", "32.0"), ("cost", "2", "41.5"), ("tokens", "1", "21.0")]):
        y = o2[1] + 24 + j * 22
        g.append(ls(o2[0], y + 0.5, o2[0] + tw_, y + 0.5, LINE, op=0.6))
        g.append(ts(o2[0] + 10, y + 16, s_, 11, FG1, mono=True))
        g.append(ts(o2[0] + 100, y + 16, x_, 11, FG1, mono=True))
        g.append(ts(o2[0] + 190, y + 16, y_, 11, FG1, mono=True))
    return svg_doc(g)


# ═══ 3.10 W-log ═══════════════════════════════════════════════════════════
def w_log():
    g = page("W-log · 日志查看器", "Datadog / Vercel Runtime Logs")
    tab_head(g, "run 20260804-2301 · stdout")
    # 工具行:过滤框(焦点)+ 级别 chips + 复制/暂停
    g.append(input_box(40, 124, 220, h=28, value="timeout", icon="🔍", mono=True, focused=True))
    cx = 272
    for name, act in [("all", True), ("pre", False), ("post", False), ("err", False)]:
        c, cw = chip(cx, 126, name, selected=act, h=24, size=11)
        g.append(c)
        cx += cw + 8
    b1, _ = button(0, 0, "⧉ 复制", "ghost", h=28)
    g.append(f'<g transform="translate(880,124)">{b1}</g>')
    b2, _ = button(0, 0, "⏸ 暂停跟随", "ghost", h=28)
    g.append(f'<g transform="translate(952,124)">{b2}</g>')
    # 深色面板(--log-bg)
    g.append(rs(40, 164, 1000, 272, 6, LOG_BG, LINE))
    g.append(ts(56, 183, "已截断,仅保留最近 500 行", 11, FG2))
    g.append(ls(56, 191.5, 1024, 191.5, LINE, op=0.5))
    lines = [("23:01:12", "pre", SIG_LLM, 'tool.call weather.query({"city":"昆明"})', False),
             ("23:01:12", "post", SIG_TOOL, "weather.query → 200 OK · 84ms", False),
             ("23:01:13", "pre", SIG_LLM, "llm.complete model=kimi-k2 temp=0.3", False),
             ("23:01:14", "post", SIG_TOOL, "llm.complete → 1842 tok · 2.1s", False),
             ("23:01:14", "err", SIG_BUDGET, "http_fetch timeout after 3000ms · retry 1/2", True),
             ("23:01:15", "post", SIG_TOOL, "http_fetch → 200 OK · 1210ms", False),
             ("23:01:15", "pre", SIG_LLM, "budget.check remaining CNY 3.20", False),
             ("23:01:16", "post", SIG_TOOL, "budget.check OK", False),
             ("23:01:16", "pre", SIG_LLM, 'tool.call hotel.search({"city":"昆明"})', False),
             ("23:01:17", "post", SIG_TOOL, "hotel.search → 200 OK · 96ms", False)]
    g.append(rs(44, 269, 992, 18, 3, BG2))  # 命中行 hover(过滤:仅此行未弱化)
    for i, (t, kind, color, text, hit) in enumerate(lines):
        base = 210 + i * 18
        row = (ts(56, base, t, 11, FG2, mono=True)
               + rs(118, base - 9, 2, 12, 0, color)
               + ts(128, base, kind, 11, color, 500, mono=True)
               + ts(168, base, text, 12, FG1, mono=True))
        g.append(row if hit else f'<g opacity="0.4">{row}</g>')
    g.append(rs(1030, 200, 4, 80, 2, BG3))  # 纵向滚动条 thumb
    # 回到底部浮胶囊
    g += shadow(866, 392, 158, 30, 15, 2)
    g.append(rs(866, 392, 158, 30, 15, BG2, LINE_STRONG))
    g.append(ts(945, 411, "↓ 回到底部", 12, FG0, anchor="middle"))
    # card
    g += card()
    p1, w1 = pill(38, 522, "128 行", "neutral")
    g.append(p1)
    p2, _ = pill(38 + w1 + 8, 522, "err ×2", "danger")
    g.append(p2)
    for j, (color, text) in enumerate([(SIG_BUDGET, "http_fetch timeout after 3000ms (1/2)"),
                                       (SIG_TOOL, "budget.check OK · remaining CNY 3.20"),
                                       (SIG_TOOL, "hotel.search → 200 OK · 96ms")]):
        y = 560 + j * 20
        g.append(rs(38, y - 9, 2, 12, 0, color))
        g.append(ts(48, y, text, 11, FG1, mono=True))
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(38, 660, "跟随中 · 上限 500 行", 11, FG2))
    # 状态变体:空态 / 暂停跟随
    vb, (o1, o2) = vboxes(["EMPTY · 空态", "已暂停 · 新到 3 行"])
    g += vb
    g.append(rs(o1[0], o1[1], o1[2], 120, 6, LOG_BG))
    g.append(ts(o1[0] + o1[2] / 2, o1[1] + 52, "还没有日志", 12, FG1, anchor="middle"))
    g.append(ts(o1[0] + o1[2] / 2, o1[1] + 76, "运行后自动写入", 11, FG2, anchor="middle"))
    g.append(rs(o2[0], o2[1], o2[2], 120, 6, LOG_BG))
    row = (rs(o2[0] + 12, o2[1] + 20, 2, 12, 0, SIG_TOOL)
           + ts(o2[0] + 22, o2[1] + 30, "hotel.search → 200 OK", 11, FG1, mono=True))
    g.append(f'<g opacity="0.4">{row}</g>')
    row2 = (rs(o2[0] + 12, o2[1] + 40, 2, 12, 0, SIG_LLM)
            + ts(o2[0] + 22, o2[1] + 50, "tool.call flight.search(…)", 11, FG1, mono=True))
    g.append(f'<g opacity="0.4">{row2}</g>')
    g += shadow(o2[0] + 30, o2[1] + 72, 240, 28, 14, 2)
    g.append(rs(o2[0] + 30, o2[1] + 72, 240, 28, 14, BG2, LINE_STRONG))
    g.append(ts(o2[0] + 150, o2[1] + 90, "⏸ 已暂停 · 3 行新日志 ↓", 11, FG0, anchor="middle"))
    return svg_doc(g)


# ═══ 3.11 W-diff ══════════════════════════════════════════════════════════
def w_diff():
    g = page("W-diff · 差异查看器", "GitHub Files changed")
    tab_head(g, "prompt diff · unified")
    # 文件头:路径 + 增删计数 + segmented(统一 激活;分屏 焦点)
    g.append(ts(40, 145, "members/lab.travel/prompt.md", 12, FG0, 500, mono=True))
    p1, w1 = pill(262, 131, "+4", "ok", h=20)
    g.append(p1)
    p2, _ = pill(262 + w1 + 8, 131, "−2", "danger", h=20)
    g.append(p2)
    seg, _ = segmented(916, 124, ["分屏", "统一"], active=1, focus_idx=0)
    g.append(seg)
    # 成员行 + tier 徽标
    g.append(ts(40, 176, "lab.travel", 12, FG1, mono=True))
    p3, _ = pill(130, 162, "▲ escalate", "warn", h=18)
    g.append(p3)
    # hunk 头
    g.append(rs(40, 188, 1000, 22, 4, LIVE, op=0.06))
    g.append(ts(52, 203, "@@ -12,4 +12,5 @@", 11, FG1, mono=True))
    rows = [("ctx", "12", "12", "你是旅行规划助手,按预算与偏好出方案。"),
            ("del", "13", "", "回答控制在五句话以内,语气轻松。"),
            ("add", "", "13", "回答控制在三句话以内,先给结论。"),
            ("add", "", "14", "预算人均 ¥500 以内。"),
            ("add", "", "15", "每天给出 1 个备选方案。"),
            ("ctx", "14", "16", "输出:行程 + 预算表 + 风险提示。")]
    top0, pitch = 210, 22
    for i, (kind, old, new, text) in enumerate(rows):
        top = top0 + i * pitch
        base = top + 15
        if kind == "add":
            g.append(rs(40, top, 1000, pitch, 0, OK, op=0.08))
            g.append(rs(40, top, 2, pitch, 0, OK))
        elif kind == "del":
            g.append(rs(40, top, 1000, pitch, 0, DANGER, op=0.08))
            g.append(rs(40, top, 2, pitch, 0, DANGER))
        g.append(ts(88, base, old, 11, FG2, mono=True, anchor="end"))
        g.append(ts(116, base, new, 11, FG2, mono=True, anchor="end"))
        sign = {"add": ("+", OK), "del": ("−", DANGER)}.get(kind, ("", FG2))
        if sign[0]:
            g.append(ts(128, base, sign[0], 12, sign[1], anchor="middle"))
        g.append(ts(144, base, text, 12, FG1 if kind == "ctx" else FG0, mono=True))
    # 折叠上下文(hover 实例)
    g.append(rs(40, 342, 1000, 22, 0, BG2))
    g.append(ts(144, 357, "[+] 展开 6 行", 11, FG2, mono=True))
    # hunk 2
    g.append(rs(40, 370, 1000, 22, 4, LIVE, op=0.06))
    g.append(ts(52, 385, "@@ -30,2 +31,2 @@", 11, FG1, mono=True))
    g.append(rs(40, 392, 1000, 22, 0, DANGER, op=0.08))
    g.append(rs(40, 392, 2, 22, 0, DANGER))
    g.append(ts(88, 407, "30", 11, FG2, mono=True, anchor="end"))
    g.append(ts(128, 407, "−", 12, DANGER, anchor="middle"))
    g.append(ts(144, 407, "不要推荐生食。", 12, FG0, mono=True))
    g.append(rs(40, 414, 1000, 22, 0, OK, op=0.08))
    g.append(rs(40, 414, 2, 22, 0, OK))
    g.append(ts(116, 429, "31", 11, FG2, mono=True, anchor="end"))
    g.append(ts(128, 429, "+", 12, OK, anchor="middle"))
    g.append(ts(144, 429, "避免生食与隔夜海鲜。", 12, FG0, mono=True))
    # card
    g += card()
    g.append(ts(38, 536, "prompt.md", 12, FG0, 500, mono=True))
    p1, w1 = pill(0, 0, "+4", "ok", h=20)
    g.append(f'<g transform="translate({342 - w1 - 36},522)">{p1}</g>')
    p2, _ = pill(310, 522, "−2", "danger", h=20)
    g.append(p2)
    g.append(rs(38, 550, 312, 20, 4, DANGER, op=0.08))
    g.append(ts(46, 564, "− 回答控制在五句话以内,语气轻松。", 11, FG1, mono=True))
    g.append(rs(38, 574, 312, 20, 4, OK, op=0.08))
    g.append(ts(46, 588, "+ 回答控制在三句话以内,先给结论。", 11, FG1, mono=True))
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(38, 660, "2 hunk · unified", 11, FG2))
    g.append(ts(350, 660, "查看全部 →", 12, LIVE, anchor="end"))
    # 状态变体:分屏 / 无变更
    vb, (o1, o2) = vboxes(["分屏 split · 小样", "无变更"])
    g += vb
    half = (o1[2] - 8) / 2
    g.append(ts(o1[0], o1[1] + 10, "旧", 11, FG2, 500))
    g.append(ts(o1[0] + half + 8, o1[1] + 10, "新", 11, FG2, 500))
    for j in range(2):
        y = o1[1] + 18 + j * 24
        g.append(rs(o1[0], y, half, 22, 4, DANGER, op=0.08))
        g.append(rs(o1[0] + half + 8, y, half, 22, 4, OK, op=0.08))
    g.append(ts(o1[0] + 6, o1[1] + 33, "− 五句话以内", 11, FG1, mono=True))
    g.append(ts(o1[0] + half + 14, o1[1] + 33, "+ 三句话以内", 11, FG1, mono=True))
    g.append(ts(o1[0] + 6, o1[1] + 57, "− 语气轻松", 11, FG1, mono=True))
    g.append(ts(o1[0] + half + 14, o1[1] + 57, "+ 先给结论", 11, FG1, mono=True))
    g.append(ts(o2[0] + o2[2] / 2, 578, "✓", 20, OK, anchor="middle"))
    g.append(ts(o2[0] + o2[2] / 2, 606, "没有差异 · 与 v3 一致", 12, FG1, anchor="middle"))
    return svg_doc(g)


# ═══ 3.12 W-md ════════════════════════════════════════════════════════════
def w_md():
    g = page("W-md · Markdown 查看器", "GitHub README / Notion 页面")
    # v2 · 用户裁决:view source 选择(segmented「预览 | 源码」置右上)
    sg, sgw = segmented(0, 0, ["预览", "源码"], active=0)
    g.append(f'<g transform="translate({1040 - sgw},88)">{sg}</g>')
    tab_head(g, "trip_report.md · 渲染视图")
    # 左栏:h1 + 段落(行内 code / 链接 hover 下划线)+ h2 + 列表 + 引用
    g.append(ts(40, 140, "云南六日行程方案", 20, FG0, 600))
    g.append(ls(40, 154.5, 520, 154.5, LINE))
    x_end, svg = spans(40, 178, [("以下方案按 ", FG0), ("预算 ¥5000", FG0, 600), (" 生成,", FG0)],
                       size=13, mono=False)
    g.append(svg)
    x2 = 40
    x2, s1 = spans(x2, 200, [("含 ", FG0)], size=13, mono=False)
    g.append(s1)
    g.append(rs(x2 - 3, 186, tw("budget.json", 12, True) + 6, 18, 3, BG3))
    x2, s2 = spans(x2, 200, [("budget.json", FG0)], size=12)
    g.append(s2)
    x2, s3 = spans(x2 + 3, 200, [(" 与 ", FG0)], size=13, mono=False)
    g.append(s3)
    x3, s4 = spans(x2, 200, [("原始需求", LIVE)], size=13, mono=False)
    g.append(s4)
    g.append(ls(x2, 204, x3, 204, LIVE))  # 链接 hover 下划线
    _, s5 = spans(x3, 200, [(" 文档。", FG0)], size=13, mono=False)
    g.append(s5)
    g.append(ts(40, 238, "第 1 天 · 抵达昆明", 16, FG0, 600))
    for j, item in enumerate(["长水机场接机,14:20 落地", "入住翠湖周边酒店", "晚餐:过桥米线(正义坊)"]):
        y = 262 + j * 22
        g.append(ts(44, y, "•", 13, FG2))
        g.append(ts(64, y, item, 13, FG0))
    g.append(rs(40, 322, 3, 30, 0, LINE_STRONG))
    g.append(ts(56, 341, "高原紫外线强,备好防晒与薄外套。", 12, FG1))
    # 右栏:代码块(⧉ 复制钮 hover 显 + 焦点环)+ 表格 + h3
    g.append(rs(560, 96, 480, 124, 8, LOG_BG))
    code = [("# 生成行程预算表", FG2), ("plan = run(", FG1),
            ('    "travel_planner",', FG1), ('    city="昆明", days=6', FG1), (")", FG1)]
    for j, (line, fg) in enumerate(code):
        g.append(ts(576, 120 + j * 20, line, 12, fg, mono=True))
    g.append(rs(984, 106, 44, 22, 6, BG2, LINE))
    g.append(ts(1006, 121, "⧉", 11, FG1, anchor="middle"))
    g.append(ring(984, 106, 44, 22, 6))
    ty = 244
    g.append(rs(560, ty, 480, 118, 6, "none", LINE))
    g.append(rs(560, ty, 480, 28, 0, BG2))
    for k, htxt in enumerate(["项目", "预算", "备注"]):
        g.append(ts(572 + k * 160, ty + 19, htxt, 12, FG1, 500))
    for j, (a, b, c) in enumerate([("机票", "¥1,800", "昆明往返"), ("酒店", "¥1,500", "翠湖 ×5 晚"),
                                   ("餐饮", "¥900", "人均 ¥30/餐")]):
        y = ty + 28 + j * 30
        g.append(ls(560, y + 0.5, 1040, y + 0.5, LINE, op=0.6))
        g.append(ts(572, y + 20, a, 12, FG0))
        g.append(ts(732, y + 20, b, 12, FG0, mono=True))
        g.append(ts(892, y + 20, c, 12, FG1))
    g.append(ts(560, 396, "备注", 14, FG0, 600))
    g.append(ts(560, 420, "儿童票按半价计;如遇暴雨,第 3 天", 13, FG0))
    g.append(ts(560, 441, "自动切换为室内方案。", 13, FG0))
    # card:首标题 + 首段 2 行摘录
    g += card()
    g.append(ts(38, 540, "云南六日行程方案", 15, FG0, 600))
    g.append(ts(38, 568, "以下方案按预算 ¥5000 生成,含", 13, FG1))
    g.append(ts(38, 590, "budget.json 与原始需求文档。", 13, FG1))
    g.append(ls(38, 640, 350, 640, LINE))
    g.append(ts(38, 660, "Markdown · 1.2 KB · 3 分钟前", 11, FG2))
    # 状态变体:空态 / 安全剥壳
    vb, (o1, o2) = vboxes(["EMPTY · 空态", "安全剥壳 · 不留空框"])
    g += vb
    g.append(empty_state(o1[0] + 150, 584, "¶", "文档为空", "写入内容"))
    bad1 = "<script>alert(1)</script>"
    w1 = tw(bad1, 11, True) + 16
    g.append(rs(o2[0], o2[1] + 4, w1, 22, 11, BG3))
    g.append(ts(o2[0] + 8, o2[1] + 19, bad1, 11, FG2, mono=True))
    _, sx = spans(o2[0] + w1 + 10, o2[1] + 19, [("→ ", FG2), ("整块不渲染", FG1)], size=11, mono=False)
    g.append(sx)
    bad2 = "[点我](javascript:alert(1))"
    w2 = tw(bad2, 11, True) + 16
    g.append(rs(o2[0], o2[1] + 38, w2, 22, 11, BG3))
    g.append(ts(o2[0] + 8, o2[1] + 53, bad2, 11, FG2, mono=True))
    _, sx2 = spans(o2[0] + w2 + 10, o2[1] + 53, [("→ ", FG2), ("整块不渲染", FG1)], size=11, mono=False)
    g.append(sx2)
    g.append(ts(o2[0], o2[1] + 88, "被剥内容整块移除,页面不留残迹", 11, FG2))
    return svg_doc(g)


# ═══ 3.13 W-bubble ════════════════════════════════════════════════════════
def w_bubble():
    g = page("W-bubble · 聊天气泡(v2)", "四区固定 · log 唯一滚动区 · 壳高度算法归宿主")
    tab_head(g, "plan.md · 段旁批注")
    # 文档上下文(锚点行 live 浅底)
    g.append(ts(40, 112, "第二天:石林一日游,晚返市区。", 12, FG2, op=0.5))
    g.append(rs(32, 124, 340, 22, 4, LIVE, op=0.12))
    g.append(ts(40, 140, "第三天:上午故宫,下午颐和园,预算 ¥720。", 12, FG1))
    # 浮层卡(--shadow-3,圆角 12)+ 小箭头指向锚点
    g.append(ps("M336 160 L348 160 L342 148 Z", BG1, LINE))
    g += shadow(280, 160, 660, 300, 12, 3)
    g.append(rs(280, 160, 660, 300, 12, BG1, LINE))
    # ① header:批注 · 锚点 + L7 徽标 + ✕
    g.append(ts(300, 186, "批注 · lab.travel / plan.md", 12, FG1, 500))
    p, _ = pill(560, 172, "L7", "neutral", h=18, mono=True)
    g.append(p)
    g.append(cs(916, 182, 11, BG2))  # ✕ hover
    g.append(ts(916, 186, "✕", 11, FG1, anchor="middle"))
    # ② quote:左 3px live 条 + 摘录 2 行截断
    g.append(rs(300, 198, 3, 34, 0, LIVE))
    g.append(ts(312, 214, "第三天行程:上午故宫,下午颐和园,", 12, FG1))
    g.append(ts(312, 232, "晚餐四季民福,预算 ¥720。", 12, FG1))
    # ③ log(唯一滚动区;虚框标注滚动域)
    g.append(rs(300, 244, 624, 158, 6, "none", stroke=LINE, dash="3 3"))
    g.append(ts(306, 240, "log(唯一滚动区)", 10, FG2))
    # 消息 1(用户)
    g.append(cs(310, 264, 10, "#33405a"))
    g.append(ts(310, 268, "沈", 10, WHITE, 500, anchor="middle"))
    g.append(ts(328, 268, "沈航", 12, FG0, 500))
    g.append(ts(328 + tw("沈航", 12) + 8, 268, "2 小时前", 11, FG2))
    g.append(ts(328, 290, "这段预算偏高,能压到 ¥600 以内吗?", 13, FG0))
    # 未读分隔线(首次打开插在游标处)
    g.append(ls(312, 306, 616, 306, LINE))
    g.append(ts(464, 310, "以下是新消息", 10, FG2, anchor="middle"))
    # 消息 2(Agent)
    g.append(cs(310, 330, 10, LIVE))
    g.append(ts(310, 334, "✦", 10, WHITE, anchor="middle"))
    g.append(ts(328, 334, "Agent", 12, FG0, 500))
    g.append(ts(328 + tw("Agent", 12) + 8, 334, "1 小时前", 11, FG2))
    g.append(ts(328, 356, "可以。故宫改景山(省 ¥60),合计 ¥596。", 13, FG0))
    # 长消息折叠(line-clamp 6 + 展开钮)
    g.append(cs(310, 378, 10, LIVE))
    g.append(ts(310, 382, "✦", 10, WHITE, anchor="middle"))
    g.append(ts(328, 382, "Agent", 12, FG0, 500))
    g.append(ts(328, 398, "详细方案:第一天高铁早班 G401(省 ¥120),石林改半日…", 12, FG0))
    g.append(ts(900, 382, "展开", 11, LIVE, 500))
    # 「↓ 新消息」pill(非底部新消息提示,不硬拽)
    pb, _ = pill(800, 366, "↓ 新消息", "live", h=22)
    g.append(pb)
    # ④ composer 钉底:autosize textarea(图示 2 行)+ 发送钮
    g.append(rs(300, 412, 472, 40, 8, BG0, LINE))
    g.append(ts(312, 430, "好的,按方案 B 更新;", 12, FG1))
    g.append(ts(312, 446, "再给我一版高铁时刻", 12, FG1))
    g.append(rs(300, 412, 472, 40, 8, "none", stroke=LIVE, sw=1.5))
    b, _ = button(784, 412, "发送", "primary", h=40, size=13)
    g.append(b)
    # 壳几何注(宿主侧)
    g.append(ts(280, 478, "宿主壳:maxHeight = clamp(200px, 45vh, 可用−16);贴底锚点翻转向上;滚动/resize 重算;120ms ease-out", 10, FG2))
    # card(收起态):22px 圆标 + hover 预览条
    g += card()
    g.append(ts(38, 536, "第三天:上午故宫,下午颐和园,预算 ¥720。", 12, FG2))
    g.append(cs(342, 532, 11, LIVE))
    g.append(ts(342, 536, "2", 11, WHITE, 600, anchor="middle"))
    g += shadow(38, 556, 312, 44, 8, 2)
    g.append(rs(38, 556, 312, 44, 8, BG1, LINE))
    g.append(ts(50, 574, "Agent:改走景山方案,合计 ¥596", 12, FG0))
    g.append(ts(50, 592, "3 条 · 2 条未读", 11, FG2))
    # 状态变体:无未读 / 发送失败
    vb, (o1, o2) = vboxes(["无未读 · 线稿 40%", "发送失败 · 行内红条"])
    g += vb
    g.append(ts(o1[0], o1[1] + 14, "第四天:滇池环线骑行。", 12, FG2))
    g.append(f'<g opacity="0.4">{cs(o1[0] + o1[2] - 12, o1[1] + 10, 11, "none", stroke=FG2, sw=1.5)}'
             f'{ts(o1[0] + o1[2] - 12, o1[1] + 14, "💬", 11, FG2, anchor="middle")}</g>')
    g.append(ts(o1[0], o1[1] + 44, "hover 出预览条", 11, FG2))
    g.append(cs(o2[0] + 10, o2[1] + 12, 10, "#33405a"))
    g.append(ts(o2[0] + 10, o2[1] + 16, "沈", 10, WHITE, 500, anchor="middle"))
    g.append(ts(o2[0] + 28, o2[1] + 16, "改成 ¥580 试试", 12, FG1, op=0.6))
    g.append(rs(o2[0], o2[1] + 32, o2[2], 30, 6, DANGER, op=0.12))
    g.append(ts(o2[0] + 10, o2[1] + 51, "⚠", 11, DANGER))
    g.append(ts(o2[0] + 26, o2[1] + 51, "发送失败,检查网络", 11, DANGER))
    g.append(ts(o2[0] + o2[2] - 10, o2[1] + 51, "重试", 11, LIVE, 500, anchor="end"))
    return svg_doc(g)


# ═══ main ═════════════════════════════════════════════════════════════════
PAGES = {
    "w-text": w_text, "w-json": w_json, "w-table": w_table, "w-kv": w_kv,
    "w-form": w_form, "w-list": w_list, "w-tree": w_tree, "w-date": w_date,
    "w-chart": w_chart, "w-log": w_log, "w-diff": w_diff, "w-md": w_md,
    "w-bubble": w_bubble,
}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in PAGES.items():
        svg = fn()
        ET.fromstring(svg)  # 写出前校验 XML well-formed
        path = OUT_DIR / f"{name}.svg"
        path.write_text(svg + "\n", encoding="utf-8")
        print(f"✓ {path.relative_to(OUT_DIR.parents[2])} ({len(svg)} B)")
    print(f"done: {len(PAGES)} 张 → {OUT_DIR}")


if __name__ == "__main__":
    main()
