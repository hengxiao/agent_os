# Chapter 3《User Memory and Knowledge Base》与 Agent OS 设计对比报告

> 评审对象:`ai-agent-book/book-en/chapter3.md`(全文 700 行,已通读)
> 对照基准:`DESIGN.md` v0.3(§1–§14,已通读)
> 评审结论先行:**关联度中等偏间接**。该章主题是跨会话持久记忆与检索增强(RAG),属于应用层/服务层能力;Agent OS 明确定位为内核,§14 已把"长期记忆服务"列为非目标。
> 但该章在四个方面对内核设计有直接价值:
> (1) 为 `agent_os.services` 预留扩展点提供了明确的契约候选;
> (2) Contextual Retrieval 思想可直接增强 spill/blob 机制;
> (3) 检索层权限过滤、间接提示注入防御印证并补强了我们的安全模型;
> (4) 其三级评测框架为 §14 开放问题 2(压缩保真基准)提供了现成模板。

---

## 章节内容概要

本章把"上下文工程"从单会话(第 2 章)扩展到**跨会话持久知识系统**,分两个尺度:

- **User Memory**:个体用户记忆,让 Agent 成为"懂你的私人助理";
- **Knowledge Base**:群体共享知识(行业法规、公司 SOP、专业文档),让 Agent 成为"领域专家";
- 两者共享底层技术(向量检索、知识压缩)与失效模式(信息冲突、知识过期、检索不准)。

### 一、用户记忆部分

**评测框架**(先立标尺再谈设计):

- 引用 **LoCoMo** 基准(Long-term Conversational Memory,arXiv:2402.17753):约 300 轮、跨 35 个会话的超长对话;
- 归纳八类记忆能力:个人信息保持、偏好追踪、上下文切换、记忆更新、多会话连续性、复杂推理、时间感知、冲突解决;
- 作者自建**三级评测框架**(贯穿全章,Experiment 3-1/3-10/3-12 复用):
  - Level 1 Basic Recall(基本回忆):直接给出的结构化事实能准确存取;
  - Level 2 Multi-Session Retrieval(多会话检索):跨实体、跨渠道、跨时段检索并推理;
  - Level 3 Proactive Service(主动服务):跨会话发现隐藏关联,未被询问即主动提醒;
- 评测方法:**LLM-as-a-judge**,对照参考答案打分(Experiment 3-1)。

**记忆层次结构**(where to store):

- **Trajectory**:单 run 完整事件流(用户消息+模型回复+工具结果),append-only 日志,只增不改;
- **User Long-Term Memory**:跨会话蒸馏出的稳定信息(偏好、事实、交互摘要),反复重写/合并/剪枝的"档案",经显式工具调用读写;
- **Business State**:开发者定义的任务阶段抽象("待澄清/处理中/待支付/已完成")。

**四种存储格式**(how to store,粒度与结构化程度递进):

- **Simple Notes**:原子事实,O(1) 操作成本最低,但切断事实间关联;
- **Enhanced Notes**:整段叙事保留完整语境,代价是冗余、更新贵、长段落伤害 embedding 检索;
- **JSON Cards**:三级嵌套(Category→Subcategory→KV),支持部分更新,但结构僵化、多维信息被压平;
- **Advanced JSON Cards**:每张卡带 `person`/`relationship`/`backstory`/时间戳,解决同名实体消歧("张医生是自己的牙医还是父亲的心脏科医生"),生成维护成本最高;
- 实践准则:**关键低密度数据用 Advanced JSON Cards,大量非关键事实用 Simple Notes**,生产系统多为混合分路(Experiment 3-2)。

**高级表示**(存储介质从模型外部到内部的谱系):

- **User as Code**(arXiv:2606.16707):记忆即代码——memory phase 把事实逐条追加到 append-only 日志,structuring phase 周期性从全量日志重新生成 typed Python 对象,即数据库 **WAL + checkpoint** 模式;聚合统计准确率约 99%(文本检索记忆仅 6%–43%);冲突检测与约束强制(如护照有效期 vs 国际航班日期)变成确定性代码;
- **User as Engram**(arXiv:2606.19172):事实写入 Engram 模型的 hash N-gram 槽位,解决事实 LoRA"存了不会用"的难题;不同用户槽位不相交、可叠加;
- **Parametric Multimodal User Memory**:感知记忆(人脸、声纹、画风)以感知形式存入冻结模型外挂的 attention memory bank,无需训练、加一行即注册新身份。

