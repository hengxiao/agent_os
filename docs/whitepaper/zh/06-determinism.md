# 06 信号、Telemetry 与确定性工程

> 章次:06 · 状态:已实现(信号总线 / 信号目录 / JSONL WAL / 检查点与恢复 / 周期检查点 / replay / diff;OTLP 与 RL-trajectory 导出、PII 脱敏 hook、MetricsCollector 为已设计未实现) · 依据:`agent_os/src/agent_os/api/v1/signals.py`、`agent_os/src/agent_os/kernel/signals.py`、`agent_os/src/agent_os/kernel/checkpoint.py`、`agent_os/src/agent_os/telemetry/jsonl_exporter.py`、`agent_os/src/agent_os/host/shared/replay.py`、`agent_os/src/agent_os/host/shared/artifacts.py`、`docs/DESIGN.md` §5.1/§10、`docs/RUNNERS.md` §3.4

## 1. 概述

信号总线是内核的 IPC 通道:内核在 agent loop 的每个关键节点广播 `Signal`,sidecar(副车)、Telemetry、调试器、Web SSE 扇出都是它的订阅者。Telemetry 把信号流持久化为 append-only 的 JSONL WAL;checkpoint 把 run 状态与整棵帧树(含每帧上下文与记账)序列化为版本化快照;resume、replay、diff 三件确定性工具都建立在这两份落盘数据之上。分层归属:总线在内核(微内核判据的 IPC 项),WAL 与快照在 Telemetry 子系统,replay/diff 在宿主共享层——确定性是贯穿三层的工程栈,不是某个单点机制。

## 2. 动机与背景(原因)

执行摘要把"过程不可复现"列为 agent 系统的四类结构性缺陷之一:LLM 调用本身非确定,出问题时无法回放现场,事后无法重建"它当时看到了什么"。本章的机制是对这条缺陷的正面应答,其形状由四个具体的架构决定塑造。

**为什么 Telemetry 不是 sidecar。** sidecar 的语义是 fire-and-forget:ASYNC sidecar 异常只记日志,永不拖垮 run(docs/DESIGN.md §5.3)。而 WAL 与检查点要求**保证落盘**的 durability 语义——检查点恢复把它放在可靠性关键路径上,所以它升格为独立子系统,以"总线的特权订阅者"身份存在(docs/DESIGN.md §10 章首、`api/v1/signals.py:4`)。同一条总线,两种可靠性档位:观察面允许静默失败,记录面不允许。

**为什么是"恢复不是重跑"。** LLM 调用花钱、工具调用有副作用。崩溃后从头重跑既浪费 token,更可能重复执行不可逆副作用(已发出的邮件再发一遍)。因此恢复必须做到"已完成的工作不重复,剩余 LLM 调用数精确可数"(`kernel/checkpoint.py:1-5`),这决定了 checkpoint 必须保存每帧完整上下文而非只保存帧骨架。

**为什么 replay/diff 放在宿主层而不进内核。** 微内核判据下,内核只留流控制、权限控制与 IPC;replay(重建 MockProvider 脚本)与 diff(两次 run 的结构化对比)是**消费**产物数据的工具,不是 loop 推进或仲裁点,故落在 `host/shared/replay.py`,CLI 与 Web 两个 runner 共用(docs/RUNNERS.md §2.4 薄宿主边界)。

**为什么确定性要做成公理而非运气。** 设计公理第四条明确"确定性是工程目标,不是运气"(执行摘要 §2.2;docs/DESIGN.md §10.2 WAL 原则:"trajectory 是 Agent 的全部状态")。这句话的直接推论是:凡内核能观察到的状态(信号流、帧树、pending 裁决、升权台账、run 级工具状态)都必须有落盘形态,凡需要复现的场景(resume/replay)都必须能从落盘数据机械重建。

## 3. 问题陈述(解决的问题)

以下每个问题都对应一类可复现的故障场景。

