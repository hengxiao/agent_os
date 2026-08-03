# Web UI 无障碍(Accessibility)审计报告与执行标准

> 版本:v0.2 · 日期:2026-08-04
> 范围:`agent_os/src/agent_os/host/web/static/`(index.html + 22 个组件 +
>   6 套主题 + tokens/app.css,合计约 12,350 行)
> 目标基线:**WCAG 2.2 AA**
> **实施状态:A0 已完成**(焦点可见性 + skip link + 高对比度兜底 + 契约断言);
>   A1-A4 未做。v0.2 相对 v0.1 的改动都来自实施反馈:§4 P0-2 补了 ink 主题
>   "替代值也是 none"的漏检、§5.2 把尺寸 token 从逐主题改为 `:root` 单一
>   事实源并记录两个层叠坑、§5.3 A7 从"必须有替代"收紧为"一律不得抑制"。
> 关系:主题契约见 `DEBUG-UI-THEMES.md`(已有对比度/双编码机检);
>   页面结构见 `WEB-UI.md` / `WEB-UI-BLOCKS.md`。
> 一句话:**对比度和色觉这两块已经做得比多数项目好且有机检;缺的是
>   键盘与焦点这一整条线,以及把它变成闸门的机制。**

---

## 1. 方法与诚实边界

本次是**静态源码审计**:通读 index.html、`js/components/*.js`、`js/*.js`、
`css/tokens.css`、`css/app.css`、`css/themes/*.css` 与 `tests/*.mjs`,按 WCAG 2.2
AA 的成功准则逐项对照代码证据。

**没有做、因此不在结论里的**(必须说明,否则这份报告会被当成它不是的东西):

- **没有跑自动化检测**(axe-core / Lighthouse):当前环境未安装 node,
  连既有的 `tests/*.mjs` 都跑不了。所有结论来自源码,不是运行时 DOM;
- **没有真实辅助技术验证**:没有用 NVDA / VoiceOver / Orca 实际听过一遍。
  屏幕阅读器的实际播报与 ARIA 源码的推断之间总有出入;
- **没有验证渲染后的对比度**:主题契约测试用的是 token 值的程序化计算,
  覆盖的是 token 配对,不覆盖"某个组件实际把哪两个颜色叠在了一起";
- **没有覆盖 `proto/` 目录**(静态原型,非产品代码)。

所以本报告的定位是:**源码级问题清单 + 标准草案**。第 4 节列的每条都附了
代码证据可以直接核对;第 5 节的标准落地后,上述三项空白应由机检和一次
人工 AT 走查补上(见 §6 分期)。

---

## 2. 结论摘要

| 维度 | 评价 |
|---|---|
| 色彩与色觉 | **优**——WCAG AA 对比度与"双编码"都已是**机检契约**,6 套主题逐一断言 |
| 语义与地标 | **良**——`lang` / 地标 / `aria-label` / 装饰元素 `aria-hidden` 基本到位 |
| ARIA 角色配对 | **良**——`option`/`listbox`、`treeitem`/`tree` 均成对,未见悬空角色 |
| 动效 | **良**——`prefers-reduced-motion` 在 tokens + 全部 6 主题都有处理 |
| **键盘与焦点** | **差**——**无焦点陷阱、无全局焦点可见样式**,是本次最主要的缺口 |
| **动态内容播报** | **差**——SSE 驱动的状态变化对屏幕阅读器**完全静默** |
| 文档结构 | **差**——全站无 `<h1>`/`<h2>`,无跳转链接 |
| 高对比度模式 | **未支持**——`forced-colors` 零处理,焦点环在 Windows HCM 下会消失 |

**一句话判断**:这个项目在"看得见的可访问性"(颜色、对比、色觉替代通道)上
做得扎实且有闸门守着;在"看不见的可访问性"(键盘、焦点、播报)上基本是空的。
两者失衡的原因很清楚——**前者有契约测试,后者没有**。所以第 5 节的重点不是
再写一份 checklist,而是把后者也变成机器判定。

---

## 3. 已经做对的(附证据,不要在重构中弄丢)

这一节存在的意义是防止后续改动踩坏已有资产。

