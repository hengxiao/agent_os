#!/usr/bin/env python3
"""生成 docs/widgets/*.svg 线框示意图(W5 计划用)。

统一线框风格:圆角画布 + 标题条 + 分区块 + 少量强调色。
用法:python3 scripts/gen_widget_svgs.py
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "widgets"

FG = "#4a4a52"
LINE = "#b9bec9"
FILL = "#f6f7f9"
ACCENT = "#3b82f6"
GOOD = "#22a06b"
BAD = "#e5534b"
WARN = "#d9a03f"
MONO = "font-family='monospace'"
SANS = "font-family='sans-serif'"


def box(x, y, w, h, label="", fill="none", stroke=LINE, rx=6, fs=10, color=FG, bold=False, dash=""):
    s = f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='{rx}' fill='{fill}' stroke='{stroke}' stroke-width='1.2' {dash}/>"
    if label:
        fw = "font-weight='bold'" if bold else ""
        s += f"<text x='{x + 8}' y='{y + 16}' font-size='{fs}' fill='{color}' {SANS} {fw}>{label}</text>"
    return s


def line(x1, y1, x2, y2, color=LINE, dash=""):
    return f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{color}' stroke-width='1' {dash}/>"


def text(x, y, t, fs=10, color=FG, mono=False, bold=False):
    fw = "font-weight='bold'" if bold else ""
    return f"<text x='{x}' y='{y}' font-size='{fs}' fill='{color}' {'font-family=\'monospace\'' if mono else SANS} {fw}>{t}</text>"


def dot(x, y, color, r=4):
    return f"<circle cx='{x}' cy='{y}' r='{r}' fill='{color}'/>"


def page(title, body):
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='380' height='240' viewBox='0 0 380 240'>"
        f"<rect x='1' y='1' width='378' height='238' rx='10' fill='{FILL}' stroke='{LINE}'/>"
        f"<rect x='1' y='1' width='378' height='26' rx='10' fill='#eceef2' stroke='{LINE}'/>"
        f"<text x='12' y='18' font-size='11' fill='{FG}' {SANS} font-weight='bold'>{title}</text>"
        f"{body}</svg>"
    )


SVGS = {}

# W-text:编辑区 + 行号槽 + 微标 + dirty 左边条
SVGS["w-text"] = page("W-text 文本编辑器",
    box(10, 36, 4, 168, "", fill=ACCENT, rx=2) + text(18, 34, "dirty 左边条(--live)", 8)
    + box(16, 40, 26, 160, "", fill="#fff")
    + "".join(text(22, 62 + i * 14, str(i + 1), 9, "#8a8f99", mono=True) for i in range(3))
    + box(42, 40, 324, 160, "", fill="#fff") + text(50, 58, "你是晚餐推荐助手…", 10, FG, mono=True)
    + text(50, 76, "按天气与日程回答…", 10, FG, mono=True)
    + text(46, 214, "mono 变体带行号槽;plain 无槽", 8, "#8a8f99")
    + box(250, 208, 116, 18, "12 行 · 340 字", fill="#eef0f4")
    + text(250, 236, "▲ 微标(右下)", 8, "#8a8f99"))

# W-json:W-text + 错误条 + format 钮
SVGS["w-json"] = page("W-json JSON 编辑器",
    box(16, 40, 26, 128, "", fill="#fff")
    + "".join(text(22, 60 + i * 14, str(i + 1), 9, "#8a8f99", mono=True) for i in range(4))
    + box(42, 40, 324, 128, "", fill="#fff")
    + text(50, 58, '{ "query": "…",', 10, FG, mono=True) + text(50, 74, '  "limit": 3,', 10, FG, mono=True)
    + text(50, 90, '  "filters" }', 10, BAD, mono=True)
    + box(300, 46, 60, 18, "format", fill="#eef0f4")
    + box(16, 174, 350, 26, "", fill="#fdecec", stroke=BAD)
    + text(24, 191, "✕ 第 4 行: 缺右括号", 9, BAD)
    + text(16, 222, "合法时:✓ 绿勾;错误条点击跳到错误行", 8, "#8a8f99"))

# W-table:列头 + 行 + 拖柄 + 添加行
SVGS["w-table"] = page("W-table 表格编辑器",
    box(16, 40, 348, 22, "用例 ▾ │ 输入(text*) │ 期望(enum)", fill="#eceef2", bold=True)
    + "".join(box(16, 62 + i * 26, 348, 26, "", fill="#fff") for i in range(3))
    + text(20, 78, "⠿", 10, "#8a8f99") + text(40, 78, "case1 │ {\"city\":\"北京\"} │ ok", 9, FG, mono=True)
    + text(20, 104, "⠿", 10, "#8a8f99") + text(40, 104, "case2 │ {\"city\":\"上海\"} │ ok", 9, FG, mono=True)
    + text(20, 130, "⠿", 10, "#8a8f99") + text(40, 130, "case3 │ … │ fail", 9, FG, mono=True)
    + text(330, 78, "✕", 10, "#8a8f99") + text(330, 104, "✕", 10, "#8a8f99") + text(330, 130, "✕", 10, "#8a8f99")
    + text(16, 152, "拖柄 ⠿ 行 DnD(§15 envelope);hover 显 ✕;Alt+↑/↓ 移行", 8, "#8a8f99")
    + box(16, 162, 348, 26, "+ 添加行(骨架按列型)", fill="#eef0f4")
    + text(16, 210, "列头:名称 + 类型徽标 + 必填 *", 8, "#8a8f99"))

# W-kv
SVGS["w-kv"] = page("W-kv 键值编辑器",
    box(16, 40, 160, 22, "key", fill="#eceef2", bold=True) + box(176, 40, 188, 22, "value", fill="#eceef2", bold=True)
    + box(16, 62, 160, 26, "team", fill="#fff") + box(176, 62, 188, 26, "ops", fill="#fff")
    + box(16, 88, 160, 26, "team", fill="#fff7e6", stroke=WARN) + box(176, 88, 188, 26, "infra", fill="#fff7e6", stroke=WARN)
    + text(20, 128, "重复 key → 黄底警示(不硬拦)", 8, WARN)
    + box(16, 138, 348, 24, "+ 添加", fill="#eef0f4")
    + text(16, 186, "Tab 在 key→value→下一行 流转", 8, "#8a8f99"))

# W-form
SVGS["w-form"] = page("W-form schema 表单",
    box(16, 40, 348, 84, "", fill="#fff")
    + text(24, 58, "query *", 10, FG, bold=True) + dot(78, 54, BAD, 3)
    + box(90, 46, 260, 20, "", fill="#fff") + text(94, 60, "北京", 9, FG, mono=True)
    + text(24, 88, "limit", 10, FG) + box(90, 76, 100, 20, "3", fill="#fff")
    + text(200, 90, "integer ≥ 1", 8, "#8a8f99")
    + text(24, 110, "filters", 10, FG) + box(90, 100, 260, 18, "未填(可选)", fill="#fafbfc")
    + box(16, 132, 348, 34, "", fill="#fff")
    + text(24, 153, "options ▸(嵌套 object 分组)", 10, FG)
    + box(16, 174, 348, 24, "", fill="#fdecec", stroke=BAD)
    + text(24, 190, "query 为必填", 9, BAD)
    + text(16, 222, "label 左/控件右;错误红边 + 行内错误语;reset 回骨架", 8, "#8a8f99"))

# W-list
SVGS["w-list"] = page("W-list 可选列表",
    box(16, 40, 348, 22, "🔍 过滤…", fill="#fff")
    + "".join(box(16, 66 + i * 28, 348, 28, "", fill="#fff") for i in range(3))
    + box(16, 66, 4, 28, "", fill=ACCENT, rx=2)
    + text(28, 84, "weather.query", 10, FG, mono=True, bold=True) + text(200, 84, "v1.2 · prompt", 8, "#8a8f99")
    + text(28, 112, "ops.janitor", 10, FG, mono=True) + text(28, 140, "ops.inspect", 10, FG, mono=True)
    + text(16, 180, "选中行左色条(--live);↑↓ 移动焦点 Enter 激活;Esc 清搜索", 8, "#8a8f99")
    + text(16, 196, "空态:插画位 + 引导文案", 8, "#8a8f99"))

# W-tree
SVGS["w-tree"] = page("W-tree 命名空间树",
    text(20, 56, "▾ system", 10, FG, bold=True)
    + text(34, 76, "▸ file (8)", 10, FG)
    + text(34, 96, "▾ net (2)", 10, FG)
    + box(48, 102, 200, 18, "", fill="#e8f0fe", rx=4)
    + text(52, 115, "http_fetch", 9, ACCENT, mono=True)
    + text(48, 132, "http_request", 9, FG, mono=True)
    + text(20, 152, "▸ ops (4)", 10, FG)
    + text(16, 176, "单层链折叠;命中过滤自动展开祖先链", 8, "#8a8f99")
    + text(16, 192, "当前项浅底(classic --bg-2 / moe --moe-blush)", 8, "#8a8f99"))

# W-date
SVGS["w-date"] = page("W-date 日期控件",
    box(16, 40, 160, 22, "2026-08-01", fill="#fff") + text(140, 55, "▾", 9)
    + box(184, 40, 160, 22, "2026-08-04", fill="#fff")
    + text(16, 82, "今天 / 昨天 / 本周 / 上周", 9, ACCENT)
    + box(16, 92, 240, 130, "", fill="#fff")
    + text(24, 108, "‹ 2026 年 8 月 ›", 10, FG, bold=True)
    + text(24, 126, "一 二 三 四 五 六 日", 8, "#8a8f99")
    + text(178, 144, "1  2  3", 9, FG) + dot(170, 140, WARN, 3)
    + box(22, 150, 100, 18, "", fill="#e8f0fe", rx=3) + text(26, 163, "4  5  6  7  8", 9, ACCENT)
    + text(24, 186, "…区间高亮带…", 8, ACCENT)
    + text(16, 232, "输入/日历双通道;range 倒置自动纠序;←→ 翻月;Esc 收层", 8, "#8a8f99"))

# W-chart
SVGS["w-chart"] = page("W-chart 图表(纯 SVG)",
    "".join(line(40, 60 + i * 26, 340, 60 + i * 26, "#dfe3ea", "stroke-dasharray='3,3'") for i in range(5))
    + line(40, 60, 40, 190) + line(40, 190, 340, 190)
    + "<polyline points='50,160 100,140 150,150 200,110 250,120 300,80' fill='none' stroke='#3b82f6' stroke-width='2'/>"
    + dot(200, 110, ACCENT, 4) + box(206, 92, 70, 20, "v3 · 88 分", fill="#fff")
    + text(44, 204, "v1    v2    v3    v4", 8, "#8a8f99")
    + text(16, 52, "图例 ■score(点击显隐)", 8, FG)
    + box(258, 38, 100, 18, "⇄ 表格视图", fill="#eef0f4")
    + text(16, 224, "空态:'还没有数据';>500 点抽稀;硬规则:表格视图等价数据", 8, "#8a8f99"))

# W-log
SVGS["w-log"] = page("W-log 日志查看器",
    box(16, 40, 348, 20, "🔍 过滤…", fill="#fff")
    + "".join(box(16, 64 + i * 22, 348, 22, "", fill="#0f1420") for i in range(4))
    + box(16, 64, 3, 22, "", fill=ACCENT) + text(24, 79, "pre:tool.call system.file.read", 9, "#c8d0e0", mono=True)
    + box(16, 86, 3, 22, "", fill=GOOD) + text(24, 101, "post:tool.call ✓ 200ms", 9, "#9fe0c0", mono=True)
    + box(16, 108, 3, 22, "", fill=BAD) + text(24, 123, "run failed: ProviderError", 9, "#f0a8a0", mono=True)
    + text(24, 145, "…", 9, "#8a8f99")
    + box(240, 172, 124, 22, "↓ 回到底部", fill="#eef0f4")
    + text(16, 210, "kind 左侧色条(--sig-*);上滚暂停跟随;截断保尾 500 行", 8, "#8a8f99"))

# W-diff
SVGS["w-diff"] = page("W-diff 差异查看器",
    box(16, 40, 172, 20, "旧(split)", fill="#eceef2", bold=True) + box(188, 40, 176, 20, "新", fill="#eceef2", bold=True)
    + box(16, 62, 172, 22, "", fill="#fdecec") + text(22, 77, "- 你是晚餐助手", 9, BAD, mono=True)
    + box(188, 62, 176, 22, "", fill="#e6f6ec") + text(194, 77, "+ 你是晚餐推荐官", 9, GOOD, mono=True)
    + box(16, 84, 172, 22, "", fill="#fdecec") + text(22, 99, "- 多聊几句再答", 9, BAD, mono=True)
    + box(188, 84, 176, 22, "", fill="#e6f6ec") + text(194, 99, "+ 直接给答案", 9, GOOD, mono=True)
    + box(16, 112, 348, 18, "[+6] 展开相同上下文", fill="#eef0f4")
    + box(16, 138, 348, 20, "成员: dinner.planner ●reversible", fill="#fff")
    + text(16, 182, "split/unified 切换;+/- 符号与色双编码", 8, "#8a8f99")
    + text(16, 198, "add=--ok 浅底 / del=--danger 浅底", 8, "#8a8f99"))

# W-md
SVGS["w-md"] = page("W-md Markdown 查看器",
    text(20, 58, "动机与背景", 14, FG, bold=True)
    + line(20, 64, 160, 64, LINE)
    + text(20, 82, "正文段落,白名单渲染…", 10, FG)
    + box(20, 92, 344, 40, "", fill="#f0f2f6")
    + text(28, 108, "def hello():  …  [复制]", 9, FG, mono=True)
    + box(20, 140, 4, 40, "", fill=LINE, rx=2) + text(32, 156, "引用条", 10, "#8a8f99")
    + text(16, 208, "先转义再白名单;javascript: 剥壳;代码块 mono+复制钮", 8, "#8a8f99")
    + text(16, 224, "安全:no innerHTML 原文,无脚本面", 8, "#8a8f99"))

# W-bubble
SVGS["w-bubble"] = page("W-bubble 聊天气泡",
    box(16, 40, 348, 60, "", fill="#fff")
    + text(24, 58, "这是文档的第二段内容…", 10, FG)
    + box(16, 64, 3, 24, "", fill=ACCENT) + text(24, 94, "锚点行高亮", 8, ACCENT)
    + box(120, 104, 244, 96, "", fill="#ffffff", stroke=ACCENT)
    + "<polygon points='110,120 120,128 120,112' fill='#ffffff' stroke='#3b82f6'/>"
    + text(130, 122, "doc.md#L7-L7", 8, "#8a8f99", mono=True)
    + text(130, 140, "你:这段太随意", 9, FG)
    + text(130, 158, "agent:建议改为…", 9, ACCENT)
    + box(128, 170, 228, 20, "输入回复…", fill="#fafbfc")
    + text(130, 214, "✕ 收起为段旁小标记(带未读数)", 8, "#8a8f99")
    + dot(36, 224, ACCENT, 8) + text(30, 228, "2", 8, "#fff"))

# 架构总览
SVGS["_arch"] = page("Widget 基座:渲染与逻辑分离",
    box(16, 40, 160, 60, "w-text.js(逻辑)", fill="#e8f0fe", bold=True)
    + text(24, 70, "状态机 / 行为 / emit", 9, FG)
    + box(204, 40, 160, 60, "w-text.render.js", fill="#e6f6ec", bold=True)
    + text(212, 70, "render(state)→html", 9, FG)
    + text(24, 82, "不碰 DOM / 不拼 HTML", 8, "#8a8f99")
    + text(212, 82, "无副作用 / 不写 state", 8, "#8a8f99")
    + line(176, 70, 204, 70, ACCENT) + text(180, 66, "state", 8, ACCENT)
    + box(16, 116, 348, 40, "widget.js 工厂(state 可序列化/事件上行/寻址)", fill="#fff")
    + box(16, 164, 348, 40, "css/widgets.css(全 token)+ 六主题 copy", fill="#fff")
    + text(16, 224, "宿主只给容器 + 订阅事件;出海只有 events → app action", 8, "#8a8f99"))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, svg in SVGS.items():
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
    print(f"wrote {len(SVGS)} svgs to {OUT}")


if __name__ == "__main__":
    main()
