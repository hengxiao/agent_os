# Skill Inlining 设计稿:预展开(merge)

> 状态:**v1(merge)已实现**——锚点测试见 `agent_os/tests/context/test_inline_merge.py`,
> 落地对应 §11 清单;§13 后续档(capsule/directed/code 免帧)仍为设计。
> 权威架构文档见 [DESIGN.md](DESIGN.md),本文引用其章节锚点(§x.y)。
> **v1 范围 = 预展开(merge)**:被调技能的 prompt 在上下文组装期并入调用方 SYSTEM,
> 调用消失,父模型在自然工作流中直接运用该能力。
> one-shot capsule 与 directed(定向回合)两档降为后续扩展,见 §13。

---

## 1. 动机与定位

### 1.1 问题

当前每次子技能调用都压一个完整的帧(§3.1):独立 FrameContext、独立 agent loop、
独立上下文组装。对"单步转换类"小技能(改写一句话、抽一个字段、格式化一个值),
这套机器的代价远大于工作本身——而且**任何保留"调用"形态的优化都至少要付
一次额外的 LLM 往返**(哪怕是最小化的胶囊请求)。

### 1.2 为什么 v1 选 merge

merge 是唯一**真正消除 LLM 调用**的档:被调技能的指令并入调用方 SYSTEM,
父模型顺带完成子任务,零额外往返。与 C++ inline 的类比在这一档最精确:

- **函数体展开进调用点,由调用方的执行引擎执行**——不是"廉价的调用",
  是"没有调用";
- **代价同样精确对应:代码膨胀**——指令常驻调用方 SYSTEM,每一步都付
  这些 token;C++ 编译器拒绝内联大函数,merge 同样需要指令长度/条数上限;
- 额外收益:**解锁 context-reader 类技能**——需要读父帧对话史的子任务
  ("总结上文要点"、"检查前文一致性")在帧隔离下做不了(§2.3 只有 input
  进得去),merge 天然可以。

工程上的第二个理由:**落地面最小**。merge 不动 runner(调用路径零改动)、
不动 checkpoint/replay(无额外调用与信号需要对齐),主战场集中在
ContextManager(组装)与 manifest(闸门)两处;对照 capsule 档需要在
`_invoke_skill` 分叉并修改 replay 的对齐逻辑(§13.1)。

### 1.3 语义取向:这不是透明优化,要诚实定价

merge **不是** C++ 那种"语义保证不变"的优化——执行主体从被调技能自己的
模型(`model.prefer`)换成了父模型,产出必然不同。本设计**放弃逐字节等价性
承诺**,代之以:

- 消融开关(§9):`inline: "off"` 时 merge 技能退化为普通压帧调用,
  两档可 A/B 对照(质量/成本评测,不是等价断言);
- 加载期闸门(§3)把适用面收窄到"纯说明书能力",让语义偏差的空间最小。

### 1.4 非目标(v1)

- ❌ one-shot capsule(保留调用形态的最小化请求)——后续档,§13.1;
- ❌ directed 定向回合(父模型执行但保留调用/校验)——后续档,§13.2;
- ❌ code 技能免帧执行——与"prompt 进上下文"无关的另一机制,后续档,§13.3;
- ❌ 自动内联启发式——v1 只做显式声明。

---

## 2. 语义定义:调用消失

| 维度 | 压帧调用(现状) | merge(本设计) |
|---|---|---|
| 呈现给父模型 | 伪工具 `skill__<name>`(§3.3) | **SYSTEM 里的"内联能力段"**,无伪工具 |
| 调用 | LLM 显式发起,内核拦截压栈 | **不存在**——父模型在自然工作流中运用 |
| input/outputs 校验 | jsonschema 硬校验 | **无**(无离散调用可校验;schema 仅存档为文档) |
| 帧 / depth | make_frame + push/pop,depth+1 | 无 |
| 记账 | 子帧独立 usage | 融入父帧的步,**无独立归因** |
| 信号 | pre/post:skill.invoke + frame.* | 无调用信号;帧首次组装发一次性 `post:context.inline`(§7) |
| 依赖图(§6.1) | permissions.skills 静态边 | **保留**(声明仍在,静态可分析) |
| 子任务可见父上下文 | 否(帧隔离,§2.3) | **是**(这是能力增益,也是风险面) |
| 模型 | 被调 manifest `model.prefer` | **父帧模型**(被调偏好失效) |