**3.1 对比度是机检的,不是口号。** `tests/themes-contract.test.mjs` 自己实现了
WCAG 相对亮度与对比度计算,对每套主题断言关键文本配对 ≥ 4.5:1、弱化层级
≥ 3:1。主题作者改错颜色当场红灯。这比"设计师肉眼看着差不多"强一个数量级。

**3.2 双编码(不依赖色觉单通道)也是机检的。** 同一测试第 3 项断言:状态元素
必须**同时**带颜色钩子与文字/图标通道——`status-pill` 必须有
`data-status` **且**有 `<span class="pill-label">` 文字;断点 gutter 必须有
`data-on` **且** `aria-pressed` **且**图标 `●`;暂停行必须有 `data-paused`
**且**图标 `▶`。这是 WCAG 1.4.1(不能仅用颜色传达信息)的**执行点**,而不是
建议。业界很少有项目把这条写成测试。

**3.3 动效降级完整。** `css/tokens.css:178` 起有 `prefers-reduced-motion: reduce`
的全局关闭,且 6 套主题各自再处理一次(ink/blueprint/pixel/terminal/moe 都有)。

**3.4 语义骨架正确。** `index.html`:`<html lang="zh-CN">`、
`<header>` / `<nav aria-label="主导航">` / `<aside aria-label="Run 列表">` /
`<main id="main">` 地标齐全;导航项有 `aria-current="page"`;装饰性 SVG 与
纯视觉字符统一 `aria-hidden="true"`;图标按钮统一给 `aria-label`
(全仓 72 处 `aria-label`)。

**3.5 ARIA 角色成对,没有悬空。** 逐一核对过:
`role="option"`(run 列表项 `app.js:184`、trace 行 `trace.js:627`、
命令面板项)都有对应的 `role="listbox"` 父容器(`index.html:67`、
`trace.js:689`、`debug-view.js:570`、`command-palette.js:84`);
`role="treeitem"`(`frame-tree.js:185`)有 `role="tree"` 容器
(`frame-tree.js:238`)。`option`/`treeitem` 也都带了 `tabindex="0"`。

**3.6 弹层有焦点还原。** 命令面板、启动对话框、快捷键面板三者都记录
`prevFocus = doc.activeElement` 并在关闭时 `prevFocus?.focus?.()`
(`command-palette.js:75,122`、`launch-dialog.js:153,382`、
`shortcuts-panel.js:20,52`),打开时也主动把焦点送进弹层。

**3.7 图标按钮达到目标尺寸下限。** `.icon-btn` 为 `--s6` = 24px 见方
(`app.css:212`、`tokens.css:73`),恰好满足 WCAG 2.2 的 2.5.8 目标尺寸(最小)
24×24 CSS px。**恰好达标意味着没有余量**——见 §4.9。

---

## 4. 问题清单

严重度:**P0** = 键盘或屏幕阅读器用户无法完成任务;**P1** = 显著障碍或
AA 不达标;**P2** = 体验缺陷 / AAA / 稳健性。

### P0-1 弹层没有焦点陷阱,而 `aria-modal="true"` 在撒谎

**证据**:4 处 `aria-modal="true"`(`launch-dialog.js`、`command-palette.js`、
`inbox.js:275`、shortcuts 面板),但全仓**没有任何一处处理 `Tab` 键**——
在四个弹层文件里 grep `Tab` 零命中。

**影响**:键盘用户在"新建 Run"对话框里连按 Tab,焦点会**走出弹层**跑到背后
的页面上,而 `aria-modal="true"` 已经告诉屏幕阅读器"背景不可达"——于是 AT
的虚拟光标和实际焦点分叉,用户彻底失去方位。这违反 WCAG 2.4.3(焦点顺序)
与 2.1.2(无键盘陷阱的反面:焦点应被正确约束)。

**修法**:两选一。
① 迁移到原生 `<dialog>` + `showModal()`——浏览器免费提供焦点陷阱、Esc 关闭、
惰性背景,代码反而变少;② 保留现结构,加一个共享的 `trapFocus(container)`
工具(Tab/Shift+Tab 在首尾元素间循环)。**推荐 ①**,四个弹层共用一条路径。

**附带**:`inbox.js` 的抽屉有 `role="dialog" aria-modal="true"` 但**连
`prevFocus` 还原都没有**(另外三个有),关闭后焦点落回 `<body>`。

