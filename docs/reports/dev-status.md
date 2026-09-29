# Agent OS 开发状态盘点

> 基线:git `bd0ceff`(8 个 commit),`121 tests 全绿`,ruff 干净
> 口径:以代码事实为准(剩余 `NotImplementedError` 位置 + 各子系统实现深度),对照 ../DESIGN.md v0.4 与 §16 里程碑

> ---
> ⚠️ **过期警告(2026-08-24 复核)**:上文基线 `bd0ceff` 仅 8 个 commit,本文已**严重过时**,请当作历史快照读。
> 当前基线:HEAD `36f0587`(206 个 commit,较原文基线 +198);测试 **1036 收集 = 994 passed + 10 条件 skip + 32 xfailed,0 失败**;
> 代码量约 src 2.4 万行 + tests 2.1 万行 + std 4.3k 行(不含 `__pycache__`/`.venv-ui`)。
> 里程碑总判断仍成立:M0–M5 完成,**M6(演化)未开始**(`memory/local_file.py` 三方法、`skills/local_file.py:293 register()` 仍全 stub)。
> 基线后新增的主要能力:Claude/Kimi 原生适配器、ask_supervisor 完整闭环、内核调试器(`kernel/debug.py`)、升权三档推导(`api/v1/escalation.py`)、
> Skill Lab 全链(草稿→五关闸门→原子发布)、std 标准技能库 11 域、CLI 全套子命令 + debug REPL、Web Runner / Web Platform、
> `agent-os.toml` 配置装配、Docker 容器沙箱、`blob_get`、todo/progress 工具与检索/记忆 std 技能包。
> 下文中**已关闭的条目以「复核 2026-08-24」就地标注**;未标注的条目经抽查仍然成立,历史结论本身未改写。
> ---
> ⚠️ **复核 2026-08-31(P0 四项已关闭)**:测试 **1098 收集 = 1056 passed + 10 条件 skip + 32 xfailed,0 失败**。
> ① **credentials 凭证注入(WS1)**——`ToolSpec.credentials` 声明键(`api/v1/tools.py:126`)+ `[credentials]` 配置段
> `{ env = "VAR_NAME" }` 间接引用(`runtime/config.py` `_credentials`/`CredentialScope`,不落盘明文)+
> `LocalPythonToolRegistry.bind_credentials(resolver)` 装配,dispatch 按声明键每次现解析(env 缺席键不出现,
> 未声明/未 bind → 空 dict;`tools/local_registry.py:300-308`),凭证不进 SkillFrame/checkpoint/trace;
> ② **confirm 两阶段闸门(WS2)**——内核 tool-confirm(`kernel/runner.py:_dispatch_call`,pre:tool.call 仲裁后、
> dispatch 前;`spec.confirm=True` 或 HumanApproval 策略在场且 EXEC 档 → `supervisor.ask` `kind="tool-confirm"`;
> reversible 三选/irreversible 两选,approve-run 复用 `Run.grants`,deny → PERMISSION_DENIED,无 supervisor
> fail-closed,pending 随 checkpoint、resume 重问;偏差:内核批准即 confirmation token,不做字面 dry run 二次调用);
> ③ **HumanApproval 下沉(WS2)**——`sidecars/builtins.py:237-257` `on_signal` 弃权,类保留为策略载体,
> `[sidecars] human_approval = true | {timeout, on_timeout}` 配置生效;
> ④ **D2 数据层 authZ**——`[data]` 配置段 + `[data.principals]` per-subject 白名单第二判据(`allow(whitelist=)`)+
> net/db 域判定 + `data.access.denied/granted` 审计信号(SIGNAL_NAMES 33 个)+ 判据回写 `ctx.credentials["_authz"]`
> + D3-lite `[web.tokens]` 多用户映射(Bearer 命中 → `Principal(issuer="api-token")`);仍未做:派生链最弱一环、
> EscalationRequest 数据面展示(归 E3)。(**2026-09-28 更新**:派生链最弱一环与升权审计面板
> 均已关闭,见头部最新复核块;**2026-09-29 更新**:留尾 EscalationRequest 数据面展示
> (确认卡片 `domains`/`sensitive`)亦已关闭,见下文 2026-09-28 E3/D3 复核块尾注。)**行为变化**:无 supervisor 的裸 run 调 `system.file.delete` 等闸门工具
> 现在 fail-closed 拒绝。另(WS4/WS5):runner 工具循环补 except Exception 兜底(INTERNAL 错误观察,run 存活);
> 编排路径 `float(timeout)` 解析失败返回 INVALID_ARGS;`[providers.kimi]`/`[providers.anthropic]` 子键生效。
> ---
> ⚠️ **复核 2026-09-27(M6 两项已关闭)**:测试 **1297 收集 = 1250 passed + 10 条件 skip + 37 xfailed,0 失败**。
> ① **Memory 子系统(WS-A/WS-B)**——`LocalFileMemoryService` 三方法全实现(`memory/local_file.py`:
> 每条目一 Markdown `<root>/YYYYMMDD-<slug>-<shortid>.md` + 手写 frontmatter(tags/source/created_at/
> freshness/trust/provenance);evict 删文件 + `.evictions.log` 审计;search = principal 过滤 →
> freshness(ttl_s/valid_until)→ BM25 → k 截断,损坏条目容错);BM25/RRF 唯一实现提取至 `memory/rank.py`
> (std `transform.py` 检索 handler 改为委托);工具面 `system.memory.search`(READ)/`system.memory.write`
> (WRITE)常驻 `with_builtins`,MemoryService 经 `bind_memory` 装配(未装配调 NOT_FOUND),write 自动打
> source/provenance(模型不可伪造);builder 的 M0 封禁移除,`[memory] dir = "./memory"` 配置段接线
> (`runtime/config.py`,缺段完全不 bind)。与 `std/memory.yaml` 边界不变:std 四件套 = 记忆的管线与纪律,
> MemoryService = 存储与治理;
> ② **`register()` 运行期写入路径(WS-C)**——`LocalFileSkillRegistry.register()`(`skills/local_file.py:314-484`):
> 纯函数闸门(命名正则 + G5 注入卫生 fail)→ 归一化(version 缺省 `default_version` bump;code 技能 handler 落
> `generated_handlers/<mod>.py`,logic 无条件钳 sandbox)→ 可选 validate_draft G1-G3(strict_refs,注入 tools
> 才跑)→ `pre:skill.register` 可 Veto → `package._atomic_write` 先证后换 → provenance `register.jsonl` →
> `post:skill.register`;信号目录 33→**35**(`api/v1/signals.py:79-80`);消费面 `system.skill.register` 工具
> (WRITE,confirm=True,`data_domains=["skills.*"]`)经内核 tool-confirm 闸门兑现"注册动作可被 HumanApproval
> 拦截"(DESIGN §6.2),目标 skills registry 经 `bind_skills` 注入(无 register 能力报 NOT_FOUND)。
> **M6 余项仍开口**:沙箱回调通道高级形态、spill/summarize/narrate 压缩策略、蒸馏 sidecar;
> register() 留尾:semver ^/~ 求解、DirectorySkillSource 写路径、文件监听热重载、
> 完整重放 + evaluator 验证门。(**2026-09-28 更新**:蒸馏 sidecar 与 context 注入槽(Memory 检索结果的
> 组装侧,manifest `context_policy.recall` opt-in)均已关闭;压缩链 spill/summarize/hierarchical
> 与沙箱回调通道 spawn/wait/parallel 亦已于本日关闭,见头部最新复核块。)(**2026-09-29 更新**:
> register() 留尾四件亦已全部关闭——semver 约束准入(只准入、不做多版本求解,裁决)、目录形态写
> (目标恒 `<dir>/registered.yaml`)、`start_watching` 热重载 watcher、smoke hook 验证门;仍开口:
> 默认重放 + evaluator 实现(挂点已就位)与 watch 线程 close 钩子。见头部最新复核块。)
> ---
> ⚠️ **复核 2026-09-27(并发三原语三项已关闭)**:测试 **1320 收集 = 1273 passed + 10 条件 skip + 37 xfailed,0 失败**。
> ① **`parallel_invoke` fork/join(WS3,§3.4 第三原语)**——`Kernel.parallel_invoke`(`kernel/runner.py:1482-1763`,
> 辅助 `_parallel_branch`/`_parallel_unsafe_tool`/`_parallel_cancelled`/`_parallel_cancel_all` :1765-1831):
> 起批前串行预检逐分支复用 spawn 前置段(spawn_frame 拆 `_spawn_whitelist_check`/`_spawn_gate`/`_spawn_register`,
> :1369-1434);all_settled 结构化 gather 永不上抛,depends_on 前置失败标 cancelled 不启动;first_success
> done-flag 只赢一次 + 级联取消败方 + `settle_timeout` 等 ack + 幂等结算(usage 不可回滚);硬失败
> (RunAborted/BudgetExceeded/MaxDepthExceeded)不折叠炸 run;`max_concurrency` Semaphore;`concurrency_safe`
> 首个强制消费(code 分支含未声明工具 → 串行降级占满额度,fail-safe;prompt 分支豁免);`LogicContext.parallel()`
> 委托(`kernel/logic_context.py:149-156` + `api/v1/logic.py:222`);checkpoint/resume 与 spawn 同形(在跑批
> 不恢复,父帧重跑重发,调用方幂等);
> ② **子树级联取消(WS1)**——`Kernel.cancel_subtree` + `_collect_subtree` DFS(`kernel/runner.py:1837-1893`),
> `RunControl.cancel_frame`(`kernel/control.py:63-71` + `api/v1/control.py:31`);`SubtreeCancelled`
> (`kernel/errors.py:34`)独立于 RunAborted——子树终态不杀 run,后台帧经 wait_frame 原样上抛,`_invoke_skill`
> 边界折叠为 interrupted 错误观察;`_spawned` 重构为 run 分桶 `dict[run_id, dict[frame_id, (parent_id, task)]]`
> (`runner.py:233`,顺带修复 `_release_run` 跨 run 误杀);帧级 stop 标志 `_frame_stop_flags`(`runner.py:226`,
> 仅 prompt 帧在 pre:step safe point 消费,:436-440);边界:point-in-time 收集、帧级标志不持久化;
> ③ **子树记账读视图(WS2)**——`Kernel.subtree_usage`(`kernel/runner.py:1951-1988`,九字段求和,total_ms
> 为子树资源占用累计非墙钟),`RunControl.get_subtree_usage`(`kernel/control.py:97-103` +
> `api/v1/control.py:37`),rca usage_panel 每帧行加 `subtree` 字段(`host/web/rca.py:122-167`);
> 明确不做:组合子 budget 强制(留 budget 参数工具面)、SkillLimits.max_steps 执行点。
> (**2026-09-28 更新**:两条均已关闭——manifest `limits.max_steps`/`limits.max_cost` 帧/子树级
> 内核强制落地,组合子作为 code 技能经 manifest 声明即受内核硬约束;race_first 的 budget 参数
> 软闸保留,与内核强制正交。见头部最新复核块。)
> **§3.4 三原语至此齐备**;spawn 的"不说 done"校验 hook 仍未实现,保持开口。
> ---
> ⚠️ **复核 2026-09-27(stub 清零 + 真实流式)**:测试 **1385 收集 = 1336 passed + 10 条件 skip + 39 xfailed,0 失败**。
> **清理批(死 stub 清零)**:`kernel/dispatch.py`(Dispatcher)整文件删除;`kernel/run.py` 的 M0 stub
> `check_control_flags` 移除(文件仅余 Run 句柄);`tools/builtins.py` 裸 `python_exec` 死函数删除
> (可用面为 `python_exec_tool` 工厂,canonical 名 `system.python.exec`)。下文 §1/§2/§3/§7/§8 相应
> "未开发"条目据此关闭,正文保留作历史快照。
> ① **ask_user/notify_user(M1 尾巴)**——`system.user.ask`/`system.user.notify`(WRITE 档)实填并常驻
> `with_builtins`(`tools/builtins.py` + `tools/local_registry.py:716-717`),宿主回调经装配钩子
> `LocalPythonToolRegistry.bind_user_channel(channel)` + `KernelBuilder.user_channel()` 注入
> (未 bind → NOT_FOUND,同 bind_memory 先例);与 ask_supervisor 分工:ask_supervisor = 内核通道
> (pending 落盘/resume 重问),ask_user = 工具面宿主回调(无 pending 语义);CLI 接线留 TODO
> (`host/cli/main.py:89-90`,`_cli_supervisor` 旁);
> ② **FileBlobStore(M3)**——`tools/blob.py`:`<root>/<run_id>/<sha256>` 内容寻址落盘,run_id/sha
> 白名单校验防目录逃逸;`[blob] dir` 配置段接线(`runtime/config.py:569-578`,缺段 = 内存版);
> ③ **merge_limits(M5)**——`logic/limits.py:32`:两级逐字段取紧(None = 未设取另一级),三级经
> 链式调用,返回新实例(尚无调用点,三级消费侧留接线);
> ④ **snapshot/JsonlExporter(M5)**——`JsonlTelemetrySink.snapshot()` WAL 视角快照
> (`telemetry/jsonl_exporter.py:112`:`Checkpoint(run_id, seq=已落盘信号数, state={})`,
> 帧树可重建状态留 `kernel/checkpoint.py`,决策写入 docstring),`JsonlExporter.export/close` 实填
> (全 run 信号汇聚单文件,行缓冲 + close 时 fsync,close 幂等);
> ⑤ resume 收尾缺口:`resume_from_checkpoint` finally 补 `_release_run`(`kernel/checkpoint.py:387-394`,
> 与 `run()` 对称);空 `[data]` 段装配期 warning(`runtime/config.py:484-493`,行为不变)。
> **真实流式 `stream()`**——`OpenAICompatibleProvider.stream`(`providers/openai_compatible.py:104`;
> SSE:content/reasoning delta 透传、tool_calls 分片缓冲末帧组装、usage chunk、`[DONE]`、断流 →
> UNAVAILABLE retryable)与 `ClaudeProvider.stream`(`providers/claude.py:100`;Anthropic 事件序列,
> thinking/signature/input_json 按块累计)实填,KimiProvider 继承获得;Mock 配 `stream_scripts` 时
> caps 报 `supports_streaming=True`,脚本支持尾随 ChatChunk(finish_reason/usage);Manager 提交点语义
> (`providers/manager.py:195-258`:首 chunk 前停滞经 `stream_idle_timeout` watchdog 杀流可重试,
> 产出后不重试直接上抛,重试耗尽走 fallback 链)+ `[retry] stream_idle_timeout` 接线
> (`runtime/config.py:590-603`);Runner:`RunConfig.stream` 缺省开(`api/v1/run.py:56`,`[run]`
> 白名单同步),caps 不支持/Mock 无脚本自动回落 chat;`_llm_call`/`_stream_call`
> (`kernel/runner.py:548-632`)逐 chunk 发 `post:llm.chunk`(payload `{model, seq, text}`,仅 ASYNC
> 观察),组装与 chat 同形态;ttft_ms/total_ms 经 `account` 可选参数帧/run 两级入账
> (`runner.py:2022-2061`);cost 折算提取为 `ProviderManager._cost_usage`(流式终 chunk 与 chat
> 同价共用);流中 stop → RunAborted、帧级 → SubtreeCancelled,finally `aclose()` 关流,半截消息不入帧。
> 边界保持:token 级 UI 未做(hub 环形缓冲 2000 挤占留后续)、sidecar 不逐 chunk 仲裁(仅 ASYNC)、
> 流中不重试/resume 整步重跑、web_platform 直达 chat 路径未流式化。
> ---
> ⚠️ **复核 2026-09-27(std 第 5 波组合子落地)**:测试 **1448 收集 = 1399 passed + 10 条件 skip + 39 xfailed,0 失败**。
> std 第 12 个域 `std/combinators.yaml` + `combinators_handlers.py`:`common.task.race_first`(code TRUSTED;
> 内部 `ctx.spawn(race_batch)` 起批 → 批内 `ctx.parallel(mode="first_success")`;budget **软强制**——watchdog
> 50ms 轮询批帧 `ctx.frame_status` 子树 usage,超限 `ctx.cancel` 返回部分结果,内核强制留立项)+ 私有批帧
> `common.task.race_batch`(给 watchdog 单一可寻址批帧)+ `common.task.subagent_cancel`(WRITE 语义,
> ctx.cancel ack)+ `common.task.subagent_status`(READ 语义,ctx.frame_status;未知帧 status=null);
> ctx 面扩张(W5-WS1):`LogicContext.cancel`/`frame_status`(`api/v1/logic.py` 协议、
> `kernel/logic_context.py:160/168`、`kernel/runner.py:2113` `frame_status_payload`、`_syscall_dispatcher`
> cancel/frame_status 路由 `:868-872`、`logic/python_sandbox.py:92-125` `_SyncCtx`/`_AsyncCtx` 桥接)。
> **剩余**:多模态契约、组合子 budget 内核强制、ask_human/set_timer 工具面。
> (**2026-09-28 更新**:组合子 budget 内核强制已关闭——manifest `limits.max_steps`/`max_cost`
> 帧/子树级强制落地,见头部最新复核块;多模态契约与 ask_human/set_timer 工具面仍开口。)
> (**2026-09-28 再更新**:多模态契约已关闭——`Message.parts` additive + provider parts
> 序列化 + narrate 策略落地,见头部最新复核块;ask_human/set_timer 工具面仍开口。)
> (**2026-09-28 三更新**:ask_human/set_timer 工具面已关闭——`system.timer.set` 内核原语 +
> `common.user.ask_human`/`common.task.set_timer` std 形态 + CLI/Web 宿主接线落地,
> 见头部最新复核块。)
> ---
> ⚠️ **复核 2026-09-28(§7.2 高级压缩链落地,WS1+WS2)**:测试 **1522 收集 = 1473 passed + 10 skipped + 39 xfailed,0 失败**。
> `SpillCompressor`(`context/spill.py`,name `"spill"`:非 pinned 超阈值 TOOL 消息移入 blob,
> 冻结 `[SPILLED]` 替换串 + `blob://` ref + head/tail + blob_get 取回提示,不删消息)、
> `SummarizeCompressor`(`context/summarize.py`,name `"summarize"`:被逐区间经 `svc.providers.chat`
> 廉价档摘要为 `[COMPRESSED]` compact note,context-aware + 保留契约,连败熔断 3 退化
> `[COMPRESSED:truncate]`)、`ChainCompressor`(`context/chain.py`:spill → summarize 有序链,
> 逐阶段重估达标短路)全落地;rolling_window 类 name 改 `"truncate"`(行为不变);模式表
> `_MODE_CHAINS`(manifest `compress` 优先,未知模式 ValueError,narrate 不在表中仍开口);
> `KernelServices.providers`/run_id 已接线(不再恒为 None);`pre:compress` 可否决(§7.4 不变量 4);
> §7.1 硬上限尾路径 `ContextOverflowError` 帧失败上抛("临近模型窗口"独立档仍开口);压缩 LLM
> 用量入账帧/run 两级并补发 `post:llm.response`(`"source": "compress"`);`[context]` TOML 段
> (summarize_model/spill_threshold_chars/summarize_breaker/summarize_temperature)与 entry point 组
> `agent_os.compressors` 接线。下文 §5(Context)的"未开发"条目据此关闭(spill/summarize/
> hierarchical;narrate 除外),正文保留作历史快照。
> ---
> ✅ **复核 2026-09-28(沙箱 ctx spawn/wait/parallel 桥接,WS2)**:沙箱 syscall ctx
> (`logic/python_sandbox.py` 的 `_SyncCtx`/`_AsyncCtx`)新增 `spawn`/`wait`/`parallel`
> 三方法,经 runner `_syscall_dispatcher` kind 路由直委托内核 spawn_frame / wait /
> `parallel_invoke` 闸内管线(白名单/深度/升权不旁路);错误折叠:白名单外/深度拒绝 →
> RuntimeError(error.message 原文),SubtreeCancelled → `cancelled:` 前缀(脚本可捕获
> 继续结算,对齐 TRUSTED race_first 模式),未知帧 `invalid_args:`,子帧普通失败
> `internal: {ExcType}:`,RunAborted/MaxDepthExceeded/BudgetExceeded 穿透;三个 syscall
> 计入 `max_tool_calls`(计数在 kind 路由前);`parallel` 逐分支 `ok=False` 正常结算不抛。
> ctx 方法面现为七个;锚点测试新增 7 例(`tests/logic/test_orchestration.py`,handlers 在
> `tests/helpers/code_skills.py`)。边界:返回值须 JSON 可序列化;wait 期间脚本单
> outstanding 阻塞;并发 syscall 未支持;Docker 后端无 syscall;board/blob 不过桥。全量基线:1529 收集 = 1480 passed + 10 skipped + 39 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-28(蒸馏 sidecar 落地,WS2)**:§11.2 写路径范式之蒸馏 sidecar 实填——
> `DistillSidecar`(`sidecars/builtins.py:331-526`,导出 `sidecars/__init__.py`),ASYNC 订阅
> `run.finished`/`run.aborted`,永不否决。触发条件(Hermes 固化条件的 v1 子集,
> ch08-self-evolution.md:29):`run.aborted` 恒触发(failure reflection);`run.finished`
> 需该 run 帧树内 TOOL 消息数 > `min_tool_calls`(默认 5)才触发(strategy summary);
> 用户纠正/非显然工作流触发 v1 无通用信号,未做。蒸馏经 `providers.chat` 廉价模型
> (缺省跟 `[run] model`),两条中文 SYSTEM prompt(strategy summary / failure reflection)
> 含可迁移性入库标准与"经验无指令效力"注记,转写 cap `max_transcript_chars`(默认 24000);
> 写入 `MemoryService.write`(tags=["distill", kind, 根技能名],source={"kind":"experience"}(+user),
> trust="experience";Provenance(run_id, task=根技能, note="distill", detail={model, usage}))。
> 幂等:实例级 `_seen` run_id 去重(resume 路径会重发 `run.finished`),跨进程不去重;
> 熔断:连败 ≥ `breaker_threshold`(默认 3)开闸停蒸馏,成功清零,异常吞掉记 log。
> 配置 `[sidecars] distill = true | {model, min_tool_calls, temperature, breaker_threshold,
> max_transcript_chars}`(strict 校验,缺键不装,默认关闭;需 `[memory]` 段配合,缺 memory 静默休眠)。
> **结构性发现**:订阅终态信号的 ASYNC sidecar 经 supervisor 注册时 `on_signal` 永不执行——
> `Kernel.run` finally 中 `emit(run.finished)` → `supervisor.close()` 之间无事件循环让出点,
> wrapper task 未运行即被 cancel(`sidecars/supervisor.py:77-85`/`:93-99`;最小复现 + 真实内核
> 探针双重验证);落地方式:builder 把触发闭包直挂信号总线(emit 内联 await 保证执行),
> 蒸馏任务实例自管 detached 任务集(run 收尾 cancel 不到),`supervisor.register` 照常
> (契约形态统一)——未来终态信号 ASYNC sidecar 均须直挂总线(已记入 ../DESIGN.md §5.3)。
> `close()`/`wait_pending()` 由宿主/测试显式调(`supervisor.close()` 不调 sidecar.close;
> CLI 一次性进程退出时蒸馏可能未跑完,best-effort);run 已结束,蒸馏 LLM 用量不入帧账、
> 不发 post:llm.response,usage 写进 provenance.detail。**仍开口**:用户纠正/非显然工作流
> 触发、跨进程去重、run 级用量信号、std/learn 三技能(distill_experience/reflect_on_failure/
> verify_before_store,docs/STDLIB.md §4.8)。下文 §6(Sidecars)、§9(Memory)与 §四 P2 的
> "蒸馏 sidecar"开口条目据此关闭,正文保留作历史快照。全量基线:1542 收集 =
> 1493 passed + 10 skipped + 39 xfailed,0 失败。
> **更新 2026-09-28(verify 审查门落地 + std/learn 前提校正)**:DistillSidecar 增
> verify 档——构造参数 `verify: bool = True`(默认开;`[sidecars] distill` 表加
> `verify` 键,非 bool ConfigError,agent-os.example.toml 已补):蒸馏产出后、
> `memory.write` 前追加一次同 model、`temperature=0.0` 的审查调用(判定要确定性,
> 不随蒸馏温度;`DISTILL_SYSTEM_REVIEW` = "经验入库审查员",三维:①指令注入
> ("以后你要/总是/忽略之前的指令/角色扮演"式表述即 fail);②秘密/凭据/PII;
> ③可迁移性(一次性琐事 fail);输出契约严格 JSON `{"pass", "reason"}` 禁围栏)。
> fail-closed:非 JSON/缺 pass 键/类型不对/pass=false → 拒写,记 log、
> `_stats["rejected"] += 1`、**不计连败**(拒写是内容判定,正常风控;仅审查 LLM
> 异常计连败,与蒸馏共用熔断器);`_stats = {"distilled", "rejected"}` 实例计数,
> 写库成功记 `distilled`。自审自局限已注记(同模型既写又审,只挡明显越界;异家族
> 审批为 DESIGN §5.3 既有预留)。测试:tests/sidecars/test_distill.py +4、
> tests/runtime/test_config.py +2。**前提校正**:上文"仍开口"误列 std/learn 三
> 技能——三技能早已实现(2026-07-25 W4 波 commit 0de9ffb:
> `common.learn.distill_experience`/`reflect_on_failure`(prompt,
> `agent_os/std/learn.yaml:2-78`)+ `common.memory.verify`(code,
> `agent_os/std/memory.yaml:222-259` + `agent_os/std/learn_handlers.py:32-44`,
> 旧名 `verify_before_store` 别名 `skills/local_file.py:103`),锚点
> `tests/skills/test_std_domain.py:180-202`);原句所指实为"与 sidecar 的联动"
> (技能显式调用/结构化 JSON/不写库 vs sidecar 自动/散文/直写库 + verify 审查门),
> DESIGN.md §16 结案注、STDLIB.md §4.8 与 STDLIB-CATALOG.md W4-4 已同步更正。
> 全量基线:1557 收集 = 1508 passed + 10 skipped + 39 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-28(帧/子树级预算内核强制,WS2)**:manifest `limits` 块成为执行点——
> `limits.max_cost` 新增 additive 字段(`api/v1/skills.py:84`,默认 None;`skills/manifest.py:26-37,82`
> 解析,非数值/bool 加载期即 SkillLoadError),既有 `limits.max_steps` 启用强制。两字段口径
> 有意不同:`max_cost` 子树求和(该帧+全部后代 cost 合计),`max_steps` 帧自身步数(全仓既有
> manifest 均按帧自身口径声明)。机制:`account()` 改 async(`kernel/runner.py:2156`),run 级
> 检查不动,末尾 `_check_subtree_budgets`(:2203-2282)沿 parent_id 链逐祖先收集预算帧,链上
> 无预算声明快路径零开销,子树求和复用 `_collect_subtree`;写路径不动(父帧 usage 仍不含子帧,
> 读侧聚合不变——与原"account() 沿祖先链累加"批注的偏差:取检查侧等效语义)。分档:预算帧
> 是根 → BudgetExceeded 炸 run;是当前帧 → SubtreeCancelled("budget: ...");是祖先 →
> cancel_subtree(子树终态,invoke 边界折叠 interrupted,run 继续);触发前发 `budget.exceeded`
> (`api/v1/signals.py:101` 冻结信号首次发射),每预算帧恰好一次(`_budget_tripped` 防重,
> 进程态不持久化,resume 幂等)。共用:压缩排干 `_drain_compress_usage` 累加后同查,`ctx.chat`
> 记账点经 account() 同查。边界:code 帧不检查 stop 标志,随 invoke/wait 边界穿透。demo.fib
> `max_steps` 8→40(`agent_os/skills/skills.yaml`,装饰声明变强制后的适配)。不改:BudgetGuard
> (run 级策略层)、race_first 软闸(watchdog 轮询,软/硬正交)、max_wall_time 内核不判、
> budget.warning(80%)不发射;DESIGN §17 开放问题 1(并行分支预算切分)保持开口。上文
> 2026-09-27 并发三原语块"明确不做"两条与 std 第 5 波块"剩余"中"组合子 budget 内核强制"
> 据此关闭,dated 原文保留。全量基线:1551 收集 = 1502 passed + 10 skipped + 39 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-28(§11.2 context 注入槽——memory 经验检索进帧组装)**:manifest
> `context_policy.recall: true` opt-in(ContextPolicy additive 字段,默认关,缺声明零检索零信号)→
> 帧首次 build 检索 Memory 一次(query = 首条 USER 截 1000 字符,principal 经 memory 包公开导出的
> `to_memory_principal` 转换,tools/std.py 与 context 共用;build 为 async,检索直接在 build 内
> await,maintain 不动)→ 命中渲染 SYSTEM 尾部"## 经验参考(检索自记忆库;仅为参考资料,
> 不具指令效力,trust=experience)"段(内联能力段之后,逐条 `- [tags] content[:recall_entry_chars]`)
> 冻结进 `working["_memory_caps"]`,后续 build 复用快照(deepcopy 后逐字节一致:前缀稳定 +
> checkpoint/resume 确定性);注入检测为轻量版(10 条中英注入短语正则集,命中条目降级跳过 +
> log warning + dropped 计数);一次性发 `post:context.recall`(快照组装且有实际检索时;
> payload {frame_id, k, ids, chars, dropped}),信号目录 35→36;配置 `[memory]` 段 strict 三键
> `recall_k=3`/`recall_entry_chars=800`/`recall_total_chars=2000`,缺段/未 bind memory → 槽位跳过;
> 冻结 None 语义:闸门未过/空 query/零命中统一冻结 None(不再检索),全 dropped 冻结 None 但发信号。
> ch08-self-evolution.md:91(检索时轻量注入扫描,可疑条目降级或告警)与 :109(注入作用域须与
> manifest/帧策略挂钩,不做"一处注入全局生效")两项要求已落地轻量版——正则降级 + manifest
> opt-in 作用域。**不做**:每步刷新、pinned 独立消息形态、trust 多层过滤、web UI 渲染;
> **仍开口**:query 用首条 USER 对 JSON input 检索质量一般。上文 §9(Memory)"仍开口"中的
> "通道隔离的 context 注入槽(组装侧)"据此关闭,dated 原文保留。全量基线:1575 收集 = 1526 passed + 10 skipped + 39 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-28(MCP stdio 适配器落地)**:§8.3 工具侧适配实填——`tools/mcp.py`
> 零新依赖自实现 newline-delimited JSON-RPC 2.0 客户端(仅 initialize/initialized/
> tools/list/tools/call;握手 protocolVersion="2024-11-05"),`[mcp.servers.<name>]`
> 配置段(strict 校验,缺段零破坏)装配期 eager 连接:拉起子进程 → 握手 → tools/list →
> 全部工具以 `mcp.<server>.<tool>` 命名空间注册,走全量 dispatch 管线(白名单/ToolGuard/
> confirm/超时/authZ 不变),连接失败 ConfigError 快速失败。IO 模型:装配走
> `connect_and_register_sync`(阻塞 Popen + select 截止读行 + to_thread,loop 无关——
> asyncio 子进程管道绑定创建它的 loop,装配 loop ≠ run loop 会打死连接),async 版保留给
> 测试与 async 嵌入方。进程清理:clients 挂 `registry._mcp_clients` + atexit 兜底,
> `close()` 幂等杀进程组(killpg SIGKILL + wait 收尸),断管重连一次。供应链清单逐条落地:
> description 注入扫描命中整段弃用为占位(正则集提取共用 `injection.py`)、干净描述
> 500 字符截断、`untrusted_source=True`/`concurrency_safe=False` 强制、permission 默认
> READ 逐 server 可升、confirm 逐 server、子进程不继承宿主 env(`{env="VAR"}` 间接引用
> 现读 os.environ)、撞名拒覆盖、非法字符名跳过记 warning。测试基线 = 罐头假服务器
> `tests/helpers/mcp_server.py`。**仍开口**:Streamable HTTP 传输、resources/prompts
> 原语、懒连接、版本锁定、未与真实 MCP server 互测(其中 Streamable HTTP 传输与
> stdio 真实 server 互测已于 2026-09-29 关闭,见头部复核块;新增开口:GET standalone
> SSE/resumability、batching、HTTP 真实 server 互测、OAuth);`agent_os.tools` EP 组仍预留。
> 下文 §3(Tool Registry)"未开发"中的"MCP 适配器"据此关闭,dated 原文保留。
> 全量基线:1595 收集 = 1546 passed + 10 skipped + 39 xfailed,0 失败(两轮复跑确认;
> 时长受并行会话负载影响波动大,不作为口径)。
> ---
> ✅ **复核 2026-09-28(多模态契约 + narrate 压缩策略落地,WS1+WS2)**:§4.1 契约 additive
> 扩展——`ContentPart{type="image", mime, ref=blob://<run_id>/<sha>}` + `Message.parts`
> (缺省 None、位置最后,位置参数兼容;content 仍是纯文本投影,parts 元素生成后不可变),
> checkpoint 仅 parts 非空落键、读容错缺键 None(CHECKPOINT_VERSION 不动);estimator
> 多模态粗估 `IMAGE_PART_TOKENS`=1024/图(精确口径 = provider usage/`token_counter`
> 仍开口);OpenAI/Claude 序列化 parts(blob.get → base64;OpenAI image_url data URI、
> Claude image base64 block;共享件 `providers/parts.py`,`_resolve_parts` async 预解析),
> `supports_vision` 且 blob 在场才走图、否则 content 尾部显式占位(不静默丢),builder
> 装配 blob 到 provider,Mock 加 supports_vision 开关,Claude system 消息 parts 恒占位;
> `NarrateCompressor`(`context/narrate.py`,name `"narrate"`)——被逐区间 parts 消息原地
> 改道,一次廉价 chat 批量生成逐句旁白(JSON 数组,容忍一层围栏),畸形/数量不符退化占位
> `[多模态内容已逐出:{mime} ×N]` + meta narrated="fallback"(不静默丢,计连败),成功则
> content=旁白、parts=None、meta narrated=True,evicted=0、marker `[NARRATED]`,usage
> 收到响应即落账(含畸形轮),连败熔断口径同 summarize;模式表 `"narrate": ["narrate",
> "truncate"]` 新增、`hierarchical` 改 `["spill", "narrate", "summarize"]`(narrate 在
> summarize 前——摘要器拿到旁白而非占位),与 summarize 同模型档([context] 无新键),
> Skill Lab 下拉补 narrate。降级语义如实:无 vision caps 的摘要模型拿到占位文本,旁白质量
> 随之降级。**仍开口**:MCP image 块 → parts 接线与 http_fetch 二进制(生产源,契约已就绪)、
> 多模态 token 精确口径、narrate 质量依赖摘要模型 vision 能力。上文 std 第 5 波块"剩余"
> 的"多模态契约"与下文 §5(Context)复核注的 narrate/多模态 token 粗估口径据此关闭,
> dated 原文保留。全量基线:1626 收集 = 1577 passed + 10 skipped + 39 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-28(E3/D3 余项:升权审计面板 + [ESCALATED] 溯源标记 + D3 派生链最弱一环,WS1 内核/数据面 + WS2 Web)**:
> ① **[ESCALATED] 溯源标记(偏差:结构化 payload 键)**——设计原文为文本前缀
> `[ESCALATED:skill@version]`;TOOL content 是 JSON,前缀破坏 resume 结算
> json.loads,故 invoke 折叠 payload 加 `"escalated": "skill@version"`、
> parallel 分支结算条目同键;spawn 偏差——wait 返回值是裸结果(加键污染
> outputs 契约),标记落 spawn 的 `post:skill.invoke`(background)payload;
> 升权子帧 `working["_escalated_from"]` = 父档快照(随 checkpoint 持久,
> 不进上下文组装,有断言钉死)。已知缝隙:checkpoint `_settle_unpaired_calls`
> 规则 2 就地改写不带 escalated 键(崩溃边沿,留开口);
> ② **Grant additive 配对字段**——`question_id`/`frame_id`
> (`api/v1/escalation.py`;checkpoint asdict 落盘,旧档默认空串兼容;
> tool-confirm approve-run 同义登记);
> ③ **D3 派生链最弱一环**——约定 `principal.attrs["via"]` = 上游 principal
> dict 列表(近端在前,递归;`tools/local_registry.py` `_check_data_access` /
> `_expand_via_chain`):链每环过 `allow` 才放行,任一拒=拒(消息含
> `链环 #N`),`data.access.denied` payload additive `via_link`;fail-closed
> (非 list/缺 subject/深度 >8 拒,payload 加 `via_error`);引擎不伪造链,
> 宿主声明;`Principal.attrs` 声明 `Mapping[str,str]`,via 载结构化列表为
> 约定偏差(已注记);
> ④ **E3 升权审计面板**——`GET /api/runs/{run_id}/escalations` →
> {summary{total,approved,denied,grant_run}, events[](时间序,params 截 200、
> paired、asked_ts), grants[](全字段)};读模型 `host/web/escalations.py`
> 纯函数,404/空态照 rca 邻端点;配对:pre↔post 按 (frame_id,skill,tier)
> 时间序闭合,question_id 经 `supervisor.ask`(kind=escalation)回补,
> grant-run 无 pre 单列,approve-once 无台账靠信号;`_KIND_HINTS` 加
> "escalation";前端 `escalations-panel.js`(run 详情 Usage 栏后,懒加载折叠栏)
> + trace.js 三条升权信号专属行(kind="escalation",✓/✗ 状态双编码,debug 台
> 零改动生效)+ 主题契约 token `--sig-escalation`(六主题定制,对比度契约断言)。
> 仍开口:D3 跨 run 自动派生(引擎无触发点)、完整多用户会话映射、
> `_settle_unpaired_calls` 规则 2 escalated 键缝隙。上文 2026-08-31 复核块
> "仍未做"两条据此关闭,dated 原文保留。全量基线:1643 收集 = 1594 passed + 10 skipped + 39 xfailed,0 失败。
> (**2026-09-29 更新**:留尾"确认卡片数据域展示"已关闭——`EscalationRequest`
> additive `domains`/`sensitive`(白名单工具 `data_domains` 浅层并集,保序去重、
> 不递归子技能;sensitive = 其中 policy 判 confidential 子集,无 [data] policy → 空),
> tool-confirm 通道同带;判定复用公开口 `sensitive_domains`,与数据闸共用
> `_resolve_declared_domains`;inbox.js `domainsChipsHtml` 渲染(敏感域
> chip-sensitive --danger 双编码,六主题 copy confirm.domains/confirm.sensitive),
> `to_pending` 不带 domains(resume 重走闸门现算);py +8、mjs 1 块,全量基线
> 全量基线:1755 收集 = 1712 passed + 10 skipped + 40 xfailed,0 失败。新开口:递归子技能域并集、审批选项按域动态化。)
> ---
> ✅ **复核 2026-09-28(ask_human/set_timer 工具面 + std 形态 + 宿主接线)**:
> ① **`system.timer.set` 内核原语**——`tools/timer.py`(`TimerService` asyncio
> 任务表按 run_id 分桶 + `timer_set_tool`,WRITE 档):one-shot `delay_seconds` /
> recurring `interval_seconds`+`count`(缺省无限),二选一缺/并给 INVALID_ARGS,
> 下限钳 0.5s,立即返回 timer_id;到点经 `ctl.inject_message` 向调用帧注入
> `[timer 到点] {note}(timer_id=…,第 N 次[/共 M 次])`(USER/INJECTED),帧终态
> 静默弃;run 收尾 `_release_run` 取消本 run 计时器;进程态不持久化(resume
> 重武装留开口);sleep 可注入(假钟测试)。
> ② **宿主接线**——CLI `_CliUserChannel`(与 `_cli_supervisor` 同构 stdin/stderr,
> 随 supervisor 开关注入,replay 不接线);Web `_InboxUserChannel`:ask 复用收件箱
> (`Question(kind="user-ask")`,既有 pending/answer 闭环),notify no-op——
> `user.notify` 信号上移工具层(`bind_signals` 补发,run/frame 归因,trace/SSE
> 可见,CLI 同享)。
> ③ **std 两技能**——`common.user.ask_human`(`std/user.yaml` + `user_handlers.py`,
> 包装 `system.user.ask`,Constrain 两触发语义落 description)/ `common.task.set_timer`
> (`std/task.yaml` + `task_handlers.py`,包装 `system.timer.set`),过 std gate。
> ④ **注册位置偏差**——三工具注册点在构造器(`LocalPythonToolRegistry.__init__`,
> §6.1 闸门联动,同 fetch_page 先例);builder build 末尾 `bind_timer(kernel.ctl)`
> 恒装配(无 sidecar/debug 时补装 RunControlImpl);未 bind → NOT_FOUND 语义不变。
> **仍开口**:挂起/未启动 run 唤醒(DESIGN §17 开放问题 4)、定时器持久化/resume
> 重武装、`monitor_shell`/`connect_channel`、TUI 接线。上文 std 第 5 波块"剩余"
> 中的"ask_human/set_timer 工具面"与 §3(Tool Registry)复核注的 user 通道
> "CLI 接线留 TODO"据此关闭,dated 原文保留。全量基线:1670 收集 = 1620 passed + 10 skipped + 40 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-29(pause 真语义落地,WS1 内核 + WS2 Web)**:DESIGN §16 跨里程碑
> 开口第一条"pause 真语义"据此关闭。
> ① **形态裁决**——checkpoint 恢复型:宿主 finalize 照常落 checkpoint,恢复走既有
> resume(新内核 config 可换,§2.4"加预算再继续"本义);进程内 await 挂起形态未采用。
> ② **内核**——`RunPaused(RunAborted)`(`kernel/errors.py`,理由原样不再拼
> `"paused: "` 前缀);`ctl.pause` 写独立 `_pause_flags`,两处 safe point(pre:step
> 循环开头/流式 chunk 循环)**stop 优先**(先查 stop 抛 RunAborted,后查 pause 抛
> RunPaused);run 边界特判落 PAUSED、发 `run.paused`(payload {"reason"},不发
> run.aborted),resume 边界同构(链式挂起);`Pause` verdict 在 pre:step/pre:tool.call
> 两处仲裁点真化;`RUN_PAUSED = "run.paused"` 信号(目录 36→37);
> `BudgetGuard(action="stop"|"pause")` + `[sidecars] budget_guard` action 键(strict)
> ——§2.4 stop→pause 降级补票。
> ③ **Web**——RunRecord 状态归口 `done|failed|aborted|paused`(`host/shared/runrecord.py`);
> `POST /api/runs/{id}/pause`(可选 {"reason"},缺省 "web pause";仅 running 生效,
> 非 running 409/未知 404),resume 对 paused 天然兼容;前端 live bar Pause 按钮、
> "pausing" 相位、paused warn Banner(带 Resume ▶)、⌘K 加 pause、status pill
> paused 槽。CLI 退出码 paused 落 else 3 不变。
> ④ **两通道划清**——RunControl.pause = checkpoint 恢复型挂起(PAUSED 有写入点);
> supervisor ask 的 await 就地挂起与调试会话挂起是另两条通道,run 保持 RUNNING
> (docs/SUPERVISOR.md §2.2 已修订);supervisor await 路径的 PAUSED 迁移有意未做。
> **仍开口**:外部事件唤醒(§17 开放问题 4)、CLI pause 子命令(一次性前台,用
> BudgetGuard action 或 web)、run 列表 paused 筛选 chips。
> 全量基线:1681 收集 = 1638 passed + 10 skipped + 40 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-29(register() 留尾四件)**:
> ① **semver 依赖约束准入**——`permissions.skills` 条目支持 `name@^x.y.z`/`@~x.y.z`/`@x.y.z`
> 后缀(`^`=同 major 且 ≥、`~`=同 major.minor 且 ≥、精确=相等;非法条目/非 x.y.z 段 →
> 加载期 SkillLoadError fail-closed);新模块 `skills/semver.py`,接入 `_load_all` 依赖检查
> (`skills/local_file.py:238-257`,逐 dep 解析、不满足消息含安装版/约束),拓扑排序等下游
> 统一用解析后纯名。**裁决:只做约束准入,不做多版本求解**——SKILL-PACKAGES-V2 §8.2/§9.2
> 维持,注册表仍单版本/name,`||`/`>=` 不做。
> ② **目录形态 register()**——目录 registry 可注册,目标恒 `<dir>/registered.yaml`
> (`package.py` `_atomic_write_registered`:候选合并 → staging 整目录全流水线证明 →
> .bak + os.replace + reload);技能名已在其他人管 yaml → SkillLoadError 指出来源文件,
> 不碰人管文件;action appended/replaced 同单文件语义;列表形态仍拒;顺带修
> `_write_generated_handler` 目录落点 bug(原落到目录外)。
> ③ **热重载 watcher**——`registry.start_watching(interval_s)`/`stop_watching()`:
> daemon 线程轮询 `_sources_mtime`,变了 reload,reload 失败吞异常旧表不动,start/stop
> 幂等,生命周期随进程;`[skills] watch_interval: float = 0` 默认关(strict;配了
> watch/smoke 但无 path → ConfigError)。
> ④ **验证门 smoke hook**(DESIGN §6.2 注入哲学落地)——`registry.bind_register_smoke(callable)`,
> register() 第 4 步(G1-G3 后、pre 信号前)执行,sync/async 兼容;ok 非真 → GateError
> (detail 透传)零写,异常 → GateError fail-closed;`[skills] register_smoke = "module:func"`;
> jsonl gates 加 `"smoke"` 键(skip/pass/fail: detail;smoke 拒绝落 `action="rejected"`
> 记录,其余闸门拒绝仍零写入)。
> **仍开口**:多版本求解/range(裁决不做)、默认重放 + evaluator 实现(挂点已就位)、
> watch 线程的内核 close 钩子(现生命周期随进程)。
> 全量基线:1739 收集 = 1696 passed + 10 skipped + 40 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-29(外部事件唤醒入口宿主层落地)**:DESIGN §17 开放问题 4
> 主体据此关闭(宿主层已答)。
> ① **POST /api/events 三通道**(`host/web/app.py:819`)——body
> `{type(必填非空), payload=dict|str, target?:{run_id}, skill?, input?, wait?}`;
> target 且 running → `inject_event`(跨线程桥 `ctl.inject_message` 根帧,
> USER/INJECTED)→ action="injected"(失败 409);target 且 paused →
> `resume_run(inject=[文本])`:checkpoint 根帧(depth 最小)context.messages
> 追加 `{role:"user", source:"injected", content, meta:{"event":{...}}}`(逐字
> 对齐 `_message_to_dict` 落盘形)后 execute_resume → action="resumed"(文件坏/
> 无根帧 409);终态 409,未知 run 404;无 target:skill 必填(缺 400)→
> start_run(input 缺省 `{"event":{...}}`,wait/principal 透传)→
> action="started"。事件文本 `[event:{type}] {json 紧凑}` 截 2000 字符。
> ② **event.received 宿主信号**(`run_manager.py` EVENT_RECEIVED)——路由成功后
> 经 per-run hub 投 received/routed 两条,SSE 实时与回放可见;刻意不进 api/v1、
> 不写 trace.jsonl(故 GET signals 端点对终态 run 不可见,取舍已注)。
> ③ **内核零改动、契约零改动**,鉴权复用 Bearer 中间件,principal 透传——ch00
> "内核保持回合制、唤醒源留给宿主"的宿主层兑现。
> **仍开口**:事件批处理/queued 策略/status bar 标记(ch04:56/:60)、initiate_X
> 命名约定(纯文档,已落 SKILL-DEV.md §6)、`monitor_shell`/`connect_channel`、
> 持久事件队列/调度、event.received 是否升格内核契约信号(现宿主层)。上文各
> 复核块「仍开口」中的"外部事件唤醒/挂起与未启动 run 唤醒"条目据此关闭,
> dated 原文保留。全量基线:1747 收集 = 1704 passed + 10 skipped + 40 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-29(定时器规格持久化 + resume 重武装落地)**:上文 2026-09-28
> (ask_human/set_timer 工具面)块「仍开口」中的"定时器持久化/resume 重武装"
> 据此关闭,dated 原文保留。
> ① **规格持久化**——`frame.context.working["_timers"]` 列表项 10 键
> {timer_id, run_id, frame_id, delay_seconds, interval_seconds, count, fired, note,
> created_at, next_fire_at}(JSON 纯类型,checkpoint 整 dict 往返);fire 回写
> fired/next_fire_at 滚动;终结标 done;`_release_run` 取消不标 done(统一留给
> resume)。
> ② **重武装**——`TimerService.rearm_from_working(frame, *, now)`;resume 结算
> 序列插入 `_settle_pending_timers`(`kernel/checkpoint.py`,`_settle_pending_tool_confirm`
> 后、`_settle_unpaired_calls` 前),未 bind 静默跳过;帧全 DONE 的 run 结算钩子
> 不跑,DONE run 不复活。
> ③ **折算语义**——one-shot 未到期按剩余重睡、过期立即补一次(欠次必还);
> recurring 剩余 count、过期只补最近一次、错过的中间触发不逐次补账(节奏从
> resume 起算,防断电轰炸);"第 N 次"计数文本跨 resume 连续;重武装/补偿后
> timer_id 改写新 id(防双火)。
> ④ **frame 可达通道**——ToolContext 无 frame 引用,服务侧
> `ctl._kernel.stack.get(frame_id)` 取帧写规格;帧不可达退回进程态旧行为
> (记 warning)。
> **仍开口**:跨 run 计时器、计时器管理工具(cancel/list 留 std 组合子)、裸
> service(无 ctl)时 fired/done 回写无处可达(真实路径 ctl 恒在)。
> 全量基线:1772 收集 = 1729 passed + 10 skipped + 40 xfailed,0 失败。
> ---
> ✅ **复核 2026-09-29(MCP Streamable HTTP 传输 + 协议版本策略 + 真实 server 互测)**:
> 上文 2026-09-28(MCP stdio 适配器落地)块「仍开口」中的"Streamable HTTP 传输"与
> "未与真实 MCP server 互测"据此关闭,dated 原文保留。
> ① **HTTP 传输**——`tools/mcp_http.py` `McpHttpClient`(官方 spec 2025-03-26 版族
> Streamable HTTP,docstring 注明出处;与 McpStdioClient 同接口,McpTool /
> connect_and_register(+_sync) 整段复用):POST 单端点,Accept 双 content-type,响应
> 按 content-type 分流 application/json 单包 / text/event-stream SSE 帧(行级解析照
> host/tui/kernel/sse.py 先例);initialize 捕获 `Mcp-Session-Id` 后续请求(含 DELETE)
> 必带,404 → 重连重新 initialize 重试一次;`MCP-Protocol-Version` 头按协商值带;
> httpx `trust_env=False`(不读代理/netrc,同 stdio 不继承宿主 env 精神);超时/取消
> 不拆会话(HTTP 每请求独立 POST 无共享流),close = best-effort DELETE + 幂等。
> ② **协议版本策略**——stdio 默认 2024-11-05 不变,HTTP 默认 2025-03-26,
> `protocol_version` 双传输可覆盖,协商返回值记录。
> ③ **配置四键**——`[mcp.servers.*]` 加 `url`/`headers`(值含 {env="VAR"} 间接)/
> `transport`(auto|stdio|http,auto 按键判)/`protocol_version`;command/url 恰居其一,
> headers 配在 stdio server 上拒,strict 全矩阵(tests/runtime/test_config.py 增 MCP
> HTTP 校验矩阵)。
> ④ **失败归一**——连接错/断流/5xx/404/协议垃圾 → 重连重试一次;其余 4xx 与
> JSON-RPC error → 直接 McpError 不重连(服务端拒绝,重连无意义)。
> ⑤ **测试面**——HTTP 锚点 = FastAPI 罐头对端 `tests/helpers/mcp_http_server.py`
> (JSON/SSE 双模式、session 强制、DELETE 记账)+ tests/tools/test_mcp_http.py;
> **真实互测跑通**:tests/tools/test_mcp_interop.py 以 npx
> `@modelcontextprotocol/server-filesystem` over stdio 真实握手/tools/list/tools/call
> (read_file 内容逐字回读、目录外拒绝、真实 server structuredContent 归一化兼容),
> skip 护栏 = npx 缺席或 AGENT_OS_MCP_INTEROP=0。
> **仍开口**:GET standalone SSE/Last-Event-ID resumability、batching、
> resources/prompts 原语、HTTP 真实 server 互测(官方 server 多无 HTTP CLI 形态)、
> OAuth、懒连接、版本锁定。
> 下文 §3(Tool Registry)开口表述同步复核(2026-09-29 行)。
> 全量基线:1795 收集 = 1752 passed + 10 skipped + 40 xfailed,0 失败。

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

