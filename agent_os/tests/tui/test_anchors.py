"""锚点互逆 + span 解析测试(T2 用户裁决:注释精度到字符)。

契约:``quote_at(text, anchor_from_range(text, s, e)) == text[s:e]``(含跨行、
末字符是换行、到文末、CJK);span 解析三策略(精确/±3 行模糊/outdated)。
"""

from __future__ import annotations

import pytest

from agent_os.host.tui.apps.doc_editor.model import (
    anchor_from_range,
    range_from_anchor,
    resolve_spans,
)
from agent_os.skills.reanchor import quote_at

TEXT = "# 标题\n\n第一段正文甲乙丙丁。\n\n- 列表甲\n- 列表乙\n\n末行无换行收尾"


def test_roundtrip_single_line():
    s, e = TEXT.index("正文甲乙"), TEXT.index("正文甲乙") + 4
    a = anchor_from_range(TEXT, s, e)
    # 同行锚点:quote_at 对单元素 seg 双重切片(实现即语义)→ ec 编码长度
    assert a == "doc.md#L3:C4-L3:C4"  # 第 4 列起,长 4(quote_at 互逆约定)
    assert quote_at(TEXT, a) == TEXT[s:e]
    assert range_from_anchor(TEXT, a) == (s, e)


def test_roundtrip_cross_line():
    s = TEXT.index("丙")  # 第一段内
    e = TEXT.index("列表乙") + 3
    a = anchor_from_range(TEXT, s, e)
    assert quote_at(TEXT, a) == TEXT[s:e]
    assert range_from_anchor(TEXT, a) == (s, e)


def test_roundtrip_selection_ending_with_newline():
    """末字符是换行:换行是行的终止符,锚点末点推到下一行 C0(join 语义)。"""
    s, e = 0, TEXT.index("\n") + 1  # "# 标题\n"
    a = anchor_from_range(TEXT, s, e)
    assert a.endswith("L2:C0")
    assert quote_at(TEXT, a) == TEXT[s:e]
    assert range_from_anchor(TEXT, a) == (s, e)


def test_roundtrip_to_eof():
    s = TEXT.index("末行")
    e = len(TEXT)
    a = anchor_from_range(TEXT, s, e)
    assert quote_at(TEXT, a) == TEXT[s:e]
    assert range_from_anchor(TEXT, a) == (s, e)


def test_roundtrip_first_char_and_single_char():
    assert quote_at(TEXT, anchor_from_range(TEXT, 0, 1)) == TEXT[0:1]
    assert range_from_anchor(TEXT, anchor_from_range(TEXT, 0, 1)) == (0, 1)


def test_anchor_from_range_rejects_empty_or_oob():
    with pytest.raises(ValueError):
        anchor_from_range(TEXT, 3, 3)  # 空选区
    with pytest.raises(ValueError):
        anchor_from_range(TEXT, 0, len(TEXT) + 1)


def test_range_from_anchor_line_level():
    """行级锚点(列缺省)= 整行(不含换行,与 quote_at join 语义一致)。"""
    rng = range_from_anchor(TEXT, "doc.md#L5-L5")
    assert rng is not None
    assert TEXT[rng[0]:rng[1]] == "- 列表甲"


def test_range_from_anchor_rejects_bad():
    assert range_from_anchor(TEXT, "not-an-anchor") is None
    assert range_from_anchor(TEXT, "doc.md#L99-L100") is None


def test_resolve_spans_exact():
    s, e = TEXT.index("正文"), TEXT.index("正文") + 2
    rec = {"anchor": anchor_from_range(TEXT, s, e), "quote": TEXT[s:e],
           "content": "注", "status": "pending"}
    spans = resolve_spans(TEXT, [rec])
    assert spans[0].resolved == "exact"
    assert (spans[0].start, spans[0].end) == (s, e)
    assert spans[0].status == "pending"


def test_resolve_spans_fuzzy_window():
    """精确失败 → ±3 行模糊(全文匹配 quote,命中换新锚点)。"""
    quote = "列表乙"
    # 旧锚点指向第 4 行(文档改版前移了,但在 ±3 行窗内),quote 还在 → 模糊命中
    rec = {"anchor": "doc.md#L4:C1-L4:C3", "quote": quote, "content": "注",
           "status": "pending"}
    spans = resolve_spans(TEXT, [rec])
    assert spans[0].resolved == "fuzzy"
    s = TEXT.index(quote)
    assert (spans[0].start, spans[0].end) == (s, s + len(quote))


def test_resolve_spans_outdated():
    """找不到 → outdated:不画 span(None),计数保留(记录在列表里)。"""
    rec = {"anchor": "doc.md#L1:C1-L1:C2", "quote": "不存在的话",
           "content": "注", "status": "pending"}
    spans = resolve_spans(TEXT, [rec])
    assert spans[0].resolved == "outdated"
    assert spans[0].start is None and spans[0].end is None
    # 已 outdated 的原样保留,不再锚
    rec2 = dict(rec, status="outdated")
    spans2 = resolve_spans(TEXT, [rec2])
    assert spans2[0].resolved == "outdated" and spans2[0].anchor == rec["anchor"]
