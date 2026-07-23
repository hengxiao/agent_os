# Ch00 引言 + 后记 — 与 Agent OS 设计对比报告

> 评审对象:`ai-agent-book/book-en/introduction.md`(102 行)、`afterword.md`(47 行)。
> 两份文件均已完整通读(均不足千行,单次 Read 即覆盖全文,无分页遗漏)。
> 对照文档:`DESIGN.md` v0.3。

> 关联度评估:引言与后记相当于全书"第 0 章",性质是纲领与展望——**原则密度高、机制细节少**。
> 与 Agent OS 内核设计的关系是"原则层"关联(为什么需要 harness、哪些原则长寿),
> 而非"机制层"关联(具体算法与工程细则)。本章对 Ch2 上下文压缩、Ch4 工具安全、
> Ch6 评估、Ch10 多 Agent 等机制仅有目录级预告,深入对比应留给对应章节的报告;
> 本报告只就已出现的概念做实对照,不越界预支后续章节内容。

## 章节内容概要

### 引言(Introduction)

- 成书背景:2025 年 8–10 月图灵"AI Agent Bootcamp"讲稿扩写;写作过程本身由作者团队的语音 Agent(Pine)以 **"whisper coding"**(口述协作)完成——"口述提纲 → Agent 调研 → 组装初稿 → 多轮讨论修订",利用语音带宽约四倍于打字的优势;本书"既讲 Agent,也是 Agent 参与创作的产物"。
- 原则来源:作者系 Pine AI 首席科学家;Pine 被描述为"首个能自主与真人交互、可靠处理涉及金钱的长周期复杂任务"的通用 Agent(替用户给运营商打电话谈判账单、向商家退款投诉、取消订阅),单次任务 routinely 达数十轮交涉,一个数字出错就是真实金钱损失——**这种不宽容的可靠性需求逐一捶打出全书反复回到的架构原则**。
- 核心叙事:**"Practice comes first, naming comes later"(实践在先,命名在后)**——Skill、harness、loop engineering 三个行业热词均非被发明后被采纳,而是大量 Agent 团队各自实践、再由 Anthropic 等公司事后蒸馏命名;给企业的推论:等热词流行再实践就已经晚了。
- 作者团队更早的实践清单(全书反复引用):
  1. **动态 prompt 加载**——抑制 prompt 无限膨胀(即后来的 Skill 概念);
  2. **命令行执行工具**——抑制工具列表膨胀;
  3. **系统状态栏(status bar)**——让 Agent 感知执行环境、用户时间、自身工作状态;
  4. **harness 手法**(Claude Code 式)——对付不稳定工具调用、幻觉、危险操作、未授权操作、指令被忽略;
  5. **proposer-reviewer 方法**(即 loop engineering,2026 年中才被行业命名):防"遇堵即称做不到"、防"循环未闭合即称完成";核心是"**让 Agent 审查自己的产出物并迭代,由验证(verification)而非模型的自我感觉决定任务何时结束**"。
