# Context:组装、压缩与前缀稳定性

> 章次:05 · 状态:已实现(基线;责任链高阶策略 spill/narrate/summarize/hierarchical 已设计未实现) · 依据:`docs/DESIGN.md` §7、`agent_os/src/agent_os/context/manager.py`、`agent_os/src/agent_os/context/rolling_window.py`、`agent_os/src/agent_os/context/estimator.py`、`agent_os/src/agent_os/api/v1/context.py`

## 1. 概述

Context 子系统是帧上下文(`FrameContext`)的全权管理者:每次 LLM 调用前,它把指令、帧消息、可见工具 schema 与内核状态**组装**成 `ChatRequest`;当上下文超过软上限时,它按策略**压缩**;并以"前缀逐字节稳定"为硬约束维护 LLM 前缀缓存的命中率。在 OS 映射表中,它对应内存管理/GC(执行摘要 §2.1)。内核不感知组装的任何细节——agent loop 只在每步固定调用 `maintain` 再 `build`(`agent_os/src/agent_os/kernel/runner.py:409-410`),策略全部外置。

## 2. 动机与背景(原因)

**为什么组装与压缩必须一家管。** 这两个职责看上去方向相反——一个往请求里放东西,一个从上下文里拿东西——但它们是同一条不变量的两面:压缩是前缀缓存的最大破坏者,组装是前缀稳定的唯一维护者,分属两个子系统必然口径打架(`docs/DESIGN.md` §7 职责段)。因此 `ContextManager` 协议把 `build` 与 `maintain` 收进同一个接口(`api/v1/context.py:20-29`)。

**为什么不放进内核。** 按微内核判据(执行摘要 §2.2),组装与压缩既不是流控制,也不是权限/否决仲裁点,更不是子系统间唯一公共通道——它是**策略**。"内核拥有循环,Skill 拥有策略"的推论在这里兑现为:内核只在固定点调用协议,估算口径、压缩算法、状态栏文案全部可换。

**为什么前缀稳定是硬约束而不是优化项。** LLM 计费与延迟都按 prompt token 计,前缀缓存命中与否直接决定长 run 的成本曲线形状。一个每步重排工具 schema 或把变化数据插进 SYSTEM 的实现,会让缓存全失效,成本从"只付增量"退化为"每步付全量"。

**为什么状态注入只信内核记账。** 状态栏被模型无条件信任——它是模型感知预算、步数的唯一通道。若数据来自工具内容,一次投毒(工具返回里伪造"预算充足")就能改变 run 的行为方向(§7.3)。

## 3. 问题陈述(解决的问题)

1. **上下文单调增长 vs 窗口有限**:agent loop 每步追加 assistant 消息与工具结果,长 run 必然撞上模型窗口。测试场景:40 个原子组 × 400 字符 ≈ 16k+ token,远超 manifest cap 4000(`tests/context/test_context.py:234-250`)。
2. **幼稚截断破坏配对协议**:assistant(带 tool_calls)与其 tool result 在 provider API 里是配对约束;从中间切一刀会产生孤儿 tool result,API 直接拒收。
3. **每步重建请求 → 前缀缓存全失效**:SYSTEM、工具 schema 的顺序或序列化结果任何抖动,都让该步请求退化为全量计费。
4. **热重载打穿同帧前缀**:内联能力段若每步从 registry 现取,一次热重载就让在跑帧的 SYSTEM 中途变化,前缀缓存与 resume 确定性同时失守(`docs/SKILL-INLINING.md` §4.2)。
5. **模型对预算与步数无感知**:没有读数就没有自我收敛;但裸读数不改变行为——"预算剩 20%"必须附操作策略才生效(§7.3)。
6. **外部无法干预膨胀的上下文**:sidecar(监督者)发现帧上下文失控时,需要一条不等 cap 触发的强制压缩通道(§7.1 外部强制)。

## 4. 设计与机制(解决的方法)

### 4.1 契约面

