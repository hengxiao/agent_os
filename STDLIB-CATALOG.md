# std 实施清单(逐条设计)

> 与 [STDLIB.md](STDLIB.md) 的分工:那份是**政策**(为什么这样分类、什么不做);
> 本份是**施工图**(每条做什么、签名、决策、坑)。按实施波次组织,波内可并行。
> 形态判定见 STDLIB §1 六条规则;引擎既有机制见 §2(不重复造)。
>
> 每条格式:`名字` — 一句话(形态,权限档)/ 签名 / 为什么 / 决策 / 坑。
> 现状已有 8 个工具:`fs_read` `fs_write` `fs_edit` `shell_exec` `http_fetch`
> `blob_get` `python_exec` `python_orchestrate`(伪工具)。

---

## 第 0 波:地基改造(不新增能力,但后面全部依赖)

这一波一件新工具都没有,却是最该先做的——它们改的是**所有工具共用的地基**。
放到后面做等于把每条新工具都返工一遍。

#### W0-1 `workdir` 可配置 — 让 agent 能碰真实项目(机制)

`[run] workdir = "..."` + 分区语义;`ToolContext.workdir` 从"每 run 临时目录"改为可配置。

- **为什么**:实机发现的头号阻塞。现状 `_workdir(run_id)` 给每个 run 分配
  `tempfile.mkdtemp()`,fs/shell 工具全部锁死其中——**agent 读不到任何真实文件**。
  coding-agent、文件问答、代码审计三类主力场景直接落空。
- **决策**:三分区(书 Ch10 四区的精简版)——
  `read_paths: []`(只读挂载,如源码目录)/ `workspace`(可写产出)/
  `scratch`(每帧私有临时,默认现行为)。fs 工具按前缀判定档位:
  只读区拒绝 WRITE 档操作,越界仍是 `INVALID_ARGS`(现有路径逃逸检查保留)。
- **坑**:①默认必须保持现状(临时目录),否则所有现存配置的安全边界被静默放宽;
  ②`shell_exec` 的 `cwd` 与 fs 工具必须用同一个解析器,否则出现
  "脚本里 `ls` 和 `fs_read` 看到不同世界"的错位(实机已踩过一次);
  ③多帧并发写同一 workspace → 需要 W0-4 的乐观锁配套。

#### W0-2 工具契约四字段 — ToolSpec 增列(机制)

`ToolSpec` 加 `idempotent: "yes"|"no"|"key"` / `concurrent_safe: bool=False` /
`cacheable: bool`(READ 默认 True)/ `cost_hint: str`。

- **为什么**:编排把同一工具在一次执行里调几十次,并在 replay/崩溃恢复时整体重跑;
  没有幂等声明,重试就是赌博。缓存与并发标记则是白拿的性能(只读工具天然可缓存可并行)。
- **决策**:字段先**声明不强制**——v1 只落库并在 `/api/tools` 与 description 里
  暴露给模型;强制(如 ToolGuard 按 `idempotent=no` 要求 pre-check)留到有真实
  违规案例再加。避免一上来就把所有内置工具卡住。
- **坑**:`cost_hint` 别写成绝对秒数(依赖环境),写量级("~100ms"/"~5s,大文件更久")。

#### W0-3 工具错误第四层 `hint` — 让错误可自纠(机制)

`ToolError` 已有 `{kind, message, retryable, hint}`,但 `hint` 全空。逐个内置工具填。

- **为什么**:书里实测该层把错误恢复率从 60% 拉到 95%,是全清单性价比最高的单点。
  实机也验证过反向案例:编排脚本的 `BrokenPipeError` 无法自纠,而 SyntaxError
  带 traceback 一轮就改对。
- **决策**:hint 是**给模型的下一步动作建议**,不是错误描述的重复。
  模板:`FileNotFound → "检查工作目录(当前 X);或用 fs_list 确认路径"`;
  `PermissionDenied → "该工具不在本技能白名单;可用的有 A/B/C"`。
- **坑**:hint 里带的路径/白名单是运行期事实,必须现取,不能硬编码进静态描述。

#### W0-4 `fs_write`/`fs_edit` 的 `if_match` — 乐观锁(改造)

参数加 `if_match: str`(mtime 或内容 hash),不匹配则拒写并返回当前版本标识。

