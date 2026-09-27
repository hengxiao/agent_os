class_name DocServices
extends RefCounted

## Doc Editor app 的宿主服务束(窗口装配时注入;widget 层永远拿不到)。

var client: AgentOsClient
var pipeline: ActionPipeline
var registry: WidgetRegistry
var tree: WidgetTree
## 复制全文到系统剪贴板
var export_copy: Callable = func(_t: String) -> void: pass
## 状态汇报 func(msg, tone)——窗口状态栏的投递口;tone: "info"|"ok"|"danger"
var report: Callable = func(_m: String, _t: String) -> void: pass
