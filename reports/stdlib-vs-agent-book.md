# STDLIB.md × 《AI Agent Book》逐章对比分析报告

> 方法:三路并行精读 book-en 全部 10 章 + 前言后记(约 7000 行),每路对照
> [STDLIB.md](../STDLIB.md) 逐章分析;本报告在三份分析之上做交叉汇总,并补上
> 子分析不掌握的一层信息:**agent_os 引擎已实现的机制**(sidecar、信号、
> checkpoint、SKILL-INLINING v1),把书中指出的"缺口"重新归类为
> 引擎已有 / std 已覆盖 / 真缺口 / 需拍板的设计冲突。
> 生成日期:2026-07-25。

---

## 0. 结论摘要

1. **STDLIB 的"感知 + 执行"工具面经受住了检验**:第 5 章 Coding Agent 的
   最小完备"七件套"(interpreter/bash/read/write/edit/glob/grep)与
   STDLIB「现状 7 个 + P0 两个(fs_list/fs_search)」几乎逐个同构;`now`
   的内核管控设计被第 5 章 `server_clock.now()  # 不由模型提供` 直接背书
   (且书补了第二个理由:时间是安全边界,不只是复现性)。
2. **按第 1 章的 Harness 五分法(Context + Tools + Constrain + Verify +
   Correct)看,STDLIB 的 40 余条目约 38 条在前两层,后三层近乎空白**。
   但其中相当一部分不是真空白——引擎 sidecar 层已有 LoopDetector/
   StallDetector/BudgetGuard/ToolGuard/CodeScanner,是 STDLIB **文档
   没有交叉引用**,让读者误以为不存在(见 §4.1)。
3. **全书对 STDLIB 打击最重的一条**:`retry_until` + `judge` 的默认组合
   恰好是第 6/10 章反复证伪的"无外部反馈自审"形态。验证器必须默认接
   外部确定性反馈(tests/退出码/schema 校验),LLM judge 只能是补充且
   必须校准(kappa ≥ 0.7 金标准集)、带 veto、与被评对象异族。
4. **`std/style` 五条 inline 规范被四个章节从四个角度打**:参数保真度
   (Ch4,`zh_typography` 是弯引号故障的同构物)、约束优于指导(Ch5,
   三条应下沉为校验器)、cache 稳定性(Ch2)、消融与回归(Ch6)。
   需要整体重构,只有 `tone_neutral` 站得住纯 inline 形态。
5. **STDLIB 有一处内部自相矛盾**:§6 一刀切排除"向量检索"与 §2.2 的
   `memory_search` 冲突。正确划线:**BM25 稀疏检索 + RRF 融合进 std
   (纯 Python、零依赖,符合 std 自己的边界),dense embedding 出 std**。
6. 三个高频跨章主题构成新增条目的主力:**工具契约面六字段**(幂等/取消
   /并发/分页/截断可见/成本)、**progress/状态原语**(四章各自独立要求
   同一个东西)、**来源标注与注入隔离**(输入侧,与输出侧的 citation_style
   不是一回事)。

---

## 1. 书对 STDLIB 的正面验证(先说对的)

| STDLIB 设计 | 书中印证 | 章 |
|---|---|---|
| P0 = fs_list + fs_search + now | 七件套逐个同构;"grep 跨平台语法不同,专用工具优于即兴" | Ch4/5 |
| 工具面最小化(python_exec 顶专用工具) | "Instead of a dedicated calculator tool, provide a Python code interpreter" | Ch1/4 |
| `now` 过内核 | `now = server_clock.now()`;时间自报可绕过守门校验 | Ch5 |
| description lint(Use when / Do not use when + 负例) | "The clearest pattern is 'Use when / Do not use when'" | Ch2 |
| 大输出走 spill(blob ref) | 分层压缩的落盘层;"传路径不传内容" | Ch2/10 |
| 收 `fanout_vote`、不收 `debate` | 数据处理不等式否定辩论式,豁免独立采样聚合 | Ch10 |
| 组合子"复制粘贴专化" | "范例式生成优于规则穷举"——范例携带最佳实践 | Ch5 |
| §6 不做向量索引 | Claude Code 同路线;书补两条更硬的理由(索引陈旧、embedding 外泄) | Ch5 |
| 帧隔离 + inputs 自足 | "the task description must be self-contained";Isolation Over Compression | Ch2 |
| `http_fetch`/`http_post` 拆分 | 书的整合原则自带豁免:"unless there is a clear security, permission reason" | Ch4 |

