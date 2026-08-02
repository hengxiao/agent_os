# 调试器:GDB 语义与干预

> 章次:12 · 状态:已实现(P1 内核原语 / P2 CLI REPL / P3 Web 后端 / P4 Web 调试台 / P5 时间旅行 + 周期 checkpoint;§6 列出 v1 明示不做的余项) · 依据:`docs/DEBUGGER.md`、`agent_os/src/agent_os/kernel/debug.py`、`agent_os/src/agent_os/host/web/run_manager.py`(P3/P5 段)、`agent_os/src/agent_os/host/web/app.py`(`/api/debug/*`)、`agent_os/src/agent_os/host/web/static/js/components/debug-view.js`、`agent_os/tests/web/test_debug_api.py`

## 1. 概述

调试器把 run 当进程调试:断点(glob 匹配)、单步(into/over/out)、随时暂停(SIGINT 语义)、帧检视、干预(改参数/注消息),以及"回放 + 调试会话"的时间旅行。架构上它是信号总线上的一个**普通订阅者**:内核 agent loop 零改动,缺省不装配即零行为变化(`kernel/debug.py:18-20`);命中断点时以"阻塞 emit"让 run 就地挂起,事件循环空转供 Web/SSE/CLI 命令通道使用。它与遥测共用同一份信号 WAL——遥测回答"发生了什么",调试器回答"正在发生什么,要不要改"。

## 2. 动机与背景(原因)

**为什么需要它**:agent run 非确定、耗时长、按步烧钱。事后翻 `trace.jsonl` 只能回答"发生了什么",回答不了"它下一步打算干什么",更无法在错误参数落地前拦住它。执行摘要把"运行不可观测"列为结构性问题之一,调试器是遥测的交互式补全:同一份信号流,从"只读审计"升级为"可读可改的暂停现场"。

**为什么不放进内核 loop**:微内核判据三条(没有它 loop 无法推进 / 它是权限否决仲裁点 / 它是子系统间唯一公共通道)调试器一条都不满足——它是纯观察者加偶发干预者。因此以 `KernelBuilder.debug_controller()` 可选装配,缺省 `None` 时不订阅任何信号(`kernel/debug.py:3-21`)。内核边界没有为调试需求扩大一分。

**为什么不经 SidecarSupervisor**:这是最关键的一次路由决策。sidecar 的 SYNC 通道有 2s 超时且 fail-closed(`sidecars/supervisor.py`,见 `docs/DEBUGGER.md:28-30`)——交互式暂停必然超时被判否决;且两者语义不同:sidecar 是规则化监督(毫秒级、自动),调试器是交互式控制(分钟级、人驱动)。`Pause` verdict 也不复用:它现状是"带标签的 abort"(服务 BudgetGuard 降级通知),不是真暂停(`docs/DEBUGGER.md:34-35`)。于是 `DebugController` 直接订阅总线,且订阅序在 Telemetry/SignalHub **之后**(订阅顺序即记录顺序):暂停发生前,该信号已落 `trace.jsonl`、已推 SSE,前端看到的轨迹永远包含暂停点本身(`kernel/debug.py:18-20`)。

**为什么是 GDB 语义**:映射表已把 run 定位为进程、SkillFrame 栈定位为调用栈,GDB 就是最自然的调试心智模型——断点、step into/over/out、SIGINT 暂停都是被验证过三十年的交互语义,重新发明一套只会增加学习成本。

## 3. 问题陈述(解决的问题)

1. **错误参数落地即事实**:run 在 `pre:tool.call` 携带错误参数(如 `code = "result = 0 + 1"`),事后 trace 能看到,但工具已执行,无法改。需要在分发前那一刻挂起 run、检视参数、改掉再放行。
2. **嵌套帧里"下一步"无定义**:F1 调用子技能 F2 时,"step over"应跨过整棵 F2 子树停在 F1 的下一条 step,而"step into"应停进 F2 第一步——没有帧栈簿记,这两个语义都无法实现。
3. **失控 run 只能杀不能看**:预算烧穿或行为异常时,唯一手段是 abort;缺少 GDB 里"发 signal 在下一个安全点停下"的随时暂停。
4. **崩溃现场丢失**:调试暂停时 checkpoint 尚未落盘,检视只能读内核内存态;而进程崩溃时连终态 checkpoint 都可能没有,恢复点在"终态"而非"最近 N 步"。
5. **停点不可复现**:live 调试中偶然走到第 7 步才发现问题,下次想直接回到第 7 步——LLM 非确定,重跑一遍停点序列就变了。
6. **多线程宿主下的命令投递**:Web REST 线程与 run worker 线程各自有事件循环,REST 线程直接 `set()` 会话的 `asyncio.Event` 会撞 `loop.call_soon` 的线程检查(`run_manager.py:822-829`)。

