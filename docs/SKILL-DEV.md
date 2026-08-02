# Skill 开发平台 · 设计方案

> 版本:v0.1(方案)
> 对象:Agent OS Web 宿主新页面(`#/lab`)+ skill 子系统(草稿存储/闸门/热重载)
> 关系:skill 契约见 `DESIGN.md` §2.1 与 `api/v1/skills.py`;分档与生产标准见
>   `ESCALATION.md` / `TIER-STANDARDS.md`;命名见 `NAMING.md`;测试基建见
>   `RUNNERS.md` §3.4(replay);UI 基线与主题契约见 `WEB-UI.md` / `DEBUG-UI-THEMES.md`
> 用户需求(原始四条 + 闸门):
>   1. 基本操作:skill 的每个部分都可以手动更改;
>   2. 主要操作:旁边一个类 Chatbot 的大输入框,让 agent 帮忙开发和修改 skill;
>   3. 测试:skill 可以在开发面板里测试;
>   4. 前提:skill 子系统提供 skill 的存储;
>   5. **提交闸门**:确认提交前必须经过标准化 test,确保内容与 metadata 的质量与合规。

---

## 1. 系统设计

### 1.1 总览

```
┌──────────────────────── #/lab(Skill Lab,三栏)────────────────────────┐
│ 编辑器(手动改每一部分) │ Agent 助手(chat) │ 测试面板(run + trace) │
└──────────┬──────────────────────┬──────────────────────┬───────────┘
           │ REST                 │ REST(run 复用)        │ REST(run 复用)
┌──────────▼──────────────────────▼──────────────────────▼───────────┐
│ SkillDevService(host/web)                                          │
│  drafts CRUD │ validate(提交闸门) │ test-run │ promote             │
└──────────┬─────────────────────────────────────────────────────────┘
┌──────────▼─────────────┐   ┌──────────────────────────────────────┐
│ DraftStore(草稿存储)    │   │ OverlaySkillRegistry                  │
│  drafts/<name>/SKILL.* │+  │  = 生产 registry + 草稿层(优先)      │
└────────────────────────┘   └──────────────────────────────────────┘
           │ promote(闸门通过)            │ reload(热重载现状)
┌──────────▼─────────────────────────────────────────────────────────┐
│ 生产 skills.yaml(唯一事实源;local_file loader 拓扑/热重载)          │
└────────────────────────────────────────────────────────────────────┘
```

关键设计决定:

- **草稿与生产分离**。生产 registry(skills.yaml)永远只装"过了闸门的 skill";
  开发中的草稿存 `drafts/` 目录,每个草稿一个目录(manifest.yaml + prompt.md +
  handler.py(可选)+ tests/)。**OverlaySkillRegistry** 把草稿层叠在生产 registry
  之上:测试运行时草稿优先,生产 run 永远看不到草稿。这避免"改一半的技能把
  生产 run 炸了"的整类事故。
- **promote 是唯一从草稿到生产的通道**,且必须先过提交闸门(§1.4)。
  promote 是 host 动作不是 skill 调用,不走升权闸;但它是真实写操作
  (L2 语义),UI 上要显式确认(§2.4)。
- **Agent 助手就是 Agent OS 的一个 skill**(dogfood):内置 meta-skill
  `skill.dev.assistant`,白名单是 `skill.draft.*` 工具组(读/写/列/校验/试跑
  草稿,L2 推导档)。它**不能 promote**——工具面里根本没有 promote,
  提交按钮只有人能点(对齐升权原则:注入者无法自己批准自己)。
- **测试面板复用 run 基建**:试跑 = 用 OverlaySkillRegistry 装配一次真 run
  (真实 provider 或 replay MockProvider),trace 用调试台既有组件渲染;
  结果用 outputs schema 校验 + RunRecord 汇总。

### 1.2 DraftStore(需求 4:skill 子系统允许存储 skill)

```
drafts/
└── weather.query/
    ├── manifest.yaml    # 与 skills.yaml 单条同形(SkillManifest 契约全集)
    ├── prompt.md        # kind=prompt 的指令体(entry 槽位启用)
    ├── handler.py       # kind=code 的处理器(可选)
    └── tests/
        ├── case1.json   # {input, expect?: {outputs 子集 | mock_script}}
        └── ...
```

