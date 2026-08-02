# 微内核与执行模型

> 章次:01 · 状态:已实现(fork/join 原语 `parallel_invoke` 与 pause 真挂起语义为已设计未实现,见 §6)· 依据:`agent_os/src/agent_os/kernel/runner.py`、`agent_os/src/agent_os/kernel/checkpoint.py`、`docs/DESIGN.md` §1-3

## 1. 概述

Kernel Runner 是 Agent OS 的微内核:映射表("微内核(调度 + 权限 + IPC)→ Kernel Runner")的落点,只做三件事——**流控制**、**权限控制**、**IPC**(docs/DESIGN.md §1 公理 1;`kernel/runner.py:156-200` 的 `Kernel` 类)。九个功能子系统(Providers/Tools/Skills/Context/Logic/Sidecars/Telemetry/Memory/Blackboard)全部外置,内核只持有它们的契约句柄,由 `KernelBuilder` 装配(`runtime/builder.py:149-247`)。在执行模型上,Run 是进程、SkillFrame 是栈帧、agent loop 是单帧执行体;检查点与断电恢复(`kernel/checkpoint.py`)是内核可靠性职责的一部分。本章是执行摘要 §3.1-3.2 的深化:不只给出分层与 loop 轮廓,而是逐行级地说明 loop 的判定顺序、仲裁点位置与恢复算法。

## 2. 动机与背景(原因)

agent loop 有一种结构性的引力:每加一种能力(压缩、监督、重试、持久化、人在环路),最省事的写法都是塞进 loop 本体。几轮之后 loop 变成不可分解的一团——机制互相纠缠,替换一个压缩器要动分发代码,想做"裸模型基线"消融实验时发现没有可拆的东西。Agent OS 的对策是把"什么配留在内核"写成一条可执行的判据(docs/DESIGN.md §1 公理 1):

- **F**:没有它 loop 无法推进(帧栈、分发、取消);
- **P**:它是权限/否决仲裁点(白名单检查、verdict 仲裁);
- **I**:它是子系统间唯一公共通道(信号总线、RunControl)。

满足其一才留在内核,其余一律子系统化。推论的另一半同样重要:内核**不**执行逻辑代码(交 Logic Kernel,执行点唯一才可仲裁)、**不**组装提示词(交 Context,估算口径唯一)、**不**做持久化(交 Telemetry——WAL 的 durability 语义与 sidecar 的 fire-and-forget 不兼容,§10)。这不是审美偏好:仲裁点分散等于没有仲裁点,审计面随内核体积线性膨胀。

第二个关键决策是**用 Python 的 await 链直接实现调用栈**,而不是另造显式调度器(docs/DESIGN.md §3.1):父帧挂起 = `await` 点,子帧完成 = 协程返回,asyncio cancellation 天然沿栈传播。代价是协程栈对外不可见——sidecar 无法检视、RunControl 无法寻址。因此帧对象仍显式登记在 `FrameStack`(`kernel/stack.py:15-27`):活动帧、按 id 索引、父子邻接表三份视图,供 `pre:frame.pop` 否决、帧树检视与检查点序列化使用。隐式栈管控制流,显式栈管仲裁与持久化,这是执行模型里最核心的一次"各取所长"。

## 3. 问题陈述(解决的问题)

1. **特权操作四散,无处可仲裁。** 模型能调工具、调子技能、生成代码,若每条路径各自直通外部世界,监督策略没有一个可挂的点。需要一切特权操作收敛到单一分发口。
2. **子任务中间过程污染父帧。** 一个子技能跑 20 步、读入几千行工具输出,若全部涌入父帧上下文,父帧既贵又被稀释。需要子帧弹栈时整段 transcript 折叠为一个返回值("isolation over compression",docs/DESIGN.md §2.3)。
3. **中断制造孤儿 tool_call。** 取消若发生在"assistant 已发 tool_calls、tool_result 尚未写入"之间,下一次请求携带未配对调用,多数 provider API 直接拒绝。配对原子性(§7.4 不变量 2)必须在中断路径同样成立。
4. **断电后恢复 = 从头重跑。** 长 run 在第 N 步崩掉,若恢复意味着重付前 N-1 步的 LLM 费用并重放工具副作用,可靠性机制就没有经济意义。
5. **失控 run 无法在干净边界停下。** 预算耗尽或死循环时,若在任意指令边界硬切,帧树停在"删了一半"的状态。停止只能发生在分发边界(safe point)。
6. **失败传播不分层。** 一个子帧的普通失败(如工具超时)不该带崩整棵帧树;而预算超支、强停这类 run 级判决,绝不能被某一帧的 `try/except` 吞掉。

