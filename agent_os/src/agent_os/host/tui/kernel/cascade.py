"""ContextCascade(docs/TUI-DOC.md §3;对译 godot/kernel/context_cascade.gd)。

Context Cascade(docs/APP-MODEL.md §16):动作触发时从触发 widget 沿 ownership
树向上逐级收集 fragment,组成级联信封。纪律:每级只贡献自己的 fragment;
级联单向向上(祖先链),不横向打听——上下文边界 = 树边界;父可经
child_context 改写子的 fragment(信息管控,COMPOUND §7 通道 2)。
scope 规则:root = "shell";root 的直接子 = "app";中间层 = "section";
触发者 = "widget"。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_os.host.tui.kernel.widget import Widget

if TYPE_CHECKING:
    from agent_os.host.tui.kernel.tree import WidgetTree


class ContextCascade:
    def __init__(self, tree: WidgetTree) -> None:
        self._tree = tree

    def build(self, trigger: Widget | None) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if trigger is None:
            return entries
        entries.append(self._entry("widget", trigger))
        o = trigger.owner
        while o is not None:
            entries.append(self._entry(self._scope_of(o), o))
            o = o.owner
        return entries

    def _scope_of(self, w: Widget) -> str:
        if w is self._tree.root:
            return "shell"
        if w.owner is self._tree.root:
            return "app"
        return "section"

    @staticmethod
    def _entry(scope: str, w: Widget) -> dict[str, Any]:
        frag = w.context_fragment()
        if w.owner is not None:
            frag = w.owner.child_context(w, frag)  # 信息管控:父改写子对外提供的信息
        return {
            "scope": scope,
            "path": w.path,
            "data": frag,
        }
