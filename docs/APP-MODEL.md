# App 化 UI 模型(App-Model UI)技术文档

> 版本:v0.5(§17 按核心原则复核代码后重写;v0.3 = Shell as App;
>   v0.2 = 三态 exec / state 服务端权威,见 §12)
> 对象:web_platform 的下一代 UI 架构——把 UI 看作一个操作系统,
>   对话 app 只是它的终端(PowerShell/cmd)。
> **核心原则(用户给定,统摄全文)**:**所有 widget 都有 context,所有 action
>   都是 skill;perform an action = 将合适的 context 给与对应的 skill 去执行。**
>
> **版本关系要读懂,否则会看到自相矛盾的两处**:
> - **v0.2**(§4/§7/§12)按一次独立评审引入了**三态 exec**
>   (endpoint / run / local),理由是"action=skill 则仲裁自动覆盖"当时为假;
> - **v0.5**(§17)按核心原则复核后判定:那次评审的**实证没错、方向反了**
>   ——正确的对齐是**把宿主函数升格为真 skill**,而不是把抽象降级为三态。
>   同时更正了 v0.2 的一处**成本误判**(code 技能不进 agent loop,无 LLM
>   开销,§17.1)。
> - **因此 §4/§7 的三态 exec 是过渡态,不是终态**;`exec.mode` 升格后降级为
>   元信息(只表示要不要起产物级 Run),执行一律经 `kernel.run()`。
>   两处冲突时**以 §17 为准**。
>
> 关系:概念承接 `AGENTIC-UI.md` 方案 A;实现基础是 web_platform 现状;
>   `WEB-PLATFORM.md` §7 的红线(不开新通道、没有第二条 promote 路径、
>   升级动作仍过人)在升格后**逐条仍然成立**——升格不是绕开闸门的新通道。
> 一句话:**每个 app 两张面孔;每个 action 都是一次 skill 调用,
>   context 由框架逐级备齐后交给它。**

---

## 1. 概念模型:五个一阶概念

### 1.1 类比表(与 Windows 对照)

| Windows | App-Model UI | 说明 |
|---|---|---|
| PowerShell / cmd | **对话 app** | 最基本的 app:意图进、动作出;其他 app 都由它孵化 |
| 程序(被 start 启动) | **App** | 一阶实体:技能包、运行、检查报告、计划…… |
| 程序主窗口 | **Tab Surface(全面)** | app 的完整界面:详细信息 + 操作区 |
| 任务栏小窗口/通知 | **Card Surface(卡面)** | app 的摘要界面:可嵌入对话或其他 app 内部 |
| Win32 API 调用 | **Action → skill 调用** | 一切后台操作(点击/提交)都经 Agent OS 的 skill 执行 |
| 进程状态 | **App State** | app 实例的数据面:输入、选择、上下文;action 的参数来源 |
| 窗口管理器 | **Compositor(左栏 tab 条)** | app 的打开/聚焦/关闭;对话 app 是默认首窗 |

### 1.2 不变量

1. **每个 app 必须有两张面孔**:Tab Surface(详情+操作)与 Card Surface(摘要+少量关键动作)。没有"只有卡片的东西"或"只有页面的东西"。
2. **每个改变外部世界的动作,都走在一条明说的仲裁通道里**(三态 exec,§4 表)——没有第四条通道;纯 UI 动作(pin/close/focus)不改变世界,不需要也不应该走仲裁。
3. **app.state 服务端权威**:state 存服务端、可序列化(刷新/重启可恢复);客户端只发事件不发状态;DOM 只是 state 的渲染。
4. **卡面可以嵌进任何容器**:对话消息、其他 app 的 tab、列表行——同一渲染函数,不同宿主。
5. **对话 app 没有特权**:它只是第一个被打开的 app;任何 app 都能孵化其他 app(技能的 run app 可以孵化调试 app)。

## 2. App 协议(契约)

```jsonc
// AppManifest(注册表静态面;类比 exe 的 PE 头)
{
  "kind": "skill-pack",                 // app 类型(注册表键)
  "v": 1,
  "title": "{name}",                    // 模板,数据驱动
  "surfaces": {
    "card": "skill-pack.card.js",       // 卡面渲染(摘要 + 关键动作 ≤2 个)
    "tab": "skill-pack.tab.js"          // 全面渲染(详情 + 全部动作)
  },
  "state_schema": { "type": "object" }, // app.state 的 JSON Schema
  "actions": [
    {
      "id": "publish",
      "label": "发布",                  // copy key,六主题
      "skill": "lab.pkg.promote",       // 被调 skill(Agent OS 一等公民)
      "args_from": ["state.plan_id", "state.warnings_ack"],
      "surface": ["card", "tab"],       // 在两张面孔上都出现
      "confirm": "summary"              // 确认语义(可选)
    }
  ]
}
```

**AppInstance**(运行态):

```jsonc
{
  "id": "app-7f3a",
  "kind": "skill-pack",
  "title": "晚餐推荐",
  "state": { /* state_schema 约束;持久化 */ },
  "created_by": "conv-12",              // 孵化者(对话 app 或另一个 app)
  "created_at": 1785789000
}
```

## 3. 两张面孔的纪律(吸收双层化)

- **Card Surface**:一句结论 + 至多两个动作。禁忌词纪律(18 词表)在这里执行——**卡面只说人话**;
- **Tab Surface**:完整数据 + 全部动作 + 原文/JSON/hash 都可以有;
- 两表面共用同一 `state` 与同一份数据,**渲染各自独立**——现在的 summaryHtml/cardHtml 就是它未泛化的雏形;
- 卡面动作与全面动作**同一 action id、同一 exec 通道**——卡面是全面的遥控器,不是另一套逻辑。

## 4. Action 管道:三态 exec

> **⚠ 本节是过渡态,终态见 §17。** v0.2 曾据此判定"三态"是正确模型;
> v0.5 按核心原则复核后推翻了那个方向——正确对齐是**把宿主函数升格为真
> skill**(§17.4 两级升格),`exec.mode` 随之降级为元信息(只表示要不要起
> 产物级 Run),执行一律经 `kernel.run()`。下表描述的是**当前实现**,
> 不是目标态;两者冲突以 §17 为准。

**当时的修正理由(v0.2)**:action 的执行方式不是单一的"skill 调用"——
认为那是错误抽象(promote 是 host 函数、pin/spawn 无世界副作用、工具分发
需要帧上下文)。当时给出的三态模型,每态授权面显式声明:

| exec | 语义 | 例子 | 授权面 |
|---|---|---|---|
| `endpoint` | 确定性写,转发既有端点(host 函数) | plan.confirm、rewind、promote | 白名单 + **人确认**(promote 的确认行就是它唯一的授权,不得当冗余删) |
| `run` | agentic,起 run 跑 skill | iterate.generate、scaffold | 内核全套(帧白名单/升权闸/数据闸) |
| `local` | 纯 state,不出海,不改变世界 | pin、spawn、close | 无(不需要) |

manifest 的 action 字段因此是:

```jsonc
{
  "id": "publish",
  "label": "发布",
  "exec": { "mode": "endpoint", "ref": "lab.pkg.promote" },  // endpoint→既有 handler 键;run→skill 名;local→无 ref
  "args_from": ["state.plan_id"],     // 服务端权威 state 绑定(客户端不可控)
  "args_input": ["warnings_ack"],     // 客户端事件载荷,须过 schema 校验——
                                      // "哪些参数用户能控"在 manifest 上一眼可见
  "surface": ["card", "tab"],
  "confirm": "summary"
}
```

管道:

```
用户点击(卡面或全面)
  → 前端只发事件(action_id + args_input 载荷,不发 state)
  → POST /platform/api/apps/{id}/actions/{action_id}
  → 服务端:manifest 裁决(action 存在/表面合法)→ args_input 过 schema
  → args_from 从服务端 state 绑定(客户端改不了)
  → 按 exec.mode 执行:endpoint→薄 handler(同 cards/action 现状)/
    run→起 run(产出 run app,持有 run_id)/local→仅改 state
  → 结果写回 state(服务端)+ 可选 spawn 新 instance
  → UI 增量刷新两张面孔
```

关键性质:

- **app.state 服务端权威**(§1.2-3):args_from 绑定的是服务端数据,防越权构造是真实成立的——因为它绑定的不经过客户端;`warnings_ack` 这类"人已阅读"凭据走 args_input,它是客户端**声明**而非证明,闸门语义仍由服务端 promote 复跑兜底;
- **长任务 = 一个 run,不是 state 标志位**(评审④):action 触发长任务时 spawn 一个 **run app** 并持有 `run_id`,直接复用既有 RunRecord/SSE/trace——app 的 running 态 = 持有 run_id 且 run 未终态,两机制不分叉;
- **升权请求只有一份权威清单**(评审⑤):supervisor 收件箱是权威存储(pending 落盘),app 卡面/对话决策卡都只是同一 pending 的**视图**——app 关闭不影响 pending 存活,重开/收件箱里仍在。

