# Agent OS Web UI 重设计方案

> 版本:v0.2(新增:运行发起与实时进度、Skills / Tools 浏览器;后端补充三个只读端点)
> 对象:`agent_os/host/web/static/` 前端(RUNNERS.md §4 的 Web UI Runner 之表现层)
> 约束:维持"无构建、无框架依赖"的非目标(§7);后端仅做 §6.2 的三个小增补

---

## 1. 现状诊断

当前 `index.html`(R3 交付)是"功能正确、体验缺位"的工程师草稿,按 UI/UX 系统标准逐项打分:

| 维度 | 现状 | 问题 |
|---|---|---|
| 信息层级 | 三区平铺,无主次 | RCA 动线(定位→检查→归因)没有视觉引导;一切信息同等重要 = 一切都不重要 |
| 视觉系统 | 无设计 token,颜色随手写 | 状态色与信号色不成体系,色板不可复用、不可扩展 |
| 帧树 | 纯文本缩进 | 无树形引导线、无状态图标、无展开折叠;深度 >3 即不可读 |
| 时间线 | 无差别列表 | 信号无分组(应按 step 分组)、无图标语义、无与帧树的选中联动 |
| 上下文检视 | JSON dumped | 消息不按角色渲染;tool_call/tool_result 不成对;reasoning 占满屏幕;无法复制 |
| 反馈状态 | 无 | 无 loading/empty/error 三态;SSE 连接状态不可见;操作无确认与结果反馈 |
| 能力覆盖 | 只读历史 | **不能发起 run、没有实时进度视图;Skills/Tools 只存在于配置文件,UI 不可见** |

## 2. 设计目标与原则

**用户与场景**:开发者做 RCA(主)、运行观察与发起(次)、技能/工具开发自检(次)。四类典型会话:

1. 打开 run → 找异常 → 看模型当时看到了什么 → 归因;
2. 改完 skills.yaml → reload → **发起一次 run** → **实时盯着它跑** → 出结果;
3. 写新技能前,翻 Skills 浏览器看现有技能的描述/权限/依赖;
4. 排查工具行为时,翻 Tools 浏览器确认参数 schema 与权限等级。

四条原则:

1. **RCA 动线即信息架构**:一切布局服务于"定位 → 检查 → 归因"。首屏直接回答:成了没?哪错了?为什么?
2. **状态先于装饰**:这是状态机可视化工具,状态编码(颜色/图标/动效)必须一致可预期;**进行中的 run 是一等公民**,live 状态有专门的视觉语言。
3. **密度与可读性兼得**:密度靠层级与折叠管理,不靠缩小字号。
4. **零仪式成本**:无构建、无框架、无登录;打开即用,深链接可分享;**回放历史与发起新运行是同一个界面**,不割裂。

## 3. 设计系统(Design Tokens)

单文件 `css/tokens.css`,全部样式只消费 token,不写散值。

### 3.1 色板(dark-first)

```css
/* 基底 */
--bg-0: #0b0e14;   /* 应用底色 */
--bg-1: #11151d;   /* 面板 */
--bg-2: #171d29;   /* 悬浮/选中 */
--bg-3: #1f2837;   /* 悬停 */
--line: #263043;   /* 分割线 */
--fg-0: #e6ebf2;   /* 主文本 */
--fg-1: #9aa7ba;   /* 次文本 */
--fg-2: #5d6b82;   /* 弱化/占位 */

/* 语义:run/frame 状态 */
--ok:      #3fb68b;   /* done / success */
--warn:    #d9a03f;   /* paused / degraded */
--danger:  #e5534b;   /* failed / veto / error */
--aborted: #9e6bde;   /* aborted(独立一色,中止≠失败) */
--live:    #3b9eff;   /* running / live 指示(带脉冲动效) */

/* 语义:信号类型 */
--sig-llm:      #6f9fff;
--sig-tool:     #3fb68b;
--sig-sidecar:  #d9a03f;
--sig-compress: #8b7cf6;
--sig-budget:   #e5534b;
--sig-frame:    #5d6b82;

/* 语义:工具权限等级 */
--perm-read:  #5d6b82;
--perm-write: #d9a03f;
--perm-net:   #6f9fff;
--perm-exec:  #e5534b;
```