## 4. 设计与机制(解决的方法)

### 4.1 暂停协议:阻塞 emit

调试器不改 runner 的 agent loop。`DebugController` 在装配期直接订阅 `InProcessSignalBus`(按订阅序 await 全部订阅者);信号命中断点时,订阅者 `await` 一个 `asyncio.Event`——**emit 不返回,run 就地挂起**,与 `ask_supervisor` 同一模式(`kernel/debug.py:5-9`):

```
run worker 事件循环                        调试前端(REST 线程 / CLI)
─────────────────────                     ─────────────────────────
runner ── emit(pre:tool.call) ──▶ SignalBus(按订阅序 await)
                                    ├─ Telemetry  → trace.jsonl 已落盘
                                    ├─ SignalHub  → SSE 已推送
                                    └─ DebugController._on_signal
                                         │ 命中断点(_match_breakpoints)
                                         ▼
                                    DebugSession._pause(kernel/debug.py:333)
                                    记录 pause_point → state=paused
                                    await resume_event.wait() ──────┐
              ◀── emit 不返回,run 就地挂起;事件循环空转 ──         │ resume()
              ◀── SSE/REST/CLI 照常可用(同循环空转)────          │ (经跨线程
                                         │ event.set() ◀────────────┘  命令桥投递)
                                         ▼
                                    _resume_verdict:None 放行 /
                                    Modify(patch) / Stop(reason)
                                         ▼
                                    emit 返回,run 继续
```

三条不变量保护这个协议(`docs/DEBUGGER.md:37-47`):

- **错误隔离**:controller 自身异常捕获 + log + 放行,调试器故障不拖垮 run(`kernel/debug.py:491-492`);
- **自动放行**:run 结束(`run.finished`/`run.aborted`)或前端 detach 时,所有阻塞中的 wait 被放行,run 不悬挂(`kernel/debug.py:481-486`);
- **verdict 仲裁点约束**:`Stop` verdict 只在 `pre:step` / `pre:tool.call` 返回(`_STOP_VERDICT_SIGNALS`,`kernel/debug.py:65`)——runner 只在这两点仲裁 pre verdict;其余暂停点收到 stop 记入 `_pending_stop`,推迟到下一个可仲裁 pre 信号落地(`kernel/debug.py:262-264`)。`pre:frame.push/pop` 上调试器永远返回 `None`(其 verdict 有纠偏语义,调试器只观察;但 step_out 可以在 `pre:frame.pop` **暂停**,只是不放 verdict)。

### 4.2 断点模型

一个断点 = **kind + 名字 glob + 命中计数**(`Breakpoint`,`kernel/debug.py:74-88`;条件断点 v1 不做):

| kind | 触发信号 | match 语义 |
|---|---|---|
| `step` | `pre:step`(每帧每步) | 忽略 |
| `tool_call` | `pre:tool.call` | 工具名 glob(fnmatch,如 `fs_*`) |
| `skill_invoke` | `pre:skill.invoke` | 技能名 glob |
| `error` | `post:tool.call` 且 `payload.ok=false` | 忽略 |

> 注:执行摘要 §6.2 写断点为"三类(pre:tool.call / step / error)",代码实为四类(多 `skill_invoke`,`kernel/debug.py:52`),以代码为准。

命中即累计 `hits`(SSE 发 `bp_hit` 差分事件);`enabled=False` 不参与匹配。P5 增加**一次性步数断点**(`until=N`,仅 `step` kind):前 N-1 次命中只计数不停下,第 N 条 `pre:step` 才暂停并自我禁用(`kernel/debug.py:299-306`)——"直达第 N 步"的 rewind 入口。

### 4.3 单步语义与随时暂停

恢复命令全集:`continue` / `step_into` / `step_over` / `step_out` / `stop`(`RESUME_COMMANDS`,`kernel/debug.py:60`)。单步边界依赖调试器维护的**帧栈簿记**(`pre:frame.push` 进 / `pre:frame.pop` 出,`kernel/debug.py:249-260`):

| 命令 | 停点判定(`_step_stop`,`kernel/debug.py:309-331`) |
|---|---|
| `continue` | 放行,命中下一断点再停 |
| `step_into` | 任意帧的下一条 `pre:step`(进子帧第一步) |
| `step_over` | **同帧**的下一条 `pre:step`(frame_id 天然过滤子帧);帧内无更多 step 时停在该帧 `pre:frame.pop` |
| `step_out` | 当前帧的 `pre:frame.pop` |
| `stop` | `Stop` verdict → `RunAborted`(正常中止路径,checkpoint 落盘) |

