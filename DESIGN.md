# Agent OS — 内核与子系统设计

> 版本:v0.4(微内核化:内核 = 流控制 + 权限控制 + IPC;新增 Telemetry / Memory / Blackboard 子系统;Context Compression 扩容为 Context)
> 语言:Python 3.11+,全异步(asyncio)
> 形态:嵌入式库;目录结构与接口预留服务化扩展
> 配套:`reports/` 内有各章书评与微内核综述,本文档是自洽的唯一设计基准

---

## 1. 设计哲学:一个贯穿始终的隐喻

整个系统的骨架是一张映射表。设计中的所有命名、边界与机制都从这张表推导出来,保证概念不自相矛盾。

| 编程 / OS 概念 | Agent OS 对应 | 含义 |
|---|---|---|
| function | **Skill** | 有名、带参、可组合的行为单元;可以调用其他 Skill |
| system call | **Tool** | 触碰外部世界的原子特权操作,由内核统一仲裁 |
| call stack / stack frame | **SkillFrame 栈** | 每次 Skill 调用压入一帧,帧持有私有上下文 |
| 局部变量(帧状态) | **FrameContext** | 本帧的消息历史 + 工作内存,对其他帧隔离 |
| 动态链接器 / loader | **Skill Registry** | 发现、校验、解析依赖、加载、写入 Skill |
| syscall 表 + seccomp | **Tool Registry + 权限模型** | 按名分发、参数校验、能力白名单 |
| 设备驱动 | **Provider** | 屏蔽各家 LLM API 差异 |
| 内存管理 / GC / swap | **Context(上下文子系统)** | 上下文组装 + 压缩 + 前缀缓存稳定性 |
| 中断 / 看门狗 / signal handler | **Sidecar** | 由信号触发的监督者,可否决、暂停、强停 |
| 进程 | **Run** | 一次完整的 Agent 运行(一个根 Skill 帧及其子树) |
| CPU / ALU(指令执行单元) | **Logic Kernel** | 逻辑代码的唯一执行点:code 技能与 LLM 动态代码 |
| 进程隔离 / namespace / seccomp | Logic Kernel 的沙箱模式 | 不可信代码的进程级隔离执行 |
| journald / auditd / 飞行记录仪 | **Telemetry** | 信号落盘为 WAL,导出与检查点 |
| 文件系统 / 持久存储 | **Memory** | 跨 run 记忆与知识,检索层权限过滤 |
| 共享内存 / 消息队列 | **Blackboard** | run 内帧间状态与消息,并发控制 |
| 微内核(调度 + 权限 + IPC) | **Kernel Runner** | 只做流控制与权限控制,功能全部外置 |

> 注:有文献把 LLM 比作 CPU(分时、无状态、可替换)。我们把 CPU/ALU 给了 Logic Kernel、LLM 放在 Provider(设备驱动)——前者服务"执行可仲裁"的论述,后者服务"屏蔽厂商差异"的论述;两种类比各自成立。

四条设计公理:

1. **微内核。** 内核只保留三件事:**流控制**(loop 推进、帧栈、调用分发仲裁、safe-point 取消、并发原语、预算中止判决、恢复熔断)、**权限控制**(三层交集检查、trust 路由、verdict 仲裁、凭证作用域)、**IPC**(信号总线、RunControl、帧间消息寻址)。判据:没有它 loop 无法推进(F),或它是权限/否决仲裁点(P),或它是子系统间唯一公共通道(I)——满足其一才留在内核,其余一律子系统化。推论:内核拥有循环,Skill 拥有策略;内核自身不执行逻辑代码(交 Logic Kernel),不组装提示词(交 Context),不做持久化(交 Telemetry/Memory)。
2. **子系统互不相识。** 任何跨子系统协作都经由契约层(`api/v1` 的 Protocol)或信号总线,由内核中介。例如压缩器需要 LLM 做摘要时,它调用的是 `ProviderManager` 协议,而不是某个具体 provider。
3. **一切特权操作可拦截。** 工具调用、子技能调用、模型请求、逻辑代码执行都是"系统调用",内核在分发前后发出信号,同步 sidecar 拥有否决权(类比 seccomp)。策略(审什么)在 sidecar,仲裁(fail-closed、优先级、超时)在内核。
4. **每层可删除。** 契约稳定,实现随时可退役:harness 的每个机制(压缩、监督、弹性)都是可独立移除的注册项,模型变强后 baseline 的宿命是被替换或退役,契约不因层的删除而变化。

每个子系统都定义一个 **baseline 实现**:满足契约的最小可用实现,是里程碑的落点,也是第三方实现契约时的参照物。各章末节给出。

---

## 2. 核心抽象与数据模型

### 2.1 Skill(函数)

Skill 是统一的执行单元,有两种形态,对内核透明:

- **prompt 技能(声明式)**:指令体是提示词模板,由内核跑 LLM agent loop 执行;
- **code 技能(代码式)**:指令体是一个 Python 协程,由 Logic Kernel 执行(见第 9 章),确定性强,适合纯逻辑(解析、校验、编排)。

形态选择判据(注脚):参数复杂度高走结构化 schema、逻辑稳定走 code、易变 know-how 走 prompt。

```yaml
# skills/web_research/skill.yaml — Skill 清单(Manifest)
name: web_research
version: 0.3.0
kind: prompt                    # prompt | code
description: 给定问题做网络调研并输出结构化报告   # 应写成路由规则:"Use when / Do not use when" + 负例
inputs:                         # JSON Schema
  type: object
  properties: { question: { type: string } }
  required: [question]
outputs:
  type: object
  properties: { report: { type: string } }
verifier: check_report@^1.0     # 可选:语义验证器(code 技能),经 Logic Kernel 确定性执行
permissions:                    # 能力白名单,加载期静态校验 + 运行期逐次检查
  tools:  [web_search, fetch_url]
  skills: [summarize_text@^1.0]
  blackboard: [status]          # 可访问的黑板命名空间(见第 12 章)
model:
  prefer: ["anthropic/claude-*", "openai/gpt-4o"]
  temperature: 0.3
context_policy:
  max_tokens: 32000             # 帧上下文软上限
  compress: hierarchical        # off | truncate | spill | summarize | hierarchical
limits:
  max_steps: 40
  timeout: 600
entry: SKILL.md                 # prompt 技能的指令体;code 技能为 handler.py:run
```

关键设计点:

- **输入输出带 JSON Schema**。子技能在父帧的 LLM 眼里呈现为一个带类型签名的伪工具(见 3.3),模型原生 function calling 即可发起调用;返回值在交付父帧前按 `outputs` schema 校验(格式层),再经可选 `verifier` 做语义层校验;不合法则作为错误观察返回给父帧(类比类型错误)。
- **权限在清单里声明**。Skill 只能用白名单内的工具、子技能与黑板命名空间——加载期依赖解析和运行期逐次检查的双重约束。
- **递归/循环调用**由 `max_depth` + 循环检测 sidecar 双重控制(见 5.4)。

### 2.2 Tool(系统调用)

Tool 是原子能力:无调用栈、无 LLM 循环、单次进出。

```python
# api/v1/tools.py
class Tool(Protocol):
    spec: ToolSpec
    async def __call__(self, args: dict, ctx: ToolContext) -> ToolResult: ...

ToolSpec = {
    "name": "fetch_url",
    "description": "...",          # 写 "when to use" + 边界 + 负例,不写 "what it does"
    "parameters": {...},           # JSON Schema
    "permission": Permission.NET,  # READ | WRITE | NET | EXEC,粒度递增
    "idempotent": True,            # 供重试与 sidecar 判断
    "timeout": 30,
    # —— v1 契约预留字段(baseline 可先不实现检查逻辑)——
    "examples": [],                # 1-5 个真实调用示例,随 schema 注入
    "cacheable": False,            # 只读工具结果可缓存
    "confirm": False,              # 不可幂等操作的两阶段语义(dry run + confirmation token)
    "concurrency_safe": False,     # 默认否(fail-safe),parallel_invoke 依赖
    "cost": None,                  # 成本注解(静态分析/路由用)
    "depends_on": [],              # 前置工具依赖
    "conflicts_with": [],          # 互斥约束
    "untrusted_source": False,     # 结果是否来自不可信内容(触发 source tagging)
}

ToolContext = {
    "run_id": ..., "frame_id": ...,
    "principal": None,             # caller identity(user/tenant),v1 恒 None,契约预留
    "workdir": ...,                # 帧工作目录(限定 fs 工具范围)
    "blob": BlobStore, "log": Logger,
    "credentials": {},             # 按工具声明注入的凭证作用域,不碰全局环境
}

ToolResult = {
    "ok": bool, "value": Any,
    "error": { "kind": ..., "message": str, "retryable": bool, "hint": str } | None,
    # 结构化错误:类型/参数/修复建议;retryable 供模型与 LoopDetector 区分"该重试"与"该换策略"
}
```

