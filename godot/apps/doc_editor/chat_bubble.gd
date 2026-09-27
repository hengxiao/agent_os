class_name ChatBubbleDef
extends WidgetDef

## chat-bubble 定义面(W-bubble 的 Godot 版)。


func get_kind() -> String:
	return "chat-bubble"


func get_events() -> Array:
	return ["submit", "apply", "close"]


func create_instance(state: Dictionary) -> Widget:
	return ChatBubbleWidget.new(self, state)


class ChatBubbleWidget:
	extends Widget

	## 锚点批注气泡(docs/WIDGETS.md W-bubble):锚点引用行 + 消息流 + 输入框。
	## 铁律:widget 不出海——submit/apply 只发事件,父(DocEditorApp)经闸门接管,
	## 出海走 comment 专属端点与 comment.apply 管道(D2 先例)。
	## state:{anchor, quote, messages:[{role,text,edits?}], busy, draft}


	func get_anchor() -> String:
		return state.get("anchor", "")


	func render() -> void:
		_clear(root)
		var card := Sty.card()
		root.add_child(card)
		card.set_anchors_preset(Control.PRESET_FULL_RECT)
		var box := Sty.vbox()
		card.add_child(box)

		# 头:锚点 + ✕(收起;消息流在服务端,重开不丢)
		var head := Sty.hbox()
		head.add_child(Sty.label(get_anchor(), "text-xs", "fg-2"))
		head.add_child(Sty.spacer())
		var close := Sty.btn("✕")
		close.tooltip_text = "收起气泡(消息保留在服务端)"
		close.pressed.connect(func() -> void:
			emit_event("close", {"anchor": get_anchor()}))
		head.add_child(close)
		box.add_child(head)

		# 锚点引用行(摘录)
		var quote: String = state.get("quote", "")
		if quote.length() > 120:
			quote = quote.left(120) + "…"
		var quote_label := Sty.rich("text-xs", "fg-1")
		quote_label.text = "[i]" + quote.replace("[", "[lb]") + "[/i]"
		box.add_child(quote_label)

		# 消息流(role=log 语义)
		var messages: Array = state.get("messages", [])
		for msg in messages:
			if not msg is Dictionary:
				continue
			var is_user: bool = msg.get("role", "") == "user"
			var line := Sty.hbox()
			var who := Sty.label("你" if is_user else "agent", "text-xs", "live" if is_user else "ok", true)
			who.custom_minimum_size.x = 36
			line.add_child(who)
			var body := Sty.rich("text-sm", "fg-0")
			body.text = str(msg.get("text", "")).replace("[", "[lb]")
			line.add_child(body)
			box.add_child(line)

			# agent 建议 → apply 按钮(人按才落,agent 永不直改——升权哲学)
			if not is_user and msg.get("edits") is Array:
				for edit in msg["edits"]:
					if not edit is Dictionary:
						continue
					var replace: String = str(edit.get("replace_text", ""))
					if replace.is_empty():
						continue
					var row := Sty.hbox()
					var suggestion: String = str(edit.get("suggestion", ""))
					if not suggestion.is_empty():
						var sg := Sty.label(suggestion, "text-xs", "fg-1")
						sg.size_flags_horizontal = Control.SIZE_EXPAND_FILL
						row.add_child(sg)
					var apply := Sty.btn(Sty.copy("doc.comment.apply"), true)
					var captured_anchor := get_anchor()
					var captured_edit: Dictionary = edit
					apply.pressed.connect(func() -> void:
						emit_event("apply", {
							"anchor": captured_anchor,
							"replace_text": str(captured_edit.get("replace_text", "")),
							"suggestion": str(captured_edit.get("suggestion", "")),
						}))
					row.add_child(apply)
					box.add_child(row)

		# busy 骨架(思考中;reduced-motion 时这是纯文案,无动画)
		if state.get("busy", false):
			box.add_child(Sty.label(Sty.copy("doc.comment.thinking"), "text-xs", "fg-2"))

		# 输入行
		var input_row := Sty.hbox()
		var draft := Sty.line_edit(Sty.copy("doc.comment.placeholder"))
		draft.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		draft.text = state.get("draft", "")
		draft.text_changed.connect(func(t: String) -> void:
			mutate_state(func(s: Dictionary) -> void: s["draft"] = t))
		var busy: bool = state.get("busy", false)
		input_row.add_child(draft)
		var send := Sty.btn(Sty.copy("doc.comment.send"), true)
		send.disabled = busy
		var do_send := func() -> void:
			var text: String = state.get("draft", "")
			if text.strip_edges().is_empty():
				return
			mutate_state(func(s: Dictionary) -> void: s["draft"] = "")
			emit_event("submit", {"anchor": get_anchor(), "text": text})
		send.pressed.connect(do_send)
		draft.text_submitted.connect(func(_t: String) -> void: do_send.call())
		input_row.add_child(send)
		box.add_child(input_row)


	## widget 级 cascade fragment;段落/全文由父(app)经 child_context 注入(§7-2)
	func context_fragment() -> Dictionary:
		return {
			"kind": get_kind(),
			"anchor": get_anchor(),
			"quote": state.get("quote", ""),
		}


	func summary() -> String:
		return "「%s」批注,%d 条消息" % [get_anchor(), (state.get("messages", []) as Array).size()]


	static func _clear(node: Node) -> void:
		for c in node.get_children():
			node.remove_child(c)
			c.queue_free()
