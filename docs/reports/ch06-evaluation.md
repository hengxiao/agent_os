# Chapter 6《Evaluating Agents》与 Agent OS 设计对比报告

> 来源:`ai-agent-book/book-en/chapter6.md`(728 行,已通读全文)
> 对照:`../DESIGN.md` v0.3(741 行,已通读全文)
> 总体关联度:**中等偏高**。

本章不是内核机制章节,而是"如何科学验证与迭代一个 Agent 系统(模型 + Harness)"的方法论章节。
Agent OS 的定位恰恰是本章所称的 Harness,因此本章对内核设计的直接要求集中在三点:

1. 记账与 trace 的数据字段是否足以支撑成本归因与问题诊断;
2. "评测/消融基础设施必须架构期内置"的警告对我们的适用性;
3. Skill 抽象能否直接表达评审(judge)与用户模拟器(user simulator)。

三点都有具体落点,详见后文。

---

## 章节内容概要

本章围绕"怎么知道 Agent 真的变好了"构建三层评测体系:评测环境(在哪测)、评测方法(怎么判)、评测驱动决策(测完怎么办)。
核心主张:**评测对象是"模型 + Harness"的组合而非模型本身**;评测系统的首要价值不是给现状打分,而是让团队在新模型发布数小时内做出数据驱动的切换决策。

**实验方法论**

- 对比实验(改变单一变量观察效果)与消融实验(ablation,逐个禁用组件看整体性能变化,暴露组件真实贡献)。
- **模型交换实验**(model swap):固定 Harness 只换模型,用于区分"模型能力不足"与"Harness 设计缺陷";强模型不涨分 → 瓶颈在 Harness。
- 完整方法闭环:观察 → 假设 → 实验 → 验证 → 新理解 → 新假设。

**评测环境(两大范式,五要素)**

- 五要素:Dataset、Environment State(真实性 vs 可控性,可 reset)、Tools(必须原子化,禁止"solve user problem"式高层抽象)、Rubric、Interaction Protocol。
- 工具调用范式:Verifiers 框架环境层级 `SingleTurnEnv` / `ToolEnv` / `StatefulToolEnv` / `SandboxEnv`;并行采样 + trajectory 缓存回放;工具失败须给清晰错误信息而非裸失败标志。
- 人机交互范式:τ-bench 的 **User Simulation**(LLM 扮演用户)+ **Progressive Information Disclosure**(信息绝不一次给全,按对话进展逐步披露,披露过程本身就是能力考察)。
- τ²-bench 改进:**Dual-Control Environment**(用户模拟器与 Agent 共同操作同一共享环境)、更精确的任务规格、"known information / task instructions" 分离、Grounding Requirements(回答须基于工具真实返回)。
- 校验方式:数据库终态检查 + 对话关键词 + 流程合规(工具调用序列分析),最终坍缩为 **binary reward**(0/1,便于算 Pass^k,代价是"差一个非关键字段"与"完全失败"同分)。

**数据集设计(五大挑战与工艺)**

- 挑战:清晰性 vs 开放性(GAIA 目标清晰路径开放)、真实性 vs 可控性(SWE-Bench Verified 从 2294 题人工筛至 500,通过率 29%)、多样性 vs 体系化(AndroidWorld 116 任务 × 20 应用 + 能力标签)、成本 vs 覆盖、**防数据污染**。
- 防泄漏手段:GAIA 自制附件(网上不存在的 PDF/音频)、SWE-bench-Live 时间新鲜度、τ²-bench/AndroidWorld **参数化模板**动态生成、Terminal-Bench 的 **canary GUID** 泄漏探针。
- 复杂度分层:GAIA 三级难度(对应人类 93.9% vs GPT-4 30.3% → 87.3% vs 0%),每级失败指向不同改进方向(提示工程 / 规划机制 / 分层架构或后训练)。
- 可验证性:SWE-Bench 的 **FAIL_TO_PASS / PASS_TO_PASS** 双重校验;OSWorld 134 个独立评估函数做深度状态检查(连数据库、解析 DOM、查后端);Terminal-Bench 容器内真实启动 QEMU 验证。
- 质量控制:SWE-Bench Verified 93 名标注者 + 标注指南;OSWorld-Verified 15 个月修复 300+ issue(环境/描述/校验逻辑/初始状态四类),50x 并行提速,轨迹全公开。