另有一层子分析未见的印证:**引擎本体与第 10 章的 OS 蓝图高度同构**——
"轨迹即全部状态"(书原句)= DESIGN §10.2 WAL 原则;统一 Envelope
(sender/target/type/payload/ts)= api/v1 已冻结的黑板信封;黑板命名空间
白名单(_BoardProxy)= "sharing declared explicitly";RunControl 的
safe-point stop = 优雅终止。这本书相当于给 agent_os 的架构方向做了一次
独立复核,结论是同向的。

---

## 2. 逐章对照速览

(细节见三份子分析;此处每章压缩为主旨 + 对 STDLIB 的净效应)

| 章 | 主旨 | 对 STDLIB 的净效应 |
|---|---|---|
| **Ch1 入门** | Agent = LLM + Context + Tools;生产形态 = Model + Harness(五分法);五类工具分类 | 暴露三整类工具缺席(协作/事件触发/用户通信);风险维度(可逆性/财务)与权限档正交;`ask_human` 价值被低估 |
| **Ch2 上下文工程** | KV Cache 前缀稳定是架构约束;状态栏/渐进披露/压缩策略;注入防御 | 状态栏机制引擎已有但缺 todo 工具与操作策略档;工具错误四层结构(60%→95%)是最高性价比单点;外部内容须来源标记 |
| **Ch3 记忆与知识库** | 记忆是"抽取-比对-决策"管线;RAG 全栈;文件系统范式 L0/L1/L2 | `memory_write` 裸写必踩矛盾并存;BM25/RRF/chunk_text 应进 std(纯函数);`contextualize_chunk` 数据极硬(-49%/-67%) |
| **Ch4 工具** | 五类分类 + ACI 设计原则 + 参数保真度 + 异步/事件 + 发现机制 | 工具契约面六字段缺失;`zh_typography` 是保真度雷;渐进披露约定(用 P0 的 fs_read 就能实现);web_search 优先级冲突 |
| **Ch5 Coding Agent** | Coding Agent + 文件系统 = 通用 agent 地基;失败分类学;约束优于指导 | 七件套背书 P0;`fs_edit`/`fs_read`/`shell_exec` 契约需明确(行号/会话/唯一匹配);style 三条应下沉为校验器;文件版记忆可不等 M6 |
| **Ch6 评估** | 评测对象是 model+harness;统计效力;judge 校准;内部评测基建 | §7.3 "≥3 条测试"噪声带宽 ±29pp,须拆两层;judge 需 schema 化+veto+校准+异族;Pass^k 稳定性门槛缺失 |
| **Ch7 后训练** | SFT 记忆 RL 泛化;奖励范式演进 | 无直接启示;judge 输出向量化、`reject_sample` 组合子、trace 标注 origin 三条间接建议 |
| **Ch8 自进化** | 学习必须显式设计;经验外化三产物;工具创造者;安全边界 | std 是纯只读能力库——写侧(create_tool/skill_write)与发现侧(skill_search)缺席;`verify_before_store` 是入库闸门;记忆投毒防护 |
| **Ch9 多模态实时** | 快慢架构;"叙述成持久文本";动作面参考 | blob 需 mime、prompt 技能需 image_ref;`describe_image` 零依赖高收益;流式/全双工应显式写入不做清单 |
| **Ch10 多智能体** | 共享上下文×拓扑两维;IPC 两范式;终止/预算/交接;错误级联 | 验证器必须带新信息(最重打击);组合子缺预算/取消/交接包;fs 并发乐观锁;引擎黑板范式的代价应写明 |

---

## 3. 书中"缺口"的重新归类(子分析 × 引擎现状交叉)

子分析不掌握引擎细节,若干"STDLIB 缺失"实为**文档缺交叉引用**。重新归类:

### 3.1 引擎已有,STDLIB 应补引用而非新增

