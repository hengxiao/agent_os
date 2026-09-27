class_name LetterHost
extends PanelContainer

## 信件宿主(docs/GAME-UI-DOC.md):信纸(阅览)+ 内联批注标记 + 批注坞(纯用户
## 批注,annotations 模型:无即时 AI 回复,攒着批处理)+ 印钮栏 + 时间卷轴
## (回溯)+ 回执联(chat)。数据与动作全在 DocEditorApp;本层只做呈现:
## 盖章/卷轴/飞入全部经 MotionPlayer 具名动效。
## 布局铁律(实锤):透明 PanelContainer 层叠铺满,不用锚点;ScrollContainer
## 子件显式给最小宽;自由摆放 + 零宽量体 = 禁止。

var _svc: DocServices
var _app: DocEditorDef.DocEditorApp

var _page: LetterPage
var _letter_area: PanelContainer
var _note_dock: VBoxContainer    # 批注坞(容器托管定宽)
var _ed_anchor: Label
var _ed_quote: RichTextLabel
var _ed_status: Label
var _ed_ainote: Label            # 生成回标后的 aiNote(改了什么/为什么忽略)
var _ed_input: TextEdit
var _ed_anchor_name := ""
var _rail_btns: Dictionary = {}  # action → Button(印钮五态机的对象面)
var _rail_labels := {}           # action → 原始文案
var _rail_busy := {}             # action → busy 拒重入
var _loop_stage := "check"      # 循环阶段(内部状态机;UI 主面 = 页栈)
var _desk: DeskScene             # 3D 书桌(空间/态势层,在内容之下)
var _pages: PageStack            # 顶栏版本页栈(翻书展开/选中回溯)
var _vtree: VersionTree          # 版本树覆盖层
var _gen_btn: Button             # 再生成(顶栏右侧主钮)
var _tree_btn: Button            # 显示版本树
var _gen_stage_row: HBoxContainer # 生成阶段条(顶栏下方窄条)
var _gen_stage_labels: Array = []
var _tree_data: Dictionary = {}  # {base, versions[]}(版本动作后重拉)
var _sum_cache: Dictionary = {}  # version → 摘要文本(对当前祖版;difsum 缓存兜底)
var _version_texts: Dictionary = {} # version → 快照全文(降级面取原文用)
var _diff_seq := 0                  # 快速拨页只认最后一次(防乱序回灌)
var _block_texts: Dictionary = {} # 上一帧块文(变化检测)


func _init(services: DocServices) -> void:
	_svc = services


func _ready() -> void:
	add_theme_stylebox_override("panel", StyleBoxEmpty.new())
	_build()


func bind(app: DocEditorDef.DocEditorApp) -> void:
	if _app != null and _app.state_changed.is_connected(_on_app_changed):
		_app.state_changed.disconnect(_on_app_changed)
	_app = app
	_app.state_changed.connect(_on_app_changed)
	_sync_all()
	if is_inside_tree():
		_page.play_reveal()


