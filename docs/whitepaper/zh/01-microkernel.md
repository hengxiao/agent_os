# 微内核与执行模型

> 章次:01 · 状态:已实现(pause 真挂起语义 2026-09-29 落地,见 §6)· 依据:`agent_os/src/agent_os/kernel/runner.py`、`agent_os/src/agent_os/kernel/checkpoint.py`、`docs/DESIGN.md` §1-3

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
| 流控制 | agent loop、帧栈、分发仲裁、safe-point 取消、spawn/parallel_invoke 并发、预算中止、输出修复熔断 | `runner.py:384-541`、`kernel/stack.py`、`runner.py:1435-1763`、`runner.py:1927-1949` |
| 权限控制 | manifest 白名单检查、三层交集的入口、升权闸调用点、verdict 仲裁 | `runner.py:599-668`、`runner.py:914-1008`、`runner.py:349-369` |
| IPC | 信号总线、RunControl(中止标志/注入/强压/子树级联取消)、StatusBoard 帧间状态 | `kernel/signals.py:28-57`、`kernel/control.py:27-103`、`runner.py:1899-1911` |

### 4.2 单帧 agent loop:判定顺序即安全语义

`_frame_loop`(`runner.py:433-542`)每一步的顺序是刻意的——先仲裁、后行动:

```
run_frame(frame)                       # runner.py:389
  ├─ pre/post:frame.push,push 时深度兜底(stack.py:29-39)
  └─ loop(每一步):
     1. safe point:查 _stop_flags[run_id] → RunAborted;查帧级     (runner.py:437-445)
        stop 标志(cancel_subtree 置位)→ SubtreeCancelled,不杀 run
     2. pre:step 发信号,verdict 仲裁:Stop/Veto/Pause → 中止;     (runner.py:446-449)
        InjectMessage/ForceCompress 经 RunControl 落地后继续       (runner.py:361-375)
     3. 消费帧级强制压缩标志 → context.maintain / build            (runner.py:450-453)
     4. pre:llm.request → _llm_call → post:llm.response           (runner.py:454-474)
        (RunConfig.stream 缺省开:caps 支持流式走 _stream_call
        chunk 循环,逐 chunk 发 post:llm.chunk,否则回落 chat;
        dict 形 tool_calls 先入站归一化为 ToolCall,runner.py:456-467)
     5. 响应入帧 + 记账(await account:run 级超限抛硬失败;          (runner.py:475-476)
        续查 manifest limits 帧/子树预算——根帧超限炸 run,
        其余子树级中止,见本节末段)
     6. 无 tool_calls → outputs 校验:失败写错误观察重试,           (runner.py:477-496)
        连败 2 次(_OUTPUT_VALIDATION_MAX_FAILURES)→ 帧失败上抛
     7. 逐个分发 call;CancelledError → 先写 interrupted 占位        (runner.py:497-508)
        tool_result 再向上抛(配对原子性在中断路径同样成立)
     8. post:step(调用签名列表,LoopDetector 观察面)+ StatusBoard (runner.py:524-542)
  ├─ pre:frame.pop 可否决:Veto → 纠偏观察回写帧上下文,回 loop;     (runner.py:406-424)
  │  Stop → RunAborted;无否决才 pop_ok
  └─ post:frame.pop,帧 transcript 随协程返回折叠为父帧的一条 tool result
```

设计取舍写明两处。其一,**输出修复熔断阈值取 2**(`runner.py:124`):最终答案不合 `outputs` schema 时给模型一次自我纠正的机会(错误观察回写,§3.2 输出修复循环),连败即判帧失败——给少了模型没有修复空间,给多了就是拿 token 赌概率,且违反"恢复路径独立熔断"的硬规则。其二,**记账与预算判决在内核而非 sidecar**(`runner.py:2156-2201`):`max_steps`/`max_cost` 是 RunConfig 的硬边界,必须沿栈上抛为 `RunAborted`/`BudgetExceeded`(`kernel/errors.py:26-34`),不可被单帧吞掉;而 BudgetGuard sidecar 面向的是宿主可配的软降级(stop→pause)场景(docs/DESIGN.md §2.4 故障致死性分层)。

