# Sidecars:信号驱动的监督

> 章次:07 · 状态:部分实现(M4 主体已落地:SidecarSupervisor、RunControl、五个内置副车可用;HumanApproval 为骨架,entry point 注册、LLM 驱动副车的配套设施未实现) · 依据:`agent_os/src/agent_os/sidecars/`、`agent_os/src/agent_os/api/v1/{sidecars,signals,control}.py`、`agent_os/src/agent_os/kernel/{signals,control,runner}.py`、`docs/DESIGN.md` §5

## 1. 概述

副车(sidecar)是被信号触发的监督者:观察运行、监督行为,必要时否决、纠偏、暂停或强停(docs/DESIGN.md §5)。在 OS 映射表里它对应"中断处理程序 + 看门狗 + seccomp"(§1)。信号总线是内核的 IPC 通道,副车是总线上的策略载体;内核只保留裁决(fail-closed、优先级、超时),策略(审什么、何时纠偏)全部外置到副车——这是"策略在副车,仲裁在内核"(§1 公理 3)的直接落点。它与 Telemetry 共用同一条信号流但语义不同:Telemetry 要求保证落盘,是特权订阅者,不算副车(§5.1、§10)。

## 2. 动机与背景(原因)

**为什么这些检查不放进内核?** 微内核判据(F/P/I,§1 公理 1)逐条过:预算阈值、循环阈值、工具规则表都不是"没有它 loop 无法推进"(F 不满足);它们自身不是否决仲裁点——仲裁只有一处,即 runner 的 `_arbitrate_pre`(P 只属于内核);它们也不是子系统间公共通道(I 不满足)。三条皆不满足,于是一律子系统化。反过来,裁决逻辑(首个非 Allow 生效、SYNC 超时 2 秒、异常 fail-closed)留在内核,因为它正是那个 P 点:若裁决可由副车自定,fail-closed 纪律就会被某个写错的副车悄悄改成 fail-open。

**为什么是信号驱动,而不是轮询或包装 loop?** 信号是内核本来就要为 Telemetry 落盘而发的生命周期事件;让监督者订阅同一条流,观察面与记录面天然一致——副车看到的和 trace 里回放的是同一份数据,不存在"监督者看到另一个世界"的口径分裂。订阅顺序因此成为纪律:Telemetry 先订阅(先落 WAL),副车其次,调试器最后(docs/DEBUGGER.md §1,`runtime/builder.py:216-246`)。

**为什么默认不喂自由文本?** 审批者若消费主模型的自由文本,注入者就有了经 rhetoric 通道影响审批的路径(§5.2 输入最小化原则)。副车默认只收结构化载荷:工具名、参数、调用签名、usage(`sidecars/builtins.py:6-8`);确需更多上下文必须显式声明 `needs_free_text = True` 并自担注入风险。

**为什么有两套预算机制?** 内核记账自带硬兜底:`Kernel.account()` 在每步后检查 RunConfig 的 `max_steps`/`max_cost`,超限直接抛 `BudgetExceeded`(`kernel/runner.py:1230-1252`)——"预算中止判决"按公理 1 本就列在流控制里。BudgetGuard 则是可替换的策略层:阈值独立于 RunConfig、stop 可降级为 pause + 通知(§2.4),供长任务宿主决定加预算还是放弃。测试装配时刻意把 `max_cost` 调到 100,把预算舞台让给 BudgetGuard(`tests/sidecars/test_builtin_sidecars.py:87`)。

## 3. 问题陈述(解决的问题)

