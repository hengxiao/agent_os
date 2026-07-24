# Agent OS Web UI 重设计方案

> 版本:v0.1(设计稿)
> 对象:`agent_os/host/web/static/` 前端(RUNNERS.md §4 的 Web UI Runner 之表现层)
> 约束:维持"无构建、无框架依赖"的非目标(§7);后端 API 不变(R3/R4 已交付)

---

## 1. 现状诊断

当前 `index.html`(R3 交付)是"功能正确、体验缺位"的工程师草稿,按 UI/UX 系统标准逐项打分:

| 维度 | 现状 | 问题 |
|---|---|---|
| 信息层级 | 三区平铺,无主次 | 用户的 RCA 动线(定位→检查→归因)没有视觉引导;一切信息同等重要 = 一切都不重要 |
| 视觉系统 | 无设计 token,颜色随手写 | 状态色(完成/失败/中止)与信号色(llm/tool/sidecar)不成体系,色板不可复用、不可扩展 |
| 帧树 | 纯文本缩进 | 无树形引导线、无状态图标、无展开折叠;深度 >3 即不可读 |
| 时间线 | 无差别列表 | 信号无分组(应按 step 分组)、无图标语义、无与帧树的选中联动 |
| 上下文检视 | JSON  dumped | 消息不按角色渲染;tool_call/tool_result 不成对;reasoning 占满屏幕;无法复制 |
| 反馈状态 | 无 | 无 loading/empty/error 三态;SSE 连接状态不可见;操作(stop/resume)无确认与结果反馈 |
| 响应式 | 固定像素 | 窗口变窄即崩;无法深链接分享某个 run/帧 |

## 2. 设计目标与原则

**用户与场景**:开发者做 RCA(主)与运行观察(辅)。一次典型会话 = 打开 run → 找异常 → 看模型当时看到了什么 → 归因(sidecar?工具?提示词?)。

四条原则:

1. **RCA 动线即信息架构**:一切布局服务于"定位 → 检查 → 归因"三步。首屏必须直接回答三个问题:成了没?哪错了?为什么?
2. **状态先于装饰**:这是一个状态机可视化工具(run/frame/signal 的状态),状态编码(颜色/图标/动效)必须一致且可预期。
3. **密度与可读性兼得**:开发者要的是高密度,但密度靠**层级与折叠**管理,不靠缩小字号。
4. **零仪式成本**:无构建、无框架、无登录;打开即用,深链接可分享。

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
```

约束:语义色与文本色对比度 ≥ 4.5:1(WCAG AA);状态永远"颜色 + 图标/文字"双编码,不单独依赖色相。

### 3.2 字体与排版

```css
--font-ui:  system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
--font-mono: "SF Mono", "JetBrains Mono", Consolas, monospace;

/* 字阶(2px 基线网格) */
--text-xs: 11px;  /* 元信息、时间戳 */
--text-sm: 12.5px;/* 列表正文、表格 */
--text-md: 14px;  /* 检视器正文 */
--text-lg: 16px;  /* 面板标题 */
--text-xl: 20px;  /* 页头 */

/* 间距阶(4px 基) */ --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s6:24px; --s8:32px;
/* 圆角 */ --r-sm:4px; --r-md:8px; --r-lg:12px;
```

### 3.3 组件原语

`StatusPill`(状态徽标:色点+文字,六种状态)、`IconBadge`(信号类型图标:字母章 L/T/S/C/B/F)、`Chip`(帧元信息:steps/cost)、`TreeRow`(树行)、`Card`(面板)、`Banner`(RCA/错误横幅)、`CopyBtn`(一键复制)、`Tooltip`、`Toast`(操作反馈)、`Skeleton`(加载骨架)。

## 4. 信息架构与布局

### 4.1 应用壳(App Shell)

```
┌──────────────────────────────────────────────────────────────────┐
│ ▎Agent OS   runs / <run_id>            ● live   ⎘ token   ⚙     │ ← TopBar(48px)
├──────────────┬───────────────────────────────────────────────────┤
│ Run 列表      │  Run Workbench                                     │
│ (280px,可折叠)│                                                    │
│  ├ 搜索/筛选  │                                                    │
│  ├ ● fib  done│                                                    │
│  ├ ● fib  fail│                                                    │
│  └ …          │                                                    │
└──────────────┴───────────────────────────────────────────────────┘
```

- TopBar:品牌、面包屑(run_id 可点击复制)、live 连接指示(SSE 状态)、全局操作;
- 侧栏:搜索框 + 状态筛选 chips(全部/失败/中止/进行中);列表项 = 技能名 + StatusPill + 相对时间 + cost;
- 深链接:`#/runs/<run_id>?frame=<fid>&signal=<i>`——刷新/分享不丢现场。

