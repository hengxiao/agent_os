# 代码编排设计稿:Logic Kernel 的工具系统调用(Tool Syscalls)

> 状态:**已实现**(锚点测试 `agent_os/tests/logic/test_orchestration.py`,16 条)。
> 落地与本稿的两处偏差见 §12。权威架构见 [DESIGN.md](DESIGN.md);
> 与 [STDLIB.md](STDLIB.md) §9"被拒绝的备选路线·现场代码编排"的关系见 §11
> ——本设计改变了当初拒绝的前提条件。

---

## 1. 动机与定位

### 1.1 缺的最后一条边

现状的调用能力矩阵:

| 调用方 | 调工具 | 调子技能 | 执行代码 |
|---|---|---|---|
| prompt 帧(LLM tool_calls) | ✅ `_dispatch_call` | ✅ `skill__*` | ✅ 经 `python_exec` |
| TRUSTED code 技能 | ✅ `ctx.call_tool` | ✅ `ctx.invoke` | ✅(它就是代码) |
| **SANDBOX code 技能** | ❌ `ctx=None` 纯计算 | ❌ | — |
| **LLM 动态脚本**(python_exec) | ❌ 强制沙箱 ⇒ 纯计算 | ❌ | — |

缺的边 = **沙箱内代码 → 工具**。补上它,一个机制同时解锁两件事:

1. **LLM 现场编写编排脚本**:循环/分支/中间变量留在执行环境,只有聚合
   结果回上下文。100 个文件逐个 `fs_read` + 统计,现状要 100 轮工具往返
   (每轮全上下文计费);脚本一次执行,LLM 只看到最终统计表——书中
   估计省约两个数量级 token;
2. **SANDBOX code 技能恢复编排能力**:现状 `logic: {mode: sandbox}` 或
   `force_sandbox`(多租户档)一开,code 技能就退化为纯计算——隔离与
   编排是互斥的。补上这条边后二者兼得,`force_sandbox` 不再阉割功能。

### 1.2 架构定位:完成微内核类比

**沙箱 = 用户态进程,工具调用 = 系统调用(syscall)。** 脚本持有的不是
能力,是一条**窄通道**;每次调用陷入内核,由内核仲裁(白名单/veto/记账)
后代为执行。这不是新增一条信任路径,而是把既有的 `_dispatch_call` 分发点
暴露给一个新的调用方——权威集不变,变的只是人机工程与 token 经济。

### 1.3 非目标

