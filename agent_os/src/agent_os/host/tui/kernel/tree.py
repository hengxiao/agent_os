"""WidgetTree(docs/TUI-DOC.md §3;对译 godot/kernel/widget_tree.gd)。

Widget 树 + 寻址(docs/APP-MODEL.md §14):路径 = ownership 链,与视图无关;
agent 三动词里 read/focus 全开放,act 的代理收口留待后续(§14.4,本期不对
agent 开放,docs/TUI-DOC.md §8 同裁决)。

与 godot 原型的差异:focus 动词不再找 ScrollContainer(无视图树)——把路径
解析结果交回调度者;滚动 + 反色脉冲的终端实现 = ``focus_target`` 回调 +
MotionPlayer("focus-pulse")(§4 映射表"镜头推镜"行)。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agent_os.host.tui.kernel.cascade import ContextCascade
from agent_os.host.tui.kernel.compound import CompoundWidget
from agent_os.host.tui.kernel.widget import Widget
from agent_os.host.tui.kernel.widget_def import WidgetDef

if TYPE_CHECKING:
    from agent_os.host.tui.tui.motion import MotionPlayer


class DesktopRootDef(WidgetDef):
    """根 compound 定义面(COMPOUND-WIDGET.md §8 / DESKTOP-WIDGET.md §2)。"""

    def get_kind(self) -> str:
        return "desktop"

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return DesktopRoot(self, state)


class DesktopRoot(CompoundWidget):
    """path = "/root",全树寻址从这里开始。T1 的 root 只做寻址/级联,不渲染
    chrome(窗口 chrome 是宿主的职责)。"""

    def __init__(self, p_def: WidgetDef, state: dict[str, Any]) -> None:
        super().__init__(p_def, state)
        self.path_segment = "root"
        self.path = "/root"

    def context_fragment(self) -> dict[str, Any]:
        """shell 级 fragment(C4.4 child_context 同构:user/theme/at)。"""
        from agent_os.host.tui.tui.theme import ThemeRegistry  # 延迟导入防环

        current = ThemeRegistry.current()
        return {
            "user": "tui-terminal",
            "theme": current.id if current is not None else "",
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def summary(self) -> str:
        return "Agent OS 桌面根(TUI)"


class WidgetTree:
    """Widget 树 + 寻址:路径 = ownership 链,查树不查视图。"""

    def __init__(self, motion: MotionPlayer | None = None) -> None:
        self.root: CompoundWidget = DesktopRoot(DesktopRootDef(), {})
        self.cascade: ContextCascade = ContextCascade(self)
        self._motion = motion
        #: focus 动词的宿主回调(滚动到视图;宿主注册,widget 层零渲染面)
        self.focus_target: Callable[[Widget], None] | None = None

    def resolve(self, path: str) -> Widget | None:
        """路径解析(查树不查视图):/root/seg/seg…;不存在返回 None。"""
        if not path:
            return None
        segs = path.split("/")
        i = 1 if segs and segs[0] == "" else 0  # 前导斜杠
        if i >= len(segs) or segs[i] != "root":
            return None
        cur: Widget = self.root
        i += 1
        while i < len(segs):
            if not isinstance(cur, CompoundWidget):
                return None
            cur = cur.find_child(segs[i])
            if cur is None:
                return None
            i += 1
        return cur

    def read_summary(self, path: str) -> str:
        """read 动词(§14.3):人话摘要(禁忌词纪律由 widget 摘要层保证)。"""
        w = self.resolve(path)
        return "" if w is None else w.summary()

    def focus(self, path: str) -> bool:
        """focus 动词(§14.3):滚动到视图 + 高亮脉冲(reduced-motion 时即时切换)。
        终端实现:宿主回调滚动 + MotionPlayer("focus-pulse")(§4 映射表)。"""
        w = self.resolve(path)
        if w is None:
            return False
        if self.focus_target is not None:
            self.focus_target(w)
        if self._motion is not None:
            self._motion.play("focus-pulse", w)
        return True

    def dump_paths(self) -> str:
        """调试用:整树路径清单。"""
        lines: list[str] = []
        self._dump_rec(self.root, lines)
        return "\n".join(lines)

    def _dump_rec(self, w: Widget, lines: list[str]) -> None:
        lines.append(f"{w.path}  [{w.get_kind()}]")
        if isinstance(w, CompoundWidget):
            for ch in w.children:
                self._dump_rec(ch, lines)