- 契约:草稿 manifest 与生产同构(`parse_manifest` 直接消费),但**允许临时
  不合规**——草稿的意义就是"还没过闸";合规性由闸门在 validate/promote 时判。
- 版本与 provenance:promote 时写 `version`(缺省 0.1.0,已存在则 bump patch,
  可手改)+ 记录 `{"promoted_by": principal.subject, "gate_report": <id>}`
  (SkillArtifact/Provenance 契约槽位的首个实际使用,对齐 M6 方向)。
- 存储实现放 `skills/draft_store.py`,与 `local_file.py` 平级;loader 只读生产,
  DraftStore 只读/写草稿,OverlaySkillRegistry 在装配层合并。

### 1.3 编辑面(需求 1:每一部分都可以手动更改)

SkillManifest 契约全集分组进编辑器,**无隐藏字段**:

| 组 | 字段 | 编辑控件 |
|---|---|---|
| 身份 | name / version / kind / description | 文本(name 实时查 NAMING.md 规范) |
| 契约 | inputs / outputs | JSON Schema 编辑器(复用 schema-view + launch-dialog 的校验) |
| 指令 | prompt / entry / handler / logic | 代码编辑器(mono 文本域;code 技能显示 handler 路径) |
| 权限 | permissions.tools / skills / blackboard | 白名单 chips 编辑器(候选来自 registry,带 perm/tier 徽标) |
| 策略 | model / context_policy / limits | 表单(prefer 模型、max_steps、timeout 等) |
| 信任 | trust.confirm / reversal / blast_radius | 表单;推导档实时显示(只读,随 permissions 变化) |
| 行为 | inline / verifier | 开关/文本;inline 在推导档 ≥L2 时禁用并提示硬闸门 |

保存 = 写草稿(不校验,可随时存半成品);**校验只在点"检查"或"提交"时发生**
——编辑器永远不打断创作流,闸门永远守在出口。

### 1.4 提交闸门(需求 5:标准化 test)

`POST /api/lab/{draft}/validate` 跑固定五关,产出**闸门报告**(结构化,
每次 validate 落盘 `drafts/<name>/gate/<ts>.json`,报告 id 进 promote 记录):

| 关 | 内容 | 依据 |
|---|---|---|
| G1 metadata | name 合 NAMING.md 层级规范;description 是 "Use when / Do not use when" 路由式;version 语义化 | 现状 lint |
| G2 契约 | inputs/outputs 是合法 JSON Schema;L2+ 每个参数有 `type`;inputs 示例骨架可生成 | 契约冻结面 |
| G3 分档合规 | 推导档计算;≥L2 禁 inline;L3 禁 confirm:first;L2 `reversal` 必填;L3 `blast_radius` 必填;白名单内工具的 side_effect 清单列出(让人看见档从哪来) | ESCALATION/TIER-STANDARDS(E3 把必填 lint 落到这里) |
| G4 冒烟试跑 | 用草稿自带 tests/ 用例跑真 run(MockProvider replay 优先,无 mock 则真实 provider 小预算);**outputs 必须过 outputs schema**;步数/token 不超 limits | RUNNERS §3.4 |
| G5 提示词卫生 | prompt 不含"跳过确认/直接执行/无视用户"类注入诱导(TIER-STANDARDS §8 反模式);不引用不存在的 skill/tool | 反模式表 |

判定:`pass | warn | fail` 逐关;**任何 fail 都不能 promote**;warn 强制
人工勾选"我已阅读警告"。报告在 UI 渲染为五关卡片(红/黄/绿 + 详情展开)。

promote 流程:`validate 全绿(或 warn 已确认)` → 前端带 `gate_report_id`
调 `POST /api/lab/{draft}/promote` → 服务端**复跑** G1-G3(防报告过期/篡改,
G4/G5 信报告)→ 写入 skills.yaml → loader reload() → 草稿归档
(`drafts/<name>/` 保留,标 `promoted_at`,可继续迭代产生下一版)。

### 1.5 API 面(host/web/app.py 挂载)

