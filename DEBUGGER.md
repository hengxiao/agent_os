# Agent OS Debugger:断点 / 单步 / 检视 / 干预 / 时间旅行

> 状态:**已实现**(P1 内核原语 / P2 CLI REPL / P3 Web 后端 / P4 Web 调试台 /
> P5 时间旅行 + 周期 checkpoint)。
> 权威架构见 [DESIGN.md](DESIGN.md);运行器契约见 [RUNNERS.md](RUNNERS.md);
> 前端主题系统见 [DEBUG-UI-THEMES.md](DEBUG-UI-THEMES.md)(token 与状态色,
> 调试台组件沿用 [WEB-UI.md](WEB-UI.md) 的设计 token)。

代码锚点:内核原语 `agent_os/src/agent_os/kernel/debug.py`;CLI 前端
`agent_os/src/agent_os/host/cli/debug.py`;Web 后端
`agent_os/src/agent_os/host/web/run_manager.py`(P3/P5 段)与
`agent_os/src/agent_os/host/web/app.py`(`/api/debug/*`);Web 调试台
`static/js/components/debug-*.js`;时间旅行复用
`agent_os/src/agent_os/host/shared/replay.py`;周期 checkpoint
`agent_os/src/agent_os/kernel/checkpoint.py`(`PeriodicCheckpointer`)。

---

## 1. 暂停协议:阻塞 emit

调试器**不改 runner 的 agent loop**。`DebugController` 在内核装配期直接订阅
信号总线(`InProcessSignalBus` 按订阅序 await 全部订阅者);信号命中断点时,
订阅者 `await` 一个 `asyncio.Event` —— **emit 不返回,run 就地挂起**,
事件循环空转,可供 Web/SSE/CLI 命令通道使用(同 `ask_supervisor` 的就地
挂起模式)。前端的恢复命令(`resume`)设置事件,emit 带着 verdict(或
`None` 放行)返回,run 继续。

**为什么不经 SidecarSupervisor**:其 SYNC 通道有 2s 超时 fail-closed
(`sidecars/supervisor.py`)——阻塞式暂停必然超时被判否决,语义上也不同
(sidecar 是规则化监督,调试器是交互式控制)。DebugController 直接订阅总线,
且订阅序在 Telemetry/SignalHub **之后**(订阅顺序即记录顺序):暂停发生前,
该信号已落 `trace.jsonl`、已推 SSE,前端看到的轨迹永远包含暂停点本身。

`Pause` verdict 不复用:它现状是"带标签的 abort"(服务 BudgetGuard 降级
通知),不是真暂停。

其他保证:

- **错误隔离**:controller 自身异常捕获 + log + 放行,不拖垮 run(与总线
  语义一致);
- **自动放行**:run 结束(`run.finished`/`run.aborted`)或前端 detach
  时,所有阻塞中的 wait 被放行,run 不悬挂;
- **verdict 仲裁点**:`Stop` verdict 只在 `pre:step` / `pre:tool.call`
  返回(runner 只在这两点仲裁 pre verdict);在其余暂停点(`post:*` /
  `pre:frame.pop` / `pre:skill.invoke`)收到 stop 会推迟到下一个可仲裁的
  pre 信号落地。`pre:frame.push/pop` 上调试器永远返回 `None`(其 verdict
  有纠偏语义,调试器只观察)。

## 2. 断点模型

一个断点 = **kind + 名字 glob + 命中计数**(`Breakpoint`,条件断点 v1 不做):

| kind | 触发信号 | match 语义 |
|---|---|---|
| `step` | `pre:step`(每帧每步) | 忽略 |
| `tool_call` | `pre:tool.call` | 工具名 glob(fnmatch,`fs_*` 等) |
| `skill_invoke` | `pre:skill.invoke` | 技能名 glob |
| `error` | `post:tool.call` 且 `payload.ok=false` | 忽略 |

命中即累计 `hits`(前端可见,Web SSE 会发 `bp_hit` 差分事件)。断点
`enabled=False` 不参与匹配。

**一次性步数断点**(P5,`until=N`,仅 `step` kind):前 N-1 次命中只计数
不停下,第 N 条 `pre:step` 才暂停并自我禁用——`--until-step` /
`until_step` 的实现,语义是"直达第 N 步"。

## 3. 单步语义(嵌套帧行为)

恢复命令全集:`continue` / `step_into` / `step_over` / `step_out` / `stop`。
单步的边界判断依赖调试器维护的**帧栈簿记**(`pre:frame.push` 进 /
`pre:frame.pop` 出):

