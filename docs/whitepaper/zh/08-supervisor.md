# Supervisor:裁决路由与人机闭环

> 章次:08 · 状态:已实现(S1 内核机制 / S2 宿主通道 / S3 策略完备;个别文档表述与实现有出入,文中以代码为准并加注) · 依据:`docs/SUPERVISOR.md`(v2)、`agent_os/src/agent_os/supervisor/{manager,inbox}.py`、`agent_os/src/agent_os/api/v1/supervisor.py`、`agent_os/src/agent_os/kernel/runner.py`

## 1. 概述

supervisor 是 Agent OS 中面向 **agent 调用方**的裁决路由子系统:任何帧都可以通过
`ask_supervisor` 伪工具请求"上级"裁决,问题被路由出 agent,交给启动本 run 的宿主
(Web 用户、CLI 用户、嵌入方应用,乃至另一个 agent),提问帧就地挂起,直到答案作为
tool result 写回、帧重入 loop。在整体架构中,它是微内核外置的九个下层子系统之一
(见执行摘要 §3.1 分层总览),与 sidecar 并列承担"监督":sidecar 是规则化监督
(确定性、fail-closed),supervisor 是决策路由(权威在 agent 之外)。内核升权确认
(escalation,见第 07 章)复用同一闭环,是它最重要的消费者。

## 2. 动机与背景(原因)

**ask_human 为什么不够。** STDLIB W5 曾设想一条"挂起 run → 人回答 → 恢复"的
`ask_human` 通道,它有两个结构性局限(`docs/SUPERVISOR.md` §1.1):上级被锁定为
"人",无法表达"调用我的那个程序";实现被误读为 **run 级**挂起,而真正常用的粒度
是**帧级**——只有提问的帧该等答案,兄弟分支不应被冻结。

**caller 语义的 v1 → v2 修正。** v1 把默认上级设计为"父帧的 LLM",在调用栈内部
做 YIELD 让渡;v2 经指正后把 caller 重新定义为**整个 agent 的调用方**(启动本 run
的宿主),删除 YIELD 机制,语义大幅简化(§2.2 详述)。这个修正的代价与收益同样
明显:内核里不再有权威链,只有一次"出参"路由;嵌套 agent 的级联完全留给调用方。

**为什么不放在内核里。** 按微内核判据(执行摘要 §2.2),内核只保留三件事中属于
流控制的部分:帧的就地挂起/恢复机制、pending ask 的配对、run 完成判定——没有它们
loop 无法推进。而"让渡给谁"(调用方通道)、"等多久、超时怎么办"(超时与兜底
策略)是策略,全部外置到 supervisor 子系统(`docs/SUPERVISOR.md` §1.3)。类比:
内核提供"帧可以让渡执行"的机制,子系统决定"让渡给谁"。这一边界使 Web 收件箱、
CLI 协议、嵌入方 handler 三条通道可以独立演化,内核代码一行不动。

## 3. 问题陈述(解决的问题)

1. **无"问上级"原语**:报销审批技能遇到超自动上限的 ¥5000 订单,要么在 prompt
   里硬编码金额阈值,要么放任 LLM 自行决定——两者都不可审计。需要一行例子:
   `ask_supervisor({"question": "批准 ¥5000 报销吗?", "options": ["approve","reject"]})`。
2. **run 级挂起的过度冻结**:后台 spawn 分支提问时,主栈仍在推进;若挂起以 run
   为粒度,一次提问冻结整棵帧树。
3. **等待窗口的断电**:问题已发出、答案未回时进程崩溃,resume 后这条
   `ask_supervisor` 调用没有 tool result——若按通用"分发到一半断电"规则补
   interrupted 占位,帧会收到一个从未发生的"失败"。
4. **嵌套 agent 的权威链**:子 agent 的上级是调用它的另一个 agent,后者可能还要
   继续上报;若把级数建模进内核,内核就要理解任意深度的外部组织结构。
5. **答非所问**:调用方回答 "maybe" 而 options 是 `["approve","reject"]`——
   格式错误不能让子帧崩溃,也不能静默接受。

## 4. 设计与机制(解决的方法)

### 4.1 契约:Question / Answer / SupervisorHandler