### 4.2 Run Workbench(核心页)

```
┌─ Banner(仅异常 run 显示)──────────────────────────────────────┐
│ ✖ aborted — MaxDepthExceeded · [定位首个错误 ⌘J] [Resume ▶]     │
├──────────────┬───────────────────────┬──────────────────────────┤
│ 帧树          │ 信号时间线             │ 上下文检视器              │
│ (260px)      │ (flex)                │ (420px,可拖宽)            │
│              │                       │                          │
│ ▾ f1 fib     │ ▶ step 3 ────────     │ ┌ system ──────────┐     │
│   3s 12t     │  L llm.response       │ │ 你是菲波拉契…     │     │
│ ▾ f2 fib     │  T python_exec ✓      │ └──────────────────┘     │
│   1s  4t     │  S ToolGuard ✖ veto   │ ┌ assistant ────────┐    │
│   └ f3 …     │    "rm -rf 被禁止"     │ │ tool_call #1      │    │
│              │  …                    │ │ python_exec(…) ⎘  │    │
│              │                       │ ├ tool_result ✖ ────┤    │
│              │                       │ │ vetoed: 禁止危险…  │    │
│              │                       │ └──────────────────┘     │
├──────────────┴───────────────────────┴──────────────────────────┤
│ Usage 按帧 ▾(可折叠底栏,表格)                                   │
└──────────────────────────────────────────────────────────────────┘
```

三个联动规则(这是整页的灵魂):

1. **选中联动**:帧树选中帧 → 时间线过滤到该帧、检视器显示该帧上下文;时间线选中信号 → 帧树定位到所属帧、检视器滚动到该信号对应的消息;三者共享一个 selection store。
2. **时间线按 step 分组**:每组 = 一次 loop 迭代(llm.request → llm.response → 若干 tool.call → step 结束),组可折叠;异常信号(veto/失败)自动展开且左侧红色边条。
3. **消息成对渲染**:assistant 的 tool_call 与其 tool_result 渲染为**同一张卡片的两半**(契约的配对原子性在 UI 上的投影);reasoning 默认折叠为一行摘要;veto/纠偏消息以 Banner 形式嵌入消息流。

### 4.3 RCA 模式

不是独立页面,是 Workbench 的一个**状态**:异常 run 打开即进入。

- 顶部 Banner(status + error 摘要 + 两个主按钮:"定位首个错误"、"Resume");
- "定位首个错误"= 调 `/rca` → 帧树选中该帧 → 时间线滚动到对应信号 → 检视器展开出错的那张 tool 卡片(红色高亮 + 脉冲一次);动线一步到位,无需用户自己找;
- 归因标注:veto 卡片显示裁决来源(sidecar 名)、理由全文、被否决的参数 JSON(可折叠)。

### 4.4 Usage 视图

底部可折叠栏(默认收起):按帧表格(frame/skill/depth/steps/prompt/completion/cache_read/cache_write/thinking/cost)+ 每列排序;cost 列附内联条形(CSS 宽度条,不引图表库);合计行固定底部。cache_read 与 cache_write 分色(缓存命中是省钱证据,要看得见)。

## 5. 交互与状态规范

