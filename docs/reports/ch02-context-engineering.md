# 第 2 章《Context Engineering》对比评审报告

> 评审对象:`ai-agent-book/book-en/chapter2.md`(1102 行,已全文通读,含 9 个实验与全部脚注)
> 对照基准:`../DESIGN.md` v0.3(Agent OS 内核与子系统设计)
> 总体判断:**关联度高**。该章是六个子系统中 Context Compression(§7)的直接理论底座,同时对 Kernel Runner(§3.1)、Providers(§4)、Tool Registry(§8)、Sidecars(§5)提出了多项 ../DESIGN.md 尚未覆盖的工程约束。章末"Isolation Over Compression"一节与我们的帧隔离语义(§2.3)互为独立殊途同归的印证。

---

## 章节内容概要

本章围绕"模型每次决策时看到什么、如何组织"展开,主线是 API 层的"static prefix + trajectory"上下文结构:静态前缀(系统提示 + 工具定义)保持稳定,对话轨迹(trajectory)随交互增长。关键概念、模式与机制清单:

### 1. API 层上下文结构(How Agents Call LLMs)

- 四种 message role(system/user/assistant/tool);tool 消息经 `tool_call_id` 与 assistant 的调用请求配对;
- API 无状态:每次请求必须携带完整历史;"request → tool call → execution → 回传 → next request"即第 1 章 ReAct 循环的 API 实现;
- 框架的核心职责之一是**维护消息列表**;模型发调用请求、框架执行——职责划分的起点;
- 上下文成分 = 静态前缀 + 动态轨迹,这一结构是后续 KV Cache、压缩等全部讨论的基座。

### 2. KV Cache / Prompt Cache 友好设计

- 前缀**逐字节稳定**才可复用:生产事故案例(系统提示注入 `Current time: {{now}}`,TTFT 从 0.5s 升到 3–5s,账单近翻倍);
- 三条实践结论:①系统提示与工具定义定稿后不改(加一个空格也失效);②动态信息(时间戳、用户状态)只追加到末尾;③必须用标准 API 格式,不手工拼接"USER: ... ASSISTANT: ..."(偏离训练格式,削弱多步推理);
- 有害模式实验(Exp 2-3):动态系统提示、动态用户配置、**动态工具排序**(固定顺序对选择准确率几乎无影响但大幅破缓存)、**滑动窗口历史**(丢早期工具结果 → Agent 反复重试同一调用陷入循环)、文本格式化拼接(最害,属模型能力问题而非缓存问题);
- 两层缓存区分:KV Cache(推理引擎内,单次请求加速)vs Prompt Cache(API 服务层,跨请求复用;缓存读约 1/10 价格;Anthropic 需显式 `cache_control` 断点 + 最短长度 + TTL 约 5 分钟,OpenAI 自动前缀缓存);
- **缓存作为架构约束**(Claude Code 案例):prompt 按缓存边界标记切分(边界前全局可缓存),每个边界前的运行时条件使 cache-key 变体呈 2^N 组合爆炸;**子 agent 与父 agent 的请求逐字节对齐**以共享 Prompt Cache;**工具结果替换串首次生成后永久冻结**,会话重启后仍复用同一字符串;
- 前沿研究"KV Cache as editable/composable notes":有显式 CoT 时字段修改可沿缓存推理传播(约 1% 算力),预计算"技能"缓存块可经 RoPE 重定位拼接入另一上下文,O(L²)→O(L),vLLM 上 p90 TTFT 加速数十至数百倍(研究阶段,不改写三条默认原则)。

### 3. Chat Template 与历史思维链

- Chat Template = API 消息到模型 token 流的"信封格式"(`<|im_start|>`/`<|im_end|>` 等特殊 token);把 tool 结果错标为 user 消息会错误触发推理上下文重置;
- 历史 CoT 处理的行业转向:DeepSeek R1 时代官方建议剥离全部历史 reasoning → DeepSeek V4 **完全反转**,强制逐字回传 `reasoning_content`(否则 API 直接报错),Kimi K2、GLM-5 采用同一协议;Claude 要求 thinking block 带签名原样回传、新 user 轮后服务端忽略历史 thinking;
- 结论:**"for Agent scenarios, thinking is not waste but state"**;使用前须查各模型最新模板文档。

### 4. Prompt Engineering 与 Prompt Injection