### P0-2 除输入框外,全站没有焦点可见样式

**证据**:2,679 行 `app.css` 里只有 3 处 `:focus`/`:focus-visible`,分别是
`[data-tip]:focus-visible::after`(工具提示)、`.search-input:focus`、
`.input:focus`。**按钮、导航链接、`tabindex="0"` 的树行/列表行/trace 行、
chip、卡片全部没有任何焦点样式**,只能依赖浏览器默认轮廓。

**影响**:WCAG 2.4.7(焦点可见)。默认轮廓在深色主题、在被大量重绘的
`pixel`/`terminal`/`moe` 主题上可能与背景几乎无对比;而这些正是本项目
可切换的一等特性。键盘用户在 6 套主题里的体验完全取决于运气。

**注意一处澄清**:全仓 7 处 `outline: none` 中,6 处**是有替代的**——在
`.input:focus` / `.search-input:focus` 规则里紧跟着一个 `box-shadow` 焦点环。
问题是这个待遇**只给了输入框**,没有推广到其他可聚焦元素,也没有沉淀成 token。

**但第 7 处是裸删**:`ink.css` 的 `.input:focus` 是
`box-shadow: none; outline: none;`——**ink 主题的输入框此前没有任何焦点指示**,
只有一个 `--fg-2 → --fg-1` 的低对比边框变化。这是实施 A0 时才发现的,
原审计只统计了"有无替代"、没有逐条看替代值是不是 `none`。

**修法**:见 §5.2。

> **已修复(A0)**:`--focus-ring` token 化 + 一条全局 `:focus-visible` 规则,
> 并**移除全部 7 处 `outline: none`**——光晕改为与 outline **叠加**而非替代。
> 实施时踩到一个层叠陷阱,记录在 §5.2 的"两个坑"。

### P0-3 SSE 驱动的状态变化对屏幕阅读器完全静默

**证据**:全仓只有 **1 处 `aria-live`**——`index.html:71` 的 toast 栈。
`app.js` / `workbench.js` / `status-pill.js` / `progress-bar.js` 里
零 `aria-live`、零 `role="status"`。

**影响**:这是一个**以实时运行为核心**的产品。run 从 running 变成 done/failed、
新的 trace 行不断追加、步数与预算推进、supervisor 挂起等待作答——这些
**全部是视觉专属的**。屏幕阅读器用户启动一个 run 之后,不会被告知它结束了,
也不会被告知它在等他裁决。违反 WCAG 4.1.3(状态消息)。

**修法**:见 §5.3——建立**一个**礼貌级 live region 承载运行态摘要
(状态迁移、终态、supervisor 待办),**不要**把 trace 流全量播报(会淹没用户)。
`role="alert"` 已有 5 处用在错误横幅上,方向是对的,把它系统化。

### P1-4 全站没有 `<h1>`/`<h2>`,标题层级断裂

**证据**:index.html 与全部 JS 组件里,标题元素只有 **9 个 `<h3>`**,
`<h1>`/`<h2>` 零。

**影响**:屏幕阅读器最主要的页内导航手段就是按标题跳转(H 键 / 标题列表)。
现在每个路由页都没有页面级标题,用户无法快速定位"我在哪、这页有哪几块"。
违反 WCAG 1.3.1(信息与关系),实践上是重度体验缺陷。

**修法**:每个路由的主区域给一个 `<h1>`(Runs / Skills / Tools / Lab / Debug),
现有 `<h3>` 按嵌套深度降级为 `<h2>`/`<h3>`。视觉上可以不变——用 CSS 控制字号,
不要为了视觉去挑标题级别。

### P1-5 SPA 路由切换不播报、不移焦

**证据**:`js/app.js` 全文没有 `document.title` 的写入;路由切换后也没有把焦点
移到新内容(唯一的 `.focus()` 在 `app.js:506`,是别的用途)。

**影响**:hash 路由切换后,屏幕阅读器用户毫无感知——标题没变、焦点还停在
刚点过的导航项上、新内容静默替换。配合 P1-4(没有 `<h1>`)后果加倍。

