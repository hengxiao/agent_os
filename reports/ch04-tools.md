# Chapter 4《Tools》与 Agent OS 设计对比报告

> 对比对象:`ai-agent-book/book-en/chapter4.md`(全书第 4 章,672 行,已分页完整通读)
> 基准:`DESIGN.md` v0.3。引用格式:§x.y 指设计文档小节;「书 …」指章节内小节标题或术语。

## 章节内容概要

本章围绕两大挑战组织:**工具选择**(工具数以百上千时如何找得准、如何从被动选择走向主动发现)与**异步与事件**(长任务不阻塞、随时可中断、外部事件可唤醒)。关键概念、模式与机制清单:

### 工具分类与通用设计原则

- **五大工具分类**(Table 4-1):Perception / Execution / Collaboration / Event-Triggered / User Communication,按"调用方向(谁发起)× 作用目标"刻画;前三类 Agent 主动调用,后两类必须架在事件驱动架构上。
- **能力表达形式抉择**:Dedicated Code Tools vs Skills + General Executors;三维决策框架 = Parameter Complexity(参数复杂度)/ Frequency of Change(变化频率)/ Model Capability(模型能力)。本章结论与第 5 章"七个核心工具 + Skill 文档"路线呼应。
- **ACI(Agent-Computer Interface)原则**:工具面向 Agent 目标而非 API 端点设计。细则:
  - 粒度取舍(integration vs separation):功能相似 + 场景重叠才合并;工具数过百后模型开始选错。
  - 通用工具优先:`code_interpreter` 替代一打专用计算器("meta-capability"),除非有安全/权限/性能理由(如生产库写操作需要专用工具的细粒度权限与审计)。
  - 描述的艺术:写"when to use"而非"what it does";显式边界与反例("大多数工具调用失败的根因不是不知道能做什么,而是不知道不能做什么");参数给具体样例(RFC3339 带 `2024-03-15T14:30:00Z`);返回值结构说明;耗时提示;1-5 个真实调用示例(基准中准确率 72%→90%);调试纪律 = 选错工具先查描述,别先怀疑模型。
- **Fidelity of Parameter Passing(参数保真)**:禁止 silent input transformation(书中实例:Cursor 编辑工具把中文弯引号静默转直引号,模型读到的与替换用的不一致,陷入无法自诊断的失败循环)与 silent parameter injection(bash 工具给 `git commit` 私加参数导致旧版 Git 失败)。核心命题:**"the world the model perceives"与"the world the tool operates on"之间不得有系统性偏差**;必要的归一化必须写进描述并在返回值中显式告知。
- **工具设计三代演进**:一代 = API 直接封装(过细);二代 = ACI(面向 Agent 目标);三代优化三件事——example-driven invocation(怎么调准)、dynamic discovery(怎么找到)、**code orchestration execution**(怎么串联:LLM 一次性生成编排脚本,中间变量留在执行环境,只回传最终结果,token 消耗可降约两个数量级,机制细节留给第 5 章)。

### MCP 与工具生态

- **MCP 要点**:client-server 架构;JSON Schema 工具描述;stdio(本地)/ Streamable HTTP(远程,SSE 方案已废弃);tools / resources / prompts 三原语分离("模型可执行的操作 / 应用可读的数据 / 用户可选的模板");生态价值 = "develop once, use everywhere"。
- **MCP 三个实践难题**:
  - 同步 request-response 无法唤醒不在场的 Agent(通知原语都在单会话内)——事件驱动架构必须建在协议之上;
  - 上下文开销:5 个 server ≈ 5.5 万 token 定义,占 200K 窗口近 30%;Cursor 的"描述同步到文件夹、默认只见索引、按需查询"省 46.9%;
  - 工具过载:按信息源类型分层组织(search / read / parse / query),系统提示中显式声明分类;动态按需检索(Anthropic 实验:Opus 4 工具使用准确率 49%→74%);Skills 把"选择问题"变成 LLM 擅长的"知识检索问题"。
- **MCP 信任模型四类风险**:tool description poisoning(描述逐字进上下文、每 session 生效的 prompt injection 变体)、malicious/compromised server(供应链与远端被攻破)、tool shadowing(同名/近似工具遮蔽,劫持含敏感参数的调用)、credential 风险(OAuth token 被骗用)。缓解 = 描述按不可信输入审查、锁定 server 版本拒绝静默更新、最小权限凭证且设过期。运行期兜底 = 后文 Sidecar 机制(只看结构化数据,抗 rhetoric 操纵)。并引出第 5 章的 **Lethal Triad**(私有数据 + 不可信内容 + 对外通信三要素齐备即成攻击闭环,持久记忆进一步放大)。

### 三类主动调用的工具

