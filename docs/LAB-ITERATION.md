# Lab 迭代工作流(Iterative Lab)设计 spec

> 版本:v0.2(spec) · 前版:v0.1
> 对象:Skill Lab 的引导式开发模式——访谈初始化 → 首稿 → 批注式迭代 → 版本回溯
> 关系:构建在 `SKILL-DEV.md`(L1-L5)与 `SKILL-PACKAGES.md` / `SKILL-PACKAGES-V2.md`
>   (P1-P4 + P1.5)之上;闸门/原子提交不变,本设计是它们的**上游**(产出物最终仍走
>   plan/promote 进生产)。助手信任边界沿用 V2 §6.7(能改能建,不能发不能删)。
> 一句话:**访谈定义"做什么",批注驱动"怎么改",版本承载"改到哪"**。
>
> **v0.2 改动**(评审后,三条结构性 + 四条收口):
> ① judge 从宿主模块改为**技能**(`skill.dev.judge`)走内核——否则会开出内核之外
>    的第二条 LLM 路径,绕过信号总线/BudgetGuard/replay(§4.4、§6 B4);
> ② 包级状态从 `drafts/<pkg>/` 移到 **`drafts/.packages/<root>/`**——原方案与
>    "包 = 闭包(推导不声明)"冲突,且 `delete()` 会连版本史一起炸(§5、§6 B1);
> ③ 复用优先补上工具面(助手白名单加 `system.skill.search`),否则 scaffold
>    只能新建、不可能复用(§3、§6 B2);
> ④ candidate 补围栏 + accept 二次校验(§4.5、§6 B3);⑤ rubric 与
>    `manifest_hash` 的证据过期面(§6 B6);⑥ 版本/working 漂移用脏指示(§4.5);
> ⑦ judge 默认不自动跑(成本,§4.4)。

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

**术语约定**:`<root>` = 包的入口技能名(包 = `closure(root)`,推导不声明,
V2 §6.1)。包级状态一律挂在 `drafts/.packages/<root>/` 下,**不挂在任何成员的
草稿目录里**(理由见 §5)。

| 实体 | 定义 | 存储 |
|---|---|---|
| **需求简报 brief** | 访谈产物:目标/输入/输出/约束/示例/tier 提示;用户确认后生效 | `drafts/.packages/<root>/brief.json` |
| **版本 version** | 包的不可变快照(全成员 manifest+prompt+handler+tests);每次"接受"产生新版本 | `drafts/.packages/<root>/versions/vNNN/` |
| **工作副本 working** | 现有草稿布局:**每成员一个平级目录**(可手改;既有 Lab 行为) | `drafts/<member>/`(现状,每成员一份) |
| **候选 candidate** | 生成产物(未接受前不影响 working) | `drafts/.packages/<root>/candidate/<member>/` |
| **批注 comment** | 锚定到成员/字段/段落/用例的意见 | `drafts/.packages/<root>/comments/<round>.json` |
| **评审 rubric** | LLM-as-judge 评分标准(随测试用例) | `drafts/<member>/tests/*.json` 扩展字段 |

不变量:

- **版本不可变**:接受即封存,rewind = 恢复副本到 working,不改历史。
  注意这是**本地开发史**的不可变,与 V2 §6.9 讲的"**已发布产物**不可变 /
  依赖静默变更要重新确认"是两件事——后者针对的是上游依赖换版本导致下游推导档
  静默抬高,与本设计无关,不要混用 GlassWorm 那条论据;
- **接受是人的动作**:助手可以生成 candidate,永远不能替用户点接受(与"能改不能发"同根);
- **brief 是生成上下文的锚**:每次生成都带 brief,防需求漂移——改需求 = 回到访谈更新 brief,不在批注里偷渡;
- **promote 路径不变**:任何版本要进生产,仍走 P2 的 plan/五关/原子提交(本设计不产生第二条发布通道);
- **不新开 LLM 通道**:访谈、生成、评分**全部是技能**,一律经内核 run
  (信号/预算/trace/replay 白拿)。宿主侧不直接调 ProviderManager(§4.4)。

