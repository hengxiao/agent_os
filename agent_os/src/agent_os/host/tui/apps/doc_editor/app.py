"""DocEditorApp(docs/TUI-DOC.md §2/§4/§5;T2 里程碑:字符级批注坞,首个写面)。

compound 组装:信箱(index)+ 信纸(letter)+ 批注坞输入(dock)三个子
widget;屏幕态/命令条/帮助面板/状态栏/批注坞全在 app 层。widget 层零网络面
——唯一出海在 app 层经 DocSource(annotations upsert/delete,§1 五口之一;
离线/演示源只读,诚实报"需在线服务",不做假写面)。

T2 用户裁决:注释精度到字符,光标到字符;框选字符串 → 基于选区创建批注。
锚点在保存时由选区偏移算(model.anchor_from_range;1-based 闭区间 L/C)。

键纪律:feed_key 进来的物理键一律先过 KeymapEngine 翻成 intent;组件只响应
intent(零按键分支)。modal 包的模式(normal/insert)只存在于撰写控件
(dock/command),正文永远不进 INSERT(§1 红线);hint 栏与帮助面板从活动
keymap 生成,永不硬编码。
"""

from __future__ import annotations

from typing import Any

from agent_os.host.tui.apps.doc_editor.model import (
    ANN_CONTENT_MAX,
    DocSource,
    anchor_from_range,
    line_diff,
)
from agent_os.host.tui.apps.doc_editor.reel import (
    WORKING,
    build_tree_rows,
    reel_items,
    reel_label,
    tree_rows_text,
)
from agent_os.host.tui.apps.doc_editor.widgets import (
    IndexDef,
    IndexWidget,
    InputDef,
    InputWidget,
    LetterDef,
    LetterWidget,
)
from agent_os.host.tui.kernel.compound import CompoundWidget
from agent_os.host.tui.kernel.registry import WidgetRegistry
from agent_os.host.tui.kernel.tree import WidgetTree
from agent_os.host.tui.kernel.widget import Widget, push_error
from agent_os.host.tui.kernel.widget_def import WidgetDef
from agent_os.host.tui.tui import sty
from agent_os.host.tui.tui.cells import CellBuffer, Region, Style, text_width
from agent_os.host.tui.tui.keymap import (
    RES_INTENT,
    RES_PREFIX,
    RES_SELF_INSERT,
    KeymapEngine,
    KeymapRegistry,
)
from agent_os.host.tui.tui.keymap import register_built_in as register_keymaps
from agent_os.host.tui.tui.keys import KeyEvent
from agent_os.host.tui.tui.motion import MotionPlayer
from agent_os.host.tui.tui.theme import ThemeRegistry
from agent_os.host.tui.tui.theme import register_built_in as register_themes

#: 状态栏 hint 摘要的 intent 面(从活动 keymap 生成;§5.1 永不硬编码)
_HINT_INTENTS = ["move_up", "move_down", "mark", "annotate", "versions", "tree",
                 "command", "help", "quit"]
#: 版本条焦点时的 hint(scrub/确认/取消)
_REEL_HINT_INTENTS = ["move_left", "move_right", "open", "quit"]
#: 坞内操作 hint(存/弃/删;删仅编辑既有批注时有意义)
_DOCK_HINT_INTENTS = ["confirm", "cancel", "delete", "newline"]

#: 批注状态章文案(双编码:文字通道;色由调用方 token 给)
_STATUS_STAMP = {"pending": "待处理", "applied": "已采纳", "ignored": "已忽略",
                 "outdated": "已过期"}