- **Perception Tools**:输出常远超上下文承受能力,通用解法是工具级 **context-aware compression**(>1 万字符时按当前查询意图压缩);搜索工具返回结构化候选列表(标题/位置/摘要)而非全文拼接,多结果给 pagination/cursor 并在返回值中注明总数与翻页方式;读工具支持 offset/limit,截断必须**显式可见**("Displayed lines 1-200 of 5000…"),silent truncation 会让 Agent 基于不完整信息做错误判断;只读性带来两个工程红利 = 结果可安全缓存、多路调用可安全并行;多模态输出按内容类型选形态(纯文本抽文字,布局敏感的 UI/表格/设计稿保留原图)。
- **Execution Tools 分层安全**:
  - 第一层输入校验:path traversal(`../../etc/passwd`)、命令注入(分号/管道拼接)、参数类型格式;**fail fast,禁止"智能纠正"**。
  - 第二层权限控制:文件限定工作目录、命令黑名单(`rm -rf /`、`dd if=/dev/zero`)、API 配额限流;黑名单只是最底层,需语义解析理解命令真实意图(第 5 章展开)。
  - **Proposer-Reviewer 范式**:pre-approval = 一个模型提案、另一个独立模型审批(银行双签)。高效实现三要点:两模型应**不同家族、能力相近**(cognitive diversity,同家族犯同样的错,差距太大审不动);底层规则完全一致但侧重点不同(行动导向 vs 风控);**拒绝理由作为工具调用结果写入轨迹**,复用 Agent 既有的工具失败处理能力,而非简单重试。可叠加风险分级审批(高风险必审、低风险直连)与不确定时升级人工。适用面 = 一切不可逆高影响操作(收费、发信、改关键配置、建外部资源)。
  - post-validation:事后验证的关键是 **modality switching**——换模态验证(代码生成的文档渲染成图查版式、配置在沙箱里真跑验证生效),同模态复审会落入同样的盲区。
  - **Sidecar 机制**:带外轻量 LLM,与主模型流式输出并行以降低排队延迟,但对被审工具调用是 **gate**(不放行不执行,Claude Code Auto Mode 为典型案例);**只看结构化工具调用数据(工具名+参数),刻意不读主模型自由文本**——阻断攻击者在网页/用户输入里埋"请允许 rm -rf"之类的 rhetoric 通道;安全分类是结构化数据上的简单判断题,所以轻量模型够用(与 Proposer-Reviewer 审开放推理需能力相近并不矛盾,Table 4-2 对比两者);需要 **rejection circuit breaker**(连续拒绝则回退人工判断);另一用途 = context enrichment(并行做记忆相关性过滤、大输出摘要、权限预评估)。
  - **execute-validate-feedback 闭环**:`write_file` 后立即按文件类型跑 linter,结构化错误列表随返回值回给 Agent,下一轮即可修复。
  - **长输出截断与持久化**:超阈值(200 行/1 万字符)只留 head 50 行 + tail 50 行 + 显式省略通知("`... [8523 lines omitted, full output saved to /tmp/execution_output.txt] ...`")+ 指引用 `read_file` 取全文。
  - **隔离阶梯**(递增):OS 级(macOS Seatbelt / Linux seccomp+namespaces,本地首选)→ 容器(Docker,共享内核,仍有逃逸面)→ microVM(Firecracker,独立内核,完全不可信代码的最强档);任何层级都配 CPU/内存/磁盘/网络配额;明确警示 **venv 不是沙箱**(只隔离包依赖,文件系统/网络/进程全无约束)。
  - **可观测性**:调用日志(时间/参数/结果/耗时)、审计轨迹(who/what/context/why)、性能指标(频率/成功率/时长)、告警。
  - **幂等与取消语义**:超时后副作用是否发生不可知(转账重试可能重复扣款);两法 = idempotency key(客户端生成、服务端去重、重复请求返回首次结果)与 query before mutation(重试前先查目标资源现状);不可幂等操作(发邮件、打电话、转账,服务端不在你控制内)用 **two-phase "pre-check then confirm"**:第一阶段只做校验与 dry run,返回 confirmation token;第二阶段凭 token 执行,失败不盲目重试原阶段,回上层重新 pre-check。
- **Collaboration Tools**:子 Agent 价值 = 分工专业化(各自独立优化 prompt/工具/知识库);提示词四要素 = 角色定义、**上下文来源标注**(`[FROM_MAIN_AGENT]`/`[FROM_USER]`/`[TOOL_RESULT]`,防来源混淆与注入)、任务边界、输出格式(统一 JSON);接口三族原语 = spawn/cancel(`spawn_subagent`/`cancel_subagent`)、message passing(`send_message_to_subagent`,运行中补充指令/追问/汇报)、discovery(`list_agents`,类比 MCP 的 `tools/list`);四种协作模式 = 同步调用 / 异步(task ID + 完成事件)/ 流式 / 多轮;HITL 工程 = 超时与默认行为("5 分钟无响应走保守策略")、优先级队列(紧急多渠道、例行走邮件)、**决策记录回流学习**(post-training 成监督数据,或 externalized learning 存案例库供检索引用)。

