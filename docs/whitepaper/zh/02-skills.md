# Skills:注册表、加载与内联

> 章次:02 · 状态:注册表/加载流水线/内联(merge v1)**已实现**;`register()` 运行期写入与版本约束求解为**契约预留(未实现)**;capsule/directed/code 免帧三档为**已设计未实现** · 依据:`agent_os/src/agent_os/skills/{local_file,manifest,loader}.py`、`agent_os/src/agent_os/context/manager.py`、`agent_os/src/agent_os/api/v1/skills.py`、`docs/DESIGN.md` §6、`docs/SKILL-INLINING.md`

## 1. 概述

Skill 是 Agent OS 的统一执行单元(编程概念映射表中的"函数"):有名、带参、可组合,prompt 与 code 两种形态对内核透明(`api/v1/skills.py:35-39`)。Skill Registry 是它的"动态链接器":负责技能的发现、校验、依赖解析、加载、热重载与运行期写入(DESIGN.md §6)。本章同时覆盖建立在 Registry 之上的**内联(inlining,merge)机制**——一个呈现层优化:纯说明书类技能的 prompt 在上下文组装期并入调用方 SYSTEM,调用本身消失。两者共享同一份 manifest 契约与同一条加载流水线:内联的合法性闸门全部落在加载期,组装与冻结全部落在 Context 子系统,内核调用路径零改动。

## 2. 动机与背景(原因)

**为什么 Registry 不在内核里。** 微内核判据只收三件事:流控制、权限控制、IPC。没有 Registry,agent loop 照样能推进(模型可以直接作答,不调任何技能),所以它不满足 F 判据,必须外置为子系统,经 `api/v1` 的 `SkillRegistry` Protocol 与内核交互(`api/v1/skills.py:161-175`)。这也兑现"契约先行,基线可换":`LocalFileSkillRegistry` 是基线实现,Skill Lab 的 Overlay Registry 复用同一契约与同一条帧构建路径(`local_file.py:127-158` 的 `build_child_frame` 被两者共用,理由是"不允许两套帧语义漂移")。

**为什么校验压在加载期。** 技能依赖图(`permissions.skills`)是声明式的,因此可以在加载期完整构建并做静态分析:循环依赖加载期报错,权限引用的工具/子技能必须存在,否则拒绝加载(DESIGN.md §6.1)。错误左移的代价是表达力收窄——运行期递归调用无法静态排除,由 `max_depth` 在运行期兜底。

**为什么需要内联。** 帧模型给每次子技能调用付固定成本:独立 FrameContext、独立 agent loop、独立上下文组装,外加至少一次额外的 LLM 往返。对"单步转换类"小技能(规范一个日期格式、抽一个字段),这套机器的代价远大于工作本身。SKILL-INLINING.md §1.2 的论证是:**任何保留"调用"形态的优化都至少要付一次额外 LLM 往返**;merge(预展开)是唯一真正消除调用的档——指令并入调用方 SYSTEM,父模型在自然工作流中顺带完成子任务。与 C++ `inline` 的类比在此最精确:不是"廉价的调用",是"没有调用";代价同样对应——代码膨胀,指令常驻 SYSTEM,每一步都付这些 token。

**为什么 merge 还有能力增益。** 帧隔离(DESIGN.md §2.3)下只有 input 能进入子帧,需要读父帧对话史的子任务("总结上文要点"、"检查前文一致性")在压帧形态下物理上做不了;merge 让指令在父帧内执行,天然可以读父上下文。这是其他档(capsule)给不了的。

**语义取向:诚实定价。** merge 不是 C++ 那种"语义保证不变"的优化——执行主体从被调技能自己的模型偏好换成了父模型,产出必然不同。设计放弃逐字节等价性承诺,代之以消融开关(on/off 两档可 A/B 对照)与加载期纯度闸门(把适用面收窄到语义偏差空间最小的"纯说明书能力")(SKILL-INLINING.md §1.3)。

## 3. 问题陈述(解决的问题)

1. **小技能大开销。** 一个把日期规范为 ISO 8601 的单步技能,压帧调用要付出:一次 LLM 调用决策 + 一次完整子帧(帧对象、上下文组装、outputs 校验、弹栈折叠)。工作本身只是一句话的改写。
2. **context-reader 类技能不可达。** "检查前文一致性"需要父帧对话史;帧隔离下子帧 messages 恰为 `[USER(input)]`(`local_file.py:149-157`),对话史物理上不在场。此类能力在压帧形态下不存在。
3. **热重载与在跑帧的版本漂移。** 若每步组装都从 Registry 现取技能 prompt,文件热重载会让同一帧的 SYSTEM 中途变化,打穿前缀缓存(DESIGN.md §7.4 不变量 5);resume 后重建的 SYSTEM 也会与断电前不一致。内联段来自*其他技能*的 manifest,该问题比帧自身技能更突出。
4. **配置错误运行期才爆。** 循环依赖、引用不存在的子技能,若不在加载期拦截,run 会半途失败且现场难以重建。
5. **内联打开的注入面。** 若允许带权限的技能内联,其指令并入低层帧后,高层能力的指令就"绕开隔离墙住进了别人家里"——干净 context 不变量被破坏(ESCALATION.md §3.4 原话)。内联必须与信任模型联动设闸。