## 5. 对话 app(终端)的底层逻辑

### 5.1 职责

意图入口 + 默认孵化器。它的"卡面"嵌在会话列表,"全面"就是对话流本身。

### 5.2 消息泵(message pump)

```
send(text):
  1. 本地追加 user 消息(乐观渲染;state.outbox)
  2. POST /platform/api/sessions/{id}/messages
  3. 状态机: sending → waiting(agent 思考中骨架) → done | failed
  4. 响应 = agent 消息 {text, cards: [CardSurface 数据], spawn?: [AppInstance]}
  5. 渲染:文本气泡 + 每个 card 调对应 kind 的 card 渲染;
     spawn 的 app 在 Compositor 里注册(不自动聚焦)
  6. 持久化(会话可刷新恢复)
```

- **接收**:decisions 轮询 5s(现状;消息本体是请求-响应,无独立轮询);留 SSE 接口(state.transport);
- **互动**:卡面动作走 §4 action 管道;动作结果以 agent 消息回插对话(因果可见);
- **失败**:sending/waiting 可重试;failed 消息带重发按钮,不静默;
- **路由标注**:agent 消息带 meta(route: llm/rule),降体验可观测(N6 已落地此雏形)。

### 5.3 对话 app 的 actions

| action | exec | 说明 |
|---|---|---|
| send | run(platform.orchestrate) | 意图 → 编排(规则/LLM 路由) |
| spawn | local | 从卡/消息孵化新 app(不改变世界) |
| retry | run(platform.orchestrate) | 重发失败消息 |
| pin | local | 会话置顶(纯 state) |

> 注:v0.1 的不变量 2 被这张表证伪(spawn/pin 无 skill)——v0.2 已修正:
> 纯 UI 动作不需要仲裁通道;改变世界的动作(send/retry)走 run。

## 6. Compositor(窗口管理器)

- 左栏 tab 条 = taskbar:每个打开的 app 一项(图标 + title + 关闭);
- **打开**:卡面"打开"链接/动作 → 新 app 或聚焦已有(kind+ref 去重,已有实现);
- **焦点**:单激活模型(当前 tab 独占主区);对话 app 是默认回落;
- **关闭**:只是隐藏,不销毁 state(重开恢复);销毁是显式 action;
- **嵌套**:tab 内可再嵌卡面(技能包 tab 里嵌 run 卡面),层级 ≤2,防俄罗斯套娃;
- **布局**:窄屏 tab 条收成图标列。

## 7. 安全与信任面(v0.2 重写:授权面按 exec 态显式声明)

> **⚠ 本节的结论已被 §17 部分推翻。** v0.2 说"action=skill 则仲裁自动覆盖"
> 是错的——这个**实证仍然成立**(下面三条逐条为真)。但 v0.5 指出它证明的是
> "**当时**没做到",不是"做不到":把宿主函数升格为真 skill、并把副作用面拆成
> **tool**(§17.4 的 L1+L2 两级),仲裁覆盖就从话术变成结构。
> **本节读作"为什么不能只靠改名就宣称安全",而不是"所以 UI 要有自己的权限层"。**

v0.1 说"action = skill 调用 → 内核仲裁自动覆盖 UI 全部副作用,UI 不需要
自己的权限层"——**这个论断在当时是错的**,三处:

1. 招牌例子是反例:`lab.pkg.promote` 这个 skill 不存在;promote 是 host
   函数(`promote_package()`),经 `POST /api/lab/packages/promote`,**不走
   升权闸**——它的授权面是"web 单用户 principal + 人工确认行"。把 promote
   卡上的人确认当成冗余删掉,就会拆掉它目前唯一的授权。
2. 工具分发需要帧上下文(`ToolDispatchContext` 含帧 manifest 白名单):
   宿主直接调工具 = 自己造帧给自己授权,不是继承,是自欺。
3. 与 `WEB-PLATFORM.md` §7 红线("零新 promote 通道、升级动作仍过人")
   冲突。本节是它的显式化而非推翻:**授权面按 exec 态分别定义**:

| exec | 授权面(必须显式声明在 manifest 与代码评审中) |
|---|---|
| `endpoint` | ACTION_WHITELIST/AppManifest 服务端裁决 + **该动作固有的人工确认**(如 promote 的确认行、升权的人审)——继承自既有端点的语义,不新增、不削弱 |
| `run` | 内核全套:帧 manifest 白名单 ∩ 工具等级 ∩ RunConfig 上限 + 升权闸(低档进高档人审)+ 数据 authZ(principal 继承,不放大) |
| `local` | 无(不改变世界);只允许动 state |

- 任何 action 归错态(该 endpoint 的写成 run,或该有确认的写成 local)=
  授权漏洞,评审必须拦;manifest 里 exec 字段是强制项,缺省拒绝注册;
- 卡面人话/全面技术面的禁忌词纪律继续有效(信息分层不变);
- principal 透传:UI 操作以 web 单用户 principal 执行,审计信号可关联。

## 8. 从现状到 App 化(迁移地图)

| 现状(web_platform) | App 化 |
|---|---|
| 会话 | **对话 app 实例**(kind: conversation) |
| 六卡型(plan/skill_pack/gate_report/diff/publish/table) | 六个 app kind 的 **Card Surface** |
| 详情 tab(gate/pack/plan/run/diff/esc) | 对应 app kind 的 **Tab Surface** |
| cards/action 白名单 | **AppManifest.actions** + skill 绑定(泛化) |
| 左栏 tab 条 | **Compositor**(语义不变,概念升格) |
| 旧 web 五页 | 五个 legacy app(Skills/Runs/Tools/Lab/Debug)——以 tab surface 形态整体接入,不做改写 |

新增优先三个 app kind:`run`(运行详情/干预)、`debug`(调试台)、`lab-draft`(草稿编辑)——它们进 tab 条后,UI 的"操作系统"感才闭环:对话孵化包 → 包 tab 里跑 run → run 卡出失败 → 一键开 debug tab。

## 9. 测试模型

- **协议测试**:每个 app kind 的 manifest 合法(双表面存在、exec 三态合法且 ref 已注册、args_from 在 state_schema 内、args_input 有 schema);
- **授权测试(评审⑥,与功能测试并列一等)**:
  ① 越权构造被拒——伪造 action id / 越表面调用 / 篡改 args 来源(客户端伪装 args_from 的字段)一律 4xx;
  ② state 服务端权威——客户端提交篡改后的 state 字段(如 warnings_ack=true)不影响 args_from 绑定结果;args_input 不合 schema 被拒;
  ③ exec 归态——endpoint 动作不带 run 副作用、local 动作零出海(无网络调用断言);
- **action 管道**:点击 → 正确 exec 调用(参数绑定正确)→ state 回写 → 两表面重渲;
- **表面纪律**:卡面禁忌词扫描(沿用)+ 全面技术字段存在;
- **Compositor**:去重聚焦/关闭回落/嵌套层级 ≤2;
- **持久化**:app.state 序列化往返;刷新恢复;
- **回退**:无 manifest 的 kind 拒绝渲染(不炸)。

## 10. 分期

| 期 | 内容 |
|---|---|
| M1 ✅ | AppManifest 注册表 + action 管道(替代 cards/action)+ 对话 app 归一(现状卡型全部转成六个 app kind 的表面) |
| M2 ✅ | Compositor 正式化(关闭≠销毁/嵌套 ≤2/图标列)+ app.state 持久化 |
| M3 ✅ | run/debug/lab-draft 三个 app kind 接入(对话 → 包 → run → debug 闭环) |
| M3.5 ✅ | exec 三态同构迁移(manifest `skill`→`exec{mode,ref}` 强制 + args_input 通道 + 授权测试三件套;行为零变化) |
| M4 ✅ | legacy 五页以 tab surface 接入 + SSE transport + 主动汇报(app 状态推送进对话) |
| M5 ✅ | shell app 化(§13/§14/§15;v0.3) |

