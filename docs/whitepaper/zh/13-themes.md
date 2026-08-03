# 主题系统:契约与六主题

> 章次:13 · 状态:已实现(动效播放层已设计未实现,见 §6) · 依据:`docs/DEBUG-UI-THEMES.md`、`docs/DEBUG-UI-MOE.md`、`agent_os/src/agent_os/host/web/static/js/themes.js`、`.../js/components/mascot.js`、`.../css/themes/`、`.../tests/themes-contract.test.mjs`、`.../tests/smoke-theme.test.mjs`

## 1. 概述

主题系统是 Web 宿主层(`agent_os/src/agent_os/host/web/static/`)的换肤架构:它把"皮肤"正式化为一份**三层契约**——语义 token、文案键(copy key)、动效名(motion name),外加一个可整体关闭的 mascot 层。六个内置主题(`classic`/`moe`/`terminal`/`blueprint`/`ink`/`pixel`)都是契约下的数据包,组件代码永远只有一份(`docs/DEBUG-UI-THEMES.md:12`)。系统完全位于浏览器侧:后端没有任何主题 API,主题状态不进 run、信号或 WAL,与内核的确定性工程(第 00 章 §6.1)正交。

## 2. 动机与背景(原因)

系统的直接起源是一次"第二个皮肤"的提案:萌系策划案(`DEBUG-UI-MOE.md`)本想给调试台做一个萌系变体。策划过程中,作者发现皮肤之间的差异可以收敛成**可枚举的三件事**:token 映射 + 文案表 + mascot/动效层(`DEBUG-UI-THEMES.md:11`)。既然差异是可枚举的,正确动作就不是再做一份皮肤,而是把差异形式化为契约——主题成为可插拔的数据包。具体塑造这个系统的张力有四组:

- **演示面 vs 工程可信度**。调试台是 Agent OS 的演示面(断点/单步/干预本身是硬技术),工程评审、对外 demo、个人把玩需要不同气质(`DEBUG-UI-THEMES.md:15`);但工具默认必须严肃——所以 `classic` 是默认主题,萌系是显式切换的选项(`DEBUG-UI-MOE.md:151`),而不是强加。
- **皮肤数量 vs 维护成本**。如果每个皮肤复制一份组件,改一处 bug 要改 N 处,行为必然漂移(`DEBUG-UI-MOE.md:155` 风险表"维护两套 UI 漂移")。答案是把组件钉死为一份,差异全部下沉到数据包。
- **开放扩展 vs 质量底线**。主题对社区开放的预设(`DEBUG-UI-THEMES.md:17`)意味着必须有程序化闸门拦住半成品——契约既是扩展点,也是质量闸门。
- **换肤能力 vs 工程约束**。Web 前端坚持"无构建、零依赖"(`DEBUG-UI-MOE.md:47`:mascot 纯 CSS/SVG,不引图片资源),这排除了 CSS-in-JS、主题编译器等方案,只剩下一条技术路线:CSS 自定义属性 + 级联。

为什么这个系统不放在内核或后端:它是纯表现层。把它挡在运行时之外,主题切换就永远不可能污染回放、审计与调试语义——这是与"薄宿主"分层(第 00 章 §3.1)一致的放置决策。

## 3. 问题陈述(解决的问题)

1. **皮肤漂移**:若萌系版实现为第二个页面/第二套组件,此后任何组件修复都要双写;不出一个迭代,两套 UI 的行为就会分叉(`DEBUG-UI-MOE.md:155`)。
2. **半成品主题上线**:某主题 css 漏定义 `--danger`,某页面的 failed 状态就"缺一块"——没有颜色、没有对比度,且只有人工走查才能发现。
3. **pastel 对比度失守**:萌系初值 `--ok: #5ec9a7` 配奶油底,对比度不足 4.5:1(`DEBUG-UI-MOE.md:80` 的可访问性红线);靠自觉调色必然退化。
4. **色觉单通道依赖**:状态若只靠颜色区分,色觉障碍用户无法分辨 done/failed/aborted。
5. **文案翻译吞掉技术原文**:萌化文案若替换错误原文,用户对工具的信任即告破产——"用户信任建立在'这只精灵从不骗我'"(`DEBUG-UI-MOE.md:26`)。
6. **气质不可分享**:把调试台切到某主题后复制链接发给同事,对方看到的仍是默认主题——深链接丢失"同款气质"(`DEBUG-UI-THEMES.md:68`)。
7. **主题越界到未验收页面**:一个只走查过调试台的主题若全站生效,Runs/Skills 等页面可能出现观感破洞。

## 4. 设计与机制(解决的方法)

### 4.1 三层契约架构