`ToolContext` 注入 run 作用域的资源(工作目录、blob store、凭证、持久 shell 句柄)——工具不碰全局状态,因此天然可测试、可沙箱化。

### 2.3 SkillFrame 与 FrameContext(调用栈与帧状态)

```python
SkillFrame = {
    "frame_id": "f-7c2",
    "run_id":   "r-01",
    "skill":    SkillRef("web_research@0.3.0"),
    "parent_id": "f-7c1",         # None = 根帧
    "input":    {...},
    "context":  FrameContext,     # 本帧私有,其他帧不可见
    "status":   PENDING | RUNNING | SUSPENDED | DONE | FAILED,
    "depth":    2,
    "result":   None,
    "error":    None,
    "usage":    Usage(...),       # 见下
}

FrameContext = {
    "messages": list[Message],    # prompt 技能的 LLM 消息历史
    "working":  dict,             # 结构化工作内存(scratchpad),压缩时优先保留
    "pinned":   list[MsgId],      # 永不压缩的消息(系统提示、任务规格)
    "token_estimate": int,
}

Usage = {                         # 帧与 run 两级的记账(v1 契约字段,成本归因的最小数据前提)
    "steps": int,
    "prompt_tokens": int, "completion_tokens": int,
    "cache_read_tokens": int, "cache_write_tokens": int,   # 缓存读约 1/10 价
    "thinking_tokens": int,                                 # 不可见但计费
    "cost": float,
    "ttft_ms": int, "total_ms": int,
}
```

隔离语义严格对齐函数调用:

- 父帧**只能**通过 `input` 向子帧传参(任务描述必须自包含);
- 子帧**只能**通过 `result`(或 `error`)向父帧返回;
- 子帧运行期间产生的全部中间上下文(工具结果、推理过程)**不进入**父帧上下文——子帧弹栈时,其整段 transcript 天然折叠为一个返回值。这是最便宜也最重要的一层"分层压缩",默认免费获得("isolation over compression" 的结构性实现)。
- 需要跨帧共享状态时,不开后门,而是显式使用内核服务(Blackboard,见第 12 章)或跨 run 服务(Memory,见第 11 章),所有读写走信号,可审计。

### 2.4 Run(进程)

```python
RunConfig = {
    "model": "anthropic/claude-sonnet-4",   # 可被 Skill 的 model.prefer 覆盖
    "max_depth": 8,
    "max_steps": 200,           # 全 run 总步数
    "max_cost": 2.0,            # 美元
    "max_wall_time": 1800,
    "compression": "hierarchical",   # "off" = 消融档(裸模型基线)
    "tool_policy": ToolPolicy(max_permission=Permission.WRITE),
    "logic_policy": LogicPolicy(force_sandbox=False),  # True = 一切逻辑代码强制沙箱
    "seed": None, "temperature": None,                 # 复现性钉死(评测用)
}
```

Run 是预算、权限、信号的作用域边界;一个 Run 一棵帧树。**故障致死性分层**:provider/工具的单点重试上限不是长任务的熔断器(可配大),`max_steps`/`max_cost`/`max_wall_time` 才是;BudgetGuard 的 stop 可降级为 pause + 通知,由宿主决定加预算还是放弃。

---

## 3. 执行模型

### 3.1 内核 agent loop(单帧)

内核用 Python 的 await 链直接实现调用栈——**async 调用链即 Skill 调用栈**。父帧挂起 = await 点;子帧完成 = 协程返回。帧对象仍显式存在,供 sidecar 检视与 RunControl 操控;停止/暂停通过每步循环开头检查的控制标志(safe point)+ asyncio cancellation 传播实现。

```python
# kernel/runner.py(语义伪码,非最终实现)
async def run_frame(frame: SkillFrame, kernel: Kernel) -> Any:
    kernel.stack.push(frame)
    await kernel.signals.emit(FRAME_PUSHED, frame)

    if frame.skill.kind == "code":
        result = await kernel.logic.execute_for_frame(frame)   # 逻辑代码交 Logic Kernel(§9)
        return await pop(frame, result)

    while True:
        # 1. 同步 sidecar 检查点(safe point):可否决 / 注入 / 暂停 / 强停
        verdict = await kernel.sidecars.pre_step(frame)
        if verdict.stop:  return await pop_err(frame, Stopped(verdict.reason))
        # 2-3. 上下文全部交 Context 子系统(§7):压缩维护 + 组装请求(含状态注入)
        await kernel.context.maintain(frame)
        req = await kernel.context.build(frame)
        # 4. Provider 调用(内含重试、限流、记账)
        resp = await kernel.providers.chat(req)
        await kernel.signals.emit(LLM_RESPONSE, frame, resp)
        # 5. 终止判断:最终答案 → outputs 校验(格式)→ verifier(语义,可选)
        if resp.is_final:
            return await pop(frame, frame.skill.validate(resp))
        # 6. 分发调用:工具走 Tool Registry,skill.* 走内核子技能分发
        for call in resp.calls:
            try:
                if call.is_skill_invoke:
                    child = kernel.skills.make_frame(call, parent=frame)
                    out   = await run_frame(child, kernel)      # 压栈,父帧挂起
                else:
                    out   = await kernel.tools.dispatch(call, frame)
            except asyncio.CancelledError:
                # 中断配对修复:占位 tool_result 保证配对原子性(§7.4 不变量 2 在中断路径同样成立)
                out = ToolResult.placeholder(call, reason="interrupted")
                frame.context.append(call.pair(out))
                raise                                           # 取消继续传播
            frame.context.append(call.pair(out))                # 观察入上下文
        # 7. 记账与预算检查(超预算 → 抛 BudgetExceeded,沿栈上抛)
        kernel.account(frame, resp.usage)

async def pop(frame, result):
    # pre:frame.pop:弹栈前同步可否决——reviewer(sidecar 或 verifier 技能)可打回并注入"继续"
    verdict = await kernel.sidecars.emit(PRE_FRAME_POP, frame, result)
    if verdict.veto:
        frame.context.append(verdict.as_observation())
        return await run_frame_continue(frame)
    return kernel.stack.pop_ok(frame, result)
```

### 3.2 错误处理与恢复工程

- **工具失败** → 结构化错误观察写入帧上下文(类型/参数/`retryable`/修复建议),让 LLM 自行恢复;
- **Skill 失败**(步数耗尽、强停、校验失败 N 次)→ 作为错误结果交付父帧,父帧 LLM 可决定补救;
- **预算/强停** → 特殊异常 `RunAborted`,不可被单帧吞掉,一路弹栈到 Run 边界;
- **恢复环路语义**(防死亡螺旋,三条硬规则):
  1. **每条调 LLM 的恢复路径独立熔断**——summarize 压缩、输出修复循环、provider fallback 各有连败计数器与熔断上限(阈值可配,默认 3);熔断后该路径退化到更便宜的下一档(summarize → truncate);
  2. **内核错误路径不调 LLM**——内核自身的错误处理代码不允许引入新的模型调用;
  3. **递归深度计数**——恢复触发的恢复有深度上限,超限即帧失败上抛。
- 每帧可配 `retry`(整体重跑该帧,幂等性由调用方保证)。不做内建事务/补偿机制。

### 3.3 子技能调用的呈现方式