**帧/子树级预算已于 2026-09-28 进入内核强制**(`runner.py:2203-2282` `_check_subtree_budgets`)。挂载点是 manifest 的 `limits` 块:`max_cost` 为新增 additive 字段(默认 None;非数值/bool 在加载期即 `SkillLoadError`,`skills/manifest.py:26-37`),`max_steps` 是既有声明、自此有了执行点。两字段口径有意不同:`max_steps` 限**帧自身** agent loop 步数(全仓既有 manifest 均按此口径声明,与 RunConfig.max_steps"全 run 总步数"对仗),`max_cost` 限**该帧及全部后代**的 cost 求和——花费沿子树累积,预算是"这棵子树总共花多少"。检查点在 `account()` 末尾:run 级检查不动,随后沿 `parent_id` 链逐祖先收集预算帧(链上无预算声明走快路径,零开销;子树求和复用 `_collect_subtree`;`account` 因此改 async——祖先分档要 await `cancel_subtree`;压缩排干 `_drain_compress_usage` 与 `ctx.chat` 记账点同查)。写路径不动:父帧 usage 仍不含子帧,聚合仍发生在读取侧(§4.6 `subtree_usage`)。超限按预算帧位置分档:根帧 → `BudgetExceeded` 炸 run;当前帧 → `SubtreeCancelled("budget: ...")`;祖先 → `cancel_subtree`,子树终态、invoke 边界折叠为 interrupted 错误观察、run 继续。触发前发 `budget.exceeded` 信号(冻结目录 `api/v1/signals.py:101` 的首次发射;payload 含 depth/frame_id/skill 与 max_cost/max_steps/subtree_cost/subtree_steps),每预算帧恰好一次(`_budget_tripped` 防重,进程态不持久化,resume 幂等)。与 BudgetGuard 的分层不变:内核强制是硬边界,BudgetGuard 仍是宿主可配的软降级策略层;`budget.warning`(80%)的发射点已于 2026-09-30 补上(同一 `_check_subtree_budgets` 路径:任一有效字段用量 ≥80% 上限时每帧每字段恰好一次,`_budget_warned` 进程态防重,先于 exceeded、不消费 trip;payload 为 exceeded 同键 + `source` + `field`)。

### 4.3 分发与压栈:一切特权操作的单一闸门

`_dispatch_call`(`runner.py:694-763`)是全部调用的唯一入口,按名分流:

- `skill.<name>` / `skill__<name>` 伪工具 → `_invoke_skill` 压栈(两种前缀均受理——docs/DESIGN.md §3.3 写的是 `skill__<name>`,仓库示例技能与测试实际使用 `skill.<name>`,以代码为准,见 `runner.py:697`、`runner.py:1012-1015`);
- `python_orchestrate` → 沙箱编排(`runner.py:878-1008`,syscall 回调仍回到 `_dispatch_call`,无权限提升);
- `ask_supervisor` → 就地挂起等裁决(`runner.py:764-807`);
- 其余 → manifest 白名单检查(不在名单即结构化错误观察,不执行)→ `pre:tool.call` 仲裁(Veto 跳过分发且理由回写、Modify 改参数、Stop/Pause 中止)→ Tool Registry 流水线 → `post:tool.call`。