> **M5 增补实现注**(2026-08-04,分支 debugger;桌面化 root widget——Windows
> 桌面式界面;红线:**不做自由排布/浮动窗口**,窗口仍是单激活最大化 tab,
> 桌面是"无 tab 激活时的主屏"):
> - **桌面主区**(无激活 tab 时):`active_tab=""` 即桌面态(本地镜像
>   `state.active=""`);主区渲染图标网格(大图标 + 名称,首字符 glyph 与
>   tab 条图标列同手法,零新资产),数据源 = 对话(恒首)+ legacy 五应用 +
>   最近关闭前 3(M2 closedTabs),开始菜单同一份 `desktopIcons()`;
>   **壁纸** = 各主题 body 背景图案透出(moe 樱花/blueprint 方格/pixel 星空/
>   ink 宣纸/terminal 扫描线/classic 点阵,已有资产零新增)——桌面态
>   `body[data-desktop="1"]` 壳层透明,chrome(顶栏/任务栏)各自持底;
>   图标点击 = tab 条同一 action(**同源**):对话 → activateTab
>   (shell.tab.focus),legacy → openDetail(shell.tab.open,同 launcher),
>   最近关闭 → reopenTab(shell.tab.open,同"最近关闭"列表);
> - **任务栏**(左竖排 tab 条升格):顶部开始按钮(九宫格纯 CSS 点阵,点开
>   应用菜单,项与桌面图标同 data-desk-open 同源点击);中部运行中 tab
>   (激活高亮/✕/DnD 重排,不动);底部系统托盘——live 指示(SSE/轮询/断线
>   三态,色点 + 状态原文双编码,点击重连)、主题切换(注册表顺序循环,
>   与顶栏同一 applyTheme + shell.theme.set 通道)、收件箱(当前会话未决
>   escalation 计数,点击回对话);托盘项一律 role=button + aria-label;
> - **窗口标题栏**(激活 app 最大化):图标 + 标题 + [最小化 —] +
>   [✕ 关闭];最小化 = 新 local action `shell.tab.minimize`(active_tab 置
>   "",tab 全保留——最小化≠关闭),✕ 仍走 M5 closeDetail(关闭≠销毁进
>   最近关闭;关闭回落 conversation 语义不动);样式全 token 进 platform.css;
> - **shell.state 扩展**:`desktop: {pinned: [], wallpaper: true}`(state_schema
>   同步;pinned 键预留,本期无 UI 面);新 local action `shell.desktop.set`
>   (args_input {wallpaper: boolean})——壁纸开关写穿透持久化(重启恢复),
>   旧持久化无 desktop 键时前端/后端各自缺省容错(壁纸开);
> - **测试**:前端 `desktop.test.mjs`(桌面渲染/同源断言/开始菜单/最小化/
>   托盘/壁纸/icon_mode 不回归);后端 `test_m5_desktop_minimize_and_wallpaper`
>   (最小化 tab 保留/壁纸持久化重启恢复/args_input 非 bool 拒);M1-M5 既有
>   测试原样全绿(前端 26 文件 + 本新文件;后端 web_platform 68 个)。

> **M5 实现注**(2026-08-03,分支 debugger;v0.3 §13/§14/§15):
> - **shell 根 app**(apps.py +1 kind;与普通 manifest 同一协议校验,无特例):
>   bootstrap 实例化(ref="shell",conv 恒首),state={tabs, active_tab, theme,
>   sessions, layout{icon_mode}, widgets} 随 instance 写穿透持久化(重启恢复
>   布局);7 个 action 全进管道——tab.open/focus/close + layout.set/move_tab
>   = local(**local 语义精化**:state 服务端权威后,"仅改 state"恰恰要在
>   服务端执行,`_LOCAL_MUTATORS` 注册面;无 mutator 的 local 仍 400,
>   M3.5 语义不破),theme.set/session.create = endpoint(薄 handler);
> - **前端 tab 条消费 shell.state**:`/api/shell` 加载 + 镜像(state.tabs/
>   active 只是渲染缓存),一切 tab/布局/主题事件转发 shell action 回镜收敛;
>   `/api/shell` 不可达回落本地 tab 模型(M1-M4 行为,降级面);
> - **widget 寻址**(§14):tab/卡面带 data-reg-path 运行时路径;注册端点
>   (开 tab 即登记/关 tab 注销,随 shell.state.widgets 持久化);
>   `GET /api/widgets/<path>`(path:path 剥斜杠归一)+ read/focus 动词端点
>   (人话摘要,404 同源);focus 的前端执行 = 滚动 + pf-pulse 高亮脉冲
>   (reduced-motion 停用);**act 本期不过管道**——用户点击语义已通,
>   agent 的 act 权限收口留 M6;
> - **DnD v1**(§15):tab/卡面 draggable + envelope(source/source_kind 必填,
>   ref 扩展键);tab 条 dragover accept 校验(非本 mime 不高亮);两对标准
>   实现——tab→tab 条 = shell.layout.move_tab、卡面→tab 条 = 同一 openDetail
>   路径(同源断言);触屏降级 = 长按 600ms 出"移到最左/最右"(同一 action);
>   图标列手动开关 = shell.layout.set 镜像 body[data-icon-mode];
> - **send/retry 未归 run 通道**(M4b 留口,本期维持):消息泵是同步
>   请求-响应语义(骨架 → agent 消息),与"长任务 = spawn run app"的异步
>   产物不同构——归一需要把对话响应改成 run 跟踪面(等 run_id → 轮询/SSE
>   → 回填),改动面超出 M5 最小改动线,留待专项(同 agent act 一起进 M6 评估)。

> **M4b 实现注**(2026-08-03,分支 debugger;M4 的表面部分,与 M4a 合为 M4):
> - **legacy 五页**(apps.py +5 kind:skills/runs/tools/lab/debug-old,state
>   最小无动作):逐页评估后 skills/tools/lab/debug-home 四页**直接挂载**
>   (旧组件的 open*(main) 装配口,同 document 零隔离;切换经 close* 收编
>   防 store 订阅泄漏,`__legacyMounts` 注入是 node 测试的替代装配口);
>   runs 列表在旧 app.js 内无独立装配口 → **深链**(摘要行 + 旧 UI 链接 +
>   行内 run tab 直达);入口 = 侧栏 launcher(五页按钮,与普通 tab 同级,
>   关闭≠销毁语义沿用);
> - **SSE transport**:`GET /api/stream`(decision.new/run.finished 帧 +
>   keepalive,服务端 2s 巡检;帧语义在 `_stream_diff` 纯函数——TestClient
>   整读缓冲,无限流不可经其测试,实测后改 openapi 面 + 纯函数守);前端
>   EventSource 主通道,onerror 断线回落 5s 轮询、onopen 复活即停(替代不
>   双轨);EventSource 缺席(node/老浏览器)直接轮询;
> - **主动汇报**:`POST /api/sessions/{id}/runs/present`——本会话发起的 run
>   (经 instance.created_by 链回溯,限深防环)到终态 → agent 消息 + 结果卡
>   (人话摘要,N1 纪律)进会话;presented_runs 游标随会话落盘(幂等);
>   SSE run.finished 触发,轮询兜底;
> - **M5 留口**(shell app 化):对话 app 自身的 send/retry 归 run 态真通道、
>   pin 等 local 动作的 manifest 化执行,未动。

> **M4a 实现注**(2026-08-03,分支 debugger;M4 的引擎部分):
> - **run 真通道**(v0.2 §4):`run_iterate` 回传 run_id/run_status(additive);
>   diff manifest 新增 `iterate.generate`(exec.mode="run",args_input
>   {comments,note});管道 run 分支——handler 结果带 run_id 即 spawn run app
>   (state={run_id,skill,status}),响应附 `run_instance`,前端直接进 run tab;
>   running 态 = 持 run_id 且未终态,**不发明新标志位**;ad-hoc run(iterate
>   不走产物面)的 Tab Surface 在 API 404 时回落 instance state 渲染;
> - **发起面归一**:run manifest 新增 `run.launch`(exec.mode="run",
>   args_from state.skill,args_input {input});handler = schema 解析(overlay
>   草稿∪生产)→ input 缺省用 `skeleton_from_schema` 骨架/给了按 schema 校验
>   (不合 400)→ start_run → spawn run instance;Tab Surface 发起面 =
>   textarea(留空=骨架,可填 JSON 改参)+ 按钮;lab-draft 试跑同通道
>   (overlay 解析序草稿优先,未单独做按钮);
> - **spawn 校验**(M3.5 留口关闭):POST /api/apps/spawn 按 manifest 的
>   state_schema 校验初始 state(不合 → 400);各 kind schema 以现状卡 data
>   为准(additionalProperties 默认放行,声明键查类型);
> - **前端时序**:openDetail 的 spawn 改 await(tab.instance 确定后再渲染,
>   fallback 无竞态)。