```
┌──────────────────────────────────────────────────────────────┐
│ 组件层(status-pill / debug-view / inbox / lab …)              │
│   只消费:var(--语义token) · copy(key) · mascotHtml(expr)      │
│   —— 组件代码中禁止出现任何主题 id(契约测试第 6 项静态扫描)   │
├──────────────────────────────────────────────────────────────┤
│ 契约层(js/themes.js)                                          │
│   CONTRACT_TOKENS(43 个变量,themes.js:25-40)                  │
│   COPY_KEYS(以 classic 表为准,69 键,themes.js:43)             │
│   动效名 × 5:bp-hit / step / resume / run-done / intervene    │
│   注册校验:css 未加载或缺 token → 不注册 + console.warn        │
├──────────────────────────────────────────────────────────────┤
│ 主题包 = css/themes/<id>.css + js/copy/<id>.js                │
│   classic · moe · terminal · blueprint · ink · pixel          │
│   mascot 注册表(mascot.js:138-161):mochi · sprite8            │
└──────────────────────────────────────────────────────────────┘
```

(架构对应 `DEBUG-UI-THEMES.md:21-33` 的三层图;组件零分支是硬约束而非倡导。)

### 4.2 token 契约与注册校验

每个主题必须在 `[data-theme="<id>"]` 规则下给契约全集 43 个变量全部赋值:基底 9(`--bg-0..3`、`--line`、`--line-strong`、`--fg-0..2`)、状态 5、信号 6、权限 4、排版 19(字体 2 + 字号 6 + 间距 7 + 圆角 4),一个不能少(`themes.js:25-40`;清单即 WEB-UI.md §3 的既有语义变量)。注册时 `sheetTokens()` 从已加载样式表中提取该主题的声明块(`themes.js:108-129`),缺变量或 css 未加载则拒绝注册并 `console.warn`(`themes.js:133-153`)——"防半成品主题上线"(`DEBUG-UI-THEMES.md:45`)。node 测试环境无 `styleSheets` API 时跳过运行时校验,完整性由契约测试直接解析 css 源断言(`themes.js:6-8`),两条校验路径共享同一份契约清单。

**取舍:拒绝注册,而不是运行时补缺。** 缺失的 token 没有合理兜底——用 classic 的值补会让主题出现"一块不像自己"的区域,比整个不可用更糟。宁可整个主题不上线(`DEBUG-UI-THEMES.md:144`:"宁可回落,不半成品")。

### 4.3 文案契约:翻译层而非替换层

`COPY_KEYS` 以 classic 表为准(69 键,覆盖状态短语、空态、确认、Skill Lab、升权卡片等,`js/copy/classic.js`),各主题表必须同 key 覆盖。组件经 `copy(key)` 取值,查询走三级回落:**有效主题 → classic → key 原文**(`themes.js:245-251`)。核心规则是**技术文本豁免**:错误原文、状态原文、工具参数永远直读,不进文案表(`DEBUG-UI-THEMES.md:50-51`);主题文案是"翻译层"——例如 moe 的中止文案是 `先到这里喵(aborted)`,原文以括号并列(`smoke-theme.test.mjs:262` 有断言)。

**取舍:文案缺 key 只告警不拒注册**(`themes.js:147-150`),与 token 缺即拒形成不对称。理由:缺文案有 classic 兜底,页面不破;缺 token 无兜底,样式破洞。两种缺失的后果不同,闸门强度也不同。

### 4.4 切换、持久化与 scope 回落

```
启动 initTheme():URL(?theme=) > localStorage > classic(themes.js:223-232)
                  URL 命中同时写入持久化——深链接分享"同款气质"
切换 applyTheme():requestedId → localStorage(agent-os.theme)
                  → hash ?theme= 同步(replaceState,不触发路由;classic 省略参数)
                  → resolveEffective(page):scope 未验收 → 强制 classic(themes.js:168-173)
                  → <html data-theme="<effective>"> → CSS 变量级联,全站即时生效
```

实现上有意区分 `requestedId`(用户选择,持久化对象)与 `effectiveId`(当前页实际生效,`themes.js:161-162`):scope 回落不覆盖用户选择,回到已验收页面时原选择自动恢复。路由变化时 `syncTheme()` 重解析(`app.js:339`)。TopBar 切换器是纯数据驱动:遍历注册表渲染,每项带三色 swatch(从主题 css 读 `--bg-0`/`--fg-0`/`--live`,`themes.js:254-260`)。

**取舍:`data-theme` 属性 + CSS 级联,而不是 JS 换肤。** 切换不需要重渲染任何组件,组件对主题完全无感知;这也让"无构建、零依赖"约束自然成立。代价是主题能力被限制在 CSS 变量能表达的范围内——但 §4.2 的契约恰好就是这个范围,约束与能力自洽。

### 4.5 mascot 层:可整体关闭的第二抽象