- **为什么**:有了编排脚本的并发与 spawn 并发,同一文件的 lost update 是必然事件,
  而乐观锁是几十年前就有的成熟答案,改动面只有一个可选参数。
- **决策**:默认不传 = 不检查(向后兼容);返回的冲突错误带 `hint: "重读后重试"`。
- **坑**:mtime 在粗粒度文件系统上分辨率不足(测试里已见过需要 `os.utime` 拨快 2 秒),
  正式实现用内容 hash 更稳。

#### W0-5 `shell_exec` 结构化返回 — 编排友好(改造)

返回值从拼接字符串改为 `{stdout, stderr, exit_code, truncated}`。

- **为什么**:实机教训。现状把 stdout/stderr/退出码拼成一个字符串给模型看,
  编排脚本只能 `listing["value"].split()` 硬解析——工具在"给模型看"和
  "给代码用"之间只选了前者。有了编排,后者同样是一等公民。
- **决策**:同时保留 `text` 字段(拼接版)供模型直读,新增结构化字段供脚本用。
  这是 STDLIB v2.1 §8 第 13 条"编排友好性"的首个落地。
- **坑**:改返回形状会动到现有测试与 examples,一次改干净;截断必须在
  `truncated` 里显式标记(不是静默截断)。

#### W0-6 `python_orchestrate` 的 stdout 回流 — 已实现,补文档

脚本 `print()` 已被捕获进 tool result 的 `stdout` 字段(实机验证)。

- **为什么记一笔**:这是编排脚本的**调试通道**——LLM 可以 print 中间量,
  出错时看得到,而这些内容不占用后续轮次的上下文(只在这一条 result 里)。
  应写进 `ORCHESTRATE_SCHEMA` 的 description,让模型知道可以这么用。

---

## 第 1 波:核心工具(P0,做完覆盖四类主力场景)

#### W1-1 `fs_list` — 目录列举(tool, READ)

`{path?, pattern?, max_entries?, cursor?}` → `{entries: [{path, size, mtime, is_dir}], total, next_cursor?}`

- **为什么**:探索文件系统是几乎所有文件任务的第一步。实机已确认缺失——
  编排脚本只能退而用 `shell_exec "ls"` 再解析字符串。
- **决策**:①**默认尊重 `.gitignore` + 跳过 `.git/`、`node_modules/`、`.venv/`**
  (`include_ignored: true` 可关)——不做这件事,任何真实仓库的第一次列举就淹没在
  依赖目录里;②`total` 与 `next_cursor` 是契约的一部分(书:静默截断是危险的);
  ③`mtime` 字段与 `now` 组合即可实现卡死检测,不必另造工具。
- **坑**:glob 递归 `**` 在大仓库上很慢,`max_entries` 必须有默认上限(建议 200)
  且截断时 `total` 如实报告。

#### W1-2 `fs_search` — 内容检索(tool, READ)

`{pattern, path?, glob?, context_lines?, max_hits?, cursor?}` → `{hits: [{path, line, text, before[], after[]}], total, next_cursor?, spill_ref?}`

- **为什么**:代码/文档任务的标配原语。书专门为它辩护:grep 跨平台语法不同,
  与其让模型即兴拼 shell,不如给一个结构化的专用工具(这是"通用优于专用"原则的
  明确豁免项)。
- **决策**:①**纯 Python 实现**(`re` + 目录遍历),不 shell out 到 ripgrep——
  std 边界是"无重型二进制依赖";②同样默认尊重 `.gitignore`;
  ③大结果自动 spill 到 blob 并返回 `spill_ref`,上下文只留前 N 条。
- **坑**:正则灾难性回溯要设超时;二进制文件要跳过(按 NUL 字节探测)。

#### W1-3 `now` — 服务端时钟(tool, READ)

`{tz?}` → `{iso, epoch, tz}`

- **为什么**:LLM 无时钟,这是真实且极便宜的缺口。书给了两个理由,第二个更硬:
  时间若由模型自报,一个假值就能绕过"退款窗口是否过期"这类守门校验——
  **它是安全边界,不只是便利**。
- **决策**:必须**可回放**——trace 记录返回值,replay 模式注入原值,否则任何
  含 `now` 的 run 都无法确定性复现。这引出一个通用机制(见坑)。