### 事件驱动异步架构

- **为什么必须异步**:长任务不应阻塞交互;事件有优先级差异(取消当前/排队/并行三种处置);中断与恢复要流畅。根本矛盾 = **synchronous training / asynchronous deployment**(API 要求 tool call 之后紧跟 tool result,现实却随时有插入)。
- **现状案例**:OpenClaw 的 Hooks(生命周期)/ Cron / Heartbeat 都只是 time-driven,对第三方事件源(新邮件、API 回调)无即时入口;PineClaw 的 **Channel 机制**(电话场景:身份验证、三方通话、谈判确认都需秒级触达用户)给出实时事件通道,标志从 time-driven 到 event-driven 的演进。
- **Event-Triggered Tools 三件套**:`set_timer`(一次性 + 周期性,周期轮询也是不支持主动通知的服务的兜底,OpenClaw Heartbeat 即其系统化)、`monitor_shell`(后台任务输出监控,含关键词过滤,避免"盯着命令行"烧 token 或干等错过问题)、`connect_channel`(外部事件源接入)。设计要点:触发条件与过滤规则要清晰(防无关事件空耗),事件 payload 要带足上下文(减少唤醒后的追加查询)。
- **User Communication Tools**:OpenClaw 式会话透明 + 双向随时发消息,"说话"本身成为显式工具调用(可带附件、按紧急度触发推送);多渠道(IM/SMS/邮件/电话/push)按紧急度+用户状态+内容性质+偏好选择,兼作用户召回(re-engagement)机制;与协作工具的边界 = 看接收者是审批者/协作者还是最终用户。
- **虚拟身份与隔离执行环境**:给 Agent 独立身份(专用邮箱/电话,像秘书有自己的办公电话)而非直接操作用户账号;虚拟电脑(VM/容器)与虚拟手机(Android 模拟器)把沙箱概念从"隔离代码执行"扩展到"隔离整个数字身份";两个现实挑战 = 反自动化机制(CAPTCHA/IP 信誉,需住宅代理)与访问用户真实账号(HITL 认证:VNC/RDP 里用户亲自登录,session token 复用);主 Agent 与虚拟环境间用**共享文件系统**(volume mount)以**文件路径引用**传数据,不消耗上下文。
- **事件处理机制**:
  - 骨架 = 事件循环 + **safe point** 纪律:事件只在循环边界被消费,LLM 推理/工具执行中途不打断;取消也在 safe point 检查(类比 Go `ctx.Done()`)。
  - **structured event modeling**:source(who)/ channel(how)/ content(what,含情绪、紧急度、是否需回复)/ context(背景,与当前任务相关性)——统一建模后才谈得上多线程上下文理解与注入防御。
  - 三种策略:**cancellation-based**(紧急事件:中断流式、取消工具、清空队列、事件并入轨迹尾部、立即重调 LLM——本质是"强制提前制造 safe point")、**queued**(常规事件:入队等待,任一 tool.result 返回时批量追加,一次让 LLM 综览)、**parallel**(独立轻量查询:另起并行会话,结果标注"与主任务并行执行"再并入轨迹)。
  - 紧急度判定:硬编码规则有局限,事件语义决定处置;推荐**轻量分类 LLM 做 event router**。
  - **同步模型支撑异步中断的五条工程规则**:assistant 消息(含 thinking/content/tool call)立即落轨迹;tool result 只在完成时落;**工具执行中被中断 → 生成占位 tool result**("The tool is executing in the background, please prioritize the new event")保证 call/result 配对后再追加中断事件;LLM 思考中被中断 → 丢弃当次思考不入轨迹;非中断事件入队批处理。正常时 LLM 看到完美同步轨迹;风险 = 占位符诱发幻觉(训练分布里 call 后必紧跟真结果),故只在真正紧急时中断。
  - **异步工具接口**:`initiate_phone_call` 式命名把"发起"与"完成"解耦,立即返回 task ID 与初始状态,进度走事件通知;名称与描述本身要传达异步语义,靠模型的语言理解力自然推断。
  - **批处理的注意力分散**:模型倾向只关注最后一个事件;干预 = prompt 层声明 + **Agent Status Bar 标记**(`[Unprocessed Event 1/4] …` + 末尾汇总"共 4 条未处理,含 1 工具结果、2 用户消息、1 系统提醒")。
  - 深层方向:异步能力最终要靠 RL 训练内化(理解乱序轨迹、恢复被打断的任务与思考、综览批量事件),基础设施 = 异步环境模拟器 + 专项奖励;过渡期内,"连续思考"(打断时不丢弃思考,强闭 `<think>` 块、注入观察、继续解码,约 200 行编排即可把现成模型变 continuous-time Agent,等待时间即免费算力)是工程折衷的极致形态,且"编排让行为可能,训练让行为有用"。