## 4. 设计与机制(解决的方法)

### 4.1 内核边界:三件事及其落点

| 职责 | 内容 | 代码锚点 |
|---|---|---|
| 流控制 | agent loop、帧栈、分发仲裁、safe-point 取消、spawn 并发、预算中止、输出修复熔断 | `runner.py:351-486`、`kernel/stack.py`、`runner.py:1130-1196`、`runner.py:1230-1252` |
| 权限控制 | manifest 白名单检查、三层交集的入口、升权闸调用点、verdict 仲裁 | `runner.py:547-598`、`runner.py:839-915`、`runner.py:315-336` |
| IPC | 信号总线、RunControl(中止标志/注入/强压)、StatusBoard 帧间状态 | `kernel/signals.py:28-57`、`kernel/control.py:25-83`、`runner.py:1202-1214` |

### 4.2 单帧 agent loop:判定顺序即安全语义

`_frame_loop`(`runner.py:395-486`)每一步的顺序是刻意的——先仲裁、后行动:

```
run_frame(frame)                       # runner.py:351
  ├─ pre/post:frame.push,push 时深度兜底(stack.py:29-39)
  └─ loop(每一步):
     1. safe point:查 _stop_flags[run_id],置位 → RunAborted      (runner.py:400-402)
     2. pre:step 发信号,verdict 仲裁:Stop/Veto/Pause → 中止;     (runner.py:403-406)
        InjectMessage/ForceCompress 经 RunControl 落地后继续       (runner.py:323-336)
     3. 消费帧级强制压缩标志 → context.maintain / build            (runner.py:407-410)
     4. pre:llm.request → providers.chat → post:llm.response       (runner.py:411-431)
        (dict 形 tool_calls 先入站归一化为 ToolCall,runner.py:415-424)
     5. 响应入帧 + 记账(account,超 max_steps/max_cost 抛硬失败)     (runner.py:432-433)
     6. 无 tool_calls → outputs 校验:失败写错误观察重试,           (runner.py:434-453)
        连败 2 次(_OUTPUT_VALIDATION_MAX_FAILURES)→ 帧失败上抛
     7. 逐个分发 call;CancelledError → 先写 interrupted 占位        (runner.py:454-466)
        tool_result 再向上抛(配对原子性在中断路径同样成立)
     8. post:step(调用签名列表,LoopDetector 观察面)+ StatusBoard (runner.py:468-485)
  ├─ pre:frame.pop 可否决:Veto → 纠偏观察回写帧上下文,回 loop;     (runner.py:368-383)
  │  Stop → RunAborted;无否决才 pop_ok
  └─ post:frame.pop,帧 transcript 随协程返回折叠为父帧的一条 tool result
```

设计取舍写明两处。其一,**输出修复熔断阈值取 2**(`runner.py:110`):最终答案不合 `outputs` schema 时给模型一次自我纠正的机会(错误观察回写,§3.2 输出修复循环),连败即判帧失败——给少了模型没有修复空间,给多了就是拿 token 赌概率,且违反"恢复路径独立熔断"的硬规则。其二,**记账与预算判决在内核而非 sidecar**(`runner.py:1230-1252`):`max_steps`/`max_cost` 是 RunConfig 的硬边界,必须沿栈上抛为 `RunAborted`/`BudgetExceeded`(`kernel/errors.py:25-34`),不可被单帧吞掉;而 BudgetGuard sidecar 面向的是宿主可配的软降级(stop→pause)场景(docs/DESIGN.md §2.4 故障致死性分层)。

### 4.3 分发与压栈:一切特权操作的单一闸门

`_dispatch_call`(`runner.py:547-598`)是全部调用的唯一入口,按名分流:

- `skill.<name>` / `skill__<name>` 伪工具 → `_invoke_skill` 压栈(两种前缀均受理——docs/DESIGN.md §3.3 写的是 `skill__<name>`,仓库示例技能与测试实际使用 `skill.<name>`,以代码为准,见 `runner.py:550`、`runner.py:842-847`);
- `python_orchestrate` → 沙箱编排(`runner.py:718-837`,syscall 回调仍回到 `_dispatch_call`,无权限提升);
- `ask_supervisor` → 就地挂起等裁决(`runner.py:604-646`);
- 其余 → manifest 白名单检查(不在名单即结构化错误观察,不执行)→ `pre:tool.call` 仲裁(Veto 跳过分发且理由回写、Modify 改参数、Stop/Pause 中止)→ Tool Registry 流水线 → `post:tool.call`。

`_invoke_skill`(`runner.py:839-915`)是帧树的生长点:白名单检查 → `pre:skill.invoke` → 深度兜底(超 `max_depth` 抛 `MaxDepthExceeded`)→ **升权闸**(被调 skill 推导档高于调用帧档时,先校验参数、再经 supervisor 确认通道裁决,`runner.py:864-882`;机制本体见升权章)→ `make_frame` 构建子帧并登记父帧 `call_id`(检查点配对用)→ 子帧继承被调 skill 的推导档 → `await run_frame(child)` 压栈,父帧挂起。失败传播在此分层:`MaxDepthExceeded`/`RunAborted` 原样上抛,其余一切子帧异常折叠为父帧的一条错误观察(`runner.py:900-913`,含 `hint` 续传)——普通失败交给父帧 LLM 自行补救,run 级判决沿栈直通 Run 边界。

### 4.4 verdict 仲裁与 RunControl:策略在外,判决在内

多个 SYNC sidecar 对同一 `pre:*` 信号各出 verdict 时,内核 `_arbitrate_pre`(`runner.py:315-321`)取**首个非 Allow**(发射端已按 priority 排序,`None` 视为 Allow)——确定性的、与 sidecar 数量无关的判决。sidecar 不直接打断 loop:它对运行的一切操控都经 RunControl(`kernel/control.py:25-83`)落到内核状态——`stop`/`pause` 置 `_stop_flags` 表,下一个 `pre:step` safe point 由 runner 自己抛 `RunAborted`;`inject_message` 追加 USER/INJECTED 消息;`force_compress` 在帧工作内存置标志,maintain 前消费。这就是公理 3 的工程形态:**策略(审什么)在 sidecar,仲裁(fail-closed、优先级、超时)在内核**;取消只发生在分发边界,不存在"删了一半被打断"的帧。

### 4.5 检查点与恢复:轨迹即全部状态

检查点遵循 WAL 原则(docs/DESIGN.md §10.2):帧 transcript 完整入档即检查点,`dump_checkpoint`(`kernel/checkpoint.py:126-172`)把 run 状态(含 usage、run 级工具状态、升权批准台账)与全部帧(上下文、working、pinned、usage、tier、principal、call_id)序列化为带版本头 `{"v": 1}` 的 JSON(`checkpoint.py:73`)。恢复不是重跑:`resume_from_checkpoint`(`checkpoint.py:321-388`)重建 Run 与帧树后,已有 `result` 的帧直接跳过,未完成帧**从深到浅**结算(子帧先恢复 DONE,父帧结算才能取到真实结果),最深的未完成帧带完整上下文重入 loop——剩余 LLM 调用数精确可数。

断电会在帧上下文留下三种配对残缺,恢复时由 `_settle_unpaired_calls`(`checkpoint.py:281-318`)逐一结算:① 已有成功结果 → 不动;② 只有断电崩出的错误观察、但对应子帧(按 `call_id` 匹配)已 DONE → **就地改写**为子帧真实结果;③ 完全无结果且无子帧 → 补写 `interrupted` 占位(与 §3.1 中断配对同形)。两类挂起调用不算断电残缺、不落占位:挂起在 `ask_supervisor` 的帧凭 `working["_pending_ask"]` 重新提问结算(`runner.py:648-673`),挂起在升权闸的凭 `working["_pending_escalation"]` 重走闸门(`runner.py:1097-1124`)——批准则当场补建子帧跑完,拒绝则写 `PERMISSION_DENIED`,配对原子性天然闭合。`PeriodicCheckpointer`(`checkpoint.py:175-222`)作为总线订阅者每 N 个 `post:step` 覆盖写"最近现场",崩溃恢复点从终态提前到最近 N 步;落盘失败只记日志,不拖垮 run。