`_invoke_skill`(`runner.py:1009-1103`)是帧树的生长点:白名单检查 → `pre:skill.invoke` → 深度兜底(超 `max_depth` 抛 `MaxDepthExceeded`)→ **升权闸**(被调 skill 推导档高于调用帧档时,先校验参数、再经 supervisor 确认通道裁决,`runner.py:1036-1057`;机制本体见升权章)→ `make_frame` 构建子帧并登记父帧 `call_id`(检查点配对用)→ 子帧继承被调 skill 的推导档 → `await run_frame(child)` 压栈,父帧挂起。失败传播在此分层:`MaxDepthExceeded`/`RunAborted` 原样上抛,其余一切子帧异常折叠为父帧的一条错误观察(`runner.py:1084-1100`,含 `hint` 续传)——普通失败交给父帧 LLM 自行补救,run 级判决沿栈直通 Run 边界。

### 4.4 verdict 仲裁与 RunControl:策略在外,判决在内

多个 SYNC sidecar 对同一 `pre:*` 信号各出 verdict 时,内核 `_arbitrate_pre`(`runner.py:354-358`)取**首个非 Allow**(发射端已按 priority 排序,`None` 视为 Allow)——确定性的、与 sidecar 数量无关的判决。sidecar 不直接打断 loop:它对运行的一切操控都经 RunControl(`kernel/control.py:30-107`)落到内核状态——`stop`/`pause` 分别置 `_stop_flags`/`_pause_flags` 表,下一个 safe point 由 runner 自己检查(**stop 优先**)抛 `RunAborted`/`RunPaused`(后者 Run 边界落 PAUSED、发 run.paused、照常落 checkpoint,可 resume,2026-09-29);`inject_message` 追加 USER/INJECTED 消息;`force_compress` 在帧工作内存置标志,maintain 前消费。这就是公理 3 的工程形态:**策略(审什么)在 sidecar,仲裁(fail-closed、优先级、超时)在内核**;取消只发生在分发边界,不存在"删了一半被打断"的帧。

**外部事件入口已在宿主层落地,在跑通道的事件批处理于 2026-09-29 补齐**:`POST /api/events`(`host/web/app.py:819`)按 run 状态分三通道——running 的 run 按 `[events].batch` 分流:批处理开(缺省)时事件条目 `{type, text, at}` 经跨线程桥追加进根帧工作内存队列 `working["_event_queue"]`(action="queued"),runner 在下一步 build 前排干为一条批头消息(`[event 批处理 N 条]` + 编号列表,USER/INJECTED,meta `{"kind":"event-batch","count":N}`;超 `batch_max` 丢最旧并计 `dropped`);批处理关时维持原行为,经 `ctl.inject_message` 立即注入根帧(action="injected")。paused 的 run 先把事件追加进 checkpoint 根帧的 context.messages,再走既有 resume(action="resumed");不带 target 时以指定 skill 起新 run(action="started",input 缺省 `{"event": {...}}`)。排干点(`Kernel._drain_event_queue`,`runner.py:470-506`,调用点 :584)选在 pre:step safe point 之后、build 之前,与 FORCE_COMPRESS 消费同区,是批头的**唯一并入点**:书上"任一 tool.result 返回时即注入"的步内多点排干被有意否决——步内并入会插在 assistant tool_calls 与对应 TOOL 结果之间,破坏 §7.4 配对不变量 2;代价是事件至多少被看见一步,换来配对原子性与"恢复即排干"(队列随 checkpoint 的 working 序列化落盘,resume 后第一次 build 前自然排干,零钩子)。批头计数同时承担 status bar 事件标记的注意力职能(评估结论:status bar 排干后恒 0,不单列 events 行)。事件归宿为根帧单点,子帧上下文纯净性不动。配置为 `[events]` 段三键(strict,缺段全默认):`batch = true`/`batch_max = 50`/`event_text_max = 2000`(事件文本截断)。这是"内核保持回合制、唤醒源留给宿主"的兑现:契约零改动,内核只在 build 前新增一个排干点;宿主信号 `event.received` 经 per-run hub 投递(SSE 实时与回放可见),刻意不进 api/v1、不写 trace.jsonl。持久事件队列/跨 run 队列、按帧寻址注入、事件过滤/限流与 `monitor_shell`/`connect_channel` 仍开口。

### 4.5 检查点与恢复:轨迹即全部状态

