class_name ContextCascade
extends RefCounted

## Context Cascade(docs/APP-MODEL.md §16):动作触发时从触发 widget 沿 ownership 树
## 向上逐级收集 fragment,组成级联信封。纪律:每级只贡献自己的 fragment;
## 级联单向向上(祖先链),不横向打听——上下文边界 = 树边界;
## 父可经 child_context 改写子的 fragment(信息管控,COMPOUND §7 通道 2)。
## scope 规则:root = "shell";root 的直接子 = "app";中间层 = "section";触发者 = "widget"。

var _tree: WidgetTree


func _init(tree: WidgetTree) -> void:
	_tree = tree


func build(trigger: Widget) -> Array:
	var entries: Array = []
	if trigger == null:
		return entries
	entries.append(_entry("widget", trigger))
	var o := trigger.owner
	while o != null:
		entries.append(_entry(_scope_of(o), o))
		o = o.owner
	return entries


func _scope_of(w: Widget) -> String:
	if w == _tree.root:
		return "shell"
	if w.owner == _tree.root:
		return "app"
	return "section"


func _entry(scope: String, w: Widget) -> Dictionary:
	var frag := w.context_fragment()
	if w.owner != null:
		frag = w.owner.child_context(w, frag) # 信息管控:父改写子对外提供的信息
	return {
		"scope": scope,
		"path": w.path,
		"data": frag,
	}
