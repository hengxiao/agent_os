# Agent OS Web Platform(方案 A:对话中枢)· 技术档案

> 版本:v0.1(档案;后端骨架期)
> 对象:`agent_os/src/agent_os/host/web_platform/`(新宿主,chat-first)
> 选型:`docs/AGENTIC-UI.md` 方案 A(对话中枢)——用户已选定;B(任务中心)/
>   C(活动流)归档,其可迁移点(决策统一队列/流式时间线)记在 §9 留口。
> 红线:**不动内核与信任模型**(升权/数据闸/闸门/promote 不变);
>   新平台只是宿主层的信息架构,不新开任何权限通道。

---

## 1. 架构分层

```
┌─ 前端(下一步)────────────────────────────────────────┐
│ 对话流 + 产物卡渲染(卡型组件零分支,照主题契约)         │
├─ host/web_platform(本包)─────────────────────────────┤
│ sessions.py    会话与消息模型(文件持久化)              │
│ artifacts.py   产物卡协议(六卡型 + schema 校验 + 白名单)│
│ orchestrator.py 意图编排(规则本期,LLM 路由留接口)      │
│ app.py         FastAPI:会话 API + messages + cards/action│
├─ 既有能力(全部复用,零新通道)──────────────────────────┤
│ 内核 / providers / DraftStore / gate(五关) / closure   │
│ plan+promote(P2 原子提交) / iterate(Flow C) / 升权收件箱 │
├─ host/web(旧 web)────────────────────────────────────┤
│ 专家模式:Runs/Skills/Tools/Lab/Debug 全量保留(§8 降级) │
└──────────────────────────────────────────────────────┘
```

装配:`host/web/app.py` 把 platform app 挂载到 `/platform`(try/except,
失败只记日志不拖垮主 app)。**两个宿主共享同一个 RunManager 与同一个
DraftStore**——草稿、闸门、生产面是同一份,切换外壳不丢工作。

## 2. 为什么不照抄旧 web(差异决策)

旧 web 按**系统数据类型**分页(Runs/Skills/Tools/Lab)——它假设人是操作员,
先找功能再跑流程。方案 A 把信息架构换成**以意图为骨架**:人只说"我要什么",
产物以卡片形式回到对话里。具体差异:

- **页面 → 卡片**:Runs/Skills/Tools/Lab 不做第二份,退化为卡片的
  "全屏展开视图"(后续期);本期它们以专家模式身份原样存在;
- **表单 → 编排**:新建/检查/提交不再各自成页,而是 orchestrator 把意图
  分解为既有能力的调用序列,结果包成卡片;
- **列表 → 会话索引**:任务/产物列表 = 会话里 artifact 卡的索引
  (sessions.list 的摘要面),不是另一种数据类型的查询页。

不照抄代码的直接原因:旧 web 的页面组件(run_manager/SSE/调试台)与
"按数据类型分页"的路由深度耦合,改造等于重写——不如新建薄宿主,
让旧 web 完整地当专家模式(降级面),而不是把它改成四不像。

## 3. 会话模型(sessions.py)

```
Session  {id, title, created_at, last_at?, messages[]}
Message  {id, role: user|agent, text?, cards: [artifact], ts}
```

- 持久化:`<artifacts_root>/platform_sessions/<id>.json`(单文件单会话,
  刷新不丢;与 runs 产物同目录语义——平台状态也是宿主产物);
- 标题 = 首条用户消息前 40 字(追加时回填);
- 列表摘要 = 左栏"任务/产物"索引:`{id, title, created_at, last_at,
  messages, cards}`(卡数 = 各消息卡数之和);坏文件隔离(目录可被外部清理)。

## 4. 产物卡协议(artifacts.py)

卡 = `{type, v: 1, ts, data, actions}`。**卡型注册表**本期六型:

| 卡型 | data 要点 | 动作 |
|---|---|---|
| plan | {goal, reuse[], create[]} | scaffold.approve |
| skill_pack | {name, tier, members[]} | — |
| gate_report | {draft, status, gates} | plan.recheck(非 fail) |
| diff | {name, diff(§Flow C 同构)} | candidate.accept / discard |
| publish | {root, members[], plan_id} | plan.confirm |
| table | {title, columns[], rows[][]} | — |

- 每型一个 `build_*` 函数;`validate_card` 是出服务端前的协议闸
  (type ∈ 注册表、v == 1、data 是 dict);
