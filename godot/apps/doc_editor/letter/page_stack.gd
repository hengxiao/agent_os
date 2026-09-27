class_name PageStack
extends Control

## 版本页栈(GAME-UI-FLOWS v1.8;顶栏主件):从当前版本回推 ≤20 版,
## 每版一枚小页面,左→右部分重叠;悬停 = 翻书展开(抬高放大 + 详情卡:
## 时间/来源/与最新版的 LLM 摘要 + 「回到这版」两段确认)。
## 页面自由摆放(手工定位;容器会铺满子件,教训不重复)。

signal rewind_requested(version: String) # 两段确认后发出(宿主接 doc.rewind)
signal page_hovered(version: String)       # 展开(宿主补摘要后 set_summary)

const PAGE_W := 64.0
const PAGE_H := 84.0
const STEP := 46.0

var _entries: Array = []
var _base := ""
var _pages: Array = []      # [PanelContainer]
var _page_versions: Array = []
var _detail: PanelContainer
var _detail_title: Label
var _detail_meta: Label
var _detail_summary: RichTextLabel
var _detail_btn: Button
var _detail_for := ""
var _armed := ""
var _outside := 0.0        # 鼠标在页/卡之外的累计时长(宽限计时)
var _ever_inside := false  # 曾悬停过才启用自动收拢(--open-fan 自拍不被收)
const HIDE_GRACE := 0.24   # 宽限秒数:穿过页与详情卡之间的缝不没收


func _ready() -> void:
	_build()


func _build() -> void:
	custom_minimum_size.y = PAGE_H + 16.0
	# 详情卡挂独立 CanvasLayer:z_index 只管绘制不管 GUI 拾取(实锤:
	# 页栈树序在信纸前,卡内按钮被树序更后的信纸件挡住,点击全落空)——
	# CanvasLayer 的分层对绘制与拾取同时生效,是弹层的正解
	var layer := CanvasLayer.new()
	layer.layer = 10
	add_child(layer)
	_detail = PanelContainer.new()
	var dsb := StyleBoxFlat.new()
	dsb.bg_color = Sty.c("bg-2")
	dsb.border_color = Sty.c("line-strong")
	dsb.set_border_width_all(1)
	dsb.set_corner_radius_all(6)
	dsb.set_content_margin_all(10)
	dsb.shadow_color = Color(0, 0, 0, 0.5)
	dsb.shadow_size = 10
	dsb.shadow_offset = Vector2(3, 5)
	_detail.add_theme_stylebox_override("panel", dsb)
	_detail.custom_minimum_size = Vector2(250, 0)
	_detail.visible = false
	_detail.z_index = 200
	var vb := VBoxContainer.new()
	_detail_title = Sty.label("", "text-md", "fg-0", true)
	vb.add_child(_detail_title)
	_detail_meta = Sty.label("", "text-xs", "fg-2")
	vb.add_child(_detail_meta)
	_detail_summary = RichTextLabel.new()
	_detail_summary.bbcode_enabled = true
	_detail_summary.fit_content = true
	_detail_summary.scroll_active = false
	_detail_summary.add_theme_font_size_override("normal_font_size", Sty.s("text-xs"))
	_detail_summary.add_theme_color_override("default_color", Sty.c("fg-1"))
	vb.add_child(_detail_summary)
	_detail_btn = Sty.btn("回到这版", true)
	_detail_btn.pressed.connect(_on_rewind_click)
	vb.add_child(_detail_btn)
	_detail.add_child(vb)
	layer.add_child(_detail)


## entries:[{version, at, source}],新→旧;base = 当前工作稿祖版
func set_versions(entries: Array, base: String) -> void:
	_base = base
	_entries = entries.slice(0, 20)
	for p in _pages:
		p.queue_free()
	_pages.clear()
	_page_versions.clear()
	for i in _entries.size():
		_make_page(i, _entries[i])
	_hide_detail()


func page_count() -> int:
	return _pages.size()


func _make_page(i: int, entry: Dictionary) -> void:
	var version := str(entry.get("version", ""))
	var page := PanelContainer.new()
	page.custom_minimum_size = Vector2(PAGE_W, PAGE_H)
	page.position = Vector2(i * STEP, 4)
	page.z_index = i # 左(新)压右(旧)
	var sb := StyleBoxFlat.new()
	sb.bg_color = Sty.c("paper-0")
	sb.border_color = Sty.c("paper-line")
	sb.set_border_width_all(1)
	sb.set_corner_radius_all(3)
	sb.set_content_margin_all(6)
	sb.shadow_color = Color(0, 0, 0, 0.25)
	sb.shadow_size = 4
	sb.shadow_offset = Vector2(2, 2)
	page.add_theme_stylebox_override("panel", sb)
	var vb := VBoxContainer.new()
	var t := Label.new()
	t.text = version
	t.add_theme_font_size_override("font_size", Sty.s("text-xs"))
	t.add_theme_color_override("font_color", Sty.c("paper-ink"))
	vb.add_child(t)
	var time_label := Label.new()
	time_label.text = _rel_time(float(entry.get("at", 0)))
	time_label.add_theme_font_size_override("font_size", 9)
	time_label.add_theme_color_override("font_color", Sty.c("paper-ink-2"))
	vb.add_child(time_label)
	if str(entry.get("source", "")) == "generate":
		var src := Label.new()
		src.text = "生成"
		src.add_theme_font_size_override("font_size", 8)
		src.add_theme_color_override("font_color", Sty.c("seal"))
		vb.add_child(src)
	if version == _base:
		var cur := Label.new()
		cur.text = "● 当前"
		cur.add_theme_font_size_override("font_size", 8)
		cur.add_theme_color_override("font_color", Sty.c("ok"))
		vb.add_child(cur)
	page.add_child(vb)
	page.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	var v := version
	page.mouse_entered.connect(func() -> void: _expand(v))
	page.gui_input.connect(func(event: InputEvent) -> void:
		if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
			_expand(v)
			page.accept_event())
	add_child(page)
	_pages.append(page)
	_page_versions.append(version)


