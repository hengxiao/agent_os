# Skill 升权系统 · 设计稿

> 版本:v0.2(设计;三档信任层级 + 附录 A 移动 OS 权限模型启示)
> 对象:Agent OS 内核(runner / skills / supervisor / context)
> 关系:权限基线见 `DESIGN.md` §8;确认通道复用 `SUPERVISOR.md` v2;
>   帧隔离现状见 `SKILL-INLINING.md` §2.3;UI 权限徽标契约 `--perm-read/write/net/exec`
> 核心原则(用户给定,逐条对应 §3):
>   1. 调用更高权 skill 必须显性给出符合该 skill 规定格式的参数;
>   2. 进入更高权限 skill 时,如需要,通知用户确认后等待;
>   3. 确认后 context 暂存后隔离,重建新 context 进入下一层;
>   4. 新 context 只含系统默认 prompt + 高层 skill prompt + 符合格式的参数输入;
>   5. 以此降低跨层 prompt injection 风险。
> v0.2:信任层级由两档(standard/elevated)改为三档(§2.1,按副作用可逆性分级),
>   确认强度随档收紧:L2 可逆 → 允许 approve-run;L3 不可逆 → 每次必须人审;
>   新增分档工程标准(§2.3):不同档按不同标准 design/implement/test。
> v0.3:E1 已实现(内核升权闸 + 干净 context 不变量,734 测试全绿);
>   §2.2 根帧 tier 修正为"直接能力档"(完整推导档会使闸门成为死代码)。

---

## 1. 问题与威胁模型

skill 嵌套调用时,父帧的内容(用户输入、工具观察、网页/文件读取结果)都可能
携带注入载荷。现状(`kernel/runner.py:820-874`)下,子帧虽然拥有独立的
`FrameContext.messages`,但:

- **权限边界是静态的**:子技能按自己的 manifest 白名单执行,父帧声明什么不影响
  子帧;一个被注入的低层 skill 只要能叫到高层 skill 的名字,就能借它的白名单
  执行高危工具(`system.shell.exec` 等 EXEC 档)。
- **确认通道由 LLM 发起**:`ask_supervisor` 是模型主动提问,内核没有
  "判定这次调用越权 → 强制挂起等待人审"的路径。
- **授权无回写通道**:即使人批准了,`ToolDispatchContext.allowed_tools` 在
  dispatch 时现取 manifest(`runner.py:528-579`),没有"本次批准临时放宽"的洞。

威胁模型:不可信内容(工具返回、用户输入、被攻破的低层 skill 输出)诱导 LLM
发起对高层 skill 的调用,借高层 skill 的权限执行恶意操作。升权系统要确保:
**任何进入更高权限层的调用,参数是结构化的、人(或策略)看过的、执行环境是
不被低层内容污染的。**

### 非目标

- **不管机密性(数据访问控制)**:数据能不能读、能读哪些,由**数据层的
  authN+Z** 完成(设计见 `DATA-AUTHZ.md`)——以 run 的 principal(启动 run
  的人/调用方身份)为判据,与 skill/帧无关。升权系统闸的是**副作用与执行权**
  ("会不会改世界"),不是"能看到什么"。因此 L1(读取档)的判据里不含
  机密性维度(见 TIER-STANDARDS.md §0)。这也带来一个连贯的三闸模型:
  **读 = 数据层 authZ;写 = 档位升权;泄露 = 写闸兜底**——把读到的机密
  发出网(NET 发送)是副作用,照样撞 L2/L3 闸门,读授权不意味着能外泄。
- 不防御高层 skill 自身 prompt 被污染(那是 skill 作者的供应链问题,交给
  Provenance/SkillArtifact 信任管线,M6);
- 不做细粒度到单个工具调用的普遍人审(那是 `ToolSpec.confirm` 的活,§8.2 未实现,
  升权系统只闸"跨层"这一刻);
- 不做自动升权:没有确认通道之外的权限放宽路径。

## 2. 定义:什么是"升权"

### 2.1 信任层级:三档,按副作用可逆性分级

层级**不按"危险程度"这种主观度量分,按副作用的客观语义分**(用户给定):

