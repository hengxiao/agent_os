# Logic Kernel 与编排沙箱

> 章次:04 · 状态:已实现(InProcess / subprocess 沙箱 / Docker 沙箱三个后端与编排 syscall 通道均已落地并有测试;沙箱内 `spawn`/`blob` 过桥、syscall 并发、Docker 后端的编排支持为已设计未实现) · 依据:`docs/DESIGN.md` §9;`docs/CODE-ORCHESTRATION.md`;`agent_os/src/agent_os/api/v1/logic.py`、`logic/`、`kernel/runner.py`、`kernel/logic_router.py`、`tools/builtins.py`

## 1. 概述

Logic Kernel 是系统中**逻辑代码的唯一执行点**,在 OS 映射表里对应 CPU/ALU:内核(Kernel Runner)负责调度与仲裁,但亲自不执行任何指令(docs/DESIGN.md §9)。两类代码都从这个咽喉点过:作者编写、版本管理的 **code 技能**(`kind: code`),以及 LLM 运行期生成的**动态代码**(经内置工具 `system.python.exec` 或编排伪工具 `python_orchestrate` 提交)。执行后端按信任档(TRUSTED / SANDBOX)路由:进程内、subprocess+rlimits、Docker 容器三档实现共存,契约相同、后端可换。编排沙箱在此基础上把"工具调用"建模为沙箱内代码的"系统调用",完成了"沙箱 = 用户态进程、工具 = syscall"的微内核类比(docs/CODE-ORCHESTRATION.md §1.2)。

## 2. 动机与背景(原因)

**为什么不放在内核里。** 微内核判据规定内核只保留流控制、权限控制与 IPC(docs/DESIGN.md §1 公理 1)。执行逻辑代码既不是 loop 推进的必要条件,也不是仲裁点——它是"策略"而非"机制",与"内核不组装提示词、不做持久化"同理,必须外置为子系统。内核只在分发时构造 `ExecRequest` 并路由(`kernel/runner.py:491-541`)。

**为什么是一个咽喉点而不是两个。** code 技能与动态代码信任属性相反:前者有版本、加载期可静态审查,默认可信;后者是模型现场生成,必须当作不可信输入。若各配一套执行设施,资源限额、监督信号、隔离策略就会有两份口径。收敛到单一 `LogicKernel` 契约(`api/v1/logic.py:181-188`)换来"三统一":统一限额、统一信号(`pre/post:logic.exec`,可审计可否决)、统一隔离策略——实例自报 `trust`,路由按档择优。

**token 经济是第二推动力。** prompt 帧每调一次工具,结果就进帧上下文,此后每一轮 LLM 请求都对它重复计费。批量场景(100 个文件逐个 `fs_read` 再统计)意味着 100 轮工具往返、中间结果全部留在上下文;让 LLM 现场写一段编排脚本在沙箱里循环调用,只有聚合结果回上下文——设计稿援引书中估计,token 消耗可降约两个数量级(docs/CODE-ORCHESTRATION.md §1.1)。

**隔离与编排曾经互斥。** 落地编排前,`logic: {mode: sandbox}` 或 `RunConfig.logic_policy.force_sandbox` 一开,code 技能的 `ctx` 即为 `None`,退化为纯计算——多租户档位以阉割功能为代价换隔离。补上"沙箱内代码 → 工具"这条边之后,二者兼得(docs/CODE-ORCHESTRATION.md §1.1 矩阵)。

这里有一个方向上的对照值得点明:升权系统管"低信任档能不能进来"(帧间的信任边界),Logic Kernel 的沙箱管"代码能不能出去"(执行体的能力边界)——两者方向相反但互补,共同构成最小权限原则在"调用"与"执行"两个维度上的闭环。

## 3. 问题陈述(解决的问题)

