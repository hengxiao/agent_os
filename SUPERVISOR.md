# Supervisor 子系统设计稿:ask_supervisor

> 状态:**已实现**(S1 内核机制 / S2 宿主通道 / S3 策略完备收官;
> S3 增量:Web 收件箱 UI + urgency 呈现 + 嵌套监督示例 + 信号 channel 标签)。
> **v1 → v2 修正**:"caller"的语义——v1 把默认上级设计为**父帧的 LLM**
> (调用栈内部路由);经指正,caller 指**整个 agent 的调用方**(启动本 run 的
> 宿主/应用)。内部栈不再做 YIELD 让渡(v2 删除该机制),语义大幅简化。
> 权威架构见 [DESIGN.md](DESIGN.md);相关:[SKILL-INLINING.md](SKILL-INLINING.md)、
> [CODE-ORCHESTRATION.md](CODE-ORCHESTRATION.md)、[STDLIB.md](STDLIB.md) §3.3。

---

## 1. 动机与定位

### 1.1 ask_human 为什么不够

STDLIB W5 的 `ask_human` 设想要一条"挂起 run → 人回答 → 恢复"的通道,
两个局限:上级被锁定为"人"(无法表达"调用我的那个程序");实现被误读为
**run 级**挂起(真正常用的是**帧级**——只有提问的帧等答案)。

### 1.2 定位:supervisor = 面向 agent 调用方的裁决路由子系统

supervisor 接收"请求上级裁决"的请求,**路由出 agent**,交给
**本 run 的调用方**(启动这个 agent 的宿主:Web 开发者、CLI 用户、
嵌入了内核的应用程序),并把提问帧挂起直到决定返回。

两条设计公理:

1. **默认 yield 到 agent 的调用方**。任何帧提问,执行都让渡给
   **启动本 run 的那个实体**,由它决定。调用方可以是
   人(Web/CLI)、另一个程序(嵌入方)、**乃至另一个 agent**
   (嵌套监督:子 agent 的上级是它的调用 agent,见 §2.5)。
2. **挂起的是帧,不是 run**。中断以帧为单位;run 转 PAUSED 只是
   "所有活跃帧都被挂起"时的自然形态(常见于根栈提问)。

与 sidecar 的分工:sidecar 是**规则化监督**(确定性,fail-closed);
supervisor 是**决策路由**(agent 之外的权威)。HumanApproval sidecar
未来可下沉为 supervisor 的一条宿主策略(§10),v1 不动。

### 1.3 与微内核的边界

- **内核保留**(流控制):帧的就地挂起/恢复机制、pending ask 的配对、
  run 完成判定;
- **supervisor 子系统**(策略):调用方通道(Web 收件箱 / CLI 协议 /
  应用回调)、超时与兜底策略;
- 类比:内核提供"帧可以让渡执行"的机制,子系统决定"让渡给谁"。

---

## 2. 语义设计:suspend → route-out → answer → resume

### 2.1 发起:`ask_supervisor` 伪工具

内核拦截式伪工具(同 `python_orchestrate` 的拦截理由:需要调用帧与
manifest 做仲裁,不进 Tool Registry):

```yaml
# manifest 声明(声明即授权,§8.2 文化)
permissions:
  tools: [ask_supervisor]
```

```jsonc
// 调用
{"question": "用户要求退 ¥5000 的订单,超自动上限,批准吗?",
 "context": {"order_id": "o3", "amount_cents": 500000},   // 给调用方的材料(可选)
 "options": ["approve", "reject"],                          // 可选,限定答案集合
 "urgency": "normal"}                                       // normal | high
// 结果(tool result)
{"answer": "approve", "decided_by": "host:web-ui", "question_id": "q-7"}
```

code 技能经 `ctx.ask_supervisor(...)`(同一仲裁路径)。

### 2.2 帧级中断:就地挂起(v2 的简化)

提问帧**就地挂起**:它的 `ask_supervisor` 分发等待一个
`asyncio.Future`,帧的 loop 停在该分发点(status = SUSPENDED)。

- **不需要在帧栈内部让渡**(v1 删除项):答案来自 agent 之外,内部栈
  没有任何一方需要为推进答案而恢复——父帧照常停在它自己的 await 点,
  兄弟 spawn 帧照常运行;