```
GET    /api/lab/drafts                     # 草稿列表(含推导档/闸门最近结果)
POST   /api/lab/drafts                     # 新建(空模板或从生产 skill 复制)
GET    /api/lab/drafts/{name}              # 读草稿(manifest + prompt + handler + tests)
PUT    /api/lab/drafts/{name}              # 保存(整体替换;自动备份上一版 .bak)
DELETE /api/lab/drafts/{name}              # 删草稿(L2 语义,UI 确认)
POST   /api/lab/drafts/{name}/validate     # 跑闸门,返回报告 id + 五关结果
POST   /api/lab/drafts/{name}/test-run     # 试跑:input 或 tests/case,返回 run_id
GET    /api/lab/drafts/{name}/tier         # 实时推导档(编辑器随 permissions 刷新)
POST   /api/lab/drafts/{name}/promote      # 闸门通过后提升进生产
```

test-run 与 agent chat 都走既有 run 管理(run_manager)+ SSE,前端零新协议。

## 2. 用户体验

### 2.1 页面布局(`#/lab`)

```
┌──────────────────────────────────────────────────────────────────┐
│ [草稿: weather.query  v0.3.1]  [推导档: L2 reversible] [检查][提交] │
├─────────────────────┬───────────────────────┬────────────────────┤
│ 编辑器               │ Agent 助手            │ 测试面板            │
│ (分组表单+代码域)     │ ┌───────────────────┐ │ [输入 JSON] [▶ 试跑] │
│ 身份/契约/指令/      │ │ chat 记录(SSE 流)  │ │ ┌────────────────┐ │
│ 权限/策略/信任/行为  │ │                   │ │ │ trace(复用调试台│ │
│                     │ │                   │ │ │  轨迹组件)      │ │
│                     │ ├───────────────────┤ │ ├────────────────┤ │
│                     │ │ [大输入框      ][↵]│ │ │ 结果+outputs    │ │
│                     │ └───────────────────┘ │ │ schema 校验     │ │
│                     │                       │ ├────────────────┤ │
│                     │                       │ │ 闸门报告(五关)  │ │
├─────────────────────┴───────────────────────┴────────────────────┤
│ 状态栏:已保存 12:03 · 草稿(未提交) · 距上次检查有改动 ⚠            │
└──────────────────────────────────────────────────────────────────┘
```

- 三栏宽度可调;窄屏折叠为 tab(编辑/助手/测试);
- **全部组件只消费契约 token**,六主题开箱即用(组件零分支红线);
  新文案 key 走 copy 契约,六主题 copy 表同步补齐(契约测试强制);
- 推导档徽标复用升权卡片的 tier→perm 色板槽位。

### 2.2 Agent 助手交互(需求 2)

- 输入框是大文本域(多行,支持粘贴一段 API 文档/错误日志);
- 助手 = `skill.dev.assistant`(prompt 技能,模型策略用 RunConfig 默认):
  - 工具:`skill.draft.read`(读当前草稿)、`skill.draft.write`(整体/局部改
    草稿,L2)、`skill.draft.list`、`skill.draft.validate`(跑闸门,只读报告)、
    `skill.draft.test_run`(试跑,读 trace);
  - 它没有 promote 工具——**人能委托它改,不能委托它发**;
  - 它改完草稿,编辑器栏实时刷新(SSE 或轮询草稿 mtime),改动在编辑器里
    高亮 diff 一行("agent 改了 permissions.tools:+fs.write");
- 典型对话:
  - "帮我写一个查天气的 skill,输入城市名,输出温度和天气"→ 助手生成
    manifest+prompt,自动跑闸门,报告贴回聊天;
  - "G3 说 L2 缺 reversal,帮我补"→ 助手补 reversal 字段并解释;
  - "把输入改成支持经纬度"→ 改 inputs schema + prompt + 自动试跑;
- 助手的所有草稿修改都是 L2 真实写入,**升权系统原生保护**:
  若有人骗助手调用 L3 skill,升权闸照样拦(dogfood 展示面)。

### 2.3 使用流程(端到端)

1. **新建**:草稿列表页 → "新技能"(空模板 / 从模板:prompt 查询类、code
   处理类 / 从生产复制改);
2. **起草**:手动填 + 或先对助手说一句"我要个……"让它打底,再手改;
3. **迭代**:改 → 试跑(测试面板)→ 看 trace/结果 → 再改;随时保存;
4. **检查**:点"检查"跑五关;红的修(可让助手修),黄的确认;
5. **提交**:全绿后"提交"亮 → 弹确认(写入生产 skills.yaml + version) →
   promote → 跳生产 skills 页,新技能已可 run;
6. **再迭代**:生产 skill → "在 Lab 中编辑"→ 复制为草稿新版本 → 回到 2。

