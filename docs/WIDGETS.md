# 基础 Widget 库设计计划

> 版本:v0.1(计划)
> 对象:web_platform 的基础控件层——text editor / table editor 等标准 widget
> 关系:APP-MODEL.md v0.3 是架构基线(app→surfaces→sections→**widgets**);
>   widget 注册与寻址遵守 §14;动作归态遵守三态 exec(§4);主题契约遵守
>   DEBUG-UI-THEMES.md(token + copy 六主题 + 组件零分支)
> 一句话:**widget 是叶子控件——有状态、发事件、可调 exec;永不直接调后端。**

---

## 1. 分层与协议

### 1.1 分层

```
app(业务对象:skill-pack/run/...)
└─ surface(card / tab 两张面孔)
   └─ section(逻辑区块:tests/findings/members)
      └─ widget(基础控件:本文目录)
```

### 1.2 Widget 协议(每个控件的定义面)

```jsonc
// WidgetDef(注册表静态面;与 AppManifest 同哲学但更小)
{
  "kind": "json-editor",
  "v": 1,
  "state_schema": { /* 控件的本地状态 */ },
  "actions": [   // 控件自己的动作(只许 local;出海参见 §1.3)
    { "id": "set_value", "exec": "local", "args_input": {"value": {"type": "string"}} },
    { "id": "format",    "exec": "local" }
  ],
  "events": ["change", "commit", "error"],  // 发给父组件的事件(父组件映射到 app action)
  "aria": { "role": "textbox", "keys": ["Enter", "Escape"] },
  "surfaces": ["card", "tab"]   // 允许出现的面孔(控件也可声明只进 tab)
}
```

### 1.3 三条铁律

1. **widget 永不直接调后端**:它只有本地状态(state)与事件(events);
   需要出海时,父组件(app/section)把事件映射成自己的 action(exec 三态)
   ——授权面因此只在 app 层存在一次,控件不需要权限概念;
2. **本地状态可序列化**:刷新/重渲染可恢复(dirty/value/selection);
   widget 注册进 §14 registry 后获得路径,agent 可 read/focus;
3. **四态齐备**:每个可交互控件都有 hover/focus-visible/active/disabled
   设计,focus 环六主题可见(商业 widget 标准,沿用)。

## 2. Widget 目录(每个的定义)

### W-text — text editor(文本编辑器)

- **用途**:description/prompt/notes 等多行文本;变量:mono(prompt/code)与正文两种;
- **state**:{value, dirty, readonly, lang: "plain"|"prompt"|"md", wrap: bool};
- **actions**:set_value(local)、commit(local,发 commit 事件)、revert(local);
- **细节**:行号(可选 mono 变体)、选区保留(重渲染不丢)、占位符、字数/行数微标;
- **a11y**:role=textbox-multiline,aria-label 必填,Esc=blur;
- **测试**:输入/选区保留/dirty 标记/readonly 拒输入/六主题 copy。

### W-json — JSON editor(带校验的文本编辑器)

- **用途**:inputs/outputs schema、参数 JSON(F11 的 textarea 升级版);
- **state**:{value, dirty, error: {line, message}|null, schema: object|null};
- **actions**:set_value(local,即时 JSON 合法性校验)、format(local)、validate(local,按 schema);
- **细节**:错误行高亮 + 行号、失焦校验、format 一键美化、schema 不合时**行级**错误提示;
- **测试**:非法 JSON 行定位/schema 不合提示/format 幂等/错误恢复。

### W-table — table editor(表格编辑器)

- **用途**:tests 用例表、members 表、键值参数表;
- **state**:{rows: [{id, cells: {...}}], selected: [ids], schema: {columns: [{key, type, label, required?}]}};
- **actions**:add_row(local)、remove_row(local)、move_row(local,**走 §15 DnD envelope**)、set_cell(local)、remove_selected(local);
- **细节**:列定义驱动(文本/数字/布尔/枚举单元格编辑器)、行 DnD 排序(标准 envelope)、新增行骨架、空态("还没有行,点下方添加");
- **a11y**:role=grid,行可键盘上下移动(Alt+↑/↓);
- **测试**:列类型渲染/行增删移/DnD envelope 合规/空态。

### W-kv — key-value editor(键值编辑器)

