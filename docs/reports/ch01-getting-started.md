# 对比报告:book-en/chapter1.md《Getting Started with AI Agents》 × Agent OS ../DESIGN.md

> 评审对象:全书第 1 章(导论章,467 行)。对照基线:../DESIGN.md v0.3。
> 总体关联度:**高**。本章是全书的"概念地图",提出 Agent = LLM + Context + Tools 与 Agent = Model + Harness 两个公式,以及 Harness 五要素(Context / Tools / Constrain / Verify / Correct)。Agent OS 整个内核就是一个 Harness 的系统化实现,本章几乎每个概念都能映射到我们的子系统或公理。

## 章节内容概要

本章是导论,从五个产品实例(Cursor、Deep Research、Manus、豆包手机助手、Pine AI)倒推出 Agent 的核心组成,再讲工程化可靠性。关键概念与机制清单:

- **核心公式一**:`Agent = LLM + Context + Tools`(推理引擎 + 工作上下文 + 行动接口),并给出与 RL 概念(Policy / Observation Space / Action Space)的对应表。
- **工具五分类**:Perception(感知)、Execution(执行)、Collaboration(协作,含子代理委派与人工确认)、Event Trigger(事件触发,如邮件/Webhook/定时器,Agent 不主动调用而是被激活)、User Communication(用户通信)。强调工具设计的 ACI(Agent-Computer Interface)视角与 Poka-yoke(防错)理念;主张**通用工具 + 沙箱**(给代码解释器而非专用计算器)。
- **Tool Calling 四步**:声明工具 → 模型决策 → 结果入上下文 → 模型继续。这是 ReAct 的基础。
- **Model as Agent 范式**:模型经 RL 内化"何时调、调哪个、传什么参"的**决策策略**,但工具执行仍在模型之外(框架侧或 API 服务端内置工具,如 Kimi K3 的 Formula 引擎、GPT-5.6 的 Responses API 内置 web_search/code interpreter);"编排循环没有消失,只是从客户端移到了服务端"。附带 Freeform Tool Calling(免 JSON 转义的原始文本参数)与 GPT-5.6 的**意图澄清(intent clarification)**机制。
- **三种学习范式**:Post-training / In-Context Learning(本质是模式匹配而非真学习)/ Externalized Learning(知识外化为知识库与工具代码)。
- **上下文五组件**:System Prompt、Tool Definitions(合称 **static prefix**)、User Messages、Assistant Messages(`reasoning`/`content`/`tool_calls` 三部分)、Tool Results(合称 **trajectory**)。即 **Agent context = static prefix + trajectory**。
- **Experiment 1-1(消融实验)**:去掉 Tool Definitions → 完全无法行动;去掉 Tool Results → 收不到反馈,**陷入无限循环**;去掉 reasoning → 连续决策自相矛盾;去掉消息历史 → 任务重来、重复已做步骤。
- **ReAct loop**:reason → act → observe 循环;trajectory 可累积、结构化、可解释,且是数据资产(可蒸馏进知识库、用于 RL 训练)。思考题点出 trajectory 全量重发的**二次方成本**问题。
- **Harness Engineering**:`Agent = Model + Harness`;Harness = Context + Tools + **Constrain + Verify + Correct** 三层保障。五要素各有核心原则:Information Sufficiency、Clear Interface、**Fail-Safe Defaults**(默认全关、显式开启)、**Input Isolation**(校验只看结构化数据、不信模型自由文本,防 prompt injection)、Correct 层"**故障未确认不可恢复前不暴露中间态**"(静默重试、熔断、连续失败转人工)。引用 Claude Code 实践:进程状态管理、多层上下文压缩、权限分级、**Circuit Breaker**、错误恢复。
- **工程范式演进**:Software → Prompt → Context → Harness → **Loop Engineering**(跨单次运行的持续自治:谁发现下一件工作、何时算真正完成,第 10 章展开)。
- **Anthropic 三原则**:Keep it simple(抽象层是调试盲区)、Keep it transparent(轨迹可见是信任前提)、ACI 工具接口。
- **编排模式**:Workflow(确定性代码路径,安全、攻击面限于单节点,但不灵活)vs Autonomous Agent(运行期依环境反馈决策,**必须设计显式停止条件**:任务完成/达最大轮次/不可恢复错误);实践中两者混用(如 n8n)。附主流框架对比表(OpenAI Agents SDK、Claude Agent SDK、LangGraph、n8n、Dify、CrewAI、OpenClaw)。
- **Guardrails 三类**:输入侧(relevance/safety classifier、内容审核、规则防护)、执行侧(**tool risk rating**,按可逆性/权限/财务影响分 low/medium/high,高风险需人工确认)、输出侧(PII 过滤、输出校验)。代表实践:Anthropic **Constitutional Classifiers**(自然语言"宪法"生成训练数据、问题+答案联合判断、轻量探针+重型分类器**两段式筛查**)。
- **Human-in-the-loop**:两类触发——超过失败阈值(重试次数封顶后升级人工)、高风险操作(取消订单、大额退款);要求 Agent 能"优雅移交控制权"。
- 全书结构表:把第 2-10 章逐一映射到 Harness 五要素 + 安全横切面。

