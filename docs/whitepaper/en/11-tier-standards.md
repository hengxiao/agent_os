# Tiered Production Standards & the Admission Gate

> Chapter: 11 · Status: Implemented (standard v0.1 finalized; gates G1/G2/G3/G5 fully landed, G4 smoke-run implemented but dependent on runner injection; remaining gaps in §6) · Sources: `docs/TIER-STANDARDS.md`, `agent_os/src/agent_os/skills/gate.py`, `agent_os/tests/skills/test_gate.py`, `docs/SKILL-DEV.md`, `docs/ESCALATION.md`

## 1. Overview

The tiered production standard (`docs/TIER-STANDARDS.md`) defines the design / implement / test engineering requirements that L1/L2/L3 skills and tools must each satisfy; the admission gate (`agent_os/src/agent_os/skills/gate.py`) is where that standard becomes enforceable — the only channel by which a Skill Lab draft reaches production (promote) requires a gate report that is all-green (or has acknowledged warnings). It complements the runtime escalation gate: escalation decides whether *this call* may enter a higher tier, the admission gate decides whether *this asset* deserves to be in production at all. This chapter expands §5.4 of the executive summary.

## 2. Motivation and Background (Why)

The three-tier trust model rests on a premise that is easy to miss: `docs/ESCALATION.md` §2.3 states that "no re-authorization within a tier is predicated on every member of that tier passing the same production standard — only then are their trust levels equal." The runtime deliberately does not confirm same-tier moves (confirmation fatigue would drown the L3 human reviews that actually matter), which outsources intra-tier safety wholesale to the production side. Without a production standard, the trust model has an unclosed edge.

Why the standard is a standalone document plus an out-of-kernel gate rather than kernel code:

- **Different subject, different time.** The bulk of the standard is per-tier norms and a PR checklist; it constrains authors and reviewers, and it applies at production time. The kernel, by the microkernel criterion (F/P/I), keeps only runtime arbitration points. Putting "is the reversal field filled in" into the kernel loop would slow the dispatch path and violate the axiom that the kernel owns the loop while skills own policy.
- **A documented standard has no enforcement point.** After TIER-STANDARDS.md was published, the mandatory `reversal`/`blast_radius` fields remained paper requirements for a long time; the `gate.py` module docstring describes G3 as "the convergence point of ESCALATION E3: the required-field lint lands here (previously there was only the design in `docs/TIER-STANDARDS.md` §4/§5, with no enforcement point)." The standard says what *should* be; the gate is what keeps non-compliant assets *out*.
- **Adjudicate at the exit, not in the editor.** SKILL-DEV §1.3 lays down "saving never fails ... the editor never interrupts the creative flow; the gate always guards the exit"; gate.py's docstring restates this as the gating philosophy: "the editor does not interrupt, the gate guards the exit — this module is the single adjudication point, shared by validate and promote." Drafts may be saved half-finished at any time; compliance is judged only at the two exits, "check" and "submit".
- **Mechanism over natural language.** The last row of the anti-pattern table in TIER-STANDARDS §8: "substituting prompt pleading for mechanism ('model, please ask the user first') is wrong — prompt pleading can be bypassed by injection; the gate must live in the runner, not in natural language." The mirror image of the same logic: sentences inside a prompt asset that *incite* bypassing the gate are intercepted sentence-by-sentence by G5.

## 3. Problem Statement (What It Solves)

Each scenario below maps to a concrete gate decision:

1. **Claiming reversible without a reversal mechanism.** A skill whitelists `system.file.write` (derived tier L2) but its manifest has no `trust.reversal` — per standard §4 it must either document a real reversal mechanism or move up to L3, yet nothing enforced that. → G3 fail (`gate.py:230-233`).
2. **High-tier skills accepting free-text parameters.** Escalation principle 1 requires cross-tier call parameters to be structured (schema validation precedes confirmation); an L2 skill whose `inputs.properties.path` lacks `type` voids the "prescribed format" contract. → G2 fail (`gate.py:196-206`).
3. **Inlining through the isolation wall.** A skill with derived tier ≥L2 marked `inline: true` would have its instruction segment merged into a lower-tier caller's SYSTEM at assembly time — the clean-context invariant broken from the production side. → G3 fail (`gate.py:222-225`; a sibling load-time hard gate exists at `skills/manifest.py:120-124`).
4. **Bulk authorization of irreversible operations.** An L3 skill declaring `trust.confirm: first` is a back door to approve-run for deletion-class operations. → G3 fail (`gate.py:226-229`).
5. **Report/content mismatch.** An author gets a green report, edits one character of the prompt, then promotes with the stale report; or fabricates a report outright. → promote's three-layer defense (`gate.py:352-371`).
6. **Injection inducement shipped inside a prompt asset.** A draft prompt contains "skip confirmation and execute the deletion directly" — the mechanism problem written back into natural language, amplified by skill distribution. → G5 fail (`gate.py:70-87`).
7. **Missing or dishonest smoke evidence.** A draft ships no `tests/*.json` cases, or its cases fail a real run / violate the outputs schema. → G4 warn / fail (`gate.py:240-272`).

