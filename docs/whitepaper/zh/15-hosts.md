# 宿主:CLI/Web 薄宿主与产物契约

> 章次:15 · 状态:已实现(RUNNERS.md R1–R4 全部落地,另有 D3/D4/D6、S2、P3/P5、L1–L5 增量;个别设计项未实现,§6 如实标注) · 依据:`docs/RUNNERS.md`;`agent_os/src/agent_os/host/{cli,shared,web}/`;`agent_os/tests/{cli,web}/`

## 1. 概述

宿主层是架构分层的最外层(执行摘要 §3.1 的"宿主层"):两个**薄宿主**——CLI(面向 coding agent)与 Web UI(面向人),以及它们共用的 `host/shared` 产物组织与读取层。内核是库而不是服务,宿主只做三件事:**组装**(配置 → `build_kernel`)、**调用**(`kernel.run`/`resume`)、**呈现**(读运行产物)(docs/RUNNERS.md §1)。两宿主之间不靠进程通信,而靠同一份**产物契约**互通:`.agent-os/runs/<run_id>/` 下的四件文件是 run 的唯一持久形态,CLI 产出的 run 可以直接在 Web UI 里打开做 RCA。

## 2. 动机与背景(原因)

**为什么宿主不进内核。** 微内核判据要求内核只保留流控制、权限控制与 IPC(执行摘要 §2.2)。宿主的三件事都不满足入场条件:组装不是 loop 推进的必要条件(嵌入方可以自己 new 内核),呈现不是仲裁点,CLI/Web 也不是子系统间的唯一公共通道——信号总线才是。RUNNERS.md §1 把这条写成硬原则:"凡是要改内核才能做的功能,先回内核立项,不在宿主里打补丁。"宿主层因此成为微内核边界是否被严格执行的试金石:它消费的所有数据(`kernel.run` 返回值、总线信号、trace WAL、checkpoint)都必须已是内核契约的一部分。

**为什么是两个宿主而不是一个。** 消费者不同,输出契约相反:coding agent 要的是机器可断言的 JSON、语义化退出码、文件路径,多一行人读输出都是噪声;开发者(人)要的是实时推送、帧树/时间线可视化和一键 RCA,JSON 流不可读。强行合一会把两边的契约都拉扯变形。共享的部分——产物布局、RunRecord schema、replay/diff——下沉到 `host/shared`,各自的表现层保持极简(RUNNERS.md §2.4)。

**为什么产物优先(artifacts first)。** 把 debug 数据的载体定为文件而不是 API 或数据库,带来三个连锁性质:两宿主天然互通(同一布局);进程重启后历史 run 可从产物目录重建(冷数据,§4.2);replay/diff/RCA 等下游工具全部离线可读,不需要一个活着的服务。这是"确定性是工程目标"(执行摘要 §2.2 公理 4)在宿主层的直接体现。

## 3. 问题陈述(解决的问题)

1. **脚本化运行与断言**:coding agent 改完 `skills.yaml` 后要跑一遍并断言结果。人读输出无法脚本化——需要 `agent-os run demo.fib --input '{"n":4}' --json` 的 stdout 恰为一个 JSON,退出码区分"输入错了"(2)与"run 败了"(3)(`host/cli/main.py:141`)。
2. **失败定位的数据分散**:一次失败后,"模型那一步看到了什么"在 checkpoint、信号顺序在 trace、最终状态在 result——三者若无统一布局与读取层,每次 RCA 都是手工考古(`host/shared/artifacts.py:192`)。
3. **非确定复现**:LLM 调用不可重放。线上失败的 run 拿回本地,需要不碰真实 API 的确定性重放,并能回答"我的修复改变了行为吗"(`agent-os replay` + `agent-os diff`,RUNNERS.md §3.4)。
4. **实时观察不能阻塞 run**:浏览器要逐帧看 run 推进,但内核在跑、信号在总线上流;扇出给 N 个 SSE 客户端时,任何一个慢客户端都不得拖垮 run(`host/web/run_manager.py:244`)。
5. **宿主进程易逝**:Web 进程重启后,历史 run 不能丢,在跑的 run 丢了也要能从产物认出"它死在进行中"(`host/web/app.py:317`)。
6. **错误归类**:run 未开始(技能不存在、输入不合 schema)与 run 开始后失败是两类事故,宿主必须把第一类上抛、第二类落 RunRecord,而不是一律 500 或一律退出码 1(`host/shared/artifacts.py:120`)。

## 4. 设计与机制(解决的方法)

### 4.1 产物契约:四件套 + RunRecord

每次 run 落一个目录,写入由 `execute_run`/`execute_resume` 收口(`host/shared/artifacts.py:50`):

