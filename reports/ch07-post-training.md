# 第 7 章《Model Post-Training》对比报告

> 评审对象:`ai-agent-book/book-en/chapter7.md`(771 行,已全文通读)
> 对照文档:`DESIGN.md` v0.3(Agent OS 内核与子系统设计)
> 总体判断:**关联度中等偏低,但有三个高价值接口点。**
> 本章主体(SFT/RL 算法、RLHF、LoRA、PPO/GRPO 推导)是模型训练侧内容,与运行时内核设计基本正交;
> 但**奖励工程(RLVP)、环境工程(仿真沙箱)、轨迹数据(rollout 与 loss masking)** 三节与内核存在具体、可操作的映射,值得吸收。

---

## 章节内容概要

本章回答"如何改变模型权重,把能力烘进参数",处于全书公式 Agent = LLM + Context + Tools 中的 LLM 一环。

两条主线贯穿全章:

- 主线一:**"SFT memorizes, RL generalizes"**——由 GeneralPoints 实验(arXiv:2501.17161)在纯语言与视觉双模态定量验证。
- 主线二:**"数据与环境比算法更重要"**——真实优先级是 prior(base model)> environment > algorithm。

### 关键概念与机制清单

**三阶段全景与顺序约束:**

- Pre-training:Next Token Prediction(NTP),奠定语言规则与世界知识,成本最高。
- SFT:与预训练数学上是同一任务,差异只有两点——数据换成"输入-输出"演示对、**loss masking**(损失只算 response 部分)。
- SFT 固化的是 **protocol knowledge**(格式、风格、流程),不是 factual knowledge(后者靠继续预训练或 RAG)。
- RL:不给标准答案,最大化期望奖励,学可迁移策略。
- **"SFT first, then RL"**:RL 的奖励函数必须先能解析模型输出(结构化 JSON / 工具调用),SFT 先立"形"(格式稳定),RL 再求"神"(策略泛化)——"form first, spirit second"。
- 边界条件:足够强的 base model 可跳过 SFT 直接 RL(DeepSeek-R1-Zero),但输出可读性差,R1 最终仍补 cold-start SFT。

**SFT 与 RL 的本质差异(第 7.1 节,全章最重要的一张表):**

- SFT 优化最大似然,是 **mass-covering**(摊平覆盖演示中的所有 mode);RL 的 KL 约束形式对应 **reverse KL**,是 **mode-seeking**(集中于少数高奖励 mode,果断丢弃其余)。
- RL 是 online 方法,带来三个 SFT 不可能有的优势:
  - 上限由任务决定而非演示者(60 分老师教不出 90 分学生,RL 可发现演示中没有的更优策略,如 SimpleVLA 的 "pushcut" 动作);
  - **verification-generation asymmetry**:验证比生成容易(数学对答案、代码跑测试),这是 RLVR 的力量来源;
  - 在自身分布上训练,避免 **covariate shift**(纯模仿误差随轨迹长度 ~T² 累积,online 可压到 ~T)。

**RLHF 与 RLVR:**

- InstructGPT 三阶段:SFT → Bradley-Terry reward model(成对偏好比较训练打分器)→ PPO。
- **per-token KL penalty** 混进奖励信号(非独立 loss 项),同时防 reward hacking 与 distribution collapse。
- **reward model over-optimization**:Goodhart 定律,代理奖励单调上升时真实质量先升后降;KL 惩罚与 early stopping 是常用缓解。
- DPO:跳过显式 RM,把偏好对直接变分类损失;简单但完全 offline,上限由偏好数据覆盖度决定。
- RLVR:用规则验证器(测试是否通过、答案是否正确)替代学习型 RM;Agent 任务大多可验证,故全章以 RLVR 为主线。

**算法对比(第 7.8 节):**