mascot 实现为独立层而非组件内分支:组件只在固定槽位调用 `mascotHtml(expr)`(如 `debug-view.js:441` 的控制条槽位),层自己读当前主题的 `mascot` 声明——`null` 主题(classic/terminal/blueprint/ink)下返回空串,组件无任何 if(`mascot.js:164-177`)。层内部是 `MASCOTS` 注册表:每个 mascot = { name, exprs, sprite },精灵是纯 SVG `<symbol>` 精灵表,色彩全部走主题 css 的类规则,SVG 内零色值(`mascot.js:21-22`)。表情映射 `mascotStateFor` 从会话快照推导(running/paused/done/failed,`mascot.js:12-19`),两个 mascot 共用同一映射函数。

`pixel` 的 `sprite8` 是这一抽象的**第二实例**——同一 `MascotLayer` 接口、另一套 8-bit 精灵表,证明层是可换的(`DEBUG-UI-THEMES.md:117`,`smoke-theme.test.mjs:424-436` 有断言)。

### 4.6 六主题目录

| 主题 | 气质 | 标志性映射 | mascot |
|---|---|---|---|
| `classic` | 严肃工程(默认,契约参考实现) | 现状即主题,值逐字搬自 tokens.css(`classic.css:2`) | 无 |
| `moe` | 萌系(奶油粉底 pastel) | Mochi 状态化身;状态pill 猫咪表情;文案翻译层 | mochi |
| `terminal` | 终端极客(磷光绿 on 黑) | ASCII 双线框;暂停行反色;shell 腔文案 | 无 |
| `blueprint` | 工程图纸(蓝图蓝 + 方格网) | 图框/图签;状态图章;`APPROVED(done)` | 无 |
| `ink` | 水墨(宣纸 + 单一朱砂强调色) | 朱砂印章状态;墨线暂停指示;文言腔 | 无 |
| `pixel` | 8-bit 复古游戏 | 预算条=HP/MP;断点=checkpoint 旗;`LEVEL CLEAR!` | sprite8 |

(`DEBUG-UI-THEMES.md §3.1-3.6`;六主题均 `scope: "app-wide"`,`themes.js:48-104`。)

### 4.7 动效档案(已设计,播放层未实现)

契约规定组件只调用具名动效(`bp-hit`/`step`/`resume`/`run-done`/`intervene`),主题为每个名字声明档位:`full`/`subtle`/`instant`,`prefers-reduced-motion` 强制 `instant`(`DEBUG-UI-THEMES.md:55-57`)。**实现现状:注册表中六个主题的五个动效名全部登记为 `subtle`**(`themes.js:54` 等),动效播放层尚未落地,契约测试第 5 项(动效降级断言)待补(`DEBUG-UI-THEMES.md:141`、执行摘要 §8)。各主题 css 内仅有零星 `@keyframes`(moe 呼吸/入场、terminal 扫描线等),不走具名动效通道。

## 5. 效果与验证(效果)

**契约测试** `tests/themes-contract.test.mjs`(node 直跑,不依赖浏览器)遍历注册表逐主题断言五组性质:

1. **token 完整性**:43 个契约变量在 css 源中全部定义且非空(`themes-contract.test.mjs:94-100`);
2. **对比度**:程序化 WCAG 相对亮度计算,关键配对(正文/状态色/信号权限色 × 基底,共 30 对)≥ 4.5:1,弱化层级 4 对 ≥ 3:1(`themes-contract.test.mjs:77-114`)——主题作者改色即时报红;
3. **双编码**:状态元素同时带颜色钩子(`data-status`/`data-on`)与文字/图标通道(状态文字/●/▶),不依赖色觉单通道(`themes-contract.test.mjs:116-135`);
4. **文案键完整**:copy 表覆盖 69 键全量(`themes-contract.test.mjs:137-139`);
5. **组件无分支**:静态扫描 `js/components/`,禁止主题 id 字面量、`data-theme` 属性、按主题 id 比较(`themes-contract.test.mjs:142-162`;`terminal` 因与图标名撞词只禁比较形态)。

**冒烟测试** `tests/smoke-theme.test.mjs` 覆盖运行时行为 12 组场景:注册负例(css 未加载的 ghost 主题不注册、注册表不被污染)、启动解析优先级(URL > localStorage > classic,URL 命中写持久化)、未知主题回落 classic、切换器渲染与联动、四主题文案腔调(terminal `[halted](paused)`、blueprint `APPROVED(done)`、ink `驻(paused)`、pixel `CLEAR!`)、MascotLayer 随主题出现/消失、调试台集成(moe 下控制条出现 Mochi 且暂停点技术原文并列)、scope 回落(合成 scoped 主题验证机制),以及 css 源级断言(moe 表情映射、六主题背景图案母题)。

