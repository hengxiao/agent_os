# Agent OS Runners 设计 — CLI Runner / Web UI Runner

> 版本:v0.1(设计稿)
> 定位:内核之上的**外部宿主**(DESIGN.md §11.3"服务化壳"的两个实现),面向开发测试场景
> 关系:DESIGN.md 是内核设计基准;本文档只消费内核契约,不向内核加需求

---

## 1. 定位与共同原则

两个 runner 都是**薄宿主**:内核是库,runner 只做三件事——组装(KernelBuilder + 配置文件)、调用(`kernel.run` / `resume`)、呈现(读取运行产物)。区别只在消费者:

| | CLI Runner | Web UI Runner |
|---|---|---|
| 消费者 | **Coding agent**(AI 编程助手) | **开发者**(人) |
| 目的 | 运行技能、拿回**机器可读**的 debug 数据 | 运行、观察、分析、**RCA**(根因分析) |
| 输出 | stdout JSON + 产物文件路径 | 浏览器页面(帧树/时间线/上下文检视) |
| 交互 | 一次性命令,脚本可组合 | 持续会话,实时推送 |

三条共同原则:

1. **薄宿主,不进内核**。runner 需要的所有数据都已存在:结果/usage(`kernel.run` 返回)、信号流(总线)、trace JSONL(Telemetry)、checkpoint(内核快照)、帧树(RunControl/信号载荷)。凡是要改内核才能做的功能,先回内核立项,不在宿主里打补丁。
2. **产物优先(artifacts first)**。debug 数据的载体是文件:trace、checkpoint、result、meta。CLI 产出它们,Web UI 读取它们;两者通过**同一个产物布局**互通——CLI 跑的 run,可以在 Web UI 里打开做 RCA。
3. **可复现**。两次运行同一技能,产物可 diff;真实 API 之外,一切可经 MockProvider 回放(replay)确定性复现。

---

## 2. 共享基础

### 2.1 配置文件 `agent-os.toml`

两个 runner 用同一份配置(等价于 §14.2 的 KernelBuilder 链式组装,TOML 形态):

