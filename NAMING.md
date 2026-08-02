# Skill & Tool Naming Convention

This document defines the hierarchical naming convention for **skills** and **tools** in `agent_os`. The goal is to make names self-describing, group related capabilities by domain, and mirror the module-naming conventions used in mainstream programming languages such as Python.

## 1. Design Principles

1. **Hierarchical**: names are dot-separated so that related capabilities share a common prefix.
2. **Predictable**: the first segment (top-level domain) tells you whether something is a runtime primitive, a reusable standard library capability, a third-party integration, or an application-specific skill.
3. **Stable**: once a name is published, it should not change without a deprecation cycle.
4. **Compact**: prefer 2–4 segments; rarely use more than 5.
5. **Lowercase**: use lowercase and `snake_case` at every level. No camelCase, no kebab-case, no uppercase.

## 2. Name Format

```text
<domain>.<category>.<subcategory>.<action>
```

- Segments are separated by `.` (U+002E FULL STOP).
- Each segment uses `snake_case`: lowercase letters, digits, and underscores only.
- The first character of each segment must be a letter or underscore.
- A name must contain **at least two segments**.

### Valid examples

- `system.file.read`
- `common.text.summarize`
- `external.anthropic.doc.summarizer`
- `project.support_desk.handle_ticket`

### Invalid examples

- `read` — missing top-level domain
- `System.File.Read` — uppercase not allowed
- `common-text.summarize` — segment uses kebab-case
- `common..summarize` — empty segment

## 3. Reserved Top-Level Domains

| Domain | Meaning | Examples |
|--------|---------|----------|
| `system` | Runtime / OS primitives provided by the agent kernel. These are capabilities the framework itself exposes and cannot be removed by a skill pack. | `system.file.read`, `system.shell.exec`, `system.time.now` |
| `common` | Reusable standard-library capabilities that are available to any skill or tool. Comparable to a language standard library. | `common.text.summarize`, `common.web.fetch_page`, `common.memory.reconcile` |
| `external` | Integrations with third-party vendors, APIs, or external services. The second segment is the vendor or service name. | `external.anthropic.doc.summarizer`, `external.openai.embedding.create` |
| `project` | Application- or project-specific skills. The second segment is the application or team name. | `project.support_desk.handle_ticket`, `project.travel_planner.plan_route` |
| `user` | User-specific or experimental custom skills. | `user.mylearning.note_taker` |

Additional top-level domains may be registered only after a formal review. The two core domains used by the framework are `system` and `common`.

## 4. Converting Existing Names

The following mapping applies to current built-in tools and standard-library skills. Legacy flat names are kept as aliases for backward compatibility until a deprecation release removes them.

| Legacy flat name | Hierarchical name | Domain reasoning |
|-------------------|-------------------|------------------|
| `fs_read` | `system.file.read` | Kernel file-system primitive |
| `fs_write` | `system.file.write` | Kernel file-system primitive |
| `fs_edit` | `system.file.edit` | Kernel file-system primitive |
| `fs_list` | `system.file.list` | Kernel file-system primitive |
| `fs_search` | `system.file.search` | Kernel file-system primitive |
| `shell_exec` | `system.shell.exec` | Kernel process primitive |
| `python_exec` | `system.python.exec` | Kernel language-runtime primitive |
| `http_fetch` | `system.net.http_fetch` | Kernel network primitive |
| `now` | `system.time.now` | Kernel time primitive |
| `todo_write` | `system.task.todo_write` | Kernel task primitive |
| `todo_update` | `system.task.todo_update` | Kernel task primitive |
| `todo_read` | `system.task.todo_read` | Kernel task primitive (added with this convention; no legacy alias) |
| `skill_search` | `system.skill.search` | Kernel skill-discovery primitive |
| `ask_user` | `system.ui.ask_user` | Kernel UI primitive |
| `notify_user` | `system.ui.notify_user` | Kernel UI primitive |
| `fetch_page` | `common.web.fetch_page` | Reusable web utility |
| `extract_json` | `common.text.extract_json` | Reusable text utility |
| `template_render` | `common.text.template_render` | Reusable text utility |
| `diff_text` | `common.text.diff` | Reusable text utility |
| `word_count` | `common.text.word_count` | Reusable text utility |
| `summarize` | `common.text.summarize` | Reusable text utility |
| `classify` | `common.text.classify` | Reusable text utility |
| `token_estimate` | `common.text.token_estimate` | Reusable text utility |
| `chunk_text` | `common.text.chunk` | Reusable text utility |
| `hash_digest` | `common.hash.digest` | Reusable hashing utility |
| `csv_to_rows` | `common.table.csv_to_rows` | Reusable table utility |
| `rows_to_markdown` | `common.table.rows_to_markdown` | Reusable table utility |
| `slugify` | `common.text.slugify` | Reusable text utility |
| `normalize_whitespace` | `common.text.normalize_whitespace` | Reusable text utility |
| `bm25_score` | `common.retrieval.bm25_score` | Reusable retrieval utility |
| `rrf_merge` | `common.retrieval.rrf_merge` | Reusable retrieval utility |
| `retrieval_metrics` | `common.retrieval.retrieval_metrics` | Reusable retrieval utility |
| `injection_scan` | `common.security.injection_scan` | Reusable security utility |
| `redact_pii` | `common.security.redact_pii` | Reusable security utility |
| `identifier_guard` | `common.text.identifier_guard` | Reusable text utility |
| `make_handoff` | `common.task.make_handoff` | Reusable task utility |
| `date_normalize` | `common.text.date_normalize` | Reusable text utility |
| `citation_check` | `common.text.citation_check` | Reusable text utility |
| `run_tests` | `common.dev.run_tests` | Reusable development utility |
| `apply_patch` | `common.dev.apply_patch` | Reusable development utility |
| `read_file_smart` | `common.dev.read_file_smart` | Reusable development utility |
| `summarize_tree` | `common.dev.summarize_tree` | Reusable development utility |
| `research_iterative` | `common.research.iterative` | Reusable research utility |
| `research_one` | `common.research.one` | Reusable research utility |
| `memory_extract` | `common.memory.extract` | Reusable memory utility |
| `memory_reconcile` | `common.memory.reconcile` | Reusable memory utility |
| `memory_check` | `common.memory.check` | Reusable memory utility |
| `memory_consolidate` | `common.memory.consolidate` | Reusable memory utility |
| `verify_before_store` | `common.memory.verify` | Reusable memory utility |
| `judge` | `common.eval.judge` | Reusable evaluation utility |
| `calibrate_judge` | `common.eval.calibrate_judge` | Reusable evaluation utility |
| `pairwise_compare` | `common.eval.pairwise_compare` | Reusable evaluation utility |
| `pairwise_judge_once` | `common.eval.pairwise_judge_once` | Reusable evaluation utility |
| `progress_track` | `common.eval.progress_track` | Reusable evaluation utility |
| `tone_neutral` | `common.style.tone_neutral` | Reusable style utility |
| `untrusted_content` | `common.security.untrusted_content` | Reusable security utility |
| `knowledge_linking` | `common.memory.knowledge_linking` | Reusable memory utility |
| `distill_experience` | `common.learn.distill_experience` | Reusable learning utility |
| `reflect_on_failure` | `common.learn.reflect_on_failure` | Reusable learning utility |
| `handle_ticket` | `project.support_desk.handle_ticket` | Application-specific |
| `classify_ticket` | `project.support_desk.classify_ticket` | Application-specific |
| `plan_route` | `project.travel_planner.plan_route` | Application-specific |

