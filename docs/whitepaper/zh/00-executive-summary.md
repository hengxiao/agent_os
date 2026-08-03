# Agent OS 技术白皮书

> 版本:v1.0(2026-08)
> 文体:执行摘要(中文;分项目深度章节见本目录后续章节;English: [../en/00-executive-summary.md](../en/00-executive-summary.md))
> 范围:项目原型、核心理念、架构设计、子系统、解决的问题、现状与路线
> 依据:本文件全部论述以仓库已实现代码与既有设计文档为准(见附录 A 引用清单),
>   区分"已实现"与"已设计未实现"两个状态,不做超出实现的承诺。

---

## 摘要

Agent OS 是一个面向 LLM 智能体(agent)的**微内核运行时**。它把操作系统
的结构思想系统地映射到 agent 领域:Skill 是函数,Tool 是系统调用,
SkillFrame 是栈帧,Run 是进程,而内核只保留流控制、权限控制与 IPC 三件事。
在这一骨架之上,项目解决了当前 agent 系统的四类结构性缺陷:**行为不可仲裁**
(权限与信任)、**过程不可复现**(确定性与回放)、**状态不可隔离**(上下文
污染与跨层注入)、**质量不可保证**(skill 的生产标准与提交闸门)。本文档
给出项目的原型动机、设计公理、架构分层、各子系统契约、信任与安全模型,
以及截至 v1.0 的实现现状。

## 1. 原型与问题陈述

### 1.1 原型

Agent OS 的原型是一个"能安全地让 LLM 调用工具和彼此"的最小运行时:
一个技能注册表、一个工具注册表、一个驱动 agent loop 的内核,以及记录
一切的信号总线。随着调试器、权限升权、数据授权、Skill Lab 等子系统
逐一落地,原型演化为一个完整的运行时与开发平台,但内核的边界从未扩大——
这是微内核判据(§2.2)被严格执行的直接证据。

### 1.2 问题陈述

构建生产级 LLM agent 时,反复出现四类结构性问题:

1. **行为不可仲裁**:agent 一旦能调用工具,就可能在不可信内容(网页、
   用户输入、被注入的上游输出)的诱导下执行不可逆操作(删除数据、
   停止进程、对外发布)。传统方案要么全放开(危险),要么全拦截(不可用),
   缺乏按操作语义分级的仲裁机制。
2. **过程不可复现**:LLM 调用是非确定的,出问题时无法回放现场;
   token 成本与步数失控时,事后无法重建"它当时看到了什么"。
3. **状态不可隔离**:多技能嵌套时,底层不可信内容与高层特权操作共享
   同一上下文,prompt injection 沿调用链跨层放大。
4. **质量不可保证**:skill(提示词资产)的生产缺乏工程标准与准入闸门,
   元数据缺失、权限过宽、指令注入诱导等问题只能在运行期暴雷。

Agent OS 的应答是:用微内核保证仲裁点唯一,用帧模型保证隔离,
用检查点/回放保证可复现,用三档信任模型与提交闸门保证安全与质量。

## 2. 核心理念

### 2.1 一张贯穿始终的映射表

系统的骨架是一张"编程/OS 概念 → Agent OS 概念"的映射表。所有命名、
边界与机制都从这张表推导,保证概念体系不自相矛盾:

| 编程 / OS 概念 | Agent OS 对应 | 含义 |
|---|---|---|
| function | **Skill** | 有名、带参、可组合的行为单元 |
| system call | **Tool** | 触碰外部世界的原子特权操作,由内核统一仲裁 |
| call stack / stack frame | **SkillFrame 栈** | 每次 Skill 调用压入一帧,帧持有私有上下文 |
| 局部变量 | **FrameContext** | 本帧消息历史 + 工作内存,对其他帧隔离 |
| 动态链接器 | **Skill Registry** | 发现、校验、解析依赖、加载、写入 Skill |
| syscall 表 + seccomp | **Tool Registry + 权限模型** | 按名分发、参数校验、能力白名单 |
| 设备驱动 | **Provider** | 屏蔽各家 LLM API 差异 |
| 内存管理 / GC | **Context 子系统** | 上下文组装 + 压缩 + 前缀缓存稳定性 |
| 中断 / 看门狗 | **Sidecar** | 信号触发的监督者,可否决、暂停、强停 |
| 进程 | **Run** | 一次完整的 Agent 运行(根帧及其子树) |
| CPU / ALU | **Logic Kernel** | 逻辑代码唯一执行点(code 技能/动态代码) |
| journald / auditd | **Telemetry** | 信号落盘为 WAL,导出与检查点 |
| 文件系统 | **Memory** | 跨 run 记忆与知识(检索层权限过滤) |
| 共享内存 / 消息队列 | **Blackboard** | run 内帧间状态与消息,并发控制 |
| 微内核 | **Kernel Runner** | 只做流控制与权限控制,功能全部外置 |

