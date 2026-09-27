class_name DocEditorDef
extends WidgetDef

## doc-editor 定义面(docs/COMPOUND-WIDGET.md C3:第一个产品级 compound 的 Godot 版)。

var _services: DocServices


func _init(services: DocServices) -> void:
	_services = services


func get_kind() -> String:
	return "doc-editor"


func create_instance(state: Dictionary) -> Widget:
	return DocEditorApp.new(self, state, _services)


class DocEditorApp:
	extends CompoundWidget

	## Doc Editor(docs/DOC-EDITOR.md):文档 = 预定义 md-viewer 子件,段落批注 =
	## 动态 chat-bubble 子件;管控三通道全用(事件闸门接管 submit/apply/close,
	## child_context 注入锚段/全文,气泡显隐由父管理)。
	## 写动作(save/snapshot/rewind/apply)全走 action 管道;comment 走 D2 专属端点先例。
	## state:{name,text,title,dirty,savedAt,view,versions,chat,bubbles}

	var _svc: DocServices
	var _server_app: ActionPipeline.AppInstance # 服务端 kind=doc 的 instance(action 管道的锚)
	var _viewer: Widget
	var _editor: TextEdit
	var _loading_editor := false
	var _edit_seq := 0              # 预览防抖序号
	var _rewind_armed := false      # 回滚两击确认(lab-iterate 同款 armed 范式)
	var _rewind_button: Button


	func _init(def: WidgetDef, state: Dictionary, services: DocServices) -> void:
		super(def, state)
		_svc = services
		if not state.has("view"):
			state["view"] = "split"
		if not state.has("dirty"):
			state["dirty"] = false
		create_slots(services.registry)
		_viewer = find_child("viewer")


	func get_doc_name() -> String:
		return state.get("name", "")


	# ---------------------------------------------------------------- compound 协议面

	func get_slots() -> Array:
		return [{"id": "viewer", "kind": "md-viewer", "state": {}}]


	func get_dynamic_allow() -> Array:
		return ["chat-bubble"]


	## app 级 cascade provider(与 web doc-editor 的 _appProvider 同构)
	func context_fragment() -> Dictionary:
		return {
			"name": get_doc_name(),
			"versions": (state.get("versions", []) as Array).duplicate(),
			"dirty": state.get("dirty", false),
		}


	## 信息管控(§7-2):气泡的 cascade fragment 注入锚段原文与全文
	func child_context(child: Widget, fragment: Dictionary) -> Dictionary:
		if child is ChatBubbleDef.ChatBubbleWidget:
			var text: String = state.get("text", "")
			fragment["anchor"] = child.get_anchor()
			fragment["paragraph"] = MdBlocks.block_text(text, child.get_anchor())
			fragment["full_text"] = text
		return fragment


	## 事件闸门:接管子件事件(放行上行,同时在此落地为动作)
	func on_child_event(child: Widget, evt_name: String, payload: Dictionary) -> bool:
		if child == _viewer and evt_name == "open-bubble":
			_open_bubble(str(payload.get("anchor", "")))
			return true
		if child is ChatBubbleDef.ChatBubbleWidget:
			match evt_name:
				"submit":
					_handle_submit(child, str(payload.get("text", "")))
				"apply":
					_handle_apply(child, payload)
				"close":
					remove_child_widget(child.id, true)
					render()
		return true


	## 装配服务端 instance(开窗时 spawn kind=doc 的结果)
	func attach_server_app(app: ActionPipeline.AppInstance) -> void:
		_server_app = app


	# ---------------------------------------------------------------- 数据装载

	## 服务端文档 JSON → 本地 state(读面直给;写动作永远走管道)
	func apply_doc_json(doc: Dictionary, bubbles: Array, annotations: Array = []) -> void:
		mutate_state(func(s: Dictionary) -> void:
			s["name"] = doc.get("name", get_doc_name())
			s["text"] = doc.get("text", "")
			var meta: Dictionary = doc.get("meta", {}) if doc.get("meta") is Dictionary else {}
			s["title"] = str(meta.get("title", ""))
			s["savedAt"] = float(meta.get("savedAt", 0))
			s["versions"] = doc.get("versions", []) if doc.get("versions") is Array else []
			s["chat"] = doc.get("chat", []) if doc.get("chat") is Array else []
			s["bubbles"] = bubbles
			s["annotations"] = annotations
			s["dirty"] = false)


	func reload_doc() -> void:
		var resp := await _svc.client.read_doc(get_doc_name())
		var bresp := await _svc.client.read_bubbles(get_doc_name())
		var aresp := await _svc.client.read_annotations(get_doc_name())
		if not resp.get("ok", false):
			_report("重载失败:" + str(resp.get("error", "")), "danger")
			return
		apply_doc_json(resp["json"], AgentOsClient.json_or(bresp, []),
			AgentOsClient.json_or(aresp, []))
		_merge_open_bubble_flows()
		render()


	# ---------------------------------------------------------------- 批注(P2 annotations;
	# 专属端点先例同 comment;出海在 app 层,widget/宿主只发事件)

	func reload_annotations() -> void:
		var aresp := await _svc.client.read_annotations(get_doc_name())
		if aresp.get("ok", false):
			mutate_state(func(s: Dictionary) -> void:
				s["annotations"] = AgentOsClient.json_or(aresp, []))
			render()


	## 存批注(upsert;创建=pending,重编回 pending 参与下一轮批处理)
	func save_annotation(anchor: String, quote: String, content: String) -> bool:
		var resp := await _svc.client.save_annotation(get_doc_name(), anchor, quote, content)
		if resp.get("ok", false):
			await reload_annotations()
			return true
		_report("存批注失败:" + str(resp.get("error", "")), "danger")
		return false


	func delete_annotation(anchor: String) -> bool:
		var resp := await _svc.client.delete_annotation(get_doc_name(), anchor)
		if resp.get("ok", false):
			await reload_annotations()
			return true
		_report("删除失败:" + str(resp.get("error", "")), "danger")
		return false


	## 按锚点取批注记录(无则空)
	func annotation_at(anchor: String) -> Dictionary:
		for a in state.get("annotations", []):
			if a is Dictionary and str(a.get("anchor", "")) == anchor:
				return a
		return {}


	## 批注批处理生成下一版(generate 专属端点;成功 → 重载全文/版本/批注;
	## 返回服务端 json(newVersion/annotationResults/diff),失败空表;
	## 409 版本冲突 → 明说 + 自动重拉最新版,FLOWS §3.5)
	func generate_next(user_prompt := "") -> Dictionary:
		if _svc.client == null:
			_report("网络面未装配", "danger")
			return {}
		var resp := await _svc.client.generate_doc(get_doc_name(), user_prompt)
		if resp.get("ok", false) and resp.get("json") is Dictionary:
			reload_doc()
			return resp["json"]
		if int(resp.get("status", 0)) == 409:
			_report("版本已变化,先看最新版", "danger")
			reload_doc() # 自动重拉(FLOWS §3.5;用户再点生成即可)
		else:
			_report("生成失败:" + str(resp.get("error", "")), "danger")
		return {}


	# ---------------------------------------------------------------- 渲染

	func render() -> void:
		_detach_owned_roots() # widget 的 root 是 widget 的产,chrome 重建前先摘出
		_clear(root)
		_sync_viewer()
		var main := Sty.vbox()
		main.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		main.size_flags_vertical = Control.SIZE_EXPAND_FILL
		root.add_child(main)
		main.set_anchors_preset(Control.PRESET_FULL_RECT)

		# ── 工具条 ──
		var bar := Sty.hbox()
		var title: String = state.get("title", "")
		bar.add_child(Sty.label(title if not title.is_empty() else get_doc_name(), "text-lg", "fg-0", true))
		bar.add_child(Sty.spacer())

		var versions: Array = state.get("versions", [])
		var version_pick := OptionButton.new()
		for v in versions:
			version_pick.add_item(str(v))
		version_pick.custom_minimum_size.x = 96
		version_pick.disabled = versions.is_empty()
		bar.add_child(version_pick)

		var save := Sty.btn(Sty.copy("doc.save"), true)
		save.pressed.connect(_do_save)
		bar.add_child(save)
		var snapshot := Sty.btn(Sty.copy("doc.snapshot"))
		snapshot.pressed.connect(_do_snapshot)
		bar.add_child(snapshot)
		_rewind_button = Sty.btn(Sty.copy("doc.rewind"))
		_rewind_button.pressed.connect(func() -> void: _do_rewind(version_pick))
		bar.add_child(_rewind_button)
		var review := Sty.btn(Sty.copy("doc.review"))
		review.pressed.connect(_do_review)
		bar.add_child(review)
		var export := Sty.btn(Sty.copy("doc.export"))
		export.pressed.connect(_do_export)
		bar.add_child(export)

		for mode in ["edit", "split", "preview"]:
			var b := Sty.btn(Sty.copy("doc.view." + mode))
			if state.get("view", "split") == mode:
				b.disabled = true # 当前面:置灰即"选中态"(双编码:文案仍在)
			var captured: String = mode
			b.pressed.connect(func() -> void:
				patch_state(func(s: Dictionary) -> void: s["view"] = captured)) # 视图切换 = 纯 state(本地)
			bar.add_child(b)
		main.add_child(bar)

		# ── 本体:编辑 | 预览 | 批注栏 ──
		var body := Sty.hbox()
		body.size_flags_vertical = Control.SIZE_EXPAND_FILL
		body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		var view: String = state.get("view", "split")

		if view != "preview":
			_editor = Sty.text_edit()
			_editor.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			_loading_editor = true
			_editor.text = state.get("text", "")
			_loading_editor = false
			_editor.text_changed.connect(_on_editor_changed)
			body.add_child(_editor)

		if view != "edit":
			var viewer_scroll := Sty.scroll()
			viewer_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
			_viewer.root.custom_minimum_size.x = 420 # ScrollContainer 不撑宽子件
			viewer_scroll.add_child(_viewer.root)
			body.add_child(viewer_scroll)

		var bubble_col := Sty.scroll()
		bubble_col.custom_minimum_size.x = 280
		var bubble_box := Sty.vbox()
		bubble_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		bubble_box.custom_minimum_size.x = 252
		bubble_col.add_child(bubble_box)
		for child in children:
			if child is ChatBubbleDef.ChatBubbleWidget:
				child.render()
				bubble_box.add_child(child.root)
		body.add_child(bubble_col)
		main.add_child(body)

		# ── 主对话条(D5:doc 作用域对话;changed 时重拉重渲)──
		main.add_child(Sty.hline())
		var chat_scroll := Sty.scroll()
		chat_scroll.custom_minimum_size.y = 96
		chat_scroll.size_flags_vertical = Control.SIZE_SHRINK_END
		var chat_box := Sty.vbox()
		chat_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		chat_box.custom_minimum_size.x = 480 # ScrollContainer 不撑宽子件
		chat_scroll.add_child(chat_box)
		for m in state.get("chat", []):
			if not m is Dictionary:
				continue
			var is_user: bool = m.get("role", "") == "user"
			var line := Sty.hbox()
			var who := Sty.label("你" if is_user else "agent", "text-xs", "live" if is_user else "ok", true)
			who.custom_minimum_size.x = 36
			line.add_child(who)
			var txt := Sty.rich("text-sm", "fg-0")
			txt.text = str(m.get("text", "")).replace("[", "[lb]")
			line.add_child(txt)
			chat_box.add_child(line)
		main.add_child(chat_scroll)

		var chat_row := Sty.hbox()
		var chat_input := Sty.line_edit(Sty.copy("doc.chat.placeholder"))
		chat_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		chat_row.add_child(chat_input)
		var chat_send := Sty.btn(Sty.copy("doc.chat.send"), true)
		var do_chat := func() -> void: _do_chat(chat_input.text, chat_input)
		chat_send.pressed.connect(do_chat)
		chat_input.text_submitted.connect(func(_t: String) -> void: do_chat.call())
		chat_row.add_child(chat_send)
		main.add_child(chat_row)

		# ── 状态栏 ──
		main.add_child(Sty.hline())
		var status := Sty.hbox()
		var dirty: bool = state.get("dirty", false)
		status.add_child(Sty.dot("warn" if dirty else "ok"))
		status.add_child(Sty.label(
			"%d 字 · %s · %d 个版本" % [
				String(state.get("text", "")).length(),
				Sty.copy("doc.status.dirty") if dirty else Sty.copy("doc.status.saved"),
				versions.size(),
			], "text-xs", "fg-2"))
		main.add_child(status)
		state_changed.emit(self) # 视图层钩子:宿主(场景/面板)据此同步


	## chrome 重建前摘下全部 widget 自有视图(Godot 不自动换父;queue_free 父容器
	## 会连坐销毁子树——widget.root 的生命归 widget,不归 chrome)
	func _detach_owned_roots() -> void:
		for child in children:
			if child.root.get_parent() != null:
				child.root.get_parent().remove_child(child.root)


	func _sync_viewer() -> void:
		if _viewer == null:
			return
		var counts := {}
		for b in state.get("bubbles", []):
			if not b is Dictionary:
				continue
			var anchor: String = str(b.get("anchor", ""))
			var msgs: Array = b.get("messages", []) if b.get("messages") is Array else []
			if not anchor.is_empty():
				counts[anchor] = maxi(msgs.size(), 1)
		_viewer.set_state({
			"source": state.get("text", ""),
			"bubbleCounts": counts,
		})


	func _on_editor_changed() -> void:
		if _loading_editor:
			return
		mutate_state(func(s: Dictionary) -> void:
			s["text"] = _editor.text
			s["dirty"] = true)
		_edit_seq += 1
		var seq := _edit_seq
		if not root.is_inside_tree():
			return
		await root.get_tree().create_timer(0.4).timeout
		if seq == _edit_seq:
			_sync_viewer() # 400ms 防抖:预览同源重渲(滚动各自,不同步)


	## 已开气泡的消息流与服务端事实源合并(重载后调)
	func _merge_open_bubble_flows() -> void:
		for child in children:
			if child is ChatBubbleDef.ChatBubbleWidget:
				var rec := _find_bubble_record(child.get_anchor())
				if rec.has("anchor"):
					var msgs: Array = rec.get("messages", [])
					child.mutate_state(func(s: Dictionary) -> void:
						s["messages"] = msgs.duplicate(true))
				child.render()


	func _find_bubble_record(anchor: String) -> Dictionary:
		for b in state.get("bubbles", []):
			if b is Dictionary and str(b.get("anchor", "")) == anchor:
				return b
		return {}


	# ---------------------------------------------------------------- 气泡流

	func _open_bubble(anchor: String) -> void:
		if anchor.is_empty():
			return
		var existing := find_child_of(ChatBubbleDef.ChatBubbleWidget,
			func(b) -> bool: return b.get_anchor() == anchor)
		if existing != null:
			_focus_widget(existing)
			return # 防重复(同锚点 early-return)
		var rec := _find_bubble_record(anchor)
		var quote := MdBlocks.block_text(state.get("text", ""), anchor)
		var bubble := _svc.registry.create("chat-bubble", {
			"anchor": anchor,
			"quote": quote,
			"messages": (rec.get("messages", []) as Array).duplicate(true) if rec.has("anchor") else [],
			"busy": false,
			"draft": "",
		})
		add_child_widget(bubble)
		render()
		_focus_widget(bubble)


	func _focus_widget(w: Widget) -> void:
		var p := w.root.get_parent()
		while p != null:
			if p is ScrollContainer:
				(p as ScrollContainer).ensure_control_visible(w.root)
				break
			p = p.get_parent()
		MotionPlayer.play("focus-pulse", w.root)


	func _handle_submit(bubble: Widget, text: String) -> void:
		bubble.mutate_state(func(s: Dictionary) -> void: s["busy"] = true)
		bubble.render()
		var cascade := _svc.tree.cascade.build(bubble) # §16 级联信封(widget→app→shell)
		var resp := await _svc.client.send_comment(get_doc_name(),
			(bubble as ChatBubbleDef.ChatBubbleWidget).get_anchor(), text, cascade)
		if resp.get("ok", false):
			var j: Dictionary = resp["json"]
			var msgs: Array = bubble.state.get("messages", [])
			msgs.append({"role": "user", "text": text})
			var assistant := {"role": "assistant", "text": str(j.get("reply", ""))}
			if j.get("edits") is Array and not (j["edits"] as Array).is_empty():
				assistant["edits"] = (j["edits"] as Array).duplicate(true)
			msgs.append(assistant)
			bubble.mutate_state(func(s: Dictionary) -> void:
				s["messages"] = msgs
				s["busy"] = false)
			# 气泡流回账到 app state(新锚点首条也落账;服务端仍是事实源)
			mutate_state(func(s: Dictionary) -> void:
				var bubbles: Array = s.get("bubbles", [])
				var rec := _find_bubble_record((bubble as ChatBubbleDef.ChatBubbleWidget).get_anchor())
				if rec.has("anchor"):
					rec["messages"] = msgs.duplicate(true)
				else:
					bubbles.append({"anchor": (bubble as ChatBubbleDef.ChatBubbleWidget).get_anchor(),
						"messages": msgs.duplicate(true)})
				s["bubbles"] = bubbles)
			_sync_viewer()
		else:
			bubble.mutate_state(func(s: Dictionary) -> void: s["busy"] = false)
			_report("评论助手暂不可用:" + str(resp.get("error", "")), "danger") # 与既有 503 归类同语义
		bubble.render()


	func _handle_apply(bubble: Widget, payload: Dictionary) -> void:
		if _server_app == null:
			_report("app instance 未装配,无法 apply", "danger")
			return
		var anchor := str(payload.get("anchor", ""))
		# 越界/已变校验面:expected = 当前块原文
		var expected := MdBlocks.block_text(state.get("text", ""), anchor)
		var resp := await _svc.pipeline.invoke(_server_app, "comment.apply", {
			"anchor": anchor,
			"replace_text": str(payload.get("replace_text", "")),
			"expected": expected,
		}, "tab", "", bubble)
		if resp.get("ok", false):
			_report("已应用修改(" + anchor + ")", "ok")
			reload_doc()
		else:
			var status := int(resp.get("status", 0))
			_report("文档已变化,请重新评审(" + anchor + ")" if status == 400 or status == 409
				else "apply 失败:" + str(resp.get("error", "")), "danger")


	# ---------------------------------------------------------------- 工具条动作(全走管道)

	func _do_save() -> bool:
		if not _check_app():
			return false
		var resp := await _svc.pipeline.invoke(_server_app, "doc.save",
			{"text": state.get("text", "")}, "tab", "", self)
		if resp.get("ok", false):
			mutate_state(func(s: Dictionary) -> void:
				s["dirty"] = false
				s["savedAt"] = Time.get_unix_time_from_system())
			_report(Sty.copy("doc.status.saved") + " · " + get_doc_name(), "ok")
			render()
			return true
		_report("保存失败:" + str(resp.get("error", "")), "danger")
		return false


	func _do_snapshot() -> bool:
		if not _check_app():
			return false
		var resp := await _svc.pipeline.invoke(_server_app, "doc.snapshot", {}, "tab", "", self)
		if resp.get("ok", false):
			_report("快照完成", "ok")
			reload_doc()
			return true
		_report("快照失败:" + str(resp.get("error", "")), "danger")
		return false


	func _do_rewind(pick: OptionButton) -> bool:
		if not _check_app():
			return false
		if pick.item_count == 0:
			_report("先选版本", "danger")
			return false
		var version := pick.get_item_text(pick.selected)
		if not _rewind_armed: # 两击确认(rewind 改工作副本,历史不动)
			_rewind_armed = true
			_rewind_button.text = Sty.copy("doc.rewind.confirm")
			if root.is_inside_tree():
				await root.get_tree().create_timer(2.5).timeout
				_rewind_armed = false
				if is_instance_valid(_rewind_button):
					_rewind_button.text = Sty.copy("doc.rewind")
			return false
		_rewind_armed = false
		return await rewind_to(version)


	## 回溯到指定版本(版本叠扇面的入口;doc.rewind 走管道,历史不动)
	func rewind_to(version: String) -> bool:
		if not _check_app():
			return false
		var resp := await _svc.pipeline.invoke(_server_app, "doc.rewind",
			{"version": version}, "tab", "", self)
		if resp.get("ok", false):
			_report("已回滚到 " + version + "(历史不动)", "ok")
			reload_doc()
			return true
		_report("回滚失败:" + str(resp.get("error", "")), "danger")
		return false


	func _do_review() -> bool:
		if _svc.client == null:
			_report("网络面未装配", "danger")
			return false
		_report("评审中…", "info")
		var resp := await _svc.client.review_doc(get_doc_name())
		if not resp.get("ok", false):
			_report("评审助手暂不可用:" + str(resp.get("error", "")), "danger")
			return false
		var bresp := await _svc.client.read_bubbles(get_doc_name())
		mutate_state(func(s: Dictionary) -> void:
			s["bubbles"] = AgentOsClient.json_or(bresp, []))
		_merge_open_bubble_flows()
		render()
		_report("评审完成:批注已挂到各段", "ok")
		return true


	func _do_export() -> void:
		var text: String = state.get("text", "")
		_svc.export_copy.call(text)
		_report("已复制全文(%d 字)" % text.length(), "ok")


	func _do_chat(text: String, input: LineEdit = null) -> bool:
		if text.strip_edges().is_empty():
			return false
		if input != null:
			input.text = ""
		var resp := await _svc.client.send_chat(get_doc_name(), text)
		if not resp.get("ok", false):
			_report("编辑助手暂不可用:" + str(resp.get("error", "")), "danger")
			return false
		var j: Dictionary = resp["json"]
		var chat: Array = state.get("chat", [])
		chat.append({"role": "user", "text": text})
		chat.append({"role": "assistant", "text": str(j.get("reply", ""))})
		mutate_state(func(s: Dictionary) -> void: s["chat"] = chat)
		if j.get("changed", false): # changed = 服务端全文对比,不信自报
			_report("文档已更新", "ok")
			reload_doc()
			return true
		render()
		return false


	func _check_app() -> bool:
		if _server_app != null:
			return true
		_report("app instance 未装配(spawn 失败?)", "danger")
		return false


	func _report(msg: String, tone: String) -> void:
		_svc.report.call(msg, tone)


	func summary() -> String:
		return "文档 %s(%d 字)" % [get_doc_name(), String(state.get("text", "")).length()]


	static func _clear(node: Node) -> void:
		for c in node.get_children():
			node.remove_child(c)
			c.queue_free()
