# Compound Widget 协议 —— 层级组装体系

> 地位:与 `docs/WIDGETS.md`(基元协议)、`docs/WIDGET-ARCH.md`(渲染/逻辑分离)同级。
> 本文定义**复合 widget**(compound):本身也是 widget,但拥有子 widget,并定义
> 渲染组合、连接、context 下发、信息管控、动态生灭、跨父转移、多视图(hard link)。
> 层级一路向上,根是 **desktop widget**。
>
> 设计公理(继承,不再论证):所有 widget 都有 context;所有 action 都是 skill;
> widget 不出海(出海只有 events);render 是纯函数;state 可序列化。

## 1. 核心概念

| 概念 | 定义 | 类比(ext4) |
|---|---|---|
| **instance** | `createWidget(def)` 的产物:state + events + 行为 API,身份唯一 | inode |
| **owner** | 拥有某 instance 的 compound;**一个 instance 同一时刻只有一个 owner** | 文件的父目录(唯一) |
| **slot** | owner layout 里给子件的占位(`<div data-slot>`),分预定义与动态两类 | 目录项 |
| **view** | instance 的一个渲染视图 = {host 元素, surface(card/tab), 绑定}。**一个 instance 可有 N 个 view,挂在意多个 DOM 位置** | hard link(多个目录项指向同一 inode) |
| **path** | instance 的寻址 = owner.path + "/" + 子段;ownership 路径全树唯一 | 绝对路径 |

ownership 唯一 + view 多处,是 hard link 语义的精确对应:**link 可以遍地,inode 只有一个**。

## 2. CompoundWidgetDef(WidgetDef 的增量)

```js
{
  ...WidgetDef 全部字段,                 // kind/v/state_schema/actions/events/aria/surfaces/context_provider
  compound: {
    slots: [                             // 预定义子件清单(静态,随实例创建)
      { id: "editor", kind: "md-viewer", state: {...}, surface: "tab" },
      { id: "sidebar", kind: "ns-tree", surface: "card" },
    ],
    dynamic: {                           // 动态子件集合(可空缺省=不许动态)
      allow: ["chat-bubble", "log-viewer"],  // 允许动态加入的 kind(白名单)
      max: 50,
    },
    layout(state, slotRefs) -> html,     // 纯函数:父 chrome + 占位;slotRefs = {id: {path, kind, surface}}
    on_child_event(child, event, payload) -> bool,   // 事件闸门:返回 false 吞掉(可选)
    child_context(child, fragment) -> fragment,      // 信息管控:改写子 context fragment(可选)
  },
}
```

registry 校验增量:`compound.layout` 必为函数;slots 的 kind 必须在注册表存在(惰性校验,mount 期);
dynamic.allow ⊆ 注册表;`on_child_event`/`child_context` 给了必须是函数。

## 3. 渲染协议

1. 父 `layout(state, slotRefs)` 是纯函数,产出父 chrome HTML,**子件位置只放 `<div data-slot="id">` 占位——子件 HTML 一律不内联进父产出**(嵌套转义、重渲级联、XSS 面全部由此消失);
2. 父 innerHTML 落 layout 后,每个子实例 `mount_view(slotEl, {surface})` 进自己的占位;
3. **重渲扇出规则**:子 `update()` 只重渲该子的全部 view;父不因子 state 变化重渲;父 state 变化 → 父 layout 重渲 → 占位重建 → 子 view **重挂**(detach→attach,instance 与 state 不动);
4. 父 layout 只能读 slotRefs 的元信息(path/kind/surface/摘要),**读不到子 state 内部**——信息管控的渲染面(§7)。

## 4. 子实例管理(compound 实例 API)