func _build() -> void:
	# ── 底层:3D 书桌(空间与态势;不交互)——PanelContainer 层叠语义:先加的在底 ──
	var desk_vp_container := SubViewportContainer.new()
	desk_vp_container.stretch = true
	desk_vp_container.mouse_filter = Control.MOUSE_FILTER_IGNORE
	desk_vp_container.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	desk_vp_container.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var desk_vp := SubViewport.new()
	desk_vp.own_world_3d = true
	desk_vp.transparent_bg = false
	_desk = DeskScene.new()
	desk_vp.add_child(_desk)
	desk_vp_container.add_child(desk_vp)
	add_child(desk_vp_container)

	var root_vbox := VBoxContainer.new()
	root_vbox.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	root_vbox.size_flags_vertical = Control.SIZE_EXPAND_FILL
	add_child(root_vbox)

	# ── 顶栏:版本页栈(主面)+ 右侧「显示版本树」「再生成」两钮 ──
	var topbar := HBoxContainer.new()
	topbar.add_theme_constant_override("separation", 10)
	root_vbox.add_child(topbar)
	_pages = PageStack.new()
	_pages.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_pages.rewind_requested.connect(_on_page_rewind)
	_pages.page_hovered.connect(_on_page_hovered)
	topbar.add_child(_pages)
	var top_right := VBoxContainer.new()
	top_right.add_theme_constant_override("separation", 4)
	_gen_btn = Sty.btn("再生成", true)
	_gen_btn.custom_minimum_size = Vector2(96, 38)
	_gen_btn.pressed.connect(func() -> void: _act_generate())
	top_right.add_child(_gen_btn)
	_tree_btn = Sty.btn("版本树")
	_tree_btn.custom_minimum_size = Vector2(96, 30)
	_tree_btn.pressed.connect(func() -> void:
		_vtree.open(_tree_data.get("versions", []), str(_tree_data.get("base", ""))))
	top_right.add_child(_tree_btn)
	topbar.add_child(top_right)

	# 生成阶段条(顶栏下窄条;具名阶段,进行中才显示)
	_gen_stage_row = HBoxContainer.new()
	_gen_stage_row.alignment = HBoxContainer.ALIGNMENT_CENTER
	_gen_stage_row.add_theme_constant_override("separation", 16)
	for st in ["读批注", "起草", "封存", "回标"]:
		var lb := Sty.label("· " + st, "text-xs", "fg-2")
		_gen_stage_row.add_child(lb)
		_gen_stage_labels.append(lb)
	_gen_stage_row.visible = false
	root_vbox.add_child(_gen_stage_row)

	var hbox := Sty.hbox()
	hbox.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hbox.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root_vbox.add_child(hbox)

	# ── 信纸区 ──
	_letter_area = PanelContainer.new()
	_letter_area.add_theme_stylebox_override("panel", StyleBoxEmpty.new())
	_letter_area.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_letter_area.size_flags_vertical = Control.SIZE_EXPAND_FILL
	hbox.add_child(_letter_area)

	var page_margin := MarginContainer.new()
	page_margin.add_theme_constant_override("margin_left", 110)
	page_margin.add_theme_constant_override("margin_right", 110)
	page_margin.add_theme_constant_override("margin_top", 20)
	page_margin.add_theme_constant_override("margin_bottom", 34)
	_letter_area.add_child(page_margin)
	_page = LetterPage.new()
	page_margin.add_child(_page)
	_page.note_requested.connect(_open_note_editor)
	_page.reply_submitted.connect(_on_reply)

	# ── 批注坞(纯用户批注:引文 + 内容 + 状态章;无对话) ──
	_note_dock = VBoxContainer.new()
	_note_dock.custom_minimum_size.x = 380
	_note_dock.visible = false
	hbox.add_child(_note_dock)
	var note_panel := PanelContainer.new()
	var note_sb := StyleBoxFlat.new()
	note_sb.bg_color = Color("#f7ecc4")
	note_sb.set_corner_radius_all(5)
	note_sb.set_content_margin_all(12)
	note_sb.shadow_color = Color(0, 0, 0, 0.4)
	note_sb.shadow_size = 10
	note_sb.shadow_offset = Vector2(4, 6)
	note_panel.add_theme_stylebox_override("panel", note_sb)
	var nb := Sty.vbox()
	note_panel.add_child(nb)

	var head := Sty.hbox()
	_ed_anchor = Sty.label("", "text-xs", "paper-ink-2")
	head.add_child(_ed_anchor)
	head.add_child(Sty.spacer())
	var close := Sty.btn("✕")
	close.pressed.connect(func() -> void:
		_note_dock.visible = false
		if _desk != null:
			_desk.focus("letter"))
	head.add_child(close)
	nb.add_child(head)

	_ed_quote = RichTextLabel.new()
	_ed_quote.bbcode_enabled = true
	_ed_quote.fit_content = true
	_ed_quote.scroll_active = false
	_ed_quote.add_theme_font_size_override("normal_font_size", Sty.s("text-xs"))
	_ed_quote.add_theme_color_override("default_color", Sty.c("paper-ink-2"))
	nb.add_child(_ed_quote)

	_ed_status = Sty.label("", "text-xs", "paper-ink-2")
	nb.add_child(_ed_status)

	_ed_ainote = Sty.label("", "text-xs", "paper-ink-2")
	_ed_ainote.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	nb.add_child(_ed_ainote)

	_ed_input = TextEdit.new()
	_ed_input.custom_minimum_size = Vector2(0, 150)
	_ed_input.placeholder_text = "就这一段写你的批注…(攒着,等「按批注改」批处理)"
	_ed_input.add_theme_font_size_override("font_size", Sty.s("text-sm"))
	_ed_input.add_theme_color_override("font_color", Sty.c("paper-ink"))
	var ed_sb := StyleBoxFlat.new()
	ed_sb.bg_color = Color("#fdf8e3")
	ed_sb.border_color = Color("#d9c98f")
	ed_sb.set_border_width_all(1)
	ed_sb.set_corner_radius_all(4)
	_ed_input.add_theme_stylebox_override("normal", ed_sb)
	nb.add_child(_ed_input)

	var btn_row := Sty.hbox()
	var save := Sty.btn("存批注", true)
	save.pressed.connect(_on_save_annotation)
	btn_row.add_child(save)
	var del := Sty.btn("删除")
	del.pressed.connect(_on_delete_annotation)
	btn_row.add_child(del)
	nb.add_child(btn_row)
	var hint := Sty.label("批注只记你的话;改动在回执联/批处理里发生", "text-xs", "paper-ink-2")
	nb.add_child(hint)
	_note_dock.add_child(note_panel)

	# ── 印钮栏(次行动作;主操作已上轨道:①评审/③生成) ──
	var rail := VBoxContainer.new()
	rail.custom_minimum_size.x = 64
	rail.add_theme_constant_override("separation", 10)
	hbox.add_child(rail)
	rail.add_child(_rail_button("保存", "save"))
	rail.add_child(_rail_button("快照", "snapshot"))
	rail.add_child(_rail_button("评审", "review"))
	rail.add_child(_rail_button("导出", "export"))
	var hint2 := Sty.label("右键段落\n写批注", "text-xs", "fg-2")
	rail.add_child(hint2)

	# 版本树覆盖层(全屏覆盖,默认收拢;本类 PanelContainer 会把它铺满)
	_vtree = VersionTree.new()
	add_child(_vtree)
	_vtree.version_chosen.connect(func(v: String) -> void:
		_pages.expand_version(v))