## 与 Agent OS 设计的映射

- **LLM(推理引擎)** → Providers 子系统(§4):`Provider` 契约、`ProviderManager` 路由/重试/记账;Skill manifest 的 `model.prefer`(§2.1)对应本章"按任务选模型"的建议。
- **Context = static prefix + trajectory** → `FrameContext`(§2.3):`pinned` 消息(系统提示、任务规格)正是 static prefix 的帧内对应;`messages` 即 trajectory。压缩子系统(§7)是对 trajectory 增长的"内存管理"应答;§7.3 不变量 2(tool_call/tool_result 原子配对)直接保障消融实验中"Tool Results 不可缺"的结论。
- **Tools(行动接口)** → Tool Registry(§8)与三层权限模型(§8.2);Collaboration 类工具(子代理委派)→ 子技能伪工具 `skill__<name>`(§3.3),子帧即子代理。
- **ReAct loop / 内核循环** → §3.1 内核 agent loop:本书的"reason → act → observe"就是 runner 伪码的第 4-6 步;公理 1"内核拥有循环,Skill 拥有策略"正是"模型决策、Harness 执行"的架构化表达。
- **Model as Agent / 服务端内置工具** → 部分落在我们的模型之外:`web_search`、`code_runner` 这类 provider 服务端工具会绕过 Tool Registry(见"差距"节)。
- **Harness = Constrain/Verify/Correct** →
  - Constrain:三层权限交集(§8.2)、ToolGuard(§5.4)、CodeScanner(§9.5)、BudgetGuard、`max_steps`/`max_depth`/`timeout`(§2.1/§2.4);
  - Verify:outputs JSON Schema 校验(§2.1)、工具参数 schema 校验(§8.1);
  - Correct:错误观察回写让 LLM 自行恢复(§3.2)、幂等工具内建重试(§8.1)、LoopDetector 先 `inject_message` 纠偏再强停(§5.4)。
- **Workflow vs Autonomous 混用** → Skill 双形态(§2.1):code 技能 = 确定性 workflow 编排者,prompt 技能 = 自治 Agent;§9.3 `LogicContext.invoke` 明确支持"确定性控制流 + 按需调 LLM 技能",正是本书推荐的混合模式。
- **显式停止条件** → `limits.max_steps`/`timeout`(§2.1)、RunConfig 全局预算(§2.4)、BudgetGuard/LoopDetector 强停(§5.4)。
- **Guardrails 执行侧(tool risk rating)** → `ToolSpec.permission`(READ<WRITE<NET<EXEC,§2.2)+ HumanApproval sidecar(§5.4)的人工闸门。
- **trajectory 是数据资产** → TraceRecorder 全量事件落盘 JSONL(§5.5)。
- **Keep it transparent** → 信号总线全量可观测(§5.1)+ TraceRecorder;帧树可检视(`RunControl.get_frame_tree`,§5.2)。
- **五要素原则 Fail-Safe Defaults** → §8.2"三层取交集,任一拒绝即拒绝"是其代数化表达。

## 一致与相互印证