1. **动态代码的执行地点。** LLM 生成的代码若在宿主进程内执行,一行 `os.environ["ANTHROPIC_API_KEY"]` 即可读走凭证并经网络外发(`logic/python_sandbox.py:271-273` 注释即以此为例)。问题:给模型"代码解释器"能力,又不把宿主进程暴露给它。
2. **批量操作的上下文膨胀。** 逐文件处理类任务产生 N 轮工具往返,N 份中间结果永久驻留帧上下文并按轮计费。问题:中间变量应当留在执行环境,只有结论回上下文。
3. **沙箱档能力阉割。** 强制沙箱场景下 code 技能失去 `ctx.invoke`/`call_tool`,编排者模式(确定性控制流 + 按需调 LLM 技能)不可用。问题:隔离强度与组合能力不应是单选。
4. **脚本绕过授权面的风险。** 若让脚本直接持有工具表或内核句柄,manifest 白名单、ToolGuard 否决、记账全部旁路——这正是 STDLIB v2 §9 当初拒绝"现场代码编排"的理由(docs/CODE-ORCHESTRATION.md §8)。问题:开放编排,但审计姿态不得弱于 prompt 帧逐次调用。
5. **CPU 密集代码阻塞事件循环。** 协程级并行共享同一事件循环,重计算 code 技能会饿死兄弟分支(docs/DESIGN.md §3.4 注)。问题:需要进程级执行档承接这类负载。

## 4. 设计与机制(解决的方法)

### 4.1 契约与信任路由

契约层冻结四个要素(`api/v1/logic.py`):`LogicKernel` Protocol(`name` / `trust` / `execute`,:181-188)、`ExecRequest`(`source/language/entry/args/ctx/limits/network/dispatch_fn`,:149-167)、`ExecResult`(`value/error/stdout/stderr/usage`,:170-178)、`LogicError` 三值(`RUNTIME_ERROR | LIMIT_EXCEEDED | REJECTED`)。结构化错误一等公民:code 技能抛 `SkillError` 时 `kind/hint/retryable` 一路保真到模型与编排脚本,不被压扁成一句人话(:67-102)。

路由规则集中在 `LogicKernelRouter`(`kernel/logic_router.py:22-37`):manifest 声明 `logic: {mode: sandbox}` 或全局 `force_sandbox` → SANDBOX,否则 TRUSTED;目标档未装配属装配错误。动态代码**不经此路由**——`system.python.exec` 工具装配时直接持有 SANDBOX 实例(`tools/builtins.py:504-595`),"LLM 动态代码强制沙箱,无配置项可关闭"在结构上就无从违犯。

### 4.2 三个执行后端

| 后端 | 实现要点 | 隔离强度 | 用途 |
|---|---|---|---|
| `InProcessLogicKernel` | `asyncio.wait_for` 协程超时;stdout/stderr 重定向捕获;返回值 JSON 序列化检查;`RunAborted`/`MaxDepthExceeded`/`SkillLoadError` 不折成结果、原样上抛(`logic/inprocess.py:84-85`) | 无,仅协程级 | 可信 code 技能(默认)、调试 |
| `PythonSandboxLogicKernel` | `sys.executable -I -c` 子进程;`preexec_fn` 设 rlimits(CPU/地址空间/文件大小/fd 数,`logic/limits.py:18-29`);**env 白名单**(`_ENV_ALLOWLIST`,不含 `HOME` 与任何 `*_API_KEY`,`logic/python_sandbox.py:152`);每次执行一个空临时 cwd;父进程 wall 看门狗杀进程 | 进程级(部分) | LLM 动态代码(强制)、声明沙箱的 code 技能 |
| `DockerPythonSandboxLogicKernel` | `docker run --rm`:`--network none` 系统级断网、`--memory/--cpus/--pids-limit` cgroup 限额、只读根 fs、`--cap-drop ALL`;退出码 137 → `LIMIT_EXCEEDED`;超时 `docker kill` 兜底(`logic/docker_sandbox.py`) | 容器级 | 有 docker 的宿主(配置 `python_exec = "docker"`) |

subprocess 后端的模块 docstring 把 v1 隔离能力逐项写明、并**显式列出三项不做**:网络不隔离、文件系统不隔离(绝对路径仍可达)、进程不隔离(`logic/python_sandbox.py:7-18`)。结论:v1 挡住"顺手拿凭证/翻项目文件",挡不住蓄意攻击;真隔离用 Docker 后端。隔离阶梯(seccomp/nsjail → microVM)以后端替换接入,契约不变;文档明警 **venv 不是沙箱**。