## 4. Design and Mechanism (How)

### 4.1 Tiering by side-effect semantics, not perceived danger

The general principles in standard §0 are the axioms of the whole system: **the tier is determined by side effects, not by "importance"** — a tool reading core secrets is still L1, because confidentiality is independently handled by the data layer's authN+Z with the run's principal as the criterion; the tier answers exactly one question: "after execution, has the outside world changed, and can the change be undone?" Tiering follows the decision tree (§1):

```
After execution, does the outside world (files/DB/network peer/process/funds) change?
├─ No (pure read / pure compute)──────────────────────────→ L1 none
└─ Yes → can the change be fully undone (no observable difference after revert/compensation)?
    ├─ Yes, or the loss is tolerable (file overwritable, message retractable)→ L2 reversible
    └─ No (data deleted, VM destroyed, money transferred)───────────────────→ L3 irreversible
```

Three accompanying rules: **round up, not down** (when unsure, place higher; downgrade later via the §6 process once control is demonstrated); **standards are cumulative** (L2 includes all of L1, L3 all of L2); **a skill's tier is derived** (max over whitelisted tools/skills, self-reporting forbidden — `derive_skill_tier` at `api/v1/escalation.py:92-104`; there is no "I only call it on rare branches" exception).

### 4.2 Per-tier standards (cumulative)

| Tier | design (required) | implement (required) | test (required) |
|---|---|---|---|
| L1 | Complete contract, routing-style description, documented freshness semantics | No write side effects; failures return structured errors, never success-looking empty results | manifest lint + schema unit tests; idempotence holds by construction |
| L2 | `trust.reversal` required, one reversal/compensation mechanism per side-effect class; minimal side-effect surface | Idempotence (keys/upsert/target-state); atomic writes (temp file + rename); results carry `affected`/`reversible_by` | containment, idempotence (two identical calls, empty diff), **reversal drill** (actually reverse, assert restoration) |
| L3 | `trust.blast_radius` required; dry-run mandatory; named targets, batch caps | dry_run and real execution **share one target-resolution code path**; empty/wildcard targets are errors; TOCTOU re-validation before execution | no approve-run path (two calls → two pendings); dry_run list == `destroyed` list; TOCTOU and injection-abuse tests |

The key stance: L2's `reversal` and L3's `blast_radius` **are not documentation fields but commitments verified by tests** — "reversal drill: actually execute the reversal mechanism and assert it restores" (TIER-STANDARDS §4). When a real reversal cannot be written, the standard's answer is to move up a tier, not to fudge the text.

### 4.3 The five-gate admission gate

The gate freezes the machine-checkable part of the standard into five gates (`gate.py:36`, `GATES = ("g1","g2","g3","g4","g5")`), each returning `pass|warn|fail`; any fail fails the whole report (`gate.py:277-281`):

| Gate | Contents | Key implementation |
|---|---|---|
| G1 metadata | name matches NAMING hierarchy (fail); semver version (warn); routing-style description (warn); all `validate_manifest` lints folded in | `gate.py:154-178` |
| G2 contract | draft parses; inputs/outputs are legal JSON Schemas (`check_schema`); derived tier L2+ requires `type` on every parameter | `gate.py:180-207` |
| G3 tier compliance | derived tier computed (overlay); ≥L2 forbids inline; L3 forbids `confirm: first`; L2 requires `reversal`; L3 requires `blast_radius` | `gate.py:209-238` |
| G4 smoke run | run the draft's `tests/*.json` cases as real runs; no cases → warn, failing case → fail; skip when no runner injected | `gate.py:240-272` |
| G5 prompt hygiene | sentence-by-sentence scan for injection inducement (pattern table `gate.py:45-60`); hit without same-sentence positive phrasing → fail | `gate.py:70-87` |

Two G3 mechanism details deserve expansion:

