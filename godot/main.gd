extends Control

## Doc Editor 的 Godot 宿主(对齐 unity/ 的 DocEditorWindow 职责):
## 装配内核(注册表/树/管道/主题)、挂 app 子件、宿主义务(连接、状态栏、主题切换)。
## 装配纪律(docs/APP-MODEL.md §17.10):框架备参,动作语义全在服务端。

const CONFIG_PATH := "user://agent-os-godot.cfg"

var _client: AgentOsClient
var _pipeline: ActionPipeline
var _registry: WidgetRegistry
var _tree: WidgetTree
var _services: DocServices

var _doc_list: Widget
var _doc_app: DocEditorDef.DocEditorApp
var _letter_host: LetterHost
var _mode := "scene" # scene = 3D 文档塔(默认);panel = 经典面板(精确/无障碍回落)
var _server_field: LineEdit
var _status_dot: Control
var _status_label: Label
var _theme_button: Button
var _mode_button: Button
var _main_host: Control
var _body_split: HSplitContainer
var _shot_path := ""

const UI_FONT := preload("res://fonts/NotoSansSC-Regular.otf")


func _ready() -> void:
	_apply_ui_font() # 代码侧挂窗口主题(project.godot 的 gui/theme/default_font 未生效的实锤绕行)
	_build_kernel()
	_build_chrome()
	ThemeRegistry.add_listener(_on_theme_changed)
	_load_doc_list()
	# 调试/演示直通:godot/run-local.sh -- --open-doc <name> [--shot <path>]
	#   [--open-note doc.md#L3-L3] 展开便签  [--open-fan] 打开版本叠
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		if args[i] == "--open-doc" and i + 1 < args.size():
			_open_doc.call_deferred(args[i + 1])
		elif args[i] == "--shot" and i + 1 < args.size():
			_shot_path = args[i + 1]
		elif args[i] == "--open-note" and i + 1 < args.size():
			var anchor := args[i + 1]
			get_tree().create_timer(2.0).timeout.connect(func() -> void:
				if _letter_host != null:
					_letter_host._open_note_editor(anchor))
		elif args[i] == "--open-fan" and i + 1 < args.size():
			var page := args[i + 1]
			get_tree().create_timer(2.2).timeout.connect(func() -> void:
				if _letter_host != null:
					_letter_host._pages.expand_version(page))
		elif args[i] == "--fan-click": # 注入真实点击,复盘按钮链路(等文档加载完)
			get_tree().create_timer(4.0).timeout.connect(_fan_click_probe)
		elif args[i] == "--open-tree":
			get_tree().create_timer(2.2).timeout.connect(func() -> void:
				if _letter_host != null:
					_letter_host._vtree.open(_letter_host._tree_data.get("versions", []),
						str(_letter_host._tree_data.get("base", ""))))
	if not _shot_path.is_empty():
		_take_shot()


## 全窗口 2D 控件的字体:挂 Window.theme(级联到所有子控件;我们从不逐控件
## 覆盖字体,只覆盖字号/颜色,所以一发命中)。Label3D 等 3D 文本自带字体面。
func _apply_ui_font() -> void:
	var theme := Theme.new()
	theme.default_font = UI_FONT
	get_window().theme = theme


## 自拍:等界面与文档就绪后,把根视口存成 PNG(无 GPU 环境走 llvmpipe 也能出图)
func _take_shot() -> void:
	# 等文档打开 + 场景稳定(导航/补间落地)
	await get_tree().create_timer(3.0).timeout
	await get_tree().process_frame
	await get_tree().process_frame
	if OS.is_debug_build():
		_debug_layout()
	var img := get_tree().root.get_texture().get_image()
	var err := img.save_png(_shot_path)
	print("SHOT saved to %s (err=%d)" % [_shot_path, err])
	quit_requested.call_deferred()