### 主动工具发现(Proactive Tool Discovery)

- **从被动选择到主动发现**:一次性全量注入在千级工具下崩溃;检索预筛选只在任务开始前匹配一次,预见不了多步跨域工具链。**MCP-Zero**:不预载任何 schema,Agent 在思考中发出结构化请求块声明能力缺口,系统按 server 级 → tool 级两级语义匹配后注入,约 2800 工具下 token 省 ~98%;工程等价物 = 只留基础工具 + 一个"tool search tool"(Anthropic Tool Search Tool、OpenAI Responses API `tool_search` + `defer_loading: true`、Claude Code `tool_reference`、Codex CLI 的 BM25 `tool_search` 常驻架构)。共同范式 = Agent 声明缺口,系统按需注入。
- **层级匹配与回退**:利用 server 分组把搜索空间从"数千工具"缩到"数十 server × 数十工具";两层候选都低于阈值时返回显式 "not found",让 Agent 换措辞重试、用基础工具凑合或造新工具(第 8 章)。
- **动态加载与 KV Cache**:新工具 schema 追加在上下文尾部、静态前缀不动;此后钉在原位作为普通历史(不是每轮重新搬到最新处),仅 TTL 过期或增删改工具集才触发重算。代价 = 模型需经训练才能理解散落在轨迹中段的工具定义。
- **Skills 渐进披露(Progressive Disclosure)**:启动只见 name+description 薄目录(几百 token),按当前上下文需要逐层翻阅子技能/脚本/子文档;不需要 embedding 索引,grep + 读文件即可,是更低维护的发现方式;配套可组合 KV Cache(预编译各 skill 的 KV 表示,RoPE relocation 以 O(L) 粘贴到任意位置,小改动增量打补丁),让反复加载不亏延迟。
- 六个实验(4-1~4-6):三类工具集 MCP server → 事件驱动邮件 Agent → 并行执行/中断恢复/状态管理 → 百级工具下 Qwen3-4B 的主动发现对照实验(50K token 全量注入 vs `discover_tools` 混合方案)。

## 与 Agent OS 设计的映射

| 书中概念 | Agent OS 对应 | 设计文档小节 |
|---|---|---|
| 五类工具中的 Perception / Execution | Tool 原子能力;权限级 READ/NET ≈ 感知,WRITE/EXEC ≈ 执行 | §2.2、§8.2 |
| Collaboration / Event-Triggered / User Communication | 无直接对应(子技能调用只覆盖协作的一小部分;后两类依赖事件架构) | §3.3、§14 |
| ACI 描述规范(何时用、边界、示例) | `ToolSpec.description` + JSON Schema `parameters`;baseline 从 docstring 首行推导 | §2.2、§8.4 |
| Fidelity of Parameter Passing | 分发流水线"schema 校验不合法 → 错误观察,不执行",无静默纠正 | §8.1 |
| code orchestration execution(三代工具) | Logic Kernel 是唯一代码执行点;code 技能可做"编排者" | §9、§9.3 |
| MCP 适配 | "MCP 适配器作为独立包后续接入,不进入内核" | §8.3 |
| MCP 信任模型 / description poisoning / shadowing | ToolGuard(规则 Veto)+ 三层权限交集;semver 寻址与版本钉住 | §5.4、§8.2、§6.1 |
| Sidecar 机制(书中的) | **Sidecar 子系统(同名同构)**:信号触发、SYNC 可否决、fail-closed | §5、§5.2、§5.3 |
| rejection circuit breaker | LoopDetector 的"先 inject 纠偏、再犯 stop"升级链 | §5.4 |
| Proposer-Reviewer pre-approval | HumanApproval sidecar(EXEC 级 `pre:tool.call` 闸门)+ Verdict.Veto | §5.4、§8.1、§5.2 |
| post-validation / modality switching | 无直接对应(最接近的是 Skill outputs schema 校验) | §2.1 |
| execute-validate-feedback(linter) | 无对应;结果归一化不含自动验证 hook | §8.1 |
| 长输出 head+tail 截断 + 存文件 | 归一化"大小封顶 → spill 到 blob store,返回 {ref, preview}";`blob_get` 取回 | §8.1、§7.2 spill |
| 感知工具 pagination / offset-limit / 显式截断 | spill 的 preview + ref 机制;工具约定层面未规范 | §7.2、§8.4 |
| 工具级 context-aware compression | 压缩作用于帧上下文整体,无"按查询意图压单个输出" | §7 |
| 只读 → 可缓存/可并行 | `ToolSpec.idempotent` 标志(供重试与 sidecar 判断) | §2.2、§8.1 |
| 隔离阶梯(seccomp→容器→microVM)+ 配额 | `PythonSandboxLogicKernel` v1 = subprocess+rlimits;重隔离以后端替换接入 | §9.2 |
| 可观测性(日志/审计/指标/告警) | TraceRecorder 全量落盘 + 信号目录 | §5.4、§5.1 |
| 幂等 key / query before mutation / two-phase confirm | `idempotent: true` 才内建重试;两阶段确认无对应 | §2.2、§8.1 |
| 子 Agent 原语(spawn/cancel/message/discovery) | 子技能伪工具(同步压栈、单次进出);RunControl 仅 sidecar 可用 | §3.3、§5.2 |
| 上下文来源标注(防注入) | FrameContext 消息历史,无来源标注约定 | §2.3 |
| 事件循环 / safe point / 三策略 | runner 每步循环开头检查控制标志(= safe point);asyncio cancellation 传播;Pause/Stop | §3.1 |
| 中断时占位 tool result(五条规则) | **无对应**,且与压缩不变量 2(配对原子性)直接相关 | §3.1、§3.2、§7.3 |
| 异步工具接口(initiate/complete 解耦) | Tool 契约"无调用栈、单次进出" | §2.2 |
| set_timer / monitor_shell / connect_channel | 无对应(信号总线只承载内核生命周期信号) | §5.1 |
| structured event modeling / event router | 无对应 | §14(可作为开放问题方向) |
| 虚拟身份 / 凭证最小权限 | ToolContext 注入工作目录/blob/logger,无凭证作用域 | §2.2 |
| 共享文件系统传路径引用 | blob store + 工作目录约定 | §7.2、§8.4 |
| Proactive Tool Discovery / 层级匹配 / KV Cache 优化 | 反方向:manifest 静态白名单 + `visible_to`,帧内可见集固定(前缀天然稳定) | §2.1、§6.2、§3.1 |
| Skills 渐进披露(书中的 Skill=文档) | 我们的 Skill=带帧函数;仅形式相似(prompt 技能 entry 也叫 SKILL.md) | §2.1 |
| HITL 超时兜底 / 决策回流学习 | HumanApproval 只在表中出现,学习闭环属范围外 | §5.4、§14 |