- **The derived tier is computed over an overlay.** A draft may reference another unsubmitted draft or redefine an existing skill; the gate disguises the single draft as a DraftStore and stacks it over the production registry as an `OverlaySkillRegistry` (`gate.py:149`, `293-305`), then calls `explain_skill_tier` (`api/v1/escalation.py:107-140`) — the derivation is the same function with the same semantics as post-promote production, and the `sources` breakdown enters the report as info-level findings, so a human sees *which* tool/sub-skill the tier comes from rather than a bare level (asserted in `test_gate.py:155-156` as `("info","tool system.file.delete: irreversible")`).
- **G5 errs on the conservative side.** Sentences are split (`gate.py:67`); a sentence matching an anti-pattern is pardoned if the same sentence hits the positive-phrasing safelist ("after confirmation / with consent / ask the user ...", `gate.py:61-66`) — "execute the deletion only after the user confirms" must not be collateral damage; one finding per sentence, no carpet bombing.

### 4.4 promote: report verification and production write

```
draft ──validate──▶ report persisted to drafts/<name>/gate/<ts>.json (records manifest_hash)
                     │ promote(report_id)
                     ▼
        ① report hash == current draft hash? (one edited byte voids it, gate.py:354-355)
        ② report has no fail?                 (gate.py:356-357)
        ③ server re-runs G1-G3, still no fail (gate.py:359-366; G4/G5 trust the report)
        ④ any warn ⇒ warnings_ack required    (gate.py:367-371)
                     ▼
        write production skills.yaml (.bak backup first) → loader reload() hot-reload
        → promotions.jsonl provenance record (promoted_by/report_id/version)
        (gate.py:373-388, 399-425)
```

`manifest_hash` is the first 16 hex chars of the sha1 over the canonical JSON of manifest+prompt+handler (`gate.py:90-105`); the report id is `<ts_ms>-<hash>`, binding the report to the content byte-for-byte. Rejections raise `GateError`, mapped to HTTP 409 semantics at the routing layer (`gate.py:330-331`).

### 4.5 Significant trade-offs

- **Why only G1-G3 are re-run while G4/G5 trust the report.** G1-G3 are pure static checks: free to re-run and essential against stale/forged reports. G4 smoke costs a real run, and its result is already hash-bound to the content; G5 is deterministic regex over the same content, so re-running adds nothing. Cost and anti-tamper needs are aligned precisely.
- **Why the required `reversal`/`blast_radius` lint lives in G3, not at load time.** The load-time hard gate (`manifest.py:113-133`) guards only the two rules that would break runtime invariants (inline, confirm:first); reversal/blast_radius are production-time commitments, and the correct enforcement point is the production entrance — by load time, the asset is already in production.
- **Why validate and promote are separate, with the report persisted.** The report is the shared artifact of the UI's five-gate cards and the CLI (`agent-os lab validate`, exit codes pass/warn=0, fail=2, `host/cli/main.py:336-340`), and it is promote's credential; persistence makes "a human saw this report" an auditable fact.
- **Why the Agent assistant has no promote tool.** The assistant can edit but cannot ship (SKILL-DEV §2.2); submission is always a human action carrying a persisted report — the gate defends not only against bad code but against a delegate acting on its own initiative.

## 5. Effects and Verification (Results)

**Unit tests** (`agent_os/tests/skills/test_gate.py`, 11 cases) cover:

- The five-gate decision matrix: a compliant L1 draft passes all gates (`test_all_pass_l1_draft`, asserting G4 is `skip` without an injected runner); G1 naming fail / description and version warn; three G2 fail shapes (parse error, bad schema, L2 parameter without type); four G3 fail shapes (inline hard gate, confirm:first, missing reversal/blast_radius) plus pass after remediation.
- G5 bilingual patterns: 7 inducement sentences (4 Chinese forms + 3 English forms) all fail; 7 positive phrasings (including "irreversible operations must be human-reviewed every time" and dry_run instructions) all pass.
- Hash anti-mismatch: editing one character of the prompt changes `manifest_hash`.
- promote orchestration: first promote appends `0.1.0`, second replaces with `0.1.1`; `.bak` exists; production is readable after reload; `promotions.jsonl` records promoted_by and gate_report_id; the rejection chain (stale report / fail report / unacknowledged warn) raises three `GateError` shapes; an explicit version overrides the bump.

**API level**: `tests/web/test_lab_api.py` — `test_validate_endpoint_report_shape`, `test_validate_fail_blocks_and_promote_rejections`, `test_promote_end_to_end_and_stale_report` — exercises the full HTTP path (report shape, fail blocking, stale-report 409). As of v1.0, the repository baseline is 812 Python tests + 24 frontend test files, all green (SKILL-DEV L5 implementation notes).