**修法**:路由切换时 ① 更新 `document.title`(`Runs · Agent OS`);
② 把焦点移到新页面的 `<h1>`(给它 `tabindex="-1"`);③ 或用 P0-3 的 live region
播报一句"已进入 Runs"。①+② 是最小可用组合。

### P1-6 没有跳过导航链接

**证据**:`index.html` 无 skip link(grep `skip` 在 index.html 零命中;
`app.css` 里两处 `skip` 是 Lab 闸门状态,无关)。

**影响**:键盘用户每次进入页面都要 Tab 过顶栏的 5 个导航项 + 主题选择器 +
收件箱 + 连接指示器才能到主内容。WCAG 2.4.1(区块跳过)。

**修法**:成本极低——`<main id="main">` 已经存在,加一个默认视觉隐藏、
`:focus` 时显形的 `<a href="#main">跳到主内容</a>` 即可。

### P1-7 不支持 Windows 高对比度模式(forced-colors)

**证据**:`forced-colors` / `prefers-contrast` 在全部 CSS 中**零命中**。

**影响**:在 Windows 高对比度模式下,浏览器会**丢弃 `box-shadow`**。而本项目
**唯一的焦点指示就是 `box-shadow` 环**(见 P0-2 澄清)——于是在 HCM 下
输入框的焦点变得完全不可见。同理,靠背景色区分的状态块会全部塌成同色
(双编码在这里救了一部分,因为还有文字通道)。

**修法**:加一段 `@media (forced-colors: active)`,把焦点环改用
`outline: 2px solid CanvasText`,并给依赖背景色的状态块补 `border`。

### P2-8 弹层用 `aria-label` 而非 `aria-labelledby` 指向可见标题

`launch-dialog.js:166` 用 `setAttribute("aria-label", "New Run")`。可用,但
可见标题与无障碍名是两份数据,会漂移(尤其本项目有六套主题的 copy 表)。
建议改为 `aria-labelledby` 指向弹层里那个可见的 `<h2>`,顺带满足 P1-4。

### P2-9 目标尺寸没有余量

`.icon-btn` 恰好 24×24,正好等于 WCAG 2.2 的 2.5.8 下限。任何一次"图标看起来
有点大、缩一点"的改动都会跌破。建议提到 28–32px,或把它写进 token 契约钉死
(见 §5.2)。

### P2-10 `title` 属性被当作可访问信息载体

trace 行用 `title="..."` 承载信号全名与绝对时间戳(`trace.js:630`)。
`title` 在触屏上不可达、键盘上不可靠、屏幕阅读器播报不一致。若这些信息重要,
应进可见 DOM 或 `aria-describedby`;若只是补充,当前用法可接受但不应扩大。

---

## 5. 执行标准:如何让以后照着做

**核心判断:不要再写一份 checklist。** 这个项目已经证明了什么有效——
对比度和双编码之所以做得好,不是因为有人记得,而是因为
`themes-contract.test.mjs` 会红。而键盘与焦点之所以是空的,正因为没有对应的
测试。所以标准的形态应当与项目既有哲学一致:**契约先行、闸门守出口、
能机器判定的就不靠人自觉**。

标准分三层,**只有第三层需要人**。

### 5.1 总纲:三层与红线

| 层 | 内容 | 判定方式 | 违反后果 |
|---|---|---|---|
| **L1 token 契约** | 焦点环、目标尺寸、强制色兜底 | 机检(扩展 themes-contract) | CI 红 |
| **L2 组件不变量** | 角色/焦点/可及名的结构性规则 | 机检(新增 a11y-contract 静态扫描) | CI 红 |
| **L3 交互模式库** | 键盘按键约定、播报策略 | 人审(PR checklist) | Review 阻塞 |

**三条红线**(任何 PR 不得违反,机检):
1. **可聚焦即可见**:任何能获得焦点的元素必须有可见焦点指示;
2. **可点击即可键盘操作**:任何绑定 click 的元素必须可聚焦且响应 Enter/Space;
3. **信息不得只走颜色**(已有,继续)。

### 5.2 L1:token 契约(扩展现有 `themes-contract.test.mjs`)

**颜色逐主题声明,尺寸只在 `:root`**(这一条与本文 v0.1 初稿不同,实施时改的):