- 企业 Agent 开发两个关键:① **真实业务把能力天花板逼到极限**并持续汲取反馈(业务要求低、一次模型升级就解决,便永远没动机打磨架构);② **评估机制**——"without evaluation, there is no progress",让"真改进还是运气好"可分辨(第 6 章展开)。
- 核心公式:**Agent = LLM + Context + Tools**,三者缺一不可。三个理解层次:实现层(公式本身)、直觉层(Brain + Eyes + Hands and Feet;作者自注 Context 还包含工具定义,"看见什么"含"有什么手脚可用")、学术层(RL 的 Policy + Observation Space + Action Space)。主张"**好的设计原则应比模型迭代周期长寿**"——它描述的不是某个模型的用法,而是智能系统与世界交互的基本模式。
- 引 Richard Sutton 宇宙演化四阶段说(尘埃→恒星→生命→agents):Agent 是史上首个"能理解自身运行机制、能通过生成代码自我举荐(bootstrap)、能创造全新 Agent 乃至自我改进"的物种——为 Ch8 自我演化伏笔。
- 全书结构预告(10 章 3 部分):
  - Ch1 基础:ReAct loop(Think → Act → Observe)、三种学习范式(Post-training / In-Context Learning / **Externalized Learning**)、workflow → 自主 Agent 的编排谱系;
  - Ch2 上下文工程(全书最关键章):消息列表、KV Cache、prompt 工程与 injection 攻防、**Agent Skills 按需加载**、**状态栏**、上下文压缩策略;
  - Ch3 用户记忆与知识库:四级记忆策略、RAG 全栈(检索、排序优化)、多模态抽取、Agentic RAG;
  - Ch4 工具:MCP、五类工具(Perception / Execution / Collaboration / **Event Triggering** / User Communication)、**执行工具安全机制**、**事件驱动异步架构**;
  - Ch5 Coding Agent(以 OpenClaw 为主线):"Coding Agent + 文件系统是通用 Agent 的核心底座";代码生成超越编程——辅助思考、建知识库、**动态创建新工具**、Agent 自我举荐;
  - Ch6 评估:tool-calling / 人机交互两大范式 + 仿真环境、数据集设计、LLM-as-a-Judge、评估驱动选型与改进闭环;
  - Ch7 后训练:SFT/RL("SFT memorizes, RL generalizes""数据与环境比算法重要")、奖励设计("奖励结果、约束过程");
  - Ch8 自我演化:经验学习(策略总结、workflow 记录、system prompt 自动优化、**Skill 知识外化**)与主动造工具(MCP-Zero、开源工具接入、用代码造工具);
  - Ch9 多模态与实时交互(Voice Agent、Computer Use、VLA/Sim2Real);Ch10 多 Agent 协作(**Shared/Independent Context × Peer/Manager/Decentralized** 框架、翻译 Agent 与 Phone+Computer Agent 案例)。
- 配套与术语:每章含带难度星级(★/★★/★★★)的 Experiment X-Y 与思考题,代码全部开源;术语约定 reasoning(思考,链式思维)≠ inference(推理,部署期前向计算)。

### 后记(Afterword)

- 回扣公式并逐章收束;点出 Ch10 多 Agent"无新事":
  - 是否共享上下文 = Ch2 的 "**isolation over compression**(隔离优于压缩)"在系统架构层的表达;
  - "Agents 互为工具"直接来自 Ch4 协作工具设计;
  - 判断多 Agent 系统的"新信息"判据呼应 Ch6 评估思想。
- "**两朵乌云**"(仿开尔文 1900 年"物理学上空的两朵乌云"):
  1. **流式实时交互**——现实世界不停顿等模型想完(语音被打断、场景在变化、邮件持续到达);
     "活着的" Agent 应边听边想边说、在没人要求时主动注意到"这封邮件该处理"。两条路径常并行:
     - **快慢架构分离**:实时性与智能近乎正交,单模型难以兼得;
       fast frontend model 保会话节奏,slow backend model 深思考;
     - **推理本身提速**:小米 MiMo 经 FP4 量化 + DFlash 并行投机解码 + TileRT 系统,
       把 1T 参数模型推到单 8 卡节点 1000 token/s;
       Taalas HC1 把 Llama 3.1 8B 固化进 6nm 芯片,约 17000 token/s、响应 <100ms,
       代价是换模型需重新流片。
  2. **经验持续积累**——今日模型像"记忆力超绝但学不到新东西的天才",
     任务结束后踩过的坑、摸索出的技巧随上下文一起被丢弃。是否真问题取决于两个对立假说:
     - "**小世界假说**":足够大的模型已含几乎所有重要通用知识,一次训练即可;
       瓶颈是数据而非学习能力;前沿实验室正逐行业"蒸馏"专业能力进同一个大模型;
     - "**大世界假说**":用户/企业专属知识(公司代码规范、团队做 PPT 的品味、某客户的脾气)
       不在任何语料中且常变,只能部署后持续学习;对应 Ch3 记忆与 Ch8 自我演化;
       AI for AI / AI for Science 的前沿没有现成语料,只能从自身实验成败中自主学习;
     - 结论倾向:"模型最强的能力最终不是记忆,而是学习与适应。"