| 档 | 值 | 副作用语义 | 例 | 确认强度 |
|---|---|---|---|---|
| L1 | `none` | **无副作用**:纯读取、纯计算,不改变任何外部状态 | 数据读取、检索、格式化、推理 | 不确认(现状行为) |
| L2 | `reversible` | **有副作用,但外部副作用可逆或可容忍** | 数据库写入、文件写入、发消息(可撤回/可补偿)、改配置 | `first`:本 run 首次调用确认,批准后 approve-run 放行 |
| L3 | `irreversible` | **有副作用,且外部副作用不可逆且不可容忍** | 删除数据、删除 VM、停止进程、转账、对外发布 | `always`:**每次调用都必须人审** |

三档恰好够了:L1/L2 的分界是"会不会改世界",L2/L3 的分界是"改了能不能
挽回"——两条都是客观可判定的线,不需要第四档。

**tier 的声明与推导(关键设计决定):**

- **工具(tool)声明**:tool 作者对自己的工具副作用最清楚,`ToolSpec` 新增
  `side_effect: "none" | "reversible" | "irreversible"`。缺省从既有
  `Permission` 枚举推导:READ→none,WRITE/NET→reversible,EXEC→irreversible
  (EXEC 是任意命令,按最坏的算;工具作者可显式下调,如只读的诊断 exec)。
  既有 `fs.delete` / `system.process.kill` 类工具必须标 irreversible——
  lint 检查命名模式(delete/kill/stop/remove)提醒作者。
- **skill 推导,不声明**:skill 的 tier = 其 `permissions.tools` 与
  `permissions.skills` 里最高的一档(取 max)。**不让 skill 作者自报**——
  自报会撒谎或过时,推导值永远反映真实权限面。manifest 里的 `trust` 块
  只保留确认策略的覆盖项:

```yaml
# skills.yaml 片段
- name: system.admin.deploy
  kind: prompt
  trust:
    confirm: always         # 可选:always | first;缺省按推导档(L2→first,L3→always)
  permissions:
    tools: [system.shell.exec, fs.write]   # 推导 tier = irreversible(exec)
    skills: []
```

- `confirm` 只能**上调**不能下调:L3 推导档的 skill 写 `confirm: first`
  是无效的(lint 报错)——不可逆操作不允许批量授权,这是硬性安全面。

### 2.2 升权事件(escalation)

`runner._invoke_skill`(现 `runner.py:820-874`)在通过白名单检查后、
`make_frame` 之前,比较调用帧与被调 skill 的推导档:

```
escalation = tier_of(target) > tier_of(caller_frame)
```

即:**从低档帧调用高档 skill = 升权事件**;同档移动不确认(同档内部的
可信度由作者负责);**高档调低档是降权,永不确认**。

帧的 tier:子帧继承被调 skill 的**完整推导档**(进入即继承整个已审信封,
信封内同层移动不再确认);**根帧 = 根 skill 的直接能力档(只看自己的
tools,不沿 skills 递归)**——run 启动只确认了根技能的直接能力面,经子
技能间接够到更高档必须过闸。

> 实现注(E1):若根帧也按完整推导档(含 skills 递归),则被调 skill 必在
> 调用方白名单内,恒有 `tier(target) ≤ tier(caller)`,升权事件在数学上
> 永不触发,闸门成为死代码。目标侧与 lint 仍用完整推导档(薄 orchestrator
> 不能靠转包一层洗档);根帧取直接能力档,闸门才有意义。

根技能由宿主直接启动,不经过升权闸门——人用 `--skill system.admin.deploy`
启动 run,本身就是确认(确认的是根技能的直接能力面,不是它能间接够到的
整张图)。

### 2.3 分档工程标准(design / implement / test)

不同档的 skill 和 tool 按不同标准生产。档位越高标准越严,且**高档标准
累积包含低档的全部要求**。同档不重新确权的前提是:同档成员都过了同一套
生产标准,可信度才相等。

**L1(none)— 现状基线:**

- design:inputs/outputs JSON Schema 齐全(现状契约);description 写成
  "Use when / Do not use when" 路由规则(现状 lint)。
- implement:无附加要求。
- test:manifest lint + schema 校验单测(现状)。