> **M3.5 实现注**(2026-08-03,分支 debugger;v0.2 §4/§7/§9/§12):
> - **schema**(apps.py):`exec` 强制项(缺省拒绝注册),mode ∈
>   endpoint/run/local;endpoint/run 的 ref 须在绑定表;local 无 ref;
>   `normalize_exec` 迁移期把旧 `skill` 键归一为 exec.endpoint 并 warn
>   (一个版本期;测试里旧键 manifest 同时充当兼容证据);
> - **args_input 通道**:`{name: schema}` 形态(非法形态拒注册);
>   `validate_args_input` 逐项过 jsonschema——客户端载荷只许出现在
>   args_input(伪装 args_from 字段 → 400);warnings_ack 迁移入内并定性
>   "客户端声明,服务端 promote 复跑兜底"(§4);管道合并
>   `{**bound(服务端 state), **input(客户端声明)}` 调 handler;
> - **归态终版**:scaffold.approve/plan.recheck/plan.confirm/decision.answer/
>   version.rewind/run.stop/run.resume/run.rerun/debug.command/draft.check/
>   draft.promote = endpoint;iterate.generate = run(不在 manifest 上,
>   绑定表注释归态,真 run 通道归 M4);conversation 的 spawn/pin/close =
>   local(声明归态不出海,调到管道 → 400);
> - **授权测试三件套**(§9):①伪造 action id/越表面/伪装 args_from 字段
>   → 4xx;②args_from 绑定服务端 state(空 args 也是服务端值)+ args_input
>   不合 schema 被拒;③endpoint 无 run 副作用(runs_started 为空)、
>   local 零出海(manager 面零调用);
> - **行为零变化**:M1-M3 全部既有测试原样通过(同构迁移的验收)。

> **M3 实现注**(2026-08-03,分支 debugger):
> - **三 kind 注册**(apps.py):run(stop/resume/rerun)、debug(continue/stop,
>   command 由管道按 action id 末段注入,与 decision.answer 同模式)、
>   lab-draft(check/promote;**promote 仅 tab 面**——重动作不上卡面,§3
>   卡面 ≤2 轻动作的首次应用);
> - **handlers**(app.py):五个薄转发(stop_run/resume_run/start_run 按产物
>   meta 重跑/debug_command/validate_draft),async 经理同款 asyncio.run,
>   异常归类与旧 web 端点一致(409 不在途/404 无 checkpoint/404 无会话);
> - **Tab Surface**(前端 details.js):run 详情复用 + 状态动作按钮 + failed/
>   running 给"开调试"链接(POST 旧 web /api/debug/sessions replay 形态 →
>   debug tab);debug = 简化调试台(暂停点/帧栈/断点 + 放行/停止,改参/注入/
>   单步留旧调试台);lab-draft = 摘要 + 检查/提交 + 深链旧 Lab(不改 lab.js
>   的最简路径,精确编辑一律回专家模式);pack 详情草稿成员带"编辑"链接;
> - **tab 面动作**:data-tab-act + 当前 tab 的 spawn instance → 同一 action
>   管道(surface="tab"),动作后重渲当前 tab(活面),结果回插对话(因果可见);
> - **Card Surface**(cards.js):debugSummary(暂停点人话)/draftSummary
>   (名称+档+检查人话)——协议面先行,对话流产出点属 M4 主动汇报;
> - **闭环**(前端测试分段):run tab → 开调试(replay)→ debug tab → 放行 →
>   回插对话;包 tab → 草稿编辑;去重聚焦不重复 spawn 有断言。

> **M1 实现注**(2026-08-03,分支 debugger):
> - **文件**:`web_platform/apps.py`(manifest 校验 + AppRegistry +
>   AppInstanceStore + bind_args + default_manifests 八 kind:conversation +
>   六卡型 + escalation);`app.py`(SKILL_BINDINGS + 管道/spawn/GET instance
>   三端点 + 卡创建即登记 instance);前端 cards.js/app.js(按钮带
>   data-app-inst 寻址,有 instance 走新管道,无则旧管道)。
> - **instance 模型**:kind = 卡型,ref = 业务锚(草稿名/plan_id/question_id…),
>   state = 卡 data + 绑定便利键(plan 的 name/template、gate 的 root);
>   卡 dict 上带 `instance` id 持久化进会话;M1 内存态(instance store),
>   持久化归 M2。
> - **新旧管道过渡**:`POST /api/cards/action` 保留(旧持久化卡无 instance,
>   前端自动回落);两管道共享同一 handler 映射(`_run_handler` 统一异常归类),
>   M3 退役旧入口。manifest 的 action label 是 copy key(六主题已同步),
>   M1 前端仍用卡 JSON 自带 label,tab surface 从 manifest 渲染动作归 M2+。
>
> **M2 实现注**(2026-08-03,分支 debugger):
> - **持久化**:AppInstanceStore(root) 写穿透到 `<artifacts>/platform_apps/
>   <instance_id>.json`(单文件单 instance,与 SessionStore 同哲学);启动全量
>   加载重建 kind+ref 索引(去重跨重启成立),坏文件/非法文件名/形态不合
>   隔离,id 合法面 `app-[0-9a-f]{8}` 防穿越;conversation instance 同样持久;
> - **Compositor**:closeTab 返回 closed → state.closedTabs(上限 3,新关在前),
>   tab 条底部"最近关闭"小列表重开(同 tab id → 同 instance);嵌套层级经
>   `summaryHtml(card, depth)` 透传,depth ≥3 剥"打开"链接(对话流=1,
>   tab 内嵌=2;当前 tab surface 无嵌卡卡面,机制 + 单测先行);窄屏
>   <720px tab 条收图标列(首字符 + title 悬停全文);
> - **spawn 前端接入**:openDetail 按 `_APP_KIND` 映射 POST /api/apps/spawn
>   (fire-and-forget,失败不阻断展示),tab 带 instance 升格 Tab Surface;
>   run 属 M3 kind(映射 null)先内部分发,有测试守着不 spawn;
> - **销毁不做**(显式动作,本期无 UI 面;最近关闭只进不出,重启即清——
>   closedTabs 是前端内存态,不持久化)。

## 11. 不做

- 不做多窗口并存/自由排布(单激活 tab 已够;分屏是另一个产品);
- 不做用户自定义 app 编辑器(manifest 是代码贡献,与主题系统同政策);
- 不做前端直连业务端点的过渡形态(action 管道一步到位);
- 不把对话 app 做成唯一入口(它很重要,但没有特权)。

## 12. v0.2 修订记录(独立评审的裁决,2026-08-03)

评审三条主要意见及处置(全部接受):

1. **§7 安全论断方向性错误**(promote 非 skill、工具分发需帧上下文、与
   WEB-PLATFORM 红线冲突)→ §4 三态 exec 表 + §7 按态授权面重写;
   manifest 的 `skill` 字段改 `exec: {mode, ref}`,成为强制项;
   promote 的人确认明写为"唯一的授权,不得当冗余删"。
2. **state 权威侧未定义,args_from 防越权悬空** → §1.2-3 服务端权威;
   §4 拆 `args_from`(服务端绑定,客户端不可控)与 `args_input`(客户端
   载荷,schema 校验),"哪些参数用户能控"manifest 上一眼可见;
   warnings_ack 定性为"客户端声明,服务端 promote 复跑兜底"。
3. **不变量 2 被 §5.3 表证伪** → 措辞改为"改变外部世界的动作经受仲裁
   通道;纯 UI 动作不需要"(§1.2-2,§5.3 加注)。
4. **run 与 running 态两套机制** → §4"长任务 = spawn run app 持有
   run_id,复用 RunRecord/SSE/trace"。
5. **升权两个收件箱** → §4"supervisor 收件箱是唯一权威存储,app 卡面
   与对话决策卡都是视图"。
6. **§9 缺授权测试** → 新增授权测试三件套(与功能测试并列)。
7. **事实误差**(轮询 2s)→ §5.2 改"decisions 轮询 5s,消息无独立轮询"。

**对分期的影响(M3.5 插入)**:M1/M2 已按旧字段(`skill` 绑定薄 handler)
实现——实现行为恰好是 endpoint 态,模型落地只需**同构迁移**:manifest
schema 加 exec 字段(skill 键映射为 exec:{mode:"endpoint", ref}),
args_input 通道分离,授权测试补齐;行为零变化(现有测试应全绿)。
M3 的三个 kind 直接按 exec 模型注册。

## 13. Shell as App:递归到顶(v0.3)

**问题**:widget 套 widget,最外面那层(整个操作系统的界面)是不是一个
超级大 widget?它的操作(开 tab、关 tab、切主题、建会话)是不是也
应该用 action 定义?

**回答:是。** Shell 是 kind 为 `shell` 的根 app——唯一的特例是它由
bootstrap 实例化,不由任何 action 孵化(递归有底,没有壳的壳)。

### 13.1 shell app 的定义

- **state**:{tabs: [{instance_id, kind, title}], active_tab, theme,
  sessions, layout};持久化(重启恢复窗口布局,不是只恢复数据);
- **surfaces**:Card Surface = 任务栏/状态条(它嵌在何处?——若 shell 被
  嵌入,见 §13.3);Tab Surface = 整个屏幕(左 tab 条 + 主区);
- **actions**(同样走 exec 三态与 args_input 纪律):
  | action | exec | 说明 |
  |---|---|---|
  | `shell.tab.open` | local(登记) | 开 tab 或聚焦(kind+ref 去重) |
  | `shell.tab.focus` / `shell.tab.close` | local | 焦点/关闭(关闭≠销毁) |
  | `shell.theme.set` | endpoint | 切主题(持久化偏好) |
  | `shell.session.create` | endpoint | 新会话(app 孵化) |
  | `shell.layout.set` | local | 布局(图标列/宽度) |