- **模型与 Harness 协同演化飞轮**:
  - harness 里"看似丑陋的意大利面代码"——**多级上下文压缩、
    数千次失败才熔断的重试逻辑、悲观默认"不安全"的权限检查**——
    每一处都记录着模型尚不可靠之处;
  - 飞轮:真实业务出难题 → 应用层用 harness 补模型短板 →
    补丁凝结为下一轮训练信号 → 模型内化约束 → 对应 harness 层可删除;
  - 对 Ch1 悬置问题的正式回答:"模型会吃掉 Harness 吗?**会——一层一层地吃**";
  - 但永远吃不完:训练以月计而业务等不了;模型无法内化每个业务的全部约束与偏好;
    每一代模型打开的新能力边疆恰是其最不可靠处;
  - "Harness 不会消失,只是随模型一起向新边疆迁移"——
    这也是 Agent 时代苦涩教训(Bitter Lesson)的读法:
    通用方法终将胜出,但"终将"之内的每段路由 harness 铺就。
- Claude Code 案例:
  - 模型与 harness 双端自持者飞轮转得最快(模型知道 harness 会怎么调它,harness 知道模型边界在哪);
  - 某实验仅改 harness(模型不变)任务准确率 52.8% → 66.5%——
    既说明 harness 今天杠杆巨大,也说明"杠杆巨大恰因模型尚未到位";
  - 给应用层的忠告:harness 是最锋利的短期技术杠杆,
    但模型每内化一层约束就会"顺手"抹掉一批仅靠 harness 建立的优势;
    持久护城河在技术之外(独占数据、渠道、用户信任、网络效应、需要人机协同的复杂场景)。
- 收束于三个不过时的问题:**它看见什么、它能做什么、如何验证它做对了**——
  它们描述的不是特定模型的用法,而是智能系统与世界交互的基本方式。

## 与 Agent OS 设计的映射

| 书中概念 | Agent OS 对应 | 设计文档小节 |
|---|---|---|
| Agent = LLM + Context + Tools | 六子系统的主轴:LLM→Providers;Context→FrameContext+压缩;Tools→Tool Registry;循环本体→Kernel Runner | §4 / §2.3+§7 / §8 / §3.1 |
| ReAct loop(Think→Act→Observe) | 内核 agent loop:组装请求→Provider→分发调用→观察入上下文→重复 | §3.1 |
| Harness(全书灵魂概念) | Agent OS 整体即 harness 内核:sidecar 监督+三层权限+压缩+弹性重试+沙箱 | §1 三公理、§5、§8.2、§7、§4.2、§9 |
| proposer-reviewer / loop engineering | 仅一半:outputs schema 校验(形式验证);缺语义级 reviewer 与"验证决定结束"机制 | §2.1、§3.1 步骤 5 |
| 动态 prompt 加载 / Skills 按需加载 | Skill Registry 加载流水线;子技能以伪工具 schema 按需向父帧呈现 | §6.1、§3.3 |
| 系统状态栏 | **无直接对应**(见"差距"节第 2 条) | — |
| 命令行工具抑制工具列表膨胀 | `shell_exec` 单工具 + manifest 工具白名单按帧限制可见工具 | §8.4、§2.1、§6.2 |
| "isolation over compression" | `collapse_child`:子帧 transcript 不进父帧,弹栈即折叠为返回值 | §2.3、§7.2 |
| "Agents 互为工具" | 子技能呈现为伪工具 `skill__<name>`,模型经原生 function calling 发起 | §3.3 |
| 悲观默认"不安全"的权限检查 | 三层权限取交集 + SYNC sidecar fail-closed + 动态代码强制沙箱 | §8.2、§5.3、§9.2 |
| 多级上下文压缩 | 策略责任链 spill→truncate→summarize(`hierarchical`),软/硬/外部三级触发 | §7.1、§7.2 |
| 数千次失败才熔断的重试 | ProviderManager 指数退避(baseline 上限 3 次)——哲学差异见"冲突"节第 2 条 | §4.2、§4.4、§8.1 |
| Externalized Learning / 用代码造工具 / Skill 知识外化 | Skill 热重载 + `fs_write` + Logic Kernel:Agent 可自写技能并加载(未点明) | §6.1、§8.4、§9 |
| 快慢架构分离 | 帧粒度模型异构:Skill 级 `model.prefer` + ProviderManager 路由;压缩摘要用廉价模型 | §2.1、§4.2、§7.2 |
| 事件驱动异步架构 / Event Triggering(Ch4 预告) | 信号总线 + ASYNC sidecar 已是事件驱动;但 run 的唤醒源仍是单次外部调用 | §5.1、§5.3 |
| User Communication 工具(Ch4 五类之一) | 最接近的是 HumanApproval sidecar(单向批准闸门);无 Agent→用户双向通信工具 | §5.4 |
| 评估闭环 / LLM-as-a-Judge(Ch6 预告) | 内核职责之外;TraceRecorder JSONL 是评估数据载体,MockProvider golden-file 是回归雏形 | §5.5、§13 |
| 两朵乌云(流式实时 / 持续学习) | 长期记忆列为非目标;流式未言明,`Provider.stream` 契约已留口子;黑板服务预留对应记忆接入 | §14、§4.1、§11.3 |
| "如何验证它做对了"(三个不过时问题之三) | sidecar 否决体系 + outputs 校验 + TraceRecorder 可回放 | §5、§2.1、§5.5 |
| RL 三层映射(Policy/Observation/Action) | 学术层表述,对内核机制无直接要求 | — |

