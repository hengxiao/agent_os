class_name VersionTree
extends Control

## 版本树覆盖层(v1.8 树状版本模型):parent 链画成分支图——
## 回推线性(每个节点沿 parent 上溯),前衍可分支(同 parent 多个子代排开)。
## 点节点 = 选中该版(宿主关层 + 页栈展开对应页);点空白/Esc = 收拢。

signal version_chosen(version: String)

var _nodes: Dictionary = {}     # version → PanelContainer
var _edges: Array[Line2D] = []
var _canvas: Control            # 节点+边挂这层,整体可拖拽平移(技能树式)
var _drag_from := Vector2.ZERO  # 按下时的鼠标位(区分点击关闭与拖拽平移)
var _dragging := false


func _ready() -> void:
	visible = false


func is_open() -> bool:
	return visible


func close() -> void:
	visible = false


## entries:[{version,parent,at,source}](新→旧);base = 当前祖版
func open(entries: Array, base: String) -> void:
	visible = true # 先亮层:容器对隐藏子件不布局,size 恒 0(实锤:--open-tree 早调兜底 150)
	if size.x <= 0 and is_inside_tree():
		await get_tree().process_frame
		await get_tree().process_frame
	_build(entries, base)
	if is_inside_tree():
		modulate.a = 0.0
		create_tween().tween_property(self, "modulate:a", 1.0, 0.16)


func node_count() -> int:
	return _nodes.size()


func _build(entries: Array, base: String) -> void:
	for c in get_children():
		remove_child(c)
		c.free()
	_nodes.clear()
	_edges.clear()
	_dragging = false

	var dim := ColorRect.new()
	dim.color = Color(0, 0, 0, 0.55)
	dim.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(dim)
	dim.gui_input.connect(func(event: InputEvent) -> void:
		if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				_drag_from = event.position
				_dragging = false
			elif not _dragging: # 位移够小才算点空白关闭;拖拽松手不关
				close()
			dim.accept_event()
		elif event is InputEventMouseMotion and event.button_mask & MOUSE_BUTTON_MASK_LEFT:
			var delta: Vector2 = event.position - _drag_from
			if _dragging or delta.length() > 6.0:
				_dragging = true
				_canvas.position += event.relative
				_clamp_canvas()
				dim.accept_event())

	_canvas = Control.new() # 图层:节点与边都画在这里,平移只动它
	_canvas.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_canvas)

	var hint := Sty.label("版本树 · 回推线性,前衍可分支;点节点 = 选中该版", "text-sm", "fg-1")
	hint.set_anchors_preset(Control.PRESET_CENTER_TOP)
	hint.position = Vector2(-170, 34)
	add_child(hint)

	# 布局:depth = 沿 parent 上溯的步数;x = depth;同 depth 纵向排开
	var parent_of := {}
	for e in entries:
		parent_of[str(e.get("version", ""))] = str(e.get("parent", ""))
	var depth_of := {}
	for e in entries:
		var v := str(e.get("version", ""))
		depth_of[v] = _depth(v, parent_of)
	var by_depth := {}
	for v in depth_of:
		var d: int = depth_of[v]
		if not by_depth.has(d):
			by_depth[d] = []
		by_depth[d].append(v)
	var max_depth: int = by_depth.keys().max() if not by_depth.is_empty() else 0

	var center := size / 2.0
	if center == Vector2.ZERO:
		center = Vector2(640, 360)
	# 步进自适应:链短时 150px 舒展;链长时收缩,至少保住节点宽+缝(56+8)
	var x_step := clampf((size.x - 200.0) / maxf(float(max_depth), 1.0), 64.0, 150.0)
	if size.x <= 0:
		x_step = 150.0
	var content_w := float(max_depth) * x_step
	# 内容放得下就居中;放不下就从左缘起排,靠拖拽看深处(技能树惯例)
	var origin_x := center.x - content_w / 2.0 if content_w <= size.x - 120.0 else 80.0

	var pos_of := {}
	for d in by_depth:
		var vs: Array = by_depth[d]
		vs.sort() # 同层按版本号定序(稳定)
		for k in vs.size():
			var x: float = origin_x + d * x_step
			var y: float = center.y + (k - (vs.size() - 1) / 2.0) * 110.0
			pos_of[vs[k]] = Vector2(x, y)

	# 边(先画线,节点在上)
	for e in entries:
		var v := str(e.get("version", ""))
		var p := str(e.get("parent", ""))
		if pos_of.has(v) and pos_of.has(p):
			var line := Line2D.new()
			line.width = 1.5
			line.default_color = Sty.c("line-strong")
			line.points = PackedVector2Array([pos_of[p] + Vector2(28, 0), pos_of[v] + Vector2(-28, 0)])
			_canvas.add_child(line)
			_edges.append(line)

	for e in entries:
		var v := str(e.get("version", ""))
		if not pos_of.has(v):
			continue
		var node := PanelContainer.new()
		node.custom_minimum_size = Vector2(56, 34)
		var sb := StyleBoxFlat.new()
		sb.bg_color = Sty.c("paper-0")
		sb.set_corner_radius_all(4)
		sb.set_content_margin_all(4)
		if v == base:
			sb.border_color = Sty.c("ok")
			sb.set_border_width_all(2)
		elif str(e.get("source", "")) == "generate":
			sb.border_color = Sty.c("seal")
			sb.set_border_width_all(1)
		node.add_theme_stylebox_override("panel", sb)
		var lb := Label.new()
		lb.text = v
		lb.add_theme_font_size_override("font_size", Sty.s("text-xs"))
		lb.add_theme_color_override("font_color", Sty.c("paper-ink"))
		lb.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		node.add_child(lb)
		node.position = pos_of[v] - Vector2(28, 17)
		node.mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND
		var chosen := v
		node.gui_input.connect(func(event: InputEvent) -> void:
			if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
				close()
				version_chosen.emit(chosen)
				node.accept_event())
		_canvas.add_child(node)
		_nodes[v] = node


## 平移钳制:画布不许被拖丢(内容边缘最多贴到视口边缘内 80px);
## 区间恒含 0(初始位),窄内容也能小拖不跳变
func _clamp_canvas() -> void:
	if _canvas == null:
		return
	var ext := Vector2.ZERO
	for v in _nodes:
		var n: Control = _nodes[v]
		ext.x = maxf(ext.x, n.position.x + n.size.x)
		ext.y = maxf(ext.y, n.position.y + n.size.y)
	var lo_x := minf(minf(80.0, size.x - ext.x - 80.0), 0.0)
	var hi_x := maxf(maxf(80.0, size.x - ext.x - 80.0), 0.0)
	var lo_y := minf(minf(80.0, size.y - ext.y - 80.0), 0.0)
	var hi_y := maxf(maxf(80.0, size.y - ext.y - 80.0), 0.0)
	_canvas.position.x = clampf(_canvas.position.x, lo_x, hi_x)
	_canvas.position.y = clampf(_canvas.position.y, lo_y, hi_y)


func _depth(v: String, parent_of: Dictionary) -> int:
	var d := 0
	var cur := v
	var guard := 0
	while parent_of.has(cur) and str(parent_of[cur]) != "" and guard < 64:
		cur = str(parent_of[cur])
		if not parent_of.has(cur):
			break
		d += 1
		guard += 1
	return d