```toml
[run]
model = "kimi/kimi-k2-thinking"     # 或 anthropic/claude-sonnet-4
max_depth = 8
max_steps = 200
max_cost = 2.0
max_wall_time = 1800
compression = "hierarchical"        # off|truncate|spill|summarize|hierarchical;off = 消融档
# stream = false                    # 关掉流式消费(缺省 true:caps 支持走 stream chunk 循环,逐 chunk
                                    # 发 post:llm.chunk 并记 ttft/total;caps 不支持自动回落 chat)
# workdir = "/path/to/project"      # §W0-1:run 工作目录(fs/shell 可写区;缺省每 run 临时目录)
# read_paths = ["/path/to/vendor"]  # §W0-1:只读挂载(可在 workdir 之外,如源码目录)

[providers.kimi]                    # KimiProvider();key 走环境变量
[providers.anthropic]               # ClaudeProvider()
# [providers.openai]
# base_url = "http://localhost:8000/v1"   # vLLM/Ollama 等兼容端点
# api_key_env = "OPENAI_API_KEY"

# [providers]
# router = "my_pkg.routers:MyRouter"  # 自定义模型路由(§4.2 ModelRouter 扩展点;dotted 无参实例化,
                                      # 加载/实例化失败 ConfigError;缺省 DefaultModelRouter:prefer 链
                                      # 按序探测 caps(tools/vision),fail-open 落链首)

[tools]
builtins = true                     # 内置工具面(27 件规范名:system.file.*/shell/net/blob/time/task/skill/memory/user/timer/schedule/monitor/channel 等,旧扁平名留别名;system.schedule.set = 跨 run 派生(2026-10-01,WRITE·confirm=True),web 宿主 [schedule] 段在场时绑 store-backed 服务,CLI 不绑 → 结构化"未装配";system.monitor.set(EXEC)/system.channel.connect(WRITE) = ch04 事件监控两件(2026-10-01 落地),fire 通道经 bind_monitor 恒装配,未 bind → 结构化"未装配",行为边界见本节末「内置事件监控工具」段)
python_exec = "docker"              # docker | subprocess | off

[skills]
path = "./skills.yaml"              # LocalFileSkillRegistry(也可指目录:多 *.yaml 排序合并;register() 目录写恒落 <dir>/registered.yaml)
# watch_interval = 5.0               # 热重载 watcher 轮询秒数(2026-09-29;daemon 线程查源文件 mtime,变了 reload,失败吞异常旧表不动;默认 0 = 关)
# register_smoke = "default"            # register() 入库前验证门(2026-09-29):"default" 哨兵 = 默认重放 evaluator
                                        # (skills/register_smoke.py:草稿 drafts/<name>/tests/*.json 重放 + expected 确定性深比较
                                        # + expect LLM 裁判,全过才放行;code 技能经 entry["_source"] 源码登台临时目录 + 沙箱真冒烟
                                        # (2026-09-30;无沙箱后端/缺 _source 仍 fail-closed),冒烟内核剥离 watcher/MCP/sidecars);
                                        # 或 "my_pkg.gates:smoke" 自定义 hook("module:func",sync/async 均可;签名 (name, entry)
                                        # 或 (name, entry, provenance)(2026-09-30 可选第三参,bind 时签名内省,二参零破坏);
                                        # ok 非真/异常 → GateError fail-closed 零写)
# register_judge_model = "kimi/cheap"    # 默认验证门 expect 判定的裁判模型(缺省跟 [run] model;需 [providers] 有可用 provider,
                                        # 否则带 expect 的用例 fail-closed)
# register_runs_root = ".agent-os/runs"  # 默认验证门录制重放证据阶段的 run 产物根(2026-09-30;str,缺省同 CLI/Web artifacts 缺省;
                                        # system.skill.register 的 source_run_id 引用的 <runs_root>/<id>/ 四件套在此下找)
# 注:strict 校验;watch_interval/register_smoke/register_judge_model/register_runs_root 需配合 path,缺 path → ConfigError;
# 默认验证门草稿根 = [lab].drafts_root(缺省 skills path 同级 drafts/;详见 SKILL-DEV.md §7)

[sidecars]
budget_guard = { max_cost = 2.0 }
loop_detector = { threshold = 3, max_strikes = 2 }
# tool_guard_rules = [["system.shell.exec", "rm -rf", "理由"]]  # 每项 = [工具名, 参数正则, 否决理由]
# human_approval = true                # WS2:EXEC 档工具过内核 tool-confirm 闸门(supervisor.ask,kind="tool-confirm")
# human_approval = { timeout = 300, on_timeout = "deny" }  # 或表:审批超时秒数 + 超时兜底(deny|allow)
# distill = true                       # 蒸馏 sidecar(DESIGN §11.2):run 终态后经廉价模型蒸馏经验写入 Memory——
                                      # run.aborted 恒触发 failure reflection;run.finished 需帧树 TOOL 消息数
                                      # > min_tool_calls 才触发 strategy summary;需 [memory] 段配合(缺则休眠)
# distill = { model = "kimi/cheap", min_tool_calls = 5, temperature = 0.2, breaker_threshold = 3, max_transcript_chars = 24000, verify = true }
                                      # 或表(strict 校验,未知键报错):model 缺省跟 [run] model;
                                      # breaker_threshold = 连败熔断阈值(默认 3);max_transcript_chars = 转写总量上限;
                                      # verify = 入库审查门(默认 true):写 Memory 前同模型 temperature=0.0 复审
                                      # (指令注入/秘密 PII/可迁移性),fail-closed 拒写且不计连败

[telemetry]
dir = ".agent-os/traces"             # WAL 目录(strict 段;配 redact/otlp 缺 dir → ConfigError——OTLP 是 exporter 不是 sink 替代)
# redact = true                      # PII 脱敏 hook(2026-09-30,DESIGN §10.2;默认 false):record 入口把 payload 换成
                                     # 脱敏副本,WAL 行与 exporters 共用同一份(telemetry/redact.py 五形态 regex 快筛:
                                     # email/phone_cn/id_card_cn/bank_card/api_key → [EMAIL] 式占位,与 std 技能
                                     # common.security.redact_pii 同款语义);开启后 WAL 不再逐字保真(合规取舍),
                                     # replay 依赖的 usage 数值不受影响
# [telemetry.otlp]                   # OTLP exporter(2026-09-30,telemetry/otlp_exporter.py):信号流 → span 树,
                                     # OTLP/HTTP JSON POST {endpoint}/v1/traces(零新依赖,无 protobuf);
                                     # best-effort——POST 失败丢批不重试、队列满丢最旧(限速 warning);
                                     # export() 零 IO(有界队列 + 后台 drainer),不阻塞 run 关键路径
# endpoint = "http://localhost:4318" # 必填,http(s):// 单端点;缺/非 http(s) → ConfigError
# headers = { Authorization = { env = "OTLP_TOKEN" } }
                                     # 值 = 字符串字面量或 { env = "VAR" } 间接引用(装配时现读 os.environ,
                                     # 不落盘明文,同 [credentials]/mcp headers 先例;变量缺席 → ConfigError)
# batch_max = 64                     # 单批 span 数上限(攒够即 flush)
# flush_interval = 2.0               # 周期 flush 秒数(到点发)
# queue_max = 1000                   # 有界队列上限;满丢最旧 + _dropped 计数
# timeout = 5.0                      # 单次 POST 超时秒数;四个调参键均须正数(strict,未知键 ConfigError)
                                     # 宿主责任:close() 是唯一排干点——CLI run/resume/replay finally 已接
                                     # (_close_telemetry);Web 宿主无 shutdown/lifespan 钩子未接,进程退出
                                     # 丢弃 exporter 队列余量(best-effort 语义内,已知缺口)

[retry]
max_attempts = 3
backoff_base = 0.5
# stream_idle_timeout = 30.0           # 流式停滞 watchdog 秒数:N 秒无新 chunk 杀流(提交点语义:
                                       # 首 chunk 产出前可退避重试,产出后错误直接上抛)

[credentials]                          # WS1:凭证作用域;值只存 env 变量名,不落盘明文
# github = { env = "GITHUB_TOKEN" }    # 工具 ToolSpec.credentials 声明键 → dispatch 每次现解析注入(env 缺席键不出现)

# [mcp.servers.<name>]                 # MCP server 工具面(tools/mcp.py stdio + tools/mcp_http.py Streamable HTTP,DESIGN §8.3);
                                       # 段存在才接线,缺段零破坏;command(stdio)/url(http)恰居其一,同给/同缺均 ConfigError
#   command = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/srv"]
                                       # stdio 必填,非空字符串数组;eager 装配:装配期拉起子进程 initialize 握手 + tools/list,
                                       # 工具注册为 mcp.<server>.<tool>(走全量 dispatch 管线),连接失败 ConfigError 快速失败;
                                       # npx 包版本警告(2026-10-06):command 末参形似 npm 包(@scope/name 或裸名)而无 @version
                                       # → 每 client 一次 warning 引导钉 @x.y.z(只引导不阻断;-y 等 flag/路径/已钉版不误伤);
                                       # 派生工具(2026-10-06):server 宣告 resources/prompts capability 且清单非空 → 各注册一个
                                       # mcp.<server>.resource_read(参数 uri)/mcp.<server>.prompt_get(参数 name,arguments),
                                       # permission/timeout/confirm 随本 server spec;blob 资源块溢写 blob store 返 blob:// ref
#   url = "https://mcp.example.com/mcp"
                                       # http 必填,http(s):// 单端点 POST(Streamable HTTP,spec 2025-03-26 版族;
                                       # initialize 捕获 Mcp-Session-Id,404 自动重连重 initialize 一次)
#   transport = "auto"                 # 缺省 auto(按键判:url → http,command → stdio);显式 stdio|http 与键不符 → ConfigError
#   protocol_version = "2025-03-26"    # 可选,非空字符串;缺省随传输(stdio 2024-11-05 / http 2025-03-26);
                                       # 显式给 = 钉扎值(2026-10-06):initialize 协商回应 ≠ 钉扎请求值 → McpError 拒连快速失败
                                       # (拒绝静默降级;确要换版本 = 显式改此键,装配归 ConfigError)
#   env = { API_KEY = { env = "MCP_API_KEY" } }
                                       # 可选(stdio);子进程不继承宿主 env,值 = 字符串字面量或 { env = "VAR" } 间接引用(连接时现读 os.environ)
#   headers = { Authorization = { env = "MCP_TOKEN" } }
                                       # 可选(仅 http 有意义,配在 stdio server 上 → ConfigError);值 = 字面量或 { env = "VAR" } 间接引用(同 env 先例)
#   permission = "read"                # 缺省 read(最小授权),可升 write|net|exec;confirm = true 则该 server 工具过 tool-confirm 闸门
#   timeout = 30.0                     # 单次调用(tools/call·resources/read·prompts/get)超时秒数;connect_timeout = 10.0 为连接/握手截止
                                       # (server 名只许 [A-Za-z0-9_-],进工具命名空间;全部键 strict 校验,未知键 ConfigError)
                                       # 维持开口(2026-10-06 边界,DESIGN §8.3):懒连接不做——eager 定案重申(G2 tools.has/runner
                                       # 白名单与 §8.2 配置即授权要求装配期工具面验明确定);GET standalone SSE/Last-Event-ID
                                       # resumability 不做(registry 无 unregister,list_changed 推送无安全落点);OAuth 不做
                                       # ({env} 静态 token + 重连重新解析即轮换已覆盖多数场景,交互 flow 建议宿主带外跑后注入);
                                       # HTTP 真实 server 互测留口(官方 server 多无 HTTP CLI 形态;罐头对端已扩面)

# [data]                               # D2 数据层 authZ(docs/DATA-AUTHZ.md §3);段存在即接线——空段 = 绑定空 policy,
                                       # principals 白名单 fail closed 全拒,启用前务必配齐 domains 与白名单
# domains = [                          # 表数组:name 必填;sensitivity 缺省 confidential(忘了配 = 最严)
#   { name = "fs.shared", sensitivity = "internal", path_prefix = "/srv/shared" },  # path_prefix|url_prefix 恰居其一
#   { name = "net.public", sensitivity = "public", url_prefix = "https://api.example.com/" },
# ]
# [data.principals."user:alice"]       # per-subject 域白名单(glob 域名模式,并入 allow() 第二判据)
# domains = ["fs.*", "net.public"]

[web.tokens]                           # D3-lite 多用户映射:Bearer token → subject(命中 → Principal(issuer="api-token"))
# "tok-alice-xxx" = "user:alice"       # 未命中/段缺席(含空段)→ 单用户行为逐字不变

# [memory]                             # M6 记忆子系统(docs/DESIGN.md §11):段存在才接线,缺段完全不 bind
# dir = "./memory"                     # LocalFileMemoryService 根目录(每条目一 Markdown + frontmatter);
                                       # 接线后 system.memory.search/write 经 bind_memory 装配(未装配调 NOT_FOUND)
# recall_k = 3                         # 经验注入槽检索条数(2026-09-28;另需 manifest context_policy.recall: true
# recall_entry_chars = 800             # 才启用,默认关、缺声明零检索零信号;三键 strict 校验)
# recall_total_chars = 2000            # 每条注入字符上限 / 注入段总字符上限;缺段或未 bind → 槽位跳过

# [blob]                               # M3 spill 文件存储(docs/DESIGN.md §8.4/§7.2):段存在才接线,
                                       # 缺段 = 进程内 InMemoryBlobStore(零破坏)
# dir = "./blobs"                      # FileBlobStore 根目录(<root>/<run_id>/<sha256> 内容寻址落盘,
                                       # run_id/sha 白名单防目录逃逸)

# [context]                            # §7.2 压缩链调参(2026-09-28);缺段 = 全默认
# summarize_model = "kimi/kimi-k2-thinking"  # summarize 策略的摘要模型,缺省跟 [run] model
# spill_threshold_chars = 4000         # TOOL 消息超此字符数移入 blob store(冻结 [SPILLED] 替换串)
# summarize_breaker = 3                # 摘要连败熔断次数;熔断开/无 providers 退化纯截断([COMPRESSED:truncate])
# summarize_temperature = 0.2          # 摘要采样温度

# [events]                             # 事件批处理(2026-09-29,POST /api/events 在跑通道,§4.3);缺段 = 全默认
# batch = true                         # 在跑 run 的事件先入根帧队列(working["_event_queue"]),下一步 build 前
                                       # 排干为一条批头消息([event 批处理 N 条];只在 build 前并入,保 §7.4 配对
                                       # 原子性;队列随 checkpoint 落盘,resume 自然排干);false = 立即注入根帧
# batch_max = 50                       # 单批条数上限;超出丢最旧,批头 meta.dropped 计数
# event_text_max = 2000                # 事件文本截断字符数([event:<type>] <payload JSON> 超此截断)

# [schedule]                           # 宿主调度器(2026-10-01,§17-3 持久事件调度 / §8.3 跨 run 计时器 / D3 自动派生的统一节拍;仅 Web 宿主装配——
                                       # 持久调度表 <artifacts_root>/schedule.json(原子写),POST /api/events 带 delay_seconds/at 的停车条目
                                       # 与 system.schedule.set 的派生条目到点经 RunManager.dispatch_event 派发,paused run 的过期计时器
                                       # 先 settle-in-file 再 resume(先结算后唤醒不双火));段缺席 = 调度器不启动(零破坏:停车 409、工具报"未装配");
                                       # 单宿主假设:schedule.json 由本进程独占,多宿主共用同一 artifacts_root 会重复派发(跨进程互斥未做)
# interval_seconds = 5.0               # 调度节拍秒数(float>0,strict;守护线程每拍现读,改动即生效)
```