**认知科学分类**(what to store):Working Memory(≈上下文窗口)/ Episodic(情景)/ Semantic(语义)/ Procedural(程序性)记忆;与层次、格式三套分类**正交组合**(表 3-1)。

**框架案例**:

- **Mem0**(arXiv:2504.19413):extract–compare–decide 两段式流水线——LLM 抽取候选记忆 → 向量检索相似旧记忆 → LLM 判 **ADD/UPDATE/DELETE/NOOP**;embedding 与存储解耦、插件化;另有图记忆变体 **Mem0-g**;
- **Memobase**:User Profile(按 topic/subtopic 组织的可配置槽位)+ Event Memory(时间线);buffered batch 摊薄 LLM 调用成本。

**记忆压缩与组织**(存储层,本章明确与第 2 章运行期压缩划界):

- 第一层:重要性评分四因子——访问频率、时间衰减、情感强度、信息唯一性,低于阈值标记为可压缩/可删除;
- 第二层:聚类相似记忆,每组生成代表摘要,原始细节归档二级存储;
- 第三层:抽象泛化,从具体情景记忆提炼语义/程序性记忆;
- 冲突检测:版本化——保留历史版本、标记最新;按信息类型决定只留最新(当前地址)还是保留全史(工作经历)。

**隐私保护**(Experiment 3-3):本地小模型(Ollama + Qwen3 0.6B)做日志 PII 清洗,JSON Schema 结构化输出(类型/位置/置信度),recall >95%;超高吞吐场景用 regex 快筛 + LLM 深扫的混合策略。

### 二、RAG / 知识库部分

**基础流水线**:retrieve → inject into context → generate;检索器质量决定 RAG 上限。

**切块(chunking)**:

- 三策略:fixed-size(512 tokens + 50–100 overlap)/ recursive-structure-aware(沿标题、段落、句子递归,生产默认)/ semantic chunking(相邻句 embedding 相似度骤降处下刀);
- 实践起点:256–1024 tokens/块 + 10%–20% 重叠;
- 固有缺陷:切块使片段脱离原始语境("该公司"是谁?哪份报告?),为后文 Contextual Retrieval 埋伏笔。

**稠密检索**:embedding + cosine similarity;Word2Vec(静态词向量,无法处理多义词)→ BERT/BGE-M3(上下文感知,多语言长文本);ANN 索引对比(Experiment 3-4):**ANNOY**(树,构建快/内存低/**不支持增量插入**)vs **HNSW**(图,**支持增量**/精度极高/内存高)——索引选型直接决定知识库更新运维成本。

**稀疏检索**:TF-IDF → **BM25**(k1 词频饱和、b 文档长度归一化;倒排索引);learned sparse(SPLADE、BGE-M3 sparse 分支,term expansion 获得部分语义泛化)(Experiment 3-5)。

**混合检索三段式**(Experiment 3-6):

1. parallel retrieval:稠密、稀疏双路并行召回;
2. result fusion:加权归一化求和 vs **RRF**(Reciprocal Rank Fusion,score = Σ 1/(k+rank),k≈60,只用 rank 不用原始分,简单鲁棒);
3. neural reranking:**cross-encoder**(如 bge-reranker-v2-m3,query+doc 拼接逐词交互)对比检索阶段的 **bi-encoder**(各自编码再算向量,快但浅)——重排不替代融合,二者串行;
- 检索质量三指标:**recall@k**(本书按 hit rate 定义,脚注明说与学术定义不同)、**MRR**、**nDCG**;"retrieval failure rate" = 1 − recall@20。

**多模态抽取三路径**(Experiment 3-7):native multimodal(ViT patch 化统一语义空间,保真度最高)/ extract-to-text(OCR/转写后纯文本,低成本丢布局)/ tool-based analysis(先给文本摘要,`analyze_image`/`analyze_pdf` 工具按需深挖)。

**结构化索引**(超越扁平文本):