**Real example**: `agent_os/examples/workspace_janitor` — four skills across three tiers (L1 scan / L2 idempotent write + approve-run / L3 named deletion + dry_run) with real tools and zero mocks; `tests/examples/test_workspace_janitor.py` asserts the dry_run list matches the actual `destroyed` list and that L3 asks every time — a living specimen of the standard §5 test requirements.

**Ripple effects**: E3 (escalation lint hardening) had its reversal/blast_radius required-field lint land via G3 — the two plans converge at the gate (SKILL-DEV §4 dependency notes); the editor's derived-tier badge, the `/tier` endpoint, and the three-tier template library (passing G1-G3 at creation) all reuse the same derivation and gate; CLI and Web share drafts_root, so a CLI validation can be promoted directly from the Web.

## 6. Limitations and Boundaries

1. **The gate enforces "written", not "true".** The reversal/blast_radius check is "non-empty string" (gate.py:230-237); whether the mechanism actually exists relies on reversal drills and human PR review (the TIER-STANDARDS §7 checklist requires pointing at the code location). This part is deliberately not mechanized — and it is residual risk.
2. **Per-tier test requirements are not executed by the gate.** Idempotence, containment, TOCTOU, and injection-abuse tests are author/PR responsibilities; G4 smoke only runs the cases the draft ships and does not check whether these specialized tests exist. The gate is a necessary condition for quality, not a sufficient one.
3. **G4 depends on runner injection and degrades on embedded paths.** With `smoke_runner=None`, G4 reports skip (gate.py:241-242) and promote can proceed without quality evidence; a draft with no cases only warns, which a human can acknowledge away — the strength of the smoke line depends on how the host wires it.
4. **G5 is a regex pattern table, not semantic judgment.** Paraphrases, encodings, and inducement variants in other languages can bypass it; "err on the conservative side" is a policy that accepts false negatives to eliminate false positives on legitimate phrasing. It stops inducements brazenly written into assets, not carefully disguised supply-chain attacks (the deliberate non-goal left for M6 Provenance).
5. **promote supports only single-file skills.yaml**; the merge strategy for directory/multi-file skill sets is unimplemented (gate.py:406-410, deferred to L5); a code skill's handler source is not copied into the production entry — the dotted path is carried as-is.
6. **Unknown references count as the lowest tier.** Derivation defensively skips tools/sub-skills it cannot resolve, counting them as none (escalation.py:64-73, 100-102) — the "must not reference nonexistent skill/tool" check designed for G5 in SKILL-DEV §1.4 was never implemented; existence is ultimately enforced by the production load-time gate, and a draft may understate its tier in the meantime.
7. **Versioning is linear**: patch bump plus a single `.bak`, no branches, merges, or history (SKILL-DEV §5 states "git is the real version system"); `.bak` holds one generation, so after two consecutive promotes the earlier production state is not recoverable.
8. **The gate judges assets, not behavior.** Every runtime call of a gated L3 skill must still pass escalation review; the gate waives no runtime check — the two gates are in series, not substitutes.

## 7. References

- Standard: `docs/TIER-STANDARDS.md` (decision tree §1, per-tier norms §3-§5, checklist §7, anti-patterns §8)
- Upstream design: `docs/ESCALATION.md` §2.1/§2.3/§3.4 (tier semantics, tiered-standard outline, E3 phasing)
- Platform design: `docs/SKILL-DEV.md` §1.3-§1.4, §4 (L2/L3/L5 implementation notes), §5
- Implementation: `agent_os/src/agent_os/skills/gate.py` (five gates, hashing, promote), `agent_os/src/agent_os/skills/manifest.py:113-133` (load-time hard gates), `agent_os/src/agent_os/api/v1/escalation.py:54-140` (tier derivation), `agent_os/src/agent_os/host/cli/main.py:336-340` (`lab validate`)
- Tests: `agent_os/tests/skills/test_gate.py`, `agent_os/tests/web/test_lab_api.py`, `agent_os/tests/examples/test_workspace_janitor.py`
- Example: `agent_os/examples/workspace_janitor`

> Source-discrepancy note: the module docstrings of `gate.py` and `test_gate.py` both state "G4/G5 are skip placeholders this phase" — leftover text from phase L2. G4 (when a smoke runner is injected) and G5 are in fact implemented (L3/L5 ✅, SKILL-DEV §4); the code is authoritative. Separately, the "inputs example skeleton can be generated" item designed for G2 in SKILL-DEV §1.4 is not implemented in gate.py.