成本真相(与其他档对照,详见 §13 表):merge 每次"调用"零额外 LLM 往返、
零请求膨胀,代价是指令**常驻**父 SYSTEM——成本从"按次付"变成"按步付"。
赢面 = 使用频率高 × 指令短。

**§2.3 隔离原则的修订边界**:merge 打破的是"子技能上下文不进父帧"的
**指令半边**(prompt 进入 SYSTEM);由于根本没有子执行轨迹,"transcript
不进父帧"的半边空置。§7.2 `collapse_child` 对 merge 技能无对象。

---

## 3. 声明与合法性闸门

### 3.1 manifest 声明(被调方)

```yaml
- name: date_style
  version: 1.0.0
  kind: prompt
  inline: true            # ← 新字段,默认 false;v1 语义 = merge
  description: >
    日期规范能力:输出中的日期一律 ISO 8601。Use when 调用方产出含日期;
    Do not use when 需要相对时间推算(那需要独立技能)。
  permissions: { tools: [], skills: [] }   # 纯度约束:必须为空
  prompt: |
    输出中出现日期时,一律规范为 ISO 8601(YYYY-MM-DD);
    含时区语义时使用 UTC 并显式标注。
```

与 C++ `inline` 关键字同位:**技能作者声明,所有把它列入
`permissions.skills` 的调用方自动受益**;调用方 manifest 零改动。

### 3.2 加载期硬闸门(违反 → `SkillLoadError`)

1. `kind: prompt` —— merge 只对 prompt 技能有意义(code 技能没有
   "prompt 可并入"一说);
2. `permissions.skills == []` 且 `permissions.tools == []` 且
   `permissions.blackboard == []` —— 纯度:内联能力不得再引入调用、
   副作用或黑板依赖(指令并入后这些权限无法执行,声明即矛盾);
3. `prompt` 非空。

### 3.3 加载期 lint(告警,不阻断)

1. **说明书形态**:prompt 含 `{...}` 格式占位符 → 告警(merge 无离散
   input 可渲染,占位符会原样暴露给模型;技术上不爆错——内联段不经
   `render_prompt`——但语义上是作者写错了形态);
2. **膨胀上限**(C++ "拒绝内联大函数"的对应物):单技能 prompt
   > 500 字符 → 告警;单个调用方 merge 依赖 > 3 条 → 告警;
3. `inputs`/`outputs`/`model`/`context_policy` 若声明 → 告警提示
   "merge 档无运行期效力,仅作文档"(为未来降级/切档保留,不禁止);
4. `verifier` 若声明 → 告警(其语义绑定帧的 pre:frame.pop 仲裁,merge 无帧)。

> 静态可分析(§6.1)保持:全部闸门与 lint 在 manifest 层完成;
> 依赖图不受影响(merge 技能是叶子,且保留在 `permissions.skills` 边上)。

---

## 4. 机制:内联能力段的组装

主战场在 **ContextManager.build**(§7.5),runner 调用路径零改动。

### 4.1 组装规则

对帧 build 时(`ContextManager.build(frame)`):

1. 取调用方 manifest `permissions.skills` 中 `inline: true` 的技能,
   按**列表声明序**排列(确定性,不按字典序——作者控制优先级);
2. SYSTEM = `render_prompt(caller.prompt, input)` + 内联能力段:

   ```
   ## 内联能力(直接运用,无需调用)

   ### date_style@1.0.0
   <prompt 原文,不经 render_prompt>
   ```

3. `visible_to` 产出的伪工具列表**过滤掉** merge 技能——父模型看不到
   `skill__date_style`,不会尝试调用;
4. 非 inline 的子技能照常生成伪工具,两类可在同一调用方混用。

### 4.2 帧内冻结(前缀稳定性 + 版本钉住)

**内联段在帧首次 build 时快照,存入 `frame.context.working["_inline_caps"]`,
后续 build 直接复用快照。** 一举解决三件事:

- **前缀稳定性(§7.4 不变量 5)**:SYSTEM 跨步逐字节一致——若每步从
  registry 现取,热重载会让同帧 SYSTEM 中途变化,打穿前缀缓存;
