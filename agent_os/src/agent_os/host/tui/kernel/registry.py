"""WidgetRegistry(docs/TUI-DOC.md §3;对译 godot/kernel/widget_registry.gd)。

注册即校验,不合规拒注册(返回 False + push_error;注册面是代码评审面)。
无 def 的 kind 拒绝创建(显式 None,与"拒绝渲染"对齐)。

有意扩展(§5.1):``required_intents`` 参数——注册时对 def.get_intents() 做
契约校验,声明的 intent 必须落在活动 keymap 契约清单内(与主题/键位
"缺项拒注册"同机制)。缺省 None = 不校验(godot 原型语义)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_os.host.tui.kernel.widget import Widget, push_error

if TYPE_CHECKING:
    from agent_os.host.tui.kernel.widget_def import WidgetDef

KNOWN_SURFACES: tuple[str, ...] = ("card", "tab")


class WidgetRegistry:
    def __init__(self, required_intents: list[str] | tuple[str, ...] | None = None) -> None:
        self._defs: dict[str, WidgetDef] = {}
        self._required_intents: frozenset[str] = (
            frozenset(required_intents) if required_intents is not None else frozenset()
        )

    def register(self, widget_def: WidgetDef) -> bool:
        if widget_def is None or not widget_def.get_kind():
            push_error("widget def 缺 kind,拒注册")
            return False
        kind = widget_def.get_kind()
        if widget_def.get_v() < 1:
            push_error(f"{kind}: v 必须 >= 1")
            return False
        action_ids: set[str] = set()
        for a in widget_def.get_actions():
            if not isinstance(a, dict) or not str(a.get("id") or ""):
                push_error(f"{kind}: action 缺 id,拒注册")
                return False
            if a["id"] in action_ids:
                push_error(f"{kind}: action id 重复 {a['id']}")
                return False
            action_ids.add(a["id"])
            if not self._surfaces_ok(kind, a.get("surfaces", KNOWN_SURFACES)):
                return False
        for e in widget_def.get_events():
            if not isinstance(e, str) or not e:
                push_error(f"{kind}: events 含空项,拒注册")
                return False
        if not self._surfaces_ok(kind, widget_def.get_surfaces()):
            return False
        if not self._intents_ok(kind, widget_def.get_intents()):
            return False
        self._defs[kind] = widget_def
        return True

    def _surfaces_ok(self, kind: str, surfaces: list[str] | tuple[str, ...]) -> bool:
        for s in surfaces:
            if s not in KNOWN_SURFACES:
                push_error(f"{kind}: surface {s} 越界(只允许 card/tab)")
                return False
        return True

    def _intents_ok(self, kind: str, intents: list[str] | tuple[str, ...]) -> bool:
        """intent 契约(docs/TUI-DOC.md §5.1):构造时给了契约清单才校验。"""
        if not self._required_intents:
            return True
        for it in intents:
            if it not in self._required_intents:
                push_error(f"{kind}: intent {it} 不在活动 keymap 契约清单内,拒注册")
                return False
        return True

    def get_def(self, kind: str) -> WidgetDef | None:
        return self._defs.get(kind)

    def create(self, kind: str, state: dict[str, Any]) -> Widget | None:
        """无 def 的 kind 拒绝创建(显式 None,与"拒绝渲染"对齐)。"""
        widget_def = self.get_def(kind)
        if widget_def is None:
            push_error(f"未注册的 widget kind: {kind}")
            return None
        return widget_def.create_instance(state)

    def kinds(self) -> list[str]:
        return list(self._defs.keys())