### 13.2 收益:UI 成为 agent 可寻址的

shell 的操作一旦是 action,**agent 就能操作用户的界面**——这是"UI 操作
系统"这句话的完整含义:

- 助手说"我帮你打开调试 tab" → 它调 `shell.tab.open(debug)`(经管道,
  用户可见、可撤销)——界面协作从"替你做"扩展到"替你摆好";
- 测试可以像测业务一样测窗口管理(开/关/聚焦的授权与去重);
- shell 的 manifest 与用户 app 同一份协议校验——**没有特例代码路径**。

### 13.3 边界(诚实)

- **递归有底**:shell 由 bootstrap 实例化;它的初始 state 来自本地启动
  (服务端镜像随后建立);不存在"创建 shell 的 action";
- **不是每个像素都是 action**:渲染是渲染,action 只覆盖**改变世界/改变
  状态**的离散操作;键盘输入、滚动、hover 不进管道(性能与语义都不该);
- **嵌入壳中壳(§13.1 Card Surface 的存在意义)**:shell 的卡面允许
  整个环境被嵌进另一宿主(如把调试环境嵌进一张卡里)——这是可能性
  声明,v0.3 不实现;
- **agent 的 shell 权限要收口**:agent(非人)可调的 shell action 是
  子集(open/focus/reopen 类"摆台"动作),destroy/publish 类永远只对人
  ——沿用升权哲学:界面动作也有档位,高档只对人开放。

### 13.4 对分期的影响

- M4 不变(引擎与接入先行);**M5 = shell app 化**:state/布局持久化、
  shell actions 进 manifest 与管道、agent 可寻址子集、窗口布局重启恢复;
- 测试加一条:shell manifest 与普通 manifest 过同一协议校验(无特例)。

## 14. Widget 寻址:从 root 到每个 widget 的运行时 id(v0.3 增补)

**问题**:Shell as App 让 agent 能操作 app,但 app 内部的 widget(某张卡、
某个按钮、某段文本)还不可寻址——agent 无法说"看这张卡的第三个用例"
或"帮你按下这个按钮"。

**模型**:每个 widget 实例有一个**从 root 到自己的运行时地址**(路径 id),
由组合链构成,与 DOM 无关(重渲染不改变它):

```
/shell                                  # 根(shell app)
  /tab/<instance_id>                    # tab 条里的 app instance(M1 的 id)
    /surface/card                       # 该 app 的 Card Surface
    /surface/tab                        # Tab Surface
      /section/<name>                   # 逻辑区块(如 tests、findings、members)
      /field/<name>                     # 语义字段(如 description、prompt)
      /action/<action_id>               # 具体动作按钮(与 manifest action 同名)
/conv/<session_id>                      # 对话 app instance
  /msg/<n>                              # 第 n 条消息
    /card/<k>                           # 消息里的第 k 张卡
```

规则:

1. **逻辑键,不用位置索引**(除非无语义键):tab 用 instance_id、字段用
   名字、动作用 action_id;只有消息/卡这类纯序列才用序号;
   重渲染/重排后地址仍指向同一对象——**稳定性来自语义,不来自 DOM**;
2. **注册制**:各 Surface 渲染时把自己的区块/字段/动作登记进 shell 的
   widget registry(state 的一部分),卸载即注销;查询路径即查 registry,
   不查 DOM;
3. **agent 三个动词**:
   - `read(path)` → 该 widget 的人话摘要(文本/状态;禁忌词纪律沿用);
   - `focus(path)` → 把它带到用户眼前(滚动+高亮脉冲;"看这里"可寻址);
   - `act(path)` → 触发该 widget 的默认动作(等价于用户点击,走同一
     action 管道与 exec 归态——**代理点击不绕过授权**);
4. **权限沿用 §13.3 收口**:agent 的 `read/focus` 全开放,`act` 只对
   摆台类开放;destroy/publish/promote 类的 `act` 永远只对人(用户自己点);
5. **测试**:路径解析(存在/重渲染后仍解析/注销后 404)、read/focus/act
   三动词的授权面、agent act 触发与用户点击同源(同一管道断言)。

**对分期的影响**:与 M5 同批(shell app 化时 registry 随 state 落地);
§13.2 的"agent 可寻址"由此从 app 粒度细化到 widget 粒度。

## 15. Drag & Drop:drop 是目标 widget 的 action(v0.3 增补)

**原则**:拖拽是手势,**drop 是被拖入方(drop target)manifest 里的一个
标准 action**。职责一刀两断:**被 drag 的 widget 只负责提供标准化数据
(§15.1 envelope,产出即义务,不关心谁接收);被 drop to 的 widget 负责
接收与处理(声明 accept、执行 drop action,不关心数据怎么被拖来)**——
每个 widget 可拖,每个 widget 可声明自己接受什么 drop;输入全系统统一,
兼容性由 schema 保障,不由组件间约定。drop 的背后也是一次 exec 调用
(local/endpoint/run,按动作语义归态)。

### 15.1 标准化输入(envelope)

源 widget 在 `dragstart` 时提供,目标 widget 的 drop action 消费:

```jsonc
// application/x-agent-os-widget 的载荷(dataTransfer)
{
  "source": "/shell/tab/app-7f3a/surface/card",  // §14 路径,必填(语义随地址,不随 DOM)
  "source_kind": "skill_pack",                    // 源 kind,必填(目标快速裁决)
  "position": { "before": "/shell/tab/app-9z2b" } // 可选:插入位置(排序/嵌放)
}
```

任何 widget 只要按这个 envelope 产出/消费,**任意两 widget 之间的拖拽
都互通**——新 kind 上线不需要和既有组件逐一配对。

### 15.2 manifest 声明

```jsonc
// 目标 widget(shell 的 tab 条/卡面容器/任意 app)
{
  "drop": {
    "accept": ["skill_pack", "run"],        // 接受的 source_kind 清单(必填)
    "exec": { "mode": "local", "ref": "shell.tab.open" },  // 归态 + 绑定
    "label": "拖到此处打开"                  // copy key(高亮提示)
  }
}
```

- `accept` 不在清单 → 落点不高亮不接收(不静默吞);
- drop 调用 = 目标 manifest 的 drop.exec,参数 = envelope(经 args_input
  同款 schema 校验)——**拖拽只是另一种 action 触发,授权面不绕过**;
- 无 drop 声明的 widget 不可作为落点(防任意元素被拖进)。

### 15.3 v1 标准实现(两对互通)

| 源 → 目标 | 目标的 drop.exec | exec |
|---|---|---|
| tab(拖) → tab 条(重排) | `shell.layout.move_tab`(position.before 排序) | local |
| 卡面(拖) → tab 条(打开) | `shell.tab.open`(与点"打开"同一 action) | local(登记) |

### 15.4 纪律

- envelope 三键是**强制最小集**;扩展键(如 position)目标不识必须可
  忽略(向前兼容);
- 触屏降级:长按菜单 = 同一 drop action 的非手势触发(a11y);
- 测试:envelope schema 合规、accept 校验、两对标准实现与点击同源断言、
  未知 kind 源被拒不静默、扩展键可忽略。

## 16. Context Cascade:动作触发时的逐级上下文协议(v0.3 增补)
**问题**:widget 的动作常常需要不止自己的数据——一个挂在某段文本上的
chat bubble 要回答"帮我精简这段",需要**点击的 span、所在段落、全文、
所在 app 的状态**。这些分布在 widget 树的每一级上,触发点自己拿不全。

**模型**:动作触发时,runtime 从触发 widget **沿树向上逐级收集上下文**,
每层各贡献一个 fragment,组成级联信封发给 skill(经 action 管道):

```
widget.action 触发
  → runtime.context_cascade(trigger_path):
      每级(从近到远)调用该级的 context_provider() → fragment
      widget → section → surface → app → shell
  → envelope 随 action 参数发给 skill(exec: run/endpoint)
```

```jsonc
{
  "trigger": "/shell/tab/app-7f3a/surface/tab/field/prompt",
  "cascade": [
    { "scope": "widget",  "path": ".../field/prompt",
      "data": { "span": [120, 156], "paragraph": "你是晚餐推荐助手…", "full_text": "…" } },
    { "scope": "section", "path": ".../section/prompt",
      "data": { "member": "dinner.planner", "fields_summary": "description/inputs 已填" } },
    { "scope": "app",     "path": "/shell/tab/app-7f3a",
      "data": { "kind": "lab-draft", "ref": "dinner.planner", "tier": "none" } },
    { "scope": "shell",   "path": "/shell",
      "data": { "active_session": "conv-12", "theme": "classic" } }
  ]
}
```

### 16.1 纪律

