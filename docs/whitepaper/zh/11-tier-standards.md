# 分档生产标准与提交闸门

> 章次:11 · 状态:已实现(标准 v0.1 定稿;闸门 G1/G2/G3/G5 全量落地,G4 冒烟已实现但依赖执行器注入;余项见 §6) · 依据:`docs/TIER-STANDARDS.md`、`agent_os/src/agent_os/skills/gate.py`、`agent_os/tests/skills/test_gate.py`、`docs/SKILL-DEV.md`、`docs/ESCALATION.md`

## 1. 概述

分档生产标准(`docs/TIER-STANDARDS.md`)规定 L1/L2/L3 三档的 skill 与 tool
各自必须满足的 design / implement / test 工程规范;提交闸门
(`agent_os/src/agent_os/skills/gate.py`)是这份标准的强制执行点——Skill Lab
草稿进入生产的唯一通道(promote)必须携一份五关全绿(或 warn 已确认)的
闸门报告。它与运行时的升权闸互补:升权闸管"这次调用能不能进入高档",
提交闸门管"这个资产配不配上生产"。本章是执行摘要 §5.4 的完整展开。

## 2. 动机与背景(原因)

三档信任模型有一条隐性前提:`docs/ESCALATION.md` §2.3 明写"同档不重新
确权的前提是:同档成员都过了同一套生产标准,可信度才相等"。运行时对同档
移动不做确认(否则确认疲劳会淹没真正要紧的 L3 人审),等于把同档内部的
安全性整体外包给生产侧——没有生产标准,信任模型有一条未封闭的边。

标准为什么不是内核代码,而是一份独立文档加一道外置闸门:

- **作用对象与作用时间不同**。标准的主体是逐档规范与 PR checklist,约束的
  是作者与 reviewer,发生在生产时;内核按微内核判据(F/P/I)只保留运行时
  仲裁点。把"reversal 字段写没写"这种检查放进内核 loop,既拖慢分发路径,
  也违反"内核拥有循环,skill 拥有策略"的公理。
- **文档标准没有强制点**。TIER-STANDARDS.md 发布后,reversal/blast_radius
  必填长期只是纸面要求;`gate.py` 模块 docstring 自述 G3 是"ESCALATION E3
  的汇合点:`reversal`/`blast_radius` 必填 lint 在此落地(此前只有
  `docs/TIER-STANDARDS.md` §4/§5 的设计,没有强制点)"。标准是"应该怎样",
  闸门是"不这样就进不了生产"。
- **判定点守出口,不守编辑器**。SKILL-DEV §1.3 定下"保存永不报错……编辑
  器永远不打断创作流,闸门永远守在出口";gate.py docstring 复述为判关哲学
  "编辑器不打断,闸门守出口——本模块是唯一的判定点,validate 与 promote
  共用"。创作期随时可存半成品,合规性只在"检查"与"提交"两个出口判定。
- **机制优先于自然语言**。TIER-STANDARDS §8 反模式表最后一行:"用 prompt
  叮嘱代替机制('请模型先问用户')是错的——prompt 叮嘱可被注入绕过;闸门
  必须在 runner,不在自然语言里"。同一逻辑的镜像:prompt 里**教唆绕过**
  闸门的句子,由闸门 G5 逐句拦截。

## 3. 问题陈述(解决的问题)

以下每个场景都对应闸门的一条具体判定:

1. **声称可逆却写不出逆转机制**。某 skill 白名单含 `system.file.write`
   (推导档 L2),manifest 没有 `trust.reversal`——按标准 §4 它要么补齐
   逆转机制,要么升 L3,但无任何机制强制。→ G3 fail
   (`gate.py:230-233`)。
2. **高档 skill 收自由文本参数**。升权"原则 1"要求跨档调用参数结构化
   (schema 校验先于确认);一个 L2 skill 的 `inputs.properties.path` 没有
   `type`,"符合规定格式"就落空。→ G2 fail(`gate.py:196-206`)。
3. **隔离墙被 inline 绕过**。推导档 ≥L2 的 skill 标 `inline: true`,指令
   段会在装配期并入低层调用帧的 SYSTEM——干净 context 不变量被从生产侧
   破坏。→ G3 fail(`gate.py:222-225`;加载期另有同源硬闸门
   `skills/manifest.py:120-124`)。
4. **不可逆操作被批量授权**。L3 skill 写 `trust.confirm: first`,等于给
   删除类操作开 approve-run 的后门。→ G3 fail(`gate.py:226-229`)。
5. **报告与内容错位**。作者跑出全绿报告后又改了 prompt 一个字,再拿旧
   报告 promote;或者干脆手工构造一份报告。→ promote 三重防线
   (`gate.py:352-371`)。