- **复核 2026-09-27**:`parallel_invoke` fork/join 已落地(`kernel/runner.py:1482-1763`,first-success、幂等结算、并发上限、批内故障隔离全实现;`LogicContext.parallel()` 委托),同批落地子树级联取消(`cancel_subtree`,:1837-1893;`RunControl.cancel_frame`)与子树记账读视图(`subtree_usage`,:1951-1988;`RunControl.get_subtree_usage`);spawn 的"未确认完成不得宣称 done"校验 hook 仍开口。
- **复核 2026-09-27(流式批)**:runner 消费 `stream()` 已落地(`_llm_call`/`_stream_call`,`kernel/runner.py:548-632`,`post:llm.chunk` 仅 ASYNC 观察,ttft/total 入账;边界见头部复核块),异步工具挂起仍开口;两个遗留死 stub 已清理(`kernel/dispatch.py` 整文件删除、`kernel/run.py` 的 `check_control_flags` 移除)。
- **复核 2026-09-28(压缩链)**:summarize 压缩已有独立连败熔断(默认 3 次,熔断退化纯截断,`context/summarize.py`);恢复熔断通用化仍开口。
- **复核 2026-09-29(事件入口)**:外部事件唤醒入口已以宿主层形态落地(`POST /api/events` 三通道:running 注入/paused 恢复/无 target 起新 run;内核零改动,见头部复核块)——本条就此关闭;其余开口(事件批处理、`monitor_shell`/`connect_channel`、持久事件队列/调度、`event.received` 是否升格内核契约信号)归宿主层与文档范畴,非内核未开发项。

