# Desktop Widget 设计(C4)—— 层级体系的根

> 前置:`docs/COMPOUND-WIDGET.md`(协议 + C1.1/C3 实现注)。
> 本文定义根 compound:`desktop`。platform 壳(app.js)迁为它,
> app = desktop 的动态子件;任务栏/图标 = 子件的 card 面,窗口 = 子件的 tab 面。
> 验收:两套测试(stub + tests-ui)+ 逐条验收清单。

## 1. 现状盘点(platform 壳做了什么)

`web_platform/static/app.js` 当前壳层职责(迁移的对照表):

| 职责 | 现状 | compound 语义归宿 |
|---|---|---|
| 桌面主屏(壁纸 + 图标行 + 壁纸切换) | 全部 app 最小化后显示 | desktop layout 的「无激活子件」分支 |
| 窗口区(单激活最大化,标题栏:图标/标题/—/✕) | app.js 手写 DOM | 激活子件的 slot + desktop chrome |
| 任务栏(tab 列表:标题 + ✕;长按重排;卡片拖入打开) | app.js 手写 DOM | children 摘要行(读 slotRefs + badge) |
| tab 激活/最小化/关闭 | shell 状态机 | desktop state.active / view detach(hidden)/ remove_child |
| 托盘收件箱(supervisor pending) | 独立面板 | 第一个**系统子件**(预定义 slot) |
| 对话卡拖入 tab 条打开详情页 | DnD + openDetail | attach_existing / add_child |

## 2. desktop def

```js
{
  kind: "desktop",
  state_schema: { type: "object" },           // + wallpaper/active/icon_order/taskbar_order
  state_defaults: { wallpaper: "default", active: null, icon_order: [], taskbar_order: [] },
  events: ["child_event", "reparent", "activate", "close"],
  surfaces: ["tab"],                          // desktop 只有完整面(它就是根)
  compound: {
    slots: [
      { id: "inbox", kind: "supervisor-inbox", surface: "card" },  // 预定义系统件
    ],
    dynamic: { allow: ["conversation", "doc-editor", "skills-explorer", "runs-explorer",
                        "tools-explorer", "lab", "debug-console"], max: 30 },
    layout(state, slotRefs) -> html,          // §3 三分支:桌面/单窗/桌面+任务栏
    on_child_event(child, event, payload),    // 转发 + 记未读(badge 数据源,§4)
    child_context(child, frag),               // 注入 desktop 级上下文(用户/主题/全局状态)
  },
}
```

**寻址**:desktop path = `/root`;子件 `/root/<app-id>`;子件的子件(doc-editor 的
bubble)顺延 `/root/<app-id>/<bubble-id>`。全树寻址自此唯一。

## 3. layout 三分支(state 驱动,纯函数)

1. `active == null && children 全最小化` → **桌面**:壁纸 + 图标栅格
   (每个 child 一个图标:icon + 名称 + badge;图标 = 子的 card 面缩略或图标位);
2. `active == <id>` → **单窗**:窗口 chrome(标题栏:icon/标题/—/✕)+
   `[data-slot="<id>"]` 激活子件的 tab 面;其余子件 view detach(**hidden 语义**:
   instance 活着,state/context 照旧——最小化不丢状态是协议给的,不用自己造);
3. 任务栏恒在底部:children 摘要行(见 §4)。

**不发明浮动窗口**(红线 §11 不变):单激活最大化是外观与行为一致的窗口模型;
将来若要双窗并排,= 两个 slot 同屏(desktop state 加 left/right active),协议已兼容。

## 4. 任务栏与 badge:一个协议补丁

矛盾:§7 规定父 layout 只读 slotRefs 元信息,读不到子 state——但任务栏需要
每子的**未读/活跃徽标**(badge)。C3 的批注栏为此留在了宿主层;桌面不能留,
否则任务栏又回到手写。

