# Tools:分发流水线与三层权限交集

> 章次:03 · 状态:核心已实现(分发流水线、三层权限交集、数据闸、路径沙箱均有实现与测试);预留与扩展部分实现(凭证注入、confirm 两阶段、MCP 适配器、流水线级归一化) · 依据:`agent_os/src/agent_os/tools/local_registry.py`、`agent_os/src/agent_os/api/v1/tools.py`、`agent_os/src/agent_os/kernel/runner.py`、`docs/DESIGN.md` §8、`docs/DATA-AUTHZ.md`

## 1. 概述

Tool 是 Agent OS 里唯一被允许触碰外部世界的原子能力:无调用栈、无 LLM 循环、单次进出(`docs/DESIGN.md` §2.2)。Tool Registry 承担工具的注册、校验、鉴权、分发与结果归一化,在 OS 隐喻中对应 **syscall 表 + seccomp + vfs**(§8 引言)。它不在内核里——内核只在 `_dispatch_call` 处持有分发仲裁点,真正的流水线由契约层之下、可替换的基线实现 `LocalPythonToolRegistry` 执行。每一次模型发起的工具调用,无论来自 LLM 的 `tool_calls`、沙箱内的 syscall 回调,还是编排代码的动态分发,最终都汇入同一条流水线、同一组闸门。

## 2. 动机与背景(原因)

**为什么不放进内核。** 微内核判据(F/P/I)要求内核只保留流控制、权限控制与 IPC。工具的"执行"显然不是仲裁点——它是可替换的功能面:本地函数、MCP 服务、远程 API 都该能挂进来。因此设计把注册表定为子系统而非内核部件:内核 `_dispatch_call`(`kernel/runner.py:547`)只做三件事——识别伪工具(`skill.*`/`python_orchestrate`/`ask_supervisor`)、过 manifest 白名单闸、收发 `pre/post:tool.call` 信号——然后把调用连同 `ToolDispatchContext` 一起交给注册表(`runner.py:584-594`)。这就是"契约先行,基线可换"公理在工具面的落点:`Tool` 是一个只有 `spec` 与 `async __call__(args, ctx)` 两个成员的 Protocol(`api/v1/tools.py:171-177`),任何满足它的实现都可替换基线。

**为什么是"函数即工具"。** 工具的作者是最懂工具的人,但要求他手写 JSON Schema、权限声明与超时策略,等于把每次修改的成本翻倍,而且 schema 与签名漂移是迟早的事。`LocalPythonToolRegistry` 因此选择 decorator 注册、从签名推导 schema(§8.4):类型注解映射 JSON 基本型,默认值决定 required,docstring 进 description。工具的文档、契约与实现从此只有一份源码。

**为什么权限是三层交集而不是单点开关。** 三层各自回答一个不同的问题,任何单层都不足以独立裁决:工具自报等级回答"这个操作本质上多危险"(作者最清楚);帧 manifest 白名单回答"这个技能的业务需要哪些能力"(技能作者的意图);RunConfig 上限回答"这次运行、这个宿主愿意容忍到什么程度"(部署方的底线)。三者取交集,任一拒绝即拒绝(§8.2)——技能作者不能靠漏写白名单获得能力,宿主也不能靠放宽上限让危险工具混进不信任的技能。

## 3. 问题陈述(解决的问题)

分发流水线要面对的不是抽象的"安全",而是一组具体、可复现的失败场景:

1. **模型生成不合契约的参数。** LLM 输出 `{"path": 123}` 这类类型错误是常态而非异常。若分发方"智能纠正"(悄悄转型、猜测意图),错误会被掩盖到更深层才爆,且不可复现。流水线的选择是 fail fast:不合 schema 直接返回 `INVALID_ARGS`,不执行(§8.1;"禁止智能纠正")。
2. **模型幻觉调用不在白名单的工具。** 技能 manifest 只列了 `system.file.read`,模型却尝试 `system.shell.exec`——无论出于幻觉还是注入诱导,都必须被结构性拒绝,而不是依赖 prompt 里的"请不要"。
3. **运行级底线被技能试探。** 宿主以 `ToolPolicy(max_permission=WRITE)` 启动 run(默认值,`api/v1/tools.py:45`),某技能声明了 EXEC 档工具并列入白名单——两层许可齐了,仍须被第三层(RunConfig 上限)拦下。
4. **路径逃逸。** 模型给 fs 工具传 `../../etc/passwd`,或给 `read_paths` 只读区发起写入——路径沙箱必须在工具执行前解析并拒绝。
5. **越权读取机密数据且错误消息本身泄密。** principal 的 clearance 不足以读 `confidential` 域时,拒绝消息若回显路径或域内内容,错误通道本身就成了泄露通道。
6. **工具超时、崩溃或被取消。** 异常若穿透分发边界进入 agent loop,帧状态机将收到非结构化的失败;取消(CancelledError)若被工具兜底吞掉,safe-point 语义失效。
7. **注入请求的 schema 顺序漂移。** 工具 schema 进入 prompt 静态前缀,顺序不稳定会击穿 KV cache(§7.4 不变量 5)——注册表必须保证顺序固定。

