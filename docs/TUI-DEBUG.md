# 终端 TUI Agent 调试器设计(GDB 操作模型·以 skill 调用栈为基础)

> 版本:v0.1(设计稿)
> 上位文档:docs/DEBUGGER.md、docs/whitepaper/zh/12-debugger.md(调试器语义权威)、
> docs/TUI-DOC.md(TUI 宿主:内核/呈现层/键位/主题契约);
> 相邻文档:DEBUG-UI-THEMES.md(主题契约与终端词汇)、APP-MODEL.md(管道)。
> 本文定义 TUI 宿主的第二个 app:**agent 调试器**。协议内核、调试后端、
> 内核调试原语**零改动**;本文只定义 app 层(数据源/布局/命令语言/键位/主题)。

---

## 0. 定位

```
CLI REPL(agent-os debug)──┐
Web 调试台(#/debug)───────┼─→ /api/debug/*(REST + SSE;state 服务端权威)
TUI 调试器(本文)──────────┘   ← 同一组端点,零新通道
```

- 代码位置:`agent_os/src/agent_os/host/tui/apps/debugger/`(与 doc_editor 平级的
  第二个 app);复用 TUI 宿主全部内核与呈现层(`kernel/` 协议内核、`tui/`
  cells/keys/keymap/theme/motion/sty);
- **client 模式**:打既有 agent-os-web 的 `/api/debug/*`(host/web/app.py:1489-1652,
  **路径无 /platform 前缀**,base = `http://127.0.0.1:8391`);
- 离线模式:直读 run 产物目录(trace.jsonl + checkpoint + meta)做**只读**回放,
  无控制面("产物即真相"先例,同 doc_editor OfflineDocSource 语义);
- **内嵌模式显式不做**(§10):无服务进程内调试已由 CLI REPL
  (`host/cli/debug.py`)覆盖,TUI 不重复造第二个形态。

### 与既有调试前端的分工