子技能对父帧 LLM 暴露为**伪工具** `skill__<name>`,schema 即该 Skill 的 `inputs`。内核在分发阶段拦截这类调用,转交 Skill 子系统压栈执行。

好处:对模型是带类型的函数签名;对内核是统一的拦截点;对清单是静态可分析的依赖图。

### 3.4 并发(显式 fork/join/spawn)

三种原语,帧树是唯一数据结构:

1. **串行调用**(默认):父帧 await 子帧。
2. **`parallel_invoke([...])`** fork/join 扇出:`asyncio.gather` 语义 + 四条规则——
   - 每分支独立帧、独立预算记账;
   - **批内故障隔离**:某分支失败只级联中止声明了依赖的同批分支,不影响独立分支与父帧;
   - **first-success 模式**(可选):首个成功分支锁定结果,级联取消其余,等 ack 或超时后**幂等结算一次**(只结算一次,竞态安全);
   - `max_concurrency` 上限;工具须声明 `concurrency_safe` 才允许批内并发执行(默认否,fail-safe)。
3. **`spawn(skill, input)`** 后台帧:父帧**不挂起**,子帧独立预算后台运行;父子经 Blackboard 交换滚动状态(见第 12 章);join 退化为读终态。配套校验 hook:**父帧读到子帧终态为完成前,拒绝输出"已完成"类结论**("不说 done" 铁律)。实时/快慢解耦场景(前台保场、后台深想)的统一表达。

协程级并行的限制注明:并行分支共享同一事件循环,CPU 密集的 code 技能应放 Logic Kernel 沙箱进程,避免阻塞兄弟分支。

---

## 4. 子系统设计(一):Providers

**职责**:把"一次 LLM 对话请求"翻译成各家 API,归一化回包;屏蔽一切厂商差异。

### 4.1 接口契约

```python
# api/v1/providers.py
class Provider(Protocol):
    name: str
    def capabilities(self) -> ProviderCaps: ...
    async def chat(self, req: ChatRequest) -> ChatResponse: ...
    async def stream(self, req: ChatRequest) -> AsyncIterator[ChatChunk]: ...

ProviderCaps = {
    "supports_tools": bool, "supports_vision": bool, "supports_json_mode": bool,
    "supports_streaming": bool, "supports_logprobs": bool,   # logprobs:蒸馏/拒绝采样预留
    "supports_realtime": bool,                               # 全双工/Omni 类模型
    "cot_protocol": "none" | "reasoning_content" | "signed_thinking",  # 思维链回传协议
    "cache_billing": bool,            # 是否报告 cache_read/write 计费
    "max_context_tokens": int,
    "token_counter": Callable | None, # 可选精确 tokenizer
}

ChatRequest  = { model, messages, tools: list[ToolSchema], temperature,
                 max_tokens, response_format, extra }
ChatResponse = { message,              # Message.reasoning 原样保留,下次请求逐字回传
                 finish_reason,
                 usage{prompt, completion, cache_read, cache_write, thinking, cost},
                 ttft_ms, total_ms,
                 raw }

Message = { role, content, tool_calls, tool_call_id, name,
            reasoning,                 # 思维链/签名 thinking block,归一化不丢字段
            source,                    # 来源标注:system | parent_input | tool_result | injected | external
            meta }

ProviderError = { kind: RATE_LIMIT | CONTEXT_OVERFLOW | AUTH | UNAVAILABLE | INVALID,
                  retryable: bool, retry_after: float | None }
```

### 4.2 ProviderManager(内核侧门面)

- **模型路由**:`"anthropic/claude-sonnet-4"` → provider 前缀路由;Skill 的 `model.prefer` 列表 → 按序探测能力匹配;预留 `ModelRouter` 协议扩展点(输入任务特征,输出模型与参数),v1 以静态 prefer 为默认实现;
- **弹性**:指数退避重试(仅 `retryable`)、每 provider 令牌桶限流(容量口径按排队论,利用率不顶过 80%)、连接超时 + **流式停滞 idle watchdog**(N 秒无新 chunk 判定停滞 → 杀流重试,独立于建连超时)、fallback 链(M5;切换前剥离前一家专有格式块,只保留契约层消息字段);
- **记账**:usage 细分(cache_read/cache_write/thinking/ttft)归一化后入帧、入 run,并发 `LLM_RESPONSE` 信号;token 口径覆盖多模态(图像/音频 token 取自 provider usage 或按分辨率公式);
- **token 估算**:优先用 provider 精确 tokenizer,否则用统一估算器(Context 子系统依赖同一估算,口径唯一)。

### 4.3 扩展点

新 provider 通过 entry point `agent_os.providers` 注册,无需改内核。Anthropic 原生适配器紧随其后。实时/全双工模型作为 Provider 接入,`extra` 预留"内部事件透传"字段(话轮决策事件转发到信号总线);内核不实现话轮管理。

### 4.4 基础实现:`OpenAICompatibleProvider` + `MockProvider`

**`OpenAICompatibleProvider`**(M1,唯一必须的真适配器):一个类覆盖所有 OpenAI 兼容端点(官方/Azure/vLLM/Ollama/网关);依赖仅 `httpx`;错误映射 429→`RATE_LIMIT`(读 `Retry-After`)、401/403→`AUTH`、400 且 context length→`CONTEXT_OVERFLOW`、5xx/超时→`UNAVAILABLE`;自身不重试;reasoning 字段 round-trip。

**`MockProvider`**(M0,测试基石):脚本化应答(静态 list 或 callable)、请求录制供 golden-file 断言、可注入故障(429/500/超时/流式停滞)。

**`ProviderManager` 基础版**(M1):前缀路由 + 指数退避(上限 3 次,长任务可配大)+ usage 记账发信号。fallback 链 M5。

---

## 5. 子系统设计(二):Sidecars

**职责**:被信号触发的监督者。观察运行、监督行为,必要时强行停止或控制上下文。类比:中断处理程序 + 看门狗 + seccomp。

### 5.1 信号总线与信号目录

内核在每个关键节点发信号。命名采用 `<阶段>:<事件>`,`pre:` 前缀表示**同步可否决**,`post:` 表示异步观察。

```
run.started / run.finished / run.aborted
pre:frame.push        post:frame.push        pre:frame.pop(弹栈前可否决)  post:frame.pop
pre:step              post:step              # 每帧每步一次,sidecar 的主检查点
pre:llm.request       post:llm.response      post:llm.chunk(流式,仅 ASYNC)
pre:tool.call         post:tool.call         # pre 可改参数/否决
pre:skill.invoke      post:skill.invoke
pre:logic.exec        post:logic.exec        # 逻辑代码执行,pre 可否决
pre:compress          post:compress
blackboard.publish    blackboard.write       # 黑板读写可审计(见第 12 章)
budget.warning (80%)  budget.exceeded
```

信号是 Telemetry(第 10 章)的持久化数据源;Telemetry 是总线的特权订阅者,不算 sidecar。

### 5.2 Sidecar 契约与控制通道

```python
# api/v1/sidecars.py
class Sidecar(Protocol):
    name: str
    subscriptions: list[SignalPattern]   # ["pre:tool.call", "budget.*"]
    mode: Mode                           # SYNC(在关键路径,可否决)| ASYNC(纯观察)
    priority: int                        # 多个 SYNC sidecar 的确定性裁决顺序
    needs_free_text: bool = False        # 输入最小化:默认只收结构化调用数据(见下)
    async def on_signal(self, sig: Signal, ctl: RunControl) -> Verdict: ...

Verdict = Allow | Modify(patch) | Veto(reason) | InjectMessage(frame_id, msg)
        | Pause(reason) | Stop(reason) | ForceCompress(frame_id)
```

- **输入最小化原则**:安全类 sidecar 默认只接收结构化调用数据(工具名/参数/skill ref/usage),**不接收主模型自由文本**——阻断注入经 rhetoric 通道影响审批;确需更多上下文的 sidecar 必须显式声明 `needs_free_text = True` 并在文档标注注入风险。
- **Veto 理由回写**:`Veto(reason)` 的 reason 作为该次调用的错误观察写入帧上下文(`retryable: false`),复用工具失败语义,让模型知道"为什么不行",而非只进 trace。