- REINFORCE / PPO(clip 限制步长 + value network + GAE 细粒度信用分配)/ GRPO(组内相对比较估优势,无 value network,便宜但信用分配粗)。
- DPO / KTO(offline 偏好优化)/ Best-of-N(推理时方法,不改权重)。
- 严格区分 on-policy(只用当前策略新采样数据)与 off-policy;SFT 是 off-policy 模仿学习,PPO/GRPO 标准形态是 on-policy。
- 结论:会用现成算法即可,成败在数据与环境;veRL、TRL 等框架已封装,换算法只是改几行配置。

**奖励设计(多轮信用分配,第 7.9-7.10 节):**

- 奖励两个设计维度:**Density**(binary / sparse / process reward)与**形式**(scalar / semi-scalar / **vector** / **generative**)。
- PRM(Process Reward Model,逐步打分,OpenAI "Let's Verify Step by Step")vs ORM(只看最终结果);RLVR 的规则验证器可视为 ORM 的特例。
- 生成式奖励模型:自动归纳评估原则 → 逐条评估执行过程 → 校验评估本身的准确性;透明、可 inference-time scaling、可与策略模型共同进化。
- 工程折中:**turn-level credit assignment**(比 token 级便宜,比轨迹级细),是当前多轮 Agent RL 框架的常见妥协;多轮场景 γ 通常直接取 1。

**RLVP(第 7.10 节,全章与内核设计最相关的机制):**

- 一句话配方:**"reward the outcome, penalize the path"**(R = O + β·Φ)。
- 核心洞察:真实环境是**非对称验证器**——判定"坏动作"(`rm -rf`、未满足前置条件、改测试文件)廉价且确定,判定"是否有进展"昂贵易错;故环境能可靠提供的稠密信号本质上是**路径上的惩罚**,而非进展奖励。
- Φ 由**确定性规则引擎**(纯函数判定"动作 + 动作前状态",非学习型 judge)逐动作给出:违规 −λ;合规/可达进展 +μ(partial credit)。
- 四条设计原则(各有 ablation 支撑):
  1. 只惩罚可机器验证的"动作",绝不惩罚"无进展"(否则最优策略变成什么都不做);
  2. 结果奖励永远是主驱动——纯惩罚导致 **inaction trap**,成功率在所有种子上塌缩到 0;
  3. 每个 −λ 配对相应 +μ(给合规出路,而非只堵路);
  4. 合规路径必须可达,违规判定必须 un-gameable(不能用学习型 compliance 打分器,否则投机问题只是从策略转移到 judge)。
- 机理统一解释:**GRPO 的优势本质是组内方差**;全败组(训练早期)/全成组(训练晚期)方差为零,产生不了梯度(zero-variance deadlock)。
- 可验证惩罚总能恢复方差(坏动作便宜可查);进展奖励受 **reachability** 门控(定理证明可达、软件修复常不可达)。
- 实验数据:TerminalBench 上违规率 3.71 → 0.66(约 6 倍)且任务成功率无损;miniF2F 上收敛迭代数 7.0 → 4.4;全败组占比 65% → 8%。

**工具调用 RL(第 7.11 节):**

- 三级难度:单工具(时机与参数)→ 多工具选择 → **工具链编排**(前置依赖、互斥约束、成本差异下的全局规划)。
- ReTool:代码解释器进 RL 循环,`<code>`/`<interpreter>` 标签交错;**environment feedback tokens 必须 loss masking**——否则模型被训练去"预测沙箱输出",偏离优化目标且训练不稳;veRL、AWorld 均内建此机制。
- AWorld:MCP server 沙箱(26 servers / 126 tools);真实 API 有限流、封号、副作用,**训练必须先建"stable, controllable, replayable"的 shadow world**。
- DAPO 四项改进:Clip-Higher(放宽探索上界)、token-level policy gradient loss(每 token 等权)、dynamic sampling(弃全对/全错提示)、overlong reward shaping(惩罚冗长)。

**样本效率前沿(第 7.12 节):**