契约冻结在 `api/v1/supervisor.py`:`Question`(:55-73)携带
question_id/question/context/options/urgency/frame_id/run_id,以及 additive 字段
`kind`("question" 为 LLM 主动提问,"escalation" 为内核判定的升权确认,
`docs/ESCALATION.md` §3);`Answer`(:76-80)是 `{answer, decided_by}` 的
TypedDict;`SupervisorHandler`(:83-87)是 `async (Question) -> dict` 的 Protocol。
`ask_supervisor` 是**内核拦截式伪工具**(`api/v1/supervisor.py:22-24`):与
`python_orchestrate` 同理,仲裁需要绑定调用帧与 manifest,故不进 Tool Registry;
manifest 以 `permissions.tools: [ask_supervisor]` 声明,声明即授权。LLM 可见的
JSON Schema(:29-52)仅在"manifest 已声明且内核装了 supervisor 通道"时由
ContextManager 追加进工具面(`runtime/builder.py:208-210`)——没有通道时不展示,
避免 LLM 调用一个注定失败的工具。

### 4.2 闭环:suspend → route-out → answer → resume

```
提问帧                    内核 runner              SupervisorManager        调用方通道
  │ ask_supervisor(args)    │                          │                      │
  ├────────────────────────▶│ ① 白名单检查(:554-562)   │                      │
  │                         │ ② pending 落盘 working   │                      │
  │                         │   ["_pending_ask"]       │                      │
  │                         │   (:624-630)             │                      │
  │                         ├─ supervisor.ask ────────▶│ ③ supervisor.ask 信号 │
  │                         │  (await,就地挂起)         ├─ handler(Question) ─▶│ ④ 收件箱/CLI/handler
  │                         │                          │   asyncio.wait_for    │   (超时 timeout_s)
  │                         │                          │◀── {answer,decided_by}─┤ ⑤ options 校验,
  │                         │                          │ ⑥ supervisor.answer   │   不合带 previous_error
  │                         │◀── {"answer","decided_by"}┤    信号               │   重问(至多 2 次)
  │◀─ tool result 写回,重入─┤ ⑦ 清 _pending_ask        │                      │
  │   帧 loop(:638-646)     │                          │                      │
```

**就地挂起(v2 的简化)**:`await supervisor.ask` 自然阻塞本帧 loop,无需任何
YIELD 机制——父帧照常停在自己的 await 点,兄弟 spawn 帧照常运行
(`kernel/runner.py:604-611`)。run 完成判定因此天然安全:run 只在整棵帧树返回后
才置 DONE,handler 未回答期间状态保持 RUNNING(同处注释)。**注**:`docs/SUPERVISOR.md`
§2.2 称"所有活跃帧挂起时 run 转 PAUSED、帧 status = SUSPENDED";代码中 supervisor
路径**不做任何状态迁移**(`RunStatus.PAUSED`、`FrameStatus.SUSPENDED` 枚举存在于
`api/v1/run.py:53`、`api/v1/frames.py:47`,但仅调试器等其他路径使用)。本章以代码
为准:"挂起"是 await 阻塞,不是状态机迁移;"等待上级"在 Web 上由收件箱 pending
列表呈现,而非 run 状态字段。

**通道选择顺序**(`docs/SUPERVISOR.md` §2.3,实现于
`host/web/run_manager.py:405-418`):run 级注入的 handler → 装配级 handler → 宿主
默认通道。三条基线通道:

| 调用方 | 通道实现 | decided_by / channel 标签 |
|---|---|---|
| 嵌入方应用 | 注入的 `SupervisorHandler`(`runtime/builder.py:100-116`) | 缺省 `"handler"` |
| Web 用户 | `InboxChannel` 收件箱(`supervisor/inbox.py`),`GET /api/supervisor/pending` + `POST /api/supervisor/{id}/answer`(`host/web/app.py:649-669`) | `"host:web-ui"` / `"inbox"` |
| CLI 用户 / coding agent | stderr 单行 JSON `{"type":"supervisor.ask",...}` + stdin 读一行,进程内闭环(`host/cli/main.py:57-87`) | `"host:cli"` / `"cli"` |

`InboxChannel` 是一个挂起式收件箱:`__call__` 即 handler 契约——Question 进 pending
表并挂起一个 `asyncio.Future`,Web 请求线程调 `answer` 经
`loop.call_soon_threadsafe` 跨线程结算(`supervisor/inbox.py:88-101`);超时或被取消
后问题经 `finally` 自动离箱(:47-51);pending 列表 urgency=high 优先、其余先问先排
(:79)。CLI 通道刻意不做跨进程 pending/answer 子命令——单进程 CLI 无收件箱可查,
异步收件箱形态由 Web 宿主承载(`docs/SUPERVISOR.md` §2.3)。

