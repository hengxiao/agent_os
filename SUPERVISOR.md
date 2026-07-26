# Supervisor 子系统设计稿:ask_supervisor

> 状态:**设计稿 v1**(未实现)。取代 STDLIB W5 的 `ask_human` 条目(与之合并立项的
> `set_timer` 共享同一条挂起/恢复通道,见 §9)。
> 权威架构见 [DESIGN.md](DESIGN.md)(§x.y 引用);相关:[SKILL-INLINING.md](SKILL-INLINING.md)、
> [CODE-ORCHESTRATION.md](CODE-ORCHESTRATION.md)、[STDLIB.md](STDLIB.md) §3.3。

---

## 1. 动机与定位

### 1.1 ask_human 为什么不够

STDLIB W5 的 `ask_human` 设想要一条"挂起 run → 人回答 → 恢复"的通道,
并把宿主(人)当作唯一上级。三个问题:

1. **上级错了粒度**。多数需要"问一下"的场景,答案就在**调用方自己的上下文里**:
   子技能拿不准"这个退款该不该批",它的调用方(编排它的那个 LLM 帧)通常有
   足够的上下文能直接决定——升级到人是过度升级;
2. **语义单一**。只有"问人"一档,无法表达"问调用方/问宿主/逐级上报"的
   权威链;
3. **实现被误读为 run 级挂起**。"run 转 paused"是最粗的一档;真正常用的是
   **帧级挂起**——只有提问的那个帧等答案,调用栈的其余部分继续运转。

### 1.2 定位:supervisor 是一个子系统

**supervisor = 权威路由子系统**:接收"请求上级裁决"的请求,路由到正确的
上级,执行**帧级中断**(挂起提问帧),拿到决定后**恢复**该帧。

两条设计公理:

1. **默认 yield 到 caller**。默认上级 = 调用方(父帧)。提问帧挂起,
   控制权让渡给调用方,由调用方(它是 LLM,有自己的上下文)决定;
   调用方答不了就再向上一级,最终到宿主(人/应用)。**由 caller 决定**。
2. **挂起的是帧,不是 run**。中断以帧为单位(微内核的流控制对象);
   run 级 paused 只是"根帧提问且路由到宿主"时的自然形态。

与 sidecar 的分工:sidecar 是**规则化监督**(确定性,fail-closed);
supervisor 是**决策路由**(权威)。HumanApproval sidecar 的"EXEC 级人工闸门"
未来可下沉为 supervisor 的一条宿主策略(§10),v1 不动。

### 1.3 与微内核的边界

- **内核保留**(流控制):帧的挂起/让渡/恢复机制、调用栈上的路由执行、
  配对原子性、run 完成判定;
- **supervisor 子系统**(策略):上级解析(`SupervisorResolver`)、超时与
  升级策略、宿主侧 supervisor 实现(Web 收件箱 / CLI 协议 / 应用回调 /
  规则自动应答);
- 类比:IPC 路由在内核,路由表在子系统——与 syscall 分发和 Tool Registry
  的关系同构。

---

## 2. 语义设计:park → route → answer → resume

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
 "context": {"order_id": "o3", "amount_cents": 500000},   // 可选,给上级的材料
 "options": ["approve", "reject", "escalate"],             // 可选,限定答案集合
 "urgency": "normal"}                                       // normal | high
// 结果(tool result)
{"answer": "approve", "decided_by": "frame:f-2(handle_ticket)",
 "question_id": "q-7"}
