# 升权系统:三档信任与干净 context

> 章次:09 · 状态:已实现(E1 升权闸 + 干净 context 不变量;E2 approve-run Grant、
> 信号三枚、spawn 闸;E3 部分落地) · 依据:docs/ESCALATION.md(v0.3)、
> agent_os/src/agent_os/api/v1/escalation.py、agent_os/src/agent_os/kernel/runner.py、
> agent_os/tests/kernel/test_escalation.py、agent_os/examples/workspace_janitor/

## 1. 概述

升权系统是 Agent OS 的**运行时副作用仲裁器**。它把 skill 按副作用可逆性分为三档
信任层级(L1 `none` / L2 `reversible` / L3 `irreversible`),并规定任何"低档帧
调用高档 skill"的跨越都必须先挂起,由人(或显式策略)看过结构化参数、明确批准,
再在一份与父帧物理隔离的干净 context 里执行。架构上它落在微内核的权限控制面:闸门
嵌在 runner 的子技能分发路径(`kernel/runner.py:865`),确认复用 supervisor 的
挂起-裁决-恢复闭环,授权台账挂在内核 Run 上随 checkpoint 序列化(`kernel/run.py:20`)。
它闸的是"低层能不能进来改世界",不管"能看到什么"——后者由数据层 authZ 承担,与
"泄露 = 写闸兜底"合成三闸模型(ESCALATION.md §1)。

## 2. 动机与背景

**静态白名单必要但不足。** manifest 白名单回答"这个 skill 声明了哪些能力",不回答
"此刻该不该用"。白名单是作者写下的静态面,注入发生在运行时:一个被注入的 L1 skill
只要能叫到 L3 skill 的名字,就能借它的白名单执行高危工具(ESCALATION.md §1)。权限
边界必须有一道运行时闸门。

**确认发起权的反转。** 原有的 `ask_supervisor` 是 LLM 主动提问——注入者的最优策略
恰恰是不问。内核缺少"判定越权 → 强制挂起等裁决"的路径,因此升权确认必须由内核
发起、强制执行,而非模型自选动作(`runner.py:917-922` 注释明写"确认不由 LLM 发起")。

**授权没有落点。** 即使人批准了,dispatch 时的 `allowed_tools` 仍现取 manifest,
"本次批准"无处回写。这要求一个随 run 存活、在判定处被消费的授权台账——Grant
机制存在的直接原因(ESCALATION.md §4)。

**为什么闸门在内核。** 升权判定要同时看到调用帧 tier、目标推导档、白名单与
supervisor 通道,四者都在内核手里;外置(sidecar 否决)会变成"先执行后否决",对
不可逆操作毫无意义。按微内核判据这是标准的权限仲裁点(P)。

分档方法学上还有一个历史决策:档**不按"危险程度"这种主观度量分,按副作用的客观
语义分**——L1/L2 的分界是"会不会改世界",L2/L3 是"改了能不能挽回";两条线都
可判定,故三档恰好够,不做第四档(§8)。附录 A 的移动 OS 权限模型调研(Android/iOS
TCC)给每条取舍一个出处,而干净 context 不变量是移动 OS 没有的闸面(§4.6)。

## 3. 问题陈述

以下场景都可复现,且全部有对应测试(§5)。

- **P1 借名升权**:L1 巡检 skill 读到含注入载荷的日志,被诱导调用
  `ops.cleanup.execute({targets: [...]})`;无闸门时白名单放行、直接压栈,真删。
- **P2 审一套跑一套**:确认界面展示的参数与实际注入子帧的参数不是同一份,人审
  形同虚设。
- **P3 磨到批准**:被拒后 LLM 无限重试同一调用,收件箱轰炸直到人误点批准。
- **P4 断电丢现场**:挂起等待人审时进程被杀,恢复后这次调用算数还是不算?
- **P5 薄 orchestrator 洗档**:L1 壳 skill 转包一层给 L3 实现;若 tier 只看自己
  的 tools,转包即洗档。
- **P6 后台帧绕道**:code 技能的 `spawn_frame` 不走 LLM 分发,E1 期是不设防的
  旁路(E2 补闸,`runner.py:1153`)。
- **P7 无人可审**:headless 嵌入未装配 supervisor 通道,升权调用默认放行还是
  默认拒绝?

## 4. 设计与机制

### 4.1 三档信任层级

| 档 | 值 | 副作用语义 | 确认强度 |
|---|---|---|---|
| L1 | `none` | 无副作用:纯读取、纯计算 | 不确认(现状行为) |
| L2 | `reversible` | 有副作用但可逆或可容忍(写入、可撤回消息、改配置) | 首次确认,可 approve-run |
| L3 | `irreversible` | 不可逆且不可容忍(删除、停进程、转账、对外发布) | **每次必人审,无 approve-run** |