另有 `pause`(仅 `running` 可发,与恢复命令互反):GDB 发 signal 的语义——置 `_pause_requested`,run 在**下一个可仲裁信号**挂起,`reason="pause"`(`kernel/debug.py:204-215`)。run 正在 LLM 调用/工具执行内部时没有信号发出,pause 落在该调用结束后的下一条 pre 信号——与 GDB"下一个安全点停下"一致。暂停在 `pre:frame.pop` 时该帧已视为弹出,"当前帧"取栈顶父帧(`_current_frame_id`,`kernel/debug.py:385-389`)。

会话状态机:`armed`(待绑定 run)→ `running` ⇄ `paused` → `detached`(终态)(`kernel/debug.py:67-71`)。

### 4.4 干预(仅 paused 可发)

- **modify**(改工具参数):仅暂停在 `pre:tool.call` 有效。被阻塞的 emit 返回 `Modify(patch)`,runner 把 patch 合并进 `call.args` 后照常分发(`kernel/debug.py:168-181`)——改的是**这一次调用**的参数,不改技能、不改上下文;改完即放行(内部走 `resume(continue)`)。
- **inject**(注入消息):经 `RunControl.inject_message` 向指定帧(缺省暂停帧)追加 `role=user, source=injected` 的消息,下一步 LLM 调用可见;注入后放行(`kernel/debug.py:183-191`)。回放模式下注入同样真实生效(后续 LLM 响应仍按脚本重放)。

两个干预都只影响**本次 run 的内存态**;产物(trace/checkpoint)如实记录干预后的轨迹——干预不伪造历史。

### 4.5 Web 后端:会话 API、跨线程命令桥与 SSE

`POST /api/debug/sessions` 开会话即起 run,请求体两形态互斥:`{skill, input, breakpoints?}`(live)或 `{replay_run_id, until_step?, breakpoints?}`(replay,响应带 `mode: "replay"`);每 run 至多一个活跃会话,冲突 409(`app.py:1071-1120`)。**breakpoints 在 run 启动之前注册**——mock brain 毫秒级推进,起 run 后再加断点会竞态错过早期信号,启动即断因此是确定性的(`run_manager.py:686-694`)。端点族:会话快照 `GET`、断点增删、`command`(仅 paused;`pause` 仅 running)、`modify` / `inject`(仅 paused)、`frames/{fid}`(live 内存态:暂停时 checkpoint 尚未落盘)、`stream`(SSE)、`rerun`、`DELETE`(detach 放行)。

**跨线程命令桥**(`_in_debug_loop`,`run_manager.py:822-846`):会话的 `asyncio.Event` 绑在 run worker 线程的循环上,REST 线程直接 `set()` 会撞线程检查;`_debug_loops` 逐会话记录 worker 循环(run.started 时捕获),resume/modify/inject/detach 一律经 `run_coroutine_threadsafe` 投递过去执行并等结果。worker 循环已退出(run 结束)时直接调用——此时没有阻塞中的 wait,`Event.set()` 无 waiter 是纯内存操作。

**调试 SSE**(`app.py:1226-1301`):连接即发 `state` 快照(`prev_state` 不取当前态,已暂停的会话立即补发 `paused`,照顾迟到客户端),随后以 `_DEBUG_SSE_POLL=0.1s`(`app.py:78`)轮询会话内存态发差分事件:`bp_hit`(hits 增长)→ `paused` / `resumed` → `run_end`(detached 后短暂等终态落盘,最多 50 轮);空闲 15s 发 keepalive(`_SSE_KEEPALIVE`,`app.py:74`)。轮询而非 await 的原因:`wait_paused` 的 Event 绑在 worker 循环上,SSE 循环不能 await,轮询是最简可靠方案(`app.py:1228-1231`)。

**rerun**(`run_manager.py:722-754`):依据 live 会话创建时回填的 `origin`(skill/input/skill_set/启动断点,`kernel/debug.py:122-124`);replay/CLI 会话无 origin → 400,快照以 `rerunnable` 告知前端。旧会话仍活跃时先收尾:paused 走 `stop` 命令(正常中止路径,checkpoint 落盘),并等 stop verdict 落地(上限 `_RERUN_STOP_TIMEOUT=10s`,`run_manager.py:115`)再 detach——立即 detach 会让 `_resume_verdict` 先见 DETACHED 吞掉 stop,旧 run 变成跑完而非中止(时序竞争,`run_manager.py:737-739`)。