补丁(协议级,COMPOUND-WIDGET §7 增补一条):
**slotRefs 增加 `badge` 元信息**——父在 `on_child_event` 闸门里维护
`state.badges[childId]`(闸门本来就是父的合法信息面),layout 读 slotRefs
时拼上。即:badge 不是父偷读子 state,是**父对子事件的记账**,语义干净。

任务栏行 = slotRefs 迭代:icon + 名称(子 def 的 title 元信息)+ badge +
激活态;点击 = activate(local action);✕ = close(remove_child,确认后);
长按重排 = taskbar_order state 变更 → relayout。

## 5. app 子件的两种来源

1. **静态注册**:每个 app 一个薄 def(conversation/doc-editor/skills/...),
   多半是已有页面的薄壳 compound——包一层 layout 把现有页面逻辑装进去,
   行为不动;doc-editor 已经是 compound,直接进 allow 白名单;
2. **动态打开**:对话卡「打开详情」/ 卡片拖入任务栏 =
   `add_child(kind, {state: {ref}})` 或 attach_existing(已在别处的 instance
   迁进窗口区——hard link 的真实用例:对话流里的 doc 卡( card 面)与窗口区的
   完整 doc-editor 是同一 instance)。

## 6. 迁移路径(四小步,每步两套测试绿)

| 步 | 内容 | 验收 |
|---|---|---|
| C4.1 ✅ | desktop def + layout 三分支 + 任务栏(badge 补丁);**与旧壳并存**:新页 `/platform/desktop.html` 先跑通 | tests-ui:开 app/最小化/重开 state 不动/关闭/重排 |
| C4.2 ✅ | conversation app 与 doc-editor 进白名单(薄壳/直进) | 既有行为测试 + 新 UI 测试 |
| C4.3 ✅ | 其余 apps(skills/runs/tools/lab/debug)薄壳化 | 同上 |
| C4.4 | platform index 切换到 desktop 根,旧壳退役 | 全量回归 + 旧壳代码删除 |

## 7. 验收清单(C4 全量)

- [ ] 桌面/单窗/任务栏三分支渲染正确,切换无 JS 错误(真实浏览器);
- [ ] 最小化 → 重开:app 内容逐字在(hidden 语义,不重建 instance);
- [ ] 关闭 = remove_child;重开后是全新 instance;
- [ ] 任务栏 badge 与子事件一致(闸门记账);重排持久于 state;
- [ ] 对话卡拖入 → 窗口区打开同一 ref;doc 卡 ↔ doc-editor 同 instance 双形态;
- [ ] inbox 预定义子件在托盘,升权请求到达有 badge;
- [ ] 全部 app action 仍走三态 exec;desktop 自身 action(activate/close/reorder)是 local;
- [ ] 寻址全树唯一:/root/... 通到 bubble 级。

## 8. 不做清单

- 不做浮动窗口/层叠/拖拽自由摆放(红线);
- 不做多桌面/workspace 切换(协议兼容,需求到了再开);
- 不做跨 app 的 state 总线(app 间通信只许经 skill/后端,不许 desktop 开后门);
- 旧壳(app.js)在 C4.4 之前不删(并存期双轨验收)。

## 9. 实现注(C4.1,2026-08-11)