## 一致与相互印证

1. **"内核拥有循环"(§1 公理 1)被 harness 理论直接支持。**
   - 后记断言 harness 存在的全部理由是"模型尚不可靠之处需要外部逻辑兜底";
   - 这正是我们把 agent loop、工具分发、预算记账收归内核(而非交给 Skill 自驱 loop)的元理由;
   - 书中"仅改 harness、模型不变,准确率 52.8%→66.5%"的实验量化了这层价值——
     harness(即我们的内核)不是脚手架,是今天最大的性能杠杆。
2. **`collapse_child` 隔离语义(§2.3、§7.2)与 Ch2 核心结论 "isolation over compression" 完全同构。**
   - 后记把该原则提升为多 Agent 架构判据(Ch10 共享 vs 独立上下文);
   - 我们把它做成结构性的默认机制("子帧弹栈时 transcript 天然折叠为一个返回值,默认免费获得");
   - 比书中作为工程实践原则的表述更形式化一步:隔离在我们这里不是选项,是调用栈语义的副产品。
3. **子技能=伪工具(§3.3)与 "agents as tools for each other"(后记,源自 Ch4)一致。**
   - 多 Agent 协作在我们这里退化为子技能调用,复用同一套分发、权限与信号管线;
   - 书中把它列为协作工具设计的正解,说明我们没有为"多 Agent"发明多余抽象。
4. **权限体系(§8.2、§5.3、§9.2)与后记 harness 典型层"权限检查悲观默认不安全"逐条对应。**
   - 三层权限取交集、SYNC sidecar fail-closed、动态代码强制沙箱;
   - HumanApproval 对 EXEC 级工具加人工闸门(§5.4)亦是书中"危险操作、未授权操作"对策的同构实现;
   - Ch4 又以"执行工具的安全机制"为焦点——这一层是全行业共识必选项,不是过度设计。
5. **多级压缩责任链与分级触发(§7.1、§7.2)对应后记 harness 清单第一项 "multi-level context compression"。**
   - 软硬上限 + sidecar `ForceCompress` 外部强制(如 DriftWatcher 话题发散时主动压缩),
     比单纯"到限就压"更接近书中实战形态。
6. **设计原则长寿论双边互证。**
   - 引言"good design principles should outlast model iteration cycles"
     与后记"三个不过时的问题(看见什么/能做什么/如何验证)",
     对应 §11.1"契约层破坏性变更只允许跨大版本"的治理策略;
   - 两边押的是同一个赌注:变化的是模型与实现,稳定的是边界与抽象;
   - 我们 §1 用一张 OS 映射表推导全部命名以保证概念不自相矛盾,
     也正是"原则先于命名"的方法论自觉。
7. **baseline 可替换 + entry point 扩展(§11.3)恰好满足"层可删除性"。**
   - "模型一层一层吃掉 harness"的直接推论:harness 每层必须可独立删除/降级,
     否则被模型内化时就成了删不掉的遗产;
   - 我们的契约层 + 注册机制让任何一层(压缩策略、sidecar、沙箱后端、Provider)
     可被替换为空操作或更强实现而不动契约;
   - 设计文档未将此写明为动机,但结构已经做对——缺的只是升格为明示原则(见行动建议 3)。