## 一致与相互印证

1. **Sidecar 机制几乎逐点同构**。书中 Sidecar(独立带外校验、对工具调用做 gate、与主流式并行降延迟、轻量模型足够)与 §5 的信号驱动 sidecar(SYNC 在关键路径可否决、ASYNC 纯观察、supervisor 托管、fail-closed)是同一架构直觉的两次独立得出,连名字都一样。书中 rejection circuit breaker 与我们 LoopDetector"先 inject 纠偏、再犯 stop"(§5.4)是同一升级逻辑。
2. **"fail fast,不做智能纠正"与 §8.1 完全一致**。书把参数保真上升为原则("模型感知的世界与工具操作的世界不得有系统性偏差"),我们的"schema 校验不合法即错误观察、不执行"正是该原则的内核化——不靠工具作者自觉,而由分发流水线强制。
3. **spill 机制被书中长输出处理验证**。head/tail 截断 + 全文落临时文件 + 指引 Agent 用读工具取回,与 §7.2 spill + §8.1 归一化 + `blob_get` 是同一模式;"显式省略通知、禁止 silent truncation"与我们 spill 的隐含约定一致。
4. **沙箱阶梯与 §9.2 完全对齐**。OS 级 → 容器 → microVM 递增、任何层级配资源配额;我们 v1 选 subprocess+rlimits、重隔离以 LogicKernel 后端替换接入且契约不变——书的分层为这条演进路径背书;"venv 不是沙箱"的警示印证我们不依赖 venv 的决定。
5. **code 技能编排者定位被"三代工具"强化**。code orchestration execution(中间变量留在执行环境、token 省约两个数量级)正是 §9.3 code 技能"确定性控制流 + 按需调 LLM 技能"的价值主张,书给出了量化收益论证。
6. **通用工具优先与内置工具集一致**。书主张 `code_interpreter` 类元能力替代大量专用工具(对应 §9.4 `python_exec`),同时承认特殊权限/审计场景仍需专用工具——这正是 `python_exec` 定为 EXEC 级、走三层权限交集(§8.2)的理由;粒度取舍中"高频功能保持独立"(如专用 grep 工具)也支持我们内置 `fs_read/fs_write` 而非全推给 shell。
7. **safe point 纪律与 runner 控制模型一致**。事件只在循环边界消费、取消在 safe point 检查;§3.1"每步循环开头检查控制标志 + asyncio cancellation 传播"是同一纪律的实现;`parallel_invoke`(§3.4)与书中 parallel 策略(独立轻量查询另起会话)同构。
8. **版本钉住与供应链缓解暗合**。书要求"锁定 server 版本、拒绝静默更新";§6.1 热重载"新帧用新版,在跑帧钉住旧版"天然满足运行期不漂移;`<namespace>:<name>@<semver>` 寻址可直接用于缓解 tool shadowing。
9. **三维决策框架印证 Skill 双形态**。书的 Parameter Complexity / Frequency of Change / Model Capability 框架,与 §2.1 `kind: prompt | code` 的划分逻辑同向(复杂参数走结构化 schema、稳定底层走 code、易变 know-how 走 prompt 文档),可作为 §2.1 的理论注脚。