1. **成本/步数失控**:模型陷入"再试一次"循环,无人喊停——每步 `cost=0.02` 永不收敛,run 一路烧穿预算。
2. **重复调用死循环**:rolling window 丢掉早期工具结果后,模型以完全相同的参数重调同一工具(调用签名 `sha1(name+args)` 不变),无干预则永远转下去(实验依据见 docs/reports/ch02-context-engineering.md §LoopDetector)。
3. **帧级停滞**:相邻两步间隔超过挂钟阈值(如 600s),应用层无任何进展——区别于 provider 流式停滞,这是行为层的静默。
4. **危险工具调用**:模型(或被注入内容诱导)发出 `system.shell.exec {"command": "rm -rf /"}`——必须在执行前拦下,且让模型知道"为什么不行",而不是无声消失。
5. **危险动态代码**:`python_orchestrate` 提交 `import os; os.system('echo pwned')`,需在进沙箱前就有一道静态扫描闸门。
6. **结果不达标**:帧要弹栈了,reviewer 认为"报告缺少引用"——需要打回并注入纠偏观察,让 loop 继续而不是交付次品。
7. **监督者自身故障**:SYNC 副车在关键路径上抛异常或超时——静默放行等于注入后审批失效,随机崩溃又不可用;必须有确定性兜底。

## 4. 设计与机制(解决的方法)

### 4.1 信号目录与总线

信号命名 `<阶段>:<事件>`;`pre:` 前缀表示同步可否决,`post:` 表示异步观察(§5.1)。目录冻结在契约层(`api/v1/signals.py:49-96`,31 个名字进 `SIGNAL_NAMES` 供契约测试断言),结构为 `Signal{name, run_id, frame_id, payload, ts}`(`api/v1/signals.py:134-142`)。订阅模式支持三种:`"*"` 全量、精确名、`"prefix.*"` 后缀通配(`kernel/signals.py:19-25`)。总线 `emit` 按订阅序 await 全部匹配 handler 并收集返回值;handler 异常吞掉记日志——订阅者故障不得拖垮 run(`kernel/signals.py:41-56`)。

### 4.2 数据流与裁决链

```
内核 agent loop(kernel/runner.py)
  │ 关键节点构造 Signal(payload 结构化:depth/skill/usage/calls…)
  ▼
InProcessSignalBus.emit —— 按订阅序 await,异常吞掉
  ├─ Telemetry("*")        特权订阅者,先落 WAL(非副车,§5.1)
  ├─ SYNC 共享 handler(每 pattern 一个,sidecars/supervisor.py:54-75)
  │    └─ 按 priority 升序逐个 wait_for(sync_timeout=2s)
  │       异常/超时 → Veto("fail-closed: …");首个非 Allow 即返回
  ├─ ASYNC handler(每副车一个,supervisor.py:77-91)
  │    └─ create_task 派发即返回;异常只记日志,永不拖垮 run
  └─ DebugController(最后订阅,绕过 supervisor,见 §5 涟漪)
  ▼
runner 回收 verdicts → _arbitrate_pre:首个非 Allow 生效(runner.py:316-321)
  ├─ Veto   → 跳过分发,理由作错误观察回写 / reviewer 打回续跑
  ├─ Modify → patch 并入 call.args(仍过 Tool Registry schema 校验)
  ├─ Stop/Pause → RunAborted,沿栈上抛不可被单帧吞掉(§3.2)
  └─ InjectMessage/ForceCompress → 经 RunControl 落地后继续
副车反向操控:RunControl(唯一特权通道)→ 置标志 → 下一 safe point 生效
```

### 4.3 契约:Sidecar、Verdict、RunControl

副车契约五字段加一方法(`api/v1/sidecars.py:97-107`):`subscriptions`、`mode`(SYNC 在关键路径可否决;ASYNC 纯观察)、`priority`(多个 SYNC 副车的确定性裁决顺序)、`needs_free_text`(默认 False)、`on_signal(sig, ctl) -> Verdict`。Verdict 是七型联合(`api/v1/sidecars.py:45-94`):Allow / Modify(patch) / Veto(reason) / InjectMessage / Pause / Stop / ForceCompress。

关键不在"有哪些 verdict",而在**每个发射点仲裁哪些 verdict**——这由 runner 逐点实现,现状如下(以代码为准):