## --fan-click 探针(全仿真):先注入 motion 到页上(走 mouse_entered 展开),
## 再移到按钮两击——复现"按钮点了没用"类报告用
func _fan_click_probe() -> void:
	if _letter_host == null:
		print("[click] letter_host 未就绪")
		return
	var pages := _letter_host._pages
	pages.rewind_requested.connect(func(v: String) -> void: print("[click] rewind_requested -> ", v))
	# 找一枚非当前版页(v002 起的第一枚旧版)
	var idx := -1
	for i in pages._page_versions.size():
		if pages._page_versions[i] != pages._base:
			idx = i
			break
	if idx < 0:
		print("[click] 无旧版页可测")
		return
	var page: Control = pages._pages[idx]
	var pc := page.get_global_rect().get_center()
	print("[click] hover page ", pages._page_versions[idx], " at ", pc)
	Input.warp_mouse(pc) # 合成 motion 不更新引擎鼠标位(实锤);warp 走系统层才真
	await get_tree().create_timer(0.4).timeout
	var hovered := get_window().gui_get_hovered_control()
	print("[click] mouse_pos=", get_window().get_mouse_position(),
		" hovered=", hovered.name if hovered else "null",
		" page_z=", page.z_index, " page_filter=", page.mouse_filter)
	var btn: Button = pages._detail_btn
	btn.pressed.connect(func() -> void: print("[click] PRESSED fired"))
	print("[click] detail rect=", pages._detail.get_global_rect(), " detail path=", pages._detail.get_path())
	for ch in pages._detail.get_child(0).get_children():
		print("[click]   card-child ", ch.name, " rect=", (ch as Control).get_global_rect())
	print("[click] detail_visible=", pages._detail.visible,
		" btn rect=", btn.get_global_rect(), " is_visible_in_tree=", btn.is_visible_in_tree())
	_inject_click(btn.get_global_rect().get_center())
	await get_tree().process_frame
	var hov2 := get_window().gui_get_hovered_control()
	print("[click] click-point hovered=", hov2.name if hov2 else "null",
		" path=", hov2.get_path() if hov2 else "?",
		" mouse=", get_window().get_mouse_position())
	await get_tree().create_timer(0.3).timeout
	print("[click] after1 text='", btn.text, "' armed=", pages._armed,
		" detail_visible=", pages._detail.visible)
	_inject_click(btn.get_global_rect().get_center())
	await get_tree().create_timer(0.6).timeout
	print("[click] after2 armed=", pages._armed, " detail_visible=", pages._detail.visible)
	# 对照组:同一注入法点顶栏「版本树」钮(普通布局),探针可信度校验
	print("[click] btn mouse_filter=", btn.mouse_filter, " disabled=", btn.disabled)
	_letter_host._tree_btn.pressed.connect(func() -> void: print("[click] TREE-BTN PRESSED fired"))
	print("[click] 对照:点版本树钮 at ", _letter_host._tree_btn.get_global_rect().get_center())
	_inject_click(_letter_host._tree_btn.get_global_rect().get_center())
	await get_tree().create_timer(0.5).timeout
	print("[click] 对照后 vtree_open=", _letter_host._vtree.is_open())


func _inject_click(global_pos: Vector2) -> void:
	Input.warp_mouse(global_pos)
	var down := InputEventMouseButton.new()
	down.button_index = MOUSE_BUTTON_LEFT
	down.pressed = true
	down.position = global_pos
	down.global_position = global_pos
	Input.parse_input_event(down)
	await get_tree().process_frame # 按下与松开拆帧,像真实点击
	var up := InputEventMouseButton.new()
	up.button_index = MOUSE_BUTTON_LEFT
	up.pressed = false
	up.position = global_pos
	up.global_position = global_pos
	Input.parse_input_event(up)


func quit_requested() -> void:
	get_tree().quit(0)