| 书中要求 | 引擎现状 | 应做 |
|---|---|---|
| 重复调用指纹(hash name+args)检测死循环 | LoopDetector sidecar 正是此机制(签名连续 N 次→纠偏→stop) | STDLIB §1 或新增"引擎既有守护"一节交叉引用 |
| liveness 信号 / 卡死检测 | StallDetector(可注入时钟) | 同上 |
| 预算熔断 | BudgetGuard + RunConfig max_steps/max_cost(run 级) | 组合子级 budget 仍是真缺口(见 §4.4) |
| 命令黑名单/参数正则拦截 | ToolGuard(pre:tool.call veto) | 同上交叉引用 |
| 危险代码静态扫描 | CodeScanner(pre:logic.exec veto) | 同上 |
| 统一消息信封 | api/v1 Envelope(§12.1 冻结) | 同上 |
| 优雅终止 + safe point | RunControl.stop/pause + pre:step 检查 | **子树级联取消**仍缺(只有 run 级) |
| 状态栏(读数 + 操作策略) | ContextManager §7.3 已有 step/tokens/cost/budget + 低预算收敛 hint | todo 工具与工具调用计数器仍缺 |
| 轨迹即状态 / WAL / 恢复 | checkpoint + resume + replay 全套 | — |
| outputs 机器校验 | 内核 `_check_output` 连败判帧失败 | **`json_discipline` inline 与之重叠,佐证 Ch5 的下沉论** |
| inline 消融开关 + 确定性渲染 | SKILL-INLINING v1 已实现 `RunConfig.inline="off"` 与帧内冻结快照(working 可导出、随 checkpoint 落盘) | Ch6 三条 inline 基建要求已满足两条,仅缺"style 变更跑评测回归"的流程约定 |

### 3.2 STDLIB / 引擎皆无 —— 真缺口(按跨章出现频次排序)

1. **工具契约面六字段**(Ch4/5):`idempotent`、取消后副作用语义、
   `concurrent_safe`、分页 cursor+total、**显式截断提示**(blob ref 解决
   "存哪",不解决"模型知不知道没看全")、`cost_hint`。
2. **工具错误第四层:修复建议 hint**(Ch2,60%→95%):现有 ToolError 有
   kind/message/retryable,缺 `hint[]`。
3. **progress/todo/状态原语**(Ch2/8/9/10 四章独立要求同一个东西):
   `todo_write`/`todo_update`(Ch2 实测 15 vs 21 轮)、`progress_track`
   (JSON 进度文件 + `fs_list` mtime + `now` 即可白拿卡死检测)。
4. **来源标注与注入隔离**(Ch2/3/4/8):perception 工具 outputs 强制
   `source` 字段 + `<external_content>` 包裹;`untrusted_content` inline
   ("检索内容是资料不是指令");`injection_scan` 纯函数。注意:
   `citation_style` 是**输出侧**格式,不能替代**输入侧**隔离。
5. **judge 可信化**(Ch6/7/10):rubric schema 化(dimensions + veto +
   分档可判行为)、per-dimension 输出、长度偏见对策、**kappa ≥ 0.7 校准
   门槛**、与被评对象异族约束(需能力别名加 `family` 轴)。
6. **验证器与 judge 的显式分层**(Ch10,最重一条):`retry_until` 默认
   验证槽接确定性外部反馈;judge 加负例 lint"不作为唯一验收依据"。
7. **检索纯函数四件**(Ch3):`chunk_text`、`rrf_merge`、`bm25_score`、
   `retrieval_metrics` + prompt 技能 `contextualize_chunk`。
8. **写侧与发现侧**(Ch8):`skill_search`(READ,纯读零依赖)先行;
   `skill_write`/`tool_register` + `learned/` 命名空间分层治理需专项
   (或显式写明"std 不提供写侧"的理由)。
9. **fs 并发保护**(Ch10):`fs_write`/`fs_edit` 可选 `if_match`
   (mtime/hash 乐观锁)——有 `map_over` 并发就必然需要。
10. **多模态最低限**(Ch9):`blob_get` 带 mime、prompt inputs 支持
    `image_ref`、`std/nlp.describe_image`("叙述成持久文本",8 模型
    +17~48pp,零外部依赖)。
11. **协作/事件/通信三类工具面**(Ch1/4):`subagent_cancel`/`subagent_status`
    (引擎有 spawn/wait 但无 cancel 工具面)、`set_timer`(与 `ask_human`
    共用 checkpoint/resume 通道,应合并立项)、通知类通道(或显式划归
    宿主层写入 §6)。
12. **`fs_edit`/`fs_read`/`shell_exec` 契约明确化**(Ch5):唯一匹配语义
    +失败区分(**注:引擎 fs_edit 实现已是唯一匹配并区分未命中/多处命中,
    是 STDLIB 文档未写明**);fs_read 行号+offset/limit(**引擎已实现,
    文档未写**);shell_exec 持久会话(真缺口)。

### 3.3 优先级排序的修正(书的排序轴 vs STDLIB 的排序轴)