## 4. 设计与机制(解决的方法)

### 4.1 分发流水线:每次调用的完整路径

一次 `ToolCall{id, name, args}`(`api/v1/messages.py:35-40`)的完整路径如下(代码锚点见右):

```
LLM tool_calls / 沙箱 syscall / 编排分发
        │
        ▼
Kernel._dispatch_call                     kernel/runner.py:547
  ├─ skill.* / skill__*      → _invoke_skill(子技能压栈,本章不展开)
  ├─ python_orchestrate      → 编排沙箱(其内部调用回到本函数,runner.py:675-716)
  ├─ manifest 白名单闸       → 不在 permissions.tools 即 PERMISSION_DENIED   :554-562
  ├─ pre:tool.call(SYNC)    → Veto / Stop / Pause / Modify(可改参数)     :567-583
  ▼
LocalPythonToolRegistry.dispatch            tools/local_registry.py:181
  ① 注册查找        → 未注册:NOT_FOUND(retryable=False)                   :189-198
  ② schema 校验     → jsonschema.validate,fail fast:INVALID_ARGS          :200-210
  ③ 数据层 authZ    → DATA_ACCESS_DENIED(双闸第一闸,见 4.3)              :213, :306-351
  ④ 三层权限交集    → PERMISSION_DENIED(见 4.2)                            :219-238
  ⑤ 回放短路        → replayable 且有记录:按调用序弹记录值返回,不执行      :242-246
  ⑥ ToolContext 构造 → workdir/read_paths 解析、principal 随帧透传          :247-260
  ⑦ wait_for 超时   → TimeoutError:TIMEOUT(retryable=True)                :262-271
  ⑧ 异常兜底        → CancelledError 原样传播(:272-273);
                      其余 Exception → INTERNAL,hint 带栈尾 500 字符      :274-283
  ⑨ 归一化          → ToolResult 透传;裸返回值包 ok=True                  :284-286
  ▼
post:tool.call → _result_payload → 观察写回帧上下文   runner.py:595-598, :1217
```

两个顺序决策值得写明。**其一,schema 校验先于一切**:参数不合法时连数据闸、权限闸都不必走——它们的存在前提是"有一个可判定的调用"。**其二,数据闸在权限闸之前**(`local_registry.py:211-212` 注释):数据闸管"碰不碰得到"(机密性),权限闸管"允不允许"(副作用),两者独立失败,先查机密性可以让越权读取在更早、信息更少的点上终止。回放短路被刻意放在全部安全闸门**之后**(:239-241 注释):回放只替换"执行"这一步,不放宽任何闸门——崩溃恢复重跑不会变成权限旁路。

### 4.2 三层权限交集

判定逻辑集中在 `local_registry.py:219-238`,一行布尔表达式:

| 层 | 判据 | 来源 | 性质 |
|---|---|---|---|
| 工具自报等级 | `spec.permission ∈ {READ<WRITE<NET<EXEC}` | 工具作者声明(`api/v1/tools.py:32-38`) | 被比较方,非许可方 |
| 帧白名单 | `call.name ∈ frame_ctx.allowed_tools`(即 manifest `permissions.tools`) | 技能作者声明,内核经 runner 传入(`runner.py:588`) | WRITE 及以上需逐名列出 |
| RunConfig 上限 | `spec.permission ≤ tool_policy.max_permission` | 宿主部署方(缺省 WRITE,`tools.py:45`) | 全 run 统一底线 |

三个细节:

- **READ 档豁免帧白名单**(§W0-1,`local_registry.py:216-218`):只读无副作用,fs 边界已由 `resolve_work_path` 分区保证,强制列名只会让白名单膨胀失真。但注意豁免只作用注册表这一层——生产路径上 runner 的 manifest 闸(:554)对 READ 档同样逐名检查,豁免的实际受益者是直接使用 registry 的嵌入方(注释原话:"生产路径权限语义不变")。也就是说,manifest 白名单在生产路径上被**检查两次**,语义刻意保持冗余。
- **工具等级是 IntEnum**,`>` 比较即档级比较;越权拒绝的 `hint` 会列出本帧可用工具,把"拒绝"变成模型可自修复的观察(:232-236)。
- **第三层之外还有第四道软闸**:HumanApproval 类 sidecar 经 `pre:tool.call` 对高等级工具加人工闸门(§8.2);这不是注册表的职责,而是信号面的职责——注册表只保证被 Veto 的调用根本到不了执行。

### 4.3 数据层 authZ:与权限正交的第二道闸

`_check_data_access`(`local_registry.py:306-351`)实现 `docs/DATA-AUTHZ.md` §3.3 的强制点。工具经 `spec.data_domains`(如 `["fs.*"]`)声明自己会碰哪类数据域;分发时用 `resolve_work_path` 解析出目标路径,按**最长前缀优先**匹配宿主注册的域边界(`register_fs_domain`,:288-294),其后落到内置默认域 `fs.workdir`(public);`allow(principal, domain)` 按 clearance ≥ sensitivity 判定。

D1 的兼容策略是明确的设计取舍:**未配置 = 不拦截**。三种情况直接放行——工具未声明 `data_domains`、principal 为 None(v1 单用户语义)、目标落不进任何已配置域——行为与引入本系统前完全一致;默认拒绝只作用于已配置域(:309-316 注释)。拒绝消息只带域名、敏感度与 clearance,**不回显路径与域内内容**(:344-349),从结构上堵住"错误通道泄密"。需要指出:这与 `DATA-AUTHZ.md` §3.1/§3.3 的文案("未配置的域默认 confidential""域解析失败按 confidential 处理")不一致,D1 实现选择了向后兼容的宽松档,代码注释与测试(`test_unconfigured_path_degrades_to_sandbox`)均确认此语义——**以代码为准**。

### 4.4 路径沙箱:统一解析器

`resolve_work_path`(`local_registry.py:544-588`)是 fs 四工具与 shell 共用的三段判定:① 目标在任一 `read_path` 下 → 只读区,读允许(**可在 workdir 之外**),写拒绝;② 在 workdir 下 → 读写;③ 其他 → `INVALID_ARGS` 逃逸拒绝,hint 带运行期实际的合法区间。设计取舍在于"分区"而非"单一目录":技能常需要读宿主的项目目录但只写自己的产出目录,单分区要么放开写(危险)要么拷进 workdir(浪费且失真)。`RunConfig.workdir` 未配置时退回每 run 临时目录(`_workdir`,:364-370),`release_run` 在 run 收尾 `rmtree` 回收(:353-362)——缺省档的安全边界不静默放宽。

### 4.5 函数即工具与契约推导

`derive_spec`(`local_registry.py:608-634`)的推导规则:名为 `ctx` 的参数是 `ToolContext` 注入点、不进 schema(:617-618);`str/int/float/bool` 映射 JSON 基本型,`list[X]`/`dict` 映射 array/object,单支 Union(即 Optional)取内层,其余注解退化为 `{}`(不校验);无默认值的参数进 required。sync 函数包 `asyncio.to_thread`、async 直接 await(`_FunctionTool.__call__`,:74-82)。`ToolSpec` 契约逐字冻结(§14.1),含全部 v1 预留字段(`examples/cacheable/confirm/concurrency_safe/...`),新增字段一律 additive 带缺省(§W0-2 增列 `cost_hint/replayable/concurrent_safe`;升权增列 `side_effect`;数据层增列 `data_domains`)。`derive_side_effect`(`tools.py:134-136`)把副作用档缺省推导为 READ→none、WRITE/NET→reversible、EXEC→irreversible——这是升权系统推导档的数据源,工具面的声明因此直接喂给信任模型。

