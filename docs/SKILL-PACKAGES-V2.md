# 能力包(Capability Package)详细设计:业界对照与四份方案

> 版本:v2.1 · 日期:2026-08-03
> **实施状态:P1-P4 + P1.5 已落地**(commit 9129e72 / 361e6dc / 08438c2 /
>   6ce01ed + 本次注册表分层重构;基线 844 passed)。本文由方案稿转为
>   **设计依据 + 实施记录**:§2 的四项现状发现逐条标注关闭情况,§6 的设计
>   条目标注已实现/未实现,剩余项(P2.5 / P5)见 §7。
> 前作:`SKILL-PACKAGES.md`(v0.1 报告)——本文不推翻它的洞察,而是补齐它
>   没回答的七个问题,并给出**可选择的四份方案**与推荐路线。
> 并行稿:「Skill Pack 设计方案」(Kimi Code,方案 A/B/C)——其贡献与冲突
>   裁决已合并进本文,逐条见 **§9**。
> 依据:本文所有关于现状的论断都经代码核对或实测复现(§2 有可复跑的复现步骤);
>   业界对照来自公开资料(§3 附来源)。
> 相关:`SKILL-DEV.md`(Lab)、`ESCALATION.md`(推导档)、`TIER-STANDARDS.md`
>   (分档标准)、`DESIGN.md` §6(加载与依赖)、`NAMING.md`(命名空间)。

---

## 1. 命题回顾与本文的增量

v0.1 的洞察成立且重要:**用户的心智单位是"功能",而一个功能 = 一个根技能
加上它背后所有会被用到的技能**。系统内部早就按闭包工作(加载期拓扑排序、
推导档闭包取 max),只有 Lab 的工作面还停在"一次一个草稿"。

v0.1 由此给出的结论是"包 = 根技能的依赖闭包,推导不声明"。这个结论**大方向
正确,但作为工程方案有七个洞**,它们全部在落地时会变成事故或体验断裂:

| # | v0.1 没回答的问题 | 后果 |
|---|---|---|
| Q1 | 闭包在**编写过程中天然是不完整的**(先建根还是先建子?),此时"包"是什么? | 悬空硬闸把正常创作节奏判成红灯(§2.2 已实测到死锁) |
| Q2 | 闭包遇到**已发布的生产技能**要不要继续往下展开? | 现实现按命名空间巧合决定,同一件事两种行为(§2.3) |
| Q3 | "原子提交"到底原子在哪一步?`reload()` 失败时生产已经被写坏了怎么办? | v0.1 的 `.bak 兜底` 不足以保证原子性(§4.4) |
| Q4 | 提交时"我审的"和"实际写进去的"如何绑定?单稿有 `manifest_hash`,**包呢**? | 包级提交没有防错位机制,单稿的 P3 防护在包粒度失效 |
| Q5 | 一个功能有**两个入口**(既能巡检也能单独清理)怎么表达? | 单根假设表达不了,用户被迫拆成两个"包" |
| Q6 | 包级冒烟"以根为入口跑一遍"——**没被跑到的 L3 成员**算验证过了吗? | L3 的 dry-run/blast_radius 是纸面承诺,不跑等于没验证 |
| Q7 | 助手获得"建草稿"能力后,信任面扩张到哪里为止? | "能改不能发"的边界需要重新划,否则注入面变大 |

本文的结构:先把现状钉死(§2),再看业界怎么解同类问题(§3),提炼出可迁移
的规律,然后给**四份方案**(§4)与推荐路线(§5),接着是推荐方案的详细设计
(§6)、分期与验收(§7)、明确不抄的清单(§8),最后是与并行设计稿的
**对照裁决**(§9)。

---

## 2. 现状盘点(经代码核对与实测)

> **本节是写作当时(P1 刚落地一半)的发现记录,四项全部已关闭**——保留原文是
> 因为后续设计的每一条都是从这些发现推出来的,删掉就只剩结论没有论证。逐条
> 关闭情况见各小节末的「关闭」行。

### 2.1 P1 当时只落地了一半(写作时尚在工作树)

写作时工作树里已有 v0.1 的 P1 部分实现,这是本文的起点而非空地:

- `skills/closure.py`(新增,122 行):`compute_closure(root, drafts, production, tools)`
  → `{root, root_status, root_tier, members, errors}`,成员四态
  `draft | production | external | missing`,环沿用 loader 语义(自引用剔边);
- `host/web/app.py`:`GET /api/lab/packages/{root}/closure` 与
  `GET /api/lab/drafts/{name}/closure`(别名)两个端点;
- `skills/gate.py`:G2 新增**引用完整性**判定(悬空工具引用、悬空子技能引用、
  环)——这同时关掉了白皮书校勘记里"G5 不引用不存在的 skill/tool 未实现"的缺口;
- `host/cli/main.py`:CLI validate 传入 `store=`。

**尚未落地**:包视图(前端)、原子提交、助手包级化、目录形态 set。

> **关闭**:P1 已提交(9129e72,含前端包面板与悬空一键成稿);当时列的四项
> "尚未落地"分别由 P2(361e6dc,原子提交)、P3(08438c2,助手包级化)、
> P4(6ce01ed,目录形态 set)补齐。

### 2.2 实测到的死锁:检查绿灯、提交永远失败

`gate.py` 的 `promote_draft` 复跑 G1-G3 时调用
`validate_draft(draft, production=production, tools=tools)`——**没有传 `store=`**
(gate.py:412)。同样漏传的还有助手工具 `lab.draft.validate`(lab_tools.py:173)。
而 Web 的检查端点(app.py:861)与 CLI(main.py:386-387)都传了。

注意这是**纯转发遗漏**而非管线缺失:`promote_draft` 的签名里本来就有
`store`(app.py:1032 传的就是 `lab_store`),它只是没有把这个已经在手的参数
往下传给复跑。修复是一行。

后果不是"少查一点",而是判定方向相反:没有 `store` 时,`_skill_resolvable`
只查生产,于是**任何指向兄弟草稿的引用都会被判成悬空**。

实测复现(建两个草稿:`lab.root` 的 `permissions.skills` 指向 `lab.child`,
二者都还在草稿层;然后走"检查 → 提交"):

```
① validate(store=store)  → pass | g2: pass          ← 用户在 UI 看到绿灯
② validate(store=None)   → fail | g2: fail
      fail 悬空引用: lab.root 引用了不存在的子技能 lab.child
③ promote → GateError: 复跑 g2 出现 fail(报告已过期): 悬空引用: lab.root 引用了不存在的子技能 lab.child
```

三个问题叠在一起:

1. **功能性死锁**——只要根草稿引用了还没发布的兄弟草稿(也就是"做一个功能"
   的标准形态),它就**永远无法 promote**。而这恰恰是能力包要支持的头号场景。
2. **错误信息撒谎**——报告并没有过期(哈希一致),真实原因是两次调用的判定
   口径不同。用户会去重新点检查,然后再次看到绿灯,陷入循环。
3. **助手与 UI 不一致**——助手跑 validate 报红、人点检查报绿,同一份草稿两个结论。

值得说明的是:这个死锁**不是纯粹的 bug,而是设计缺口的暴露**。它把 v0.1 里
"断点 1.1-C 半吊子 promote"从静默的生产损坏,变成了显式的硬拦——就安全性
而言方向是对的(不再可能写出损坏的生产 registry),但它同时证明:
**引用完整性闸门一旦落地,原子提交就从"打磨项"变成了必需品**。二者是同一
个设计的两半,不能只落一半。P2 的优先级因此应当高于 v0.1 的排期。

