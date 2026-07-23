# 综述:书中功能分类与 Agent OS 微内核子系统重构

> 输入:10 份章节对比报告(`reports/ch00`–`ch10`,ch1 报告因中断缺失,其要点已被 ch00/ch04/ch05 覆盖)
> 基准:DESIGN.md v0.3
> 结论先行:**新增 3 个子系统(Telemetry / Memory / Blackboard),1 个改名扩容(Context Compression → Context),内核按微内核原则瘦身为"流控制 + 权限控制 + IPC"。**

---

## 1. 微内核判据

用户的架构定位:内核只负责**权限控制**与**流控制**,子功能尽可能由独立子系统承担。据此给出三条判据,作为全文分类的唯一标准:

> 一个功能留在内核,当且仅当:
> **(F)** 没有它 agent loop 无法推进(流控制);或
> **(P)** 它是权限/否决的仲裁点(权限控制);或
> **(I)** 它是子系统间唯一的公共通道(IPC = 信号总线)。
> 其余一切功能都是子系统,一切子系统都可替换、可缺席。

"可缺席"同时是验收标准(呼应 ch6"消融必须架构期内置"):KernelBuilder 不挂任何子系统(仅 MockProvider + 空工具表)时,裸 loop 仍能跑通——这是微内核纯粹性的消融测试。

**按此判据,当前 DESIGN.md 的内核越界两处**:① `build_request`(组装提示词是策略,不是流控);② TraceRecorder 落盘(持久化不是流控)。两者分别让渡给 §5、§4.1 的子系统。内核记账保留最小集(steps/tokens/cost 计数器)——预算是流控中止判决的依据,属 (F);记账的汇聚与导出归子系统。

---

## 2. 功能总分类

10 份报告去重后约 60 个功能点,按归属分为十类。优先级:P0 = v1 契约冻结前必须进 `api/v1`(哪怕只进字段不实现);P1 = v1 应做;P2 = 增量可做;P3 = 开放问题/文档;APP = 应用层,不进内核。

### A. 上下文组装与压缩 → Context 子系统(改名扩容,见 §5)

| 功能 | 来源 | 优先级 |
|---|---|---|
| 前缀稳定性不变量(指令/schema 字节稳定、动态数据只末尾追加、spill 替换串冻结、工具序固定) | ch2 | **P0**(写入 §7.3 不变量) |
| Status Bar 注入(内核记账数据、key-value、附操作策略、ephemeral;含预算感知 BAVT) | ch2/ch0/ch10 | P1(依赖 A 的组装职责) |
| context-aware summarize + 保留契约(架构决策/验证状态/标识符逐字)+ 摘要档位可配 | ch2 | P1 |
| contextualized spill preview(主体+时间+意图前缀) | ch3 | P1 |
| 压缩熔断 + `[COMPRESSED]` 幂等标记 + 缓存失效成本核算 | ch2/ch5 | P1(熔断机制属内核 E 类) |
| narrate 策略(多模态旁白式压缩)+ 多模态 token 口径 | ch9 | P2 |
| `compression: "off"` 消融档 | ch6 | P1(一行配置 + 文档) |
| 驱逐价值序(低价值优先,可插拔 scoring) | ch2/ch3 | P3 |
| 消息来源标注约定(`[FROM_X]`) | ch4 | P2 |

### B. 可观测与轨迹 → Telemetry 子系统(**新增**,见 §4.1)

| 功能 | 来源 | 优先级 |
|---|---|---|
| trace = WAL 的设计定位(恢复 = 重放轨迹 + 静态前缀) | ch10 | **P0**(原则入文档) |
| trace schema 版本化(append-only + 版本头) | ch0 | P0(契约) |
| OpenTelemetry / OpenInference 映射(帧树 ≡ span 树) | ch6 | P1 |
| 训练就绪导出(压缩前原始报文 + 消息 provenance 支撑 loss masking) | ch7 | P2 |
| PII 脱敏 hook | ch3 | P2 |
| MetricsCollector(action legality rate / path efficiency 汇聚) | ch6 | P2 |
| 检查点快照(M5 既有事项重新归属) | ch10/原 §13 | P1 |