- **配对原子性零特判**:挂起期间子帧的 ask 调用只是"未完成的调用",
  恢复时答案作为其 tool result 写回,配对自然闭合(不变量 2 天然成立);
- **run 状态**:所有活跃帧都被挂起时 run 转 PAUSED;部分挂起时
  run 保持 RUNNING(帧树上有"等待上级"的标注帧)。

### 2.3 路由:到 agent 的调用方

supervisor 子系统把问题(含 context/options/urgency)送达调用方通道:

| 调用方 | 通道 |
|---|---|
| Web UI 开发者 | 收件箱:pending 列表 + 回答表单(`GET /api/runs/{id}/supervisor/pending`、`POST .../answer`) |
| CLI 用户 / coding agent | JSON 问题写 stderr;作答:`agent-os supervisor answer <run_id> <question_id> --answer '...'`(或答案文件) |
| 嵌入方应用 | 装配/启动 run 时注入的 `supervisor_handler: Callable[[Question], Awaitable[Answer]]`(默认通道) |

通道选择顺序:run 级注入的 handler → 装配级 handler → 宿主默认通道
(Web 收件箱 / CLI 协议)。

### 2.4 回答与恢复

- 调用方作答(可带 `decided_by` 标注)后,内核将
  `{"answer": ..., "decided_by": ...}` 作为提问帧那条 pending
  `ask_supervisor` 调用的 tool result 写回该帧上下文,**重新进入该帧的
  agent loop**——帧带着答案继续;
- run 之前转 PAUSED 的,恢复 RUNNING。

### 2.5 嵌套监督(调用方是另一个 agent)

嵌入方若自己也是 agent(用本内核或别的系统),它的 `supervisor_handler`
就是它的裁决点——它可以直接答,也可以**再向它的调用方上报**。
由此自然形成外部权威链:子 agent → 调用 agent → … → 人,
**完全发生在 agent 边界之外,内核不感知级数**(只看到一次 handler 调用的
往返)。这是 v2 相对 v1 最大的简化:内核里没有权威链,只有一次"出参"路由。

---

## 3. 超时与兜底语义

| 情况 | 语义 |
|---|---|
| 调用方在 `timeout_s` 内回答 | 正常闭环 |
| 超时 + `on_timeout="fail"` | tool result = `{ok: false, error: {kind: "supervisor_timeout", retryable: true, hint: "上级未答,可降级或重问"}}`(帧可自行补救) |
| 超时 + `on_timeout="default_answer"` | 用配置兜底答案闭环,标 `decided_by: "policy:default"` |
| 答案不匹配 `options` | 把格式错误**返回给调用方重答**(不重问子帧) |
| run 等待中收到 stop | pending ask 以 `{kind: "interrupted"}` 结算,帧走正常取消路径 |

升级(向"更上一级")不作为内核概念存在——嵌套监督(§2.5)把级联
留给了调用方自己。

---

## 4. 持久化:checkpoint / resume / replay

- **checkpoint**:挂起帧随栈正常入档;pending 问题以
  `frame.context.working["_pending_ask"] = {question_id, question, context, asked_at}`
  序列化(复用现有结构,无 schema 变更);
- **resume**:恢复的帧发现 `_pending_ask` → **重新向调用方通道提问**
  (新 question_id;原问题已答的按 trace 的 `supervisor.answer` 直接取回);
- **replay**:`supervisor.answer` 在 trace 中,replay 按记录值回放,
  不问第二次。

---

## 5. 信号与可观测

```
supervisor.ask      {question_id, frame_id, question, context 摘要, channel}
supervisor.answer   {question_id, decided_by, answer 摘要}
supervisor.timeout  {question_id, after_s}
```

Web(收件箱随 S2):pending 列表(run/帧/问题/context 展开/ urgency
排序)、回答表单、超时倒计时;trace 视图里 ask/answer 成对渲染;
帧树上挂起帧标"等待上级"。CLI:S2 即做(stderr JSON 协议 + answer 子命令)。

---

## 6. 配置