失败路径:闸门 fail → 报告指到具体关与条款;试跑 fail → trace 里定位;
promote 后想回滚 → skills.yaml 的 .bak(promote 自动备份)+ git。

### 2.4 关键 UX 原则

- **编辑器不打断,闸门守出口**:保存永不报错,错误全部汇聚到闸门报告;
- **所见即所得**:试跑跑的就是生产形态的 run(同装配、同闸门),
  不存在"开发环境能跑上线就挂";
- **人最终拍板**:agent 可以改、可以试、可以查,提交按钮只有人点;
- **每处都有出处**:推导档悬停显示"来自 permissions.tools 里
  system.file.delete(irreversible)";闸门每条 fail 链到 TIER-STANDARDS 条款。

## 3. 与现有系统的接点

| 系统 | 接法 |
|---|---|
| skill 契约/loader | 草稿 manifest 同构复用 `parse_manifest`;promote 走 loader `reload()` 热重载 |
| 闸门 | G1 复用 `validate_manifest`;G3 复用 `validate_escalation_gates` + tier 推导(E3 的 reversal/blast_radius 必填 lint 在此落地);G4 复用 replay/run |
| 升权 | 助手技能本身是 L2;助手被骗调 L3 → 升权闸原生拦截(展示面);promote 不走升权(host 动作) |
| 数据 authZ | 草稿目录注册为数据域 `fs.drafts`(internal);助手 principal = web 单用户 |
| Web 基建 | run_manager/SSE/trace 组件/主题契约/copy 契约全部复用;新页面进 `data-route="lab"`,主题 scope app-wide 自动覆盖 |
| CLI | `agent-os lab validate <draft>` 暴露同一闸门(头less 检查,coding agent 可用) |

## 4. 分期

| 期 | 内容 | 验收 |
|---|---|---|
| L1 ✅ | DraftStore + drafts CRUD API + 编辑器(全字段)+ 推导档实时显示 | 手动建/改/存草稿;UI 无 agent/测试。已实现:`skills/draft_store.py` + OverlaySkillRegistry、`/api/lab` 六端点、`#/lab` 三栏骨架与七组编辑器;783 Python + 22 前端测试全绿 |

> 实现注(L1):
> 1. POST /api/lab/drafts 的复制参数名用 `from_skill`(`from` 是 JS/Python
>    双端关键字,避免转义面);
> 2. `/tier` 增加 `?tools=&skills=` 查询覆盖——编辑器**未保存**的白名单也能
>    实时推导(§2.4"保存永不打断"的自然延伸,磁盘草稿不被污染);
> 3. drafts_root 配置键定为 `[lab].drafts_root`(照 `[web].user` 先例),
>    缺省 `<artifacts_root>/drafts`;
> 4. 编辑器在客户端拦 JSON 不合法的 inputs/outputs(提示而非写坏草稿);
>    "保存永不报错"指服务端不校验,不代表把语法错写进存储;
> 5. 中右栏(Agent L4/测试 L3)与"检查/提交"按钮为占位/置灰,copy key
>    六主题已同步。
| L2 | 闸门 G1-G3 + validate API + 报告卡片 + promote(含 .bak 与 version bump) | 不合规草稿提交被拒,合规草稿进生产可 run |
| L3 | 测试面板:test-run + trace 复用 + outputs 校验 + G4 冒烟入闸 | tests/case 驱动试跑,失败 trace 可见 |
| L4 | Agent 助手:`skill.dev.assistant` + `skill.draft.*` 工具组 + chat 栏 | 对话式建/改 skill,助手无 promote 能力 |
| L5 | G5 提示词卫生 + CLI `lab validate` + 模板库 + diff 视图打磨 | 全套体验走查 |

依赖说明:L2 的 G3 必填 lint 即 ESCALATION 的 E3 一部分(两份计划在此汇合);
L4 依赖 L1-L3(助手改的是同一草稿、查的是同一闸门、试的是同一面板)。

## 5. 不做

- 不做多人协作/评论/审阅流(单用户优先;多用户等 DATA-AUTHZ D3);
- 不做版本分支/合并(promote 是线性版本 + .bak;git 才是真正的版本系统);
- 不做 skill 市场/分发(Provenance 信任链是 M6 的事);
- 不做助手自动 promote(永远人点提交);
- 不做可视化拖拽编排(文本契约是唯一事实源,编辑器是契约的视图)。
