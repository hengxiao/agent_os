# Skill Lab:草稿、助手与开发闭环

> 章次:14 · 状态:已实现(L1-L5 全部落地;个别打磨项未做,见 §6) ·
> 依据:docs/SKILL-DEV.md;agent_os/src/agent_os/skills/draft_store.py、skills/gate.py、
> skills/lab_assistant.py、tools/lab_tools.py、host/web/app.py、host/web/run_manager.py、
> host/web/static/js/components/lab.js、host/cli/main.py

## 1. 概述

Skill Lab 是 skill(提示词/代码资产)的开发闭环子系统:草稿存储(DraftStore)、叠在生产 registry 之上的草稿层(OverlaySkillRegistry)、五关提交闸门(gate.py)、对话式开发助手(skill.dev.assistant)与测试面板,对外呈现为 Web 宿主的三栏页面 `#/lab` 与 CLI 头less 入口 `agent-os lab validate`。它在架构上属于**宿主层 + skills 子系统**,不进内核:装配、仲裁、信号全部复用现有 run 基建,内核没有任何一行 Lab 专属代码。本章是执行摘要 §6.4 的深化,覆盖草稿/生产隔离、闸门判定逻辑、promote 校验链与"能改不能发"的助手信任模型。

## 2. 动机与背景(原因)

执行摘要 §1.2 列出的第四类结构性缺陷是"质量不可保证":skill 是系统里最重要的可编程资产,但其生产长期没有工程标准与准入闸门,元数据缺失、权限过宽、提示词含注入诱导等问题只能在运行期暴雷。Skill Lab 是对这一缺陷的应答,其形态由四股具体的架构张力塑造:

- **为什么不能直接在生产 registry 上改?** 生产 skills.yaml 是唯一事实源,loader 对它做拓扑排序与热重载;在途编辑意味着"改一半的技能"随时可能被生产 run 装配——一次半个 manifest 的保存就是一次生产事故。所以草稿必须与生产**物理分离**(独立 drafts/ 目录),合并只发生在装配层(OverlaySkillRegistry),且只对 Lab 自己的 run 可见(docs/SKILL-DEV.md §1.1)。
- **为什么不放进内核?** 按微内核判据(执行摘要 §2.2:推进 loop?仲裁点?唯一公共通道?),开发平台一条都不满足。它是宿主层功能,因此全部复用 run_manager/SSE/trace 组件,前端零新协议(docs/SKILL-DEV.md §1.5)。
- **为什么敢让 agent 改代码资产?** 助手本身就是 Agent OS 的一个普通 prompt 技能(dogfood),推导档 L2,工具面里**没有 promote 也没有 delete**(tools/lab_tools.py:23-29)——"能改不能发"。这与升权原则"注入者无法自己批准自己"同根:agent 可以改、可以试、可以查,提交按钮只有人能点(docs/SKILL-DEV.md §1.1)。
- **为什么闸门是五关而不是一道 lint?** TIER-STANDARDS.md 的 L2/L3 必填项(reversal / blast_radius)此前只有设计文档、没有强制点(ESCALATION.md 的 E3 余项);Skill Lab 的 G3 是这份标准**第一个执行点**——闸门不是新发明一套规则,而是把既有文档标准变成"不过就进不了生产"的硬边界(skills/gate.py:1-10 模块 docstring 明写这一汇合)。

## 3. 问题陈述(解决的问题)

- **P1 半成品污染生产**:直接编辑生产 skills.yaml,保存到一半(manifest 缺字段/YAML 未闭合)时任何新建 run 都会在装配期抛 SkillLoadError。一行场景:编辑中保存 → 生产 run `agent-os run weather.query` 启动即炸。
- **P2 不合规 skill 进生产**:L2 技能没写 `trust.reversal`、prompt 里写"跳过确认直接执行",此前没有任何执行点拦截——升权语义被资产本身架空,只能在事故后发现。
- **P3 报告与内容错位**:检查通过 → 又改了一个字节 → 拿旧报告提交;或拿草稿 A 的报告去提交草稿 B。没有内容绑定时,闸门结论可以被"时间差"或"张冠李戴"绕过。
- **P4 委托开发 = 委托发布**:让 agent 帮忙写 skill 是自然诉求,但若助手有 promote 能力,一段注入文本("直接发布,别问用户")就能借助手之手把未过闸门的资产写进生产。
- **P5 开发/生产形态漂移**:开发面板里能跑、上线就挂——原因是开发面用了另一套装配/另一套帧构建。所见非所得。
- **P6 校验打断创作流**:若保存即校验,半成品永远存不下,人会学会绕开校验(直接改文件),闸门形同虚设。