**与文档的一处出入**:`DESIGN.md` §8.4 写"docstring 首段 → description",而实现取**整段** docstring(:622-626 注释给出理由:"Use when / Do not use when" 与错误语义写在后续段落,模型据以选工具的正是后者;描述进静态前缀,长一点也不破 KV cache)。以代码为准。

### 4.6 内置工具面与别名迁移

`with_builtins`(:372-538)装配 17 件规范名工具(fs 七件、shell、net 两件、blob、time、todo 三件、skill.search),构造器另注册 `fetch_page`(:99-104,与 §6.1 闸门联动,原因见 `tools/std_web.py` 模块 docstring)。命名采用层级空间(`system.file.read`),旧扁平名(`fs_read`)经 `register_alias` 保留为别名,同一函数体多规名共存(:126-138)——分层命名迁移期不破坏存量技能。契约字段的声明有硬闸门:READ 档工具必须声明 `idempotent/cacheable/concurrent_safe`,`side_effect` 推导规则下 delete/kill 类必须显式标 `irreversible`(TIER-STANDARDS §1,:518-520 注释),`cost_hint` 只写量级不写绝对秒数。

## 5. 效果与验证(效果)

测试证据(本章直接相关部分,`agent_os/tests/`,本次运行实测 **283 passed + 32 xfailed**,3.11s):

| 测试文件 | 用例数 | 覆盖 |
|---|---|---|
| `tools/test_builtins.py` | 14 | 内置装配、`test_tool_policy_caps_permission`(:189,上限闸)、`test_fs_path_traversal_rejected`(:97,逃逸)、edit 唯一匹配、别名解析、shell 超时钳制无孤儿进程 |
| `tools/test_data_authz.py` | 13 | `test_dispatch_order_data_before_permission`(:132,闸门次序)、`test_configured_confidential_domain_denied_without_leak`(:150,拒绝不泄漏)、未配置降级(:194)、principal 跨帧不变量(:366)与 checkpoint 往返(:395) |
| `tools/test_std_foundation.py` | 9 | workdir 三分区、只读区拒写(:82)、逃逸 hint 可操作(:143)、READ 档契约字段齐备(:213) |
| `tools/test_std_tools.py` / `test_blob.py` / `test_builtin_side_effects.py` | 17 / 5 / 2 | std 工具行为、blob ref 形态、副作用档推导 |
| `test_contracts.py` | 7 | ToolSpec 等冻结面契约(§14.1) |
| `test_std_gate.py` | 参数化 | 工具门槛:description 必须写"何时用"、参数必须是 object schema、READ⇒cacheable、双拼写同步 |

32 例 xfail 集中在同一项:`test_parameters_are_documented`——`derive_spec` 从签名推导 schema,尚无逐参数 description 的机制(xfail 理由引实测:该项影响工具调用准确率 72%→90%)。这是被显式标记的已知缺口,不是静默失败。

真实配置示例:`instance/agent-os.toml` + `instance/skills.yaml` 是宿主侧装配形态;`agent_os/examples/workspace_janitor` 等示例技能的白名单直接消费本权限模型。**涟漪效应**:① 工具 schema 顺序固定(注册序,`schemas_for` :153-163)是 §7.4 前缀缓存不变量 5 的数据源;② `ToolErrorKind.retryable` 供 LoopDetector 与模型区分"该重试"与"该换策略"(`tools.py:53-62`);③ `side_effect` 推导是技能信任档递归取 max 的叶子值,工具声明质量直接决定升权判定的质量;④ 沙箱内 syscall 通道复用同一条 `_dispatch_call`(`runner.py:690-716`),编排代码无权限提升旁路;⑤ `specs()` 是 Web UI Tools 浏览器的数据源(:143-145)。

## 6. 局限性与边界(局限性)