## 差距与可借鉴点

1. **中断时的轨迹修复(最重要的洞)**。书的 Rule 3(工具执行中被中断 → 生成占位 tool result,保证 call/result 配对)直接命中我们的空白:§3.1 中 sidecar `Stop`/取消经 asyncio cancellation 传播,若落在 `kernel.tools.dispatch` 执行中途(§3.1 第 6 步),assistant 的 tool_call 已入帧上下文而 result 永远不会到达——恰好破坏 §7.3 不变量 2(配对原子性),只是破坏源不是压缩而是中断。占位结果机制是标准修法,且与压缩子系统的原子组构造(§7.5)天然兼容。书还提醒占位符有幻觉风险,宜只在真正紧急时触发——这与我们"停止/暂停是特例路径"的定位相符。
2. **Sidecar 的"输入最小化"安全原则**。安全 sidecar **只看结构化工具调用数据,刻意不读主模型自由文本**,阻断 prompt injection 经 rhetoric 通道影响审批。§5.2 契约对 sidecar 可见数据无任何约束,`Signal` 载荷默认全量。这条原则应写入契约层,尤其对将来引入的 LLM 驱动 sidecar(当前 ToolGuard/CodeScanner 都是规则/静态模式)。
3. **Proposer-Reviewer 的两个具体机制**。(a) **拒绝理由作为工具结果回写轨迹**——把审批否决复用为 Agent 已有的"工具失败自恢复"能力;我们的 Veto 只说拒绝,未规定 reason 以何种形态进帧上下文。(b) 审批模型应**不同家族、能力相近**(cognitive diversity;同家族犯同样的错,差距太大审不动)——HumanApproval 升级为模型审批时的关键工程细节。另可叠加**风险分级审批**(高风险必审、低风险直连),与我们权限分级(§8.2)天然对接。
4. **post-validation / modality switching 与 execute-validate-feedback**。§8.1 对工具结果只有大小归一化,没有"结果正确性"的自动验证 hook。书的模式很轻:写文件后自动跑 linter、结构化错误随返回值回给 Agent;换模态验证(渲染图查版式、沙箱真跑配置)则提示验证要尽量换一个观察通道。可实现为内置 sidecar 或工具包装器,不动契约。
5. **幂等语义的工程深度**。我们只有 `idempotent` 布尔位(§2.2);书给出 idempotency key(客户端生成、服务端去重)、query before mutation,以及不可幂等操作的 **two-phase confirm**(dry run + confirmation token,失败回上层重新 pre-check 而非盲目重试)。对 EXEC 级工具(发邮件、转账类),这是比人工审批更细的结构性防线。
6. **工具级 context-aware compression**。我们的压缩作用于帧上下文整体(§7);书的感知工具在输出超阈值时**按当前查询意图**压单个输出——压缩目标更精准(知道为什么查)、损失更小,可作为 `Compressor` 新策略或归一化的可选档,与 spill 并列。
7. **rejection circuit breaker 的显式化**。连续拒绝 → 回退人工,防"重试烧资源 + 用户被困在循环里"。我们有 LoopDetector 的升级链但只针对循环调用;审批路径上的熔断没有显式机制。
8. **MCP 供应链安全清单**。ToolGuard(§5.4)只做运行期规则匹配;书的四类风险提示 §8.3 的 MCP 适配器需要加载期缓解:描述按不可信输入审查、版本锁定(semver 寻址 + 钉住已具雏形)、同名工具 namespace 隔离、**最小权限凭证作用域**——ToolContext 目前没有凭证概念,是真空洞。
9. **事件驱动入口(范围外但路径清晰)**。事件队列、structured event modeling、cancel/queue/parallel 三策略、轻量 LLM event router、`initiate_X` 异步命名约定、`monitor_shell` 监控模式,构成完整的"外部世界唤醒 Agent"设计。我们的信号总线(§5.1)是天然接入点(外部事件 → 信号 → Run/帧注入),§14 值得把这条路径写成开放问题而不是空白。
10. **协作工具的 message passing / cancel 原语**。子技能调用单次进出(§3.3),父帧无法在子帧运行中补充指令或主动取消(RunControl 只给 sidecar,§5.2)。`send_message_to_subagent` / `cancel_subagent` 提示:长运行子帧需要帧间邮箱,可与 §14 开放问题 4(黑板服务)统一考虑。
11. **上下文来源标注**。子 Agent 提示词中显式区分 `[FROM_MAIN_AGENT]` / `[FROM_USER]` / `[TOOL_RESULT]`,是便宜的注入缓解;FrameContext 消息构造(§2.3)没有来源标注约定,可零成本采纳为内置约定。
12. **工具描述的工程细节**。可操作的清单(何时用、边界反例、参数示例、返回值结构、耗时提示、1-5 个调用示例)与"先查描述再怪模型"的调试纪律;§8.4 的"docstring 首行 → description"过于单薄,`ToolSpec` 缺 examples 字段。书中的对照数据(72%→90%)说明这不是润色而是一等工作。
13. **HITL 的超时兜底与优先级队列**。HumanApproval(§5.4)目前只是"挂起等待人工批准",没有超时默认行为("5 分钟无响应走保守策略")与紧急度分级通道;这在常驻/无人值守场景是必需品。

