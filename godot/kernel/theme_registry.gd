class_name ThemeRegistry
extends RefCounted

## 主题注册表(docs/DEBUG-UI-THEMES.md §2.5):声明式清单 + 契约校验
## (缺变量 → 不注册)+ 切换持久化 + 顺序循环。组件零分支:组件只消费
## current 的 token/copy/motion,不出现主题 id 字符串。
## (GDScript 无静态信号 → 监听器 Callable 列表替代 theme_changed 事件。)

enum { MOTION_FULL, MOTION_SUBTLE, MOTION_INSTANT }

## 契约清单(§2.1 全集 + 组件实际消费的扩展 token)
const REQUIRED_COLORS := [
	"bg-0", "bg-1", "bg-2", "bg-3", "line", "line-strong",
	"fg-0", "fg-1", "fg-2",
	"ok", "warn", "danger", "aborted", "live",
	"sig-llm", "sig-tool", "sig-sidecar", "sig-compress", "sig-budget", "sig-frame",
	"perm-read", "perm-write", "perm-net", "perm-exec",
	"focus-ring",
]

const REQUIRED_SIZES := [
	"text-xs", "text-sm", "text-md", "text-lg", "text-xl",
	"s1", "s2", "s3", "s4", "s6", "s8",
	"r-sm", "r-md", "r-lg",
]

## Doc Editor 用到的 copy 键(doc.* 主题;技术原文豁免直读)
const REQUIRED_COPY := [
	"doc.list.empty", "doc.new", "doc.create",
	"doc.save", "doc.snapshot", "doc.rewind", "doc.rewind.confirm", "doc.review", "doc.export",
	"doc.status.dirty", "doc.status.saved",
	"doc.comment.placeholder", "doc.comment.send", "doc.comment.apply", "doc.comment.thinking",
	"doc.chat.placeholder", "doc.chat.send",
	"doc.view.edit", "doc.view.preview", "doc.view.split",
]

const CONFIG_PATH := "user://agent-os-godot.cfg"

static var _packs: Dictionary = {}
static var _order: Array = []
static var current: ThemePack = null
## 对齐 prefers-reduced-motion:所有具名动效解析为 Instant,对所有主题生效。
static var reduced_motion := false
static var _listeners: Array[Callable] = []


## 契约:返回缺失项清单;空 = 通过
static func validate(pack: ThemePack) -> Array:
	var missing: Array = []
	for c in REQUIRED_COLORS:
		if not pack.colors.has(c):
			missing.append("color:" + c)
	for s in REQUIRED_SIZES:
		if not pack.sizes.has(s):
			missing.append("size:" + s)
	for k in REQUIRED_COPY:
		if not pack.copy.has(k):
			missing.append("copy:" + k)
	return missing


## 契约不过不注册
static func register(pack: ThemePack) -> bool:
	var missing := validate(pack)
	if not missing.is_empty():
		push_error("主题 %s 缺契约项,拒注册:%s" % [pack.id, ", ".join(missing)])
		return false
	if not _packs.has(pack.id):
		_order.append(pack)
	_packs[pack.id] = pack
	if current == null:
		current = pack
	return true


static func apply(id: String) -> void:
	if not _packs.has(id):
		return
	current = _packs[id]
	_save_pref(id)
	_notify()


## 注册表顺序循环(与 web 托盘主题切换同一通道语义)
static func cycle() -> String:
	if _order.is_empty():
		return ""
	var i := _order.find(current)
	var next: ThemePack = _order[(i + 1) % _order.size()]
	apply(next.id)
	return next.id


## 启动恢复(持久化偏好;缺省第一个注册主题)
static func restore_saved() -> void:
	var saved := _load_pref()
	if not saved.is_empty() and _packs.has(saved):
		current = _packs[saved]
	if current == null and not _order.is_empty():
		current = _order[0]


static func add_listener(cb: Callable) -> void:
	if not _listeners.has(cb):
		_listeners.append(cb)


static func remove_listener(cb: Callable) -> void:
	_listeners.erase(cb)


static func _notify() -> void:
	for cb in _listeners:
		cb.call()


static func _save_pref(theme_id: String) -> void:
	var cfg := ConfigFile.new()
	cfg.load(CONFIG_PATH) # 已有配置保留(如 server url)
	cfg.set_value("ui", "theme", theme_id)
	cfg.save(CONFIG_PATH)


static func _load_pref() -> String:
	var cfg := ConfigFile.new()
	if cfg.load(CONFIG_PATH) != OK:
		return ""
	return str(cfg.get_value("ui", "theme", ""))