- **On-Policy Distillation**(Thinking Machines 2025):学生自己跑轨迹(on-policy,解决分布失配)+ 教师对每个 token 给完整概率分布(逐 token KL 对齐,dense signal,解决奖励稀疏)。
- 效果:达到纯 RL 性能只需约 1/10 训练步;长链推理优势最明显;前提是仿真环境足够真实。
- 作者亲历案例:Agent "时间感"训练中 DPO 与四种 RL 配方先后失败(稀疏、代理指标错位、rollout 形状失配、模式塌缩),换 On-Policy Distillation 后收敛,四种条件通过率提升 23-47 个百分点。

**工程判断与陷阱:**

- 环境保真度决定训练成败("A distorted environment means a dead policy");建高保真环境的工程量常超过训练本身。
- 数据质量三维度:coverage(覆盖长尾)/ diversity(多样性)/ annotation accuracy(标注正确性);rejection sampling / RFT / STaR 是拉满准确率的标准管线。
- "多数场景 SFT 数据够好就不需要 RL"(Anthropic 2025 年前以高质量 SFT + RLAIF 为主为例)。
- 8 条常见陷阱:用 post-training 记事实(应交给 RAG)、格式未稳就上 RL(解析失败率 >20% 则 RL 必败)、reward hacking、忽视仿真保真度、过训练、value function collapse、低估 RL 算力成本(10-100 倍于 SFT)、低质数据。
- AdaptThink:RL 学"何时不思考",响应长度降 45-64% 而精度不降;prompt/CoT distillation 的适用边界(产品形态稳定才值得蒸馏,探索期保留显式 prompt)。

---

## 与 Agent OS 设计的映射

本章是训练侧内容,大部分概念在我们的架构里没有对应物(也不需要)。存在真实映射的有四处:

**映射 1:内核 agent loop = RL rollout 结构(§3.1)。**

- 本章明确指出多轮交互的物理形态"正是第 1、4 章的 ReAct 循环——Think → Act → Observe"。
- 我们"内核拥有循环"(§1 公理 1)使每一步都经过内核,这正是训练侧所需的 rollout 产生器。
- TraceRecorder(§5.4)落盘的 `traces/<run_id>.jsonl` 本质上就是轨迹数据集,对应本章"τ²-bench 的完整轨迹记录为模仿学习提供数据"的论断。

**映射 2:RLVP 的 Φ 规则引擎 = 我们的 SYNC sidecar / ToolGuard(§5.2、§5.4、§8.1)。**

- 本章的"确定性规则引擎逐动作判定 violation"与我们的 `pre:tool.call` 同步否决链同构。
- ToolGuard 的规则表(工具名/参数正则,§5.5)就是 Φ 的推理时形态;两个文档甚至用了同一组例子(`rm -rf`、改测试文件让测试通过)。
- 三层权限模型(§8.2:工具等级 ∩ manifest 白名单 ∩ RunConfig 上限)对应 RLVP 约束的部署形态。

**映射 3:outputs JSON Schema 校验 = RLVR 的规则验证器(§2.1、§3.1 步骤 5)。**

- 本章"verification-generation asymmetry"是 RLVR 的力量来源。
- 我们把 outputs 校验失败作为错误观察回写父帧(§2.1、§13 风险表),正是"验证比生成容易"的运行时运用。
- Skill 的 `inputs/outputs` schema 同时是运行时闸门与潜在的奖励信号源。

**映射 4:MockProvider + 沙箱 + blob store = "shadow world" 雏形(§4.4、§9.2、§8.4)。**

- 本章强调真实 API 有限流/封号/副作用,训练必须先在"稳定、可控、可重放的影子世界"进行。
- 我们的 MockProvider 脚本化应答 + 故障注入(§4.4)、PythonSandboxLogicKernel(§9.2)、blob store(§8.4)、MCP 适配器规划(§8.3),与 AWorld 的 MCP 沙箱训练环境是同一思路的运行时版本。

**无映射(也无需映射)的部分:**

- PPO/GRPO/KL 惩罚/LoRA/value network/GAE/dynamic sampling 等训练算法本体。
- On-Policy Distillation 的教师 logit 分布(我们的 Provider 是黑盒 API)。
- VLA、action chunking 等机器人内容。