func _rail_button(label_text: String, action: String) -> Control:
	var b := Sty.btn(label_text)
	b.custom_minimum_size = Vector2(52, 44)
	_rail_btns[action] = b
	_rail_labels[action] = label_text
	match action:
		"save":
			b.pressed.connect(func() -> void: _act_save())
		"snapshot":
			b.pressed.connect(func() -> void: _act_snapshot())
		"review":
			b.pressed.connect(func() -> void: _act_review())
		"export":
			b.pressed.connect(func() -> void: _app._do_export())
	return b


## 印钮五态(GAME-UI-FLOWS §0):idle/disabled/busy/ok/fail;
## busy 拒重入;fail = 红 + 「重试」文案;ok 的回声由具名动效承担
func _rail_state(action: String, st: String) -> void:
	var b: Button = _rail_btns.get(action)
	if b == null:
		return
	match st:
		"idle":
			_rail_busy[action] = false
			b.disabled = false
			b.text = _rail_labels[action]
			b.modulate = Color.WHITE
		"disabled":
			b.disabled = true
			b.modulate = Color.WHITE
		"busy":
			_rail_busy[action] = true
			b.disabled = true
			b.text = _rail_labels[action] + "…"
			b.modulate = Color(1, 1, 1, 0.75)
		"fail":
			_rail_busy[action] = false
			b.disabled = false
			b.text = "重试"
			b.modulate = Color(Sty.c("danger"))


func _rail_is_busy(action: String) -> bool:
	return _rail_busy.get(action, false) == true


func _act_save() -> void:
	if _rail_is_busy("save"):
		return
	_rail_state("save", "busy")
	if await _app._do_save():
		_rail_state("save", "idle")
		_stamp()
	else:
		_rail_state("save", "fail")


func _act_snapshot() -> void:
	if _rail_is_busy("snapshot"):
		return
	_rail_state("snapshot", "busy")
	if await _app._do_snapshot():
		_rail_state("snapshot", "idle")
		_stamp()
		if _desk != null:
			_desk.drop_sheet() # 新页落叠
	else:
		_rail_state("snapshot", "fail")


func _act_review() -> void:
	if _rail_is_busy("review"):
		return
	_rail_busy["review"] = true
	_svc.report.call("评审中…(通读全文)", "info")
	if await _app._do_review():
		_rail_busy["review"] = false
	else:
		_rail_busy["review"] = false
		_svc.report.call("评审助手暂不可用,点①重试", "danger")


