# Lab 迭代工作流(Iterative Lab)设计 spec

> 版本:v0.1(spec)
> 对象:Skill Lab 的引导式开发模式——访谈初始化 → 首稿 → 批注式迭代 → 版本回溯
> 关系:构建在 `SKILL-DEV.md`(L1-L5)与 `SKILL-PACKAGES.md` / `SKILL-PACKAGES-V2.md`
>   (P1-P4)之上;闸门/原子提交不变,本设计是它们的**上游**(产出物最终仍走
>   plan/promote 进生产)。助手信任边界沿用 V2 §6.7(能改能建,不能发不能删)。
> 一句话:**访谈定义"做什么",批注驱动"怎么改",版本承载"改到哪"**。

---

## 1. 概念模型

### 1.1 三个阶段

```
① 初始化(Interview)          ② 首稿(Scaffold)           ③ 迭代(Iterate)
┌──────────────────┐   确认   ┌──────────────────┐  批注→生成 ┌──────────────────┐
│ Chatbot 访谈需求  │ ──────▶ │ 完整技能包首稿:    │ ────────▶ │ 左:既有版本      │
│ → 需求简报        │         │ entry+支撑+测试+  │           │ 中:批注→[生成]   │
│ (brief,用户确认)  │         │ 评审标准+开发笔记   │ ◀──────── │ 右:候选版本      │
└──────────────────┘         └──────────────────┘  满意→接受  │ (可跑测试/对比)  │
                                                     └──────────────────┘
                                                       ↑ 不满意→重新生成;随时可 rewind
```

### 1.2 核心实体

| 实体 | 定义 | 存储 |
|---|---|---|
| **需求简报 brief** | 访谈产物:目标/输入/输出/约束/示例/tier 提示;用户确认后生效 | `drafts/<pkg>/brief.json` |
| **版本 version** | 包的不可变快照(全成员 manifest+prompt+handler+tests);每次"接受"产生新版本 | `drafts/<pkg>/versions/vNNN/` |
| **工作副本 working** | 现有草稿布局(可手改;既有 Lab 行为) | `drafts/<pkg>/`(现状) |
| **候选 candidate** | 生成产物(未接受前不影响 working) | `drafts/<pkg>/candidate/` |
| **批注 comment** | 锚定到成员/字段/段落/用例的意见 | `drafts/<pkg>/comments/<round>.json` |
| **评审 rubric** | LLM-as-judge 评分标准(随测试用例) | `drafts/<pkg>/tests/*.json` 扩展字段 |

不变量:

- **版本不可变**(采纳 Skill Pack 方案的 GlassWorm 防线):接受即封存,rewind = 恢复副本到 working,不改历史;
- **接受是人的动作**:助手可以生成 candidate,永远不能替用户点接受(与"能改不能发"同根);
- **brief 是生成上下文的锚**:每次生成都带 brief,防需求漂移——改需求 = 回到访谈更新 brief,不在批注里偷渡;
- **promote 路径不变**:任何版本要进生产,仍走 P2 的 plan/五关/原子提交(本设计不产生第二条发布通道)。

## 2. 阶段一:初始化访谈(Interview)

### 2.1 流程

1. 用户在 Lab 点"新建(引导模式)",中栏变为访谈 chat;
2. Chatbot(`skill.dev.interviewer`,新 meta-skill,prompt 专门化)通过多轮对话采集:目标(一句话功能)、输入/输出示例、约束(能不能写文件/联网/执行)、质量偏好(简洁 vs 详尽)、测试倾向(有没有现成例子);
3. 访谈收敛后,chatbot 输出**结构化简报卡片**(非自由文本):

```jsonc
// drafts/<pkg>/brief.json
{
  "goal": "每天早上按天气和日程推晚餐建议",
  "entry": "dinner.planner",
  "inputs":  { "type": "object", "properties": { "date": {"type":"string"} } },
  "outputs": { "type": "object", "properties": { "menu": {"type":"string"},
                "reason": {"type":"string"} } },
  "constraints": { "allow_write": false, "allow_net": true, "allow_exec": false },
  "examples": [{ "input": {"date":"2026-08-04"}, "expect_hint": "雨天推荐热汤类" }],
  "test_focus": "推荐要合时令;必须给出理由"
}
```

