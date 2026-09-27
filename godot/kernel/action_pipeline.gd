class_name ActionPipeline
extends RefCounted

## Action 管道客户端(docs/APP-MODEL.md §4):用户点击(任何面孔)→ 只发事件
## (action_id + args_input 载荷,不发 state)→ POST /apps/{id}/actions/{action}。
## manifest 裁决/args_from 服务端绑定/schema 校验全部在服务端;trigger 非空时
## 自动携带 §16 级联信封(action 声明 context: [] 弃权是服务端语义,客户端不裁剪)。

var _client: AgentOsClient
var _tree: WidgetTree


func _init(client: AgentOsClient, tree: WidgetTree) -> void:
	_client = client
	_tree = tree


## 返回 AgentOsClient 的响应信封 {ok, status, json|error};
## ok 时把响应里的 instance 镜像回 app(state 服务端权威)。
func invoke(
	app: AppInstance,
	action_id: String,
	args_input: Dictionary,
	surface: String,
	session_id := "",
	trigger: Widget = null
) -> Dictionary:
	var body := {
		"surface": surface if not surface.is_empty() else "tab",
		"args": args_input,
	}
	if not session_id.is_empty():
		body["session_id"] = session_id
	if trigger != null:
		body["cascade"] = _tree.cascade.build(trigger)
	var resp: Dictionary = await _client.invoke_action(app.id, action_id, body)
	if resp.get("ok", false) and resp.get("json") is Dictionary:
		var inst = resp["json"].get("instance")
		if inst is Dictionary:
			app.mirror_from(inst)
	return resp


## app instance(docs/APP-MODEL.md §2 AppInstance):state 服务端权威,
## 客户端只镜像、只发事件不发状态(§1.2-3)。
class AppInstance:
	extends RefCounted

	var id := ""
	var kind := ""
	var ref := ""
	var title := ""
	var state: Dictionary = {}

	static func from_json(j: Dictionary) -> AppInstance:
		var app := AppInstance.new()
		app.mirror_from(j)
		return app

	## 从服务端响应镜像最新 state(管道响应里的 instance 面)
	func mirror_from(j: Dictionary) -> void:
		id = str(j.get("id", id))
		kind = str(j.get("kind", kind))
		ref = str(j.get("ref", ref))
		title = str(j.get("title", title))
		if j.get("state") is Dictionary:
			state = j["state"]
