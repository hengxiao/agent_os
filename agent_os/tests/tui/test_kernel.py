"""协议内核语义测试(docs/TUI-DOC.md §3;对译面的行为锚点)。

锚住对译的协议语义,防漂移:/root 寻址、事件闸门吞/放、badge 记账、
cascade 单向向上(scope 序列)、管道 invoke 镜像、统一信封、SSE 帧解析。
"""

from __future__ import annotations

from typing import Any

from agent_os.host.tui.kernel.client import AgentOsClient
from agent_os.host.tui.kernel.compound import CompoundWidget
from agent_os.host.tui.kernel.pipeline import ActionPipeline, AppInstance
from agent_os.host.tui.kernel.registry import WidgetRegistry
from agent_os.host.tui.kernel.sse import SseClient
from agent_os.host.tui.kernel.tree import WidgetTree
from agent_os.host.tui.kernel.widget import Widget
from agent_os.host.tui.kernel.widget_def import WidgetDef


class _LeafDef(WidgetDef):
    def get_kind(self) -> str:
        return "leaf"

    def get_events(self) -> list[str]:
        return ["ping"]

    def create_instance(self, state: dict[str, Any]) -> Widget:
        return Widget(self, state)


class _BoxDef(WidgetDef):
    def __init__(self, kind: str = "box", gate: bool = True) -> None:
        self._kind = kind
        self._gate = gate

    def get_kind(self) -> str:
        return self._kind

    def create_instance(self, state: dict[str, Any]) -> Widget:
        box = CompoundWidget(self, state)
        box.on_child_event = lambda _c, _e, _p: self._gate  # type: ignore[method-assign]
        return box


def _tree_with(registry: WidgetRegistry) -> tuple[WidgetTree, Any]:
    tree = WidgetTree()
    app = registry.create("box", {})
    tree.root.add_child_widget(app, "app")
    app.create_slots(registry)
    return tree, app


def test_resolve_and_read_summary():
    reg = WidgetRegistry()
    reg.register(_BoxDef())
    reg.register(_LeafDef())
    tree, app = _tree_with(reg)
    leaf = reg.create("leaf", {})
    app.add_child_widget(leaf, "para-0")
    assert tree.resolve("/root/app/para-0") is leaf
    assert tree.resolve("root/app/para-0") is leaf  # 前导斜杠可有可无
    assert tree.resolve("/root/app/ghost") is None
    assert tree.resolve("/etc/passwd") is None
    assert leaf.path == "/root/app/para-0"
    assert tree.read_summary("/root/app/para-0") == "leaf"


def test_event_gate_swallow_and_badge():
    reg = WidgetRegistry()
    gate_open = {"v": True}

    class GateDef(_BoxDef):
        def create_instance(self, state: dict[str, Any]) -> Widget:
            box = CompoundWidget(self, state)
            box.on_child_event = lambda _c, _e, _p: gate_open["v"]  # type: ignore[method-assign]
            return box

    reg.register(GateDef())
    reg.register(_LeafDef())
    _tree, app = _tree_with(reg)
    leaf = reg.create("leaf", {})
    app.add_child_widget(leaf, "para-0")
    seen: list[tuple[str, dict[str, Any]]] = []
    app.on_child_event_signal(lambda c, e, p: seen.append((e, dict(p))))

    # 闸门关 → 吞掉,不进 badge 账
    gate_open["v"] = False
    assert leaf.emit_event("ping", {"badge": 3})  # 声明过 → True;闸门吞掉是父的事
    assert seen == []
    assert "badges" not in app.state

    # 闸门开 → 放行 + badge 记账(number 记 / 0·None 摘)
    gate_open["v"] = True
    leaf.emit_event("ping", {"badge": 3})
    assert app.state["badges"][leaf.id] == 3
    leaf.emit_event("ping", {"badge": 0})
    assert leaf.id not in app.state["badges"]
    assert seen[0][0] == "ping"


def test_undeclared_event_not_emitted():
    reg = WidgetRegistry()
    reg.register(_LeafDef())
    leaf = reg.create("leaf", {})
    assert not leaf.emit_event("teleport")  # 未声明事件不发


def test_cascade_build_upward_only():
    """cascade 单向向上:widget → app → shell 的 scope 序列。"""
    reg = WidgetRegistry()
    reg.register(_BoxDef())
    reg.register(_LeafDef())
    tree, app = _tree_with(reg)
    leaf = reg.create("leaf", {})
    app.add_child_widget(leaf, "para-0")
    entries = tree.cascade.build(leaf)
    assert [e["scope"] for e in entries] == ["widget", "app", "shell"]
    assert entries[0]["path"] == "/root/app/para-0"
    assert entries[-1]["path"] == "/root"
    assert tree.cascade.build(None) == []