- `continue`:放行,命中下一断点再停;
- `step_into`:任意帧的下一条 `pre:step` 即停——当前帧调用子技能时,
  停在**子帧**的第一步;
- `step_over`:记住当前帧;**同帧**的下一条 `pre:step` 停(子帧内的 step
  不停,frame_id 天然不同),当前帧没有更多 step 时停在该帧的
  `pre:frame.pop`;
- `step_out`:放行到当前帧的 `pre:frame.pop` 停;
- `stop`:`Stop` verdict,run 以 `RunAborted` 收尾(走正常中止路径,
  checkpoint 落盘)。

另有不属于恢复命令的 `pause`(仅 `running` 可发,与恢复命令互反):
**随时暂停**,GDB 里发 signal 的语义——前端置暂停请求,run 在**下一个
可仲裁信号**(`pre:step` / `pre:tool.call`,与 stop verdict 同一落地集合)
挂起,暂停原因 `reason="pause"`;run 正在 LLM 调用/工具执行内部时没有
信号发出,pause 落在该调用结束后的下一条 pre 信号。

暂停在 `pre:frame.pop` 时该帧已视为弹出,"当前帧"取栈顶父帧。

## 4. 干预(仅 paused 可发)

- **modify**(改工具参数):仅暂停在 `pre:tool.call` 有效。本次被阻塞的
  emit 返回 `Modify(patch)`,runner 把 patch 合并进 `call.args` 后照常
  分发——改的是**这一次调用**的参数,不改技能/上下文;改完即放行。
- **inject**(注入消息):经 `RunControl.inject_message` 向指定帧(缺省
  暂停帧)追加一条 `role=user, source=injected` 的消息,下一步 LLM
  调用可见;注入后放行。回放模式下注入同样真实生效(后续 LLM 响应仍按
  脚本重放,见 §6 边界)。

两个干预都只影响**本次 run 的内存态**;产物(trace/checkpoint)如实记录
干预后的轨迹。

## 5. 前端用法

### 5.1 CLI REPL(`agent-os debug`)

```
agent-os debug <skill> --input '<json>|@file' [--config ...] [--artifacts ...]
agent-os debug --replay <run_id> [--until-step N]        # P5 时间旅行(§6)
```

pdb 语义:会话开出即加一次性 `step` 断点,run 启动即停在第一条
`pre:step`,给用户设断点的机会。命令表(`h` 在 REPL 内查看):

| 命令 | 语义 |
|---|---|
| `b tool <glob>` / `b skill <glob>` / `b step` / `b error` | 加断点 |
| `del <bp_id>` / `info b` | 删断点 / 断点列表(含 hits) |
| `c` | continue |
| `si` / `so` / `out` | step_into / step_over / step_out |
| `stop` | 中止 run(退出码 3) |
| `q` | 摘下调试器,run 跑完(stdin EOF 等价) |
| `bt` | 帧栈(`*` 标记暂停帧) |
| `i [--messages] [--working]` | 检视暂停帧(渲染对齐 `inspect`) |
| `mod <json>` | 改本次工具调用参数(仅 `pre:tool.call`) |
| `inj <text>` | 向暂停帧注入一条用户消息 |

REPL 与 run 跑在**同一事件循环**(调试原语要求);读命令走
`asyncio.to_thread(stdin.readline)`,故可脚本化喂 stdin 做回归测试
(`tests/cli/test_debug.py`)。

### 5.2 Web API + 调试台

`POST /api/debug/sessions` 开会话(开会话即起 run;每 run 至多一个活跃
会话,冲突 409);请求体两形态互斥:`{skill, input, breakpoints?}`(live)
或 `{replay_run_id, until_step?, breakpoints?}`(replay,响应带
`mode: "replay"`)。`breakpoints` 在 run 启动**之前**注册——调试启动即断
是确定性的(起 run 后再加断点会竞态错过早期信号)。

| 端点 | 语义 |
|---|---|
| `GET /api/debug/sessions/{sid}` | 会话快照:state / pause_point / 断点(含 hits)/ 帧栈 |
| `POST .../breakpoints` · `DELETE .../breakpoints/{bp_id}` | 断点增删 |
| `POST .../command` | `{cmd: continue\|step_into\|step_over\|step_out\|stop}`(仅 paused);`{cmd: pause}`(仅 running,随时暂停) |
| `POST .../modify` · `POST .../inject` | 干预(仅 paused) |
| `GET .../frames/{fid}` | 帧检视(live 内存态:暂停时 checkpoint 尚未落盘) |
| `GET .../stream` | SSE:`state` → `bp_hit`/`paused`/`resumed` → `run_end` |
| `POST .../rerun` | 重新运行:停掉并结束当前会话,以创建参数(skill/input/启动断点)重开新会话 |
| `DELETE /api/debug/sessions/{sid}` | detach 放行,run 跑完 |