### 2. Providers — ✅ ~75%

完成:ProviderManager(前缀路由、指数退避 respect retry_after、每 provider 令牌桶、**fallback 链 + 切换归一化**、**stream idle watchdog**);OpenAICompatibleProvider(消息/tools/usage/reasoning 映射、429/401/400(context length)/5xx/超时错误映射);MockProvider(脚本化 chat/stream、请求录制、故障注入)。

未开发:Anthropic 原生适配器;ModelRouter 动态路由实现;OpenAI SSE 真实 stream;API 级微压缩(服务端 context editing);logprobs;多模态 token 精确口径;真实 API 夜间冒烟。

- **复核 2026-08-24**:Anthropic 原生适配器已实现(`providers/claude.py`,另有 `providers/kimi.py`);真实 `stream()` 仍为 stub(`openai_compatible.py:104` 标 M1、`claude.py:99` 标 M5);ModelRouter 仍只有契约(`api/v1/providers.py:142`);其余各项抽查仍成立。
- **复核 2026-09-27(流式批)**:真实 `stream()` 已实填(`openai_compatible.py:104` SSE、`claude.py:100` Anthropic 事件序列,KimiProvider 继承;Manager 提交点语义 `manager.py:195-258`),runner 消费与 ttft 记账同步落地(见头部复核块);ModelRouter 仍只有契约(`api/v1/providers.py:141`),logprobs/多模态精确口径仍开口。