- **用途**:attrs/metadata/配置映射(table 的两列特化);
- **state**:{entries: [{key, value}], allow_dup: bool};
- **actions**:add/remove/set(local);重复 key 即时警示(非硬拦,警告态);
- **测试**:重复 key 警示/序列化往返。

### W-form — schema 驱动表单

- **用途**:run.launch 参数面(textarea JSON 的正式升级)、skill inputs 填写;
- **state**:{values: {...}, errors: {field: msg}, schema: object};
- **actions**:set_field(local)、validate(local)、reset(local);
- **细节**:按 JSON Schema 生成字段(string/number/integer/boolean/enum/array/object 嵌套);required 星标;integer 的 minimum/maximum 约束;数组项编辑器(内嵌 W-table 单行版);
- **测试**:六类型字段生成/required 校验/嵌套对象/与 skeletonFromSchema 一致的默认值。

### W-list — 可选列表

- **用途**:草稿列表、技能选择、会话列表;
- **state**:{items: [{id, label, hint?, icon?}], selected: id|[ids], filter: string};
- **actions**:select(local)、filter(local)、activate(local,Enter);
- **细节**:搜索过滤(命中祖先链展开——复用 ns-tree 语义)、单/多选、键盘导航(↑↓/Enter);
- **测试**:过滤/多选/键盘路径/空态。

### W-tree — 命名空间树(ns-tree 控件化)

- **用途**:Skills/Tools 树、包成员树;
- **state**:{nodes, expanded: Set, selected};
- **actions**:toggle(local)、select(local)、filter(local);
- **细节**:单层链折叠、默认展开策略(沿用 L4.5)、可拖节点(§15);
- **测试**:沿用 ns-tree 现有测试语义 + 控件协议面。

### W-diff — diff 查看器(现 diff 渲染控件化)

- **用途**:iterate diff、版本对比;
- **state**:{left, right, mode: "split"|"unified"};
- **actions**:set_mode(local);
- **细节**:字段两列 + 行级红绿(既有呈现提取为控件)、长文本折叠上下文;
- **测试**:三态渲染/折叠/模式切换。

### W-md — Markdown 查看器(安全渲染)

- **用途**:agent 消息、NOTES.md、报告;
- **state**:{source};
- **细节**:白名单标签(标题/列表/代码块/链接/表格),**禁 HTML 注入**(纯文本转义后白名单渲染,不 innerHTML 原文);
- **测试**:XSS 向量(<script>/onerror 属性)全部转义、代码块 mono。

### W-log — 日志/终端查看器

- **用途**:run 输出、trace、调试控制台;
- **state**:{lines: [...], follow: bool, filter};
- **actions**:append(local)、toggle_follow(local)、filter(local)、copy_all(local);
- **细节**:跟随模式(新行自动滚底,上滚即暂停跟随并显示"回到底部"钮)、长窗口截断(保留尾部 N 行)、kind 着色(信号色 token);
- **测试**:跟随/暂停/截断/复制。

### W-chart — 图表(纯 SVG,零依赖)

- **用途**:usage 趋势(token/成本随时间)、迭代分数曲线(版本间对比)、
  run 时长分布;**不引图表库**(零 bundler 架构,SVG 直绘);
- **state**:{series: [{name, points: [{x, y}]}], type: "line"|"bar"|"spark",
  extent: {xmin, xmax, ymin, ymax} | "auto"};
- **actions**:set_series(local)、hover(local,读值气泡)、toggle_series(local,多序列显隐);
- **细节**:网格/轴刻度自动;空数据态("还没有数据");spark 变体(无轴行内迷你图,用于表格行内);长序列抽稀(>500 点降采样);
- **a11y(图表的硬规则)**:**必须有等价数据表备选**——图旁可切"表格视图"(同一份 series 渲染成 W-table),读屏与打印都走它;图本体 aria-hidden=false + role=img + aria-label 摘要;
- **测试**:三类型渲染/空态/抽稀/hover 读值/表格视图等价数据/六主题(token 色,不内嵌调色板)。

### W-date — 日期时间控件

- **用途**:browse 的时间窗(上周/今天/自定义区间)、任务排期(未来的
  schedule 面)、报告区间;
- **state**:{value: iso string | {start, end}, mode: "date"|"datetime"|"range",
  min?, max?};