- **RAPTOR**:自底向上递归聚类 + LLM 摘要成树,检索可在任意抽象层进行,适合"从宏观概念钻取细节";
- **GraphRAG**:LLM 抽取实体-关系三元组成知识图谱 + 社区检测摘要;强项是**多跳关系推理**与**实体消歧**;但三元组化丢失条件/时序逻辑("如果下周下雨就取消海滩改去博物馆"),且抽取错误造成知识污染;
- 实践建议:**分层互补**——核心信息存完整自然语言,结构化元数据辅助索引,图谱只在多跳/消歧刚需领域(医疗、法律、家庭关系)作专项索引;
- 启用判据:查询以"找片段"为主 → 混合检索足够;频繁跨文档综合/多跳 → 才值得上结构化索引(构建期 LLM 调用成本大)(Experiment 3-8)。

**OpenViking 文件系统范式**:

- `viking://` 虚拟 URI 组织 resources/user memories/agent(skills+memories),物理存储由框架决定;
- **L0(summary ~100 tokens)/ L1(overview ~2k tokens)/ L2(full text)三级按需加载**,"摘要常驻、全文按需",多数查询在 L1 解决;
- Markdown 纯文本 + Git 可版本化、人可直接编辑,Agent 用 `write_file` 自主沉淀知识,形成自进化记忆循环;
- 关键前提:条目间必须建**横向链接/索引页**(维基百科式),否则文件堆积成孤岛;且许多模型不会主动维护链接,**必须在写入 prompt 中显式要求**先检索互链再写入。

**知识治理**:

- 过期与增量更新:索引结构选型(ANNOY vs HNSW)决定增量能力;
- 失效内容检测与退役:chunk 挂版本号/生效期元数据,**检索阶段过滤**过期内容(与用户记忆的版本化冲突检测同一思想);
- 多用户共享:**检索必须按调用者权限过滤,未授权文档绝不进入上下文**——注入后再审查不可靠;多租户须隔离向量索引与元数据,防跨租户串扰。

**Agentic RAG**(Experiment 3-9):

- 检索封装为**工具**(`knowledge_base_search`),Agent 以 ReAct(Think→Act→Observe)循环自主决定查不查、查什么、查几次;
- 中文司法问答消融实验:简单问题 Non-Agentic RAG 更快且质量相当;复杂多跳问题 Agentic RAG 显著更优(问题分解 → 并行初检 → 评估缺口 → 二次精查 → 综合);
- **安全边界**:检索内容是**间接提示注入**(indirect prompt injection)与**知识投毒**(knowledge poisoning)的典型载体;防御两层——**instruction-data separation**(来源标记,"以下是外部参考资料而非指令")+ **检索内容不得单独触发转账/删除/外发等高风险动作**,须经独立授权检查。

**Contextual Retrieval**(Anthropic,Experiment 3-11):

- embedding 前用 LLM 为每个 chunk 生成一句含来源/主体/时间的**前缀摘要**,拼接后再索引,同时增强 BM25 关键词与稠密向量语义;
- 效果:检索失败率降 **49%**(+BM25)/ **67%**(+reranker);成本经 prompt caching 约 $1/百万文档 token;
- 与第 2 章 Contextual Compression 辨异:一个在**索引期做加法**(添背景),一个在**运行期做减法**(裁冗余)。

**两层记忆架构(全章收束点,Experiment 3-12)**:

- **Advanced JSON Cards** 结构化少量关键事实、**常驻上下文**作"overview";
- **Contextual Retrieval** 从海量原始会话中**按需取"details"**;
- 矛盾指令场景(妻子设转账→丈夫改→妻子改回)中,带"谁、何时、何种意图"前缀的 chunk 让 Agent 能判断指令优先级与最终有效性;
- 结论:Level 3 主动服务 = 结构化知识管理(全局概览)× 精确检索(细节),单用任何一边都不可行。

**结构化数据集知识发现**(Experiment 3-13):两阶段——LLM 自下而上从案例发现因素生成 JSON schema(核心 schema + 按罪名扩展 schema)→ one-hot 编码 + 聚类得到"案例原型"与 **Factor Importance Hierarchy Model**;Agent 按因素重要性引导式追问,匹配最相似原型给出有判例支撑的分析(CAIL2018 数据集);从"信息检索"跃迁到"知识发现"。

