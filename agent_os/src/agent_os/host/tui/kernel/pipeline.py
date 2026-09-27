"""ActionPipeline(docs/TUI-DOC.md §3;对译 godot/kernel/action_pipeline.gd)。

Action 管道客户端(docs/APP-MODEL.md §4):用户点击(任何面孔)→ 只发事件
(action_id + args_input 载荷,不发 state)→ POST /apps/{id}/actions/{action}。
manifest 裁决/args_from 服务端绑定/schema 校验全部在服务端;trigger 非空时
自动携带 §16 级联信封(action 声明 context: [] 弃权是服务端语义,客户端不裁剪)。

T1 纪律:invoke 允许存在但本期无调用方(写面五个口都在 T2+);翻译是为接口
不漂移。godot 侧 invoke 是 async,Python 侧 httpx 同步(本宿主无事件循环)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_os.host.tui.kernel.client import AgentOsClient
    from agent_os.host.tui.kernel.tree import WidgetTree
    from agent_os.host.tui.kernel.widget import Widget


class AppInstance:
    """app instance(docs/APP-MODEL.md §2 AppInstance):state 服务端权威,
    客户端只镜像、只发事件不发状态(§1.2-3)。"""

    def __init__(self) -> None:
        self.id: str = ""
        self.kind: str = ""
        self.ref: str = ""
        self.title: str = ""
        self.state: dict[str, Any] = {}

    @staticmethod
    def from_json(j: dict[str, Any]) -> AppInstance:
        app = AppInstance()
        app.mirror_from(j)
        return app

    def mirror_from(self, j: dict[str, Any]) -> None:
        """从服务端响应镜像最新 state(管道响应里的 instance 面)。"""
        self.id = str(j.get("id", self.id))
        self.kind = str(j.get("kind", self.kind))
        self.ref = str(j.get("ref", self.ref))
        self.title = str(j.get("title", self.title))
        if isinstance(j.get("state"), dict):
            self.state = j["state"]


class ActionPipeline:
    def __init__(self, client: AgentOsClient, tree: WidgetTree) -> None:
        self._client = client
        self._tree = tree

    def invoke(
        self,
        app: AppInstance,
        action_id: str,
        args_input: dict[str, Any],
        surface: str,
        session_id: str = "",
        trigger: Widget | None = None,
    ) -> dict[str, Any]:
        """返回 AgentOsClient 的响应信封 {ok, status, json|error};
        ok 时把响应里的 instance 镜像回 app(state 服务端权威)。"""
        body: dict[str, Any] = {
            "surface": surface if surface else "tab",
            "args": args_input,
        }
        if session_id:
            body["session_id"] = session_id
        if trigger is not None:
            body["cascade"] = self._tree.cascade.build(trigger)
        resp = self._client.invoke_action(app.id, action_id, body)
        if resp.get("ok", False) and isinstance(resp.get("json"), dict):
            inst = resp["json"].get("instance")
            if isinstance(inst, dict):
                app.mirror_from(inst)
        return resp