### 4.6 时间旅行与周期 checkpoint(P5)

**时间旅行 = 回放 + 调试会话**。从产物目录(`meta.json` + `trace.jsonl` + `checkpoint.json`)经 `build_mock_script` 重建 MockProvider 脚本,`replace_providers` 换掉内核 provider 面,再以原 run 的 skill/input(取自 `meta.json`)起**新 run** 并挂调试会话(`run_manager.py:756-800`)——断点、单步、检视、干预与 live 完全一致,停点序列逐位复现。`until_step=N` 注册一次性步数断点,run 直达第 N 条 `pre:step` 才暂停。回放边界(`host/shared/replay.py` 模块 docstring):**LLM 不碰真实 API,按 trace 记录值重放;工具副作用仍按真实环境重跑**。

**周期 checkpoint**:`RunConfig.checkpoint_interval`(默认 0=关)挂载 `PeriodicCheckpointer`,计 `post:step`,每 N 步 `dump_checkpoint` **覆盖写** `checkpoint.json`——该文件语义是"最近现场",崩溃恢复点从"终态"提前到"最近 N 步";落盘失败捕获 + log,不拖垮 run(`docs/DEBUGGER.md` §7)。配置流:TOML `[run] checkpoint_interval = 20`、CLI `--checkpoint-interval N`、Web `overrides`(只影响本次 run)。

### 4.7 调试台前端(P4)

页面 `#/debug`(首页:技能/输入/启动前断点表单,`debug-home.js`)与 `#/debug/<session_id>`(`debug-view.js:1-19`):顶部控制条(会话状态 + Continue/Into/Over/Out/Stop,非 paused 禁用,`rerunnable` 时出 ⟳ 重新运行按钮,`debug-view.js:435-436`)、左栏调用栈(暂停帧高亮)+ 断点列表、中栏执行轨迹(复用 trace.js 行语言,行首 gutter 点击切换断点,暂停行高亮 + ▶)、右栏检视器(暂停点 payload + 选中帧 messages + Modify/Inject 表单,`debug-view.js:686-713`)。数据源:会话快照 + run 信号流(调试 SSE 不带 run 信号,轨迹走 `/api/runs/{run_id}/signals`)+ 调试 SSE;EventSource 为主,断线回退 2s 轮询快照,2s ticker 常驻刷轨迹(信号数变化才重绘,不打扰滚动)。modify 预填当前 args,同一暂停点(指纹未变)保留用户编辑,新暂停点才重填(`debug-view.js:649-696`)。

## 5. 效果与验证(效果)

调试器测试三层共 **44 例**:内核原语 `tests/kernel/test_debug.py`(14 例)、CLI REPL `tests/cli/test_debug.py`(12 例)、Web API `tests/web/test_debug_api.py`(18 例),全部在项目基线(Python 812 例 + 前端 24 个测试文件)内常绿。停点断言共用同一事实锚点:`demo.fib` n=3 的信号序列(F1 step1 → invoke F2(一步即弹)→ F1 step2 内 `pre:tool.call(system.python.exec)` → F1 step3 终答 → F1 pop)(`tests/web/test_debug_api.py:21-24`)。关键断言:

- **全流程**(`test_debug_full_flow`):启动即断(hits 累计、帧栈非空)→ step_into 进子帧(depth=2,frame_id 变化)→ step_out 停在子帧 `pre:frame.pop` → continue 命中 tool_call 断点(`reason="breakpoint"`)→ live 帧检视读到内存态 messages → modify 把 `code` 改为 `"result = 41"`,run 跑完,**结果随改后参数变化**:`{"seq": [0, 1, 41]}`;run 结束会话自动 detached(:84-145);
- **step_over 边界**:同帧下一条 pre:step 停(中间跨过整个子帧);帧尾停在 `pre:frame.pop`(:148-174);
- **inject 落痕**:注入消息以 `role=user, source=injected` 出现在帧上下文,frame_id 缺省 = 暂停帧(:182-204);
- **pause 语义**:自由运行中 pause 落在下一条可仲裁信号,`reason="pause"`;若 pause 到达前 run 已跑完则归 409——两个分支都是正确语义(:297-314);
- **错误语义矩阵**:未知会话 404、未知断点 kind 400、未知 cmd 422、非 paused 发 command/modify/inject 409、每 run 第二个会话 409、校验错 200+`failed`(:337-404);
- **SSE 流**:`state` → `bp_hit(hits=2)` → `paused` → `resumed` → `run_end(done)`,已暂停会话连接后立即补发 `paused`(:426-486);
- **时间旅行**:replay 会话响应带 `mode="replay"`,停点序列与 live 一致;`until_step=2` 直达第 2 条 `pre:step`(子帧);回放结果与原 run 逐位一致(`{"seq": [0, 1, 1]}`)(:495-538)。

