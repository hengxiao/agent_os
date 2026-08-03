# Agent OS 开发状态盘点

> 基线:git `bd0ceff`(8 个 commit),`121 tests 全绿`,ruff 干净
> 口径:以代码事实为准(剩余 `NotImplementedError` 位置 + 各子系统实现深度),对照 ../DESIGN.md v0.4 与 §16 里程碑

## 一、总览

- **里程碑**:M0–M5 完成(其中 M5 拆为 a/b/c 三个子提交);**M6(演化)未开始**。
- **代码量**:约 5,700 行 src(api/v1 契约 1,309 + 内核与子系统 4,400)+ 约 3,000 行测试。
- **测试**:121 个 = 60 契约冒烟 + 61 里程碑锚点(fib 5、M1 13、M2 8、M3 10、M4 8、M5a 3、M5b 7、M5c 7)。
- **开发方式**:全程"锚点测试先行 → 子代理实现 → 主 agent 复核 → 按里程碑提交"。

## 二、子系统逐个盘点

图例:✅ 主体完成 / 🟡 部分完成 / ⬜ 未开始

### 1. Kernel(微内核)— ✅ ~90%

完成:run/run_frame loop;FrameStack(深度兜底);信号总线(通配订阅);**三检查点 verdict 仲裁**(pre:step / pre:tool.call / pre:frame.pop);veto 理由回写;**中断占位配对**(P0 洞已修);outputs 校验连败熔断;记账与预算中止;LogicKernelRouter;KernelLogicContext(invoke / call_tool / spawn / wait);RunControl(stop/pause/inject/force_compress/tree/usage);**checkpoint/resume 断电恢复**;spawn 后台帧;StatusBoard 写入。

未开发:

- `parallel_invoke` fork/join(first-success、幂等结算、并发上限、批内故障隔离)——§3.4 只实现了 spawn;
- runner 消费 `stream()`(`post:llm.chunk` 信号链)与异步工具挂起;
- 恢复熔断通用化(目前只有 outputs 校验一条路径有连败计数;summarize/fallback 的独立熔断未做);
- spawn 的"未确认完成不得宣称 done"校验 hook;
- 外部事件唤醒入口(§17 开放问题 4);
- 两个遗留死 stub:`kernel/dispatch.py`、`kernel/run.py` 中的 M0/M1 占位(实现已长在 runner 里,可清理)。

### 2. Providers — ✅ ~75%

完成:ProviderManager(前缀路由、指数退避 respect retry_after、每 provider 令牌桶、**fallback 链 + 切换归一化**、**stream idle watchdog**);OpenAICompatibleProvider(消息/tools/usage/reasoning 映射、429/401/400(context length)/5xx/超时错误映射);MockProvider(脚本化 chat/stream、请求录制、故障注入)。

未开发:Anthropic 原生适配器;ModelRouter 动态路由实现;OpenAI SSE 真实 stream;API 级微压缩(服务端 context editing);logprobs;多模态 token 精确口径;真实 API 夜间冒烟。

### 3. Tool Registry — ✅ ~80%

完成:LocalPythonToolRegistry(decorator 推导、ctx 注入约定、ToolResult 直通);全量分发流水线(schema 校验、三层权限交集、超时、结构化错误四层);6 个内置工具(fs_read 行号+offset/fs_write/fs_edit/shell_exec/http_fetch/python_exec);InMemoryBlobStore。

未开发:FileBlobStore 与 `blob_get`;`ask_user`/`notify_user`;MCP 适配器;`confirm` 两阶段语义;**credentials 凭证注入**(契约字段在,未接线);归一化 enrichment 三件套(head+tail preview、调用计数注释、untrusted_source 的 source tagging 包裹——字段已标,包裹未做);ToolSpec 预留字段语义化(examples 注入、cost/depends_on/conflicts_with 检查)。

### 4. Skill Registry — ✅ ~70%

完成:LocalFileSkillRegistry(单 YAML 加载、manifest 校验、拓扑排序循环依赖报错、自引用合法、构造即加载、mtime reload、make_frame/visible_to、description lint 告警);code 技能惰性 import;装配期权限闸门(§6.1)。

未开发:`register()` 运行期写入路径(M6)与入库前验证门;DirectorySkillSource(目录包形态);版本约束求解;文件监听自动热重载;可见集膨胀后的语义检索层。

### 5. Context(上下文)— 🟡 ~55%

完成:TokenEstimator(char/4 + 校准系数接口);RollingWindowCompressor(原子组结构、整组驱逐、保留末组硬截断、`[COMPRESSED]` 标记);ContextManager(build 组装 + **状态注入**(ephemeral、预算 hint)+ maintain 触发压缩 + pre/post:compress 信号 + off 短路 + force_compress);前缀稳定性(工具序固定 + golden 断言);不变量 hypothesis 测试。

