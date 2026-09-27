extends SceneTree

## 活端集成冒烟(需要本地后端在跑):
##   instance/run-web.sh &  # 先起服务
##   godot --headless --path godot/ -s res://tests/live.gd
## 只打确定性端点(list/create/read/spawn/save/snapshot/rewind);
## LLM 面(comment/chat/review)不在此——凭证 15 分钟过期,契约即文档。

const DOC := "godot.smoke.e2e"
const TEXT_V1 := "# 冒烟标题\n\n第一段原文\n"
const TEXT_V2 := "# 冒烟标题\n\n第一段改写\n"

var _failures := 0
var _started := false
var _client: AgentOsClient
var _pipeline: ActionPipeline
var _tree: WidgetTree


## 注意:SceneTree 脚本模式下 _initialize 阶段 root 尚未就绪(HTTPRequest 会
## 挂死);推迟到首帧再装配——此时树已迭代,HTTPRequest 正常工作。
func _process(_delta: float) -> bool:
	if _started:
		return false
	_started = true
	_client = AgentOsClient.new()
	get_root().add_child(_client)
	_tree = WidgetTree.new()
	_pipeline = ActionPipeline.new(_client, _tree)
	_run() # async,帧持续迭代驱动 HTTPRequest
	return false


func check(label: String, cond: bool) -> void:
	if cond:
		print("PASS ", label)
	else:
		_failures += 1
		printerr("FAIL ", label)


func _run() -> void:
	print("== Agent OS Godot 活端集成(%s)==" % _client.base_url)

	check("ping", await _client.ping())

	# 已存在(409)不挡:读得到就算在
	var created := await _client.create_doc(DOC, "", TEXT_V1)
	if not created.get("ok", false) and int(created.get("status", 0)) != 409:
		check("create_doc", false)
		printerr("创建失败:", created.get("error", ""))
		quit(1)
		return
	check("create_doc(或已存在 409)", true)

	var doc := await _client.read_doc(DOC)
	check("read_doc", doc.get("ok", false) and doc["json"] is Dictionary)

	var spawned := await _client.spawn("doc", DOC, DOC, {"name": DOC}, "godot-e2e")
	check("spawn", spawned.get("ok", false) and spawned.has("app"))
	if not spawned.has("app"):
		quit(1)
		return
	var app: ActionPipeline.AppInstance = spawned["app"]
	check("spawn 去重复用", app.id.length() > 0)

	# 密封流(重跑不受残留影响):先落成已知 v1 → 快照 → 记下新版本号
	var save1 := await _pipeline.invoke(app, "doc.save", {"text": TEXT_V1}, "tab", "", null)
	check("doc.save(v1) 走管道", save1.get("ok", false))
	var before := await _client.read_doc(DOC)
	var before_versions: Array = (before.get("json") as Dictionary).get("versions", [])
	var snap := await _pipeline.invoke(app, "doc.snapshot", {}, "tab", "", null)
	check("doc.snapshot", snap.get("ok", false))
	var after := await _client.read_doc(DOC)
	var after_versions: Array = (after.get("json") as Dictionary).get("versions", [])
	var new_version := ""
	for v in after_versions:
		if not before_versions.has(v):
			new_version = str(v)
			break
	check("快照产生新版本", not new_version.is_empty())

	var saved := await _pipeline.invoke(app, "doc.save", {"text": TEXT_V2}, "tab", "", null)
	check("doc.save(v2) 走管道", saved.get("ok", false))
	var doc2 := await _client.read_doc(DOC)
	check("保存后读回一致", str((doc2.get("json") as Dictionary).get("text", "")) == TEXT_V2)

	var rew := await _pipeline.invoke(app, "doc.rewind", {"version": new_version}, "tab", "", null)
	check("doc.rewind", rew.get("ok", false))
	var doc4 := await _client.read_doc(DOC)
	check("回滚恢复快照原文", str((doc4.get("json") as Dictionary).get("text", "")) == TEXT_V1)

	# 越权构造反面例:args 塞 state 字段名(args_from 是服务端权威)应被 400 拒
	var forged := await _client.invoke_action(app.id, "doc.save",
		{"surface": "tab", "args": {"name": "evil.doc", "text": "x"}})
	check("伪装 args_from 字段被拒(400 面)", not forged.get("ok", false)
		and int(forged.get("status", 0)) == 400)

	if _failures == 0:
		print("== 活端全部通过 ==")
	else:
		printerr("== %d 项失败 ==" % _failures)
	quit(0 if _failures == 0 else 1)