**L2(reversible)— 附加"可逆性证明":**

- design:manifest 新增 `trust.reversal` 字段,**必填**,写明该档副作用的
  逆转/补偿机制(写入有 `.bak` 备份、消息可撤回、DB 变更附回滚 SQL)。
  声称可逆却写不出逆转机制的,档应上调 L3——这是 L2 档的诚实闸门,
  lint 检查非空。
- implement:**幂等性**。approve-run 意味着同一 skill 会被反复放行,
  重试/重复调用不得重复施加副作用(幂等键、upsert 语义、写前查重)。
- test:附加**副作用 containment 测试**——副作用不越出声明面(fs 走
  `resolve_work_path` 沙箱、DB 工具只碰声明的库表);**幂等性测试**——
  同参数连调两次,外部状态等价于调一次。

**L3(irreversible)— 附加"所见即所毁":**

- design:破坏性工具必须支持 **dry-run/preview**(`dry_run: true` 参数
  返回将影响的清单),确认卡片可直接展示"将删除的 3 个 VM";manifest
  新增 `trust.blast_radius` 字段,**必填**,写明最坏影响面(删什么、
  影响哪些下游);参数全类型化,删除类操作必须指名目标。
- implement:空目标/通配目标**报错而非"全部"**;dry_run 与真实执行
  **共享同一份目标解析逻辑**——防"预览一套删一套"。
- test:在 L2 基础上——流转测试断言**不存在 approve-run 路径**(连调
  两次必出两次 pending);dry_run 与真实执行的目标一致性测试;
  **注入滥用场景测试**——构造注入载荷作参数,断言它停在确认闸门
  而非到达执行。

tool 级 `side_effect` 声明(§2.1)让同一套标准适用于 tool 的生产:
L2/L3 工具的 design/implement/test 要求与同名档 skill 一致。

> 本节是纲要;可执行的完整标准(定档判定树、逐档规范、PR checklist、
> 反模式)见 `TIER-STANDARDS.md`。

## 3. 升权流程(核心原则逐条落地)

```
父帧(L1 低档)               内核 runner                     宿主(Web/CLI 收件箱)
    │  skill.admin.deploy({...})    │                               │
    ├──────────────────────────────▶│                               │
    │                          ① 白名单检查(现状)                   │
    │                          ② 参数预校验(原则 1)                 │
    │                          ③ 判定:升权事件(低档→高档)           │
    │                          ④ 父帧挂起 + checkpoint(原则 3 暂存) │
    │                          ⑤ 发结构化 EscalationRequest ────────▶│
    │                                                               │ ⑥ 用户看到:
    │                                                               │    谁调谁/档位/参数/权限集
    │                          ⑦ 批准/拒绝            ◀─────────────┤   approve-once
    │                          (原则 2:确认后等待)                    │   L2 另有 approve-run
    │                          ⑧a 批准 → 隔离新帧(原则 3/4)          │   deny
    │                          ⑧b 拒绝 → 父帧错误观察                 │
    │◀── 子帧 result 折叠为 tool result ──                          │
```

### 原则 1:显性参数,先校验后确认

升权调用必须先通过被调方 `inputs` 的 JSON Schema 校验(现 `make_frame`
`skills/local_file.py:250-256`),**校验失败不产生确认请求**——直接返回父帧
INVALID_ARGS 错误观察(现状行为)。确认请求里展示的参数就是校验过的、将
原样注入子帧的那份 JSON,用户审的就是要执行的,不存在"审一套跑一套"。

对 L2/L3 skill 的 lint 加强(`skills/manifest.py` `validate_manifest`):
`inputs` 必须非空且每个参数有 `type`——高层 skill 不收自由文本参数,
这是"符合规定好的参数格式"的强制面。

### 原则 2:确认后等待(复用 supervisor 挂起闭环)

确认**不由 LLM 发起**,是内核判定升权后强制走的路径。复用 ask_supervisor
的完整闭环(pending 落盘 → 挂起 await → 答案写回 → resume 重入),但请求是
结构化的新类型而非自由文本:

