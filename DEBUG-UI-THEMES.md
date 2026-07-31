# Web 调试台主题系统 · 策划案

> 版本:v0.1(策划)
> 对象:Debugger P4 调试台(`#/debug`)先行,架构按**全站可推广**设计
> 关系:`WEB-UI.md` 定义设计基线(= 内置主题 `classic`);`DEBUG-UI-MOE.md` 降格为主题目录中的一个主题

---

## 1. 为什么做主题系统而不是一次性皮肤

萌系策划(DEBUG-UI-MOE.md §7)已经把差异收敛成三件事:**token 映射 + 文案表 + mascot/动效层**。
既然差异是可枚举的,就把它正式化为**主题契约**:主题是可插拔的数据包,组件代码永远只有一份。

收益:
- 调试台是 Agent OS 的演示面——不同场合(工程评审/对外 demo/个人把玩)需要不同气质,切换零成本;
- 主题契约本身就是设计系统的**可测试边界**:每个主题过同一组契约测试,皮肤再多也不漂移;
- 社区/用户以后可以按契约贡献主题,不动组件代码。

## 2. 架构:三层契约

```
┌─────────────────────────────────────────────────┐
│ 组件层(debug-view 等)  只消费语义 token/文案键/动效名 │
├─────────────────────────────────────────────────┤
│ 主题契约(Themes Contract)                          │
│  · 语义 token 全集(WEB-UI.md §3 的既有变量)        │
│  · 文案键(copy key)清单                            │
│  · 动效名(motion name)清单                         │
├─────────────────────────────────────────────────┤
│ 主题包(themes/<id>/) = tokens.css + copy.js +      │
│   (可选)motion profile + (可选)mascot 层            │
└─────────────────────────────────────────────────┘
```

### 2.1 语义 token 契约(必须全量定义)

每个主题必须给下列**既有**变量全部赋值(即 WEB-UI.md §3 的全集,一个不能少):

- 基底:`--bg-0..3` `--line` `--fg-0..2`
- 状态:`--ok` `--warn` `--danger` `--aborted` `--live`
- 信号:`--sig-llm/tool/sidecar/compress/budget/frame`
- 权限:`--perm-read/write/net/exec`
- 排版:`--font-ui` `--font-mono` `--text-xs/sm/md/lg/xl` `--s1..s8` `--r-sm/md/lg`

加载时注册表校验完整性,缺变量 → 主题不注册(防半成品主题上线)。

### 2.2 文案表契约

`js/copy/<theme>.js` 导出同一份 key 清单(状态短语、空态、确认、暂停/恢复提示)。
规则继承萌系策划:**技术文本豁免**——错误原文、状态原文、工具参数永远可直读,
主题文案是"翻译层";classic 的文案表就是现状文案的抽离,行为不变。

### 2.3 动效档案(motion profile)

组件只调用具名动效(`motion.play("bp-hit" | "step" | "resume" | "run-done" | "intervene")`),
主题给每个名字一个实现档位:`full`(主题特色动画)/ `subtle`(变色淡入)/ `instant`(即时切换)。
`prefers-reduced-motion` 强制 `instant`,对所有主题生效。

### 2.4 mascot 层(可选)

主题是"气质包",mascot 只是可选资产:实现为**可整体关闭的独立层**
(`MascotLayer`,挂在控制条与暂停点),主题声明 `mascot: "mochi" | "sprite8" | null`。
null 主题下该层不渲染,组件无任何分支判断。

### 2.5 切换与持久化

- `<html data-theme="<id>">` 全局生效;TopBar 主题选择器(下拉,每项带三色 swatch 预览);
- 持久化:localStorage + URL 参数同步(`#/debug/<sid>?theme=moe`),深链接可分享"同款气质";
- 注册表:`js/themes.js` 声明式清单 `{id, name, css, copy, motion, mascot, scope}`,新增主题 = 加一个目录 + 一行注册。

## 3. 主题目录(v1 六个)

### 3.1 `classic` — 严肃工程(默认)

现状即主题。深色底、信息密度优先、零 mascot、文案简technical(`paused @ pre:tool.call`)。
动效全 `subtle`。它是契约的**参考实现**:新主题先回答"和 classic 差在哪"。

### 3.2 `moe` — 萌系(详见 DEBUG-UI-MOE.md)

奶油底 pastel、圆角加大、Mochi 精灵状态化身、文案翻译层(技术原文并列保留)。
动效 `full` 仅五组状态转移。M1 随 P4 交付。

### 3.3 `terminal` — 终端极客

- **气质**:90 年代主机房。磷光绿(`#33ff66`)on 黑(`#0a0f0a`),全 mono 字体,扫描线纹理(CSS repeating-gradient,2% 透明度),可选琥珀色变体(`--variant=amber`)。
- **组件映射**:面板边框是 ASCII 双线框(`═║` 字符边框,真字符不是图片);断点是行首 `●`;暂停行反色显示(黑绿互换)而不是改底色;调用栈缩进用 `└─ ├─` 树线。
- **文案**:shell 腔(`bp set tool fs_*`、`run halted @ pre:tool.call`、`resumed`);空态 `no bps. set one.`。
- **动效**:`step` = 暂停行打字机逐字出现;`bp-hit` = 光标块闪烁一次;CRT 不闪(伤眼,放弃)。
- **mascot**:无。气场不需要。

### 3.4 `blueprint` — 蓝图