### C. 记忆与知识 → Memory 子系统(**新增**,见 §4.2)

| 功能 | 来源 | 优先级 |
|---|---|---|
| MemoryService 契约:读写分离、检索层权限过滤、pinned 注入常驻事实 | ch3 | P1(契约先定) |
| 经验存储:append-only、强制 source tagging、按 tag 可驱逐、新鲜度一等属性 | ch8 | P2 |
| 通道隔离(检索结果以"参考资料"角色注入、显式无指令效力)+ 注入作用域按帧声明 | ch8 | P1(安全底线,随契约) |
| VFS 存储区域语义(私有 scratchpad / 共享 workspace / 外部挂载 / 内置只读)+ 乐观锁/worktree 并发控制 | ch10 | P2 |
| LocalFileMemoryService baseline(Markdown 文件 + 检索工具,MEMORY.md 式) | ch5/ch8 | P2 |

### D. 共享状态与帧间通信 → Blackboard 子系统(**新增**,见 §4.3)

| 功能 | 来源 | 优先级 |
|---|---|---|
| StatusBoard:run 作用域 KV,append-only + 最新值视图,读写走信号,权限并入 manifest 白名单(回答开放问题 4) | ch9 | **P0/P1**(书中"撤掉通道成功率归零") |
| 帧间消息 envelope(sender/target/type/payload),帧可发布/订阅(status_update、问询) | ch10/ch4 | P1 |
| 并发控制候选(乐观锁版本号、worktree 隔离、read-before-write) | ch10 | P2 |

### E. 流控制 → 内核(K-F)

| 功能 | 来源 | 优先级 |
|---|---|---|
| **中断配对修复**:cancellation 落在 dispatch 中途时合成占位 tool_result,不变量 2 在中断路径同样成立 | ch4 | **P0**(当前设计最硬的洞) |
| `parallel_invoke` 语义补全:first-success 级联终止、幂等结算(只结算一次)、并发上限、批内故障隔离 | ch10/ch5 | P1 |
| 后台帧 `spawn`(父帧不挂起、子帧独立记账、join 退化为读终态)+"未确认完成不得宣称 done"校验 hook | ch9 | P1(依赖 D) |
| `pre:frame.pop` 同步信号(弹栈前可否决,proposer-reviewer 落点) | ch0/ch10 | P1 |
| 恢复环路熔断:每条调 LLM 的恢复路径(summarize/修复循环/fallback)独立连败计数 + 熔断;原则"内核错误路径不调 LLM";递归深度计数 | ch5/ch2 | **P0**(死亡螺旋防御) |
| runner 可选消费 `stream()` + 异步工具挂起(结果后续回填观察)+ `post:llm.chunk` 信号 | ch9/ch5 | P2 |
| 外部事件唤醒入口(事件源 → 信号 → run) | ch4/ch0 | P3(开放问题) |

### F. 权限控制 → 内核(K-P)

| 功能 | 来源 | 优先级 |
|---|---|---|
| `ToolContext.principal`(caller identity,v1 恒 None)与 credentials 作用域注入 | ch3/ch4 | **P0**(契约字段,后补即破坏性变更) |
| sidecar 输入最小化:安全类 sidecar 默认只收结构化调用数据,不收自由文本 | ch4 | P0(契约约束) |
| 运行期注册产物的信任管线:默认不信任 = 强制沙箱 + CodeScanner + 注册信号可被 HumanApproval 拦截 | ch8 | P1 |
| MCP 供应链清单(版本锁定、namespace 隔离、描述按不可信输入审查) | ch4 | P2(随 MCP 适配器) |

### G. 契约字段丰富 → 各子系统 `api/v1`