```jsonc
// EscalationRequest(进 /api/supervisor/pending,kind 区分于普通 question)
{
  "kind": "escalation",
  "question_id": "esc-...",
  "run_id": "...", "frame_id": "父帧",
  "skill": "system.admin.deploy",
  "tier": "irreversible",               // 被调 skill 的推导档(none/reversible/irreversible)
  "params": { /* 已通过 schema 校验的调用参数 */ },
  "requested": { "tools": ["system.shell.exec", "fs.write"], "skills": [] },
  "reason_hint": "none → irreversible",   // 机器生成:调用帧档 → 目标档,不是 LLM 写的
  "options": ["approve-once", "deny"],     // L3 无 approve-run;L2 才有
  "asked_at": 1730000000
}
```

答案语义:
- `approve-once`:仅放行本次调用(所有档都有);
- `approve-run`:**仅 L2 档**——本次 + 本 run 内同 skill 的后续调用(对应
  `confirm: first` 的缓存粒度)。**L3 永不提供此选项**:不可逆操作不允许
  批量授权,每一次删除 VM/停止进程都必须单独过人眼,这是三档模型里
  最硬的一条规则;
- `deny`:父帧收到 PERMISSION_DENIED 错误观察(与现状白名单拒绝同形,
  LLM 可据此改道,但重试同样的调用会再次挂起确认——防"磨到批准")。

Web 收件箱(SUPERVISOR.md §2.2 的 InboxChannel)与 CLI 协议
(stderr 协议行 + stdin 作答,§2.3)天然承接——pending 项多一个 `kind` 和
结构化载荷,UI 对 `kind == "escalation"` 渲染专门卡片(参数 JSON、权限集
徽标、选项按钮),普通问答现状不变。

> 实现注(E2):`spawn_frame`(§3.4 后台帧)同样过闸——语义与 `_invoke_skill`
> 一致(预校验 → 判定 → 挂起/Grant/放行/拒绝),确认等待发生在 spawn 调用点
> 本身(await 裁决后才 `create_task`),父帧不挂起的设计不变;spawn 不走 LLM
> 分发、没有 tool result 观察通道,拒绝/参数不合以 `SkillLoadError` 上抛
> (与白名单拒绝同形,交 code 技能处理)。

### 原则 3/4:暂存、隔离、干净 context

现状已经几乎就是目标形态,设计将其**明文化为不变量**:

1. **暂存**:父帧在发确认请求时挂起,`frame.context.working["_pending_escalation"]`
  落盘(照 `_pending_ask` 的 checkpoint 模式,`runner.py:585-627`),run 崩溃/
  重启后 resume 重问或带答案重入,语义确定。
2. **隔离**:子帧 = 新 `FrameContext`,messages 只有一条
  `USER(input, source=PARENT_INPUT)`(现 `local_file.py:257-273`)。
   父子不共享 messages(§2.3 隔离原则),父帧的对话历史、工具观察、注入载荷
   **物理上不在子帧 context 里**。
3. **干净 context 不变量**(新 context 的组成白名单):
   - SYSTEM = 系统默认段 + `render_prompt(target_skill.prompt, input)`
     (现 `context/manager.py:99-152` 的组装路径,父帧 prompt 天然不进);
   - 首条 USER = 经 schema 校验的参数 JSON(原则 1 的同一份);
   - 工具 schema = 目标 skill 自己的白名单 + 伪工具;
   - **没有了**。任何想进高层 context 的东西必须走这三扇门之一。
4. **inline 硬闸门**:推导档 ≥ L2 的 skill **禁止 `inline: true`**(lint 报错)。
   inline 会把指令段合并进调用帧(manager.py:154-201),对升权 skill 而言
   这等于绕开隔离墙直接住进了别人家里——干净 context 不变量被 inline
   破坏,必须堵死。低层 skill 引用的 inline 段只来自低层,不构成跨层污染。

### 返回路径:高层结果回到低层时降级为数据

子帧 result 折叠为父帧的 tool result(现状)。对升权帧,result 文本前加
provenance 标记(`[ESCALATED:system.admin.deploy@1.2.0]`,照压缩标记
`[COMPRESSED]` 的先例):提醒父帧 LLM 这是高层执行结果,同时让审计/重放
能区分。outputs schema 校验(现 `_check_output`)不变。