约束:语义色与文本色对比度 ≥ 4.5:1(WCAG AA);状态永远"颜色 + 图标/文字"双编码。

### 3.2 字体与排版

```css
--font-ui:  system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
--font-mono: "SF Mono", "JetBrains Mono", Consolas, monospace;

--text-xs: 11px;  /* 元信息、时间戳 */
--text-sm: 12.5px;/* 列表正文、表格 */
--text-md: 14px;  /* 检视器正文 */
--text-lg: 16px;  /* 面板标题 */
--text-xl: 20px;  /* 页头 */

--s1:4px; --s2:8px; --s3:12px; --s4:16px; --s6:24px; --s8:32px;
--r-sm:4px; --r-md:8px; --r-lg:12px;
```

### 3.3 组件原语

`StatusPill`(状态徽标)、`IconBadge`(信号类型字母章)、`Chip`(元信息)、`TreeRow`、`Card`、`Banner`、`CopyBtn`、`Tooltip`、`Toast`、`Skeleton`、`PermBadge`(权限等级徽标,§4.6)、`ProgressBar`(steps/cost 双轨,§4.3)、`SchemaView`(JSON Schema 可读化,§4.6/4.7)、`Modal`。

## 4. 信息架构与布局

### 4.1 应用壳(App Shell)

```
┌──────────────────────────────────────────────────────────────────┐
│ ▎Agent OS │ Runs  Skills  Tools │      ● live │ + New Run │ ⚙  │ ← TopBar(48px)
├──────────────┬───────────────────────────────────────────────────┤
│ Run 列表      │  主视图(Runs Workbench / Skills / Tools)          │
│ (280px,可折叠)│  (随顶部导航切换)                                 │
│  ├ 搜索/筛选  │                                                   │
│  ├ ● fib done │                                                   │
│  ├ ● fib fail │                                                   │
│  └ …          │                                                   │
└──────────────┴───────────────────────────────────────────────────┘
```

- TopBar:品牌、**三个导航页(Runs / Skills / Tools)**、live 连接指示、**主操作 `+ New Run`**(全局唯一主色按钮);
- 侧栏仅 Runs 页显示:搜索 + 状态筛选 chips + 列表项(技能名 + StatusPill + 相对时间 + cost);
- 深链接:`#/runs/<run_id>?frame=<fid>&signal=<i>`、`#/skills/<name>`、`#/tools/<name>`——刷新/分享不丢现场。

### 4.2 Run Workbench(运行详情,核心页)

```
┌─ Banner(仅异常 run)────────────────────────────────────────────┐
│ ✖ aborted — MaxDepthExceeded · [定位首个错误 ⌘J] [Resume ▶]      │
├─ 进度条(仅进行中 run)──────────────────────────────────────────┤
│ ● live  已用 12.4s │ steps 8/200 ▓▓░░ │ cost $0.31/$2.00 ▓░░     │
├──────────────┬───────────────────────┬──────────────────────────┤
│ 帧树          │ 信号时间线             │ 上下文检视器              │
│ (320px)      │ (flex)                │ (420px,可拖宽)            │
│ ┌─●fib─────┐ │ ▶ step 3 ────────     │ ┌ system ──────────┐     │
│ │▾ 6 steps·│ │  L llm.response       │ │ 你是菲波拉契…     │     │
│ │ ┌●fib───┐│ │  T python_exec ✓      │ └──────────────────┘     │
│ │ │运行中·││ │  S ToolGuard ✖ veto   │ ┌ assistant ────────┐    │
│ └──────────┘ │    "rm -rf 被禁止"     │ │ tool_call #1      │    │
│              │  …                    │ │ python_exec(…) ⎘  │    │
│              │                       │ ├ tool_result ✖ ────┤    │
│              │                       │ │ vetoed: 禁止危险…  │    │
│              │                       │ └──────────────────┘     │
├──────────────┴───────────────────────┴──────────────────────────┤
│ Usage 按帧 ▾(可折叠底栏)                                        │
└──────────────────────────────────────────────────────────────────┘
```