> **关闭**(361e6dc):`promote_draft` 复跑补传 `store=` 且 `strict_refs=True`;
> 助手 `lab.draft.validate` 同步补 `store=`(三宿主判定口径统一);复跑错误
> 信息两分——哈希不符 = "报告不一致",哈希相符但判定变了 = "判定不一致,
> 非报告过期"。同一场景现在的实测输出:
>
> ```
> ① validate(store=store)  → pass | g2: pass
> ② validate(store=None)   → warn | g2: warn   ← 两阶段严格性,草稿期不再误判 fail
> ③ promote → GateError: 根草稿引用了未发布的兄弟草稿 lab.child
>              ——这是一个包,请用包级提交(POST /api/lab/packages/lab.root/plan → promote)
> ```
>
> 注意 ③ 仍然拒绝,但**拒绝得诚实**:它不再谎称报告过期,而是指出这是包并
> 给出包级提交的路径。这正是 §6.2 尾段要的结果。

### 2.3 闭包边界靠命名空间巧合

`closure.py` 用根技能的**第一段命名空间**判定生产节点是"包内"还是"外链":
同段 → `production`(继续向下展开),异段 → `external`(停止展开)
(closure.py:39, 49-50, 91)。

这条规则的因果关系是错的。以仓库现有示例为准:

- `ops.scan.workspace`(examples/workspace_janitor)引用
  `ops.plan.write` / `ops.cleanup.execute` / `ops.service.stop`——若其中某个
  已发布,闭包会继续展开它的整棵子树,把一堆**已发布、已过闸、用户根本没在编辑**
  的技能拉进"包"里;
- 同一个根引用 `common.text.summarize` 则立即停止。

同样是"引用一个已发布技能",行为取决于命名巧合。这正是 Nix 记录在案的
**closure bloat**(闭包膨胀)失败模式的翻版:自动推导的闭包很容易把不该进来的
东西吸进来。正确的判据在 §6.1。

> **关闭**(361e6dc):命名空间启发式已被 §6.1 的两种闭包取代——`edit` 模式
> **只有 draft 节点下传**(生产/外链都是纯显示叶子),`runtime` 模式全展开;
> 闭包 >8 成员或深度 >4 出告警。closure 端点加 `?mode=runtime`(6ce01ed)。

### 2.4 生产写入面的三个硬限制

`write_production_entry`(gate.py:452-478)当前:

- 只支持**单文件** `skills.yaml`,目录/多文件形态直接抛 `SkillLoadError`;
- 每次写入前 `shutil.copy2` 到**同一个** `.bak`——N 个成员逐个写就是 N 次覆盖,
  最早的版本立刻丢失,`.bak` 在包场景下形同虚设;
- 写完直接 `production.reload()`,**没有"装不起来就别换"的证明步骤**。

> **关闭**(361e6dc + 6ce01ed):三条逐一——① 目录形态由 P4 的 set 落盘支持
> (编辑闭包 ≥2 成员落为 `<ns>/` 目录形态,单成员仍写单文件);② 一次事务
> **整文件单 `.bak`**,不再每成员一次覆盖;③ 先证后换落地——候选全集先写
> staging 并**过 loader 全流水线加载验证**,通过才原子 rename + 单次 reload,
> staging 失败时生产零变化(有断言测试)。

---

## 3. 业界对照:同一个问题,四种成员关系模型

我调研了七个有可比性的系统。把它们按**"包的成员关系由谁定"**排列,规律非常
清楚——这也是本文四份方案的分类轴。

### 3.1 成员**推导**(computed):Nix / Bazel / changesets

Nix 的 closure 就是本文的闭包:"your thing and everything it depends on,
recursively"。关键差异在于 **Nix 不要求显式声明运行期依赖,而是扫描输出里的
store path 哈希自动检测**——与大多数包管理器相反。收益是依赖永不缺失,代价被
社区明确记录为:*"it's easy to accidentally create runtime dependencies"*,
即闭包膨胀,以至于出现了专门的 `nix why-depends` 与 closure-size 可视化工具。

**可迁移的两条**:(a) 推导确实永不腐烂,v0.1 的判断成立;(b) **推导系统必须
配套"为什么它在包里"的追溯能力和体积告警**,否则用户会被自己看不懂的成员列表
淹没。现实现的 `ref_by` 字段已经是 `why-depends` 的雏形,应当在 UI 里兑现。

changesets(monorepo 发布)提供了另一半:当一个包发布时,**自动 bump 依赖它的
包**,并且"两个 minor 变更不会变成两次 minor bump"——即**变更聚合**。这直接
对应 §6.5 的版本策略。

### 3.2 成员**声明**(declared):npm / Helm / Claude Code Plugin / Salesforce

**所有以分发为目的的系统都是声明式的**,无一例外。

- **Helm umbrella chart**:"a chart of charts",子图在 `Chart.yaml` 声明,
  `Chart.lock` 锁定以保证可复现;设计目的原话是"a collection of software
  elements that each have their own individual charts but must be installed or
  upgraded as **a single atomic unit**"——与能力包的动机逐字重合。`--atomic`
  标志保证失败即回滚到上一版本。已知坑:子图之间**并非完全隔离**,同一依赖
  被多个子图引用会出问题。
- **Claude Code Plugin**:与 Agent OS 最同构的对照。`.claude-plugin/plugin.json`
  把 skills / agents / commands / hooks / MCP servers 打成**一个带版本的可安装单元**。
  值得注意的细节:当源仓库只有 SKILL.md 而没有 plugin.json 时,marketplace 条目
  可以用 `strict: false` + 显式 `skills` 数组**从外部声明成员**,路径可以跨多个
  子目录挑出"a curated subset"。也就是说,Anthropic 同时支持**清单声明**与
  **外部策展**两种模式。
- **Salesforce unlocked package**:强调"business capability needs versioned
  releases, dependency management, upgrades"。它给出的最有价值的一条是反模式
  警告:*"It is inappropriate to treat each package as if it is a totally
  independent territory"*——包不是独立王国,必须考虑与同一 org 内其他包的交互。
  对应到 Agent OS:包不能假装自己独占命名空间。

**可迁移的一条**:**开发期的成员关系可以推导,分发期的成员关系必须声明。**
v0.1 把两者合并成一个概念,然后在 §4.4 又悄悄引入了一个声明式产物(以根命名的
目录形态 set)——这个自相矛盾正是 Q5 的来源。

### 3.3 成员是**策展清单**、且刻意**无功能依赖**:VS Code Extension Pack

VS Code 的 Extension Pack 就是一个 `package.json` 里带 `extensionPack` 数组的
空扩展。它的设计原则被明确写下来:

> An Extension Pack should **not have any functional dependencies** with its
> bundled extensions, and the bundled extensions should be manageable
> **independent of the pack**.

这是与能力包**方向相反**的设计:pack 是"我推荐的一组东西",不是"我需要的
一组东西"。它解决的是分享与批量启停,不是依赖完整性。

**结论:这条不抄。** 但它给了一个有用的反问——Lab 里是否也存在"这几个技能
我想一起管,但它们互不引用"的需求?有(见方案 C)。如果有,那它是一个**独立
于闭包的概念**,不该混进包。

### 3.4 成员是**运行期路由单元**:Salesforce Agentforce Topic

Agentforce 的 Topic 是"a collection of paired actions and instructions",
agent 收到 utterance 后先选 Topic,再在 Topic 的 scope/instructions 内选 action。
**最有信息量的一条是:2026 年 4 月起,Topic 被改名为 Subagent,Topic Selector
改名为 Agent Router。**

也就是说,业界最大的 agent 平台在实践中发现:"一组指令 + 一组它能用的动作"
这个东西,本质就是一个子 agent。而在 Agent OS 里,**"一个 prompt + 一份
`permissions` 白名单"就是一个 skill**——这个实体早就存在了。

**结论:强证据支持 v0.1 §7 的决定"不做运行时包实体"。** Agent OS 不需要
Topic,因为 skill 已经是 Topic。包必须严格限定在**开发期组织与准入面**。

### 3.5 先审后落:Terraform plan / apply

Terraform 的 saved plan 给出的保证是:*"Applying a saved plan file guarantees
that what you reviewed is exactly what gets executed."*