```
.agent-os/runs/<run_id>/
├── meta.json         # {run_id, skill, input, host: "cli"|"web", started_at, ...}
├── trace.jsonl       # 信号 WAL(从 telemetry 目录按 run_id 归档;缺则空文件)
├── checkpoint.json   # 终态/中止时 kernel.checkpoint 快照(帧含完整上下文)
└── result.json       # {status, result, error, usage 汇总}(usage 取自 checkpoint)
```

四件套的分工即"调试数据分类法"(RUNNERS.md §2.3):result 回答成败,trace 回答顺序,checkpoint 回答"谁调用谁、模型看到了什么"。**checkpoint 是 RCA 与 resume 的共同数据源**——run 失败但 run 对象存在时也照常快照(`artifacts.py:63`),因为帧上下文恰是失败现场。

宿主对外的机器消费面是 **RunRecord**(`host/shared/runrecord.py:22`):`{"v": 1, run_id, status, result, error, usage, frames, artifacts{dir,trace,checkpoint}}`。CLI 全部子命令经同一 `dumps` 序列化,coding agent 只依赖这一个版本化 schema(RUNNERS.md §3.5)。

**错误归类的判定逻辑**是本章最精炼的一段代码(`artifacts.py:120-129`):`execute_run` 订阅 `run.started` 捕获 run_id;异常到来时,若 run_id 已产生(帧已开始推进),一律捕获进 RunRecord(`failed`/`aborted`);若 run 未开始(校验/装配类),原样上抛,由宿主归类。同一份判定,CLI 映到退出码,Web 映到 HTTP 语义:

| 类别 | 例 | CLI(RUNNERS.md §3.3) | Web(`app.py`) |
|---|---|---|---|
| 成功 | status=done | 0 | 200 + RunRecord |
| run 未开始(校验) | SkillLoadError、输入不合 schema | 2 | 200 + `{"status":"failed","error":...}`(`app.py:546`) |
| run 开始后失败/中止 | 技能返回错误、RunAborted | 3 | (在 RunRecord 内) |
| 宿主/基础设施 | 配置缺失、provider 装配失败 | 4(`_InfraError`,`main.py:46`) | 500/装配期异常 |
| 请求本身非法 | 未知 skill_set、未知断点 kind | 2 | 400/404/409 |

### 4.2 CLI:机器消费面

入口 `agent-os = agent_os.host.cli.main:main`(`pyproject.toml:31`),argparse 实现,零新依赖(RUNNERS.md §3.5)。子命令:`run / trace / inspect / resume / replay / diff / skills {validate,list} / lab validate / debug`(`main.py:447`)。输出契约(`main.py:125`):`--json` 时 stdout 仅 RunRecord 单行 JSON;缺省为人读摘要 + **末行 JSON**——人和机器各取所需,解析约定都是"取最后一行"。

CLI 同时是 supervisor 的**裁决通道**之一(`main.py:57`):run 挂起时把 question 以单行 JSON 写 stderr(coding agent 可解析的协议行,含 `kind` 以区分升权确认),从 stdin 读一行作答,单命令进程内闭环;`replay` 明确不注入该 handler——回放按 trace 记录值走,不问第二次(`main.py:276`)。

### 4.3 replay 与 diff:确定性复现

replay 的对齐规则利用了一个内核性质:runner 只在帧上下文**追加** LLM 响应,因此 trace 中第 N 个属于帧 F 的 `post:llm.response` 信号 ↔ checkpoint 中帧 F 的第 N 条 `role=="assistant"` 消息(`host/shared/replay.py` 模块 docstring)。`build_mock_script`(`replay.py:49`)按信号序取出这些消息重建 MockProvider 脚本;`finish_reason` 由有无 tool_calls 推导,usage 取信号载荷。`replace_providers`(`replay.py:96`)把 MockProvider 注册为原 model 前缀(`"mock/fib"` → `"mock"`),模型路由与原 run 自然吻合。**取舍**:脚本取自 checkpoint 的 assistant 消息而非 trace 载荷——载荷只是摘要,上下文里的消息才是模型响应的完整物(含 tool_calls 结构);代价是 trace 与 checkpoint 对不齐时 replay 直接报错(`ValueError` → 退出码 2),不猜测。

`diff_runs`(`replay.py:115`)按 `(name, payload.skill, payload.tool, payload.ok, payload.depth)` 逐位比较两条信号序列,忽略 frame_id/ts 等动态值;`first_divergence` 给出首个分岔下标(一方为前缀时取较短者长度)。它是**查询而非判决**:算出报告即退出码 0(`main.py:312`)。replay 的边界明示:只覆盖 LLM 调用面,工具副作用按真实环境重跑(RUNNERS.md §3.4)。

