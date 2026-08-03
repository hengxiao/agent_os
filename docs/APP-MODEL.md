# App 化 UI 模型(App-Model UI)技术文档

> 版本:v0.3(§13 Shell as App:整个界面也是 app;v0.2 按独立评审修订——
>   三态 exec、state 服务端权威、安全论断重写,见 §12)
> 对象:web_platform 的下一代 UI 架构——把 UI 看作一个操作系统,
>   对话 app 只是它的终端(PowerShell/cmd),世界改变动作经受仲裁的通道。
> v0.2 修订(评审驱动,逐条见 §12):
>   1. **推翻 v0.1 "每个动作都是 skill 调用 → 内核仲裁自动覆盖" 的论断**——
>      该论断在其招牌例子上为假(promote 是 host 函数不走升权闸;工具分发
>      需要帧上下文,宿主调工具=自我授权)。改为**三态 exec**(§4/§7):
>      endpoint / run / local,各自的授权面显式声明;
>   2. **app.state 服务端权威**,客户端只发事件不发状态(§4);
>   3. 不变量措辞修正:改变外部世界的动作经受仲裁通道;纯 UI 动作不需要。
> 关系:概念承接 `AGENTIC-UI.md` 方案 A;实现基础是 web_platform 现状;
>   红线遵守 `WEB-PLATFORM.md` §7(本模型的 exec 表是它的显式化,§7)。
> 一句话:**每个 app 两张面孔;每个改变世界的动作都走在明说的仲裁通道里。**

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

**核心修正(v0.2)**:action 的执行方式不是单一的"skill 调用"——那是错误
抽象(promote 是 host 函数、pin/spawn 无世界副作用、工具分发需要帧上下文)。
正确模型是三态,每态的授权面显式声明:

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

v0.1 说"action = skill 调用 → 内核仲裁自动覆盖 UI 全部副作用,UI 不需要
自己的权限层"——**这个论断是错的**,三处:

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
| M4 | legacy 五页以 tab surface 接入 + SSE transport + 主动汇报(app 状态推送进对话) |

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
