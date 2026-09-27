class_name CompoundWidget
extends Widget

## 复合 widget 基座(docs/COMPOUND-WIDGET.md):拥有子 widget,定义组合、事件闸门、
## context 改写、动态生灭。ownership 唯一;寻址路径随挂载重算(reparent 安全)。
## v1 留口:多 view(hard link)与跨父转移(move_child)未实现,见 godot/README.md。

signal child_event(child: Widget, evt_name: String, payload: Dictionary)

var children: Array[Widget] = []


## 预定义子件清单(静态,随实例创建):[{id, kind, state?, surface?}]
func get_slots() -> Array:
	return []


## 动态子件 kind 白名单(空 = 不许动态)
func get_dynamic_allow() -> Array:
	return []


func get_dynamic_max() -> int:
	return 50


## 事件闸门(§7 通道 1):false 吞掉;true 放行(实现内可改写 payload)
func on_child_event(_child: Widget, _evt_name: String, _payload: Dictionary) -> bool:
	return true


## 信息管控(§7 通道 2):cascade 收集子 fragment 时经父改写(过滤/补充)
func child_context(_child: Widget, fragment: Dictionary) -> Dictionary:
	return fragment


## 按 slots 创建预定义子件(kind 惰性校验在 registry)
func create_slots(registry: WidgetRegistry) -> void:
	for slot in get_slots():
		var child := registry.create(slot["kind"], slot.get("state", {}))
		if child == null:
			continue
		child.surface = slot.get("surface", "tab")
		add_child_widget(child, slot["id"])


## 挂载子件(ownership 唯一 + 祖先链防环,§10)。slot_id 即路径段。
func add_child_widget(child: Widget, slot_id: String = "") -> Widget:
	if child == null:
		return null
	if child.owner != null:
		push_error("ownership 唯一:子件已有 owner(%s)" % child.owner.path)
		return null
	var p: Widget = self
	while p != null:
		if p == child:
			push_error("ownership 是树,不允许成环")
			return null
		p = p.owner
	if slot_id.is_empty():
		var allow := get_dynamic_allow()
		if allow.is_empty():
			push_error("%s 不许动态子件" % get_kind())
			return null
		if not allow.has(child.get_kind()):
			push_error("kind %s 不在 %s 的 dynamic.allow 白名单" % [child.get_kind(), get_kind()])
			return null
		if _count_dynamic() >= get_dynamic_max():
			push_error("%s 动态子件超上限 %d" % [get_kind(), get_dynamic_max()])
			return null
	child.owner = self
	child.path_segment = slot_id if not slot_id.is_empty() else child.id
	children.append(child)
	repath(child)
	return child


func _count_dynamic() -> int:
	var slot_ids := {}
	for s in get_slots():
		slot_ids[s["id"]] = true
	var n := 0
	for c in children:
		if not slot_ids.has(c.path_segment):
			n += 1 # 预定义不占动态额
	return n


## 移除子件。destroy=false = detach(instance 活着,可被别家 attach,§4)
func remove_child_widget(id_or_segment: String, destroy := true) -> Widget:
	for i in children.size():
		var child := children[i]
		if child.id == id_or_segment or child.path_segment == id_or_segment:
			children.remove_at(i)
			_clear_badge(child)
			child.owner = null
			if destroy:
				_destroy_rec(child)
			return child
	return null


func find_child(segment: String) -> Widget:
	for c in children:
		if c.path_segment == segment or c.id == segment:
			return c
	return null


func find_child_of(script_class, pred: Callable = Callable()) -> Widget:
	for c in children:
		if is_instance_of(c, script_class) and (not pred.is_valid() or pred.call(c)):
			return c
	return null


static func _destroy_rec(w: Widget) -> void:
	if w is CompoundWidget:
		for ch in w.children.duplicate():
			_destroy_rec(ch)
	w.destroy()


## 路径随 ownership 链重算,后代级联(C4.4 _repathSubtree 同语义)
static func repath(w: Widget) -> void:
	w.path = w.path_segment if w.owner == null else w.owner.path + "/" + w.path_segment
	if w is CompoundWidget:
		for ch in w.children:
			repath(ch)


## 事件闸门入口(子 emit_event 的必经路):
## 闸门 → badge 记账 → 订阅者 → 继续上行。
## badge 记账(COMPOUND §7 补丁):放行负载带 badge 字段(number 记 / 0·null 摘),
## 是父对子事件的记账,不是父偷读子 state。
func dispatch_child_event(child: Widget, evt_name: String, payload: Dictionary) -> void:
	if not on_child_event(child, evt_name, payload):
		return # 吞掉
	if payload.has("badge"):
		var badges: Dictionary = state.get("badges", {})
		var b = payload["badge"]
		if b == null or (b is int and b == 0):
			badges.erase(child.id)
		else:
			badges[child.id] = b
		state["badges"] = badges
	child.raise_direct(evt_name, payload)
	child_event.emit(child, evt_name, payload)
	if owner != null:
		owner.dispatch_child_event(child, evt_name, payload) # 沿树继续上行


func _clear_badge(child: Widget) -> void:
	var badges: Dictionary = state.get("badges", {})
	if badges.erase(child.id):
		state["badges"] = badges


func destroy() -> void:
	for ch in children.duplicate():
		_destroy_rec(ch)
	children.clear()
	super.destroy()