```python
class RunControl(Protocol):   # 内核暴露给 sidecar 的特权接口(仅此通道可操控运行)
    async def stop(self, run_id, reason): ...
    async def pause(self, run_id, reason): ...
    async def inject_message(self, frame_id, msg): ...
    async def force_compress(self, frame_id): ...
    async def get_frame_tree(self, run_id) -> FrameTree: ...   # sidecar(代码)→内核的 pull,不耗 token
    async def get_usage(self, run_id) -> Usage: ...
```

### 5.3 监督语义(可靠性关键)

- **SYNC sidecar** 在关键路径上执行,有超时(默认 2s);自身抛异常时**默认 fail-closed**(视为 Veto),可配置 fail-open;
- **ASYNC sidecar** 在监督任务里跑,异常只记日志,**永远不允许拖垮 run**;
- sidecar 由 supervisor 统一托管:注册、心跳、重启(仅 ASYNC)、关停;
- 引入 LLM 驱动的 sidecar 时:审批模型应与执行模型**不同家族、能力相近**;连续否决触发 rejection circuit breaker 回退人工。

### 5.4 内置 sidecar

| Sidecar | 订阅 | 行为 |
|---|---|---|
| BudgetGuard | `post:llm.response` | 累计成本/步数/时长,超限 → `ctl.stop()`(长任务宿主可配 stop→pause 降级) |
| LoopDetector | `post:step` | 重复调用模式(同工具同参 N 次)→ `inject_message` 带操作策略纠偏,再犯 → `stop` |
| StallDetector | `post:step` | 帧级静默超时(长期无进展)→ inject 纠偏 → stop |
| ToolGuard | `pre:tool.call` | 规则表(工具名/参数模式)→ `Veto`。**能力上限声明:正则/关键字对 shell 组合爆炸无效,`shell_exec` 的防护主体是沙箱(§9.2)+ 权限(§8.2);语义解析器作为后续替换实现预留** |
| CodeScanner | `pre:logic.exec` | 动态代码静态模式扫描(危险 import、可疑调用)→ `Veto` |
| HumanApproval | `pre:tool.call`(EXEC 级) | 挂起等待人工批准,超时走可配默认(如"超时拒绝");高风险可叠加模型审批 |

TraceRecorder 不在此列——它已升格为 Telemetry 子系统的 JSONL exporter(见第 10 章)。自定义 sidecar 经 entry point `agent_os.sidecars` 注册。

---

## 6. 子系统设计(三):Skill Registry

**职责**:Skill 的发现、校验、依赖解析、加载、热重载与**运行期写入**。类比动态链接器。

### 6.1 加载流水线

```
discover → parse manifest → validate(schema/权限引用存在/description lint)
         → resolve deps(版本约束求解,拓扑排序)
         → materialize(Skill 对象:prompt 体/代码入口 + 策略)
         → publish(命名空间可查)
```

- **来源**:本地目录(一个技能一个包:`skill.yaml` + `SKILL.md` / `handler.py`)、Python entry points、远程源(未来);
- **寻址**:`<namespace>:<name>@<semver>`,版本约束用 `^`/`~` 语义;
- **静态可分析**:依赖图在加载期即可完整构建——循环依赖在加载期报错(运行期递归则由 `max_depth` 兜底);
- **热重载**:文件监听 → 新版本加载 → 新帧用新版,在跑帧钉住旧版(类比 SO 库版本共存);
- **加载期权限闸门**:manifest 声明的工具/子技能必须存在且权限等级不超过 RunConfig 上限,否则拒绝加载并报出具体缺失项;
- **description lint**:描述应含触发条件与边界("Use when / Do not use when" + 负例),过短或无触发条件 → 警告不阻断。

### 6.2 运行期写入路径(自我进化供给侧)

```python
class SkillRegistry(Protocol):
    def get(self, ref: SkillRef) -> Skill: ...
    def visible_to(self, frame: SkillFrame) -> list[SkillSchema]: ...  # 生成伪工具 schema
    def make_frame(self, call: SkillCall, parent: SkillFrame) -> SkillFrame: ...
    async def register(self, artifact: SkillArtifact, provenance: Provenance) -> SkillRef: ...
```

- **`register()` 信任管线**:运行期生成的技能(Agent 自写)**默认不信任**——code 部分强制 SANDBOX 执行、经 CodeScanner 扫描、注册动作本身发信号并可被 HumanApproval 拦截、manifest 权限从严;
- **入库前验证门**:publish 前经重放(可用 MockProvider 脚本化)+ evaluator 确认任务真完成——"程序性记忆需要验证门,否则自我改进循环必然腐坏";
- v1 最小实现:Agent 经 `fs_write` 写技能包文件 + `reload()`,契约形状先行。

### 6.3 基础实现:`LocalFileSkillRegistry`(M2)

单 YAML 文件加载,一个文件声明全部技能:

```yaml
# skills.yaml
skills:
  - name: summarize_text
    version: 1.0.0
    kind: prompt
    description: 将长文本压缩为要点。Use when 输入超过一万字符;Do not use when 需要逐字保真。
    inputs:  { type: object, properties: { text: { type: string } }, required: [text] }
    outputs: { type: object, properties: { summary: { type: string } } }
    permissions: { tools: [], skills: [] }
    model: { prefer: ["openai/gpt-4o-mini"] }
    prompt: |
      你是摘要器。把以下文本压缩为不超过 5 条要点,输出 JSON {"summary": ...}:
      {text}
  - name: extract_entities
    version: 0.1.0
    kind: code
    handler: my_skills.handlers:extract_entities   # async def run(input, ctx)
    inputs: { type: object, properties: { text: { type: string } } }
```

要点:`yaml.safe_load` → manifest 校验 → materialize;prompt 技能 `str.format` 渲染;code 技能 dotted path importlib 加载并校验协程函数;命名空间固定 `local`;不做版本约束求解(单版本,依赖只查存在);依赖图拓扑排序保留;热重载 = 手动 `reload()`(mtime 检查);目录包形态作为后续 `DirectorySkillSource`,契约不变。

---

## 7. 子系统设计(四):Context(上下文)

**职责**:帧上下文的全权管理——**组装**(build request:指令 + 帧上下文 + 可见 schema + 状态注入 + 来源标注)、**压缩**(超限时按策略压缩)、**前缀缓存稳定性**维护。组装与压缩必须一家管:压缩破坏前缀缓存,组装维护前缀稳定,一体两面,分属两个子系统必然口径打架。

### 7.1 触发

- **软上限**(manifest `max_tokens` 或模型窗口 80%):压缩到目标水位;
- **硬上限**(临近模型窗口):激进压缩;仍超限则该帧失败上抛;
- **外部强制**:sidecar `ForceCompress`;
- `compression: "off"`(RunConfig 消融档):全部策略短路,用于裸模型基线对照。

### 7.2 策略(责任链,可组合)

| 策略 | 机制 | 代价 |
|---|---|---|
| `collapse_child` | 子帧弹栈时 transcript 不进入父帧,只留返回值(默认,结构性获得) | 无 |
| `spill` | 大工具输出移入 blob store,上下文只留 `{ref, preview}`;preview = 头部+尾部+省略通知(字节数、ref、取回方式),可选 LLM 生成"contextualized preview"(主体+时间+意图前缀);**替换串一经生成永久冻结** | 低,首选 |
| `truncate` | 原子组粒度丢弃最旧的非 pinned 消息;驱逐顺序可插拔(默认 FIFO,可按价值评分) | 低,会丢信息 |
| `narrate` | 多模态消息逐出前经 ProviderManager 生成一句文本旁白留置 | 一次廉价调用 |
| `summarize` | 经 ProviderManager 把被逐出区间摘要成 compact note,**context-aware**:压缩 prompt 含帧任务规格;保留契约 = 架构决策与关键约束(不可摘要)、已修改文件清单、验证状态、未完成 TODO、**标识符逐字保留**(UUID/hash/URL/文件名);摘要模型档位可配;连败熔断(默认 3 次)后退化 truncate | 一次 LLM 调用,有损 |
| `hierarchical` | 上述按序组合(spill → truncate → summarize),manifest 默认 | — |