检查点遵循 WAL 原则(docs/DESIGN.md §10.2):帧 transcript 完整入档即检查点,`dump_checkpoint`(`kernel/checkpoint.py:126-174`)把 run 状态(含 usage、run 级工具状态、升权批准台账)与全部帧(上下文、working、pinned、usage、tier、principal、call_id)序列化为带版本头 `{"v": 1}` 的 JSON(`checkpoint.py:73`)。恢复不是重跑:`resume_from_checkpoint`(`checkpoint.py:321-394`)重建 Run 与帧树后,已有 `result` 的帧直接跳过,未完成帧**从深到浅**结算(子帧先恢复 DONE,父帧结算才能取到真实结果),最深的未完成帧带完整上下文重入 loop——剩余 LLM 调用数精确可数;其 `finally` 与 `run()` 对称补 `_release_run` 收尾(`checkpoint.py:387-394`,在册后台帧/telemetry 句柄/工具临时目录不滞留)。

断电会在帧上下文留下三种配对残缺,恢复时由 `_settle_unpaired_calls`(`checkpoint.py:281-320`)逐一结算:① 已有成功结果 → 不动;② 只有断电崩出的错误观察、但对应子帧(按 `call_id` 匹配)已 DONE → **就地改写**为子帧真实结果;③ 完全无结果且无子帧 → 补写 `interrupted` 占位(与 §3.1 中断配对同形)。两类挂起调用不算断电残缺、不落占位:挂起在 `ask_supervisor` 的帧凭 `working["_pending_ask"]` 重新提问结算(`runner.py:808-834`),挂起在升权闸的凭 `working["_pending_escalation"]` 重走闸门(`runner.py:1279-1313`)——批准则当场补建子帧跑完,拒绝则写 `PERMISSION_DENIED`,配对原子性天然闭合。`PeriodicCheckpointer`(`checkpoint.py:175-224`)作为总线订阅者每 N 个 `post:step` 覆盖写"最近现场",崩溃恢复点从终态提前到最近 N 步;落盘失败只记日志,不拖垮 run。

### 4.6 并发原语:三原语齐备

串行 `await` 是默认形态;`spawn_frame`/`wait_frame`(`runner.py:1530-1563`)实现 §3.4 的后台帧:白名单/深度/升权检查与 invoke 一致(spawn 管线拆为 `_spawn_whitelist_check`/`_spawn_gate`/`_spawn_register` 三段,`runner.py:1464-1845`),子帧经 `asyncio.create_task` 独立运行,join 退化为读终态(异常原样上抛;子树被级联取消时改抛 `SubtreeCancelled`,是否捕获由 code 技能决定),父子经 StatusBoard 交换滚动状态。fork/join 扇出 `parallel_invoke` 已落地(`runner.py:1795-1859`,辅助 `_parallel_branch`/`_parallel_unsafe_tool`/`_parallel_cancelled`/`_parallel_cancel_all` :1860-1931;code 技能侧经 `LogicContext.parallel()` 委托,`kernel/logic_context.py:149-156`,契约 `api/v1/logic.py:222`):起批前串行预检逐分支复用 spawn 前置段;`all_settled` 结构化 gather 永不上抛,`depends_on` 前置失败标 cancelled 不启动;`first_success` done-flag 只赢一次、败方 `cancel_subtree` 级联取消 + `settle_timeout` 等 ack、父侧唯一 join 点幂等结算;硬失败(RunAborted/BudgetExceeded/MaxDepthExceeded)不折叠炸 run;`max_concurrency` 以 Semaphore 封顶。`api/v1/tools.py:105` 预留的 `concurrency_safe` 字段在此迎来首个强制消费:code 分支白名单含未声明工具即串行降级、占满全部并发额度(fail-safe 不拒绝,日志可观察),prompt 分支豁免。配套落地的还有 §5.2 的子树级联取消(`Kernel.cancel_subtree`,`runner.py:1954-1993`;`RunControl.cancel_frame`,`kernel/control.py:63-71`)与子树记账读视图(`Kernel.subtree_usage`,`runner.py:2062-2104`;`RunControl.get_subtree_usage`,`kernel/control.py:97-103`)——前者是 first_success 败方收尾与 `subagent_cancel` 类组合子的引擎地基,后者按子树聚合九字段 Usage,rca usage_panel 每帧行同步加 `subtree` 字段(`host/web/rca.py:122-167`)。**并行分支的预算切分于 2026-09-30 落地**(DESIGN §17 开放问题 1 关闭):默认保持**按需抢占**——共享 run 池、记账点事后检查,与记账模型同构(LLM 花费事前不可知,无预留挂点);显式切分走逐分支 `budget` 键(`parallel_invoke` 分支 dict;`spawn_frame(..., budget=…)` keyword-only;均为 `{"max_steps", "max_cost"}` strict 键集,非法即 SkillLoadError,镜像 manifest limits 校验),记账点**按字段覆盖** manifest `limits`(缺席字段回落 manifest;max_steps 限分支根帧自身步数、max_cost 限子树求和),超限只级联取消该分支(parallel 条目 ok=False、兄弟无感、run 存活;spawn 的 wait_frame 原样抛 SubtreeCancelled),`budget.exceeded`/`budget.warning` payload 以 `"source": "branch"|"manifest"` 区分额度来源——后者同批补上发射点(用量 ≥80% 每帧每字段恰好一次,先于 exceeded)。LogicContext.spawn 与 syscall spawn 刻意不暴露该参数(契约冻结);沙箱桥零改动,branches dict 逐字透传,code 技能 `ctx.parallel` 分支带 budget 直接生效。