## 4. 设计与机制(解决的方法)

### 4.1 契约:manifest 与 Registry 协议

`SkillManifest` 字段逐字对齐 DESIGN.md §2.1:`name/version/kind/description/inputs/outputs/verifier/permissions/model/context_policy/limits/entry/prompt/handler/logic/inline/trust`(`api/v1/skills.py:91-114`)。其中 `description` 被要求写成**路由规则**("Use when / Do not use when" + 负例),因为父模型靠它决定调不调——加载期 lint 只警告不阻断(`manifest.py:151-154`)。`SkillRegistry` Protocol 四个方法:`get / visible_to / make_frame / register`(`api/v1/skills.py:161-175`),`register()` 签名在 §14.1 冻结清单内(自我进化供给侧,见 §6)。

### 4.2 加载流水线

```
discover ─→ parse ─→ validate ─→ resolve deps ─→ materialize ─→ publish
(三形态源)  (YAML)   (硬闸门+lint)  (Kahn 拓扑)    (Skill 对象)    (可查)
```

- **源三形态**:单文件 / 目录(其下 `*.yaml` 按文件名排序合并)/ 路径列表(按给定序合并)(`local_file.py:173-180`);跨文件 name 去重与依赖校验与单文件同一逻辑(`local_file.py:202-214`)。
- **解析**:`yaml.safe_load` → 逐字段构造 manifest,`trust.confirm` 非法值加载期拒绝(`manifest.py:71-84`)。
- **校验**:硬闸门抛 `SkillLoadError`,lint 返回告警不阻断。除通用 lint 外,`inline: true` 触发专门闸门(见 4.4);装配点(KernelBuilder)另传入推导档执行升权分档闸门:**推导档 ≥L2 禁止 inline**,**L3 禁止 confirm: first**(`manifest.py:113-133`,调用点 `runtime/builder.py:190`;Skill Lab 提交闸门 G3 复用同一判定,`skills/gate.py:222-224`)。
- **依赖解析**:Kahn 拓扑排序(显式自引用合法,边忽略),循环依赖报错并列出成环节点(`local_file.py:105-124`);依赖只查存在,**不做版本约束求解**(单版本,见 §6)。迁移期旧扁平名经 `LEGACY_SKILL_ALIASES`(约 48 条)解析为点分层次名并记 warning(`local_file.py:42-101`)。
- **物化**:prompt 技能取指令体;code 技能 handler **惰性 import**——load 期不 import,首次调用才解析 dotted path 并校验协程函数(`loader.py:18-47`);prompt 渲染走 `str.format`,裸露 `{}` 直接报错(`loader.py:50-55`)。
- **热重载**:手动 `reload()`,mtime 未变返回 False;变更则重走全流程,**失败保留旧表抛错,不毁可用状态**;成功则"新帧用新版,在跑帧钉住旧版 Skill 对象"(`local_file.py:231-243`)。

### 4.3 呈现面:伪工具与子帧构建

子技能对父帧 LLM 呈现为伪工具 `skill.<name>`,schema 即被调方 `inputs`(`local_file.py:262-277`);内核在分发阶段按前缀拦截压栈(`kernel/runner.py:550`,`skill.` 与 `skill__` 两种前缀都拦截——命名出入见文末存疑)。`make_frame`/`build_child_frame` 做三件事:调用参数过被调方 `inputs` 的 jsonschema 硬校验(失败转为父帧的错误观察)、`depth+1`、**principal 原样继承**(升权改的是副作用许可,不是身份),帧输入以首条 USER 消息(`Source.PARENT_INPUT`)进入私有上下文(`local_file.py:127-158`)。

### 4.4 内联(merge)v1:闸门、组装与冻结

**声明与闸门。** 被调方 manifest 加 `inline: true`(默认 false,additive 扩展,`api/v1/skills.py:113`);调用方零改动,白名单声明照旧。加载期三条硬闸门(`manifest.py:160-185`):仅 `kind: prompt`;**纯度**——`permissions.tools/skills/blackboard` 全空(指令并入后这些权限无法执行,声明即矛盾);`prompt` 非空。lint 四条:prompt 含 `{` 占位符(merge 无离散 input 可渲染)、单技能 prompt >500 字符(`INLINE_PROMPT_MAX_CHARS`,`manifest.py:107`)、`inputs/outputs/model/context_policy` 声明(merge 档无运行期效力,仅作文档)、`verifier` 声明(其语义绑定帧弹栈仲裁,merge 无帧)。调用方侧另有一条:merge 依赖 >3 条告警(`INLINE_DEPS_MAX`,`local_file.py:217-224`)——对应 C++ "编译器拒绝内联大函数"。