prompt injection 风险账:低层内容进高层只剩"schema 校验过的参数"一条窄道;
高层结果回低层被标记为数据,低层 LLM 即便被注入,下一轮再调高层仍会撞
确认闸门(L3 每次必撞)或撞上用户已拒绝的事实。**注入者无法自己批准自己。**

## 4. 授权回写(grant)

批准结果需要一个能影响 dispatch 的落点(现状空白)。引入 `Grant`:

```python
# api/v1/escalation.py(新)
@dataclass(frozen=True)
class Grant:
    skill: str
    tier: str                            # 批准时被调 skill 的推导档(快照)
    scope: Literal["once", "run"]        # 对应 approve-once / approve-run
    decided_by: str                      # "user:web-inbox" | "user:cli" | ...
    decided_at: float
```

- 存放:`run` 级 `grants: list[Grant]`(RunContext 新字段,随 checkpoint 序列化,
  保证 replay 确定);`once` 档消费即焚。
- 消费点:`_invoke_skill` 的升权判定处——目标档为 L2 且存在 `run` 档
  Grant 时跳过确认直接放行(仍记信号)。

> 实现注(E2):存放点落在内核侧 `kernel/run.py` 的 `Run.grants`(契约层
> `Run` 不动,checkpoint `run.grants` 字段 additive);`approve-once`
> **不登记** Grant——它的"本次"就是发牌这一刻,直接放行,下次同调用必
> 再撞闸;`once` 档的"消费即焚"路径留在 `_consume_grant` 兜底(崩溃残留
> /手工构造的 once Grant 命中即移除)。
- **run 档 Grant 只对 L2 有意义**:L3 的 EscalationRequest 不提供
  approve-run 选项(§3 原则 2),即使有人手工构造答案,消费点也只对
  `tier == "reversible"` 的 Grant 生效——双保险。
- **Grant 只放行"进入更高档 skill"这一动作,不修改任何 manifest 白名单、
  不放宽 `ToolPolicy.max_permission`**。高层 skill 的工具权限是它自己
  声明的、人审过的静态面;升权系统闸的是"低层能不能进来",不是"高层能干
  更多"。 EXEC 档工具的人审(`ToolSpec.confirm`/HumanApproval,§8.2 未实现)
  是独立的一层,不在本设计范围。
- `ToolContext.principal`(现恒 None,tools.py:121)在升权帧内填
  `{"escalated": true, "tier": ..., "granted_by": decided_by}`——高层工具
  知道自己在被授权的上下文里跑,审计信号可关联。

## 5. 信号与审计

新信号(照 `pre:skill.invoke` 先例,runner 发射,sidecar/Web 可订阅;**E2 已实现**):

| 信号 | 载荷 | 时机 |
|---|---|---|
| `pre:skill.escalate` | {skill, tier, frame_id, params, requested} | 确认请求发出时 |
| `post:skill.escalate` | {skill, tier, decision, decided_by, scope} | 收到批准/拒绝 |
| `skill.escalation.denied` | {skill, tier, decided_by} | 拒绝(父帧收 PERMISSION_DENIED) |

> 实现注(E2):Grant 命中放行时无确认请求,故只发 `post:skill.escalate`
> (`decision="grant-run"`,无配对 pre);`decision` 取值
> `approve-once | approve-run | grant-run | deny`。

run 详情页信号流直接可读:谁、何时、批了哪次升权、档位与 scope 是什么。
Web 调试台的时间线(`.dbg-row`)为 escalation 行加 kind=`escalation`,
主题系统按信号色渲染(这是 UI 侧唯一接入点,组件零分支不变)。

## 6. 对现有代码的改动面