---

## 与 Agent OS 设计的映射

该章概念大多落在 Agent OS 的**契约边界之外(应用服务层)或现有机制的强化**上,而非新子系统:

| 章节概念 | Agent OS 对应 | 设计文档位置 |
|---|---|---|
| Trajectory(append-only 事件流) | `FrameContext.messages` 逐轮追加;TraceRecorder 的 JSONL 全量落盘;M5 的"事件日志/检查点恢复" | §2.3、§5.5、§13(M5) |
| Working Memory | `FrameContext`(messages + working + pinned) | §2.3 |
| 记忆压缩(重要性评分/聚类/抽象,**存储层**) | Context Compression 子系统(**运行层**)——本章自己在"Memory Compression"一节末尾划清了两层边界 | §7(尤其 §7.2、§7.5) |
| User Long-Term Memory / Mem0 / Memobase | **显式非目标("长期记忆服务")**;接入通道是 `agent_os.services`(共享内存/黑板预留)与 blob store | §14、§11.3、§2.3 |
| 两层架构:JSON Cards 常驻 overview | `FrameContext.pinned`(永不压缩)+ `working` 结构化 scratchpad | §2.3、§7.3 不变量 1 |
| 两层架构:Contextual Retrieval 按需取 details | `spill` 策略的 `{ref, preview}` + `blob_get` 工具按 ref 取回 | §7.2、§8.3 |
| Agentic RAG(检索工具 + ReAct) | 检索即 Tool(系统调用),在内核 agent loop 内迭代——无需任何新机制,正是公理 1"内核拥有循环"的直接应用 | §1、§3.1、§8 |
| 检索层权限过滤 / 租户隔离 | 三层权限模型(工具等级 ∩ manifest 白名单 ∩ RunConfig 上限)的延伸——应下沉到记忆/检索服务内部;多租户本身是 v1 非目标 | §8.2、§14 |
| 间接提示注入防御(来源标记 + 高风险动作独立授权) | 工具结果归一化阶段可统一包裹来源标记;`pre:tool.call` 可否决 + ToolGuard/HumanApproval + EXEC 级权限 | §8.1、§5.4、§8.2 |
| User as Code(typed Python + 约束函数) | Logic Kernel 是唯一代码执行点;记忆 structuring 产物可由 code 技能承载,约束检查经 `python_exec` 强制沙箱 | §9、§9.2、§9.4 |
| Procedural Memory(学会的行为流程) | Skill 本身(有名、带参、可组合的行为单元);"从重复模式沉淀流程"≈ 技能生成与热重载 | §2.1、§6.1 |
| 三级评测 / LLM-as-a-judge | 测试策略与开放问题 2(压缩保真基准)的方法论模板 | §13 测试策略、§14 开放问题 2 |
| 日志 PII 清洗(本地小模型) | TraceRecorder 落盘前的脱敏 hook(ASYNC sidecar 形态) | §5.4、§5.5 |
| OpenViking L0/L1/L2 分级加载 | spill 的 preview/ref 二级结构的增强版;与 Skill 伪工具"先看 schema 再调用"的渐进披露同构 | §7.2、§3.3 |
| Business State | `SkillFrame.status` + `FrameContext.working` 可承载,属应用层约定 | §2.3 |
| BM25/embedding/RRF/reranker/RAPTOR/GraphRAG/HNSW | 检索服务**内部实现**,对内核契约透明(工具背后用什么引擎与内核无关) | 无直接对应 |

---

## 一致与相互印证