`api/v1/context.py` 冻结四个符号:`ContextManager` 协议(`build`/`maintain`,:20-29)、`Compressor` 协议(`name` + `compress(ctx, target_tokens, svc)`,:33-40)、`CompressionReport`(evicted/before/after/cache_invalidation_estimate/marker,:44-54)、`KernelServices`(estimator/providers/blob,:58-67)。新压缩策略经 entry point `agent_os.compressors` 注册——契约先行,基线可换。

### 4.2 build:组装流水线

```
┌─ ChatRequest ────────────────────────────────────────────────┐
│ SYSTEM  render_prompt(skill.prompt, frame.input)             │
│         + 内联能力段(帧首次 build 冻结快照,见 4.3)          │
│ msgs    frame.context.messages(帧私有,原样)                 │
│ USER    状态元消息(ephemeral,meta={"kind":"status"},不写入帧)│
│ tools   白名单工具 schema                                     │
│         + python_orchestrate(manifest 声明 且 RunConfig.on)   │
│         + ask_supervisor    (manifest 声明 且内核装了通道)    │
│         + skill.<name> 伪工具(过滤被 inline 隐藏的)         │
│ model   manifest.model.prefer[0] → RunConfig.model            │
└──────────────────────────────────────────────────────────────┘
```

关键判定逻辑(`context/manager.py:99-152`):

- 两个伪工具**不在工具注册表**(内核分发时拦截),由 build 按"声明 + 消融/装配开关"补进可见工具面(:112-126)。`ask_supervisor` 的取舍写在注释里:内核没装 supervisor 通道时,模型调了也只能吃 `not_found`,不如不呈现(:118-119)。
- model 解析顺序:manifest `prefer[0]` 优先,空则回落 `RunConfig.model`;temperature 同理(:133-141)。技能作者可以钉模型,宿主可以兜底。
- 状态消息只在请求尾部**追加**,绝不写回 `frame.context.messages`(:143-146)——动态信息走末尾,静态前缀不动,这就是不变量 5 的实现姿势。

### 4.3 内联能力段:首次 build 冻结快照

`inline: true` 的纯说明书技能不压帧,其 prompt 并入调用方 SYSTEM。组装规则(`_inline_caps`,manager.py:154-201):消融档 `inline == "off"` 直接返回 `None`(merge 技能退化为普通压帧调用);否则帧**首次** build 时按 registry 现值组装,快照存进 `frame.context.working["_inline_caps"]`(:46,:194),后续 build 一律复用。快照随帧入 checkpoint,一举解决三件事:前缀稳定(不变量 5)、热重载"在跑帧钉旧版"、resume 后内联段与断电前逐字节一致(`docs/SKILL-INLINING.md` §4.2)。被 inline 的技能同时从伪工具面隐藏(:127-132),模型不会尝试调用一个"调用不存在"的技能。

### 4.4 状态注入(Status Bar)

每步 build 尾部追加一条 `role=USER, source=INJECTED, meta={"kind": "status"}` 的 key-value 元消息(`_status_message`,manager.py:203-224):step/tokens/cost/budget_remaining 裸读数 + `hint` 操作策略。判定逻辑只有一行:预算剩余低于 `LOW_BUDGET_RATIO = 0.2`(:35)时 hint 切换为"收敛到可直接交付的方案",否则"正常推进"。run 有 TODO 时并入进度摘要行(`_todo_status`,:226-265),数据同样只来自内核记账。设计取舍:裸读数不改变行为,读数+策略才改变(§7.3)——所以 hint 不是装饰,是契约的一部分。

### 4.5 maintain:触发与压缩路径

```
每步 loop 顶部(runner.py:408-410):
  working 弹出 _force_compress 标志? ──是──▶ force_compress(无视 cap)
  maintain(frame):                              │
    estimate = estimator.estimate(messages)     │
    cap = manifest.context_policy.max_tokens    │
          或 default 128_000                    │
    compression=="off"(RunConfig 或 manifest)? ──是──▶ 短路
    estimate ≤ cap 或无 compressor? ──是──▶ 只更新估算
    否则:pre:compress 信号
          → compressor.compress(ctx, int(cap*0.8), svc)
          → post:compress 信号(含 report 全字段)
```