加载器落点:`runtime/config.py`(已实现:CLI/Web 两个宿主共用,均支持 `--config`)。

**内置事件监控工具 monitor/channel 行为边界(2026-10-01 落地,`tools/monitor.py` 的 MonitorService;两个 runner 共用内核,行为一致)**:

- **触发**:`system.monitor.set`(`{command, pattern?, note?, max_fires=20}` → monitor_id;EXEC 档)起 asyncio 子进程(独立进程组,stdout/stderr 合并逐行读);`system.channel.connect`(`{path, pattern?, note?, interval=1.0, max_fires=20}` → channel_id;WRITE 档,路径经 `resolve_work_path` 三段判定、越界 INVALID_ARGS)轮询事件文件(缺省 1.0s,下限钳 0.5s)新增完整行。两者均 regex 命中即经 `ctl.inject_message` 注入**调用帧**(USER/INJECTED);工具本身立即返回 id,不占调用回合空等。
- **封顶**:命中次数到 `max_fires`(缺省 20)注入一条封顶事件后自停(shell 源同步终止进程组);shell 进程退出另发 exit 事件(带 exit_code)并标规格 done。
- **重武装边界**:规格随帧 `working["_monitors"]` 落 checkpoint,resume 经 `_settle_pending_monitors` 重武装——shell 源 = **命令从头重跑**(输出重放可能重复命中,caveat 已注明);channel 源 = 从持久化 `offset` 续读**不重放**(末尾残行不消费;rotation(size < offset)→ offset=0 从文件头续读,记 log 不注入)。
- **单 run 作用域**:run 收尾 `release_run` 取消本 run 全部在册监控(shell 源进程组同步 SIGKILL),不跨 run 误杀、不标 done(统一留给 resume 重武装)。
- **防空耗**:pattern 可选 regex(缺省空串 = 每行命中,description 明示慎用)+ `max_fires` 封顶 + 命中行截 200 字符;注入文本由纯函数 `format_match_text`/`format_exit_text`/`format_cap_text` 单点构造。