- **坑**:与其为 `now` 做特例,不如做成通用的 **`replayable` 工具标记**:
  工具声明 `replayable=True` → 内核把结果记进 trace → replay 按调用序返回记录值。
  未来 `web_search`、`http_fetch` 都能复用。建议连同 W1-3 一起做成机制。

#### W1-4 `todo_write` / `todo_update` — 任务规划(tool, WRITE·run 级状态)

`todo_write{items: [{id, text, status}]}` → `{count}`;
`todo_update{id, status, note?}` → `{item}`

- **为什么**:书里数据最硬的单点之一(带 TODO 15 轮 vs 不带 21 轮,且显著减少
  漏做子任务)。当前 std 连"任务规划"这个概念都没有。
- **决策**:①**两个工具而非一个**——`write` 用于(重新)规划,`update` 用于
  推进,后者极便宜且高频;②状态存 **run 级**而非文件系统(跨帧存活、随
  checkpoint 落盘);③清单摘要注入**状态栏**(引擎已有的 §7.3 机制),
  不占常规消息位。
- **坑**:状态栏是 ephemeral 的,TODO 必须是内核记账的一部分才不会漂移;
  别让 LLM 自己维护 TODO 文本(书的三条教训之一:状态必须由代码维护)。

#### W1-5 `skill_search` — 技能/工具发现(tool, READ)

`{query, kind?: "skill"|"tool"|"all", limit?}` → `{results: [{name, kind, description, permissions}]}`

- **为什么**:std 铺开后条目数会逼近书里"过百即选错"的门槛;渐进披露的前提是
  能按需查找。纯读、零依赖,现在就能做。
- **决策**:v1 用子串/关键词匹配即可;等 W2-9 `bm25_score` 落地后换成 BM25
  排序(自举:std 用自己的检索件)。
- **坑**:结果里必须带 `permissions`,否则模型选中一个自己无权调的技能,
  白白浪费一轮。

---

## 第 2 波:`std/transform` 纯函数包(code skill, sandbox 可执行)

全部零 LLM、零外部依赖、确定性可测——**风险最低、可完全并行开发**的一批,
适合作为 std 的第一个完整技能包落地并跑通全套质量门槛(§8)。