| 功能 | 来源 | 优先级 |
|---|---|---|
| `Usage` 细分:cache_read/cache_write/thinking tokens、TTFT/总延迟;`ProviderCaps` 对应能力位 | ch6/ch2 | **P0**(成本归因的数据前提) |
| 消息模型 reasoning 字段 round-trip(含签名 thinking block 原样回传) | ch2 | **P0**(部分模型不回传即报错) |
| `ToolSpec` 预留字段:examples、cacheable、confirm(two-phase)、concurrency_safe、cost、depends_on、conflicts_with、untrusted_source | ch4/ch5/ch7 | **P0**(先入契约,检查逻辑可后实现) |
| 工具结果形状:结构化错误四层 + `retryable` 字段、调用计数注释、source tagging 包裹、head+tail preview | ch2/ch4/ch5 | P1(其中结果形状属 P0 契约) |
| manifest `verifier:` 槽位(code 技能作语义验证器,Logic Kernel 确定性执行) | ch10 | P1 |
| `ModelRouter` 扩展点(v1 静态 prefer 为默认实现) | ch6 | P2 |
| `ProviderCaps` 预留 logprobs、实时能力位、Provider 内部事件透传字段 | ch7/ch9 | P3 |
| RunConfig seed/temperature 钉死 | ch6 | P2 |

### H. 工具与内置工具集 → Tool Registry

| 功能 | 来源 | 优先级 |
|---|---|---|
| `fs_edit`(old_string→new_string 唯一匹配)+ `fs_read` 行区间/行号前缀 | ch5/ch9 | P1 |
| `shell_exec` 持久会话(run 作用域资源句柄经 ToolContext 注入,哨兵判完成) | ch5/ch9 | P1 |
| `ask_user`/`notify_user`(User Communication 类,宿主注入回调) | ch0/ch4 | P2 |
| `blob_get` offset/limit、blob ref 采用 `blob://` URI | ch4/ch3 | P2 |
| execute-validate-feedback hook(写文件后自动 linter,结果并入返回值) | ch4/ch5 | P2 |
| 归一化流水线 enrichment(见 G 类结果形状) | ch2/ch3/ch4 | P1 |

### I. 自我进化供给侧 → Skill/Tool Registry 写入路径

| 功能 | 来源 | 优先级 |
|---|---|---|
| `register(artifact, provenance)` 写入契约(两个 Registry 同形) | ch8 | P1(契约形状先定,v1 最小实现 = fs_write + reload) |
| 入库前验证门(重放 + evaluator 确认才 publish;"无验证门的自我改进循环必然腐坏") | ch8 | P2 |
| 蒸馏 sidecar(订阅 run.finished,触发条件满足时蒸馏经验写入 Memory) | ch8 | P3(依赖 C) |
| description lint(路由规则/负例/"Use when") | ch2/ch4 | P2 |
| 可见集膨胀后的检索层(skill discovery) | ch8 | P3(开放问题) |

### J. 应用层/文档(不进内核)

judge/user-simulator 评测三件套(ch6)、Best-of-N 汇合模式(ch7)、睡眠整理示例技能(ch8)、轨迹编译离线工具(ch8)、computer-use 参考工具集(ch9)、忠诚度准则与 handoff package 编写指南(ch5/ch10)、MCP/A2A 适配器(ch4/ch10)、全双工实时模型接入(仅 Provider,ch9)、感知门模式文档(ch9)、统计显著性 eval harness(ch6)。

---

## 3. 内核瘦身:保留集与让渡集

```
保留(微内核)                          让渡(子系统化)
─────────────────────────────        ─────────────────────────────
流控制:                                build_request 组装 ──────→ Context(§5)
  loop 推进 / 帧栈操作                   trace 落盘 / 导出 / 快照 ─→ Telemetry(§4.1)
  dispatch 仲裁点(tool/skill/logic)    持久记忆 ────────────────→ Memory(§4.2)
  safe-point 取消 / 级联终止             共享状态存储 ────────────→ Blackboard(§4.3)
  并发原语(spawn/join/parallel)
  预算中止判决 / 恢复熔断计数
权限控制:
  三层交集检查 / trust 路由
  verdict 仲裁(fail-closed、优先级)
  principal 与凭证作用域
IPC:信号总线 / RunControl / 帧间消息寻址与权限仲裁
```

边界细则:

- **Sidecar 与内核**:策略(审什么)在 sidecar 子系统,仲裁(fail-closed、优先级、短路、2s 超时)在内核——seccomp 的策略/机制分离。
- **Blackboard 与内核**:帧发消息须经内核寻址与权限检查(目标帧存在、manifest 允许),存储在 Blackboard——与 tool dispatch 同构:仲裁在内核,执行在子系统。
- **记账**:计数器留在帧对象(预算判决要读),汇聚/导出归 Telemetry。
- runner 伪码变化仅一行:步骤 3 由 `kernel.build_request(frame)` 改为 `kernel.context.build(frame)`。

---

## 4. 新增子系统

### 4.1 Telemetry(可观测与轨迹)

- **职责**:信号流的可持久化汇聚点。事件日志 = WAL;schema 版本化;导出管线(JSONL baseline / OTLP / 训练就绪轨迹);检查点快照;PII 脱敏;过程指标汇聚。
- **隐喻**:journald / auditd + 飞行记录仪。
- **为什么不是 sidecar**(决定性理由):sidecar 语义是"ASYNC 异常只记日志,永不拖垮 run"(fire-and-forget);而 WAL/检查点要求**保证落盘**的 durability 语义,且 M5 检查点恢复把它放在可靠性关键路径上。它有独立契约(writer 格式、exporter、snapshot),五章(ch0/3/6/7/10)独立指向。
- **契约要点**:`TelemetrySink(Protocol)`:`record(signal) -> None`(同步落盘队列,内核不等待 IO)、`exporters`(JSONL baseline;OTLP;RL-trajectory)、`snapshot(run_id) -> Checkpoint`。TraceRecorder 从 sidecar 表中移除,降级为 JSONL exporter。
- **Discipline**:golden-file 测试集标注"评估专用"(ch7 训练/评估隔离原则)。

### 4.2 Memory(记忆与知识)

- **职责**:跨 run 持久记忆与知识的**契约层**——读(检索即工具,出存储前按 principal 权限过滤)、写(离线 extract–compare–decide,或运行期经验追加)、常驻(事实经 pinned 注入)、治理(source tagging 强制、通道隔离、可驱逐、新鲜度)。
- **隐喻**:文件系统 / 持久存储。
- **与 §14 非目标的关系**:非目标改述为"内核不实现检索算法(embedding/ANN/RAPTOR 等),但 `api/v1` 定义 `MemoryService` 契约"。附 `LocalFileMemoryService` baseline(Markdown 文件 + 检索工具,ch5 论证的 MEMORY.md 路线),重实现归第三方 entry point(`agent_os.services`)。
- **安全底线随契约内建**(ch8 经验投毒三层):写入前审查 + source tagging;注入以"参考资料"角色、显式无指令效力、按帧声明作用域;可溯源、可驱逐。事后补强远不如契约内建便宜。
- **VFS 四区域语义**(ch10)作为其存储治理模型:私有 scratchpad(现有工作目录)/ 共享 workspace(乐观锁/worktree)/ 外部挂载 / 内置只读(技能包)。

### 4.3 Blackboard(共享状态与帧间通信)

- **职责**:run 作用域内的帧间共享状态与异步消息。StatusBoard KV(append-only + 最新值视图)+ 消息 envelope 路由;读写走信号可审计;权限并入 manifest 白名单——**正面回答 §14 开放问题 4**。
- **隐喻**:共享内存 + 消息队列。
- **与信号总线的边界**:信号 = 内核生命周期事件(广播、生命周期语义);黑板消息 = 帧↔帧寻址通信 + KV 状态(应用语义)。前者是 kernel IPC,后者是子系统,内核只做寻址与权限仲裁。
- **与 Memory 的边界**:Blackboard 易失、run 作用域(帧树内通信);Memory 持久、跨 run。后台帧(ch9 P0)的滚动状态通道就是 Blackboard 的首个承重场景。
- **契约要点**:`Blackboard(Protocol)`:`publish(envelope)` / `subscribe(frame_id, pattern)` / `put(ns, key, value, cas_version)` / `get(ns, key)`;namespace 按 frame_id 作用域;CAS 乐观锁与 read-before-write 内建于 `put`。

---

## 5. 扩容改名:Context Compression → Context(上下文子系统)