### 4.3 超时、兜底与格式校验

`SupervisorManager.ask`(`supervisor/manager.py:71-127`)是策略核心:

| 情况 | 行为 | 代码锚点 |
|---|---|---|
| 正常作答 | 发 `supervisor.answer`,返回 `{answer, decided_by}` | manager.py:113-116 |
| `asyncio.wait_for` 超时 + `on_timeout="fail"` | 发 `supervisor.timeout`,返回 `{ok:false, error:{kind:"supervisor_timeout", retryable:true}}`,帧可自行降级 | manager.py:129-150 |
| 超时 + `on_timeout="default_answer"` | 用配置兜底答案闭环,`decided_by="policy:default"` | manager.py:139-141 |
| 答案不合 options | 以 `previous_error` 重问**调用方**(不重问子帧),初问+重问至多 2 次(`_MAX_ASK_ATTEMPTS=2`);仍不合法按 fail 闭环 | manager.py:40、:106-127 |
| Web 路由层预校验 | 答案不在 options 内 → 400,问题保持挂起 | run_manager.py:935-953 |

重问上限取 2 而非无限,是可用性与防死循环的取舍:调用方连续两次答非所问,大概率
是协议理解错误,继续重问只会空转;此时把结构化错误交给子帧,帧可按 prompt 预设
降级(测试中的 mock 大脑即演示了 `fallback_defer` 路径)。

### 4.4 持久化:checkpoint / resume / replay

pending ask 以 `frame.context.working["_pending_ask"] = {question_id, call_id,
question, context, asked_at}` 随帧入档(`kernel/runner.py:624-630`),复用 working
结构,无 checkpoint schema 变更。仅正常闭环(含超时/兜底)才清 pending;异常(断电/
取消)保留供 resume 重问(:634-635)。恢复时 `_settle_pending_ask`(:648-673)**先于**
通用未配对结算执行(checkpoint.py:31-36、:362-364):凭 call_id 找到那条未配对的
ask 调用,重新走 `_ask_supervisor`(新 question_id)向调用方再问一次,真实答案写为
tool result——配对原子性(不变量 2)天然闭合,不落 interrupted 占位。replay 时
`supervisor.answer` 已在 trace 中,按记录值回放,不问第二次(CLI replay 路径直接
不装 supervisor 通道,`host/cli/main.py:276`)。

### 4.5 信号与升权共用

三枚信号(`api/v1/signals.py:94-96`):`supervisor.ask`(payload 含 channel、kind
标签,manager.py:90-105)、`supervisor.answer`(答案摘要截断 200 字符,
manager.py:179)、`supervisor.timeout`。channel 标签由 handler 的
`supervisor_channel` 属性声明(manager.py:63-65),使"问题走了哪条通道"在 trace
里可审计。内核升权确认复用同一闭环:`_escalate` 把 `EscalationRequest` 转为
`kind="escalation"` 的 supervisor 提问(`kernel/runner.py:1003`),无 supervisor
通道且无 Grant 命中时 fail-closed(:957-965)——升权无人可审等于无人把关。

## 5. 效果与验证(效果)

**测试证据**(全绿,属 Python 812 例基线的一部分):

- `tests/kernel/test_supervisor.py`:**12 例**,覆盖 §9 锚点清单的内核侧——handler
  闭环、就地挂起(挂起期间父帧 await 点不动)、pending ask 阻止 run 提前判完成、
  超时 fail 返回 retryable 错误、default_answer 兜底且标 policy 来源、options 不合
  重问调用方、未声明 manifest 得 PERMISSION_DENIED、checkpoint 入档 + resume 重问
  闭环、挂起/恢复全程无孤儿 tool result(配对不变量)、信号 channel 标签三例。
- `tests/web/test_supervisor_channel.py`:**4 例**——收件箱 pending 列出 → 作答 →
  run 恢复且 `result == {"decision": "approve"}`;trace 中 `supervisor.ask` 的
  `channel == "inbox"` 端到端落盘;options 外答案 400 且问题仍挂起;LLM 可见
  schema 含 `ask_supervisor`。