`force_compress` 是 §7.1 的外部强制触发:sidecar 发 `ForceCompress`,RunControl 在帧工作内存置标志(`kernel/control.py:21-22`),runner 在 maintain 前消费(runner.py:408)——仍然走同一条 `_compress` 路径(manager.py:303-326),只是 pre 信号载荷带 `forced=True`。消融档对强制触发同样短路(:287-288)。

### 4.6 RollingWindowCompressor:原子组整组驱逐

基线压缩器(rolling_window.py)是纯函数、无 LLM/blob 依赖的三步算法:

1. **分组**(`atomic_groups`,:33-60):assistant(带 tool_calls)与其后续**连续**且 `tool_call_id` 匹配的全部 TOOL 消息绑成一个原子组;其他消息各自成组。配对原子性(不变量 2)由此**结构性**保证,而不是靠事后检查——驱逐的最小单位是组,组内不可分。
2. **整组驱逐**(:83-89):含 pinned 消息(`m.meta["id"] in ctx.pinned`)的组永不弹;从最旧的非 pinned 组开始整组弹出直到 `estimate ≤ target`,但**始终保留最后一个非 pinned 组**——全弹光会让帧丢失全部近期上下文,它是硬截断兜底的对象。
3. **硬截断兜底**(:91-94,:106-121):驱逐后仍超限(单个组过大),对最早保留的非 pinned 组逐消息做字符级截断,标注 `[truncated]`;只动 content,结构不动,配对不破。短于标注串本身的内容跳过——"截它比留着更贵"(:117)。

报告携带 `marker = "[COMPRESSED]"`(:25)——为责任链多策略重复处理预留的幂等标记(见 §6)。

### 4.7 估算器:口径唯一

`TokenEstimator`(estimator.py):char/4 粗估 + 每消息固定开销 4 token(:14) + tool_calls 参数 JSON 折算,`calibration` 系数全局可调。它与 ProviderManager 共用同一口径(§4.2)——压缩水位判断与计费估算不会出现两套数字。`per_provider_factor` 预留各家 tokenizer 偏差的折算入口(:38-43)。

### 4.8 策略链:设计全景与实现落点

| §7.2 策略 | 状态 | 说明 |
|---|---|---|
| `collapse_child` | 已实现(结构性) | 子帧弹栈时 transcript 不进父帧,只留返回值(fib 测试断言"整段子帧轨迹已折叠",`tests/kernel/test_fib_slice.py:112`) |
| `truncate`(rolling window) | 已实现(基线) | 本章 4.6 |
| `spill` / `narrate` / `summarize` | 已设计未实现 | `KernelServices.providers` 当前恒 `None`(manager.py:336);blob 通道已预留(:337) |
| `hierarchical`(责任链默认) | 已设计未实现 | 链尾必须有 summarize 或 spill 承接(§7.6 定位警告) |

## 5. 效果与验证(效果)

测试基线:`agent_os/tests/context/` 共 **31 例全绿**(`pytest tests/context -q`,test_context.py 14 例 + test_inline_merge.py 15 例 + 参数化展开)。关键证据:

- **不变量 property 测试**(hypothesis,`test_context.py:175-199`):随机消息历史(段数 1-24、组内 tool_calls 0-3、消息 50-600 字符,40 例)下,压缩后配对完好、pinned 永驻、估算单调不升——§7.4 前三条不变量直接可测。
- **整组驱逐顺序**(:122-141):最旧组先走、最新组保留、pinned 系统消息原位;**单组超限硬截断**(:157-167)带 `[truncated]` 标注且配对不破。
- **触发与短路**(:234-265):超 cap 压到 `int(cap × target_ratio)` 水位(容差一个组的粒度),`pre/post:compress` 恰好各一次;`compression: "off"` 下消息数不变、零信号。
- **force_compress**(:319-344):未超 cap 也强制压一次、pre 载荷 `forced is True`;消融档下仍短路。无 compressor 时静默跳过但估算照常更新(:347-365)。
- **状态注入**(:273-299):状态消息在请求尾部、`kind=status`、ephemeral 不进帧上下文;预算剩 5% 时 hint 含"收敛"。
- **前缀稳定性**(:302-311):连续两次 build,剔除 status 消息后 `(role, content)` 逐字节一致;端到端层面,fib 切片测试断言同一帧相邻两次请求的 SYSTEM 逐字节一致(`tests/kernel/test_fib_slice.py:114-115`)。
- **inline 快照**:热重载后同帧 SYSTEM 不变、新帧用新版(`test_inline_merge.py:230`);快照冻结进 `working`、resume 逐字节确定(:254);`post:context.inline` 信号一次性发出(:281)。