### 3. Tool Registry — ✅ ~80%

完成:LocalPythonToolRegistry(decorator 推导、ctx 注入约定、ToolResult 直通);全量分发流水线(schema 校验、三层权限交集、超时、结构化错误四层);6 个内置工具(fs_read 行号+offset/fs_write/fs_edit/shell_exec/http_fetch/python_exec);InMemoryBlobStore。

未开发:FileBlobStore 与 `blob_get`;`ask_user`/`notify_user`;MCP 适配器;`confirm` 两阶段语义;**credentials 凭证注入**(契约字段在,未接线);归一化 enrichment 三件套(head+tail preview、调用计数注释、untrusted_source 的 source tagging 包裹——字段已标,包裹未做);ToolSpec 预留字段语义化(examples 注入、cost/depends_on/conflicts_with 检查)。

- **复核 2026-08-24**:`blob_get` 已实现并注册(`tools/builtins.py:422`、`tools/local_registry.py:393/462-463`,别名 `system.blob.get`);`FileBlobStore` 仍是 M3 stub(`tools/blob.py:40/43`,内存版 InMemoryBlobStore 在用);`ask_user`/`notify_user` 仍为 M1 stub;credentials 注入在分发层仍固定填 `{}`(`tools/local_registry.py`);`confirm=True` 仍只声明不强制;归一化 enrichment 仍未做(`local_registry.py` 无 head+tail preview 痕迹)。
- **复核 2026-09-27(清理批)**:`FileBlobStore` 已落地(`tools/blob.py`,内容寻址落盘 + 白名单防逃逸,`[blob] dir` 配置段接线);`ask_user`/`notify_user` 已实填(`system.user.ask`/`system.user.notify`,WRITE 档,`bind_user_channel` 装配,未 bind → NOT_FOUND;CLI 接线留 TODO);归一化 enrichment 与 ToolSpec 预留字段语义化仍开口。
- **复核 2026-09-28**:MCP 适配器(stdio,工具侧)已落地——`tools/mcp.py` + `[mcp.servers.<name>]` 配置段,eager 装配、失败 ConfigError,工具以 `mcp.<server>.<tool>` 走全量 dispatch 管线,§8.3 供应链清单逐条落地(详见头部复核块);仍开口:Streamable HTTP、resources/prompts、真实 server 互测;归一化 enrichment 与 ToolSpec 预留字段语义化仍开口。
- **复核 2026-09-29**:MCP Streamable HTTP 传输已接入(`tools/mcp_http.py`,spec 2025-03-26 版族;配置段加 `url`/`headers`/`transport`/`protocol_version` 四键,command/url 恰居其一),stdio 官方 server 真实互测已跑通(npx `@modelcontextprotocol/server-filesystem`,`tests/tools/test_mcp_interop.py`;详见头部复核块);仍开口:resources/prompts、HTTP 真实 server 互测(官方 server 多无 HTTP CLI 形态)、GET standalone SSE/resumability、batching、OAuth、懒连接、版本锁定;归一化 enrichment 与 ToolSpec 预留字段语义化仍开口。