1. **每级只贡献自己的 fragment**:widget 不知道 app 有什么,app 不替
   widget 说话——`context_provider` 是 manifest/控件声明的一部分
   (widget 协议加 `context: (state) => fragment`);
2. **级联单向向上,不横向打听**:fragment 只能来自祖先链(§14 路径
   的每一级前缀),同级/下级不可见——上下文边界 = 树边界,这也是
   权限边界(子帧看不到兄弟,沿用帧隔离哲学);
3. **action 声明需要的级数**:manifest 的 action 可加
   `context: ["widget", "app"]`(缺省全链)——轻动作不背大信封;
4. **原文进信封,纪律管呈现**:cascade 可以含全文/原文(给 skill 用),
   但显示给用户的摘要仍守禁忌词纪律(两个面不混);
5. **测试**:级联顺序(近→远)、每级只出自己 fragment(无横向)、
   action 级数裁剪、某级缺 provider 时跳过不炸、信封与 §14 路径一致。

### 16.2 首个消费者:chat bubble

chat bubble(见 docs/WIDGETS.md W-bubble)是本协议的首个落地:
气泡挂在任意 widget 上,submit 时 runtime 自动级联——span/段落/全文
来自 text widget,成员与草稿状态来自 app,助手回复因此**看着全文改
一段**,而不是看着一段猜全文。

> **实现注**(2026-08-03,W2 落地,分支 debugger):
> 运行时 = `widgets/cascade.js`(provider 注册制 + contextCascade 纯函数,
> provider 在自己的 prefix 级贡献);消费者 = lab-iterate 的边注气泡
> (`w-bubble.js` submit 事件携带 cascade,出海在父组件 POST
> `/api/lab/drafts/{name}/comment` → `skill.dev.commenter`,tools=[]
> 白名单收口);级联单向向上/级数裁剪/缺级缺席,均有 widgets.test.mjs
> 与 test_lab_comment.py 断言在案。

---

## 17. 原则对齐审计(v0.5;2026-08-05 复核代码后重写)

> 核心原则(用户给定):**所有 widget 都有 context,所有 action 都是 skill;
> perform an action = 将合适的 context 给与对应的 skill 去执行。**
> 本节 v0.4 是首轮审计,v0.5 逐条**对代码复核**后重写——三处判定偏乐观、
> 四处漏项、一处靠改术语达成的"符合",以及一条 v0.4 完全没写的关键结构
> (§17.4 升格是两级)。

### 17.1 与 v0.2 的张力,以及一次成本误判的更正

v0.2 评审证明"action=skill 调用则内核仲裁自动覆盖"在**当时**为假(promote
是 host 函数、工具分发需要帧上下文)。那次评审的实证没错,但结论方向反了:
**正确的对齐不是把抽象降级为三态 exec,而是把宿主函数升格为真 skill**,
让"所有 action 都是 skill"成为事实,仲裁覆盖才从话术变结构。

**同时更正一条成本误判**(v0.4 审计与评审都写错了)。曾以为"每个动作升格
= 一帧 + 一次 run + LLM",据此认为 `shell.theme.set` 这类动作开销与语义不成
比例。查 `kernel/runner.py:389` 的 `_execute_frame`:

```python
if skill_obj.manifest.kind is SkillKind.CODE:
    return await self._run_code_frame(frame, skill_obj)   # → Logic Kernel
return await self._frame_loop(frame, skill_obj)           # → agent loop(LLM)
```

**code 技能完全不进 `_frame_loop`**——没有 LLM 往返、没有 `context.build`、
没有压缩、没有 token 记账。真实开销 = 压帧/弹帧 + 4 个信号
(`pre/post:frame.*`、`pre/post:logic.exec`)+ outputs schema 校验。

而且 `kernel.run()` 是**进程内**的;run 产物(meta/trace/checkpoint/result)
是宿主 `run_manager.start_run` 才写——`run_iterate` 走的正是前者。**所以有
两档调用面**,不是每个动作都得起一个带产物的 Run。

结论:成本不构成反对理由,**§17.5 因此选 (b) 全量升格**。

### 17.2 真实符合度:0 / 36

`web_platform/apps.py` 现有 **36 个 action:23 endpoint + 11 local + 2 run**。

v0.4 读起来像"run 态那 2 个已符合"。但看分发实现,`app.py:1521` 的注释自陈
**`endpoint/run 当前同一薄 handler 面`**;而那张映射表虽名为 `SKILL_BINDINGS`,
值却是 Python 函数(`_act_scaffold_approve`、`_act_plan_confirm`…),生产
registry 里**没有任何 `platform.*` 技能**。

> **管道层面的真实符合度是 0/36。** 名字已按目标态起好(`platform.*`),
> 实现一个都没到位——这反而是好事:升格不用改名,但文档不能让人以为
> run 态已经符合。

### 17.3 逐项审计(v0.5 修正表)

| # | 现状 | 判定 | 处置 |
|---|---|---|---|
| 1 | **全部 36 个 action 都是 host Python**(不止 v0.4 列的 14 个;`run` 态那 2 个也是) | ❌ | L1+L2 升格(§17.4) |
| 2 | **11 个 local 是服务端 mutator**(shell.tab.open/close/move_tab、pin…) | ❌ | 升格为真 code skill(`tier: none`,无 tools),**不是**"叫 local skill 的 JS 闭包" |
| 3 | **widget actions 是裸 JS**(set_value/format/toggle…) | ❌ | 同 #2 |
| 4 | **cascade 的 provider 机制是死代码**:`registerContextProvider` 全仓**零调用点**;唯一消费者 lab-iterate 用 `cascadeProviders:` 按调用手搓传入 | ❌ | 比 v0.4 判的更弱——不是"按需注册",是**注册面从未启用**。registry 加缺省 provider,消费者改用注册制 |
| 5 | action 管道不带 cascade(args_from 只绑 state) | ❌ | 管道自动携带级联信封;`context: []` 显式弃权 |
| 6 | 对话 send/retry 是同步请求-响应 | ❌ | 升格为 orchestrate run |
| 7 | decisions 作答走专属端点,不过管道 | ❌ | 收编为 `escalation` app 的 action |
| 8 | **旧 web 的 27 个写端点整体在审计外** | ❌ **v0.4 漏项** | §8 迁移图以"legacy 五页不做改写"豁免——这是**规模最大的常驻偏离**。要么排进升格序,要么明确承认它是永久例外(那原则就有边界) |
| 9 | **调试干预 `inject` / `modify`** | ❌ **v0.4 漏项** | v0.4 只列了 `debug.command`。这两个直接改写在跑帧的参数/注入消息后放行,是**全系统特权最高的 UI 动作**,最该经帧白名单与升权闸 |
| 10 | **`/api/supervisor/{qid}/answer`(旧 web)** 与 platform 的 `decision.answer` 两条路径 | ❌ **v0.4 漏项** | 裁决作答有两个入口,收编时一并归一 |
| 11 | **`/api/skills/reload`、草稿 `DELETE`** | ❌ **v0.4 漏项** | 前者热重载生产 registry(影响其后所有 run),后者 `rmtree`(连版本史一起删,见 SKILL-PACKAGES-V2 §6.11)。都是"改变世界",都不在任何清单里 |
| 12 | 升权人审 / 干净 context / args_from 服务端权威 | ✅ | 保留;升格后由 skill 的 tier 与帧模型原生承担 |
| 13 | widget 零 fetch、事件上行 | ✅ | 保留;这正是"widget 有 context 但不执行 action"的控件侧表达 |

### 17.4 升格是两级 —— v0.4 只写了第一级

这是本次复核最重要的结构性发现。看 `_run_code_frame` 给 handler 的东西:

```python
ctx=KernelLogicContext(self, frame, manifest) if trusted else None
```

TRUSTED 档的 handler 是**进程内的任意 Python**。如果只是把 `_act_plan_confirm`
的函数体搬进 handler、里面照样直接 `promote_package(...)`,那么:

- ✅ 拿到:帧、推导档、被低档调用时的升权闸、`pre/post:frame.*` 与
  `logic.exec` 信号(→ trace/replay)、outputs 校验、timeout;
- ❌ **没拿到**:三层权限交集与数据 authZ——**那两道闸在工具分发上**,
  而这个 handler 根本没经 `ctx` 调工具,是直接 import 干活的。

结果是"**戴着 tier 徽章的宿主函数**"。§17.1 说升格让"仲裁从话术变结构",
这句话只有做完第二级才成立。

| 级 | 动作 | 拿到什么 | 适用面 |
|---|---|---|---|
| **L1** host 函数 → code skill | 机械搬运,便宜 | 帧 / 推导档 / 升权闸 / 信号 / trace / replay / outputs 校验 | **全部 36 个** |
| **L2** 特权操作本身 → **Tool** | 要拆:skill 只做编排,副作用落在 tool 上 | **三层权限交集 + 数据 authZ** | 只给真有副作用的那些 |

