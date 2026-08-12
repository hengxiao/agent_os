"""批注重锚(设计 v2 §7.1;P1 纯函数面):在新版本文本上重定位旧批注锚点。

策略(宁多勿错——模糊必须全文匹配 quote):

1. **精确匹配**:按行号+列号截取,文本与 quote 一致 → 锚点原位不变
   (行级锚点 quote = 整段文本;零宽点锚点 quote 为空,只按行存在判);
2. **模糊匹配**:行号 ±3 行窗口内搜 quote 全文(可跨行)→ 命中则更新锚点
   行列(保持原锚点的行级/列级形态);空 quote 不做模糊(空串到处匹配,
   没意义);
3. **找不到** → status 标 outdated(锚点原样保留,供人工核对)。

列语义与前端 doc-editor.js ``anchorColOffsetsOf`` 对齐:1-based 闭区间
(C4-C20 = 该行第 4–20 个字符,python 切片 ``[3:20]``)。
"""

from __future__ import annotations

import re
from typing import Any

#: 锚点格式(与 app.py _ANCHOR_RE / doc-editor.js parseAnchor 同形):
#: doc.md#L<start>[:C<col>]-L<end>[:C<col>](1-based,列可选)
ANCHOR_RE = re.compile(r"^doc\.md#L(\d+)(?::C(\d+))?-L(\d+)(?::C(\d+))?$")

#: 模糊匹配的行号偏移窗(设计 §7.1:±3 行)
FUZZY_WINDOW = 3


def parse_anchor(anchor: str) -> tuple[int, int | None, int, int | None] | None:
    """``doc.md#L3:C4-L5:C2`` → ``(3, 4, 5, 2)``(列缺省 None);非法 → None。"""
    m = ANCHOR_RE.match(str(anchor or ""))
    if not m:
        return None
    return (
        int(m.group(1)),
        int(m.group(2)) if m.group(2) is not None else None,
        int(m.group(3)),
        int(m.group(4)) if m.group(4) is not None else None,
    )


def make_anchor(sl: int, sc: int | None, el: int, ec: int | None) -> str:
    """``(3, 4, 5, 2)`` → ``doc.md#L3:C4-L5:C2``(列 None = 行级)。"""
    s = f"L{sl}" + (f":C{sc}" if sc is not None else "")
    e = f"L{el}" + (f":C{ec}" if ec is not None else "")
    return f"doc.md#{s}-{e}"


def quote_at(text: str, anchor: str) -> str | None:
    """按锚点从全文截取 quote(1-based 闭区间列;越界/非法锚点 → None)。"""
    parsed = parse_anchor(anchor)
    if not parsed:
        return None
    sl, sc, el, ec = parsed
    lines = text.split("\n")
    if sl < 1 or el < sl or el > len(lines):
        return None
    seg = list(lines[sl - 1 : el])
    if sc is not None:
        if sc - 1 > len(seg[0]):
            return None
        seg[0] = seg[0][sc - 1 :]
    if ec is not None:
        seg[-1] = seg[-1][:ec]
    return "\n".join(seg)


def reanchor_one(new_text: str, anchor: str, quote: str) -> tuple[str, bool]:
    """单条重锚:``(新锚点, ok)``;ok=False = 找不到(调用方标 outdated,
    锚点原样带回)。"""
    parsed = parse_anchor(anchor)
    if not parsed:
        return str(anchor or ""), False
    sl, sc, el, ec = parsed
    lines = new_text.split("\n")
    quote = str(quote or "")
    # 零宽点锚点(quote 空):没有文本可对,只按行存在判(宁多勿错:不做模糊)
    if not quote:
        return (str(anchor), True) if el <= len(lines) else (str(anchor), False)
    # ① 精确:原位截取 == quote
    if quote_at(new_text, str(anchor)) == quote:
        return str(anchor), True
    # ② 模糊:±3 行窗口内 quote 全文匹配(可跨行)
    lo = max(1, sl - FUZZY_WINDOW)
    hi = min(len(lines), el + FUZZY_WINDOW)
    block = "\n".join(lines[lo - 1 : hi])
    idx = block.find(quote)
    if idx < 0:
        return str(anchor), False
    # 命中位置 → 行列(按 \n 折算);保持原锚点形态(行级 → 行级,列级 → 列级)
    pre = block[:idx]
    start_line = lo + pre.count("\n")
    start_col = idx - (pre.rfind("\n") + 1) + 1
    end_line = start_line + quote.count("\n")
    if "\n" in quote:
        end_col = len(quote.rsplit("\n", 1)[1])  # 末行字符数 = 1-based 闭区间末列
    else:
        end_col = start_col + len(quote) - 1
    if sc is None and ec is None:
        return make_anchor(start_line, None, end_line, None), True
    return make_anchor(start_line, start_col, end_line, end_col), True


def reanchor_annotations(new_text: str, annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量重锚(保序;不 mutate 入参)。

    - 已 outdated 的不再锚(锚已失效,留在原值供人工核对);
    - **pending** 找不到 → 标 outdated(宁多勿错,锚点原样保留);
    - 非 pending(applied/ignored 终态)找不到 → **状态保留**,锚点原样
      (applied 的原文通常就是被应用改掉了,抹成 outdated 会废掉状态面)。
    """
    out = []
    for ann in annotations:
        rec = dict(ann)
        if rec.get("status") == "outdated":
            out.append(rec)
            continue
        new_anchor, ok = reanchor_one(new_text, rec.get("anchor", ""), rec.get("quote", ""))
        if ok:
            rec["anchor"] = new_anchor
        elif rec.get("status", "pending") == "pending":
            rec["status"] = "outdated"
        out.append(rec)
    return out