STDLIB 按**依赖纯度**排序,书按**能力必要性**排序,冲突集中在:

- `web_search` P2 → **P1**(Ch4 基础三件套 / Ch8 自进化入口,双章施压;
  保留"无 key 则不注册"的实现);
- `ask_human` P2 → **P1**(Ch1:Constrain 层唯一 std 落地点;与 `set_timer`
  合并立项摊薄成本);
- `read_file_smart` 的行号/分页部分从 §3.6 应用层**上提为工具契约**(Ch4/5)。

---

## 4. 真正的设计冲突(需要拍板,不是补漏)

### 4.1 预制组合子 vs 现场代码编排(Ch4 §Proactive Discovery)

书主张让模型现场写编排脚本(中间变量留执行环境,省约两个数量级 token);
STDLIB 选择预制组合子 + 静态白名单(保依赖图与权限可审计)。**这是安全
/可审计性与 token 效率的实打实取舍**,且被三章共同施压(Ch4 代码编排、
Ch8 运行时技能创建、Ch10 动态拓扑)。建议:开放问题 2 落结论方向——
**manifest 层参数化白名单(声明技能集合/前缀,加载期解析),拒绝运行期
通配**;并把"代码编排路线"作为被拒绝备选显式记录成立条件。

### 4.2 blackboard(共享内存)vs 消息传递(Ch10)

引擎唯一 IPC 是黑板(Go 格言站在对面)。不必改架构,但应在文档写明
范式代价(所有权/可追溯性),std 层给出单写者约定;Envelope 已存在,
补的是"何时用 KV 何时用消息"的指引。

### 4.3 `std/style` 的形态重构(Ch2/4/5/6 四章合击)

- `json_discipline` → 删(与内核 outputs 校验重叠,弱的一方挤占 SYSTEM);
- `date_style`/`citation_style` → 下沉为 transform 校验器/outputs 强制
  `sources[]`,inline 版只作引导;
- `zh_typography` → 降级为 lint 报告形态,**禁止用于 fs_edit old_string
  场景**(参数保真度雷);
- `tone_neutral` → 唯一站得住的纯 inline;
- 补流程约定:style 变更须跑评测集回归(消融与快照 SKILL-INLINING v1 已有)。

### 4.4 §7 质量门槛的统计学重写(Ch6)

- §7.3 拆两层:回归冒烟(≥5 条,加诱导攻击 + 参数化,3 连跑无 flaky)
  /质量评测(prompt 技能 ≥50 条、跑 3 次报离散度、Pass^3、配对分析);
- §7.5 加稳定性:replay diff 为空(可复现)≠ Pass^k(可依赖),双门槛;
- 新增:参数保真度(不得静默改写输入)、描述完整性(参数示例 + 返回值
  字段 + 成本提示,72%→90%)、副作用契约(幂等声明/pre-check 形态)、
  回滚可用性(WRITE/EXEC 须有快照或 git 路径)、成本标注。

### 4.5 记忆路线:文件版先行(Ch3/5/8)

把 `memory_*` 从"等 M6"拆成两档:**P1 文件版**(MEMORY.md + Markdown
约定 + fs_* + BM25,零新依赖,OpenClaw 实证)/ P2 服务版(M6)。
配套 `std/memory` 管线技能(extract/reconcile/consolidate/check)与
写入信任审查(Lethal Triad 第四维)。§6 划线修正:sparse 进、dense 出、
RAPTOR/GraphRAG 点名排除并写明理由。

---

## 5. 修订建议总表(三路去重合并)

### P0(改动小、收益大、无前置依赖)

| # | 动作 | 来源 |
|---|---|---|
| 1 | `retry_until` 默认验证器改外部反馈型;`judge` 加负例 lint | Ch6/10 |
| 2 | §2 表格加 `idempotent`/`concurrent_safe`/`cacheable`/`cost_hint` 四列 | Ch4 |
| 3 | 统一工具错误 schema 加 `hint[]`(修复建议层) | Ch2 |
| 4 | `fs_search`/`fs_list` 契约补 cursor + total + 显式截断提示;`fs_read`/`fs_edit` 把引擎已实现的行号/唯一匹配语义写进文档 | Ch4/5 |
| 5 | 新增 `todo_write`/`todo_update`(run 级状态) | Ch2 |
| 6 | `fs_write`/`fs_edit` 可选 `if_match` 乐观锁 | Ch10 |
| 7 | §7.3 拆回归冒烟/质量评测两层 + Pass^3 稳定性门槛 | Ch6 |
| 8 | `judge` schema 升级(dimensions/veto/per-dimension)| Ch6/7 |
| 9 | `zh_typography` 降级 lint 报告;`json_discipline` 删除 | Ch4/5 |
| 10 | 新增"引擎既有守护机制"交叉引用节(sidecar 五件 + 状态栏 + WAL) | 综合 |

