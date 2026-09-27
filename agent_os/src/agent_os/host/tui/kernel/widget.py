"""Widget 实例基类(docs/TUI-DOC.md §3;对译 godot/kernel/widget.gd)。

三铁律不变(docs/WIDGETS.md §1.2/§1.3):
  1. 有状态(state 可序列化 dict;游标/选中项也收在里面,§3 末段);
  2. 发事件(emit_event 上行,经 owner 事件闸门,永不直接出海);
  3. render 纯渲染(state → cell buffer 区域;先清后建,幂等可重入)。

与 godot 原型的对应:signal 换成订阅回调列表;``root: Control``(唯一引擎
耦合)换成"cell buffer 上的一段区域"——widget 协议不假设渲染层,render_into
是宿主层约定的渲染钩子,内核对它零依赖。
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_os.host.tui.kernel.compound import CompoundWidget
    from agent_os.host.tui.kernel.widget_def import WidgetDef


def push_error(msg: str) -> None:
    """push_error 对译:stderr 单行(注册面是代码评审面;TUI raw 模式下宿主
    可在 raw 期间重定向,见 __main__)。"""
    print(f"[tui-kernel] {msg}", file=sys.stderr)


class Widget:
    """Widget 实例基类:state / emit_event / render 三铁律。"""

    _seq: int = 0

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        self.def_: WidgetDef = p_def
        Widget._seq += 1
        self.id: str = f"{p_def.get_kind()}-{Widget._seq:04x}"
        self.path_segment: str = self.id
        self.state: dict[str, Any] = copy.deepcopy(p_state)
        self.path: str = ""  # /root/... 全树唯一(docs/APP-MODEL.md §14)
        self.surface: str = "tab"  # "card" | "tab"(当前面孔)
        self.owner: CompoundWidget | None = None
        self._event_subs: list[Callable[[Widget, str, dict[str, Any]], None]] = []
        self._state_subs: list[Callable[[Widget], None]] = []

    def get_kind(self) -> str:
        return self.def_.get_kind()

    # ------------------------------------------------------------------
    # 订阅(signal 对译:widget_event / state_changed)
    # ------------------------------------------------------------------

    def on_event(self, cb: Callable[[Widget, str, dict[str, Any]], None]) -> None:
        self._event_subs.append(cb)

    def on_state_changed(self, cb: Callable[[Widget], None]) -> None:
        self._state_subs.append(cb)

    # ------------------------------------------------------------------
    # state 三态(set / patch / mutate;对应 godot 同名方法)
    # ------------------------------------------------------------------

    def set_state(self, next_state: dict[str, Any]) -> None:
        """整态替换 + 重渲。"""
        self.state = copy.deepcopy(next_state)
        self.render()

    def patch_state(self, mutator: Callable[[dict[str, Any]], None]) -> None:
        """就地改 state + 重渲。"""
        mutator(self.state)
        self.render()

    def mutate_state(self, mutator: Callable[[dict[str, Any]], None]) -> None:
        """静默改 state 不重渲(高频路径用,防重建丢焦点;全量重渲丢焦点的
        踩坑史先例,docs/TUI-DOC.md §3 末段照抄)。"""
        mutator(self.state)

    # ------------------------------------------------------------------
    # 事件上行(emit_event → owner 闸门 → 订阅者;COMPOUND-WIDGET.md §7 通道 1)
    # ------------------------------------------------------------------

    def emit_event(self, evt_name: str, payload: dict[str, Any] | None = None) -> bool:
        """校验已声明(未声明事件不发),经 owner 事件闸门放行后才到订阅者。"""
        if payload is None:
            payload = {}
        if evt_name not in self.def_.get_events():
            push_error(f"{self.get_kind()} 未声明事件: {evt_name}")
            return False
        if self.owner is not None:
            self.owner.dispatch_child_event(self, evt_name, payload)
        else:
            self.raise_direct(evt_name, payload)
        return True

    def raise_direct(self, evt_name: str, payload: dict[str, Any]) -> None:
        """闸门放行后由父调起(勿直接用)。"""
        for cb in self._event_subs:
            cb(self, evt_name, payload)

    # ------------------------------------------------------------------
    # 渲染(纯渲染:state → 视图;宿主层约定 render_into 画进 cell buffer 区域)
    # ------------------------------------------------------------------

    def render(self) -> None:
        """state → 视图的渲染钩子(先清再建,可从任意 state 重建)。
        基类广播 state_changed;TUI 宿主做全量重渲,画面产出在 render_into。"""
        for cb in self._state_subs:
            cb(self)

    def render_into(self, _cells: Any, _region: Any) -> None:
        """TUI 渲染钩子(docs/TUI-DOC.md §3):把当前 state 画进 cell buffer
        的指定区域(state → 2D cell buffer 是真纯函数)。基类空实现。"""

    # ------------------------------------------------------------------
    # context / read 动词面
    # ------------------------------------------------------------------

    def context_fragment(self) -> dict[str, Any]:
        """context_provider(docs/APP-MODEL.md §16):本 widget 贡献给 cascade 的
        fragment。缺省 = kind + 人话摘要;声明者覆盖。父可经 child_context 改写。"""
        return {"kind": self.get_kind(), "summary": self.summary()}

    def summary(self) -> str:
        """read 动词的人话摘要(卡面禁忌词纪律由具体 widget 自律)。"""
        return self.get_kind()

    def destroy(self) -> None:
        """销毁(godot 里 queue_free 视图根;TUI 无视图根,清订阅即可)。"""
        self._event_subs.clear()
        self._state_subs.clear()