4. 用户在卡片上**逐项编辑/确认**(每项可改,确认键在卡片底部)——确认前不写任何草稿;
5. 确认 → 进入首稿生成。

### 2.2 访谈收敛判据

- 必填:goal / entry(命名合法)/ inputs / outputs;
- 访谈轮数软上限 6 轮,超出后 chatbot 必须给出当前最佳简报请用户直接改;
- 用户可随时说"直接给我简报"跳过对话。

## 3. 阶段二:首稿(Scaffold)

确认 brief 后,`skill.dev.assistant`(包级化版本,P3 已完成)按 brief 一次生成:

| 产物 | 内容 | 依据 |
|---|---|---|
| entry skill | manifest(name/description 路由式/inputs/outputs/permissions/trust)+ prompt 初稿 | brief |
| 支撑 skills | 分解出的子技能(复用优先:先查注册表已有技能,命中即外链不新建) | P3 包级能力 |
| **测试套件** | `tests/*.json`:≥3 个 I/O 用例(正常/边界/反例),每个带 `expect`(精确断言,可选)与 `rubric`(评审标准,LLM judge 用) | brief.examples + test_focus |
| **评审标准 rubric** | 用例级自然语言评分点(如"推荐包含当季食材 1分/给出理由 1分/无危险建议 1分",满分与及格线) | brief.test_focus |
| **开发笔记** | `NOTES.md`:分解理由、已知薄弱点、建议的下一轮改进方向 | 助手输出 |

首稿生成后:**自动跑一轮测试套件**(含 judge 评分),分数随首稿呈现——
用户的第一眼就是"它现在 62 分,这三个用例挂了"。

首稿落为 working 草稿 + 快照为 `versions/v001`(source: "scaffold")。

## 4. 阶段三:批注式迭代(Iterate)

### 4.1 三栏布局(迭代模式)

```
┌──────────────────────── #/lab/<pkg>(迭代模式)────────────────────────┐
│ 顶栏:包名 · 版本下拉(v003 ▾ · rewind) · tier 徽标 · [测试全部]      │
├──────────────────────┬──────────────────────┬───────────────────────┤
│ 左:既有版本 v003      │ 中:批注与生成          │ 右:候选 v004(未接受)  │
│ (只读渲染,可批注)     │ ┌──────────────────┐ │ ┌───────────────────┐ │
│ ┌─ entry: dinner.planner│ 批注列表(3):    │ │ │ diff 摘要:        │ │
│ │ description  …[💬]  │ │ ① description:   │ │ │ prompt 重写 60%   │ │
│ │ prompt:        [💬] │ │   "太啰嗦"       │ │ │ inputs +dry_run   │ │
│ │  "你是晚餐…"  [💬]  │ │ ② case2:         │ │ │ tests +2 用例     │ │
│ │   选中段落→右键批注   │ │   "加雨天反例"   │ │ ├───────────────────┤ │
│ ├─ tests/             │ │ ③ trust:         │ │ │ 测试结果:         │ │
│ │  case1 ✓ 92分 [💬]  │ │   "不该是 L2"    │ │ │ case1 ✓ 95 (↑3)  │ │
│ │  case2 ✗ 41分 [💬]  │ ├──────────────────┤ │ │ case2 ✓ 78 (↑37) │ │
│ │  case3 ✓ 88分 [💬]  │ │ [补充说明…]      │ │ │ case3 ✓ 90 (↑2)  │ │
│ ├─ weather.query(外链) │ │                  │ │ │ 聚合:88 (↑17)   │ │
│                      │ │   【 ✨ 生成 】    │ │ ├───────────────────┤ │
│ [▶ 手动运行] [🧪 跑测试]│ └──────────────────┘ │ │ [接受 ✓] [重生成] │ │
│                      │                      │ └───────────────────┘ │
└──────────────────────┴──────────────────────┴───────────────────────┘
```

### 4.2 批注(commenting)

- 左栏渲染既有版本的**结构化视图**(不是 raw YAML):成员分节(manifest 字段 / prompt 段落 / permissions / trust / tests 用例);
- **每个可批注单元带锚点**:字段级(如 `description`)、prompt 段落级(选中文本后右键"添加批注",锚 = {member, field, span:{start,end}})、用例级、成员级;
- 批注写进当前轮 `comments/<round>.json`,中栏列表可改可删;
- 批注是**意见通道**,不是编辑动作——左栏永远不被批注直接改变(版本不可变)。