8. **帧粒度模型异构已在位。**
   - 书中"快慢架构分离"对应 §2.1 `model.prefer` 按技能路由 + §4.2 能力匹配探测
     + §7.2 summarize 显式使用廉价模型;
   - "不同帧用不同档位的模型"是双方都认可的基线能力,
     我们的实现粒度(帧/技能级)比"前后端双模型"更细。
9. **测试策略与评估文化同向。**
   - 书中"没有评估就没有进步";
   - 我们 §13 的 MockProvider 脚本化应答 + golden-file 比对 + 故障注入
     + 压缩不变量 property-based 测试(hypothesis),
     是把"改动是否真好"变成可判定问题的同一方法论在内核层的落地。

## 差距与可借鉴点

引言/后记是纲领章,机制细节有限;但以下几个被作者加粗强调的实战概念,我们缺失或只做了一半:

1. **proposer-reviewer:"验证决定结束"。**
   - 书中:全书反复回到的原则——"verification, not the model's own feeling,
     decides when a task ends";做法是"让 Agent 审查自己的产出物并迭代"。
   - 我们:outputs schema 校验(§2.1)只是形式验证,回答"格式对不对",
     回答不了"任务真的做完了吗"。
   - 为什么有价值:这是长任务可靠性的核心手段,直接命中真实业务最痛的两类失败
     (遇阻即弃、未闭环报捷);且与信号机制天然兼容——缺的是一个
     **`pre:frame.pop` 同步信号**(弹栈前可否决),让 reviewer(另一个 LLM 帧或规则 sidecar)
     打回最终答案、注入"继续"指令。成本约等于复用一次已有 `pre:step` 机制。
2. **系统状态栏(status bar)。**
   - 书中:作者列为 Skill 热词之前的三大实践之首——让 Agent 感知执行环境、用户时间、自身工作状态。
   - 我们:FrameContext 对模型只暴露消息历史;usage、预算、深度、墙钟都存在帧对象里(§2.3)
     但 LLM 看不见。
   - 为什么有价值:预算感知可让模型自主决定收敛策略(临超支就提速收尾);
     时间感知是长任务(书中"一个电话打几小时")刚需;环境感知减少幻觉性操作。
     实现便宜——`build_request`(§3.1 步骤 3)每步注入一行状态文本即可,
     配合压缩策略把状态行视为 ephemeral(可被 `truncate` 丢弃、不 pinned)。
3. **"数千次失败才熔断"的重试哲学。**
   - 书中:harness 的重试 "trips a circuit breaker only after thousands of failures"——
     源于"一个电话打几小时、一次失误就是真金白银"的业务,瞬态故障绝不该杀死长任务。
   - 我们:baseline 是 provider 重试上限 3 次、工具仅幂等重试(§4.4、§8.1)。
   - 为什么有价值:差距不在次数,而在**故障致死性分层**没有说清——
     真正的熔断器应是 `max_steps`/预算/墙钟(§2.4),单点重试上限不应成为长任务的死因;
     这一分层说清楚,长任务宿主才敢把 run 交给内核。
4. **评估数据的形状是内核的责任。**
   - 书中:"without evaluation, there is no progress"是全书立场
     (Ch6 展开 LLM-as-a-Judge、评估驱动选型与改进闭环;两大评估范式之一正是 tool-calling)。
   - 我们:内核不该内置评估(越界),但 TraceRecorder 的 JSONL(§5.5)目前只是
     "每条信号一行、payload 摘要"。
   - 为什么有价值:评估闭环的全部上游数据由内核产生;trace 契约不稳,下游评估就是沙上建塔。
     trace schema 需要版本化、字段稳定、足以重建完整 tool-calling 过程。
5. **工具列表膨胀治理未显性化。**
   - 书中:以"命令行工具抑制工具列表膨胀"回应"工具过多稀释模型注意力"之病。
   - 我们:manifest 工具白名单(§2.1)按帧限制可见工具、`visible_to`(§6.2)生成 schema,
     方向正确;但"单帧可见工具数预算"没有成为一等概念(软上限 + 超限告警)。
   - 为什么有价值:中等——属 prompt 工程层面的加固,对工具生态变大
     (第三方 entry point、MCP 适配接入,§8.3、§11.3)后的模型表现有直接保护。
