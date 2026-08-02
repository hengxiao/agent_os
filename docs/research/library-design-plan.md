# Common & System Library Design Plan for agent_os

## 1. Goal

This plan designs two layered standard libraries for `agent_os`:

- **`system.*`** — runtime/kernel primitives that the agent framework itself
  provides and that require elevated trust or OS interaction.
- **`common.*`** — portable, reusable, policy-free capabilities that agent
  skills can compose like a standard library.

The design is grounded in the naming convention in `../NAMING.md` and informed by
how Python, Go, Rust, Java, Node.js, and .NET partition their system and
standard libraries (see `research/stdlib-research.md`).

## 2. Design principles

1. **Kernel stays small**. Only privileged or OS-adjacent primitives live in
   `system.*`. Everything else is a `common.*` skill or an external skill pack.
2. **Group by domain**. Use second-level namespaces (`system.file`,
   `common.text`, `common.memory`) rather than flat lists.
3. **Function-shape first**. Each skill/tool does one thing, has explicit
   `inputs`/`outputs` schemas, and a clear `Use when / Do not use when` routing
   description.
4. **Composable**. `common.*` skills call `system.*` tools and other `common.*`
   skills; they do not call the OS directly.
5. **Deterministic where possible**. Code skills are preferred for pure
   computation; prompt skills are used only when language understanding is
   required.
6. **No duplication**. Do not recreate a capability already available in
   `system.*`; wrap or route to it instead.
7. **Backward compatibility**. The current flat names continue to work as
   aliases during the migration.

## 3. Current state

The existing tools and skills already map cleanly to the new hierarchy.

### Existing tools (after naming migration)

| Current flat name | New `system.*` name | Notes |
|-------------------|---------------------|-------|
| `fs_read` | `system.file.read` | read file with pagination |
| `fs_write` | `system.file.write` | write/overwrite file |
| `fs_edit` | `system.file.edit` | localized edit |
| `fs_list` | `system.file.list` | directory listing |
| `fs_search` | `system.file.search` | regex content search |
| `shell_exec` | `system.shell.exec` | run shell command |
| `python_exec` | `system.python.exec` | sandboxed Python execution |
| `http_fetch` | `system.net.http_fetch` | raw HTTP fetch |
| `fetch_page` | `common.web.fetch_page` | fetch + HTML text extraction |
| `now` | `system.time.now` | server clock (replayable) |
| `todo_write` | `system.task.todo_write` | run-level todo list |
| `todo_update` | `system.task.todo_update` | update todo item |
| `skill_search` | `system.skill.search` | search registry |
| `ask_user` | `system.ui.ask_user` | user prompt |
| `notify_user` | `system.ui.notify_user` | user notification |
| `blob_get` | `system.blob.get` | blob read (future) |

### Existing standard-library skills (after migration)

| Current flat name | New `common.*` name | Kind |
|-------------------|---------------------|------|
| `extract_json` | `common.text.extract_json` | code |
| `template_render` | `common.text.template_render` | code |
| `diff_text` | `common.text.diff` | code |
| `word_count` | `common.text.word_count` | code |
| `token_estimate` | `common.text.token_estimate` | code |
| `chunk_text` | `common.text.chunk` | code |
| `bm25_score` | `common.retrieval.bm25_score` | code |
| `rrf_merge` | `common.retrieval.rrf_merge` | code |
| `retrieval_metrics` | `common.retrieval.retrieval_metrics` | code |
| `hash_digest` | `common.hash.digest` | code |
| `csv_to_rows` | `common.table.csv_to_rows` | code |
| `rows_to_markdown` | `common.table.rows_to_markdown` | code |
| `slugify` | `common.text.slugify` | code |
| `normalize_whitespace` | `common.text.normalize_whitespace` | code |
| `injection_scan` | `common.security.injection_scan` | code |
| `redact_pii` | `common.security.redact_pii` | code |
| `identifier_guard` | `common.text.identifier_guard` | code |
| `make_handoff` | `common.task.make_handoff` | code |
| `date_normalize` | `common.text.date_normalize` | code |
| `citation_check` | `common.text.citation_check` | code |
| `summarize` | `common.text.summarize` | prompt |
| `classify` | `common.text.classify` | prompt |
| `extract` | `common.text.extract` | prompt |
| `translate` | `common.text.translate` | prompt |
| `rewrite` | `common.text.rewrite` | prompt |
| `qa_over_text` | `common.text.qa_over_text` | prompt |
| `compress_context` | `common.text.compress_context` | prompt |
| `judge` | `common.eval.judge` | prompt |
| `calibrate_judge` | `common.eval.calibrate_judge` | code |
| `pairwise_compare` | `common.eval.pairwise_compare` | code |
| `pairwise_judge_once` | `common.eval.pairwise_judge_once` | prompt |
| `run_tests` | `common.dev.run_tests` | code |
| `apply_patch` | `common.dev.apply_patch` | code |
| `read_file_smart` | `common.dev.read_file_smart` | code |
| `summarize_tree` | `common.dev.summarize_tree` | code |
| `memory_extract` | `common.memory.extract` | prompt |
| `memory_reconcile` | `common.memory.reconcile` | prompt |
| `memory_check` | `common.memory.check` | prompt |
| `verify_before_store` | `common.memory.verify` | prompt |
| `research_one` | `common.research.one` | prompt |
| `research_iterative` | `common.research.iterative` | prompt |
| `fetch_page` (skill) | `common.web.fetch_page` | prompt |
| `tone_neutral` | `common.style.tone_neutral` | inline merge |
| `untrusted_content` | `common.security.untrusted_content` | inline merge |
| `knowledge_linking` | `common.memory.knowledge_linking` | inline merge |