涟漪效应:不变量 5 反向塑造了 inline 子系统的形态——"首次 build 冻结进 working"不是 inline 的私设,而是前缀稳定约束的推导结果(SKILL-INLINING.md §4.2);`KernelServices` 的三件服务形状为 spill/summarize 预留了挂点,未动契约。

## 6. 局限性与边界(局限性)

1. **裸 rolling window 是已知循环诱因**:丢早期工具结果 → 模型重复调用已丢的工具。源码与 §7.6 都自带定位警告——它只是 hierarchical 链的中间层基座,链尾必须有 summarize 或 spill 承接,**不得读作推荐做法**;而链本身尚未实现,当前生产形态就是这个有已知缺陷的基线。
2. **被驱逐的信息不可恢复**:spill(blob + preview 取回)未实现,rolling window 驱逐即永别;`svc.blob` 虽已接线(manager.py:337),基线压缩器不用它。
3. **char/4 是粗估**:对代码、中文、JSON 密集的上下文偏差可观,cap 判断可能提前或滞后触发;`per_provider_factor` 恒返回 1.0(estimator.py:43),校准系数是占位接口。§7.6 提到的"多模态口径(图像按分辨率公式)"在估算器中没有实现分支——以代码为准,多模态估算未落地。
4. **§7.1 的硬上限路径未实现**:设计中"临近模型窗口 → 激进压缩,仍超限则帧失败上抛"没有对应代码;cap 只有一档(manifest `max_tokens` 或默认 128_000,manager.py:76/291-301),真正撞模型窗口的兜底目前依赖 provider 报错。
5. **状态栏只实现了"短轨迹逐轮替换"一支**:§7.3 设计了"长轨迹持久追加(完全保缓存)"分支,代码中状态消息每步重建、尾部 ephemeral 追加,等价于逐轮替换;设计中的"当前时间、工具调用计数"字段也未出现在状态行(manager.py:209-215)。以代码为准。
6. **hint 策略硬编码**:20% 阈值(`LOW_BUDGET_RATIO`)与两条文案写死在 manager 里,技能无法按自身任务形态配置收敛策略;状态栏信任模型(模型无条件信任)放大了这条固定策略的影响面。
7. **`[COMPRESSED]` 幂等标记暂无消费者**:它只出现在 `CompressionReport` 里(rolling_window.py:103),防责任链重复处理是预留语义——链不存在,标记当前只是记录。
8. **压缩触发粒度为"每步 build 前"**:单步内(如一次工具调用写回超大结果)不干预,最早下一步 maintain 才处理;这期间估算已超 cap 的上下文会在 checkpoint 里原样落盘。

## 7. 引用

- 设计文档:`docs/DESIGN.md` §7(Context 子系统)、§4.2(token 口径唯一)、§3.1(agent loop);`docs/SKILL-INLINING.md` §4(组装/冻结/消融);`docs/SUPERVISOR.md` §2.1(ask_supervisor 呈现条件);`docs/CODE-ORCHESTRATION.md` §2.1(orchestrate 伪工具)
- 契约:`agent_os/src/agent_os/api/v1/context.py`、`agent_os/src/agent_os/api/v1/frames.py`(FrameContext/Usage)
- 实现:`agent_os/src/agent_os/context/manager.py`、`agent_os/src/agent_os/context/rolling_window.py`、`agent_os/src/agent_os/context/estimator.py`;调用点 `agent_os/src/agent_os/kernel/runner.py:408-410`、`agent_os/src/agent_os/kernel/control.py:21-22`
- 测试:`agent_os/tests/context/test_context.py`、`agent_os/tests/context/test_inline_merge.py`、`agent_os/tests/kernel/test_fib_slice.py`