## 5. 效果与验证(效果)

测试证据(本章直接覆盖面):`tests/kernel/` 16 个文件 137 例 + `tests/telemetry/test_trace_checkpoint.py` 3 例,共 140 例,实测全部通过;全仓 `pytest --collect-only` 收集 1911 例(1861 passed / 10 skipped / 40 xfailed,本次核对时点)。关键锚点:

- **执行模型纵向切片**(`tests/kernel/test_fib_slice.py`,5 例):递归技能 `demo.fib`(`agent_os/skills/skills.yaml`,fib(n) 先 invoke 自己算 fib(n-1)、再调沙箱工具求和)端到端返回正确数列;帧树形状断言——fib(5) 恰好压 4 帧、深度 1-4、LLM 调用恰好 10 次、沙箱执行恰好 3 次;**帧隔离断言**——父帧第二次请求的消息序列为 `[SYSTEM, USER, ASSISTANT, TOOL]`,子帧整段轨迹折叠为一条 `{"ok": true, "value": {"seq": [0,1,1,2]}}` 工具结果,且同一帧相邻请求的 SYSTEM 前缀逐字节一致;`max_depth=2` 时 fib(4) 抛 `MaxDepthExceeded`;`bad_brain` 连败触发 `OutputValidationError`。
- **断电恢复**(`tests/telemetry/test_trace_checkpoint.py`,3 例):fib(5) 在第 6 次 LLM 调用处注入断电异常,新内核从检查点恢复后**只补 5 次调用**(`len(mock2.recorded) == 5`)即返回完整数列——"恢复不是重跑"以调用计数钉死;检查点 JSON 含版本头、run.usage、running/done 混合帧状态与完整帧上下文。
- **RunControl**(`tests/kernel/test_run_control.py`,9 例):pause 在下一 safe point 落可恢复挂起(RunPaused → PAUSED + run.paused,resume 续跑),stop 语义不变;注入消息包装为 USER/INJECTED 并出现在后续 LLM 请求中;帧不存在时注入/强压静默丢弃不崩 run;`get_frame_tree` 返回嵌套帧树;`get_usage` 返回记账快照;`cancel_frame` 置帧级 stop 标志、DFS 收齐子树,子树终态(SubtreeCancelled)而 run 存活。
- **parallel_invoke**(`tests/kernel/test_parallel_invoke.py`,12 例):按分支序返回;分支故障隔离与 depends_on 级联;逐分支白名单/深度预检;批形态错抛 SkillLoadError;first_success 锁定并级联取消败方、全败返回错误条目;max_concurrency 封顶;concurrency_safe 闸串行降级;run stop 中止在跑批;BudgetExceeded 不折叠;checkpoint resume 批整体重发。
- **子树记账**(`tests/kernel/test_subtree_usage.py`,4 例):`subtree_usage` 沿后代求和;根帧子树等于 run 级记账;未知帧返回零值 Usage;`RunControl.get_subtree_usage` 委托内核。
- **周期检查点**(`tests/kernel/test_periodic_checkpoint.py`,3 例):按 `post:step` 计数覆盖写,run 结束停止计数。

