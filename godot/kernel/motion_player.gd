class_name MotionPlayer
extends RefCounted

## 具名动效播放层(主题契约 §2.3):组件只调用具名动效,档位由主题档案给;
## ReducedMotion 强制 Instant。性能纪律:只动 modulate(合成层等价物)。


static func play(motion_name: String, target: Control) -> void:
	if target == null or not is_instance_valid(target) or not target.is_inside_tree():
		return
	var level := ThemeRegistry.MOTION_SUBTLE
	if ThemeRegistry.current != null:
		level = ThemeRegistry.current.motion_of(motion_name)
	if ThemeRegistry.reduced_motion:
		level = ThemeRegistry.MOTION_INSTANT
	if level == ThemeRegistry.MOTION_INSTANT:
		return
	match motion_name:
		"focus-pulse":
			_pulse(target, 3 if level == ThemeRegistry.MOTION_FULL else 2)
		"change-flash":
			_flash(target)
		"text-reveal":
			_reveal(target)
		"note-arrive":
			_arrive(target)
		"stamp-press":
			_stamp(target)
		# ── 手感层(game feel;§FLOWS v1.5)──
		"pop-in":
			_pop_in(target)
		"dock-in":
			_dock_in(target)
		"paper-shake":
			_paper_shake(target)
		"status-in":
			_status_in(target)
		"tick":
			_tick(target)


## 手感闸:本动效当前是否允许播放(reduced-motion / 主题 instant 档直接否)
static func allowed(motion_name: String) -> bool:
	if ThemeRegistry.reduced_motion:
		return false
	if ThemeRegistry.current == null:
		return true
	return ThemeRegistry.current.motion_of(motion_name) != ThemeRegistry.MOTION_INSTANT


## 焦点脉冲:透明度呼吸 N 次后复位
static func _pulse(t: Control, times: int) -> void:
	var tween := t.create_tween()
	for i in times:
		tween.tween_property(t, "modulate:a", 0.35, 0.12)
		tween.tween_property(t, "modulate:a", 1.0, 0.12)


## 变化高亮:ok 色淡入后淡出一次(不循环)
static func _flash(t: Control) -> void:
	var ok := Sty.c("ok")
	var tween := t.create_tween()
	tween.tween_property(t, "modulate", Color(ok.r, ok.g, ok.b, 1.0), 0.1)
	tween.tween_property(t, "modulate", Color.WHITE, 1.2)


## 文字晕染入场:透明度渐入 + 轻微上浮(container 托管的子件只动 modulate)
static func _reveal(t: Control) -> void:
	t.modulate.a = 0.0
	var tween := t.create_tween()
	tween.tween_property(t, "modulate:a", 1.0, 0.32).set_trans(Tween.TRANS_SINE)


## 便签飞入:右缘滑入 + 旋转收束 + 弹簧过冲(自由摆放的 Control 用;容器托管件勿用)
static func _arrive(t: Control) -> void:
	var final_pos := t.position
	t.position = final_pos + Vector2(46, -8)
	t.pivot_offset = t.size / 2.0
	t.rotation = 0.14
	t.modulate.a = 0.0
	var tween := t.create_tween().set_parallel(true)
	tween.tween_property(t, "position", final_pos, 0.26).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
	tween.tween_property(t, "rotation", 0.0, 0.26).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
	tween.tween_property(t, "modulate:a", 1.0, 0.18)


## 盖章:目标自上方砸落 + 极轻旋转晃动(朱砂印交给调用方准备)
static func _stamp(t: Control) -> void:
	var final_pos := t.position
	t.position = final_pos + Vector2(0, -110)
	t.pivot_offset = t.size / 2.0
	t.rotation = -0.06
	t.modulate.a = 0.0
	var tween := t.create_tween()
	tween.set_parallel(true)
	tween.tween_property(t, "position", final_pos, 0.18).set_trans(Tween.TRANS_CUBIC).set_ease(Tween.EASE_IN)
	tween.tween_property(t, "modulate:a", 1.0, 0.1)
	tween.set_parallel(false)
	tween.tween_property(t, "rotation", 0.05, 0.06)
	tween.tween_property(t, "rotation", 0.0, 0.09)


# ---------------------------------------------------------------- 手感层(全部只动 modulate/scale/rotation,不碰布局)

## 弹入:缩小淡入 + 过冲落定(新物件出现)
static func _pop_in(t: Control) -> void:
	t.pivot_offset = t.size / 2.0
	t.scale = Vector2(0.5, 0.5)
	t.modulate.a = 0.0
	var tween := t.create_tween().set_parallel(true)
	tween.tween_property(t, "scale", Vector2(1.08, 1.08), 0.16).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
	tween.tween_property(t, "modulate:a", 1.0, 0.12)
	tween.chain().tween_property(t, "scale", Vector2.ONE, 0.1)


## 入场:淡出 + 轻缩回弹(面板/坞出现;容器托管件安全)
static func _dock_in(t: Control) -> void:
	t.pivot_offset = t.size / 2.0
	t.scale = Vector2(0.96, 0.96)
	t.modulate.a = 0.0
	var tween := t.create_tween().set_parallel(true)
	tween.tween_property(t, "scale", Vector2.ONE, 0.2).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
	tween.tween_property(t, "modulate:a", 1.0, 0.14)


## 纸震:盖章/落纸时纸面极轻晃动(rotation 微调,不动位置)
static func _paper_shake(t: Control) -> void:
	t.pivot_offset = t.size / 2.0
	var tween := t.create_tween()
	tween.tween_property(t, "rotation", 0.005, 0.05)
	tween.tween_property(t, "rotation", -0.004, 0.07)
	tween.tween_property(t, "rotation", 0.0, 0.08)


## 状态行滑入:一句话到达 = 淡入(文字是通道,动效只强调)
static func _status_in(t: Control) -> void:
	t.modulate.a = 0.0
	var tween := t.create_tween()
	tween.tween_property(t, "modulate:a", 1.0, 0.16)


## 选中 tick:过冲跳一下(卷轴刻度选中、列表选中)
static func _tick(t: Control) -> void:
	t.pivot_offset = t.size / 2.0
	var tween := t.create_tween()
	tween.tween_property(t, "scale", Vector2(1.1, 1.1), 0.08)
	tween.tween_property(t, "scale", Vector2.ONE, 0.16).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)