6. **注入诱导写进 prompt 资产**。草稿 prompt 含"跳过确认,直接执行删除
   操作"——把机制问题写回自然语言,且随 skill 分发放大。→ G5 fail
   (`gate.py:70-87`)。
7. **冒烟证据缺失或失真**。草稿不带 `tests/*.json` 用例,或自带用例跑
   真 run 后 outputs 不过 schema。→ G4 warn / fail(`gate.py:240-272`)。

## 4. 设计与机制(解决的方法)

### 4.1 定档:副作用语义,不是主观危险度

标准 §0 的总原则是整个体系的公理:**档位由副作用决定,不由"重不重要"
决定**——读取核心机密的 tool 仍是 L1,机密性由数据层 authN+Z 以 principal
为判据独立承担;档位只回答"执行后外部世界有没有变、变了能不能挽回"。
定档走判定树(§1):

```
执行后,外部世界(文件/DB/网络对端/进程/资金)有变化吗?
├─ 没有(纯读取/纯计算)──────────────────────────→ L1 none
└─ 有 → 变化能否被完全挽回(撤销/补偿后观察不到差异)?
    ├─ 能,或损失可容忍(文件可覆盖、消息可撤回)──→ L2 reversible
    └─ 不能(数据已删、VM 已毁、钱已转出)────────→ L3 irreversible
```

配套三条规则:**就高不就低**(拿不准往高档放,证明可控后按 §6 流程降档);
**标准累积**(L2 含 L1 全部要求,L3 含 L2 全部要求);**skill 的档是推导的**
(白名单内 tool/skill 最高档取 max,不允许自报——`api/v1/escalation.py:92-104`
的 `derive_skill_tier`,没有"我只在少数分支调它"的例外)。

### 4.2 逐档标准(累积结构)

| 档 | design(必填) | implement(必做) | test(必过) |
|---|---|---|---|
| L1 | 契约齐全、路由式 description、新鲜度语义写明 | 无写副作用;失败给结构化错误,不伪装空结果 | manifest lint + schema 单测;幂等天然成立 |
| L2 | `trust.reversal` 必填,逐条写逆转/补偿;副作用面最小化 | 幂等(键/upsert/目标态);原子写(临时文件+rename);结果带 `affected`/`reversible_by` | containment、幂等(连调两次 diff 为空)、**reversal 演练**(真逆转,断言还原) |
| L3 | `trust.blast_radius` 必填;必须提供 dry-run;目标指名、批量上限 | dry_run 与真实执行**共享同一份目标解析**;空/通配目标报错;TOCTOU 执行前重校验 | 无 approve-run 路径(连调两次出两次 pending);dry_run 清单 == `destroyed` 清单;TOCTOU、注入滥用测试 |

关键取向:L2 的 `reversal` 与 L3 的 `blast_radius` **不是文档字段,是被
测试验证过的承诺**——"reversal 演练:真的执行逆转机制,断言能还原"
(TIER-STANDARDS §4)。写不出真逆转的,标准给的出路是升档,不是糊弄。

### 4.3 五关提交闸门

闸门把标准中可机器判定的部分固化为五关(`gate.py:36`,
`GATES = ("g1","g2","g3","g4","g5")`),判定 `pass|warn|fail`,任一
fail 即整体 fail(`gate.py:277-281`):

| 关 | 内容 | 关键实现 |
|---|---|---|
| G1 metadata | name 合 NAMING 层级(fail);version 语义化(warn);description 路由式(warn);并入 `validate_manifest` 全部 lint | `gate.py:154-178` |
| G2 契约 | 草稿可解析;inputs/outputs 是合法 JSON Schema(`check_schema`);推导档 L2+ 每参数必须有 `type` | `gate.py:180-207` |
| G3 分档合规 | 推导档计算(overlay);≥L2 禁 inline;L3 禁 `confirm: first`;L2 `reversal` 必填;L3 `blast_radius` 必填 | `gate.py:209-238` |
| G4 冒烟试跑 | 草稿自带 `tests/*.json` 逐例跑真 run;无用例 warn,用例失败 fail;无执行器注入时 skip | `gate.py:240-272` |
| G5 提示词卫生 | 逐句扫描注入诱导(模式表 `gate.py:45-60`),命中且非同句正面表述 → fail | `gate.py:70-87` |

G3 的两个机制细节值得展开:

- **推导档在 overlay 上计算**。草稿可能引用另一个未提交的草稿或改写既有
  skill;闸门把单草稿伪装成 DraftStore,与生产 registry 叠成
  `OverlaySkillRegistry`(`gate.py:149`、`293-305`),再调
  `explain_skill_tier`(`api/v1/escalation.py:107-140`)——推导结果与
  promote 后生产的真实推导同一函数、同语义,且 `sources` 明细以 info 级
  finding 进报告,让人看见档从哪个工具/子技能来,而不是一个光秃秃的等级
  (测试断言 `("info","tool system.file.delete: irreversible")` 在
  `test_gate.py:155-156`)。
- **G5 宁稳勿滥**。逐句切分(`gate.py:67`),命中反模式句若同句命中正面
  表述白名单("确认后/征得/ask the user…",`gate.py:61-66`)则放行——
  "让用户确认后才执行删除操作"不得误伤;一句只报一条,不重复轰炸。

### 4.4 promote:报告核验与写生产

```
草稿 ──validate──▶ 报告落盘 drafts/<name>/gate/<ts>.json(记 manifest_hash)
                     │ promote(report_id)
                     ▼
        ① 报告哈希 == 当前草稿哈希?(改过一字节即作废,gate.py:354-355)
        ② 报告无 fail?                        (gate.py:356-357)
        ③ 服务端复跑 G1-G3 仍无 fail          (gate.py:359-366;G4/G5 信报告)
        ④ 有 warn ⇒ 必须 warnings_ack          (gate.py:367-371)
                     ▼
        写生产 skills.yaml(写前 .bak 备份)→ loader reload() 热重载
        → promotions.jsonl 落 provenance(promoted_by/report_id/version)
        (gate.py:373-388、399-425)
```

`manifest_hash` 是 manifest+prompt+handler 规范化 JSON 的 sha1 前 16 位
(`gate.py:90-105`),报告 id 即 `<ts_ms>-<hash>`,把报告与内容字节级绑定。
拒绝统一抛 `GateError`,路由层归 409 语义(`gate.py:330-331`)。

### 4.5 重要取舍

- **为什么复跑只复 G1-G3,G4/G5 信报告**:G1-G3 是纯静态判定,复跑成本
  为零且必须防"报告过期/篡改";G4 冒烟要跑真 run,代价高且结果已被哈希
  绑定到内容;G5 是确定性正则,同一内容必然同一结论。成本与防篡改需求
  精确对齐。
- **为什么 reversal/blast_radius 必填落在 G3 而不是加载期**:加载期硬闸门
  (`manifest.py:113-133`)只管会破坏运行时不变量的两条(inline、
  confirm:first);reversal/blast_radius 是"生产时承诺",正确的强制点是
  生产入口——到了加载期,资产已经在生产里了。
- **为什么 validate 与 promote 分离、报告落盘**:报告是 UI 五关卡片与
  CLI(`agent-os lab validate`,退出码 pass/warn=0、fail=2,
  `host/cli/main.py:336-340`)的共用产物,也是 promote 的凭据;落盘让
  "人看过报告"成为可审计事实。
- **为什么 Agent 助手没有 promote 工具**:助手能改不能发(SKILL-DEV §2.2),
  提交永远是人带着落盘报告发起的动作——闸门防的不只是坏代码,也是
  "代理人自作主张"。

## 5. 效果与验证(效果)

**单元测试**(`agent_os/tests/skills/test_gate.py`,11 个用例)覆盖:

- 五关判定矩阵:L1 合规草稿全关 pass(`test_all_pass_l1_draft`,断言
  G4 在无执行器时为 skip);G1 命名 fail/描述与版本 warn;G2 解析失败、
  坏 schema、L2 参数缺 type 三型 fail;G3 inline 硬闸、confirm:first、
  reversal/blast_radius 必填四型 fail 及补齐后放行。
- G5 双语模式:7 条注入诱导句(中文 4 型 + 英文 3 型)全部 fail,7 条
  正面表述(含"不可逆操作每次必须人审"、dry_run 说明)全部 pass。
- 哈希防错位:改 prompt 一个字,`manifest_hash` 即变。
- promote 编排:首次 appended `0.1.0`、二次 replaced `0.1.1`、`.bak`
  存在、reload 后生产可读、`promotions.jsonl` 记录 promoted_by 与
  gate_report_id;拒绝链(过期报告/fail 报告/warn 未 ack)三型
  `GateError`;显式 version 覆盖优先于 bump。

**API 级**:`tests/web/test_lab_api.py` 的
`test_validate_endpoint_report_shape`、`test_validate_fail_blocks_and_promote_rejections`、
`test_promote_end_to_end_and_stale_report` 走 HTTP 全链路(报告形态、
fail 阻断、过期报告 409)。截至 v1.0,全仓测试基线 Python 812 例 +
前端 24 个测试文件全绿(SKILL-DEV L5 实现注)。