## 布局自检(--shot 时随带打印;诊断"看不见的容器"类问题)
func _debug_layout() -> void:
	var nodes := {
		"main_host": _main_host,
		"letter_host": _letter_host,
		"doc_list_root": _doc_list.root,
	}
	for k in nodes:
		var c: Control = nodes[k]
		if c != null:
			print("LAYOUT %s rect=%s visible=%s" % [k, c.get_global_rect(), c.is_visible_in_tree()])
	if _letter_host != null:
		print("LAYOUT letter_host children=%d" % _letter_host.get_child_count())
		print("LAYOUT page rect=%s" % [_letter_host._page.get_global_rect()])
		print("LAYOUT note_dock visible=%s" % _letter_host._note_dock.visible)
	if _doc_list != null:
		print("LAYOUT doc_list items=%d root_children=%d" % [
			(_doc_list.state.get("items", []) as Array).size(), _doc_list.root.get_child_count()])
		var lh: Control = _doc_list.get("_list_host")
		if lh != null:
			var vb := lh.get_parent().get_parent() as Control # sc 的外层 vbox
			print("LAYOUT list_vbox rect=%s" % [vb.get_global_rect()])
			print("LAYOUT list_host rect=%s children=%d" % [lh.get_global_rect(), lh.get_child_count()])
			if lh.get_child_count() > 0:
				var row0 := lh.get_child(0) as Control
				print("LAYOUT row0 rect=%s visible=%s modulate=%s" % [
					row0.get_global_rect(), row0.is_visible_in_tree(), row0.modulate])
				var sc := lh.get_parent() as Control
				print("LAYOUT sc rect=%s clip=%s sc_visible=%s" % [
					sc.get_global_rect(), sc.get("clip_contents"), sc.is_visible_in_tree()])
				if row0.get_child_count() > 0:
					var vb0 := row0.get_child(0) as Control
					if vb0.get_child_count() > 0:
						var lab := vb0.get_child(0) as Label
						print("LAYOUT row0.label text=%s rect=%s" % [lab.text, lab.get_global_rect()])


func _build_kernel() -> void:
	# 主题:契约校验不过不注册(防半成品);恢复上次选择
	ThemeRegistry.register(BuiltInThemes.classic())
	ThemeRegistry.register(BuiltInThemes.pixel())
	ThemeRegistry.restore_saved()

	_registry = WidgetRegistry.new()
	_tree = WidgetTree.new()
	_client = AgentOsClient.new()
	_client.base_url = _load_server_pref()
	add_child(_client) # HTTPRequest 需要在 SceneTree 内才工作
	_pipeline = ActionPipeline.new(_client, _tree)
	_services = DocServices.new()
	_services.client = _client
	_services.pipeline = _pipeline
	_services.registry = _registry
	_services.tree = _tree
	_services.export_copy = func(t: String) -> void: DisplayServer.clipboard_set(t)
	_services.report = _set_status

	_registry.register(DocListDef.new())
	_registry.register(MdViewerDef.new())
	_registry.register(ChatBubbleDef.new())
	_registry.register(DocEditorDef.new(_services))

	_doc_list = _registry.create("doc-list", {"items": [], "filter": "", "selected": ""})
	_doc_list.widget_event.connect(_on_doc_list_event)

	_letter_host = LetterHost.new(_services)