- 系统提示设计:XML 标签携带语义、Markdown 组织层级;**流程驱动(SOP)优于规则堆砌**(规则冲突时模型无所适从);业务规则必须细化到可执行(计费案例:退款/取消订阅"NEVER use percentage_based",成功率阈值映射计费模型,产品经理而非工程师写 prompt);
- few-shot:2–3 个覆盖边界的示例优于十个近重复;位置(系统提示 vs 首轮合成消息)与缓存稳定性(一旦选定逐字节冻结,不按请求动态检索);
- 工具定义设计:使用边界("NEVER invoke grep as Bash")、具体示例、性能提示、工具间关系;**Tau-Bench 消融实验(Exp 2-4):去掉信息组织 → 成功率降 30%+;去掉工具描述文字 → 工具调用错误率升 45%**;语气风格影响有限;方法论价值:先做消融再改 prompt;
- **工具渐进披露**已成 API 原生能力:OpenAI Responses API 的 `tool_search` + `defer_loading: true`、Anthropic `tool_reference`、Claude Code 默认延迟加载 MCP 工具、Codex CLI 的 BM25 `tool_search`;schema 命中后**只追加一次、保持原位**,后续轮次命中缓存;依赖模型专门训练(GPT-5.4+/Claude 4.5+);
- Prompt Injection:指令与数据分离是核心原则;Source Tagging(`<external_content source="webpage">...</external_content>`)、结构化 role、输入消毒(仅辅助);攻击面随感知工具扩张(网页不可见元素、PDF 元数据、图片 EXIF);**Skills 与 Status Bar 自身构成新的注入面**(Skill 以高权威指令身份进上下文;状态栏被模型高度信任);上下文层防御只是第一道防线,执行层(权限、沙箱、人工复核)见第 4/5 章;Exp 2-5 覆盖直接注入、间接注入、**记忆注入**(跨会话潜伏)三类攻击。

### 5. Agent Skills(渐进式披露)

- 三层结构:Layer 1 元数据(`SKILL.md` 的 YAML frontmatter,name+description,常驻注入,约数百 token);Layer 2 核心流程(经专用 Skill 工具按需加载,以 tool result 入上下文);Layer 3 细节子文档(如 PPTX Skill 的 `html2pptx.md`);可捆绑可执行脚本与模板;
- description 应写成**路由规则**而非功能摘要:"Use when / Do not use when" + **负例**(负例不是可选,是路由准确性的关键);"when to use me"远比"what I can do"重要;
- 三种实现取舍:①注入系统提示(指令遵循最强,但每换 Skill 破一次缓存);②当中普通文件读取(不动缓存,但中段指令遵循因模型而异,Claude 训练最充分);③Claude Code 生产方案——元数据以 `<system-reminder>` user 消息注入末尾 + **增量发送**(每个 Skill 只发一次),全文经专用工具加载(模型更愿遵循自己刚调用的工具的输出);
- "KV Cache-friendly"≠零成本,而是 **write once, benefit repeatedly**;
- Skill + 通用执行器模型下工具集可以保持很小(第 5 章:仅 7 个核心工具)。

### 6. Agent Status Bar(状态栏)

- 机制:以 **user-role 元消息**在上下文末尾注入运行态(工具计数、TODO、时间、环境),`<agent_status>` 标签包裹;贴近待生成 token,获得最高注意力权重(placement 注意力引导);
- 理论基础:in-context learning **偏检索而非推理**,注意力缺乏"蒸馏层"——计数、聚合、进度等关于内容的结论不会自动产生,每次都要从原始记录重算,成本随上下文增长;
- Context Distillation 基准(3 类任务 × 11 模型 × 约 2.4 万次评测):弱模型准确率 **+40~54pp**(本地 2B 模型追平无状态栏的前沿模型);强模型推理 token **降 80~90%+**;无状态栏时单次查询推理量随上下文持续增长,有状态栏后变为**近似常数**;必须写成可快速定位的 key-value 对(`Clothes: 9 items (Pass 7, Defect 2)`),散文形式效果显著更差;
- 三条工程教训:①**用代码而非 LLM 维护状态栏**(20 行正则达到 ground-truth 级,前沿模型一遍式摘要反而低于无状态栏基线;若必须用 LLM,逐条抽取 + 代码聚合);②状态栏是**有损投影**——删除原文前须确认覆盖所有可能被问的维度(反例:只存两两组合计数,问三元交集时 Claude 从 100% 崩到 7.6%;"看似合理但不完整的状态栏 = 虚假权威");③**模型几乎无条件信任状态栏**(误差 <10% 收益基本保留;更大误差比没有更糟;状态栏数据绝不可来自可被污染的数据源——status bar poisoning);
- Interaction Scaling(交互是 test-time compute 第三轴):状态栏最有价值的内容往往不是模型自己能数出来的,而是**模型无法推断的外部事实**(测试是否通过、按钮是否溢出);"verifier, not the model, is the bottleneck";
- 组成:任务规划(TODO)、事件侧信道(精确时间/间隔)、环境状态、可用能力列表(即 Skills 元数据,复用同一注入通道);
- 两种更新实现的缓存取舍:逐轮替换(失效范围限于末尾几轮,短轨迹/大状态适用)vs 持久追加(Claude Code `<system-reminder>`,完全保缓存,长轨迹适用,代价是过期状态累积);
- Exp 2-8 五项技术:时间戳前缀、**工具调用计数器**("Tool call #3 for read_file",促使换策略 + 隐形成本意识)、TODO 管理(受 Manus 启发;有 TODO 平均 15 轮完成任务,无则 21 轮且常漏子任务)、**详细错误信息四层**(类型描述/完整参数 JSON/调用栈/针对性修复建议;错误恢复率 60%→95%)、系统状态感知(cwd 随 `cd` 自动更新);组合产生涌现效果;
- **time sense 三轴**(urgency 预算轴/persistence 终点轴/vigilance 监测轴):四条件对照实验证明**裸读数几乎不改变行为**(原始时间戳 vs 无信息仅差 2–3pp),"读数 + 操作策略"才把 pass rate 从约 10% 提到 40–50%(+19~49pp);跨 4 个厂商 6 个模型一致成立;可 prompt 期补齐,也可蒸馏进权重(第 7 章)。