```css
/* css/themes/<id>.css —— 契约 token,6 套主题必须各自定义且非空 */
--focus-ring:        /* 焦点环颜色,与该主题 --bg-0..3 四层对比度均 ≥ 3:1 */

/* css/tokens.css :root —— 策略常量,单一事实源,主题不得覆盖 */
--focus-ring-width:  /* ≥ 2px */
--focus-ring-offset: /* ≥ 1px,保证环不被内容压住 */
--target-min:        /* 交互目标最小边长,≥ 24px;建议 28px */
```

**为什么尺寸不做成逐主题契约**:宽度和偏移是**策略**不是风格。把它们交给
主题定义,就等于允许某套主题写 `--focus-ring-width: 0` 把焦点环悄悄调没,
而契约测试只会看到"已定义且非空"、判它通过。颜色必须逐主题(每套主题的
背景不同,对比度只能各算各的),尺寸必须集中(它是下限,不是口味)。
契约测试因此多一条:**任何主题文件里出现 `--focus-ring-width` /
`--focus-ring-offset` 即判失败**。

配套断言(复用现有 `contrast()` 实现,零新机制):

```js
/* 焦点环对比度:WCAG 2.2 的 1.4.11 非文本对比 */
for (const bg of ["--bg-0", "--bg-1", "--bg-2", "--bg-3"]) {
  assert.ok(contrast(tokens["--focus-ring"], tokens[bg]) >= 3,
    `[${theme.id}] 焦点环在 ${bg} 上对比度不足 3:1`);
}
assert.ok(parseInt(tokens["--target-min"]) >= 24, `[${theme.id}] 目标尺寸 < 24px`);
```

并在 `app.css` 写**一条**全局规则,主题只换 token、不碰规则(与"组件零分支"
同哲学):

```css
:where(a, button, input, select, textarea, summary, [tabindex]):focus-visible {
  outline: var(--focus-ring-width) solid var(--focus-ring);
  outline-offset: var(--focus-ring-offset);
}
@media (forced-colors: active) {
  :where(a, button, input, select, textarea, summary, [tabindex]):focus-visible {
    outline: 2px solid CanvasText;   /* box-shadow 在 HCM 下被丢弃,必须用 outline */
  }
}
```

> 用 `outline` 而不是 `box-shadow` 作为**基础**焦点指示,是因为 outline
> 在 forced-colors 下存活、且不参与布局。主题想要光晕效果可以**叠加**
> box-shadow,但不得把 outline 去掉——这一条由 L2 静态扫描守。

#### 实施时踩到的两个坑(写下来免得重犯)

**坑一:层叠顺序会让全局规则失效。** `index.html` 的加载顺序是
tokens → app.css → **6 套主题**。而 `:where(...)` 的特异性是 0,
`:focus-visible` 贡献 (0,1,0),与 `.input:focus` **同特异性**——于是后写的赢。
既有的 7 处 `outline: none` 全都在全局规则之后(2 处在 app.css 下游、
5 处在主题文件里),**会把新加的全局 outline 全部盖掉**,而且在
forced-colors 下连 box-shadow 兜底都没有。

正确做法不是提高特异性、也不是把规则挪到文件末尾(主题仍在其后),
而是**把 7 处 `outline: none` 全部删掉**,让 outline 与各主题的光晕
**叠加**。删完之后 A7 不变量(§5.3)才真正可执行——这也是为什么 A7 从
"必须有替代"收紧成了"一律不得抑制"。

**坑二:skip link 的落点必须可编程聚焦。** `<a href="#main">` 指向
`<main id="main">` 时,浏览器只滚动、**不移动键盘焦点**(下一次 Tab 仍回到
导航)。落点必须加 `tabindex="-1"`(不进 Tab 序列,但可编程聚焦)。
这是 skip link 最常见的失效方式,契约测试已把它钉死。

### 5.3 L2:组件不变量(新增 `tests/a11y-contract.test.mjs`)

与 `themes-contract` 同级、同风格(静态扫描 + fixture 渲染断言),进 CI。
下面每条都是**可机器判定**的,给出判据而不是形容词:

> **已落地部分(A0)**:A7、A9 与 token 契约已随 A0 一并实现,写在既有的
> `tests/themes-contract.test.mjs` 第 7/8 组断言里(**不另起文件**——焦点环
> 本就是主题契约的一部分)。剩余 A1–A6、A8、A10 待 A3 期新建
> `a11y-contract.test.mjs`。

