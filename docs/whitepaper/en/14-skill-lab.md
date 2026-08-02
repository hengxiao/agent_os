# Skill Lab: Drafts, Assistant & the Development Loop

> Chapter: 14 · Status: implemented (L1–L5 all landed; a few polish items outstanding, see §6) ·
> Sources: docs/SKILL-DEV.md; agent_os/src/agent_os/skills/draft_store.py, skills/gate.py,
> skills/lab_assistant.py, tools/lab_tools.py, host/web/app.py, host/web/run_manager.py,
> host/web/static/js/components/lab.js, host/cli/main.py

## 1. Overview

Skill Lab is the development-loop subsystem for skills (prompt/code assets): a draft store (DraftStore), a draft layer stacked over the production registry (OverlaySkillRegistry), a five-gate submission gate (gate.py), a conversational development assistant (skill.dev.assistant), and a test panel — surfaced as the three-column `#/lab` page in the Web host and as the headless CLI entry point `agent-os lab validate`. Architecturally it belongs to the **host layer plus the skills subsystem**, not the kernel: assembly, arbitration, and signals all reuse the existing run infrastructure, and the kernel contains not a single Lab-specific line. This chapter deepens §6.4 of the executive summary, covering draft/production isolation, the gate's decision logic, the promote verification chain, and the assistant's "can edit, cannot ship" trust model.

## 2. Motivation and Background (Why)

The fourth structural defect listed in §1.2 of the executive summary is "unguaranteed quality": skills are the most important programmable assets in the system, yet their production long lacked engineering standards and an admission gate — missing metadata, over-broad permissions, and injection-inducing prompts could only blow up at runtime. Skill Lab is the answer to that defect, and its shape was molded by four concrete architectural tensions:

- **Why not edit the production registry directly?** Production skills.yaml is the single source of truth; the loader topologically sorts and hot-reloads it. Editing in place means a half-written skill can be assembled by a production run at any moment — one save of half a manifest is one production incident. Drafts must therefore be **physically separated** (their own `drafts/` tree); merging happens only at the assembly layer (OverlaySkillRegistry) and is visible only to the Lab's own runs (docs/SKILL-DEV.md §1.1).
- **Why not put it in the kernel?** By the microkernel criterion (executive summary §2.2: does it advance the loop? is it an arbitration point? is it the only common channel?), a development platform satisfies none. It is host-layer functionality, so it reuses run_manager, SSE, and the trace components wholesale — zero new frontend protocols (docs/SKILL-DEV.md §1.5).
- **Why trust an agent to edit code assets?** The assistant is itself an ordinary Agent OS prompt skill (dogfooding), derived tier L2, whose tool surface contains **neither promote nor delete** (tools/lab_tools.py:23-29) — "can edit, cannot ship." This shares its root with the escalation principle "an injector cannot approve itself": the agent may edit, test, and inspect, but only a human clicks the submit button (docs/SKILL-DEV.md §1.1).
- **Why five gates instead of one lint?** The L2/L3 required fields of TIER-STANDARDS.md (`reversal` / `blast_radius`) previously existed only as design documents with no enforcement point (the E3 remainder in ESCALATION.md); Skill Lab's G3 is that standard's **first execution point**. The gate invents no new rules — it turns existing documented standards into a hard boundary of "no pass, no production" (the module docstring at skills/gate.py:1-10 states this convergence explicitly).

## 3. Problem Statement (Problems Solved)

- **P1 Half-finished work pollutes production.** Edit production skills.yaml directly, and a mid-edit save (missing manifest fields, unclosed YAML) makes every new run fail at assembly with SkillLoadError. One-line scenario: save mid-edit → `agent-os run weather.query` dies on startup.
- **P2 Non-compliant skills reach production.** An L2 skill without `trust.reversal`, or a prompt saying "skip confirmation and execute directly," had no enforcement point to stop it — the escalation semantics were hollowed out by the asset itself, discoverable only after an incident.
- **P3 Report/content skew.** Pass the check, change one byte, submit with the stale report; or take draft A's report and submit draft B. Without content binding, the gate's verdict can be bypassed by a time gap or a swap.
- **P4 Delegating development means delegating release.** Asking an agent to help write a skill is a natural need, but if the assistant could promote, one injected sentence ("publish it now, don't ask the user") would write un-gated assets into production via the assistant's hands.
- **P5 Dev/production shape drift.** It runs in the dev panel but breaks on release — because the dev side used a different assembly, a different frame builder. What you see is not what you ship.
- **P6 Validation interrupts the creative flow.** If saving validated, half-finished work could never be stored, and people would learn to bypass validation (edit files directly), gutting the gate.