> - **badge 协议补丁**(COMPOUND-WIDGET §7 增补条文;`js/widgets/compound.js`):
>   闸门放行的负载带 `badge` 字段(number 记 / 0·null 摘)→ 基座记
>   `state.badges[childId]`(可序列化)+ 值变 relayout;slotRefs 附 `badge`;
>   吞掉不记账;`remove_child` 清讫离树子件的账。同值幂等(不重复 relayout)。
> - **desktop def**(`js/widgets/w-desktop.js`):`DESKTOP_DEF`(kind `desktop`,
>   surfaces `["tab"]`,events `activate/close`,dynamic.allow = §2 白名单,
>   预定义 slot `inbox`)+ `SUPERVISOR_INBOX_DEF`(薄壳:card/tab 双形态,
>   pending 由宿主适配层喂;badge = pending 数,真实升权流 C4.2 接管)。
>   layout = `renderDesktopLayout` 纯函数,三分支按 §3;图标/标题取子 def 的
>   `aria.label`(首字 glyph,与 platform tab 同源惯例);inbox 系统件渲染在
>   托盘位,不进任务行/图标。
> - **新页** `/platform/desktop.html`(+ `desktop-page.js` 驱动 + app.py 同型
>   路由):行为层全 local——activate = `state.active` + relayout;最小化 =
>   激活位清空(子 view 摘下,§5 hidden,重开内容逐字在);关闭 = 两段确认
>   ✕ → `remove_child`;重排 = 任务行 pointer 拖拽(8px 阈值,落点中线判前后)
>   → `state.taskbar_order` + relayout;演示子件 = `conversation`/`runs-explorer`
>   占位薄壳(可输入状态面;真实 app 迁移 C4.2/4.3);inbox badge 由「模拟升权」
>   钮演示;`window.__desktop` 调试钩。
> - **与设计的偏差**(C4.1 从简,均留 C4.2+):
>   ① `child_context`(§2 设计有)未挂——def 无 state 面,注入只能是静态值,
>     用户/主题上下文 C4.4 接管 app.js 时落;
>   ② 桌面启动面 = 页顶「打开」钮(发起面对话化在 C4.4);桌面图标 =
>     已开子件(§3-1 原义),种子两个最小化 app 供首屏;
>   ③ inbox 空态不新立 copy 键(托盘只显计数,清单面 C4.2);
>   ④ 关闭确认 = 两段点击(2.5s arm),非模态(测试可驱动)。
> - **tests-ui 抓出两处**:拖拽收尾的 click 吞没旗标须下一拍清零(否则误吞
>   后续真实点击);test_doceditor 开文档等待从 800ms 定长改等挂载完成标记
>   `.doc-viewseg`(fresh 会话首拉 bubbles 较慢,与本次改动无关的既有隐患)。
> - **测试**:stub `desktop-widget.test.mjs`(def 结构/layout 三分支纯函数/集成:
>   预定义 inbox、白名单闸、hidden 语义 canonical 逐字、badge 记账、
>   taskbar_order 持久、remove 后全新 instance、activate/close 事件)+
>   `compound.test.mjs` badge 补丁块;31 文件全绿(旧壳 M5 桌面测试
>   desktop.test.mjs 并存不回退)。tests-ui `test_desktop.py`
>   30 项;四套(compound/desktop/doceditor/sandbox)114 项全绿,三跑稳定;
>   BUILD 2026-08-11.3 三方同步(desktop.html 直接按 .3 立)。

## 10. 实现注(C4.2,2026-08-11)