### 4.6 并发原语:串行为默认,后台帧已实现

串行 `await` 是默认形态;`spawn_frame`/`wait_frame`(`runner.py:1130-1196`)实现 §3.4 的后台帧:白名单/深度/升权检查与 invoke 一致,子帧经 `asyncio.create_task` 独立运行,join 退化为读终态(异常原样上抛),父子经 StatusBoard 交换滚动状态。fork/join 扇出 `parallel_invoke`(批内故障隔离、first-success 结算)在 docs/DESIGN.md §3.4 已设计,**当前未实现**——全仓仅 `api/v1/tools.py:105` 的 `concurrency_safe` 字段注释提及,为其预留的契约字段先行。

## 5. 效果与验证(效果)

测试证据(本章直接覆盖面):`tests/kernel/` 10 个文件 92 例 + `tests/telemetry/test_trace_checkpoint.py` 3 例,共 95 例,实测全部通过(2.88s);全仓 `pytest --collect-only` 收集 854 例(写作时点)。关键锚点:

- **执行模型纵向切片**(`tests/kernel/test_fib_slice.py`,5 例):递归技能 `demo.fib`(`agent_os/skills/skills.yaml`,fib(n) 先 invoke 自己算 fib(n-1)、再调沙箱工具求和)端到端返回正确数列;帧树形状断言——fib(5) 恰好压 4 帧、深度 1-4、LLM 调用恰好 10 次、沙箱执行恰好 3 次;**帧隔离断言**——父帧第二次请求的消息序列为 `[SYSTEM, USER, ASSISTANT, TOOL]`,子帧整段轨迹折叠为一条 `{"ok": true, "value": {"seq": [0,1,1,2]}}` 工具结果,且同一帧相邻请求的 SYSTEM 前缀逐字节一致;`max_depth=2` 时 fib(4) 抛 `MaxDepthExceeded`;`bad_brain` 连败触发 `OutputValidationError`。
- **断电恢复**(`tests/telemetry/test_trace_checkpoint.py`,3 例):fib(5) 在第 6 次 LLM 调用处注入断电异常,新内核从检查点恢复后**只补 5 次调用**(`len(mock2.recorded) == 5`)即返回完整数列——"恢复不是重跑"以调用计数钉死;检查点 JSON 含版本头、run.usage、running/done 混合帧状态与完整帧上下文。
- **RunControl**(`tests/kernel/test_run_control.py`,7 例):pause 在下一 safe point 以 `"paused: "` 前缀理由中止;注入消息包装为 USER/INJECTED 并出现在后续 LLM 请求中;帧不存在时注入/强压静默丢弃不崩 run;`get_frame_tree` 返回嵌套帧树;`get_usage` 返回记账快照。
- **周期检查点**(`tests/kernel/test_periodic_checkpoint.py`,3 例):按 `post:step` 计数覆盖写,run 结束停止计数。

涟漪效应:信号目录 + 显式帧树是后续系统共同的地基——Telemetry 以特权订阅者身份把同一信号流落为 JSONL WAL(`builder.py:216-218`);调试器(`kernel/debug.py`)与 replay 直接复用总线与帧上下文;升权闸与干净 context 不变量都挂在本章的分发点与帧隔离语义上——若 `_invoke_skill` 不是唯一压栈点,升权模型无处可立。消融方面,`KernelBuilder` 允许只装 MockProvider + 空工具表跑裸 loop(docs/DESIGN.md §14.2 的微内核纯粹性验收;`runtime/builder.py:17-18`),大量测试即以这种最小装配运行(如 `tests/helpers/kernels.py:38-73`)。

## 6. 局限性与边界(局限性)