**L2 名单**(有真实世界副作用,必须拆出 tool):
`plan.confirm`(promote 写盘 + reload)、`candidate.accept`、`doc.save`、
`doc.apply`、`run.stop`/`resume`/`rerun`、`debug.modify`/`inject`、
草稿 `delete`、`skills.reload`。

以 `plan.confirm` 为例:L2 意味着 `promote_package` 的**写盘与 reload 变成
一个声明了 `permission: WRITE` / `side_effect: reversible` 的 tool**,skill
只负责取参数、调它、回写 state。这样"UI 副作用被内核仲裁"才是真的。

**11 个 local 只需要 L1**——它们本来就没有副作用要仲裁,升格的收益是
统一管道 + 进 trace(见 §17.5)。

### 17.5 裁决:走 (b),全量真 skill

v0.4 的 #2/#3 处置是把 local mutator 与 widget 裸 JS "**建模为 local skill**",
并注明"宿主内执行,**不进内核**"。**这不是符合原则,是重新定义术语让原则
为真**——若 "skill" 可以指一个不在 registry、无 manifest、无 tier、不进帧的
JS 闭包,那"所有 action 都是 skill"就成了不可证伪的同义反复,验收里的静态
扫描也退化成查命名规范。

曾提出的二选一:

- **(a)** 纯 UI 动作不算 action,退出概念,不叫 skill;
- **(b)** 它们是真 skill:进 registry、有 manifest、`tier: none`、在帧里执行。

**裁决:(b)。** 依据是 §17.1 更正后的成本实测——code 技能不进 agent loop,
开销只有压帧 + 4 信号;`kernel.run()` 还可走进程内不落产物。既然便宜,就
没有理由为了省开销把一类交互放到原则之外。

**(b) 的额外收益**:UI 动作进 trace,agent 因此**真的能看见用户在界面上做了
什么**,甚至能 replay——这正是 §13.2"UI 成为 agent 可寻址的"想要的,
(a) 给不了。

> 明确否决 **(c):叫它 skill 但不当 skill 做**。那是最坏的一种——原则看起来
> 达成了,实际什么约束都没多。

### 17.6 原则本身的一处未定义:读取算不算 action

"所有 widget 都有 context"——**context 从哪来?** 今天是 app 去 GET 拉数据。

- **算** → `platform.*` 还需一整套读 skill,GET 端点也要升格;
- **不算** → context 的**供给面在原则之外**,需单独定义谁负责把数据放进
  widget 的 context。

**这条不定,§17.7 第 3 步"registry 缺省 context_provider"没法落笔**——
不知道 provider 该从哪取数。建议裁决为"**读不算 action,但 context 供给必须
声明式**":widget 的 `context` 声明自己要什么,由框架(§17.9 的职责分工)
去取,widget 仍然零 fetch。

### 17.7 升格序

1. **`platform.*` 内置 skill 包(L1 全量)** ✅(2026-08-05,分支 debugger):
   36 条调用面(35 个 manifest action + 旧卡面 version.rewind)归并为
   **32 个唯一 code 技能**(共享 ref:decision.answer ×3 / debug.command ×2 /
   plan.recheck ×2 只计一次),落在 `skills/platform/` 包(包内 skills.yaml
   注册,全部 trusted 档);管道的 `exec.mode` 从"选执行通道"降级为
   **元信息**(local/run 只表示要不要起产物级 Run),调用一律经
   `kernel.run()`(进程内,不落产物——run_iterate 先例);
2. **L2 拆 tool** ✅(2026-08-05,分支 debugger):§17.4 名单的副作用面拆成
   7 个声明了 permission/side_effect/data_domains 的 tool
   (`skills/platform/tools.py`,注册进 platform 内核的 Tool Registry),
   skill 退为编排(handler 经 `ctx.call_tool` 调用——帧白名单 ∩ RunConfig
   上限 ∩ 工具自报档 + 数据层 authZ 进路径);**最高特权的三个旧 web 写面
   一并收编**(调试 modify/inject → `platform.debug.intervene`、草稿 delete
   → `platform.draft.delete`[全包唯一 irreversible]、skills.reload →
   `platform.skills.reload`),旧端点改为跑对应技能,行为逐字不变;
3. **cascade 上线** ✅(2026-08-05,分支 debugger):WidgetDef registry 加
   **缺省 `context_provider`**(kind + state 摘要,声明者可覆盖,非函数拒注册),
   widget 实例 `register()`/`destroy()` 自动挂/摘 cascade 运行时;
   消费者改注册制(W-bubble 的 doc-editor 与 lab-iterate 手搓
   `cascadeProviders:` 传入均退役,注销随 destroy,同位替换不堆叠);
   管道自动携带级联信封(`AppActionBody.cascade`,前端按 tab 路径经注册
   provider 组装,widget 零 fetch 信任边界不变),action 可声明
   `context: []` 显式弃权(`apply_action_cascade`);
4. **收编三条旁路** ✅(2026-08-05,分支 debugger):decisions 作答归管道
   (#7——前端一律经 escalation app 的 action,skill =
   `platform.decision.answer`;无 instance 的旧卡先 spawn 再走管道;
   404"已被处理"归类保留;专属端点 `POST /api/decisions/{qid}` 已删除,
   GET 列表是读面保留);旧 `POST /platform/api/cards/action` **实测仍有
   调用面**(M1 前持久化旧卡无 instance,前端 cardAction 旧路径在用)——
   按预定裁决**保留并标 deprecated**,不新增调用方;send/retry 归 run
   经评估**不做**(见实现注结论);