6. **自我演化通路已有全部原料但未点明。**
   - 书中:三种学习范式含 Externalized Learning(权重外学习);
     Ch8 展开"用代码造工具、Skill 知识外化、system prompt 自动优化"。
   - 我们:`fs_write` 写文件(§8.4)+ Skill 热重载(§6.1,"新帧用新版,在跑帧钉住旧版")
     + Logic Kernel 执行动态代码(§9)——串起来就是
     "Agent 运行期 authoring 新技能 → 热重载 → 后续帧可用"的完整闭环。
   - 为什么有价值:这是本书视角下我们最具差异化潜力的能力——
     多数 agent 框架的技能集是启动时冻结的;设计文档只字未提这条通路,
     等于把卖点埋进了实现细节。
7. **User Communication 工具缺位(Ch4 五类工具之一)。**
   - 书中:与用户的通信列为独立工具类型。
   - 我们:HumanApproval(§5.4)是"批准/否决"的单向闸门,
     不是"Agent 主动向用户提问、汇报、确认"的通信通道。
   - 为什么有价值:长任务涉及金钱时,"拿不准就问人"比"拿不准就停下"体验好得多;
     可作为内置工具走 8.1 标准管线(权限 READ 级、宿主注入回调),不进内核机制。

## 冲突或不同取舍

1. **循环驱动方式:回合制 vs 流式实时。**
   - 书中:后记"第一朵乌云"指出行业终局是流式(边听边想边说、无人要求时主动行动),
     路径是快慢分离或推理提速。
   - 我们:内核 loop(§3.1)是严格回合制;§14 未把流式列为非目标,也无任何朝向它的机制。
   - 评估:各自成立——v1 内核面向批式长任务编排,回合制让 pre:/post: 信号、同步否决、
     预算记账的语义干净;`Provider.stream`(§4.1)说明 token 级流出并未堵死。
     真正的缺口比 token 流式更本质:**外部事件唤醒挂起/未启动的 run**
     (书中 Ch4 Event Triggering 与事件驱动架构,"没人问时也注意到这封邮件该处理"),
     目前只能靠宿主轮询。这是架构方向差异,不是对错之分:
     内核保持回合制、把唤醒源留给宿主,与书中架构在系统层可以拼合。
2. **熔断哲学:预算硬顶 vs 不死的长任务。**
   - 书中:任务涉及真金白银,一次错误就是真实损失,故 harness 宁可重试数千次也不熔断。
   - 我们:RunConfig 设 `max_steps`/`max_cost`/`max_wall_time`(§2.4),
     BudgetGuard 超限即 `ctl.stop()`(§5.4)。
   - 评估:不冲突,是两种缺省——内核提供硬上限是对的
     (资源安全是内核职责,无人看管的 run 必须有死线);
     但对长任务宿主,stop 应可降级为 pause + 通知,由宿主决定加预算还是放弃。
     `Verdict` 已有 `Pause`(§5.2),能力在位,只是未作为推荐用法写出。
     各自成立的条件:无人值守的批式 run 用硬顶;有人兜底的业务 run 用 pause。
3. **"harness 会被模型逐层吃掉" vs 契约跨大版本稳定。**
   - 书中:harness 持续迁移、永不消失但层层可删。
   - 我们:§11.1 承诺契约层只跨大版本破坏。
   - 评估:不矛盾,但需自觉——**契约要稳定,实现要随时准备被删除**。
     隐患:若某 baseline(如 RollingWindowCompressor)被用户深依赖,
     模型变强后也"删不动",baseline 就从"最小可用实现"变成"历史包袱"。
     §13 已说"高级形态以替换注册项方式进入,不动契约",
     但反向的"baseline 可退役"未说,需文档层面事先声明,而非事后追认。
4. **小世界 vs 大世界假说 与 长期记忆非目标。**
   - 书中:若大世界假说成立,部署后持续学习是必答题(Ch3/Ch8),
     记忆是 Agent 系统的基本件而非可选件。
   - 我们:§14 把长期记忆服务列为非目标。
   - 评估:内核不管记忆是对的——记忆是应用层关注点,内核管了反而越界;
     黑板服务预留(§11.3 `agent_os.services`)与开放问题 4
     (§14,"黑板引入后跨帧共享的权限模型如何与 manifest 白名单统一")已留桥,无需改设计;
     但应点明记忆服务接入与黑板是同一个问题,避免将来两条路各走一半。