### 2.2 产物布局 `.agent-os/runs/<run_id>/`

每次运行落一个目录(runner 从 `run.started` 信号捕获 run_id 后组织):

```
.agent-os/runs/<run_id>/
├── meta.json         # {run_id, skill, input, host: "cli"|"web", started_at}(resume 产物目录加 resumed_from)
├── trace.jsonl       # Telemetry WAL(版本头,全部信号)
├── checkpoint.json   # 结束/中止/挂起(paused)时自动快照(帧含完整上下文)
└── result.json       # {status, result, error, usage 汇总}
```

- `trace.jsonl` 由 JsonlTelemetrySink 按 run 写入,runner 在 run 结束后归档到该目录;
- `checkpoint.json` 由 runner 在 `run.finished`/`run.aborted`/`run.paused` 时调 `kernel.checkpoint(run_id, path)`;
- **checkpoint 是 RCA 与 resume 的数据源**:帧的完整上下文(模型每一步看到了什么)都在里面。

### 2.3 调试数据分类法(两个 runner 共用)

| 数据 | 载体 | 回答的问题 |
|---|---|---|
| 结果与错误 | `result.json` | 成了还是败了,败在哪一类错误 |
| 信号时间线 | `trace.jsonl` | 发生了什么,按什么顺序 |
| 帧树 | checkpoint / `post:frame.*` | 调用了谁,谁挂起,谁失败 |
| 帧上下文 | checkpoint | **模型那一步看到了什么**(RCA 核心) |
| LLM 请求/响应 | `pre:llm.request` / `post:llm.response` + checkpoint | 提示词对不对,模型回了什么 |
| 工具调用与结果 | `pre/post:tool.call` + TOOL 消息 | 调用是否合法、被谁否决、结果是什么 |
| veto/纠偏 | veto 回写 + `InjectMessage` | 哪个 sidecar 介入了,理由是什么 |
| usage 细分 | 帧/Run `Usage`(含 cache_read/write、thinking) | 钱花哪了,哪帧是大头 |
| 压缩事件 | `pre/post:compress` | 上下文何时被压,压掉了什么 |