### 4.3 LogicContext:TRUSTED 档的编排者能力

TRUSTED 模式下 code 技能与 prompt 技能有同等的组合能力,只是用代码表达。`KernelLogicContext`(`kernel/logic_context.py`)提供 `invoke`(压子帧)、`call_tool`(走 Tool Registry)、`spawn`/`wait`(§3.4 后台帧)、`board`(黑板命名空间代理,按 manifest 白名单逐次仲裁)、`chat`(直连 ProviderManager,自驾驶多轮)。关键性质:**全部回到内核 `_dispatch_call` 分发路径**——白名单、信号、记账与 prompt 帧的调用完全一致。code 技能因此是"编排者":确定性控制流(循环/分支/聚合)+ 按需调 LLM 技能。

### 4.4 编排沙箱:把工具调用做成 syscall

`python_orchestrate` 是内核拦截式伪工具(`ORCHESTRATE_TOOL`,`api/v1/logic.py:116`):不进 Tool Registry,`_dispatch_call` 在分发阶段拦截转 `_run_orchestration`(`kernel/runner.py:552-553,718-837`)。双重闸门:配置层 `[tools] python_orchestrate` 缺省 false(Fail-Safe Default,`runtime/config.py:260-262`),技能层须在 manifest `permissions.tools` 显式声明——两关都过才可见、可用。

```
父帧 LLM ──tool_call: python_orchestrate{code}──▶ 内核 runner._run_orchestration
                                                     │ ① 配置/manifest 双闸门
                                                     │ ② pre:logic.exec(CodeScanner 可 Veto)
                                                     ▼
                                          SANDBOX Logic Kernel(子进程)
   ┌────────────────────────────────────────────────────────────────┐
   │ 沙箱脚本                socketpair(单行 JSON,版本常量 v1)      │
   │ ctx.call_tool("fs_read",…) ──{"syscall":"tool","id":"s1",…}──▶  │
   │                                │ _serve_syscalls(纯传输层)      │
   │                                ▼                                │
   │                     _syscall_dispatcher(内核侧计数,默认上限 50) │
   │                                ▼                                │
   │                     _dispatch_call(以调用帧身份):               │
   │                     manifest 白名单 ∩ RunConfig → pre:tool.call │
   │                     (ToolGuard 可 Veto)→ 执行 → 记账           │
   │ ◀────────────── {"id":"s1","ok":true,"value":…} ──────────────  │
   │ 中间变量留在沙箱;只有脚本的 result 回帧上下文                  │
   └────────────────────────────────────────────────────────────────┘
```

要点:

- **无权限提升(安全支点)**:脚本以调用帧身份执行,可调集合 = 该帧 manifest 白名单 ∩ RunConfig 上限;每条 syscall 照发 `pre/post:tool.call`(payload 带 `"via": "orchestrate"`),ToolGuard 逐条可否决。同一个 LLM 本来就能逐次调这些工具——变化只在调用的形状(批量/程序化)与成本(不过上下文)(docs/CODE-ORCHESTRATION.md §2.3)。
- **服务循环是纯传输层**(`logic/python_sandbox.py:189-250`):分发与限额都在内核侧。限额超限回结构化错误(`hint: 收敛脚本`)而不断管,脚本可自行处置;跑飞脚本由 wall_time 兜底。这是落地记录的有意偏差:设计原把限额放传输层,实测超限会变成脚本无法自纠的 `BrokenPipeError`(docs/CODE-ORCHESTRATION.md §12.1)。
- **硬失败不降级**:`RunAborted`/`MaxDepthExceeded` 在服务循环里不停服降级为"脚本可无视的错误",而是关通道、由 `_collect` 先于一切结果归一化上抛(`logic/python_sandbox.py:231-237,360-363`)——否则预算超限与 Stop 会被脚本吃掉,run 照常跑完(实测过的真实缺陷)。
- **两种 ctx 形态共享同一传输层**:`_SyncCtx` 给 LLM 写的直线脚本(无 async 样板),`_AsyncCtx` 给 code 技能 handler——与 TRUSTED 档契约逐字一致,同一 handler 两档运行行为等价(`logic/python_sandbox.py:86-111`)。
- **崩溃语义**:脚本中途崩,已执行的 syscall 副作用已发生且已入 trace;返回 `RUNTIME_ERROR` + 已执行调用清单(`calls/failed/limit_hit`),模型可据 `idempotent` 契约决定补偿(`kernel/runner.py:810-826`)。