## 4. Proposed `system.*` library

### 4.1 Layer rationale

`system.*` is the kernel tool surface. These tools are registered directly with
`LocalPythonToolRegistry` and are the only things that may read from/write to
the host file system, spawn processes, open network sockets, or talk to the
user.

### 4.2 `system.file.*`

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.file.read` | `path`, `offset`, `limit` | file content or `ToolResult` | READ | already exists |
| `system.file.write` | `path`, `content`, `if_match` | confirmation or `ToolResult` | WRITE | already exists |
| `system.file.edit` | `path`, `old_string`, `new_string`, `if_match` | confirmation or `ToolResult` | WRITE | already exists |
| `system.file.list` | `path`, `pattern`, `max_entries`, `cursor` | entries, total, next_cursor | READ | already exists |
| `system.file.search` | `pattern`, `path`, `glob`, `max_hits`, `cursor` | hits, total, spill_ref | READ | already exists |
| `system.file.stat` | `path` | size, mtime, is_dir, exists | READ | implemented (Phase 3); probe semantics — missing path returns `exists: false` |
| `system.file.delete` | `path`, `if_match` | confirmation | WRITE | implemented (Phase 3); `confirm=True`, files + empty dirs only |
| `system.file.mkdir` | `path` | created | WRITE | implemented (Phase 3); parents/exist_ok, idempotent |

### 4.3 `system.shell.*`

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.shell.exec` | `command`, `timeout` | stdout, stderr, exit_code | EXEC | already exists |

Future:

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.shell.spawn` | `command`, `env` | process handle | EXEC | persistent session; future milestone |
| `system.shell.kill` | `handle` | confirmation | EXEC | future milestone |

### 4.4 `system.net.*`

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.net.http_fetch` | `url`, `max_bytes` | status, content, truncated | NET | already exists |
| `system.net.http_request` | `method`, `url`, `headers`, `body`, `max_bytes` | status, content, truncated | NET | implemented (Phase 3); POST/PUT/PATCH/DELETE/HEAD/OPTIONS; shares its execution body with `http_fetch` |
| `system.net.dns_resolve` | `host` | IPs | NET | **new**; future milestone |

### 4.5 `system.time.*`

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.time.now` | `tz` | iso, epoch, tz | READ | already exists; replayable |
| `system.time.sleep` | `seconds` | confirmation | READ | **new**; future milestone |

### 4.6 `system.task.*`

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.task.todo_write` | `items` | count | WRITE | already exists; run-level state |
| `system.task.todo_update` | `id`, `status`, `note` | item | WRITE | already exists; run-level state |
| `system.task.todo_read` | `status` (optional) | todos, count | READ | already exists; explicit read of full run-level todo list |

Rationale: the context manager already injects a todo status block into every frame.
The block includes the overall progress, the current `doing` task, and up to two
previous and next tasks, so the model knows where it is in the queue. Adding
`system.task.todo_read` makes the surface symmetric and lets skills retrieve the full list
when the injected summary is not enough.

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.skill.search` | `query`, `kind`, `limit` | results | READ | already exists |
| `system.skill.reload` | `path` | ok | WRITE | **new**; future milestone |

### 4.8 `system.ui.*`

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.ui.ask_user` | `question` | answer | UI | stub; host callback |
| `system.ui.notify_user` | `message` | none | UI | stub; host callback |
| `system.ui.show_progress` | `percent`, `message` | none | UI | **new**; future milestone |