#### W2-1 `extract_json` — 从杂文本抠 JSON
`{text, mode?: "first"|"all"}` → `{data | items[]}`
- **为什么**:模型输出带 ```json 栅栏、前后寒暄、尾逗号是日常。每个调用方各写一遍正则是浪费。
- **决策**:容错顺序:直接 parse → 剥栅栏 → 括号配对扫描 → 修尾逗号。全失败才报错。
- **坑**:别做"智能修复"(补引号之类),错就是错,静默猜测比失败更危险。

#### W2-2 `template_render` — 安全模板渲染
`{template, data, strict?}` → `{text}`
- **为什么**:数据驱动的文本拼接不该让 LLM 干填空。
- **坑**:与 prompt 技能的 `str.format` 渲染共用一套"裸花括号"陷阱,错误信息要指明位置。

#### W2-3 `diff_text` — 结构化行级 diff
`{a, b, context?}` → `{hunks: [{op, line, text}], added, removed}`
- **为什么**:审阅/验证类技能的输入原语;也是"改动是否符合预期"的确定性判据。

#### W2-4 `word_count` / `token_estimate` — 计量
`{text}` → `{chars, words, lines}` / `{tokens}`
- **决策**:`token_estimate` 必须复用内核的 `TokenEstimator`(同一口径),不另立算法。

#### W2-5 `hash_digest` — 内容指纹
`{text|ref, algo?}` → `{hex, algo}`
- **为什么**:去重、缓存键、W0-4 乐观锁的 `if_match` 值来源。

#### W2-6 `csv_to_rows` / `rows_to_markdown` — 表格进出
- **决策**:只处理小表;大表明确引导到 `python_exec`(工具面最小化纪律)。

#### W2-7 `slugify` / `normalize_whitespace` — 字符串清洗
- **注意**:这类"规范化"函数是参数保真度(§8 第 7 条)的高危区——
  只能由调用方**显式**调用,绝不能被任何工具内部静默套用。

#### W2-8 `chunk_text` — 切分
`{text, size?, overlap?, mode?: "fixed"|"recursive"}` → `{chunks: [{text, start, end}]}`
- **决策**:默认 512 token + 15% overlap(书给的起点区间中位);`recursive` 模式
  按段落/标题边界优先切,避免把一句话劈两半。

#### W2-9 `bm25_score` — 稀疏检索打分
`{query, docs: [{id, text}], k?}` → `{ranked: [{id, score}]}`
- **为什么**:STDLIB §7 划线修正的核心——sparse 进 std、dense 出 std。
  纯 Python 可实现,零依赖,正好落在 std 边界内;没有它 `memory_search`
  与 `skill_search` 都没有默认实现。
- **决策**:**无状态打分器**,不做索引持久化(索引是调用方的事)。语料大时
  由调用方先用 `fs_search` 粗筛。

#### W2-10 `rrf_merge` — 多路检索融合
`{lists: [[id...]], k?}` → `{ranked: [id...]}`
- **为什么**:十行代码,却是混合检索的标准做法(只看排名、丢弃原始分数,
  从而不需要跨路归一化)。

#### W2-11 `retrieval_metrics` — 检索评测
`{predicted: [id...], relevant: [id...], k?}` → `{recall_at_k, mrr, ndcg}`
- **为什么**:没有它,检索链路的任何"改进"都无法验证。零 LLM。

#### W2-12 `injection_scan` — 注入模式扫描
`{text}` → `{suspicious: [{pattern, span}], score}`
- **决策**:正则层快筛,不做语义判断(那是 LLM 的事,留给调用方)。
  与 inline 的 `untrusted_content`(W3-2)配对使用:一个标记、一个检测。

#### W2-13 `redact_pii` — PII 脱敏
`{text, kinds?}` → `{text, redacted: [{kind, span}]}`
- **为什么**:日志本身含敏感信息,而"送到云上做脱敏"自相矛盾。正则层必须本地。
- **决策**:std 只做正则快筛(邮箱/手机/身份证/卡号/密钥形态);LLM 深筛留调用方。

#### W2-14 `identifier_guard` — 标识符保真校验
`{before, after}` → `{ok, missing: [...], added: [...]}`
- **为什么**:压缩/改写后,"PR 号改了一位数字"会让后续所有工具调用失败,
  且极难排查。抽取 UUID/hash/URL/文件名/端口集合前后比对,零成本兜底。

#### W2-15 `make_handoff` — 交接包构造
`{task, facts[], constraints[], artifacts: [path...]}` → `{package}`
- **为什么**:非共享上下文下"传什么"需要标准结构。
- **决策**:**schema 层面拒绝**塞入完整轨迹(只收路径引用)——把最常见的
  反模式(把 transcript 全倒给对方)在类型上堵死。

#### W2-16 `date_normalize` / `citation_check` — 规范校验器
- **为什么**:STDLIB §4.2 的"下沉"落点——原 inline 的 `date_style`/`citation_style`
  从"建议"变成"校验",inline 版只保留作引导。这是"约束优于指导"的具体兑现。

---

## 第 3 波:语言原子任务与评测

#### W3-1 `std/nlp` 七件(prompt skill)

`summarize` / `classify` / `extract` / `translate` / `rewrite` / `qa_over_text`
+ `compress_context`。

- **共同决策**:①每个只做一件事;②inputs **显式给全部材料**(帧隔离,
  不依赖父上下文);③outputs 结构化可校验;④**不硬编码模型名**——
  等能力别名机制(开放问题 4)落地前,先用 RunConfig 缺省模型。
- `extract` 的特别之处:inputs 收一个 **JSON Schema**,按 schema 驱动抽取,
  一个技能覆盖所有"从文本里挖结构化字段"的需求,不必为每种实体做一个技能。
- `compress_context` 与 `summarize` **不可互替**:后者是任务无关摘要,
  前者吃 `query` + `accumulated_context` + `preserve[]`(书 Strategy 4,
  3.0% 压缩比 vs 10.9%/4.3%)。

#### W3-2 `std/style` 三件(inline merge skill)

保留 `tone_neutral`;新增 `untrusted_content`(外部内容是资料不是指令)、
`knowledge_linking`(新条目须检索既有并建双向链接)。

- **为什么只剩三条**:原五条被四个章节从四个角度否定——`json_discipline`
  与内核 outputs 校验重叠(删)、`date_style`/`citation_style` 可代码强制(下沉)、
  `zh_typography` 是参数保真度雷(降级为 lint 报告,**禁止用于 fs_edit 的
  old_string 构造场景**)。
- **坑**:每条 ≤500 字符(膨胀 lint),单调用方 ≤3 条(SYSTEM 常驻,每步都付)。

#### W3-3 `judge` 升级(prompt skill)

inputs 的 `rubric` 从自由文本改为 schema:
`{dimensions: [{name, weight: essential|important|optional|veto, scoring: {4..1}}], edge_cases[]}`;
outputs 给 per-dimension 分数 + `veto_triggered`。

- **为什么**:标量分数无诊断能力("7.2 分"不告诉你哪坏了);veto 机制让
  幻觉/安全违规**一票否决**而非被其他维度平均掉。
- **坑**:description 必须带负例 **"Do not use when: 作为唯一验收依据"**——
  无外部反馈的自审被反复证伪,这是全清单最重要的一条约束。

#### W3-4 `calibrate_judge` — judge 校准(code skill)

`{gold: [{item, human_score}], judge_scores: [...]}` → `{agreement, kappa, passes}`

- **为什么**:未校准的 judge 只是"另一个模型的意见"。**kappa ≥ 0.7 是 judge
  进 std 的门禁**,judge 模型或 rubric 变更后须重校准。
- **决策**:零 LLM 的纯计算——这是"能不用 LLM 就不用"的漂亮案例:
  质量体系的元层本身是确定性的。

#### W3-5 `pairwise_compare` — 成对比较(prompt + code)

- **决策**:**内建交换顺序两次评**,不一致判平——位置偏见不是可选的后处理,
  是这个技能的定义的一部分。

---

## 第 4 波:领域包

#### W4-1 `std/files`

`progress_track`(JSON 进度文件 + `passes` 语义 + resume 摘要)、
`run_tests`(EXEC + 结构化解析)、`read_file_smart`(L0/L1/L2 结构摘要;
行号/分页已是工具契约,不在此)、`apply_patch`、`summarize_tree`。

- `progress_track` 优先:书的第 8/9/10 三章从不同场景独立要求了同一个原语,
  一份实现三处受益;配 `fs_list` 的 mtime + `now` 即可白拿卡死检测。
- `run_tests` 是 `retry_until` 的**默认验证器**——它比 judge 重要得多。

#### W4-2 `std/memory` 文件版(不等 M6)

`memory_extract`(会话→候选事实)/ `memory_reconcile`(候选 × 既有 →
ADD/UPDATE/DELETE/NOOP)/ `memory_consolidate`(周期重构)/ `memory_check`(code,约束交叉校验)。

- **为什么现在能做**:底层就是 `fs_*` + Markdown 约定 + `bm25_score`,零新依赖。
  Markdown 记忆相对向量库有三个优势:人可读可改、天然保序、可 Git 回滚。
- **坑**:裸 `memory_write` 必然产生"两条矛盾记录并存"——`reconcile` 的四决策
  不是可选项,是这个包成立的前提。

#### W4-3 `std/web`

`fetch_page`(http_fetch + 正文抽取 + 超长 spill;**outputs 强制 `source` 字段,
正文以 `<external_content source=...>` 包裹**)、`research_one`、
`research_iterative`(检索 → 判充分 → 精化 → 再检索)。

- **坑**:`citation_style` 管的是**输出侧**格式,不能替代**输入侧**隔离——
  每个感知工具都是注入入口,包裹标记是零成本的第一道防线。

#### W4-4 `std/learn`

`distill_experience` / `reflect_on_failure` / `verify_before_store`。

- `verify_before_store` 是**入库闸门**:重置环境 + 重放 + 验收判定。
  书的论断很硬:没有验证门,自我改进循环必然腐烂。
- **与编排的连接**(开放问题 9):跑通的一次性编排脚本 + `verify_before_store`
  → `learned/` code skill,这是自进化闭环的最短路径。

---

## 第 5 波:依赖引擎缺口(先立项内核,再做 std)

| 条目 | 阻塞在 |
|---|---|
| `ask_human`(特殊档) | 需要 tool 层的"挂起 run"语义:工具请求 → checkpoint → run 转 paused → 宿主呈现问题 → resume 时把答案作为该 tool result 注入。**全清单工程量最大的一条**,但它是 Constrain 层唯一的 std 落点 |
| `set_timer` | 与 `ask_human` **共用同一条 checkpoint/resume 通道**,应合并立项摊薄成本 |
| `subagent_cancel` / `subagent_status` | 引擎缺口 1:子树级联取消(现只有 run 级 stop) |
| `race_first` 组合子 | 同上 |
| 组合子统一 `budget` 参数 | 引擎缺口 3:按子树切分记账 |
| `describe_image` + 多模态最低限 | 契约层:`blob_get` 带 mime + prompt 技能 inputs 支持 `image_ref` |
| `memory_*` 服务版 | M6 MemoryService |

---

## 依赖与建议顺序

```
W0 地基(workdir / 契约四字段 / 错误 hint / if_match / shell 结构化)
        │
        ├─→ W1 核心工具(fs_list · fs_search · now+replayable · todo · skill_search)
        │            │
        │            └─→ W4-1 std/files(progress_track · run_tests)
        │
        ├─→ W2 transform 纯函数包(可完全并行;bm25_score 反哺 skill_search)
        │            │
        │            └─→ W4-2 std/memory 文件版
        │
        └─→ W3 nlp / style / eval(judge 可信化后,retry_until 才有意义)
                     │
                     └─→ W4-3 std/web · W4-4 std/learn

