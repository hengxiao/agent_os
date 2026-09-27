class_name BuiltInThemes
extends RefCounted

## 内置主题:值逐字移植自 host/web/static/css/themes/{classic,pixel}.css(行为同源)。
## classic = 契约参考实现(严肃工程,dark-first);pixel = 8-bit 复古游戏(证明可插拔,
## 游戏腔 copy 是"翻译层",技术原文豁免直读)。


static func classic() -> ThemePack:
	var t := ThemePack.new()
	t.id = "classic"
	t.display_name = "严肃工程"
	# 基底(classic.css §3.1)
	_paint(t, {
		"bg-0": "#0b0e14", "bg-1": "#11151d", "bg-2": "#171d29", "bg-3": "#1f2837",
		"line": "#263043", "line-strong": "#33405a",
		"fg-0": "#e6ebf2", "fg-1": "#9aa7ba", "fg-2": "#5d6b82",
		"ok": "#3fb68b", "warn": "#d9a03f", "danger": "#e5534b", "aborted": "#9e6bde", "live": "#3b9eff",
		"sig-llm": "#6f9fff", "sig-tool": "#3fb68b", "sig-sidecar": "#d9a03f",
		"sig-compress": "#8b7cf6", "sig-budget": "#e5534b", "sig-frame": "#5d6b82",
		"perm-read": "#5d6b82", "perm-write": "#d9a03f", "perm-net": "#6f9fff", "perm-exec": "#e5534b",
		"focus-ring": "#3b9eff",
		# 纸张 token(信件模型视图层;DEBUG-UI-THEMES §2.1 之外的视图层扩展,两主题同约)
		"paper-0": "#f5efdf", "paper-1": "#efe7d2", "paper-ink": "#3a352c",
		"paper-ink-2": "#7a7468", "paper-line": "#d8cdb4", "seal": "#c0392b",
	})
	_sizes(t, 0) # r-sm..lg 见 _sizes(base_radius_offset)
	t.sizes["r-sm"] = 4; t.sizes["r-md"] = 8; t.sizes["r-lg"] = 12
	# copy:classic 的文案表 = 现状文案的抽离(简 technical)
	t.copy = {
		"doc.list.empty": "还没有文档",
		"doc.new": "+ 新建文档", "doc.create": "创建",
		"doc.save": "保存", "doc.snapshot": "快照",
		"doc.rewind": "回滚", "doc.rewind.confirm": "确认回滚?",
		"doc.review": "评审", "doc.export": "导出",
		"doc.status.dirty": "未保存", "doc.status.saved": "已保存",
		"doc.comment.placeholder": "就这一段提问…", "doc.comment.send": "发送",
		"doc.comment.apply": "应用此修改", "doc.comment.thinking": "思考中…",
		"doc.chat.placeholder": "告诉 agent 要怎么改这篇文档…", "doc.chat.send": "发送",
		"doc.view.edit": "编辑", "doc.view.preview": "预览", "doc.view.split": "分屏",
	}
	t.motion = {"focus-pulse": ThemeRegistry.MOTION_SUBTLE, "change-flash": ThemeRegistry.MOTION_SUBTLE,
		"text-reveal": ThemeRegistry.MOTION_SUBTLE, "note-arrive": ThemeRegistry.MOTION_SUBTLE,
		"stamp-press": ThemeRegistry.MOTION_SUBTLE, "page-fan": ThemeRegistry.MOTION_SUBTLE,
		"ui-press": ThemeRegistry.MOTION_SUBTLE, "pop-in": ThemeRegistry.MOTION_SUBTLE,
		"dock-in": ThemeRegistry.MOTION_SUBTLE, "paper-shake": ThemeRegistry.MOTION_SUBTLE,
		"status-in": ThemeRegistry.MOTION_SUBTLE, "tick": ThemeRegistry.MOTION_SUBTLE}
	return t