func _expand(version: String) -> void:
	_ever_inside = true
	_outside = 0.0
	for i in _pages.size():
		_pose_page(_pages[i], _page_versions[i] == version, i)
	_show_detail(version)
	page_hovered.emit(version)


## 单页定姿:展开/复位同路径;先杀在跑的旧 tween——快速划过时收拢设值
## 会被未完的展开动画抢回去(页翘着不复位,实锤类)
func _pose_page(page: PanelContainer, on: bool, idx: int) -> void:
	var target_scale := Vector2(1.35, 1.35) if on else Vector2.ONE
	var target_y := -8.0 if on else 4.0
	page.z_index = 150 if on else idx
	page.pivot_offset = Vector2(PAGE_W / 2.0, PAGE_H)
	if page.has_meta("tw"): # get_meta 的 default 参在 4.7 仍对缺 key 报错(实锤)
		var old_tw: Tween = page.get_meta("tw")
		if old_tw != null and old_tw.is_valid():
			old_tw.kill()
	if is_inside_tree() and MotionPlayer.allowed("page-fan"):
		var tw := page.create_tween()
		tw.set_parallel(true)
		tw.tween_property(page, "scale", target_scale, 0.14).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
		tw.tween_property(page, "position:y", target_y, 0.14)
		page.set_meta("tw", tw)
	else:
		page.scale = target_scale
		page.position.y = target_y


func _show_detail(version: String) -> void:
	_detail_for = version
	_armed = ""
	_detail_btn.text = "回到这版"
	_detail_btn.visible = version != _base
	var entry := _find_entry(version)
	_detail_title.text = version + ("(当前)" if version == _base else "")
	_detail_meta.text = "%s · %s" % [_source_name(str(entry.get("source", ""))),
		_rel_time(float(entry.get("at", 0)))]
	_detail_summary.text = "[color=#5d6b82]摘要加载中…[/color]"
	# 详情卡贴在页下方(CanvasLayer 内用全局坐标)
	var idx := _page_versions.find(version)
	if idx >= 0:
		var origin := get_global_rect().position
		var px: float = _pages[idx].position.x
		_detail.global_position = Vector2(
			origin.x + clampf(px - 60, 0, maxf(size.x - 260, 0)),
			origin.y + PAGE_H + 8)
	_detail.visible = true


## 收拢判定按鼠标真实位置(每帧):entered/exited 时序在「页 → 缝 → 详情卡」
## 的途中必先把卡没收(实锤);宽限 HIDE_GRACE 秒内容忍穿缝,超时才收。
func _process(delta: float) -> void:
	if not _detail.visible:
		_outside = 0.0
		return
	if _pointer_inside():
		_outside = 0.0
		_ever_inside = true
		return
	if not _ever_inside:
		return
	_outside += delta
	if _outside >= HIDE_GRACE:
		_hide_detail()


func _pointer_inside() -> bool:
	var mp := get_global_mouse_position()
	if _detail.visible and _detail.get_global_rect().has_point(mp):
		return true
	for p in _pages:
		if (p as Control).get_global_rect().has_point(mp):
			return true
	return false


func _hide_detail() -> void:
	_detail.visible = false
	_detail_for = ""
	_armed = ""
	_ever_inside = false
	# 收拢时页面一并复位(原 bug:移开后页还翘着,直到下次展开)
	for i in _pages.size():
		_pose_page(_pages[i], false, i)


## 宿主回灌摘要(diffsum 缓存面,命中即回)
func set_summary(version: String, text: String) -> void:
	if version == _detail_for:
		_detail_summary.text = "[color=#9aa7ba]" + text.replace("[", "[lb]") + "[/color]"


func _on_rewind_click() -> void:
	if _detail_for.is_empty():
		return
	if _armed != _detail_for: # 两段确认(改写工作副本,谨慎动作配两段)
		_armed = _detail_for
		_detail_btn.text = "确认回到 " + _detail_for + "?"
		return
	var v := _detail_for
	_armed = ""
	_hide_detail()
	rewind_requested.emit(v)


## 版本树选了某版 → 展开对应页(宿主接线)
func expand_version(version: String) -> void:
	if _page_versions.has(version):
		_expand(version)


func _find_entry(version: String) -> Dictionary:
	for e in _entries:
		if str(e.get("version", "")) == version:
			return e
	return {}


func _source_name(source: String) -> String:
	match source:
		"generate":
			return "批处理生成"
		"manual":
			return "手动快照"
		"pre-generate":
			return "生成前封存"
		_:
			return "快照" if not source.is_empty() else "—"


static func _rel_time(epoch_sec: float) -> String:
	if epoch_sec <= 0:
		return ""
	var span := Time.get_unix_time_from_system() - epoch_sec
	if span < 60:
		return "刚刚"
	if span < 3600:
		return "%d 分钟前" % int(span / 60)
	if span < 86400:
		return "%d 小时前" % int(span / 3600)
	return "%d 天前" % int(span / 86400)