class DocEditorDef(WidgetDef):
    def get_kind(self) -> str:
        return "tui.doc_editor"

    def get_events(self) -> list[str]:
        return ["quit"]

    def get_intents(self) -> list[str]:
        # app 自身响应的 intent = 跨包共有面(modal 专属的 mode_normal/
        # mode_insert 不进契约面——modeless 包无此面,声明了会破坏跨包注册校验)
        return ["quit", "command", "help", "confirm", "cancel", "save",
                "annotate", "delete", "clear_mark", "versions", "tree"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return DocEditorApp(self, state)


class DocEditorApp(CompoundWidget):
    """state(全部可序列化):screen / mode / status / command{active,text} /
    dock{active,quote,anchor,range,status_label} / help / quit。
    数据源与引擎由装配注入(host 接线,不进 state)。"""

    DOCK_ROWS = 6  # 坞高(分隔 + 引文 + 编辑区 3 行 + 操作行)

    def __init__(self, p_def: WidgetDef, p_state: dict[str, Any]) -> None:
        super().__init__(p_def, p_state)
        self.state.setdefault("screen", "index")
        self.state.setdefault("mode", "normal")  # modal 包撰写控件用
        self.state.setdefault("status", "")
        self.state.setdefault("command", {"active": False, "text": ""})
        self.state.setdefault("dock", {"active": False, "quote": "", "anchor": None,
                                       "range": None, "status_label": ""})
        self.state.setdefault("help", False)
        self.state.setdefault("quit", False)
        # T3:版本面(versions = {base, versions:[…]};reel = 版本条焦点/scrub/
        # 两段确认 armed;tree = 版本树覆盖层)。全部可序列化。
        self.state.setdefault("versions", {"base": "", "versions": []})
        self.state.setdefault("reel", {"active": False, "sel": 0, "armed": None})
        self.state.setdefault("tree", {"active": False, "sel": 0})
        #: 装配注入(host 接线;widget 协议不假设数据源/渲染层)
        self.source: DocSource | None = None
        self.engine: KeymapEngine | None = None
        self.motion: MotionPlayer | None = None
        self._diff_cache: dict[str, list[tuple[str, str]]] = {}  # 瞬态(不进 state)

    # ------------------------------------------------------------------
    # 组合面
    # ------------------------------------------------------------------

    def get_slots(self) -> list[dict[str, Any]]:
        return [
            {"id": "index", "kind": "tui.index"},
            {"id": "letter", "kind": "tui.letter"},
            {"id": "dock", "kind": "tui.input"},
        ]

    def get_dynamic_allow(self) -> list[str]:
        return ["tui.index", "tui.letter", "tui.input"]

    def index_widget(self) -> IndexWidget:
        return self.find_child("index")  # type: ignore[return-value]

    def letter_widget(self) -> LetterWidget:
        return self.find_child("letter")  # type: ignore[return-value]

    def dock_widget(self) -> InputWidget:
        return self.find_child("dock")  # type: ignore[return-value]

    def wire(self) -> None:
        """装配后接线:订阅本层 child_event(compound 通道,每层只放一次;
        不订阅子的 widget_event——原型语义下它沿祖先链每层重放一次)。"""
        self.on_child_event_signal(self._on_child_event)

    def _on_child_event(self, child: Widget, evt_name: str, payload: dict[str, Any]) -> None:
        if child is self.index_widget() and evt_name == "open":
            self.open_doc(str(payload.get("name") or ""))

    # ------------------------------------------------------------------
    # 数据面(app 层唯一出海;§7 客户端纪律)
    # ------------------------------------------------------------------

    def reload_index(self) -> None:
        """信箱整表重载(数据源 → widget state;失败进状态行,不炸)。"""
        if self.source is None:
            return
        try:
            entries = self.source.list_docs()
        except Exception as e:  # noqa: BLE001 — 数据源故障归状态行(只读阅览不炸)
            msg = f"信箱加载失败: {e}"
            self.mutate_state(lambda st: st.update(status=msg))
            return
        self.index_widget().set_entries([
            {
                "name": e.name, "title": e.title, "version": e.version_label(),
                "date": e.date_label(), "saved_at": e.saved_at, "annotations": e.annotations,
            }
            for e in entries
        ])

    def open_doc(self, name: str) -> None:
        """打开一封信:读信 → 信纸整态替换 → 切屏 → focus-pulse(§4 镜头推镜行)。"""
        if self.source is None:
            return
        try:
            letter = self.source.read_letter(name)
        except Exception as e:  # noqa: BLE001 — 读信失败归状态行
            msg = f"读信失败: {e}"
            self.mutate_state(lambda st: st.update(status=msg))
            return
        self._load_letter_state(letter)
        self.mutate_state(lambda st: st.update(
            screen="letter", status="",
            reel={"active": False, "sel": 0, "armed": None},
            tree={"active": False, "sel": 0}))
        self.load_versions()
        if self.motion is not None:
            self.motion.play("focus-pulse", self.letter_widget())

    def load_versions(self) -> None:
        """拉版本面(tree 端点;失败给空面,不炸)。app.state["versions"] 是唯一事实。"""
        if self.source is None or not self.source.supports_versions:
            self.mutate_state(lambda st: st.update(versions={"base": "", "versions": []}))
            return
        name = str(self.letter_widget().state.get("name") or "")
        if not name:
            return
        try:
            tree = self.source.versions(name)
        except Exception as e:  # noqa: BLE001 — 版本面故障给空,状态行不吵
            push_error(f"版本面加载失败: {e}")
            tree = {"base": "", "versions": []}
        self._diff_cache.clear()
        self.mutate_state(lambda st: st.update(versions=tree))

    def _load_letter_state(self, letter: Any, keep_cursor: bool = False) -> None:
        """Letter → 信纸 state(批注重拉后原位刷新用 keep_cursor)。"""
        prev = self.letter_widget().state
        self.letter_widget().set_state({
            "name": letter.name,
            "title": letter.title,
            "version": letter.version,
            "text": letter.text,
            "spans": [
                {"anchor": sp.anchor, "quote": sp.quote, "content": sp.content,
                 "status": sp.status, "start": sp.start, "end": sp.end,
                 "resolved": sp.resolved}
                for sp in letter.spans
            ],
            "cursor": prev.get("cursor", 0) if keep_cursor else 0,
            "mark": None,
            "goal_col": None,
            "scroll": prev.get("scroll", 0) if keep_cursor else 0,
        })

    # ------------------------------------------------------------------
    # 键面(物理键 → intent → 路由;优先级:帮助 > 命令条 > 坞 > 屏幕)
    # ------------------------------------------------------------------

    def feed_key(self, ev: KeyEvent) -> None:
        if self.engine is None:
            return
        if self.state.get("help"):
            self.mutate_state(lambda st: st.update(help=False))
            return
        modal = self.engine.pack.modal
        if self.state["command"]["active"]:
            self._feed_command_key(ev)
            return
        if self.state["dock"]["active"]:
            self._feed_dock_key(ev, modal)
            return
        # T3:版本树覆盖层 / 版本条焦点优先于信纸(都是 letter 屏的子面)
        if self.state.get("screen") == "letter" and self.state["tree"]["active"]:
            self._feed_tree_key(ev)
            return
        if self.state.get("screen") == "letter" and self.state["reel"]["active"]:
            self._feed_reel_key(ev)
            return
        context = str(self.state.get("screen", "index"))
        res, payload = self.engine.resolve(ev, context)
        if res == RES_PREFIX:
            return  # 前缀挂起:状态栏回显 pending(渲染期读 engine)
        if res == RES_SELF_INSERT:
            return  # 阅览屏无自插入面(§1:正文永远不进 INSERT)
        if res == RES_INTENT:
            self._dispatch_intent(str(payload), context)
        else:
            self._on_unbound(ev, context)

    def _dispatch_intent(self, intent: str, context: str) -> None:
        """intent 路由:app 级全局面先行,余下交当前屏 widget。"""
        if intent == "help":
            self.mutate_state(lambda st: st.update(help=True, status=""))
            return
        if intent == "command":
            self.mutate_state(lambda st: st.update(
                command={"active": True, "text": ""}, status="", mode="insert"))
            return
        if intent == "quit":
            self._quit(context)
            return
        if intent == "annotate":
            self.open_dock()
            return
        if intent == "versions":
            self.toggle_reel()
            return
        if intent == "tree":
            self.open_tree()
            return
        if intent == "open" and context == "letter":
            # Enter 落在既有批注 span 上 = 开坞重编(同锚点 upsert 语义)
            if self.letter_widget().span_at(int(self.letter_widget().state.get("cursor", 0))):
                self.open_dock()
            return
        target = self.index_widget() if context == "index" else self.letter_widget()
        if context == "letter":
            handled = target.handle_intent(intent, page_rows=self._body_rows())
        else:
            handled = target.handle_intent(intent)
        if handled:
            self.mutate_state(lambda st: st.update(status=""))
            return
        self._unsupported(intent)

    def _quit(self, context: str) -> None:
        """q/C-g:信纸 → 退回信箱;信箱 → 退出 app(emit "quit" 给宿主)。
        清选区是独立 intent(clear_mark:vim Esc / emacs C-g 在 letter 面)。"""
        if context == "letter":
            self.letter_widget().emit_event("close", {})
            self.mutate_state(lambda st: st.update(screen="index", status=""))
            return
        self.mutate_state(lambda st: st.update(quit=True))
        self.emit_event("quit", {})

    def _on_unbound(self, ev: KeyEvent, context: str) -> None:
        """未绑定键:信纸上按可打印键 → 一句人话引导(§1.5;copy 走 keymap/
        theme copy 键,键名从活动包生成)。"""
        if context != "letter" or self.engine is None:
            return
        if len(ev.key) == 1 and ev.key.isprintable() and not ev.ctrl and not ev.meta:
            hint = sty.copy("doc.readonly.hint").format(
                annotate=self.engine.binding_for("annotate", "letter") or "?",
                open=self.engine.binding_for("open", "letter") or "Enter",
            )
            self.mutate_state(lambda st: st.update(status=hint))

    def _unsupported(self, intent: str) -> None:
        """已声明但本期未开放的 intent(save/generate/review/versions 等)。"""
        label = (self.engine.pack.copy.get(intent, intent) if self.engine else intent)
        self.mutate_state(lambda st: st.update(
            status=sty.copy("doc.intent.later").format(label=label)))

    # ------------------------------------------------------------------
    # 批注坞(T2 写面;选区开坞 / 光标落 span 重编 / 存删出海)
    # ------------------------------------------------------------------

    def open_dock(self) -> None:
        """annotate intent 入口(仅信纸屏)。优先级:选区 → 新建;光标在 span
        内 → 重编;都无 → 引导人话。只读源直接报"需在线服务"(诚实优先)。"""
        if self.state.get("screen") != "letter":
            self.mutate_state(lambda st: st.update(status="先打开一封信(Enter)再批注"))
            return
        if self.source is None or not self.source.writable:
            self.mutate_state(lambda st: st.update(status=sty.copy("doc.ann.readonly_source")))
            return
        lw = self.letter_widget()
        cur = int(lw.state.get("cursor", 0))
        sel = lw.selection()
        if sel is not None:
            quote = lw.text()[sel[0]:sel[1]]
            dock = {"active": True, "quote": quote, "anchor": None,
                    "range": [sel[0], sel[1]], "status_label": "pending"}
            self.dock_widget().set_state({"text": "", "cursor": 0})
        else:
            span = lw.span_at(cur)
            if span is None:
                mark_key = self.engine.binding_for("mark", "letter") if self.engine else ""
                self.mutate_state(lambda st: st.update(
                    status=sty.copy("doc.ann.need_selection").format(mark=mark_key or "?")))
                return
            dock = {"active": True, "quote": str(span.get("quote") or ""),
                    "anchor": str(span.get("anchor") or ""), "range": None,
                    "status_label": str(span.get("status") or "pending")}
            content = str(span.get("content") or "")
            self.dock_widget().set_state({"text": content, "cursor": len(content)})
        self.mutate_state(lambda st: st.update(dock=dock, mode="insert", status=""))

    def _feed_dock_key(self, ev: KeyEvent, modal: bool) -> None:
        assert self.engine is not None
        mode = str(self.state.get("mode", "normal")) if modal else None
        res, payload = self.engine.resolve(ev, "dock", mode)
        if res == RES_SELF_INSERT and isinstance(payload, KeyEvent):
            self.dock_widget().insert_char(payload.key)
            return
        if res != RES_INTENT:
            return
        intent = str(payload)
        if intent == "confirm":
            self._dock_save()
        elif intent == "cancel":
            self._dock_close()
        elif intent == "delete":
            self._dock_delete()
        elif intent in ("mode_normal", "mode_insert"):
            self.mutate_state(lambda st: st.update(mode="normal" if intent == "mode_normal" else "insert"))
        else:
            self.dock_widget().handle_intent(intent)

    def _dock_close(self) -> None:
        self.mutate_state(lambda st: st.update(
            dock={"active": False, "quote": "", "anchor": None, "range": None,
                  "status_label": ""},
            mode="normal"))

    def _dock_save(self) -> None:
        """存(先管道后盖章:动画只随成功播,T2 只给状态行人话)。"""
        if self.source is None:
            return
        dock = self.state["dock"]
        content = str(self.dock_widget().state.get("text") or "").strip()
        if not content:
            self.mutate_state(lambda st: st.update(status="批注为空,未发送"))
            return
        if len(content) > ANN_CONTENT_MAX:
            self.mutate_state(lambda st: st.update(status=sty.copy("doc.ann.too_long")))
            return
        lw = self.letter_widget()
        anchor = dock.get("anchor")
        quote = str(dock.get("quote") or "")
        if anchor is None:
            rng = dock.get("range")
            if not rng:
                self._dock_close()
                return
            anchor = anchor_from_range(lw.text(), int(rng[0]), int(rng[1]))
        try:
            self.source.save_annotation(str(lw.state.get("name") or ""), anchor, quote, content)
        except Exception as e:  # noqa: BLE001 — 出海失败归状态行(坞不关,内容不丢)
            msg = f"批注存失败: {e}"
            self.mutate_state(lambda st: st.update(status=msg))
            return
        # 成功:重拉批注(原位刷新)+ 状态行人话 + 关坞 + 清选区
        try:
            self._load_letter_state(self.source.read_letter(str(lw.state.get("name") or "")),
                                    keep_cursor=True)
        except Exception as e:  # noqa: BLE001 — 重拉失败不挡成功回声(留痕 stderr)
            push_error(f"批注重拉失败: {e}")
        self.mutate_state(lambda st: st.update(status=sty.copy("doc.ann.saved")))
        self._dock_close()
        self.reload_index()

    def _dock_delete(self) -> None:
        """删(仅既有批注可删;新建无 anchor 时退化为弃)。"""
        if self.source is None:
            return
        dock = self.state["dock"]
        anchor = dock.get("anchor")
        if not anchor:
            self._dock_close()
            return
        try:
            self.source.delete_annotation(str(self.letter_widget().state.get("name") or ""),
                                          str(anchor))
        except Exception as e:  # noqa: BLE001
            msg = f"批注删失败: {e}"
            self.mutate_state(lambda st: st.update(status=msg))
            return
        try:
            self._load_letter_state(
                self.source.read_letter(str(self.letter_widget().state.get("name") or "")),
                keep_cursor=True)
        except Exception as e:  # noqa: BLE001
            push_error(f"批注重拉失败: {e}")
        self.mutate_state(lambda st: st.update(status=sty.copy("doc.ann.deleted")))
        self._dock_close()
        self.reload_index()

    # ------------------------------------------------------------------
    # 版本条(T3;letter 屏底部条;scrub + diff 摘要 + 两段确认回溯)
    # ------------------------------------------------------------------

    def toggle_reel(self) -> None:
        """versions intent:进入/退出版本条焦点。无版本面 → 状态行人话。"""
        if self.source is None or not self.source.supports_versions:
            self.mutate_state(lambda st: st.update(status=sty.copy("doc.versions.none")))
            return
        if self.state.get("screen") != "letter":
            return
        reel = self.state["reel"]
        if reel["active"]:
            self.mutate_state(lambda st: st.update(
                reel={"active": False, "sel": 0, "armed": None}, status=""))
            return
        if not self.state["versions"]["versions"]:
            self.mutate_state(lambda st: st.update(status="尚无版本(盖章封存后这里出条)"))
            return
        # 进入:默认落在首个版本(左端工作稿是对照锚,不是 scrub 目标)
        self.mutate_state(lambda st: st.update(
            reel={"active": True, "sel": 1, "armed": None}, status=""))

    def _feed_reel_key(self, ev: KeyEvent) -> None:
        """版本条焦点的键面:h/l(C-f/C-b)scrub;Enter = armed → 再 Enter =
        确认执行;Esc/C-g/q 先取消 armed,未 armed 则退出焦点。"""
        assert self.engine is not None
        res, payload = self.engine.resolve(ev, "letter")
        if res != RES_INTENT:
            return
        reel = self.state["reel"]
        items = reel_items(self.state["versions"])
        sel = int(reel["sel"])
        if payload == "move_left":
            self.mutate_state(lambda st: st["reel"].update(
                sel=max(0, sel - 1), armed=None))
            return
        if payload == "move_right":
            self.mutate_state(lambda st: st["reel"].update(
                sel=min(len(items) - 1, sel + 1), armed=None))
            return
        if payload in ("quit", "clear_mark"):
            if reel["armed"] is not None:
                self.mutate_state(lambda st: st.update(status=""))  # 取消 armed
                self.mutate_state(lambda st: st["reel"].update(armed=None))
            else:
                self.mutate_state(lambda st: st.update(
                    reel={"active": False, "sel": 0, "armed": None}))
            return
        if payload == "open":
            self._reel_confirm()
            return
        # 其余 intent(tree/annotate/command/help/mark…):退出版本条焦点并
        # 转派回信纸(条是瞬态子面,不吞阅读期动作)
        self.mutate_state(lambda st: st.update(reel={"active": False, "sel": 0, "armed": None}))
        self._dispatch_intent(str(payload), "letter")

    def _reel_confirm(self) -> None:
        """两段确认(§10;armed 是本地 UI 态不出海):第一击 armed(状态行
        "再按 Enter 确认回溯到 vNNN"),第二击走管道;Esc/C-g 取消 armed。"""
        reel = self.state["reel"]
        items = reel_items(self.state["versions"])
        sel = int(reel["sel"])
        if sel >= len(items):
            return
        item = items[sel]
        if item == WORKING:
            self.mutate_state(lambda st: st.update(status=sty.copy("doc.diff.same")))
            return
        if reel["armed"] != item:
            confirm_key = (self.engine.binding_for("open", "letter") or "Enter") \
                if self.engine else "Enter"
            self.mutate_state(lambda st: st.update(
                status=sty.copy("doc.rewind.armed").format(
                    confirm=confirm_key, version=item)))
            self.mutate_state(lambda st: st["reel"].update(armed=item))
            return
        # 确认:armed 归位,走管道(先管道,后盖章——动画只随成功播)
        self.mutate_state(lambda st: st["reel"].update(armed=None))
        self._rewind_to(item)

    def _rewind_to(self, version: str) -> None:
        """rewind 走管道(app 层出海;godot rewind_to 先例)。成功:stamp-press
        + 重拉文档与版本条 + 状态行人话;失败:状态行错误,无动画。"""
        if self.source is None:
            return
        name = str(self.letter_widget().state.get("name") or "")
        try:
            self.source.rewind(name, version)
        except PermissionError as e:
            msg = str(e)
            self.mutate_state(lambda st: st.update(status=msg))
            return
        except Exception as e:  # noqa: BLE001 — 管道/越界失败归状态行,无动画
            msg = f"回溯失败: {e}"
            self.mutate_state(lambda st: st.update(status=msg))
            return
        # 成功路径(先管道后盖章):
        try:
            self._load_letter_state(self.source.read_letter(name))
        except Exception as e:  # noqa: BLE001
            push_error(f"回溯后重拉失败: {e}")
        self.load_versions()
        self.reload_index()
        self.mutate_state(lambda st: st.update(
            status=sty.copy("doc.rewind.done").format(version=version),
            reel={"active": True, "sel": 0, "armed": None}))
        if self.motion is not None:
            self.motion.play("stamp-press", self, text=f"[ 已盖章 {version} ]")

    # ------------------------------------------------------------------
    # diff 摘要(选中版 vs 工作稿;LLM 摘要优先,失败/离线回落行差集)
    # ------------------------------------------------------------------

    def _diff_for(self, item: str) -> list[tuple[str, str]]:
        """版本条右侧随行 diff:[("text"|"+"|"-", 内容)];缓存按条目名。
        工作稿/与 base 一致 → 明示"一致"(doc.diff.same)。"""
        if self.source is None or not self.source.supports_versions:
            return []
        if item == WORKING:
            return [("text", sty.copy("doc.diff.same"))]
        name = str(self.letter_widget().state.get("name") or "")
        if not name:
            return []
        cache_key = f"{name}@{item}"
        if cache_key in self._diff_cache:
            return self._diff_cache[cache_key]
        base = str(self.state["versions"].get("base") or "")
        out: list[tuple[str, str]] = []
        if item == base:
            out = [("text", sty.copy("doc.diff.same"))]
        else:
            # online:先打 diff-summary(LLM 面 180s 档;故障/不可用 → None 回落)
            summary = self.source.diff_summary(name, item, base) if base else None
            if summary:
                out = [("text", summary)]
            else:
                try:
                    old = self.source.read_version(name, item)
                    new = self.letter_widget().text()
                    diff = line_diff(old, new)
                    out = diff if diff else [("text", sty.copy("doc.diff.same"))]
                except (OSError, FileNotFoundError):
                    out = [("text", "(版本内容不可读)")]
        self._diff_cache[cache_key] = out
        return out

    # ------------------------------------------------------------------
    # 版本树覆盖层(T3;ASCII 分支图;Enter = 关层 + 版本条定位)
    # ------------------------------------------------------------------

    def open_tree(self) -> None:
        """tree intent:开覆盖层(parent 链;当前 base ◉ 标记)。无版本面 → 人话。"""
        if self.source is None or not self.source.supports_versions:
            self.mutate_state(lambda st: st.update(status=sty.copy("doc.versions.none")))
            return
        if self.state.get("screen") != "letter":
            return
        if not self.state["versions"]["versions"]:
            self.mutate_state(lambda st: st.update(status="尚无版本"))
            return
        base = str(self.state["versions"].get("base") or "")
        rows = build_tree_rows(self.state["versions"])
        sel = next((i for i, r in enumerate(rows) if r.version == base), 0)
        self.mutate_state(lambda st: st.update(tree={"active": True, "sel": sel}, status=""))

    def _feed_tree_key(self, ev: KeyEvent) -> None:
        assert self.engine is not None
        res, payload = self.engine.resolve(ev, "letter")
        if res != RES_INTENT:
            return
        rows = build_tree_rows(self.state["versions"])
        sel = int(self.state["tree"]["sel"])
        if payload in ("move_down", "move_right"):
            sel = min(len(rows) - 1, sel + 1)
            self.mutate_state(lambda st: st["tree"].update(sel=sel))
        elif payload in ("move_up", "move_left"):
            sel = max(0, sel - 1)
            self.mutate_state(lambda st: st["tree"].update(sel=sel))
        elif payload in ("quit", "clear_mark"):
            self.mutate_state(lambda st: st.update(tree={"active": False, "sel": 0}))
        elif payload == "open" and rows:
            # 关层 + 版本条定位到该版
            version = rows[sel].version
            items = reel_items(self.state["versions"])
            reel_sel = items.index(version) if version in items else 0
            self.mutate_state(lambda st: st.update(
                tree={"active": False, "sel": 0},
                reel={"active": True, "sel": reel_sel, "armed": None}))


    # ------------------------------------------------------------------
    # 命令条(§5.4:两派之外的兜底入口;T1 面:set keymap / keymap / q)
    # ------------------------------------------------------------------

    def _feed_command_key(self, ev: KeyEvent) -> None:
        assert self.engine is not None
        mode = "insert" if self.engine.pack.modal else None
        res, payload = self.engine.resolve(ev, "command", mode)
        if res == RES_SELF_INSERT and isinstance(payload, KeyEvent):
            self.mutate_state(lambda st: st["command"].update(text=st["command"]["text"] + payload.key))
            return
        if res != RES_INTENT:
            return
        if payload == "confirm":
            text = str(self.state["command"]["text"])
            self.mutate_state(lambda st: st.update(command={"active": False, "text": ""},
                                                   mode="normal"))
            self.run_command(text)
        elif payload == "cancel":
            self.mutate_state(lambda st: st.update(command={"active": False, "text": ""},
                                                   mode="normal"))
        elif payload == "edit_back":
            self.mutate_state(lambda st: st["command"].update(text=st["command"]["text"][:-1]))

    def run_command(self, text: str) -> None:
        """命令条是 action 界面(§1.6):本期只接 set keymap / keymap / q;
        写面命令(:w 等)按已声明 intent 走 _unsupported。"""
        words = text.strip().split()
        if not words:
            return
        pack = self.engine.pack if self.engine else None
        alias = pack.commands.get(words[0]) if pack else None
        if words[0] == "q":
            self._quit(str(self.state.get("screen", "index")))
            return
        if words[0] == "keymap":
            cur = KeymapRegistry.current()
            ids = " / ".join(KeymapRegistry.pack_ids())
            self.mutate_state(lambda st: st.update(
                status=f"keymap = {cur.id if cur else '?'}(可选:{ids})"))
            return
        if words[0] == "set" and len(words) == 3 and words[1] == "keymap":
            target = words[2]
            if target in KeymapRegistry.pack_ids():
                KeymapRegistry.apply(target)
                if self.engine and KeymapRegistry.current():
                    self.engine.set_pack(KeymapRegistry.current())
                self.mutate_state(lambda st: st.update(status=f"keymap → {target}"))
            else:
                ids = " / ".join(KeymapRegistry.pack_ids())
                self.mutate_state(lambda st: st.update(
                    status=f"未知 keymap: {target}(可选:{ids})"))
            return
        if alias is not None:
            self._dispatch_intent(alias, str(self.state.get("screen", "index")))
            return
        self.mutate_state(lambda st: st.update(status=f"未知命令: {text.strip()}"))

    # ------------------------------------------------------------------
    # 渲染(全量重渲:先清后建;focus-pulse 区域反色在所有内容之后)
    # ------------------------------------------------------------------

    def _body_rows(self, total_h: int = 20) -> int:
        """信纸正文行数(供 page intent 翻页粒度;渲染期同式)。"""
        return max(1, total_h - 2 - (self.DOCK_ROWS if self.state["dock"]["active"] else 0))

    def render_into(self, buf: CellBuffer, region: Region) -> None:
        screen = str(self.state.get("screen", "index"))
        dock_active = bool(self.state["dock"]["active"])
        reel_visible = screen == "letter" and bool(self.state["versions"]["versions"])
        dock_h = self.DOCK_ROWS if dock_active else 0
        reel_h = 1 if reel_visible else 0
        body = Region(region.x, region.y + 1, region.w,
                      max(0, region.h - 2 - dock_h - reel_h))
        self._render_header(buf, region, screen)
        if screen == "letter":
            self.letter_widget().render_into(buf, body)
        else:
            self.index_widget().render_into(buf, body)
        if dock_active:
            self._render_dock(buf, Region(region.x, region.y + 1 + body.h, region.w, dock_h))
        if reel_visible:
            self._render_reel(buf, Region(region.x, region.y + 1 + body.h + dock_h,
                                          region.w, 1))
        if self.state.get("help"):
            self._render_help(buf, body)
        if screen == "letter" and self.state["tree"]["active"]:
            self._render_tree(buf, body)
        self._render_status(buf, region, screen)
        if self.motion is not None:
            for _target, reg in self.motion.active_regions():
                buf.reverse_region(reg.x, reg.y, reg.w, reg.h)

    def _render_header(self, buf: CellBuffer, region: Region, screen: str) -> None:
        if screen == "letter":
            lw = self.letter_widget()
            title = f"─ {lw.state.get('title', '')}({lw.state.get('version', '') or '工作稿'}) "
        else:
            title = "─ 信箱 "
        rule = title + "─" * max(0, region.w - text_width(title))
        buf.write(region.x, region.y, rule, sty.text("fg-2"), max_width=region.w)

    def _render_dock(self, buf: CellBuffer, region: Region) -> None:
        """批注坞 = 底部分屏:引文 + 内容编辑区 + 状态章 + 操作 hint。"""
        dock = self.state["dock"]
        sep = "─ 批注 " + "─" * max(0, region.w - 5)
        buf.write(region.x, region.y, sep, sty.text("fg-2"), max_width=region.w)
        quote = str(dock.get("quote") or "").replace("\n", " ⏎ ")
        buf.write(region.x, region.y + 1, f"引文:「{quote}」", sty.paper(dim=True),
                  max_width=region.w)
        edit = Region(region.x, region.y + 2, region.w, max(1, region.h - 4))
        self.dock_widget().render_into(buf, edit)
        # 操作行:状态章(文字通道)+ keymap 生成的存/弃 hint
        stamp = _STATUS_STAMP.get(str(dock.get("status_label") or "pending"), "待处理")
        ops = self.engine.hint_summary("dock", _DOCK_HINT_INTENTS,
                                       str(self.state.get("mode", "normal"))) \
            if self.engine else ""
        line = f"  状态:[{stamp}]"
        buf.write(region.x, region.y + region.h - 1, line, sty.text("fg-1"),
                  max_width=region.w - 1)
        if ops:
            buf.write(region.x + max(0, region.w - text_width(ops) - 1),
                      region.y + region.h - 1, ops, sty.text("fg-1"))

    def _render_reel(self, buf: CellBuffer, region: Region) -> None:
        """版本条(letter 屏底部、状态栏之上):工作稿锚 + v008·v007… 左新右旧,
        超宽按选中项滚动;选中版右侧同行 diff 摘要(LLM 人话 / 红绿行兜底);
        armed 版本 seal 色标记(双编码:色 + 状态行人话)。"""
        reel = self.state["reel"]
        items = reel_items(self.state["versions"])
        if not items:
            return
        sel = min(int(reel["sel"]), len(items) - 1)
        y = region.y
        active = bool(reel["active"])
        base_style = sty.text("fg-0", bg_token="bg-1") if active else sty.text("fg-2")
        buf.fill(region.x, y, region.w, 1, base_style)
        x = region.x + 1
        # 超宽滚动:让 sel 尽量入窗(条 = 单行横向窗,选中项左半可见)
        labels = [reel_label(it) for it in items]
        start = 0
        while start < sel and sum(text_width(labels[i]) + 1 for i in range(start, sel)) \
                >= region.w // 2:
            start += 1
        sel_x_end = 0
        for i in range(start, len(items)):
            label = labels[i]
            w = text_width(label)
            if x + w >= region.x + region.w - 1:
                break
            style = base_style
            if i == sel:
                style = sty.selected() if active else sty.text("fg-0", bg_token="bg-2")
                sel_x_end = x + w
            if reel["armed"] == items[i]:
                style = Style(sty.c("seal"), sty.c("bg-1"), frozenset({"bold"}))
            x += buf.write(x, y, label, style, max_width=max(0, region.x + region.w - x)) + 1
        # 右侧:选中版的 diff 摘要(随行;LLM 摘要 / 行差集红绿行)
        if active and sel < len(items):
            diff = self._diff_for(items[sel])
            dx = max(sel_x_end + 2, region.x + region.w // 2)
            for tag, text in diff:
                if dx >= region.x + region.w - 2:
                    break
                style = {"+": sty.text("ok", bg_token="bg-1"),
                         "-": sty.text("danger", bg_token="bg-1")}.get(
                             tag, sty.text("fg-1", bg_token="bg-1"))
                piece = (f"+{text}" if tag == "+" else f"-{text}" if tag == "-" else text)
                dx += buf.write(dx, y, piece + " ", style,
                                max_width=max(0, region.x + region.w - 1 - dx))

    def _render_tree(self, buf: CellBuffer, region: Region) -> None:
        """版本树覆盖层(ASCII 分支图;选中行反色;Enter = 关层 + 版本条定位)。"""
        rows = tree_rows_text(build_tree_rows(self.state["versions"]))
        if not rows:
            return
        sel = min(int(self.state["tree"]["sel"]), len(rows) - 1)
        w = min(region.w, max(text_width(t) for t, _v in rows) + 6)
        h = min(region.h, len(rows) + 2)
        x0 = region.x + max(0, (region.w - w) // 2)
        y0 = region.y + max(0, (region.h - h) // 2)
        buf.blank(x0, y0, w, h, sty.panel())
        buf.write(x0 + 2, y0, "版本树", sty.text("fg-0", bg_token="bg-1", bold=True),
                  max_width=w - 4)
        for i, (text, _vid) in enumerate(rows[: h - 2]):
            style = sty.selected() if i == sel else sty.text("fg-0", bg_token="bg-1")
            buf.write(x0 + 1, y0 + 1 + i, text, style, max_width=w - 2)

    def _render_status(self, buf: CellBuffer, region: Region, screen: str) -> None:
        y = region.y + region.h - 1
        style = sty.status_bar()
        buf.fill(region.x, y, region.w, 1, style)
        if self.state["command"]["active"]:
            prompt = (self.engine.pack.messages.get("command.prompt", ":") if self.engine else ":")
            buf.write(region.x, y, prompt + self.state["command"]["text"], style)
            return
        status = str(self.state.get("status") or "")
        # 右格:光标 行:列 + 选区宽(letter 屏常驻,位置反馈优先)在前,
        # keymap 生成的 hint 摘要随后;挂起前缀回显最前。状态消息在时 hint 让位。
        right = ""
        if self.engine is not None and not status:
            if self.state["dock"]["active"]:
                right = self.engine.hint_summary(
                    "dock", _DOCK_HINT_INTENTS, str(self.state.get("mode", "normal")))
            elif self.state["reel"]["active"]:
                right = self.engine.hint_summary("letter", _REEL_HINT_INTENTS)
            else:
                right = self.engine.hint_summary(screen, _HINT_INTENTS)
            pending = self.engine.pending_display
            if pending:
                right = f"{pending}…  " + right
        if screen == "letter" and not self.state["dock"]["active"]:
            lw = self.letter_widget()
            line, col = lw.cursor_line_col()
            pos = f"{line}:{col}"
            sel = lw.selection()
            if sel:
                pos += f" ·选{sel[1] - sel[0]}字"
            right = f"{pos}   {right}" if right else pos
        right_w = text_width(right)
        # 左格:vim 模式指示(emacs 包无,§5.2 copy 义务)/ 状态消息(优先)/
        # 文档名 · 版本(截断先吃文档名,位置反馈在右格)
        left = ""
        pack = KeymapRegistry.current()
        if pack is not None and pack.modal:
            mode = str(self.state.get("mode", "normal"))
            left = f" {pack.messages.get('mode.insert' if mode == 'insert' else 'mode.normal', 'NORMAL')} ─"
        if status:
            left += f" {status}"
        elif screen == "letter":
            lw = self.letter_widget()
            left += f" {lw.state.get('title', '')} · {lw.state.get('version', '') or '工作稿'}"
        left_max = max(0, region.w - right_w - 2) if right else region.w - 1
        buf.write(region.x, y, left, style, max_width=left_max)
        if right:
            buf.write(region.x + max(0, region.w - right_w - 1), y, right, style)
        # stamp-press(T3 真实现):盖章回声定格 ~300ms(seal 色 + 文字章,
        # 居中覆盖状态栏;动画只随成功播,定格期恒亮不闪)
        stamps = self.motion.active_stamps() if self.motion else []
        if stamps:
            text = stamps[-1]
            buf.write(region.x + max(0, (region.w - text_width(text)) // 2), y, text,
                      Style(sty.c("paper-0"), sty.c("seal"), frozenset({"bold"})))

    def _render_help(self, buf: CellBuffer, region: Region) -> None:
        """? 帮助面板:从活动 keymap 生成(§5.1),居中覆盖。"""
        if self.engine is None:
            return
        screen = str(self.state.get("screen", "index"))
        mode = str(self.state.get("mode", "normal"))
        context = "dock" if self.state["dock"]["active"] else screen
        rows = self.engine.help_lines(context, mode)
        key_w = max((text_width(k) for k, _t in rows), default=6)
        content_w = max((key_w + 2 + text_width(t) for _k, t in rows), default=10)
        title = sty.copy("doc.help.title")
        content_w = max(content_w, text_width(title))
        w = min(region.w, content_w + 4)
        h = min(region.h, len(rows) + 2)
        x0 = region.x + max(0, (region.w - w) // 2)
        y0 = region.y + max(0, (region.h - h) // 2)
        panel = sty.panel()
        buf.blank(x0, y0, w, h, panel)  # 覆盖层:先抹下层内容再写
        buf.write(x0 + 2, y0, title, sty.text("fg-0", bg_token="bg-1", bold=True),
                  max_width=w - 4)
        for i, (key, label) in enumerate(rows[: h - 2]):
            buf.write(x0 + 2, y0 + 1 + i, key, sty.text("fg-0", bg_token="bg-1"), max_width=key_w)
            buf.write(x0 + 2 + key_w + 2, y0 + 1 + i, label, panel,
                      max_width=max(0, w - 4 - key_w - 2))

    # ------------------------------------------------------------------

    def context_fragment(self) -> dict[str, Any]:
        return {"kind": self.get_kind(), "app": "doc_editor",
                "screen": self.state.get("screen", "index")}

    def summary(self) -> str:
        screen = self.state.get("screen", "index")
        if screen == "letter":
            return self.letter_widget().summary()
        return self.index_widget().summary()


# ---------------------------------------------------------------------------
# 装配(host 接线:数据源/键位/动效注入;__main__ 与无头测试共用)
# ---------------------------------------------------------------------------

def build_registries() -> None:
    """内置主题/键位包注册(幂等;契约校验在注册面)。"""
    if ThemeRegistry.current() is None:
        register_themes()
    if KeymapRegistry.current() is None:
        register_keymaps()


def build_app(
    source: DocSource,
    *,
    keymap_id: str = "",
    reduced_motion: bool = False,
    tree: WidgetTree | None = None,
) -> tuple[WidgetTree, DocEditorApp, KeymapEngine, MotionPlayer]:
    """装配一棵可跑的树:注册表 → tree → app(slots)→ 数据源/引擎接线。
    返回 (tree, app, engine, motion);测试拿 app.feed_key + render 做无头冒烟。"""
    build_registries()
    pack = KeymapRegistry.current()
    if keymap_id:
        KeymapRegistry.apply(keymap_id)
        pack = KeymapRegistry.current()
    assert pack is not None
    ThemeRegistry.reduced_motion = reduced_motion
    engine = KeymapEngine(pack)
    motion = MotionPlayer()
    registry = WidgetRegistry(required_intents=list(pack.intents()))
    for def_ in (IndexDef(), LetterDef(), InputDef(), DocEditorDef()):
        registry.register(def_)
    if tree is None:
        tree = WidgetTree(motion=motion)
    app = registry.create("tui.doc_editor", {})
    assert isinstance(app, DocEditorApp)
    tree.root.add_child_widget(app, "doc-editor")
    app.create_slots(registry)
    app.source = source
    app.engine = engine
    app.motion = motion
    app.wire()
    # T3:rewind 走 action 管道(§1 五口;Online 源注入 pipeline,cascade 要 tree)
    from agent_os.host.tui.apps.doc_editor.model import OnlineDocSource
    from agent_os.host.tui.kernel.pipeline import ActionPipeline
    if isinstance(source, OnlineDocSource):
        source.attach_pipeline(ActionPipeline(source.client, tree))
    app.reload_index()
    tree.focus_target = _focus_target(app)
    return tree, app, engine, motion


def _focus_target(app: DocEditorApp):
    """focus 动词的宿主回调(§3:滚动 + 反色脉冲;目标 = 当前屏 widget)。"""
    def scroll_to(w: Widget) -> None:
        if w is app.letter_widget():
            app.mutate_state(lambda st: st.update(screen="letter"))
    return scroll_to
