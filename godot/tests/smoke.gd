extends SceneTree

## 内核 + Doc Editor 的 headless 冒烟测试:
##   godot --headless --path godot -s res://tests/smoke.gd
## 覆盖:注册闸 / 事件声明纪律 / 寻址 / cascade 注入 / md 分块与锚点 /
##       主题契约 / 动态白名单 / badge 记账 / instance 镜像。网络面不在此(需要真实后端)。

var _failures := 0


func _init() -> void:
	print("== Agent OS Godot 内核冒烟 ==")
	_test_registry_gate()
	_test_event_discipline()
	_test_theme_contract()
	_test_md_blocks()
	_test_tree_and_cascade()
	_test_badge_and_allow()
	_test_instance_mirror()
	if _failures == 0:
		print("== 全部通过 ==")
	else:
		printerr("== %d 项失败 ==" % _failures)
	quit(0 if _failures == 0 else 1)


func check(label: String, cond: bool) -> void:
	if cond:
		print("PASS ", label)
	else:
		_failures += 1
		printerr("FAIL ", label)


class BadDef:
	extends WidgetDef
	func get_kind() -> String: return ""


class SmokeWidgetDef:
	extends WidgetDef
	func get_kind() -> String: return "smoke-widget"
	func get_events() -> Array: return ["ping"]
	func create_instance(state: Dictionary) -> Widget: return SmokeWidget.new(self, state)


class SmokeWidget:
	extends Widget
	func render() -> void: pass


class SmokeCompoundDef:
	extends WidgetDef
	var _allow: Array
	func _init(allow: Array) -> void: _allow = allow
	func get_kind() -> String: return "smoke-compound"
	func create_instance(state: Dictionary) -> Widget: return SmokeCompound.new(self, state, _allow)


class SmokeCompound:
	extends CompoundWidget
	var _allow: Array
	func _init(def: WidgetDef, state: Dictionary, allow: Array) -> void:
		super(def, state)
		_allow = allow
	func get_dynamic_allow() -> Array: return _allow
	func render() -> void: pass


func _test_registry_gate() -> void:
	var reg := WidgetRegistry.new()
	check("缺 kind 拒注册", not reg.register(BadDef.new()))
	check("正常注册", reg.register(DocListDef.new()))
	check("未注册 kind 拒绝创建", reg.create("nope", {}) == null)


func _test_event_discipline() -> void:
	var reg := _make_registry()
	var viewer := reg.create("md-viewer", {})
	check("未声明事件被拒", not viewer.emit_event("nope", {}))
	var got := {"fired": false}
	viewer.widget_event.connect(func(_s, _e, _p) -> void: got["fired"] = true)
	check("已声明事件放行", viewer.emit_event("open-bubble", {"anchor": "doc.md#L1-L1"}))
	check("事件到达订阅者", got["fired"])


func _test_theme_contract() -> void:
	var classic := BuiltInThemes.classic()
	var pixel := BuiltInThemes.pixel()
	check("classic 过契约", ThemeRegistry.validate(classic).is_empty())
	check("pixel 过契约", ThemeRegistry.validate(pixel).is_empty())
	var broken := ThemePack.new()
	broken.id = "broken"
	broken.colors["bg-0"] = Color.BLACK
	check("半成品主题被契约拦下", not ThemeRegistry.validate(broken).is_empty())
	check("契约不过不注册", not ThemeRegistry.register(broken))
	check("classic 注册", ThemeRegistry.register(classic))
	check("pixel 注册", ThemeRegistry.register(pixel))
	ThemeRegistry.apply("classic")
	check("主题为 classic", ThemeRegistry.current.id == "classic")
	check("循环切到 pixel", ThemeRegistry.cycle() == "pixel")
	ThemeRegistry.apply("classic")


func _test_md_blocks() -> void:
	var text := "# 标题\n\n第一段\n\n- 列表项\n\n| a | b |\n\n```\ncode\n```\n"
	var blocks := MdBlocks.split(text)
	check("块数 = 5", blocks.size() == 5)
	check("首块 heading", blocks[0]["kind"] == "heading" and blocks[0]["anchor"] == "doc.md#L1-L1")
	check("段落锚点", blocks[1]["anchor"] == "doc.md#L3-L3")
	check("列表项独占", blocks[2]["kind"] == "list-item" and blocks[2]["anchor"] == "doc.md#L5-L5")
	check("表格行独占", blocks[3]["kind"] == "table-row")
	check("代码块跨行", blocks[4]["kind"] == "code" and blocks[4]["anchor"] == "doc.md#L9-L11")
	check("block_text 取段", MdBlocks.block_text(text, "doc.md#L3-L3") == "第一段")
	check("非法锚点", MdBlocks.parse_anchor("doc.md#L9-L2").is_empty())
	check("越界取段为空", MdBlocks.block_text(text, "doc.md#L88-L99") == "")


