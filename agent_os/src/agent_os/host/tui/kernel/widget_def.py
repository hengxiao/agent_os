"""WidgetDef(docs/TUI-DOC.md §3;对译 godot/kernel/widget_def.gd)。

Widget 协议定义面(docs/WIDGETS.md §1.2 的 WidgetDef;APP-MODEL.md §2 同哲学):
kind / v / actions / events / surfaces + 实例工厂。widget 是代码资产,注册进
WidgetRegistry(注册即校验,不合规拒注册)。

TUI 新增(§5.1):``intents`` 声明面——widget 声明自己响应的 intent 集合,
注册时对照活动 KeymapPack 的契约清单校验。godot 原型无此面(TUI 键位体系
特有),属于有意扩展。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_os.host.tui.kernel.widget import Widget


class WidgetDef:
    """Widget 协议定义面。子类覆盖 get_* 声明面与 create_instance 工厂。"""

    def get_kind(self) -> str:
        return ""

    def get_v(self) -> int:
        return 1

    def get_actions(self) -> list[dict[str, Any]]:
        """action 声明面:[{id, exec, ref, args_input[], surfaces[]}];
        exec: "local" | "endpoint" | "run"(APP-MODEL §4 三态;客户端语义 = 要不要出海)。"""
        return []

    def get_events(self) -> list[str]:
        return []

    def get_surfaces(self) -> list[str]:
        return ["card", "tab"]

    def get_intents(self) -> list[str]:
        """intent 声明面(docs/TUI-DOC.md §5.1):widget 响应的 intent 集合;
        注册时校验落在活动 keymap 契约清单内(缺绑定 = 拒注册,同机制)。"""
        return []

    def create_instance(self, state: dict[str, Any]) -> Widget:
        """实例工厂。state 必须可 JSON 序列化(dict;刷新/重渲染可恢复)。"""
        raise NotImplementedError