## 行动建议

按优先级排列(标注影响子系统 / 设计文档小节):

1. **【高】新增 `pre:frame.pop` 同步可否决信号,支撑 proposer-reviewer 模式。**
   - 弹栈前允许 SYNC sidecar 否决最终答案,并经 `InjectMessage` 强制该帧继续
     (防"遇阻即弃"与"未闭环报捷"两类早夭);
   - 配套文档化两种 reviewer 形态:规则 sidecar(检查产出物存在性/完整性)、
     语义 reviewer(子技能做 LLM 评审);
   - 影响:Sidecars 子系统(§5.1 信号目录、§5.4 内置表)、执行模型(§3.1 步骤 5);
   - 成本≈复用已有 `pre:step` 机制一次;书中 loop engineering 的直接落点。
2. **【高】内置状态栏注入。**
   - `build_request` 每步向帧上下文追加一行系统状态
     (当前时间、步数、预算消耗、栈深、工作目录);
   - 压缩策略中状态行按 ephemeral 处理(可被 `truncate` 丢弃、不进 `pinned`);
   - 影响:Kernel runner(§3.1)、FrameContext 与压缩(§2.3、§7.2);
   - 直接移植书中三大实战手法之一,实现量小、长任务与预算自主管理收益明确。
3. **【中】把"harness 层可删除性"写进设计原则。**
   - 在 §1 三公理后补一条(或并入 §11.1):每个监督/压缩/弹性机制都是可独立移除的注册项,
     契约不因层的删除而变化;baseline 的宿命是被模型内化后退役;
   - 纯文档改动;回应后记"模型逐层吃掉 harness"的核心论断,
     也是契约层治理(§11.1)与"高级形态替换注册项"(§13)的元理由。
4. **【中】故障致死性分层说明。**
   - 明确"provider/工具单点重试上限不是长任务的熔断器,预算与步数上限才是";
   - §4.2 弹性策略注明重试上限对长任务可配大;
   - §5.4 BudgetGuard 文档化 stop→pause 降级用法(配合宿主加预算流程);
   - 影响:Providers(§4.2)、RunConfig(§2.4)、Sidecars(§5.4),均为文档与配置语义,无新机制。
5. **【中】Trace schema 版本化,面向评估数据。**
   - 把 §5.5 TraceRecorder 的 JSONL 行格式定为带版本头的稳定契约
     (复用 §13"检查点格式演进"的对策手法:append-only + 版本头);
   - 字段足以重建完整 tool-calling 过程,支撑内核外 Ch6 式 LLM-as-a-Judge 评估与 golden-file 回归;
   - 影响:Sidecars(§5.5)、测试策略(§13)。
6. **【低】点名自我演化通路。**
   - 在 §6.1 热重载处或 §14 开放问题补一段:`fs_write` + 热重载 + Logic Kernel
     已构成"Agent 运行期自写技能并加载"的闭环
     (书中 Externalized Learning / Ch8"Skill 知识外化、用代码造工具"的内核形态);
   - 是否提供内置 `skill_create` 元工具留作开放问题;
   - 影响:Skill Registry(§6.1)、§14。
7. **【低】可见工具数预算 + User Communication 内置工具。**
   - 前者:§2.1 manifest 或 §6.2 `visible_to` 处加工程建议(非新机制)——
     单帧可见工具 schema 设软上限、超出时 Registry 告警,对应书中"工具列表膨胀"之病;
   - 后者:以 `ask_user`/`notify_user` 内置工具(READ 级、宿主注入回调)
     补齐 Ch4 五类工具中的 User Communication,走 8.1 标准管线;
   - 影响:Skill Registry(§2.1、§6.2)、Tool Registry(§8.3 内置清单)。
8. **【低】§14 非目标澄清:流式与事件唤醒。**
   - 写明 token 流式已由 `Provider.stream`(§4.1)预留;
   - "外部事件唤醒挂起的 run"(书中 Event Triggering / 事件驱动架构)
     经信号总线由宿主扩展接入,不进 v1 内核;
   - 记忆服务接入与黑板服务(开放问题 4)是同一个权限模型问题;
   - 影响:§14 文档。