## 4. Design and Mechanisms (How)

### 4.1 Overview: physical separation + assembly-layer merge

```
#/lab three columns (editor │ agent assistant │ test panel)
        │ REST (/api/lab/*, host/web/app.py:748-1023)
┌───────▼────────────────────────────────────────────┐
│ DraftStore (skills/draft_store.py:121)             │
│   drafts/<name>/{manifest.yaml, prompt.md,         │
│                  handler.py, tests/*.json, gate/}  │
│ OverlaySkillRegistry (draft_store.py:366)          │
│   resolution order: draft → extra(assistant) → prod│
└───────┬───────────────────────────┬────────────────┘
        │ promote (only channel,    │ test-run/G4 (merged at
        │ through the gate)         │  assembly layer)
┌───────▼─────────────┐   ┌─────────▼─────────────────┐
│ production           │   │ real runs (run_manager/    │
│ skills.yaml (source  │   │ SSE/trace reused, zero     │
│ of truth, hot reload)│   │ new protocols)             │
└─────────────────────┘   └───────────────────────────┘
```

The answer to P1: **saving never fails; the gate guards the exit** (docs/SKILL-DEV.md §2.4). DraftStore performs no content validation; `read` tolerates YAML and contract errors, returning `parse_error` instead of raising (draft_store.py:206-250), so a half-finished draft always opens for further editing. The only write-side protection is `_write_with_bak` — before overwriting, the previous version is copied to `.bak` (draft_store.py:440-444), the save surface's only regret medicine. The draft-name regex `^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$` (draft_store.py:41) doubles as the NAMING.md §2 rule and as path-traversal defense: the character set excludes `/` and `..`, and the directory name is the draft name. The frontend blocks JSON syntax errors before saving (lab.js:468-476) — "never interrupt the flow" means the server does not validate, not that broken JSON gets written to storage (SKILL-DEV.md L1 implementation note 4).

### 4.2 OverlaySkillRegistry: drafts win, half-baked drafts don't shadow

The overlay implements the full SkillRegistry protocol surface (get/visible_to/make_frame/manifests, draft_store.py:387-428); `get` resolves in the order **draft → extra → production**, and a temporarily non-compliant draft **falls back transparently** to the production namesake (draft_store.py:388-394) — a half-baked shadow never masks a usable version. Test runs wire in via `start_run(kernel_patcher=)`; `swap_skills_overlay` swaps the overlay into the kernel's three skills reference points (kernel.skills / ContextManager._skills / tools._skills, host/web/run_manager.py:456-464). The overlay lives only in the Lab request scope; production runs are unaffected (run_manager.py:446-453).

The answer to P5 rests on two reuses: child-frame construction shares the production function `local_file.build_child_frame` (draft_store.py:414-417; prevents two frame semantics from drifting), and traces render through the debugger's existing `deriveTraceView/renderTrace` (lab.js:21). A trial run is a production-shaped run — same assembly, same gates, same rendering.

### 4.3 The five-gate submission gate

`validate_draft` (skills/gate.py:122) runs five fixed gates, each judged `pass|warn|fail` (plus a `skip` placeholder state), and the report is persisted to `drafts/<name>/gate/<ts>.json`:

| Gate | Scope | Key decisions | Code anchor |
|---|---|---|---|
| G1 metadata | name/description/version | non-hierarchical name = fail; missing "Use when" in description, non-semver version = warn; folds in the existing `validate_manifest` lints | gate.py:155-178 |
| G2 contract | inputs/outputs | invalid JSON Schema = fail; at derived tier ≥L2 every inputs parameter must declare `type` (no free-text parameters) | gate.py:181-207 |
| G3 tier compliance | derived tier + trust fields | ≥L2 forbids inline, L3 forbids `confirm: first`, L2 requires `reversal`, L3 requires `blast_radius` = fail; whitelist sources listed as info (so you see where the tier comes from) | gate.py:210-238 |
| G4 smoke run | tests/*.json, real runs | overlay-assembled real run; outputs must pass the outputs schema; no cases = warn, not fail | gate.py:241-272; app.py:865-884 |
| G5 prompt hygiene | injection-inducement scan | sentence-by-sentence anti-pattern matching (CN/EN patterns); a hit is released if the same sentence matches the positive-phrasing safelist ("after confirmation / ask the user", etc.) | gate.py:45-87 |

Two trade-offs deserve explicit statement. First, G3's tier is not self-reported: it is computed on the spot by `explain_skill_tier` as the recursive max over the whitelist (gate.py:149-150) — self-reports lie. This is the same function the production escalation check uses; the Lab has no second tier semantics. Second, G5 is "steady over zealous" (gate.py:43-44): sentence-level matching plus a positive-phrasing safelist exists so that *correct* phrasings like "execute only after user confirmation" are not collateral damage — too many false positives teach people to switch the gate off, while false negatives are backstopped by G4 and human review.

### 4.4 The promote verification chain (the only channel to production)

Promote is a host action, not a skill call, so it does not pass the escalation gate; but it is a real write (L2 semantics) and the UI confirms it explicitly (docs/SKILL-DEV.md §1.1). Its verification chain (gate.py:334-396) is the complete answer to P3:

```
POST /api/lab/{draft}/promote {report_id, version?, warnings_ack}
  ① the report must exist (read_gate_report looks it up by id —
      fabrication-proof; draft_store.py:329)
  ② report manifest_hash == current draft hash
      — the hash covers manifest+prompt+handler (gate.py:90-105);
        one changed byte voids the old report (GateError 409)
  ③ the report contains no fail
  ④ the server re-runs G1–G3 with no fail (G4/G5 trust the report)
  ⑤ any warn requires warnings_ack (frontend "I have read the warnings")
  ⑥ write production skills.yaml: .bak backup first, replace-or-append
      (write_production_entry, gate.py:399-425)
  ⑦ loader reload() hot-reloads (affects only subsequently created runs)
  ⑧ promotions.jsonl appends {promoted_by, gate_report_id, version}
      — first actual use of the Provenance contract slot
```

The report id is `<ts_ms>-<manifest_hash>` (draft_store.py:323): readable for audit, with the anti-skew comparison key embedded. Why re-run only G1–G3 while trusting G4/G5? Cost: G4 assembles a kernel and runs for real (app.py:865-884), disproportionate on the promote path; G1–G3 are pure static checks, cheap to re-run, and they cover exactly the "asset declaration surface" — the hash already guarantees content is unchanged, so the re-run defends against a hand-forged report. The version defaults to a patch bump; a non-semver version falls back to 0.1.0 — "if you can't bump it, don't pretend you can" (gate.py:313-327). The frontend mirrors this as a state machine: `promoteReady` requires a report, no fail, not stale, and warn acknowledged (lab.js:304-316); saving immediately dims the submit button — the server-side hash check is the backstop, the client is the experience.

### 4.5 The agent assistant: a dogfooded trust model

The assistant is the built-in meta-skill `skill.dev.assistant` (skills/lab_assistant.py:39-64): an ordinary prompt skill whose whitelist is exactly five `lab.draft.*` tools, with limits max_steps=12 and timeout=120. It is injected through the overlay's `extra` slot (resolving after drafts, before production), never entering the production registry; the tools are registered on the spot inside the kernel_patcher, invisible to production runs (app.py:973-986). All five tools declare their side_effect **explicitly** (tools/lab_tools.py:69-192):

| Tool | permission | side_effect | Notes |
|---|---|---|---|
| lab.draft.read / list | READ | none | read a draft / list drafts |
| lab.draft.write | WRITE | reversible | whole-manifest or field-local edit; `.bak` is the reversal mechanism |
| lab.draft.validate | READ | none | runs the gate; **read-only report, not persisted** |
| lab.draft.test_run | READ | none | overlay real run; side effects confined to the run's workdir; in-tool step cap 25 (lab_tools.py:52) |

The answer to P4 has three layers: the tool surface has no promote/delete (injection-assisted release is impossible at the protocol level); `lab.draft.validate` reports are not persisted, and promote accepts only persisted reports — the assistant's report does not count, same root as "can edit, cannot ship" (SKILL-DEV.md L4 implementation note 3); and the field-local write whitelist `_WRITABLE_FIELDS` excludes `name` (lab_tools.py:33-49), since the name is pinned by the directory — the assistant cannot write its way into a path-traversal shape. If someone tricks the assistant into calling an L3 skill, the escalation gate blocks it as usual — the assistant has no privileged bypass. One contract constraint surfaced during implementation: the designed name was `skill.draft.*`, but the kernel's `_dispatch_call` intercepts the `skill.` prefix as a sub-skill call, so those calls would never reach the Tool Registry; the tools were renamed `lab.draft.*` with semantics unchanged (SKILL-DEV.md L4 implementation note 1). On the frontend, after the assistant edits a draft the editor reloads from the server copy, and a top-level-field diff produces a one-line highlight (lab.js:620-636).

### 4.6 The three-column frontend and the CLI

The `#/lab` page: the left column is a seven-group full-field editor (identity/contract/instruction/permissions/policy/trust/behavior, lab.js:188-264) with no hidden fields; the derived-tier badge reuses the escalation card's tier→perm palette, and the `/tier` endpoint accepts `?tools=&skills=` query overrides — even an **unsaved** whitelist in the editor derives the tier live, without polluting the on-disk draft (app.py:812-839); at derived tier ≥L2 the inline checkbox is disabled with a hard-gate hint (lab.js:686-689). Deletion is L2 semantics: two-click confirm (lab.js:764-777). The CLI `agent-os lab validate` runs the same `validate_draft` as the Web, persists reports to the same `gate/` directory (shared drafts_root — a CLI check can be promoted from the Web), and exits 0 for pass/warn, 2 for fail (host/cli/main.py:335-358).

## 5. Effects and Verification (Results)

After landing, a complete development loop reads: create (empty / three templates / copy from production) → manual editing plus conversational assistant edits → trial runs in the test panel (visible traces, explicit outputs validation) → check (five gate cards, red/yellow/green/grey) → human clicks submit (the confirm row allows a version override) → production hot-reloads and the skill is runnable; the next iteration goes "production skill → edit in Lab," copying back to a draft. Test evidence (6 Lab-specific Python test files, 43 cases, all green; repo-wide baseline: 812 Python tests + 24 frontend test files, docs/SKILL-DEV.md §4 L5):

- tests/skills/test_draft_store.py (9 cases): name/path-traversal rejection (`test_name_validation_path_traversal`), tolerant reads that never 500, `.bak` backup, overlay draft-wins plus transparent fallback for non-compliant drafts, and all three templates passing G1/G2 at creation.
- tests/skills/test_gate.py (11 cases): per-gate assertions; `manifest_hash` tracking content; G5 failing injection inducement while releasing positive phrasing (`test_g5_positive_phrasing_passes`); promote's success path and its three rejections (stale report / failing report / unacknowledged warn, `test_promote_rejects_stale_report_and_fail_and_unacked_warn`); version bump and manual override.
- tests/web/test_lab_api.py (9 cases): CRUD round trip, non-compliant draft reads that don't 500, tier override queries, validate report shape, promote end-to-end with stale-report rejection (409).
- tests/web/test_lab_testrun.py (5 cases): input-form and case-form trial runs, mock_script driving MockProvider, runs marked failed when outputs miss the schema, G4 warning on zero cases, G4 pass/fail.
- tests/web/test_lab_assistant.py (5 cases): **the tool surface is exactly five tools, with no promote/delete** (`test_tool_surface_exactly_five_and_tiers`), assistant whitelist and L2 tier, field-local and whole-manifest writes, read-only validate, and an end-to-end conversation.
- tests/cli/test_lab.py (4 cases): exit code 0 for pass/warn and 2 for fail; reports persisted to the same directory the Web uses; missing drafts exit cleanly without a traceback.
- Frontend static/tests/lab.test.mjs: name validation, draft↔form mapping, gate cards, promote enable/disable conditions, save flow and DOM smoke.

Ripple effects: ESCALATION E3's required-field lints for reversal/blast_radius landed in G3 — the two plans converge there (SKILL-DEV.md §4); the SkillArtifact/Provenance contract slot saw its first actual use (promotions.jsonl); `build_child_frame` was extracted into a shared function so overlay and production build child frames identically; and the `skill.`-prefix interception constraint became visible through the tool renaming and was recorded in the implementation notes. A concrete example: the three templates (prompt_query / file_process / danger_op, draft_store.py:55-118) carry trust placeholders that are real mechanism descriptions, so the L2/L3 templates pass G3's required fields at creation.

## 6. Limitations and Boundaries (Limitations)

1. **Promote supports only single-file skills.yaml.** For directory or multi-file skill_set layouts, `write_production_entry` raises explicitly (gate.py:406-410); the merge strategy is unimplemented (SKILL-DEV.md L2 implementation note 1). A multi-file production surface cannot be published to from the Lab.
2. **A code skill's handler source does not reach production.** Promote inlines only prompt.md into the production entry; the handler travels as a dotted path, and source merging is not done (L2 implementation note 4). Copying a code skill from production has the same gap — the source "cannot be copied into a runnable form" (comment at draft_store.py:197-198), so the path is left for a human to resolve.
3. **G4 smoke evidence can go stale.** `manifest_hash` covers only manifest+prompt+handler (gate.py:96-104), not tests/; editing the cases does not void the old report, and promote re-runs only G1–G3 while G4 trusts the report (gate.py:359-366) — one can therefore promote carrying stale smoke evidence. The mitigating reading is that a G4 verdict means "these cases passed at that time," and changing the cases does not change that past fact; but strictly speaking this is a known, honest gap.
4. **G5 is a regex pattern table with a miss surface.** It covers only the CN/EN expressions in the table (gate.py:45-60); rephrased variants, other languages, and encoding tricks slip through; it scans only the prompt, not other free text like description; the safelist (gate.py:61-66) can also over-release. It is positioned as a "steady over zealous" first sieve, not a complete injection detector.
5. **G4 with zero cases is only a warn, not a fail** (gate.py:246-252): smoke runs are the main quality evidence, yet a human can check "I have read the warnings" and promote with zero cases — the gate leaves the final call to the human, and leaves the room for wishful thinking with it.
6. **Shallow rollback surface.** Deleting a draft has no recycle bin (draft_store.py:297-302); `.bak` keeps exactly one layer, so two consecutive saves lose the earlier version; promote is linear versioning plus .bak with no branching or merging — git is the real version system (SKILL-DEV.md §5 states this non-goal explicitly).
7. **Single-user, single-session premise.** After the assistant edits, the editor reloads from the server copy (L4 implementation note 5); there is no collaboration, commenting, or review flow; promote does not pass the escalation gate, so its authorization boundary equals the Web single-user principal plus the UI confirmation. Authorization isolation under multi-user deployment depends on DATA-AUTHZ D3 (designed, not implemented).
8. **Frontend polling instead of SSE.** Trial runs and assistant replies poll every 500 ms with a 120 s ceiling (lab.js:548-550, 605-606); a timeout only means the frontend stopped waiting — the run itself keeps going. SSE incremental rendering and the diff-view polish are not done (L3 note 2, L5 note 3).
9. **Assistant capability ceiling.** limits cap the assistant at max_steps=12 and the test_run tool at 25 steps — complex multi-round refactors hit the ceiling; when an assistant run fails, the frontend shows only the error text, with no trace-panel linkage for the assistant's own run.

## 7. References

- Design document: docs/SKILL-DEV.md (system/UX/API/phasing, with L1–L5 implementation notes); adjacent contracts: docs/DESIGN.md §2.1, docs/ESCALATION.md, docs/TIER-STANDARDS.md, docs/NAMING.md, docs/RUNNERS.md §3.4
- Drafts and assembly: agent_os/src/agent_os/skills/draft_store.py; agent_os/src/agent_os/skills/local_file.py (build_child_frame)
- Gate and promote: agent_os/src/agent_os/skills/gate.py
- Assistant and tools: agent_os/src/agent_os/skills/lab_assistant.py; agent_os/src/agent_os/tools/lab_tools.py
- Hosts: agent_os/src/agent_os/host/web/app.py (/api/lab/*); agent_os/src/agent_os/host/web/run_manager.py (assemble_lab_kernel/swap_skills_overlay); agent_os/src/agent_os/host/cli/main.py (_cmd_lab)
- Frontend: agent_os/src/agent_os/host/web/static/js/components/lab.js; static/tests/lab.test.mjs
- Tests: agent_os/tests/skills/test_draft_store.py, tests/skills/test_gate.py, tests/web/test_lab_api.py, tests/web/test_lab_testrun.py, tests/web/test_lab_assistant.py, tests/cli/test_lab.py

> Errata note: the executive summary §6.4 quotes CLI exit codes as "0/2/4"; the code is authoritative — `agent-os lab validate` actually has only two outcomes, pass/warn = 0 and fail = 2 (host/cli/main.py:335-358; asserted likewise in tests/cli/test_lab.py).