### 2.2 四条设计公理

1. **微内核**。内核只保留三件事:**流控制**(loop 推进、帧栈、调用分发
   仲裁、safe-point 取消、并发原语、预算中止判决、恢复熔断)、
   **权限控制**(三层交集检查、trust 路由、verdict 仲裁、凭证作用域)、
   **IPC**(信号总线、RunControl、帧间消息寻址)。判据:没有它 loop 无法
   推进(F),或它是权限/否决仲裁点(P),或它是子系统间唯一公共通道(I)。
   推论:内核拥有循环,Skill 拥有策略。
2. **子系统互不相识**。跨子系统协作只能经由契约层(`api/v1` 的 Protocol)
   或信号总线,由内核中介。压缩器需要 LLM 摘要时调用的是
   `ProviderManager` 协议,而不是某个具体 provider。
3. **契约先行,基线可换**。每个子系统定义满足契约的最小基线实现,
   第三方实现契约时以基线为参照物。`api/v1` 是冻结面,新增字段一律
   additive 并带缺省。
4. **确定性是工程目标,不是运气**。检查点随帧序列化、信号落 WAL、
   replay 可按 trace 重建 MockProvider 脚本——任何 run 都可以确定性
   重放(工具副作用除外,边界见 §7)。

### 2.3 最小权限与分层信任

权限不是布尔开关,而是按**副作用语义**分级的连续面:工具自报权限等级
(READ/WRITE/NET/EXEC)与副作用档位(none/reversible/irreversible),
技能的信任档由权限面**推导**(不允许自报),跨档调用必须经过人审。
机密性则由数据层 authN+Z 以 principal 为判据独立承担(§5.3)。

### 2.4 人在环路是一等公民

supervisor 子系统把"请求上级裁决"建模为一等原语:任何帧都可以挂起
等待调用方(人、另一个程序、另一个 agent)的裁决,挂起-落盘-恢复
全程确定。人不是事后的审计员,而是执行路径上的仲裁者。

## 3. 架构设计

### 3.1 分层总览

```
┌──────────────────────────────────────────────────────────────────┐
│ 宿主层(薄宿主,不进内核)                                          │
│   CLI(面向 coding agent)  │  Web(面向人:UI/SSE/收件箱/调试台)   │
│   ───────────── host/shared:产物组织 / RunRecord / replay ──────  │
├──────────────────────────────────────────────────────────────────┤
│ 契约层 api/v1(冻结面:Skill/Tool/Frame/Signal/Principal/…)        │
├──────────────────────────────────────────────────────────────────┤
│ 微内核 Kernel Runner = 流控制 + 权限控制 + IPC                     │
│   agent loop │ 帧栈 │ 分发仲裁 │ safe-point 取消 │ 预算判决        │
├───────────┬───────────┬───────────┬───────────┬──────────────────┤
│ Providers │ Sidecars  │ Skills    │ Tools     │ Context          │
│ (设备驱动)│ (监督者)  │ Registry  │ Registry  │ (组装/压缩)      │
├───────────┼───────────┼───────────┼───────────┼──────────────────┤
│ Logic     │ Telemetry │ Memory    │ Blackboard│ Supervisor       │
│ Kernel    │ (WAL)     │ (跨run记忆)│ (帧间共享) │ (裁决路由)      │
└───────────┴───────────┴───────────┴───────────┴──────────────────┘
```

### 3.2 内核 agent loop(单帧)

```
loop:
  1. 上下文交 Context 子系统(压缩维护 + 组装请求 + 状态注入)
  2. 请求 ProviderManager → LLM 响应
  3. 无工具调用 → 帧结束(outputs schema 校验)
  4. 有工具调用 → 逐个分发仲裁:
       a. 伪工具(skill.* / ask_supervisor / python_orchestrate)→ 专用路径
       b. 真实工具 → schema 校验 → 数据层 authZ → 三层权限交集 → 执行
       c. 子技能 → 白名单检查 → 升权判定(§5.2)→ 压栈
  5. 观察写回帧上下文,回到 1
```

内核在每一步都发出信号(§4.5);safe-point 让取消/暂停只发生在
分发边界,不会发生"删了一半被打断"。

### 3.3 帧模型与隔离