- **action 白名单**(`ACTION_WHITELIST`,唯一裁决面):action id →
  (method, endpoint 模板)。校验时 action 的 method/endpoint 必须与白名单
  一致——卡不能自己声明白名单外的端点,**防任意 URL 发射器**;
- 端点只允许指向既有面:`/api/lab/drafts*`、`/api/lab/packages/*`。
  开新动作 = 先登记白名单(代码评审面)。

## 5. 意图编排(orchestrator.py)

`handle(session, text) → agent 消息(文本 + 卡)`。本期三意图,**规则路由**
(关键词正则);**LLM 意图路由留 provider 接口**(`_route_llm` 预留位,
注入 provider 即启用,返回面与规则一致):

| 意图 | 触发(规则) | 产物 |
|---|---|---|
| ① 做个/写个 X 技能 | 做个/写个/帮我做…+技能 | plan 卡:复用检索(名字/描述含主题词的生产技能,≤3)+ 新建建议;批准动作 scaffold.approve |
| ② 为什么挂/失败 | 为什么…挂/失败/fail | table 卡:最近失败 run 的摘要(runs_provider 注入,隔离装配细节) |
| ③ 其他 | — | help 卡(三句引导) |

**工具面红线**:编排只**读**生产 registry 与 run 记录;一切写动作走
卡片 action 白名单转发(§6),编排自身零写权限。

## 6. API 面(app.py)

```
GET  /api/sessions                     会话摘要列表(左栏索引)
POST /api/sessions                     创建会话
GET  /api/sessions/{id}                读会话(404)
POST /api/sessions/{id}/messages       user 意图 → agent 消息(+卡;卡过协议闸)
POST /api/cards/action                 卡片按钮统一入口(白名单 → 转发)
```

`cards/action` 的转发面(每件都薄,调既有 store/gate/package 能力,
**不开新 promote 路径**):

| action id | 转发到 |
|---|---|
| scaffold.approve | DraftStore.create + validate_draft(五关)→ skill_pack + gate_report 卡 |
| iterate.generate | skills/iterate.run_iterate(Flow C 同一路径)→ diff 卡 |
| candidate.accept/discard | DraftStore 快照/清候选(与 Lab 端点同语义) |
| plan.recheck | packages.build_plan → publish 卡 |
| plan.confirm | packages.promote_package(P2 原子提交) |
| version.rewind | DraftStore.restore_version(历史不动) |

## 7. 与既有系统的边界(红线逐条)

- **复用**:内核/providers(经 manager 装配)、DraftStore(与 Lab 同一实例)、
  gate 五关、closure、plan/promote、Flow C iterate、升权收件箱(决策类卡的
  直接来源,后续期把 supervisor pending 也建模为卡);
- **不开新通道**:卡片动作只转发白名单内的既有端点语义;orchestrator 与
  handler 没有任何"绕过闸门"的调用;**没有第二条 promote 路径**
  (plan.confirm 就是 packages/promote);
- **信任边界对齐**:scaffold/iterate 的工具面与 P3/Flow C 完全一致
  (lab.draft.* 围栏、lab.cand.write 只写候选);升级动作仍过人
  (plan.confirm 的按钮在卡里,点的是人);
- **生产面零变化**:旧 web 的 run 管理/SSE/调试台不受影响,platform 挂载
  失败时主 app 照常。

## 8. 降级策略(agent 故障 → 专家模式)

- orchestrator 是纯函数式规则,故障面集中在"生成类动作"(iterate/scaffold):
  provider 故障 → 既有 503"助手暂不可用"语义(与 Lab iterate 一致);
- 平台装配失败 → 主 app 跳过挂载(日志),旧页照常;
- **旧 web 就是专家模式**:任何精确操作(改单字段、逐关看报告、断点调试)
  在旧页完成;新平台不追求覆盖,只追求意图级闭环(§2.4 AGENTIC-UI 风险①
  的既定答案:卡片可展开到专家页,而不是在对话里做一切)。

## 9. 留口(后续期)

- LLM 意图路由(`_route_llm` 已留 provider 接口);
- 产物卡全屏展开视图(Runs/Skills/Tools/Lab 的卡片化外壳);
- 决策统一队列(supervisor pending + gate warn + publish plan 统一成
  "待决策"卡列——B 方案最大的结构红利,不占本期);
- `static/` 前端(下一步:对话流 + 六卡型渲染组件);
- sessions 搜索/归档;多用户会话隔离(等 DATA-AUTHZ D3)。
