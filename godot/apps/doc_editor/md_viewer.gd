class_name MdViewerDef
extends WidgetDef

## md-viewer 定义面(W-md 的 Godot 版:白名单渲染,转义先行)。


func get_kind() -> String:
	return "md-viewer"


func get_events() -> Array:
	return ["open-bubble"]


func create_instance(state: Dictionary) -> Widget:
	return MdViewerWidget.new(self, state)


class MdViewerWidget:
	extends Widget

	## Markdown 预览(W-md):按 MdBlocks 分块渲染;每块「注」锚点钮 + 右键开泡。
	## 白名单纪律:先整体转义再加工有限标记(标题/粗斜体/行内码/代码块/列表/表格行),
	## 不做原文注入。块内联气泡壳有意不做——气泡聚在 app 的批注栏,viewer 重渲
	## 不摘气泡宿主(web 侧 D2 弯腰点 ② 的结构性规避)。
	## state:{source, bubbleCounts:{anchor:n}, changed:[anchor]}
	## 注:默认字体链无 emoji 字形,批注标记用汉字「注」(web 的 💬)。

	func render() -> void:
		_clear(root)
		var source: String = state.get("source", "")
		var counts: Dictionary = state.get("bubbleCounts", {})
		var changed: Array = state.get("changed", [])

		var vbox := Sty.vbox()
		vbox.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		vbox.size_flags_vertical = Control.SIZE_EXPAND_FILL
		root.add_child(vbox)
		vbox.set_anchors_preset(Control.PRESET_FULL_RECT)

		if source.strip_edges().is_empty():
			vbox.add_child(Sty.label("(空文档 —— 在下方对话里说出你要什么)", "text-sm", "fg-2"))
			return

		for block in MdBlocks.split(source):
			var anchor: String = block["anchor"]
			var row := Sty.hbox()
			row.size_flags_horizontal = Control.SIZE_EXPAND_FILL

			var content := Sty.rich("text-sm", "fg-0")
			content.text = _to_bbcode(block)
			row.add_child(content)

			var count: int = int(counts.get(anchor, 0))
			var open := Sty.btn("注" + (str(count) if count > 0 else ""))
			open.tooltip_text = "在这一段上留批注(或右键)"
			var captured := anchor
			open.pressed.connect(func() -> void:
				emit_event("open-bubble", {"anchor": captured}))
			row.add_child(open)

			# 右键开泡(D5:contextmenu 任意块)
			row.gui_input.connect(func(event: InputEvent) -> void:
				if event is InputEventMouseButton \
					and event.button_index == MOUSE_BUTTON_RIGHT and event.pressed:
					emit_event("open-bubble", {"anchor": captured})
					row.accept_event())

			vbox.add_child(row)
			if changed.has(anchor):
				MotionPlayer.play("change-flash", row)
			vbox.add_child(Sty.hline())


	func _to_bbcode(block: Dictionary) -> String:
		var text: String = block["text"]
		match block["kind"]:
			"code":
				return "[color=%s]%s" % [Sty.hex_of("sig-llm"), _escape(text)]
			"heading":
				var m := MdBlocks._re("^\\s*(#{1,6})\\s+(.*)$").search(text)
				if m != null:
					var level: int = m.get_string(1).length()
					var size: int = maxi(Sty.s("text-xl") - (level - 1) * 2, Sty.s("text-md"))
					return "[font_size=%d][b]%s[/b][/font_size]" % [size, _inline(m.get_string(2).strip_edges())]
			"list-item":
				var m2 := MdBlocks._re("^(\\s*)([-*+]|\\d+\\.)\\s+(.*)$").search(text)
				if m2 != null:
					return _escape(m2.get_string(1)) + "• " + _inline(m2.get_string(3))
		return _inline(text)


	## 行内白名单:转义后仅加工 **粗** / *斜* / `码` 三种
	func _inline(text: String) -> String:
		var s := _escape(text)
		s = MdBlocks._re("\\*\\*(.+?)\\*\\*").sub(s, "[b]$1[/b]", true)
		s = MdBlocks._re("(?<!\\w)\\*([^*\\n]+?)\\*(?!\\w)").sub(s, "[i]$1[/i]", true)
		s = MdBlocks._re("`([^`\\n]+?)`").sub(s, "[color=" + Sty.hex_of("sig-llm") + "]$1[/color]", true)
		return s


	## bbcode 转义: "[" 是唯一危险字符(Godot 写法 [lb])
	func _escape(s: String) -> String:
		return s.replace("[", "[lb]")


	static func _clear(node: Node) -> void:
		for c in node.get_children():
			node.remove_child(c)
			c.queue_free()


	func summary() -> String:
		return "文档预览(%d 字)" % String(state.get("source", "")).length()