- **actions**:set(local,键盘输入)、prev/next(local,翻页)、pick(local,日历点选)、
  quick(local,今天/昨天/本周/上周 快捷项);
- **细节**:输入与日历双通道(输入即时校验 ISO 格式);range 模式下
  start>end 即时警示;月份翻页键盘可达(←→);时区按本地显示(不引入
  时区选择,见不做);
- **测试**:键盘输入校验/range 倒置警示/快捷项/翻页键盘路径/六主题。

### W-bubble — 聊天气泡(锚点评论,核心控件)

- **用途**:挂在任意 widget 上的 Confluence 式 comment——右键(或锚点钮)
  在文本/字段/用例上开气泡,提疑问或修改意见,**气泡里的助手会回复**;
  是 Flow C 边注的完全体(边注=只进不出,气泡=可来回);
- **state**:{anchor: <§14 路径+可选 span>, messages: [{role, text, ts}],
  open: bool, busy: bool, draft: string};
- **actions**:
  - open/close(local;anchor 即本控件路径,span 可选);
  - **send(exec: run,经 context cascade)**——提交时 runtime 自动走
    APP-MODEL §16 级联:本控件出 span/段落/全文,祖先出成员/草稿/会话;
    助手(评论技能,白名单收口:只读级联内容,回复建议;**不能直接改**)
    在气泡里回复;
  - apply_reply(local,把某条回复作为批注/修改建议提交给父组件——
    由父组件决定接不接受,气泡不越权);
- **细节**:气泡卡(锚点引用行 + 消息流 + 输入框),多条气泡并存于同一
  widget(计数徽标);未读标记;busy 骨架;
- **a11y**:role=log(消息区)、role=dialog(气泡卡,Esc 关闭、Enter 发送);
- **测试**:级联信封内容(span+全文+app 状态三级都在)/回复渲染/
  apply_reply 只发事件不直接改/多条并存/Esc/Enter。

## 3. 组合与装配规则

- **widget 组合只允许向下**:section 可以组合 widget,widget 可以组合 widget(W-form 内嵌 W-table);禁止反向(app 进 widget);
- **事件映射表在 section**:widget 的 change/commit → section 声明的 handler → app action(exec);控件不知道 app 存在;
- **数据流单向**:state 从 app → section → widget 下行;事件上行;widget 不拉取;
- **寻址自动**:section 渲染 widget 时把 §14 路径前缀传给控件,控件自己登记叶子路径(`.../section/tests/table/row/3/cell/input`)。

## 4. 主题与文案

- 全部样式只消费契约 token;每控件一个 css 区块(app.css 或平台 css);六主题走查项写进各控件测试;
- 文案全走 copy key(六主题):空态/按钮/错误提示;技术原文(JSON 错误消息)豁免直读;
- reduced-motion:所有动效(高亮脉冲/展开)在媒体查询下停用。

## 5. 测试库定义(每控件一套)

| 层 | 内容 |
|---|---|
| 协议 | state_schema 合法;actions 全 local(无 endpoint/run 断言);events 清单完整 |
| 渲染 | 四态/空态/错误态/禁用态截图级断言(类与属性) |
| 交互 | 状态机路径(输入→dirty→commit→事件);键盘;a11y 属性 |
| 边界 | XSS(md)/非法 JSON/超长文本/空数据 |
| 主题 | 六主题 copy key 覆盖;focus 环存在 |

## 6. 分期

| 期 | 内容 |
|---|---|
| W1 ✅ | Widget 协议与注册表 + W-text + W-json(编辑器是基础中的基础) |
| W2 ✅ | W-table + W-kv + **W-bubble(含 APP-MODEL §16 context cascade 落地)**:表格家族 + 锚点聊天气泡,首个级联消费者 |
| W3 ✅ | W-form + W-list + W-tree + W-date(数据输入与导航;run.launch 表单化落地;browse 时间窗) |
| W4 ✅ | W-diff + W-md + W-log + W-chart(呈现家族;agent 消息/调试面/usage 可视化的统一) |