### 4. Skill Registry — ✅ ~70%

完成:LocalFileSkillRegistry(单 YAML 加载、manifest 校验、拓扑排序循环依赖报错、自引用合法、构造即加载、mtime reload、make_frame/visible_to、description lint 告警);code 技能惰性 import;装配期权限闸门(§6.1)。

未开发:`register()` 运行期写入路径(M6)与入库前验证门;DirectorySkillSource(目录包形态);版本约束求解;文件监听自动热重载;可见集膨胀后的语义检索层。

- **复核 2026-08-24**:`register()` 运行期写入仍为 M6 stub(`skills/local_file.py:293`);入库前验证门已由 Skill Lab 承载(草稿 `skills/draft_store.py` → 五关闸门 `skills/gate.py` → 原子发布 `skills/package.py`,CLI/Web 双侧);其余各项抽查仍成立。
- **复核 2026-09-27**:`register()` 已落地(`skills/local_file.py:314-484`,闸门 + pre:skill.register 否决 + 原子写 + provenance),消费面 `system.skill.register` 工具(confirm=True 过 tool-confirm 闸门);DirectorySkillSource 写路径、版本约束求解、文件监听热重载、完整重放+evaluator 门仍开口。
- **复核 2026-09-29(register() 留尾四件已关闭)**:① semver 依赖约束准入已落地(`skills/semver.py`;`permissions.skills` 条目 `name@^x.y.z`/`@~x.y.z`/`@x.y.z`,非法条目加载期 SkillLoadError fail-closed;接入 `_load_all` `local_file.py:238-257`;**裁决:不做多版本求解/range,注册表仍单版本/name**);② 目录形态 register() 已落地(目标恒 `<dir>/registered.yaml`,`package._atomic_write_registered` staging 整目录证明 → .bak + os.replace;人管 yaml 撞名 → SkillLoadError 不碰人管文件);③ 文件监听热重载已落地(`start_watching(interval_s)`/`stop_watching()` daemon 轮询 mtime,失败吞异常旧表不动;`[skills] watch_interval` 默认关);④ 验证门 smoke hook 已落地(`bind_register_smoke`,G1-G3 后 pre 信号前,sync/async 兼容,ok 非真/异常 → GateError fail-closed 零写;`[skills] register_smoke`;jsonl gates 增 `"smoke"` 键,smoke 拒绝落 `action="rejected"` 记录);仍开口:默认重放 + evaluator 实现(挂点已就位)、watch 线程的内核 close 钩子、可见集膨胀后的语义检索层。