---

## 一致与相互印证

**印证 1:"先 Harness 工程,再考虑训练"验证了我们项目的存在性前提。**

- 本章决策框架第一条:"先问:需要 post-training 吗?如果问题能通过 Harness 工程(prompt、工具设计、上下文管理)解决,就不需要训练——**大多数 Agent 应用落在这里**。"
- Agent OS 正是 harness 层的内核基础设施,本章为"内核优先于微调"的投入顺序提供了训练侧背书。

**印证 2:"一切特权操作可拦截"(§1 公理 3)被 RLVP 的前提独立验证。**

- RLVP 的立足点是"坏动作可廉价、确定地机器验证"——这正是我们把 ToolGuard 做成确定性规则表(§5.5)而非 LLM judge 的同一判断。
- 本章原则①"只惩罚可验证的动作"与原则④"判定必须 un-gameable、不能用学习型 compliance judge",直接支持我们 seccomp 式的 SYNC sidecar 设计,反对"用另一个模型审查行为"的方案。

**印证 3:"form first"支持 outputs schema 运行时强校验 + 修复循环。**

- 本章:格式不稳(JSON 解析失败率 >20%)时 RL 完全失败,SFT 必须先立"形"。
- 我们在 §2.1 把 outputs schema 校验做成硬约束、§13 风险表给出"校验失败 → 错误观察回写,最多 N 次修复"的机制,是同一约束在推理时的对应物:内核在运行时强制"形",训练在权重里固化"形"。

**印证 4:错误观察设计(§3.2)与 covariate shift 分析互相印证。**

- 本章:纯模仿的模型进入训练数据外的状态后不知如何恢复,误差沿轨迹累积;online 方法让模型"在自己要走的路上练习,学会从自己的错误中恢复"。
- 我们把工具失败作为错误观察写入帧上下文、让 LLM 自行恢复(§3.2),正是推理时的"从错误中恢复"机制。
- LoopDetector 先 `inject_message` 纠偏、再犯才 `stop`(§5.4)也是同一哲学。

**印证 5:压缩不变量 2(tool_call/tool_result 配对原子性,§7.3)与训练数据合法性要求一致。**

- 本章 ReTool 的 loss masking 依赖轨迹中严格区分"模型生成 token"与"环境返回 token"。
- 我们的原子组结构性保证配对同进同出(§7.5),天然产出形态合法、可正确 mask 的轨迹;朴素压缩器的孤儿 tool result 问题在训练侧同样是毒药。

**印证 6:MockProvider + 故障注入的测试策略(§4.4、§13)与"环境保真度"论断同向。**

- 本章:环境失真 = 策略死亡,建高保真环境的工程量常超过训练本身。
- 我们的 golden-file + 429/500 注入 + 夜间真实 API 冒烟(§13 测试策略)是同一原则在测试侧的缩小版。

**印证 7:MCP 适配器规划(§8.3)与 AWorld 的 MCP 沙箱趋势吻合。**

- 训练侧主流已经在 MCP server 沙箱上建环境;我们"把 MCP server 工具桥接进注册表、不进内核"的边界划分与之一致。

**印证 8:成本观一致。**

- 本章 AdaptThink(学"何时不思考",长度降 45-64%)、DAPO 的 overlong reward shaping(惩罚冗长),与我们 BudgetGuard(§5.4)、`max_steps`/`max_cost`(§2.4)的运行时预算仲裁是同一关注点的两面。

---

## 差距与可借鉴点

**差距 1:TraceRecorder 的产物不是"训练就绪"的轨迹格式(影响 §5.4 / §5.5)。**