func _test_tree_and_cascade() -> void:
	var reg := _make_registry()
	var services := _make_services(reg)
	ThemeRegistry.register(BuiltInThemes.classic())
	ThemeRegistry.restore_saved()
	var tree := WidgetTree.new()
	services.tree = tree

	var app := reg.create("doc-editor", {"name": "t.doc", "text": "# 标题\n\n第一段\n"}) as DocEditorDef.DocEditorApp
	tree.root.add_child_widget(app, "doc-t.doc")
	check("app 寻址", app.path == "/root/doc-t.doc")
	check("slot 子件寻址", app.find_child("viewer").path == "/root/doc-t.doc/viewer")
	check("resolve", tree.resolve("/root/doc-t.doc/viewer") == app.find_child("viewer"))
	check("resolve 缺失为 null", tree.resolve("/root/nope") == null)
	check("read 摘要含文档名", tree.read_summary("/root/doc-t.doc").contains("t.doc"))

	# 经闸门开泡(viewer 发事件 → app 接管)
	var viewer := app.find_child("viewer")
	viewer.emit_event("open-bubble", {"anchor": "doc.md#L3-L3"})
	var bubble := app.find_child_of(ChatBubbleDef.ChatBubbleWidget)
	check("闸门开泡", bubble != null)
	if bubble == null:
		return
	check("气泡寻址在 app 下", bubble.path.begins_with("/root/doc-t.doc/chat-bubble-"))

	var cascade := tree.cascade.build(bubble)
	check("级联三级", cascade.size() == 3)
	check("级联顺序 widget→app→shell",
		cascade[0]["scope"] == "widget" and cascade[1]["scope"] == "app" and cascade[2]["scope"] == "shell")
	check("widget 级注入锚段", cascade[0]["data"].get("paragraph", "") == "第一段")
	check("widget 级注入全文", String(cascade[0]["data"].get("full_text", "")).contains("# 标题"))
	check("app 级带文档名", cascade[1]["data"].get("name", "") == "t.doc")
	check("shell 级带主题", cascade[2]["data"].get("theme", "") == "classic")


func _test_badge_and_allow() -> void:
	var reg := _make_registry()
	reg.register(SmokeWidgetDef.new())
	reg.register(SmokeCompoundDef.new(["smoke-widget"]))
	var comp := reg.create("smoke-compound", {}) as SmokeCompound
	var child := reg.create("smoke-widget", {})
	comp.add_child_widget(child)
	child.emit_event("ping", {"badge": 3})
	check("badge 记账", (comp.state.get("badges", {}) as Dictionary).get(child.id, 0) == 3)
	child.emit_event("ping", {"badge": 0})
	check("badge 摘账", not (comp.state.get("badges", {}) as Dictionary).has(child.id))

	# comp 的 dynamic.allow 只有 smoke-widget → 别的 kind 应被拒
	var odd := reg.create("doc-list", {})
	check("白名单外拒挂", comp.add_child_widget(odd) == null)


func _test_instance_mirror() -> void:
	var app := ActionPipeline.AppInstance.from_json({
		"id": "app-1234abcd", "kind": "doc", "ref": "t.doc", "state": {"name": "t.doc"}})
	check("instance 解析", app.id == "app-1234abcd" and app.state.get("name") == "t.doc")
	app.mirror_from({"id": "app-1234abcd", "state": {"name": "t.doc", "dirty": true}})
	check("镜像回写", app.state.get("dirty", false) == true)


func _make_registry() -> WidgetRegistry:
	var reg := WidgetRegistry.new()
	reg.register(MdViewerDef.new())
	reg.register(ChatBubbleDef.new())
	reg.register(DocListDef.new())
	return reg


func _make_services(reg: WidgetRegistry) -> DocServices:
	var services := DocServices.new()
	services.registry = reg
	reg.register(DocEditorDef.new(services))
	return services