- ❌ TRUSTED 执行 LLM 动态代码(§9.2 公理"LLM 动态代码强制沙箱,无配置
  可关闭"**保持不变**——本设计正因为存在,才不需要动这条公理);
- ❌ 运行期通配白名单(脚本的可调集合仍由调用帧 manifest 静态圈定);
- ❌ 替代组合子(`std/combinators` 是可复用的**模式**,编排脚本是一次性
  **胶水**;两者互补,见 §11)。

---

## 2. 语义设计

### 2.1 呈现:`python_orchestrate` 伪工具(runner 拦截式)

新增内核级伪工具 `python_orchestrate`(命名同 `skill__*` 逻辑:分发阶段
被 runner 拦截,不进 Tool Registry)。与 `python_exec` 并存,分工:

| | `python_exec`(不变) | `python_orchestrate`(新增) |
|---|---|---|
| 用途 | 纯计算(算式/转换) | 编排(代码内调工具/子技能) |
| ctx | 无 | syscall 代理 ctx |
| 契约 | concurrent_safe/cacheable | 有副作用,不可缓存 |
| 授权 | EXEC | EXEC,**且可调集合 = 调用帧 manifest 白名单** |

为什么拦截式而不是注册工具:工具函数拿不到 kernel 引用,而 syscall 服务
需要绑定**调用帧 + 调用方 manifest**做仲裁——这与 `skill__*` 的拦截理由
完全相同,权限敏感的分发留在内核。

**为什么不合并成 `python_exec(tools=true)` 一个工具**(设计决策记录):

1. **权限按名字授予**:`permissions.tools` 是名字列表,三层权限全部以
   工具名为仲裁单位。合并意味着所有现存声明了 `python_exec` 的 manifest
   在无人改动的情况下授权面被静默放大(纯计算 → 触达整个帧白名单),
   违反"声明即授权";参数级差异还会迫使 ToolGuard 解析 args 才能仲裁,
   把静态授权降级为运行期参数检查;
2. **契约不可共名**:`python_exec` 享受 `cacheable + concurrent_safe`
   纯函数红利,编排副作用满身;一个名字两套契约 = 契约表只能写
   "视参数而定",恰是契约字段要消灭的模糊;
3. **先例一致**:与 `http_fetch`/`http_post` 拆分同一理由(书的整合原则
   自带豁免:"unless there is a clear security, permission reason");
4. **误用代价不对称**:误用 exec 写 `ctx.call_tool` → NameError 一轮自纠;
   合并后"以为是纯计算"的调用静默带上工具触达,无报错信号。

分开的只是**接口与授权平面**;执行平面统一——同一沙箱、同一驱动协议,
服务循环仅在 `dispatch_fn` 非 None 时激活,维护成本不因拆分增加。

manifest 侧:技能要用它须在 `permissions.tools` 声明 `python_orchestrate`
(声明即授权,§8.2 文化);装配期闸门照常。

### 2.2 syscall 协议(沙箱 ↔ 内核)

沙箱驱动脚本注入 `ctx` 对象;每次调用经专用管道(fd 3,避免与业务
stdout 混流)交换单行 JSON:

```
脚本 → 内核: {"syscall": "tool",  "id": "s1", "name": "fs_read", "args": {...}}
内核 → 脚本: {"id": "s1", "ok": true, "value": ..., "error": null}
脚本 → 内核: {"syscall": "skill", "id": "s2", "name": "summarize", "args": {...}}
```

- 脚本侧 `ctx.call_tool(name, args) -> dict` / `ctx.invoke(skill, input)`,
  **同步阻塞**语义(脚本等响应);返回形态与现有 `{"ok","value","error"}`
  逐字段同构——TRUSTED 与 SANDBOX 的 ctx 契约一致,code 技能可在两档
  之间迁移而不改代码;
- 内核侧:logic kernel 从 `communicate()` 一次性模式改为**服务循环**
  (asyncio 读管道 → 每条 syscall 经回调 `dispatch_fn` 进
  `_dispatch_call(call, caller_frame, caller_manifest)` → 写回响应 →
  进程退出或超限则收尾);
- 同一协议同时覆盖 subprocess 与 Docker 两个沙箱后端(都是子进程管道);
  协议成为 LogicKernel 契约的一部分(版本号首行,同 telemetry 惯例);
- `ctx.spawn`/`wait`/`board` v1 不过桥(后续档);`ctx.blob` 过桥
  (大中间结果 spill 在沙箱侧就地发起)。

### 2.3 权限:无提升论证(本设计的安全支点)

脚本以**调用帧的身份**执行:可调集合 = 该帧 manifest `permissions.tools`
∩ RunConfig 上限,每次 syscall 照发 `pre:tool.call`(ToolGuard 可 veto)。
对比现状:同一个 LLM 在同一帧里,本来就可以用普通 tool_calls 逐个调用
这些工具。**脚本没有拿到任何它原本拿不到的权威——变化只在调用的形状
(批量/程序化)与成本(不过上下文)。** 注入攻击面同理:被注入的模型
能让脚本干的坏事,不多于它直接发 tool_calls 能干的。

新增的真实风险只有**速率**(脚本可以在一次执行里烧掉大量调用),由
§4 的限额覆盖;以及**组合语义**(单次都合规、组合起来有害),这与
prompt 帧多轮调用的组合风险同类,仍由 ToolGuard 规则与 sidecar 观察面
承接——syscall 逐条发信号,sidecar 看到的粒度不变。

CodeScanner 照常在 `pre:logic.exec` 扫描脚本源码(veto 点保持)。

---

## 3. 与既有子系统的整合

| 子系统 | 语义 |
|---|---|
| **记账** | 每 syscall = 一次工具调用,记入调用帧 usage;`ctx.invoke` 压子帧,深度/步数照常。run 级 max_steps/max_cost 天然生效 |
| **限额** | 新增 `limits.max_tool_calls`(单次编排的 syscall 上限,默认如 50);墙钟:脚本 wall_time **不含**内核侧执行 syscall 的时间(分别计时,否则一次慢工具就烧光脚本预算) |
| **信号** | `pre/post:logic.exec` 包住整次执行;每 syscall 照发 `pre/post:tool.call` / `pre/post:skill.invoke`(payload 加 `"via": "orchestrate"`)。LoopDetector 现订阅 post:step 的 calls 列表——需补一条:编排期间以 syscall 序列喂它(或订阅 post:tool.call),防脚本内死循环调用 |
| **中断** | RunControl stop → 下一条 syscall 返回错误并杀进程 → RunAborted 上抛;CancelledError 路径补 INTERRUPTED 占位(配对原子性,§7.4 不变量 2 由"脚本调用不进上下文"天然免除——进上下文的只有整次编排的单条 tool result) |
| **崩溃** | 脚本中途崩:已执行的 syscall 副作用已发生且已入 trace;返回 RUNTIME_ERROR + traceback + **已执行调用清单**(模型可据此决定补偿,幂等契约字段在此生效) |
| **checkpoint/replay** | 编排调用在父帧上下文里是一条 tool call + 单条 result(与普通工具同构)→ checkpoint 零改动。replay:脚本重执行,内部 syscall 命中确定性工具则逐字节复现;trace 里的 syscall 信号序列可 diff 校验 |
| **Web/RCA** | 单条 tool call 块 + 信号时间线上的 syscall 序列;RCA 错误观察形态不变 |

---

## 4. 弹性与失败语义

1. **超时分层**:脚本墙钟(净计算时间)/ 单 syscall 超时(工具自身
   contract)/ 总 syscall 数上限,三者独立;
2. **错误回脚本而非回模型**:syscall 失败以 `{"ok": false, "error"}`
   返回脚本,**脚本代码决定重试/降级/上抛**——这正是编排的价值(错误
   处理逻辑也程序化了);只有脚本整体结果才回模型;
3. **部分成功报告**:整次编排的 tool result 值形态
   `{"result": ..., "calls": N, "failed": [...]}`,失败清单默认带
   `hint`(STDLIB v2 错误契约);
4. **死循环**:max_tool_calls 硬上限 + LoopDetector 签名指纹(§3);
5. **幂等**:编排放大了重放/崩溃后重执行的概率——STDLIB v2 工具契约的
   `idempotent` 字段从"最好有"升级为"编排可调工具强烈建议声明"。

---

## 5. 实现落点(§按改动量排序)

| 文件 | 改动 |
|---|---|
| `logic/python_sandbox.py` | **主战场 1**:驱动脚本注入 syscall ctx(fd 3 协议);`execute` 增加服务循环模式(`dispatch_fn` 回调参数,None 则纯计算,向后兼容) |
| `kernel/runner.py` | **主战场 2**:`_dispatch_call` 拦截 `python_orchestrate` → `_run_orchestration`(构造 ExecRequest,绑定 `dispatch_fn = lambda call: self._dispatch_call(call, frame, manifest)`,经 logic router 执行);信号 payload 加 `via` |
| `logic/docker_sandbox.py` | 同协议接入(管道已有,加服务循环) |
| `kernel/logic_context.py` | SANDBOX 档 code 技能的 ctx 从 None 改为 syscall 代理(TRUSTED 不变);两档 ctx 契约对齐 |
| `api/v1` | `SkillLimits.max_tool_calls`;协议版本常量 |
| `runtime/config.py` | `[tools] python_orchestrate = true|false`(默认 false,显式开启——Fail-Safe Defaults) |
| STDLIB.md | §3.3 增列 `python_orchestrate`;§9 更新(见 §11) |

**不动**:checkpoint、replay 对齐逻辑、Web 前端、权限模型本体。

## 6. 锚点测试清单

1. 脚本内 `ctx.call_tool` 白名单内工具成功,白名单外折叠 PERMISSION_DENIED
   **进脚本**(不是崩整次编排);
2. ToolGuard 对 syscall 生效(危险参数 veto,理由回到脚本);
3. 中间结果不进上下文:100 次 fs_read 的编排,父帧只多一条 tool result;
   信号里有 100 条 post:tool.call(`via: orchestrate`);
4. `ctx.invoke` 从脚本压子帧,深度/记账正确;
5. max_tool_calls 超限 → 编排失败,已执行清单回报;
6. 墙钟不计 syscall 内核时间(慢工具不烧脚本预算);
7. stop 中断:syscall 返回错误 + 进程被杀 + RunAborted;
8. SANDBOX code 技能经同机制获得 ctx,与 TRUSTED 行为等价(同一 handler
   两档运行结果一致);
9. replay:含编排的 run diff 为空(确定性工具);
10. CodeScanner veto 编排脚本(危险 import 不执行);
11. `python_exec` 行为零变化(回归)。

## 7. 开放问题

1. syscall 并发:脚本内 `asyncio.gather(ctx.call_tool(...), ...)` 要不要
   支持(协议需 id 乱序响应;内核侧天然可并发)——v1 先串行,协议
   预留 id 已兼容;
2. `ctx.spawn` 过桥(脚本发起后台帧)——涉及沙箱进程生命周期与帧
   生命周期解耦,后续档;
3. 编排脚本的产物能否直接成为 code skill(与 STDLIB §10.6 写侧治理
   衔接:跑通的脚本 + `verify_before_store` → `learned/`)——这是
   自进化闭环的最短路径,单独立项;
4. 流式 syscall(长跑工具的进度回脚本)——依赖工具层流式契约,不做;
5. Docker 后端的 fd 3 映射细节(`docker run` 的 fd 透传或改用 socket)。

## 8. 与 STDLIB §9 的关系(修订记录)

STDLIB v2 §9 拒绝"现场代码编排"的理由是**审计/白名单**("违反收紧
文化")。本设计消解了该前提:syscall 中介使每次调用仍过同一闸门,
可调集合仍由 manifest 静态圈定——审计姿态与 prompt 帧完全相同(静态
知道"可能调什么",运行期决定"实际调什么")。当初真正该拒绝的是
**运行期通配白名单**,本设计不需要它。

修订后的分工:`std/combinators` 承载**可复用、可测试、有名字**的编排
模式(map/retry/vote);`python_orchestrate` 承载**一次性、任务特定**
的胶水编排。前者是库,后者是脚本——正如 Python 里 itertools 与
现场 for 循环并存。

## 12. 落地记录(实现与设计稿的偏差)

1. **限额从传输层移到内核侧**(§2.2/§4.1 修订)。设计稿把 `max_tool_calls`
   放在 `ExecRequest`、由沙箱服务循环执行;实现中发现这会让脚本在超限后
   收到 `BrokenPipeError`——一个无法自纠的错误,违反"错误回脚本、脚本自行
   处置"(§4.2)与 STDLIB v2 的错误 hint 契约。改为:**限额是内核策略**,
   在 `_syscall_dispatcher` 计数并回结构化错误(`已用尽 … hint: 收敛脚本`),
   通道不断;跑飞脚本由 `wall_time` 兜底。`ExecRequest.max_tool_calls` 随之
   删除,传输层退化为纯管道(职责更干净)。
2. **通道用 socketpair 而非两条 pipe**(§2.2)。全双工、单 fd、
   `asyncio.open_connection(sock=...)` 直接拿到带背压的 reader/writer
   ——大结果(文件全文)`drain()` 不阻塞事件循环。fd 号经
   `AGENT_OS_SYSCALL_FD` 环境变量告知子进程(`-I` 只阻止 PYTHONPATH 进
   `sys.path`,不清空 `os.environ`)。
3. 两种 ctx 形态(实现细节,设计稿未写明):`_SyncCtx` 给编排脚本
   (LLM 写直线代码,无 async 样板)、`_AsyncCtx` 给 code 技能 handler
   (`await ctx.call_tool(...)`,与 TRUSTED 档逐字一致),共享同一传输层。
4. 伪工具名与 LLM 可见 schema 定义在契约层(`api/v1/logic.py` 的
   `ORCHESTRATE_TOOL` / `ORCHESTRATE_SCHEMA`),ContextManager 按 manifest
   声明 + 消融开关补进可见工具面;KernelBuilder 的装配期闸门放行该名字
   (伪工具不进 registry)。
