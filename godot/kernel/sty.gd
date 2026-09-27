class_name Sty
extends RefCounted

## 样式助手:组件只消费当前主题的 token(对齐"全部样式只消费契约 token,
## 不写散值"的纪律;WEB-UI.md §3 / DEBUG-UI-THEMES.md §4-6 组件无分支)。
## Godot 侧用 StyleBoxFlat + theme override 实现,按钮四态(normal/hover/
## pressed/disabled + focus)齐备——WIDGETS.md §1.3-3 的硬要求。


static func c(token: String) -> Color:
	var t := ThemeRegistry.current
	if t == null:
		return Color.MAGENTA
	return t.colors.get(token, Color.MAGENTA)


static func s(token: String) -> int:
	var t := ThemeRegistry.current
	if t == null:
		return 12
	return t.sizes.get(token, 12)


static func copy(key: String) -> String:
	var t := ThemeRegistry.current
	if t == null:
		return key
	return t.copy.get(key, key) # 缺 key 上屏 key 本体,契约测试的肉眼版


# ---------------------------------------------------------------- 基础件

static func stylebox(bg_token: String, border_token: String, radius_token := "r-md", border_w := 1) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = c(bg_token)
	sb.border_color = c(border_token)
	sb.set_border_width_all(border_w)
	sb.set_corner_radius_all(s(radius_token))
	sb.set_content_margin_all(s("s3"))
	return sb


## 面板(bg-1 + line 描边 + r-md)
static func panel(bg_token := "bg-1") -> PanelContainer:
	var pc := PanelContainer.new()
	pc.add_theme_stylebox_override("panel", stylebox(bg_token, "line"))
	return pc


## 卡片(悬浮面 bg-2 + 强调描边)
static func card() -> PanelContainer:
	var pc := PanelContainer.new()
	pc.add_theme_stylebox_override("panel", stylebox("bg-2", "line-strong"))
	return pc


## 按钮:四态齐备(normal/hover/pressed/disabled + focus 环);
## 手感层(FLOWS v1.5):压下 0.92 / 回弹 BACK 过冲 / hover 提亮——
## 全局一次,任何经 Sty.btn 的按钮都有;ui-press 档位/reduced-motion 可关
static func btn(text: String, primary := false) -> Button:
	var b := Button.new()
	b.text = text
	var normal := stylebox("live" if primary else "bg-2", "line-strong" if primary else "line", "r-sm")
	normal.content_margin_left = s("s3")
	normal.content_margin_right = s("s3")
	normal.content_margin_top = s("s1")
	normal.content_margin_bottom = s("s1")
	var hover := normal.duplicate() as StyleBoxFlat
	hover.bg_color = c("bg-3") if not primary else c("live").lightened(0.15)
	var pressed := normal.duplicate() as StyleBoxFlat
	pressed.bg_color = c("bg-1") if not primary else c("live").darkened(0.15)
	var disabled := normal.duplicate() as StyleBoxFlat
	disabled.bg_color = c("bg-1")
	var focus := StyleBoxFlat.new()
	focus.set_border_width_all(2)
	focus.border_color = c("focus-ring")
	focus.set_corner_radius_all(s("r-sm"))
	focus.bg_color = Color(0, 0, 0, 0)
	b.add_theme_stylebox_override("normal", normal)
	b.add_theme_stylebox_override("hover", hover)
	b.add_theme_stylebox_override("pressed", pressed)
	b.add_theme_stylebox_override("disabled", disabled)
	b.add_theme_stylebox_override("focus", focus)
	b.add_theme_color_override("font_color", c("bg-0") if primary else c("fg-0"))
	b.add_theme_color_override("font_hover_color", c("bg-0") if primary else c("fg-0"))
	b.add_theme_color_override("font_pressed_color", c("fg-1"))
	b.add_theme_color_override("font_disabled_color", c("fg-2"))
	b.add_theme_font_size_override("font_size", s("text-sm"))
	_juice_wire(b)
	return b