1. **"内核拥有循环"被本书背书**。本书反复强调:RL 内化的是决策策略,工具执行与编排循环永远在模型之外;"模型越强,Harness 越重要",Harness 是核心竞争力。这正是公理 1 与整个内核存在的理由(§1)。
2. **通用工具 + 沙箱,而非专用工具**。本书"给 Python 解释器和安全沙箱,而非计算器"与 §9.4 `python_exec` 薄壳工具 + Logic Kernel 强制沙箱完全一致,连动机都相同(通用工具让 Agent 创造性组合基础能力)。
3. **消融实验为我们的硬不变量提供实验证据**。缺 Tool Results → 无限循环,印证 §7.3 不变量 2(配对原子性,且由 RollingWindowCompressor 的原子组结构**结构性保证**而非事后检查)与 LoopDetector 存在的必要性;缺 reasoning → 决策自相矛盾,提醒我们消息模型必须保留推理痕迹(见差距 4)。
4. **Fail-Safe Defaults = 三层权限交集**。本书把"默认全关、显式开启"列为 Constrain 的核心原则,§8.2 的白名单+全局上限+工具等级三层取交集正是该原则的机制化。
5. **显式停止条件是自治 Agent 的必要条件**。本书"否则死循环或任务完成后空转"直接对应 §2.1 `max_steps`、BudgetGuard 超限强停、LoopDetector 纠偏—强停两段式(§5.4)。
6. **workflow/autonomous 混合优于纯自治**。本书"关键流程走 workflow,灵活部分走 autonomous"与 Skill 双形态 + code 技能编排 prompt 技能(§9.3)同构;我们的帧树模型让两种模式共享同一套权限/信号/记账,比书中列举的框架(n8n 双节点并存)更统一。
7. **透明性是信任前提** ↔ 信号目录 + TraceRecorder(§5.1/§5.5);本书"黑盒错误无法从外部定位"正是我们"一切特权操作发信号"(公理 3)的动机。
8. **Correct 层的"静默重试"** ↔ §4.2 ProviderManager 内部重试不暴露给帧上下文;工具失败才作为错误观察写回(§3.2)——颗粒度一致:瞬态故障内部消化,确认失败才上升。

## 差距与可借鉴点

