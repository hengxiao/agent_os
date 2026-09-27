extends SceneTree

## 信件视图(信纸 + 内联批注 + 批注坞 + 时间卷轴)的 in-tree 冒烟:
##   godot --headless --path godot/ -s res://tests/scene_smoke.gd
## 不依赖后端:用构造的 doc JSON 走真实 bind/render/sync 路径,专抓运行期错误。

var _failures := 0
var _started := false


func _process(_delta: float) -> bool:
	if _started:
		return false
	_started = true
	_run()
	return false


func check(label: String, cond: bool) -> void:
	if cond:
		print("PASS ", label)
	else:
		_failures += 1
		printerr("FAIL ", label)


func _run() -> void:
	print("== 信件视图冒烟 ==")
	ThemeRegistry.register(BuiltInThemes.classic())
	ThemeRegistry.restore_saved()

	var registry := WidgetRegistry.new()
	var tree := WidgetTree.new()
	var services := DocServices.new()
	services.registry = registry
	services.tree = tree
	registry.register(MdViewerDef.new())
	registry.register(ChatBubbleDef.new())
	registry.register(DocListDef.new())
	registry.register(DocEditorDef.new(services))

	var app := registry.create("doc-editor", {"name": "s.doc"}) as DocEditorDef.DocEditorApp
	app.apply_doc_json({
		"name": "s.doc", "text": "# 标题\n\n第一段\n\n第二段\n", "meta": {},
		"versions": ["v002", "v001"], "chat": [],
	}, [], [
		{"anchor": "doc.md#L3-L3", "quote": "第一段", "content": "这段太绕", "status": "pending"},
	])
	tree.root.add_child_widget(app, "doc-s.doc")

	var host := LetterHost.new(services)
	get_root().add_child(host)
	host.bind(app)

	check("信纸块数 = 3", host._page.block_count() == 3)
	# 内联标记:行文末尾带 [注×N] meta(L3 有一条批注)
	var row := host._page.row_of("doc.md#L3-L3")
	check("锚点段存在", row != null)
	var rich := row.get_child(0) as RichTextLabel
	check("行文内联批注标记", rich.text.contains("注×1") and rich.text.contains("url=doc.md#L3-L3"))
	var row2 := host._page.row_of("doc.md#L1-L1")
	check("无批注段不带标记", not (row2.get_child(0) as RichTextLabel).text.contains("注×"))

	# 批注坞:开签 = 引文 + 已有批注内容,纯用户批注(无对话面)
	host._open_note_editor("doc.md#L3-L3")
	check("批注坞可见", host._note_dock.visible)
	check("批注内容预填", host._ed_input.text == "这段太绕")
	check("状态章 pending", host._ed_status.text.contains("pending"))

	# 版本页栈(顶栏主面):页数/当前标记/悬停展开/两段确认回溯
	var entries := [
		{"version": "v002", "parent": "v001", "at": 100.0, "source": "generate"},
		{"version": "v001", "parent": "", "at": 50.0, "source": "manual"},
	]
	host._pages.set_versions(entries, "v002")
	check("页栈页数 = 版本数", host._pages.page_count() == 2)
	var rewind_to: Array = []
	host._pages.rewind_requested.connect(func(v: String) -> void: rewind_to.append(v))
	host._pages.expand_version("v001")
	check("悬停展开详情卡", host._pages._detail.visible)
	check("详情卡挂独立拾取层", host._pages._detail.get_parent() is CanvasLayer)
	host._pages.set_summary("v001", "这一版加了翻译对照表")
	check("摘要进详情卡", host._pages._detail_summary.text.contains("翻译对照表"))
	host._pages._on_rewind_click() # 第一下 = 预持
	check("回溯首击预持", rewind_to.is_empty())
	host._pages._on_rewind_click() # 第二下 = 发出
	check("回溯再击发出", rewind_to == ["v001"])
	# 当前版不给回溯钮
	host._pages.expand_version("v002")
	check("当前版无回溯钮", not host._pages._detail_btn.visible)

	# 版本树:节点数/选中发射
	var chosen: Array = []
	host._vtree.version_chosen.connect(func(v: String) -> void: chosen.append(v))
	host._vtree.open(entries, "v002")
	await host.get_tree().process_frame # open 在 size 未出时等两帧布局,这里让出帧拍齐
	await host.get_tree().process_frame
	await host.get_tree().process_frame
	check("版本树节点", host._vtree.node_count() == 2)
	host._vtree.close()

	# 行差集保留(摘要降级面用)
	var diff := LetterHost._line_diff("甲\n乙\n丙", "甲\n丁\n丙\n戊")
	check("行差集:删旧增新", diff == [{"sign": "-", "text": "乙"}, {"sign": "+", "text": "丁"}, {"sign": "+", "text": "戊"}])
	check("空差集给明示", LetterHost._diff_bbcode([]).contains("一致"))

	# ── 批注终态着色 / aiNote / 核心循环轨道 / 印钮五态(FLOWS §0.1/§10)──
	# pending 标记色(琥珀)
	var rich0 := (host._page.row_of("doc.md#L3-L3").get_child(0)) as RichTextLabel
	check("pending 标记色", rich0.text.contains("#8a6d1f"))
	# 再生成钮(annotations 尚有 pending → 可用 + 计数文案)
	host._refresh_top()
	check("有 pending 再生成可用", not host._gen_btn.disabled and host._gen_btn.text.contains("1"))
	# 终态 applied + aiNote
	app.mutate_state(func(s: Dictionary) -> void:
		s["annotations"] = [{"anchor": "doc.md#L3-L3", "content": "这段太绕", "status": "applied",
			"generation": {"aiNote": "已拆成两句"}}])
	host._sync_all()
	var rich1 := (host._page.row_of("doc.md#L3-L3").get_child(0)) as RichTextLabel
	check("applied 标记变绿", rich1.text.contains("#2e8b57"))
	host._open_note_editor("doc.md#L3-L3")
	check("aiNote 进批注坞", host._ed_ainote.text.contains("已拆成两句"))
	# pending 清零 → 再生成置灰
	host._refresh_top()
	check("无 pending 再生成置灰", host._gen_btn.disabled)
	# 印钮五态机
	host._rail_state("save", "busy")
	check("busy 拒重入", host._rail_btns["save"].disabled)
	host._rail_state("save", "fail")
	check("fail 红重试", host._rail_btns["save"].text == "重试" and not host._rail_btns["save"].disabled)
	host._rail_state("save", "idle")
	check("idle 复原", host._rail_btns["save"].text == "保存")

	# ── 手感层(FLOWS v1.5):闸与接线 ──
	var jb := Sty.btn("试")
	check("按钮接手感线", jb.button_down.get_connections().size() > 0)
	ThemeRegistry.reduced_motion = true
	check("reduced-motion 手感全关", not MotionPlayer.allowed("ui-press") and not MotionPlayer.allowed("tick"))
	ThemeRegistry.reduced_motion = false
	check("默认手感开", MotionPlayer.allowed("ui-press"))
	MotionPlayer.play("pop-in", host._page.row_of("doc.md#L1-L1"))
	MotionPlayer.play("dock-in", host._note_dock)
	check("在树动效不炸", true)

	# ── 2.5D 书桌(空间/态势层;v1.7)──
	check("版本叠入景", host._desk._stack_holder.get_child_count() == 2) # versions v002/v001
	var notes := 0
	for c in host._desk._board_holder.get_children():
		if c.name == "note":
			notes += 1
	check("便签板上墙", notes == 1)
	host._desk.focus("stack")
	host._desk.drop_sheet()
	check("镜头调度与新页落叠不炸", true)

	if _failures == 0:
		print("== 信件冒烟全部通过 ==")
	else:
		printerr("== %d 项失败 ==" % _failures)
	quit(0 if _failures == 0 else 1)