### 4.9 `system.blob.*`

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.blob.get` | `ref`, `offset`, `limit` | content | READ | stub in builtins.py |
| `system.blob.put` | `data`, `run_id` | ref | WRITE | currently internal on `ctx.blob` |
| `system.blob.list` | `run_id` | refs | READ | **new**; future milestone |

### 4.10 `system.python.*`

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.python.exec` | `code` | stdout, stderr, result | EXEC | already exists; sandboxed |

### 4.11 `system.security.*` (future)

| Name | Inputs | Outputs | Permission | Notes |
|------|--------|---------|------------|-------|
| `system.security.scan_code` | `source`, `language` | verdict | EXEC | future CodeScanner surface |

## 5. Proposed `common.*` library

### 5.1 Layer rationale

`common.*` skills are implemented in `agent_os/std/skills.yaml` (or future
sub-pack files) and run inside the normal skill frame. They can call `system.*`
tools and other `common.*` skills, but they cannot directly access the OS.

### 5.2 `common.text.*` — text manipulation and extraction

These are pure or deterministic text operations. They are the most heavily used
skills in agent workflows.

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.text.extract_json` | code | `text`, `mode` | `data` or `items` | extract JSON from model output |
| `common.text.template_render` | code | `template`, `data`, `strict` | `text` | str.format-style rendering |
| `common.text.diff` | code | `a`, `b`, `context` | hunks, added, removed | line-level diff |
| `common.text.word_count` | code | `text` | chars, words, lines | basic text metrics |
| `common.text.token_estimate` | code | `text` | tokens | budget-aware token count |
| `common.text.chunk` | code | `text`, `size`, `overlap`, `mode` | chunks | fixed/recursive text chunking |
| `common.hash.digest` | code | `text`, `ref`, `algo` | hex, algo | SHA and friends |
| `common.table.csv_to_rows` | code | `text` | rows | CSV parsing |
| `common.table.rows_to_markdown` | code | `rows` | text | Markdown table rendering |
| `common.text.slugify` | code | `text` | text | ASCII slug generation |
| `common.text.normalize_whitespace` | code | `text` | text | whitespace folding |
| `common.text.identifier_guard` | code | `before`, `after` | ok, missing, added | identifier fidelity check |
| `common.text.date_normalize` | code | `text` | text, dates | Chinese/MM-DD-YYYY to ISO |
| `common.text.citation_check` | code | `text`, `sources` | ok, missing, uncited | citation alignment |
| `common.text.summarize` | prompt | `text` | summary | task-independent summary |
| `common.text.classify` | prompt | `text`, `categories` | category, confidence | single-label classification |
| `common.text.extract` | prompt | `text`, `schema` | object | schema-driven extraction |
| `common.text.translate` | prompt | `text`, `target_lang` | translation | translation |
| `common.text.rewrite` | prompt | `text` | rewritten | fact-preserving rewrite |
| `common.text.qa_over_text` | prompt | `text`, `question` | answer | grounded QA |
| `common.text.compress_context` | prompt | `text`, `query`, `preserve` | compressed, dropped_estimate | task-aware compression |

### 5.3 `common.web.*` — web and network utilities

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.web.fetch_page` | code/tool | `url`, `max_chars` | source, content, truncated | fetch + HTML text extraction |
| `common.web.search` | prompt/skill | `query`, `limit` | results | web search abstraction (provider-agnostic) |
| `common.web.parse_html` | code | `html` | text, links, title | HTML extraction |
| `common.web.extract_links` | code | `html`, `base_url` | links | link extraction |