1. **`parallel_invoke` 未实现。** §3.4 三原语缺其一:没有 fork/join 扇出,并行只能靠 spawn 后台帧 + Blackboard 手工组合,first-success 与批内故障隔离语义暂无落点。
2. **pause 的 v1 语义就是 stop。** `RunControlImpl.pause` 仅给理由加 `"paused: "` 前缀(`kernel/control.py:39-41`),不存在"挂起运行中 run、稍后原地继续";要续跑只能走检查点序列化 + resume 重建,且 resume 是从最近检查点重入 loop,不是从暂停指令处精确续行。
3. **`max_wall_time` 内核不判。** `Kernel.account` 只检查 `max_steps`/`max_cost`(`runner.py:1245-1252`);挂钟上限由 BudgetGuard sidecar 承担(`sidecars/builtins.py:72-73`),且其阈值来自宿主 TOML 配置而非 `RunConfig.max_wall_time` 字段——未装配 sidecar 时 run 的挂钟时长无熔断。
4. **spawn 后台帧不随检查点恢复。** `_spawned` 任务表与 `_stop_flags` 是进程内状态(`runner.py:195-200`);断电后 spawn 的子树丢失,恢复语义是"code 父帧整体重跑、重新走到 spawn 闸门"(`runner.py:1164-1166` 注释)——后台帧已产生的副作用不重放但也不结算,需调用方自行保证幂等。
5. **单事件循环,协程级并行。** 并行分支共享同一 asyncio 循环,CPU 密集的 code 技能会阻塞兄弟分支(docs/DESIGN.md §3.4 注明);多机分布式执行是 §17 明示的非目标。
6. **检查点 schema v1 无迁移路径。** 版本不符直接 `ValueError`(`checkpoint.py:329-330`);格式演进靠"additive 字段 + 缺省值"维持兼容(如 tier/principal 的缺省恢复,`checkpoint.py:234-246`),破坏性变更目前没有答案。
7. **safe point 管不住进行中的副作用。** 停止只在 step 边界与分发边界生效;已进入沙箱的编排脚本被强停时,"已执行的 syscall 副作用已发生",内核只能把已执行清单回报给模型决定补偿(`runner.py:816-826`),不做事务回滚——内建事务/补偿是 §3.2 明示不做的。
8. **成本熔断依赖价格表。** 无价格表时 `usage.cost` 恒为 0,`max_cost` 与 BudgetGuard 均不触发(fail-open 在钱上),builder 只能告警(`runtime/builder.py:124-132`)。
9. **代码组织遗留。** `kernel/dispatch.py` 是 M0 骨架(方法体全是 `NotImplementedError`),真实分发在 `runner.py` `_dispatch_call`;`Kernel.run()` 的 docstring 与 `builder.build()` 注释里"注入 Dispatcher"的表述与现状不符——不影响行为,但读代码者需以 runner 为准。

## 7. 引用

- 设计文档:`docs/DESIGN.md` §1(设计公理)、§2(核心抽象)、§3(执行模型)、§10.2(WAL 原则)、§14.2(组装与消融)、§17(非目标)
- 内核源码:`agent_os/src/agent_os/kernel/runner.py`、`kernel/checkpoint.py`、`kernel/stack.py`、`kernel/signals.py`、`kernel/control.py`、`kernel/errors.py`、`kernel/run.py`、`kernel/dispatch.py`(M0 遗留骨架)
- 契约层:`agent_os/src/agent_os/api/v1/frames.py`、`api/v1/run.py`、`api/v1/signals.py`、`api/v1/tools.py:105`(`concurrency_safe` 预留)
- 组装:`agent_os/src/agent_os/runtime/builder.py`
- 相邻系统:`docs/ESCALATION.md`(升权闸)、`docs/SUPERVISOR.md`(裁决通道)、`docs/CODE-ORCHESTRATION.md`(编排沙箱)
- 测试:`agent_os/tests/kernel/`(92 例)、`agent_os/tests/telemetry/test_trace_checkpoint.py`(3 例)、`agent_os/tests/helpers/brains.py`、`agent_os/tests/helpers/kernels.py`
- 示例:`agent_os/skills/skills.yaml`(`demo.fib` 递归技能)