## 手感接线(压弹/hover;只动 scale/modulate,不碰布局)
static func _juice_wire(b: Button) -> void:
	b.resized.connect(func() -> void: b.pivot_offset = b.size / 2.0)
	b.button_down.connect(func() -> void:
		if MotionPlayer.allowed("ui-press") and b.is_inside_tree():
			var t := b.create_tween()
			t.tween_property(b, "scale", Vector2(0.92, 0.92), 0.06))
	b.button_up.connect(func() -> void:
		if MotionPlayer.allowed("ui-press") and b.is_inside_tree():
			var t := b.create_tween()
			t.tween_property(b, "scale", Vector2.ONE, 0.22).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT))
	b.mouse_entered.connect(func() -> void:
		if MotionPlayer.allowed("ui-press") and not b.disabled:
			b.modulate = Color(1.07, 1.07, 1.07))
	b.mouse_exited.connect(func() -> void:
		b.modulate = Color.WHITE)


static func label(text: String, size_token := "text-md", color_token := "fg-0", bold := false) -> Label:
	var l := Label.new()
	l.text = text
	l.add_theme_font_size_override("font_size", s(size_token) + (1 if bold else 0))
	l.add_theme_color_override("font_color", c(color_token))
	# 不设 clip_text/autowrap:容器里 clip 会让最小宽度塌成 0(标题消失实锤);
	# 单行标签溢出由布局兜,长文本用 rich()
	return l


static func rich(size_token := "text-sm", color_token := "fg-0") -> RichTextLabel:
	var r := RichTextLabel.new()
	r.bbcode_enabled = true
	r.fit_content = true
	r.scroll_active = false
	r.selection_enabled = true
	r.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	r.add_theme_font_size_override("normal_font_size", s(size_token))
	r.add_theme_color_override("default_color", c(color_token))
	return r


static func line_edit(placeholder := "") -> LineEdit:
	var f := LineEdit.new()
	f.placeholder_text = placeholder
	f.add_theme_font_size_override("font_size", s("text-sm"))
	f.add_theme_color_override("font_color", c("fg-0"))
	f.add_theme_color_override("font_placeholder_color", c("fg-2"))
	f.add_theme_stylebox_override("normal", stylebox("bg-1", "line", "r-sm"))
	f.add_theme_stylebox_override("focus", stylebox("bg-1", "focus-ring", "r-sm"))
	return f


static func text_edit() -> TextEdit:
	var f := TextEdit.new()
	f.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	f.size_flags_vertical = Control.SIZE_EXPAND_FILL
	f.add_theme_font_size_override("font_size", s("text-sm"))
	f.add_theme_color_override("font_color", c("fg-0"))
	f.add_theme_color_override("background_color", c("bg-0"))
	f.add_theme_stylebox_override("normal", stylebox("bg-0", "line", "r-sm"))
	f.add_theme_stylebox_override("focus", stylebox("bg-0", "focus-ring", "r-sm"))
	return f


## 状态点(双编码的一半:颜色;文字/图标通道由调用方另给,a11y 纪律)
static func dot(color_token: String, diameter := 10) -> Control:
	var p := PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = c(color_token)
	sb.set_corner_radius_all(diameter / 2)
	p.add_theme_stylebox_override("panel", sb)
	p.custom_minimum_size = Vector2(diameter, diameter)
	return p


static func hline() -> Control:
	var r := ColorRect.new()
	r.color = c("line")
	r.custom_minimum_size = Vector2(0, 1)
	r.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	return r


static func vbox() -> VBoxContainer:
	return VBoxContainer.new()


static func hbox() -> HBoxContainer:
	return HBoxContainer.new()


static func spacer() -> Control:
	var sp := Control.new()
	sp.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	return sp


static func scroll() -> ScrollContainer:
	var sc := ScrollContainer.new()
	sc.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	sc.size_flags_vertical = Control.SIZE_EXPAND_FILL
	# 横向永不滚。注意:ScrollContainer 不会把子件撑到自身宽度——子件需要
	# 显式 custom_minimum_size.x(定值;resize 回调同步构成重排环路,消息队列
	# 打满崩溃,已两次实锤,勿再试)
	sc.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	return sc


## 颜色 → bbcode 十六进制(RichTextLabel [color] 用)
static func hex_of(color_token: String) -> String:
	return "#" + c(color_token).to_html(false)