### 7.3 状态注入(Status Bar)

每步组装时,内核记账数据(帧 `usage`、预算余量、步数、工具调用计数、当前时间)生成为 key-value 元消息追加到帧上下文末尾,**附操作策略**(如"预算剩余 20%,收敛到可直接交付的方案"——裸读数不改变行为,读数+策略才改变):

- 数据只允许来自内核观测,绝不来自工具内容(模型无条件信任状态栏,投毒即致命);
- 状态行按 ephemeral 处理:可被 truncate 丢弃、不进 pinned、不进静态前缀(保缓存);
- 短轨迹逐轮替换,长轨迹持久追加(完全保缓存,容忍过期累积),按轨迹长度选择;
- LoopDetector/BudgetGuard 的纠偏消息复用同一通道,必须带操作指令。

### 7.4 不变量(可测的硬约束)

1. `pinned` 消息永不移除;
2. **tool_call/tool_result 配对原子性**:同进同出,绝不产生孤儿 tool result——**在中断路径上同样成立**(占位 tool_result,见 §3.1);
3. 压缩后 token 估算严格下降;
4. 压缩动作发 `pre/post:compress` 信号,可记录、可否决;
5. **前缀稳定性**:`build` 输出跨步逐字节稳定——指令体与工具/子技能 schema 的顺序与序列化结果固定,动态信息只经末尾追加,spill 替换串冻结;压缩批量低频,非每轮。验收:golden-file 断言相邻两步请求前缀 diff 为空。

### 7.5 接口

```python
# api/v1/context.py
class ContextManager(Protocol):                       # 内核步骤 2/3 的调用点
    async def build(self, frame: SkillFrame) -> ChatRequest: ...
    async def maintain(self, frame: SkillFrame) -> None: ...   # 压缩 + 状态注入

class Compressor(Protocol):
    name: str
    async def compress(self, ctx: FrameContext, target_tokens: int,
                       svc: KernelServices) -> CompressionReport: ...

CompressionReport = { evicted, before_tokens, after_tokens, cache_invalidation_estimate, marker }
# marker = "[COMPRESSED]" 幂等标记,防责任链多策略重复处理
```

`KernelServices` 提供:token 估算器、ProviderManager(摘要用)、blob store。新策略经 entry point `agent_os.compressors` 注册。

### 7.6 基础实现:`RollingWindowCompressor` + 估算器(M3)

rolling window:保留 pinned + 最近若干原子组,超目标即从最旧整组驱逐。

```
1. atomic_groups(messages):assistant(带 tool_calls)与其全部 tool result 绑成原子组
   —— 不变量 2 由此结构性保证,而不是靠事后检查
2. 从最旧的非 pinned 组开始整组弹出,直到 token_estimate ≤ target
3. 仍超限(单个组过大)→ 对最早保留组的 content 做硬截断(字符级,标注 [truncated])
```

- 纯函数、无 LLM/blob 依赖,是不变量测试(hypothesis)的最佳载体;
- **定位警告**:裸 rolling window 是已知的循环诱因(丢早期工具结果 → 重复调用);它只是 `hierarchical` 链的中间层基座,链尾必须有 summarize 或 spill 承接,文档不得读作"推荐做法";
- token 估算器随此交付:char/4 粗估 + 按 provider 校准系数 + 多模态口径(图像按分辨率公式),接口预留精确 tokenizer。

---

## 8. 子系统设计(五):Tool Registry

**职责**:工具的注册、校验、鉴权、分发与结果归一化。类比 syscall 表 + seccomp + vfs。

### 8.1 分发流水线(每次调用的完整路径)

```
parse args → JSON Schema 校验(不合法 → 错误观察,不执行;fail fast,禁止"智能纠正")
→ 帧权限检查(调用帧 manifest.tools 白名单 + RunConfig.tool_policy 上限)
→ 凭证注入(按工具声明从凭证作用域取,不碰全局环境)
→ pre:tool.call 信号(SYNC sidecar 可 Veto/Modify)
→ 超时执行(内置重试仅限 idempotent: true)
→ 结果归一化:
    大小封顶 → 超限 spill 到 blob store,preview = head + tail + 省略通知(替换串冻结)
    调用计数注释("Tool call #3 for fetch_url")
    结构化错误四层(类型/参数/调用栈/修复建议)+ retryable 标志
    untrusted_source: true 的结果包裹来源标记("<external_content source=...>",注入防御)
→ post:tool.call 信号
→ 写入帧上下文
```

可选 hook:execute-validate-feedback(写代码文件后自动跑 linter,结构化错误并入返回值)——内置 sidecar 或工具包装器实现,不动流水线本体。

### 8.2 权限模型

- 工具声明权限等级 `READ < WRITE < NET < EXEC`;
- Skill manifest 声明工具白名单(同时继承了工具的等级);
- RunConfig 设全局上限。三层取交集,任一拒绝即拒绝。HumanApproval sidecar 可对高等级工具加人工闸门;
- 不可幂等工具(EXEC 级,发邮件/转账类)可声明 `confirm: true` 走两阶段:dry run 返回 confirmation token,凭 token 执行,失败回上层重新 pre-check 而非盲目重试。

### 8.3 来源与扩展

内置:`fs_read`(行区间 + 行号前缀)、`fs_write`、`fs_edit`(old_string→new_string 唯一匹配,否则报错)、`shell_exec`(EXEC,持久会话:run 作用域句柄,跨调用保持 cwd/env,哨兵判完成)、`http_fetch`(NET)、`blob_get`(offset/limit 分页)、`ask_user`/`notify_user`(User Communication 类,宿主注入回调)、`python_exec`(委托 Logic Kernel 沙箱,见 9.4);entry point `agent_os.tools` 注册第三方;MCP 适配器作为独立包后续接入——供应链清单:描述按不可信输入审查、版本锁定、同名工具 namespace 隔离、最小权限凭证。

### 8.4 基础实现:`LocalPythonToolRegistry`(M1)

**函数即工具**:decorator 注册,schema 从签名推导。

```python
registry = LocalPythonToolRegistry()

@registry.tool(permission=Permission.NET, timeout=30)
async def fetch_url(url: str, max_bytes: int = 100_000) -> str:
    """抓取 URL,返回文本内容。Use when 需要读取公开网页;Do not use when 需要登录态。"""
    ...

# 推导规则:str/int/float/bool → JSON 基本型;list[X]/dict[str, X] → array/object;
# 有默认值 → 非 required;docstring 首段 → description(倡导"何时用/边界/负例")
```

- sync 函数包 `asyncio.to_thread`,async 函数直接 await;
- 8.1 分发流水线**全量实现**(schema 校验、三层权限、凭证、信号、超时、归一化)——契约本体,不是可简化项;
- blob store 基础版:运行目录下的文件存储,ref 采用 `blob://<run_id>/<sha>` URI 形态(为跨 run 记忆层留命名空间)。

---

## 9. 子系统设计(六):Logic Kernel

**职责**:逻辑代码的唯一执行点。类比 CPU/ALU——内核负责调度与仲裁,但亲自不执行任何指令。两类代码都从这个咽喉点过:

1. **code 技能**(`kind: code` 的 Skill handler):作者编写、版本管理、加载期可静态审查;
2. **动态代码**:LLM 运行期生成,经内置工具 `python_exec` 提交。

把所有代码执行收敛到一个子系统,换来统一资源限额、统一信号(可审计、可否决)、统一隔离策略。

### 9.1 接口契约