涟漪效应:信号目录 + 显式帧树是后续系统共同的地基——Telemetry 以特权订阅者身份把同一信号流落为 JSONL WAL(`builder.py:216-218`);调试器(`kernel/debug.py`)与 replay 直接复用总线与帧上下文;升权闸与干净 context 不变量都挂在本章的分发点与帧隔离语义上——若 `_invoke_skill` 不是唯一压栈点,升权模型无处可立。消融方面,`KernelBuilder` 允许只装 MockProvider + 空工具表跑裸 loop(docs/DESIGN.md §14.2 的微内核纯粹性验收;`runtime/builder.py:17-18`),大量测试即以这种最小装配运行(如 `tests/helpers/kernels.py:38-73`)。

## 6. 局限性与边界(局限性)

1. **在跑批不随 checkpoint 恢复,分支 usage 不可回滚。** `parallel_invoke` 与 spawn 同形:批不做检查点配对,断电后在跑批不恢复,code 父帧 resume 时整体重跑、批整体重发,调用方须保证幂等(`runner.py:1616-1617`);分支 usage 实时入 run 记账,first_success 败方被级联取消后其消耗不返还——与"进行中的副作用既成事实"的既定语义一致。子树级联取消同为 point-in-time 收集:取消发起后新 spawn 的帧不在取消集内,帧级 stop 标志不持久化。
2. **pause 是 checkpoint 恢复型挂起,不是原地续行(2026-09-29 真语义落地后的边界)。** `RunControlImpl.pause` 置独立 `_pause_flags`(`kernel/control.py:43-50`),safe point(stop 优先)抛 `RunPaused`,Run 边界落 PAUSED、发 `run.paused`(不发 run.aborted)、照常落 checkpoint;恢复走检查点序列化 + resume 重建(新内核 config 可换,§2.4"加预算再继续")——即从最近检查点重入 loop,不是从暂停指令处精确续行。supervisor await 与调试会话的进程内挂起是另外两条通道(run 保持 RUNNING);CLI 无 pause 子命令。
3. **`max_wall_time` 内核不判。** `Kernel.account` 的 run 级检查只判 RunConfig `max_steps`/`max_cost`(`runner.py:2193-2200`;manifest `limits` 帧/子树预算另查,见 §4.2 末段);挂钟上限由 BudgetGuard sidecar 承担(`sidecars/builtins.py:72-73`),且其阈值来自宿主 TOML 配置而非 `RunConfig.max_wall_time` 字段——未装配 sidecar 时 run 的挂钟时长无熔断。
4. **spawn 后台帧不随检查点恢复。** `_spawned` 任务表(run 分桶 `dict[run_id, dict[frame_id, (parent_id, task)]]`)与 `_stop_flags`/`_frame_stop_flags` 是进程内状态(`runner.py:226-238`);断电后 spawn 的子树丢失,恢复语义是"code 父帧整体重跑、重新走到 spawn 闸门"(`runner.py:1501` 注释)——后台帧已产生的副作用不重放但也不结算,需调用方自行保证幂等。
5. **单事件循环,协程级并行。** 并行分支共享同一 asyncio 循环,CPU 密集的 code 技能会阻塞兄弟分支(docs/DESIGN.md §3.4 注明);多机分布式执行是 §17 明示的非目标。
6. **检查点 schema v1 无迁移路径。** 版本不符直接 `ValueError`(`checkpoint.py:329-330`);格式演进靠"additive 字段 + 缺省值"维持兼容(如 tier/principal 的缺省恢复,`checkpoint.py:234-246`),破坏性变更目前没有答案。
7. **safe point 管不住进行中的副作用。** 停止只在 step 边界与分发边界生效;已进入沙箱的编排脚本被强停时,"已执行的 syscall 副作用已发生",内核只能把已执行清单回报给模型决定补偿(`runner.py:986-995`),不做事务回滚——内建事务/补偿是 §3.2 明示不做的。
8. **成本熔断依赖价格表。** 无价格表时 `usage.cost` 恒为 0,`max_cost` 与 BudgetGuard 均不触发(fail-open 在钱上),装配期只能告警(`runtime/config.py:583-588`)。
9. **流式消费的边界。** runner 缺省走 stream(`RunConfig.stream=True`,caps 不支持或 Mock 未配 `stream_scripts` 时自动回落 chat);流中不重试——Manager 提交点语义:首 chunk 产出前的停滞/错误可退避重试,产出后错误直接上抛(`providers/manager.py:195-258`);resume 时整步重跑(流式不改变检查点粒度);`post:llm.chunk` 仅 ASYNC 观察,sidecar 不逐 chunk 仲裁;token 级 UI 未做(Web hub 环形缓冲 2000 的挤占问题留后续),web_platform 直达 chat 路径未流式化。
10. **事件批处理只在根帧、只在 build 前排干(2026-09-29 落地后的边界)。** 在跑事件经 `POST /api/events` 入根帧队列 `working["_event_queue"]`,下一步请求组装前才并入一条批头消息——事件至多少被看见一步;步内多点排干("任一 tool.result 返回时即注入")经裁决不做,因为它会把消息插进 assistant tool_calls 与 TOOL 结果之间,破 §7.4 配对不变量 2(§4.4)。按帧寻址注入(事件直达指定子帧)、事件过滤/限流、跨 run 队列仍开口。