W5 全部阻塞在内核立项(挂起语义 / 级联取消 / 子树记账 / 多模态契约)
```

**若只做三件**:W0-1(workdir)、W1-1+W1-2(fs_list/fs_search)、W0-3(错误 hint)。
第一件解锁"能碰真实项目",第二件解锁"能找到东西",第三件让失败可自纠——
这三件之后,配合已落地的编排桥,文件问答与代码审计类场景就跑得起来了。

**首个完整交付建议 W2**(transform 包):零依赖、零风险、可并行,且能作为
std 的样板跑通全套质量门槛(§8 的 13 条),把收录流程本身先验证一遍。

---

## 测试策略:四层阶梯,裁判在最后一层且永不进 CI

目录里约七成条目**不需要 LLM 裁判**。更关键的是:很多"看起来要裁判"的性质,
应该先做成**技能内的运行期校验器**——那样坏输出在生产里就被拦住,测试也退化为
"校验器有没有触发"(确定性、免费)。这是"约束优于指导"用在技能内部。

### 层 1:确定性断言 —— tool + code skill

W0/W1/W2 三波,加 `calibrate_judge`、`memory_check`、`identifier_guard`——
精确输出比对,毫秒级,零成本。目录里约 26 条落在这层,**它们不需要任何 LLM**。

分页类工具(`fs_list`/`fs_search`)有三条必测不变量:`total ≥ len(entries)`、
cursor 走完覆盖全集且**不重不漏**、截断时 `truncated`/`total` 显式可见
(静默截断是被点名的危险)。

### 层 2:输出不变量 —— prompt skill 的代码可判性质

prompt 技能输出自然语言,但大多带有代码可判的性质。**先做成技能内校验器,
再做成测试断言**:

| 技能 | 不变量(可代码判定) |
|---|---|
| `qa_over_text` | **quotes 必须是 context 的逐字子串**——幻觉最主要形态(编造引文)被一个 `in` 抓住 |
| `extract` | 合调用方给的 schema(硬);**抽取值逐字出现在原文** |
| `classify` | `label ∈ labels`(硬);`confidence ∈ [0,1]` |
| `compress_context` | **token 估算严格下降**(DESIGN §7.4 不变量 3);`preserve[]` 全部在场 |
| `summarize` | 长度 ≤ `max_words`;`identifier_guard` 通过;输出 ≠ 输入(没偷懒复制) |
| `translate` / `rewrite` | `identifier_guard`(数字/专名/URL 保真);长度比在带内 |
| `judge` | veto 维度 fail ⇒ 总分归零;per-dimension 齐全 |
| `contextualize_chunk` | 输出含原 chunk 全文(前缀化不改原文) |
| `memory_reconcile` | 决策 ∈ {ADD,UPDATE,DELETE,NOOP};UPDATE 必带目标 id |
| `make_handoff` | schema 层拒绝 transcript;artifacts 全是路径 |

**同一条断言在两处跑、含义不同**:CI 里(mock brain)测的是 harness 没破坏
不变量;离线里(真模型)测的是模型是否遵守。写一次,两处用。

### 层 3:mock brain golden fixtures —— 测 harness 不测模型

现有 `MockProvider` + 脚本化 brain 的模式。覆盖 prompt 渲染、schema 强制、
错误折叠、重试逻辑。**这是 CI 的主力**。

### 层 4:离线质量评测 —— 唯一需要裁判的地方

真模型 + 裁判/人工;≥50 例、跑 3 次报离散度、报 Pass^3(3 例的噪声带宽约
±29pp,什么结论都下不了)。**nightly 或发版前跑,不进 CI。**

### 递归陷阱与终止条件

用 `judge` 测 `summarize`,谁测 `judge`?递归必须在**人工金标准**终止:
`calibrate_judge` 拿 100–200 条人工标注算 Cohen's kappa,≥0.7 才准用;
judge 模型或 rubric 变更后重新校准。这也是 `calibrate_judge` 被设计成零 LLM
纯计算的原因——**质量体系的元层本身必须是确定性的**。

裁判**不得与被评对象同族**(同族偏见同向叠加,且失效是静默的:agent 会逐渐
学会避开裁判的盲区,分数看着一直很好)。依赖能力别名的 `family` 轴
(STDLIB 开放问题 4);未落地前靠人工配置保证。

### 真的需要裁判的三种情况(及成本账)

排序选优(`pairwise_compare`,内建交换顺序两评)、开放式质量("改写是否保持
原意")、rubric 化多维评估(`judge` 自身用例)。

代价要算清:每技能 100–200 条人工标注 × 10 个 prompt 技能 = 1000–2000 条。
**务实建议:只给 `summarize`/`extract`/`judge` 三个核心技能建金标准集**,
其余靠层 2 不变量 + 层 3 冒烟守住,退化能抓到八成。

### 元测试:一条测试守住整个 §8 门槛

比"记得给每个工具写测试"可靠得多的做法是**遍历 registry 做契约体检**:
迭代所有注册工具与 std 技能,断言 §8 的可机检条款(description 含
Use when/Do not use when、参数 schema 完整、READ ⇒ cacheable+concurrent_safe、
WRITE/EXEC/NET 显式声明 idempotent、`cost_hint` 非空)。新增条目漏任何一条自动失败。
落点:`tests/test_std_gate.py`。运行期的错误 hint 检查仍留在各工具自己的测试里
(需要构造失败调用,不适合泛化扫描)。

### 白拿的一件事:用 replay 当 fixture

项目已有 `agent-os replay`(从 trace + checkpoint 重建 MockProvider 脚本)。
这意味着**离线质量评测跑一次真模型后,它的 trace 就是现成的 CI fixture**——
不必另建 cassette 机制。真实模型的输出形态进了 CI,而 CI 仍然零 API key、
零成本、确定性。这是层 3 与层 4 之间的桥,现成能力。

### CI 与离线的分界(不可破的线)

现有套件的性质很珍贵:**~290 个测试 10 秒跑完、无需 API key、零 flaky**。
层 1–3 全进 CI;层 4 走 nightly/发版前,需要 key、花钱、报置信区间。
对抗用例(§7.3a 的"诱导攻击")对 `injection_scan`/`untrusted_content`
是确定性的——攒注入语料库断言检测是否触发,不需要裁判,进 CI。

---

## 本轮明确不做(与 STDLIB §7 一致,此处只记新增判断)

- `map_over` / `pipeline` 组合子:编排脚本 3 行即是全部语义,降级为文档示例;
- `read_document`(PDF/Word 抽取):二进制依赖,走 entry point;若要收,
  必须是"统一工具 + file_type 参数"而非一类一个;
- 随机数/UUID 工具:复现性毒药,需要时经 `RunConfig.seed` 管控的内核原语另议;
- `fs_delete`:`shell_exec` + ToolGuard 规则已覆盖,单独给一个高危工具名不划算。