> **W4 实现注**(2026-08-03,分支 debugger;控件库收尾):
> - **W-diff**(`w-diff.js`):`diffBodyHtml(diff, {mode})`——split 与 cards.js
>   原呈现**逐字节一致**(diffCard 已改委托,platform.test 未破);unified =
>   单列新旧堆叠 + same 行折叠上下文([+n] 展开钮);set_mode 切换;
> - **W-md**(`w-md.js`):`mdToHtml` 先整体转义再白名单加工(标题/列表/
>   代码块 mono/粗斜体/链接[仅 https? 与站内]/表格);`javascript:` 链接剥壳
>   成纯文本;`looksMarkdown` 保守判定;**装配 = agent 消息文本**(msgHtml:
>   含结构才走 md,普通文本保持 esc 原文);
> - **W-log**(`w-log.js`):跟随模式(新行自动滚底;上滚暂停 + "回到底部"
>   钮;回底自动恢复)、截断保尾部(上限 500)、kind 着色(信号色 token)、
>   过滤、copy_all(剪贴板缺席降级为事件);**装配 = run tab "原始信号"
>   折叠区**(trace 主视图不动,并列增强);
> - **W-chart**(`w-chart.js`):line/bar/spark 纯 SVG 直绘(零依赖);
>   网格 + 1/2/5×10^n 刻度自动;>500 等距抽稀保首尾;hover title 读值;
>   颜色只消费契约 token(var(--live)/var(--perm-write),不内嵌调色板);
>   **等价数据表硬规则**:chartTableHtml 同 series 表格 + 切换钮;
>   **装配 = usage 面板并列**(帧 cost 折线在原生 usage 表上方——面板本身
>   就是等价数据表,语义不变;注:旧面板本无私有 SVG 图表可提取,故走并列)。

> **W3 实现注**(2026-08-03,分支 debugger):
> - **W-form**(`w-form.js`):六类型生成(string/number/integer[min/max]/
>   boolean/enum/嵌套 object/数组项编辑器);required 星标;默认值复用
>   launch-dialog 的 skeletonFromSchema(逐字节一致有断言);
>   `validateValues` 轻量字段校验(required/type/min-max;硬校验在服务端);
>   **装配点 = run.launch**(web_platform run tab):schema 经
>   `GET /api/skills/{name}` 取得,已知时 textarea 升级为逐字段表单,
>   "高级:JSON"折叠保留 textarea;提交前 validate(不合先拦),
>   args.input 与 textarea 时代同构直传;schema 未知/ad-hoc 保持 textarea;
> - **W-list**(`w-list.js`):单/多选/过滤(平列表子串)/↑↓+Enter 键盘
>   路径/空态;**装配点 = lab 草稿下拉**(原生 select 隐藏为表单模型锚——
>   lab 的 change 委托与测试区域提取零改,W-list 驱动它;选 lab 不选
>   平台会话下拉的理由:平台 sessionSel 是紧凑原生下拉且测试断言面大,
>   草稿列表才是"可选列表"语义);
> - **W-tree**(`w-tree.js`):**ns-tree.js 零改**——薄协议封装(构建/过滤/
>   默认展开/渲染全委托 ns-tree.js 纯函数),控件层只补 state/actions/事件
>   上行;装配留 W4(技能树仍用原件,不重复造轮子);
> - **W-date**(`w-date.js`):date/datetime/range 三模;输入即时 ISO 校验;
>   range 倒置警示(点选自动纠序);自绘月历(←→ 翻页键盘可达);
>   quick 快捷项(今天/昨天/本周[周一起]/上周,本地时区);
>   **装配点 = browse 时间窗**(平台 legacy runs tab:range 过滤行内 run,
>   首屏行内渲染 + change 时 region 重渲)。