## 7. 引用

- 设计文档:`docs/DESIGN.md` §1(设计公理)、§2(核心抽象)、§3(执行模型)、§10.2(WAL 原则)、§14.2(组装与消融)、§17(非目标)
- 内核源码:`agent_os/src/agent_os/kernel/runner.py`、`kernel/checkpoint.py`、`kernel/stack.py`、`kernel/signals.py`、`kernel/control.py`、`kernel/errors.py`、`kernel/run.py`(`kernel/dispatch.py` 死骨架已于 2026-09-27 清理批删除)
- 契约层:`agent_os/src/agent_os/api/v1/frames.py`、`api/v1/run.py`、`api/v1/signals.py`、`api/v1/tools.py:105`(`concurrency_safe`,parallel_invoke 已消费)、`api/v1/logic.py:222`(`parallel`)、`api/v1/control.py:31,37`(`cancel_frame`/`get_subtree_usage`)
- 组装:`agent_os/src/agent_os/runtime/builder.py`
- 相邻系统:`docs/ESCALATION.md`(升权闸)、`docs/SUPERVISOR.md`(裁决通道)、`docs/CODE-ORCHESTRATION.md`(编排沙箱)
- 测试:`agent_os/tests/kernel/`(137 例)、`agent_os/tests/telemetry/test_trace_checkpoint.py`(3 例)、`agent_os/tests/helpers/brains.py`、`agent_os/tests/helpers/kernels.py`
- 示例:`agent_os/skills/skills.yaml`(`demo.fib` 递归技能)
