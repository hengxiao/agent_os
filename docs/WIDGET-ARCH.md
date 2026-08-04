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
| W5.4 | 清扫:宿主手写桥接代码删除;架构测试记录更新(弯腰点 ①⑤ 关闭情况) | 无装饰器残留 |

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

## 4. 不做

- 不引框架(React/Vue 会破坏零 bundler 架构;render 纯函数 + 字符串已够);
- 不做虚拟 DOM(简单件全量重渲 + 选区保留已够;性能问题出现再优化);
- 不改 widget 协议面(def/state/事件/寻址不动——只把渲染拿进控件);
- 不改六主题契约结构(token/copy/组件零分支沿用)。