这句话与 Agent OS 升权系统的不变量"**审的就是要执行的**"(白皮书 §5.2)、
以及 Lab promote 的报告哈希绑定(P3 的解法)是**同一条设计原则的三次独立发现**。
Agent OS 已经在单稿粒度实现了它(`manifest_hash` 覆盖 manifest+prompt+handler,
草稿改一字节旧报告即作废)。

**可迁移的一条,也是本文最重要的一条**:把这条原则**泛化到包粒度**——引入
`package_hash` 与提交计划(plan),promote 消费 plan 而不是消费一个 root 名字。
这直接关掉 Q4,而且不引入任何新哲学,只是把系统已有的不变量提升一个维度。

### 3.6 多包一起编辑:VS Code 多根工作区 / JetBrains Project Attach

*"Workspaces solve 'these packages are developed together.' Monorepos solve
'all our code lives in one place.' They overlap but aren't the same thing."*

这句区分正中 Q1:**"我正在一起编辑的东西"与"互相依赖的东西"是两个集合**,
它们高度重叠但不相等。编写过程中前者领先于后者(你先建了三个草稿,还没连线)。

### 3.7 一次生成一整套:Backstage Scaffolder

Backstage 的 software template 用多步骤 YAML 一次生成整套骨架文件。社区记录
在案的头号痛点是**模板组合性**(#15241):用户想让模板 include/extend 其他模板
以免重复步骤,但这很难做对。

**可迁移的一条**:功能包模板值得做,但**不要设计模板继承体系**——业界在这上面
摔过跤。保持模板扁平、少而具体。

同一家族里,**扣子(Coze)/ Dify 的"模板即闭包、一键复制"**给出了冷启动体验的
标杆:一个模板 = 智能体 + 工作流 + 插件引用的完整组合,复制到自己的空间即可用;
插件是独立审核的生态单位,模板**引用**插件而不内嵌。可迁移的一条是**官方模板
是生态早期唯一的质量杠杆**——种子期由官方维护少量高质量包,比开放投稿更有效。
对应到本文:P4 的两件功能包模板(`pkg.inspect_clean` / `pkg.research_report`)
就是这个位置。

### 3.8 检测缺失 → 一键补齐:ComfyUI Manager(最贴近的交互参照)

用户导入一个 workflow(≈ 用户的目标产物),Manager 扫描发现缺失节点 →
**「Install Missing Custom Nodes」一键安装整个闭包**。用户从不手工逐个装依赖。

三条记录在案的坑,对本设计都是预警:

1. **命名不一致导致自动解析失败**——节点在 workflow 里存 type name,registry 里
   登记 display name,对不上就解析不到。**依赖标识必须全局唯一且稳定。**
   Agent OS 在这一条上**已经安全**(NAMING.md 的点分层次名 + 草稿名即目录名 +
   `manifest` 里 `name` 由目录名钉死),这是确认而非待办——也正是 §6.10 拒绝
   "草稿加前缀"的直接理由:前缀会**主动制造**这个 bug 类别。
2. **依赖地狱**(多个包要求同一库的不同版本)→ 需要版本范围与冲突检测。
   本设计明确不做(§8 第 2 条),因为基线是单版本。
3. **装完要重启才生效** → 依赖变更需要有"生效时机"概念。Agent OS 对应的是
   `reload()` 只影响后续新建的 run(在跑的 run 钉住旧版)。

**可迁移的核心**:「检测缺失 → 一键补齐」是闭包体验的核心交互。这与 v0.1 从
命名空间树推出的"悬空节点一键成稿"是**同一个交互的两次独立发现**,已在 P1
落地、P2 把它接进了 plan 的 `blockers[].fix`。

### 3.9 组合交给运行时:Anthropic Agent Skills

Skill = 一个文件夹(SKILL.md + scripts/references/assets),渐进式披露(启动只
加载 name+description);**skill 之间没有显式依赖清单**,可组合性由 agent 在
运行时按任务自行触发;`allowed-tools` 前置声明工具权限。

**评价**:这把正确性责任推给了运行时——对 L1 检索类能力没问题,**对 L3 不可逆
操作不可接受**(Agent OS 的整个升权模型正是建立在"闭包在装配期已知"之上:
推导档取 max 需要静态闭包)。值得拿走的两条:`allowed-tools` 式的前置权限声明
(Agent OS 的 `permissions` 已是同构),以及**validate 工具独立于编辑器存在**
(对应 `agent-os lab validate` 的 headless 入口)。

### 3.10 传递依赖是攻击面:GlassWorm(反面教材)

已核实的真实攻击:2025-10 由 Koi Security 发现,不到两周内污染 **72 个 Open VSX
扩展 + 88 个 npm 包 + 151 个 GitHub 仓库**,约 35,800 次安装受影响;
2026-05 由 CrowdStrike 联合 Google、Shadowserver 捣毁基础设施。

关键机制**恰好落在包设计的要害上**:攻击者先发布正常扩展积累信任,
**在后续更新里才往 `extensionPack` / `extensionDependencies` 里塞入恶意依赖**,
让一个看起来独立的扩展变成传递投递载体——初始审查完全失效。

**可迁移的一条(而且不需要 registry,今天就成立)**:**传递依赖的"静默变化"
必须被当作高危事件**。这直接推出 §6.9 的依赖变更档位告警——在 Agent OS 里,
上游依赖换个版本就可能**静默抬高下游技能的推导档**,而下游作者不会收到任何
通知。详见 §6.9。

---

## 4. 四份方案

四个方案沿"成员关系由谁定"这条轴展开。它们**不是互斥的**——§5 给出组合建议。

### 方案 A:纯闭包(把 v0.1 走完)

**模型**:包 ≡ `closure(root)`,零新实体、零新文件。Lab 的所有动作从"对单草稿"
升级为"对闭包"。

**要做**:包视图、包级五关、原子提交、助手包级化、功能包模板。

**优点**
- 永不腐烂,与推导档同哲学("能算出来的就不让人维护");
- 概念预算为零——用户不需要学"包"是什么,它就是"这个技能和它用到的东西";
- 改动最小,P1 已在途。

**缺点**
- **Q1 无解**:授时闭包不完整,包的边界在编辑过程中闪烁;
- **Q5 无解**:单根假设,双入口功能表达不了;
- **无包身份**:挂不了人话标题、描述、版本、README——而分发迟早需要;
- 边界判据脆弱(§2.3),需要额外规则约束闭包体积。

**适用**:功能形态是一棵清晰的单入口调用树、命名规范严格、暂不考虑对外分发。

### 方案 B:闭包 + 包身份(推导成员,声明身份)

**模型**:成员**仍然推导**(不引入成员清单),但引入一个**极薄的包身份**:
只有元数据,没有成员列表。落点是**根技能 manifest 的一个 additive 段**,
而不是新文件类型:

```yaml
- name: ops.scan.workspace
  version: 1.2.0
  package:                      # additive,缺省即"这不是包的入口"
    title: 工作区巡检与清理      # 人话,给非作者看
    summary: 扫描工作区、生成清理计划、执行清理与停服务
    entries: [ops.scan.workspace, ops.cleanup.execute]   # 多入口(Q5)
```

包 = `⋃ closure(e) for e in entries`。缺省 `entries = [自己]`,退化为方案 A。

**依据**:Helm `Chart.yaml`、Claude `plugin.json`、Salesforce package directory——
**所有分发系统都需要一个挂身份的地方**。放进根 manifest 而非新文件,是为了
不新增一类会腐烂的产物:成员仍然推导,声明的只有"这个功能叫什么、从哪进"。

**优点**
- 关掉 Q5;给包一个稳定 id(Lab 状态、promotions 记录、UI 标题都需要它);
- 为分发(目录形态 set、导出、未来的 marketplace)留好了唯一必需的锚点;
- 腐烂面极小——`entries` 写错会被闸门当场发现(入口必须可解析)。

**缺点**
- 多了一个可以写错的字段,与"零声明"的纯粹性有张力;
- 多入口使提交事务变大,闭包并集可能出现意料外的成员。

### 方案 C:工作区(编辑面与依赖面解耦)

**模型**:不把"包"当作编辑单元,而引入 **Lab 工作区**——一次打开 N 个草稿
一起编辑(多标签/分栏),成员由**用户选择**决定,闭包退居为三种辅助角色:
① 建议加入("你引用了 X,要一起打开吗");② lint(悬空);③ 提交事务的**最小
完整性约束**(要提交根,闭包内的草稿成员必须一起提交)。

**依据**:VS Code 多根工作区 / JetBrains Project Attach;"workspaces solve
'these packages are developed together'"。

**优点**
- **正面解决 Q1**:工作区不要求成员互相引用,你可以先建三个草稿再连线,
  编辑面不闪烁;
- 覆盖 §3.3 那类"想一起管但互不引用"的真实需求;
- 也解决 Q5(工作区可以有多个入口,它本来就不是树)。

**缺点**
- 引入一个**需要用户维护的会话状态实体**,与"能算出来就别让人维护"直接冲突;
- 工作区与包的关系需要解释,概念预算最高;
- 提交语义变复杂:提交工作区 ≠ 提交闭包,二者不一致时听谁的?

**缓解**:工作区**由闭包自动播种**,用户只能微调(加入尚未连线的草稿、
移出不想动的成员)。这样默认零维护,只在闭包不够用时才动手。

### 方案 D:功能优先生成(入口层,必须叠在 A/B/C 之上)

**模型**:Lab 的入口从"新建技能"改为"描述你要的功能"。助手 + 功能包模板
一次生成整个闭包骨架 → 人在 **plan 面板**审阅(哪些草稿会被创建、各自什么档)
→ 确认后落盘为一组草稿。

**依据**:Backstage scaffolder、Rails generator;审阅面沿用 Terraform plan 模式。

**复用优先(关键子设计)**:分解需求时**先搜注册表,能复用不新建**
(`system.skill.search` 已在助手可及的工具面上)。这是包思路真正的复利——
**用户的新需求很可能大部分已经被现有 skill 拼出来了**,包越多,新需求需要
新建的成员越少。它决定了助手的第一步动作:先搜,再决定建什么,而不是上来
就生成一整套。分解提案里每个节点都应标注"新建 / 复用已有 `name@version`",
每行可被用户拒绝。

**优点**
- 最贴用户的原始表述——"根据一个功能需求,用户需要的其实是一个 skill 库";
- 把 Lab 的心智从"技能工坊"真正变成"功能工坊";
- 让 §3.7 的模板痛点绕过去:模板保持扁平,组合性交给助手而不是模板引擎;
- 复用优先让生态滚雪球,而不是每个功能都从零长一棵新树。

**缺点**
- 一次生成 6 个技能,人很可能一个都不细看——**必须配 plan 面板**,否则是负收益;
- 助手需要 `lab.draft.create`,信任面扩张(Q7);
- 它**不是成员模型**,只是入口,必须叠在 A/B/C 之一上。

### 4.5 对比

| | A 纯闭包 | B 闭包+身份 | C 工作区 | D 功能优先 |
|---|---|---|---|---|
| 成员由谁定 | 推导 | 推导 | 用户选(闭包播种) | 生成时确定 |
| 概念预算 | 零 | 很低 | 高 | 低(入口) |
| 关 Q1 授时不完整 | ✗ | ✗ | ✓ | 部分(一次生成齐) |
| 关 Q5 多入口 | ✗ | ✓ | ✓ | — |
| 支持分发 | ✗ | ✓ | ✗ | — |
| 腐烂风险 | 无 | 极低 | 无(会话态) | — |
| 落地成本 | 低(在途) | 低+ | 中高 | 中(依赖 plan 面板) |
| 业界背书 | Nix / Bazel | Helm / Claude Plugin | VS Code 工作区 | Backstage |

---

## 5. 推荐路线

**推荐:以 A 为骨架,取 C 的两个最小切片解决 Q1,把 B 推迟到分发时兑现,
D 作为 P3 的入口层。**

理由:

1. **不引入工作区实体,但要吃掉工作区解决的问题。** C 的完整形态(用户维护的
   会话实体)代价过高,与项目哲学冲突;但它指出的 Q1 是真问题。用两个小切片
   即可覆盖绝大部分:
   - **两阶段严格性**(§6.2):悬空引用在**草稿期是 warn + 一键修复**,
     只在**提交期是 fail**。授时顺序不再被判红灯。
   - **提交计划可勾选**(§6.3):plan 里列出的成员集用户可微调,这就是工作区
     的核心能力,但它只在提交那一刻存在,不需要用户长期维护。
2. **B 的成本几乎为零,但收益要到分发期才兑现。** `package:` 段是 additive
   的,可以在 P4 与目录形态 set 一起落,不阻塞前面。现在就把字段名定下来,
   避免以后返工。
3. **P2 必须提前。** §2.2 实测证明,引用完整性闸门已经落地,而原子提交没落地,
   系统当前处于"包场景无法提交"的状态。P2 从 v0.1 的第二期升为**紧接 P1 的必需项**。

---

## 6. 推荐方案详细设计

### 6.1 两种闭包:编辑闭包 vs 运行闭包

Nix 区分 build-time closure 与 runtime closure。能力包需要同样的区分,而且这
恰好替换掉 §2.3 那条命名空间启发式。

**编辑闭包(edit closure)**——从根出发,**只经由草稿节点向下展开;任何生产
节点都是叶子**。判据是"它还在不在你的编辑面",而不是命名巧合。

- 用于:包视图的编辑区、**提交事务的成员集**、包级闸门的逐成员判定;
- 依据:生产节点按定义已经过闸、已发布、不是本次提交的对象。展开它没有任何
  动作可做,只会制造 §2.3 的闭包膨胀。

**运行闭包(runtime closure)**——全展开,穿过生产节点。

- 用于:包级冒烟的**覆盖率统计**(§6.6)、闭包体积告警、环检测;
- 生产面的环与依赖存在性,loader 在加载期本来就查(local_file.py:105-124),
  这里不重复承担。

**具体改动**(`closure.py`):`resolve()` 的四态保留供显示,但下传规则从
`if status in ("external","missing"): return`(closure.py:91)改为
**只有 `draft` 才下传**。`production` 与 `external` 的区别退化为纯显示标签
(同命名空间的已发布 = "包内已发布",跨空间 = "外部依赖"),不再影响遍历。
`compute_closure` 增加 `mode: "edit" | "runtime"` 参数,缺省 `edit`。

**追溯与体积**(Nix 的教训):`ref_by` 已在成员表里,UI 必须把它兑现为
"为什么它在包里"的悬浮说明;编辑闭包 >8 个成员或深度 >4 时 warn(职责没拆好的
信号),运行闭包设硬遍历上限防病态图。

### 6.2 两阶段严格性:草稿期 warn,提交期 fail

这是 Q1 的解法,也是 §2.2 死锁的正解之一。

```
validate_draft(..., strict_refs: bool = False)

strict_refs=False(草稿期,UI "检查" / 助手 validate / CLI 缺省)
    悬空引用 → warn,findings 里带可执行的修复提示("创建草稿 X")
strict_refs=True(promote 复跑 / CLI --strict / 包级提交前)
    悬空引用 → fail
```

依据:Terraform 的 validate / plan / apply 三段严格性递增;IDE 里未解析的
import 是 error 但带 quick fix。创作期的不完整是**过程状态**,不是缺陷。

**修正:引用解析是三态,不是两态。** 上面的两档表述有一个陷阱——转发 `store=`
之后,语义会反过来出错:根草稿引用兄弟草稿 `lab.child` 时,带 store 的 G2 会
判 pass → promote 放行 → 生产写入了引用"尚未发布的 `lab.child`"的根技能 →
**生产 registry 进入损坏态**(正是 v0.1 断点 1.1-C)。正确的判定要区分三态:

| 引用解析结果 | 草稿期 `strict_refs=False` | 提交期 `strict_refs=True` |
|---|---|---|
| 生产已发布 | pass | pass |
| **仅草稿存在** | pass(包内成员,创作常态) | **拒绝**:"这是包成员,请用包级提交" |
| 查无此名 | warn + 修复提示 | fail:悬空引用 |

**环(cycle)不参与两阶段**:它永远是 fail——它不是创作顺序的产物,任何时候
都是错的。

**同时必须修的三处**(否则两阶段严格性也救不了 §2.2):

1. promote 复跑加 `store=store`——**这是死锁的直接原因**;
2. 助手 `lab.draft.validate` 加 `store=`,否则助手与 UI 结论不一致;
3. 复跑失败时的错误信息不能再说"报告已过期"——它撒谎。应当区分
   "报告不一致"(哈希不符)与"判定不一致"(哈希相符但判定变了)。

修完之后,单稿 promote 的语义变成:**根草稿引用了未发布的兄弟草稿 → 明确
拒绝,并指路"这是一个包,请用包级提交"**。这比假"报告过期"诚实得多。

> **状态:已实现**(361e6dc)。实测输出见 §2.2 的关闭行。

### 6.3 提交计划(plan):把"审的就是要执行的"提升到包粒度

这是 Q4 的解法,也是本设计与 v0.1 差别最大的一处。v0.1 的 promote 接口是
`{root, report_id}`——服务端拿着 root 现算成员集,**用户审的东西和服务端算的
东西之间没有绑定**。

引入包哈希与计划,泛化单稿的 `manifest_hash`(gate.py 的 `manifest_hash`
覆盖 manifest+prompt+handler):

```python
package_hash = sha256("\n".join(f"{name}:{manifest_hash(member)}"
                                for name, member in sorted(members)))
```

```
POST /api/lab/packages/{root}/plan
→ {
    plan_id, package_hash, root,
    members: [
      { name, action: "create"|"replace"|"unchanged",
        from_version, to_version, manifest_hash,
        gate_status: "pass"|"warn"|"fail", gate_report_id }
    ],
    blockers: [ {kind: "dangling"|"cycle"|"gate_fail", member, message, fix?} ],
    warnings: [...]
  }

POST /api/lab/packages/promote { plan_id, warnings_ack }
→ 服务端按 plan 里的成员重算 package_hash;不一致 → 409
  (语义与单稿的"草稿改过一字节旧报告即作废"完全一致)
```

`action` 三态直接对应 Terraform plan 的输出形态,而且**未变更的成员标
`unchanged` 且不重写**——这就是 §6.5 版本策略的落点。`blockers` 里的 `fix`
字段承载"一键成稿"这类可执行修复。

用户看到的东西(plan 面板)与服务端将要写入的东西,由 `package_hash` 绑定。
这与升权卡片"展示的参数与注入子帧的参数是同一份 JSON"是同一条不变量。

**plan 面板还应展示两项聚合信息(合并自 Kimi 稿的公理 1/2,§9)**——它们是包
真正的"我要发布什么"的语义,而逐成员列表给不出:

- **聚合 tier 与它的来源**:包档 = 闭包取 max,标题行给徽标,并注明**是谁把包
  抬到了这一档**(点名那个成员)。一个 L1 的根因为一个 L3 子技能而整包变 L3,
  用户必须在提交前看见这件事。
- **闭包 tool 权限并集**:整个包会碰到的工具权限全集,整体展示、整体确认。

两项的数据源都已存在(`derive_skill_tier` 已按白名单递归取 max,成员 manifest
里就有 `permissions.tools`),是纯呈现,不新增机制。**状态:未实现**(P2 的
plan 面板给了逐成员 action / gate 色点 / blockers,聚合两项待补)。

### 6.4 真正的原子性:先证后换,而不是先写后祈祷

Q3 的解法。v0.1 说"全部成员一次落盘后统一 reload",但**如果 reload 失败,
生产已经被写坏了**——而这正是最需要保护的场景(包提交一次写入 N 个成员,
出错概率高于单稿)。

正确的事务顺序:

```
1. 在内存里构造候选 skills.yaml 全文(所有成员一次性合并)
2. 写临时文件 skills.yaml.staging(同目录,保证 rename 原子)
3. 用一个**临时 registry 加载 staging**
     —— 走 loader 全流水线:manifest 解析、拓扑排序、循环检测、依赖存在性
     —— 这是唯一能证明"生产装得起来"的方法,不是推断
4. 加载成功:
     a. copy2(现网 → .bak)          ← 整个文件一份,不是每成员一份
     b. os.replace(staging, 现网)     ← 同目录 rename,原子
     c. 现网 registry.reload()
5. 任一步失败:删除 staging,**现网一字节未动**
6. 4c 仍然失败(理论上不该发生):从 .bak 回滚 + reload,
   并向 promotions.jsonl 追加一条 failure 记录
```

依据:Helm `--atomic`("failure of one component to install rolls both back")。
第 3 步是关键——它把 v0.1 的".bak 兜底"(事后补救)换成了"先证后换"(事前证明)。

**同时解决 §2.4 的 `.bak` 失效问题**:一次事务只备份一次整文件。

**「入口最后」不变量(合并自 Kimi 稿的「叶子先行」,§9)。** 拓扑序上根技能
最后写入、叶子先写,这样**任何中断时刻生产都不含悬空引用**。这个洞察是对的,
而且比 v0.1 的"写入顺序无关"更严谨。但实现上取一次性写入而非 N 次顺序 promote:

| | N 次顺序 promote(叶子先行) | 一次性 staging + 加载验证 |
|---|---|---|
| 中断时生产是否损坏 | 否(叶子无悬空引用) | 否 |
| 中断时是否留下中间态 | **是**——已发布的孤儿叶子 | 否,零中间态 |
| reload 次数 | N 次 | 1 次 |
| 装不起来能否事前发现 | 否,逐个试错 | 是,staging 全量加载 |

所以「入口最后」作为**不变量**保留(候选全文合并时按拓扑序排列条目),
执行取一次性写入——两者不冲突,后者严格更强。

### 6.5 版本与回滚

**只 bump 变更过的成员。** 判据:成员的 `manifest_hash` 与
`promotions.jsonl` 里上一次记录的哈希不同。未变成员标 `unchanged`,不重写、
不 bump。依据:changesets 的变更聚合——"two minor changes don't become two
minor bumps";避免包提交把整个命名空间的版本号一起抬高,制造无意义的版本噪音。

**promotions 记录升级为包级**,并且**存下被覆盖的旧条目**:

```json
{ "kind": "package", "root": "ops.scan.workspace", "plan_id": "...",
  "package_hash": "...", "promoted_by": "local", "at": 1754...,
  "members": [ {"name":"ops.plan.write","from":"1.0.0","to":"1.0.1",
                "action":"replace","manifest_hash":"..."} ],
  "previous_entries": [ { ...被覆盖成员的完整旧 manifest 条目... } ] }
```

`previous_entries` 让**一键回滚**成为可能:把旧条目重新写回,走**同一条 §6.4
事务**。这是 `.bak` 只有一层这个已知缺口(白皮书 §6 局限 6)的正解——`.bak`
是文件级的最后一次快照,`previous_entries` 是语义级的、可选择的历史。

### 6.6 包级五关与冒烟覆盖率

五关不变,判定面扩到编辑闭包(与 v0.1 §3.4 一致),此处只补 v0.1 没说清的两处:

**G2 引用完整性**:按 §6.2 两阶段严格性执行;环沿用 loader 语义。

**G4 包级冒烟与覆盖率**(Q6 的解法):以根为入口跑用例后,从 trace 的
`post:frame.push` 信号收集**实际被压栈的成员集**,与编辑闭包成员集做差:

- 未被任何用例覆盖的 **L1/L2 成员 → warn**;
- 未被任何用例覆盖的 **L3 成员 → fail**。

理由:L3 的 `dry_run` 与 `blast_radius` 是 `TIER-STANDARDS.md` 要求的
**可演练**承诺。一个从未在冒烟里被压栈过的 L3 成员,它的这些承诺一次都没有
被执行过,"整包跑通了"是假绿灯。这条不引入任何新机制——用的是已有信号
和已有的分档标准,只是把两者接起来。

报告按成员分组呈现,fail 定位到具体成员。

### 6.7 助手包级化与信任边界(Q7)

新增两件工具:

| 工具 | permission | side_effect | 约束 |
|---|---|---|---|
| `lab.pkg.closure` | READ | none | 读包树(成员/状态/档位/ref_by) |
| `lab.draft.create` | WRITE | reversible | 见下三条硬约束 |

`lab.draft.create` 的信任面扩张必须同时收三条口子:

1. **命名空间围栏**:新草稿名必须落在当前包的入口前缀内(根名的第一段命名
   空间),助手不能在别处建草稿。与 `_WRITABLE_FIELDS` 不含 `name`
   (lab_tools.py:33-49)同源——助手改不出路径穿越,也建不出包外的东西;
2. **创建配额**:每 run ≤5 个,防"注入让它建一百个草稿"把 drafts 目录淹掉;
3. **仍然没有 promote / delete**:"能改不能发"扩展为**"能改能建,不能发不能删"**。
   删除仍然只有人能做,因为它是唯一不可逆的动作。

助手 prompt 升级为包级(先读包树 → 判断该改哪个成员还是该拆新子技能 →
新增引用必须指向包内成员或已发布技能 → 改完跑包级检查)。

### 6.8 前端

- **包面板**(左栏顶部):复用 `ns-tree.js` 折叠树;节点 = 末段名 + tier 徽标 +
  状态徽标(草稿/已发布/外部/悬空);`ref_by` 做悬浮说明(Nix `why-depends` 的
  兑现);悬空节点行内"创建该草稿"按钮(消费 plan 的 `blockers[].fix`);
- **编辑器标题变面包屑**:`包标题 / 当前成员`,切换成员不离开包上下文;
- **提交计划面板**:逐成员 create/replace/unchanged + 版本变化 + 每关状态,
  成员可勾选微调(§5 理由 1 的工作区最小切片);blockers 非空时提交按钮置灰
  并给出理由(沿用现有置灰理由的 UX 约定);
- **包体积提示**:编辑闭包 >8 成员或深度 >4 时的轻量提示,不硬拦。

### 6.9 依赖变更的档位告警(**未实现**,P2.5)

这一条来自 §3.10 GlassWorm,但**不需要任何 registry,今天就成立**——它是当前
升权模型里一个真实的静默缺口。

**缺口。** Agent OS 的推导档 = 闭包取 max。设 `X`(推导档 L1)依赖已发布的 `Y`。
某天有人 promote 了新版 `Y`,而新版开始用 `system.file.delete`——于是 `X` 的
推导档**静默变成 L3**。运行期的强制是对的(下次调 `X` 会拦人审),但:

- `X` 的作者从头到尾没有被告知他的技能换了档;
- 闸门只在有人主动点检查时才跑,不会因为上游变化而重跑;
- 从 `X` 的角度看,它自己一个字节都没改。

这与 GlassWorm 的机制同构:**信任是在依赖静止时建立的,而依赖会动**。差别只是
Agent OS 里的"上游"目前是同一个人,所以是**误伤风险**而非攻击面;一旦将来有
共享/分发,它就变成攻击面。

**设计。** promote 任一技能时,服务端反查**谁依赖它**(反向闭包 = 遍历生产全量
manifest 的 `permissions.skills`),计算这些下游技能在本次提交**前后**的:

- 推导档(`derive_skill_tier`)差异;
- tool 权限并集差异。

有抬高时在确认行里列出「本次提交会抬高以下技能的档位:`X` L1 → L3(因为
`Y` 新增了 `system.file.delete`)」,并要求显式确认。无变化时完全静默,不打扰。

数据源全部已存在(`derive_skill_tier` + 生产 registry 的全量 manifest),
**无新机制**,是一次反向遍历加一次差分。

### 6.10 注册表分层与草稿身份(**已实现**,P1.5)

**动机:同一语义曾有三处实现。** `OverlaySkillRegistry`(`draft_store.py`,完整
协议面,解析序草稿 → extra → 生产)、`closure._Overlay`(`closure.py`,只有
`get`,**且已与前者产生差异**——不处理 `extra` 槽)、`gate._SingleDraftStore`
(把单个在编草稿伪装成 store)。P2 之后消费方还在增加。

**方向:`DraftSkillRegistry` + `CompoundSkillRegistry`。** 前者把 `DraftStore`
包成一个满足 `SkillRegistry` 协议的层;后者按**列表顺序**组合任意多层
(`layers[0]` 优先),`extra` 槽退化为链上一个普通的静态 registry 而不再是特例
参数。`visible_to` / `make_frame` / `manifests` 只实现一次、全部用 `self.get`
表达——现有 overlay 已经是这个写法,照搬即可。

**三条最容易在重构中丢掉的语义:**

1. **不是纯 shadowing,是 try-and-continue。** 当前"草稿不合规时透明回落生产
   同名"靠的是捕获 `SkillLoadError` 继续往下找。丢了这条,一个存了一半的草稿
   会让整个 Lab 装配不起来——"半成品影子不遮蔽可用版本"这条性质就没了。
2. **写入面不在协议里。** `reload()` / `path` 是 `write_production_entry` 用
   duck-typing 摸的,`manifests()` 也是协议外的事实扩展。compound 必须显式指定
   **哪一层可写**,否则会出现"promote 写进了草稿层"这类事故。
3. **`make_frame` 按解析结果委托**,不是按方法委托——谁解析出这个 skill 就用
   谁的帧构建。现在因为都共用 `local_file.build_child_frame` 看不出来。

顺带修一个可读性陷阱:原构造签名是 `(production, store)`,而解析是**草稿
优先**——参数顺序与解析顺序相反。用 `layers` 列表后,**顺序即优先级**。

> **落地**(`skills/compound.py` 新增):`CompoundSkillRegistry(layers, *,
> writable, names)` + `StaticSkillRegistry`(`extra` 槽退化成的普通层)+
> `DraftSkillRegistry`(`draft_store.py`,把 DraftStore 包成一层)。
> `OverlaySkillRegistry` 保留原构造签名,内部变成一个三层实例
> (draft / extra / production,`writable=2`),既有调用点零改动;
> `closure._Overlay` 已删除,推导档的 skills 视图改用两层组合。
> `resolve(ref) -> (Skill, layer_index)` 与 `layer_name(i)` 提供层来源。
> `gate._SingleDraftStore` 无需改动即可作为 `DraftSkillRegistry` 的底层
> ——后者只要求 `load_skill`,这正是"三处收敛为一处"的兑现。
>
> 三条语义有回归锁(`tests/skills/test_compound.py`,7 例):顺序即优先级 +
> 层来源、try-and-continue(坏层/缺层两种 MISS 都继续往后)、写入面只走
> 显式可写层(未指定 `writable` 时 `path`/`reload()` 报错)。
> 重构前后全仓基线一致(837 passed),新增 7 例后 **844 passed**。

#### 为什么草稿**不**加 `dev.temp.draft.*` 前缀

曾提出的另一个方案是给所有草稿强制命名空间前缀。**不采纳**,三条理由:

1. **它制造"发布时改名"。** 草稿叫 `dev.temp.draft.ops.plan.write`、发布后叫
   `ops.plan.write`,那么兄弟草稿的 `permissions.skills` 该写哪个?写带前缀的,
   promote 时必须重写所有引用方;写不带前缀的,试跑期草稿之间解析不到对方。
   **这正是 §3.8 ComfyUI「命名不一致导致自动解析失败」教训的翻版**——那是
   本文调研里唯一一条"我们已经安全"的结论,加前缀等于主动把它作废。
2. **推导档会失真。** `derive_skill_tier` 沿 `permissions.skills` 递归取 max。
   草稿名 ≠ 生产名 → 闭包不同 → **Lab 里算出来的档不等于上生产后的档**,
   而 G3 的全部意义就是"Lab 和生产是同一个档函数"。
3. **与 NAMING.md 的轴冲突。** 第一段是**能力域**(`system`/`common`/`external`/
   `project`/`user`),回答"这是什么种类的能力";`dev.temp.draft` 回答的是
   "它处在什么生命周期阶段"。两个正交的轴不该塞进同一个命名空间——一个 skill
   从草稿变成生产,它的**种类**并没有变。

**前缀真正想要的东西,用更小的解法。** 底下的合理诉求是:**现在从 trace 看不出
这个 run 用没用草稿**。解法是标记 **run**(给 Lab 试跑的 run 打 lab 标记)+
让 compound 的解析结果带层来源(`resolve(ref) -> (Skill, layer)`),
而不是重命名 **skill**。

`compute_closure` 已经把这件事做对了:成员表里 `status`
(`draft`/`production`/`external`/`missing`)与 `name` 是**分开的两个字段**——
身份归身份,层归层。前缀是把层信息硬编码进身份,同一个信息放错了地方。

想标"实验性"的作者可以用 NAMING.md 里已有的 `user.*` 域,作者自愿选,
系统不强制、不改名。

### 6.11 草稿层的持久性(**已知缺口**,只记录不改行为)

草稿的存放现状:`drafts_root` 缺省 `<artifacts>/drafts`,即 `.agent-os/drafts`,
而 `.gitignore` 忽略整个 `.agent-os/` → **草稿不进版本控制、无历史**。回滚面
只有一层 `.bak`(连续两次保存丢失更早版本);`delete()` 直接 `shutil.rmtree`
整个目录,**连 `.bak` 一起删**。

但 `draft_store.py` 的 `delete()` 注释写的是"不做回收站——git/.bak 是回滚面"
——**缺省配置下 git 并不是回滚面,注释与事实矛盾**。

**对包的含义(这是为什么它值得单列)**:promote 之前,包的**全部 N 个成员**
都只活在这个无历史的目录里。单技能时风险是一份,包化之后是 N 份,而且成员
之间还有引用关系——丢一个成员,整个闭包就断了。另外 §6.4 的事务若打算用
"失败就回到草稿态"兜底,这个前提比看上去脆。

**本文不给出行为改动**:把 `drafts_root` 指向一个进 git 的路径是**配置层的
决定**,不该由设计文档替用户定。记录在此作为待决问题;若要动,优先级最高的
一条是让 `delete()` 不要连 `.bak` 一起删。

---

## 7. 分期、验收与测试

| 期 | 内容 | 关闭 | 状态 |
|---|---|---|---|
| **P1** | `compute_closure` + closure API + 包视图 + G2 引用完整性 | v0.1 断点 1.1-B | ✅ 9129e72 |
| **P1'**(修正) | ① 修 promote 复跑 / 助手 validate 漏传 `store=`;② 复跑失败错误信息区分"报告不一致"与"判定不一致";③ 编辑闭包语义(只经草稿下传)+ `mode` 参数;④ 两阶段严格性 `strict_refs` | §2.2 死锁、§2.3 边界、Q1、Q2 | ✅ 361e6dc |
| **P2**(升为必需) | plan 端点 + `package_hash` + 先证后换事务 + 整文件单 `.bak` + 包级 promotions | Q3、Q4、§2.4 | ✅ 361e6dc |
| **P3** | 提交计划面板 + 悬空一键成稿;助手 `lab.draft.create`/`lab.pkg.closure` + 信任四围栏 + 包级 prompt | Q7、v0.1 断点 1.1-A | ✅ 08438c2 |
| **P4** | 目录形态 set 落盘 + Skills 树包徽标 + 功能包模板两件(方案 D 入口) | 断点打磨 | ✅ 6ce01ed |
| **P1.5** | `DraftSkillRegistry` + `CompoundSkillRegistry`(§6.10)——纯重构,消掉 `closure._Overlay` 重复;`resolve()` 带层来源 | §6.10 三处重复 | ✅ 本次(844 passed) |
| **P2.5** | 依赖变更档位告警(§6.9):反向闭包 + 档位/权限并集差分 + 确认行 | §3.10 静默抬档 | ⬜ 未实现 |
| **P5** | plan 面板聚合两项(§6.3 尾:聚合 tier 来源 + tool 权限并集);`package:` 身份段与多入口(方案 B / Q5);包级 G4 覆盖率(Q6) | Q5、Q6 | ⬜ 未实现 |

> **口径说明**:P1-P4 的实际落地顺序与本表初稿略有出入(包视图在 P1 就落了,
> `package:` 身份段与 G4 覆盖率则推迟到 P5)。表中已按**实际 commit** 校正,
> 不保留初稿排期——排期是手段,不是记录。

**验收要点(每条都应有测试)**

- P1':§2.2 的复现脚本三步全绿(检查绿 → 提交给出**说得通**的结论);
  编辑闭包遇生产节点即停(用 `ops.*` 同命名空间的已发布技能断言,防回归到
  命名空间启发式);同一草稿在 UI / 助手 / CLI 三条路径判定一致。
- P2:staging 加载失败时**现网文件字节不变**(核心断言);reload 失败能从
  `.bak` 回滚;plan 生成后改动任一成员 → promote 返回 409;`unchanged` 成员
  版本号不变。
- P3:助手无法在包命名空间外创建草稿;创建配额生效;助手工具面**恰好七件**
  且仍无 promote/delete(对齐现有 `test_tool_surface_exactly_five_and_tiers`
  的断言风格)。
- P4:多入口包的闭包 = 各入口闭包并集;L3 成员未覆盖 → G4 fail。

---

## 8. 从业界学到、但明确**不抄**的

写下来是为了防止后续讨论反复:

1. **不抄 VS Code Extension Pack 的"无功能依赖"约束**(§3.3)。那条约束服务的是
   "推荐清单"语义;能力包天生有依赖,把它拆成互不依赖的独立体是南辕北辙。
2. **不抄 npm/Helm 的 semver 约束求解。** 白皮书第 02 章已明示:基线实现单版本、
   依赖只查存在(`local_file.py`)。包不改变这一点——包是开发期组织,不是版本仲裁器。
3. **不抄 Agentforce 的 Topic 作为运行时实体**(§3.4)。Salesforce 自己在 2026-04
   把 Topic 改名为 Subagent,等于承认它就是子 agent;而 Agent OS 的
   "prompt + permissions 白名单"已经是这个东西。运行期零新调度单元,
   这条与 v0.1 §7 一致且现在有了外部证据。
4. **不抄 Helm 的 global values 跨子图覆盖。** 子图隔离不彻底是 Helm 公认的坑;
   Agent OS 的帧隔离(FrameContext 私有、父子只经参数与结果通信)比它强得多,
   包绝不能开一个"包级共享配置"的口子把它破坏掉。
5. **不做模板继承/组合体系**(§3.7)。Backstage 在这上面卡了很久。功能包模板
   保持扁平、少而具体;组合性交给助手。
6. **不做 `package.yaml` 成员清单**(与 v0.1 一致)。方案 B 声明的只有身份
   (title/summary/entries),成员永远推导。
7. **不给草稿加命名空间前缀**(§6.10)。分层解析已经解决撞名,前缀反而取消了
   分层的核心能力(遮蔽),并制造"发布时改名"。

---

## 9. 与并行稿「Skill Pack 设计方案」的对照裁决

另有一份并行设计稿(Kimi Code,方案 A 声明式技能包 / B 需求驱动脚手架 /
C 完整包管理),参照系为 Anthropic Agent Skills、ComfyUI Manager、
VS Code Extension Pack、扣子 & Dify、GlassWorm。两稿在最重要的地方**独立收敛**
——闭包是交付单位、缺失依赖要"检测 + 一键补齐"、提交必须是事务。本节固化
差异处的裁决,避免后续反复。

### 9.1 已采纳(并入本文)

| 来自并行稿 | 并入位置 |
|---|---|
| 包权限 = 闭包 tool 权限并集,提交前整体展示;聚合 tier 要点名"是谁抬高的" | §6.3 尾 |
| 传递依赖静默变化是高危事件(GlassWorm)→ 依赖变更档位告警 | §3.10 + §6.9 |
| 复用优先:分解需求时先搜注册表,能复用不新建 | §4 方案 D |
| 「叶子先行」拓扑序 → 提炼为「入口最后」不变量 | §6.4 |
| ComfyUI「检测缺失 → 一键补齐」 | §3.8(与 v0.1 悬空一键成稿是同一交互的独立发现) |
| 官方模板是生态早期的质量杠杆(扣子) | §3.7 |

其中 **§6.9 是本次合并最重要的增量**:它指出的缺口在当前代码里真实存在,
而且不需要任何新基础设施就能修。

### 9.2 未采纳(附理由)

1. **`depends_on` 与 `permissions.skills` 解耦**(构建期图 vs 运行时授权)。
   在别的系统里"允许调用"确实宽于"依赖",拆开有意义;但 Agent OS 的
   `visible_to` 是**直接从 `permissions.skills` 生成伪工具 schema** 的,白名单外
   的技能物理上调不到。两者是同一个集合,拆成两份清单只会制造必须手工对齐的
   腐烂面——正是包设计要消灭的东西。
2. **版本 `range` 约束**。白皮书第 02 章明确记载:版本约束求解是设计承诺、
   代码不做,基线单版本、依赖只查存在。加 range 不是包特性,是一次独立的架构
   变更,不该搭包的车(与 §8 第 2 条同)。
3. **包清单里的 `members` 列表**。成员推导不腐烂,清单会。但**保留 `entry`**
   ——它正是本文方案 B 的 `package.entries`,解决多入口(Q5),列入 P5。
4. **新增 G6 关**。引用完整性 P1 已落在 G2;权限并集与 tier 抬升属 G3 语义。
   不为同一批判定新开一关。
5. **完整包管理(方案 C:registry + lockfile + 更新中心)**。单用户/早期阶段
   过度设计,且有运营成分(审核、精选)。但其中**"依赖树 diff 必须重新确认"**
   一条已被提取为 §6.9,不必等 registry。

### 9.3 事实更正(并行稿成稿时比代码旧一个 commit)

- **"跨 skill 检查五关都不查"**——P1(9129e72)已把依赖存在性与循环检测放进 G2。
- **"试跑只跑表面 skill,跑挂了才知道依赖没就位"**——不成立。
  `OverlaySkillRegistry.get` 解析序为草稿 → extra → 生产,且被换进
  `kernel.skills` / `context._skills` / `tools._skills` 三个引用点,子技能草稿
  正常解析、正常压帧。**试跑一直是闭包级的**——v0.1 自己就把这条列为"系统早已
  按包工作"的第三个证据。
- **"依赖 tier 比表面 skill 高,五关不查"**——G3 本来就按白名单递归取 max 推导档。
  真正成立的点是**没有把权限并集和"谁抬高了 tier"展示出来**,那是呈现缺口
  不是检查缺口(已并入 §6.3)。

---

## 附:来源

- Nix closure(推导式依赖与 closure bloat):[Zero to Nix — Closures](https://zero-to-nix.com/concepts/closures/)、[nix why-depends](https://nix.dev/manual/nix/2.28/command-ref/new-cli/nix3-why-depends.html)、[How does Nix compute runtime dependencies?](https://discourse.nixos.org/t/how-does-nix-compute-runtime-dependencies/11381)
- Helm umbrella chart / 原子安装:[Helm Umbrella Charts for Multi-Component Applications](https://oneuptime.com/blog/post/2026-01-17-helm-umbrella-charts-multi-component/view)、[Managing Helm Chart Dependencies and Subcharts](https://oneuptime.com/blog/post/2026-01-17-helm-chart-dependencies-subcharts/view)、[Isolation issues with Helm umbrella charts](https://mikemybytes.com/2020/11/25/isolation-issues-with-helm-umbrella-charts/)
- Claude Code Plugin 打包:[Plugins reference — Claude Code Docs](https://code.claude.com/docs/en/plugins-reference)、[Marketplace and Plugin System — anthropics/skills](https://deepwiki.com/anthropics/skills/2.3-marketplace-and-plugin-system)、[Packaging Claude Code Plugins](https://jsmanifest.com/claude-code-plugin-packaging-guide)
- Salesforce unlocked package:[5 Anti-Patterns In Package Dependency Design](https://medium.com/salesforce-architects/5-anti-patterns-in-package-dependency-design-and-how-to-avoid-them-87bb50331cb8)、[Organize Metadata for Effective Package Development](https://trailhead.salesforce.com/content/learn/modules/unlocked-packages-for-customers/organize-your-metadata)
- VS Code Extension Pack:[Extension Manifest — extensionPack](https://code.visualstudio.com/api/references/extension-manifest)、[Extension Packs](https://code.visualstudio.com/blogs/2017/03/07/extension-pack-roundup)
- Agentforce Topic → Subagent:[Agentic Architecture 101: Custom Topics, Actions, and Best Practices](https://lanefour.com/agentforce/agentic-architecture-101-custom-topics-actions-and-best-practices/)、[Overview of Agentforce Components](https://academy.asagarwal.com/c/blog-posts/overview-of-agentforce-components-agents-topics-actions-prompts)
- Terraform plan/apply(先审后落):[How to Preview Infrastructure Changes with terraform plan](https://oneuptime.com/blog/post/2026-02-23-how-to-preview-infrastructure-changes-with-terraform-plan/view)、[Terraform Dry Run Explained](https://spacelift.io/blog/terraform-dry-run)
- changesets(变更聚合与依赖 bump):[Changesets docs](https://changesets-docs.vercel.app/)、[Monorepo version management with changesets](https://blog.alec.coffee/monorepo-version-management-with-the-changesets-npm-package)
- 工作区 vs 包:[Workspaces and Monorepos in Package Managers](https://nesbitt.io/2026/01/18/workspaces-and-monorepos-in-package-managers.html)、[VS Code multi-root workspaces for monorepos](https://medium.com/rewrite-tech/visual-studio-code-tips-for-monorepo-development-with-multi-root-workspaces-and-extension-6b69420ecd12)
- Backstage scaffolder 与模板组合性:[Writing Templates](https://backstage.io/docs/features/software-templates/writing-templates/)、[Feature: scaffolder template composability #15241](https://github.com/backstage/backstage/issues/15241)
- ComfyUI Manager(缺失依赖一键补齐 / 命名不一致 / registry 通道):[ComfyUI 官方文档](https://docs.comfy.org/)、[ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager)
- Anthropic Agent Skills(SKILL.md、渐进披露、allowed-tools、运行时组合):[skills/README — anthropics/skills](https://github.com/anthropics/skills/blob/main/README.md)、[Agent Skills 工程博客](https://www.anthropic.com/engineering)
- GlassWorm 供应链攻击(传递依赖静默变更):[GlassWorm Supply-Chain Attack Abuses 72 Open VSX Extensions](https://thehackernews.com/2026/03/glassworm-supply-chain-attack-abuses-72.html)、[GlassWorm: The First Self-Propagating VS Code Extension Worm — Veracode](https://www.veracode.com/blog/glassworm-vs-code-extension/)、[Inside CrowdStrike's Takedown of a Developer-Targeting Botnet](https://www.crowdstrike.com/en-us/blog/inside-crowdstrike-takedown-of-a-developer-targeting-botnet/)
- 扣子(Coze)/ Dify(模板即闭包、一键复制、官方模板作为质量杠杆):平台模板商店与生态对比资料
