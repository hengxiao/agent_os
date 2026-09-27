class_name DocListDef
extends WidgetDef

## doc-list 定义面(W-list 语义:过滤/选择/激活)。


func get_kind() -> String:
	return "doc-list"


func get_events() -> Array:
	return ["select", "create"]


func create_instance(state: Dictionary) -> Widget:
	return DocListWidget.new(self, state)


class DocListWidget:
	extends Widget

	## 文档列表(Card Surface 的数据源面):搜索过滤 + 选择 + 新建入口。
	## state:{items:[{name,title,first_line,chars,savedAt}], filter, selected, creating, draft}
	## 渲染分两档:render() 重建 chrome;_rebuild_items() 只重建列表项(过滤输入不丢焦点)。

	var _list_host: VBoxContainer


	func render() -> void:
		_clear(root)
		var vbox := Sty.vbox()
		root.add_child(vbox)
		vbox.set_anchors_preset(Control.PRESET_FULL_RECT)

		var filter := Sty.line_edit("过滤…")
		filter.text = state.get("filter", "")
		filter.text_changed.connect(func(t: String) -> void:
			mutate_state(func(s: Dictionary) -> void: s["filter"] = t)
			_rebuild_items()) # 只重渲列表区,过滤框焦点不动
		vbox.add_child(filter)

		if state.get("creating", false):
			var row := Sty.hbox()
			var name_input := Sty.line_edit("文档名(点分,如 design.new-ui)")
			name_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			name_input.text = state.get("draft", "")
			name_input.text_changed.connect(func(t: String) -> void:
				mutate_state(func(s: Dictionary) -> void: s["draft"] = t))
			row.add_child(name_input)
			var create := Sty.btn(Sty.copy("doc.create"), true)
			create.pressed.connect(func() -> void:
				var doc_name: String = str(state.get("draft", "")).strip_edges()
				if doc_name.is_empty():
					return
				mutate_state(func(s: Dictionary) -> void:
					s["creating"] = false
					s["draft"] = "")
				emit_event("create", {"name": doc_name}))
			row.add_child(create)
			vbox.add_child(row)
		else:
			var add := Sty.btn(Sty.copy("doc.new"))
			add.pressed.connect(func() -> void:
				patch_state(func(s: Dictionary) -> void: s["creating"] = true))
			vbox.add_child(add)
		vbox.add_child(Sty.hline())

		var sc := Sty.scroll()
		vbox.add_child(sc)
		_list_host = Sty.vbox()
		_list_host.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_list_host.custom_minimum_size.x = 196 # ScrollContainer 不撑宽子件;左栏定宽,取定值
		sc.add_child(_list_host)
		_rebuild_items()


	func _rebuild_items() -> void:
		if _list_host == null:
			return
		_clear(_list_host)
		var items: Array = state.get("items", [])
		var filter_text: String = str(state.get("filter", "")).to_lower()
		var shown := 0
		for it in items:
			if not it is Dictionary:
				continue
			var doc_name: String = str(it.get("name", ""))
			if not filter_text.is_empty() and not doc_name.to_lower().contains(filter_text):
				continue
			shown += 1
			var row := Sty.panel("bg-3" if state.get("selected", "") == doc_name else "bg-1")
			row.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
			var vb := Sty.vbox()
			row.add_child(vb)
			vb.add_child(Sty.label(str(it.get("title", doc_name)), "text-sm", "fg-0", true))
			var first_line: String = str(it.get("first_line", ""))
			if not first_line.is_empty():
				vb.add_child(Sty.label(first_line, "text-xs", "fg-2"))
			var meta := "%d 字" % int(it.get("chars", 0))
			var saved_at := float(it.get("savedAt", 0))
			if saved_at > 0:
				meta += " · " + _rel_time(saved_at)
			vb.add_child(Sty.label(meta, "text-xs", "fg-2"))
			var captured := doc_name
			row.gui_input.connect(func(event: InputEvent) -> void:
				if event is InputEventMouseButton \
					and event.button_index == MOUSE_BUTTON_LEFT and event.pressed:
					mutate_state(func(s: Dictionary) -> void: s["selected"] = captured)
					_rebuild_items()
					emit_event("select", {"name": captured})
					row.accept_event())
			_list_host.add_child(row)
		if shown == 0:
			_list_host.add_child(Sty.label(Sty.copy("doc.list.empty"), "text-sm", "fg-2"))


	static func _rel_time(epoch_sec: float) -> String:
		var span := Time.get_unix_time_from_system() - epoch_sec
		if span < 60:
			return "刚刚"
		if span < 3600:
			return "%d 分钟前" % int(span / 60)
		if span < 86400:
			return "%d 小时前" % int(span / 3600)
		return "%d 天前" % int(span / 86400)


	func summary() -> String:
		return "文档列表(%d 篇)" % (state.get("items", []) as Array).size()


	static func _clear(node: Node) -> void:
		for c in node.get_children():
			node.remove_child(c)
			c.queue_free()