### 7. Context Compression(压缩策略)

- 双重动机:①长度与成本;②**提升推理质量**——"总结后的知识比原始信息对模型更有用"(10 次搜索的原始结果散布全文 vs 一次蒸馏出"已知 A/B、C 待查"的结构化笔记);理论同源于"检索而非推理":状态栏是把算好的结论**加入**上下文,压缩是用算好的结论**替换**膨胀的原始记录;
- **Context Rot**(装得下但找不到)区别于 context overflow(装不下):上下文变长注意力被摊薄,无关内容主导后决策质量悄悄退化;"针尖干草堆"实验与位置偏置(开头/结尾回忆好,中段差);Karpathy:"差记忆"在某种程度上是特性(逼出抽象);设计原则:**不要让模型在海量原料中被动检索,主动提供精炼的结构化知识**;
- 压缩与 KV Cache 的统一:压缩发生在**两次 API 调用之间**;系统提示与工具定义永不触碰;压缩对象是工具结果,替换点之后缓存失效、之前保留;**应接近阈值时批量压缩,而非每轮压缩**;
- Exp 2-9 六策略对比(128K 窗口硬约束的研究任务):不压缩第 5 轮即溢出失败;逐条摘要(10.9% 压缩率,信息碎片化);合并摘要(4.3%,超长输入需截断丢尾部);**context-aware 压缩**(把 query 意图与已积累信息写进压缩 prompt;3.0% 压缩率,仅 7 轮 4 万 token 完成;一次 147,877 → 1,963 字符仍保留关键事实;不同阶段动态调整压缩焦点);context-aware + 引用(每事实带来源 URL,4.1%,可回溯验证——**有损语义压缩 + 无损索引**);**Adaptive Windowing**(80% 阈值触发、一次性批量压缩全部未标记工具结果、`[COMPRESSED]` 标记防重复处理);
- **生产级五层分层压缩**(以 Claude Code 为参照,顺序即成本与缓存友好度):①工具结果预算控制(大输出落盘,模型只看预览,**替换决策一经做出即冻结**);②直接噪音删除(低价值内容不摘要直接删——摘要噪音浪费 token);③API 级微压缩(服务端 context editing 移除指定工具结果,本地列表不变,零本地实现成本;删除点后缓存失效,宜仅临溢出时用);④归档式逐轮摘要(**git log 式**每轮独立记录,而非 git squash 合并,保留对话逻辑线);⑤全量压缩(两阶段:先压会话内存,失败再全压;**连续失败熔断器**——生产数据显示大量会话卡在反复压缩失败上空烧费用);
- 四原则:信息价值非均匀分布(关键决策点 > 支撑证据 > 冗余噪音)、语义完整性("Sutskever left OpenAI in May 2024" 不能压成 "Sutskever left")、任务相关性(同内容不同任务不同压缩结果)、**压缩即理解**(显式压缩结果可审查、可跨会话复用);
- 架构含义:压缩模块需要接近主模型的语言能力 → 递归模型调用架构;压缩策略与任务类型耦合(检索任务保广度、分析任务保深度、创作任务保灵感触点),未来应自适应选择;context-aware 压缩 token 用量降 75%+,ROI 极高;
- **保留优先级清单**(压缩最易丢的不是细节,而是早期架构决策、约束背后的理由、失败路径):①架构决策与关键约束(不可摘要);②已修改文件清单与关键变更记录(完整保留);③验证状态 pass/fail(必留);④未完成 TODO 与回滚备注(必留);⑤工具输出(可删,只留 pass/fail 结论);**UUID/hash/IP/端口/URL/文件名等标识符逐字保留**(PR 号改一位,后续工具调用直接失败);
- **Isolation Over Compression(子 agent 上下文隔离)**:把"读大量文件""广域代码搜索"等产生海量中间内容的任务委托给独立子 agent,主上下文只增加任务描述 + 数百 token 结论;**以隔离取代压缩**——压缩是有损事后补救且需额外 LLM 调用,隔离从源头挡住噪音且不动主上下文缓存前缀;代价:子 agent 看不到主上下文,**任务描述必须自包含**;生产实例:Claude Code Task 工具、Deep Research 检索子 agent。

