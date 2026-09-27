class_name ThemePack
extends RefCounted

## 主题包(docs/DEBUG-UI-THEMES.md §2 三层契约):可插拔数据包,组件代码永远只有一份。
## v1 主题 = 代码贡献(§6 政策);token 值逐字移植自 host/web/static/css/themes/*.css。

var id := ""
var display_name := ""
var mascot := "" # "mochi" | "sprite8" | ""(可整体关闭的可选层)

var colors: Dictionary = {} # token 名 → Color
var sizes: Dictionary = {}  # text-* / s* / r-* → int
var copy: Dictionary = {}   # 文案键(翻译层)
var motion: Dictionary = {} # 具名动效 → ThemeRegistry.MOTION_*


func motion_of(motion_name: String) -> int:
	return motion.get(motion_name, ThemeRegistry.MOTION_SUBTLE)