帧树为**嵌套跨度块**:每帧一个圆角块递归嵌套,左侧 3px 状态 accent(done/running 脉冲/failed/aborted);
块头单行 = chevron + 状态点 + skill 名 + kind chip + 右侧 metadata(`6 steps · 128 tok · $0.02 · 1.4s`,
无值字段省略,窄块按 cost→duration→tok→chip→steps 逐级丢弃,完整串在 title);兄弟块间 1px 连接线
(末子块只到自身);选中块 --bg-2 + accent 加粗 4px + --live 边框;depth>3 默认折叠,折叠块显示 +N 子帧计数。

三个联动规则(整页灵魂):

1. **选中联动**:帧树选中帧 → 时间线过滤到该帧、检视器显示该帧上下文;时间线选中信号 → 帧树定位所属帧、检视器滚动到对应消息;三者共享一个 selection store。
2. **时间线按 step 分组**:每组 = 一次 loop 迭代,组可折叠;异常信号自动展开 + 左侧红条;**进行中 run 的信号流自动跟随滚动**(用户上翻则暂停,出现"回到底部"悬浮钮)。
3. **消息成对渲染**:assistant 的 tool_call 与其 tool_result 渲染为同一张卡片的两半;reasoning 默认折叠一行;veto/纠偏以 Banner 嵌入消息流。

### 4.3 运行发起与实时进度(Launch & Live)

**发起(`+ New Run` → Modal,不打断当前页):**

```
┌─ New Run ──────────────────────────────────────┐
│ Skill      [ fib            ▾]  ← 下拉(来自 /api/skills,带描述悬浮提示)
│ Input      { "n": 5 }        ← JSON 编辑器(mono,按该技能 inputs schema 实时校验,
│                                红线下标错误行;合法才允许 Run)
│ 高级 ▾     model [继承配置 ▾]  max_cost [2.00]  max_steps [200]
│                                   [Cancel]  [Run ▶]
└────────────────────────────────────────────────┘
```

- Skill 下拉即选即显示该技能的 description(路由规则)与 inputs schema;**Run 按钮按下即关闭 Modal 并跳转 `#/runs/<new_id>`**,无缝进入 live 视图;
- 高级区覆盖 RunConfig 的 model/max_cost/max_steps(POST body 的 `overrides`)。

**Live 视图(进行中 run 的 Workbench 变体):**

- **进度条区**(Workbench 顶部,仅 running 时显示):live 脉冲点、已用时长(秒级走动)、steps 与 cost 双 ProgressBar(占 max_steps/max_cost 比例;超 80% 转 `--warn` 色,与 BudgetGuard 阈值语义一致);
- **帧树实时生长**:新帧以滑入动画出现,running 帧显示旋转指示(不止文本"运行中");
- **时间线自动跟随**(见 §4.2 规则 2);**Stop 按钮常驻进度条右侧**(确认后调 `/stop`,按钮转 loading → Toast 结果);
- 结束瞬间:进度条区替换为结果 Banner(done 绿 / failed 红 / aborted 紫),live 指示熄灭,SSE 收到 `event: end` 后停止追加。

### 4.4 RCA 模式

异常 run 打开即进入:顶部 Banner(status + error 摘要 + "定位首个错误"/"Resume");一键定位 = 调 `/rca` → 帧树选中 → 时间线滚动 → 检视器展开出错卡片(红色高亮 + 脉冲一次);veto 卡片显示裁决 sidecar、理由全文、被否决参数 JSON(可折叠)。

### 4.5 Usage 视图

底部可折叠栏:按帧表格(frame/skill/depth/steps/prompt/completion/cache_read/cache_write/thinking/cost),列排序;cost 列内联条形(CSS 宽度条);cache_read 与 cache_write 分色;合计行固定底部。

### 4.6 Skills 浏览器