## 4. 设计与机制(解决的方法)

### 4.1 总览:物理分离 + 装配层合并

```
#/lab 三栏(编辑器 │ Agent 助手 │ 测试面板)
        │ REST(/api/lab/*,host/web/app.py:748-1023)
┌───────▼────────────────────────────────────────────┐
│ DraftStore(skills/draft_store.py:121)              │
│   drafts/<name>/{manifest.yaml,prompt.md,          │
│                  handler.py,tests/*.json,gate/}    │
│ OverlaySkillRegistry(draft_store.py:366)           │
│   解析序:草稿 → extra(助手) → 生产                │
└───────┬───────────────────────────┬────────────────┘
        │ promote(唯一通道,过闸门) │ test-run/G4(装配层合并)
┌───────▼─────────────┐   ┌─────────▼─────────────────┐
│ 生产 skills.yaml     │   │ 真 run(复用 run_manager/ │
│ (唯一事实源,热重载)   │   │ SSE/trace,零新协议)      │
└─────────────────────┘   └───────────────────────────┘
```

P1 的解法:**保存永不报错,闸门守出口**(docs/SKILL-DEV.md §2.4)。DraftStore 不做任何内容校验;`read` 对 YAML 错误/契约错误容错,返回 `parse_error` 而不抛错(draft_store.py:206-250),半成品永远能打开继续改。写操作唯一的防护是 `_write_with_bak`——覆盖写前把上一版复制为 `.bak`(draft_store.py:440-444),这是保存面唯一的后悔药。草稿名正则 `^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$`(draft_store.py:41)同时是 NAMING.md §2 规范与路径穿越防护:字符面不含 `/`、`..`,目录名即草稿名。前端在保存前拦 JSON 语法错(lab.js:468-476)——"不打断创作流"指服务端不校验,不代表把语法错写进存储(SKILL-DEV.md L1 实现注 4)。

### 4.2 OverlaySkillRegistry:草稿优先,半成品不遮蔽

overlay 实现 SkillRegistry 全协议面(get/visible_to/make_frame/manifests,draft_store.py:387-428),`get` 的解析序为 **草稿 → extra → 生产**;草稿暂不合规时**透明回落**生产同名(draft_store.py:388-394)——半成品影子不遮蔽可用版本。test-run 经 `start_run(kernel_patcher=)` 接线,`swap_skills_overlay` 把 overlay 换进内核的三个 skills 引用点(kernel.skills / ContextManager._skills / tools._skills,host/web/run_manager.py:456-464);overlay 只活在 Lab 请求作用域,生产 run 不受影响(run_manager.py:446-453)。

P5 的解法落在两个复用上:压帧构建与生产共用同一函数 `local_file.build_child_frame`(draft_store.py:414-417;防两套帧语义漂移),trace 用调试台既有组件 `deriveTraceView/renderTrace` 渲染(lab.js:21)。试跑的就是生产形态的 run——同装配、同闸门、同渲染。

### 4.3 五关提交闸门

`validate_draft`(skills/gate.py:122)跑固定五关,逐关判 `pass|warn|fail`(另有 `skip` 占位态),报告落盘 `drafts/<name>/gate/<ts>.json`:

| 关 | 内容 | 关键判定 | 代码锚点 |
|---|---|---|---|
| G1 metadata | 命名/描述/版本 | name 不合层级规范 = fail;description 缺 "Use when"、version 非语义化 = warn;并入 `validate_manifest` 现状 lint | gate.py:155-178 |
| G2 契约 | inputs/outputs | 非法 JSON Schema = fail;推导档 ≥L2 时每个 inputs 参数必须有 `type`(不收自由文本) | gate.py:181-207 |
| G3 分档合规 | 推导档 + 信任字段 | ≥L2 禁 inline、L3 禁 `confirm: first`、L2 必填 `reversal`、L3 必填 `blast_radius` = fail;白名单来源逐条 info(让人看见档从哪来) | gate.py:210-238 |
| G4 冒烟试跑 | tests/*.json 真跑 | overlay 装配真 run,outputs 必须过 outputs schema;无用例 = warn 不 fail | gate.py:241-272;app.py:865-884 |
| G5 提示词卫生 | 注入诱导扫描 | 逐句匹配反模式表(中/英),同句命中正面表述白名单("确认后/征得/ask the user"等)则放行 | gate.py:45-87 |

两个设计取舍值得写明。其一,G3 的推导档不允许自报,由 `explain_skill_tier` 按白名单递归取 max 现算(gate.py:149-150)——自报会撒谎,这与生产侧升权判定同一函数,Lab 不存在第二套档语义。其二,G5"宁稳勿滥"(gate.py:43-44):注入诱导检测用逐句判定 + 正面表述白名单,是为了不误伤"让用户确认后才执行"这类**正确**表述——误报多了人会关掉闸门,漏报的代价由 G4/人审兜底。

### 4.4 promote 校验链(唯一生产通道)

promote 是 host 动作而非 skill 调用,不走升权闸;但它是真实写操作(L2 语义),UI 显式确认(docs/SKILL-DEV.md §1.1)。其校验链(gate.py:334-396)是 P3 的完整解法:

```
POST /api/lab/{draft}/promote {report_id, version?, warnings_ack}
  ① 报告必须存在(read_gate_report 按 id 查,防编造;draft_store.py:329)
  ② 报告 manifest_hash == 当前草稿哈希
     —— 哈希覆盖 manifest+prompt+handler(gate.py:90-105),
        草稿改一字节旧报告即作废(GateError 409)
  ③ 报告无 fail
  ④ 服务端复跑 G1-G3 仍无 fail(G4/G5 信报告)
  ⑤ 有 warn 必须 warnings_ack(前端勾选"我已阅读警告")
  ⑥ 写生产 skills.yaml:写前 .bak 备份,同名替换/追加
     (write_production_entry,gate.py:399-425)
  ⑦ loader reload() 热重载(只影响后续新建的 run)
  ⑧ promotions.jsonl 追加 {promoted_by, gate_report_id, version}
     —— Provenance 契约槽位的首个实际使用
```

报告 id = `<ts_ms>-<manifest_hash>`(draft_store.py:323):审计可读,防错位比对键内嵌其中。为什么复跑只 G1-G3 而 G4/G5 信报告?成本:G4 要装配内核真跑 run(app.py:865-884),promote 路径上重跑的代价不成比例;而 G1-G3 是纯静态判定,复跑便宜且恰好覆盖"资产声明面"——哈希已保证内容未变,复跑防的是"报告本身被手工篡改"。版本缺省 bump patch,非语义化版本回落 0.1.0,"无法 bump 就不假装能 bump"(gate.py:313-327)。前端把同一逻辑做成状态机:`promoteReady` 要求报告在、无 fail、不过期、warn 已勾选(lab.js:304-316),保存后立即让提交按钮熄灭——服务端哈希校验是兜底,客户端是体验。

### 4.5 Agent 助手:dogfood 的信任模型

助手是内置 meta-skill `skill.dev.assistant`(skills/lab_assistant.py:39-64):普通 prompt 技能,白名单恰好五件 `lab.draft.*` 工具,limits max_steps=12/timeout=120。它经 overlay 的 `extra` 槽注入(解析序在草稿之后、生产之前),不进生产 registry;工具在 kernel_patcher 里现场注册,生产 run 不可见(app.py:973-986)。五工具的 side_effect 全部**显式声明**(tools/lab_tools.py:69-192):

| 工具 | permission | side_effect | 说明 |
|---|---|---|---|
| lab.draft.read / list | READ | none | 读草稿/列草稿 |
| lab.draft.write | WRITE | reversible | 整体或局部改;`.bak` 即逆转机制 |
| lab.draft.validate | READ | none | 跑闸门,**只读报告不落盘** |
| lab.draft.test_run | READ | none | overlay 真 run;副作用圈在 run 的 workdir;工具内限步 25(lab_tools.py:52) |

P4 的解法有三层:工具面没有 promote/delete(注入者借助手发布在协议上不可能);`lab.draft.validate` 的报告不落盘,promote 只认落盘报告——助手报告不作数,与"能改不能发"同根(SKILL-DEV.md L4 实现注 3);局部写字段白名单 `_WRITABLE_FIELDS` 不含 `name`(lab_tools.py:33-49),name 由目录名钉死,助手改不出路径穿越形态。若有人骗助手调用 L3 技能,升权闸照样拦——助手没有特权旁路。一个实现期暴露的契约约束:设计稿原名 `skill.draft.*`,但内核 `_dispatch_call` 把 `skill.` 前缀当子技能调用拦截,永远到不了 Tool Registry,故改名 `lab.draft.*`,语义不变(SKILL-DEV.md L4 实现注 1)。前端配套:助手改稿后编辑器以服务端草稿为准重载,顶层字段 diff 给一行高亮(lab.js:620-636)。

### 4.6 前端三栏与 CLI

`#/lab` 页面:左栏七组全字段编辑器(身份/契约/指令/权限/策略/信任/行为,lab.js:188-264),无隐藏字段;推导档徽标复用升权卡片的 tier→perm 色板,`/tier` 端点接受 `?tools=&skills=` 查询覆盖——编辑器**未保存**的白名单也能实时推导,磁盘草稿不被污染(app.py:812-839);推导档 ≥L2 时 inline 复选框禁用并提示硬闸门(lab.js:686-689)。删除是 L2 语义:两击确认(lab.js:764-777)。CLI `agent-os lab validate` 与 Web 跑同一 `validate_draft`,报告同样落盘 `gate/`(共用 drafts_root,CLI 查完 Web 可直接 promote),退出码 pass/warn=0、fail=2(host/cli/main.py:335-358)。

## 5. 效果与验证(效果)

落地后,一条完整的开发闭环是:新建(空/三档模板/从生产复制)→ 手动编辑 + 助手对话式修改 → 测试面板试跑(trace 可见、outputs 显式校验)→ 检查(五关卡片,红/黄/绿/灰)→ 人点提交(确认行内可改版本号)→ 生产热重载后可 run;再迭代走"生产 skill → 在 Lab 中编辑"复制回草稿。测试证据(lab 直接相关 6 个 Python 测试文件 43 例,全绿;全仓库基线 Python 812 例 + 前端 24 个测试文件,docs/SKILL-DEV.md §4 L5):

- tests/skills/test_draft_store.py(9 例):命名/路径穿越拒绝(`test_name_validation_path_traversal`)、容错读不 500、`.bak` 备份、overlay 草稿优先与不合规透明回落、三档模板创建即过 G1/G2。
- tests/skills/test_gate.py(11 例):五关逐关断言;`manifest_hash` 随内容变化;G5 注入诱导 fail 而正面表述放行(`test_g5_positive_phrasing_passes`);promote 成功路径与三类拒绝(过期报告/fail 报告/warn 未确认,`test_promote_rejects_stale_report_and_fail_and_unacked_warn`);版本 bump 与手改覆盖。
- tests/web/test_lab_api.py(9 例):CRUD 往返、不合规草稿读取不 500、tier 覆盖查询、validate 报告形态、promote 端到端与 stale 拒绝(409)。
- tests/web/test_lab_testrun.py(5 例):input/case 两种试跑形态、mock_script 走 MockProvider、outputs 不合时 run 标 failed、G4 无用例 warn、G4 过/败。
- tests/web/test_lab_assistant.py(5 例):**工具面恰好五件、无 promote/delete**(`test_tool_surface_exactly_five_and_tiers`)、助手白名单与 L2 档、局部写/整体写、validate 只读不落盘、端到端对话。
- tests/cli/test_lab.py(4 例):退出码 pass/warn=0、fail=2;报告落盘与 Web 共用;草稿不存在不 traceback。
- 前端 static/tests/lab.test.mjs:命名校验、草稿↔表单映射、五关卡片、promote 点亮/熄灭条件、保存与 DOM 冒烟。

涟漪效应:ESCALATION E3 的 reversal/blast_radius 必填 lint 在 G3 落地,两份计划在此汇合(SKILL-DEV.md §4);SkillArtifact/Provenance 契约槽位首次实际使用(promotions.jsonl);`build_child_frame` 抽为共享函数,overlay 与生产的子帧构建同一实现;`skill.` 前缀拦截约束因工具命名而显形并写进实现注。真实示例:三档模板库(prompt_query / file_process / danger_op,draft_store.py:55-118)的 trust 占位是真实机制描述,L2/L3 模板创建即过 G3 必填项。

## 6. 局限性与边界(局限性)

1. **promote 只支持单文件 skills.yaml**:`write_production_entry` 对目录/多文件 skill_set 形态显式报错(gate.py:406-410),归并策略未实现(SKILL-DEV.md L2 实现注 1);多文件形态的生产面无法经 Lab 发布。
2. **code 技能的 handler 源码不进生产**:promote 只把 prompt.md 内联进生产条目;handler 以 dotted path 原样携带,源码归并未做(L2 实现注 4)。从生产复制 code 技能时同理——源码"复制不出可运行形态"(draft_store.py:197-198 注释),只能留路径由人处理。
3. **G4 冒烟证据可过期**:`manifest_hash` 只覆盖 manifest+prompt+handler(gate.py:96-104),不含 tests/;改完用例旧报告不作废,而 promote 复跑只 G1-G3、G4 信报告(gate.py:359-366)——因此可以带着过期的冒烟证据 promote。缓解面:G4 证据语义是"当时跑通过这些用例",用例变化不改变当时结论;但严格性上这是一个已知的诚实缺口。
4. **G5 是正则模式表,有漏报面**:只覆盖模式表内的中/英表达(gate.py:45-60),变体措辞、其他语种、编码混淆可绕过;只扫 prompt,不扫 description 等其他自由文本;白名单(gate.py:61-66)也可能误放。定位是"宁稳勿滥"的第一道筛,不是注入检测的完整解。
5. **G4 无用例仅 warn 而非 fail**(gate.py:246-252):冒烟是质量面的主要证据,但人勾选"我已阅读警告"即可带零用例 promote——闸门把最终决定留给人,也把侥幸的空间留给人。
6. **回滚面浅**:删除草稿无回收站(draft_store.py:297-302);`.bak` 只有一层,连续两次保存丢失更早版本;promote 是线性版本 + .bak,无分支/合并——git 才是真正的版本系统(SKILL-DEV.md §5 明示不做)。
7. **单用户单会话前提**:助手改稿后编辑器以服务端草稿为准重载(L4 实现注 5);无协作/评论/审阅流;promote 不走升权闸,其授权边界 = Web 单用户 principal + UI 确认。多用户部署下的授权隔离依赖 DATA-AUTHZ 的 D3(已设计未实现)。
8. **前端轮询而非 SSE**:test-run 与助手回复用 500ms 轮询、120s 封顶(lab.js:548-550、605-606);超时只表示前端放弃等待,run 本身仍在跑。SSE 增量渲染与 diff 视图打磨未做(L3 注 2、L5 注 3)。
9. **助手能力上限**:limits max_steps=12、test_run 工具内限步 25——复杂多轮重构会触顶;助手 run 失败时前端只能看到错误文本,没有助手侧的 trace 面板联动。

## 7. 引用

- 设计文档:docs/SKILL-DEV.md(系统/UX/API/分期,含 L1-L5 实现注);相邻契约:docs/DESIGN.md §2.1、docs/ESCALATION.md、docs/TIER-STANDARDS.md、docs/NAMING.md、docs/RUNNERS.md §3.4
- 草稿与装配:agent_os/src/agent_os/skills/draft_store.py;agent_os/src/agent_os/skills/local_file.py(build_child_frame)
- 闸门与 promote:agent_os/src/agent_os/skills/gate.py
- 助手与工具:agent_os/src/agent_os/skills/lab_assistant.py;agent_os/src/agent_os/tools/lab_tools.py
- 宿主:agent_os/src/agent_os/host/web/app.py(/api/lab/*);agent_os/src/agent_os/host/web/run_manager.py(assemble_lab_kernel/swap_skills_overlay);agent_os/src/agent_os/host/cli/main.py(_cmd_lab)
- 前端:agent_os/src/agent_os/host/web/static/js/components/lab.js;static/tests/lab.test.mjs
- 测试:agent_os/tests/skills/test_draft_store.py、tests/skills/test_gate.py、tests/web/test_lab_api.py、tests/web/test_lab_testrun.py、tests/web/test_lab_assistant.py、tests/cli/test_lab.py

> 勘误注:执行摘要 §6.4 称 CLI 退出码为 "0/2/4";以代码为准,`agent-os lab validate` 实际只有 pass/warn=0、fail=2 两档(host/cli/main.py:335-358;tests/cli/test_lab.py 断言同)。