## 2. 阶段一:初始化访谈(Interview)

### 2.1 流程

1. 用户在 Lab 点"新建(引导模式)",中栏变为访谈 chat;
2. Chatbot(`skill.dev.interviewer`,新 meta-skill,prompt 专门化)通过多轮对话采集:目标(一句话功能)、输入/输出示例、约束(能不能写文件/联网/执行)、质量偏好(简洁 vs 详尽)、测试倾向(有没有现成例子);
3. 访谈收敛后,chatbot 输出**结构化简报卡片**(非自由文本):

```jsonc
// drafts/.packages/<root>/brief.json
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
| 支撑 skills | 分解出的子技能(**复用优先**:先搜生产注册表,命中即外链不新建) | P3 包级能力 + `system.skill.search` |
| **测试套件** | `tests/*.json`:≥3 个 I/O 用例(正常/边界/反例),每个带 `expect`(精确断言,可选)与 `rubric`(评审标准,LLM judge 用) | brief.examples + test_focus |
| **评审标准 rubric** | 用例级自然语言评分点(如"推荐包含当季食材 1分/给出理由 1分/无危险建议 1分",满分与及格线) | brief.test_focus |
| **开发笔记** | `NOTES.md`:分解理由、已知薄弱点、建议的下一轮改进方向 | 助手输出 |

### 3.1 复用优先需要一件助手现在没有的工具

包思路真正的复利是"**用户的新需求可能大部分已被现有 skill 拼出来**"
(V2 §4 方案 D)。但助手当前白名单恰好是 `LAB_DRAFT_TOOLS` 七件
(`tools/lab_tools.py`:read / write / list / validate / test_run / create /
`lab.pkg.closure`),**没有一件能搜生产注册表**——`lab.draft.list` 列的是草稿,
`lab.pkg.closure` 读的是当前包闭包。

因此 V1 必须做二选一,不能默认它会自己发生:

- **(推荐)** 把已有的 `system.skill.search` 加进助手白名单。它是 READ/none,
  **不推高推导档**(助手仍是 L2),围栏面不变;
- 或者明确写下"V1 不做复用优先,scaffold 一律新建",在 §9 里认账。

### 3.2 测试套件的粒度

**用例挂成员,不挂包**:`drafts/<member>/tests/*.json`(沿用现状布局)。
包级"跑测试"= 以 entry 为入口跑 entry 的用例;成员级用例可单独跑,供拆解
子技能后的定点验证。左栏按成员分节渲染各自的用例(§4.1)。

理由:用例要跟着被测对象走,成员被 promote 出去之后用例仍然有效;挂在包上
则一旦成员脱离这个包就孤儿了。代价是"整包端到端"的覆盖率要靠 entry 用例
撑,V2 §6.6 的包级覆盖率(未实现,P5)是它的补充而不是替代。

首稿生成后:**自动跑一轮精确断言**(`expect` / outputs schema),分数随首稿
呈现——用户的第一眼就是"这三个用例挂了"。**judge 评分默认不自动跑**
(它要花真钱,§4.4),由用户点"评分"触发;首屏给出预估调用数。

首稿落为 working 草稿(每成员一个目录)+ 快照为
`.packages/<root>/versions/v001`(source: "scaffold")。

## 4. 阶段三:批注式迭代(Iterate)

### 4.1 三栏布局(迭代模式)

```
┌─────────────────────── #/lab/pkg/<root>(迭代模式)──────────────────────┐
│ 顶栏:包名 · 版本下拉(v003* ▾ · rewind) · tier 徽标 · [跑测试][评分 6次]│
│        ↑ 星号 = working 已在 v003 基础上被改(package_hash 比对,§4.5)  │
├──────────────────────┬──────────────────────┬───────────────────────┤
│ 左:既有版本 v003      │ 中:批注与生成          │ 右:候选 v004(未接受)  │
│ (只读渲染,可批注)     │ ┌──────────────────┐ │ ┌───────────────────┐ │
│ ┌─ entry: dinner.planner│ 批注列表(3):    │ │ │ diff 摘要:        │ │
│ │ description  …[💬]  │ │ ① description:   │ │ │ ＋成员 dinner.veto │ │
│ │ prompt:        [💬] │ │   "太啰嗦"       │ │ │ prompt 重写 60%   │ │
│ │  "你是晚餐…"  [💬]  │ │ ② case2:         │ │ │ inputs +dry_run   │ │
│ │   选中段落→右键批注   │ │   "加雨天反例"   │ │ │ tests +2 用例     │ │
│ ├─ tests/(挂成员)     │ │ ③ trust:         │ │ ├───────────────────┤ │
│ │  case1 ✓  [💬]      │ │   "不该是 L2"    │ │ │ 断言:case1✓case2✓ │ │
│ │  case2 ✗  [💬]      │ ├──────────────────┤ │ │ 评分(点了才有):   │ │
│ │  case3 ✓  [💬]      │ │ [补充说明…]      │ │ │ case2 41→78(↑37) │ │
│ ├─ weather.query(外链) │ │                  │ │ │ 聚合 71→88(↑17)  │ │
│                      │ │   【 ✨ 生成 】    │ │ ├───────────────────┤ │
│ [▶ 手动运行] [🧪 跑测试]│ └──────────────────┘ │ │ [接受 ✓] [重生成] │ │
│ [⚖ 评分(3次调用)]    │                      │ └───────────────────┘ │
└──────────────────────┴──────────────────────┴───────────────────────┘
```

图上三处与 v0.1 不同,都对应本版修正:**① `v003*` 脏标**(§4.5);
**② 评分与跑测试分开**、按钮上标预估调用数(§4.4,judge 花真钱);
**③ diff 摘要顶部显式列出成员增删**(§4.3,否则"接受"这个授权无从行使)。

### 4.2 批注(commenting)

- 左栏渲染既有版本的**结构化视图**(不是 raw YAML):成员分节(manifest 字段 / prompt 段落 / permissions / trust / tests 用例);
- **每个可批注单元带锚点**:字段级(如 `description`)、prompt 段落级(选中文本后右键"添加批注",锚 = {member, field, span:{start,end}})、用例级、成员级;
- 批注写进当前轮 `comments/<round>.json`,中栏列表可改可删;
- 批注是**意见通道**,不是编辑动作——左栏永远不被批注直接改变(版本不可变)。

### 4.3 生成(Generate)

- 点"生成"→ 助手收到:`brief + 当前版本全量 + 本轮批注(+ 可选补充说明)`;
- 助手在 **candidate 区**产出新版本(工具面:`lab.draft.read` + `lab.pkg.closure` + `lab.cand.write`;**写不到 working,更写不到生产**——信任边界物理化);
- 右栏渲染 diff 摘要(成员级:哪个成员的哪个字段变了)+ 完整候选内容;
- 生成失败(LLM/校验错):右栏显示错误与原始批注,不丢批注,可重试。

**candidate 不能成为围栏缺口。** P3 给助手立了两道围栏(`lab.draft.create` 限
包命名空间前缀、`lab.draft.write` 限编辑闭包,`tools/lab_tools.py`)。
`lab.cand.write` 若只约束"写在 candidate/ 下",助手就能往候选里塞一个**包外
成员**,而用户看的是 diff 摘要、未必注意到多出一个成员,一点接受就落进 working。
所以两处都要卡:

1. `lab.cand.write` **复用同一套围栏**——成员必须 ∈ 编辑闭包 ∪ 本包命名空间
   内的新建成员,越界即拒;
2. **accept 时再校验一次**(围栏是助手侧的,accept 是人侧的最后一道)——
   候选成员集越界则拒绝接受并指名越界成员。

"人点接受"是授权,不是审计豁免:diff 摘要必须**显式列出新增/删除的成员**
(而不只是字段级改动),否则人无从行使这个授权。

### 4.4 验证(双侧运行)

- **跑测试**(精确断言):左右各自可跑;右栏在生成完成后自动跑一轮——
  这一档**不花 LLM 钱**(走现有 test-run 装配线,`expect` / outputs schema 判定);
- **评分**(judge):**默认不自动跑**,按钮上标出预估调用数(= 用例数 × 侧数);
  结果并排:每用例 左分 → 右分(Δ),聚合分对比,judge 的扣分理由可展开;
- **手动运行**:两侧各自输入自定义 input 跑一次(对比同一输入的产出差异)。

#### judge 是一个技能,不是宿主模块(v0.2 结构性修正)

v0.1 把 judge 写成 `skills/judge.py`,由宿主直接调 ProviderManager,并自带一套
成本护栏。**这会开出内核之外的第二条 LLM 路径**,绕过三样东西:

| 绕过 | 后果 |
|---|---|
| 信号总线(`pre/post:llm.request`) | judge 的调用不进 `trace.jsonl`,事后无法归因 |
| BudgetGuard sidecar | 才不得不自制"judge 成本护栏"——重复造轮子 |
| replay(`build_mock_script` 从 trace 重建) | 含 judge 的 run 重放不出来 |

这与"内核是唯一仲裁点、信号总线是唯一公共通道"直接冲突。当前全仓**没有**
内核外的 provider 调用(上下文压缩用 ProviderManager,但 context 本身就是内核
子系统),judge 会是第一个,不值得为它破例。

**改为**:`skill.dev.judge` —— 一个普通 prompt 技能,权限面为空(推导档
`none`),经 overlay 的 `extra` 层注入(与 `skill.dev.assistant` /
`skill.dev.interviewer` 同一条路)。输入 `{case, rubric, actual_output}`,
输出 `{score, pass, rationale}`。收益全部白拿:

- 调用自动进 trace,可归因、可回放;
- 预算由 BudgetGuard 统一管——§4.4 原来那套自制护栏**整块删掉**;
- 又一次 dogfood(judge 自己就是一个 skill,能被 Lab 自己迭代)。

落地成本比写 `judge.py` 更低:P1.5 已把 `extra` 槽重构成 `StaticSkillRegistry`
一层,往链上加第二、第三个 meta-skill 是一行的事。

聚合与降级:批量评分逐用例调用,总量 = 用例数;预算中止(BudgetGuard 判决)
时降级为"无 judge 分,仅精确断言结果",并在 UI 显示是被预算截断而非评分为 0。

### 4.5 接受 / 重生成 / 回溯

- **接受 ✓**:先过成员集越界校验(§4.3)→ candidate 快照为 `versions/vNNN+1`
  (source: "iterate", parent: vNNN, comments_digest 随档)→ working 覆盖为
  candidate 内容(**逐成员写各自的 `drafts/<member>/`**)→ 进入下一轮
  (批注清空,round+1);
- **重生成**:候选废弃(candidate 区清空,版本不产生),批注保留可改;
- **再批注**(不接受也不重生成,回左栏继续加批注):**candidate 保留但标记为
  "已过期"**,下次生成整体覆盖;不自动废弃是因为用户可能只是想对比着看;
- **rewind**:版本下拉选任意 vNNN → 恢复为 working(历史不动;此后若接受新候选,parent 记为 vNNN,历史成 DAG 而非线);
- **发布**:任何时候点"提交",对当前 working 走 P2 的 plan/原子提交流程(版本与生产版本的对应关系记进 promotions.jsonl)。

**版本指针与 working 会漂移,必须显式标脏。** `current.json` 记的是"上次
rewind/accept 到哪个版本",但 working 是文件、随时可手改(专家模式、直接编辑
文件都会改)。rewind 到 v003 后手改一行,下拉仍显示 v003 就是在说谎。

处理:版本下拉在 working 与 `current.json` 指向的版本内容不一致时显示 `v003*`
(脏标),悬浮说明"working 已在 v003 基础上被修改"。判据复用包哈希
(V2 §6.3 的 `package_hash`:全成员 `manifest_hash` 排序后取哈希)——
与版本快照的 `package_hash` 比对即可,不需要新机制。UI 上复用 Lab 已有的
脏指示器(commit `a360b89` F6 脏提交陷阱那套)。

## 5. 数据模型与存储

### 5.1 为什么包级状态不能挂在 entry 的草稿目录下(v0.2 结构性修正)

v0.1 把 `versions/` / `candidate/` / `comments/` / `brief.json` 画在
`drafts/<pkg>/` 里,并把 working 也画在同一层。这与现状和包模型都冲突:

- **`DraftStore` 是"一个草稿一个目录"**(`_dir()` = `root/<name>`;`list()` 要求
  目录内有 `manifest.yaml`)。**包是推导出来的闭包,不是目录**——一个包的成员是
  `drafts/dinner.planner/`、`drafts/weather.query/` 这样的**平级兄弟目录**。
  所以 v0.1 树里那行 `manifest.yaml` 只是 entry 自己的,其余成员的 working
  根本不在 `drafts/<pkg>/` 下,而 `versions/`(全成员快照)却是包级的
  ——**同一棵树里混了两种粒度**。
- **`delete()` 会连版本史一起炸**:它是 `shutil.rmtree(整个目录)`。删掉 entry
  草稿 = 删掉整个包的全部版本历史,其余成员则变成孤儿留在原地。叠加
  V2 §6.11(drafts 缺省落 `.agent-os/drafts`、被 gitignore、无历史),
  **这是不可恢复的**。
- entry 改名或换根,历史直接孤儿。

**改为 `drafts/.packages/<root>/`。** 它有一个正好的性质:草稿名正则是
`^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$`,**以点开头的 `.packages` 永远不可能
与草稿名相撞**;而 `list()` 只收含 `manifest.yaml` 的目录,所以它天然不会出现
在草稿列表里。删单个成员草稿不再影响包历史。

### 5.2 布局

```
drafts/
├── <member>/                                # working:每成员一个平级目录(现状)
│   ├── manifest.yaml / prompt.md / handler.py
│   ├── tests/*.json                         # 用例挂成员(§3.2;扩展 rubric 字段)
│   └── gate/                                # 闸门报告与 promotions(现状)
└── .packages/<root>/                        # 包级状态(点开头,不与草稿名相撞)
    ├── brief.json                           # 需求简报(§2.1)
    ├── NOTES.md                             # 开发笔记(首稿/接受时可更新)
    ├── comments/<round>.json                # 每轮批注 [{id, anchor, text, at}]
    │   # anchor: {member, kind: field|span|config|case, path, span?: {start,end}}
    ├── candidate/<member>/{manifest.yaml,prompt.md,tests/,...}   # 生成暂存
    └── versions/
        ├── v001/<member>/…  + meta.json {source, parent, at,
        │                                  comments_digest, package_hash}
        ├── v002/…
        └── current.json  # {version: "v003"}(rewind 导航;
                          #  working 以文件为准,不一致时 UI 标脏,§4.5)
```

`meta.json` 里存 `package_hash`(V2 §6.3 同一算法)是为了 §4.5 的脏判定,
以及"这个版本对应哪次 promote"的对账。

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

> **rubric 落在 `tests/` 会踩到一个已知的诚实缺口。** `manifest_hash` 只覆盖
> manifest+prompt+handler,**不含 `tests/`**(白皮书第 14 章 §6 局限 3 明写:
> 可以带着过期的冒烟证据 promote)。把评分标准也放进 `tests/`,意味着**连判分
> 依据都能在闸门报告出具之后被改而报告不失效**。
>
> 两个出路,V1 至少选一个:① 把 `tests/` 纳入 `manifest_hash`——顺手把那条老
> 缺口一并关掉(推荐,改动小,且让 G4 证据真正绑定内容);② judge 分**完全不
> 进闸门报告**,只作 UI 信息。§6 B6 按 ① 写。

## 6. 后端改动

| # | 模块 | 改动 |
|---|---|---|
| B1 | `skills/draft_store.py` | **包级状态存储**(`drafts/.packages/<root>/`,§5.1):`snapshot(root, source, parent)`、`restore(root, v)`、`write_candidate` / `clear_candidate`、comments CRUD、brief 读写。快照/恢复按**成员集**遍历(每成员各自的 `drafts/<member>/`),不是拷一个目录。`list()` 不受影响(`.packages` 无 `manifest.yaml`,天然不入列) |
| B2 | `skills/lab_assistant.py` + `skills/interviewer.py`(新) | interviewer meta-skill(访谈 prompt 专门化 + brief schema 校验);assistant 生成支持 candidate 区与 brief 上下文;**白名单加 `system.skill.search`**(§3.1 复用优先;READ/none,不推高推导档) |
| B3 | `tools/lab_tools.py` | `lab.cand.write`(L2,只能写 `candidate/`)**并复用 P3 的围栏**:成员必须 ∈ 编辑闭包 ∪ 本包命名空间新建(§4.3);信任边界不变:无 working 直写、无 promote/delete |
| B4 | `skills/lab_judge.py`(meta-skill 定义,非 provider 调用层) | **judge 是技能不是宿主模块**(§4.4):`skill.dev.judge`,prompt kind,权限面空(推导档 none),经 overlay `extra` 层注入(与 assistant/interviewer 同路)。输入 `{case, rubric, actual_output}` → 输出 `{score, pass, rationale}`。**不写自制预算护栏**——预算归 BudgetGuard,调用自动进 trace/可 replay |
| B5 | `host/web/app.py` API | `POST /lab/pkg/<root>/brief`、`.../scaffold`、`.../iterate`(生成候选)、`.../accept`(含成员集越界校验)、`.../rewind {version}`、`GET /lab/pkg/<root>/versions`、`POST /lab/pkg/<root>/judge {target, case?}`(起 judge run)、`POST /lab/pkg/<root>/run {target, input}`(手动运行)。路径用 `/lab/pkg/<root>/` 前缀,与现状 `/api/lab/<draft>/*` 的单草稿面分开,避免 `<draft>` 通配吃掉包路由 |
| B6 | `skills/gate.py` | **`manifest_hash` 纳入 `tests/`**(§5 注:关掉"冒烟证据可过期"的老缺口,也让 rubric 变更能作废旧报告);G4 可选消费 judge 聚合分作为信息项,**不改变现有判定** |
| B7 | diff | 成员级字段 diff(working vs candidate)纯函数(供右栏摘要);**必须显式给出新增/删除的成员**(§4.3:否则人无从行使"接受"这个授权) |

## 7. 前端改动

| # | 组件 | 改动 |
|---|---|---|
| F1 | `lab.js` 模式切换 | 专家模式(现状三栏)与**迭代模式**(本 spec 三栏)按草稿有无 brief 自动分流;新建入口加"引导模式" |
| F2 | 访谈面板 | 中栏 chat + 简报卡片(逐项可编辑 + 确认按钮;复用 gateCards 的卡片语言) |
| F3 | 左栏版本视图 | 结构化只读渲染 + 批注锚点(字段 💬 / prompt 段落选择右键菜单 / 用例行 💬) |
| F4 | 中栏批注面板 | 批注列表(增删改)+ 补充说明框 + 生成按钮(生成中态/失败重试) |
| F5 | 右栏候选视图 | diff 摘要(**成员增删置顶**,§4.3)+ 候选内容 + 双侧结果表(断言 / 评分分列,左→右 Δ)+ 接受/重生成;accept 被越界校验拒绝时的错误态 |
| F6 | 版本下拉 | versions 列表(source/at)+ rewind 确认 + **脏标 `vNNN*`**(package_hash 比对,复用现有脏指示器) |
| F7 | 评分按钮 | 与"跑测试"分开;按钮上显示预估调用数;预算中止时显示"被预算截断"而非 0 分(§4.4) |
| F8 | copy | 六主题同步全部新 key(契约测试强制) |

## 8. 状态机

```
IDLE ──新建引导──▶ INTERVIEW ──简报确认──▶ SCAFFOLDING ──首稿+首测──▶ ITERATING
ITERATING ──批注就绪点生成──▶ GENERATING ──成功──▶ REVIEW
REVIEW ──接受(过越界校验)──▶ ITERATING(v+1)   REVIEW ──重生成──▶ GENERATING
REVIEW ──再批注──▶ ITERATING(candidate 保留并标"已过期",§4.5)
ITERATING ──rewind──▶ ITERATING(working 恢复;working 被手改则版本下拉标脏)
任意状态 ──提交──▶(P2 plan/promote 流程,不变)
异常面:scaffold/generate 失败 → 停留原状态 + 错误卡(批注/brief 不丢);
        accept 越界校验失败 → 停留 REVIEW + 指名越界成员(候选不丢)
```

judge 不是状态机上的一环:它是 ITERATING/REVIEW 里由用户按需触发的一次
**普通 kernel run**(§4.4),失败或被预算中止都不改变主状态。

## 9. 边界与局限(诚实清单)

- **LLM-as-judge 不替代硬断言**:judge 有方差、有成本、可被漂亮话骗;它的定位是"方向性评分",精确断言(expect/outputs schema)仍是判定主力;G4 只把 judge 分当参考信息项;
- **首稿质量无承诺**:scaffold 是起点不是成品——所以首测结果必须在第一屏呈现,管理预期;
- **版本体积**:versions 全量快照,大 prompt × 多版本会涨;v1 不做压缩(草稿量级小),>20 版本 warn;
- **judge/生成都要真 LLM**:凭证过期时(当前部署约束)迭代模式整体降级为专家模式 + 明确提示;
- **草稿层本身没有历史**(V2 §6.11):`.packages/<root>/versions/` 建立在
  `drafts/` 之上,而 `drafts/` 缺省落 `.agent-os/drafts` 且被 gitignore。
  §5.1 的改动只解决了"删成员不炸包史",**没有解决"整个 drafts 丢了就全丢"**
  ——那要么把 `drafts_root` 指到进 git 的路径(配置层决定),要么等 §6.11 的
  待决项。本设计不替用户定;
- **judge 分不是可复现的判据**:即便走内核(可 replay),judge 本身有方差,
  同一候选两次评分可能不同分——版本 meta 里记的是"当时那次评分",不承诺可重算;
- **越界校验只覆盖成员集,不覆盖内容**:accept 校验的是"有没有多出包外成员",
  不判断成员内容好坏——那是闸门和人的事;
- **不做多人协作批注**(单用户);
- **不做 brief 自动演化**(需求变更回访谈,不让批注偷渡需求);
- **rewind 不做分支合并**(DAG 仅记录 parent,不合并)。

## 10. 分期

| 期 | 内容 | 验收 |
|---|---|---|
| **V0** | 地基三件(都很小,但后面全依赖):① `.packages/<root>/` 包级存储 API(B1);② 助手白名单加 `system.skill.search`(B2);③ `manifest_hash` 纳入 `tests/`(B6) | 包级快照/恢复往返一致;助手能搜到生产技能;改 `tests/` 后旧闸门报告即失效 |
| V1 | brief 访谈(interviewer + 简报卡片)+ scaffold 首稿(含测试套件与 NOTES)+ v001 快照 | 一句需求 → 可跑的包 + 首测(精确断言)结果 |
| V2 | candidate/comments + 三栏迭代(批注/生成/diff)+ `lab.cand.write` 围栏 + accept 越界校验 + 接受/重生成 | 一轮完整批注迭代;助手写不出包外成员,accept 拒绝越界候选 |
| V3 | `skill.dev.judge`(meta-skill,走内核)+ 双侧分数对比 + G4 信息项 | rubric 评分并排可读;judge 调用出现在 trace 里、受 BudgetGuard 管 |
| V4 | rewind/版本下拉/DAG parent + 脏标(package_hash 比对)+ 版本体积告警 + 引导/专家模式分流打磨 | 全流程走查;手改 working 后下拉显示 `vNNN*` |

依赖:**V0 是新增的地基期**——它三件事互相独立、都不大,但 V1/V2/V3 分别依赖
其中一件,先做能避免返工;V1 依赖 P3 助手(已有)与 V0②;V2 是主体,依赖 V0①;
V3 依赖 V0③(否则 judge 分进不了闸门),但**技能形态本身可提前独立做**;V4 收尾。
