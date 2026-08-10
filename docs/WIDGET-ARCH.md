# Widget 基座重构计划(W5):渲染与逻辑分离

> 版本:v0.1(计划)
> 起因:W1-W4 的 widget 是**装饰器**(挂在宿主 DOM 上加行为)——渲染归宿主,
> 控件无法保证视觉质量;state 是 DOM 值的抄本,双真相;事件桥接靠宿主手写。
> 本计划把 widget 基座重构为**自渲染组件**,并把渲染与逻辑拆成两个文件。
> 原则:渲染纯函数化(state → 视图),主题只吃契约;逻辑不碰 DOM;
> 迁移逐控件进行,行为测试不许破。

---

## 1. 基座新形态(每个 widget 两个文件)

```
js/widgets/
├── widget.js              # 实例工厂(state/事件/寻址;不变)
├── registry.js            # WidgetDef 注册表(不变;加 render 面校验)
├── w-text.js              # ← 逻辑:状态机、行为、事件语义(不碰 DOM)
├── w-text.render.js       # ← 渲染:state → HTML 字符串/节点(不写逻辑)
├── w-json.js / w-json.render.js
└── ...
```

### 1.1 职责切分(铁律)

| 逻辑文件(`<kind>.js`) | 渲染文件(`<kind>.render.js`) |
|---|---|
| 状态机(输入→dirty→commit) | `render(state) -> html`(纯函数,无副作用) |
| 行为(commit/revert/validate) | 局部样式钩子(class 命名) |
| 事件发射(emit) | **零逻辑**:不允许出现条件分支之外的行为 |
| 不 import DOM API | 不写 state、不发事件 |

渲染文件**禁止**操作 state、禁止发事件、禁止调后端——它只回答"这个 state 长什么样"。
逻辑文件**禁止**拼 HTML——它只回答"发生了什么、state 怎么变"。

### 1.2 主题支持(渲染层的契约)

- 渲染产出只带**语义 class**(`wd-*`),视觉全部走契约 token;
- 控件级样式集中在 `css/widgets.css`(新,从 app.css 拆出),`[data-theme]` 覆盖
  只许出现在主题 css 里(组件零分支红线不变);
- 六主题验收:每控件 render 后过对比度/双编码检查(契约测试扩展);
- copy 仍六主题,渲染层只调 `copy(key)` 不内嵌文案。

### 1.3 生命周期

```
mount(host, {state, path}):
  1. 校验(注册表)
  2. render(state) → host.innerHTML(控件自己产出,宿主不预置元素)
  3. 事件绑定(委托在控件根)
update(patch):
  state 合并 → 重渲染(选区/焦点保留——diff 或局部更新;简单件全量重渲)
事件:emit → 父组件订阅 → app action(exec 三态,不变)
```

**选区/焦点保留**是自渲染的必答题(不是可选项):update 前后存取
selectionStart/End 与 document.activeElement,重渲后恢复(有测试)。

### 1.4 双形态(card / tab;W5.6)

每个 widget 两张面孔(APP-MODEL「每个 app 两张面孔」在控件层的落地;
registry 的 `surfaces ⊆ {card, tab}` 校验从 W1 就预留了)。**card = 小·摘要·
内嵌;tab = 大·完整·操作**——同一实例 state,两种渲染,这就是「两张面孔」
的实证。

设计纪律(定死):

1. **签名统一**:`render<Kind>(state, { surface = "tab" } = {})`;缺省 = tab,
   既有调用零改动;mount 函数同步加 `surface` 选项(缺省 tab)。
2. **card = 只读摘要 + 开放入口**:textarea/input/button/select 一律不进 card
   渲染;唯一交互是整卡可点(点击 / Enter / Space),逻辑面 emit `open`
   事件(def.events 必须声明;宿主委托经 `bindCardOpen`,只挂这一个);
   tab = 完整交互,既有行为不回退;update() 有无维持现状(state 合并 →
   按当前 surface 全量重渲)。
3. **card 不是 tab 的缩小版,是重新设计的摘要视图**:信息层级、截断省略号
   (.wd-card-line / .wd-card-excerpt)、计数徽标(.wd-badge);card 根元素带
   `data-surface="card"` 与紧凑类 `wd-card`(role=button + tabindex=0 +
   aria-label=copy("w.card.open"),整卡即按钮)。
4. **样式全进 widgets.css、全 token 零硬编码**;card 形态在六主题下都成立
   (语义 token + copy 六主题,组件零主题分支红线不变)。

逐控件 card 形态定义(tab 全部保持现状):

| 控件 | card 摘要内容 |
|---|---|
| W-text | 只读前 3 行预览(mono 保留行号槽)+ 行/字微标 + dirty 左边条 |
| W-json | 状态行(✓ 合法绿 / ✕ 第 N 行错误红;复用 check() 的 .wd-json-ok/.wd-errbar 钩子)+ 首行预览;无 format 钮 |
| W-table | 列头摘要(列名+类型徽标,≤3 列,溢出 +N)+ 行数徽标 + 前 2 行只读;无拖柄/✕/添加行 |
| W-kv | 键值对计数 + 前 3 条只读 + 重复 key 警示计数(有则黄,data-tone="warn") |
| W-form | 必填完成度(已填 x/y)+ 缺失必填名(≤2 个,溢出 +N;无必填时给字段计数);无控件 |
| W-list | 当前选中项(名称+元信息;多选 +N)+ 总数徽标;无过滤框 |
| W-tree | 当前路径(mono)+ 叶子计数徽标;无过滤框/树体 |
| W-date | 当前日期或区间(起 → 止)+ 快捷标签徽标(值精确命中 today/yesterday/week/lastweek 时);不开日历层 |
| W-chart | 迷你图(去坐标轴文字,保留折线/柱形本体与最新值点)+ 最新值读数 + 图例压成序列计数 |
| W-log | 总行数徽标 + 最近 3 行(kind 色条保留);无过滤框/回到底部 |
| W-diff | +add/−del 计数徽标(双编码:符号+data-kind 色)+ 首个 hunk 的 2 行预览(固定紧凑 unified);无模式切换 |
| W-md | 首个标题 + 首段摘录(2 行截断;跳过代码块/列表/表格);无代码块复制钮 |
| W-bubble | 消息计数 + 未读徽标(state.unread >0 才显,--live 调)+ 最后一条消息摘录;无输入框;open 负载沿用 {anchor} 语义 |

## 2. 逐控件详设(示意图见 docs/widgets/<kind>.svg)

> 每个控件:效果要求(所见)+ 交互要求(所为)+ 主题注意点。
> 示意图是线框(wireframe),标注结构而非最终视觉。