- `tests/examples/test_supervision_nested.py`:**4 例**——嵌套监督:外层 handler
  内部再起一个 run,外层 LLM 答案流回内层,`decided_by` 链(`"agent:team_lead"`)
  可观察。

**真实示例**:`agent_os/examples/supervision/`——`junior_clerk` 报销文员提问,
`team_lead` 主管 agent 按政策裁决(≤ ¥3000 自动批准):`outer.py 5000` → reject,
`outer.py 500` → approve。两级是**两个独立内核**,内层只看到一次 handler 往返。

**涟漪效应**:升权系统(ESCALATION E1/E2)整个确认闸门建在 supervisor 闭环之上
(kind=escalation、Web 收件箱升权卡片、CLI kind 透传),没有另造通道;Web 调试器的
断点挂起-恢复复用同一"就地挂起"模式(`kernel/debug.py:9` 注释明写)。supervisor
由此成为"人机闭环"的单一载体:凡需要 agent 之外权威的点,都收敛到这一个路由口。

## 6. 局限性与边界(局限性)

1. **状态迁移未实现(文档与代码出入)**:`docs/SUPERVISOR.md` §2.2 的"run 转
   PAUSED / 帧置 SUSPENDED"在代码中不存在;挂起纯由 await 表达。代价:无法按
   run 状态过滤"等待上级"的 run,UI 只能依赖收件箱 pending 列表;状态枚举里的
   PAUSED/SUSPENDED 对 supervisor 路径是死字母。
2. **§9 锚点清单未全覆盖**:第 7 条"spawn 后台帧提问只挂起该分支"无专项测试
   (`tests/kernel/test_blackboard_spawn.py` 只测 spawn 本身);该性质目前由"就地
   挂起不触碰其他帧"的结构论证支撑,而非断言。
3. **CLI 通道是阻塞式单行协议**:一次一问、stdin 读一行;stdin EOF 读得空串,
   通常不合 options,重问耗尽后按不合法答案闭环(`host/cli/main.py:61-63`)。
   跨进程异步收件箱明确不做,代价是 CLI 场景下人必须在场。
4. **handler 粒度只到 run 级**:不支持 per-frame 覆盖(§10 开放问题 5);一个 run
   内所有帧共享同一调用方通道,无法按子树分流给不同裁决者。
5. **答案无 schema 化分级**:options 仅字符串数组,自由文本答案与结构化答案
   未分级(§10 开放问题 3);校验是裸字符串比对,大小写/空白归一化留给通道。
6. **HumanApproval sidecar 未下沉**:规则化"人答"与决策路由仍是两套体系
   (§10 开放问题 1),宿主需要同时理解 sidecar verdict 与 supervisor 裁决两种
   人机交互形态。
7. **超时策略是单点**:`asyncio.wait_for` 到点即结算,无提醒、无升级(升级明确
   不是内核概念,级联留给调用方);`urgency=high` 也只在收件箱排序优先,无打断
   性呈现(§10 开放问题 4)。
8. **信号中的答案只有摘要**:`supervisor.answer` payload 截断 200 字符
   (manager.py:179),长答案的完整形态只存在于帧上下文与 result.json,审计长
   裁决时需跨产物拼凑。

## 7. 引用

- 设计文档:`docs/SUPERVISOR.md`(v2,含 §9 锚点清单与 §10 开放问题)、
  `docs/ESCALATION.md`(§3 升权确认复用闭环)
- 契约:`agent_os/src/agent_os/api/v1/supervisor.py`、`api/v1/signals.py:94-96`
- 子系统:`agent_os/src/agent_os/supervisor/manager.py`、`supervisor/inbox.py`
- 内核集成:`agent_os/src/agent_os/kernel/runner.py:554-673、:957-1003`、
  `kernel/checkpoint.py:31-36、:362-364`
- 装配与配置:`agent_os/src/agent_os/runtime/builder.py:100-116、:196-210`、
  `runtime/config.py:295-314`
- 宿主通道:`agent_os/src/agent_os/host/web/app.py:649-669`、
  `host/web/run_manager.py:405-418、:928-953`、`host/cli/main.py:57-87、:276`
- 测试:`agent_os/tests/kernel/test_supervisor.py`(12 例)、
  `agent_os/tests/web/test_supervisor_channel.py`(4 例)、
  `agent_os/tests/examples/test_supervision_nested.py`(4 例)
- 示例:`agent_os/examples/supervision/`(嵌套监督)