**真实示例**:`agent_os/examples/workspace_janitor` 四技能覆盖三档剧情
(L1 巡检 / L2 幂等写入 + approve-run / L3 指名删除 + dry_run),全真工具
零 mock;`tests/examples/test_workspace_janitor.py` 断言 dry_run 清单与
真实 `destroyed` 一致、L3 每次必问——即标准 §5 测试要求的活样本。

**涟漪效应**:E3(升权 lint 严格化)的 reversal/blast_radius 必填随 G3
落地,两份计划在闸门汇合(SKILL-DEV §4 依赖说明);编辑器推导档徽标、
`/tier` 端点与三档模板库(创建即过 G1-G3)复用同一推导与闸门;CLI
与 Web 共用 drafts_root,CLI 查完 Web 可直接 promote。

## 6. 局限性与边界(局限性)

1. **闸门只强制"写了",不强制"是真的"**。reversal/blast_radius 的判定
   是"非空字符串"(gate.py:230-237),机制是否真实存在靠 reversal 演练
   测试与 PR 人审(TIER-STANDARDS §7 checklist 要求指出代码位置)——
   这部分不可机器化,是刻意的留白,也是残余风险。
2. **逐档 test 面要求不由闸门执行**。幂等测试、containment、TOCTOU、
   注入滥用等是作者/PR 责任;G4 冒烟只跑草稿自带用例,不检查这些专项
   测试是否存在。闸门是质量面的必要条件,不是充分条件。
3. **G4 依赖执行器注入,嵌入路径退化**:`smoke_runner=None` 时 G4 按
   skip(gate.py:241-242),质量面证据缺位也能 promote;无用例仅 warn,
   人工 ack 即可通过——冒烟防线的强度取决于宿主接线。
4. **G5 是正则模式表,不是语义判定**。改写、编码、其他语言的诱导变体
   可绕过;"宁稳勿滥"意味着策略上接受漏报以杜绝误伤正面表述。它挡的
   是"明目张胆写进资产的诱导",挡不住精心伪装的供应链攻击(那是 M6
   Provenance 的非目标留白)。
5. **promote 只支持单文件 skills.yaml**,目录/多文件 skill_set 的归并
   策略未实现(gate.py:406-410,留 L5);code 技能的 handler 源码不进
   生产条目,dotted path 原样携带。
6. **未知引用按最低档计**。推导档对查不到的工具/子技能按 none 防御性
   跳过(escalation.py:64-73、100-102)——SKILL-DEV §1.4 为 G5 设计的
   "不引用不存在的 skill/tool"检查并未实现,存在性最终由生产加载期闸门
   兜底,草稿期可能低估档。
7. **版本管理是线性的**:patch bump + 单 `.bak` 备份,无分支/合并/历史
   (SKILL-DEV §5 明示"git 才是真正的版本系统");`.bak` 只有一版,连续
   两次 promote 后更早的生产态不可回滚。
8. **闸门判的是资产,不是行为**。过闸的 L3 skill 在运行时的每次调用仍
   须过升权人审;闸门不减免任何运行时检查,两套闸串联而非替代。

## 7. 引用

- 标准:`docs/TIER-STANDARDS.md`(判定树 §1、逐档规范 §3-§5、checklist §7、反模式 §8)
- 上游设计:`docs/ESCALATION.md` §2.1/§2.3/§3.4(三档语义、分档标准纲要、E3 分期)
- 平台设计:`docs/SKILL-DEV.md` §1.3-§1.4、§4(L2/L3/L5 实现注)、§5
- 实现:`agent_os/src/agent_os/skills/gate.py`(五关、哈希、promote)、
  `agent_os/src/agent_os/skills/manifest.py:113-133`(加载期硬闸门)、
  `agent_os/src/agent_os/api/v1/escalation.py:54-140`(推导档)、
  `agent_os/src/agent_os/host/cli/main.py:336-340`(`lab validate`)
- 测试:`agent_os/tests/skills/test_gate.py`、`agent_os/tests/web/test_lab_api.py`、
  `agent_os/tests/examples/test_workspace_janitor.py`
- 示例:`agent_os/examples/workspace_janitor`

> 资料矛盾注:`gate.py` 与 `test_gate.py` 的模块 docstring 均称 "G4/G5
> 本期 skip 占位",为 L2 期残文;实际 G4(smoke_runner 注入时)与 G5
> 均已实现(L3/L5 ✅,SKILL-DEV §4),以代码为准。另 SKILL-DEV §1.4
> 为 G2 设计的"inputs 示例骨架可生成"未在 gate.py 实现。