---

## 与 Agent OS 设计的映射

| 书中概念/机制 | Agent OS 对应 | 设计文档小节 |
|---|---|---|
| Harness 维护消息列表、驱动 ReAct loop | 内核 agent loop;"内核拥有循环,Skill 拥有策略" | §3.1,§1 公理 1 |
| tool_call / tool_result 经 `tool_call_id` 配对 | 配对原子性不变量;atomic_groups 结构性保证 | §7.3 不变量 2,§7.5 |
| static prefix(系统提示+工具定义)/ trajectory 二分 | `build_request` 组装的指令 + 帧上下文 + 可见工具/子技能 schema | §3.1 步骤 3 |
| KV Cache / Prompt Cache 前缀稳定性、缓存经济学 | **无对应机制**(差距 1) | — |
| Chat Template;历史 CoT 强制回传协议 | Provider 归一化消息模型、`extra` 字段、能力位 | §4.1,§4.4 |
| 系统提示工程(结构化、SOP、可执行业务规则、few-shot) | prompt 技能指令体(SKILL.md / 内联 prompt 模板)的作者侧约定 | §2.1,§6.3 |
| 工具定义质量(描述、边界、示例、工具间关系) | ToolSpec.description;decorator 从 docstring 首行推导 | §2.2,§8.4 |
| `tool_search` / `tool_reference` 工具渐进披露 | `visible_to` 按帧白名单静态过滤可见 schema(方向相同,机制不同) | §6.2,§3.1 步骤 3 |
| Agent Skills 三层渐进披露(注意:书中 Skill 是**知识包**,非执行单元) | Skill 目录包形态(skill.yaml + SKILL.md);manifest.description 作路由依据 | §6.1,§2.1,§3.3;术语碰撞见"冲突 4" |
| Agent Status Bar / Context Distillation | **无系统对应**;最近挂钩:`Verdict.InjectMessage`、`RunControl.inject_message`(LoopDetector 纠偏即一次手工注入) | §5.2,§5.4 |
| time sense(读数 + 操作策略) | BudgetGuard / budget.warning 信号(目前只给 sidecar,不给模型) | §5.1,§5.4 |
| Interaction Scaling(观测写回上下文) | sidecar 体系观察运行;TraceRecorder 落盘(但观测不回写帧上下文) | §5,§5.4 |
| Context Rot(质量动机) | 压缩触发仅按 token 阈值;DriftWatcher 经 ForceCompress 预留了质量触发入口 | §7.1 |
| Adaptive Windowing(80% 阈值、批量、`[COMPRESSED]`) | 软上限(窗口 80%)触发,压到目标水位;无防重复标记 | §7.1,§7.5 |
| 五层压缩①工具结果预算控制(落盘+预览+冻结) | `spill` 策略 + blob store + `blob_get` 取回 | §7.2,§8.1 |
| 五层压缩②噪音直接删除 | `truncate` 整组驱逐(按"最旧"而非按价值) | §7.2,§7.5 |
| 五层压缩③API 级微压缩(服务端 context editing) | **无**;可在 Provider 层预留 | §4.1(差距 6) |
| 五层压缩④归档式逐轮摘要(git log 式) | `summarize` 生成 compact note(未规定逐轮独立记录语义) | §7.2 |
| 五层压缩⑤全量压缩 + 失败熔断器 | 硬上限激进压缩,仍超限帧失败上抛;**无熔断器** | §7.1,§3.2 |
| context-aware 压缩(任务意图进压缩 prompt) | `summarize` 固定保留清单(未完成事项/已确认事实/已做决定) | §7.2 |
| 保留优先级清单 / 标识符逐字保留 | 同上一行,清单更短且无标识符规则 | §7.2 |
| Isolation Over Compression(子 agent 隔离) | **collapse_child**:子帧 transcript 弹栈折叠为返回值,结构性免费获得 | §2.3,§7.2 |
| 子 agent 任务描述自包含约束 | 父帧只能经 `input` 传参(语义相同,未写明自包含要求) | §2.3,§3.3 |
| Source Tagging / 指令-数据分离 / 注入防御分层 | ToolGuard 规则 veto + 三层权限 + 沙箱(执行层齐全);**上下文层防御缺失** | §5.4,§8.2,§9.2 |
| 记忆注入(跨会话潜伏攻击) | 黑板/共享服务预留中的安全隐患(开放问题 4) | §14 |
| 详细错误信息四层(恢复率 60%→95%) | 工具失败作为错误观察回写(结构未细化) | §3.2,§8.1 |
| 工具调用计数注释 | LoopDetector 滑窗签名计数(sidecar 侧,模型不可见) | §5.4 |
| TODO 管理工具 | FrameContext.working(scratchpad,无显式 TODO 工具) | §2.3 |
| "压缩即理解"、递归模型调用架构、压缩策略与任务类型耦合 | 压缩器经 `KernelServices` 调 ProviderManager;manifest `context_policy.compress` 按技能声明 | §7.4,§2.1,§1 公理 2 |
| 压缩质量评测范式(固定任务 × 压缩率/轮次/token) | 开放问题 2(摘要保真基准) | §14 |

