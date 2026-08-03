# App 化 UI 模型(App-Model UI)技术文档

> 版本:v0.1(设计)
> 对象:web_platform 的下一代 UI 架构——把 UI 看作一个操作系统,
>   对话 app 只是它的终端(PowerShell/cmd),一切操作都是 skill 调用。
> 关系:概念承接 `AGENTIC-UI.md` 方案 A;实现基础是 web_platform 现状
>   (会话/卡型/tab/双层化);双层化已被本模型吸收并泛化(§3)。
> 一句话:**UI 里的每个 app 都有两张面孔——嵌进别处的卡片,和独占一屏
>   的标签页;每个点击都是一次带状态的 skill 调用。**

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
2. **每个动作都是 skill 调用**:UI 不直接改世界;按钮 = `skill.<x>(args)`,args = `f(app.state, event)`。升权/数据闸/闸门因此**自动覆盖 UI 的全部副作用**——UI 不需要自己的权限体系。
3. **状态在 app 里,不在 DOM 里**:app.state 可序列化(刷新/重启可恢复);DOM 只是 state 的渲染。
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
- 卡面动作与全面动作**同一 action id、同一 skill 调用**——卡面是全面的遥控器,不是另一套逻辑。

## 4. Action 管道:点击如何变成 skill 调用

```
用户点击(卡面或全面)
  → UI 收集 args:f(app.state, event)
  → POST /platform/api/apps/{id}/actions/{action_id}
  → 服务端:AppManifest 校验(action 存在、args 齐、表面合法)
  → skill 调用(经 Agent OS 内核:白名单/升权闸/数据闸全过)
  → 结果写回 app.state(服务端)+ 可选产生新 app(发布成功 → Skills app)
  → UI 增量刷新两张面孔
```

关键性质:

- **前端永不直接调业务端点**:actions 是唯一出海口,服务端按 manifest 做参数绑定(防前端越权构造);
- **可中断**:action 对应长任务时,app.state 进入 `running` 态(两表面都显示进度),完成/失败回写;
- **升权请求回到发起它的 app**:L3 action 触发的 escalation pending,呈现为该 app 卡面上的待决状态(不污染对话流,除非用户让它进对话)。

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

- **接收**:现轮询(2s);留 SSE 接口(state.transport);
- **互动**:卡面动作走 §4 action 管道;动作结果以 agent 消息回插对话(因果可见);
- **失败**:sending/waiting 可重试;failed 消息带重发按钮,不静默;
- **路由标注**:agent 消息带 meta(route: llm/rule),降体验可观测(N6 已落地此雏形)。

### 5.3 对话 app 的 actions

| action | skill | 说明 |
|---|---|---|
| send | platform.orchestrate | 意图 → 编排(规则/LLM 路由) |
| spawn | (内嵌) | 从卡/消息孵化新 app |
| retry | platform.orchestrate | 重发失败消息 |
| pin | (本地) | 会话置顶(纯 state) |

## 6. Compositor(窗口管理器)

- 左栏 tab 条 = taskbar:每个打开的 app 一项(图标 + title + 关闭);
- **打开**:卡面"打开"链接/动作 → 新 app 或聚焦已有(kind+ref 去重,已有实现);
- **焦点**:单激活模型(当前 tab 独占主区);对话 app 是默认回落;
- **关闭**:只是隐藏,不销毁 state(重开恢复);销毁是显式 action;
- **嵌套**:tab 内可再嵌卡面(技能包 tab 里嵌 run 卡面),层级 ≤2,防俄罗斯套娃;
- **布局**:窄屏 tab 条收成图标列。

## 7. 安全与信任面(与内核对齐)

- action → skill 调用意味着:**升权闸、数据 authZ、闸门、原子提交自动覆盖 UI 全部副作用**;UI 没有也不该有自己的权限层;
- AppManifest 的服务端校验 = UI 层的白名单(防前端构造任意 skill 调用),与 ACTION_WHITELIST 同一哲学但泛化为 skill 粒度;
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

- **协议测试**:每个 app kind 的 manifest 合法(双表面存在、actions 的 skill 已注册、args_from 在 state_schema 内);
- **action 管道**:点击 → 正确 skill 调用(参数绑定正确)→ state 回写 → 两表面重渲;
- **表面纪律**:卡面禁忌词扫描(沿用)+ 全面技术字段存在;
- **Compositor**:去重聚焦/关闭回落/嵌套层级 ≤2;
- **持久化**:app.state 序列化往返;刷新恢复;
- **回退**:无 manifest 的 kind 拒绝渲染(不炸)。

## 10. 分期

| 期 | 内容 |
|---|---|
| M1 ✅ | AppManifest 注册表 + action 管道(替代 cards/action)+ 对话 app 归一(现状卡型全部转成六个 app kind 的表面) |
| M2 | Compositor 正式化(关闭≠销毁/嵌套 ≤2/图标列)+ app.state 持久化 |
| M3 | run/debug/lab-draft 三个 app kind 接入(对话 → 包 → run → debug 闭环) |
| M4 | legacy 五页以 tab surface 接入 + SSE transport + 主动汇报(app 状态推送进对话) |

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

## 11. 不做

- 不做多窗口并存/自由排布(单激活 tab 已够;分屏是另一个产品);
- 不做用户自定义 app 编辑器(manifest 是代码贡献,与主题系统同政策);
- 不做前端直连业务端点的过渡形态(action 管道一步到位);
- 不把对话 app 做成唯一入口(它很重要,但没有特权)。