| # | 不变量 | 判据(静态扫描 `js/components/*.js` + `index.html`) |
|---|---|---|
| A1 | 角色成对 | 出现 `role="option"` 的文件必须出现 `role="listbox"`;`treeitem` ↔ `tree`;`tab` ↔ `tablist` |
| A2 | 可聚焦性 | 每个 `role="option"\|"treeitem"\|"tab"\|"button"` 的模板串必须同时含 `tabindex=` |
| A3 | **焦点陷阱** | 每个含 `aria-modal="true"` 的文件必须含 `showModal(` 或 `trapFocus(` |
| A4 | **焦点还原** | 同上文件必须含 `prevFocus`(或统一封装名) |
| A5 | 可及名 | `<button` 模板串若无文字子节点,必须含 `aria-label` 或 `aria-labelledby` |
| A6 | 装饰元素 | 每个 `<svg` 必须含 `aria-hidden="true"` 或(`role="img"` 且 `aria-label`) |
| A7 | **outline 一律不得抑制** ✅已落地 | 任何样式表出现 `outline:\s*(none\|0)` 即判 fail。**比初稿更严**——初稿写的是"必须有替代",但 ink 主题的替代恰好也是 `none`(§4 P0-2),且 box-shadow 在 HCM 下会被丢弃,所以"有替代"不是充分条件 |
| A8 | 标题层级 | 每个路由根模板必须含且仅含一个 `<h1`;不得跳级(出现 `<h3` 则文件内须先有 `<h2` 或 `<h1`) |
| A9 | 强制色 | `app.css` 必须含 `@media (forced-colors: active)` 段 |
| A10 | live region | `index.html` 至少含一个 `aria-live="polite"` 的运行态区域(id 固定,见 §5.4) |

**分档**:A1–A7 判 `fail`(CI 红);A8–A10 首期判 `warn`,补完后转 `fail`
——与五关闸门"宁稳勿滥、先可见再强制"的先例一致。

### 5.4 L3:交互模式库(人审,写进 `WEB-UI-BLOCKS.md`)

机器管得住结构,管不住"按了 Enter 应该发生什么"。这部分固化为**模式表**,
新组件必须声明自己属于哪一类,评审时照表核对:

| 模式 | 键盘契约 | 现有实例 |
|---|---|---|
| 列表(listbox) | ↑↓ 移动、Enter 激活、Home/End 跳首尾;容器 `tabindex=0` 或行 roving tabindex | run 列表、trace 行 |
| 树(tree) | ↑↓ 移动、←→ 折叠/展开、Enter 激活 | 帧树、ns-tree |
| 弹层(dialog) | Esc 关闭、Tab 循环、打开移焦入内、关闭还原 | 启动对话框、命令面板、快捷键面板、收件箱抽屉 |
| 命令面板 | ↑↓ 选、Enter 执行、Esc 关、输入框常驻焦点 | 命令面板 |
| 可展开区(disclosure) | Enter/Space 切换,`aria-expanded` 同步 | trace chevron、Lab 折叠区 |

**播报策略**(对应 P0-3,避免"全量播报淹没用户"):

- **一个** `aria-live="polite"` 运行态区域(`id="runStatusLive"`),只播报:
  run 状态迁移(running→done/failed/aborted)、终态摘要、supervisor 待办出现;
- **`role="alert"`**(assertive)只给错误与需要立即介入的裁决请求——现有 5 处
  错误横幅已符合;
- **trace 流不播报**(高频、会淹没);用户主动聚焦某行时由该行自身的可及名承载。

### 5.5 PR checklist(短到有人会真的看)

写进 PR 模板,五条:

1. 新增可点击元素 → 是 `<button>`/`<a>` 吗?不是的话有 `role` + `tabindex` + 键盘激活吗?
2. 新增弹层 → 用了 `<dialog>`/`trapFocus` 吗?Esc 能关吗?关了焦点回哪?
3. 新增图标/纯符号 → 有 `aria-label` 或 `aria-hidden` 吗?
4. 新增状态显示 → 除了颜色,还有文字或图标通道吗?(A 已机检,此处自查)
5. 新增异步结果 → 用户不看屏幕的话,他怎么知道它完成了?