- 本章把轨迹数据视为一等资产(rollout → rejection sampling → RFT;τ²-bench 轨迹供模仿学习),并指出两个硬性技术要求:轨迹必须区分模型 token 与环境 token 以做 loss masking;训练/评估数据必须严格隔离。
- 我们的 TraceRecorder 目前只记"每条信号一行 JSONL 摘要",不含完整消息体与 provenance 元数据,无法直接导出为 SFT/RL 数据。
- 价值:内核是全系统唯一能看到完整轨迹(含子帧折叠前 transcript)的位置;补齐导出能力后 Agent OS 可兼任"rollout 基础设施"——这是本章指明的高杠杆方向,且成本只落在一个 sidecar 上。

**差距 2:缺少"可验证信号"的统一抽象(影响 §5.1 信号目录 / §8.1)。**

- RLVP 的 Φ(违规 −λ、合规 +μ、可达进展 partial credit)提示:我们的 `pre:tool.call` Veto 只利用了信号的"阻断"用法,没有"评分"用法。
- 同一套确定性规则完全可以双用途:运行时 Veto 坏动作,训练时输出 −λ 惩罚;我们缺一个把 sidecar 判定结果结构化为标量/向量奖励的出口。
- 价值:RLVP 实验证明这种信号几乎免费地把违规降 6 倍并恢复组内方差;设计上只需在 Verdict 之外增加一类 observe-only score 语义,不动 SYNC 关键路径。

**差距 3:ToolSpec 元数据不足以支撑工具链编排策略(影响 §2.2 / §8)。**

- 本章工具 RL 第三级难度明确指出多工具场景需要处理**前置依赖**(先搜索才能浏览具体页)、**互斥约束**、**成本差异**。
- 我们的 ToolSpec 只有 permission / idempotent / timeout,没有 depends_on / conflicts_with / cost 注解。
- 价值:这些注解静态可分析,既能让内核在分发期提前拒绝不可行调用序列,也是训练侧工具选择奖励设计的输入;与 manifest 的 permissions 白名单(§2.1)是同类静态信息,加入成本低。

**差距 4:无 Best-of-N / 验证器选优的一等原语(影响 §3.4)。**

- 本章把 Best-of-N 列为推理时方法:"早期快速提升质量、为 RL 提供奖励上界估计"。
- 我们的 `parallel_invoke` 扇出 N 个分支后,没有"由验证器选优汇合"的内建模式;而 outputs schema 校验器(§2.1)天然就是这个验证器。
- 价值:小改动即可支持一个常用且被本章背书的推理时优化模式。

**差距 5:Provider 契约无 logprobs / 采样分布能力声明(影响 §4.1 ProviderCaps)。**

- On-Policy Distillation 与 rejection sampling 需要教师模型的 token 级分布或至少多候选采样;ProviderCaps 目前没有相关 capability 位。
- 价值低但接口预留便宜,符合"契约层为替换预留"的既有风格(§11.1)。

**差距 6:"评估环境即训练环境"对我们测试资产的镜像提醒。**

- 本章 SWE-Gym 之于 SWE-bench:评估环境可改造为训练环境,但评估数据必须隔离。
- 我们的 golden-file 测试与 `web_research` 集成锚点(§13)未来若规模化,可复用为 SFT 数据管线(任务分布定义 → 批量生成 → 规则过滤 → 去重配比),本章给出了现成管线模板。
- 反向纪律同样成立:golden file 一旦用于训练即失去评估效力,应显式标注"评估专用"。

---

## 冲突或不同取舍

**取舍 1:压缩即丢弃 vs 训练需要完整轨迹。**

- 我们的 Context Compression(§7)刻意丢弃/摘要历史以控制窗口;训练侧要求轨迹完整保真——若导出的轨迹取自压缩后上下文,等价于在错误的状态分布上训练。
- 评估:两者其实不冲突。压缩作用于发给 Provider 的请求,TraceRecorder 作用于信号流;只要训练数据从信号流(压缩前)导出,两目标同时满足。
- 但这要求 TraceRecorder 记录**请求级原始报文**而非压缩后视图;当前设计(§5.5 "payload 摘要")未明确这一点,值得写进文档。

**取舍 2:稠密过程奖励 vs 我们的稀疏运行时闸门。**