### 2.1 W-text 文本编辑器 [示意图](widgets/w-text.svg)

- **效果**:等宽/正文两变体;行号槽(mono 变体);右下微标(行数/字数);
  dirty 时左边条变色;readonly 灰底斜杠光标;
- **交互**:输入即 change;Ctrl+S=commit;Esc=blur;revert 保留选区;
  占位符;自动高度(可选 max-rows);
- **主题**:左边条用 --live;focus 环六主题可见。

### 2.2 W-json JSON 编辑器 [示意图](widgets/w-json.svg)

- **效果**:继承 W-text;底部错误条(行号 + 摘录,红边);合法时绿勾;
  右上角 format 钮;
- **交互**:失焦校验;错误条点击跳到错误行;format 幂等;
- **主题**:错误条 --danger 边条 + 浅底。

### 2.3 W-table 表格编辑器 [示意图](widgets/w-table.svg)

- **效果**:列头(名称+类型徽标+必填*);行 hover 浮起;行首拖柄(⠿);
  行尾删除钮(hover 显形);底部"添加行";
- **交互**:单元格点击即编(按列型出编辑器);Alt+↑/↓ 移行;DnD 走 §15 envelope;
- **主题**:拖柄 --fg-2,hover 行 --bg-2。

### 2.4 W-kv 键值编辑器 [示意图](widgets/w-kv.svg)

- **效果**:两列(key/value)+ 行尾删除;重复 key 行黄底警示;
- **交互**:Tab 在 key→value→下一行间流转;重复 key 不硬拦(警告即可);
- **主题**:警示 --warn 浅底。

### 2.5 W-form schema 表单 [示意图](widgets/w-form.svg)

- **效果**:字段分组(fieldset 卡片);label 左、控件右;required 星标;
  错误字段红边 + 行内错误语;嵌套 object 缩进分组;
- **交互**:输入即清该字段错;提交前全量校验;reset 回骨架;
- **主题**:错误 --danger,星标 --danger。

### 2.6 W-list 可选列表 [示意图](widgets/w-list.svg)

- **效果**:顶部搜索框;条目行(图标+主标+副标);选中行左色条;
  hover 浮起;空态插画位;
- **交互**:↑↓ 移动焦点、Enter 激活、Esc 清搜索;多选 checkbox 变体;
- **主题**:选中 --live 左条。

### 2.7 W-tree 命名空间树 [示意图](widgets/w-tree.svg)

- **效果**:缩进层级 + 折叠箭头 + 计数徽标;叶子图标;当前项腮红包围;
- **交互**:箭头折叠/展开(动画 150ms);搜索过滤命中祖先链展开;
- **主题**:当前项 --moe-blush 类装饰由主题定(classic 用 --bg-2)。

### 2.8 W-date 日期控件 [示意图](widgets/w-date.svg)

- **效果**:输入框 + 日历弹层(月网格、今天圆点、选中区间高亮带);
  快捷项左列(今天/昨天/本周/上周);
- **交互**:输入 ISO 即时校验;←→ 翻月;range 点选自动纠序;Esc 收层;
- **主题**:选中带 --live 浅底;今天点 --warn。

### 2.9 W-chart 图表 [示意图](widgets/w-chart.svg)

- **效果**:SVG 折线/柱/spark;网格 + 刻度;hover 读值点;图例(可点显隐);
  右上"表格视图"切换钮;空态"还没有数据";
- **交互**:hover 读值;图例显隐;表格视图切换;
- **主题**:数据线用 --live/--ok/--warn 序列;grid --line。

### 2.10 W-log 日志查看器 [示意图](widgets/w-log.svg)

- **效果**:mono 滚动区;kind 左侧色条;底部"回到底部"浮钮(暂停时显);
  顶部过滤框;
- **交互**:跟随/暂停自动切换;复制全部;过滤即时;
- **主题**:kind 色条走信号色(--sig-*)。

### 2.11 W-diff 差异查看器 [示意图](widgets/w-diff.svg)

- **效果**:split 双列(旧红新绿)/ unified 单列(+绿 -红);
  折叠上下文 [+n] 钮;成员头(tier 徽标);
- **交互**:模式切换;折叠展开;
- **主题**:add=--ok 浅底、del=--danger 浅底(双编码:+/- 符号必在)。

### 2.12 W-md Markdown 查看器 [示意图](widgets/w-md.svg)

- **效果**:标题层级、代码块卡片(mono+复制钮)、表格、引用条;
- **交互**:代码块一键复制;链接新窗口;
- **主题**:代码块 --bg-1 卡片;引用 --line-strong 左边条。

### 2.13 W-bubble 聊天气泡 [示意图](widgets/w-bubble.svg)

- **效果**:浮出卡(锚点引用行+消息流+输入框);小箭头指锚点;
  收起态 = 段落旁小圆标(带未读数);
- **交互**:Esc 收起/Enter 发送;多条并存;点击标记重开;
- **主题**:气泡卡 --bg-1 浮层影;未读数 --live 圆点。

## 3. 迁移计划(W5 分期)