## 冲突或不同取舍

1. **动态工具发现 vs 静态白名单**。书的基调是工具多到必须运行期发现(MCP-Zero、tool_search、defer_loading);我们的基调是每个 Skill 在 manifest 静态声明白名单、加载期校验(§2.1、§6.1),帧内可见集固定。各自成立的条件:书面向"几百上千个第三方 MCP 工具"的开放生态;我们面向受控部署,用静态可分析性、最小权限与 KV Cache 稳定(帧内工具集不变 → 前缀稳定;书自己也承认动态加载会失效 KV Cache、弱模型还会在非常规位置产生畸形调用)换取可审计与安全。**不冲突,是不同部署形态的最优解**;接入大型 MCP 生态时可在白名单内加层级命名空间与"白名单内 discover"的折中。
2. **书中 Skill(渐进披露文档)vs 我们的 Skill(带帧函数)**。书的 Skill 注入同一上下文、省一次 LLM 调用、KV Cache 友好,但无权限隔离、无输出校验、无独立预算;我们的 Skill 有 typed I/O、独立 FrameContext、独立 usage 记账(§2.1、§2.3),代价是延迟与 token。书自己的三维决策框架恰好解释何时用哪种:轻量易变 know-how 用文档式;需要隔离、审计、组合的行为单元用帧式。两者可共存——prompt 技能内部同样可以渐进披露子文档。
3. **子 Agent 控制原语暴露给模型 vs 收到 sidecar 特权通道**。书把 `cancel_subagent`、`send_message_to_subagent` 做成模型可调工具;我们把 RunControl(§5.2)严格限定 sidecar 专属,模型只能经伪工具发起同步调用(§3.3)。我们的取舍在多租户/不可信内容场景更安全(控制面与数据面分离),书的取舍在单租户、灵活编排场景更顺手。若未来开放,应走"内核中介 + 权限检查"的子集,而非直接暴露 RunControl。
4. **书中 code orchestration 要求沙箱内回调工具 vs 我们 SANDBOX v1 纯计算**。书的编排范式里,LLM 生成的脚本在执行环境内直接调工具;§9.3 明确 SANDBOX v1 不持有内核回调,跨进程回调通道列为开放问题(§14 问题 5)。书的两个数量级 token 收益是该开放问题最有力的"值得做"论据;真正张力在于回调通道越大、沙箱隔离价值越稀释。成立条件:回调全部回到内核分发路径(白名单、信号、记账一样不少)——这正是 §9.3 已定下的约束。
5. **LLM 做审批/路由 vs 规则确定性**。书多处用轻量 LLM(安全分类器、event router、紧急度判断);我们内置 sidecar 全是确定性规则(§5.4、§5.5)。契约(§5.2)对实现方式中立,故不矛盾;但引入 LLM sidecar 会放大 SYNC 超时与 fail-closed 语义(§5.3)的重要性,rejection circuit breaker 应一并引入。
6. **批处理事件 vs 帧隔离**。书的 queued 策略把多源事件批量并入同一轨迹;我们的 FrameContext 严格私有、观察只来自本帧的调用(§2.3)。若未来接事件源,批量注入会稀释"帧上下文只含本帧任务相关观察"的纯净性,需要书的 status bar 标记类技术维持模型注意力——属于采纳事件架构时的配套成本,现在不必付。

## 行动建议

按优先级排列(标注影响的子系统 / 设计文档小节):