涟漪效应:调试器是**信号契约的第一个交互式消费者**,证明了"订阅序 + 阻塞 emit"足以承载人在环路的分钟级交互,同一模式随后支撑了升权确认的挂起-恢复闭环;`rerunnable`/`origin` 让"以创建参数重开 run"成为宿主层通用能力;CLI REPL 与 run 同事件循环 + stdin 脚本化喂命令(`asyncio.to_thread(stdin.readline)`)成为 CLI 交互组件的回归测试范式(`docs/DEBUGGER.md:129-131`)。

## 6. 局限性与边界(局限性)

1. **无条件断点**:匹配只有 kind + 名字 glob + 命中计数(`until` 是唯一计数型变体);无法表达"第 3 次且参数含 X 才停"(`docs/DEBUGGER.md` §8)。
2. **回放中工具副作用真实重跑**:时间旅行沿用"LLM Mock 回放 + 工具真实重跑"边界,有副作用的工具(文件、shell、网络)在回放中会再执行一次;`replay_records` 接线未做。回放调试不是沙箱。
3. **周期 checkpoint 只留"最近现场"**:覆盖写 `checkpoint.json`,不留 `checkpoint.<step>.json` 历史序列;"回到第 N 步"只能靠 replay 重放(重执行),不能从历史快照恢复(状态还原)。
4. **pause 精度受信号边界限制**:run 在 LLM 调用/工具执行内部时没有信号发出,pause 落在该调用结束后——一次 30s 的 LLM 调用意味着最长 30s 的暂停延迟;无法真中断调用本身。
5. **stop 在非仲裁点延迟落地**:在 `post:*` / `pre:frame.pop` / `pre:skill.invoke` 暂停时发 stop,verdict 推迟到下一个 `pre:step`/`pre:tool.call` 才生效,其间 run 会继续推进若干工作(包括一次 LLM 调用)。
6. **暂停中的 run 无看门狗**:`resume_event.wait()` 无超时;Web 客户端关掉标签页不等于 detach(SSE 断线可重连是特性),被遗弃的 paused 会话会让 run 无限期悬挂,只能靠显式 `DELETE` / `rerun` 收尾。
7. **并发控制最简化**:一个会话一个调试端(Web 或 CLI);多客户端并发 resume 无锁,以后到者为准。
8. **干预面窄**:modify 只能改 `pre:tool.call` 的本次参数(不能改 LLM 请求、不能改 working 内存);inject 只能追加 user 消息。两者不持久化——改完这次调用,技能定义与后续 run 不受影响。
9. **调试台首页无 replay 表单**:replay 会话只能经 API/CLI 发起(调试台 `#/debug/<session_id>` 对已开的 replay 会话照常可用)。

## 7. 引用

- 设计文档:`docs/DEBUGGER.md`(暂停协议/断点/单步/干预/时间旅行/已知限制)
- 内核原语:`agent_os/src/agent_os/kernel/debug.py`(`DebugController` / `DebugSession` / `Breakpoint`)
- CLI 前端:`agent_os/src/agent_os/host/cli/debug.py`(`agent-os debug` REPL)
- Web 后端:`agent_os/src/agent_os/host/web/run_manager.py`(P3 会话管理 + 跨线程命令桥;P5 replay/rerun)、`agent_os/src/agent_os/host/web/app.py`(`/api/debug/*`,:1071-1301)
- Web 前端:`agent_os/src/agent_os/host/web/static/js/components/debug-view.js`、`debug-home.js`、`breakpoint-list.js`
- 回放:`agent_os/src/agent_os/host/shared/replay.py`(`build_mock_script` / `replace_providers`,模块 docstring 为边界权威)
- 周期 checkpoint:`agent_os/src/agent_os/kernel/checkpoint.py`(`PeriodicCheckpointer`)
- 测试:`agent_os/tests/kernel/test_debug.py`、`agent_os/tests/cli/test_debug.py`、`agent_os/tests/web/test_debug_api.py`
- 相邻文档:`docs/DESIGN.md`(内核与信号总线)、`docs/RUNNERS.md`(宿主)、`docs/DEBUG-UI-THEMES.md`(调试台主题 token)