```
Run(进程)
└─ 根帧 SkillFrame(depth=0, 根 skill)
   ├─ 子帧(depth=1, skill A)        每帧持有:
   │  └─ 子帧(depth=2, skill B)      · FrameContext.messages(私有,不共享)
   └─ 子帧(depth=1, skill C)        · working(工作内存,随检查点序列化)
                                     · tier(信任档,继承被调 skill 推导档)
父子之间唯一的通道:调用参数(父→子,JSON Schema 校验)与
返回结果(子→父,折叠为 tool result)。父帧的对话历史、工具观察、
注入载荷物理上不在子帧上下文里。
```

这一模型是"干净 context 不变量"(§5.2)的载体:升权帧的上下文组成
是白名单——系统默认 prompt + 目标 skill prompt + 经 schema 校验的参数,
没有第四扇门。

## 4. 子系统

### 4.1 Providers(设备驱动)

契约:`ChatProvider` 协议(chat/stream/usage/tokenizer)+
`ProviderManager`(内核侧门面,负责路由、token 估算口径统一)。
基线:`OpenAICompatibleProvider`(覆盖主流厂商)与
`MockProvider`(测试与 replay 脚本执行)。token 估算优先使用 provider
精确 tokenizer,否则统一估算器——Context 子系统依赖同一估算,口径唯一。

### 4.2 Skills(函数与动态链接器)

skill 是一等资产:manifest 契约冻结(name/version/kind/description/
inputs/outputs/permissions/model/context_policy/limits/trust 等),
指令体可以是 prompt 模板或 code handler。加载流水线做拓扑排序、
依赖存在性检查、循环检测与热重载。子技能对父帧 LLM 暴露为伪工具
`skill.<name>`,schema 即被调方 inputs;内核在分发阶段拦截并压栈。
`inline: true` 的纯说明书技能可在装配期并入调用方 SYSTEM(纯度闸门
+ 快照冻结,见 SKILL-INLINING.md);推导档 ≥L2 的技能禁止 inline(硬闸门)。

### 4.3 Tools(系统调用与仲裁)

每个工具声明 `permission`(READ<WRITE<NET<EXEC)与
`side_effect`(none/reversible/irreversible,缺省按 permission 推导)。
分发流水线每次调用完整走:schema 校验 → **数据层 authZ**(§5.3)→
三层权限交集(工具自报等级 × 帧 manifest 白名单 × RunConfig 上限)→
超时执行。路径沙箱(`resolve_work_path`)与数据域判定(§5.3)正交:
一个管"出不出 workdir",一个管"这个 principal 能不能读这片"。

### 4.4 Context(内存管理)

帧上下文的组装与压缩:build(SYSTEM 渲染 + 消息 + 状态注入 + 工具 schema)、
maintain(估算超 cap 触发压缩,责任链策略,rolling-window 基线:原子组
驱逐、pinned 永不压、幂等标记 `[COMPRESSED]`)。前缀缓存稳定性是硬约束:
inline 段在帧首次 build 时冻结进 working,resume/热重载后前缀一致。

### 4.5 Sidecars 与 Telemetry(监督与黑匣子)

信号总线是全系统的公共通道:`pre/post:llm.*`、`pre/post:tool.call`、
`pre/post:skill.invoke`、`pre/post:skill.escalate`、`run.*` 等,
全部落盘为 WAL(`trace.jsonl`)。sidecar 由信号触发,可同步否决
(Veto)、修改(Modify)、暂停(Pause)、强停——BudgetGuard(预算)、
ToolGuard(规则)等为内置实现。遥测不止用于审计:replay(§7)与
调试器(§6.2)都建立在同一份 WAL 之上。

### 4.6 Logic Kernel(ALU 与沙箱)

逻辑代码的唯一执行点:code 技能与 LLM 动态代码(`python_orchestrate`)
都经它路由。`logic: {mode: sandbox}` 或全局强制沙箱时,代码在
隔离环境运行(无 `ctx`,只能经 syscall 通道回到内核分发——注释明写
"无权限提升")。这是与升权系统方向相反但互补的机制:升权管
"低层能不能进来",沙箱管"代码能不能出去"。

### 4.7 Supervisor(裁决路由)

`ask_supervisor` 伪工具与内核强制的升权确认共用同一闭环:
pending 落盘(`frame.context.working`)→ 就地挂起 → 调用方通道作答
(Web 收件箱 / CLI stderr-stdin 协议 / 嵌入方 handler)→ 答案写回为
tool result → run 恢复。超时按 `on_timeout: fail|default_answer` 闭环;
options 校验不合带 `previous_error` 重问。裁决请求是结构化数据
(含 `kind`),Web 收件箱按 kind 渲染专用卡片(如升权卡片)。