5. **旧 web 的 27 个写端点**(#8)✅(归类决策,2026-08-05):
   ~~`inject`/`modify`/`skills.reload`/草稿 `delete` 四个优先(#9/#11)~~
   已在第 2 步收编(L2 工具面);其余写端点的归类决策——**随 §8 迁移地图
   逐步收编,未迁移前承认是 legacy 例外**(原则有边界,边界就是 §8 的时间表;
   每收编一个旧页,其写端点随页退役或升格,不再单列排期);
6. **全程红线**:promote 的人确认、升权人审、数据 authZ **一行都不许因升格
   而削弱**——升格让仲裁从话术变结构,不是绕开它的新通道。

> **第 1 步实现注**(2026-08-05,分支 debugger,L1 全量 ✅):
> - **包**:`skills/platform/`(`__init__.py` + `skills.yaml` + `handlers.py`)。
>   32 个 code 技能(21 endpoint ref + 8 服务端 local + 3 个 conversation
>   前端本地动作的账面注册),全部 trusted 档、白名单空(tools/skills 皆
>   [])、逐技能 inputs/outputs schema;handler = 原 `_act_*`/`_mut_*`
>   的机械搬运,只做了三处适配:依赖从闭包改为 `ctx._kernel.platform_deps`
>   (宿主装配时注入,每 app 一颗 platform 内核);`HTTPException` 直抛改
>   `PlatformActionError(status, detail)`(异常过不了 Logic Kernel 执行边界,
>   `_guarded` 折 `_action_error` 信封,管道 `_run_handler` 原位翻译回
>   HTTPException——状态码/文案逐字一致);`asyncio.run(manager.*)` 改
>   直接 `await`(帧内不能再开私有 loop;`iterate.py` 因此拆出
>   `arun_iterate` async 本体,`run_iterate` 留作同步包装)。
> - **管道**(`web_platform/app.py`):endpoint/run/local 三分支统一走
>   `asyncio.run(platform_kernel.run(ref, payload))`;`exec.mode` 只剩两处
>   元信息用途(run 态 spawn run instance / local 态的"不出海"拒绝);
>   `SKILL_BINDINGS` 退役为 `platform_skill_names()`(AppRegistry 的
>   known_skills 数据源);`_LOCAL_MUTATORS` 退役为
>   `PLATFORM_LOCAL_SKILLS`(action id → 技能名);旧卡面
>   `_ACTION_HANDLERS` 退役为 `_CARD_ACTION_REFS`(七条,id → ref)。
> - **conversation 的 spawn/pin/close**:registry 有 `platform.act.*` 同名
>   技能(§17.5(b) 账面),但它们是前端本地动作——管道侧仍 400"local
>   不出海"(M3.5 语义,行为零变化优先)。
> - **force_sandbox 豁免(§17.9)**:`LogicPolicy` 新增
>   `trusted_builtin_prefixes: tuple = ("platform.",)`(显式白名单,清空即
>   无豁免);`LogicKernelRouter.route` 命中白名单的内置技能在
>   force_sandbox 下仍走 TRUSTED 进程内,用户技能照 SANDBOX;技能自报
>   `logic.mode=sandbox` 优先于豁免。
> - **L2 候选标注**(§17.4 名单,本期不拆):plan.confirm / candidate.accept
>   / doc.save / doc.apply / run.stop / run.resume / run.rerun 的 handler
>   注释已标"直接 import 业务写函数,L2 拆 tool"。
> - **验收**:§17.8 静态扫描(每个 action 的 ref 解析到真实技能、无孤
>   handler)+ force_sandbox 豁免矩阵 + kernel.run 进程内冒烟,落在
>   `tests/web_platform/test_platform_skills.py`;行为零变化由既有
>   web_platform 全套(92)与全量 pytest 背书。
>
> **第 2 步实现注**(2026-08-05,分支 debugger,L2 拆 tool ✅):
> - **skill → tool 映射**(7 件,`skills/platform/tools.py`;全部 WRITE):
>   plan.confirm→`platform.skill.promote`(reversible,skills.* 域)、
>   candidate.accept→`platform.draft.accept`(reversible,drafts.*)、
>   doc.save/doc.apply→`platform.doc.write`(reversible,docs.*+drafts.*;
>   整文/按段双模)、run.stop/resume/rerun→`platform.run.control`
>   (reversible,runs.*;stop/resume 改进程态、rerun 新建不动旧产物,
>   均不构成数据销毁;resume 阻塞到 run 终态,timeout 对齐 max_wall_time)、
>   debug.modify/inject→`platform.debug.intervene`(reversible,runs.*;
>   §17.3 #9 最高特权面)、草稿 delete→`platform.draft.delete`
>   (**irreversible**——rmtree 连版本史;删除确认在 UI,技能/工具层
>   无 approve-run 式放行面)、skills.reload→`platform.skills.reload`
>   (reversible,skills.*)。
> - **错误通道**:异常过不了 Logic Kernel/Tool 分发两个边界——tool 侧
>   `_domain_guard` 把可归类域异常折成结构化 ToolResult(invalid_args→400
>   /not_found→404/vetoed→409),handler 侧 `_tool()` 原位翻译回
>   PlatformActionError,`_guarded` 折信封,管道/端点翻译回 HTTPException;
>   状态码与文案逐字不变。ProviderError(503)不在 tool 面(七件都不碰 LLM),
>   留在 handler 的 `_guarded`。
> - **白名单生效**:7 个既有技能 manifest 的 `permissions.tools` 声明各自
>   tool(帧白名单交集才放行);新收编的 3 个技能同理。
> - **旧 web 收编**(`web/app.py`):POST /api/skills/reload、DELETE
>   /api/lab/drafts/{name}、POST /api/debug/sessions/{sid}/modify|inject
>   四个端点改为跑对应 platform.* 技能(本 app 自装配一颗 platform 内核,
>   deps 只含 manager/lab_store);响应/错误归类逐字保留。
> - **data_domains 声明是 D2 预告面**:D1 数据闸只强制 fs.* 域
>   (local_registry._check_data_access 跳过非 fs 声明),drafts.*/docs.*/
>   runs.*/skills.* 声明即账面,判定留 D2。
> - **未做**:scaffold.approve/draft.check 的草稿区读写不在 §17.4 名单
>   (草稿写是 lab.draft.* 工具面地盘),保持直调;conversation send/retry
>   归 run 留第 4 步;§17.8 的 L2 静态扫描(handler AST 禁直调业务写函数)
>   落在 test_platform_skills.py::test_l2_handlers_no_direct_side_effect_calls。
>
> **第 3/4/5 步实现注**(2026-08-05,分支 debugger,cascade 上线 + 旁路收编 ✅):
> - **cascade 注册制**:`widgets/registry.js` 的 WidgetDef 新增
>   `context_provider`——缺省兜底(kind + state 摘要,JSON 可序列化),
>   声明者覆盖,非函数拒注册("widget 没有 provider = 协议不合规"落到
>   registerWidgetDef 校验);`widgets/widget.js` 在 `register()` 时把
>   provider 按 path 挂进 cascade 运行时(scope "widget",读实时 state),
>   `destroy()` 注销;`cascade.js` 的注册加**同位替换**语义(同 prefix+scope
>   最新赢,宿主重挂载不堆叠过期闭包)。消费者侧:doc-editor 的气泡
>   (widget 级 = 锚点段+全文,app 级 = 文档状态,挂 `/doc/<name>[/<anchor>]`)
>   与 lab-iterate(`_iterateProviders` 手搓形态退役为
>   `_registerIterateProviders`,注销随气泡 close)都改注册制——
>   `registerContextProvider` 调用点从 0 变为 5(widget.js/lab-iterate/
>   doc-editor),死代码验收闭合。
> - **管道携带信封**:`AppActionBody.cascade`(可选)——前端 tab action
>   poster 按 `/{kind}/{ref}` 路径经 `contextCascade` 组装随 body 出海
>   (widget 零 fetch,组装在前端,服务端信任边界不变);管道经
>   `apply_action_cascade` 并入执行输入,action 声明 `context: []` 弃权。
> - **decisions 收编(#7)**:前端 answerDecision 一律经 action 管道
>   (无 instance 的 M1 前旧卡先 spawn escalation instance 再走,去重幂等);
>   404"已被处理"归类保留;`POST /api/decisions/{qid}` 专属端点**已删除**
>   (GET /api/decisions 列表是读面,§17.6 保留);答案非法现在由 manifest
>   裁决 404("无 action",比旧端点的 manager 400 更早一层——归类变化
>   只此一处,语义等价:都是拒绝)。test_platform_w2 的作答断言改走管道。
> - **cards/action 实测结论**:前端 cardAction 的旧面(M1 前持久化卡无
>   instance)仍在调——按任务预定裁决**保留端点并标 deprecated**,
>   测试(test_platform_api/test_cred_degrade/test_scaffold_smoke)不动。
> - **send/retry 归 run 评估结论(不做)**:对话 send 是亚秒级同步应答
>   (orchestrator 规则/薄 LLM 路由),改 run 跟踪面要动消息泵 + 会话持久化
>   + SSE 三处,收益(可跟踪/可 replay)对同步交互不抵改动面;且 send 是
>   对话主通道(§5)不是"动作"。**保持同步现状**,留口:若将来对话应答
>   长任务化,再按 run 通道归一(M4a 发起面已有先例)。
> - **第 5 步归类决策**:四个最高特权写面已在第 2 步收编;其余旧 web 写
>   端点随 §8 迁移地图逐页收编,未迁移前承认 legacy 例外(原则边界 = §8
>   时间表),不再单列排期。

### 17.8 验收(原则符合度测试)

- **静态扫描**:`apps.py` 每个 action 的 `exec.ref` 必须能在 registry 里解析
  到一个真实 skill(不是 `SKILL_BINDINGS` 里的 Python 函数);无孤 handler;
- **L2 断言**:§17.4 名单里的每个 skill,其副作用必须经 tool 分发——
  handler 内禁止直接 import 业务写函数(静态扫描 import 面);
- 每个注册 widget 必有 `context_provider`(缺省或声明);
  `registerContextProvider` 的调用点数 > 0(v0.5 审计时为 **0**(死代码);
  第 3 步后 = 5,widgets 测试静态扫描断言);
- 管道抽样:任选 action,其执行输入含 cascade 信封或显式 `context: []`;
- **行为零变化**:既有 **950 passed / 10 skipped / 32 xfailed**(2026-08-05
  实测基线)全绿——升格只换执行面的身份,不改语义。

### 17.9 边界:force_sandbox 下的开销

`RunConfig.logic_policy.force_sandbox = True` 时,`ctx` 变 None、代码跨进程经
syscall 通道执行(`api/v1/run.py:22`)。**那种部署下每个 UI 动作都是一次子
进程**,§17.1 的成本结论不适用。

升格方案必须声明:platform 的动作 skill 在 force_sandbox 宿主下是**豁免**
(它们是可信内置,与用户提供的 skill 不同源),还是接受代价。建议豁免,
并在 `logic_policy` 上加一个显式的可信内置白名单——**豁免要显式,不能靠
"恰好没被覆盖"**。

### 17.10 职责分工(skill 定义逻辑,框架准备参数)

用户澄清(v0.4 补记,v0.5 保留):**action 的逻辑由 skill 定义;但"从 action
到备齐参数发给对应 skill"是框架的职责**。两边都不越界:

| 框架(action 管道)负责 | skill 负责 |
|---|---|
| 触发寻址(action id → skill 名) | 业务逻辑本体 |
| 参数准备:cascade 级联(§16)+ args_from 服务端绑定 + args_input 校验 | 声明 inputs schema(参数形状) |
| 执行环境:帧/白名单/升权闸/数据闸 | 在权限面内干活,不管仲裁 |
| 结果回写 state + spawn 新 instance | 返回 outputs |

推论:skill 永远**显式声明它要的参数**(inputs schema),框架永远**显式给出
参数来源**(cascade/args_from/args_input 三通道,§4)——"需要什么"与
"从哪来"在两侧都一眼可见,没有隐式注入。