| 期 | 内容 | 验收 |
|---|---|---|
| W5.1 ✅ | 基座:render/logic 分文件模式 + `css/widgets.css` 拆出 + 更新协议校验(render 面)+ W-text/W-json 先迁(lab 编辑器行为测试不破) | 两控件自渲染;选区保留测试过 |
| W5.2 ✅ | W-table/W-kv/W-form/W-list 迁移(装配点:run.launch 表单/lab 下拉不破) | 装配点测试过 |
| W5.3 ✅ | W-tree/W-date/W-chart/W-log/W-diff/W-md/W-bubble 迁移 | 全部 render 纯函数 |
| W5.4 ✅ | 清扫:宿主手写桥接代码删除;架构测试记录更新(弯腰点 ①⑤ 关闭情况) | 无装饰器残留 |
| W5.5 ✅ | 控件沙盒(widget.html 调试页 + 样例表 + URL 协议) | 沙盒测试过 |
| W5.6 ✅ | 渲染层双形态:13 控件 render(state, {surface}) + card 摘要视图 + mount surface 选项 + open 事件 + 沙盒形态切换 | 双形态测试全绿(两 surface × 全样例 + card 断言) |
| W6.0 ✅ | 视觉基建(docs/WIDGET-DESIGN.md §1):--log-bg 契约 token(六主题)+ widgets.css 共享层(chip/badge/skeleton/empty/btn/动效工具类) | themes-contract + 结构断言过 |
| W6.1 ✅ | W-text/W-json 按设计终稿重做(§3.1/§3.2):编辑器结构 + JSON 着色层 + 错误三件套 + 失焦校验 | 行为测试全绿 + 视觉结构断言过 |
| W6.2 ✅ | W-table/W-kv/W-form 按设计终稿重做(§3.3-3.5):内联编辑/⚠ tooltip/switch/chips/stepper/操作行 | 行为测试全绿 + 视觉结构断言过 |
| W6.3 ✅ | W-list/W-tree/W-date 按设计终稿重做(§3.6-3.8):命中高亮/调光过滤/双月弹层/纠序闪提示 | 行为测试全绿 + 视觉结构断言过 |
| W6.4 ✅ | W-chart/W-log/W-diff/W-md/W-bubble 按设计终稿重做(§3.9-3.13):tooltip/深色面板/双编码行/排印阶梯/气泡卡 | 行为测试全绿 + 视觉结构断言过 |
| W6.5 ✅ | 视觉验收报告发现项(F1-F7)+ 流程两项(构建号/文案契约) | 对比度实测断言 + 结构断言全绿 |
| W6.6 ✅ | 六条设计裁决(用户验收反馈 v2):json card/表格简化/表单单列/树紧凑/日期可用性/md view source | 裁决断言全绿;DESIGN v2 + 效果图重生成 |
| W6.7 ✅ | 宿主重新组装:既有 UI 主动「用新」(lab/usage/doc-editor/cards/launch/browse) | 29 前端全绿 + pytest 961 过 + 三页 200 |
| C1 ✅ | Compound Widget 基座(docs/COMPOUND-WIDGET.md):createCompound 全协议 + registry 增量 + def.mount 面 | compound.test.mjs 协议七块全绿 |
| C2 ✅ | Compound playground(compound.html 新页,createCompound 自实现) | 冒烟断言 + curl 200 |
| C3 ✅ | doc-editor 迁为 compound(首个产品级;md-viewer 预定义 slot + chat-bubble 动态子件;基座两修补见 COMPOUND-WIDGET §12) | stub 30 全绿 + tests-ui 三套全绿 |
| C4.1 ✅ | desktop 根落地首步(docs/DESKTOP-WIDGET.md):badge 协议补丁 + desktop def + /platform/desktop.html 与旧壳并存 | stub 31 全绿 + tests-ui 四套全绿(114 项) |
| C4.2 ✅ | conversation 薄壳 + doc-editor 直进(hard link 活卡)+ inbox 真实化(docs/DESKTOP-WIDGET.md §10) | stub 32 全绿 + tests-ui 四套全绿(118 项) |

> **W5.1 实现注**(2026-08-04,分支 debugger):
> - **基座**:`registry.js` 加 render 面校验(声明了 render 必须是函数);
>   `widget.js` 加 `preserveSelection(host, fn, {selector})`(update 全量重渲
>   前后存取 selectionStart/End + activeElement,重渲后恢复——选区保留测试
>   在 widgets.test.mjs 的 W-text revert 块)。
> - **两控件**:`w-text.render.js`/`w-json.render.js` = 纯函数
>   `render(state)→html`(同 state 同 html/不改 state/XSS 转义,有单测);
>   `w-text.js`/`w-json.js` 只留状态机/行为/事件,不拼 HTML;W-json 复用
>   W-text 的 `_mountText`。效果面落地:mono 行号槽/右下微标/dirty 左边条/
>   readonly 灰底/错误条(行级)/合法绿勾/format 钮/错误条点击跳行。
> - **岛屿模式**:editorHtml 内联的就是控件 render 的首渲产出(宿主不手写
>   控件 DOM,只调它的 render 函数),mount 幂等重渲 + 绑行为——lab 的
>   data-field 委托模型与装配点行为测试零改动。
> - **子元素监听纪律**:重渲会换元素,监听一律委托在 host(format/错误条
>   点击),不直接挂子元素。
> - **CSS**:`css/widgets.css` 从 app.css 拆出(137 行 wd-* 全量迁移 +
>   W5.1 新增 .wd-text/.wd-gutter/.is-dirty/.is-readonly/.wd-json-ok),
>   全 token 零硬编码色值;两个 index.html 各加一行 link。

每控件迁移 = 逻辑文件(已有行为剥离)+ render 文件(新)+ 宿主装配点
从"挂既有元素"改一行 mount——既有测试全绿是硬验收。