- **P1 断电丢进展。** fib(5) 跑到第 6 次 LLM 调用时进程被杀:已完成的 5 次调用与已完成的子帧全部作废,重跑等于重花一遍钱。对应测试以异常模拟断电(`tests/telemetry/test_trace_checkpoint.py:70-87`)。
- **P2 配对残缺。** ASSISTANT 消息已发出 tool_call,TOOL 结果尚未写回帧上下文时断电:消息历史里悬着一个未配对调用,直接重入 loop 会把残缺 transcript 发给 provider。
- **P3 子帧已完、父帧不知。** 子帧已 DONE 并随 checkpoint 落盘,但父帧上下文里该调用只有断电崩出的错误观察:若恢复时重跑子帧,副作用重复;若保留错误观察,父帧 LLM 会看到与事实矛盾的历史。
- **P4 挂起中崩溃。** `ask_supervisor` 或升权确认已发出、答案未归时进程死亡:这不是"分发到一半",不能补 interrupted 占位——那等于欺骗模型"裁决被中断"(`kernel/checkpoint.py:31-37`)。
- **P5 行为漂移不可判定。** 改了 skill 实现后,无法回答"行为变没变":没有两次 run 的结构化对比手段,回归只能靠肉眼读日志。
- **P6 线上失败不可复现。** 生产 run 失败后,本地没有真实 API 的同等输入,无法逐帧重现现场做 RCA。
- **P7 观察面连坐。** 调试器、SSE 扇出等订阅者抛异常,若沿总线上抛会把健康的 run 拖垮——观察通道的故障域必须与被观察者隔离。
- **P8 长 run 恢复点过晚。** 只有 run 收尾才写 checkpoint 时,崩在第 999 步等于全部重来;崩溃恢复点必须能提前到"最近 N 步"。

## 4. 设计与机制(解决的方法)

### 4.1 信号目录:一张冻结的事件表

信号命名 `<阶段>:<事件>`:`pre:` 前缀同步可否决,`post:` 前缀异步观察(`api/v1/signals.py:1-5`)。目录全集 `SIGNAL_NAMES` 共 **31 个**信号名(`api/v1/signals.py:99-131`),覆盖 run 生命周期(3)、帧栈(4)、步(2)、LLM(3)、工具(2)、子技能(2)、升权(3)、逻辑执行(2)、压缩(2)、内联(1)、黑板(2)、预算(2)、supervisor 裁决(3)。`Signal` 结构只有五字段:`name/run_id/frame_id/payload/ts`(`api/v1/signals.py:134-142`)——刻意单薄,语义全部在 name 与 payload 约定里。注:docs/DESIGN.md §5.1 的目录清单成文较早(未含升权三信号、`post:context.inline`、supervisor 三信号),以代码 `SIGNAL_NAMES` 为准。

### 4.2 总线:忠实广播,故障隔离

`InProcessSignalBus`(`kernel/signals.py:28-57`)是 M0 地基实现:订阅按登记顺序排列,`emit` 按订阅序 await 全部匹配 handler(`*` 全量、精确名、`prefix.*` 后缀通配,`kernel/signals.py:19-25`)并返回结果 list。两条硬规则:

1. **handler 异常吞掉只记日志**(`kernel/signals.py:53-56`):信号是观察通道,订阅者故障不得拖垮 run——这是 P7 的直接应答;
2. **总线不裁决**:`pre:*` 的 verdicts 由内核取"首个非 Allow"(`kernel/runner.py:317` 附近),总线只负责忠实收集;多个 SYNC sidecar 的确定性顺序由 priority 与订阅序保证(docs/DESIGN.md §5.2)。

### 4.3 WAL:信号流的持久化形态

`JsonlTelemetrySink`(`telemetry/jsonl_exporter.py:25-107`)把每个 run 的全部信号 append 到 `<traces_dir>/<run_id>.jsonl`,行格式为版本化契约:

```
{"v": 1, "type": "header", "schema": "agent_os.trace/1"}        # 每文件首行
{"v": 1, "type": "signal", "name", "run_id", "frame_id", "ts", "payload"}
```

durability 三件套:行缓冲打开(每行即写,`jsonl_exporter.py:52`)、`flush()` 里显式 `os.fsync`(行缓冲只到 page cache,WAL 语义要求真正落盘,`jsonl_exporter.py:81`)、run 收尾 `close_run` 先 fsync 再关句柄(防 fd 泄漏与归档拷到半行,`jsonl_exporter.py:83-96`)。run 结束时宿主把该文件归档为产物目录的 `trace.jsonl`(`host/shared/artifacts.py:39-47`)。

### 4.4 检查点:轨迹即全部状态

`dump_checkpoint`(`kernel/checkpoint.py:126-172`)把 run(状态/usage/result/run_state/升权台账 grants)与全部帧(frame_id/skill/parent_id/深度/结果/错误/调用渊源 call_id/信任档/principal/usage/**完整上下文**)序列化为 schema v1 JSON。帧的 `status` 字段是检查点视角的进展标签:`"done"` = 帧已完成,或一步 LLM 调用都未完成(`usage.steps == 0`,无可保存进展);`"running"` = 持有未完成进展(`kernel/checkpoint.py:119-123`)。

恢复算法(`resume_from_checkpoint`,`kernel/checkpoint.py:321-388`):

```
读档 → 版本校验(v != 1 拒绝)→ 重建 Run(恢复 usage/grants/run_state)
  → 全部帧按深度登记回帧树
  → result 已结算的帧直接 pop_ok 跳过(不看 status 标签,看结算凭证)
  → 非 DONE 帧按深度从深到浅逐个处理:
      ① _settle_pending_ask      (pending 裁决:重问,不落 interrupted 占位)
      ② _settle_pending_escalation(pending 升权:重走闸门)
      ③ _settle_unpaired_calls   (结算配对残缺,见下表)
      ④ _execute_frame 带着完整上下文重入 loop
  → 根帧完成 → run.finished(不补发 run.started)
```

配对残缺的三条结算规则(`kernel/checkpoint.py:281-318`,对应 P2/P3):

| 断电后调用形态 | 判定依据 | 恢复动作 |
|---|---|---|
| 已有 `ok=True` 工具结果(或非 JSON 结果) | 已结算 | 不动 |
| 只有错误观察,且 call_id 匹配的子帧已 DONE | 子帧先恢复完成 | **就地改写**为子帧真实结果 |
| 无任何工具结果且无子帧 | 分发到一半断电 | 追加 `interrupted` 占位结果 |
| `working` 含 `_pending_ask` / `_pending_escalation` | 挂起中崩溃(P4) | 先重问/重走闸门,不适用上三条 |

关键取舍有三:

- **跳过判断看 `result` 不看 `status` 标签**(`kernel/checkpoint.py:350-352`):标签只是"有无进展"的提示,结算凭证才是幂等依据——恢复重复执行也安全。
- **就地改写而非删除重写**(P3):严格保持 tool_call/tool_result 一一配对的消息序,父帧 LLM 看到的是"调用成功了"这一事实,而非被篡改的历史。
- **从深到浅结算**:子帧先于父帧恢复 DONE,父帧结算时才能取到子帧真实结果(`kernel/checkpoint.py:356-357`)。

P8 由 `PeriodicCheckpointer`(`kernel/checkpoint.py:175-222`)应答:订阅 `post:step`,每 N 条覆盖写一次 `checkpoint.json`——文件语义是"最近现场",不保留历史快照;落盘失败捕获只记日志(与总线错误隔离同旨);`checkpoint_interval = 0` 时宿主不挂载,零行为变化(`host/shared/artifacts.py:115-118`)。

### 4.5 replay 与 diff:确定性复现与回归判据

replay 的对齐规则来自一个结构性事实:runner 只在帧上下文**追加** LLM 响应,所以 trace 中第 N 个属于帧 F 的 `post:llm.response` 信号 ↔ checkpoint 中帧 F 的第 N 条 assistant 消息(`host/shared/replay.py:1-11`)。`build_mock_script` 按信号出现顺序取出这些消息重建 MockProvider 脚本(`replay.py:49-93`);`replace_providers` 把内核 provider 面整体替换为 MockProvider 单例,注册名取 config model 的前缀(`"mock/fib"` → `"mock"`),模型路由与原 run 自然吻合(`replay.py:96-103`)。

diff 把两次 run 的信号序列按 `(name, payload.skill, payload.tool, payload.ok, payload.depth)` 逐位比较,忽略 frame_id/ts 等动态值,外加 result 相等性与 usage 汇总,报告 `first_divergence`(首个分叉下标;一方是另一方前缀时为较短者长度;`replay.py:106-135`)。diff 的自我定位是"查询而非判决":报告不含退出语义,是否算回归由人判定。

整体数据流:

```
内核 loop(runner.py)每个关键节点 emit(Signal)
        ▼
InProcessSignalBus(订阅序 await;handler 异常吞掉)
  ├─ SYNC sidecar:pre:* verdicts → 内核取首个非 Allow
  ├─ JsonlTelemetrySink ── <run_id>.jsonl(版本头+行缓冲+fsync)
  ├─ PeriodicCheckpointer ── 每 N 步覆盖写 checkpoint.json
  └─ 宿主订阅者(CLI 捕获 run_id / Web SSE 扇出 / 调试器)
        ▼ run 收尾 _finalize_run(artifacts.py:50-87)
产物四件套:meta.json / trace.jsonl / checkpoint.json / result.json
  ├─ resume:重建帧树 → 结算配对残缺 → 重入 loop(不重复已完成工作)
  ├─ replay:trace 响应序 × checkpoint assistant 消息 → MockProvider 脚本
  └─ diff:信号序列五元组逐位比较 → first_divergence
```

## 5. 效果与验证(效果)

核心行为由 11 例锚点测试固定,本次撰写已实跑通过(0.78s):

- **WAL 与恢复**(`tests/telemetry/test_trace_checkpoint.py`,3 例):trace 首行是版本头且含完整生命周期信号;fib(5) 在第 6 次调用处断电后,新内核从 checkpoint 恢复得到正确结果 `{"seq": [0,1,1,2,3]}`,且恢复后的 MockProvider 只收到 **5** 次新调用(`len(mock2.recorded) == 5`)——"恢复不是重跑"被精确计数断言;checkpoint 文件含版本号、run.usage、done/running 帧与完整帧上下文。
- **周期检查点**(`tests/kernel/test_periodic_checkpoint.py`,3 例):interval=2 时第 2 步即见"最近现场"、第 1 步不落盘;interval=0 不挂载(零行为变化);配置项经 `build_kernel` 透传。
- **replay/diff**(`tests/cli/test_replay.py`,5 例):录制 fib(4) 后 replay 产出相同结果且与原 run 的 diff 为空(`result_equal` 与 `signals_equal` 均真),replay 是新 run_id;不同参数的两次 run 被 diff 判定分叉;同参数两次 run diff 为空。

真实配置面:`instance/agent-os.toml` 的 `[run].checkpoint_interval` 即可开启周期快照;CLI 消费面为 `agent-os run/resume/replay/diff`(`host/cli/main.py:271-320`)。测试基线整体口径:当前 `pytest --collect-only` 收集 **854 例**(执行摘要成文时记 812 例,以实际收集为准)。

涟漪效应:调试器的断点命中即 pending,走同一挂起-落盘-恢复闭环(docs/DEBUGGER.md);Web SSE 扇出与 RCA 页是总线 `"*"` 订阅者(docs/RUNNERS.md §4.2);升权台账与 run 级工具状态以 additive 字段随 checkpoint 落盘,是"schema v1 不变、字段可加"契约纪律的实例(`kernel/checkpoint.py:139-141`);Skill Registry 的入库前验证门把"重放 + evaluator 确认"设计为自我进化的信任前提(docs/DESIGN.md §6.2,已设计未实现)。

## 6. 局限性与边界(局限性)

- **单进程总线。** `InProcessSignalBus` 是 M0 地基,无跨进程语义;多副本部署没有共享 WAL,分布式追踪只能等 OTLP 导出(已设计未实现)。
- **checkpoint 不捕捉外部世界。** 恢复假设工具副作用的外部状态仍然有效;幂等性靠生产标准约定(L2 必须幂等,docs/TIER-STANDARDS.md),系统本身不强制、不回滚。
- **replay 只覆盖 LLM 调用面。** 工具副作用(fs/shell/docker)按真实环境执行(docs/RUNNERS.md §3.4 边界),原 run 之后环境已变的,replay 结果可能分叉;`--sandbox` 只能把 shell/python 强制切到 docker 后端,不能冻结世界。
- **WAL 的 durability 有窗口。** 行缓冲保证到 page cache,`fsync` 只在 `flush`/`close_run` 时发生(`jsonl_exporter.py:81`);机器级断电(非进程崩溃)可能丢失最近若干行。
- **schema v1 单向兼容,无迁移器。** resume 遇其他版本直接 `ValueError`(`kernel/checkpoint.py:329-330`);旧档缺新字段时按保守缺省恢复(tier 缺省 `none` 不放大权限、principal 缺省 `None` 单用户语义,`checkpoint.py:233-246`),代价是新语义对旧档不生效。
- **周期快照是覆盖写。** "最近现场"不保留历史;崩溃窗口内(不足 N 步)的进展仍要重放,N 是恢复点提前量与 IO 开销的手工折中。
- **diff 不是语义等价判定。** 只比五元组,工具参数值、token 数、消息文本的变化不视为分叉(usage 另列供人读);frame_id/ts 被忽略也意味着并发交错差异不在比较面内。
- **观察面故障静默。** handler 异常只进日志(`kernel/signals.py:53-56`),Telemetry 若持续写盘失败,trace 缺行不会有任何 run 级告警——这是故障隔离的反面代价。
- **已设计未实现项。** OTLP/OpenInference 映射、RL-trajectory 训练导出、PII 脱敏 hook、MetricsCollector(docs/DESIGN.md §10.2)均为文档预留;`TelemetrySink.snapshot` 与 `JsonlExporter.export` 在代码里是 `NotImplementedError`(`jsonl_exporter.py:105-122`),快照语义实际由 `Kernel.checkpoint` 承担。

## 7. 引用

- 文档:`docs/DESIGN.md` §5.1(信号目录)、§5.2-5.3(sidecar 契约与监督语义)、§10(Telemetry)、§14.1(契约冻结);`docs/RUNNERS.md` §2.2(产物布局)、§3.4(replay 与 golden 调试)
- 源码:`agent_os/src/agent_os/api/v1/signals.py`(信号目录与 `Signal`)、`agent_os/src/agent_os/kernel/signals.py`(总线)、`agent_os/src/agent_os/kernel/checkpoint.py`(检查点/恢复/周期快照)、`agent_os/src/agent_os/kernel/runner.py`(发射点与 verdict 收集)、`agent_os/src/agent_os/telemetry/jsonl_exporter.py`(JSONL WAL)、`agent_os/src/agent_os/host/shared/replay.py`(replay/diff)、`agent_os/src/agent_os/host/shared/artifacts.py`(产物四件套与挂载接线)、`agent_os/src/agent_os/host/cli/main.py`(CLI 命令)
- 测试:`agent_os/tests/telemetry/test_trace_checkpoint.py`(3 例)、`agent_os/tests/kernel/test_periodic_checkpoint.py`(3 例)、`agent_os/tests/cli/test_replay.py`(5 例)