### 5. Context(上下文)— 🟡 ~55%

完成:TokenEstimator(char/4 + 校准系数接口);RollingWindowCompressor(原子组结构、整组驱逐、保留末组硬截断、`[COMPRESSED]` 标记);ContextManager(build 组装 + **状态注入**(ephemeral、预算 hint)+ maintain 触发压缩 + pre/post:compress 信号 + off 短路 + force_compress);前缀稳定性(工具序固定 + golden 断言);不变量 hypothesis 测试。

未开发(§7.2 策略表中只实现了 truncate 一档):**spill**(blob ref/preview/替换串冻结)、**summarize**(context-aware、保留契约、连败熔断)、**narrate**(多模态旁白)、**hierarchical** 责任链组合;contextualized preview;压缩缓存失效核算;多模态 token 口径;驱逐价值序。

- **复核 2026-09-28**:spill/summarize/hierarchical 责任链已落地(见头部复核块与 docs/DESIGN.md §7.2 实现状态);仍开口:narrate(多模态契约)、contextualized preview、压缩缓存失效核算、多模态 token 口径、驱逐价值序。
- **复核 2026-09-28(多模态批)**:narrate 已落地(`context/narrate.py`;hierarchical 链改序 spill→narrate→summarize,模式表新增 narrate 档),多模态契约 `Message.parts` additive 落地、估算器粗估分支 `IMAGE_PART_TOKENS`=1024/图入估算器(见头部复核块);仍开口:contextualized preview、压缩缓存失效核算、多模态 token **精确**口径、驱逐价值序、MCP image 块 → parts 接线、http_fetch 二进制。