档的比较只凭一张全序表(`escalation.py:51`),字符串本身无大小语义;未知档按最低
计——推导面之外的输入不放大权限(`escalation.py:54-56`)。

### 4.2 档的声明与推导:tool 声明,skill 只推导

- **tool 声明** `ToolSpec.side_effect`(作者最清楚自己的工具),缺省按 `Permission`
  推导:READ→none、WRITE/NET→reversible、EXEC→irreversible(`api/v1/tools.py:127-136`);
  只读诊断类 exec 可显式下调,读取工具可上调——就高不就低。
- **skill 推导不声明**:`derive_skill_tier` 取白名单 tools 与 skills(递归)的最高
  一档(`escalation.py:92-104`)。自报会撒谎或过时,推导值永远反映真实权限面;
  `explain_skill_tier` 还返回每处来源(`escalation.py:107-140`)。
- manifest 的 `trust` 块只剩确认策略覆盖项 `confirm: always|first`,且**只能上调
  不能下调**:L3 推导档写 `confirm: first` 装配期直接报错(硬闸)。

### 4.3 升权事件与帧 tier 的两种取法

```
升权事件 ⟺ tier(目标 skill) > tier(调用帧)     (escalation.py:59-61)
```

同档移动不确认(同档可信度由生产标准保证);**高档调低档是降权,永不确认**。帧 tier
的取法是全设计最微妙的一处:

- **目标侧 / lint 侧**:完整推导档(tools + skills 递归取 max)——P5 的薄
  orchestrator 洗不了档;
- **子帧**:继承被调 skill 的完整推导档(`runner.py:897`)——进入即继承整个已审
  信封,信封内同层移动不再确认;
- **根帧**:根 skill 的**直接能力档**,只看自己的 tools,不沿 skills 递归
  (`runner.py:238`,`derive_tools_tier`,`escalation.py:76-89`)。

为什么根帧不用完整推导档?若用了,被调 skill 必在调用方白名单内,恒有
`tier(target) ≤ tier(caller)`,升权事件在数学上永不触发,闸门成为死代码。人启动
run 时确认的是根技能的直接能力面,不是它能间接够到的整张图(ESCALATION.md §2.2
实现注);根技能由宿主直接启动本身不过闸:启动即确认。

### 4.4 闸门时序

```
父帧(低档)              内核 runner                          调用方(人/策略)
  │ skill X(params)      │                                    │
  ├─────────────────────▶│ ① 白名单检查(runner.py:848)        │
  │                      │ ② tier 判定(:865)不升权→直通       │
  │                      │ ③ 参数预校验(:870)失败→INVALID_ARGS│
  │                      │    **不产生确认请求**               │
  │                      │ ④ Grant 消费(:940)命中→直接放行    │
  │                      │ ⑤ pending 落盘(:990)+ pre 信号     │
  │                      │ ⑥ EscalationRequest ─────────────▶│ ⑦ 看:谁调谁/档/
  │                      │    (kind="escalation",结构化载荷)   │    参数/权限集
  │                      │ ⑧ 裁决                    ◀────────│ approve-once /
  │                      │    approve-run 仅 L2;L3 永不提供    │ approve-run(L2)/
  │                      │ ⑨a 批准→make_frame 隔离新帧(:884)   │ deny
  │                      │ ⑨b 拒绝→PERMISSION_DENIED(:1059)   │
  │◀── 子帧 result 折叠为 tool result(:915)                   │
```

关键性质:**审的就是要执行的**——确认请求展示的 `params` 就是已通过 inputs schema
校验、将原样注入子帧的那份 JSON(`escalation.py:154-172`);`reason_hint` 是机器
生成的"调用帧档 → 目标档",不是 LLM 写的。无 supervisor 通道时 **fail-closed**
(`runner.py:957-967`)。`spawn_frame` 走同一闸门;因 spawn 无 tool result 观察
通道,拒绝/参数不合以 `SkillLoadError` 上抛(`runner.py:1163-1174`)。

### 4.5 裁决选项与 Grant 生命周期

- `approve-once`:放行本次,**不登记** Grant——下次同调用必再撞闸,防 P3"磨到
  批准"(`runner.py:1010-1014` 注释);
- `approve-run`(仅 L2 的 options 里有):登记 run 档 Grant(tier 快照 + decided_by),
  本 run 内同 skill 后续调用直接放行,发 `post:skill.escalate(decision="grant-run")`
  (`runner.py:1015-1024`);