```toml
[supervisor]
timeout_s = 120
on_timeout = "fail"          # fail | default_answer
default_answer = ""
# Web 宿主通道默认开收件箱;嵌入方用 KernelBuilder.supervisor(handler) 注入
```

manifest 侧只需 `permissions.tools` 声明 `ask_supervisor`。

---

## 7. 与既有子系统的整合

| 子系统 | 语义 |
|---|---|
| **runner** | `_dispatch_call` 拦截 `ask_supervisor` → supervisor.ask → future;回答注入 + 重入帧 loop;run 完成判定含"无 pending ask" |
| **spawn** | spawn 的后台帧提问 → 只挂起该分支,其余帧照常(§2.2) |
| **checkpoint** | `_pending_ask` 在 working 中随帧入档(§4) |
| **sidecars** | 无交集;ask/answer 信号可照常订阅 |
| **tools** | 伪工具不进 registry;装配期闸门放行该名字(同 `python_orchestrate` 先例) |
| **Web/CLI** | 调用方通道(§2.3) |

---

## 8. 落地改动清单

| 文件 | 改动 |
|---|---|
| `api/v1/signals.py` | 三个 supervisor.* 信号名 |
| `api/v1/supervisor.py`(新) | `Question` / `Answer` / `SupervisorHandler` 契约 |
| `supervisor/`(新子系统) | 通道注册与选择(handler → 装配默认 → 宿主通道)+ 超时/兜底 policy |
| `kernel/runner.py` | 拦截 `ask_supervisor`;future 挂起/回答注入/重入;run 完成判定 |
| `kernel/checkpoint.py` | working 已带,无需改(验证 `_pending_ask` 序列化) |
| `runtime/builder.py` + `runtime/config.py` | `KernelBuilder.supervisor(handler)`;`[supervisor]` 段加载 |
| `host/cli/main.py` | `agent-os supervisor answer` 子命令 + stderr JSON 协议 |
| `host/web/app.py` | pending/answer 两个端点(S2) |

**不动**:checkpoint schema、replay 对齐逻辑、sidecar 体系、权限模型本体。

里程碑:**S1** 内核机制(拦截/挂起/注入/重入 + handler 通道 + 信号)→
**S2** 宿主通道(CLI + Web 端点 + 收件箱 UI 基础)→ **S3** 策略完备
(超时/兜底 + urgency 排序 + 嵌套监督文档与示例 + Web 收件箱 UI +
信号 channel 标签;嵌套示例见 `agent_os/examples/supervision/`)。

---

## 9. 锚点测试清单

1. **handler 闭环**:子帧 ask → run 挂起(或该分支挂起)→ 注入的
   handler 收到 question(含 context/options)→ 回答 → 子帧以答案为
   tool result 恢复 → 全树 DONE;decided_by 正确;
2. **就地挂起**:挂起期间父帧仍在其 await 点(无 YIELD 机制);
   恢复后父帧拿到子帧最终结果时序正确;
3. **超时**:`on_timeout=fail` → 子帧收到结构化错误观察(retryable)且可继续;
   `default_answer` → 闭环且标 policy 来源;
4. **options 校验**:答案不在 options 内 → 调用方收到格式错误并可重答;
5. **权限**:manifest 未声明 → PERMISSION_DENIED;
6. **run 完成判定**:存在 pending ask 时 run 不得判完成;
7. **spawn 分支**:后台帧 ask → 仅该分支挂起,主栈照常推进;
8. **checkpoint**:pending ask 入档 → resume 后重新提问 → 回答闭环;
9. **配对原子性**:挂起/恢复全程无孤儿 tool result(不变量 2 成立);
10. **嵌套监督**:handler 内部再起一个 run(它自己也 ask)→ 两级各自闭环。

---

## 10. 开放问题

1. HumanApproval sidecar 何时下沉为 supervisor 的宿主策略(统一"人答"通道)?
2. `set_timer` 是否复用"挂起 + 外部事件唤醒"通道(结构上兼容,单独立项)?
3. `options` 之外的自由文本与结构化答案(schema 化 answer)要不要分级?
4. urgency=high 在收件箱里要不要打断性呈现(而不仅是排序)?
5. handler 是 per-run 注入还是 per-frame 可覆盖(粗粒度 v1 只到 run)?