### 4.3 生成(Generate)

- 点"生成"→ 助手收到:`brief + 当前版本全量 + 本轮批注(+ 可选补充说明)`;
- 助手在 **candidate 区**产出新版本(工具面:`lab.draft.read` + `lab.pkg.closure` + candidate 写入工具;**写不到 working,更写不到生产**——信任边界物理化);
- 右栏渲染 diff 摘要(成员级:哪个成员的哪个字段变了)+ 完整候选内容;
- 生成失败(LLM/校验错):右栏显示错误与原始批注,不丢批注,可重试。

### 4.4 验证(双侧运行)

- **跑测试**:左右各自可跑测试套件;右栏默认在生成完成后自动跑一轮;
- 结果并排:每用例 左分 → 右分(Δ),聚合分对比;judge 的扣分理由可展开;
- **手动运行**:两侧各自输入自定义 input 跑一次(对比同一输入的产出差异);
- judge 成本护栏:批量评分按用例逐个调用,总量 ≤ 用例数;聚合失败(超预算)时降级为"无 judge 分,仅精确断言结果"。

### 4.5 接受 / 重生成 / 回溯

- **接受 ✓**:candidate 快照为 `versions/vNNN+1`(source: "iterate", parent: vNNN, comments_digest 随档)→ working 覆盖为 candidate 内容 → 进入下一轮(批注清空,round+1);
- **重生成**:候选废弃(candidate 区清空,版本不产生),批注保留可改;
- **rewind**:版本下拉选任意 vNNN → 恢复为 working(历史不动;此后若接受新候选,parent 记为 vNNN,历史成 DAG 而非线);
- **发布**:任何时候点"提交",对当前 working 走 P2 的 plan/原子提交流程(版本与生产版本的对应关系记进 promotions.jsonl)。

## 5. 数据模型与存储

```
drafts/<pkg>/
├── manifest.yaml / prompt.md / handler.py   # working(现状,可手改)
├── tests/*.json                             # working 测试套件(扩展 rubric 字段)
├── brief.json                               # 需求简报(§2.1)
├── NOTES.md                                 # 开发笔记(首稿/接受时可更新)
├── comments/<round>.json                    # 每轮批注 [{id, anchor, text, at}]
│   # anchor: {member, kind: field|span|config|case, path, span?: {start,end}}
├── candidate/                               # 生成暂存(接受前)
│   └── <member>/{manifest.yaml,prompt.md,...}
└── versions/
    ├── v001/  # 全成员快照 + meta.json {source, parent, at, comments_digest}
    ├── v002/
    └── current.json  # {version: "v003"}(rewind 导航;working 以文件为准)
```

测试用例扩展(向后兼容,旧字段不变):

```jsonc
// tests/case2.json
{ "input": {"date": "2026-08-04"},
  "expect": { "outputs_contains": ["热汤"] },        // 可选:精确断言(现状)
  "mock_script": [],                                 // 可选(现状)
  "rubric": {
    "criteria": ["推荐合时令 1分", "给出理由 1分", "无不安全建议 1分"],
    "pass_score": 2 } }
```

## 6. 后端改动