rerun 的依据是 live 会话创建时回填的 `origin`(skill/input/skill_set/启动
断点,`kernel/debug.py` 的 `DebugSession.origin`);快照带
`rerunnable`(replay/CLI 会话无 origin → rerun 归 400)。旧会话仍活跃时
先收尾:paused 走 `stop` 命令(正常中止路径,checkpoint 落盘),running
置中止标志,随后 detach + 清注册表(同 DELETE 语义);已结束的会话直接
重开。调试台控制条在 `rerunnable` 时出「⟳ 重新运行」按钮,成功后跳新会话。

REST 线程与 run worker 线程之间经**跨线程命令桥**
(`run_coroutine_threadsafe` 投递到 worker 事件循环)执行命令——
`asyncio.Event` 无线程亲和性,REST 线程直接 `set()` 会撞线程检查。

调试台页面:`#/debug`(首页:技能/输入/启动前断点)与
`#/debug/<session_id>`(控制条 + 调用栈 + 执行轨迹 gutter 断点 + 检视器,
组件见 `static/js/components/debug-*.js`,主题 token 见
[DEBUG-UI-THEMES.md](DEBUG-UI-THEMES.md))。

## 6. 时间旅行(P5)

**时间旅行 = 回放 + 调试会话**。从产物目录
(`<artifacts>/runs/<run_id>/` 的 `meta.json` + `trace.jsonl` +
`checkpoint.json`)经 `build_mock_script` 重建 MockProvider 脚本,
`replace_providers` 换掉内核 provider 面,再以原 run 的 skill/input 起
**新 run** 并挂调试会话——断点、单步、检视、干预与 live 调试完全一致,
停点序列可逐位复现。

- CLI:`agent-os debug --replay <run_id> [--until-step N]`;
- Web:`POST /api/debug/sessions {replay_run_id, until_step?}`,响应带
  `mode: "replay"`;
- `--until-step N` / `until_step`:一次性步数断点(§2),run 直达第 N 条
  `pre:step` 才暂停——"rewind 到第 N 步"的入口。

**回放边界**(`host/shared/replay.py` 模块 docstring 的明示边界,务必读):
runner 只在帧上下文追加 LLM 响应,故 trace 中第 N 个属于帧 F 的
`post:llm.response` ↔ checkpoint 中帧 F 的第 N 条 assistant 消息,按序
重建脚本即可确定性重放整棵帧树——**LLM 不碰真实 API,按 trace 记录值
重放;工具副作用仍按真实环境重跑**(文件、shell 等会再执行一次)。
回放不问第二次 supervisor(按记录值走)。

## 7. 周期 checkpoint(P5)

`RunConfig.checkpoint_interval: int = 0`(**0 = 关**,N = 每 N 步):
内核装配期挂载的订阅者(`PeriodicCheckpointer`)计 `post:step`,每 N 步
调 `dump_checkpoint` **覆盖写** `<artifacts>/runs/<run_id>/checkpoint.json`
——该文件语义是"**最近现场**"(不回溯保留历史快照),崩溃恢复点从
"终态"提前到"最近 N 步";run 收尾的终态快照仍由宿主产物路径写同一文件。
落盘失败捕获 + log,不拖垮 run。

配置流(默认关):

- TOML:`[run] checkpoint_interval = 20`;
- CLI:`agent-os run ... --checkpoint-interval N`(覆盖本次 run);
- Web:`POST /api/runs {..., "overrides": {"checkpoint_interval": N}}`
  (`overrides` 只影响本次 run)。

## 8. 已知限制(v1 不做)

- **条件断点**(表达式求值):断点匹配只有 kind + 名字 glob + 命中计数
  (`until` 是唯一的计数型变体);
- **跨线程唤醒之外的并发控制**:一个会话一个调试端(Web 或 CLI),
  多客户端并发 resume 以后到的为准,无锁;
- **工具副作用回放**(`replay_records` 接线):时间旅行沿用"LLM Mock
  回放 + 工具真实重跑"边界,有副作用的工具在回放中会再执行一次;
- **历史快照序列**:周期 checkpoint 只保留"最近现场",不留
  `checkpoint.<step>.json` 历史序列;
- Web 调试台首页暂未提供 replay 会话表单(replay 会话走 API/CLI 发起,
  调试台 `#/debug/<session_id>` 对 replay 会话照常可用)。