| 发射点 | Veto | Modify | Stop/Pause | Inject/ForceCompress |
|---|---|---|---|---|
| `pre:step`(runner.py:323-336) | 中止 run(RunAborted) | 不处理 | 中止 run | 经 ctl 落地后续跑 |
| `pre:tool.call`(runner.py:570-583) | 跳过分发,`kind=vetoed`、`retryable=false` 回写 | patch 并入 args,继续分发 | 中止 run | 不处理 |
| `pre:frame.pop`(runner.py:371-383) | 纠偏观察"reviewer 打回:{reason}"入帧,回 loop 续跑 | 不处理 | 中止 run | 不处理 |
| `pre:logic.exec`(编排路径,runner.py:792-800) | `kind=vetoed` 回写,不进沙箱 | 不处理 | 中止 run | 不处理 |

**Veto 理由回写**(§5.2)是行为闭环的核心:理由复用工具失败语义作为错误观察写入帧上下文,模型下一步能看到"为什么不行"并换策略,而非只进 trace 供事后审计。

`RunControl` 是副车操控运行的唯一通道(`api/v1/control.py:19-33`,实现 `kernel/control.py`):`stop` 置 run 中止标志,runner 在下一个 `pre:step` safe point 检查并抛 RunAborted(control.py:35-37;runner.py:400-402);`pause` v1 语义同 stop,理由带 `"paused: "` 前缀(control.py:39-41);`inject_message` 向指定帧追加 USER/INJECTED 消息,帧不存在则丢弃记日志不崩 run(control.py:43-51);`force_compress` 在帧 `working` 置标志,runner 在 maintain 前消费(runner.py:407-408);`get_frame_tree`/`get_usage` 是副车(代码)→内核的 pull,不耗 token。

### 4.4 监督语义与内置副车

`SidecarSupervisor` 统一托管(`sidecars/supervisor.py`):SYNC 副车按订阅 pattern 注册共享 handler(同 pattern 只挂一次),handler 内按 priority 稳定排序逐个 `wait_for`,**超时或任何异常一律 fail-closed 视为 Veto**;ASYNC 副车每个 (pattern, sidecar) 挂一个 fire-and-forget handler;`close()` 在 run 收尾取消全部在跑 ASYNC 任务。

| 副车 | 订阅 | 模式/优先级 | 行为(实现要点) |
|---|---|---|---|
| BudgetGuard(builtins.py:31-77) | `post:llm.response` | ASYNC/100 | 按 run_id 累计成本/步数/挂钟,超限 → `ctl.stop`;`_stopped` 集合去重,每 run 只停一次 |
| LoopDetector(builtins.py:80-129) | `post:step` | ASYNC/100 | 按帧跟踪签名(sha1 前 12 位,runner.py:147-153),连续重复达 threshold → 注入带操作指令的纠偏消息;再犯 max_strikes 次 → stop;签名变化即重置 |
| StallDetector(builtins.py:132-171) | `post:step` | ASYNC/100 | 相邻 step 间隔超 `max_idle_seconds` → 先纠偏注入,再犯 → stop;`clock` 可注入(测试用假钟) |
| ToolGuard(builtins.py:174-199) | `pre:tool.call` | SYNC/10 | 规则表 `(工具名, 参数正则, 理由)`,命中 → Veto;能力上限自声明:正则对 shell 组合爆炸无效,防护主体是沙箱+权限 |
| CodeScanner(builtins.py:202-234) | `pre:logic.exec` | SYNC/10 | 默认危险集(import os/os.system/ctypes/subprocess/socket)扫描 `payload["source"]`,命中 → Veto;无 source 的发射(如 code 技能帧)无可扫描对象,放行 |
| HumanApproval(builtins.py:237-253) | `pre:tool.call`(EXEC 级) | SYNC/20 | **骨架**:`on_signal` 抛 `NotImplementedError("M4")`,见 §6 |

**设计取舍**:

- **首个非 Allow 生效,而非投票/合并**:裁决必须确定性、可解释——同优先级保持稳定注册序,审计时能指认"是哪一条规则拦的";代价是副车间无合议机制,叠加策略靠 priority 排序表达。
- **fail-closed 为默认**:SYNC 副车是安全闸门,它坏了等于审批缺失;宁可误拦(可用性损失)不可漏放(安全损失)。ASYNC 则相反:观察者的故障绝不许传导到 run。
- **纠偏走注入消息而非改 prompt**:LoopDetector/StallDetector 的纠偏复用 §7.3 状态注入通道,必须带操作指令("停止重试,改用其他策略或宣告受阻")——裸读数不改变行为,读数+策略才改变(docs/reports/ch02-context-engineering.md)。
- **Modify 先于 Registry 校验落地**:patch 并入 args 后仍走 §8.1 分发流水线的 schema 校验,副车改参数不会绕过类型闸门。

## 5. 效果与验证(效果)

**测试证据**(直接锚点 17 例,另有相邻套件覆盖交互面):

- `tests/sidecars/test_builtin_sidecars.py`(8 例):预算超限强停(`test_budget_guard_stops_run`,匹配 RunAborted 含 "BudgetGuard");循环先纠偏后强停,且纠偏消息确实进入后续 LLM 请求(`test_loop_detector_injection_reaches_context` 断言 mock 录制里出现"停止重试");ToolGuard veto 理由回写(`kind=vetoed`、`retryable is False`、理由原文在工具结果里)且被 veto 的调用**不产生** `post:tool.call`(`test_tool_guard_veto_skips_dispatch`);reviewer 打回后第二次弹栈放行、打回理由进入第二次请求;SYNC 副车抛异常 → fail-closed → RunAborted 含 "fail-closed";StallDetector 假钟验证。
- `tests/sidecars/test_code_scanner.py`(2 例):危险代码被 veto 且无 `post:logic.exec`(未执行);干净代码放行(对照组)。
- `tests/kernel/test_run_control.py`(7 例):pause 理由带 "paused: " 前缀;注入消息包装为 USER/INJECTED 并出现在后续请求;帧不存在时注入/强压被丢弃不崩 run;`get_frame_tree` 嵌套形状;`get_usage` 快照。
- 相邻覆盖:`tests/logic/test_orchestration.py` 断言编排脚本的 syscall 照发 `pre:tool.call`、ToolGuard veto 理由回到脚本(:211),且编排期间的 RunAborted 不被降级为脚本可吞的错误(:424 回归测试);`tests/test_contracts.py` 断言冻结信号名(含 `pre:frame.pop`)。

**真实配置**:`instance/agent-os.toml:43-45` 与三个 examples 的 TOML 均启用 `budget_guard = { max_cost = 2.0 }`、`loop_detector = { threshold = 3, max_strikes = 2 }`;`runtime/config.py:193-224` 以白名单解析 `[sidecars]` 段(仅 budget_guard / loop_detector / tool_guard_rules,未知键报 ConfigError)。

**涟漪效应**:

- **调试器刻意绕过副车体系**(docs/DEBUGGER.md §1):SYNC 通道 2 秒超时 fail-closed 与交互式暂停水火不容,DebugController 直接订阅总线、订阅序在 Telemetry 之后——暂停点本身先落 trace 再暂停。这反过来验证了"信号总线是公共通道、副车只是其中一类订阅者"的架构判断。
- **supervisor 子系统接管"人答"通道**(docs/SUPERVISOR.md §1.2):分工被明确为"副车是规则化监督(确定性,fail-closed);supervisor 是决策路由",HumanApproval 未来下沉为 supervisor 的宿主策略。
- **编排沙箱复用同一闸门**(docs/CODE-ORCHESTRATION.md §2.3):沙箱内 syscall 回到内核 `_dispatch_call`,白名单、ToolGuard veto、信号、记账全部沿用——监督面没有为快路径开后门。

## 6. 局限性与边界(局限性)