**指标体系**

- 过程指标:**action legality rate**(非法工具名/错误参数类型/越权操作占比)、tool call correctness(参数语义合理性)、**path efficiency**(步数、冗余动作、回溯频率)、retrieval coverage、成本与延迟(区分输入/输出成本与 KV Cache)。
- 结果指标:task success rate;**Pass@k**(k 次至少一次成功,能力上限)vs **Pass^k**(k 次全部成功,稳定性)vs **Best@k**(质量上限);数字示例:单发 0.6 时 Pass@5≈99% 而 Pass^5≈7.8%,混用必误判。
- 安全合规:**零容忍否决原则**,一次严重违规否决整体评价,不因其他维度优秀豁免。
- **轨迹与结果双重覆盖**:Agent 说"订完了"是轨迹级;数据库真有记录是结果级。只看轨迹漏"说了没做",只看结果漏中间歧途(Anthropic 订机票发现政策漏洞的反例)。
- 人工抽查与对抗审查;**judge calibration**:100-200 例金标准集测评委与人一致率(Cohen's kappa > 0.7 才允许规模化);多评委分歧大时转人工。

**LLM-as-a-Judge 与 Rubric**

- **长度偏置**三防御:rubric 显式罚冗长并按任务类型限长、pairwise 先把两候选对齐到相近长度、定期审计分数与长度的相关性。
- **Rubric 四原则**(Scale AI "Rubrics as Rewards"):专家指导、全面覆盖(含显式 Pitfalls)、权重分级(Essential/Important/Optional/**Veto**,支持否决项)、自包含(每条独立可判,拒绝"展现深刻理解"式抽象标准)。
- 显式防 **Reward Hacking**(关键词堆砌、谄媚、回避难题);rubric 是迭代产物,从评委分歧演化为案例集。
- **同族模型问题** + **Goodhart's Law**:被测者与评委同族时会学会利用评委盲区;缓解 = **多源异构评审**(不同模型家族任评委,偏差正交,加权平均或一致性检查聚合)。
- 多模态评审:TTS/ASR(语义影响评估,"一千"误成"一万")/UI 的 **Proposer-Reviewer**/视频关键帧校验。

**Pairwise comparison 与排名**

- **Elo rating**(在线增量更新,爆冷修正幅度大)及其统计基础 **Bradley-Terry model**(隐实力分数,MLE 联立求解);Chatbot Arena 匿名盲投,排名受用户提问分布影响。
- **Position Bias**:标准缓解 = **交换顺序评两次**取平均;严格做法只计两次一致的样本,不一致判平局或送人工。
- pairwise 信号向训练迁移:GRPO 用同题多候选的相对优劣估计优势,省掉 PPO 的 value network(细节在第 7 章)。

**模型选型与成本**

- 吞吐/延迟:Prefill 决定 **TTFT**,Decode 决定生成速度;thinking latency(thinking 长度与效果未必正相关,须在自有负载上实测);p95 尾延迟优于均值。
- **成本三层分解**:模型推理(含**上下文累积效应**——第 n 轮重发全部历史,无缓存时 1000+2000+3000=6000 而非 3×1000——与 **thinking token 计费**)、工具调用(外部 API 费 + **工具返回注入上下文后每轮重复计费**的隐性成本)、基础设施。
- 优化三板斧:**KV Cache 复用**(稳定前缀,省 30%-60% 输入成本)、**Context Compression**、**Tiered Model Routing**;另加异步批处理与**成本监控 + 每任务预算封顶**(循环或过深探索自动终止)。
- 分层混用模型须经评评测证"整体收益大于复杂度代价"。

**统计显著性**

- 二项标准误 √(p(1-p)/n):100 例 70% 成功率 → ±9pp 噪声带宽;3% 差异落在带宽内,据此换模型与掷硬币无异。
- 同一任务集上的**配对分析(McNemar's test)** 只看两配置结论相反的任务,比独立差分灵敏得多;√2 独立假设只是心算筛子。
- 多次运行取均值(3-5 次不同种子)并报均值与离散度;扩样本收益按 √n 递减(100→400 仅减半噪声)。
- **多重比较陷阱**:6 个并行假设至少一个假阳性的概率约 26%;对策为 Bonferroni 式校正或独立复测确认。

**可观测性**

- trace/span 树模型;**OpenTelemetry** 通用标准 + **OpenInference** LLM 语义约定(prompt、模型参数、token 用量的记录规范);标准协议让采集与分析解耦、避免厂商锁定。
- LangSmith/Langfuse/Phoenix;异步批量采集不影响 Agent 延迟;A/B 测试与 prompt 版本管理。
- **生产 trace 回流为评测资产**:失败/可疑案例 → 脱敏 → 蒸馏为新测试与回归用例;评测集从一次性静态集合变为随产品演化的活资产。

**从报告到改进 + 内部评测基础设施**

- 性能下降时**先查评测系统再查 Agent**(环境资源耗尽杀进程、scorer bug、用例漂移,头条数字上与模型退化无法区分,须看完整 trace)。
- **能力标签矩阵**定位结构性弱点;表层/中层/深层三层假设框架(AndroidWorld 假想案例 H1-H8:导航提示 → 修多模态管线 → 换模型/加元素树的 2×2 实验);机制指标 vs 目标指标;护栏指标。
- OpenClaw 式内部评测设施:**消融总开关**(一键禁用 thinking/压缩/记忆等得 "bare model" 基线;开关须在启动路径极早期注入,**必须架构期内置**;定期消融发现 **feature debt**);多臂 A/B 测试;**双层 Feature Flag**(编译期物理移除 + 运行期下发,flag 是一等架构组件);**Prompt 敏感性评估**(确定性渲染 + 版本快照 + 每次变更跑回归,等同代码 CI);**隐私感知分析**(类型系统强制)。
- 仿真环境:Rubric/validator 即 **RLVR** 奖励函数;训练级 **reset 语义**与高吞吐;AWorld(26 个 MCP server 沙箱、无状态实例、14.6x 加速);Domain Randomization 缩小 sim-to-real 差距。

---

## 与 Agent OS 设计的映射

- **Harness = Agent OS 本体**。本章的"模型 + Harness"评测对象,对应我们"一个 RunConfig + 一组 Skill 版本 + 一套内核组装"的组合。
- **模型交换实验** → 改 `RunConfig.model`(§2.4)或 manifest `model.prefer`(§2.1),内核其余不动——我们的静态模型声明天然支持该实验。
- **消融实验** → KernelBuilder 增减子系统(§11.2);契约层 + 信号总线(§11.1、§5.1)保证子系统可缺席。
- **trace/span 树 → 帧树与信号总线**。span 的父子关系即 SkillFrame 的 `frame_id`/`parent_id`(§2.3);LLM/工具调用已由 `post:llm.response`、`post:tool.call` 信号携带(§5.1);TraceRecorder(§5.4/§5.5)即采集层;`RunControl.get_frame_tree()`(§5.2)即执行树查询。
- **trajectory 缓存回放 → golden-file + MockProvider**(§13);"生产 trace 回流评测集"对应失败 trace 转 MockProvider 脚本。
- **Rubric / LLM-as-a-Judge → prompt 技能**。评委 = `kind: prompt`、prompt 体为 rubric 模板、`outputs` schema 约束结构化打分的 Skill(§2.1);多源评审 = 同一 judge 在不同 `model.prefer` 下各跑一遍;交换顺序评两次 = 两次子技能调用(§3.3)。
- **User Simulation → 双技能编排**。模拟器与被测 Agent 各为根技能;模拟器的"已知信息 + 披露规则"是其 FrameContext 私有状态(§2.3),帧隔离语义天然保证"信息不提前泄露";交替对话可用 code 技能编排(§9.3 `LogicContext.invoke`)。
- **零容忍否决项 → SYNC sidecar 的 Veto**。评测侧"一次严重违规否决整体"是运行侧 `Verdict.Veto` + fail-closed(§5.2/§5.3)、ToolGuard(§5.4)的镜像。
- **成本监控与预算封顶 → BudgetGuard + usage 记账 + `RunConfig.max_cost`**(§5.4、§2.3、§2.4);"循环自动终止" → LoopDetector(§5.4)。
- **Tiered Model Routing → per-Skill `model.prefer` + ProviderManager 前缀路由**(§2.1、§4.2);"子任务专家模型 + sub-agent 协作" = 子技能帧用不同模型(§3.3)。
- **上下文累积效应与工具结果重复计费 → 第 7 章压缩**;尤其 `spill`(§7.2)与结果归一化大小封顶(§8.1)正是针对"5,000 token 网页每轮重复计费"。
- **KV Cache 稳定前缀 → `pinned` 不变量**(§7.3 不变量 1)+ `collapse_child`(§7.2,子帧 transcript 折叠直接消灭重复计费源)。
- **原子工具要求 → Tool 定义**(§2.2"无调用栈、无 LLM 循环、单次进出");组合能力上收 Skill,与本章工具原子化逐字对应。
- **工具失败给清晰错误信息 → 错误观察语义**(§3.2)。
- **action legality rate → 分发流水线的可计数事件**:schema 校验失败、权限拒绝、Veto 都是显式信号事件(§8.1),离线可算。
- **轨迹 vs 结果双重覆盖 → 帧 `result` 是自报的**(§2.3);outputs schema 校验只管类型合法(§2.1),语义正确性留待评测层独立状态检查——分工与本章一致。
- **仿真环境 / RLVR → Logic Kernel 沙箱 + MCP 适配器预留**(§9、§8.3);validator 可作为 code 技能或沙箱程序执行,契约未封死。

---

## 一致与相互印证

1. **预算与成本治理独立收敛**。本章从生产经验得出"分维度记账 + 每任务成本封顶 + 循环/过深探索自动终止",与 BudgetGuard + `max_cost/max_steps/max_wall_time` + LoopDetector(§2.4、§5.4)逐条对应——这层运行时治理不是过度设计。
2. **压缩与 spill 的价值被成本模型量化证实**。"上下文累积效应"与"工具结果重复计费"给出精确成本归因,正是 `collapse_child` 与 `spill`(§7.2)存在的理由;"压缩是输入侧三大杠杆之一"支持第 7 章作为一级子系统的地位。
3. **分层模型路由被双重背书**。本章"轻量模型跑简单请求、强模型保复杂任务、专家模型经 sub-agent 协作"正是 manifest `model.prefer` + 前缀路由(§2.1、§4.2)要实现的能力;本章补上了它的评测方法论(选型维度 + 显著性检验)。
4. **原子工具 + 高层组合入 Skill 的分层被评测侧独立要求**。本章评测环境"工具原子化逼迫 Agent 规划组合"与我们 Tool/Skill 分层(§2.1/§2.2)完全一致;也意味着我们的工具集天然适合搭建本章式评测环境。
5. **错误观察而非失败标志**。本章"工具失败给清晰错误信息让 Agent 学习"与 §3.2 逐字对应。
6. **否决机制两侧同构**。rubric 第四原则的 veto 项、安全指标零容忍,与 SYNC sidecar fail-closed veto(§5.3)是同一"底线不可交易"思想;我们的 Verdict 模型已把它机制化。
7. **结果验证独立于自报**。本章"说了没做"的盲区支持我们的克制:outputs schema 只验类型(§2.1),语义验证交给评测环境的状态检查,内核不假装能判语义对错。
8. **评测基础设施架构期内置**。OpenClaw 的消融开关教训印证公理 2(子系统互不相识、契约层中介,§1、§11.1)的方向:可替换、可缺席是消融能力的前提。

---

## 差距与可借鉴点

1. **评测子系统整体缺位——本章最大提醒**。../DESIGN.md 只有内核自身测试策略(§13),没有"评测跑在内核上的 Skill/Agent"的任何设施。本章立场:消融、flag、prompt 回归必须架构期内置,事后加装代价高。我们虽不必照搬产品级 feature flag,但 M5 之后"评测模式"缺位会立刻显现。
2. **Usage 记账字段不足以支撑成本归因**。本章要求区分:input/output 分开计价、**cache read/write**(约 0.1×/1.25× 输入价)、**thinking tokens**(不可见但计费)、TTFT 与总延迟、每个工具返回的 token 体量。我们的 `Usage(steps, tokens, cost)`(§2.3)与 `ChatResponse.usage{prompt, completion, cost}`(§4.1)过粗,`ProviderCaps`(§4.1)也未声明 cache/thinking 能力。没有这组字段,"哪个 Skill/工具是成本大头"无从回答,本章实验 6-7 的成本基线无法建立。
3. **Trace 未对齐标准协议**。本章明确 OpenTelemetry + OpenInference 的价值在采集/分析解耦、避免厂商锁定;TraceRecorder 目前是私有 JSONL(§5.5)。帧树本就是 span 树,补 OTLP/OpenInference 映射成本极低,收益是直接接入 LangSmith/Phoenix 生态与"trace 回流评测资产"通道。
4. **过程指标无汇聚点**。action legality rate、path efficiency、回溯频率都能从信号流算出,但设计中没有任何指标汇聚定义(哪怕一个 ASYNC MetricsCollector)。LoopDetector(§5.4)已是 path efficiency 的在线特例,说明信息足够,只差汇聚语义。
5. **统计显著性与多次采样无抓手**。`RunConfig`(§2.4)没有 seed/temperature 钉死等复现性支持,也没有"n 次运行 + 均值/离散度汇总"的批量入口。可在内核外 eval harness 实现,但设计文档应指明归属,否则无人认领。
6. **评审工程质量知识未覆盖**。judge calibration(kappa 门槛)、位置偏置(交换顺序)、长度偏置(罚冗长)、多源异构评审——若提供 judge skill 示例,这些应写进示例而非留给用户踩坑。
7. **动态/条件化模型路由缺失**。H4(全局开 thinking 被拒)→ H7(按任务特征条件启用)表明静态 `model.prefer` 不足以表达"按任务特征路由";ProviderManager(§4.2)目前只有静态 prefer + fallback,无路由决策扩展点。
8. **环境状态与 reset 语义在视野外**。StatefulToolEnv 的可重置、RLVR 的训练级 reset,都在我们的无状态 Tool 模型(§2.2)之外。v1 可不支持,但 §14 非目标应点名,避免被误读为已覆盖。
9. **"先查评测系统"的自检纪律**。golden-file + 故障注入(§13)假设 harness 正确;本章列举评测侧典型故障(环境杀进程、scorer bug、用例漂移)。对压缩不变量测试、MockProvider 脚本同样适用——测试设施本身也需要被测试。
10. **feature debt 对压缩默认策略的拷问**。"随模型进化,曾有效的功能可能不再必要"直接适用于默认 `hierarchical` 压缩(§7.2):大窗口时代,有损压缩是否仍划算应可关闭、可消融验证,而非写死为默认最佳。

---

## 冲突或不同取舍

1. **Binary reward vs 多维 rubric**。τ-bench 为算 Pass^k 把多层校验坍缩成 0/1;rubric 派用四级多维打分换诊断力。我们的帧级结果本质二值(`result`/`error`,§2.3)。非冲突而是分工:内核保持二值(决定弹栈/上抛),多维评分属评测层。成立条件:回归用二值 + Pass^k;能力诊断用 rubric。
2. **压缩默认开启 vs 可消融**。manifest 默认 `hierarchical`(§2.1、§2.4)隐含"压缩总是需要";本章 feature debt 概念提示某些模型/任务下"不压缩"质量更优。各自成立条件:长任务、工具结果庞大 → 压缩必需;短链路高质量场景 → 应允许 `off`。代价:多一个配置维度需测试,但 `off` 档位实现近零成本。
3. **静态模型声明 vs 动态路由**。静态 `model.prefer`(§2.1)换来可预测、可静态分析、golden-file 可测;H7 式条件路由换来成本/延迟最优但引入运行时方差(同一 skill 不同任务走不同模型,评测噪声变大)。静态对内核正确;动态作为 ProviderManager 后续扩展点,不进 v1 契约。
4. **串行默认 vs 评测高吞吐**。本章追求大规模并行(AWorld 14.6x、OSWorld-Verified 50x);我们 §3.4 默认串行。不冲突:评测并行是"多个独立 Run 并发",Run 间无共享状态(预算、信号、trace 按 run_id 隔离,§2.4、§5.5),单进程 asyncio 即可拉起;分布式执行已被 §14 显式列为非目标,与 AWorld 的取舍一致。
5. **人工抽查 vs 自动化优先**。本章坚持自动化之上仍要人工抽查 + 对抗审查;我们 §13 全自动化。边界应写清:内核行为确定性可测 → 自动化;技能质量评估 → 人机在环。
6. **评测环境有状态 vs 工具无状态**。本章要求环境可 reset、状态转换遵循业务逻辑;我们 Tool 无全局状态(§2.2)利于沙箱化与重试。各自成立:有状态评测环境作为工具的宿主/外部服务存在,内核无需知情。

---

## 行动建议

按优先级排序,标注影响面:

**P1(v1 契约定型前必须做)**

- **P1-1 扩展记账字段** — 影响子系统 Providers / Sidecars;设计文档 §2.3 `Usage`、§4.1 `ChatResponse.usage` 与 `ProviderCaps`、§4.2 记账、§5.4 BudgetGuard。
  usage 增加 `cache_read_tokens` / `cache_write_tokens` / `thinking_tokens`;响应增加 TTFT 与总延迟;工具结果归一化时记录原始 token 体量(§8.1 已在此处做大小封顶,顺手记账)。
  理由:本章成本分析的最小数据前提;契约层 v1 定型前补齐字段成本低,错过窗口期后补是破坏性变更。
- **P1-2 TraceRecorder 增加 OpenTelemetry/OpenInference 映射** — 影响 Sidecars;§5.4/§5.5、扩展点 §11.3。
  帧 = span,LLM/工具/子技能调用 = 子 span;保留 JSONL 为 baseline。
  理由:帧树与 span 树同构,映射是机械工作;换来分析生态与 trace 回流评测资产的通道。
- **P1-3 以示例交付"评测模式"三件套,不动内核** — 影响 §12 `examples/`、§13 测试策略。
  (a) judge skill:rubric 四原则模板 + `outputs` schema 结构化打分 + 交换顺序的 pairwise 变体;
  (b) user-simulator skill:progressive information disclosure 脚本化模拟器;
  (c) 多源评审示例:同一 judge 在不同 `model.prefer` 下运行并汇总一致性。
  理由:Skill 抽象表达力的直接验证;也是回应 §14 开放问题 2(压缩保真基准)的方法论雏形。

**P2(M5 前后纳入)**

- **P2-1 `RunConfig.compression` 增加 `"off"` 档,文档化"裸模型基线"构建方式** — §2.4、§7.1、§11.2。
  明确"KernelBuilder 不挂压缩/不挂某 sidecar"即消融开关,给出对照实验操作手册。
  理由:本章"消融基础设施必须架构期内置"的核心告诫;模块化已具备九成能力,只差显式档位与文档。
- **P2-2 ProviderManager 预留动态路由扩展点** — §4.2、§4.3。
  定义 `ModelRouter` 协议(输入任务特征/帧摘要,输出模型与参数),v1 以静态 prefer 作默认实现。
  理由:承接 tiered routing 与条件化 thinking(H7)方向,不破坏 v1 静态可分析性。
- **P2-3 压缩保真评测基准落地** — §7.2 `summarize`、§14 开放问题 2。
  固定评估集 + judge skill + 配对显著性分析(McNemar),作为 spill/summarize 策略(M5)验收附件。
  理由:本章正好提供开放问题 2 缺失的方法论:多次运行、配对检验、噪声带宽内不做决策。

**P3(文档与边界)**

- **P3-1 MetricsCollector(ASYNC sidecar,内置表增补)** — §5.4。离线/在线汇聚 action legality rate、path efficiency、回溯频率。信号流信息已足,低成本补齐过程指标。
- **P3-2 §14 非目标点名评测环境状态与 RLVR** — §14。显式声明 stateful/resettable 评测环境、仿真环境、训练对接为 v1 非目标,并指出契约层(沙箱、MCP 适配器)未封死该方向。
- **P3-3 eval harness 脚本归属写明** — §13。n 次重复运行、种子/温度钉死、均值与离散度汇总、标准误计算,放内核外 `tests/` 或独立工具包。统计显著性是本章决策方法论的地基,且明确不该进内核。