### 4.5 关键取舍

- **为什么不合并成 `python_exec(tools=true)`**:权限按名字授予,合并会让所有现存声明 `python_exec` 的 manifest 授权面被静默放大;`python_exec` 享 `cacheable/concurrent_safe` 纯函数红利,编排副作用满身,契约不可共名;与 `http_fetch`/`http_request` 拆分同一先例;误用代价不对称(docs/CODE-ORCHESTRATION.md §2.1)。
- **为什么通道用 socketpair 而非设计稿的 fd 3 双 pipe**:全双工、单 fd、`asyncio.open_connection(sock=…)` 直接拿到带背压的 reader/writer,大结果 `drain()` 不阻塞事件循环;fd 号经 `AGENT_OS_SYSCALL_FD` 环境变量告知子进程(docs/CODE-ORCHESTRATION.md §12.2)。
- **为什么拦截式而非注册工具**:工具函数拿不到内核引用,而 syscall 仲裁必须绑定调用帧 + 调用方 manifest——与 `skill.*` 的拦截理由相同,权限敏感的分发留在内核。
- **为什么 STDLIB §9 的拒绝被推翻**:当初拒绝的前提是"现场编排必然绕过白名单/审计"。syscall 中介让每次调用仍过同一闸门、可调集合仍由 manifest 静态圈定,审计姿态与 prompt 帧完全相同;真正该拒绝的是运行期通配白名单,本设计不需要它(docs/CODE-ORCHESTRATION.md §8)。分工随之明确:`std/combinators` 承载可复用模式,`python_orchestrate` 承载一次性胶水。

## 5. 效果与验证(效果)

测试基线(实测 `pytest tests/logic/test_orchestration.py tests/logic/test_python_sandbox.py tests/kernel/test_code_skills.py -q`:**38 passed**):

- `agent_os/tests/logic/test_orchestration.py`:17 个测试函数(18 用例,含参数化)。关键断言:白名单外调用折叠为脚本可见的 `PERMISSION_DENIED` 而非崩掉整次编排(`test_out_of_whitelist_tool_denied_into_script`);ToolGuard 否决 syscall 且理由回脚本;CodeScanner 在 `pre:logic.exec` 否决整段脚本;100 次 `fs_read` 循环后父帧只多一条 tool result、信号流有 100 条 `via: orchestrate` 记录(`test_loop_keeps_intermediates_out_of_context`);`max_tool_calls` 超限回报已执行清单;同一 handler 在 TRUSTED/SANDBOX 两档结果一致(`test_trusted_and_sandbox_equivalent`);含编排的 run replay diff 为空(确定性工具);硬失败穿透编排上抛(`test_hard_failure_escapes_orchestration`)。
- `agent_os/tests/logic/test_python_sandbox.py`:12 例,覆盖 wall 超时杀进程、stderr 尾部映射错误、stdout 截断、模块驱动形态、**宿主环境变量不继承**(`test_host_env_is_not_inherited`)、cwd 隔离(`test_sandbox_cwd_is_isolated_temp_dir`)。
- `agent_os/tests/logic/test_docker_sandbox.py`:8 例(docker 不可用时整文件 skip),含 `--network none` 断网与内存超限 OOM 断言。
- `agent_os/tests/kernel/test_code_skills.py`:8 例,覆盖 code 技能编排 prompt 技能、`ctx.call_tool`、`pre/post:logic.exec` 携带 trust、白名单拒绝、`force_sandbox` 路由。

真实示例:`agent_os/examples/research_pipeline/skills.yaml` 把研究管道的确定性阶段(规划、抓取、合并、校验、计数等 10 个 `kind: code` 技能)交给 Logic Kernel,LLM 只承担易变环节——"逻辑稳定走 code、易变 know-how 走 prompt"判据的完整示范。`instance/agent-os.toml:38` 以 `python_exec = "docker"` 启用容器档(docker 不可用时自动回退 subprocess 并告警,`runtime/config.py:155-168`)。