**组装(主战场在 ContextManager,runner 零改动)。**

```
调用方 SYSTEM(on 档):
┌────────────────────────────────────────────┐
│ render_prompt(调用方.prompt, input)          │  ← 正常渲染
│                                            │
│ ## 内联能力(直接运用,无需调用)             │  ← 固定标头(字节稳定)
│ ### date_style@1.0.0                       │
│ <prompt 原文,不经 render_prompt>           │  ← 按 permissions.skills 声明序
└────────────────────────────────────────────┘
伪工具面:skill.<inline 技能> 被过滤;非 inline 子技能照常
分流:config.inline == "off" → 不组装、不过滤,退化为普通压帧调用
```

实现为 `_inline_caps`(`context/manager.py:154-201`):帧**首次** build 时按 Registry 现值组装快照,冻结进 `frame.context.working["_inline_caps"]`(含 `text/hidden/skills` 三项),后续 build 直接复用;同时一次性发 `post:context.inline` 信号(payload 含各技能 name/version/chars,`api/v1/signals.py:85`)。一个快照同时解决三件事:**前缀稳定**(SYSTEM 跨步逐字节一致)、**热重载语义**(在跑帧钉旧版)、**resume 确定性**(working 随帧入 checkpoint,恢复后重建逐字节一致)。消融判断放在 ContextManager(它持有 RunConfig),Registry 不感知消融档(`manager.py:163-164`)。

**降级路径(全部无特判)。** on 档下模型看不到伪工具,但若幻觉发起 `skill.X` 调用:该技能仍在白名单,内核照常压帧执行——行为正确,无害降级。`ctx.invoke`(程序化调用)与 `spawn`(后台帧)同理照常压帧:merge 只改变 **LLM 的呈现面**,不改任何程序化路径(SKILL-INLINING.md §4.4/§8)。

**关键取舍(SKILL-INLINING.md §14,照录理由):**

| 方案 | 不选理由 |
|---|---|
| capsule 先行 | 只省帧开销不省调用;落地面更大(动 runner + replay);给不了 context-reader 增益 |
| 加载期宏展开进调用方 prompt 文件 | 固化到磁盘破坏热重载与版本钉住;消融开关无法对照 |
| 内联段放 USER/INJECTED 消息 | 会进 `context.messages` 参与压缩驱逐,能力可能"中途失灵";SYSTEM 天然免驱逐且语义正确 |
| 自动内联启发式 | 行为不可预测;v1 只做显式声明 |

## 5. 效果与验证(效果)

**测试证据。** 锚点测试 `tests/context/test_inline_merge.py`(15 个用例函数,含参数化)与 `tests/skills/test_loader.py`(15 个),本次运行两文件合计 **35 例全绿**(0.60s)。关键断言:

- **闸门与 lint**:code 技能/非空权限/空 prompt → `SkillLoadError`;占位符/超长/无效字段/verifier → 告警不阻断(`test_inline_merge.py:151-186`);
- **组装**:on 档 SYSTEM 含标头与 `### test.tidy@1.0.0`、伪工具 `skill.test.tidy` 消失而 `skill.test.helper` 保留;off 档完全退化(`194-216`);
- **帧内冻结**:同帧相邻 build SYSTEM 逐字节一致;热重载(mtime bump)后在跑帧不变、新帧用新版;模拟 resume(working 深拷贝 + Registry 已更新)重建逐字节一致(`230-273`);
- **信号**:快照组装时 `post:context.inline` 只发一次,无内联依赖的帧不发(`281-295`);
- **A/B(经真实内核)**:on 档零子帧(`post:frame.push` 仅根帧 1 次),off 档真实压帧(2 次 push)——不断言产出等价,断言调用形态差异(`376-388`);
- **降级**:幻觉调用 / `ctx.invoke` / `spawn` 一个 merge 技能均照常压帧、结果正确(`391-419`);
- **replay 零改动**:含 merge 技能的 run 经 CLI replay 且 diff 为空(`result_equal` 与 `signals_equal` 均真),直接验证设计稿 §6 的论断(`427-449`)。

**真实示例。** `skillsets/inline_demo/skills.yaml`:`demo.date_style`(`inline: true` 的日期规范说明书)被 `demo.report_writer` 列入 `permissions.skills`,后者零改动自动受益。