## 生成(顶栏「再生成」主钮;FLOWS §3):具名阶段窄条;busy 拒重入;
## 成功 = 新版本入页栈 + 盖章 + 书桌落页;409 由 app 层自动重拉
func _act_generate() -> void:
	if _rail_is_busy("generate"):
		return
	_rail_busy["generate"] = true
	_gen_btn.disabled = true
	_gen_btn.text = "生成中…"
	_gen_stage_row.visible = true
	_gen_stage_step(0) # 读批注
	await get_tree().process_frame
	_gen_stage_step(1) # 起草(LLM 长任务在飞)
	var result: Dictionary = await _app.generate_next()
	if result.is_empty():
		_gen_stage_row.visible = false
		_rail_busy["generate"] = false
		_gen_btn.text = "重试生成"
		_gen_btn.modulate = Color(Sty.c("danger"))
		_gen_btn.disabled = false
		_refresh_top()
		return
	_gen_stage_step(2) # 封存(服务端已落新版)
	_gen_stage_step(3) # 回标(批注章变色)
	var applied := 0
	var ignored := 0
	for r in result.get("annotationResults", []):
		if r is Dictionary:
			if r.get("status") == "applied":
				applied += 1
			elif r.get("status") == "ignored":
				ignored += 1
	_svc.report.call("已生成 %s · 采纳 %d / 忽略 %d" % [
		str(result.get("newVersion", "")), applied, ignored], "ok")
	_stamp()
	if _desk != null:
		_desk.drop_sheet() # 新版本落叠(空间回声)
	_rail_busy["generate"] = false
	_gen_btn.modulate = Color.WHITE
	if is_inside_tree():
		await get_tree().create_timer(1.2).timeout
	_gen_stage_row.visible = false
	_refresh_top() # pending 已清零,页栈插新页


func _gen_stage_step(idx: int) -> void:
	for i in _gen_stage_labels.size():
		var lb: Label = _gen_stage_labels[i]
		var name: String = ["读批注", "起草", "封存", "回标"][i]
		if i < idx:
			lb.text = "✓ " + name
			lb.add_theme_color_override("font_color", Sty.c("ok"))
		elif i == idx:
			lb.text = "● " + name
			lb.add_theme_color_override("font_color", Sty.c("live"))
		else:
			lb.text = "· " + name
			lb.add_theme_color_override("font_color", Sty.c("fg-2"))


func _pending_count() -> int:
	var n := 0
	for a in _app.state.get("annotations", []):
		if a is Dictionary and str(a.get("status", "pending")) == "pending":
			n += 1
	return n


## 顶栏刷新:再生成钮(pending 计数/置灰)+ 拉版本树填页栈
func _refresh_top() -> void:
	if _rail_is_busy("generate"):
		return
	var pending := _pending_count()
	_gen_btn.disabled = pending == 0
	_gen_btn.text = ("再生成(%d)" % pending) if pending > 0 else "再生成"
	_gen_btn.tooltip_text = "批注批处理生成下一版" if pending > 0 else "先右键段落写批注"
	_refresh_tree()


## 版本树数据:打开/快照/生成/回溯后重拉(版本动作才变,不随每次同步刷)
func _refresh_tree() -> void:
	if _svc.client == null:
		return
	var resp := await _svc.client.read_version_tree(_app.get_doc_name())
	if resp.get("ok", false) and resp.get("json") is Dictionary:
		_tree_data = resp["json"]
		_pages.set_versions(_tree_data.get("versions", []), str(_tree_data.get("base", "")))


# ---------------------------------------------------------------- 数据同步

func _on_app_changed(_w: Widget) -> void:
	_sync_all()