### 2.4 包结构(薄宿主边界)

```
src/agent_os/host/
├── shared/           # 配置加载、产物组织、RunRecord 读取层
├── cli/              # CLI runner(argparse,无新依赖)
├── web/              # Web runner(FastAPI,optional extra `agent-os[web]`)
└── web_platform/     # Web Platform(对话中枢宿主,docs/WEB-PLATFORM.md)
```

`host/` 是独立顶层包:只 import `agent_os.api.v1` 与内核公开件(builder/manager/sink/checkpoint),不 import 内核私有实现——边界由 ruff 自定义规则或 import-lint 守护(可选)。

---

## 3. CLI Runner 设计

### 3.1 场景

一个 coding agent 在 Agent OS 上开发/调试技能:改完 `skills.yaml` 或 handler,需要跑一遍并拿到**结构化、可断言、可 diff** 的 debug 数据。它不要人类可读的漂亮输出,要 JSON、退出码、文件路径。

### 3.2 命令集

```
agent-os run <skill> --input '<json>'|@file
    [--config agent-os.toml] [--json] [--artifacts .agent-os]
    [--inline on|off] [--checkpoint-interval N]
  → 运行;stdout = RunRecord JSON(见 3.3);产物落盘
  (注:--skills/--model/--max-cost/--seed 旗标未实现——覆盖走 agent-os.toml;
   单次覆盖只有 Web 的 POST /api/runs overrides 面,见 §4.3)

agent-os trace <run_id> [--format json|table] [--artifacts .agent-os]
  → 从 trace.jsonl 读信号时间线(coding agent 用 json;--kind 过滤未实现)

agent-os inspect <run_id> [--frame <frame_id>] [--messages] [--json]
  → 从 checkpoint.json 读帧树;--frame 指定帧输出其完整上下文
    (messages 逐条:role/source/content/tool_calls)——"模型当时看到了什么"

agent-os resume <checkpoint.json> [--config agent-os.toml] [--json]
  → 从 checkpoint 恢复运行(断电/改代码后续跑)
  (注:只收 checkpoint 路径,不收 run_id;无 --model/--max-cost 覆盖旗标)

agent-os replay <run_id> [--config agent-os.toml] [--json]
  → 用 trace 里的 LLM 请求/响应对构建 MockProvider 脚本,确定性重放
    该次运行(不碰真实 API);验证修复是否改变行为

agent-os diff <run_id_a> <run_id_b> [--json]
  → 两次运行的结构化 diff(result/usage/信号序列一次全算;--kind 细分未实现)

agent-os debug [skill] [--input '<json>'|@file] [--replay <run_id>] [--until-step N]
  → 交互式调试 REPL(断点/单步/检视/注入;--replay = 时间旅行回放)

agent-os skills list|validate <path.yaml> [--json]
  → manifest lint(描述质量/权限引用/循环依赖),开发者自检

agent-os lab validate <name> [--config agent-os.toml] [--json]
  → 草稿跑提交闸门(G1-G5,与 Web 同一 gate.py);pass/warn 退出码 0,fail 2
```