### 4.8 Memory 与 Blackboard

Memory:跨 run 记忆与知识,检索层做权限过滤(与数据 authZ 同判据)。
Blackboard:run 内帧间状态与消息的共享内存,并发控制,供 fork/join
并行的帧交换中间结果。两者都是契约先行,基线最小可用。

## 5. 信任与安全模型

### 5.1 三档信任层级(按副作用可逆性)

| 档 | 值 | 副作用语义 | 例 | 确认强度 |
|---|---|---|---|---|
| L1 | `none` | 无副作用:纯读取、纯计算 | 数据读取、检索、推理 | 不确认 |
| L2 | `reversible` | 副作用可逆或可容忍 | DB/文件写入、可撤回消息 | 首次确认,可 approve-run |
| L3 | `irreversible` | 副作用不可逆且不可容忍 | 删除数据、删除 VM、停止进程、转账 | **每次必人审,无 approve-run** |

档的声明与推导:**tool 声明** `side_effect`(作者最懂自己的工具,
缺省按 Permission 推导:READ→none、WRITE/NET→reversible、
EXEC→irreversible);**skill 推导不声明**(白名单 tools/skills 递归
取 max——自报会撒谎,薄 orchestrator 洗不了档)。

### 5.2 升权流程(低档 → 高档必须过人)

```
父帧(低档)          内核                     调用方(人)
  │ skill X(params)   │                        │
  ├──────────────────▶│ ① 白名单检查            │
  │                   │ ② 参数 schema 预校验     │
  │                   │    (失败不发确认)        │
  │                   │ ③ tier(target) > tier(frame)? │
  │                   │ ④ 挂起 + checkpoint 落盘 │
  │                   │ ⑤ EscalationRequest ──▶│ ⑥ 展示:谁调谁/档/参数
  │                   │ ⑦ 裁决        ◀────────│ approve-once / approve-run(仅 L2)/ deny
  │                   │ ⑧a 批准→隔离新帧         │
  │                   │    (干净 context 不变量) │
  │                   │ ⑧b 拒绝→PERMISSION_DENIED│
  │◀── 子帧结果(折叠为 tool result)            │
```

> 注:返回路径的 `[ESCALATED:...]` provenance 标记已设计(ESCALATION.md §3)
> 未实现——升权子帧结果目前与普通 tool result 同形(见第 09 章 §6)。

关键性质:

- **审的就是要执行的**:确认请求展示的参数与通过 schema 校验、
  将原样注入子帧的参数是同一份 JSON;
- **注入者无法批准自己**:确认由内核发起,不是 LLM 发起;
- **干净 context 不变量**:升权帧 messages 恰为 `[USER(参数)]`,
  SYSTEM 只含系统默认段 + 目标 skill prompt(有测试断言);
- **Grant 短命化**:approve-run 只对 L2 存在,授权随 run 死亡;
- **resume 确定**:挂起中崩溃,resume 重走闸门。

### 5.3 三闸模型(读/写/泄露)

```
读(机密性)   → 数据层 authN+Z:Principal × 数据域 × 敏感度,默认拒绝
写(副作用)   → 档位升权:低档进高档必须人审
泄露(exfil) → 写闸兜底:NET 发送是副作用,必过 L2/L3 闸门
```

数据层 authN+Z 以 principal(谁启动的 run)为判据,与 skill/帧无关:
**升权改的是副作用许可,不是身份**——普通用户启动的 run 即便升进
L3 skill,能读的数据仍是这个用户能读的。两套系统正交,共享的只有
principal 字段。已知残余风险(明示):同 principal 的 run 内部不做
数据隔离(那是同一个"人"的数据);多用户隔离由 run 边界承担。

### 5.4 生产标准与提交闸门(Tier Standards & Gate)

信任模型要成立,前提是同档成员过了同一套生产标准
(TIER-STANDARDS.md):L2 必须有可演练的逆转机制(`reversal` 必填)
与幂等实现;L3 必须支持 dry-run、`blast_radius` 必填、指名目标、
TOCTOU 防护。Skill Lab 的**五关提交闸门**把标准变成执行点:
G1 metadata(命名/路由式描述)、G2 契约(schema 合法、L2+ 参数带类型)、
G3 分档合规(上述必填项 + inline 硬闸门)、G4 冒烟试跑(outputs 必须过
schema)、G5 提示词卫生(注入诱导检测)。任何 fail 不能进生产。

## 6. 工程化:确定性、调试与开发者体验