### 5.4 `common.retrieval.*` — retrieval and ranking algorithms

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.retrieval.bm25_score` | code | `query`, `docs`, `k` | ranked | sparse retrieval scoring |
| `common.retrieval.rrf_merge` | code | `lists`, `k` | ranked | reciprocal rank fusion |
| `common.retrieval.retrieval_metrics` | code | `predicted`, `relevant`, `k` | recall, mrr, ndcg | retrieval evaluation |
| `common.retrieval.deduplicate` | code | `items`, `key` | unique, duplicates | deterministic deduplication |
| `common.retrieval.sort_by` | code | `items`, `key`, `reverse` | items | stable sort helper |
| `common.retrieval.group_by` | code | `items`, `key` | groups | grouping helper |

### 5.5 `common.memory.*` — memory and knowledge management

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.memory.extract` | prompt | `text` | facts, entities | extract memory candidates |
| `common.memory.reconcile` | prompt | `new_facts`, `existing_facts` | reconciled, conflicts | merge memory |
| `common.memory.check` | prompt | `query`, `facts` | answer, citations | memory-grounded QA |
| `common.memory.verify` | prompt | `fact`, `sources` | ok, missing, issues | verify before storing |
| `common.memory.knowledge_linking` | inline | — | — | merge instruction: maintain bidirectional links |

### 5.6 `common.research.*` — iterative research and reporting

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.research.one` | prompt | `query`, `sources` | answer, citations | single-round research |
| `common.research.iterative` | prompt | `topic`, `depth`, `budget` | report, sources | multi-round research |
| `common.research.report` | prompt | `outline`, `findings` | report | assemble final report |
| `common.research.source_rank` | code | `sources`, `query` | ranked | rank sources by relevance |

### 5.7 `common.dev.*` — development and coding utilities

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.dev.run_tests` | code | `path`, `pattern`, `timeout` | summary, failures | pytest runner |
| `common.dev.apply_patch` | code | `patch`, `path` | applied, rejected | unified diff patcher |
| `common.dev.read_file_smart` | code | `path`, `level` | content | context-aware file reading |
| `common.dev.summarize_tree` | code | `path`, `max_tokens` | summary | directory tree summary |
| `common.dev.lint_diff` | code | `diff` | issues | lint a diff before applying |
| `common.dev.find_definition` | code | `path`, `symbol` | locations | simple symbol search |

### 5.8 `common.eval.*` — evaluation and judging

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.eval.judge` | prompt | `subject`, `rubric` | dimensions, veto_triggered | rubric-based evaluation |
| `common.eval.calibrate_judge` | code | `gold`, `judge_scores` | agreement, kappa, passes | judge calibration |
| `common.eval.pairwise_compare` | code | `a`, `b`, `question` | winner, rounds | debiased pairwise comparison |
| `common.eval.pairwise_judge_once` | prompt | `a`, `b`, `question` | winner, reason | single A/B judgment |
| `common.eval.progress_track` | prompt | `todos`, `context` | status, blockers | progress tracking |

### 5.9 `common.security.*` — safety and trust

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.security.injection_scan` | code | `text` | suspicious, score | regex-based injection detection |
| `common.security.redact_pii` | code | `text`, `kinds` | text, redacted | local PII redaction |
| `common.security.untrusted_content` | inline | — | — | merge instruction: isolate external content |

### 5.10 `common.style.*` — output style and formatting

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.style.tone_neutral` | inline | — | — | merge instruction: neutral, factual tone |

### 5.11 `common.task.*` — task orchestration helpers

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.task.make_handoff` | code | `task`, `facts`, `constraints`, `artifacts` | package | handoff package construction |

### 5.12 `common.learn.*` — learning and reflection

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.learn.distill_experience` | prompt | `run_trace`, `goal` | lessons, patterns | distill experience from run traces |
| `common.learn.reflect_on_failure` | prompt | `failure`, `context` | analysis, suggestions | reflect on failures and propose fixes |

### 5.13 `common.math.*` (future)

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.math.mean` | code | `values` | mean, count | arithmetic mean |
| `common.math.percentile` | code | `values`, `p` | percentile | percentile calculation |
| `common.math.stddev` | code | `values` | stddev, mean | standard deviation |
| `common.math.dot_product` | code | `a`, `b` | dot | vector dot product |
| `common.math.linalg.normalize` | code | `vector` | normalized | vector normalization |