### P1

| # | 动作 | 来源 |
|---|---|---|
| 11 | `web_search`、`ask_human` 提级 P1(后者与 `set_timer` 合并立项) | Ch1/4/8 |
| 12 | 来源标注三件:perception 工具 `source` 字段 + `untrusted_content` inline + `injection_scan` | Ch2/3/8 |
| 13 | 检索纯函数:`chunk_text`/`rrf_merge`/`bm25_score`/`retrieval_metrics` + `contextualize_chunk` | Ch3 |
| 14 | `std/eval`:`calibrate_judge`(code,kappa)+ `pairwise_compare`(交换顺序两评) | Ch6 |
| 15 | 组合子统一 `budget` 参数 + 失败分类(retryable)+ 故障边界;`race_first`、`cross_check`、`reject_sample` | Ch5/7/10 |
| 16 | `subagent_cancel`/`subagent_status` 工具面;`make_handoff` 交接包 | Ch4/10 |
| 17 | `progress_track` + mtime 卡死检测约定(一份实现,Ch8/9/10 三处受益) | Ch8/10 |
| 18 | `shell_exec` 持久会话 + 后台/监控形态 | Ch5 |
| 19 | 多模态最低限:blob mime + `image_ref` + `describe_image` | Ch9 |
| 20 | `skill_search`(READ);渐进披露约定(catalog 只暴露 name+description) | Ch4/8 |
| 21 | `std/learn` 三件:`distill_experience`/`reflect_on_failure`/`verify_before_store` | Ch8 |
| 22 | 文件版 `std/memory`(不等 M6)+ 写入信任审查 | Ch3/5/8 |

### P2 / 专项讨论

23. `skill_write`/`tool_register` + `learned/` 分层治理(或写明不做的理由)(Ch8);
24. 文件系统四区约定 + ToolGuard 路径模板(Ch10);
25. MCP 立场表态(Ch4);`read_document` 收录或显式排除(Ch4);
26. 开放问题修订:2(manifest 参数化白名单方向)、4(能力别名加
    `family` 与 `vision` 轴)、新增"组合子 vs 代码编排"取舍记录、
    §6 补"流式/全双工不在范围"、§7 加 trace `origin: model|env` 标注。

---

## 6. 对已实现功能(SKILL-INLINING v1)的专项影响

书中四处触及 inline merge 机制,现状核对:

| 书中要求 | v1 现状 | 结论 |
|---|---|---|
| 消融:每个特性可独立关闭(Ch6) | `RunConfig.inline="off"` 已实现 | ✅ 已满足(全局粒度;per-skill 粒度记开放问题) |
| SYSTEM 可确定性渲染 + 版本快照(Ch6) | 帧内冻结快照(working 落 checkpoint,可导出) | ✅ 基本满足;缺"按 git 版本导出展开后 SYSTEM"的工具化 |
| 同帧 inline 集合冻结防 cache 击穿(Ch2) | 快照机制正是此设计 | ✅ 已满足 |
| style 变更跑评测回归 CI(Ch6) | 无 | ❌ 真缺口,流程层 |
| use-site 组合收敛防 2^N cache key(Ch2) | 无约束条款 | ❌ 文档层补约束 |

即:v1 的架构决策(消融 + 冻结快照)恰好落在书的要求线上,领先于
子分析的判断;剩余两项都是文档/流程层补丁,无需改机制。

---

## 7. 报告使用建议

- 若只做三件事:总表 #1(验证器分层)、#2-4(工具契约面)、§4.3
  (style 重构)——它们分别修复"质量闭环的可信度"、"工具面的工程成熟度"
  和"唯一会主动制造故障的现有设计"。
- STDLIB 下一版建议增加两节:「引擎既有守护机制」(§3.1 的交叉引用表)
  与「被拒绝的备选路线」(代码编排、辩论式多 agent、dense 检索,各附
  成立条件),把沉默的取舍变成显式的取舍。
- 三份逐章子分析全文(含全部原文引句与小节定位)可另行归档,本报告
  仅保留结论层。