```

code 技能经 `ctx.ask_supervisor(...)`(同一仲裁路径);`ctx.chat` 的
驾驶场景同样可用。

### 2.2 路由:权威链

`SupervisorResolver.resolve(frame) -> chain`:

```
提问帧 → 父帧(caller)→ 祖父帧 → … → 根帧 → 宿主(Web/CLI/应用回调)
```

默认 `default = "caller"`:第一可答级 = 父帧。配置(§6)可改为首级直达宿主。

**逐级语义**:当前级在超时内未答 → 按 `on_timeout = "escalate"` 升级到
下一级(新的 question_id 关联原问题);`"fail"` 则把失败作为 tool result
还给提问帧(帧可自行补救);`"default_answer"` 用配置兜底答案。

### 2.3 帧级中断:park(让渡)

子帧提问时,**子帧挂起,控制权让渡给父帧的 loop**:

1. 子帧的 `ask_supervisor` 分发被拦截后,内核将子帧标记 `SUSPENDED`,
   并以 **YIELD 哨兵**作为该子帧调用的"临时结果"交还父帧分发点;
2. 父帧 loop 把 YIELD 落成一条工具结果(配对原子性:父帧原 `skill__子帧`
   调用必须当场闭合,否则下一个请求会因悬挂 tool_call 被 provider 拒绝):

   ```
   {"ok": true, "value": {"_supervisor_pending": {"question_id": "q-7",
    "from": "extract_facts", "question": "批准吗?", "context": {...}}}
   ```

3. 子帧**不出栈**:frame 留在栈上(SUSPENDED),其 context 完整冻结;
   子帧真正完成时,结果以**注入观察**(非 tool result,因配对已闭合)
   送达父帧上下文:"子帧 X 已完成: <result>";
4. **run 完成判定**:存在 RUNNING/SUSPENDED 帧时 run 不得判完成
   (子帧被 park 后父帧直接给最终答案也不许 run 结束——内核 hold 住,
   等子帧结算或被显式 stop)。

### 2.4 回答:`supervisor_answer` 伪工具

父帧(或宿主)回答经第二个伪工具:

```jsonc
{"question_id": "q-7", "answer": "approve"}
```

- 内核校验回答者身份:必须是该问题当前路由到的上级帧(或宿主通道);
- 回答落位:内核把 `{"answer": ..., "decided_by": ...}` 作为**子帧那条
  pending `ask_supervisor` 调用的 tool result** 写回子帧上下文(配对闭合),
  然后**重新进入子帧的 agent loop**——子帧带着答案继续;
- 父帧的 LLM 看到回答后的走向:它自己的 loop 继续(回答只是它的一步)。

### 2.5 宿主档(根帧提问或升级到顶)

- 根帧提问(无父帧):子帧是根帧 → park 后**run 状态转 PAUSED**,
  发 `supervisor.ask` 信号;
- 宿主通道(§7):Web 收件箱(`POST /api/runs/{id}/supervisor/answer`)
  或 CLI(JSON 问题 → stdin 读回答);
- 回答注入后 run 恢复(重新进入根帧 loop)。

### 2.6 中断执行的两面

"supervisor 进行中断执行"在本设计中只有一个机制:**提问帧挂起**——

- 提问帧视角:像一次阻塞的 syscall(等上级裁决,帧内时间停止);
- 系统视角:只有该帧停,调用栈其余部分照常运转(父帧继续思考、
  兄弟帧照常运行、sidecar 照常观察)。

不做"上级主动打断别人"(上级只能回答/升级/按 policy 失败该提问)——
主动终止已由 RunControl/sidecar 覆盖,不重复造。

---

## 3. 超时、升级与失败语义

| 情况 | 语义 |
|---|---|
| 上级在 `timeout_s` 内回答 | 正常闭环 |
| 超时 + `on_timeout=escalate` | 升级到下一级(新 question_id,`supersedes: q-7`),原问题标记 superseded;到顶(宿主)后再超时 → 按 fail 处理 |
| 超时 + `on_timeout=fail` | tool result = `{ok: false, error: {kind: "supervisor_timeout", retryable: true, hint: "上级未答,可降级或重问"}}` |
| 超时 + `on_timeout=default_answer` | 用配置兜底答案闭环,payload 标 `decided_by: "policy:default"` |
| 父帧把 YIELD 观察当噪音忽略 | 与超时同路径(升级或失败) |
| 回答不匹配 `options` | 作为格式错误观察还回答方(不重问子帧) |

**防踢皮球**:单问题的升级链最多 N=3 级(可配);到顶即按上表 fail/default 结算。

---

## 4. 持久化:checkpoint / resume / replay

- **checkpoint**:park 中的帧随栈正常入档;pending 问题以
  `frame.context.working["_pending_ask"] = {question_id, question, context, asked_at}`
  序列化(复用现有结构,无 schema 变更);
- **resume**:恢复的帧发现 `_pending_ask` → **重新路由该问题**(问不到
  原 question_id 的回答,上级看到的是新一次提问;已回答的问题以 trace 的
  `supervisor.answer` 信号为准可 replay 直接取回);
- **replay**:`supervisor.answer` 在 trace 中(信号),回放时按 replayable
  语义取记录值,不问第二次。

---

## 5. 信号与可观测

```
supervisor.ask       {question_id, frame_id, question, route: "parent:f-2"|"host"}
supervisor.escalate  {from_question, to_question, from: "f-2", to: "host", reason: "timeout"}
supervisor.answer    {question_id, decided_by, answer(摘要)}
supervisor.timeout   {question_id, level}
```

Web(后续档,不在 S1):收件箱(待答问题列表 + 回答框 + context 展开)、
trace 视图里 ask/answer 成对渲染、park 中的帧在帧树上标"等待上级"。
CLI:S1 即做——问题以 JSON 写 stderr,coding agent 可程序化作答
(`agent-os supervisor answer <run_id> <question_id> --answer '...'`)。

---

## 6. 配置

```toml
[supervisor]
default = "caller"          # caller | host(首级路由)
timeout_s = 120
on_timeout = "escalate"     # escalate | fail | default_answer
max_escalations = 3
default_answer = ""
```

manifest 侧只需 `permissions.tools` 声明 `ask_supervisor`;帧可用
`supervisor_answer` 无需声明(内核按路由身份仲裁,不做白名单)。

---

## 7. 与既有子系统的整合

| 子系统 | 语义 |
|---|---|
| **runner** | `_dispatch_call` 拦截两个伪工具;park 的 YIELD 哨兵从子帧 loop 贯穿到父帧 `_invoke_skill`;`supervisor_answer` 路由回答 + 重入子帧 loop;run 完成判定含 SUSPENDED |
| **spawn** | spawn 的子帧问 supervisor → 路由仍是其父帧(spawn 语义不变);park 与 spawn 共享"后台帧"基础设施 |
| **checkpoint** | `_pending_ask` 在 working 中随帧入档(§4) |
| **sidecars** | 无交集(规则监督 vs 决策路由);ask/answer 信号它们可照常订阅(如 BudgetGuard 不计费的问题) |
| **tools** | 伪工具不进 registry;装配期闸门放行这两个名字(同 `python_orchestrate` 先例) |
| **Web/CLI** | 宿主档通道(§5);`GET /api/runs/{id}/supervisor/pending` + `POST .../answer` |

---

## 8. 落地改动清单

| 文件 | 改动 |
|---|---|
| `api/v1/signals.py` | 四个 supervisor.* 信号名 |
| `api/v1/supervisor.py`(新) | `SupervisorResolver` / `Question` / `Answer` / 策略枚举 契约 |
| `supervisor/`(新子系统) | resolver(权威链)+ policy(超时/升级/兜底)+ host 通道基类 |
| `kernel/runner.py` | 拦截 `ask_supervisor`/`supervisor_answer`;YIELD 哨兵与 park;重入子帧;run 完成判定含 SUSPENDED |
| `kernel/checkpoint.py` | working 已带,无需改(验证 `_pending_ask` 序列化) |
| `runtime/config.py` | `[supervisor]` 段加载 |
| `host/cli/main.py` | `agent-os supervisor answer` 子命令 + 问题的 stderr JSON 协议 |
| `host/web/app.py` | pending/answer 两个端点(S2) |

**不动**:checkpoint schema、replay 对齐逻辑、sidecar 体系、权限模型本体。

里程碑:**S1** 内核机制(park/route/resume + 两伪工具 + 信号 + 配置)→
**S2** 宿主通道(CLI + Web 端点)→ **S3** 策略完备(超时/升级/兜底 + Web 收件箱 UI)。

---

## 9. 锚点测试清单

1. **caller 闭环**:子帧 ask → 父帧上下文出现 YIELD 观察(配对闭合)→ 父帧
   `supervisor_answer` → 子帧以答案为 tool result 恢复 → 子帧正常弹栈,
   最终结果以注入观察送达父帧;全树 DONE;
2. **host 闭环**:根帧 ask → run PAUSED + `supervisor.ask` 信号 → 控制通道
   注入回答 → run 恢复,结果含回答;
3. **超时升级**:父帧不答(brain 忽略问题)→ 升级到宿主(escalate 信号);
   `on_timeout=fail` 时子帧收到结构化错误且可继续;
4. **options 校验**:答案不在 options 内 → 回答方收到格式错误观察;
5. **权限**:manifest 未声明 `ask_supervisor` → PERMISSION_DENIED;
6. **run 完成判定**:子帧 park 中父帧给最终答案 → run 不判完成(直到子帧结算);
7. **checkpoint**:park 中 checkpoint → resume 后问题重新路由,回答后闭环;
8. **多跳**:孙帧 ask → 父帧不答 → 祖父帧答 → 闭环;
9. **park 的配对原子性**:park/恢复全程无孤儿 tool result(不变量 2 成立)。

---

## 10. 开放问题

1. HumanApproval sidecar 下沉为 supervisor 宿主策略的时机(统一"人答"通道)?
2. `set_timer` 是否复用本通道(park + 时钟事件唤醒)——结构上兼容,单独立项;
3. 上级回答是否也计入其帧的 token 归因(它消耗的是父帧正常步,自然归因,无需特判——确认即可);
4. 多级上报时 context 是否逐层截断(防 context 膨胀)?v1 原样上送;
5. `options` 之外的自由文本与结构化答案(schema 化 answer)要不要分级?