---

## 一致与相互印证

1. **子帧隔离 = "Isolation Over Compression"**。书中独立得出"隔离优于压缩"的结论:压缩是有损事后补救且每次需额外 LLM 调用,隔离从源头让噪音不进主上下文、不动主上下文缓存前缀;代价是任务描述必须自包含。这与 §2.3 的帧隔离语义(父帧只传 `input`、子帧只回 `result/error`、弹栈时整段 transcript 折叠为一个返回值)及 §7.2 `collapse_child`"最便宜也最重要的一层压缩"完全同构。我们在此基础上额外获得权限与预算的作用域边界(§2.4、§8.2),且书中明确指出"任务描述必须自包含"恰好解释了 §2.3"需要跨帧共享状态时不走后门、显式走内核服务"的合理性。Claude Code Task 工具与 Deep Research 检索子 agent 是该模式的两个生产证据。
2. **"内核拥有循环"的职责划分**。书中"框架的核心职责之一是维护消息列表""模型决定调什么、框架负责执行"正是 §1 公理 1 与 §3.1 的分工;也只有每一步都经内核,状态注入、压缩、记账、信号才有落点——书中全部机制(状态栏、批量压缩、替换冻结)都预设了框架对消息列表的完全掌控,反向印证公理 1 是这些机制存在的前提。
3. **工具结果落盘 + 预览 + 冻结**。五层压缩第①层与 §7.2 `spill` + §8.1 结果归一化(大小封顶 → blob store → `{ref, preview}`)+ `blob_get` 按 ref 取回是同一机制;书中"有损语义压缩 + 无损索引"的表述为 spill 提供了精确的理论定性;"替换串一经生成永久冻结"则补充了我们漏掉的缓存一致性要求(见差距 1)。
4. **阈值触发 + 批量压缩**。Adaptive Windowing 的 80% 触发与 §7.1 软上限(模型窗口 80%)数值一致;"接近阈值批量压缩而非每轮压缩"与 §3.1 每步 `maybe_compress` 检查、超限才动作的设计同向。
5. **LoopDetector 的实验依据**。Exp 2-3 实测滑动窗口丢早期工具结果 → Agent 反复重试同一调用;书中工具调用计数器直接纠偏。这为 §5.4 LoopDetector(同签名 N 次 → `inject_message` 纠偏 → 再犯 `stop`)提供了量化动机,也说明纠偏消息应带明确操作指令(见差距 3)。
6. **执行层防御分层**。书中明确"上下文层防御(来源标注、指令-数据分离、输入消毒)只是第一道防线,执行层靠权限控制、沙箱隔离、高风险操作人工复核"——正是 §8.2 三层权限交集、§9.2 动态代码强制沙箱、HumanApproval sidecar 的结构;两侧对"单层防御不可靠"的判断一致。
7. **压缩需要模型能力、递归调用架构**。书中"Compression is Understanding:压缩模块需要接近主模型的语言能力,形成递归模型调用架构",印证 §7.4 压缩器经 `KernelServices` 调 ProviderManager 而非内核内嵌启发式,也印证 §1 公理 2(压缩器只依赖契约层)的边界划分。
8. **token 口径统一的必要性**。书中全部缓存/压缩决策(80% 阈值、最小可缓存长度、TTL)都以准确 token 计数为前提,印证 §4.2/§7 "统一估算器、口径唯一"与 §13 风险表"估算不一致 → 窗口溢出 → 校准系数 + 10% 余量"的对策。
9. **第三方 Skill 必须加载期审查**。书中指出 Skill 以高权威指令身份进上下文、比网页隐藏文本更危险,安装前须像审查代码一样审查——对应 §6.1 加载期权限闸门(manifest 权限引用存在、不超 RunConfig 上限,缺失即拒绝加载)。
10. **静态前缀内不应放动态数据**。Exp 2-3 的"动态用户配置"反例(把余额等塞进上下文)与我们把预算/用量放在帧 `usage` 与信号里(§2.3、§5.1)而非提示词里的方向一致;书进一步要求动态数据以末尾追加方式进入,这是我们尚未补全的一半(见差距 1/2)。

---

