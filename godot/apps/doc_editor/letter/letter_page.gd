class_name LetterPage
extends PanelContainer

## 信纸(docs/GAME-UI-DOC.md §1):一页居中的暖白纸——标题/版本朱砂印/行文块/
## 当前段微光左道/信末回执联。纸张与墨色消费主题纸张 token(paper-*/seal);
## 便签牵线由 LetterHost 的覆盖层按 block_row(anchor) 的全局矩形逐帧对齐。
## 注意:继承 PanelContainer(透明)——容器逻辑铺满子件;不要用锚点
## (锚点在容器托管的父级内不生效,已两次实锤)。

signal block_selected(anchor: String)
signal note_requested(anchor: String)
signal reply_submitted(text: String)

var _rows: Dictionary = {} # anchor → Control(行)
var _blocks: Array = []
var _ann_info: Dictionary = {} # anchor → {count, status}(内联标记数据源)
var _current := ""
var _paper: PanelContainer     # 纸面本体(纸震的靶子)

## 批注状态 → 标记色(双编码:颜色 + 「注×N」文字同在;色板取 paper-ink 系的纸面可读档)
const ANN_COLORS := {
	"pending": "#8a6d1f",   # 琥珀(待批处理)
	"applied": "#2e8b57",   # 绿(已誊回)
	"ignored": "#8a8578",   # 灰(已忽略)
	"outdated": "#c06420",  # 橙(已过时)
}
var _content: VBoxContainer
var _seal: Control
var _seal_label: Label
var _reply_input: LineEdit


func _ready() -> void:
	add_theme_stylebox_override("panel", StyleBoxEmpty.new())


## 整文重建(数据来自 app state;视图无自有事实源)
## ann_info:{anchor: {count, status}}——非零段落在行文末尾内联 [注×N] 标记,
## 颜色随批注终态(pending 琥珀/applied 绿/ignored 灰/outdated 橙)
func set_document(doc_title: String, text: String, versions: Array, ann_info: Dictionary = {}) -> void:
	_blocks = MdBlocks.split(text)
	_ann_info = ann_info
	_rebuild(doc_title, versions)


func block_anchor_at(index: int) -> String:
	if index < 0 or index >= _blocks.size():
		return ""
	return _blocks[index]["anchor"]


func block_count() -> int:
	return _blocks.size()


func index_of(anchor: String) -> int:
	for i in _blocks.size():
		if _blocks[i]["anchor"] == anchor:
			return i
	return -1


func row_of(anchor: String) -> Control:
	return _rows.get(anchor)


func set_current(anchor: String) -> void:
	_current = anchor
	for a in _rows:
		_style_row(_rows[a], a == anchor)


func get_current() -> String:
	return _current


## 改动段晕染(change-flash;调用方负责算锚点集)
func flash_blocks(anchors: Array) -> void:
	for a in anchors:
		if _rows.has(a):
			MotionPlayer.play("change-flash", _rows[a])


func play_reveal() -> void:
	# 段落入场:逐段晕染(容器托管,只动 modulate;每段错开 60ms)
	var i := 0
	for a in _rows:
		var row: Control = _rows[a]
		row.modulate.a = 0.0
		var delay := 0.06 * i
		if is_inside_tree():
			var tween := create_tween()
			tween.tween_interval(delay)
			tween.tween_property(row, "modulate:a", 1.0, 0.3).set_trans(Tween.TRANS_SINE)
		else:
			row.modulate.a = 1.0
		i += 1


## 版本印:盖章动画的落点(右页角)
func seal_control() -> Control:
	return _seal


## 纸面本体(盖章纸震的靶子)
func paper_control() -> Control:
	return _paper