### 3.3 输出契约(coding agent 的消费面)

**stdout**:RunRecord JSON(`--json` 时唯一输出;无 `--json` 时人读摘要 + 末行 JSON):

```json
{
  "run_id": "r-...",
  "status": "done | failed | aborted | paused",
  "result": { "...": "技能返回值" },
  "error": null,
  "usage": { "steps": 10, "prompt_tokens": 0, "completion_tokens": 0,
             "cache_read_tokens": 0, "cache_write_tokens": 0,
             "thinking_tokens": 0, "cost": 0.0 },
  "frames": 4,
  "artifacts": {
    "dir": ".agent-os/runs/r-...",
    "trace": ".../trace.jsonl",
    "checkpoint": ".../checkpoint.json"
  }
}
```

**stderr**:人类可读日志(信号摘要、警告)。**退出码**:

| code | 含义 |
|---|---|
| 0 | run 成功(status=done) |
| 2 | 输入/配置/技能校验错误(SkillLoadError、输入不合 schema) |
| 3 | run 失败或中止(技能返回错误、OutputValidationError、RunAborted) |
| 4 | 宿主/基础设施错误(配置缺失、provider 不可达、docker 不可用) |

### 3.4 replay 与 golden 调试

- trace 的 `pre:llm.request`/`post:llm.response` 载荷含模型与消息摘要;replay 按**顺序匹配**构建 MockProvider 脚本(与 fib 测试同一机制),重放整棵帧树;脚本重建(`build_mock_script`/`replace_providers`)已下沉 `telemetry/replay.py`(2026-09-30,trace 格式所有者;register() 验证门的录制重放证据同样消费,`host/shared/replay.py` re-export 兼容、`diff_runs` 留 host);**对齐修复(2026-09-30)**:对齐计数过滤 `payload.source=="compress"` 的 post:llm.response——压缩排干补发信号无对应 assistant 消息,此前 CLI replay 对压缩 run 会错配/ValueError;
- 用途 A(回归):改了技能实现后 replay,`diff` 与原始 run 的信号序列;
- 用途 B(复现):线上失败的 run 拿回本地 replay,在 checkpoint 上 `inspect` 逐帧检查;
- 边界:replay 只能覆盖 LLM 调用面;工具副作用(fs/shell/docker)按真实环境执行。`--sandbox` 档(把 system.shell.exec/python_exec 强制切到 docker 后端)未实现——目前只能改 `[tools] python_exec` 配置。

