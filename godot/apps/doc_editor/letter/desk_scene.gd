class_name DeskScene
extends Node3D

## 2.5D 书桌场景(GAME-UI-DOC 的空间层):信纸铺在桌面上(2D 精读面在前),
## 3D 负责空间与状态——版本叠在右后(一版一页),便签板在左后(一批注一签,
## 终态变色),暖光台灯。镜头有呼吸感,动作时聚焦到对应物件。
## 全部材质消费主题 token(组件零分支延伸到 3D);本场景 v1 不交互(纯态势)。

var _camera: Camera3D
var _stack_holder: Node3D
var _board_holder: Node3D
var _lamp: OmniLight3D
var _cam_offset_goal := Vector3.ZERO
var _sheet_proto_y := 0.0
var _time := 0.0

const STACK_POS := Vector3(2.5, 0.0, -0.55)
const BOARD_POS := Vector3(-2.55, 0.85, -1.0)


func _ready() -> void:
	_build()


func _build() -> void:
	# 环境:深色书房 + 轻雾
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Sty.c("bg-0")
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Sty.c("fg-2")
	env.ambient_light_energy = 0.32
	env.fog_enabled = true
	env.fog_light_color = Sty.c("bg-0")
	env.fog_depth_begin = 6.0
	env.fog_depth_end = 18.0
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)

	# 桌面
	var desk := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(14, 10)
	desk.mesh = plane
	desk.material_override = _mat("bg-1", 0.0)
	add_child(desk)

	# 暖光台灯(点光,左上方)
	_lamp = OmniLight3D.new()
	_lamp.position = Vector3(-1.6, 2.6, 0.4)
	_lamp.light_color = Color("#ffd9a0")
	_lamp.light_energy = 1.6
	_lamp.omni_range = 7.0
	add_child(_lamp)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, 30, 0)
	sun.light_energy = 0.25
	add_child(sun)

	# 版本叠(右后)
	_stack_holder = Node3D.new()
	_stack_holder.position = STACK_POS
	add_child(_stack_holder)

	# 便签板(左后,斜立)
	_board_holder = Node3D.new()
	_board_holder.position = BOARD_POS
	_board_holder.rotation_degrees = Vector3(-6, 30, 0)
	add_child(_board_holder)
	var board := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = Vector3(1.7, 1.1, 0.06)
	board.mesh = bm
	board.material_override = _mat("bg-2", 0.0)
	_board_holder.add_child(board)

	# 相机:略带俯视;桌面上沿与两侧要露出来(3D 的存在感来自留白处的景深)
	_camera = Camera3D.new()
	_camera.position = Vector3(0.0, 2.25, 3.9)
	_camera.fov = 55.0
	_camera.current = true
	add_child(_camera)
	_camera.look_at(Vector3(0, 0.28, -0.5))


func _mat(token: String, emission_energy: float) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = Color(token) if token.begins_with("#") else Sty.c(token)
	m.roughness = 0.9
	if emission_energy > 0.0:
		m.emission_enabled = true
		m.emission = Sty.c(token)
		m.emission_energy_multiplier = emission_energy
	return m


## 状态驱动物件:版本叠高度 = 版本数(封顶 12),便签板 = 批注(终态变色)
func set_state(versions_count: int, annotations: Array) -> void:
	_rebuild_stack(versions_count)
	_rebuild_notes(annotations)


func _rebuild_stack(versions_count: int) -> void:
	for c in _stack_holder.get_children():
		_stack_holder.remove_child(c)
		c.free() # 立即释放:queue_free 延迟一帧,同帧重建会数出双份(实锤)
	var n := mini(versions_count, 12)
	for i in n:
		var sheet := MeshInstance3D.new()
		var sm := BoxMesh.new()
		sm.size = Vector3(0.9, 0.035, 1.2)
		sheet.mesh = sm
		sheet.material_override = _mat("paper-1", 0.0)
		sheet.position = Vector3(0, 0.02 + i * 0.038, 0)
		sheet.rotation.y = sin(float(i) * 1.7) * 0.05
		_stack_holder.add_child(sheet)
	_sheet_proto_y = 0.02 + n * 0.038


func _rebuild_notes(annotations: Array) -> void:
	for c in _board_holder.get_children():
		if c is MeshInstance3D and c.name == "note":
			_board_holder.remove_child(c)
			c.free() # 同上:立即释放
	var shown := 0
	for a in annotations:
		if shown >= 12:
			break
		var status := "pending"
		if a is Dictionary:
			status = str(a.get("status", "pending"))
		var note := MeshInstance3D.new()
		note.name = "note"
		var nm := BoxMesh.new()
		nm.size = Vector3(0.26, 0.2, 0.02)
		note.mesh = nm
		var token := "warn"
		match status:
			"applied": token = "ok"
			"ignored": token = "fg-2"
			"outdated": token = "#c06420"
		note.material_override = _mat(token, 0.25 if status == "pending" else 0.05)
		var col := shown % 4
		var rowi := shown / 4
		note.position = Vector3(-0.58 + col * 0.39, 0.38 - rowi * 0.3, 0.05)
		note.rotation.z = sin(float(shown) * 2.3) * 0.06
		_board_holder.add_child(note)
		shown += 1


## 新页落叠(快照/生成成功的空间回声)
func drop_sheet() -> void:
	if not is_inside_tree():
		return
	var sheet := MeshInstance3D.new()
	var sm := BoxMesh.new()
	sm.size = Vector3(0.9, 0.035, 1.2)
	sheet.mesh = sm
	sheet.material_override = _mat("paper-0", 0.15)
	_stack_holder.add_child(sheet)
	var target_y := _sheet_proto_y
	sheet.position = Vector3(0, target_y + 1.2, 0)
	sheet.rotation.y = 0.12
	var tween := create_tween()
	tween.tween_property(sheet, "position:y", target_y, 0.22).set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN)
	tween.tween_property(sheet, "rotation:y", 0.0, 0.12)
	focus("stack")


## 镜头聚焦(动作调度):letter(默认)/ stack(版本叠)/ board(便签板)
func focus(what: String) -> void:
	match what:
		"stack":
			_cam_offset_goal = Vector3(0.5, 0.18, -0.35)
		"board":
			_cam_offset_goal = Vector3(-0.55, 0.28, -0.4)
		_:
			_cam_offset_goal = Vector3.ZERO


func _process(delta: float) -> void:
	if _camera == null:
		return
	_time += delta
	# 呼吸感:极轻的待机漂移(reduced-motion 下静帧)
	var sway := Vector3.ZERO
	if not ThemeRegistry.reduced_motion:
		sway = Vector3(sin(_time * 0.5) * 0.045, sin(_time * 0.34) * 0.02, 0.0)
	var base := Vector3(0.0, 2.05, 4.3) + _cam_offset_goal + sway
	_camera.position = _camera.position.lerp(base, 1.0 - exp(-delta * 3.2))
	_camera.look_at(Vector3(0, 0.5, -0.4) + _cam_offset_goal * 0.55)