| 前端 | 形态 | 场景 |
|---|---|---|
| CLI REPL | 无服务内嵌、脚本化(stdin 喂命令) | CI/回归、无 web 环境 |
| Web 调试台 | 浏览器三栏 + 鼠标 gutter | 图形环境、长会话检视 |
| Web 控制台(#/debug/<sid>/console) | 浏览器内 GDB 命令语言,与 TUI 同方言 | 图形环境、键盘流 |
| **TUI 调试器(本文)** | 有服务全屏、GDB 命令语言 | SSH/终端肌肉记忆、键盘流 |

### 使用原则(锁死)

**语义是借内核的,操作是学 GDB 的。**

1. **命令窗是第一界面,不是兜底。** GDB 用户活在 `(gdb)` 提示符里;TUI 调试器
   的命令窗常驻底部(等价 GDB TUI 的 command window),所有调试动作都有命令
   形式。快捷键只是命令的回声——状态行显示当前动作的等价命令,教用户命令语言。
2. **命令方言逐字学 GDB。** 命令名、缩写、参数语法对齐 GDB 真实习惯
   (`b/c/s/n/finish/until/bt/frame/up/down/info b/disable/delete/run/kill/
   detach/q`);agent OS 特有概念走 GDB 最近邻映射(§5),不自造词汇。
3. **GDB 的小习惯全移植**:裸 Enter 重复上一命令;C-c 中断 = pause(SIGINT);
   每次停止自动打印停止行;frame/up/down 选帧即改检视上下文;↑/↓ 命令历史;
   断点数字编号 `1..N`(`info b` 的 Num 列,后端 bp_id 只做内部映射)。
4. **GDB TUI 式窗口布局**:大窗 = 源码窗等价物(信号轨迹,▶ = 程序计数器),
   辅窗 = backtrace + 断点表,底部 = 命令窗;`C-x o` 切焦点,方向键在焦点窗滚动。
5. 红线沿用 TUI-DOC:widget 三铁律(state 可序列化 / 事件不出海 / render 纯)、
   组件零按键分支(intent 层)、组件零主题分支、双编码(色+符号)、
   **客户端零裁决**(modify/inject 的权限裁决在服务端,TUI 只是出海)。
6. 诚实优先于形似:后端没有的能力(enable/disable 端点、条件断点)命令照收、
   回人话拒绝(§6 错误语式),不做假功能。

## 1. 心智模型:run 即进程,SkillFrame 栈即调用栈

语义映射与 12-debugger.md §2 一致;本文新增**操作习惯映射**:

| GDB | TUI 调试器 |
|---|---|
| inferior 进程 | run |
| 调用栈(C stack) | SkillFrame 栈(会话快照 `frame_stack`,app.py:522-537) |
| `(gdb)` 提示符命令窗 | 常驻命令窗,提示符 `(adb)` |
| 源码窗 + PC 行 | 信号轨迹窗 + `▶` 暂停行 |
| `bt` / `frame N` / `up` / `down` | 调用栈窗 + 同名命令 |
| `b main` / `b file:line` | `b fs_*` / `b skill:fib` / `b step` / `b error`(§5 spec 语法) |
| C-c(SIGINT) | `pause`(下一个可仲裁信号挂起,kernel/debug.py:204-215) |
| 裸 Enter 重复上一命令 | 同(白名单语义,§5) |
| `run` / `kill` / `detach` / `quit` | 开会话 / stop / detach 放行 / 退 TUI(会话保留) |
| `[Inferior 1 exited normally]` | `Run finished: done`(命令窗打印,§6) |

会话状态机镜像内核:armed → running ⇄ paused → detached(kernel/debug.py:67-71),
状态行双编码(色 + PAUSED/RUNNING/ENDED 文本)。

## 2. 进程模型与模块树

```
apps/debugger/
├── app.py          # DebuggerApp(根 CompoundWidget):state/feed_key/render_into;
│                   #   SSE 线程 → queue → tick drain(§4)
├── model.py        # DebugSource protocol + Online/Offline/Demo 三实现(§3)
├── commands.py     # 命令方言:解析(纯函数)+ 执行路由(§5);help 文本从命令表生成
├── trace_rows.py   # 信号 → 轨迹行(对译 web trace.js 行语言;pausedSignalIndex/
│                   #   bpTargetForRow 的 Python 对译,web debug-view.js:505-532)
└── widgets.py      # StackWidget(bt 行)/BpWidget(info b 表)/TraceWidget(gutter 行)
                    #   /CmdWidget(命令窗:滚动输出 + (adb) 输入行 + 历史)
```

入口:`agent-os-tui debug [--session SID | --skill S --input JSON |
--replay RUN_ID [--until N]] [--base-url …] [--offline --run-dir …] [--demo]`
——复用 `host/tui/__main__.py` 的 TerminalGuard/raw 模式/主循环
(__main__.py:128-202);`debug` 子命令选择 debugger 的 `build_app`
(与 doc_editor 同签名四元组:`(tree, app, engine, motion)`)。

## 3. 数据源:DebugSource 分层(照 DocSource 先例)

Protocol(doc_editor/model.py:280-307 同体裁):

```python
class DebugSource(Protocol):
    writable: bool                     # 控制面开关(offline=False;demo=True 但不出海)
    # 会话
    open_live(skill, input, breakpoints) -> {session_id, run_id}
    open_replay(run_id, until_step, breakpoints) -> {session_id, run_id, mode}
    snapshot(sid) -> SessionDoc        # state/pause_point/breakpoints/frame_stack/rerunnable
    signals(run_id) -> list[dict]      # GET /api/runs/{rid}/signals(app.py:649)
    frame(sid, fid) -> FrameDoc        # GET …/frames/{fid}(live 内存态,app.py:1641)
    detach(sid); rerun(sid) -> {session_id, run_id}
    # 控制(仅 writable;只读源 raise PermissionError → 命令窗人话)
    add_breakpoint(sid, kind, match); remove_breakpoint(sid, bp_id)
    command(sid, cmd)                  # continue/step_into/step_over/step_out/stop/pause
    modify(sid, patch); inject(sid, text)
    # 辅助
    list_skills() -> list[str]         # run 表单用(GET /api/skills)
```

- **OnlineDebugSource**(唯一可写):base `http://127.0.0.1:8391`(**无前缀**——
  既有 `AgentOsClient` 默认打 `/platform`,kernel/client.py:22,调试器需第二个
  base 或 client 加 base 参数);统一信封 `{ok,status,json|error}` 纪律不变;
  错误语义:404 找不到 / 400 请求非法 / 409 状态冲突 / run 校验失败
  200+`{"status":"failed"}`——全部翻成命令窗人话(§6),不弹窗。
- **OfflineDebugSource**:`--offline --run-dir <artifacts>/runs/<id>` 直读
  trace.jsonl + checkpoint.json + meta.json;轨迹/bt/翻帧可用;控制命令回
  `The run is not being debugged (offline replay).`——学 GDB 对未运行程序回
  `The program is not being run.` 的语式。
- **DemoDebugSource**:内置 demo.fib n=3 信号序列(与 web 测试同一事实锚点,
  tests/web/test_debug_api.py:21-24),脚本化假会话:暂停/单步/断点命中按
  内核语义模拟,`set args` 可复现 `{"seq":[0,1,41]}` 的修改效果——无服务可
  体验全交互,也是无头冒烟的数据基座。

## 4. live 刷新:SSE 线程 → queue → 主循环 drain(TUI 首个先例)

TUI 主循环至今是纯键驱动 + 定时 tick(__main__.py:187-197),没有任何后台
注入先例;调试器是第一个真实需求。纪律:

```
SSE 线程                          主循环(唯一 state 写者)
─────────────                     ─────────────────────────
SseClient.run_forever()           tick(0.06s 动效 / 0.5s idle)
  event_received(evt, data) ──▶ queue.Queue ──▶ drain:
    (只入队,绝不碰 state)            state 快照 → 全量换 doc
                                     bp_hit → 计数原地更新
                                     paused → 拉快照+signals → 停止行(§6)
                                     resumed → state=running
                                     run_end → 终态打印 + 停线程
                                  app.mutate_state(...)
```

- **线程不碰 state**——同 RunManager `call_soon_threadsafe` 纪律
  (run_manager.py:834-858 的 TUI 对偶);queue 是唯一通道。
- **SseClient 一处泛化**(D2 已落地):`start_stream(base, path=…)`
  (kernel/sse.py:34),`stream_url = base + path`;调试流是
  `/api/debug/sessions/{sid}/stream`(app.py:1658),event:/data:
  帧解析(sse.py:88-113)原样复用,坏帧丢弃纪律不变。
- paused 事件只带 pause_point → **拉全量快照 + signals**(web debug-view.js
  :202-212 同语义)后再打印停止行与定位 ▶;**先打印后动效**(先管道后盖章)。
- 断线:`connection_changed(False)` → 回落 2s 轮询快照 + signals(web 既有
  语义)+ 连接态点变灰 + 命令窗一句人话;SSE 恢复自动切回。
- run_end → 打印 `Run finished: <done|failed|aborted>`(GDB `[Inferior 1
  exited …]` 的对应物)+ 状态行终态横幅;**`q` 退 TUI 不 detach**(语义同
  web 关标签页:会话留给下次连);detach 是显式命令。

## 5. 命令方言(本文核心章节)

解析器是纯函数(`commands.py`):`parse(line, context) -> Command | ParseError`;
执行路由经 DebugSource 出海。容错学 GDB:**唯一前缀缩写可省**(`fin` =
finish,`i b` = info b);歧义报错列出候选;`help <cmd>` 文本从命令表生成,
不硬编码。

### 5.1 命令全集

| 命令 | 语义(→ 后端) | 说明 |
|---|---|---|
| `run <skill> [--input <json>] [-b <spec>]…` | POST /api/debug/sessions(live) | GDB `run` 起进程;`-b` 带启动前断点(启动即断的确定性语义,run_manager.py:691-714) |
| `run --replay <run_id> [--until N]` | POST …/sessions(replay) | P5 时间旅行;skill/input 从产物 meta 读 |
| `b <spec>` / `break <spec>` | POST …/breakpoints | spec 语法学 GDB 位置规范:`b fs_*`(缺省 tool_call)/ `b skill:fib` / `b step` / `b error` / `b *N`(until 步数断点,仅 step,kernel/debug.py:299-306);**编号从 1 起**,界面只露 Num |
| `info b` / `info breakpoints` | 快照 breakpoints | GDB 表格:`Num Kind Match Enb Hits` |
| `delete <N>` | DELETE …/breakpoints/{bp_id} | Num → bp_id 内部映射 |
| `enable/disable <N>` | —(后端缺口) | 现状无端点:诚实回 `Not supported: delete and re-add.`,不造假(§10) |
| `c` / `continue` | command continue | 仅 paused;否则 §6 人话 |
| `s` / `step` | command step_into | |
| `n` / `next` | command step_over | |
| `finish` / `fin` | command step_out | |
| `until <N>` | until 步数断点 + continue | GDB until 直达语义 |
| **C-c**(running 中) | command pause | SIGINT 习惯;落在下一个可仲裁信号(精度边界 12-debugger.md §6.4,帮助中明示) |
| `bt` / `backtrace` | 快照 frame_stack → 命令窗 | GDB 行格式:`#0 fib (f-abc) at pre:step step 2` |
| `frame <N>` / `up` / `down` | 选帧(本地)+ GET …/frames/{fid} | 选帧即改检视上下文(GDB 习惯) |
| `p` / `print` | 暂停点 payload → 命令窗 | `p`(全量)/ `p args`(子集,客户端取值,零裁决) |
| `info frame` / `info args` | 帧摘要 / 调用 args | GDB info 族 |
| `x messages` / `x working` | 帧 messages / working 全文 | GDB examine 的对应物 |
| `set args <json>` | modify(patch) | GDB `set args` 改本次调用参数;**仅停在 pre:tool.call**;提交即放行(命令窗先打一行明示) |
| `inject <text>` | inject(暂停帧) | agent OS 特有,无 GDB 对应;帮助注明"注入一条 user 消息,注入即放行" |
| `kill` | command stop | **两段确认,逐字学 GDB**:`Kill the run being debugged? (y or n)` |
| `detach` | DELETE 会话 | GDB detach 逐字对应:放行,run 继续跑完 |
| `rerun` | POST …/rerun | GDB 再敲一次 `run` 的习惯;`rerunnable=false` 回人话 |
| `session` / `info sessions` | 已知会话列表 | client 模式无服务端列表端点:本地记录(同 web localStorage 先例,语义写明) |
| `q` / `quit` | 退 TUI(会话保留) | 明示"不 detach";退前有 paused 会话给一行提示 |
| `help` / `h [<cmd>]` / `apropos <词>` | 帮助 | 从命令表生成 |

### 5.2 裸 Enter 重复(GDB 最高频肌肉记忆)

空行 = 重复上一命令,**白名单**:`c/s/n/finish/until/up/down`(GDB 同款:
step/next 系可重复,危险命令不重复)。`kill/run/detach/set args/inject`
永不因空行重复。

### 5.3 快捷键层(keymap 契约扩展)

命令窗 INSERT 时一切可打印键自插入(doc_editor InputWidget/模式感知先例,
TUI-DOC §T2 实现注);只读窗焦点时才给裸键:

- 新 context:`"dbg-cmd"`(命令窗 INSERT)+ `"dbg-trace"` / `"dbg-stack"`
  (只读窗);扩 `CONTEXTS`(tui/keymap.py:28)与 `REQUIRED_INTENTS`(:40);
- 新 intent:`focus_next`(C-x o,GDB TUI 焦点切换习惯)/ `scroll_up/down`
  (↑↓/PgUp/PgDn,焦点窗内)/ `repeat_last`(命令窗裸 Enter)/ `interrupt`
  (C-c = pause)/ `bp_toggle`(焦点在轨迹行时 `b` 直打/消该行断点——
  web gutter 的键盘等价物,bpTargetForRow 对译决定该行可否打)/ `redraw`
  (C-l,GDB TUI 习惯);
- vim/emacs 双包各配最小绑定(契约测试 test_contracts.py 会强制);命令窗
  行编辑两包都收 readline 基本键(C-a/C-e/C-u/C-w/↑↓历史)。

## 6. 停止展示与命令窗输出规范(GDB 习惯的细节面)

- **停止行**(每次暂停自动打印,学 `Breakpoint 1, main () at main.c:4`):
  `Breakpoint 1, pre:tool.call system.python.exec, frame f-abc (fib), step 2`
  `Run paused by user (pause), frame f-abc, step 3`
  `Step finished, pre:step, frame f-def (fib), step 1`
  → 同时轨迹窗 ▶ 定位暂停行(pausedSignalIndex 对译,web debug-view.js:519-532);
- **命令回声**:每个出海命令成功后打一行等价动作(教学 + 可审计):
  `Breakpoint 2 set: tool_call fs_*` / `resumed (step_into)`;
- **错误人话**(学 GDB 语式,不弹窗、不打原始 HTTP):
  非 paused 发 c → `The run is not paused.`
  无会话发控制命令 → `The run is not being debugged.`
  set args 停在别处 → `Cannot set args: not paused at pre:tool.call.`
  409/404 → 同款语式;校验失败(200+failed)→ 原样转述 error 字段;
- **干预留痕**:modify/inject 成功打 `args patched: {...}` /
  `injected -> frame f-abc (fib)`——"干预不伪造历史"的后端纪律(kernel
  /debug.py §4.4)在前端的回声;
- **终态**:`Run finished: done`(failed/aborted 同款换词)+ 产物路径一行。

## 7. 布局(GDB TUI 式)

```
┌ #s-01 · fib · [PAUSED] ──────────────────────────────────────────────┐
│ 调用栈 (bt)          │ 执行轨迹(源码窗等价物)                       │
│ ▶#0 fib    f-abc     │      0001 pre:step          fib              │
│  #1 fib    f-def     │  ●   0002 pre:skill.invoke  fib              │
│                      │  ▶   0003 pre:tool.call     system.python.exec│
│ 断点 (info b)        │      0004 post:tool.call    ok               │
│ Num Kind     Match   │                                               │
│ 1   tool_call fs_* y ×2│                                             │
├──────────────────────┴───────────────────────────────────────────────┤
│ Breakpoint 1, pre:tool.call system.python.exec, frame f-abc, step 2  │
│ (adb) s█                                                             │
├ PAUSED · foc=trace (C-x o 切换) · conn●SSE · help 帮助 ──────────────┤
```

- 左栏上下分:**调用栈窗**(#N 编号 + ▶ 当前帧 + 选中帧反色,depth 树线
  `└─ ├─`,DEBUG-UI-THEMES §3.3 词汇)+ **断点窗**(info b 表格常驻,
  bp_hit 事件原地刷新 ×hits);
- 右大窗:**信号轨迹**(行号 + gutter `●` + ▶ 暂停行反色;行语言对译 web
  trace.js,保证与 web 调试台/Workbench 轨迹同构);
- 底部**命令窗**:多行滚动输出(停止行/回声/info 输出全进此窗)+ `(adb)`
  输入行 + ↑↓ 历史;
- 状态行:会话状态(双编码)+ 焦点窗 + 连接态点(●SSE/○轮询/─已结束);
- **检视器默认不进版面**:`p`/`x`/`info` 把 payload/messages 打进命令窗
  (GDB 习惯——输出即历史,可回滚对照);独立检视窗作为版面变体留实现注。

## 8. 主题与 copy

- **色 token 零新增**:基底/状态(ok|warn|danger|aborted|live)/信号
  `sig-*`/focus-ring 族在 tui/theme.py:84 REQUIRED_COLORS 已齐
  (DEBUG-UI-THEMES token 逐字对齐过的);断点行、暂停行、状态徽标全部消费
  现有 token;
- **copy 键新增 `dbg.*` 族**(进 REQUIRED_COPY,theme.py:105):停止行模板、
  错误人话(§6)、空态、终态行、帮助行——**停止行/错误语式是文案契约**,
  主题可译但 `{signal}`/`{frame}` 等占位符不可吞(DEBUG-UI-THEMES §4.4
  纪律);技术原文(信号名、工具名、JSON)豁免翻译,永远直读;
- **双编码钉死**:断点 `●`+色、暂停行 `▶`+反色、当前帧 `▶`、连接态 `●/○/─`;
- **动效名**:`bp-hit`(断点行短闪,SUBTLE 一亮一灭 / FULL 两回合)/
  `paused`(暂停行脉冲,focus-pulse 同档)/ `run-done`(状态行定格章,
  stamp-press 同档,章文走 `dbg.end.run` copy 键);档位归主题(classic
  paused/run-done 给 FULL,terminal 全 SUBTLE),reduced-motion 全局强制
  instant(先管道后动效:命令路径 `_motion_for_transition` 与 SSE/轮询
  路径同过 `_pp_reported`/`_ended_reported` 去重,同一停点只播一次)。

## 9. 测试策略

- **命令解析器纯函数单测**:spec 语法(缺省 kind/skill: 前缀/*N)、唯一前缀
  缩写、歧义候选、裸 Enter 重复白名单、危险命令不重复;
- **cell buffer 快照**(plain_text 纯字符串比对):三窗布局、bt 行格式、
  info b 表格、停止行、命令窗滚动、gutter `●`、▶ 定位;
- **契约测试扩展**(tests/tui/test_contracts.py):新 context/intent/copy 键
  的拒注册矩阵;
- **DemoDebugSource 全流程无头冒烟**(feed_key 驱动,conftest demo_app
  先例):`run` → 停止行 → `b fs_*` → `c` → `s` + 裸 Enter×3 → `bt` /
  `frame 1` → `set args {"code":"result = 41"}` → 结果复现
  `{"seq":[0,1,41]}` → `kill`(两段确认)→ `q`;
- **SSE 注入单测**:queue drain 语义、线程不碰 state 纪律、断线回落轮询、
  坏帧丢弃;
- **pty 实机冒烟**:备用屏幕恢复、CJK 双宽、命令窗宽行折行(T1.1 CRLF
  教训:行间显式 \r\n,回归测试钉死"输出流无裸 LF")。

## 10. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **D1 命令窗 + 只读骨架** | commands.py 解析器 + 三窗渲染 + Demo/Offline 源(run/b/info b/bt/frame/p/x 全可用,无 live) | demo 下全命令语言可浏览;cell 快照全绿 |
| **D2 live 控制** ✅(§14) | Online 源 + SSE queue 注入 + c/s/n/finish/until/C-c/b/delete + 断线回落 | 打真服务全流程;停止行正确;断线回落轮询 |
| **D3 检视与干预** ✅(§15) | frame/up/down 检视跟随 + p/x/info frame + set args/inject + kill 两段确认 | demo.fib modify 改结果复现(web 测试锚点) |
| **D4 会话管理** ✅(§15) | run(live/replay/until/启动断点)+ rerun + detach + info sessions | 与 web 调试台同能力面 |
| **D5 主题打磨** ✅(§16) | 具名动效 bp-hit/paused/run-done + copy 表完善 + 帮助从命令表生成 | 换肤零组件分支;契约测试全绿 |

## 11. 不做(v1)

- **内嵌模式**(无服务进程内调试——CLI REPL 已是该形态,不造第二形态);
- GDB 的汇编窗/寄存器窗(无对应物)、watchpoint/条件断点(内核不支持,
  12-debugger.md §6.1)、`enable/disable <N>` 真端点(后端缺口,命令照收
  诚实拒绝,不造假);
- 独立检视器常驻窗(p/x 输出进命令窗已覆盖;留实现注);
- 多会话并行窗、鼠标交互;
- agent 面 act 收口(同 doc_editor v1 纪律;agent 调试 agent 走 CLI/API)。

## 12. 引用

- 调试器语义:`docs/DEBUGGER.md`、`docs/whitepaper/zh/12-debugger.md`、
  `agent_os/src/agent_os/kernel/debug.py`(DebugController/DebugSession)
- Web 后端:`agent_os/src/agent_os/host/web/app.py:1489-1652`(/api/debug/*)、
  :522-537(会话快照)、:649(signals)、:97(_DEBUG_SSE_POLL)
- TUI 宿主:`docs/TUI-DOC.md`、`agent_os/src/agent_os/host/tui/`(kernel/tui/
  apps/doc_editor)、`kernel/sse.py:34`(start_stream path 泛化点)、
  `tui/keymap.py:28,40`(CONTEXTS/REQUIRED_INTENTS)、
  `tui/theme.py:84,105`(REQUIRED_COLORS/REQUIRED_COPY)
- Web 调试台(行语言与纯函数对译源):
  `host/web/static/js/components/debug-view.js`(pausedSignalIndex :519 /
  bpTargetForRow :505)、`trace.js`
- CLI REPL(命令词对齐与事实锚点):`host/cli/debug.py`、
  `agent_os/tests/web/test_debug_api.py:21-24`(demo.fib n=3 信号序列)
- 主题契约:`docs/DEBUG-UI-THEMES.md`(§3.3 终端词汇、§4.4 copy 纪律)

## 13. 实现注(D1,2026-09-27)

D1 已落地(`agent_os/src/agent_os/host/tui/apps/debugger/`,零内核改动、
零新 pip 依赖):命令方言解析/执行(commands.py)、三窗 + 命令窗
(widgets.py + app.py)、trace.js 行语言对译(trace_rows.py)、
Demo/Offline/Online(stub)三源(model.py)、`python -m agent_os.host.tui
debug --demo|--offline --run-dir …` 入口(__main__.py)。测试:
tests/tui 164 例全绿(调试器新增 32 例:commands 17 + render 6 + smoke 5
+ contracts 4);全量回归 1371 passed / 10 skipped / 39 xfailed;
ruff check 干净。

**有意偏离 / 裁决记录**:

1. **§9 冒烟序列的 `b fs_*` 与 demo 锚点不符**:tests/web/test_debug_api.py
   :21-24 的 demo.fib n=3 唯一工具调用是 `system.python.exec`,fnmatch 下
   `fs_*` 永不命中,`c` 会直达 run 终态(后续 `s` 失去意义)。冒烟改用
   `b system.*`;`b fs_*` 另设一例(test_debugger_render.py)钉死"加了
   但不命中"的诚实行为(回声正常、gutter 不亮、`c` 直达 `Run finished:
   done`)。§9 的 `set args … → 结果复现` 属 D3(modify 未接线,本期回
   「属后续里程碑(D3)」人话)。
2. **入口断点学 CLI pdb 语义**(cli/debug.py:348 先例):demo 会话开出即加
   一次性 step 断点,`run` 停在第一条 `pre:step`,首停即删;停止行断点号
   经 pause_point.breakpoint_nums 记账(删后仍可报 `Breakpoint 1, …`)。
3. **控制面提前到 D1 可体验**:§10 里程碑表把 c/s/n/finish/until/kill 两
   段确认列在 D2/D3;D1 的 Demo 源按 kernel/debug.py 语义同步模拟(断点
   命中/until 语义/into·over·out 步进/Stop verdict 落地可仲裁信号/run
   终态自动 detach),故这些命令在 demo 下全可用;live 接线(Online 源 +
   SSE)仍属 D2,set args/inject 仍属 D3,detach/rerun/session 仍属 D4——
   均回里程碑人话,不做假功能。
4. **gutter ● 精确匹配语义**(对译 debug-view.js:544):只有 bp 的
   kind+match 与行目标原名逐字相等才亮;glob 断点(`b system.*`)能命中
   执行但不点亮 gutter——与 web 一致,不装熟。键盘等价物 = trace 窗 `b`
   (bp_toggle,目标行经 bpTargetForRow 对译判定)。
5. **trace.js 未译折叠/窗口化**:行语言(pre/post 配对、call/ret 头、
   exec 折进 tool、pre:step 不占行、veto 推断)全译;长轨迹折叠与虚拟
   窗口留后续(命令窗滚动 + trace 窗滚动已覆盖 D1 体量)。
6. **键纪律的数据化解法**:C-u/C-w/↑↓ 历史以新 intent(edit_kill_line/
   edit_kill_word/history_prev/history_next)入 keymap 契约清单;dbg-cmd
   把与 global 冲突的可打印键(q/:/\?)显式绑回 `self_insert`,app 对该
   intent 做 insert_char——`q` 在命令窗是命令文本而非退出键,引擎未改。
7. **暂停行定位对译 pausedSignalIndex/rowForSignal**:不占行的暂停信号
   (pre:step)的 ▶ 落在最近可见前行,与 web 同。

## 14. 实现注(D2,2026-09-27)

D2 已落地,调试器打真服务全流程可跑:OnlineDebugSource 实实现
(`apps/debugger/model.py:595`,REST 全走 AgentOsClient 统一信封、裸
base 无 /platform 前缀;client.py:192 起 debug 方法族)、SSE 线程 →
queue → 主循环 drain(§4 纪律:线程只 `queue.put` 不碰 state;
`kernel/sse.py:34` start_stream 泛化 path 参数)、断线回落 2s 轮询
(app.py:107 POLL_INTERVAL,对译 web debug-view.js POLL_MS)、入口旗标
全接 live(`__main__.py:283-322`:--skill/--input/-b/--session/
--replay --until/--base-url,fail-fast 探针 ping → stderr + exit 2)。
测试:tests/tui 184 例全绿(D2 新增 20 例:online 17 + live 2 +
sse path 1);全量回归 1402 passed / 10 skipped / 39 xfailed;
ruff check 干净。

**裁决记录**:

1. **tick 先轮询后 drain**(app.py:396):同一 tick 内若先 drain SSE
   paused 事件(原地更新)再轮询全量快照,bp_hit 的计数会被旧快照覆盖
   ——顺序必须是快照在前、事件在后(事件永远比快照新)。实测抓获。
2. **断线判定 = SSE 线程上报**(connection_changed(False) 或线程死亡);
   `_conn_was_ok`(app.py:448)区分"首次 False = 尚未连上(start_stream
   同步上报,静默等连)"与"真连上过再断"(打 `dbg.conn.down_msg` 人话,
   切 ○ poll)。恢复静默切回 ● SSE,不打第二行人话。
3. **C-c 是键事件不是信号**:keys.py `tty.setraw` 关 ISIG,C-c 进
   KeyEvent("c", ctrl=True),dbg 三 context 已绑 `interrupt` → pause;
   __main__ 的 SIGINT handler 只兜外部 `kill -INT`,不重复处理。
4. **渲染路径快照缓存** `_snap`(app.py:176):header/状态行/徽标每帧
   渲染只读本缓存,不每帧 GET;缓存只由 refresh/_apply_snapshot/
   _report_run_end 写。
5. **断点界面编号 num 是客户端显示号**:REST 只有 bp_id;snapshot() 时
   reconcile id→num(model.py:752 `_num_for`,映射稳定,GDB 习惯);
   停止行断点号经 snap.breakpoints 反查(真内核 pause_point 无
   breakpoint_nums,那是 demo 源的记账字段)。
6. **live 下 until 诚实拒绝**:REST 断点端点(DebugBreakpointBody)只有
   kind/match 无 until 字段;`--replay --until N`(服务端注册一次性步数
   断点)与 demo 可用,live 回人话不造假。`run -b *N` 同理回 usage。
7. **服务端 SSE paused 改按暂停点身份补发**(host/web/app.py:1658-1679):
   纯状态沿检测(prev_state)在 poll 间隙内的 paused→running→paused 快
   循环(mock brain 毫秒级 vs _DEBUG_SSE_POLL=0.1s)观测不到 running
   过渡,第二停永远不上流——live 冒烟实测抓获。修复:`_pause` 每次换
   新 dict,以 `last_pause` 身份比较补发;web 回归
   tests/web/test_debug_api.py::test_debug_sse_rapid_repause_emits_paused
   钉死(monkeypatch 拉大 poll 周期制造确定性竞态)。
8. **live 冒烟 = 真 uvicorn**(tests/tui/test_debugger_live.py):
   ephemeral port 线程内起真服务 + 真 OnlineDebugSource(httpx REST +
   真 SSE 线程),`app.tick()` 由测试驱动(主循环等价物)。两处实测
   差异钉进注释:uvicorn 优雅关停默认等长连接,须
   `timeout_graceful_shutdown=0` SSE 才真断;真内核 pre:tool.call 负载
   无 step 顶层字段(runner.py:715),停止行不带 ", step N" 后缀
   (demo 脚本才有)。

**入口断点链路**(live):open_live/open_replay 无 until 时前置
("step","*") 断点(pdb 语义,D1 注 2 的 live 对偶);`_on_opened` 记
注册顺序第一个 bp id;首停停止行打印后 app 调
`ack_entry_breakpoint(pp)`(breakpoint_ids 含它才删)经 REST DELETE,
删了则重拉快照刷断点表。

## 15. 实现注(D3+D4,2026-09-28)

D3(检视与干预)与 D4(会话管理)已落地:命令表 §5.1 全命令可用,
「属后续里程碑」人话随 `_later`/`dbg.later` 一并拆除。测试:tests/tui
206 例全绿(D3 新增 12:intervene 6 + online 5 + live 1;D4 新增 10:
session 3 + online 5 + store 落盘 1 + live 1);ruff check 干净。

**D3 裁决**:

1. **demo 的 modify 是"诚实窄模拟"**(model.py:418 `_DemoSession.modify`):
   patch 合入 args 后就地重算派生行——python.exec 语义用
   `_simulate_py_exec`(model.py:207;命名空间 exec + stdout 末行 JSON,
   与 tools/builtins.py:565 `_parse_result` 同契约),终答按 fib_brain
   公式 `seq + [total]` 改写 post:tool.call / F1 pre:frame.pop /
   run.finished 三处;**已发出的 pre:tool.call 行不改**(干预不伪造
   历史)。exec 失败 → post:tool.call ok=False,终答不动(不模拟 brain
   对工具报错的反应,docstring 注明)。锚点复现:`{"code":"result =
   41\nprint(result)"}` → `{"seq": [0, 1, 41]}`(与
   tests/web/test_debug_api.py:134-142 同 patch 同结果)。
2. **JSON/停点预检全在命令层本地**(commands.py:517 `_cmd_set_args`):
   JSON 从 `cmd.raw` 取(值可含空格,parse 的 word 切分吃不了);非法
   JSON/非对象/停错点一律本地拦,不打后端——§6 语式
   `Cannot set args: not paused at pre:tool.call.` 两源统一;Online 的
   409 竞态(本地快照旧)经 `_human` 原样转述兜底。
3. **干预回声即放行明示**(§6):`args patched: {…}(提交即放行)` /
   `injected -> frame f-xxx (skill)(注入即放行)`;同步源(demo)随后
   由 `_advance_lines` 端出推进结果,异步源(live)等 SSE/轮询回流。
4. **inject 的 demo 留痕**:帧消息每会话自持一份(不再共享模块级模板),
   `{"role":"user","source":"injected"}` 落暂停帧上下文,`x messages`
   可见——与 web inject 锚点(test_debug_api.py:200-205)同语义。
5. **p/x/info frame 的 live 面 D2 已齐**(OnlineDebugSource.frame 经
   GET …/frames/{fid} 取内存态,选帧跟随是本地 selected_frame),D3 只补
   了干预路径,无重复建设。

**D4 裁决**:

6. **rerun 换绑纪律**(model.py OnlineDebugSource.rerun:998):POST 成功
   后 `_on_opened` 停旧 SSE 流 + 清事件队列 + 复位 ended/sse_down/
   bp 编号;`drain_events` 加 sid 过滤,旧会话尾巴(run_end s-1)永不
   罩新会话。app 侧 `ExecResult.restarted` → `_on_session_restarted`
   (app.py:450):终态/停止行去重/连接簿记清零,conn 回 poll 等首连。
   入口断点随服务端 origin(含客户端 prepend 的 step 断点)重生,编号
   重新从 1 起(GDB 新 run 习惯)。无 origin(replay/CLI 会话)→ 服务端
   400 原样转述,不本地预检(demo 源 rerunnable=False 但可模拟重开,
   预检会误伤)。
7. **detach 的终态面**:DELETE 摘注册表后快照端点 404 是**真态**;
   SSE 模式 run_end 照常经流送达(生成器持会话引用)。断线回落(poll)
   模式下 snapshot() 对 "404 + 显式 detach" 给合成 detached 快照——
   **仅当 run detail 已终态**;未终态则抛错让轮询静默下周期再来,不
   提前造假 `Run finished`(model.py snapshot 的 `_detached` 分支)。
8. **session/info sessions = 本机账本**(model.py:54 SessionStore):
   client 模式无服务端列表端点,照 web localStorage 先例落
   `~/.config/agent-os/debugger-sessions.json`(JSON 数组,sid 去重,
   cap 50 FIFO;坏文件/写失败静默——账本不是真相)。`OnlineDebugSource`
   缺省不记账(store=None,无头/测试零磁盘副作用),真入口由
   `__main__.py build_debug_source` 注入;`session <SID>` = attach
   切换(消逝会话吃诚实 404)。
9. **demo 源 replay 仍拒**:`_DEMO_NO_REPLAY` 人话指向真服务
   --replay / --offline --run-dir(demo 无产物可回放,不造假)。

live 冒烟(真 uvicorn)新增两条:`test_live_set_args_reproduces_41`
(set args 出海,run detail result == {"seq":[0,1,41]})、
`test_live_rerun_and_detach`(rerun 新会话首停 → detach → run_end)。

## 16. 实现注(D5,2026-09-28)

D5(主题打磨)已落地,§10 五个里程碑全收。测试:tests/tui 217 例全绿
(D5 新增 11:动效 7 + help 自动跟随 1 + 契约 3);全量 1435 passed /
10 skipped / 39 xfailed;ruff check 干净。

**D5 裁决**:

1. **三具名进契约清单**(theme.py MOTION_NAMES):`bp-hit` / `paused` /
   `run-done`——两内置包的 motion 表按 MOTION_NAMES 全量给档,加名即
   两包自动齐;档位归主题:classic 的 paused/run-done 给 FULL(严肃工程
   的确认感),terminal 全 SUBTLE(克制主题),bp-hit 两包 SUBTLE(常驻
   事件,克制短闪)。MotionPlayer.play 真实现(motion.py):paused =
   _Pulse(focus-pulse 同节奏,FULL 3 回合 / SUBTLE 2 回合),run-done =
   _Stamp(stamp-press 同节奏,章定格 ~300ms),bp-hit = 短脉冲(FULL 2
   回合 / SUBTLE 1 回合);旧名 focus-pulse/stamp-press 保留(doc_editor
   在用)。reduced-motion 全局强制 instant,三具名一律不记帧(管道照走)。
2. **命令路径动效钩 `_motion_for_transition`**(app.py):探查发现同步源
   (demo/offline)的停点/终态在命令返回内落定,不经 `_report_pause`/
   `_report_run_end`(那是 SSE/轮询回流路径)——D1-D4 的 play 调用只在
   回流路径,命令路径实际无动效。现钩在 `_echo_and_execute`/`_kill_answer`/
   `_interrupt` 的 refresh 之后,按状态迁移播(先管道后动效);异步源
   (live,`sync_control=False`)不在此播,等 SSE 事件。两边同过
   `_pp_reported`/`_ended_reported` 去重,同一停点/同一终态只播一次。
3. **bp-hit 的行级短闪**:BpWidget 记 `hit_num`(bp_hit 事件 num 解析)
   + 渲染期记该行 Region(`pulse_region()` 约定,跟布局走);app 在原地
   更新计数之后播(先 mutate 后 play)。
4. **copy 收编**(theme.py `_DBG_COPY` + REQUIRED_COPY):新键
   `dbg.bps.header`(窗格表头)/ `dbg.info_b.header`(命令输出表头,列宽
   与窗格不同,各自一键)/ `dbg.sessions.header` / `dbg.bt.row`
   (`#{n}  {skill} ({fid}){at_suffix}`)/ `dbg.frame.row`;窗格表头、
   栈帧行、会话表头不再硬编码。model.py 源层 RuntimeError 人话字面量
   有意保留——那是数据源协议消息(命令层 `str(e)` 原样转述),测试逐字
   钉死,不算界面 copy。
5. **换肤可观测面**:`_DBG_COPY_TERMINAL` 差异表(窗格标题/表头大写
   铭文:STACK/TRACE/BREAKS/CMD)叠在共享 `_DBG_COPY` 之后——停止行/
   错误语式是 GDB 逐字契约两包同值,窗格标题属可译文案,换肤要有
   可观测面(契约测试 `test_theme_switch_dbg_copy_observable` 钉死
   "7 键不同 + 5 键相同")。
6. **help/h/apropos 自动跟随**钉死:`test_help_apropos_follow_command_table`
   往 COMMAND_SPECS  monkeypatch 假命令,help 列表与 apropos 输出自动
   出现;`?` 帮助面板不单独造——dbg-cmd 是常驻 INSERT context(`?` 绑回
   self_insert,是命令文本),help intent(只读窗 `?` / global 绑定)落到
   `run_command("help")`,与命令表同源即满足 §5。
7. **换肤零组件分支**:debugger 组件源码(app/commands/model/widgets/
   trace_rows)grep 无 `classic`/`terminal` 字样,契约测试
   `test_debugger_zero_theme_branch` 扫源钉死。

§11 不做清单逐项确认:内嵌模式未造(CLI REPL 已是该形态);汇编/寄存器窗、
watchpoint/条件断点、`enable/disable` 真端点未做(命令照收诚实拒绝);
独立检视器常驻窗未做(p/x 进命令窗);多会话并行窗、鼠标交互未做;
agent 面 act 收口未做(走 CLI/API)。

## 17. 实现注(Web 控制台,2026-09-28)

GDB 风格调试控制台(`#/debug/<sid>/console`)已落地:**后端零改动,纯前端**;
命令方言与 TUI 同表逐语义移植(§5),停止行/错误语式逐字对齐 theme.py
dbg.* copy(§6),布局照 §7 四窗(顶条 + 左栏栈/断点 + 右大窗轨迹 + 底部命令窗)。

**文件清单**:

- `host/web/static/js/components/debug-commands.js`(282 行,纯函数,node 直测):
  命令表 COMMAND_SPECS(help/apropos 从表生成)、`parse`/`parseBpSpec`
  (spec 四形态 `b fs_*`/`b skill:fib`/`b step`/`b error`/`b *N`)、唯一前缀缩写 +
  歧义候选、REPEATABLE 裸 Enter 白名单、`stopLine`/`infoBLines`/`btLines`/
  `frameRowLine` 与 MSG 文案(§6 逐字);
- `host/web/static/js/components/debug-console.js`(1091 行,页面模块,照
  debug-view.js 结构):openDebugConsole/closeDebugConsole 幂等 + consoleClick
  委托;数据面零新通道(快照 + signals + frames + 调试 SSE,断线 toast +
  2s 轮询回退,ticker 常驻刷轨迹按信号数变化才重绘);kill 两段确认、
  ↑↓ 历史、裸 Enter 重复全在命令窗输入行(真实 `<input>`);
- `host/web/static/js/app.js`:路由 `debug-console`(seg[2]==="console")+
  开/关接线 + 点击委托两行;
- `host/web/static/css/app.css`:`.dbc-*` 四窗网格与命令窗样式(色只消费
  语义 token,组件零主题分支);
- 入口:debug-view.js 控制条加「控制台」链接、debug-home.js 会话行加
  「控制台」链接(既有行为不变);
- 测试:`static/tests/debug-console.test.mjs`(222 行,解析器单测)+
  `static/tests/smoke-debug-console.test.mjs`(572 行,dom-stub 冒烟九段)。

**裁决与留口**:

1. **断点界面编号 num 是客户端显示号**(REST 只有 bp_id;TUI D2 注 5 同款):
   页面层 `bpNums` 映射按首见顺序分配,删后不复用,GDB 习惯。
2. **技能名短形两步叠加**:`shortSkillName` 先剥命名空间/版本
   (`local:fib@1.0.0` → `fib`,web util.js 语义)再剥点号尾段
   (`demo.fib` → `fib`,trace_rows short_skill 语义)——两种命名习惯都得到
   §6 例句的 `(fib)`。
3. **诚实人话(不造假)**:run 收但指向 `#/debug` 首页表单(console 不做开
   会话表单);`enable/disable` 回 `Not supported: delete and re-add.`;
   `until`/`b *N` 回「REST 断点端点只有 kind/match」人话(TUI live 同款,
   D2 注 6);`session` 读 localStorage 账本(与 debug-home 同键),
   `session <SID>` = attach 切换(消逝会话吃诚实 404);`q` = 回
   `#/debug/<sid>` 三栏视图(会话保留,不 detach)。
4. **干预回声即放行明示**(§6):`args patched: {…}(提交即放行)` /
   `injected -> frame f-xxx (skill)(注入即放行)`;非法 JSON/停错点本地拦,
   不打后端(`Cannot set args: not paused at pre:tool.call.` 两源统一)。
5. **停止行/终态去重**:`ppReported` 指纹保证同一停点只打一次停止行
   (SSE 与轮询路径同过);run_end/轮询 detached 同过 `ended` 闸门,
   `Run finished: <status>` 只打一次 + 输入禁用 + 终态横幅。
6. **留口**:轨迹长轨迹折叠/虚拟窗口未进控制台(debug-view 既有行语言全量
   渲染,体量同调试台);独立检视器常驻窗不做(p/x/info 输出进命令窗,
   §11 同款);主题 copy 表未收编 dbg.* web 侧文案(v1 照 debug-view 先例
   直接写文案,六主题 copy 表留后续)。

**测试数字**:新增前端测试 2 个文件全绿(解析器 8 组断言 + 冒烟 9 段);
`static/tests` 全量回归 34 个 .test.mjs 零失败(含 themes-contract 焦点环
契约——`.dbc-input` 不抑制 outline);`agent_os` pytest 1502 passed /
10 skipped / 39 xfailed 保持绿。ruff 不涉及(零 Python 改动)。