```
┌─ Skills ────────────────────────────────────────────────────────┐
│ 搜索…                                        [↻ Reload]        │
├──────────────┬──────────────────────────────────────────────────┤
│ 列表          │ 详情:local:fib@1.0.0                  [Run ▶]    │
│  fib          │ ┌ Meta ────────────────────────────────────────┐ │
│   v1.0.0 code │ │ kind: prompt  version: 1.0.0                 │ │
│  summarize    │ │ description(路由规则全文)                   │ │
│   v1.0.0      │ ├ Inputs ──────────────────────────────────────┤ │
│  web_research │ │ n: integer, minimum 1 (required)             │ │
│               │ ├ Outputs ─────────────────────────────────────┤ │
│               │ │ seq: array<integer>                          │ │
│               │ ├ Permissions ─────────────────────────────────┤ │
│               │ │ tools: [python_exec]  skills: [fib]          │ │
│               │ │ blackboard: [status]                         │ │
│               │ ├ Model / Limits ──────────────────────────────┤ │
│               │ │ prefer: mock/fib   max_steps: 8  timeout: 60 │ │
│               │ ├ Prompt 模板 ─────────────────────────────────┤ │
│               │ │ (mono 只读,关键字高亮,可复制)               │ │
│               │ └──────────────────────────────────────────────┘ │
└──────────────┴──────────────────────────────────────────────────┘
```

- 数据源:`GET /api/skills`(列表)+ `GET /api/skills/{name}`(全量 manifest 含 prompt 模板);
- 详情分区:Meta(kind/version/namespace)、**description 按"路由规则"渲染**(Use when / Do not use when 分行高亮)、Inputs/Outputs(`SchemaView`:JSON Schema → 可读字段表,类型/必填/约束)、Permissions(工具/子技能/黑板三组,权限等级继承显示)、Model & Limits、Prompt 模板(mono 只读 + 复制);
- **依赖关系**:permissions.skills 渲染为可点击链接(跳到目标技能),循环依赖在列表项上以 `--warn` 色警示;
- 操作:`↻ Reload`(调 `/api/skills/reload`,Toast 显示 reloaded true/false 与 mtime);`Run ▶`(带该技能预填打开 New Run Modal);
- 校验提示:description lint 结果(过短/缺触发条件)在详情顶部以 `--warn` 横幅显示——技能作者的自检入口。

### 4.7 Tools 浏览器

```
┌─ Tools ─────────────────────────────────────────────────────────┐
│ 搜索…  权限筛选:全部 READ WRITE NET EXEC                        │
├──────────────┬──────────────────────────────────────────────────┤
│ 列表          │ 详情:python_exec                    [EXEC]      │
│  fs_read      │ ┌ Spec ────────────────────────────────────────┐ │
│   READ        │ │ description("Use when … Do not use when …")  │ │
│  fs_edit      │ ├ Parameters ──────────────────────────────────┤ │
│   WRITE       │ │ code: string (required)                      │ │
│  shell_exec   │ │ timeout: 10  idempotent: false               │ │
│   EXEC        │ │ concurrency_safe: false  untrusted: false    │ │
│  http_fetch   │ ├ Examples ────────────────────────────────────┤ │
│   NET         │ │ (ToolSpec.examples,逐条 mono 卡片)           │ │
│  python_exec  │ ├ 来源 ────────────────────────────────────────┤ │
│   EXEC        │ │ builtin / registry entry point               │ │
│               │ └──────────────────────────────────────────────┘ │
└──────────────┴──────────────────────────────────────────────────┘
```

- 数据源:`GET /api/tools`(注册表全量 ToolSpec);
- 详情分区:Spec(description)、Parameters(`SchemaView`)、执行属性(timeout/idempotent/concurrency_safe/cacheable/untrusted_source 以 chip 显示)、Examples(有则逐条)、来源(builtin/entry point);
- **权限等级是首要视觉信息**:列表与详情头部均显示 `PermBadge`(§3.1 四级色),EXEC 级附"危险操作,受 sidecar/人工闸门约束"提示行——这是把内核三层权限模型(§8.2)在 UI 上的投影;
- 不做(非目标):从 UI 直接试运行工具(写操作风险面大;需要时后续单独立项,经 HumanApproval 路径)。

## 5. 交互与状态规范

- **三态齐全**:每面板 loading(Skeleton)/ empty(引导语,Skills 空时提示"在 agent-os.toml 配置 skills.path")/ error(Toast + 面板内重试);
- **live 指示**:SSE 连接中 = TopBar 蓝点脉冲;断开 = 黄点 + "已断开,点击重连";进行中的 run 列表项也有 mini 脉冲(不打开页面也能看到它在跑);
- **键盘**:`j/k` 上/下一条信号,`gg/G` 首/尾,`/` 搜索,`⌘J` 定位首个错误,`⌘K` 命令条(New Run / Stop / Resume / Reload / 跳 Skills / 跳 Tools),`?` 快捷键面板;
- **操作反馈**:stop/resume/reload 均为"按钮 loading → Toast 结果";stop 需确认(不可逆提示);New Run 的校验错误**内联**(编辑器行号 + 底部错误条),不弹 Toast;
- **时间格式**:相对时间 + title 绝对时间;live 时长秒级更新;
- **复制**:run_id、skill manifest、tool spec、帧上下文、单条消息、错误全文,全部一键复制。