### 5.14 `common.crypto.*` (future)

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.crypto.hmac` | code | `message`, `key`, `algo` | hex | HMAC digest |
| `common.crypto.sha256` | code | `text` | hex | SHA-256 digest |
| `common.crypto.verify_signature` | code | `message`, `signature`, `public_key` | ok | signature verification |

### 5.15 `common.format.*` (future)

| Name | Kind | Inputs | Outputs | Purpose |
|------|------|--------|---------|---------|
| `common.format.markdown_to_text` | code | `text` | text | strip Markdown |
| `common.format.yaml_load` | code | `text` | data | safe YAML parse |
| `common.format.toml_load` | code | `text` | data | TOML parse |

## 6. Integration with the naming convention

All names follow the convention in `../NAMING.md`:

- `system.<domain>.<action>` for kernel tools.
- `common.<domain>.<action>` for standard-library skills.
- `external.<vendor>.<domain>.<action>` for third-party integrations.
- `project.<app>.<domain>.<action>` for application-specific skills.

Skill invocations inside prompts use the `skill.<dotted.name>` prefix.
Tool invocations use the dotted name directly.

## 7. Implementation phases

### Phase 1: Complete the naming migration — **done**

- Rename all tools and skills to dotted names.
- Register flat-name aliases for backward compatibility.
- Update all tests, examples, and configuration files.
- Run the full test suite until green.

### Phase 2: Reorganize `std/skills.yaml` into sub-pack files — **done**

- `LocalFileSkillRegistry` now accepts a directory (sorted `*.yaml` merge),
  a list of files, or a single file (unchanged behavior); reload watches the
  max mtime of all sources.
- `agent_os/std/skills.yaml` was split into 11 domain files and deleted:
  `text.yaml` (text + hash + table), `retrieval.yaml`, `eval.yaml`,
  `dev.yaml`, `memory.yaml`, `research.yaml`, `web.yaml`, `security.yaml`,
  `style.yaml`, `task.yaml`, `learn.yaml`.

### Phase 3: Fill gaps in `system.*` — **done (core)**

- Implemented `system.file.stat`, `system.file.delete`, `system.file.mkdir`.
- Implemented `system.net.http_request` (generic HTTP).
- Deferred with reasons:
  - `system.time.sleep` — conflicts with the READ-tier gate (`cacheable`)
    and replay semantics.
  - `system.blob.list` — blob stores expose only `put`/`get`; needs a store
    interface extension first.
  - `system.ui.show_progress` — no host-callback channel exists yet
    (`ask_user`/`notify_user` are `NotImplementedError` stubs).

### Phase 4: Fill gaps in `common.*` — **done (except web search)**

- Added `common.retrieval.deduplicate`, `common.retrieval.sort_by`,
  `common.retrieval.group_by` (the earlier draft said `common.data.*`;
  §5.4 settled on `common.retrieval`).
- Added `common.dev.lint_diff`, `common.dev.find_definition`.
- Added `common.research.source_rank`, `common.research.report`.
- Added `common.web.parse_html`, `common.web.extract_links`.
- `common.web.search` deliberately **not** added: a provider-agnostic search
  needs provider credentials/config, which belongs to an `external.*` skill
  pack rather than `common`.

### Phase 5: Add advanced optional domains

- `common.math.*`
- `common.crypto.*`
- `common.format.*`

### Phase 6: Deprecation and cleanup

- After one release cycle, remove flat-name aliases from the tool registry.
- Remove legacy alias support from `LocalFileSkillRegistry`.
- Update all remaining examples to use dotted names exclusively.

## 8. Open questions

1. Should `common.web.fetch_page` remain a tool (because it does network I/O) or
   become a skill (because it composes `system.net.http_fetch` + text
   extraction)? The current implementation is a tool; it could be reimplemented
   as a thin skill once the framework supports skill-to-tool calls cleanly.
2. ~~How should we handle `common.data.*` vs `common.search`?~~ **Resolved**:
   retrieval helpers (`bm25_score`, `rrf_merge`, `retrieval_metrics`,
   `deduplicate`, `sort_by`, `group_by`) live under `common.retrieval`.
3. How should we handle LLM-provider-specific skills (e.g. an Anthropic
   document summarizer)? These should be `external.anthropic.doc.summarizer`
   and live in a separate skill pack, not in `common` or `system`.
4. Should the `system` and `common` namespaces be versioned separately? For now,
   we keep a single version per skill/tool and rely on semantic versioning per
   name. A future registry could support `system.file.read@2.0.0`.

## 9. Success criteria

1. Every tool in `system.*` has a clear `Permission` level and is registered in
   `LocalPythonToolRegistry.with_builtins()` or via the kernel builder.
2. Every skill in `common.*` has explicit `inputs`, `outputs`, and a routing
   description in `Use when / Do not use when` format.
3. No `common.*` skill directly performs OS I/O; it calls `system.*` tools.
4. The test suite passes with the new names and aliases.
5. The directory structure under `agent_os/std/` is grouped by `common.*` domain.
6. `../NAMING.md` and `../STDLIB-CATALOG.md` are updated to reflect the final hierarchy.