- **三态齐全**:每面板有 loading(Skeleton)、empty(插画位+一句引导,如"还没有 run,用 CLI 或上方按钮跑一个")、error(Toast + 面板内重试按钮);
- **live 指示**:SSE 连接中 = TopBar 蓝点脉冲;断开 = 黄点 + "已断开,点击重连";进行中的 run 时间线自动跟随滚动(用户上翻则暂停跟随,出现"回到底部"悬浮钮);
- **键盘**:`j/k` 上/下一条信号,`gg/G` 首/尾,`/` 聚焦搜索,`⌘J` 定位首个错误,`⌘K` 命令条(跑新 run/stop/resume/reload skills),`?` 快捷键面板;
- **操作反馈**:stop/resume/reload 均为"按钮 loading → Toast 结果";stop 需确认(不可逆性提示);
- **时间格式**:相对时间(3m ago)+ title 绝对时间;持续时间 `1.2s`/`3m40s`;
- **复制**:run_id、帧上下文(JSON)、单条消息内容、错误全文,全部一键复制(开发者要贴给 coding agent)。

## 6. 技术方案

### 6.1 结构(无构建,ES Modules)

```
static/
├── index.html          # 壳:<script type="module" src="./js/app.js">
├── css/
│   ├── tokens.css      # §3 全部 token
│   └── app.css         # 布局与组件(只消费 token)
└── js/
    ├── app.js          # 入口:路由(hash)、selection store、SSE 管理
    ├── api.js          # REST/SSE 封装
    ├── store.js        # 极简响应式 store(pub/sub,<40 行)
    └── components/
        ├── status-pill.js  tree-row.js   timeline.js
        ├── message-card.js usage-panel.js banner.js
        └── (每个组件 = 一个 render(state) → HTMLElement 纯函数)
```

- 无框架:组件即纯函数 + 事件委托;状态集中在 `store.js`(selection/runList/runDetail/sseStatus 四片);
- 无依赖:不引任何 CDN(离线可用);图标用单字符徽标(L/T/S/C/B/F)+ 少量内联 SVG;
- 后端零改动:现有 `/api/runs`、`/signals`、`/frames/{id}`、`/usage`、`/rca`、`/stream` 全部够用。

### 6.2 性能与边界

- 时间线虚拟化:>500 条只渲染视窗 ±50 条(简单窗口化,不引库);SSE 增量 append 不整树重绘;
- 大消息:单条消息 >20k 字符默认折叠为 head+tail(与内核 spill 语义呼应),点击展开;
- 帧树 >100 帧:默认折叠 depth>3,搜索可定位;
- 打印样式:RCA 页可打印(开发者贴 issue 用)。

### 6.3 可访问性(基线)

- 全部交互元素键盘可达、可见 focus 环(`--live` 色 2px outline);
- 状态双编码(色+图标/文字),色盲可辨;
- 动效遵守 `prefers-reduced-motion`(live 脉冲可关)。

## 7. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **D1 设计系统 + 应用壳** | tokens.css、TopBar、侧栏(搜索/筛选/StatusPill)、深链接路由 | 换肤只改 token;列表三态齐全;URL 可分享 |
| **D2 Workbench 三联动** | 帧树组件、按 step 分组的时间线、消息卡片(成对渲染/reasoning 折叠/复制)、selection store | 三点联动;fib(5) 全过程可读性评审通过 |
| **D3 RCA + Usage** | 异常 Banner、一键定位动线、veto 归因卡片、Usage 折叠栏(排序/内联条形) | veto/循环/断电三类 run 一键定位 ≤1 秒;usage 按帧成本可见 |
| **D4 打磨** | 快捷键全套、live 指示与断线重连、长列表窗口化、三态与打印 | 键盘完成全部 RCA 动线;>500 信号流畅 |

测试方式:组件级继续手测(§4.5);联动逻辑(selection store、分组、窗口化)写成纯函数,补进 pytest 之外的 node-free 校验不可行——以 `tests/` 的 API 契约测试 + 手动验收清单为准(每个里程碑一份 checklist)。

## 8. 非目标

登录/多用户、主题切换(只做 dark)、移动端适配、图表库引入、框架迁移(React/Vue)、国际化(文案中文优先,token 化以便将来)。