### 3.5 实现要点

- `argparse`(stdlib,零新依赖);entry point `agent-os = agent_os.host.cli.main:main`;
- 子命令输出全部经同一 `RunRecord` 序列化函数,coding agent 只依赖这一个 JSON schema(版本化,`"v": 1`);
- 测试:`capsys`/子进程跑 CLI,MockProvider + tmp_path 技能文件;断言 stdout JSON schema、退出码、产物文件存在与内容。

---

## 4. Web UI Runner 设计

### 4.1 场景

开发者(人)在浏览器里:跑一个技能 → **实时**看它逐帧推进 → 失败后做 RCA:哪一帧失败、那帧的模型看到了什么、哪个 sidecar 否决了什么、预算花在哪儿。

### 4.2 架构

```
浏览器 SPA(静态文件,无构建步骤)
   │  REST /api/*           SSE /api/runs/{id}/stream
   ▼
FastAPI app ─── RunManager(进程内)
   │               ├─ KernelBuilder.from_config(agent-os.toml)
   │               ├─ 每个 run 一个内核 task(kernel.run/resume)
   │               └─ 信号扇出:总线订阅 → 内存环形缓冲 → SSE 订阅者
   └── RunRecord 读取层(host/shared):trace/checkpoint/result 读取与分页
```

- **RunManager**:单进程、单事件循环;run 作为 asyncio task 运行;状态只留内存 + 产物目录(`.agent-os/runs/`)——重启后历史 run 从产物目录重建索引(冷数据),在跑的 run 重启即丢(开发工具,可接受);持久调度表(`[schedule]` 段启用时的 `<artifacts_root>/schedule.json`,2026-10-01)随产物目录原子落盘,重启后到点条目由调度器继续结算——**单宿主假设**:调度器假定单一宿主进程独占 artifacts_root,多宿主共用同一目录会重复派发(跨进程互斥留开口);
- **SSE 扇出**:RunManager 在装配内核时向总线订阅 `"*"`,把信号写进 per-run 环形缓冲(默认 2000 条)并广播给 SSE 客户端;新客户端先收回放再收实时;
- **前端**:单页静态应用(一个 `index.html` + 原生 JS + SSE `EventSource`),FastAPI 挂静态目录;**无 npm 构建、无框架依赖**——它是 dev 工具,维护成本必须趋近于零;
- **依赖**:`fastapi` + `uvicorn` 进 optional extra(`pip install agent-os[web]`),不进主依赖。

### 4.3 API 契约

```
POST   /api/runs                     {skill, input, overrides?} → {run_id}
GET    /api/runs                     → [{run_id, skill, status, started_at, cost}]  (含历史,来自产物索引)
GET    /api/runs/{id}                → RunRecord 详情(status/result/error/usage/帧树)
GET    /api/runs/{id}/signals?kind=&after=  → 信号时间线(分页;kind=llm|tool|frame|sidecar|all)
GET    /api/runs/{id}/frames/{fid}   → 帧完整上下文(messages 逐条、usage、input/result/error)
POST   /api/runs/{id}/stop           → RunControl.stop
POST   /api/runs/{id}/pause          → RunControl.pause(可恢复挂起;可选 {"reason"},缺省 "web pause";仅 running 生效,否则 409)
POST   /api/runs/{id}/resume         → 从 checkpoint 恢复(aborted/paused 均可)
POST   /api/events                   → 外部事件唤醒入口(2026-09-29;在跑通道批处理同日落地;定时停车 2026-10-01 落地)。请求 {type(必填非空), payload=dict|str, target?:{run_id}, skill?, input?, wait?, delay_seconds?, at?};delay_seconds(相对秒数)/at(epoch 秒绝对时刻)二选一且须 >0(同现/非正 → 400)→ 停车进持久调度表(<artifacts_root>/schedule.json,ScheduleStore 原子写),响应 {"action":"scheduled", schedule_id, fire_at},到点由宿主调度器经 RunManager.dispatch_event 派发(与即时通道同一份三通道由);两键缺席 = 逐字旧行为;调度器未启用([schedule] 段缺席)时请求调度 → 409 fail-closed。即时三通道:target 且 running → 按 [events].batch 分流(§2.1):开(缺省)→ 事件条目 {type, text, at} 入根帧队列 working["_event_queue"],下一步 build 前排干为一条批头消息([event 批处理 N 条],USER/INJECTED;超 batch_max 丢最旧计 dropped;队列随 checkpoint,resume 零钩子自然排干)→ {action:"queued"};关 → 立即注入根帧(ctl.inject_message,USER/INJECTED)→ {action:"injected"};target 且 paused → checkpoint 根帧注入事件后 resume → {action:"resumed"};无 target → skill 必填起新 run(input 缺省 {"event":{...}},wait/principal 透传)→ {action:"started"}。错误语义:缺 type/skill 400;未知 run 404;注入失败/paused checkpoint 坏或缺根帧/run 终态 409。路由成功后 per-run hub 投 event.received received/routed 两条(SSE 可见;刻意不进 api/v1、不写 trace.jsonl)
GET    /api/schedule                 → 持久调度表挂起条目清单(2026-10-01;[schedule] 段未启用 → 空表)
GET    /api/runs/{id}/stream         → SSE:先回放缓冲,后实时信号
GET    /api/skills                   → 已加载技能清单(manifest 摘要)
POST   /api/skills/reload            → 热重载 skills.yaml
```