- 本章主张信号越稠密越好(partial credit、PRM);我们的 sidecar 哲学是"不违规即沉默",只有 Veto / Stop 等稀疏干预。
- 评估:层次不同,各自成立。运行时干预必须稀疏——每次干预都打断 agent loop,SYNC 路径有 2s 超时预算(§5.3);稠密评分应落在 ASYNC 观察通道与导出数据里,而非关键路径。
- 这恰好与 RLVP "惩罚在训练时、护栏在运行时"的分工一致。

**取舍 3:轨迹级信用分配 vs 帧树粒度记账。**

- GRPO 把轨迹级优势均摊到所有 token(信用稀释),PPO 用 value network 细分配,工程折中是 turn-level。
- 我们的 SkillFrame 树(§2.3)按调用边界天然切分"回合",每帧独立 usage 记账(§3.1 步骤 7)。
- 评估:无冲突。帧粒度是比 turn 更规整的信用分配单元;若未来做 agent RL,帧树可直接用作 turn-level credit assignment 的结构——这是设计无意间预留的红利。

**取舍 4:本章默认"模型可被改变",我们默认"模型是黑盒 API"。**

- 全章前提是能拿到权重/采样分布;我们的 Provider 契约(§4.1)只到 chat / stream。
- 评估:这是定位差异而非错误——嵌入式库面向 API 用户。差距 5 的 logprobs 预留已覆盖少数自建模型场景,不值得为训练场景改变 Providers 子系统的主契约。

---

## 行动建议

按优先级排列(P0 = 建议进设计文档,P3 = 仅记录备选):

**P0 — 训练就绪的轨迹导出(影响 §5.4 TraceRecorder / §5.5 baseline)。**

- 在 TraceRecorder 条目中补充:记录完整请求/响应报文(压缩前)、消息 provenance(assistant / tool_result / system,支撑 loss masking)、帧树结构。
- 声明其目标是"可导出为 SFT/RL rollout 数据"。
- 同时加一条纪律:golden-file 测试集标注"评估专用",呼应本章训练/评估隔离原则。
- 成本集中在一个 sidecar,不动契约层。

**P1 — ToolSpec 增加编排元数据(影响 §2.2 / §8.1)。**

- 为 ToolSpec 预留 `cost`、`depends_on`、`conflicts_with` 可选字段;baseline 可不实现检查逻辑,仅入契约。
- 依据:本章工具 RL 第三级难度(依赖/互斥/成本)与 manifest 静态白名单同构;先入契约可避免后续破坏性变更(契约层破坏性变更只允许跨大版本,§11.1)。

**P1 — 明确"可验证信号"双用途的叙述(影响 §5.1 信号目录 / §8.2 权限模型)。**

- 在 Sidecars 一章补一段:SYNC sidecar 的确定性规则既是运行时护栏,也是训练时奖励整形(reward shaping)的信号源,对应 RLVP 的 "reward the outcome, penalize the path"。
- 不改代码,只把对应关系写入设计理由,为后续 RewardEmitter 类扩展留位。

**P2 — Best-of-N 汇合模式(影响 §3.4 并发)。**

- 在 §3.4 补一句:`parallel_invoke` + outputs 校验器可组成 Best-of-N(扇出 N 分支、按 schema 校验/评分选优),列为后续内置模式而非新子系统。

**P3 — ProviderCaps 预留 logprobs capability 位(影响 §4.1)。**

- 一行契约预留,服务未来 distillation / rejection sampling 数据收集,不实现。

**P3 — 帧树粒度信用分配的备注(影响 §2.3 或 §14 开放问题)。**

- 记录"SkillFrame 树天然适配 turn-level credit assignment"作为开放问题之一,供未来 agent RL 方向参考。

**不建议采纳:**

- 任何把训练算法(PPO/GRPO/KL/LoRA)、教师 logit 蒸馏、VLA 相关机制引入内核的想法——超出嵌入式库边界,与 §14 非目标一致。