1. **HumanApproval 只是骨架**:`on_signal` 抛 `NotImplementedError`(builtins.py:253),EXEC 级工具当前没有人审闸门;人工裁决的实际承载是 supervisor 的 `ask_supervisor` 与升权确认(docs/ESCALATION.md),两者都不经过副车契约。
2. **规则类副车的能力上限是自声明的**:ToolGuard/CodeScanner 的正则对 shell 组合爆炸、混淆代码无效,docstring 明写"防护主体是沙箱(§9.2)+ 权限(§8.2)";语义解析器仅作预留。把它们当成安全边界是误用。
3. **仲裁覆盖不全**:`pre:skill.invoke`(runner.py:857)、`pre:llm.request`(runner.py:411)、`pre:compress`(context/manager.py:310)与 code 技能帧的 `pre:logic.exec`(runner.py:523)当前**只发射不仲裁**——DESIGN §5.1 的"pre 可否决"在这些点是契约先行,实现未跟(以代码为准)。
4. **ASYNC 强停有延迟且状态不进检查点**:`ctl.stop` 在下一个 safe point 才生效,在跑的一步/一个工具调用会完成;BudgetGuard/LoopDetector 的累计器是进程内存,检查点只序列化 run 与帧(`kernel/checkpoint.py:12-16`)——跨进程恢复后侧车累计清零(内核自身的 max_cost 兜底因 `run.state.usage` 入档而存活)。
5. **Pause verdict 是"带标签的 abort",不是真暂停**(control.py:39-41;docs/DEBUGGER.md §1 原话):BudgetGuard 的 stop→pause 降级在 v1 实际是"换理由中止",可恢复的暂停属于调试器/supervisor 通道。
6. **`budget.warning`/`budget.exceeded` 在冻结目录里但无人发射**:`account()` 直接抛 BudgetExceeded(runner.py:1249-1252),80% 预警目前只以 Web 进度条客户端语义存在(docs/WEB-UI.md:186)。
7. **LoopDetector 的观察面有两个盲区**:签名是精确哈希,语义等价但参数字面不同的调用不可见;编排脚本内部的 syscall 序列不进 `post:step` 载荷,脚本内死循环它看不见(docs/CODE-ORCHESTRATION.md §4 已列待补)。
8. **SYNC 副车是关键路径成本**:每个 `pre:tool.call` 最坏要排一条 priority 链、每环 2 秒上限;§16 风险表要求压测预算内才可注册 SYNC。supervisor 的"心跳、重启(仅 ASYNC)"(§5.3)未实现,现只有注册与关停。
9. **输入最小化靠自律不靠强制**:`needs_free_text=False` 的载荷裁剪未做机制保证(docs/reports/dev-status.md:58);自定义副车的 entry point 注册在 pyproject.toml 里是注释占位,第三方副车只能经代码装配。

## 7. 引用

- 设计:`docs/DESIGN.md` §1(公理)、§2.4(故障致死性分层)、§3.1-3.2(loop 与恢复)、§5(Sidecars 全章)、§7.3(状态注入)、§16(风险表)
- 契约:`agent_os/src/agent_os/api/v1/sidecars.py`、`signals.py`、`control.py`
- 实现:`agent_os/src/agent_os/sidecars/{supervisor,builtins}.py`、`agent_os/src/agent_os/kernel/{signals,control,runner,checkpoint}.py`、`agent_os/src/agent_os/runtime/{builder,config}.py`、`agent_os/src/agent_os/context/manager.py`
- 相邻文档:`docs/DEBUGGER.md` §1、`docs/SUPERVISOR.md` §1.2/§10、`docs/CODE-ORCHESTRATION.md` §2.3/§4、`docs/ESCALATION.md`、`docs/reports/dev-status.md`、`docs/reports/ch02-context-engineering.md`
- 测试:`agent_os/tests/sidecars/test_builtin_sidecars.py`、`agent_os/tests/sidecars/test_code_scanner.py`、`agent_os/tests/kernel/test_run_control.py`、`agent_os/tests/logic/test_orchestration.py`、`agent_os/tests/test_contracts.py`
- 配置样例:`instance/agent-os.toml`、`agent_os/examples/*/agent-os.toml`
