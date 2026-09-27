"""版本条 reel / 版本树覆盖层的纯布局面(docs/TUI-DOC.md §4 映射表;
T3 里程碑)。纯函数,无渲染依赖——cell 绘制在 app.py,布局可单测。

- ``reel_items``:版本条条目 = ["working" 对照锚] + 版本号(左新右旧);
  工作稿不在版本条上(它就是"现在"),但条上有位(§4 裁决)。
- ``build_tree_rows``:版本树 ASCII 分支图(parent 链,同层排开,当前 base
  标记 ◉)。列分配:最新子承父列(主干直行),其余兄弟向右开新列。
"""

from __future__ import annotations

from typing import Any

#: 版本条上"工作稿"对照锚的条目 id(非版本号;渲染层翻成人话)
WORKING = "working"


def reel_items(tree: dict[str, Any]) -> list[str]:
    """版本条条目(左新右旧;左端 = 工作稿对照锚)。"""
    vids = [str(v.get("version") or "") for v in tree.get("versions", [])]
    return [WORKING] + [v for v in vids if v]


def reel_label(item: str) -> str:
    return "工作稿" if item == WORKING else item


# ---------------------------------------------------------------------------
# 版本树(ASCII 分支图)
# ---------------------------------------------------------------------------

class TreeRow:
    """一行树节点:version(空 = 纯连线行)、col、该行各列的竖线集合、
    分支横线(from_col → col)。"""
    __slots__ = ("branch_from", "col", "is_base", "source", "version", "verticals")

    def __init__(self, version: str, col: int, verticals: frozenset[int],
                 branch_from: int | None, source: str, is_base: bool) -> None:
        self.version = version
        self.col = col
        self.verticals = verticals      # 该行要画 │ 的列
        self.branch_from = branch_from  # 非 None = 从该列横拉 ┌─┘ 到本节点列
        self.source = source
        self.is_base = is_base


def build_tree_rows(tree: dict[str, Any]) -> list[TreeRow]:
    """版本树 → 行列表(新→旧)。无 parent 的面(offline 旧档)= 线性排。

    布局:每个版本一行;列 = 分支道;最新子承父列(主干),其余兄弟右开新列;
    父子不同列时该行画横线连接(branch_from = 父列)。"""
    versions = [v for v in tree.get("versions", []) if isinstance(v, dict)]
    if not versions:
        return []
    base = str(tree.get("base") or "")
    by_id = {str(v.get("version")): v for v in versions}
    order = [str(v.get("version")) for v in versions]  # 新→旧
    # children: parent → [children](新→旧,首 = 最新子 = 承父列)
    children: dict[str, list[str]] = {}
    for v in versions:
        p = str(v.get("parent") or "")
        if p in by_id:
            children.setdefault(p, []).append(str(v.get("version")))
    # 列分配(旧→新处理,父先得有列)。主干 = base 链(沿 parent 从 base
    # 回根)上的子承父列;链外兄弟右开新列。
    base_chain: set[str] = set()
    cur = base
    while cur and cur in by_id:
        base_chain.add(cur)
        p = by_id[cur].get("parent")
        cur = str(p) if p else ""
    col_of: dict[str, int] = {}
    for vid in reversed(order):
        p = by_id[vid].get("parent")
        parent = str(p) if p else ""
        if parent and parent in col_of:
            siblings = children.get(parent, [])
            trunk_child = next((c for c in siblings if c in base_chain),
                               siblings[0] if siblings else None)
            if vid == trunk_child:
                col_of[vid] = col_of[parent]  # 主干子承父列
            else:
                col_of[vid] = max(col_of.values()) + 1  # 兄弟右开新列
        else:
            col_of[vid] = 0 if not col_of else max(col_of.values()) + 1
    # 行生成:每节点一行;竖线 = 该列有更早节点(上方)且本行之下还有同列节点
    row_of = {vid: i for i, vid in enumerate(order)}
    rows: list[TreeRow] = []
    for vid in order:
        col = col_of[vid]
        parent = str(by_id[vid].get("parent") or "")
        verticals: set[int] = set()
        cols_present = set(col_of.values())
        for c in cols_present:
            members = [v for v, cc in col_of.items() if cc == c]
            above = any(row_of[m] < row_of[vid] for m in members)
            below = any(row_of[m] > row_of[vid] for m in members)
            if above and below:
                verticals.add(c)
        verticals.discard(col)
        branch_from = None
        if parent and parent in col_of and col_of[parent] != col:
            branch_from = col_of[parent]
            verticals.discard(branch_from)
        rows.append(TreeRow(vid, col, frozenset(verticals), branch_from,
                            str(by_id[vid].get("source") or ""), vid == base))
    return rows


def tree_rows_text(rows: list[TreeRow], col_w: int = 4) -> list[tuple[str, str]]:
    """TreeRow → (行文本, 该行版本号|"")。渲染层只负责上色/选中,不重算布局。

    形态:``● v003``(普通节点)/ ``◉ v005``(当前 base)/ ``├─● v004``(分支点)。
    """
    out: list[tuple[str, str]] = []
    for r in rows:
        cells = [" "] * ((max((r.col, *r.verticals, r.branch_from or 0)) + 1) * col_w)
        for c in r.verticals:
            cells[c * col_w] = "│"
        if r.branch_from is not None:
            start = r.branch_from * col_w
            cells[start] = "│"
            for x in range(start + 1, r.col * col_w):
                cells[x] = "─"
        glyph = "◉" if r.is_base else "●"
        label = f"{glyph} {r.version}" + ("(当前)" if r.is_base else "")
        for i, ch in enumerate(label):
            x = r.col * col_w + i
            if x >= len(cells):
                cells.extend([" "] * (x - len(cells) + 1))
            cells[x] = ch
        out.append(("".join(cells).rstrip(), r.version))
    return out