| # | 模块 | 改动 |
|---|---|---|
| B1 | `skills/draft_store.py` | versions/candidate/comments/brief 的读写与快照(accept/rewind/scaffold);DraftStore API:`snapshot(source,parent)`、`restore(v)`、`write_candidate`、`clear_candidate`、comments CRUD |
| B2 | `skills/lab_assistant.py` + `skills/interviewer.py`(新) | interviewer meta-skill(访谈 prompt 专门化 + brief schema 校验);assistant 生成支持 candidate 区与 brief 上下文 |
| B3 | `tools/lab_tools.py` | 候选写入工具(`lab.cand.write`,L2;**只能写 candidate/**);信任边界:assistant 仍无 working 直写(生成路径),无 promote/delete |
| B4 | `skills/judge.py`(新) | LLM-as-judge:rubric 渲染 → provider 调用 → {score, pass, rationale};批量 + 聚合 + 预算护栏;provider 复用 ProviderManager(不动内核) |
| B5 | `host/web/app.py` API | `POST /lab/<pkg>/brief`(存简报)、`POST /lab/<pkg>/scaffold`(首稿)、`POST /lab/<pkg>/iterate`(生成候选)、`POST /lab/<pkg>/accept`、`POST /lab/<pkg>/rewind {version}`、`GET /lab/<pkg>/versions`、`POST /lab/<pkg>/judge {target, case?}`、`POST /lab/<pkg>/run {target, input}`(手动运行) |
| B6 | `skills/gate.py` | G4 可选消费 judge 聚合分(作为 G4 的增强信息项,不改变现有判定) |
| B7 | diff | 成员级字段 diff(working vs candidate)纯函数(供右栏摘要) |

## 7. 前端改动

| # | 组件 | 改动 |
|---|---|---|
| F1 | `lab.js` 模式切换 | 专家模式(现状三栏)与**迭代模式**(本 spec 三栏)按草稿有无 brief 自动分流;新建入口加"引导模式" |
| F2 | 访谈面板 | 中栏 chat + 简报卡片(逐项可编辑 + 确认按钮;复用 gateCards 的卡片语言) |
| F3 | 左栏版本视图 | 结构化只读渲染 + 批注锚点(字段 💬 / prompt 段落选择右键菜单 / 用例行 💬) |
| F4 | 中栏批注面板 | 批注列表(增删改)+ 补充说明框 + 生成按钮(生成中态/失败重试) |
| F5 | 右栏候选视图 | diff 摘要 + 候选内容 + 双侧测试分数表(左→右 Δ)+ 接受/重生成 |
| F6 | 版本下拉 | versions 列表(source/at/聚合分) + rewind 确认 |
| F7 | copy | 六主题同步全部新 key(契约测试强制) |

## 8. 状态机

```
IDLE ──新建引导──▶ INTERVIEW ──简报确认──▶ SCAFFOLDING ──首稿+首测──▶ ITERATING
ITERATING ──批注就绪点生成──▶ GENERATING ──成功──▶ REVIEW
REVIEW ──接受──▶ ITERATING(v+1)   REVIEW ──重生成──▶ GENERATING
REVIEW ──再批注──▶ ITERATING      ITERATING ──rewind──▶ ITERATING(working 恢复)
任意状态 ──提交──▶(P2 plan/promote 流程,不变)
异常面:scaffold/generate 失败 → 停留原状态 + 错误卡(批注/brief 不丢)
```

## 9. 边界与局限(诚实清单)

- **LLM-as-judge 不替代硬断言**:judge 有方差、有成本、可被漂亮话骗;它的定位是"方向性评分",精确断言(expect/outputs schema)仍是判定主力;G4 只把 judge 分当参考信息项;
- **首稿质量无承诺**:scaffold 是起点不是成品——所以首测分数必须在第一屏呈现,管理预期;
- **版本体积**:versions 全量快照,大 prompt × 多版本会涨;v1 不做压缩(草稿量级小),>20 版本 warn;
- **judge/生成都要真 LLM**:凭证过期时(当前部署约束)迭代模式整体降级为专家模式 + 明确提示;
- **不做多人协作批注**(单用户);
- **不做 brief 自动演化**(需求变更回访谈,不让批注偷渡需求);
- **rewind 不做分支合并**(DAG 仅记录 parent,不合并)。

## 10. 分期

| 期 | 内容 | 验收 |
|---|---|---|
| V1 | brief 访谈(interviewer + 简报卡片)+ scaffold 首稿(含测试套件与 NOTES)+ v001 快照 | 一句需求 → 可跑的包 + 首测分数 |
| V2 | versions/candidate/comments 存储 + 三栏迭代(批注/生成/diff)+ 接受与重生成 | 一轮完整批注迭代 |
| V3 | judge.py + 双侧分数对比 + 预算护栏 + G4 信息项 | rubric 评分并排可读 |
| V4 | rewind/版本下拉/DAG parent + 版本体积告警 + 引导/专家模式分流打磨 | 全流程走查 |

依赖:V1 依赖 P3 助手(已有);V2 是主体;V3 可提前(独立模块);V4 收尾。