static func pixel() -> ThemePack:
	var t := ThemePack.new()
	t.id = "pixel"
	t.display_name = "像素复古"
	t.mascot = "sprite8"
	# 基底:深靛夜(pixel.css)
	_paint(t, {
		"bg-0": "#1a1c2c", "bg-1": "#23253a", "bg-2": "#2c2f4a", "bg-3": "#363a58",
		"line": "#3d4166", "line-strong": "#5a5f8c",
		"fg-0": "#f2f3fa", "fg-1": "#b4b9dc", "fg-2": "#8287ad",
		"ok": "#5dd65d", "warn": "#f8d838", "danger": "#ff6b5e", "aborted": "#c08cf8", "live": "#4ecdf8",
		"sig-llm": "#7ec4ff", "sig-tool": "#5dd65d", "sig-sidecar": "#f8d838",
		"sig-compress": "#c08cf8", "sig-budget": "#ff6b5e", "sig-frame": "#8287ad",
		"perm-read": "#8287ad", "perm-write": "#f8d838", "perm-net": "#7ec4ff", "perm-exec": "#ff6b5e",
		"focus-ring": "#4ecdf8",
		# 纸张 token(像素信纸:偏黄的粗纸色)
		"paper-0": "#f0e6c8", "paper-1": "#e8dcb8", "paper-ink": "#3a352c",
		"paper-ink-2": "#7a7468", "paper-line": "#d0c198", "seal": "#c0392b",
	})
	_sizes(t, 0)
	t.sizes["r-sm"] = 0; t.sizes["r-md"] = 2; t.sizes["r-lg"] = 2 # 圆角归零(§3.6)
	# copy:中文游戏腔(技术原文照常并列,翻译层纪律;英文游戏腔对中文用户=乱码)
	t.copy = {
		"doc.list.empty": "还没有卷轴。",
		"doc.new": "+ 开新档", "doc.create": "开档",
		"doc.save": "存档", "doc.snapshot": "设存档点",
		"doc.rewind": "读档", "doc.rewind.confirm": "确认读档?",
		"doc.review": "全场扫描", "doc.export": "导出",
		"doc.status.dirty": "● 有改动", "doc.status.saved": "已存档",
		"doc.comment.placeholder": "这段有啥问题?", "doc.comment.send": "发送",
		"doc.comment.apply": "采纳", "doc.comment.thinking": "…",
		"doc.chat.placeholder": "说怎么改这篇文档…", "doc.chat.send": "上",
		"doc.view.edit": "编辑", "doc.view.preview": "预览", "doc.view.split": "分屏",
	}
	t.motion = {"focus-pulse": ThemeRegistry.MOTION_FULL, "change-flash": ThemeRegistry.MOTION_FULL,
		"text-reveal": ThemeRegistry.MOTION_FULL, "note-arrive": ThemeRegistry.MOTION_FULL,
		"stamp-press": ThemeRegistry.MOTION_FULL, "page-fan": ThemeRegistry.MOTION_FULL,
		"ui-press": ThemeRegistry.MOTION_FULL, "pop-in": ThemeRegistry.MOTION_FULL,
		"dock-in": ThemeRegistry.MOTION_FULL, "paper-shake": ThemeRegistry.MOTION_FULL,
		"status-in": ThemeRegistry.MOTION_FULL, "tick": ThemeRegistry.MOTION_FULL}
	return t


static func _paint(t: ThemePack, hex_map: Dictionary) -> void:
	for k in hex_map:
		t.colors[k] = Color(hex_map[k])


static func _sizes(t: ThemePack, _reserved := 0) -> void:
	# 排版(WEB-UI §3.2;12.5px → 12)
	t.sizes["text-xs"] = 11; t.sizes["text-sm"] = 12; t.sizes["text-md"] = 14
	t.sizes["text-lg"] = 16; t.sizes["text-xl"] = 20
	t.sizes["s1"] = 4; t.sizes["s2"] = 8; t.sizes["s3"] = 12
	t.sizes["s4"] = 16; t.sizes["s6"] = 24; t.sizes["s8"] = 32
