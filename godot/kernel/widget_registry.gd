class_name WidgetRegistry
extends RefCounted

## Widget 注册表(docs/WIDGETS.md §1.2 / widgets/registry.js):注册即校验,
## 不合规拒注册(返回 false + push_error;注册面是代码评审面)。

const KNOWN_SURFACES := ["card", "tab"]

var _defs: Dictionary = {}


func register(def: WidgetDef) -> bool:
	if def == null or def.get_kind().is_empty():
		push_error("widget def 缺 kind,拒注册")
		return false
	if def.get_v() < 1:
		push_error("%s: v 必须 >= 1" % def.get_kind())
		return false
	var action_ids := {}
	for a in def.get_actions():
		if not a is Dictionary or not a.has("id") or str(a["id"]).is_empty():
			push_error("%s: action 缺 id,拒注册" % def.get_kind())
			return false
		if action_ids.has(a["id"]):
			push_error("%s: action id 重复 %s" % [def.get_kind(), a["id"]])
			return false
		action_ids[a["id"]] = true
		if not _surfaces_ok(def.get_kind(), a.get("surfaces", KNOWN_SURFACES)):
			return false
	for e in def.get_events():
		if not e is String or e.is_empty():
			push_error("%s: events 含空项,拒注册" % def.get_kind())
			return false
	if not _surfaces_ok(def.get_kind(), def.get_surfaces()):
		return false
	_defs[def.get_kind()] = def
	return true


func _surfaces_ok(kind: String, surfaces: Array) -> bool:
	for s in surfaces:
		if not KNOWN_SURFACES.has(s):
			push_error("%s: surface %s 越界(只允许 card/tab)" % [kind, s])
			return false
	return true


func get_def(kind: String) -> WidgetDef:
	return _defs.get(kind)


## 无 def 的 kind 拒绝创建(显式 null,与"拒绝渲染"对齐)
func create(kind: String, state: Dictionary) -> Widget:
	var def := get_def(kind)
	if def == null:
		push_error("未注册的 widget kind: " + kind)
		return null
	return def.create_instance(state)


func kinds() -> Array:
	return _defs.keys()