1. **"检索即工具"印证公理 1(内核拥有循环,§1)。** Agentic RAG 的全部能力——自主决定查不查、查什么、查几次(Experiment 3-9 的多轮迭代)——在 Agent OS 中不需要任何专门子系统:`knowledge_base_search` 只是一个普通 Tool,内核 loop(§3.1)天然提供 ReAct 循环,权限、信号、记账自动生效。这说明抽象层级切对了:RAG 是应用在工具层涌现的行为,不是内核机制。
2. **两层记忆架构印证 `pinned` + `spill` 的结构性设计。** 本章收束的"overview 常驻 + details 按需"(Experiment 3-12)与 §2.3 的 `pinned`、§7.2 的 `spill`(大输出移出留 `{ref, preview}`,`blob_get` 取回)是同一思想的两种尺度。我们"子帧弹栈 transcript 折叠为返回值"(§2.3、§7.2 `collapse_child`)也正是其"Trajectory 是日志、长期记忆是蒸馏档案"的单 run 内对应物。
3. **权限过滤必须下沉到检索层,印证三层权限模型的哲学。** 本章强调"未授权文档绝不进入上下文,因为进了上下文就难保不泄露",与我们"一切特权操作在分发前拦截"(§1 公理 3)、`pre:tool.call` 同步否决(§5.2)是同一种 fail-closed 直觉在数据面的推广。§14 开放问题 4(黑板权限与 manifest 白名单如何统一)在问同一个问题——本章给出的答案方向是:**数据离开存储层之前过滤,而不是进入上下文之后审查**。
4. **存储层压缩与运行层压缩的分层,印证 §7 的职责边界。** 本章明确区分"存储层记忆组织"(离线、周期性、LLM 驱动)与"运行期窗口压缩"(第 2 章),与我们把 RollingWindowCompressor 做成纯函数、无 LLM 依赖(§7.5)、把 summarize 放在责任链后段(§7.2)的取舍完全一致:热路径要便宜、确定、可测;贵的 LLM 压缩放冷路径。
5. **知识过期/版本化印证热重载的"版本共存"语义。** 技能热重载"新帧用新版、在跑帧钉住旧版"(§6.1)与知识库"chunk 挂版本号/生效期、检索时过滤过期内容"是同一治理问题在代码资产与知识资产上的同构。
6. **User as Code 印证 Logic Kernel 统一咽喉点的价值。** 记忆结构化产物是 typed Python 代码,聚合/冲突检测/约束强制都要执行代码;宿主实现此类记忆服务时,执行点自然落在 Logic Kernel(§9),动态生成的检查代码走 `python_exec` 强制沙箱(§9.2、§9.4)——从需求侧验证了"逻辑代码只有一个执行点"不是过度工程。
7. **多模态 tool-based 路径印证工具化渐进披露。** 其"先给文本摘要、`analyze_pdf` 按需深挖"与我们的 spill preview + `blob_get`、子技能伪工具 schema 先签名后调用(§3.3)是同一模式:轻量元数据先行,重内容按需拉取。

---

## 差距与可借鉴点