> **W5.2 实现注**(2026-08-04,分支 debugger):
> - **四控件迁移**:`w-table/w-kv/w-form/w-list` 各拆出
>   `*.render.js`(`render<Kind>(state)→html` 纯函数;同 state 同 html /
>   不改 state / XSS 转义,widgets.test.mjs 有单测),逻辑文件只留状态机/
>   行为/事件,不拼 HTML;def 均挂 `render:`(registry 校验面生效)。
> - **样板沿用 W5.1**:监听一律委托在 host;`preserveSelection` 用于
>   W-list 过滤框与键盘导航的重渲(单 input,selector 定位);focus 从闭包
>   变量收进 state(`state.focus`,渲染才可纯);`dupKeys` 移入 render 文件
>   (w-kv.js re-export 兼容);`visibleItems` 提炼为渲染面纯函数(逻辑共用)。
> - **§2.3-2.6 视觉补齐**(全 token,进 css/widgets.css):W-table 行首拖柄
>   ⠿ + 列头类型徽标(.wd-drag/.wd-type);W-kv 重复 key 行黄底
>   (.wd-kv-warn,--warn color-mix);W-form 错误字段红边(.wd-field-err,
>   --danger);W-list 选中行左色条(aria-selected + inset --live)。
> - **装配点**:run.launch 表单(mountFormEditor)与 lab 下拉
>   (mountSelectList)签名零改动;行为测试(launch-dialog/lab/platform)
>   未动一字,全绿。
> - **排障**:测试文件分批 import 有 TDZ——render 纯函数断言按 import 批次
>   分区放置(W2 区断 table/kv,W3 区断 form/list)。
>
> **W5.3 实现注**(2026-08-04,分支 debugger):
> - **七控件迁移**:`w-tree/w-date/w-chart/w-log/w-diff/w-md/w-bubble` 各拆出
>   `*.render.js`(纯函数;同 state 同 html/不改 state/转义,单测在
>   widgets.test.mjs 末段),逻辑文件不拼 HTML;def 均挂 `render:`。
>   共享纯函数(mdToHtml/looksMarkdown/diffBodyHtml/chartSvg/chartTableHtml/
>   downsample/niceTicks/monthGridHtml/visibleLogLines)迁入渲染面,逻辑文件
>   原样 re-export——消费方(cards.js/doc-editor.js/app.js/各测试)零改动。
> - **§2.7-2.13 硬规则落地**:W-tree 过滤命中自动展开祖先链(render 面:
>   过滤态取 `allNamespaces(过滤树)`)+ 当前项 `data-current` 浅底(缺省
>   leaf 行;CSS `.wd-tree .ns-row[data-current]`);W-date 日历弹层化
>   (`state.open`,Esc 收层/点输入区重开;cursor 翻月游标收进 state);
>   W-log kind 左侧色条(--sig-* 信号色);W-diff 双编码浅底(`.wd-diff`
>   作用域内 add=--ok/del=--danger;platform.css 卡面不动);W-md 代码块
>   复制钮(`data-md-copy` 序号 → 逻辑面按序取块文本,clipboard + copy
>   事件;copy 新键 `w.md.copy` 六主题);W-bubble 抽出 renderBubble(浮出/
>   箭头/收起标记在 doc-editor 装配层,**装配行为未动**——切换留 W5.4)。
> - **state 纪律**:cursor(date)/expanded(diff: Set→数组)/focus(list,
>   W5.2)等游标全收进 state(可序列化),渲染才能纯;label(chart)这类
>   挂载期常量经 opts 传入,不进 state。
> - **排障**:ns-tree 的"单层链折叠 + 一级默认展开"会让深名单链 namespace
>   变一级——自动展开测试须用"分叉 + ≥阈值"结构才能造出默认折叠态。
>
> **W5.4 实现注**(2026-08-04,分支 debugger;清扫):
> - **装饰器残留排查(验收:无残留)**:全部 mount 调用点逐一看过——
>   lab.js(4 个编辑器挂点 + W-list 下拉)、usage-panel.js(W-chart)、
>   lab-iterate.js(W-bubble)、platform/app.js(W-form/W-log/W-date)、
>   doc-editor.js(W-bubble + W-md 预览)——均已是一行 mount + 空挂点
>   (或岛屿模式首渲,见 W5.1 注),无宿主手写控件 DOM;launch-dialog.js
>   无 widget 桥接。死代码:无(D5 已删 doc-editor 的 W-text shim;W-json
>   的旧 hint 槽复用代码随 W5.1 重写退役)。
> - **两条 grep 复核**(2026-08-04 实测):
>   1. `widgets/*.js` 逻辑文件 innerHTML 只允许两种形态——
>      `host.innerHTML = render<Kind>(...)`(单次产出赋值)与 destroy 的
>      清空;字面 HTML 标签扫描(`<div/<span/<button/<input`)零命中;
>   2. `css/widgets.css` 硬编码色值扫描(`#xxx`/`#xxxxxx`)零命中
>      (全契约 token;`font-size: 8px` 等尺寸值不在色值面)。
> - **W-bubble 装配切割线(评估结论:不切,关闭此项)**:控件
>   (w-bubble)负责气泡卡本体——锚点引用行/消息流/输入框/busy 骨架/
>   submit/apply/open/close 事件语义;宿主(doc-editor)保留——浮出定位壳
>   (.doc-bubble-pop + 箭头,锚在段落块上,是宿主布局)、✕/marker 显隐
>   交互(未读数来自 doc-editor 的 seen 游标,与批注列表同源,是应用态)、
>   预览重渲后的挂回(innerHTML 重渲的 DOM 稳定性不归控件假设——D2 弯腰点
>   ②的既有裁决:挂回是 mount 方职责)。**不切的理由**:切进去需要控件
>   认识 seen 游标/未读数/段落锚三个 doc-editor 概念,通用控件被应用语义
>   污染;交互行为零变化优先。
> - **弯腰点 ①⑤ 关闭情况**(出处:docs/DOC-EDITOR.md D1 架构测试记录):
>   - **①(W-text aria-label vs 字符串骨架)** → **已关闭(W5.1)**:
>     新形态 mount 接受显式 `label`/`field` 选项(host dataset 推导),
>     aria-label 由控件 render 自产出;doc-editor 的 host shim 在 D5 两栏
>     重构时已删(无 textarea 面),D1 建议的"显式选项优先于 DOM 读取"
>     落地。
>   - **⑤(doc 的入口从哪开)** → **已关闭(D4)**:doc_list 对话卡 +
>     卡上"新建文档"入口(data-doc-create,唯一名起稿)落地,正是 D1
>     建议的"对话卡接入"路径;与 W5 无关但记录关闭。
>   - 另:D2 弯腰点②(innerHTML 重渲 vs 气泡宿主)的处置 = 宿主挂回
>     (沿用,见上切割线);D2 弯腰点①(bubble anchor 对象 vs 字符串)
>     维持"留特例"裁决不变。
>
> **W5.6 实现注**(2026-08-04,分支 debugger;双形态):
> - **签名**:`render<Kind>(state, { surface = "tab" } = {})` 全 13 控件统一
>   (renderChart 的 opts 第二参是先例;renderTreeWidget 的 opts 加 surface
>   字段);缺省 = tab,lab.js 岛屿首渲等既有调用零改动。mount 函数同步加
>   `surface` 选项;card 形态下宿主委托只挂 open(`widget.js` 新增
>   `bindCardOpen(host, widget, payload?)`:点击/Enter/Space → emit "open",
>   缺省负载 {path};W-bubble 显式给 {anchor} 沿用自身 open 语义)。
> - **def 面**:12 控件 events 补 "open"(chat-bubble 本有);W-bubble
>   state_defaults 补 `unread: 0`(card 未读徽标的数据面)。
> - **card 摘要视图**(重设计,非缩小版):共享件 = `.wd-card` 基座(整卡
>   role=button)/`.wd-card-line`(单行截断)/`.wd-card-excerpt`(2 行截断)/
>   `.wd-badge`(计数徽标,warn/live 双 tone + diff 的 add/del 双编码)——
>   全进 widgets.css、全契约 token;六主题 copy 新增 13 个 `w.card.*` /
>   `w.json.ok` 键(themes-contract 全覆盖断言盯)。
> - **复用不重复**:W-json card 的状态行复用逻辑面 check() 的
>   .wd-json-ok/.wd-errbar 钩子(初值校验局部刷新,不重渲);W-text card 的
>   微标/dirty 边条复用 syncDirty 钩子;W-log card 复用 .wd-log-line
>   (kind 色条 CSS 面);W-diff card 复用 .pf-dline(.wd-diff 作用域浅底);
>   W-date card 快捷命中检测复用 quickRange(render→logic 循环 import,
>   提升的函数声明,渲染期调用,安全)。
> - **沙盒**:顶栏形态切换(完整/卡片),`&surface=card` 进 URL 与分享链接
>   (tab 缺省省略);card 形态舞台收窄至 340px(`.sb-stage[data-surface]`);
>   样例表不动(形态与样例正交,mount 时 `{...options, surface}` 合入)。
> - **测试**:widgets.test.mjs 末段 W5.6 区(协议面双 surface + open 声明;
>   16 组 card case:纯函数三要素/零编辑控件/摘要关键内容/转义/缺省=tab;
>   mount 面 update 在 card 工作、json check() 钩子两形态共用、bubble open
>   负载语义);widget-sandbox.test.mjs 逐样例 × 逐声明 surface 挂载矩阵 +
>   surface URL 解析/序列化 + widget.html smoke 两项。
>
> **W6.0 实现注**(2026-08-04,分支 debugger;视觉基建,基准 docs/WIDGET-DESIGN.md §1):
> - **tokens**:`--log-bg` 进 tokens.css(效果图值 #0c1018)+ 六主题 css
>   压暗映射(classic 同步同值,moe/ink 亮主题给深色面板值)+
>   CONTRACT_TOKENS 收录(themes-contract 逐主题盯);阴影三级沿用既有
>   --shadow-sm/pop/md(设计文档已注映射,不动);reduced-motion 全局降级
>   沿用 tokens.css 既有基线(不重复 media query)。
> - **共享层**(widgets.css 首部):.wd-chip/.wd-badge(四 tone:ok/warn/
>   danger/live;W5.6 的 badge 定义并入)/.wd-focus-ring(组装策略常量)/
>   .wd-skeleton(wd-shimmer 1200ms)/.wd-empty-box(图标位+引导+主操作槽)/
>   .wd-btn-primary/.wd-btn-ghost/.wd-a-*(120–160ms ease-out 工具类)——
>   全契约 token;「widgets.css 零硬编码色值」进 widgets.test 静态断言。
>
> **W6.1 实现注**(2026-08-04;W-text/W-json 按 §3.1/§3.2 终稿重做):
> - **W-text 结构**:头部(field · lang + readonly 🔒)+ .wd-editor(行号槽
>   逐行 .wd-gl + .wd-code > textarea + 微标胶囊);行高 20px(=--text-sm×1.6)
>   三面同算式;mono 变体 wrap=off(行号/着色对齐前提);当前行 = .wd-curline
>   浮槽(syncCursor 写 top)+ 行号转正色;微标胶囊 = dirty 圆点 + 计数 +
>   hover 行列 tip(syncCursor 写,全部局部 DOM 写不重渲);焦点环:has
>   (textarea:focus-visible) 上移到编辑区容器(textarea 自身环让位,无
>   :has 浏览器回落默认环);dirty = 编辑区左缘 inset 2px --live。
> - **W-text card**:标题行(图标位 + 名称 500 + dirty 圆点 + 「打开 →」
>   hover 渐显)+ 前三行预览(末行 .wd-fade 0.35)+ meta 行(行数·字数·
>   relTime(updated_at));卡 hover 抬升 --shadow-pop(§1.2:hover 不改边框,
>   W5.6 的 border-color hover 随之退役)。
> - **W-json**:语法着色 = pre.wd-hl 叠层 + 透明文字 textarea(caret-color
>   --live/选区 18%/placeholder 保持;滚动 capture 委托同步);着色器
>   jsonHighlightHtml 逐字符扫描(tolerant,未闭合串不炸;token 四色走契约);
>   matchBrace 字符串掩码跳过串内括号,<mark class="wd-brace"> 浅底;
>   错误三件套 = 行号槽红点(.wd-gl.is-err::before)+ 整行红波浪
>   (.wd-hl-line.is-err wavy underline)+ 底部错误条(✕ + 行级文案 +
>   「点击跳转 →」,点击跳行并闪行 1.5s wd-flash);✓ 绿徽标带键数
>   (jsonKeyCount 递归);format ghost 40%/hover 显形,非法 disabled + title
>   说明;**失焦才校验**(§3.2 设计批准的行为变更:输入只刷着色层不闪红,
>   widgets.test.mjs 即时校验块改写,其余行为面零改动);结构槽位化
>   (textEditorTabHtml slots:overlay/headSide/foot),render 仍纯、lab.js
>   岛屿首渲零改动;card = 状态胶囊/错误行 + 首行预览 + meta(UTF-8 大小 ·
>   键数 · 相对时间),错误态 .is-err 左边条 --danger。
> - **copy**:六主题新增 w.card.go/w.text.ph/w.text.pos/w.json.okn/w.json.keys/
>   w.json.jump/w.json.fmt_dis/w.time.now/min/hour/day;w.json.ok 退役(被
>   okn 取代)。
>
> **W6.2 实现注**(2026-08-04;W-table/W-kv/W-form 按 §3.3-3.5 终稿重做):
> - **W-table**:面板头(title · N 行 × M 列,mount 新增可选 title)+
>   sticky 列头(类型语义图标 Aa/#/≡/📅/☑ + hover 显排序/⋯ 槽,纯视觉
>   aria-hidden);行 40px,hover=--bg-2、选中=inset 2px --live + 8% 浅底
>   (只改底色);拖柄 30%↔hover 全显;**单元格点击进内联编辑**
>   (state.editing="id:key";展示态 = 文本/enum chip,编辑态 = 32px input
>   或 select;boolean 始终 checkbox;focusout 离表才退出,Enter/Esc 同效;
>   值仍走 set_cell 即时同步);行尾 ✕ hover 显;DnD 加 wd-dragging 浮起 +
>   wd-drop-before 2px 指示线(envelope/accept 零改动);空态 = 表头 +
>   骨架行 + 「添加第一行」主操作(w.table.empty 退役,w.table.addfirst/
>   w.table.count 新增);card = 标题行 + 迷你列头网格(--cols 变量)≤3 列
>   溢出 +N + 前 2 行 + 「查看全部 →」。
> - **W-kv**:KEY/VALUE 列头(11px 600);输入框平时隐形、聚焦显形;重复
>   key = 双行 warn 8% 浅底 + ⚠(data-tip + CSS ::after tooltip,零 JS);
>   **末行 value 回车自动加行**(新行为,纯增量);空态 ∷ 图标 + 引导 +
>   ghost 主操作;card 加 ⚠ 警示徽标与「共 N 条 · M 处重复」meta(与 tab
>   同 dupKeys 同源)。
> - **W-form**:label 上置 12px/500 + spec.description 帮助文字;**boolean
>   改 switch**(role=switch,点击翻转 + aria 局部刷新;设计批准的行为变更,
>   测试断言改写)、**enum 改 chips 单选组**(radiogroup,同上)、number
>   加 **stepper**(钳 min/max,局部写回);嵌套 object 卡片化(标题+描述);
>   数组项小卡 + 拖序(§15 envelope,source_kind=form-arr-item);**操作行
>   落地**:dirty 跟踪(values ↔ 骨架深比较)+ reset ghost(dirty 才可用,
>   局部刷新)+「有未保存改动」圆点 + submit 主操作(validate 通过 → emit
>   submit(事件早声明、此前无 UI 触发面),不通过 → 滚动聚焦首个错误);
>   card = 进度条(4px --live)+ 必填 x/y + 缺失 chips(≤2 溢出 +N)+
>   schema meta;空 schema 走 .wd-empty-box(主操作不落,widget 不出海)。
> - **CSS**:widgets.css 加 W6.2 节(.wd-pane-head/.wd-add/.wd-row-x 三件
>   三控件共用 + 各控件专属),全 token;零硬编码色值断言持续绿。
> - **copy**:六主题新增 w.table.count/addfirst、w.kv.count/total/dupn/empty/
>   addfirst、w.card.viewall、w.form.save/reset/dirty/meta/empty;改值
>   w.kv.dup/w.card.dup;退役 w.table.empty。
> - **samples**:table/kv/form 典型样例补 title 与 description(演示面板头
>   与帮助文字);其余不动。
>
> **W6.3 实现注**(2026-08-04;W-list/W-tree/W-date 按 §3.6-3.8 终稿重做):
> - **W-list**:搜索框容器(🔍 + input + 「Esc 清空」提示,环在容器
>   focus-within);命中子串 `<mark class="wd-hit">`(--live 文字色,先转义
>   再包 mark);选中 = 2px 左条 + 8% 浅底 + 右侧 ✓;键焦 data-focus 与
>   hover 同 --bg-2;Esc 清空过滤(新增);过滤空态 =「没有匹配『xx』的项」
>   + 清除搜索钮;多选浮条「已选 N · 清除」(sticky 胶囊 + --shadow-pop,
>   清除为新行为);card = 当前选中 + meta(hint · 共 N 项)+ 更换 →。
> - **W-tree**:**过滤语义变更(设计批准)= 剪枝 → 调光**(命中高亮 +
>   祖先链展开 + 非命中 50% 透明;共享 filterNsTree 不动,控件渲染面不再
>   用它裁剪);ns-tree.js 只加可选 `nsAttrs` 行属性钩子(共享组件零行为
>   变化,data-chain/data-dim/data-focus 经此上行);父链名称转正色;键盘
>   ←→ 折叠展开 / ↑↓ 移动 / Enter 选中(state.focusKey + _visibleRows 与
>   渲染同 walk 序);深层横向滚动(overflow-x + nowrap);card = 面包屑
>   (段 / 分隔,末段转正色)+ N 叶子 + M 命名空间徽标 + 打开 →。
> - **W-date**:📅 字段框(环在容器)+ 手输非法红边 + 行内 ⚠ 提示
>   (state.invalid,值不丢);快捷 chips 选中态(_quickHit 与 card 徽标
>   同源);**range 倒置输入/点选同律自动纠序 + 1.5s 闪提示**(设计批准
>   的行为变更,w.date.inverted 退役,platform.test 断言随改);弹层
>   --shadow-md + wd-pop 140ms + 下溢上翻(_flipLayer);**range 双月并排**
>   (cursor 月 + 次月,真实历算;星期头;邻月日 35% 可点;is-range 连续
>   色带整格贯通;端点实底 + 今天端点叠底点双编码);**点外收层**
>   (document 委托,destroy 摘除);日格方向键走格(越界自动翻月),
>   Enter = 原生钮选定;card 加 meta(mode · 共 N 天)。
> - **copy**:六主题新增 16 键(w.list.esc/nomatch/clear/selcount/clearall/
>   current/total/change、w.tree.current/ns、w.date.hint/invalid/fix/days/
>   weekdays/monthtitle);退役 w.date.inverted。
> - **samples**:list/tree/date 典型样例补 title;深层树样例改演示调光过滤。
>
> **W6.4 实现注**(2026-08-04;W-chart/W-log/W-diff/W-md/W-bubble,§3.9-3.13):
> - **W-chart**:头部 = 标题 500 + 图例(色点 + 名,隐藏 40%)+ segmented
>   「图表 | 表格」(.wd-seg 共享件)+ 抽稀留痕提示(state.trimmed,mount/
>   set_series 比较原始长度);SVG 重画:水平虚线网格(≤5 条,无垂直线无轴)、
>   折线 2px 无端点圆点、首序列面积渐变(固定 id 渐变定义,幂等)、序列色
>   板 --live/--sig-*;hover tooltip 槽(chartTipHtml 纯函数,mousemove 按
>   最近 x 档位锚定,贴点不贴鼠;stub 不可测,守卫降级);隐藏全部序列 →
>   空态;card = 大读数(20px 600)+ 涨跌徽标(▲ok/▼danger 带百分比)+
>   120×40 迷你折线(末端亮点)+ meta(近 N 点 · 均值)。
> - **W-log**:深色面板 --log-bg + **新契约 token --log-fg**(亮主题的主 fg
>   是深色,压暗面板上须亮色;六主题值实测 ≥4.5);工具行 = 过滤框 + 级别
>   chips(all + 现有 kind)+ 复制 + ⏸ 暂停跟随;**过滤 = 调光不剪枝**
>   (wd-dim 40%,设计批准的行为变更);截断留痕(state.truncated → 顶部
>   弱提示);暂停期新行计数(state.pausedNew → 浮囊「已暂停 · N 行新日志
>   ↓」);长行横滚不 wrap;moe/ink 的 kind 色字在主题 css 提亮映射
>   ([data-theme] 覆盖的合法位,实测 5.5–8.9);card 加 err ×N 徽标与
>   「跟随中 · 上限 N 行」meta。
> - **W-diff**:文件头(路径 mono + 绿 +N / 红 −N 徽标 + segmented 分屏|
>   统一);add/del 行 8% 浅底 + 2px 左条(双编码);折叠上下文「[+] 展开
>   N 行」整宽可点(copy 键,旧硬编码退役);成员 tier 徽标(●reversible/
>   ▲escalate);无变更空态;行号列/@@ hunk 头未落(diff 对象无行号元数据,
>   不伪造,卡片 hunk 数按非 same 连续段近似);diffCard 逐字节同构保持。
> - **W-md**:排印阶梯(h4=20/600+hairline、h5=16、h6=14);引用块 > 新增;
>   **剥壳语义变更(设计批准):非法链接整块不渲染**(mdToHtml 白名单行为
>   变化,断言随改);代码块 --log-bg 深底 + ⧉ hover 显 + 复制后 1s ✓;
>   line-break: strict 避头尾;空态 ¶;card 加 meta(Markdown · 大小 ·
>   相对时间)。
> - **W-bubble**:切割线不动(浮出壳/箭头/段旁圆标仍在 doc-editor/platform
>   .css);卡本体升级:头部(批注 · 锚点 + ✕ 收起)+ 锚点引用块(3px live
>   条 + 2 行截断 + L# 徽标自 anchor.path 提取;anchor.quote 可选)+ 消息流
>   (头像圆 + 名称 500 + relTime + 内容,不分左右)+ typing 三点(替代
>   pf-skel,断言随改)+ 失败行内红条 + 重试(notifyError + lastText 重发
>   不重复追加)+ 发送钮空输入禁用(局部刷新);card 未读实心徽标。
> - **copy**:六主题新增 19 键(w.chart.trimmed/latest/meta、w.log.truncated/
>   pause/follow2/paused/emptyhint/errn/following/capped、w.diff.fold/ctx/
>   split/unified/nochange、w.md.empty、w.bubble.you/comment/fail/retry/close)。
>
> **W6.5 实现注**(2026-08-05,分支 debugger;视觉验收报告发现项 F1-F7 + 流程两项):
> - **F1(P0)**:W-md 代码块文字改 `--log-fg`(与 --log-bg 配对)——浅色
>   主题(moe/ink)原实测 1.14:1,修后六主题 7.81–12.85;W-json 着色层
>   同步复查:编辑区底是 --bg-1 非深底,key/str/num/标点四色六主题
>   5.35–13.27 全过(fg-2 3.39–4.31 弱色档 ≥3);themes-contract 新增
>   「深面板配对」断言(--log-fg vs --log-bg ≥4.5,逐主题)。
> - **F2(P1)**:tier 徽标配齐三类并补 split 路径——●reversible(--ok)/
>   ▲escalate(--warn)/■irreversible(--danger),split/unified 成员行
>   同接(W6.4 只接了 unified);无 tier 不渲染(有断言)。
> - **F3(P2)**:classic `w.json.format` =「格式化」(逐字 spec);moe
>   「美美化」等主题语气不动(特性,见 DESIGN §5)。
> - **F4(P2)**:W-form 帮助文字/错误行收进字段根元素——原 text 分支
>   help/errbar 是 grid 直接子项(落右侧格),现每字段单一 grid 项,
>   help 贴控件下、错误行在其下。
> - **F5(P2)**:W-tree 叶子显示短名(末段),全名留 `title` tooltip 与
>   `data-wt-leaf` 寻址;单层链折叠行不受影响。
> - **F6(P2)**:`.wd-md a` 常显下划线。
> - **F7(P2)**:W-md card 摘录剥壳——`>` 前缀、行内符号、链接取纯文本。
> - **流程**:widget.html 全部 /static 链接带 `?v=<build>`(缓存破坏),
>   页角构建号元素 + `widget-sandbox.js` 的 `BUILD` 常量(单一来源,
>   手动同步,测试盯三方一致);WIDGET-DESIGN.md 新增 §5「主题文案契约」
>   (功能文案随主题变是特性:classic 逐字对 spec,其它主题只验语义)。
>
> **W6.6 实现注**(2026-08-05,分支 debugger;六条设计裁决 = 用户验收反馈,
>   优先级高于效果图/规格书原文;DESIGN 以「变更记录」v2 同步,未静默改条文):
> - **① W-json card 重设计**:状态行(✓/✕ 同前)+ **顶层键名 chips**
>   (前 4 溢出 +N,mono,_topKeys 纯函数)+ meta(N 键 · 大小 · 相对时间);
>   **首行原文预览撤掉**(「{」没有信息量);错误态左边条 danger 保留。
> - **② W-table 简化**:单元格 = **KV 式常驻可编辑**(隐形 input,focus
>   才显;enum = 隐形样式原生 select,boolean = checkbox),**编辑态切换
>   全废**(state.editing/点击进编辑/focusout 退出全部移除);**Excel 键盘
>   逻辑**(Tab/Shift+Tab 横向、Enter 下移、←/→ 光标位于端点时跨格,
>   checkbox/select 无光标直跨);**行 DnD 与 envelope 一并退役**(拖柄
>   列/draggable/dragstart·over·leave·drop·end 监听/_DND_MIME 全删,
>   Alt+↑/↓ 键盘移行保留);列头撤排序/⋯ 槽,sticky 保持;✕ hover 显与
>   虚线添加行保留。API 面(add_row/remove_row/move_row/set_cell/
>   serialize)零变化。
> - **③ W-form 排版**:诊断 = W6.2 发明的两列网格在字段高不齐时显乱
>   (F4 收根后每字段已是单 grid 项),裁决**单列为主**(grid 1fr);
>   「排版结构断言」钉每字段 label→control→help?→err? 的顺序与嵌套。
> - **④ W-tree 紧凑化**(widget 作用域 CSS,不动共享 app.css 的 .ns-row):
>   行高 ≈26px、缩进 12px/级(--s3)、chevron 10px;行内只留 图标+名称+
>   计数胶囊;hover/选中/调光/键盘全保留。
> - **⑤ W-date 可用性(真实浏览器三条根因 → 修法)**:
>   1) 输入逐字重渲丢焦点/光标 → `set()` 改 `preserveSelection`
>      (定位面 = `[data-wd-{which}="1"]`,有断言);
>   2) **点外收层误伤弹层内点击**:pick/翻月整树重渲,bubble 阶段的
>      document 委托收到 click 时目标已脱离文档,contains 误判为「外」
>      → 一点就关 → 委托改 **capture 阶段**(重渲之前执行,contains 成立);
>   3) 右缘出视口 → `_flipLayer` 加横向翻转(`.wd-date-layer.right` 贴
>      宿主右边);层叠/z-index/双月小容器复查无问题(弹层 absolute 出
>      网格流,340px 容器不裁)。
> - **⑥ W-md view source**:segmented「预览 | 源码」(copy 键 w.md.preview/
>   w.md.source 六主题);state.view ∈ {preview, source},切换不重取数据;
>   源码态 = W-text readonly 同族(行号槽复用编辑器共享件 + mono pre +
>   右下微标);**全文复制钮两态都在**(emit copy 全文 + 1s ✓)。
> - **效果图**:`scripts/gen_widget_design_svgs.py` 的 w-json(键 chips)/
>   w-table(常驻编辑+撤拖柄排序槽)/ w-form(单列)/ w-tree(24px 紧凑)/
>   w-md(segmented)已更新并幂等重生成;w-date 视觉无变化(v2 是行为修复,
>   静态效果图无差异),函数未动仅重跑。
> - **BUILD**:2026-08-05 → 2026-08-05.2(widget-sandbox.js + widget.html
>   全部 ?v= 同步;测试盯三方一致)。
>
> **W6.7 实现注**(2026-08-05,分支 debugger;宿主重新组装——W6 渲染重做后
>   的主动「用新」,准则:能复用 widget 渲染的宿主不自己拼,冲突删宿主):
> - **① lab.js(W-text/W-json/W-list 岛屿)= 核对通过,零改动**:新 chrome
>   (面板头 field · lang/微标胶囊/错误三件套/着色层)在 `.lab-field` 列内
>   协调;app.css 无 `.lab-editor textarea` 级旧样式可清(查过);岛屿装配
>   (内联首渲 + mount 幂等 + data-field 委托表单模型)与新结构兼容
>   (textarea 本体与 data-field 保持)。
> - **② usage-panel.js(W-chart)= 核对通过 + 一处 widget 侧修正**:新版
>   头部(标题/segmented「图表|表格」;单序列无图例项)与 `<details>` 面板
>   布局天然衔接,`.us-chart` 容器本无样式可冲突;**改**:`widgets.css` 加
>   `.wd-chart { max-width: 100%; height: auto }`(viewBox 等比缩,窄幅宿主
>   不溢出)。**card 形态不进摘要位**:details summary 是 details 折叠的
>   点击区,内嵌 role=button 的卡会与折叠交互打架(事件双触发),记为
>   不采用的裁决。
> - **③ web_platform(cards/details/doc-editor)**:
>   - **diff 卡**:`cards.js diffCard` 外包 `.wd-diff` 作用域——新版 add/del
>     行样式(8% 浅底 + 2px 左条双编码)与 **tier 徽标**(F2 接的三类)随之
>     在平台对话卡生效;**删 platform.css 冲突旧规则**(`.pf-dline[data-kind=
>     "add"/"del"]` 的 16% 底 + line-through;判定:宿主旧样式 vs 新 widget
>     样式,删宿主)。逐字节断言更新为包装形态。`.pf-dmember`/`.pf-twocol`
>     保留(字段两列视觉,不在 widget 作用域)。
>   - **doc-editor**(bubble 切割线不动):anchor 透传 `quote: blockTextOf
>     (anchor)`(新锚点引用块 = 锚段原文 2 行截断 + L# 徽标,path 自带);
>     seedMessages 透传 ts(相对时间);发送失败改走控件失败态
>     `bubble.notifyError`(行内红条 + 重试,重试重发同一文本——替换原伪造
>     assistant 回复;无测试依赖旧文案,apply 失败路径保持原样);
>     **view source 开放**:宿主 segmented「预览 | 源码」插进 .doc-toolbar,
>     预览 = 块渲染 + 批注锚点(原状),源码 = `mountMarkdownViewer(preview,
>     {source, title, view:"source", bar:false})`——**widget 侧新增 bar
>     选项**(render/mount 透传,chrome 归宿主时省略 segmented/全文钮);
>     批注列表点击在源码态先切回预览再定位(bubbles 映射在,挂回走
>     renderPreview 既有逻辑)。
>   - **log 卡(原始信号)/ launch 表单**:核对通过——W-log 深色面板进
>     details 原始信号 details 区无样式冲突;W-form W6.6 单列化在 launch
>     对话框(data-launch-form)观感即设计终稿,无宿主样式可清。
> - **⑤ browse 时间窗(W-date)/ skills 树**:date 装配点(mountDatePicker
>   mode:"range")**自动受益** W6.6 三条根因修复(逐字丢焦点/点外误收层/
>   右缘翻转)——platform.test 的倒置→纠序断言持续绿;**skills 树**用的
>   是共享 `ns-tree.js` 组件(browse-tree.js),不是 W-tree 控件,不在本次
>   组装面(注:W6.3 给组件加的 nsAttrs 钩子其渲染路径未启用,零影响)。
> - **⑥ 对话卡统一 card**:diffCard 本就是 widget 渲染(diffBodyHtml),
>   核对+包装见③;其它对话卡(pack/publish/escalation 等)是 app 领域卡,
>   非 widget surface,不换。
> - **BUILD**:2026-08-05.2 → 2026-08-05.3(w-md.js/render.js 又动,三方一致)。

## 4. 不做

- 不引框架(React/Vue 会破坏零 bundler 架构;render 纯函数 + 字符串已够);
- 不做虚拟 DOM(简单件全量重渲 + 选区保留已够;性能问题出现再优化);
- 不改 widget 协议面(def/state/事件/寻址不动——只把渲染拿进控件);
- 不改六主题契约结构(token/copy/组件零分支沿用)。

## 5. 沙盒(单控件调试页;W5.5)

`web/static/widget.html`(经 `/static/widget.html` 访问,服务端零改动)——
一个一个控件地调试的开发工具(非产品 UI:文案平实中文,不走主题 copy)。

- **URL 协议**:`?kind=<注册 kind>&theme=<主题 id>&sample=<序号>&surface=<tab|card>`
  (W5.6;缺省 tab),另支持 `#options=<urlencoded json>` 覆盖样例 mount options
  (surface 与样例正交,不进 options);「复制分享链接」把当前
  kind/theme/sample/surface(及 options 覆盖)序列化进 URL 复制到剪贴板。
  顶栏「形态」切换完整(tab)/卡片(card);card 形态舞台收窄至 340px,
  贴近真实内嵌场景。解析/序列化是纯函数(`js/widget-sandbox.js` 的
  `parseSandboxUrl`/`buildSandboxUrl`,有单测)。
- **加新控件样例**:往 `js/widgets/samples.js` 加一条
  `kind: { mount: "<index.js 的 mount 函数名>", samples: [{name, options}] }`
  ——options 直接喂 mount(per-kind options 形态);样例表是沙盒专用,
  **生产页面不许 import**。覆盖测试会盯 listWidgetKinds() 与样例表一一对应
  (新增控件不加样例 = 测试红)。
- **面板**:Events = 订阅 def.events 全部声明事件(时间戳 + 事件名 +
  payload JSON,新的在上,上限 100 条);State = widget.state 实时 JSON
  (事件触发 + 500ms 轮询),「应用」走 `widget.update(patch)`——
  有 update() 的控件(W-text/W-json,W5.1)可整段 patch 重渲,
  其余控件禁用该钮并提示「该控件暂无 update()」。