- **职责** = 组装(build_request:指令 + 帧上下文 + 可见 schema + Status Bar 注入 + 来源标注)**+** 压缩(原有全部)**+** 前缀稳定性维护。
- **为什么组装与压缩必须一家管**:压缩破坏前缀缓存(ch2:替换点后缓存失效),组装维护前缀稳定——两者是同一枚硬币的两面,分属两个子系统必然口径打架(ch2 最大教训:缓存一致性是前置架构约束)。微内核下内核不该做 prompt 组装(那是策略),而它又不能和压缩分家,故合并为一个 Context 子系统。
- **内核接口**:`context.build(frame) -> ChatRequest 草稿`、`context.maintain(frame) -> None`(原 maybe_compress + 状态注入)。runner 步骤 2/3 合并为对 Context 的两次调用。

---

## 6. 既有子系统接口加强(摘要)

- **Providers**:usage 细分字段与 reasoning round-trip(**P0 契约**);stream idle watchdog(P1,静默停滞是最危险的流式失败);fallback 跨 provider 消息归一化(P2);ModelRouter 扩展点(P2);多模态 token 口径进统一估算器(P1)。
- **Tool Registry**:ToolSpec 预留字段批(**P0 契约先入**);归一化 enrichment(结构化错误 + retryable、计数注释、source tagging、head+tail);`fs_edit`/持久 shell(P1);凭证作用域注入(随 K-P)。
- **Skill Registry**:`register()` 写入契约(P1)+ 验证门(P2);`verifier:` 槽位(P1);description lint(P2)。
- **Sidecars**:输入最小化契约约束(P0);Veto 理由回写为错误观察(P1);内置表 +StallDetector/MetricsCollector(P2);HumanApproval 超时兜底与模型审批工程注记(P2)。
- **Logic Kernel**:沙箱回调通道从开放问题 5 **升级为设计方向**(ch4 code orchestration 给出两个数量级 token 收益的论据;回调全部回到内核分发路径的约束不变);网络出口目的级白名单(P2)。

## 7. v1 契约冻结清单(现在不进、以后就是破坏性变更)

`Usage` 细分字段;reasoning round-trip;`ToolContext.principal` + credentials;`ToolSpec` 全部预留字段;结构化工具结果形状(含 retryable);manifest `verifier` 槽位;`register()` 签名;消息 envelope;`MemoryService` / `Blackboard` / `TelemetrySink` 三个 Protocol 骨架;`pre:frame.pop` 信号名;`compression: "off"` 档。

## 8. 里程碑修订

| 里程碑 | 调整 |
|---|---|
| M0–M2 | 不变 |
| M3 | 更名 Context 子系统:rolling window + **组装职责与前缀不变量**(golden-file 断言相邻步前缀 diff 为空) |
| M4 | 不变(sidecar 四件套);TraceRecorder 条目移出,为 Telemetry 让位 |
| M5 | 重排:**Telemetry(WAL + OTLP + 检查点)**、**中断配对修复 + 恢复熔断**(P0 洞)、stream idle watchdog、`fs_edit`、**Blackboard 契约 + spawn 后台帧**、PythonSandboxLogicKernel + python_exec + CodeScanner(既定) |
| M6(新增,可选) | Memory 契约 + LocalFile baseline、register 写入路径 + 验证门、沙箱回调通道、narrate/多模态压缩、spill/summarize 高级策略 |
| 契约冻结点 | v1.0 前过 §7 清单,字段全进 `api/v1`(可无实现) |

## 9. 隐喻表追加

| 编程 / OS 概念 | Agent OS 对应 |
|---|---|
| 微内核(调度 + 权限 + IPC) | **Kernel Runner(重新定位)**:只做流控制与权限控制 |
| journald / auditd / 飞行记录仪 | **Telemetry**:信号落盘为 WAL,导出与检查点 |
| 文件系统 / 持久存储 | **Memory**:跨 run 记忆与知识,权限过滤检索 |
| 共享内存 / 消息队列 | **Blackboard**:run 内帧间状态与消息,并发控制 |

另注一处分歧备忘(ch10):书把 LLM 比作 CPU(分时、无状态),我们把 CPU/ALU 给了 Logic Kernel、LLM 放 Provider(设备驱动)——各自服务不同论述目标,§1 可加一行注记避免读者困惑。