## 差距与可借鉴点

1. **KV/Prompt Cache 意识整体缺失(最大差距)**。书中论证缓存一致性是**前置架构约束**而非事后优化,给出多条可落地规则,我们全部未覆盖:
   - `build_request`(§3.1)必须跨步**逐字节稳定**:指令体、工具/子技能 schema 的顺序与序列化结果固定(Exp 2-3:动态排序工具即破缓存,固定顺序对选择准确率几乎无影响);
   - 动态信息(时间、预算、计数)只允许以**末尾追加**进入,绝不改前缀;
   - 压缩即缓存失效:批量、低频;spill 的 `{ref, preview}` 替换串**一经生成永久冻结**(会话恢复后仍复用);
   - 同模型的子帧与父帧若共享前缀(指令模板、工具集),可命中跨请求 Prompt Cache(书中"子 agent 与父 agent 逐字节对齐")。
   价值:直接关系 TTFT 与计费(缓存读约 1/10 价格),是生产成本的量级问题,且改起来都是契约级小改动,越早纳入越便宜。
2. **Agent Status Bar / Context Distillation(第二大差距)**。我们没有把运行态(步数、token、预算余量、工具调用计数、TODO)系统性注入帧上下文的机制。书中证据强:弱模型 +40~54pp、强模型推理 token 降 80~90%、详细错误信息使恢复率 60%→95%、TODO 使迭代数 21→15。三条工程教训可直接吸收:**用代码(内核记账数据)而非 LLM 维护**;写成 key-value 而非散文;状态栏数据只能来自内核观测、绝不来自工具内容(模型无条件信任 → 投毒即致命)。`RunControl.inject_message`(§5.2)是现成通道,LoopDetector 纠偏就是一次手工状态注入——缺的是每步自动、结构化、由内核数据源驱动的注入点。与我们架构的契合点独特:状态栏数据天然来自帧 `usage`(§2.3)与内核记账(§3.1 步骤 7),可信度高于任何框架外统计。
3. **裸读数不够,必须附操作策略**。time sense 实验:只给 `elapsed_ms=5000` 模型不改变行为(与无信息仅差 2–3pp),附操作指引后 pass rate 约 10% → 40–50%。我们的 `budget.warning`(§5.1)只发给 sidecar;若把预算读数注入上下文,必须同时注入策略(如"预算剩余 20%,收敛到可直接交付的方案");LoopDetector 纠偏消息同理("已重复 3 次:停止重试,改用 X 或宣告受阻")。这是把 sidecar 观测转化为模型行为的通用原则。
4. **context-aware 压缩与保留契约**。Exp 2-9 的最大赢家是把 query 意图与已积累信息写进压缩 prompt(token 降 75%+、轮次最少、3.0% 压缩率仍保关键事实)。§7.2 `summarize` 目前只有固定保留清单,不把帧的任务规格(`input` / pinned 任务描述)传给摘要器;§7.4 `Compressor` 契约未规定摘要必须面向任务裁剪。另可借鉴:五层保留优先级(架构决策/已改文件清单/验证状态/未完成 TODO/工具输出只留 pass-fail)与**标识符逐字保留**(UUID/hash/URL/文件名)应成为 summarize 的契约级约束,而不是实现细节;git log 式逐轮独立摘要(而非 squash)保留了对话逻辑线,与 §7.5"逐出时挂 hook 改道 summarize"的扩展点天然兼容。
5. **压缩鲁棒性三件套**。我们没有:压缩连续失败的**熔断器**(书中生产数据:大量会话死在反复压缩失败上空烧费用;我们的 §7.1 硬上限"仍超限则帧失败上抛"只兜底了结果,没有限制压缩本身的重试);`[COMPRESSED]` 防重复处理标记(hierarchical 链多策略叠加时的幂等保障);`CompressionReport` 未记录本次压缩造成的缓存失效代价,无法做压缩频率的成本核算。
6. **历史思维链 round-trip 与 Provider 能力表达**。行业协议已转向强制回传 `reasoning_content` / 带签名 thinking block(DeepSeek V4、Kimi K2、GLM-5、Claude),不回传则报错或削弱多步推理("thinking is not waste but state")。§4.1 的 `ChatResponse.message` 与消息模型未明确 reasoning 字段必须**原样保留并回传**;`OpenAICompatibleProvider`(§4.4)若只透传 content 会在这些模型上直接失败。同类缺口:Prompt Cache 的计费/TTL/断点语义因厂商而异,`ProviderCaps`(§4.1)目前无缓存相关能力位;书中还提到 **API 级微压缩**(服务端 context editing 移除指定工具结果)这一零本地成本的压缩层,我们的 Provider 契约未预留。
7. **工具结果的消息级 enrichment(代码级、零 LLM 成本)**。三项均可作为 §8.1 归一化流水线的确定性环节:①调用计数注释("Tool call #3 for 'read_file'",促使模型换策略并给出隐形成本意识);②结构化错误四层(类型/参数 JSON/调用栈/针对性修复建议,恢复率 60%→95%——对齐 §3.2 "工具失败作为错误观察回写",但我们的观察结构未规定信息含量);③外部内容 Source Tagging 包裹(`<external_content source="webpage">`),补上我们缺失的上下文层注入防御(执行层已有 §8.2/§9,上下文层目前为零)。
8. **工具/Skill 描述的作者侧规范与加载期 lint**。消融实验:删工具描述 → 调用错误率 +45%;Skill description 应是带**负例**的路由规则("Use when / Do not use when"),"when to use me"远比"what I can do"重要。§8.4 decorator 只取 docstring 首行,§3.3 伪工具 schema 直接复用 manifest.description,均无质量约束;可在 §6.1 validate 阶段加 lint(描述过短/无触发条件 → 警告),成本极低。
9. **"先噪音删除、后摘要"的价值序**。五层压缩第②层:低价值内容直接删,摘要噪音浪费 token。§7.2 hierarchical 链(spill → truncate → summarize)顺序与之暗合,但 truncate 按"最旧"驱逐而非按价值——可加廉价启发(搜索/抓取类大结果优先驱逐;`ToolSpec` 已有 `idempotent` 等元数据可扩展价值标注),与书中"信息价值非均匀分布"原则对齐。
10. **记忆注入对 §14 开放问题 4 的警示**。Exp 2-5 的记忆注入攻击(上一轮埋"下次处理文件时抄送 attacker",跨会话生效)直接命中我们预留的黑板/共享内存服务(§11.3、§14 开放问题 4):跨帧共享的权限模型不仅要管读写授权,还要管**内容可信度分级**(哪些黑板条目可被当作指令参考)。