1. **spill 的 preview 太弱,缺 Contextual Retrieval 的"身份标签"。** §7.2 的 spill 只留 `{ref, preview}`,若 preview 只是字符截断(§7.5 的 `[truncated]`),模型日后判断"该不该 `blob_get` 取回"时缺乏依据,等同于本章批评的 context-free chunk。本章给出强证据:为每个被索引片段生成一句含**主体、时间、意图**的前缀摘要,检索失败率降 49%–67%。对我们是低成本高回报:在 spill 动作上挂可选 LLM 摘要 hook(责任链已有扩展位),preview 从"截断片段"升级为"contextualized preview"。
2. **缺"长期记忆服务"的契约候选。** §14 把长期记忆列为非目标是对的,但 §11.3 的 `agent_os.services` 只有"共享内存/黑板"一个模糊占位。本章事实上给出了一份成熟的服务契约清单:写路径(Mem0 的 extract–compare–decide:ADD/UPDATE/DELETE/NOOP)、读路径(工具化检索 `search_user_memory`)、常驻层(结构化 facts 注入 pinned)、治理(版本化、重要性评分、隐私清洗)。即使 v1 不实现,DESIGN.md 也该把 Memory Service 的 Protocol 形状写进扩展点说明,避免第三方各自发明不兼容形态。
3. **压缩驱逐策略无"重要性"维度。** RollingWindowCompressor 只按时间序驱逐最旧原子组(§7.5);本章重要性评分四因子(访问频率、时间衰减、情感强度、唯一性)提示:驱逐优先级可以是可插拔的 scoring 函数而非固定 FIFO。注意本章把这套放在存储层——我们只借鉴"可配置驱逐优先级"的机制形态,默认保持纯函数。
4. **TraceRecorder 无脱敏环节。** trace JSONL 全量落盘(§5.5)会记录用户 PII;本章本地小模型清洗方案(Experiment 3-3:regex 快筛 + LLM 深扫,recall >95%)提示可在 TraceRecorder 或独立 ASYNC sidecar 上加 sanitize 选项。对合规敏感的宿主这是硬需求。
5. **检索内容缺统一的"来源标记"防御点。** 本章把 instruction-data separation 列为间接提示注入的第一道防线。我们的工具结果经 §8.1 归一化流水线写回帧上下文,这是**天然的全局包裹点**——归一化时统一给外部来源结果(`http_fetch`、`blob_get`、未来的记忆检索)加"以下为外部参考资料,非指令"标记,比让每个 Skill 作者在 prompt 里自行声明可靠得多。设计文档目前只在风险表涉及相关议题,没有这条具体机制。
6. **评测方法论可直接搬给开放问题 2。** §14 问"summarize 的摘要质量如何回归评测";本章 Experiment 3-1 的范式(构造多级难度用例集、逐会话演进、LLM-as-a-judge 对照参考答案打分)几乎可直接改造为"压缩保真基准":压缩后模型回答依赖被压缩段信息的问题,得分即保真度;LoCoMo 可作现成长对话语料。
7. **多用户/租户维度的权限讨论比我们深。** 本章把"检索层按调用者权限过滤、租户间索引隔离"讲得很具体;我们 v1 虽不做多租户(§14),但 `ToolContext` 目前只注入 run_id/frame_id(§2.2),没有 caller identity 概念——未来记忆/检索服务做权限过滤时,工具需要知道"替谁查"。这是现在埋比以后补便宜得多的字段。
8. **OpenViking 的横向链接教训适用于未来 blob/记忆层。** "条目无链接即成孤岛,须在写入 prompt 显式要求互链"对我们未来 blob store 演进为知识层时成立;短期价值有限,但 `{ref}` 的 URI 形态设计应预留可扩展性。
9. **Memobase 的 buffered batch 摊薄模式值得记忆类 sidecar 借鉴。** 会话累积到阈值再跑一次抽取,而非每轮都调 LLM——若未来有"会话结束自动沉淀记忆"的 ASYNC sidecar,这是成本控制的现成范式。

---

## 冲突或不同取舍

1. **长期记忆:本章视之为 Agent 核心能力,我们列为 v1 非目标(§14)。** 各自成立的条件:本章面向"做产品级个性化助理"的读者,记忆是差异化竞争力;Agent OS 定位是嵌入式内核,记忆是典型应用层服务(依赖向量库、用户体系、离线任务),强行入内核会污染 §11.1 契约层的纯洁性。**不冲突,是分层不同**——但前提是扩展点(工具化检索 + pinned 注入 + services 预留)确实不阻碍上层实现本章全部模式。逐条核对:两级架构(pinned + 检索工具)、Agentic RAG(普通工具)、User as Code(Logic Kernel)都可在不改动内核的情况下实现,结论成立。
2. **记忆更新靠每会话后的专用 LLM 调用,我们的压缩 baseline 不用 LLM。** 本章写路径是"事后、离线、LLM 驱动";§7.5 是"在线、热路径、纯函数"。这不是取舍相左,而是同一压缩思想在不同延迟预算下的两个工作点。需要注意的是 §7.2 的 `summarize` 策略已在热路径引入 LLM 调用,其延迟与失败语义(摘要调用失败时是否退化为 truncate?)实现时必须明确,本章没有现成答案。
3. **本章信任模型自主检索与行动,我们对一切特权操作设闸门。** 本章自己也要求"检索内容不得单独触发高风险动作,须经独立授权检查",承认了纯自主的边界;我们的三层权限 + HumanApproval(§8.2、§5.4)正是这条防线的内核化。条件差异:本章场景以只读检索为主,宽松可行;Agent OS 面向通用宿主,默认收紧正确。
4. **记忆表示的参数化方向(Engram/多模态参数记忆)与我们完全正交。** 把记忆写进模型参数属于 Provider 之下(模型本体)的层次,内核既不支持也不阻碍;评审意见:无需任何设计响应,了解即可。
5. **本章鼓励模型自主维护知识条目互链(OpenViking),并承认"许多模型不会主动这么做"。** 这与我们"Skill 拥有策略"(prompt 技能指令体由作者编写,§2.1)一致——这类要求属于技能作者的 prompt 工程,不是内核机制;若未来提供记忆类内置技能,应把"写入时先检索并互链"写进其指令体。