- 预定义:创建时按 `compound.slots` 逐个 `createWidget` + 注册 path;
- `add_child(kind, {state, surface, slot?}) -> child`:动态创建(dynamic.allow 白名单 + max 闸);
- `attach_existing(child, {slot, surface})`:收养一个已存在实例(转移与 hard link 的入口;child 必须无 owner 或已从原 owner detach);
- `remove_child(id, {destroy = true})`:移除;`destroy:false` = **detach**(instance 活着,可被别家 attach);
- `move_child(id, newOwner, {slot})` = `remove_child(id, {destroy:false})` + `newOwner.attach_existing(...)` 的事务包装(两步任一失败回滚);
- `child(id)` / `children()` / `children_snapshot()`(可序列化:[{id, kind, slot, path}]);
- destroy 语义:`instance.destroy()` 递归销毁全部 view 与子树;`view.detach()` 只摘一个视图。

## 5. 多视图(hard link)

```js
inst.mount_view(host, {surface: "card"});   // 视图 A:卡片
inst.mount_view(host2, {surface: "tab"});   // 视图 B:完整 —— 同一 instance
```

- view 挂载点**不限于 owner 的 slot 内**:只要 DOM 可达,任何位置都能挂(跨父、跨页面区域);
- `update()` 扇出到全部 view,各按自己的 surface 渲染(同 state 两面孔,§W5.6 协议的实例化);
- 事件 listener 挂在 instance 上,不随 view 复制;view 内的 DOM 委托各自独立;
- 计数:`inst.views.length`;最后一个 view detach 时 instance **不销毁**(它仍是 owner 的子,只是不可见——`hidden` 语义,state/context 照旧)。

## 6. 跨父转移(reparent)

语义 = `mv`:state 不动、身份不变、path 重算。

1. `oldOwner.remove_child(id, {destroy:false})`:旧 path 注销(onUnregister + cascade provider 注销),view 随迁出;
2. `newOwner.attach_existing(child, {slot})`:新 path 注册(onRegister + provider 按新 path 重注册),view 挂进新 slot;
3. 全树广播 `reparent` 事件:{child, from: oldPath, to: newPath}——寻址依赖方(agent、调试器)据此改指;
4. 迁移中的 view 闪烁控制:detach→attach 同帧执行,无中间空态。

## 7. 信息管控与事件闸门

父对子的管控只有三个正式通道(不许父直接摸子 state 内部):

1. **事件闸门** `on_child_event(child, event, payload)`:子 emit 先经父;返回 false 吞掉,返回 true 继续上行(父也可改写 payload 后再上行——返回 `{payload}` 变体);
2. **context 改写** `child_context(child, fragment)`:cascade 收集子 context fragment 时经父改写(过滤/脱敏/补充)——「父控制子对外提供什么信息」的运行时面;
3. **surface 与可见性管控**:父决定子在自己 layout 里以什么 surface 渲染、显隐(slot 占位渲染但 `hidden`)。

cascade 协议(`cascade.js`)不变:provider 按 path 注册;reparent 时按新 path 重注册(§6)。

## 8. Desktop widget(根)

`kind: "desktop"` 的 compound:

- state:{wallpaper, taskbar 顺序, 激活子 id};
- dynamic children = 各 app/窗口(不限 kind 白名单,或 app 级白名单);
- layout = 壁纸区 + 图标列 + 窗口区(子 slot)+ 任务栏(子件摘要,读 slotRefs);
- 桌面自身也注册 path `"root"`,全树寻址从它开始;
- 桌面的 action(开新件/关件/重排)同样是 skill(exec 三态),纯 UI 操作 = local。

## 9. 分期

| 期 | 内容 | 验收 |
|---|---|---|
| C1 ✅ | `compound.js` 基座:createCompound、slots/动态生灭/attach/detach/move、多 view 扇出、事件闸门、context 改写、reparent 广播;协议测试 | 四能力(预定义/动态/转移/hard link)各有测试 |
| C2 ✅ | playground 演示件(`web/static/compound.html` 新页):双栏 compound,可从列表加件、左右互移、同一实例左 card 右 tab | 目检可走通四能力 |
| C3 ✅ | doc-editor 迁为 compound(md-viewer + 动态 bubble 子件) | 既有行为测试不破 |
| C4 | desktop widget 落地,platform 壳迁移 | 另行设计 |