func _sync_all() -> void:
	if _app == null or _page == null:
		return
	var doc_name := _app.get_doc_name()
	var text: String = _app.state.get("text", "")
	var title: String = _app.state.get("title", "")

	# 批注计数 + 终态 → 行文末内联标记(状态优先级:pending > outdated > applied > ignored)
	var ann_info := {}
	var priority := {"pending": 4, "outdated": 3, "applied": 2, "ignored": 1}
	for a in _app.state.get("annotations", []):
		if not a is Dictionary:
			continue
		var anchor := str(a.get("anchor", ""))
		if anchor.is_empty():
			continue
		var status := str(a.get("status", "pending"))
		var cur: Dictionary = ann_info.get(anchor, {})
		if cur.is_empty():
			cur = {"count": 0, "status": status} # 初见即自身(否则被假初值锁死,实锤)
		elif int(priority.get(status, 0)) > int(priority.get(str(cur["status"]), 0)):
			cur["status"] = status
		cur["count"] = int(cur["count"]) + 1
		ann_info[anchor] = cur
	_page.set_document(title if not title.is_empty() else doc_name, text,
		_app.state.get("versions", []), ann_info)
	_refresh_top()
	# 书桌态势:版本叠高度 = 版本数;便签板 = 批注(终态变色)
	if _desk != null:
		_desk.set_state((_app.state.get("versions", []) as Array).size(),
			_app.state.get("annotations", []))

	# 变化段晕染(锚点集 = 文本有差异的既有块)
	var changed: Array = []
	for block in MdBlocks.split(text):
		var a: String = block["anchor"]
		if _block_texts.has(a) and _block_texts[a] != block["text"]:
			changed.append(a)
	if not _block_texts.is_empty() and not changed.is_empty():
		_page.flash_blocks(changed)
	_block_texts.clear()
	for block in MdBlocks.split(text):
		_block_texts[block["anchor"]] = block["text"]

	# 批注坞若开着,跟着刷新状态章(内容不冲掉用户正在写的字)
	if _note_dock.visible and not _ed_anchor_name.is_empty():
		_refresh_status()




# ---------------------------------------------------------------- 批注坞(纯用户批注)

func _open_note_editor(anchor: String) -> void:
	if anchor.is_empty():
		return
	_ed_anchor_name = anchor
	_ed_anchor.text = anchor
	var quote := MdBlocks.block_text(_app.state.get("text", ""), anchor)
	if quote.length() > 120:
		quote = quote.left(120) + "…"
	_ed_quote.text = "[i]" + quote.replace("[", "[lb]") + "[/i]"
	var rec := _app.annotation_at(anchor)
	_ed_input.text = str(rec.get("content", ""))
	_refresh_status()
	_note_dock.visible = true
	_note_dock.pivot_offset = _note_dock.size / 2.0
	MotionPlayer.play("dock-in", _note_dock) # 便签坞入场(弹)
	if _desk != null:
		_desk.focus("board") # 镜头偏向便签板
	_ed_input.grab_focus()


func _refresh_status() -> void:
	var rec := _app.annotation_at(_ed_anchor_name)
	# aiNote(生成回标的说明:改了什么/为什么忽略)
	var note := ""
	var gen = rec.get("generation")
	if gen is Dictionary and str(gen.get("aiNote", "")) != "":
		note = str(gen["aiNote"])
	_ed_ainote.text = ("编者注:" + note) if not note.is_empty() else ""
	if rec.is_empty():
		_ed_status.text = "● 新批注(存下即 pending)"
		return
	var status := str(rec.get("status", "pending"))
	var human := {
		"pending": "● 待批处理(pending)",
		"applied": "● 已誊回(applied)",
		"ignored": "● 已忽略(ignored)",
		"outdated": "● 已过时(outdated)",
	}
	_ed_status.text = human.get(status, "● " + status)


func _on_save_annotation() -> void:
	var content := _ed_input.text.strip_edges()
	if content.is_empty() or _ed_anchor_name.is_empty():
		return
	var quote := MdBlocks.block_text(_app.state.get("text", ""), _ed_anchor_name)
	if await _app.save_annotation(_ed_anchor_name, quote, content):
		_refresh_status()
		# 新标记落定 = 弹一下(成功的回声;§FLOWS 总则 6)
		var row := _page.row_of(_ed_anchor_name)
		if row != null:
			MotionPlayer.play("pop-in", row)


func _on_delete_annotation() -> void:
	if _ed_anchor_name.is_empty():
		return
	if await _app.delete_annotation(_ed_anchor_name):
		_note_dock.visible = false
		_ed_anchor_name = ""


# ---------------------------------------------------------------- 动作与动效

func _stamp() -> void:
	# 盖章:朱砂印砸落页角 + 纸面轻震(动作已成功,动画只做确认)
	var seal := _page.seal_control()
	if seal != null:
		MotionPlayer.play("stamp-press", seal)
	var paper := _page.paper_control()
	if paper != null:
		MotionPlayer.play("paper-shake", paper)