---

## 行动建议

按优先级排列(P0 = 值得进设计文档修订,P1 = 实现期注意,P2 = 备忘):

1. **[P0] spill preview 增加 contextualized 摘要选项 —— 影响 §7.2(Context Compression / spill 策略)。** 在 spill 责任链上定义可选 `preview_enricher` hook:逐出大输出时调廉价模型生成一句"主体+时间+意图"前缀,与 preview 一起留存。依据:Contextual Retrieval 的 49%–67% 失败率降幅;成本可用 prompt caching 控制。不动契约,只丰富 baseline 策略说明。
2. **[P0] 在 §11.3 扩展点表中补 Memory Service 契约要点 —— 影响 §11.3 / §14。** 把"长期记忆服务"从一句话非目标扩写为三行契约约束:读写分离(写=离线 extract–compare–decide,读=普通 Tool);检索层权限过滤(数据出存储前按 caller 过滤);常驻事实经 pinned 注入。保持非目标定位不变,给第三方实现锚定形态,并呼应开放问题 4。
3. **[P0] 工具结果归一化阶段加统一来源标记 —— 影响 §8.1(分发流水线)/ §13 风险表。** 在"结果归一化"一步为外部内容类工具(`http_fetch`、`blob_get`,及声明 `untrusted_source: true` 的第三方工具)自动包裹 instruction-data separation 标记,作为间接提示注入的内核级缓解;风险表补一行"检索内容间接提示注入 → 归一化来源标记 + 高风险动作权限闸门"。
4. **[P1] `ToolContext` 预留 caller identity 字段 —— 影响 §2.2。** 加可选 `principal`(user_id/tenant_id),v1 恒为 None;为未来记忆/检索工具的权限过滤留通道。现在加是一行,以后加是破坏性契约变更。
5. **[P1] 压缩保真评测采用本章三级框架范式 —— 影响 §14 开放问题 2 / §13 测试策略。** 借鉴 Experiment 3-1:构造依赖被压缩段信息的问答集,压缩后作答、LLM-as-a-judge 打分,作为 `summarize` 策略(M5)的回归基准;评估是否直接采用 LoCoMo 语料。
6. **[P1] TraceRecorder 增加可插拔脱敏 hook —— 影响 §5.4 / §5.5。** 定义 ASYNC sanitize 钩子(默认关闭),文档提示可用本地小模型做 PII 清洗(参照 Experiment 3-3 的 regex+LLM 混合方案),供合规敏感宿主启用。
7. **[P2] 驱逐优先级可插拔化备忘 —— 影响 §7.5。** RollingWindowCompressor 保持 FIFO 纯函数默认,接口注释注明"驱逐顺序可替换为重要性评分函数"(访问频率/时间衰减/唯一性),作为 hierarchical 链的后续扩展方向。
8. **[P2] blob ref 采用可扩展 URI 形态 —— 影响 §8.4(blob store 基础版)。** ref 形如 `blob://<run_id>/<sha>`,为未来跨 run 记忆层与条目互链(OpenViking 式横向关联)留命名空间,零成本。

---

### 评审附注:关联度判定

本章约 60% 内容(embedding/ANN/BM25/RRF/reranker/RAPTOR/GraphRAG/多模态抽取/数据集知识发现)是**检索服务内部实现**,对内核契约透明——Agent OS 只需保证"检索能作为工具被调用、被授权、被监督",这部分关联度低,不作逐点对照。

真正影响内核设计的是上面 8 条行动建议,集中在 Context Compression(§7)、Tool Registry(§8)、扩展点治理(§11)三处;均为**增强而非返工**,无一处要求修改核心抽象(Skill / Tool / SkillFrame / 信号 / 权限模型)。
