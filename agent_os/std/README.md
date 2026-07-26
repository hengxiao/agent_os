# std — 标准技能包

STDLIB 第 2 波(STDLIB-CATALOG §W2;STDLIB §4.1):20 个 code 技能,全部零 LLM、
零外部依赖(仅标准库)、确定性,permissions 全空——纯转换,不触网、不读状态
(`hash_digest` 的 `ref` 形参除外,它按调用方显式给的路径读文件取指纹)。

内容:`extract_json` / `template_render` / `diff_text` / `word_count` /
`token_estimate` / `hash_digest` / `csv_to_rows` / `rows_to_markdown` / `slugify` /
`normalize_whitespace` / `chunk_text` / `bm25_score` / `rrf_merge` /
`retrieval_metrics` / `injection_scan` / `redact_pii` / `identifier_guard` /
`make_handoff` / `date_normalize` / `citation_check`。

STDLIB 第 3 波(STDLIB-CATALOG §W3;STDLIB §4.2-§4.4),同一 `skills.yaml` 追加:

- `std/nlp` 七件(prompt):`summarize` / `classify` / `extract`(JSON Schema
  驱动)/ `translate` / `rewrite` / `qa_over_text` / `compress_context`
  (任务感知,与 `summarize` 不可互替)。一律省略 model 段(回落 RunConfig
  缺省模型)、permissions 全空、prompt 无裸 `{}`;
- `std/style` 三件(inline merge):`tone_neutral` / `untrusted_content`
  (`<external_content>` 标记约定,与 `injection_scan` 配对)/ `knowledge_linking`;
- `std/eval`:`judge`(prompt,rubric schema 化,veto 一票否决)、
  `calibrate_judge`(code,Cohen's kappa ≥ 0.7 门禁)、`pairwise_compare`
  (code 编排,内建交换顺序两评,不一致判平;私有依赖 `pairwise_judge_once`)。

STDLIB 第 4 波(STDLIB-CATALOG §W4-1),同一 `skills.yaml` 追加:

- `std/files` 五件(code,TRUSTED 档):`progress_track`(JSON 进度文件,
  init/update/summary,确定性不读钟)/ `run_tests`(EXEC 级,workdir 内跑
  pytest 并结构化解析 `{passed, failed, errors, failures, ok}`)/
  `read_file_smart`(L0 概要 / L1 结构 / L2 带行号分页)/ `apply_patch`
  (unified diff,上下文不匹配整组拒绝)/ `summarize_tree`(目录树摘要,
  与 fs_list 同一棵遍历器)。permissions 全空:KernelBuilder §6.1 闸门拒绝
  "声明了未注册工具"的 manifest,而既有装配用空工具表加载本文件;路径安全
  由内核统一解析器 resolve_work_path 保证(与 fs 工具同一边界)。

STDLIB 第 4 波续(STDLIB-CATALOG §W4-2~W4-4;STDLIB §4.6/§4.7),同一
`skills.yaml` 追加:

- `std/web`:`fetch_page`(code,同名工具的 run() 薄适配——工具面在
  `LocalPythonToolRegistry` **构造器**注册:三个 web 技能 permissions.tools
  都声明 `[fetch_page]`,而既有锚点用空工具表加载本文件,§6.1 闸门要求声明
  对每个注册表可见;http_fetch 依赖在调用时按名查找,可同名覆盖 mock)/
  `research_one`(prompt,单轮检索调研)/ `research_iterative`(code,
  经 `ctx.chat` 自驾驶"检索→自评→精化→再检索"协议——帧循环把无
  tool_calls 的响应判为终答,自评回合走不通,形态取舍详见
  `web_handlers.py` 模块 docstring);
- `std/memory` 文件版四件:`memory_extract` / `memory_reconcile`(prompt
  管线对;矛盾项必须 DELETE 不得并存,四决策是这个包成立的前提)+
  `memory_consolidate`(code,评分剪枝 + 近重复去重,`today` 可注入)/
  `memory_check`(code,"主语+属性"同键不同值即冲突,纯规则);
- `std/learn` 三件:`distill_experience` / `reflect_on_failure`(prompt)+
  `verify_before_store`(code 入库闸门:结构归一后深比较,bool ≠ 1)。
  handler 模块:`web_handlers` / `memory_handlers` / `learn_handlers`。

## 装配

handler 为 `<模块>:<fn>` dotted path(W2 是 `transform`,W3 eval 是
`eval_handlers`,W4-1 是 `files_handlers`;均在本目录),装配方需把本目录放上 `sys.path`
(LocalFileSkillRegistry 不做注入):测试侧由仓库根 `conftest.py`
钉入;宿主/CLI 侧把本目录作为一个 skill set 经 `--skillsets <其父目录>` 加载
(`load_skillsets` 约定:`<root>/<set>/skills.yaml`,set 目录在装配时自动注入
`sys.path`,见 runtime/config.py 与 host/web/run_manager.py)。

```bash
.venv/bin/pytest -q tests/skills/test_std_transform.py   # W2 锚点
.venv/bin/pytest -q tests/skills/test_std_nlp.py         # W3 锚点
.venv/bin/pytest -q tests/skills/test_std_files.py       # W4-1 锚点
.venv/bin/pytest -q tests/skills/test_std_domain.py      # W4-2~4 锚点
```