1. **凭证注入未实现。** §8.1 流水线列有"凭证注入(按工具声明从凭证作用域取)",契约也预留 `ToolContext.credentials`,但分发固定填 `{}`(`local_registry.py:259`)。需要凭证的工具目前只能靠宿主环境变量绕行——恰是契约想禁止的做法。
2. **confirm 两阶段只声明、未强制。** `system.file.delete` 声明 `confirm=True`(:521-528),但 dispatch 没有任何 dry-run/confirmation token 逻辑;§8.2 描述的两阶段语义停在契约层,当前实际防线是白名单 + 人审 sidecar。
3. **并发与缓存声明不强制。** `cacheable/concurrent_safe/concurrency_safe` 全部"声明不强制"(`tools.py:91-93` 注释),`parallel_invoke` 依赖的并发安全判定尚无一处消费。错误声明当前无代价。
4. **流水线级结果归一化未落地。** §8.1 的"大小封顶 → spill 到 blob""调用计数注释(Tool call #N)"在 registry 中不存在;spill 是工具各自为之(`std_web.py` 的 fetch_page、`std.py:376-383` 的 fs_search),阈值与保留策略不统一,`"Tool call #"` 字样全仓仅出现在 DESIGN.md。`untrusted_source` 同理:契约字段在、http_fetch 声明在,统一的包裹标记未实现。
5. **READ 档白名单豁免语义窄。** 如 4.2 所述,生产路径上 runner 闸先查白名单,豁免只便利直接嵌入方;两层检查语义不同步(一层豁免、一层不豁免)是刻意冗余,但也是理解成本。
6. **数据层 authZ 仅 D1。** 只覆盖 fs 域;db/net 域声明与判定属 D2(:322-323);`agent-os.toml [data]` 配置段未接线,域边界只能由宿主代码调 `register_fs_domain` 注册;"未配置不拦截"与 DATA-AUTHZ 文档的"默认 confidential"目标态之间留有差距,多用户隔离由 run 边界承担。
7. **类型推导能力有限。** 多支 Union、嵌套泛型、字面量等注解退化为 `{}`——schema 校验对这类参数形同虚设,fail-fast 承诺只覆盖基本型。
8. **sync 工具的取消是假的。** `asyncio.to_thread` 无法中断线程,TIMEOUT 返回后底层函数可能继续运行;shell_exec 以超时钳制与子进程回收兜底(`test_shell_exec_timeout_clamped_to_spec_no_orphan`),但一般 sync 工具无此待遇。
9. **MCP 与持久 shell 未接入。** §8.3 的 MCP 适配器、供应链隔离仍是文档承诺;`shell_exec` 是一次性子进程,§8.3 描述的"run 作用域持久会话(cwd/env 跨调用保持)"明确标注为后续里程碑(`with_builtins` docstring,:380-381)——文档与实现口径不同,以代码为准。
10. **回放接线未完成。** `replayable` 的弹出机制已实现并有锚点测试,但记录源(host trace → `replay_records`)的接线"留后续里程碑"(:11-12,:239-240 注释);当前 replay 重放 LLM 侧,工具副作用仍真实发生。

## 7. 引用

- 设计文档:`docs/DESIGN.md` §2.2、§2.4、§7.4、§8(Tool Registry 全节)、§14.1;`docs/DATA-AUTHZ.md` §2-§3、§5.2;`docs/ESCALATION.md` §2.1;`docs/TIER-STANDARDS.md` §1
- 契约:`agent_os/src/agent_os/api/v1/tools.py`(Permission/ToolPolicy/ToolErrorKind/ToolSpec/ToolContext/ToolDispatchContext/Tool/BlobStore);`agent_os/src/agent_os/api/v1/messages.py:35-40`(ToolCall)
- 实现:`agent_os/src/agent_os/tools/local_registry.py`(dispatch :181-286;`_check_data_access` :306-351;`resolve_work_path` :544-588;`derive_spec` :608-634;`with_builtins` :372-538);`agent_os/src/agent_os/tools/blob.py`;`agent_os/src/agent_os/tools/builtins.py`;`agent_os/src/agent_os/tools/std.py`;`agent_os/src/agent_os/tools/std_web.py`
- 内核闸:`agent_os/src/agent_os/kernel/runner.py:547-598`(`_dispatch_call`)、:675-716(syscall 通道)
- 测试:`agent_os/tests/tools/test_builtins.py`、`agent_os/tests/tools/test_data_authz.py`、`agent_os/tests/tools/test_std_foundation.py`、`agent_os/tests/tools/test_std_tools.py`、`agent_os/tests/tools/test_blob.py`、`agent_os/tests/tools/test_builtin_side_effects.py`、`agent_os/tests/test_contracts.py`、`agent_os/tests/test_std_gate.py`
- 示例与配置:`agent_os/examples/workspace_janitor`、`instance/agent-os.toml`、`instance/skills.yaml`