### 6. Sidecars — ✅ ~80%

完成:SidecarSupervisor(SYNC priority 序 + 2s 超时 + fail-closed;ASYNC 派发 + close);5 个内置(BudgetGuard / LoopDetector / StallDetector / ToolGuard / CodeScanner);reviewer(proposer-reviewer)经 pre:frame.pop 落地;纠偏消息带操作指令。

未开发:HumanApproval(骨架);MetricsCollector;蒸馏 sidecar(M6);LLM 驱动 sidecar 的配套设施(rejection circuit breaker、不同家族审批);输入最小化的强制过滤(现靠 payload 结构化自律,未做强制裁剪)。

- **复核 2026-09-28**:蒸馏 sidecar 已落地(`DistillSidecar`,`sidecars/builtins.py:331-526`,ASYNC 订阅 `run.finished`/`run.aborted`;触发闭包直挂信号总线——终态信号的 supervisor ASYNC 派发有结构性竞态,详见头部复核块与 docs/DESIGN.md §5.3);MetricsCollector、LLM 驱动 sidecar 配套设施(rejection circuit breaker、不同家族审批)、输入最小化强制过滤仍开口。

### 7. Logic Kernel — ✅ ~70%

完成:InProcessLogicKernel(模块路径入口、stdout 捕获、序列化检查、硬失败透传);PythonSandboxLogicKernel(子进程 + rlimits + 驱动脚本执行 code 技能、`-I` 下 PYTHONPATH 处理);信任选路(manifest/force_sandbox)。