> **W2 实现注**(2026-08-03,分支 debugger):
> - **cascade**(`widgets/cascade.js`):`registerContextProvider`/`contextCascade`
>   ——provider 在自己的 prefix 级贡献 fragment(app fragment 在 app 路径),
>   祖先链判定(无横向),levels 级数裁剪,provider 异常缺席不炸;纯本地
>   (信封的出海永远由父组件完成,widgets 零 fetch 纪律不变);
> - **W-table**(`w-table.js`):四列型编辑器(text/number/boolean/enum)+
>   required 星标;增/删/移行(新增行骨架按列型);Alt+↑/↓ 键盘移行;
>   行 DnD 走 §15 envelope(source_kind="table-row",accept 校验,落空区 =
>   移到末尾);空态;change 事件上行(set_cell 不重渲保焦点);
> - **W-kv**(`w-kv.js`):重复 key 即时警示(警告态非硬拦);
>   entriesToObject/objectToEntries 往返(后者覆盖,与 JSON 语义一致);
> - **W-bubble**(`w-bubble.js`):锚点引用行 + 消息流(role=log)+ 输入框 +
>   busy 骨架(role=dialog,Esc 关 Enter 发);submit 事件携带
>   {anchor, text, cascade}(cascade 本地组装,`triggerPath` 独立字段不污染
>   锚);apply_reply 只发事件;`receiveReply` 由父级回填;多条并存各锚点独立;
> - **评论技能**(skills/lab_assistant.py `commenter_skill` +
>   `POST /api/lab/drafts/{name}/comment`):**tools=[] 白名单收口**——只读
>   级联内容、回复建议、不能直接改;503 与 iterate 同归类;
> - **装配点**(lab-iterate.js):💬 边注弹框换 W-bubble——既有边注作种子
>   消息(数据兼容),submit 父级 POST comment(cascade 三级随信),
>   apply → 边注挂左栏(锚键与单元 data-anchor 同构);
> - **装配留口**:lab 编辑器没有 tests/*.json 用例编辑区(用例只读,来自
>   scaffold/iterate),skills 也无现成键值编辑点——W-table/W-kv 本期 =
>   控件 + 测试库,真实装配归 W3(用例编辑面落地时一并接入)。

> **W1 实现注**(2026-08-03,分支 debugger):
> - **协议面**(`host/web/static/js/widgets/`;与 components/ 平级):
>   `registry.js`(WidgetDef 注册表,校验:结构齐/actions 全 local——
>   endpoint/run 拒注册/events 清单/aria.role 必填/surfaces ⊆{card,tab});
>   `widget.js`(实例工厂:{kind,state,emit,on,destroy};未声明事件不发;
>   注册/注销经 onRegister/onUnregister 回调——注册动作是宿主职责);
>   `index.js`(协议面导出);**目录零 fetch(** 静态扫描进测试(剥注释防自述);
> - **W-text**(`w-text.js`):挂宿主既有 textarea(不接管值所有权);
>   aria-label 从 data-field 推导(缺省且无 field 拒装);行数/字数微标;
>   input→dirty→commit(发事件)→revert(回 baseline,**选区保留**:
>   写回前后存取 selectionStart/End);Esc=blur;
> - **W-json**(`w-json.js`):即时 JSON 校验 + **行级错误定位**
>   (position→行号、SpiderMonkey 行号直读、新版 V8 无 position 时提取
>   "Unexpected token 'x'" 搜行、end 类兜底全文行数);format 一键美化
>   (幂等;不合法不美化;美化后 dispatch input 同步宿主表单模型);
>   `schemaErrorAt` 轻量 schema 校验(required/type,字段级→行级;
>   硬校验永远在服务端的闸门);失焦校验;errorSlot 复用 lab 的
>   data-json-hint 槽(宿主旧提示先写,行级信息收尾);
> - **装配点**(lab.js):description/prompt → W-text(mono 变体),
>   inputs/outputs → W-json;`_renderEditor` 后 `_mountEditorWidgets`,
>   textarea 本体与 data-field 委托模型不动(formToManifest/draftToForm
>   零改,lab.test.mjs 未破);样式入 app.css(.wd-*,全契约 token);
> - **copy**:w.text.count/w.json.format/w.json.errline ×6 主题。

每期交付:协议实现 + 该期控件 + 测试库 + 至少一个真实装配点
(W1 装进 lab 编辑器;W2 装进 tests 编辑与 **Flow C 迭代模式的边注气泡化**;W3 装进 run.launch 与 browse 时间窗;W4 装进对话流与 usage 面板)。

## 7. 不做

- 不做富文本/WYSIWYG(W-md 是查看器;编辑器是纯文本,代码编辑器属另一产品级组件);
- **不引图表/日历第三方库**(零 bundler 架构;W-chart 纯 SVG 直绘,W-date 自绘月历——体量受控,见各控件定义);
- 不做图表交互全家桶(缩放/刷选/导出 PNG;hover 读值与显隐已够本期);
- 不做时区选择(本地时区显示;跨时区协作是另一个问题);
- 不做拖拽造布局的 GUI 构建器(widget 是代码资产,组装在代码里);
- 不重写已有稳定组件(ns-tree/diff 只控件化提取,不改行为)。