整体基线:前端 24 个测试文件全绿(执行摘要 §8),其中主题系统占 2 个。`classic` 抽离做到行为零变化(值逐字搬自 `tokens.css`);swatch 取色直连真实 css(如 moe = `#fff5f7`/`#5c3d47`/`#c2245c`,`smoke-theme.test.mjs:150`)。

**涟漪效应**:萌系策划从"第二个页面"自我降格为主题目录中的一员(`DEBUG-UI-MOE.md` 文头 v0.2);文案契约超出了调试台——Skill Lab 与升权收件箱的文案同样走 copy 表(`lab.*`、`escalation.*` 键),主题系统实际成了全站 UI 文案的唯一出口;`sprite8` 证明 mascot 抽象可换后,"主题 = 气质包,mascot 只是可选资产"的判据成立(`DEBUG-UI-THEMES.md:61`)。T4 分期原拟逐页验收后再开放 `scope`,实际 T1.1 即全站开放——依据正是组件零分支 + 契约全集校验使"缺一块"在结构上不可能(`DEBUG-UI-THEMES.md:146-149`)。

## 6. 局限性与边界(局限性)

1. **动效播放层未实现**:motion 字段目前只是注册数据(全 `subtle`),契约五组具名动效没有运行时;契约测试第 5 项(reduced-motion 强制 `instant` 的断言)空缺,降级目前只靠各主题 css 内的 `prefers-reduced-motion` 兜底。
2. **对比度测试只覆盖 token 静态配对**:测试计算的是"文字色 × 基底变量"的配对(`themes-contract.test.mjs:77`),不覆盖 body 背景图案(樱花/方格网/扫描线)与面板叠加后的实际观感;图案透明度靠作者克制,不在闸门内。
3. **"技术文本豁免"是约定不是强制**:测试断言 copy 键覆盖与个别并列形态(如 `(aborted)`),但没有静态扫描阻止作者把错误原文塞进文案表;豁免规则的执行最终靠评审。
4. **mascot 表达能力远小于设计**:MOE 文档 §2 的九行状态表(揉眼、托腮冒泡、塞纸条、鞠躬退场等)大部分未实现;当前只有 running/paused/done/failed 四个表情 + ready 复用,aborted 等状态不映射——层整体消失(`mascot.js:18`),此时 mascot 状态表达存在空窗。
5. **主题 = 代码贡献**:不做用户自定义主题编辑器(`DEBUG-UI-THEMES.md:153`);主题变体不繁殖(terminal 的 amber 是设计中的唯一 `--variant` 示例,v1 未做,`terminal.css:6` 注释)——个性化的代价是写 css 走贡献流程。
6. **scope 回落机制未经真实使用**:六主题全部 `app-wide`,回落路径只有合成主题测试覆盖(`smoke-theme.test.mjs:331-355`);机制为"未来半成品主题"保留,尚未拦截过真实案例。
7. **双环境校验存在漂移空间**:浏览器注册校验读 `styleSheets`,node 测试解析 css 源,是两条独立代码路径;二者共享契约清单但不共享解析器,理论上可能出现一边过一边不过的边缘情形(如选择器写法差异)。
8. **无音效系统**:pixel 留了挂点但默认静音(`DEBUG-UI-THEMES.md:154`),`LEVEL CLEAR!` 等游戏化反馈只有视觉通道。

## 7. 引用

- 设计文档:`docs/DEBUG-UI-THEMES.md`(主题系统策划:契约/目录/分期/不做)、`docs/DEBUG-UI-MOE.md`(moe 单主题规格)、`docs/WEB-UI.md`(设计基线 = classic 主题)
- 源码:`agent_os/src/agent_os/host/web/static/js/themes.js`(契约清单/注册表/切换层)、`.../js/components/mascot.js`(MascotLayer 与 MASCOTS 注册表)、`.../css/themes/{classic,moe,terminal,blueprint,ink,pixel}.css`、`.../js/copy/{classic,moe,terminal,blueprint,ink,pixel}.js`
- 测试:`agent_os/src/agent_os/host/web/static/tests/themes-contract.test.mjs`(契约五组断言)、`.../tests/smoke-theme.test.mjs`(运行时 12 组场景)
- 相关章节:第 00 章 §6.3(执行摘要中的定位)

> 资料矛盾附注(以代码为准):① `DEBUG-UI-MOE.md` §3 的色板初值(奶油底 `#fdf6f0`、`--ok: #5ec9a7` 等)已被 T1.2 sakura 重构替换,`moe.css:13` 注明"原 beige 奶油底 #fdf6f0 系退役",实际值为 `--bg-0: #fff5f7`、`--ok: #1b7355`(压明度保 4.5:1);② `DEBUG-UI-THEMES.md` §2.1 的文字清单未列 `--line-strong`/`--text-2xs`/`--s1-5`/`--r-conn`,而 `CONTRACT_TOKENS`(`themes.js:25-40`)包含它们——契约变量以代码清单为准,共 43 个。