1. **缺 Circuit Breaker(熔断)机制**。本书把熔断列为 Correct 层标配("连续失败后 fallback 到人工"),Claude Code 实践中也单列。我们只有:幂等工具的调用内重试(§8.1)、同签名重复的 LoopDetector、全局预算的 BudgetGuard。**没有"同一工具连续失败 N 次(参数不同)即跳闸"的机制**——一个系统性故障的工具(如 API 持续 500)会让 LLM 每步都拿到错误观察、空耗步数与预算直到 max_steps。价值:把"可靠性缺口"从预算兜底提前到步数层面,实现成本低(一个订阅 `post:tool.call` 的 ASYNC sidecar + 滑窗失败计数),应入 §5.4 内置 sidecar 清单。
2. **缺用户通信工具与"优雅移交控制权"通道**。本书工具五分类中的 User Communication 与 Collaboration(请求人工确认)在我们这里只有 HumanApproval sidecar 的"挂起等批准"(§5.4),且它是被动的闸门;**Agent 无法主动发起提问/澄清/汇报/移交**——GPT-5.6 的意图澄清、本书 HITL 的"hand over control gracefully"都需要一个 `ask_user` / `handoff_to_human` 内置工具,其结果就是挂起帧等待外部输入。这与我们的暂停语义(SUSPENDED,§2.3)天然兼容,值得纳入 §8.3 内置工具。
3. **缺 Event Trigger(事件驱动激活)**。本书把 webhook/定时/邮件触发列为工具系统的一翼;我们的 Run 只能由调用方同步发起(§2.4),信号总线是内向的。v1 可在非目标中显式标注(§14 已部分覆盖),但建议在 `run.started` 语义上预留"外部事件作为根帧 input"的通道,避免将来事件源接入时破契约。
4. **消息模型未显式包含 `reasoning`**。本书明确 assistant 消息有 reasoning/content/tool_calls 三部分,且消融实验证明去掉 reasoning 会导致决策自相矛盾。../DESIGN.md 的 `Message`(§2.3)与 `ChatResponse.message`(§4.1)未规定推理痕迹的承载与保留策略;压缩时 reasoning 是否随原子组一起驱逐也未说明(§7.5)。这是契约层 `api/v1/messages` 必须钉死的细节,否则跨 provider(Anthropic thinking blocks / OpenAI reasoning)会丢一致性。
5. **Guardrails 缺输入侧与输出侧**。本书三类护栏中,执行侧我们覆盖良好(ToolGuard/CodeScanner/HumanApproval),但**输入侧**(relevance/safety classifier、注入检测)与**输出侧**(PII 过滤、内容校验)没有对应信号挂点:`run.started` 是异步观察语义,没有 `pre:run.input` 这种可否决信号;输出只有 schema 校验(§2.1),没有内容级检查挂点。建议信号目录补 `pre:run.input`、`pre:frame.output`(§5.1),自定义 sidecar 即可承载分类器,内核零成本。
6. **tool risk rating 比我们的权限等级多维**。本书按**可逆性、权限、财务影响**三维评级,且思考题 7 点出"同工具不同参数风险不同"(delete 普通文件 vs 系统文件)需要动态评级。我们的 `Permission` 是一维四级(§2.2),参数相关风险只能靠 ToolGuard 正则(§5.4)逐条手写。可借鉴:ToolSpec 增加 `reversible: bool`(`idempotent` 已有一半语义),HumanApproval 的触发条件支持"等级 + 参数模式"组合——我们已有信号与 Veto 通道,只是缺声明式配置。
7. **服务端内置工具(Model as Agent)是审计盲区**。Kimi K3 的 `web_search`、GPT-5.6 的 `code_runner` 在 provider 服务端闭环执行,**不经过 Tool Registry,没有 `pre:tool.call` 信号,ToolGuard 与三层权限全部失效**。本书指出这是行业趋势("编排循环移到服务端"),不能装作不存在。建议 `ProviderCaps`(§4.1)增加 `server_side_tools: list[str]` 声明,ProviderManager 在响应中透传服务端工具调用记录,至少让 TraceRecorder 可审计、BudgetGuard 可记账;权限语义上标记为"已出网,内核不可拦截"。
8. **两段式护栏(Constitutional Classifiers)是 sidecar 成本模型的好范式**。"零成本轻量探针全量筛 → 可疑才升级重型分类器"与我们 SYNC sidecar 2s 超时、压测预算内才可注册 SYNC(§5.3/§13)的考量同向,可作为自定义 sidecar 的文档范式:规则探针 SYNC,LLM 分类器 ASYNC 升级。
9. **Freeform Tool Calling**。GPT-5.6 允许工具参数为原始文本(代码/SQL),免 JSON 转义。我们的 `ChatRequest.tools` 与 ToolSpec 假设 JSON Schema 参数(§2.2/§4.1)。可在 `ToolSpec.parameters` 之外预留 `freeform: true` 形态,Provider 层按能力协商——低成本预留,避免将来适配时改契约。
10. **Loop Engineering 指明 v1 之后的方向**。"谁发现下一件工作、何时算真正完成"超出 Run 的单次语义,与差距 2/3(用户通信、事件触发)合流,可作为 §14 开放问题的补充条目。

## 冲突或不同取舍