## 页栈事件:展开 = 拉该版对当前祖版的 LLM 摘要(difsum 缓存);
## 确认回溯 = 两段已在页内走完,这里直接走管道
func _on_page_hovered(version: String) -> void:
	if _svc.client == null:
		return
	var base := str(_tree_data.get("base", ""))
	if version.is_empty() or version == base:
		_pages.set_summary(version, "当前工作稿祖版")
		return
	if _sum_cache.has(version):
		_pages.set_summary(version, _sum_cache[version])
		return
	var resp := await _svc.client.diff_summary(_app.get_doc_name(), version, base)
	if not resp.get("ok", false):
		_pages.set_summary(version, "摘要暂不可用")
		return
	var text := str((resp["json"] as Dictionary).get("summary", ""))
	_sum_cache[version] = text
	_pages.set_summary(version, text)


func _on_page_rewind(version: String) -> void:
	if await _app.rewind_to(version):
		_refresh_top()
		_page.play_reveal() # 新稿落案:文字重新晕染


func _on_rewind_pick(version: String) -> void:
	_on_page_rewind(version)


static func _esc_bb(s: String) -> String:
	return s.replace("[", "[lb]")


## 降级面的红绿行组装(标题级行差集的呈现)
static func _diff_bbcode(lines: Array) -> String:
	var bb := ""
	var shown := 0
	for ln in lines:
		if shown >= 9:
			break
		var esc := _esc_bb(str(ln.get("text", "")))
		if str(ln.get("sign", "")) == "+":
			bb += "[color=#3fb68b]+ " + esc + "[/color]\n"
		else:
			bb += "[color=#e5534b]− " + esc + "[/color]\n"
		shown += 1
	if shown == 0:
		return "[color=#9aa7ba]与最新版一致[/color]"
	if lines.size() > shown:
		bb += "[color=#5d6b82]… 共 %d 行差异[/color]" % lines.size()
	return bb


func _fetch_version(version: String):
	if _version_texts.has(version):
		return _version_texts[version]
	var resp := await _svc.client.read_doc_version(_app.get_doc_name(), version)
	if not resp.get("ok", false):
		return null
	var text := str((resp["json"] as Dictionary).get("text", ""))
	_version_texts[version] = text
	return text


## 提示级行差集(不重顺序:新增 = 新有旧无;删除 = 旧有新无;版本 diff 全量在后面板)
static func _line_diff(old_text: String, new_text: String) -> Array:
	var pool := {}
	for l in new_text.split("\n"):
		pool[l] = int(pool.get(l, 0)) + 1
	var removed: Array = []
	for l in old_text.split("\n"):
		if int(pool.get(l, 0)) > 0:
			pool[l] = int(pool[l]) - 1
		elif l.strip_edges() != "":
			removed.append(l)
	var pool2 := {}
	for l in old_text.split("\n"):
		pool2[l] = int(pool2.get(l, 0)) + 1
	var added: Array = []
	for l in new_text.split("\n"):
		if int(pool2.get(l, 0)) > 0:
			pool2[l] = int(pool2[l]) - 1
		elif l.strip_edges() != "":
			added.append(l)
	var out: Array = []
	for l in removed:
		out.append({"sign": "-", "text": l})
	for l in added:
		out.append({"sign": "+", "text": l})
	return out


func _on_reply(text: String) -> void:
	var changed: bool = await _app._do_chat(text)
	if changed:
		pass # 变化段晕染在 _sync_all 内完成


func _unhandled_key_input(event: InputEvent) -> void:
	if _app == null or _page == null:
		return
	if event is InputEventKey and event.pressed and not event.echo:
		var idx := _page.index_of(_page.get_current())
		if event.keycode == KEY_S or event.keycode == KEY_DOWN:
			_move_current(idx + 1)
			accept_event()
		elif event.keycode == KEY_W or event.keycode == KEY_UP:
			_move_current(idx - 1)
			accept_event()
		elif event.keycode == KEY_ESCAPE and _vtree.is_open():
			_vtree.close()
			accept_event()


func _move_current(idx: int) -> void:
	if _page.block_count() == 0:
		return
	idx = clampi(idx, 0, _page.block_count() - 1)
	var anchor := _page.block_anchor_at(idx)
	_page.set_current(anchor)
	var row := _page.row_of(anchor)
	if row != null:
		_ensure_visible(row)


func _ensure_visible(row: Control) -> void:
	var p := row.get_parent()
	while p != null:
		if p is ScrollContainer:
			(p as ScrollContainer).ensure_control_visible(row)
			return
		p = p.get_parent()