| 模块 | 改动 |
|---|---|
| `api/v1/tools.py` | `ToolSpec.side_effect` 字段(none/reversible/irreversible;缺省按 Permission 推导:READ→none,WRITE/NET→reversible,EXEC→irreversible);`ToolContext.principal` 在升权帧填充 |
| `api/v1/skills.py` | `SkillTrust{confirm, reversal, blast_radius}` + `SkillManifest.trust` 字段(冻结面新增,向后兼容);skill 推导档 = 白名单内工具/技能最高档 |
| `api/v1/escalation.py`(新) | `Grant`、`EscalationRequest` 数据类;tier 推导与比较 |
| `skills/manifest.py` | trust 块解析;分档 lint(§2.1/§2.3):L2/L3 inputs 必带类型、L2 `reversal` 必填、L3 `blast_radius` 必填、推导档 ≥L2 禁 inline、L3 禁止 confirm: first、delete/kill/stop 类工具名提醒标 irreversible |
| `kernel/runner.py` `_invoke_skill` | 升权判定(低档→高档)→ 参数预校验提前 → 挂起/Grant 消费/放行三态 |
| `supervisor/manager.py` | `ask_structured(EscalationRequest)` 入口(复用 pending/超时/options 闭环) |
| `host/web/app.py` | `/api/supervisor/pending` 条目透传 `kind` 与结构化载荷(已 dict 直通,改动极小) |
| `host/web/static/js/components/inbox*.js` | `kind=="escalation"` 渲染升权卡片(档位徽标 + 参数 JSON + 选项;L3 不显示 approve-run) |
| `api/v1/frames.py` / RunContext | `grants` 字段 + checkpoint 序列化;帧 tier 继承 |
| 测试 | tier 推导矩阵 / 升权判定矩阵(3×3)/ 挂起-批准-恢复 / 拒绝路径 / L3 无 approve-run / Grant once 即焚 / replay 确定性 / 干净 context 不变量(断言子帧 messages 恰为 [USER(input)] 且 SYSTEM 不含父帧内容)/ ≥L2 inline 硬闸门 |

## 7. 分期

| 期 | 内容 |
|---|---|
| E1 ✅ | 三档推导 + 升权判定 + 挂起确认(复用 inbox)+ approve-once/deny + 干净 context 不变量测试。已实现:`api/v1/escalation.py`、`_invoke_skill` 升权闸、分档 lint 硬闸门(≥L2 禁 inline、L3 禁 confirm:first)、checkpoint/resume 重走闸门;734 测试全绿 |
| E2 ✅ | L2 的 approve-run Grant + 信号三枚 + Web 升权卡片 + CLI 答案透传 + `spawn_frame` 升权闸(E1 遗留的绕道口子)。已实现:内核 `Run.grants` 随 checkpoint 往返、`_consume_grant` 消费点(双保险仅 L2)、options 按档区分(L2 三枚/L3 两枚)、inbox.js `escalationCardHtml`(copy 六主题同步)、CLI `kind` 透传;746 Python + 21 前端测试全绿 |
| E3 | 审计面板(按 run 列升权事件)+ lint 严格化(reversal/blast_radius 必填)+ 文档(DESIGN.md §8 引用更新)。**部分落地**:reversal/blast_radius 必填 lint 已于 Skill Lab 提交闸门 G3 落地(docs/SKILL-DEV.md §1.4,L2 期;`skills/gate.py`),审计面板与 DESIGN.md 引用更新待做 |

> 完整示例(全真工具、零 mock,三档剧情:approve-run / L3 每次必问 / deny /
> 崩溃恢复)见 `agent_os/examples/workspace_janitor`。

## 8. 不做

- 不做自动升权/策略引擎自动批准(策略批 = 没人批,违背原则 2;将来要加也是
  独立的 PolicyEngine sidecar,且只对 L2 白名单内的 low-risk 组合;
  **L3 永远只对人生效**,这是不可逆档的底线);
- 不做跨 run 持久授权("永远信任某 skill")——授权随 run 死亡,下次重新确认;
- 不做第四档及以上的信任层级——"会不会改世界 / 改了能不能挽回"两条客观线
  分出三档恰好够,档越多边界越模糊;
- 不做 skill 自报 tier——档由权限面推导,作者只能声明工具的副作用语义和
  上调确认强度,不能把自己说低;
- 不做 LLM-as-approver(让另一个模型审批升权)——审批面必须是人或显式策略,
  否则注入者只需骗过两个模型;
- 不动 `ToolSpec.confirm` / HumanApproval(§8.2 工具级人审)——那是独立一层,
  本设计不越俎代庖;