未开发(§7.2 策略表中只实现了 truncate 一档):**spill**(blob ref/preview/替换串冻结)、**summarize**(context-aware、保留契约、连败熔断)、**narrate**(多模态旁白)、**hierarchical** 责任链组合;contextualized preview;压缩缓存失效核算;多模态 token 口径;驱逐价值序。

### 6. Sidecars — ✅ ~80%

完成:SidecarSupervisor(SYNC priority 序 + 2s 超时 + fail-closed;ASYNC 派发 + close);5 个内置(BudgetGuard / LoopDetector / StallDetector / ToolGuard / CodeScanner);reviewer(proposer-reviewer)经 pre:frame.pop 落地;纠偏消息带操作指令。

未开发:HumanApproval(骨架);MetricsCollector;蒸馏 sidecar(M6);LLM 驱动 sidecar 的配套设施(rejection circuit breaker、不同家族审批);输入最小化的强制过滤(现靠 payload 结构化自律,未做强制裁剪)。

### 7. Logic Kernel — ✅ ~70%

完成:InProcessLogicKernel(模块路径入口、stdout 捕获、序列化检查、硬失败透传);PythonSandboxLogicKernel(子进程 + rlimits + 驱动脚本执行 code 技能、`-I` 下 PYTHONPATH 处理);信任选路(manifest/force_sandbox)。

未开发:**网络隔离**(unshare/nsjail——目前断网靠"不装网络工具"而非系统级阻断,这是已知最大的安全缺口);**沙箱回调通道**(JSON-RPC 代理 LogicContext,设计方向已定);merge_limits;mem_peak 记账;JS/Wasm/Remote 后端。

### 8. Telemetry — 🟡 ~50%

完成:JsonlTelemetrySink(版本头 WAL、按 run_id 分文件、总线特权订阅);内核 checkpoint/resume(帧含完整上下文序列化、按深度结算未配对调用、恢复只补未完成部分)。

未开发:`sink.snapshot()`;OTLP/OpenInference 导出;训练就绪导出(压缩前原始报文 + provenance);PII 脱敏 hook;MetricsCollector。

### 9. Memory — ⬜ ~5%

仅 32 行骨架(全部 M6 stub)。契约在 api/v1(MemoryService/MemoryEntry),builder 仍封禁。未开发:LocalFileMemoryService、检索工具、source tagging、通道隔离、新鲜度治理。

### 10. Blackboard — ✅ ~70%

完成:LocalBlackboard(CAS 乐观锁、publish/subscribe、读写信号);StatusBoard(runner 每步/弹栈写入);spawn 后台帧配套;manifest.permissions.blackboard 命名空间仲裁代理。

未开发:run 作用域隔离强化;worktree 式隔离;read-before-write 强制;跨 run 持久化。

### 11. Runtime — ✅ ~60%

完成:KernelBuilder 全链式装配(仅 memory 仍封禁)、缺省补全、装配期权限闸门。

未开发:`runtime/config.py` TOML 配置加载(M0 stub 至今)。

## 三、契约层与设计的偏差(待回写)

开发中实际发生的 3 处 api/v1 增补,../DESIGN.md 尚未同步:`SkillFrame.call_id`、`LogicContext.spawn/wait/board`、`BlackboardConflict`。下次更新设计文档时回写(§2.3、§9.3、§12.1)。

## 四、未开发功能汇总(按优先级)

**P0(安全/契约相关)**:沙箱系统级网络隔离;credentials 凭证注入;source tagging 包裹;HumanApproval。

**P1(主线里程碑 M6)**:Memory 子系统(LocalFile baseline + 契约接线);`register()` 写入路径 + 入库前验证门;spill/summarize 压缩策略(hierarchical 链补全);parallel_invoke。

**P2(增量)**:OTLP 导出;runner 消费 stream;蒸馏 sidecar;TOML 配置加载;blob_get / ask_user / notify_user;恢复熔断通用化;"不说 done" hook;死 stub 清理。

**P3(开放问题)**:事件唤醒入口;语义检索可见层;FrameContext 继承/克隆;帧树粒度信用分配。

## 五、建议的下一步

两个方向可选:

1. **补安全短板**(工作量小、风险敞口大):沙箱网络隔离(unshare -n)+ credentials 注入 + source tagging 包裹 + HumanApproval——把"权限控制"半边天补齐;
2. **进 M6 主线**(设计节奏):Memory + register() + 验证门——打通自我进化供给侧,是 ../DESIGN.md 规划的最后一个里程碑。