1. **"Keep it simple:直接调 API 优于复杂框架" vs 我们的六子系统内核**。Anthropic 的忠告是抽象层即调试盲区;Agent OS 恰恰是一个重抽象的内核。各自成立条件:本书面向**应用开发者**构建单个 Agent,够用就好是对的;我们定位是**可嵌入的内核/库**,目标是让"约束、验证、恢复"成为结构性能力而非每个应用重写的样板——本书自己也说"生产系统大部分 Harness 代码在 Constrain/Verify/Correct",这正是把这些代码沉淀为内核的理由。风险真实存在:baseline 必须保持最小(§1"满足契约的最小可用实现")以对冲复杂度,M0-M4 的里程碑切分是对的。
2. **workflow 优先(能用 workflow 就别用 Agent)vs 我们统一帧模型**。本书的排序建议(prompts → workflow → autonomous)是应用层选型;我们把两者统一为 Skill 双形态,看似"一上来就给了自治"。实际不冲突:统一模型的价值在于权限/信号/记账一套通吃,而"优先用 code 技能编排"可以作为使用范式写在文档里,不需要架构上区分。
3. **校验"只看结构化数据"(Input Isolation)vs 我们把错误观察交 LLM 自行恢复**。本书 Verify 原则不信任模型自由文本;§3.2 让工具失败回写上下文由 LLM 决策补救,看似把判断权给了不可信方。实质不冲突:安全相关的否决(ToolGuard/CodeScanner/BudgetGuard)全部在 sidecar 以结构化数据执行,fail-closed(§5.3);交给 LLM 的只是"如何恢复"的策略选择,不涉及放行与否。但值得在 §3.2 明确声明这条边界,防止后续贡献者把安全判断写进 prompt。
4. **轨迹全量累积 vs 我们的压缩/子帧折叠**。本书把 trajectory 的累积性称为设计之美,同时(思考题 2)承认二次方成本问题。我们的 `collapse_child`(§7.2)与压缩策略是有损取舍:父帧看不到子帧过程。成立条件:子技能契约(input/output schema,§2.1)保证返回值是充分的;当任务需要子过程可回溯时,TraceRecorder 落盘的完整 trajectory 承担"证据"角色——有损的给模型,无损的给人,这个分工应在文档中点明。

## 行动建议

按优先级排序(标注影响子系统 / 设计文档小节):

1. **【高】新增 CircuitBreaker 内置 sidecar**:订阅 `post:tool.call`,滑窗统计同工具连续失败(与 LoopDetector 的"同参重复"正交),超阈值先 `inject_message` 建议换路,再犯 `ctl.stop()`。影响:Sidecars(§5.4 清单、§5.5 baseline),M4 范围内工作量小。
2. **【高】新增 `ask_user` 内置工具 + 挂起—恢复语义**:Agent 主动提问/移交人工的通道,帧进入 SUSPENDED 等待外部输入,与 HumanApproval 共用恢复路径。影响:Tool Registry(§8.3)、执行模型(§3.1 暂停语义)、HITL 叙事(§5.4 HumanApproval 互补)。
3. **【高】契约层钉死 reasoning 承载**:`api/v1/messages` 的 assistant 消息显式含 `reasoning` 部分;`ChatResponse`(§4.1)归一化各家 thinking/reasoning 字段;压缩策略说明 reasoning 随原子组同进同出(§7.3 不变量 2 扩展)。影响:Providers(§4)、Compression(§7)。
4. **【中】信号目录补 `pre:run.input` / `pre:frame.output`**:输入/输出侧护栏的可否决挂点,分类器以自定义 sidecar 承载。影响:Sidecars(§5.1),仅增信号不发默认行为,零兼容性代价。
5. **【中】ToolSpec 增加 `reversible: bool`,HumanApproval 支持"等级+参数模式"声明式触发**:对齐本书 tool risk rating 的可逆性维度与动态评级(思考题 7)。影响:Tool Registry(§2.2/§8.2)、Sidecars(§5.4)。
6. **【中】ProviderCaps 声明 `server_side_tools`,ProviderManager 透传服务端工具调用记录**:堵住 Model-as-Agent 趋势下的审计盲区,Trace/Budget 至少可观察。影响:Providers(§4.1/§4.2),并需在 §8.2 注明服务端工具不受三层权限约束的语义边界。
7. **【中】文档明确"有损上下文给模型,无损轨迹给 TraceRecorder"的分工**,以及 §3.2"安全判断不进 prompt"的边界声明。影响:§3.2、§7 文字,无代码。
8. **【低】ToolSpec 预留 `freeform` 参数形态与参数 `examples` 字段**(对齐 Freeform Tool Calling 与 ACI/Poka-yoke 的"参数带示例"原则)。影响:§2.2/§4.1 契约预留字段。
9. **【低】§14 开放问题补两条**:事件驱动激活(Event Trigger 作为根帧 input 来源);两段式护栏(规则 SYNC 探针 + LLM ASYNC 升级)作为推荐 sidecar 范式。影响:§14。