### 4.4 页面与 RCA 流程

三个视图,按 RCA 动线组织:

1. **Run 列表页**:状态/技能/耗时/成本列;失败行红色;点进详情。
2. **Run 详情页(主 RCA 面)**:
   - 左:帧树(深度缩进,状态色:running/done/failed;每帧 skill、steps、cost);
   - 中:**信号时间线**(类型着色:llm 蓝/tool 绿/sidecar 黄/error 红;点击任意信号定位到对应帧上下文);
   - 右:帧上下文检视器(messages 逐条渲染:role/source 标签、tool_calls 折叠、veto 红条、纠偏黄条);
   - 顶:usage 面板(steps/tokens/cache_read/cache_write/thinking/cost,按帧分列)。
3. **失败定位(RCA 一键)**:"Jump to first error"——找到时间线上首个错误类信号(veto / 失败 tool result / run.aborted),自动选中该帧,展示**产生错误的那个 LLM 请求**(pre:llm.request 载荷 + checkpoint 中该帧当时的 messages)与**错误观察原文**;veto 场景同时高亮裁决的 sidecar 与理由(§5.2 理由回写已经在数据里,UI 只是把它做成一键可达)。

实时页与详情页同一组件:`/runs/{id}` 在 run 进行中自动挂 SSE,信号追加进时间线,帧树状态随 `pre/post:frame.*` 更新。

### 4.5 实现要点

- FastAPI 路由层只调用 host/shared 的读取层与 RunManager,**不 import 内核私有实现**;
- RunManager 内部复用 CLI 同一套产物组织代码(两个 runner 产物互通:CLI 跑的 run 会出现在 Web 列表页);
- 测试:FastAPI `TestClient` + MockProvider;断言 API 契约(状态码/JSON 形状)、SSE 首帧回放、stop/resume 行为;前端不自动化测试,手测为主(dev 工具);
- 单用户 localhost:无认证;`--host 127.0.0.1` 默认,绑定非 loopback 时要求 `--token`。

---

## 5. 测试策略

- CLI:`pytest` 调 main()/子进程;MockProvider + tmp 技能文件;stdout JSON schema、退出码、产物三件套;
- Web:FastAPI TestClient;API 契约 + SSE + resume;
- 共享:一组 **fixture run**(fib 成功、veto 失败、循环中止、断电恢复)同时喂给 CLI 与 Web 的测试,保证两个 runner 读同一份产物行为一致;
- replay/diff:录制一次 fib(5) run,replay 后 diff 信号序列应为空。

## 6. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **R1 CLI 核心** | config 加载、产物组织、`run/trace/inspect/resume`、输出契约 | coding agent 一条命令拿到完整 debug JSON;退出码正确 |
| **R2 CLI 复现** | `replay`/`diff`/`skills validate` | fib(5) 录制回放 diff 为空 |
| **R3 Web 基础** | FastAPI、RunManager、SSE 扇出、列表页 + 详情页(帧树/时间线/上下文) | 浏览器实时看 fib 运行;历史 run 可打开 |
| **R4 Web RCA** | 失败定位一键、usage 按帧面板、stop/resume 按钮、skills reload | 三类失败(veto/循环/断电)均可一键定位到出错帧与请求 |

## 7. 非目标

多用户与权限、持久化队列/分布式 worker、生产级部署形态、前端框架化(npm/构建链)、cron 表达式与跨进程多宿主互斥(单宿主定时调度已落地:POST /api/events 三通道 + [events] 段(2026-09-29)+ [schedule] 段宿主调度器与 system.schedule.set(2026-10-01),见 §4.3/§2.1)、CLI 的交互式 TUI。