```python
# api/v1/logic.py
class LogicKernel(Protocol):
    name: str
    trust: TrustLevel                     # TRUSTED | SANDBOX,实例自报隔离等级
    async def execute(self, req: ExecRequest) -> ExecResult: ...

ExecRequest = {
    "source":   str,                      # 源码文本,或已加载 code 技能的入口引用
    "language": "python",                 # 预留多语言路由
    "entry":    "run",                    # code 技能入口函数名
    "args":     dict,                     # 帧 input
    "ctx":      LogicContext | None,      # 仅 TRUSTED 模式注入(见 9.3)
    "limits":   ResourceLimits,           # wall_time / cpu_time / memory_mb / stdout_bytes
    "network":  False | NetworkPolicy,    # 开启需 NET 权限;可为目的级白名单
}

ExecResult = {
    "value":  Any | None,                 # 入口返回值(须可 JSON 序列化)
    "error":  { "kind": LogicError, "message": str, "traceback": str } | None,
              # LogicError: RUNTIME_ERROR | LIMIT_EXCEEDED | REJECTED
    "stdout": str, "stderr": str,         # 超限截断,全文 spill 至 blob store
    "usage":  { "cpu_ms": int, "mem_peak_mb": int, "wall_ms": int },
}
```

### 9.2 信任等级与执行模式

| 模式 | 实现 | 用于 | 隔离强度 |
|---|---|---|---|
| `InProcessLogicKernel` | 进程内 await 调用 + 协程超时 + 记账 | 可信 code 技能(默认)、调试 | 无,仅协程级超时 |
| `PythonSandboxLogicKernel` | 子进程 + `setrlimit`(CPU/内存/文件大小) + 临时只读工作目录 + 默认断网(目的级白名单可配) | LLM 动态代码(强制)、声明 `sandbox: true` 的 code 技能 | 进程级 |

选择规则:

- **动态代码永远走 SANDBOX**,无配置项可关闭;
- code 技能默认 TRUSTED,manifest 可声明 `logic: {mode: sandbox}` 主动提升隔离;
- `RunConfig.logic_policy.force_sandbox = True` 时一切逻辑代码强制沙箱(多租户宿主场景);
- 隔离阶梯(后端替换,契约不变):subprocess+rlimits(v1)→ OS 级(seccomp/nsjail)→ 容器 → microVM。警示:**venv 不是沙箱**(只隔离包依赖,文件系统/网络/进程全无约束)。

### 9.3 LogicContext 与沙箱回调通道

TRUSTED 模式下,code 技能与 prompt 技能有同等的组合能力,只是用代码表达:

```python
class LogicContext(Protocol):
    frame_id: str
    async def invoke(self, skill: str, input: dict) -> Any: ...   # 调子技能 → 压栈
    async def call_tool(self, tool: str, args: dict) -> Any: ...  # 调工具 → 走 Tool Registry
    blob: BlobStore
    log: Logger
```

`invoke` / `call_tool` 全部回到内核分发路径:manifest 白名单检查、信号、记账一样不少。code 技能因此可以做"编排者":确定性控制流 + 按需调 LLM 技能。

SANDBOX 回调通道(设计方向,M6 目标):沙箱内经 **JSON-RPC 代理**获得受限 `LogicContext`——编排脚本(code orchestration)的中间变量留在执行环境,只回传最终结果(token 消耗可降约两个数量级)。约束不变:所有回调回到内核分发路径,白名单、信号、记账一样不少;回调面越大隔离价值越稀释,故通道只暴露 `invoke`/`call_tool` 两个方法。

### 9.4 两个调用方

- **内核 runner**:`kind == "code"` 的帧构造 `ExecRequest` 交 Logic Kernel(见 3.1);
- **`python_exec` 内置工具**:LLM 的"代码解释器"。薄壳——校验参数后委托 SANDBOX 实例执行,结果走 8.1 归一化流水线。`permission: EXEC`,受三层权限模型与 HumanApproval 闸门约束。

### 9.5 信号与监督

- `pre:logic.exec`(SYNC 可否决):CodeScanner 静态模式扫描(危险 import、`ctypes`、可疑网络调用),命中 → `Veto`;
- `post:logic.exec`:cpu/mem 用量入帧 usage,Telemetry 落盘(含源码哈希,供审计);
- 超限(时间/内存)→ `LIMIT_EXCEEDED`,结构化错误上抛,父帧 LLM 可补救;
- sidecar `Stop` 对运行中的沙箱 = 杀进程组;对 TRUSTED = 取消协程。

### 9.6 扩展点

entry point `agent_os.logic_kernels`:`JSLogicKernel`、`WasmLogicKernel`、`RemoteLogicKernel`(gRPC 远端执行,服务化场景的天然形态)。`language` 字段路由到对应后端;同语言多后端按信任等级择优。

### 9.7 基础实现:`InProcessLogicKernel`(M2)

- `trust = TRUSTED`;进程内 `await asyncio.wait_for(handler(args, ctx), wall_time)`;
- stdout/stderr 捕获:`contextlib.redirect_stdout/stderr` 到 StringIO(协程内有效);
- 返回值 JSON 序列化检查(`json.dumps` 试探),不可序列化 → `RUNTIME_ERROR`;
- usage:`wall_ms`(perf_counter)+ `cpu_ms`(process_time 差值);`mem_peak_mb` 不支持记 0;
- 错误:traceback 格式化入 `error`;超时 → `LIMIT_EXCEEDED`;
- 明确不做:内存限额、隔离、网络管控——那是 `PythonSandboxLogicKernel`(M5)存在的意义。

---

## 10. 子系统设计(七):Telemetry(可观测与轨迹)

**职责**:信号流的**可持久化**汇聚点。类比 journald / auditd + 飞行记录仪。

**为什么不是 sidecar**:sidecar 语义是"ASYNC 异常只记日志,永不拖垮 run"(fire-and-forget);而 WAL/检查点要求**保证落盘**的 durability 语义,检查点恢复把它放在可靠性关键路径上。它有独立契约与导出管线,故为独立子系统。

### 10.1 契约

```python
# api/v1/telemetry.py
class TelemetrySink(Protocol):
    async def record(self, sig: Signal) -> None: ...      # 内核不等待 IO(内部队列)
    async def flush(self) -> None: ...
    async def snapshot(self, run_id: str) -> Checkpoint: ...   # 检查点快照
    # exporters 注册制:JSONL(baseline)/ OTLP / RL-trajectory
```

### 10.2 设计要点

- **WAL 原则**:帧 transcript 实时 append-only 落盘 = 任何时刻持有完整检查点;"trajectory 是 Agent 的全部状态",恢复 = 重放轨迹 + 静态前缀;
- **schema 版本化**:JSONL 行格式为带版本头的稳定契约,字段足以重建完整 tool-calling 过程;
- **OTLP / OpenInference 映射**:帧树 ≡ span 树,LLM/工具/子技能调用 = 子 span,映射是机械工作,换来分析生态(LangSmith/Phoenix)与"生产 trace 回流评测资产"通道;
- **训练就绪导出**:记录**压缩前**的请求级原始报文与消息 provenance(assistant/tool_result/system,支撑 loss masking)、帧树结构——内核是全系统唯一能看到完整轨迹(含子帧折叠前 transcript)的位置;导出目标 = SFT/RL rollout 数据;
- **PII 脱敏 hook**:落盘前可插拔清洗(默认关闭;regex 快筛 + 本地小模型深扫的混合方案),供合规敏感宿主启用;
- **MetricsCollector**:在线汇聚过程指标(action legality rate、path efficiency、回溯频率),信号流信息已足;
- **纪律**:golden-file 测试集标注"评估专用"(训练/评估数据严格隔离);
- **baseline**(M5):`JsonlTelemetrySink`(`traces/<run_id>.jsonl`,版本头 + 队列批量落盘)。

---

## 11. 子系统设计(八):Memory(记忆与知识)

**职责**:跨 run 持久记忆与知识的**契约层**。类比文件系统/持久存储。

**与非目标的关系**:内核不实现检索算法(embedding/ANN/RAPTOR/GraphRAG 等)——那是第三方的事;`api/v1` 定义 `MemoryService` 契约并附极简 baseline,避免第三方各自发明不兼容形态。

### 11.1 契约

```python
# api/v1/memory.py
class MemoryService(Protocol):
    # 读写分离:读 = 检索(经工具调用暴露);写 = 离线蒸馏或运行期经验追加
    async def search(self, query: str, k: int, principal: Principal) -> list[MemoryEntry]: ...
    async def write(self, entry: MemoryEntry, provenance: Provenance) -> EntryRef: ...
    async def evict(self, ref: EntryRef, reason: str) -> None: ...

MemoryEntry = {
    "content": ..., "tags": [...],
    "source": {...},          # 强制 source tagging:来源 run_id / 任务 / 内容来源
    "created_at": ..., "freshness": {...},   # 新鲜度一等属性:失效条件/TTL
    "trust": "experience",    # 经验条目不具指令效力
}
```