> - **conversation 薄壳**(`web_platform/static/conversation-app.js`,新):
>   `CONVERSATION_DEF` = compound(layout 纯函数 `renderConversation`:消息流 +
>   busy 骨架 + composer,卡面复用 cards.js `renderCardSurface`,零新渲染套);
>   `createConversation({load, onOpenDoc})` 工厂 = **实例级适配层**(会话装载/
>   发送/卡动作/升权作答/轮询汇聚一份,随实例生灭——不随 view);`mount_view`
>   重包 = layout 首渲 + 按 view 绑委托;消息增量只刷 log 区(draft 在
>   canonical,增量不重排 layout,输入不丢焦点)。行为契约与 app.js 同端点
>   (sessions/messages/cards·action/decisions answer/decisions|runs present),
>   orchestrator 意图/卡片/SSE·轮询照旧。load:`latest`(旧壳同语义,无则新建)
>   /`new`(顶栏「+ 新对话」= 新会话新实例)。占位 conversation 退役。
> - **doc-editor 直进**(§5-2;`doc-editor.js` 改造,旧壳 stub+UI 双绿在先):
>   `mountDocEditor` 拆为 `createDocEditor`(工厂:实例/def/compound/闭包一次)
>   + 兼容壳;`mount_view` 重包 = **自包含**(无骨架先落 docTabHtml 骨架)
>   + 按 view 绑定(`wireView`:viewseg/contextmenu/锚点钮/主对话/host 委托,
>   card 面只渲染不绑批注交互)+ 交互面 `cur` 收编全部宿主耦合(bubble 壳/
>   工具条/批注栏归属);`inst.relayout` 覆写带壳挂回(desktop 扇出经
>   live.update 也不掉壳)。打开路径:对话流 doc 卡「打开详情」→ 驱动
>   `openDocWindow`(fetch doc+bubbles → 工厂 → `_compoundId = 文档名` →
>   `attach_existing(slot=文档名)` → activate;同名聚焦不重复)。
> - **hard link 真实用例**:窗口区 tab 面 = 完整编辑器(交互面),对话流
>   doc 卡下挂同一 instance 的 card 面活视图(`link_view(surface:"card")` =
>   纯预览骨架,内容同源,relayout 扇出两面同步);log 重渲 wiping 后按
>   `isConnected` 摘旧 view 重挂(驱动 `_hangLiveDocCards`,conversation 的
>   onLogRendered 钩 + activate 后补挂)。
> - **inbox 真实化**:`/platform/api/decisions` 读面 + SSE decision.new 扇入
>   (断线回落 5s 轮询,同 app.js),pending 行(skill·tier 人话/reason_hint,
>   与 escalation 卡同语料)进 state,emit change {badge: pending 数} 记账;
>   托盘整卡 open → 回对话(决策在对话处理,同旧托盘)。C4.1 模拟升权退役。
> - **与设计的偏差**(C4.2 边界,留 C4.3+):
>   ① doc 写动作(snapshot/rewind/export/apply)走平台 app 实例管道,desktop
>     窗口 `getTabInstance: () => null` 暂惰(comment.send/chat/review 专属
>     端点正常);② detail 链接只接 doc,其余 kind(gate/pack/run...)未接;
>   ③ 会话切换下拉未迁(一会话一实例);④ 卡 DnD 未迁;⑤ conversation 未读
>     badge 未做(最小化期新消息徽标——需 owner 侧可见性判定,inbox 已示范
>     badge 通道);⑥ runs-explorer 仍占位。
> - **测试**:stub 新增 `conversation-app.test.mjs`(def 结构/layout 纯函数/
>   装载/发送契约/open-doc 上行/升权作答/轮询/序列化,fetch 全 stub);
>   32 文件全绿。tests-ui `test_desktop.py` 重写(34 项:真实对话出卡 →
>   doc 窗口完整编辑器 → 活卡同源 → 最小化重开消息+草稿逐字 → 重排 →
>   +新对话两段关闭 → doc 窗口右键开泡 → inbox 真实 pending 对照);
>   四套 118 项全绿(两跑);BUILD 2026-08-11.4 三方同步(desktop.html 加挂
>   platform.css:pfs-卡面/doc-editor chrome 样式同源)。

## 11. 实现注(C4.3,2026-08-12)