- **气质**:工程图纸。蓝图蓝底(`#1a3a6b`)+ 白/青细线,背景 24px 方格网(CSS gradient),面板是虚线边框"图框",角落有"图签"(session id/时间戳排成制图签栏)。
- **组件映射**:断点是红色虚线圆圈(图纸标注圈);调用栈是剖面层级标注(左标尺刻度线);轨迹行高亮用"云线"(修订云,圆角波浪描边)而不是填色。
- **文案**:制图腔,克制不卖萌(`MARK @ pre:tool.call`、`REV A`、`APPROVED`);状态用**图章**风格(倾斜 8° 描边红章 `DONE`/`HALT`)。
- **动效**:`bp-hit` = 暂停行描边自绘(stroke-dashoffset 动画);其余 `subtle`。
- **mascot**:无。

### 3.5 `ink` — 水墨

- **气质**:宣纸+墨+一枚朱砂。纸白底(`#faf7f0`)+ 墨色文本(`#2b2b2b`),**全主题只有一个强调色**:朱砂(`#c0392b`),只给暂停点与图章。衬线 CJK(`"Noto Serif SC", serif` 回退链),留白比 classic 大一号(s 系列 ×1.25)。
- **组件映射**:状态不是彩色 pill,是**印章**(方形朱砂章,白文:`成`/`止`/`误`/`行`);断点是小红点(句读);暂停行无底色,改左侧一道墨线(书法竖划,粗细渐隐)。
- **文案**:极简文言腔(`驻于 pre:tool.call`、`未下句读`、`墨尽`(done))——技术原文照常并列。
- **动效**:全部墨色晕染式淡入(300ms,只动 opacity);印章"盖下"一次(轻微缩放+透明度,似盖章)。
- **mascot**:无。禅不需要团子。

### 3.6 `pixel` — 像素复古游戏

- **气质**:8-bit 冒险。深色底+饱和 8-bit 色板(红白机色系),`image-rendering: pixelated`,边框是 2px 阶梯像素边(CSS box-shadow 拼),字体用像素体回退链(`"Press Start 2P"` 不内嵌,回退 mono 小号)。
- **组件映射**(信息映射最贴的一组):
  - **预算条 = HP/MP**:steps 是 HP 绿条,cost 是 MP 蓝条,临近上限变黄闪——比 classic 的 ProgressBar 直觉;
  - 断点 = 小旗子( checkpoint flag);命中 = 旗子升起 + "CHECKPOINT!" 横幅一次;
  - 帧栈 = 地牢层数(`B1 fib n=4 / B2 fib n=3`),当前层亮;
  - done = `LEVEL CLEAR!` + 金币音效位(音效默认关,见不做清单)。
- **文案**:游戏腔(`PAUSED — PRESS ▶`、`+1 STEP`、`GAME OVER: <error 原文>`)。
- **动效**:`step` = 角色跳一格;`run-done` = 金币蹦出一次。
- **mascot**:`sprite8`(8-bit 小勇者,4 帧差分:走/停/摔/庆祝),与 Mochi 共用 MascotLayer 接口——**它是 mascot 抽象的第二实例,证明层是可换的**。

## 4. 契约测试(每个主题都必须过)

`static/tests/themes-contract.test.mjs`,遍历注册表逐主题断言:

1. **token 完整性**:契约清单每个变量在 `[data-theme]` 下都有定义且非空;
2. **对比度**:状态色/文本色关键配对 ≥ 4.5:1(程序化计算,主题作者改色即时反馈);
3. **双编码**:渲染 fixture 后,每个状态元素同时带颜色与文字/图标(不依赖色觉单通道);
4. **文案键完整**:copy 表覆盖全部 key,且技术占位(`{error}` 等)未被翻译吞掉;
5. **动效降级**:`reduced-motion` 下所有具名动效解析为 `instant`;
6. **组件无分支**:静态扫描组件代码,禁止出现主题 id 字符串(差异必须走契约层)。

## 5. 分期

| 期 | 内容 |
|---|---|
| T1(随 P4) | 契约 + 注册表 + 切换器 + `classic` 抽离(行为零变化)+ `moe` M1 + 契约测试骨架(§4 的 1/2/3) |
| T2 | `terminal` + `pixel`(一动一静,验证动效档案与 mascot 抽象)+ 契约测试补全(4/5/6) |
| T3 | `blueprint` + `ink`(两个克制系,验证"无 mascot 主题"路径)+ 文案表全量 |
| T4(可选) | 推广到 Runs/Skills/Tools 页(每页逐主题验收后才开放 `scope`) |

每个主题的 `scope` 字段声明它验收过哪些页面;未验收页面强制回落 `classic`——**宁可回落,不半成品**。

> 现状(T1.1):moe 已放开 `scope: "app-wide"`。依据是组件零分支架构——全站
> 组件只消费语义 token,主题 css 又经契约全集校验,样式不存在"缺一块"的可能;
> 各页面的观感走查改为随用随修(发现不搭的组合直接改 moe.css,不回收 scope)。
> scope 回落机制保留,供未来半成品主题使用。

## 6. 不做

- 不做用户自定义主题编辑器(v1 主题 = 代码贡献;编辑器是另一个产品);
- 不做音效系统(pixel 留了挂点,但浏览器自动播放策略 + 办公场景,默认静音);
- 不做暗色萌系/亮色终端等"主题的变体"——变体用 `--variant` 子档(terminal 的 amber 是唯一示例),不繁殖;
- 不把 mascot 做成 AI 助手(它是状态化身,不是另一个 agent)。