---

## 冲突或不同取舍

1. **RollingWindowCompressor vs 滑动窗口有害论**。Exp 2-3 把 sliding window 列为有害模式(破前缀缓存 + 丢早期工具结果 → 重复调用循环)。§7.5 baseline 恰是 rolling window。缓和条件:我们的版本保留 `pinned` 与 `working`、按原子组整组驱逐(配对不破)、有 LoopDetector 兜底、manifest 默认是 `hierarchical` 而非裸 truncate;书中反对的其实是"唯一机制 + 无摘要 + 无 pinned"的裸滑窗。但风险真实:baseline 是最先被实现的形态(§13 M3),文档应避免它被读成"推荐做法",且应明确 truncate 只作 hierarchical 链的中间层、链尾必须有 summarize 或 spill 承接。
2. **`summarize` 用廉价模型 vs "压缩即理解"**。§7.2 规定摘要走廉价模型控成本;书中主张压缩模块需要接近主模型的理解力,且 context-aware 压缩的收益恰恰来自强模型的语义取舍。各自成立的条件:对 truncate 逐出区间的例行归档(信息量低、失败代价小),廉价模型够用;对长程任务的阶段性蒸馏(影响后续全部决策),应路由到更强模型。折中:§7.4 `KernelServices` 暴露模型档位选择,manifest `context_policy` 声明该帧的摘要档位——这与书中"压缩策略与任务类型耦合"的判断一致。
3. **每帧独立系统提示 + `model.prefer` vs 跨帧缓存字节对齐**。书中 Claude Code 追求子 agent 与父 agent 请求前缀逐字节一致以共享 Prompt Cache;我们让每个技能有独立指令体与模型偏好(§2.1、§4.2),帧间前缀天然不同。这是有意的取舍:拿缓存复用换隔离、权限与专门化,且 §2.3 隔离的收益(transcript 不进父帧)通常远大于共享缓存的收益。成立条件:同模型、同工具集、高扇出并行子帧(§3.4 `parallel_invoke`)场景,若各分支指令体共享同一稳定前缀模板,仍可命中缓存——值得作为技能作者约定与 Provider 层提示,不宜作为内核强制。
4. **书中 Agent Skills(同上下文渐进披露)vs 我们的 Skill(隔离执行帧)**。术语碰撞但机制正交:书中 Skill 把知识以 tool result 灌进**同一**上下文,解决注意力稀释与 token 经济,不提供隔离/权限/预算;我们的 Skill 压栈执行,恰恰相反。两者不互斥:对我们,"知识包"可作为 prompt 技能指令体的**延迟加载形态**(帧启动不内联全部 SKILL.md,以工具按需读子文档),或未来 `agent_os.services` 的知识服务。评审与文档中必须明确区分,否则两套术语互相污染。
5. **状态栏更新:逐轮替换 vs 持久追加**。书中生产实现(Claude Code `<system-reminder>`)采持久追加(完全保缓存,容忍过期状态累积);`RunControl.inject_message`(§5.2)目前是单次注入,未规定过期语义。若实现状态注入,需按轨迹长度选择:短轨迹/大状态用逐轮替换(失效限于末尾几轮,上下文干净),长轨迹用持久追加——不能无思考地每轮替换,否则在长 run 里缓存成本反超收益。
6. **窗口规模假设不同**。书的默认画面是单上下文 + 大窗口(128K–1M),机制围绕"一个不断变长的消息列表"展开;我们是帧树 + 每帧小上限(manifest `max_tokens` 如 32k,§2.1)。书中部分机制(五层压缩的全部层次、API 级微压缩)在小帧里性价比下降;但帧内多步 loop 仍会快速积累工具结果,且"小窗口更早撞 rot"反而强化了状态栏与 context-aware 压缩的价值。取舍成立,但意味着我们应有选择地移植而非全量照搬。