- 不把高档当"超级用户 shell":高档 skill 的权限面仍是自己 manifest
  的白名单 + run 级 `max_permission` 上限。

---

## 附录 A:移动 OS 权限模型的启示(调研,2026-08)

Android 与 iOS 的权限管理是同一骨架的两条演化路线:**清单静态声明 → 运行时
用户同意 → 沙箱强制隔离**。本设计的每条核心取舍都能在两家的二十年教训里
找到出处。

### A.1 Android

- 三级保护级别:`normal`(低风险,安装即授)、`dangerous`(隐私资源,API 23
  起运行时逐项弹窗)、`signature`(同签名才授)。第三方可自定义
  `protectionLevel`,是"静默同意"过度授权的重灾区
  ([arXiv: Silent Consent, Persistent Risk](https://arxiv.org/html/2605.27667v1))。
- **权限组的历史教训**:早期按组授权(批一个=批一组),用户无法理解授权范围,
  Android 10 起逐步拆细为单项——粒度必须落在用户能理解的单位上。
- 沙箱:每 app 独立 Linux UID + SELinux 强制访问控制。
- 持续收紧([Permissions updates in Android 11](https://developer.android.google.cn/about/versions/11/privacy/permissions?hl=en)):
  Android 11 **一次性权限**("Only this time",进程死即失效)+ **auto-reset**
  (闲置数月自动撤销已授权限);Android 12 模糊位置与使用指示器;
  Android 13 通知运行时授权;Android 14 照片部分访问。
- 特殊权限(悬浮窗、安装未知应用)不进普通弹窗,必须跳设置页手动开——
  高危操作的摩擦是刻意设计的。

### A.2 iOS / macOS

- **TCC(Transparency, Consent, Control)**:按"app × 服务"的规则库集中管理,
  首次访问触发一次性弹窗,无命令行接口,只能在系统设置里改
  ([Explainer: Permissions, privacy and TCC](https://eclecticlight.co/2025/11/08/explainer-permissions-privacy-and-tcc/))。
  **attribution chain**:权限责任沿调用链归属到主 app。
- **Purpose strings**:`Info.plist` 的 `NSCameraUsageDescription` 等用途说明
  在弹窗中展示;缺 key 则访问即 crash + App Review 拒审
  ([OWASP MSTG](https://github.com/MobSF/owasp-mstg/blob/master/Document/0x06h-Testing-Platform-Interaction.md))——
  "声明即用户可见理由"的硬约束,但内容是开发者写的自由文本,可以糊弄。
- Entitlements 烤进代码签名,用户不可改;沙箱限定文件访问;SIP 连 root 都挡。
- 细粒度授权:Allow Once、模糊位置、Limited Photos、粘贴板提示。
- 声明面扩大:App Tracking Transparency、Privacy Manifest(强制声明收集的
  数据类型与 required-reason API),系统 + App Review 双闸执行。

### A.3 对本设计的逐条映射

| 移动 OS 经验 | 本设计对应 | 差异 |
|---|---|---|
| 声明 + 运行时确认双层 | manifest 白名单(静态)+ 升权确认闸门(运行时) | 一致 |
| 一次性权限(Only this time / Allow Once) | `approve-once` | 一致 |
| auto-reset 闲置撤销 | Grant 随 run 死亡,不跨 run 持久 | 我们更激进:无豁免通道 |
| 权限组→过度授权→拆细的教训 | tier 只设三档,档内粒度留在逐项白名单 | 我们用副作用语义划线,比"危险/普通"的主观分级更可判定 |
| purpose string(自由文本理由) | EscalationRequest 展示 schema 校验过的结构化参数 + 机器生成的 reason_hint | 我们更硬:作者无法美化要执行的内容 |
| attribution chain(责任归属) | `decided_by` / `ToolContext.principal` 回写 | 一致 |
| 特殊权限跳设置页(高危摩擦) | L3 每次必人审、无 approve-run | 一致的哲学:摩擦与不可逆性成正比 |
| —(移动 OS 无对应物) | 干净 context 不变量 | **我们独有的闸面**:移动 OS 闸资源访问,不管 app 内部状态;LLM agent 的威胁是 context 污染,必须闸"进入执行环境"本身 |