func _build_chrome() -> void:
	# 先摘下存活中的 widget/宿主视图(queue_free 父容器会连坐)
	if _doc_list != null and _doc_list.root.get_parent() != null:
		_doc_list.root.get_parent().remove_child(_doc_list.root)
	if _doc_app != null and _doc_app.root.get_parent() != null:
		_doc_app.root.get_parent().remove_child(_doc_app.root)
	if _letter_host != null and _letter_host.get_parent() != null:
		_letter_host.get_parent().remove_child(_letter_host)
	for c in get_children():
		if c != _client:
			remove_child(c)
			c.queue_free()

	var bg := ColorRect.new()
	bg.color = Sty.c("bg-0")
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(bg)

	var root_box := Sty.vbox()
	root_box.set_anchors_preset(Control.PRESET_FULL_RECT)
	root_box.offset_left = 10
	root_box.offset_right = -8
	root_box.add_theme_constant_override("separation", 8)
	add_child(root_box)

	# 顶栏:server · 连接状态 · 主题切换(托盘语义)
	var top := Sty.hbox()
	top.add_child(Sty.label("Agent OS", "text-lg", "fg-0", true))
	_server_field = Sty.line_edit("http://127.0.0.1:8391")
	_server_field.custom_minimum_size.x = 240
	_server_field.text = _client.base_url
	_server_field.text_submitted.connect(_on_server_changed)
	_server_field.focus_exited.connect(func() -> void: _on_server_changed(_server_field.text))
	top.add_child(_server_field)
	var refresh := Sty.btn("刷新列表")
	refresh.pressed.connect(_load_doc_list)
	top.add_child(refresh)
	_status_dot = Sty.dot("fg-2", 10)
	_status_dot.tooltip_text = "后端连接状态"
	top.add_child(_status_dot)
	top.add_child(Sty.spacer())
	_mode_button = Sty.btn("视图:" + ("信件" if _mode == "scene" else "面板"))
	_mode_button.tooltip_text = "信件 = 信纸阅览+页边便签(docs/GAME-UI-DOC.md);面板 = 经典编辑器(精确操作/无障碍回落)"
	_mode_button.pressed.connect(func() -> void:
		_mode = "panel" if _mode == "scene" else "scene"
		_mode_button.text = "视图:" + ("信件" if _mode == "scene" else "面板")
		_present())
	top.add_child(_mode_button)
	_theme_button = Sty.btn("主题:" + ThemeRegistry.current.display_name)
	_theme_button.pressed.connect(func() -> void:
		ThemeRegistry.cycle()) # 顺序循环,与 web 托盘同语义
	top.add_child(_theme_button)
	root_box.add_child(top)

	# 主体:左列表 + 右 app 面
	_body_split = HSplitContainer.new()
	_body_split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_body_split.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	root_box.add_child(_body_split)

	var left := Sty.panel("bg-1")
	left.custom_minimum_size.x = 260
	left.size_flags_vertical = Control.SIZE_EXPAND_FILL
	left.add_child(_doc_list.root)
	_doc_list.render()
	_body_split.add_child(left)

	_main_host = Sty.panel("bg-0")
	_main_host.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_main_host.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_main_host.add_child(Sty.label("← 选一篇文档,或新建(打开即见信纸)", "text-sm", "fg-2"))
	_body_split.add_child(_main_host)

	# 状态栏
	_status_label = Sty.label("", "text-xs", "fg-2")
	root_box.add_child(_status_label)


func _on_theme_changed() -> void:
	_theme_button.text = "主题:" + ThemeRegistry.current.display_name
	# 主题 = 数据包,组件零分支:全量重渲即换肤(state 在 widget,不丢)
	_build_chrome()
	_load_doc_list()
	if _doc_app != null:
		_doc_app.render()
		_present()


func _exit_tree() -> void:
	ThemeRegistry.remove_listener(_on_theme_changed)


# ---------------------------------------------------------------- 文档流

func _load_doc_list() -> void:
	var resp := await _client.list_docs()
	if resp.get("ok", false):
		_doc_list.mutate_state(func(s: Dictionary) -> void:
			s["items"] = resp["json"] if resp["json"] is Array else [])
		_doc_list.render()
		_set_conn(true)
	else:
		_set_conn(false)
		_set_status("连不上后端(%s):%s —— 先用 instance/run-web.sh 起服务"
			% [_client.base_url, str(resp.get("error", ""))], "danger")


func _on_doc_list_event(_w: Widget, evt_name: String, payload: Dictionary) -> void:
	if evt_name == "select":
		_open_doc(str(payload.get("name", "")))
	elif evt_name == "create":
		_create_doc(str(payload.get("name", "")))


