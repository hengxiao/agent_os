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
compression = "hierarchical"        # off = 消融档

[providers.kimi]                    # KimiProvider();key 走环境变量
[providers.anthropic]               # ClaudeProvider()
# [providers.openai]
# base_url = "http://localhost:8000/v1"   # vLLM/Ollama 等兼容端点
# api_key_env = "OPENAI_API_KEY"

[tools]
builtins = true                     # fs_read/fs_write/fs_edit/shell_exec/http_fetch
python_exec = "docker"              # docker | subprocess | off

[skills]
path = "./skills.yaml"              # LocalFileSkillRegistry

[sidecars]
budget_guard = { max_cost = 2.0 }
loop_detector = { threshold = 3, max_strikes = 2 }
# tool_guard_rules = "./tool-guard.rules"

[telemetry]
dir = ".agent-os/traces"

[retry]
max_attempts = 3
backoff_base = 0.5
```

加载器落点:`runtime/config.py`(目前 M0 stub,随 R1 一并实现)。

### 2.2 产物布局 `.agent-os/runs/<run_id>/`

每次运行落一个目录(runner 从 `run.started` 信号捕获 run_id 后组织):

```
.agent-os/runs/<run_id>/
├── meta.json         # {run_id, skill, input, config 摘要, started_at, host: "cli"|"web"}
├── trace.jsonl       # Telemetry WAL(版本头,全部信号)
├── checkpoint.json   # 结束/中止时自动快照(帧含完整上下文)
├── result.json       # {status, result|error, usage 汇总}
└── stderr.log        # 内核与宿主日志
```

- `trace.jsonl` 由 JsonlTelemetrySink 按 run 写入,runner 在 run 结束后归档到该目录;
- `checkpoint.json` 由 runner 在 `run.finished`/`run.aborted` 时调 `kernel.checkpoint(run_id, path)`;
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
└── web/              # Web runner(FastAPI,optional extra `agent-os[web]`)
```

`host/` 是独立顶层包:只 import `agent_os.api.v1` 与内核公开件(builder/manager/sink/checkpoint),不 import 内核私有实现——边界由 ruff 自定义规则或 import-lint 守护(可选)。

---

## 3. CLI Runner 设计

### 3.1 场景

一个 coding agent 在 Agent OS 上开发/调试技能:改完 `skills.yaml` 或 handler,需要跑一遍并拿到**结构化、可断言、可 diff** 的 debug 数据。它不要人类可读的漂亮输出,要 JSON、退出码、文件路径。

### 3.2 命令集

```
agent-os run <skill> --input '<json>'|@file
    [--config agent-os.toml] [--skills path.yaml] [--model m]
    [--max-cost 1.0] [--seed 42] [--json] [--artifacts .agent-os]
  → 运行;stdout = RunRecord JSON(见 3.3);产物落盘

agent-os trace <run_id> [--kind llm|tool|frame|all] [--format json|table]
  → 从 trace.jsonl 读信号时间线(coding agent 用 json)

agent-os inspect <run_id> [--frame <frame_id>] [--messages]
  → 从 checkpoint.json 读帧树;--frame 指定帧输出其完整上下文
    (messages 逐条:role/source/content/tool_calls)——"模型当时看到了什么"

agent-os resume <run_id>|<checkpoint.json> [--json]
  → 从 checkpoint 恢复运行(断电/改代码后续跑)

agent-os replay <run_id> [--json]
  → 用 trace 里的 LLM 请求/响应对构建 MockProvider 脚本,确定性重放
    该次运行(不碰真实 API);验证修复是否改变行为

agent-os diff <run_id_a> <run_id_b> [--kind signals|usage|result]
  → 两次运行的结构化 diff(信号序列、usage 增量、结果差异)

agent-os skills list|validate <path.yaml>
  → manifest lint(描述质量/权限引用/循环依赖),开发者自检
```

### 3.3 输出契约(coding agent 的消费面)

**stdout**:RunRecord JSON(`--json` 时唯一输出;无 `--json` 时人读摘要 + 末行 JSON):

```json
{
  "run_id": "r-...",
  "status": "done | failed | aborted",
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

- trace 的 `pre:llm.request`/`post:llm.response` 载荷含模型与消息摘要;replay 按**顺序匹配**构建 MockProvider 脚本(与 fib 测试同一机制),重放整棵帧树;
- 用途 A(回归):改了技能实现后 replay,`diff` 与原始 run 的信号序列;
- 用途 B(复现):线上失败的 run 拿回本地 replay,在 checkpoint 上 `inspect` 逐帧检查;
- 边界:replay 只能覆盖 LLM 调用面;工具副作用(fs/shell/docker)按真实环境执行,`--sandbox` 档可把 shell_exec/python_exec 强制切到 docker 后端。

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

- **RunManager**:单进程、单事件循环;run 作为 asyncio task 运行;状态只留内存 + 产物目录(`.agent-os/runs/`)——重启后历史 run 从产物目录重建索引(冷数据),在跑的 run 重启即丢(开发工具,可接受);
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
POST   /api/runs/{id}/resume         → 从 checkpoint 恢复
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

多用户与权限、持久化队列/分布式 worker、生产级部署形态、前端框架化(npm/构建链)、run 的定时调度与事件唤醒(§17 开放问题 4)、CLI 的交互式 TUI。