> **C1/C2 实现注**(2026-08-05,分支 debugger):
> - **C1 基座**(`js/widgets/compound.js`):`createCompound(def, opts)` 全协议——
>   预定义 slots 随实例创建;`add_child`(dynamic.allow 白名单 + max 闸)/
>   `attach_existing`(ownership 唯一校验 + 祖先链防环,§10)/
>   `remove_child({destroy})`(destroy:false = detach,实例可被别家 attach)/
>   `move_child`(remove+attach 事务包装,attach 失败回滚挂回原 owner)/
>   `child(id)`/`children()`/`children_snapshot()`(可序列化);
>   多 view(§5)= view{host, surface, live},live 是该 kind mount 全装的**视图
>   实例**,接线 = state 引用同一化(live.state = canonical.state)+ emit 转发
>   (live.emit = canonical.emit)——事件只有一份、先入 `on_child_event` 闸门
>   (false 吞 / true 上行 / {payload} 改写),统一包 `child_event` 上行;
>   渲染协议(§3):layout 纯函数只放 `<div data-slot>`(不内联),父 innerHTML
>   后子 view 进占位,结构变化触发父 relayout → 子 view detach→attach 同帧重挂
>   (canonical/state 不动);`child_context` 在 compound 注册的 cascade provider
>   里改写(§7-2);reparent:path 重算(canonical.path 也随)+ onUnregister/
>   onRegister 链 + provider 按新 path 重注册 + 全树 `reparent` 事件
>   ({child, from, to},新旧 owner 各发)。
> - **接线与生命周期两条裁决**(实现期实测,记录备查):
>   1) view 挂载不给 path(挂成 "")——mount 内 widget.register 的注册/注销会
>      把 view detach 误伤成 cascade provider 摘除,§5 hidden 语义(末 view
>      detach 后 state/context 照旧)要求 provider 生命周期归 compound 统一;
>   2) 接线后首渲对齐——mount 用自己的 options 先渲,swap 到 canonical state
>      后补一次 update({})/render 重渲。
> - **registry 增量**(§2):`def.compound.layout/on_child_event/child_context`
>   形态校验 + `def.mount` 函数校验;slots/dynamic.allow 的 kind ⊆ 注册表 =
>   惰性校验(mount/add 期);13 个 def 挂 `mount` 字段(函数声明提升,def
>   字面量期可用);`move_child(id, newOwner, {slot, surface?})` 的 surface
>   为文档 {slot} 的扩展项(card↔tab 演示需要,已注)。
> - **C2 playground**(`web/static/compound.html` + `js/compound-playground.js`):
>   pg-root 的左右栏 = pg-stack/pg-stage 两个 compound(compound 套 compound,
>   栈 def 的 mount 用「本页单实例」闭包桥),加件/互移/✕/⧉链接全部走公开
>   API;链接区把 id 的第二 view 以 tab 挂入(与左卡同 instance,update 扇出
>   可目检);reparent 广播与 child_event 上屏;BUILD 三方同步(2026-08-05.4)。
> - **测试**:tests/compound.test.mjs(新文件;七块 + C2 冒烟)——预定义渲染
>   + layout 不内联、动态生灭(白名单/max/detach 活/destroy 静默)、move 全链
>   (path/context/state/广播/回滚)、hard link(双 view 同 state 引用、扇出
>   同步、事件一份、detach hidden)、闸门三态、context 改写、防环;dom-stub
>   面断言走 slot 区域串([data-slot] 无值区域提取)。

## 10. 不做清单

- 不做跨进程/跨页面 instance 共享(view 共享只在同文档内);
- 不做父对子 state 的任意读写(§7 三通道之外无后门);
- 不做 layout 内联子 HTML(§3-1);
- 不做环(ownership 是树,attach 前查祖先链防环);
- 不发明第二套寻址(path 协议沿用 APP-MODEL §14)。

## 11. 实现注(C1.1 真实浏览器修复,2026-08-11)