### 11.2 设计要点

- **检索层权限过滤**:数据离开存储层之前按 `principal` 过滤——未授权内容绝不进入上下文(注入后再审查不可靠);
- **通道隔离**(防经验投毒):检索结果经 Context 组装时以独立"参考资料"角色注入,显式声明无指令效力;注入作用域按帧/manifest 声明,不做"一处注入全局生效";写入前审查 + 可溯源 + 可驱逐是安全底线,随契约内建;
- **存储区域语义**(VFS 四区):私有 scratchpad(帧工作目录,随 run 销毁)/ 共享 workspace(任务级持久,需并发控制)/ 外部挂载(受外部权限约束,读为主)/ 内置只读(技能包);
- **常驻层**:高价值结构化事实经 `pinned` 注入帧上下文("overview 常驻 + details 按需");
- **写路径范式**:离线 extract–compare–decide(ADD/UPDATE/DELETE/NOOP),或蒸馏 sidecar(订阅 `run.finished`,触发条件满足时廉价模型蒸馏经验写入);
- **baseline**(M6):`LocalFileMemoryService`——Markdown 文件 + frontmatter(tags/created/freshness)+ 检索工具(grep/BM25 即可),即 `MEMORY.md` 路线:可人读人改、保序、Git 可版本化。

---

## 12. 子系统设计(九):Blackboard(共享状态与帧间通信)

**职责**:run 作用域内的帧间共享状态与异步消息。类比共享内存 + 消息队列。**正面回答原开放问题 4**(跨帧共享的权限模型如何与 manifest 白名单统一):权限并入 manifest 声明(`permissions.blackboard: [ns]`),内核在读写时逐次仲裁。

### 12.1 契约

```python
# api/v1/blackboard.py
Envelope = { sender: frame_id, target: frame_id | "*", type: str, payload: dict, ts }
# type 约定:status_update / query / result / terminate …

class Blackboard(Protocol):
    async def publish(self, env: Envelope) -> None: ...            # 帧→帧寻址消息
    async def subscribe(self, frame_id: str, pattern: str) -> AsyncIterator[Envelope]: ...
    async def put(self, ns: str, key: str, value: Any,
                  cas_version: int | None = None) -> int: ...      # CAS 乐观锁
    async def get(self, ns: str, key: str) -> tuple[Any, int]: ... # (value, version)
```

### 12.2 设计要点

- **与信号总线的边界**:信号 = 内核生命周期事件(广播、生命周期语义,kernel IPC);黑板消息 = 帧↔帧寻址通信 + KV 状态(应用语义)。帧发消息须经内核寻址与权限检查(目标帧存在、manifest 允许),存储在 Blackboard——与 tool dispatch 同构:仲裁在内核,执行在子系统;
- **与 Memory 的边界**:Blackboard 易失、run 作用域(帧树内通信);Memory 持久、跨 run;
- **StatusBoard 模式**:子帧每步把进度摘要写入约定命名空间(append-only + 最新值视图),父帧按需读取——后台帧(§3.4 spawn)的滚动状态通道,是"前台保场、后台深想"拓扑的承重墙;
- **并发控制**:`put` 带 CAS 版本号(乐观锁:读记版本、写时校验、失败重读重做)、read-before-write 约束内建;worktree 式隔离(分支命名空间 + 合并点)作为后续增强;
- **读写走信号**(`blackboard.publish` / `blackboard.write`),可审计、可被 sidecar 否决;
- **baseline**(M5):`LocalBlackboard`——进程内 dict + asyncio.Queue,单 run 作用域。

---

## 13. 一次完整调用的生命周期(子系统协同时序)

```
用户 → Kernel.run("web_research", {question: ...})
 1. Skill Registry 解析根技能,构建根帧               [skills]
 2. run.started;Telemetry 开始 WAL 落盘              [telemetry]
 3. pre:step → BudgetGuard/LoopDetector 放行         [sidecars]
 4. Context.maintain:帧上下文 28k/32k → 不压缩       [context]
 5. Context.build:组装请求(指令 + 输入 + 状态栏注入 + fetch_url/search/skill__summarize_text schema,
    前缀逐字节稳定)                                   [context]
 6. ProviderManager.chat → Anthropic;usage 细分记账   [providers]
 7. 模型调用 web_search → Tool Registry 分发          [tools]
    pre:tool.call → ToolGuard 放行 → 执行 → 结果 12k → 归一化(计数注释 + head/tail 预览)
 8. 模型调用 skill__summarize_text → 内核拦截         [skills]
    子帧压栈 → 子帧 loop(步骤 3-7 递归)→ 返回摘要 → pre:frame.pop 放行 → 子帧弹栈
    子帧 transcript 折叠为一个返回值,父帧上下文 +200 token
 9. 帧上下文 33k > 32k 软上限 → spill 大网页(替换串冻结)+ summarize 旧段 → 24k  [context]
10. 模型给出报告 → outputs 校验 → verifier(可选)→ pre:frame.pop → 弹栈
11. run.finished;Telemetry flush;usage 汇总返回调用方
```

注:若某次调用是 code 技能或 `python_exec` 工具,则在第 7/8 步的分发阶段进入 Logic Kernel:`pre:logic.exec` 信号(CodeScanner 可否决)→ 按信任等级就地或沙箱执行 → 结果归一化后写回帧上下文(见第 9 章)。若子帧以 `spawn` 后台运行,则第 8 步不阻塞父帧,父子经 Blackboard 交换滚动状态(见第 12 章)。

---

## 14. 接口治理与扩展性

### 14.1 契约层(api/v1)

所有跨边界类型与 Protocol 集中在 `agent_os/api/v1/`:`messages`、`frames`、`providers`、`tools`、`skills`、`sidecars`、`signals`、`context`、`logic`、`telemetry`、`memory`、`blackboard`、`run`、`control`。**内核与各子系统只依赖契约层**;契约层的破坏性变更只允许跨大版本。

**v1 契约冻结清单**(冻结前必须进 `api/v1`,哪怕只进字段不实现):Usage 细分字段;Message.reasoning 与 source;ToolContext.principal 与 credentials;ToolSpec 全部预留字段(examples/cacheable/confirm/concurrency_safe/cost/depends_on/conflicts_with/untrusted_source);结构化 ToolResult(含 retryable);manifest verifier 槽位;`register()` 签名;Envelope;`MemoryService` / `Blackboard` / `TelemetrySink` 三个 Protocol 骨架;`pre:frame.pop` 信号名;`compression: "off"` 档。

### 14.2 组装

```python
kernel = (KernelBuilder(config)
          .providers(OpenAICompatibleProvider(base_url=..., api_key=...))
          .tools(LocalPythonToolRegistry.with_builtins())
          .skills(LocalFileSkillRegistry("./skills.yaml"))
          .context(ContextManager.default(RollingWindowCompressor()))
          .logic_kernels(InProcessLogicKernel(), PythonSandboxLogicKernel())
          .sidecars(BudgetGuard(max_cost=2.0), LoopDetector())
          .telemetry(JsonlTelemetrySink("./traces"))
          .memory(LocalFileMemoryService("./memory"))
          .blackboard(LocalBlackboard())
          .build())
result = await kernel.run("web_research", {"question": "..."})
```

配置文件(TOML)可等价完成上述组装,供非代码宿主使用。**消融测试**:不挂任何子系统(仅 MockProvider + 空工具表)时裸 loop 仍能跑通——微内核纯粹性的验收。

### 14.3 扩展点总览