func _rebuild(doc_title: String, versions: Array) -> void:
	for c in get_children():
		remove_child(c)
		c.queue_free()
	_rows.clear()

	var paper := PanelContainer.new()
	_paper = paper
	paper.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	paper.size_flags_vertical = Control.SIZE_EXPAND_FILL
	var paper_sb := StyleBoxFlat.new()
	paper_sb.bg_color = Sty.c("paper-0")
	paper_sb.set_corner_radius_all(4)
	paper_sb.set_content_margin_all(28)
	paper_sb.shadow_color = Color(0, 0, 0, 0.45)
	paper_sb.shadow_size = 12
	paper_sb.shadow_offset = Vector2(4, 8)
	paper.add_theme_stylebox_override("panel", paper_sb)
	add_child(paper) # 本类是透明 PanelContainer:子件由容器铺满,不用锚点

	var scroll := Sty.scroll()
	paper.add_child(scroll)
	_content = Sty.vbox()
	_content.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_content.custom_minimum_size.x = 520 # ScrollContainer 不撑宽子件(实锤教训)
	_content.add_theme_constant_override("separation", 10)
	scroll.add_child(_content)

	# ── 信头:标题 + 版本印 ──
	var head := Sty.hbox()
	var title := Sty.label(doc_title, "text-xl", "paper-ink", true)
	head.add_child(title)
	head.add_child(Sty.spacer())
	_seal = _make_seal(versions)
	head.add_child(_seal)
	_content.add_child(head)

	var meta := Sty.label("design/review 信箱 · 共 %d 版" % versions.size(), "text-xs", "paper-ink-2")
	_content.add_child(meta)
	var rule := ColorRect.new()
	rule.color = Sty.c("paper-line")
	rule.custom_minimum_size = Vector2(0, 1)
	_content.add_child(rule)

	# ── 行文块 ──
	if _blocks.is_empty():
		_content.add_child(Sty.label("(空信 —— 在回执联写下你要什么)", "text-sm", "paper-ink-2"))
	for block in _blocks:
		_content.add_child(_make_block_row(block))

	# ── 回执联(虚线撕口 + 写给编者) ──
	_content.add_child(_dash_line())
	var slip_hint := Sty.label("回执联 · 写给编者(改动由编者誊回,信纸不换)", "text-xs", "paper-ink-2")
	_content.add_child(slip_hint)
	var slip_row := Sty.hbox()
	_reply_input = LineEdit.new()
	_reply_input.placeholder_text = "说怎么改这封信…"
	_reply_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_reply_input.add_theme_font_size_override("font_size", Sty.s("text-sm"))
	_reply_input.add_theme_color_override("font_color", Sty.c("paper-ink"))
	_reply_input.add_theme_color_override("font_placeholder_color", Sty.c("paper-ink-2"))
	var slip_sb := StyleBoxFlat.new()
	slip_sb.bg_color = Sty.c("paper-1")
	slip_sb.border_color = Sty.c("paper-line")
	slip_sb.set_border_width_all(1)
	slip_sb.set_corner_radius_all(6)
	_reply_input.add_theme_stylebox_override("normal", slip_sb)
	slip_row.add_child(_reply_input)
	var send := Sty.btn("寄出", true)
	send.pressed.connect(_submit_reply)
	_reply_input.text_submitted.connect(func(_t: String) -> void: _submit_reply())
	slip_row.add_child(send)
	_content.add_child(slip_row)


func _make_seal(versions: Array) -> Control:
	var wrap := Control.new()
	wrap.custom_minimum_size = Vector2(56, 56)
	var p := PanelContainer.new()
	p.set_anchors_preset(Control.PRESET_FULL_RECT)
	var sb := StyleBoxFlat.new()
	sb.bg_color = Sty.c("seal")
	sb.set_corner_radius_all(4)
	sb.border_color = Color(1, 1, 1, 0.75)
	sb.set_border_width_all(2)
	sb.set_content_margin_all(4)
	p.add_theme_stylebox_override("panel", sb)
	_seal_label = Label.new()
	_seal_label.add_theme_font_size_override("font_size", 13)
	_seal_label.add_theme_color_override("font_color", Color(0.96, 0.94, 0.87))
	_seal_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_seal_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	if versions.is_empty():
		_seal_label.text = "未封"
		sb.bg_color = Color(Sty.c("seal"), 0.25)
	else:
		_seal_label.text = str(versions[0])
	p.add_child(_seal_label)
	wrap.add_child(p)
	wrap.pivot_offset = Vector2(28, 28)
	wrap.rotation = 0.1
	return wrap


