class_name Widget
extends RefCounted

## Widget 实例基类(docs/WIDGETS.md §1.2/§1.3 三条铁律):
## 有状态(state 可序列化 Dictionary)、发事件(emit_event 上行,永不直接调后端)、
## 四态齐备由主题层保证。render() 是 state → 视图的纯渲染:先清后建,幂等可重入。

signal widget_event(source: Widget, evt_name: String, payload: Dictionary)

## 视图层钩子:重渲完成广播(场景/面板等宿主层据此同步;widget 间联动仍走事件)
signal state_changed(source: Widget)

static var _seq: int = 0

var def: WidgetDef
var id: String
var state: Dictionary
var path: String = ""           # /root/... 全树唯一(docs/APP-MODEL.md §14)
var path_segment: String = ""   # owner 路径下的段(slot id 或实例 id)
var surface: String = "tab"     # "card" | "tab"(当前面孔)
var owner: CompoundWidget = null
var root: Control               # 视图根(UI 控件树;widget 协议不假设渲染层)


func _init(p_def: WidgetDef, p_state: Dictionary) -> void:
	def = p_def
	Widget._seq += 1
	id = "%s-%04x" % [def.get_kind(), Widget._seq]
	path_segment = id
	state = p_state.duplicate(true)
	# root 用 PanelContainer(透明面板):容器逻辑把子件铺满——不依赖锚点
	# (锚点在"容器托管的 widget root"内不生效,列表/预览塌高实锤教训)
	root = PanelContainer.new()
	root.add_theme_stylebox_override("panel", StyleBoxEmpty.new())
	root.name = "%s#%s" % [def.get_kind(), id]


func get_kind() -> String:
	return def.get_kind()


## 整态替换 + 重渲
func set_state(next: Dictionary) -> void:
	state = next.duplicate(true)
	render()


## 就地改 state + 重渲
func patch_state(mutator: Callable) -> void:
	mutator.call(state)
	render()


## 静默改 state 不重渲(文本输入等高频路径用,防重建丢焦点)
func mutate_state(mutator: Callable) -> void:
	mutator.call(state)


## 事件上行:校验已声明(未声明事件不发,对齐 widgets/registry.js 语义),
## 经 owner 事件闸门(COMPOUND-WIDGET.md §7 通道 1),放行后才到订阅者。
func emit_event(evt_name: String, payload: Dictionary = {}) -> bool:
	if not def.get_events().has(evt_name):
		push_error("%s 未声明事件: %s" % [get_kind(), evt_name])
		return false
	if owner != null:
		owner.dispatch_child_event(self, evt_name, payload)
	else:
		widget_event.emit(self, evt_name, payload)
	return true


## 闸门放行后由父调起(勿直接用)
func raise_direct(evt_name: String, payload: Dictionary) -> void:
	widget_event.emit(self, evt_name, payload)


## state → root 的渲染。约定:先清再建,可从任意 state 重建。
func render() -> void:
	pass


## context_provider(docs/APP-MODEL.md §16):本 widget 贡献给 cascade 的 fragment。
## 缺省 = kind + 人话摘要;声明者覆盖。父可经 child_context 改写。
func context_fragment() -> Dictionary:
	return {"kind": get_kind(), "summary": summary()}


## read 动词的人话摘要(卡面禁忌词纪律由具体 widget 自律)
func summary() -> String:
	return get_kind()


func destroy() -> void:
	if is_instance_valid(root):
		root.queue_free()