- `deny`:父帧收 PERMISSION_DENIED 错误观察,与白名单拒绝同形,LLM 可改道;重试
  同样的调用会再次挂起。

Grant 存放于内核侧 `Run.grants`(`kernel/run.py:20`),随 checkpoint 序列化(resume
后判定一致),随 run 死亡——不做跨 run 持久授权。消费点有**双保险**:即使有人手工
构造 Grant 或答案,run 档也只对 `reversible` 目标生效(`runner.py:1083-1086`);once
档消费即焚(兜崩溃残留)。L3 的 options 里根本没有 approve-run,手工输入该答案会被
supervisor 的 options 校验打回、带 `previous_error` 重问(`supervisor/manager.py:163`)。

### 4.6 干净 context 不变量

批准后进入高层 skill 的执行环境是白名单制,只有三扇门:

1. SYSTEM = 系统默认段 + `render_prompt(目标 skill.prompt, input)`——父帧 prompt
   天然不进;
2. 首条 USER = 经 schema 校验的参数 JSON(`source=PARENT_INPUT`);
3. 工具 schema = 目标 skill 自己的白名单 + 伪工具。

**没有了**。父帧的对话历史、工具观察、注入载荷物理上不在子帧 context 里——这不是
过滤,是隔离:子帧是全新 `FrameContext`,messages 恰为 `[USER(input)]`(断言见 §5)。
配套硬闸门:推导档 ≥L2 的 skill 禁止 `inline: true`(`skills/manifest.py:120-122`)
——inline 会把指令段合并进调用帧,等于绕开隔离墙。

注入风险账由此闭合:低层内容进高层只剩"schema 校验过的参数"一条窄道;低层 LLM 即便
被注入,下一轮再调高层仍撞确认闸门(L3 每次必撞)。**注入者无法自己批准自己。**

### 4.7 信号与审计

| 信号 | 载荷 | 时机 |
|---|---|---|
| `pre:skill.escalate` | skill, tier, frame_id, params, requested | 确认请求发出时 |
| `post:skill.escalate` | skill, tier, decision, decided_by, scope | 收到裁决或 Grant 命中 |
| `skill.escalation.denied` | skill, tier, decided_by | 拒绝时 |

`decision ∈ {approve-once, approve-run, grant-run, deny}`;Grant 命中无确认请求,故
只有无配对 pre 的 post(`runner.py:941-956`)。Web 收件箱对 `kind == "escalation"`
渲染升权卡片(档位徽标、参数 JSON、选项按钮,L3 不显示 approve-run),调试时间线按
信号可读"谁、何时、批了哪次升权"。

### 4.8 关键取舍(为什么 A 不选 B)

- **三档,不是四档**:分界线必须客观可判定;"危险程度"主观分级正是 Android 权限组
  过度授权的教训(附录 A.3)。
- **推导,不是自报**:skill 自报 tier 会撒谎或过时;推导值永远反映真实权限面。
  代价:工具 `side_effect` 标错,档就错(见 §6)。
- **approve-once 不缓存**:E1 曾定义 once 档 Grant,最终实现为"本次即放行,不登记"
  ——批准语义越短,滥用面越小;`_consume_grant` 保留 once 消费即焚仅为兜底
  (`runner.py:1069-1089`)。
- **fail-closed 而非 fail-open**:无人可审时默认拒绝(P7)。代价:headless 嵌入必须
  装配 supervisor 通道才能用高档 skill。
- **不做 LLM-as-approver**:审批面必须是人或显式策略,否则注入者只需骗过两个模型
  (ESCALATION.md §8)。

## 5. 效果与验证

**测试证据。** 升权系统有两层测试,全部实跑通过(2026-08-02 复验,`pytest
tests/kernel/test_escalation.py tests/examples/test_workspace_janitor.py`,
**36 passed in 0.89s**):

- `tests/kernel/test_escalation.py`(**28 例**):推导矩阵与显式覆盖、升权判定 3×3 全矩阵、挂起→approve-once→恢复
  全流程(确认恰好一次,kind/options/urgency/params/requested/reason_hint 逐项断言)、deny 后子帧从未执行、拒绝后重试
  必再挂起、参数不合不发确认、同档/降权直通、无 supervisor fail-closed、checkpoint 含 `_pending_escalation` 且 resume
  重走闸门、干净 context 不变量(子帧 SYSTEM 不含父帧 prompt 标记、首条 USER 即参数 JSON)、≥L2 inline 硬闸、L3 禁
  `confirm: first`、L3 手工 approve-run 被打回重问、approve-run 登记 Grant 且二次不再问、Grant 双保险与 once 即焚、
  信号三枚载荷、grants 随 checkpoint 往返、spawn 闸四态、CLI kind 透传。