> 由 tests-ui(真实 Chromium)抓出、stub 测不出的两个基座 bug:
>
> 1. **mount_view 名撞 = 无限递归**(RangeError,页面零渲染):hard link 入口
>    曾覆写实例的 `mount_view`,而 compound 子件的「父视图挂载」也叫
>    `mount_view`——def.mount 桥一回 call 就成死循环。协议修正:hard link
>    入口独立命名 **`link_view(host, {surface})`**;`mount_view` 只保留
>    「compound 渲染自己的 layout+子树」一义。§5 的 `inst.mount_view`
>    字样均按 `link_view` 理解。
> 2. **僵尸子件**:`_spawn` 曾对 compound kind 也 createWidget,子件有 def
>    无 compound API。修正:kind 带 compound 字段时递归 `createCompound`;
>    compound 子件的 view 装配走实例自身 `mount_view`(不再需要 def.mount 桥),
>    其 update 面 = 新增 `inst.relayout()`。
>
> 另:UI 测试架构落地于 `web/static/tests-ui/`(Playwright + 项目内隔离
> Chromium;`./run.sh`),test_compound 四能力与 test_sandbox 全控件矩阵
> 在真实浏览器全绿——此后 compound 层改动以两套都绿为交付门槛。

## 12. 实现注(C3 doc-editor 迁移,2026-08-11)

> 第一个产品级 compound:`web_platform/static/doc-editor.js` 重写为
> `createCompound`(kind `doc-editor`),对外 API 与 fetch 路径零变化。
>
> - **def 结构**:预定义 slot `doc` = md-viewer(文档主体;state.options
>   均给 `view:"source"` + `bar:false`,chrome 归宿主);dynamic.allow =
>   `["chat-bubble"]`(段落批注,生灭随右键/锚点钮/评审);`layout(state)`
>   纯函数——`view:"preview"` 出 D5 同构段落块 chrome(doc-para/锚点钮/
>   changed/guide),`"source"` 出 `<div data-slot="doc">` 占位(§3-3 占位
>   进出不动子 instance)。def 按挂载实例注册(同名 kind 覆盖;旧实例持旧
>   闭包)——layout/child_context 需要 mount 级 currentText 闭包。
> - **管控三通道全用**(§7):`child_context` 给每个 bubble 注入
>   `{anchor, paragraph, full_text}`(widget 级 fragment;app 级 provider
>   照原注册);`on_child_event` 吞 open、放行 submit/apply/close →
>   `child_event` 监听接管(comment.send/apply 出海仍父级);可见性管控 =
>   close 后宿主摘壳。
> - **切割线不动**(W5.4):浮出定位壳/未读游标/批注栏仍宿主职责——
>   bubble 的 view 经 §5 `link_view` 挂进宿主壳内(视图不限 slot 内),
>   **不进 layout 占位**;批注栏读子 state 的未读条数,layout 读不到(§7
>   layout 只能读 slotRefs 元信息);seen 游标进 compound state(可序列化,
>   localStorage 备份照原)。
> - **基座两处修补**(tests-ui 抓出,stub 测不出):
>   1) `_slotEl` 退化路径收窄——`[data-slot]` 无值 fallback 仅供 stub
>      区域提取,命中元素必须本身不带值;否则动态子件(slot id = 锚点串,
>      永无占位)在真实 DOM 会错挂进别的 slot 占位(bubble 挤进 doc 占位);
>   2) `childInst.views = rec.views` 仅限 leaf 子件——compound 子有自身
>      父视图账(mount_view 记),覆盖会两账混一,fanout 撞上无 live 的
>      父视图记录(TypeError)。
> - **child_event 负载形态**:`{child: rec.id(字符串), event, payload}`
>   ——handler 取锚须 `typeof child === "string" ? child : ...`,不能
>   `child.state`(静默哑火)。
> - **测试**:stub compound.test.mjs 追加 C3 块(children_snapshot/
>   link_view 挂壳/child_context 信封/close 可见性/source slot 进出),
>   widgets.test.mjs 的 W6.7 doc-editor 断言改写为 compound 形态,30 文件
>   全绿;tests-ui 新增 test_doceditor.py(真实 Chromium:对话发卡 →
>   开文档 → 右键开泡 → 发消息 → 收起 → 批注跳转 → view source 往返),
>   三套(compound/doceditor/sandbox)全绿;BUILD 2026-08-11.2 三方同步。
