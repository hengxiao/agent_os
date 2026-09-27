"""CompoundWidget(docs/TUI-DOC.md §3;对译 godot/kernel/compound_widget.gd)。

复合 widget 基座(docs/COMPOUND-WIDGET.md):拥有子 widget,定义组合、事件
闸门、context 改写、动态生灭。ownership 唯一;寻址路径随挂载重算
(reparent 安全)。三通道管控不变:事件闸门 / child_context 改写 /
surface 管控;badge = 父对子事件的记账。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agent_os.host.tui.kernel.widget import Widget, push_error

if TYPE_CHECKING:
    from agent_os.host.tui.kernel.registry import WidgetRegistry


class CompoundWidget(Widget):
    """复合 widget 基座:children / 事件闸门 / badge 记账 / 动态白名单。"""

    def __init__(self, p_def: Any, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.children: list[Widget] = []
        self._child_event_subs: list[Callable[[Widget, str, dict[str, Any]], None]] = []

    # ------------------------------------------------------------------
    # 声明面(子类覆盖)
    # ------------------------------------------------------------------

    def get_slots(self) -> list[dict[str, Any]]:
        """预定义子件清单(静态,随实例创建):[{id, kind, state?, surface?}]"""
        return []

    def get_dynamic_allow(self) -> list[str]:
        """动态子件 kind 白名单(空 = 不许动态)"""
        return []

    def get_dynamic_max(self) -> int:
        return 50

    def on_child_event(self, _child: Widget, _evt_name: str, _payload: dict[str, Any]) -> bool:
        """事件闸门(§7 通道 1):False 吞掉;True 放行(实现内可改写 payload)"""
        return True

    def child_context(self, _child: Widget, fragment: dict[str, Any]) -> dict[str, Any]:
        """信息管控(§7 通道 2):cascade 收集子 fragment 时经父改写(过滤/补充)"""
        return fragment

    def on_child_event_signal(self, cb: Callable[[Widget, str, dict[str, Any]], None]) -> None:
        """child_event signal 对译(订阅者通道)。"""
        self._child_event_subs.append(cb)

    # ------------------------------------------------------------------
    # 组合(slots / add / remove / find;ownership 唯一 + 祖先链防环 §10)
    # ------------------------------------------------------------------

    def create_slots(self, registry: WidgetRegistry) -> None:
        """按 slots 创建预定义子件(kind 惰性校验在 registry)。"""
        for slot in self.get_slots():
            child = registry.create(slot["kind"], slot.get("state", {}))
            if child is None:
                continue
            child.surface = slot.get("surface", "tab")
            self.add_child_widget(child, slot["id"])

    def add_child_widget(self, child: Widget | None, slot_id: str = "") -> Widget | None:
        """挂载子件(ownership 唯一 + 祖先链防环)。slot_id 即路径段。"""
        if child is None:
            return None
        if child.owner is not None:
            push_error(f"ownership 唯一:子件已有 owner({child.owner.path})")
            return None
        p: Widget | None = self
        while p is not None:
            if p is child:
                push_error("ownership 是树,不允许成环")
                return None
            p = p.owner
        if not slot_id:
            allow = self.get_dynamic_allow()
            if not allow:
                push_error(f"{self.get_kind()} 不许动态子件")
                return None
            if child.get_kind() not in allow:
                push_error(f"kind {child.get_kind()} 不在 {self.get_kind()} 的 dynamic.allow 白名单")
                return None
            if self._count_dynamic() >= self.get_dynamic_max():
                push_error(f"{self.get_kind()} 动态子件超上限 {self.get_dynamic_max()}")
                return None
        child.owner = self
        child.path_segment = slot_id if slot_id else child.id
        self.children.append(child)
        self.repath(child)
        return child

    def _count_dynamic(self) -> int:
        slot_ids = {s["id"] for s in self.get_slots()}
        return sum(1 for c in self.children if c.path_segment not in slot_ids)

    def remove_child_widget(self, id_or_segment: str, destroy: bool = True) -> Widget | None:
        """移除子件。destroy=False = detach(instance 活着,可被别家 attach,§4)。"""
        for i, child in enumerate(self.children):
            if child.id == id_or_segment or child.path_segment == id_or_segment:
                self.children.pop(i)
                self._clear_badge(child)
                child.owner = None
                if destroy:
                    self._destroy_rec(child)
                return child
        return None

    def find_child(self, segment: str) -> Widget | None:
        for c in self.children:
            if c.path_segment == segment or c.id == segment:
                return c
        return None

    def find_child_of(self, py_class: type, pred: Callable[[Widget], bool] | None = None) -> Widget | None:
        for c in self.children:
            if isinstance(c, py_class) and (pred is None or pred(c)):
                return c
        return None

    @staticmethod
    def _destroy_rec(w: Widget) -> None:
        if isinstance(w, CompoundWidget):
            for ch in list(w.children):
                CompoundWidget._destroy_rec(ch)
            w.children.clear()
        w.destroy()

    @staticmethod
    def repath(w: Widget) -> None:
        """路径随 ownership 链重算,后代级联(C4.4 _repathSubtree 同语义)。"""
        w.path = w.path_segment if w.owner is None else f"{w.owner.path}/{w.path_segment}"
        if isinstance(w, CompoundWidget):
            for ch in w.children:
                CompoundWidget.repath(ch)

    # ------------------------------------------------------------------
    # 事件闸门入口(子 emit_event 的必经路):
    # 闸门 → badge 记账 → 订阅者 → 继续上行。
    # ------------------------------------------------------------------

    def dispatch_child_event(self, child: Widget, evt_name: str, payload: dict[str, Any]) -> None:
        if not self.on_child_event(child, evt_name, payload):
            return  # 吞掉
        # badge 记账(COMPOUND §7 补丁):放行负载带 badge 字段
        # (number 记 / 0·None 摘),是父对子事件的记账,不是父偷读子 state
        if "badge" in payload:
            badges: dict[str, Any] = self.state.get("badges", {})
            b = payload["badge"]
            if b is None or (isinstance(b, int) and b == 0):
                badges.pop(child.id, None)
            else:
                badges[child.id] = b
            self.state["badges"] = badges
        child.raise_direct(evt_name, payload)
        for cb in self._child_event_subs:
            cb(child, evt_name, payload)
        if self.owner is not None:
            self.owner.dispatch_child_event(child, evt_name, payload)  # 沿树继续上行

    def _clear_badge(self, child: Widget) -> None:
        badges: dict[str, Any] = self.state.get("badges", {})
        if child.id in badges:
            del badges[child.id]
            self.state["badges"] = badges

    def destroy(self) -> None:
        for ch in list(self.children):
            self._destroy_rec(ch)
        self.children.clear()
        super().destroy()