## 5. Runtime Addressing

A skill is canonically addressed as:

```text
<namespace>:<hierarchical.name>@<version>
```

Examples:

- `local:common.text.summarize@1.0.0`
- `external:external.anthropic.doc.summarizer@2.1.0`

When a skill is exposed to an LLM as a callable pseudo-tool, the prefix changes from the historical `skill__<name>` to a dot-separated form that mirrors the hierarchical name:

```text
skill.<hierarchical.name>
```

Examples:

- `skill.common.text.summarize`
- `skill.system.file.read`
- `skill.project.support_desk.handle_ticket`

Tools that are registered directly with the tool registry use the hierarchical name as their tool name, with no prefix.

### Provider wire format

The dot-separated form is the internal canonical name. OpenAI-compatible and Anthropic APIs reject dots in function names (Anthropic allows only `^[a-zA-Z0-9_-]{1,64}$`), so providers translate at the API boundary only:

- Outbound (requests): tool schemas and assistant-history `tool_calls` names are mangled `.` → `__` (`system.file.read` → `system__file__read`).
- Inbound (responses): model-returned `tool_calls` names are un-mangled `__` → `.`; names without `__` pass through unchanged (legacy flat aliases, hallucinated names).

Implementation: `agent_os/src/agent_os/providers/naming.py` (`mangle_name` / `unmangle_name`), applied by `openai_compatible.py` (and its subclass `kimi.py`) and `claude.py`. Mapping is collision-safe because canonical names and aliases never contain `__`.

## 6. Migration Rules

1. All new skills and tools must use the hierarchical naming convention.
2. Existing flat names in `std/skills.yaml`, `tools/builtins.py`, and `tools/std.py` are renamed to hierarchical names.
3. Legacy flat names are kept as aliases for at least one release cycle, with a deprecation warning when used in `permissions.tools`, `permissions.skills`, or direct invocation.
4. Documentation and examples are updated to use hierarchical names.
5. After the deprecation period, aliases are removed and only hierarchical names remain.

## 7. Examples by Domain

### `system` — kernel primitives

```text
system.file.read
system.file.write
system.file.edit
system.file.list
system.file.search
system.shell.exec
system.python.exec
system.net.http_fetch
system.net.http_request
system.time.now
system.time.sleep
system.task.todo_write
system.task.todo_update
system.task.todo_read
system.skill.search
system.ui.ask_user
system.ui.notify_user
```

### `common` — standard library

```text
common.text.summarize
common.text.extract_json
common.text.template_render
common.text.diff
common.text.word_count
common.text.chunk
common.text.token_estimate
common.text.classify

common.web.fetch_page
common.web.search

common.memory.extract
common.memory.reconcile
common.memory.check
common.memory.verify

common.research.one
common.research.iterative
common.research.report

common.dev.run_tests
common.dev.apply_patch
common.dev.read_file_smart
common.dev.summarize_tree

common.eval.judge
common.eval.progress_track
```

### `external` — third-party integrations

```text
external.anthropic.doc.summarizer
external.anthropic.message.create
external.openai.embedding.create
external.openai.chat.complete
external.google.search.execute
```

### `project` — application-specific skills

```text
project.support_desk.handle_ticket
project.support_desk.classify_ticket
project.support_desk.process_refund
project.travel_planner.plan_route
project.travel_planner.select_hotel
```

## 8. Related Documents

- `DESIGN.md` — overall architecture
- `STDLIB.md` — standard library design
- `STDLIB-CATALOG.md` — current standard library catalog
- `SKILL-INLINING.md` — skill inlining mechanics