### 6.1 确定性工程(检查点 / 恢复 / 回放)

每次运行落四件产物:`meta.json`、`trace.jsonl`(信号 WAL)、
`checkpoint.json`(帧状态快照)、`result.json`。检查点随帧序列化
(含 working 里的 pending 裁决与升权请求),崩溃后 resume 从检查点
重入,pending 重问或带答案重入。replay 利用"runner 只在帧上下文
追加 LLM 响应"的性质,从 trace 重建 MockProvider 脚本,确定性重放
整棵帧树(不碰真实 API;工具副作用仍按真实环境执行——这是边界)。

### 6.2 调试器(GDB 语义)

Web 调试台把 run 当进程调试:断点(pre:tool.call / skill.invoke / step /
error 四类,glob 匹配)、单步(into/over/out)、随时 pause(SIGINT 语义)、
帧检视与干预(modify args / inject message 后放行)。调试会话同样
走挂起-恢复闭环,断点命中即 pending。

### 6.3 主题系统(六主题,组件零分支)

UI 主题 = token 映射 + 文案表 + (可选)mascot 层。六个内置主题
(classic/moe/terminal/blueprint/ink/pixel)全部通过契约测试:
token 完整性、WCAG 对比度、双编码(颜色 + 文字双通道)、文案键完整、
组件无主题分支(静态扫描)。mascot 抽象有两个实例(Mochi/sprite8)
证明层可换。

### 6.4 Skill Lab(开发平台)

skill 的开发闭环:DraftStore 草稿层(与生产物理分离)→ 七组全字段
编辑器(推导档实时显示)→ Agent 助手(`skill.dev.assistant`,
只有 `lab.draft.*` 五件工具,**能改不能发**)→ 测试面板(试跑走
生产同一装配线)→ 五关闸门 → 人点提交(promote 三重防:报告哈希
绑内容、fail 硬拒、服务端复跑 G1-G3)→ 生产热重载。CLI 提供同一
闸门(`agent-os lab validate`,退出码 pass/warn=0、fail=2),coding agent 可头less 消费。

## 7. 解决的问题(映射回顾)

| 问题(§1.2) | 机制 | 状态 |
|---|---|---|
| 行为不可仲裁 | 三档信任模型 + 升权闸 + approve-once/run + 分档生产标准 | 已实现(E1/E2) |
| 数据访问无判据 | 数据层 authN+Z(Principal/数据域/默认拒绝) | D1 已实现;配置段/多用户(D2/D3)已设计 |
| 过程不可复现 | 检查点/resume/replay + 信号 WAL | 已实现 |
| 状态不可隔离 | 帧模型 + 干净 context 不变量 + inline 纯度闸门 | 已实现(含测试断言) |
| 质量不可保证 | 五关提交闸门 + 分档生产标准 + Skill Lab | 已实现(L1-L5) |
| 运行不可观测 | 信号目录 + 调试器 + RCA | 已实现 |
| skill 自写信任链 | SkillArtifact/Provenance | 契约预留(M6,未实现) |

## 8. 现状与路线

截至 v1.0(2026-08):内核九子系统全部落地并有基线实现;升权系统
E1/E2 已实现(含 spawn 闸、Web 升权卡片);数据层 authZ D1 已实现
(单用户无感,嵌入宿主/多用户可显式生效);Skill Lab L1-L5 全部落地;
Web 六主题全部通过契约测试;测试基线 Python 812 例 + 前端 24 个
测试文件全绿。已设计未实现:E3 余项(审计面板)、D2/D3(数据域
配置段/派生链/多用户)、M6(信任管线)、动效播放层(主题契约
测试第 5 项)、多文件 skill_set 归并、handler 源码进生产。

路线原则:契约先行、基线可换、闸门守出口;每一项新能力先回答
"它的仲裁点在哪、它的确定性如何保证、它的测试在哪"。

## 附录 A:引用文档(本仓库)

- 架构基准:`DESIGN.md`(内核与子系统)、`RUNNERS.md`(宿主)、
  `CODE-ORCHESTRATION.md`(编排沙箱)、`SKILL-INLINING.md`(内联)
- 信任与安全:`ESCALATION.md`(升权)、`TIER-STANDARDS.md`(生产标准)、
  `DATA-AUTHZ.md`(数据授权)、`SUPERVISOR.md`(裁决通道)
- 体验:`DEBUGGER.md`、`DEBUG-UI-THEMES.md`、`WEB-UI.md`、`SKILL-DEV.md`
- 示例:`agent_os/examples/workspace_janitor`(升权全真示例)、
  `support_desk`、`supervision` 等