- `tests/examples/test_workspace_janitor.py`(**8 例**):全真工具零 mock,真实文件
  系统断言(approve-run 命中、L3 每次必问、deny 保文件、真 kill 进程、dry_run 一致性、
  干净 context)。

**真实示例。** `examples/workspace_janitor` 是四技能三档剧情的全真演示:L1 巡检根 → L2 写计划(连调两次,
演示 approve-run 第二次不问)→ L3 真删文件(每次必问,dry_run 预览)→ L3 真停 `sleep` 演示进程;爆炸半径
由 run 的 workdir 沙箱圈住。CLI、Web 收件箱升权卡片、`kill -9` 后 resume 重走闸门三种玩法见该目录 README。

**涟漪效应。** 推导档成为其他子系统的输入:Skill Lab 编辑器实时显示推导档及来源,
提交闸门 G3 落地 L2 `reversal` / L3 `blast_radius` 必填 lint(E3 部分);数据层
authZ 与升权构成"读/写/泄露"三闸;`spawn_frame` 闸补齐了 E1 的绕道口子。

## 6. 局限性与边界

- **返回路径 provenance 标记未实现**。ESCALATION.md §3 设计了 `[ESCALATED:skill@version]`
  结果标记(执行摘要 §5.2 图中亦沿用),但源码中不存在该标记——升权子帧的 result
  目前与普通 tool result 同形,审计区分只能靠信号流。以代码为准:已设计未实现。
- **升权帧的 principal 注解未实现**。§4 设计升权帧内 `ToolContext.principal` 填
  `{"escalated": true, ...}`;现状 principal 只透传数据层身份
  (`tools/local_registry.py:255`),高层工具并不知道自己在被授权上下文里跑。
- **授权粒度是"跨层这一刻",不是参数级**。approve-run 放行本 run 内同 skill 的全部
  后续调用,不区分参数:一次批准的"写计划"可以用于写任何 path。参数级授权是明确的
  非目标。
- **确认疲劳是设计选择的代价**。L3 每次必问,N 次删除 N 次弹窗;设计拒绝批量授权、
  自动批准、跨 run 持久授权与 LLM 审批(§8),代价是交互吞吐与 headless 可用性门槛。
- **推导档的正确性依赖工具标定**。未注册工具与伪工具按 none 计(`escalation.py:64-73`);
  工具作者把 `side_effect` 标低,闸门就少拦一道;lint 只按命名模式提醒,不能证明语义。
- **同档信任是流程约定,内核不验证**。"同档不重新确权"的前提是同档成员过了同一套
  生产标准(TIER-STANDARDS.md);E3 的严格化 lint 只部分落地(G3),审计面板待做。
- **干净 context 同时挡住了有用上下文**。父帧的调查发现进不了子帧,必须经参数传递,
  受 inputs schema 的类型与尺寸约束——高层 skill 的"知情度"上限由 schema 决定。
- **拒绝不熔断**。deny 后重试同一调用会再次挂起(防"磨到批准"的必要语义),但没有
  速率限制,被注入的 LLM 可以持续轰炸收件箱。
- **闸门不管"谁能启动 run"**。根技能由宿主直接启动不过闸;启动权限是宿主与数据层
  authN 的事,升权系统不防御"滥用宿主启动 L3 根技能"。

## 7. 引用

- 设计文档:`docs/ESCALATION.md`(v0.3,本章基准)、`docs/TIER-STANDARDS.md`、
  `docs/SUPERVISOR.md`、`docs/DATA-AUTHZ.md`、`docs/SKILL-INLINING.md` §2.3
- 契约层:`agent_os/src/agent_os/api/v1/escalation.py`、`api/v1/tools.py:118-136`、
  `api/v1/frames.py:98-101`(帧 tier 字段)
- 内核:`agent_os/src/agent_os/kernel/runner.py:839-1189`(`_invoke_skill`、
  `_confirm_escalation`、`_consume_grant`、`_settle_pending_escalation`、`spawn_frame`)、
  `kernel/run.py:20`、`skills/manifest.py:120-122`、`supervisor/manager.py:163`
- 测试:`agent_os/tests/kernel/test_escalation.py`(28 例)、
  `agent_os/tests/examples/test_workspace_janitor.py`(8 例);示例:
  `agent_os/examples/workspace_janitor/`(README.md、skills.yaml、make_fixture.py)