未开发:**网络隔离**(unshare/nsjail——目前断网靠"不装网络工具"而非系统级阻断,这是已知最大的安全缺口);**沙箱回调通道**(JSON-RPC 代理 LogicContext,设计方向已定);merge_limits;mem_peak 记账;JS/Wasm/Remote 后端。

- **复核 2026-08-24**:系统级网络隔离已由 Docker 容器档落地(`logic/docker_sandbox.py`:`--network none` + `--read-only` + `--cap-drop ALL` + tmpfs,commit `4750e4e`),实际走的是容器路线而非 unshare/nsjail;subprocess 档仍不隔离网络与文件系统,但已补 env 白名单与 cwd 临时目录隔离(`logic/python_sandbox.py:148-164/297-310`,commit `6ced3d3`),凭证直读缺口已堵。沙箱回调通道已实现(socketpair + `_serve_syscalls` + runner `_syscall_dispatcher`,见 ../CODE-ORCHESTRATION.md)。`merge_limits` 仍 M5 stub(`logic/limits.py:34`);`mem_peak` 仍不记账(`logic/inprocess.py:6`)。
- **复核 2026-09-27(清理批)**:`merge_limits` 已实填(`logic/limits.py:32`,两级取紧链式得三级,返回新实例;尚无调用点);`mem_peak` 仍不记账。

### 8. Telemetry — 🟡 ~50%

完成:JsonlTelemetrySink(版本头 WAL、按 run_id 分文件、总线特权订阅);内核 checkpoint/resume(帧含完整上下文序列化、按深度结算未配对调用、恢复只补未完成部分)。

未开发:`sink.snapshot()`;OTLP/OpenInference 导出;训练就绪导出(压缩前原始报文 + provenance);PII 脱敏 hook;MetricsCollector。

- **复核 2026-09-27(清理批)**:`sink.snapshot()` 已实现为 WAL 视角快照(`telemetry/jsonl_exporter.py:112`,seq = 已落盘信号数、`state={}`;帧树重建留 `kernel/checkpoint.py`),`JsonlExporter` 同批实填(全 run 汇聚单文件、close 幂等);OTLP/PII/MetricsCollector 仍开口。

### 9. Memory — ⬜ ~5%

仅 32 行骨架(全部 M6 stub)。契约在 api/v1(MemoryService/MemoryEntry),builder 仍封禁。未开发:LocalFileMemoryService、检索工具、source tagging、通道隔离、新鲜度治理。

- **复核 2026-09-27**:已关闭主体。`LocalFileMemoryService` 三方法全实现(`memory/local_file.py`,Markdown + frontmatter;search = principal 过滤 → freshness → BM25 → k 截断,evict 带 `.evictions.log` 审计);BM25/RRF 共用在 `memory/rank.py`;`system.memory.search/write` 工具常驻,`bind_memory` 装配,builder 封禁移除,`[memory] dir` 配置段接线。仍开口:常驻层 pinned 注入(recall 注入槽不走 pinned 形态);context 注入槽(组装侧,manifest `context_policy.recall` opt-in)与蒸馏 sidecar 写路径已于 2026-09-28 关闭(见头部复核块)。

### 10. Blackboard — ✅ ~70%

完成:LocalBlackboard(CAS 乐观锁、publish/subscribe、读写信号);StatusBoard(runner 每步/弹栈写入);spawn 后台帧配套;manifest.permissions.blackboard 命名空间仲裁代理。

未开发:run 作用域隔离强化;worktree 式隔离;read-before-write 强制;跨 run 持久化。

### 11. Runtime — ✅ ~60%

完成:KernelBuilder 全链式装配(仅 memory 仍封禁)、缺省补全、装配期权限闸门。

未开发:`runtime/config.py` TOML 配置加载(M0 stub 至今)。

- **复核 2026-08-24**:已关闭。`runtime/config.py` 已全量装配(`[run]`/`[providers]`/`[skills]`/`[tools]`/`[sidecars]`/`[supervisor]`/`[telemetry]`/`[prices]`/`[retry]`);CLI `agent-os run --config`(`host/cli/main.py`,默认 `agent-os.toml`)与 Web `agent-os-web --config`(`host/web/serve.py:51`)均吃 `--config`。
- **复核 2026-09-27**:builder 的 memory 封禁已移除——`[memory] dir` 配置段(`runtime/config.py`,缺段不 bind)与 `KernelBuilder.memory()` 均接线,MemoryService 经 `bind_memory` 注入工具 registry。

## 三、契约层与设计的偏差(待回写)

开发中实际发生的 3 处 api/v1 增补,../DESIGN.md 尚未同步:`SkillFrame.call_id`、`LogicContext.spawn/wait/board`、`BlackboardConflict`。下次更新设计文档时回写(§2.3、§9.3、§12.1)。

## 四、未开发功能汇总(按优先级)

**P0(安全/契约相关)**:沙箱系统级网络隔离;credentials 凭证注入;source tagging 包裹;HumanApproval。

(**复核 2026-08-24**:沙箱系统级网络隔离已关闭——Docker 容器档;source tagging 包裹在 web 感知工具侧已落地——`tools/std_web.py` 的 `<external_content source="...">`;credentials 注入仍固定 `{}`、HumanApproval 仍 M4 骨架(`sidecars/builtins.py:253`),这两项仍开口。)

**P1(主线里程碑 M6)**:Memory 子系统(LocalFile baseline + 契约接线);`register()` 写入路径 + 入库前验证门;spill/summarize 压缩策略(hierarchical 链补全);parallel_invoke。

(**复核 2026-08-24**:本档全部仍开口——memory 三方法与 `register()` 仍 M6 stub;`context/` 仍只有 truncate 一档;`parallel_invoke` fork/join 未实现。验证门见 §4 复核注,已由 Skill Lab 承载。)

(**复核 2026-09-27**:Memory 子系统与 `register()` 写入路径已关闭(见头部复核块);spill/summarize 压缩策略与 `parallel_invoke` 仍开口。)

(**复核 2026-09-27(并发三原语)**:`parallel_invoke` 已关闭(见头部复核块);spill/summarize 压缩策略仍开口。)

**P2(增量)**:OTLP 导出;runner 消费 stream;蒸馏 sidecar;TOML 配置加载;blob_get / ask_user / notify_user;恢复熔断通用化;"不说 done" hook;死 stub 清理。

(**复核 2026-08-24**:TOML 配置加载与 blob_get 已关闭(见 §11/§3 复核注);`ask_user`/`notify_user`、runner 消费 `stream()`、"不说 done" hook 抽查仍开口;死 stub `kernel/dispatch.py`/`kernel/run.py` 仍在;OTLP 仍只有契约层 docstring。)

(**复核 2026-09-27(stub 清零+流式)**:`ask_user`/`notify_user`、runner 消费 `stream()`(真实 provider SSE/Anthropic 序列 + `post:llm.chunk` + ttft 记账)、死 stub 清理(`dispatch.py` 删除、`check_control_flags` 与裸 `python_exec` 移除)已关闭,见头部复核块;OTLP、"不说 done" hook、恢复熔断通用化、蒸馏 sidecar 仍开口。)

(**复核 2026-09-28**:蒸馏 sidecar 已关闭(见头部复核块);OTLP、"不说 done" hook、恢复熔断通用化仍开口。)

**P3(开放问题)**:事件唤醒入口;语义检索可见层;FrameContext 继承/克隆;帧树粒度信用分配。

(**复核 2026-09-29**:事件唤醒入口已由宿主层 POST /api/events 三通道兑现(见头部复核块),移出开放问题清单;其余三项仍开口。)

## 五、建议的下一步

两个方向可选:

1. **补安全短板**(工作量小、风险敞口大):沙箱网络隔离(unshare -n)+ credentials 注入 + source tagging 包裹 + HumanApproval——把"权限控制"半边天补齐;

   (**复核 2026-08-24**:网络隔离已按 Docker 容器档落地而非 unshare;source tagging 包裹已在 web 感知工具落地;剩 credentials 注入与 HumanApproval 仍开口。)
2. **进 M6 主线**(设计节奏):Memory + register() + 验证门——打通自我进化供给侧,是 ../DESIGN.md 规划的最后一个里程碑。

   (**复核 2026-09-27**:Memory 与 register()(带验证门)已落地;M6 剩余为沙箱回调通道高级形态、spill/summarize/narrate、蒸馏 sidecar 与 register() 留尾四项。)

   (**复核 2026-09-28**:蒸馏 sidecar 已落地(见头部复核块);M6 剩余为沙箱回调通道高级形态、narrate 压缩策略与 register() 留尾,蒸馏留尾:用户纠正/非显然工作流触发、跨进程去重、run 级用量信号、std/learn 三技能。)
   (**复核 2026-09-28(多模态批)**:narrate 压缩策略已落地(见头部复核块);M6 剩余为沙箱回调通道高级形态与 register() 留尾。)