涟漪效应:STDLIB §9 的拒绝条款被正式修订(编排与组合子分工重写);SANDBOX 档 code 技能恢复编排能力,`force_sandbox` 不再阉割功能;syscall 协议成为契约的一部分(`SYSCALL_PROTOCOL_VERSION = 1`,`api/v1/logic.py:143`);装配期闸门为伪工具专门放行(`runtime/builder.py:183-185`)。

## 6. 局限性与边界(局限性)

1. **subprocess 沙箱不是安全边界。** 网络、文件系统(绝对路径)、进程三项 v1 均不隔离(模块 docstring 逐项自认,`logic/python_sandbox.py:7-18`)。env 白名单只防"顺手读凭证",硬编码路径仍可达凭证文件。蓄意对抗场景必须用 Docker 后端,而 Docker 依赖宿主装有 docker CLI 与镜像。
2. **Docker 后端不支持编排。** `docker_sandbox.py` 忽略 `dispatch_fn`(fd 透传细节是设计稿开放问题 5);配置 `python_exec = "docker"` 时若启用 `python_orchestrate`,沙箱内 `ctx` 为 `None`,脚本一调即 `NameError`。编排能力目前只属于 subprocess 后端——隔离最强的档反而没有编排。
3. **脚本墙钟含内核侧 syscall 时间。** 设计稿要求"脚本 wall_time 不含内核执行 syscall 的时间"(§3),实现是 `asyncio.wait_for(proc.communicate(), timeout=wall)` 总口径(`logic/python_sandbox.py:340`);慢工具会烧脚本预算,只能靠调大 `timeout` 参数缓解。锚点测试清单第 6 条无对应用例。
4. **syscall 通道能力窄。** 仅 `call_tool`/`invoke` 两种、串行阻塞语义;`ctx.spawn`/`wait`/`board`/`blob` 未过桥(设计 §2.2 的 `blob` 过桥未落地),并发 syscall(协议已预留 id)未支持——沙箱内做不了"前台保场、后台深想"。
5. **CodeScanner 只是辅助。** 正则模式扫描对混淆代码无效,能力上限已写入文档;防护主体是沙箱 + 权限,不是扫描器。
6. **记账口径粗糙。** InProcess 的 `mem_peak_mb` 恒 0;两个沙箱后端的 `cpu_ms` 以 `wall_ms` 充数(`logic/python_sandbox.py:367-368`);`merge_limits` 三级限额取紧仍是 `NotImplementedError`(`logic/limits.py:32-34`)。
7. **replay 的边界不因编排消失。** 编排 replay 仅当内部 syscall 命中确定性工具时逐字节复现;工具副作用仍按真实环境执行。
8. **省 token 是机会式的。** 编排默认关闭且需 manifest 显式声明;一两步调用直接调工具更简单(schema 描述自己也这么写)。

## 7. 引用

- 设计文档:`docs/DESIGN.md`(§9 Logic Kernel;§3.4 协程级并行注);`docs/CODE-ORCHESTRATION.md`(编排沙箱设计稿与 §12 落地偏差记录)
- 契约:`agent_os/src/agent_os/api/v1/logic.py`(`LogicKernel`/`ExecRequest`/`ExecResult`/`SkillError`/`ORCHESTRATE_TOOL`/`SYSCALL_PROTOCOL_VERSION`)
- 实现:`agent_os/src/agent_os/logic/inprocess.py`、`logic/python_sandbox.py`、`logic/docker_sandbox.py`、`logic/limits.py`;`kernel/logic_router.py`、`kernel/logic_context.py`、`kernel/runner.py`(`_run_code_frame`/`_syscall_dispatcher`/`_run_orchestration`);`tools/builtins.py`(`python_exec_tool`);`sidecars/builtins.py`(`CodeScanner`);`runtime/config.py`、`runtime/builder.py`
- 测试:`agent_os/tests/logic/test_orchestration.py`、`tests/logic/test_python_sandbox.py`、`tests/logic/test_docker_sandbox.py`、`tests/kernel/test_code_skills.py`
- 示例与配置:`agent_os/examples/research_pipeline/skills.yaml`;`instance/agent-os.toml`