- **P0 — 补齐中断时的配对修复**:在 §3.1 第 6 步 dispatch 与 §3.2 错误处理中规定:工具/子技能调用被 cancellation(Stop/Pause/预算强停)打断时,内核必须向帧上下文追加合成的占位 tool_result(注明 interrupted 及原因),保证 §7.3 不变量 2 在中断路径上同样成立;不变量测试(§13 测试策略)增加"中断随机注入"用例。影响:kernel runner(§3.1、§3.2)、compression(§7.3)。
- **P1 — sidecar 输入最小化原则入契约**:§5.2 Sidecar 契约或 §5.1 信号目录增加约束:安全类 sidecar 默认只接收结构化调用数据(工具名/参数/skill ref),不接收主模型自由文本;需要更多上下文的 sidecar 必须显式声明并在文档中标注注入风险。影响:sidecars(§5.2、§5.3)。
- **P1 — Veto/拒绝理由的标准回写形态**:规定 `Veto(reason)` 与 HumanApproval 拒绝时,reason 作为该次 tool_call 的错误观察写入帧上下文(复用 §3.2 工具失败语义),而非只进 trace;同步给 HumanApproval 补超时默认行为(可配置,如"超时走保守策略")。影响:sidecars(§5.2 Verdict、§5.4)、tools(§8.1)、runner(§3.2)。
- **P1 — 长输出归一化采用 head+tail 格式**:§8.1 归一化与 §7.2 spill 的 preview 明确为"头部 N 行 + 尾部 N 行 + 显式省略通知(省略多少、完整内容 ref、如何取回)";`blob_get` 支持 offset/limit(对齐书的读工具分页约定)。影响:tools(§8.1、§8.4)、compression(§7.2)。
- **P2 — ToolSpec 增加可选语义字段**:`examples`(1-5 个调用示例,随 schema 注入)、`cacheable`(只读工具结果可缓存)、不可幂等工具的 `confirm` 两阶段语义占位(dry run + confirmation token);同步更新 §8.4 docstring 推导约定("何时用/边界/反例"写作规范)。影响:tools(§2.2、§8.4)。
- **P2 — MCP 适配器安全清单写入 §8.3**:工具描述按不可信输入审查、版本锁定 + 同名工具 namespace 隔离、最小权限凭证作用域(ToolContext 增加 credentials 字段,按工具声明注入,不碰全局环境)。影响:tools(§8.3、§2.2 ToolContext)。
- **P2 — 工具结果自动验证 hook**:以内置 sidecar 或工具包装器提供 execute-validate-feedback 样本(写代码文件后跑 linter,结构化错误并入返回值);不动 §8.1 流水线本体。影响:sidecars(§5.4 表加一行)或 tools(§8.4)。
- **P2 — §9.2 增补两条表述**:引用"OS 级 → 容器 → microVM"阶梯作为后端演进路线论证;明确写入"venv 不是沙箱"的警示(防误配)。影响:logic(§9.2)。
- **P2 — FrameContext 来源标注约定**:§2.3 消息构造增加来源标注内置约定(系统指令/父帧输入/工具结果/注入消息),作为便宜的注入缓解。影响:kernel(§2.3、§3.1 组装请求)。
- **P2 — §2.1 注脚化三维决策框架**:把 Parameter Complexity / Frequency of Change / Model Capability 作为 `kind: prompt | code` 形态选择的判据写进 §2.1 关键设计点。影响:skills(§2.1)。
- **P3 — §14 开放问题增补三条**:(a) 外部事件入口:事件源 → 信号总线 → Run 的唤醒路径(含 `initiate_X` 异步工具命名约定、`set_timer`/`monitor_shell`/`connect_channel` 形态、事件批处理时的 status bar 标记);(b) 帧间邮箱(父帧向运行中子帧补充指令/取消)是否值得做,与开放问题 4 合并讨论;(c) 工具级 context-aware 压缩作为 Compressor 新策略,其保真评测与开放问题 2 合并。影响:§14、sidecars(§5.1)、tools(§2.2)、compression(§7.2)。
- **P3 — HumanApproval 升级为模型审批时的工程注记**:§5.4 该条目下记录书的两条经验:审批模型与执行模型不同家族、能力相近;连续拒绝触发 circuit breaker 回退人工。影响:sidecars(§5.4)。

---

**总体判断**:本章与 Agent OS 内核设计关联度**高**。它的 Sidecar 机制、参数保真原则、沙箱阶梯、长输出处理、safe point 纪律与我们的 §5/§8/§9/§7/§3 多处同构,属于难得的互相印证;真正的硬缺口只有一个——**中断路径上的 tool_call/tool_result 配对修复**(P0);其余多为机制深化(P1/P2)与 v1 范围外的方向性储备(事件驱动入口、动态工具发现,P3)。书的"Skills = 渐进披露文档"与我们"Skill = 带帧函数"是命名相同、抽象不同的两条路线,按书自己的三维决策框架,二者互补而非互斥。
