class_name WidgetDef
extends RefCounted

## Widget 协议定义面(docs/WIDGETS.md §1.2 的 WidgetDef;APP-MODEL.md §2 同哲学):
## kind / v / actions / events / surfaces + 实例工厂。widget 是代码资产,
## 注册进 WidgetRegistry(注册即校验,不合规拒注册)。


func get_kind() -> String:
	return ""


func get_v() -> int:
	return 1


## action 声明面:[{id, exec, ref, args_input[], surfaces[]}]
## exec: "local" | "endpoint" | "run"(APP-MODEL §4 三态;客户端语义 = 要不要出海)
func get_actions() -> Array:
	return []


func get_events() -> Array:
	return []


func get_surfaces() -> Array:
	return ["card", "tab"]


## 实例工厂。state 必须可 JSON 序列化(Dictionary;刷新/重渲染可恢复)。
func create_instance(_state: Dictionary) -> Widget:
	return null