## 6. 技术方案

### 6.1 结构(无构建,ES Modules)

```
static/
├── index.html
├── css/
│   ├── tokens.css      # §3 全部 token
│   └── app.css         # 布局与组件(只消费 token)
└── js/
    ├── app.js          # 入口:hash 路由(runs/skills/tools)、selection store、SSE 管理
    ├── api.js          # REST/SSE 封装
    ├── store.js        # 极简响应式 store(pub/sub,<40 行)
    └── components/
        ├── status-pill.js   tree-row.js      timeline.js
        ├── message-card.js  usage-panel.js   banner.js
        ├── launch-dialog.js progress-bar.js  perm-badge.js
        ├── schema-view.js   skills-view.js   tools-view.js
```

无框架(纯函数组件 + 事件委托)、无 CDN(离线可用)、图标用字母章 + 少量内联 SVG。

### 6.2 后端补充端点(三个只读小端点,host 侧)

| 端点 | 数据 | 说明 |
|---|---|---|
| `GET /api/skills` | 共享 registry 的 manifest 摘要列表(name/version/kind/description/permissions) | RunManager 已持共享 registry(R4 reload 路径),直接读 |
| `GET /api/skills/{name}` | 全量 manifest(含 prompt 模板、lint 警告) | 同上 |
| `GET /api/tools` | 共享 tools registry 的全量 ToolSpec | 装配时留一份引用即可 |

`POST /api/runs` 已支持 `overrides`(model/max_cost/max_steps,§4.3 高级区);如 R3 实现未含,一并补上。

### 6.3 性能与边界

- 时间线 >500 条窗口化渲染;SSE 增量 append;帧树 >100 默认折叠 depth>3;
- 大消息 >20k 字符折叠 head+tail;Skills/Tools 列表 >200 同样窗口化;
- 打印样式(RCA 页可打印贴 issue)。

### 6.4 可访问性(基线)

键盘可达 + 可见 focus 环;状态双编码;`prefers-reduced-motion` 时关闭 live 脉冲与滑入动画。

## 7. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **D1 设计系统 + 应用壳** | tokens.css、TopBar(三导航 + New Run 主按钮)、Runs 侧栏(搜索/筛选/StatusPill)、深链接路由 | 换肤只改 token;三态齐全;URL 可分享 |
| **D2 Workbench 三联动** | 帧树、step 分组时间线、消息卡片(成对/折叠/复制)、selection store | 三点联动;fib(5) 历史 run 可读性评审通过 |
| **D3 Launch & Live** | New Run Modal(schema 实时校验/高级覆盖)、Live 进度条(steps/cost 双轨)、帧树实时生长、Stop 流、结束态切换 | 从发起到盯完一次 fib(4) 全程不离开页面;Stop 一次循环 run 可中止 |
| **D4 Skills & Tools 浏览器** | 三个后端端点 + 两个浏览页(SchemaView/PermBadge/依赖链接/lint 横幅/Reload/Run ▶ 预填) | 技能 lint 警告可见;EXEC 级权限标识清晰;从技能详情一键发起 run |
| **D5 RCA + Usage + 打磨** | 异常 Banner、一键定位、veto 归因卡片、Usage 折叠栏、快捷键全套、长列表窗口化、打印 | 三类失败(veto/循环/断电)一键定位 ≤1 秒;键盘完成全部动线 |

测试方式:组件手测 + 里程碑 checklist;后端三端点补 API 契约测试(pytest);联动/校验逻辑写成纯函数尽量可测。

## 8. 非目标

登录/多用户、亮色主题、移动端、图表库、框架迁移、国际化、**从 UI 试运行工具**(写操作风险面,后续单独立项走 HumanApproval 路径)、技能/工具的在线编辑(编辑器是另一个产品,不在此壳内)。