- **热重载语义(§6.1)**:"新帧用新版,在跑帧钉旧版"对内联能力**同样成立**
  ——现状该保证只覆盖帧自己的技能,内联段来自其他技能的 manifest,
  必须显式钉住;
- **resume 确定性(§8/§10.2)**:`working` 随帧入 checkpoint,恢复的帧
  内联段与断电前逐字节一致,不受恢复时 registry 版本影响。

### 4.3 消融分流(实现位置)

消融判断放在 **ContextManager**(它持有 RunConfig),registry 不感知消融档:

- `config.inline == "on"`(默认):按 §4.1 组装;
- `config.inline == "off"`:不组装内联段、不过滤伪工具——merge 技能
  退化为普通可调用技能,压帧路径照旧。

### 4.4 幻觉调用的防御性降级

on 档下父模型看不到 `skill__X` 伪工具,但若仍幻觉发起该调用:
`_invoke_skill` 照常处理(该技能在 `permissions.skills` 白名单里)→
**压帧执行,行为正确,无害降级**。不加任何特判——这是 merge 不动 runner
的直接好处。

---

## 5. 记账与限额

- 子任务成本融入父帧的正常步,**无独立归因**——诚实说明,不做补偿性
  估算(任何"内联任务占比"的拆分都是猜测,违反"状态栏数据只来自内核
  记账"的文化,§7.3);
- run 级 max_steps / max_cost 天然生效(没有新的调用形态需要覆盖);
- max_depth 无涉(无帧);
- 膨胀的真实约束是 §3.3 的 lint 上限 + token 估算(内联段计入 SYSTEM,
  estimator 如实计量,压缩水位判断自动包含它;但内联段在 SYSTEM 里,
  **不参与压缩驱逐**——SYSTEM 本就不是 RollingWindow 的驱逐对象)。

---

## 6. 持久化:checkpoint / resume / replay

- **checkpoint**:内联段快照在 `frame.context.working` 里随帧入档
  (§4.2);无新增字段、无 schema 变更;
- **resume**:恢复的帧从 working 读快照,SYSTEM 重建逐字节一致;
  无新增补记逻辑——没有调用,就没有未配对调用;
- **replay:零改动**。无额外 LLM 调用、无额外信号,`(frame_id, 第 n 条
  assistant 消息)` 对齐关系不变。这是 merge 相对 capsule 的显著优势
  (capsule 需要 replay 跳过 inlined 信号并反推胶囊脚本,§13.1)。

---

## 7. 可观测性

运行期没有调用可观测——这是 merge 的固有代价。补偿手段:

- **新信号 `post:context.inline`**(一次性,帧首次 build 组装快照时发):
  payload `{"frame_id", "skills": [{"name", "version", "chars"}]}`——
  遥测/Web 至少知道"这个帧带了哪些内联能力、各占多少字符";
  信号名不在 §14.1 冻结清单,新增无契约冲突;
- **Web(可选,非 v1)**:帧详情页显示内联能力列表(数据源:上述信号
  或 checkpoint working);
- **RCA 的诚实边界**:"内联能力没起作用"(如日期没规范化)在运行期
  **没有锚点可查**——没有帧、没有信号、没有校验。排查手段只有:关消融
  开关对照、看 SYSTEM 快照确认指令确实在场。设计上接受这一点,
  这正是 §3.3 把适用面收窄到"短小说明书"的原因。

sidecar 落点对照:ToolGuard / CodeScanner / LoopDetector / reviewer
全部无涉(无调用、无执行、无弹栈)。BudgetGuard 照常(它按 run 级
post:llm.response 累计)。

---

## 8. 与其他子系统的交互

- **spawn(§3.4)**:spawn 的语义是"后台帧 + frame_id 句柄",与 merge
  无交集。spawn 一个 `inline: true` 技能 → **照常压帧**(spawn 路径
  不读 inline 标志),返回 frame_id。文档化即可,无代码分叉;
- **`LogicContext.invoke`(§9.3)**:code 技能显式 `ctx.invoke` 一个
  merge 技能 → 同 §4.4,照常压帧执行(invoke 是程序化调用,不受
  "模型看不看得见伪工具"影响)。merge 只改变**LLM 的呈现面**;
- **多调用方**:同一 merge 技能被多个调用方列入白名单 → 各自的 SYSTEM
  独立展开(C++ 的每调用点复制);版本以各帧快照时的 registry 为准;
- **merge 技能之间**:互不可见(纯度约束禁止 skills 依赖),不存在
  内联链/传递展开。

---

## 9. 消融开关与评测

`RunConfig.inline: "on" | "off"`(默认 `"on"`;配置 `[run] inline = "off"`)。

**off 档语义**:merge 技能退化为普通压帧技能——伪工具恢复、内联段消失。
两档**不承诺产出等价**(执行主体不同:父模型 vs 被调技能独立帧),消融的
用途是:

1. **调试**:怀疑内联指令引起父帧行为异常时,关掉对照;
2. **质量/成本评测**:同一任务集上 A/B 两档的结果质量与 token 消耗,
   作为"该技能适不适合 merge"的实证依据(离线,非 CI 锚点)。

---

## 10. 不变量

1. **前缀稳定性继承**(§7.4 不变量 5):内联段帧内冻结,SYSTEM 跨步
   逐字节一致;热重载不影响在跑帧;
2. **纯度**:merge 技能无 tools/skills/blackboard 权限(加载期保证);
3. **静态依赖图保持**(§6.1):`permissions.skills` 声明不因 merge 改变,
   加载期可完整分析;
4. **调用路径零侵入**:runner/_invoke_skill/checkpoint/replay 的行为
   对 merge 技能与普通技能无差别(merge 只发生在呈现层——伪工具过滤
   与 SYSTEM 组装);
5. **快照即真相**:一个帧运用的内联能力以其 `working["_inline_caps"]`
   快照为准,checkpoint/resume/遥测均以此为源。

---

## 11. 落地改动清单(实现阶段的地图,非本稿交付)

| 文件 | 改动 |
|---|---|
| `src/agent_os/api/v1/skills.py` | `SkillManifest` 加 `inline: bool = False`(加字段属向后兼容扩展,不触 §14.1 冻结) |
| `src/agent_os/api/v1/run.py` | `RunConfig` 加 `inline: str = "on"` |
| `src/agent_os/api/v1/signals.py` | 新增 `POST_CONTEXT_INLINE = "post:context.inline"` |
| `src/agent_os/skills/manifest.py` | 解析 `inline`;§3.2 三条硬闸门 + §3.3 四条 lint |
| `src/agent_os/context/manager.py` | **主战场**:内联段组装与帧内冻结快照(§4.1/§4.2)、伪工具过滤、消融分流(§4.3)、`post:context.inline` 信号;`MinimalContextManager` 不支持(M0 基线保持极简) |
| `src/agent_os/runtime/config.py` | `_RUN_FIELDS` 加 `inline` |
| `DESIGN.md` | §3.3 补"inline 技能不生成伪工具";§7.2 策略表补注;§7.5 build 步骤补内联段 |
| runner / checkpoint / replay / web | **零改动**(§4.4/§6;web 徽标为可选后续) |

## 12. 锚点测试清单(tests/context/test_inline_merge.py 等)

1. **闸门**:`inline: true` + code 技能 / 非空 tools/skills/blackboard /
   空 prompt → SkillLoadError;占位符 / 超长 / 超条数 / 声明 outputs →
   告警不阻断;
2. **组装**:on 档 SYSTEM 含内联段(按声明序)、无对应伪工具;
   非 inline 子技能伪工具照常;off 档完全退化;
3. **前缀稳定**:同帧多步 build,SYSTEM 逐字节一致;registry 热重载
   (mtime bump)后:在跑帧 SYSTEM 不变,新帧用新版;
4. **快照入档**:checkpoint 的帧 working 含 `_inline_caps`;resume 后
   SYSTEM 重建与断电前逐字节一致(且不受恢复时 registry 版本影响);
5. **信号**:帧首次 build 发一次 `post:context.inline`,payload 含
   skills/chars;后续步不重发;
6. **降级**:on 档 brain 仍发 `skill__X`(幻觉调用)→ 压帧执行成功,
   结果正确;`ctx.invoke` merge 技能 → 压帧执行成功;
7. **spawn**:spawn merge 技能照常压帧、返回 frame_id;
8. **replay**:含 merge 技能的 run 可 replay 且 diff 为空(验证 §6
   "零改动"论断);
9. **消融 A/B**(确定性 brain):on/off 两档各自跑通;不断言产出等价,
   断言 off 档产生了真实子帧、on 档没有。

---

## 13. 后续档(本次不做,保留设计)

### 13.1 one-shot capsule(单发胶囊)

保留调用形态的最小化执行:`skill__X` 调用不压帧,内核用被调技能自己的
prompt/model/outputs 构造**一次独立 LLM 调用**,结果折叠为父帧 tool result
(与压帧折叠形态逐字段同构)。语义保真最高(可承诺消融等价性:确定性
brain 下 on/off 逐字节一致),适合纯转换且要保留校验/记账/观测的场景。
落地需动 `_invoke_skill`(分叉 `_invoke_inline`)与 replay(对齐计数跳过
inlined 信号、胶囊脚本从 tool result 反推)。

### 13.2 directed(定向回合)

保留调用形态、父模型执行:调用配对"指令即结果",父模型下一轮产出子结果,
内核截获并按被调 outputs 校验。**成本上比 capsule 贵**(每次调用付整个
父上下文的 prompt tokens),赢面仅在子任务需要读父上下文**且**要保留
校验/记账时。截获鲁棒性(定向回合内夹带其他 tool_calls)是主要开放问题。

### 13.3 code 技能免帧执行

跳过帧、不跳过执行层:TRUSTED code 技能经 logic router 直接执行,
pre/post:logic.exec 与 CodeScanner 落点保持。与"prompt 进上下文"正交,
可独立立项。

### 13.4 三档 + merge 的成本对照

| 档 | 额外 LLM 调用 | 每次调用 prompt tokens | 常驻开销 | 子任务可见父上下文 | 校验/记账/观测 |
|---|---|---|---|---|---|
| 压帧(现状) | 1+ | 子帧上下文(小) | 无 | 否 | 全 |
| capsule | 1 | 极小 | 无 | 否 | 全 |
| directed | 1 | **整个父上下文** | 无 | 是 | 全 |
| **merge(v1)** | **0** | 0 | 指令常驻 SYSTEM,每步付 | 是 | **无** |

## 14. 备选方案与不选理由(v1 范围内)

| 方案 | 不选理由 |
|---|---|
| capsule 先行(原 v1 草案) | 只省帧开销不省调用;落地面反而更大(动 runner + replay);merge 的能力增益(context-reader)它给不了 |
| 加载期宏展开进调用方 prompt 文件 | 与 §4.2 帧内冻结快照相比,把展开固化到磁盘破坏热重载与版本钉住语义,且调试时无法用消融开关对照 |
| 内联段放 USER/INJECTED 消息而非 SYSTEM | 会进入 `context.messages` 参与压缩驱逐,指令可能被驱逐导致能力"中途失灵";SYSTEM 位置天然免驱逐且语义正确(它就是指令) |
| 自动内联启发式 | 行为不可预测;显式声明 + lint 上限先铺路 |

## 15. 开放问题

1. 内联段的**指令冲突**:多个 merge 技能(或与调用方自身 prompt)指令
   矛盾时无仲裁机制(C++ 宏污染类比)。v1 靠条数 lint + code review;
   要不要加载期做语义冲突检查(可能需要 LLM 评审,成本高,倾向不做)?
2. `working["_inline_caps"]` 用工作内存存快照是复用现有结构的最小改动,
   但 working 语义上是"技能的工作内存"——要不要给 FrameContext 加正式
   字段(动 §2.3 冻结结构,代价大,倾向 v1 不动)?
3. 膨胀 lint 的阈值(500 字符 / 3 条)纯拍脑袋——要不要按 token 估算
   (estimator 口径)而非字符数?
4. 消融开关要不要细到 per-skill(`inline: "off"` 全局关 vs 调用方逐技能
   opt-out)?v1 全局,够用再说;
5. merge 技能的 `description` 是否也该并入内联段(帮助父模型判断何时
   运用)?v1 只并 prompt,description 留给人读。