| 扩展 | 机制 | entry point |
|---|---|---|
| 新 LLM | 实现 `Provider` | `agent_os.providers` |
| 新工具 | 实现 `Tool` | `agent_os.tools` |
| 新技能 | 目录包 / entry point / `register()` | `agent_os.skills` |
| 新 sidecar | 实现 `Sidecar` | `agent_os.sidecars` |
| 新压缩策略 | 实现 `Compressor` | `agent_os.compressors` |
| 新逻辑执行后端(JS/Wasm/远程) | 实现 `LogicKernel` | `agent_os.logic_kernels` |
| 新遥测导出 | 实现 Telemetry exporter | `agent_os.telemetry` |
| 新记忆服务 | 实现 `MemoryService` | `agent_os.memory` |
| 新黑板后端 | 实现 `Blackboard` | `agent_os.blackboard` |
| 服务化(HTTP/RPC 壳;A2A 为协议候选) | 薄层包装 KernelBuilder API,不进内核 | — |

---

## 15. 项目结构

代码库位于 `agent_os/` 子文件夹(Python 项目根,src 布局):

```
agent_os/                  # 工作区(DESIGN.md / reports/ / ai-agent-book/)
└── agent_os/              # 代码库根
    ├── pyproject.toml
    ├── src/agent_os/
    │   ├── api/v1/            # 契约层:全部 Protocol 与数据模型(唯一跨边界依赖)
    │   ├── kernel/            # runner / stack / dispatch / signals(bus)/ control / run
    │   ├── context/           # ContextManager + estimator + rolling_window(M3);spill/summarize/narrate 后续
    │   ├── providers/         # manager + openai_compatible.py(M1)+ mock.py(M0)
    │   ├── tools/             # local_registry.py(decorator+schema 推导)+ builtins + blob store
    │   ├── skills/            # local_file.py(skills.yaml 加载)+ manifest / loader
    │   ├── sidecars/          # supervisor + budget / loop / stall / guard / scanner / approval
    │   ├── logic/             # inprocess.py(M2)/ python_sandbox.py(M5)/ limits.py
    │   ├── telemetry/         # sink + jsonl_exporter(M5);OTLP/RL 导出后续
    │   ├── memory/            # local_file.py(M6)
    │   ├── blackboard/        # local.py(M5)
    │   └── runtime/           # KernelBuilder / config / 组装
    ├── skills/                # 示例:skills.yaml(web_research, summarize_text)
    ├── tests/
    └── examples/
```

---

## 16. 项目计划

### 里程碑(单人全职口径,可并行压缩)

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M0 地基**(1 周) | api/v1 契约层(含 §14.1 冻结清单全部字段)、InProcessSignalBus、配置、MockProvider、单帧 runner | "echo agent":无工具的 prompt 技能跑通一轮对话;消融测试(裸组装)通过 |
| **M1 工具与模型**(1 周) | LocalPythonToolRegistry + 4 个内置工具、OpenAICompatibleProvider、Manager 重试/限流/记账(usage 细分) | 单技能 agent 用工具完成真实任务;429 注入下自动恢复 |
| **M2 技能与调用栈**(1 周) | LocalFileSkillRegistry(单 YAML 加载)、压栈/挂起/返回、深度与预算限制、InProcessLogicKernel | 技能互调嵌套运行;code 技能经 `LogicContext.invoke` 编排 LLM 技能;循环依赖加载期报错;深度超限正确上抛 |
| **M3 Context**(1 周) | ContextManager(组装 + 状态注入 + 前缀稳定性)、token 估算器、RollingWindowCompressor、blob store、不变量测试 | 10 万 token 对话压缩后配对不变量零破坏;golden-file 断言相邻步前缀 diff 为空 |
| **M4 sidecar 与信号**(1 周) | supervisor、5 个基础 sidecar(budget/loop/stall/guard)、RunControl、`pre:frame.pop` | 预算超限强停;循环检测先纠偏后强停;veto 生效且理由回写;reviewer 打回弹栈生效 |
| **M5 可靠性**(持续) | **中断配对修复 + 恢复熔断(P0 洞)**、Telemetry(WAL + 版本头 + 检查点恢复)、Blackboard + spawn 后台帧、stream idle watchdog、`fs_edit`、PythonSandboxLogicKernel + python_exec + CodeScanner、OTLP 导出 | 断电恢复演示;中断注入下配对不变量零破坏;沙箱内资源滥用被限制并正确报错;spawn 快慢模式演示 |
| **M6 演化**(可选,持续) | Memory 契约 + LocalFile baseline、register() 写入路径 + 验证门、沙箱回调通道、spill/summarize/narrate 高级策略、蒸馏 sidecar | 经验跨 run 复用演示;Agent 自写技能经验证门注册并复用 |

每个里程碑交付恰是对应子系统的 baseline;高级形态(版本求解、fallback 链、hierarchical 压缩、目录包技能源、OTLP)都在 baseline 跑通后以"替换注册项"的方式进入,不动契约。

**契约冻结点**:v1.0 前过 §14.1 清单,字段全进 `api/v1`(可无实现)。

### 测试策略

- **MockProvider 脚本化应答**:所有 loop 测试不碰真实 API;golden-file 比对消息序列(含相邻步前缀 diff 断言);
- **压缩不变量**:property-based 测试(hypothesis),随机消息序列上验证 7.4 全部不变量;
- **故障注入**:provider 429/500/流式停滞、sidecar 超时与抛异常、工具超时、沙箱超限、**中断随机注入**(验证占位配对)——验证 fail-closed 与恢复路径;
- **集成锚点**:`web_research` 示例技能 + 真实 API 的夜间冒烟;评测三件套(judge/user-simulator/多源评审)以示例技能交付,不进内核;
- 统计显著性 eval harness(n 次运行、种子钉死、均值离散度、McNemar)放内核外 `tests/` 或独立工具包。

### 风险与对策

| 风险 | 对策 |
|---|---|
| token 估算与厂商口径不一致导致窗口溢出 | 统一估算器 + 按 provider 校准系数 + 硬上限留 10% 余量 |
| prompt 技能输出不合 schema | 校验失败 → 错误观察回写,最多 N 次修复循环后判帧失败 |
| SYNC sidecar 拖慢关键路径 | 每 sidecar 独立超时;压测预算内才可注册 SYNC |
| 沙箱逃逸或资源滥用 | 动态代码强制 SANDBOX:默认断网 + rlimits + 临时只读工作目录;容器级隔离以后端替换接入,契约不变 |
| 恢复路径级联失败(死亡螺旋) | §3.2 三条硬规则:每路径独立熔断、内核错误路径不调 LLM、递归深度计数 |
| ToolGuard 正则对 shell 组合爆炸无效 | 能力上限写入 §5.4;防护主体回归沙箱 + 权限;语义解析器预留 |
| 前缀缓存被动态内容破坏 | 不变量 5 + golden-file 前缀 diff 断言;状态注入只走末尾追加 |
| 检查点格式演进破坏兼容 | 事件日志 append-only + 版本头;快照可重建、可丢弃 |

---

## 17. 非目标与开放问题

**非目标(v1)**:多机分布式执行、多租户隔离、UI、MCP 双向宿主(仅工具侧适配)、检索引擎实现(embedding/ANN 等,Memory 只有契约与文件 baseline)、训练算法与教师 logit 蒸馏、有状态可重置评测环境与 RLVR 仿真、容器/微虚拟机级沙箱(v1 用子进程沙箱,重隔离以 LogicKernel 后端形式预留)、话轮管理与打断(实时模型作为 Provider 接入)。

**开放问题**(设计预留,不阻塞 M0-M4):

1. 并行分支的预算如何切分(均分 vs 按需抢占)?
2. 压缩保真基准落地:固定任务集 + judge 技能 + 配对显著性检验(方法论已备,随 M6 的 summarize 验收)?
3. 检查点恢复与热重载叠加时,在跑帧钉住旧版技能的序列化形态?
4. 外部事件唤醒入口:事件源 → 信号总线 → 挂起/未启动 run 的唤醒路径(含 `initiate_X` 异步工具命名约定与事件批处理)?
5. 技能可见集膨胀后(运行期注册大量产物),`visible_to` 是否需要语义检索层?
6. 是否支持 FrameContext 继承/克隆(共享上下文角色链,"超窗口 50% 则不共享"判据)?
7. 帧树粒度天然适配 turn-level credit assignment——若未来做 agent RL,记账与轨迹导出如何对接?