> - **五 explorer 薄壳**(`web_platform/static/explorer-apps.js`,新;占位
>   runs-explorer 退役):
>   - skills/tools/lab = legacy ES module **原样包装**(openX/closeX 一行不改):
>     薄壳 compound + 工厂;`mount_view` 重包——摘 view 时子树挪**保活囊**
>     (DOM 移动不销毁,`children[0]`+splice 逐子搬移,监听/模块单件态随元素
>     存活),重挂原树接回(逐字级 hidden);显式 destroy 才 close 模块;
>   - debug-console 同形但**重挂 = 重开**(dh.main 运行时引用宿主元素,保活
>     会写空树;数据在服务端/localStorage,重开即还原);
>   - runs-explorer = app.js runs 装配迁正自装(`/api/runs` + W-date 时间窗 +
>     行内 run 链接 + 深链;state(range/locate/rows)全 canonical,重挂从
>     state 重渲;locate 命中行提首——slice(8) 截断不漏高亮)。
>   - `locate(ref)` 定位面:未开 = 首挂带名打开;已开可见 = 关模块重开带名
>     (导航语义;lab 未保存 dirty 在跨草稿定位时不保,记此 caveat);
>     debug-console 无定位面(激活即定位)。
> - **detail 链接全 kind 路由**:conversation wireView 统一 emit
>   `open-detail {kind, ref}`(取代 open-doc;doc_create 同口)+ 驱动
>   `_DETAIL_ROUTE` 映射:doc→窗口区,run→runs,pack/decompose/publish→skills,
>   tool→tools,gate/diff/draft→lab,debug→debug-console;esc 不接(对话内处理)。
>   路由口有两条:conversation 内(wireView 回调)+ conversation 外(#dt-root
>   委托,跳过 [data-cv-log] 防双路由)——runs 行内链接走后一条。
> - **卡 DnD**(APP-MODEL §15 不变):`#dt-root` dragstart 产 envelope
>   {source, source_kind, ref}`.dt-tasks` 收 drop → `_CARD_ROUTE` 同旧映射
>   进路由;**整卡无 ref 的卡(如浏览表卡)拖了不路由**(行级 ref 在行链接上,
>   与 app.js 卡面拖开详情同语义);tests-ui 用「为什么挂」摘要卡(整卡 ref)验。
> - **conversation 未读 badge**(COMPOUND-WIDGET §7 增补「可见性条件」):
>   conversation 的 change 带 `arrived`(本轮 agent 新消息数;send/poll 两路);
>   驱动把 `DESKTOP_DEF.compound.on_child_event` 赋为带闭包的闸门——
>   **可见性在父不在子**:激活子改写 `badge:null` 摘徽(不记),最小化子累计
>   `unseen` 记 `badge:n`;`activate()` 清该子 unseen + state.badges。
> - **会话切换裁决**:**保持「一会话一实例」(app 即会话),会话切换不做进
>   窗口;发起面加会话列表**(既有会话 → 按 id 开 conversation 窗,同会话
>   去重聚焦)。理由:desktop 心智里窗 = app 实例,conversation 的实例身份
>   即会话(path `/conv/<sid>`、state.session);窗内切换会话 = 实例身份
>   中途换绑,既糊寻址又造两个会话切换面(旧壳 sessionSel + 窗内下拉);
>   「开会话」语义 = 发起(与开文档同构),不是窗口模式切换。
> - **与设计的偏差**(本期):① lab 跨草稿 locate 不保未保存 dirty(导航语义,
>   模块级 dirty 无跨草稿暂存面);② debug-console 无 ref 定位面(激活即定位);
>   ③ 浏览表卡整卡拖不路由(无整卡 ref);④ doc 写动作暂惰照旧(C4.4);
>   ⑤ conversation 窗题名统一「对话」(aria.label 静态;区分靠任务栏 title,
>   per-instance 题名需 slotRefs 加 meta,C4.4 评估)。
> - **测试**:stub 新增 `explorer-apps.test.mjs`(五 def 注册面/runs layout
>   纯+range+locate/fetch 装载/包装器机制探针——保活囊 hidden/destroy close
>   /locate 三分支,fake open/close 断账)+ `conversation-app.test.mjs` ④ 改
>   open-detail 全 kind;33 文件全绿。tests-ui `test_desktop.py` 扩为 55 项
>   (badge 三态/run 链接 locate/DnD/四 legacy 开·保活·关/会话去重/重排/
>   C4.2 链不回退);五套 135 项全绿(两跑);BUILD 2026-08-11.5 三方同步。
