class_name WidgetTree
extends RefCounted

## Widget 树 + 寻址(docs/APP-MODEL.md §14):路径 = ownership 链,与视图无关;
## agent 三动词里 read/focus 全开放,act 的代理收口留待后续(§14.4,本期不对 agent 开放)。

var root: CompoundWidget
var cascade: ContextCascade


func _init() -> void:
	root = DesktopRoot.new(DesktopRootDef.new(), {})
	cascade = ContextCascade.new(self)


## 路径解析(查树不查视图):/root/seg/seg…;不存在返回 null
func resolve(path: String) -> Widget:
	if path.is_empty():
		return null
	var segs := path.split("/")
	var i := 0
	if segs.size() > 0 and segs[0].is_empty():
		i = 1 # 前导斜杠
	if i >= segs.size() or segs[i] != "root":
		return null
	var cur: Widget = root
	i += 1
	while i < segs.size():
		if not cur is CompoundWidget:
			return null
		cur = (cur as CompoundWidget).find_child(segs[i])
		if cur == null:
			return null
		i += 1
	return cur


## read 动词(§14.3):人话摘要(禁忌词纪律由 widget 摘要层保证)
func read_summary(path: String) -> String:
	var w := resolve(path)
	return "" if w == null else w.summary()


## focus 动词(§14.3):滚动到视图 + 高亮脉冲(reduced-motion 时即时切换)
func focus(path: String) -> bool:
	var w := resolve(path)
	if w == null:
		return false
	var p := w.root.get_parent()
	while p != null:
		if p is ScrollContainer:
			(p as ScrollContainer).ensure_control_visible(w.root)
			break
		p = p.get_parent()
	MotionPlayer.play("focus-pulse", w.root)
	return true


## 调试用:整树路径清单
func dump_paths() -> String:
	var lines: Array[String] = []
	_dump_rec(root, lines)
	return "\n".join(lines)


func _dump_rec(w: Widget, lines: Array[String]) -> void:
	lines.append("%s  [%s]" % [w.path, w.get_kind()])
	if w is CompoundWidget:
		for ch in (w as CompoundWidget).children:
			_dump_rec(ch, lines)


## 根 compound 定义面(COMPOUND-WIDGET.md §8 / DESKTOP-WIDGET.md §2)
class DesktopRootDef:
	extends WidgetDef

	func get_kind() -> String:
		return "desktop"

	func create_instance(state: Dictionary) -> Widget:
		return DesktopRoot.new(self, state)


## path = "/root",全树寻址从这里开始。v1 的 root 只做寻址/级联,不渲染 chrome
## (窗口 chrome 是宿主的职责;壁纸/图标/任务栏属后续 3D 化的桌面 app)。
class DesktopRoot:
	extends CompoundWidget

	func _init(def: WidgetDef, state: Dictionary) -> void:
		super(def, state)
		path_segment = "root"
		path = "/root"

	## shell 级 fragment(C4.4 child_context 同构:user/theme/at)
	func context_fragment() -> Dictionary:
		return {
			"user": "godot-editor",
			"theme": ThemeRegistry.current.id if ThemeRegistry.current != null else "",
			"at": Time.get_datetime_string_from_system(),
		}

	func summary() -> String:
		return "Agent OS 桌面根(Godot)"