func _create_doc(doc_name: String) -> void:
	var resp := await _client.create_doc(doc_name)
	if resp.get("ok", false):
		_set_status("已创建 " + doc_name, "ok")
		_open_doc(doc_name)
	else:
		_set_status("重名:" + doc_name if int(resp.get("status", 0)) == 409
			else "创建失败:" + str(resp.get("error", "")), "danger")


func _open_doc(doc_name: String) -> void:
	if doc_name.is_empty():
		return
	_set_status("打开 " + doc_name + " …", "info")
	var doc := await _client.read_doc(doc_name)
	if not doc.get("ok", false):
		_set_status("打开失败:" + str(doc.get("error", "")), "danger")
		return
	var bubbles := await _client.read_bubbles(doc_name)
	var annotations := await _client.read_annotations(doc_name)
	# spawn = app 管道锚(kind+ref 去重;state 过服务端 schema 闸)
	var spawned := await _client.spawn("doc", doc_name, doc_name,
		{"name": doc_name, "dirty": false, "view": "split"}, "godot-editor")
	if not spawned.get("ok", false) or not spawned.has("app"):
		_set_status("spawn 失败:" + str(spawned.get("error", "")), "danger")
		return

	# 旧 app 离树(关闭 = remove_child;重开是全新 instance,DESKTOP §7 语义)
	if _doc_app != null:
		_tree.root.remove_child_widget(_doc_app.id, true)
		_doc_app = null

	var app := _registry.create("doc-editor", {"name": doc_name}) as DocEditorDef.DocEditorApp
	app.attach_server_app(spawned["app"])
	app.apply_doc_json(doc["json"], AgentOsClient.json_or(bubbles, []),
		AgentOsClient.json_or(annotations, []))
	_tree.root.add_child_widget(app, "doc-" + doc_name)
	_doc_app = app
	_doc_app.render()
	_present()
	_set_status("已打开 %s(instance %s)" % [doc_name, spawned["app"].id], "ok")


## 当前视图模式呈现 app:场景 = 信件(信纸/便签/版本叠);面板 = 经典编辑器(回落面)
func _present() -> void:
	if _doc_app == null:
		return
	if _mode == "scene":
		_fill_main(_letter_host)
		_letter_host.bind(_doc_app)
	else:
		_fill_main(_doc_app.root)


func _fill_main(content: Control) -> void:
	if content.get_parent() == _main_host:
		_main_host.remove_child(content) # 换肤重挂:先摘,清完再放回
	for c in _main_host.get_children():
		_main_host.remove_child(c)
		# widget/宿主自有视图只摘不毁(生命归 widget;chrome 占位标签才 queue_free)
		if _doc_app != null and c == _doc_app.root:
			continue
		if c == _letter_host:
			continue
		c.queue_free()
	_main_host.add_child(content)


# ---------------------------------------------------------------- 宿主义务

func _on_server_changed(text: String) -> void:
	_client.base_url = text.strip_edges().rstrip("/")
	var cfg := ConfigFile.new()
	cfg.load(CONFIG_PATH)
	cfg.set_value("net", "server", _client.base_url)
	cfg.save(CONFIG_PATH)


func _load_server_pref() -> String:
	var cfg := ConfigFile.new()
	if cfg.load(CONFIG_PATH) != OK:
		return "http://127.0.0.1:8391"
	return str(cfg.get_value("net", "server", "http://127.0.0.1:8391"))


func _set_conn(ok: bool) -> void:
	if _status_dot == null:
		return
	# 色点 + tooltip 文字 = 双编码
	(_status_dot.get_theme_stylebox("panel") as StyleBoxFlat).bg_color = Sty.c("live" if ok else "warn")
	_status_dot.tooltip_text = "已连接" if ok else "连接失败"


func _set_status(msg: String, tone: String) -> void:
	if _status_label == null:
		return
	_status_label.text = msg
	_status_label.add_theme_color_override("font_color",
		Sty.c("danger" if tone == "danger" else "ok" if tone == "ok" else "fg-2"))
	MotionPlayer.play("status-in", _status_label) # 状态到达 = 淡入强调(文字是通道)