func _make_block_row(block: Dictionary) -> Control:
	var anchor: String = block["anchor"]
	var row := PanelContainer.new()
	row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
	_rows[anchor] = row
	_style_row(row, anchor == _current)

	var rich := RichTextLabel.new()
	rich.bbcode_enabled = true
	rich.fit_content = true
	rich.scroll_active = false
	rich.selection_enabled = true
	rich.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	rich.add_theme_font_size_override("normal_font_size", Sty.s("text-md"))
	rich.add_theme_color_override("default_color", Sty.c("paper-ink"))
	rich.text = _to_bbcode(block)
	# 批注内联标记:跟在行文末尾([注×N],meta 可点;色随终态)
	var info: Dictionary = _ann_info.get(anchor, {})
	var ann := int(info.get("count", 0))
	if ann > 0:
		var ann_color: String = ANN_COLORS.get(str(info.get("status", "pending")), "#8a6d1f")
		rich.text += "  [url=%s][color=%s][b]注×%d[/b][/color][/url]" % [anchor, ann_color, ann]
		rich.meta_clicked.connect(func(meta: Variant) -> void:
			var a := str(meta)
			if a.begins_with("doc.md#"):
				set_current(a)
				note_requested.emit(a))
	row.add_child(rich)

	# 点击事件挂在 rich 上(它会吃掉行的事件面:文本选择优先);
	# 左键 = 当前段(不拦截,文本照选);右键 = 写批注(拦截)
	rich.gui_input.connect(func(event: InputEvent) -> void:
		if event is InputEventMouseButton and event.pressed:
			if event.button_index == MOUSE_BUTTON_LEFT:
				set_current(anchor)
				block_selected.emit(anchor)
			elif event.button_index == MOUSE_BUTTON_RIGHT:
				set_current(anchor)
				note_requested.emit(anchor)
				rich.accept_event())
	row.gui_input.connect(func(event: InputEvent) -> void:
		if event is InputEventMouseButton and event.pressed:
			if event.button_index == MOUSE_BUTTON_LEFT:
				set_current(anchor)
				block_selected.emit(anchor)
				row.accept_event()
			elif event.button_index == MOUSE_BUTTON_RIGHT:
				set_current(anchor)
				note_requested.emit(anchor)
				row.accept_event())
	return row


func _style_row(row: Control, current: bool) -> void:
	var sb := StyleBoxFlat.new()
	if current:
		sb.bg_color = Color(Sty.c("live"), 0.08)
		sb.border_color = Sty.c("live")
		sb.border_width_left = 3
	else:
		sb.bg_color = Color(0, 0, 0, 0)
	sb.set_content_margin_all(6)
	sb.content_margin_left = 10
	row.add_theme_stylebox_override("panel", sb)


## 信纸 bbcode:白名单加工(标题/粗斜体/行内码/代码块),墨色系
func _to_bbcode(block: Dictionary) -> String:
	var text: String = block["text"]
	match block["kind"]:
		"code":
			return "[color=%s]%s" % ["#6f5a1f", _escape(text)]
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


func _inline(text: String) -> String:
	var s := _escape(text)
	s = MdBlocks._re("\\*\\*(.+?)\\*\\*").sub(s, "[b]$1[/b]", true)
	s = MdBlocks._re("(?<!\\w)\\*([^*\\n]+?)\\*(?!\\w)").sub(s, "[i]$1[/i]", true)
	s = MdBlocks._re("`([^`\\n]+?)`").sub(s, "[color=#6f5a1f]$1[/color]", true)
	return s


func _escape(s: String) -> String:
	return s.replace("[", "[lb]")


func _dash_line() -> Control:
	var d := Control.new()
	d.custom_minimum_size = Vector2(0, 14)
	d.draw.connect(func() -> void:
		var y := 7.0
		var x := 0.0
		while x < d.size.x:
			d.draw_line(Vector2(x, y), Vector2(minf(x + 6.0, d.size.x), y), Sty.c("paper-line"), 1.0)
			x += 11.0)
	return d


func _submit_reply() -> void:
	var text := _reply_input.text.strip_edges()
	if text.is_empty():
		return
	_reply_input.text = ""
	reply_submitted.emit(text)