**涟漪效应。** 内联把"干净 context 不变量"从运行期性质变成了加载期闸门:推导档 ≥L2 禁 inline 同时落在装配点(`runtime/builder.py:190`)与 Skill Lab 提交闸门 G3(`skills/gate.py:222-224`),升权系统的隔离承诺因此不被呈现层优化绕开(ESCALATION.md §3.4)。

## 6. 局限性与边界(局限性)

1. **无校验、无记账、无观测。** merge 技能的 `inputs/outputs` 无运行期硬校验;成本融入父帧的步,无独立 usage 归因;"内联能力没起作用"在运行期没有锚点可查——没有帧、没有信号、没有校验,排查手段只有关消融对照与看 SYSTEM 快照(SKILL-INLINING.md §7)。这是设计明确接受的代价,也是纯度闸门把适用面收窄到"短小说明书"的原因。
2. **语义不等价。** 执行主体是父模型,被调方的 `model.prefer` 失效;on/off 两档不承诺产出等价,消融仅用于调试与离线质量/成本评测,不作 CI 等价断言(SKILL-INLINING.md §9)。
3. **版本约束求解未实现。** DESIGN.md §6.1 的 `<namespace>:<name>@<semver>` 与 `^`/`~` 语义是契约;基线实现单版本、依赖只查存在(`local_file.py:3-4`)。同名多版本共存、按约束解析都还没有。
4. **`register()` 未实现(M6)。** 运行期写入路径(Agent 自写技能的信任管线)只有冻结签名,调用即 `NotImplementedError`(`local_file.py:287-293`);v1 的最小替代是写文件 + `reload()`。
5. **指令冲突无仲裁。** 多个 merge 技能(或与调用方自身 prompt)指令矛盾时没有任何检测与仲裁,靠条数 lint + code review(SKILL-INLINING.md §15 开放问题 1)。
6. **膨胀阈值是经验拍值。** 500 字符 / 3 条均非按 token 估算口径推导,设计稿自承"纯拍脑袋"(SKILL-INLINING.md §15 开放问题 3)。
7. **纯度闸门的代价:不可组合。** merge 技能不能声明任何 skills 依赖,不存在内联链/传递展开;能力稍复杂(需要一次工具调用)就必须退回压帧形态。
8. **快照即真相的另一面。** 在跑帧钉旧版意味着修复一个错误的内联指令,对已开始的长 run 内的在跑帧不生效——这是前缀稳定与 resume 确定性的直接代价,不是缺陷,但运维上要知道。
9. **降级路径的隐性成本。** 幻觉调用虽无害(行为正确),但会付出一次未预期的完整帧执行;`spawn` 路径不读 inline 标志,spawn 一个 merge 技能照常压帧——语义上这是"文档化的不生效"而非报错,误用者无反馈。
10. **覆盖面缺口。** `MinimalContextManager` 不支持内联(M0 基线保持极简);Web 帧详情的内联能力徽标为可选后续,未做(SKILL-INLINING.md §11)。

## 7. 引用

- 设计文档:`docs/DESIGN.md`(§2.1 契约、§3.3 伪工具、§6 Skill Registry、§7.4 前缀不变量)、`docs/SKILL-INLINING.md`(merge 设计全稿)、`docs/ESCALATION.md`(§3.4 inline 硬闸门)、`docs/NAMING.md`(§4 旧名映射)
- 契约:`agent_os/src/agent_os/api/v1/skills.py`(manifest/Registry 协议)、`api/v1/run.py:36`(消融档)、`api/v1/signals.py:85`(`post:context.inline`)
- 实现:`agent_os/src/agent_os/skills/local_file.py`(Registry)、`skills/manifest.py`(解析/闸门/lint)、`skills/loader.py`(物化/渲染)、`context/manager.py:154-201`(内联组装与冻结)、`runtime/builder.py:190` / `skills/gate.py:222-224`(分档闸门落点)、`kernel/runner.py:550`(伪工具拦截)
- 测试:`agent_os/tests/context/test_inline_merge.py`(15 例)、`agent_os/tests/skills/test_loader.py`(15 例)
- 示例:`skillsets/inline_demo/skills.yaml`

---

**资料存疑(以代码为准)**:① 伪工具命名——DESIGN.md §3.3 与 SKILL-INLINING.md 行文写作 `skill__<name>`,代码生成的是 `skill.<name>`(`local_file.py:272`),runner 两种前缀都拦截(`runner.py:550, 844-845`);本章统一按代码写 `skill.<name>`。② DESIGN.md §6.3 称目录包形态为"后续 DirectorySkillSource",代码已支持目录形态(多 `*.yaml` 排序合并,`local_file.py:173-180`)。③ DESIGN.md §6.1 的版本约束求解为设计承诺,代码明确不做(`local_file.py:3`)。