---

## 行动建议

按优先级排序(标注影响子系统 / 设计文档小节):

1. **【高】把"前缀稳定性"写进设计不变量**。规定 `build_request` 输出跨步逐字节稳定(指令体与 schema 顺序固定、序列化规范化);动态数据只经末尾追加;spill 替换串一经生成冻结;压缩批量低频。影响:§3.1(loop 步骤 3)、§7.3(新增不变量:前缀稳定/缓存意识)、§7.2 spill、§8.1 归一化。子系统:Kernel Runner + Context Compression。验收:golden-file 断言相邻两步请求前缀 diff 为空。
2. **【高】新增内核维护的帧状态注入(Agent Status Bar 对应物)**。每步由内核用记账数据(帧 `usage`、RunConfig 预算余量、工具调用计数、步数)生成 key-value 元消息,以 user-role 追加到帧上下文末尾,附操作策略文本;数据只允许来自内核观测;LoopDetector/BudgetGuard 的纠偏消息复用同一通道并带操作指令。影响:§3.1(loop 新步骤)、§2.3 FrameContext、§5.2 inject_message 语义、§5.4 LoopDetector/BudgetGuard。子系统:Kernel Runner + Sidecars。依据:弱模型 +40~54pp、强模型省 80~90% 推理 token、错误恢复 60%→95%。
3. **【高】`summarize` 升级为 context-aware 并扩充保留契约**。压缩调用传入帧任务规格(`input`、pinned 任务描述);保留清单扩为:架构决策与关键约束、已修改文件清单、验证状态、未完成 TODO、标识符逐字保留;摘要模型档位可配。影响:§7.2 summarize、§7.4 Compressor 接口与 KernelServices、§2.1 context_policy。子系统:Context Compression。
4. **【中】Provider 消息模型保证 reasoning 字段 round-trip**。消息模型显式携带 `reasoning_content`/thinking block(含签名),归一化不丢字段;`ProviderCaps` 增加 CoT 回传协议与缓存计费/TTL 能力位;`OpenAICompatibleProvider` 与 `MockProvider` 相应支持,golden-file 覆盖。影响:§4.1、§4.2、§4.4。子系统:Providers。
5. **【中】§8.1 归一化流水线增加三个代码级 enrichment**:工具调用计数注释、结构化错误四层(类型/参数/调用栈/修复建议,对齐 §3.2)、外部内容 Source Tagging 包裹(`http_fetch` 等 NET 级工具结果)。影响:§8.1、§8.4 内置工具、§3.2。子系统:Tool Registry。
6. **【中】压缩鲁棒性三件套**:连续失败熔断(N 次失败后该帧只用 spill/truncate)、`[COMPRESSED]` 幂等标记、`CompressionReport` 增加缓存失效估算。影响:§7.1、§7.4、§7.5。子系统:Context Compression。
7. **【低】描述质量 lint**:加载期校验 Tool/Skill description 是否含触发条件与边界(鼓励 "Use when / Do not use when" 与负例),警告不阻断。影响:§6.1 validate、§8.4。子系统:Skill Registry + Tool Registry。
8. **【低】压缩评测资产**:参照 Exp 2-9 范式建基准(固定多步研究任务,对比 truncate/summarize/context-aware 的轮次、token、成功率),回应 §14 开放问题 2。影响:§13 测试策略。子系统:测试/评测。
9. **【低】文档消歧与术语保护**:在 §6 注明本书"Agent Skills"(知识包渐进披露)与本设计 Skill(执行单元)的区别;知识包形态作为 prompt 技能延迟加载的候选扩展记入 §14 开放问题;记忆注入风险写入 §14 开放问题 4(黑板内容可信度分级)。影响:§6、§14。
10. **【低】工具价值标注**:truncate 驱逐顺序从"最旧"升级为"最旧且低价值"(搜索/抓取类大结果优先),`ToolSpec` 可选价值提示字段。影响:§7.5、§2.2。子系统:Context Compression + Tool Registry。