### 4.4 Web:RunManager 与 SSE 扇出

```
浏览器 SPA(静态文件,无构建)
   │  REST /api/*                SSE /api/runs/{id}/stream
   ▼
FastAPI 路由层(app.py) ──────── 只调 host/shared 读取层与 RunManager
   ▼
RunManager(进程内,run_manager.py)
   ├─ 每 run 独立内核:build_kernel(config 重新读) → 独立线程
   │    └─ execute_run 内部 asyncio.run 自起事件循环(与请求循环解耦)
   ├─ 信号扇出:总线订阅 "*" → SignalHub(环形缓冲 2000 + 订阅者队列)
   └─ 状态:内存 _active + 产物目录;历史 run 重启后由产物重建(冷数据)
```

三个关键机制:

- **每 run 一线程一内核**。设计稿(RUNNERS.md §4.2)写"run 作为 asyncio task 运行";实现改为独立线程 + 线程内 `asyncio.run`(`run_manager.py:519`),原因是 TestClient 每请求一个 portal、uvicorn 请求循环随时更替——run 的终态写回不能依赖任何请求循环存活(模块 docstring,`run_manager.py:1-13`)。以代码为准。每 run 重新装配内核还带来免费的隔离:`reload_skills` 只影响后续新建的 run,在跑的 run 钉住旧版(`run_manager.py:979`)。
- **SignalHub 原子快照**(`run_manager.py:269`):`subscribe` 在同一把锁内完成订阅者登记与缓冲快照,新客户端先收回放再收实时,**回放与实时之间无遗漏、无重复**;发布方(worker 线程)经 `loop.call_soon_threadsafe` 投递,订阅者循环已关闭的静默摘除——订阅者故障不得拖垮 run。run 结束投 `HUB_CLOSED` 哨兵,SSE 发 `event: end`(`app.py:1058`);15 秒 keepalive 防代理断连(`app.py:74`)。
- **`_StopBridge` 装配占位**(`run_manager.py:130`):M4 起内核**有 sidecar 才**装配 `kernel.ctl`,而 Web 的 stop 要求每个 run 都有 ctl。RunManager 给每个内核挂一个不订阅任何信号的 no-op ASYNC 副车,纯粹为了让 builder 走 sidecar 装配路径——这是宿主在内核契约内解决自身需求的典型手法,而不是要求内核改规则。

跨线程的另一处是调试命令桥:调试会话的 `asyncio.Event` 绑在 run worker 的循环上,REST 线程直接 `set()` 会撞线程检查,故 resume/modify/inject 一律经 `run_coroutine_threadsafe` 投递到 worker 循环执行(`run_manager.py:822`)。

**冷数据与半写窗口**:`_list_runs`(`app.py:317`)把产物目录重建的历史与内存态合并(meta 在、result 未落的视为在途——崩溃/断电遗留);历史 run 的 SSE 从 trace.jsonl 回放(`app.py:1050`)。产物落盘非原子,读侧以 `try/except` 降级为"在途,请重试"(`app.py:281`),这是 dev 工具对原子写文件的刻意回避(简单优先)。

**认证**(`app.py:497`,RUNNERS.md §4.5):单用户 localhost 免认证;给 `--token` 则全站走 Bearer 门,常量时间比对(`secrets.compare_digest`),EventSource 不能带自定义头故允许 `?token=`。`serve.py:67` 在绑定非 loopback 且无 token 时**拒绝启动**——该服务能执行带 shell_exec 的技能,无令牌暴露等于开放 RCE。

### 4.5 宿主即身份与裁决通道

数据层身份(docs/DATA-AUTHZ.md §2.2)由宿主构造:CLI 用 `cli_principal()`(本机用户,`main.py:163`),Web 用 `web_single_user_principal`(登录名取配置 `[web].user`,`run_manager.py:430`)。supervisor 通道按"run 级注入 > 装配级注入 > 进程共享 InboxChannel"顺序选择(`run_manager.py:409-413`):Web 收件箱即默认宿主通道,装配即得;CLI 则注入 `_cli_supervisor` 走 stderr/stdin 协议。升权确认、人审裁决因此不需内核感知宿主形态——这是"人在环路是一等公民"(执行摘要 §2.4)在宿主层的落点。

## 5. 效果与验证(效果)

**测试基线**:`tests/cli/` 4 文件 28 例 + `tests/web/` 12 文件 80 例,合计 108 个测试函数(参数化展开 116 例);本次全量运行 **116 passed,12.14s**。关键断言:

- `tests/cli/test_run.py:46`:run 成功 → 退出码 0、`"v": 1`、产物四件齐全、usage.steps 与帧数精确;
- `tests/cli/test_run.py:61/68`:校验错 → 2,run 失败 → 3;`tests/cli/test_replay.py`:录制-回放-diff 闭环;
- `tests/web/test_runs_api.py:103`:SSE 首帧回放 + `event: end` 终止;`test_runs_api.py:65`:列表含产物重建的历史 run;
- `tests/web/test_rca_control.py:59`:veto run 的 RCA 定位到裁决帧;`:115/127`:stop/resume 行为;
- 前端 24 个 `static/tests/*.test.mjs`(Node 直跑,无构建),覆盖帧树/时间线/RCA 面板/收件箱等组件——执行摘要"前端 24 个测试文件"即此。

**真实示例**:`instance/agent-os.toml`(真实 Kimi Code 端点 + mock 演示档双配置)与 `instance/run-web.sh`(从 kimi-code 凭据库取 token 后起 `agent-os-web`),即 §4 全部机制的落地形态。

**涟漪效应**:产物契约成为后续系统的公共地基——调试器的时间旅行(P5)直接消费 trace+checkpoint 重建回放脚本(`run_manager.py:756`);Skill Lab 的 test-run/G4 冒烟复用同一装配线与 replay 机制(`app.py:104`);`signal_row` 让 SSE、环形缓冲、trace.jsonl 三处行形同构(`run_manager.py:214`),前端一份解析代码吃三个数据源。

## 6. 局限性与边界(局限性)

1. **单用户 localhost,无多用户与权限**。RUNNERS.md §7 明示非目标;token 门只是防误暴露,不是多租户方案;持久化队列/分布式 worker/生产部署形态均不做。
2. **在跑的 run 重启即丢**。内存态不持久(§4.2"开发工具,可接受");遗留目录在列表里显示为"running",只能靠人工判读或 resume。
3. **replay 只覆盖 LLM 面**。工具副作用(fs/shell/docker)真实重跑,两次 replay 的外部世界可能不同;RUNNERS.md §3.4 的 `--sandbox` 强制档**已设计未实现**。
4. **环形缓冲截断**。hub 容量 2000 条(`run_manager.py:122`),超长 run 的 SSE 回放会丢头部信号(trace.jsonl 仍全量,但 `/signals` 的在途回退路径只有尾部)。
5. **产物写入非原子**。半写窗口靠读侧降级消化(§4.4),并发读写到极端时序可能给出"在途"的假相;不采用临时文件+rename 是刻意的简单性取舍。
6. **薄宿主边界靠纪律不靠工具**。RUNNERS.md §2.4 的 ruff/import-lint 守护标注"可选",实际未配置;宿主代码已 import `kernel.debug`/`kernel.checkpoint`/`supervisor` 等内核公开件(超出"只 import api.v1"的字面表述),边界由 review 维持。
7. **文档与实现的存留偏差(以代码为准)**:CLI `trace` 的 `--kind` 过滤未实现(Web 侧有,`app.py:363`);`resume` 只接受 checkpoint 路径,不接受 run_id;RUNNERS.md §2.2 布局中的 `stderr.log` 未落盘。
8. **一站多 set 的已知限制**:code 技能 handler 调用时惰性 import,跨 set 同名 handler 模块不支持,需改名规避(`run_manager.py:47`)。
9. **调试 SSE 是轮询**(0.1s)而非事件驱动(简单优先,`app.py:77`);浏览器端 E2E 仍手测,前端单测覆盖不到真实 SSE 时序。

## 7. 引用

- 文档:`docs/RUNNERS.md`(宿主设计基准);`docs/WEB-UI.md`(表现层);`docs/DEBUGGER.md`、`docs/SKILL-DEV.md`、`docs/SUPERVISOR.md`、`docs/DATA-AUTHZ.md`(相邻系统边界)
- 源码:`agent_os/src/agent_os/host/cli/main.py`、`host/cli/debug.py`;`host/shared/artifacts.py`、`host/shared/replay.py`、`host/shared/runrecord.py`;`host/web/app.py`、`host/web/run_manager.py`、`host/web/rca.py`、`host/web/serve.py`、`host/web/static/`;`agent_os/pyproject.toml`(entry points 与 `web` extra)
- 测试:`agent_os/tests/cli/`(4 文件)、`agent_os/tests/web/`(12 文件)、`host/web/static/tests/`(24 个 `*.test.mjs`)
- 实例:`instance/agent-os.toml`、`instance/run-web.sh`