### 5.6 为什么不引入 axe-core / Lighthouse 作为闸门

值得说明,免得后续反复:**可以引入,但不能只靠它**。

- 自动化工具公认只能覆盖 WCAG 问题的一部分(经验值约三分之一),
  且它们**查不出本报告里最严重的两条**——焦点陷阱缺失和"运行结束了没人告诉你",
  这两条都需要理解交互意图;
- 本项目前端是**零 bundler、零 npm 依赖**的原生 ESM(`static/package.json`
  与 `node --test` 直跑)。引入 axe 会带来第一个运行时依赖树,与既有取舍冲突;
- 因此建议:**L1/L2 用自写静态契约(零依赖,与 themes-contract 同一形态)守
  回归;axe/Lighthouse 作为周期性人工体检(非闸门)**,发现的新问题再沉淀成
  L2 的新不变量。

---

## 6. 分期与验收

| 期 | 内容 | 验收 |
|---|---|---|
| **A0** ✅**已完成** | skip link + `<main tabindex="-1">`(P1-6)、`--focus-ring` 六主题 token 化 + 全局 `:focus-visible`(P0-2)、`forced-colors` 段(P1-7)、移除全部 7 处 `outline: none`、契约测试第 7/8 组断言 | 60 条新断言全过(**见下方验证说明**);后端 850 passed 无回归 |
| **A1**(P0 收口) | 四个弹层迁 `<dialog>`/`trapFocus` + inbox 补焦点还原(P0-1);运行态 live region(P0-3) | Tab 出不去弹层;run 结束有播报 |
| **A2**(结构) | 每路由 `<h1>` + 标题降级(P1-4);路由切换更新 title 与移焦(P1-5) | 屏幕阅读器按标题可导航;切路由有感知 |
| **A3**(闸门) | `tests/a11y-contract.test.mjs` 落地(§5.3 A1–A7 fail、A8–A10 warn);token 契约并入 themes-contract(§5.2) | 故意写一个无 `aria-label` 的图标按钮 → CI 红 |
| **A4**(补空白) | 一次真实 AT 走查(NVDA 或 Orca)+ 一次 axe 体检;把发现沉淀为新的 L2 不变量;A8–A10 转 fail | §1 列的三项方法空白被补上 |

**依赖**:A1 与 A2 无依赖可并行;A3 依赖 A0/A1/A2 的产出(否则新写的闸门当场
就是红的);A4 需要能跑 node 与浏览器的环境。

> ### A0 的验证说明(必读,涉及证据强度)
>
> **本机没有任何 JS 运行时**(node / bun / deno 均未安装),所以 A0 新写进
> `themes-contract.test.mjs` 的第 7/8 组断言**在提交时没有被真正执行过**。
>
> 采取的替代验证:用 Python 复算了同一批断言——**刻意使用与 JS 测试逐字相同
> 的正则与 WCAG 相对亮度算法**(包括 `themeTokens()` 的
> `\[data-theme="<id>"\]\s*\{([^}]*)\}`、`:root\s*\{([\s\S]*?)\n\}`、
> 以及 outline / skip-link 的匹配式),60 条全部通过。这能证明"这些断言对
> 当前文件内容成立",**不能证明**该 `.mjs` 文件在 node 下语法/导入无误。
>
> **因此**:第一个有 node 的环境必须先跑一次
> `node static/tests/themes-contract.test.mjs`,把这条证据补实。在那之前,
> A0 的状态应读作"逻辑已验证、执行未验证"。
>
> 后端 `pytest agent_os/tests` 850 passed / 10 skipped / 32 xfailed,
> 这条是真跑的——但它与前端改动正交,只说明没有连带回归。

---

## 7. 与既有文档的关系

- `DEBUG-UI-THEMES.md`:§5.2 的 token 契约是它第 1/2 项的直接延伸,应并入
  同一份契约测试而不是另起一套;
- `WEB-UI-BLOCKS.md`:§5.4 的交互模式表应写进它,作为组件块的附加约束;
- `WEB-UI.md`:§5.3 的 A10(运行态 live region)涉及页面结构,应在此登记
  `id="runStatusLive"` 这个契约位。
