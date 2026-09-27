"""cell buffer 测试(docs/TUI-DOC.md §3/§9):CJK 双宽收口、截断、ANSI 输出。

渲染结果 = 纯字符串比对(比 GUI 截图走查容易得多,§9)。ANSI 快照比对:
精确 SGR 序列逐字断言,防样式面漂移。
"""

from __future__ import annotations

from agent_os.host.tui.tui.cells import (
    CellBuffer,
    Region,
    Style,
    char_width,
    text_width,
    wrap_text,
)

BOLD_RED = Style(1, None, frozenset({"bold"}))


def test_char_width_cjk_double():
    assert char_width("a") == 1
    assert char_width("信") == 2  # W
    assert char_width("。") == 2  # F
    assert char_width("▸") == 1   # N(符号不双宽)
    assert char_width("\x00") == 0
    assert text_width("信ab") == 4


def test_put_cjk_occupies_two_cells():
    buf = CellBuffer(6, 2)
    assert buf.put(1, 0, "信") == 2
    snap = buf.grid_snapshot()["rows"][0]
    assert snap[1]["ch"] == "信" and not snap[1]["cont"]
    assert snap[2]["cont"]  # 第二格 = 延续占位
    assert buf.line_text(0) == " 信"


def test_cjk_clipped_at_right_edge():
    """宽字符截断:右缘放不下整字 → 不写半格。"""
    buf = CellBuffer(3, 1)
    assert buf.put(2, 0, "信") == 0  # 只剩 1 格,宽字符不画
    assert buf.line_text(0) == ""


def test_write_truncates_without_half_cells():
    buf = CellBuffer(10, 1)
    used = buf.write(0, 0, "ab信件cd", max_width=5)
    assert used == 4  # a b 信 = 1+1+2;下一个是宽字符,放不下就截
    assert buf.line_text(0) == "ab信"


def test_write_cjk_mixed_line_text():
    buf = CellBuffer(20, 3)
    buf.write(0, 1, "信箱 index ▸")
    assert buf.line_text(1) == "信箱 index ▸"


def test_overwrite_wide_char_clears_neighbor():
    """覆盖写入双宽字符任一格,邻格清掉(防半格残影)。"""
    buf = CellBuffer(6, 1)
    buf.write(0, 0, "信件")
    buf.put(0, 0, "a")
    assert buf.line_text(0) == "a 件" or buf.line_text(0) == "a件"
    # 第一格被 ASCII 覆盖后,原"信"的延续格必须不再占位
    snap = buf.grid_snapshot()["rows"][0]
    assert not snap[1]["cont"]


def test_ansi_snapshot_exact():
    """ANSI 输出快照:样式段 SGR 逐字比对;行间 \r\n(ONLCR 不可依赖)。"""
    buf = CellBuffer(8, 2)
    buf.write(0, 0, "hi", BOLD_RED)
    buf.write(4, 0, "信", Style(2))
    expected = (
        "\x1b[1;38;5;1mhi\x1b[0m  \x1b[38;5;2m信\x1b[0m  \x1b[0m\r\n"
        "\x1b[0m        \x1b[0m"
    )
    assert buf.to_ansi() == expected


def test_ansi_sequence_never_splits_wide_char():
    """ANSI 序列不跨入格子:双宽字符的样式只随首格,延续格零输出。"""
    buf = CellBuffer(4, 1)
    buf.write(0, 0, "信", Style(3))
    out = buf.to_ansi()
    assert out == "\x1b[38;5;3m信\x1b[0m  \x1b[0m"


def test_fill_and_reverse_region():
    buf = CellBuffer(6, 2)
    buf.write(0, 0, "ab")
    buf.fill(0, 0, 6, 1, Style(None, 4))
    assert buf.style_at(0, 0).bg == 4
    buf.reverse_region(0, 0, 6, 1)
    assert "reverse" in buf.style_at(0, 0).attrs
    buf.reverse_region(0, 0, 6, 1)
    assert "reverse" not in buf.style_at(0, 0).attrs  # 反色可逆(脉冲灭期)


def test_wrap_text_cjk():
    assert wrap_text("读信写批注", 4) == ["读信", "写批", "注"]
    assert wrap_text("ab cd", 4) == ["ab c", "d"]
    assert wrap_text("", 4) == [""]


def test_plain_text_and_region():
    buf = CellBuffer(10, 3)
    r = Region(2, 1, 6, 1)
    buf.write(r.x, r.y, "ok", max_width=r.w)
    assert buf.line_text(1) == "  ok"
    assert buf.plain_text().splitlines()[1] == "  ok"


def test_to_ansi_uses_crlf_between_rows():
    """行间必须 \r\n:raw 模式 OPOST 已关(ONLCR 不可依赖),裸 \n 换行不回车
    → 阶梯漂移(实机 bug:只见框架不见内容)。此断言钉死这条终端语义。"""
    buf = CellBuffer(8, 3)
    buf.write(0, 0, "ab")
    buf.write(0, 1, "cd")
    out = buf.to_ansi()
    assert out.count("\r\n") == 2  # 3 行 2 个分隔
    assert "\n" not in out.replace("\r\n", "")  # 不允许裸 LF