def test_pipeline_invoke_mirrors_instance():
    """invoke 只发事件不发 state;ok 时镜像 instance 回 app(state 服务端权威)。"""
    sent: list[tuple[str, str, dict[str, Any]]] = []

    class FakeClient:
        def invoke_action(self, instance_id: str, action_id: str, body: dict[str, Any]) -> dict[str, Any]:
            sent.append((instance_id, action_id, body))
            return {"ok": True, "status": 200,
                    "json": {"instance": {"id": "a1", "kind": "doc", "ref": "r",
                                          "title": "t", "state": {"v": 2}}}}

    tree = WidgetTree()
    pipe = ActionPipeline(FakeClient(), tree)  # type: ignore[arg-type]
    app = AppInstance.from_json({"id": "a1", "kind": "doc", "ref": "r", "title": "t", "state": {"v": 1}})
    reg = WidgetRegistry()
    reg.register(_LeafDef())
    trigger = reg.create("leaf", {})
    tree.root.add_child_widget(trigger, "t")
    resp = pipe.invoke(app, "save", {"x": 1}, "card", trigger=trigger)
    assert resp["ok"]
    assert app.state == {"v": 2}  # 镜像回写
    body = sent[0][2]
    assert body["surface"] == "card" and body["args"] == {"x": 1}
    assert body["cascade"][0]["scope"] == "widget"  # trigger 非空自动带级联信封
    assert "state" not in body  # 只发事件不发 state


def test_client_envelope_error_unpack():
    """统一信封:{detail} 拆包 / 4xx 归 error 面(无网络,直测拆包函数)。"""
    assert AgentOsClient._extract_detail('{"detail": "nope"}', 404) == "404: nope"
    assert AgentOsClient._extract_detail("plain", 500) == "500: plain"
    assert AgentOsClient.json_or({"ok": True, "json": [1]}, []) == [1]
    assert AgentOsClient.json_or({"ok": False}, "fb") == "fb"


def test_sse_frame_parsing():
    """event:/data: 帧解析;keepalive 注释帧忽略;坏帧丢弃(容错优先)。"""
    got: list[tuple[str, dict[str, Any]]] = []
    conn: list[bool] = []
    c = SseClient()
    c.event_received = lambda e, d: got.append((e, d))
    c.connection_changed = conn.append
    c.feed(": keepalive\n\n")
    assert got == []
    c.feed('event: decision.new\ndata: {"id": 1}\n\n')
    assert got == [("decision.new", {"id": 1})]
    c.feed('data: {"id": 2}\n\n')  # 缺省 message
    assert got[-1] == ("message", {"id": 2})
    c.feed("data: {broken\n\n")  # 坏帧丢弃
    assert len(got) == 2
    # 分块凑帧(跨块边界)
    c.feed('event: run.finished\nda')
    c.feed('ta: {"run": 3}\n\n')
    assert got[-1] == ("run.finished", {"run": 3})


def test_sse_stream_path_param():
    """D2 泛化(docs/TUI-DEBUG.md §4):stream_url 的 path 可换
    (调试会话流 /api/debug/sessions/{sid}/stream);缺省 /api/stream 不变;
    start 即上报未连上(proto 语义),stop 幂等。"""
    c = SseClient()
    conn: list[bool] = []
    c.connection_changed = conn.append
    c.start_stream("http://127.0.0.1:8391/")
    assert c.stream_url == "http://127.0.0.1:8391/api/stream"
    assert conn == [False]  # 先未连上;run_forever 连上后转 true
    c.start_stream("http://127.0.0.1:8391", path="/api/debug/sessions/s-1/stream")
    assert c.stream_url == "http://127.0.0.1:8391/api/debug/sessions/s-1/stream"
    assert conn == [False, False]  # 换流重来,仍先未连上
    c.stop_stream()
    assert not c._running


def test_focus_verb_pulse_and_scroll():
    """focus 动词(§3):路径解析 + 宿主滚动回调 + focus-pulse。"""
    from agent_os.host.tui.tui.motion import MotionPlayer
    from agent_os.host.tui.tui.theme import ThemeRegistry, classic_pack

    ThemeRegistry.reset()
    ThemeRegistry.register(classic_pack())
    motion = MotionPlayer()
    tree = WidgetTree(motion=motion)
    reg = WidgetRegistry()
    reg.register(_LeafDef())
    leaf = reg.create("leaf", {})
    tree.root.add_child_widget(leaf, "t")
    focused: list[Widget] = []
    tree.focus_target = focused.append
    assert not tree.focus("/root/ghost")
    assert tree.focus("/root/t")
    assert focused == [leaf]
    assert motion.busy()  # 反色脉冲记上帧
