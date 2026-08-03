# Skills: Registry, Loading & Inlining

> Chapter 02 · Status: registry / loading pipeline / inlining (merge v1) **implemented**; `register()` runtime write path and version-constraint solving are **contract-reserved (unimplemented)**; the capsule / directed / code frame-free tiers are **designed, not implemented** · Sources: `agent_os/src/agent_os/skills/{local_file,manifest,loader}.py`, `agent_os/src/agent_os/context/manager.py`, `agent_os/src/agent_os/api/v1/skills.py`, `docs/DESIGN.md` §6, `docs/SKILL-INLINING.md`

## 1. Overview

A Skill is Agent OS's uniform unit of execution — the "function" in the concept-mapping table: named, parameterized, composable, with prompt and code forms transparent to the kernel (`api/v1/skills.py:35-39`). The Skill Registry is its "dynamic linker": discovery, validation, dependency resolution, loading, hot reload, and the runtime write path (DESIGN.md §6). This chapter also covers the **inlining (merge) mechanism** built on top of the registry — a presentation-layer optimization in which a pure-specification skill's prompt is merged into the caller's SYSTEM message during context assembly, and the call itself disappears. The two share one manifest contract and one loading pipeline: all inlining legality gates run at load time, assembly and freezing live in the Context subsystem, and the kernel's call path is untouched.

## 2. Motivation & Background (Why)

**Why the registry is not in the kernel.** The microkernel criterion admits only three things: flow control, permission control, and IPC. The agent loop advances fine without a registry (the model can simply answer without invoking any skill), so the registry fails the F criterion and must live outside, behind the `SkillRegistry` Protocol in `api/v1` (`api/v1/skills.py:161-175`). This also honors "contract first, baseline replaceable": `LocalFileSkillRegistry` is the baseline, and Skill Lab's overlay registry reuses the same contract and the same frame-construction path (`build_child_frame` at `local_file.py:127-158` is shared by both, explicitly so that "two frame semantics cannot drift apart").

**Why validation happens at load time.** The skill dependency graph (`permissions.skills`) is declarative, so it can be fully built and statically analyzed at load time: cyclic dependencies fail the load, and every referenced tool/sub-skill must exist or loading is refused (DESIGN.md §6.1). The price of shifting errors left is reduced expressiveness — runtime recursion cannot be ruled out statically and is backstopped by `max_depth`.

**Why inlining exists.** The frame model charges every sub-skill call a fixed cost: a private FrameContext, its own agent loop, its own context assembly, plus at least one extra LLM round trip. For single-step transformation skills (normalize a date, extract a field), the machinery costs far more than the work. SKILL-INLINING.md §1.2 argues that **any optimization that keeps the "call" shape still pays at least one extra LLM round trip**; merge (pre-expansion) is the only tier that truly eliminates the call — the instructions join the caller's SYSTEM and the parent model performs the sub-task in its natural workflow. The C++ `inline` analogy is most exact here: not "a cheaper call" but "no call at all"; the cost corresponds too — code bloat, with instructions resident in SYSTEM and paid in tokens at every step.

**Why merge is also a capability gain.** Under frame isolation (DESIGN.md §2.3) only `input` enters a child frame, so sub-tasks that need the parent's conversation history ("summarize the points above", "check consistency with earlier text") are physically impossible as framed calls. Merge executes the instructions inside the parent frame, where that context is naturally visible. No other tier (capsule included) provides this.

**Semantic stance: honest pricing.** Merge is not a C++-style semantics-preserving optimization — the executor changes from the callee's own model preference to the parent model, so output necessarily differs. The design abandons byte-equivalence and substitutes an ablation switch (on/off A/B comparison) plus load-time purity gates that narrow applicability to pure-specification skills where semantic drift has the least room (SKILL-INLINING.md §1.3).

## 3. Problem Statement (What It Solves)

1. **Heavy machinery for tiny skills.** A single-step skill that normalizes dates to ISO 8601 pays, per framed call: one LLM decision + one full child frame (frame object, context assembly, outputs validation, collapse on pop). The actual work is rewriting one sentence.
2. **Context-reader skills are unreachable.** "Check consistency with earlier text" needs the parent's conversation history; under isolation a child frame's messages are exactly `[USER(input)]` (`local_file.py:149-157`) — the history is physically absent. This capability class does not exist in framed form.
3. **Version drift between hot reload and running frames.** If every assembly step re-reads skill prompts from the registry, a hot reload changes a running frame's SYSTEM mid-flight and breaks the prefix cache (DESIGN.md §7.4 invariant 5); a resumed frame would also rebuild a SYSTEM that differs from the pre-crash bytes. The problem is sharper for inline sections because they come from *other* skills' manifests.
4. **Configuration errors surfacing at runtime.** Cyclic dependencies and references to nonexistent sub-skills, if not caught at load time, fail a run halfway through with a scene that is hard to reconstruct.
5. **The injection surface inlining opens.** If a privileged skill could be inlined, its instructions would merge into a lower-tier frame — "bypassing the isolation wall and moving into someone else's house," breaking the clean-context invariant (ESCALATION.md §3.4, verbatim). Inlining must be gated jointly with the trust model.

## 4. Design & Mechanism (How)

### 4.1 Contract: manifest and registry protocol

`SkillManifest` mirrors DESIGN.md §2.1 field for field: `name/version/kind/description/inputs/outputs/verifier/permissions/model/context_policy/limits/entry/prompt/handler/logic/inline/trust` (`api/v1/skills.py:91-114`). `description` is required to be written as a **routing rule** ("Use when / Do not use when" plus negative examples), because the parent model relies on it to decide whether to call — enforced as a load-time lint that warns without blocking (`manifest.py:151-154`). The `SkillRegistry` Protocol has four methods — `get / visible_to / make_frame / register` (`api/v1/skills.py:161-175`) — with `register()`'s signature on the §14.1 freeze list (the self-evolution supply side; see §6).

### 4.2 The loading pipeline

```
discover ─→ parse ─→ validate ─→ resolve deps ─→ materialize ─→ publish
(3 source   (YAML)   (hard gates   (Kahn topo)    (Skill        (queryable)
 forms)              + lints)                      objects)
```

- **Three source forms**: a single file / a directory (all `*.yaml` merged in filename order) / an explicit path list (merged in given order) (`local_file.py:173-180`); cross-file name dedup and dependency checks follow the same logic as a single file (`local_file.py:202-214`).
- **Parse**: `yaml.safe_load` → field-by-field manifest construction; illegal `trust.confirm` values are rejected at load time (`manifest.py:71-84`).
- **Validate**: hard gates raise `SkillLoadError`; lints return warnings without blocking. Beyond the generic lints, `inline: true` triggers dedicated gates (see 4.4). The assembly point (KernelBuilder) additionally passes the derived tier into escalation gates: **derived tier ≥L2 forbids inline**, and **L3 forbids confirm: first** (`manifest.py:113-133`, call site `runtime/builder.py:190`; Skill Lab's submission gate G3 reuses the same check, `skills/gate.py:222-224`).
- **Dependency resolution**: Kahn topological sort (explicit self-references are legal and ignored), cycles raise an error naming the nodes involved (`local_file.py:105-124`); dependencies are checked for existence only — **no version-constraint solving** (single version; see §6). During migration, legacy flat names resolve to dotted hierarchical names via `LEGACY_SKILL_ALIASES` (~48 entries) with a warning (`local_file.py:42-101`).
- **Materialize**: prompt skills take their instruction body; code-skill handlers are **lazily imported** — no import at load time, the dotted path is resolved and checked for being a coroutine function on first call (`loader.py:18-47`); prompt rendering uses `str.format`, and bare `{}` literals fail loudly (`loader.py:50-55`).
- **Hot reload**: manual `reload()`; unchanged mtime returns False. On change the full pipeline reruns, and **a failure keeps the old table while raising — usable state is never destroyed**; on success, "new frames get the new version, running frames keep the old Skill object pinned" (`local_file.py:231-243`).

### 4.3 Presentation: pseudo-tools and child-frame construction

Sub-skills appear to the parent-frame LLM as pseudo-tools `skill.<name>` whose schema is the callee's `inputs` (`local_file.py:262-277`); the kernel intercepts these by prefix at dispatch time and pushes a frame (`kernel/runner.py:550` — both `skill.` and `skill__` prefixes are intercepted; see the naming note at the end). `make_frame`/`build_child_frame` does three things: hard-validates call arguments against the callee's `inputs` jsonschema (failures become error observations in the parent), sets `depth+1`, and **inherits the principal verbatim** (escalation changes side-effect permission, not identity); the frame input enters the private context as a first USER message with `Source.PARENT_INPUT` (`local_file.py:127-158`).

### 4.4 Inlining (merge) v1: gates, assembly, freezing

**Declaration and gates.** The callee's manifest adds `inline: true` (default false, an additive extension; `api/v1/skills.py:113`); callers change nothing — the whitelist declaration stays. Three load-time hard gates (`manifest.py:160-185`): `kind: prompt` only; **purity** — `permissions.tools/skills/blackboard` must all be empty (once merged, such permissions cannot execute, so declaring them is a contradiction); non-empty `prompt`. Four lints: `{` placeholders in the prompt (merge has no discrete input to render), prompt >500 chars (`INLINE_PROMPT_MAX_CHARS`, `manifest.py:107`), declared `inputs/outputs/model/context_policy` (no runtime effect in merge; documentation only), declared `verifier` (its semantics bind to frame-pop arbitration; merge has no frame). One more caller-side lint: more than 3 merge dependencies warns (`INLINE_DEPS_MAX`, `local_file.py:217-224`) — the counterpart of a C++ compiler refusing to inline large functions.

**Assembly (the main battleground is ContextManager; the runner is untouched).**

```
Caller SYSTEM (inline on):
┌────────────────────────────────────────────┐
│ render_prompt(caller.prompt, input)        │  ← rendered normally
│                                            │
│ ## 内联能力(直接运用,无需调用)           │  ← fixed header (byte-stable)
│ ### date_style@1.0.0                       │
│ <prompt verbatim, NOT render_prompt'ed>    │  ← in permissions.skills order
└────────────────────────────────────────────┘
Pseudo-tool surface: skill.<inlined> filtered out; non-inline skills unchanged
Branch: config.inline == "off" → no assembly, no filtering; plain framed call
```

Implemented as `_inline_caps` (`context/manager.py:154-201`): at a frame's **first** build, the section is assembled from current registry values and the snapshot is frozen into `frame.context.working["_inline_caps"]` (with `text/hidden/skills` entries); later builds reuse the snapshot verbatim. A one-shot `post:context.inline` signal is emitted with each skill's name/version/chars (`api/v1/signals.py:85`). One snapshot solves three problems at once: **prefix stability** (SYSTEM byte-identical across steps), **hot-reload semantics** (running frames pin the old version), and **resume determinism** (working serializes with the frame into the checkpoint, so a restored frame rebuilds byte-identically). The ablation branch lives in ContextManager (which holds the RunConfig); the registry is unaware of it (`manager.py:163-164`).

**Degradation paths (all special-case-free).** With inline on, the model cannot see the pseudo-tool, but if it hallucinates a `skill.X` call anyway: the skill is still on the whitelist, so the kernel frames and executes it normally — correct behavior, harmless degradation. `ctx.invoke` (programmatic call) and `spawn` (background frame) likewise frame as usual: merge changes only the **LLM-facing presentation**, never a programmatic path (SKILL-INLINING.md §4.4/§8).

**Key trade-offs (rationale per SKILL-INLINING.md §14):**

| Option | Why not |
|---|---|
| capsule first | Saves frame overhead but not the call; larger landing surface (touches runner + replay); cannot provide the context-reader gain |
| Load-time macro expansion into the caller's prompt file | Freezing expansion onto disk breaks hot reload and version pinning; the ablation switch could no longer compare |
| Inline section as USER/INJECTED message | It would enter `context.messages` and become evictable under compression — the capability could "silently vanish mid-run"; SYSTEM is naturally eviction-free and semantically correct |
| Automatic inline heuristics | Unpredictable behavior; v1 is explicit-declaration only |

## 5. Effects & Verification (Results)

**Test evidence.** Anchor tests: `tests/context/test_inline_merge.py` (15 test functions, some parametrized) and `tests/skills/test_loader.py` (15) — **35 cases pass** together (0.60s on this run). Key assertions:

- **Gates and lints**: code skill / non-empty permissions / empty prompt → `SkillLoadError`; placeholders / over-length / dead fields / verifier → warn, never block (`test_inline_merge.py:151-186`);
- **Assembly**: with inline on, SYSTEM contains the header and `### test.tidy@1.0.0`, `skill.test.tidy` disappears from the tool surface while `skill.test.helper` remains; off fully degrades (`194-216`);
- **In-frame freezing**: adjacent builds of one frame are byte-identical; after hot reload (mtime bump) the running frame is unchanged while new frames pick up the new version; simulated resume (deep-copied working + updated registry) rebuilds byte-identically (`230-273`);
- **Signal**: `post:context.inline` fires exactly once at snapshot assembly; frames without inline dependencies never emit it (`281-295`);
- **A/B (through the real kernel)**: on produces zero child frames (only 1 `post:frame.push`, the root), off really frames (2 pushes) — output equivalence is not asserted; the call-shape difference is (`376-388`);
- **Degradation**: hallucinated call / `ctx.invoke` / `spawn` of a merge skill all frame and execute correctly (`391-419`);
- **Zero-change replay**: a run containing merge skills replays via the CLI with an empty diff (`result_equal` and `signals_equal` both true), directly verifying the design doc's §6 claim (`427-449`).

**Real example.** `skillsets/inline_demo/skills.yaml`: `demo.date_style` (an `inline: true` date-formatting specification) is listed in `demo.report_writer`'s `permissions.skills`, and the caller benefits with zero changes.

**Ripple effects.** Inlining turned the clean-context invariant from a runtime property into a load-time gate: "derived tier ≥L2 forbids inline" is enforced both at the assembly point (`runtime/builder.py:190`) and in Skill Lab's submission gate G3 (`skills/gate.py:222-224`), so the escalation system's isolation promise cannot be bypassed by a presentation-layer optimization (ESCALATION.md §3.4).

## 6. Limitations & Boundaries

1. **No validation, no accounting, no observability.** A merge skill's `inputs/outputs` get no runtime hard validation; cost folds into the parent's steps with no per-skill usage attribution; "the inlined capability didn't work" has no runtime anchor to investigate — no frame, no signal, no validation. The only recourse is ablation comparison and inspecting the SYSTEM snapshot (SKILL-INLINING.md §7). This cost is explicitly accepted, and it is why the purity gates narrow applicability to "short specifications."
2. **No semantic equivalence.** The executor is the parent model; the callee's `model.prefer` is void. on/off makes no output-equivalence promise; ablation is for debugging and offline quality/cost evaluation, not CI equivalence assertions (SKILL-INLINING.md §9).
3. **Version-constraint solving is unimplemented.** DESIGN.md §6.1's `<namespace>:<name>@<semver>` with `^`/`~` semantics is contract; the baseline runs single-version and checks dependency existence only (`local_file.py:3-4`). Multi-version coexistence and constraint resolution do not exist yet.
4. **`register()` is unimplemented (M6).** The runtime write path (trust pipeline for agent-authored skills) is a frozen signature that raises `NotImplementedError` (`local_file.py:287-293`); the v1 substitute is writing files plus `reload()`.
5. **No arbitration for instruction conflicts.** When multiple merge skills (or the caller's own prompt) contradict each other, nothing detects or arbitrates it — the defense is the count lint plus code review (SKILL-INLINING.md §15, open question 1).
6. **Bloat thresholds are educated guesses.** 500 chars / 3 dependencies are not derived from token-estimator measurements; the design doc itself calls them arbitrary (SKILL-INLINING.md §15, open question 3).
7. **The price of purity: no composition.** Merge skills cannot declare any skills dependency — no inline chains, no transitive expansion. Anything slightly more complex (needing even one tool call) must fall back to framed form.
8. **The flip side of snapshot-as-truth.** Running frames pin the old version, so fixing a wrong inlined instruction does not reach frames already running inside a long run. This is the direct cost of prefix stability and resume determinism — not a defect, but operators should know it.
9. **Hidden costs of degradation paths.** A hallucinated call is harmless (correct behavior) but pays for a full unexpected frame execution; the spawn path does not read the inline flag and frames a merge skill as usual — semantically this is "documented no-op" rather than an error, so misuse gets no feedback.
10. **Coverage gaps.** `MinimalContextManager` does not support inlining (the M0 baseline stays minimal); the Web frame-detail inline badge is an optional follow-up, not built (SKILL-INLINING.md §11).

## 7. References

- Design docs: `docs/DESIGN.md` (§2.1 contract, §3.3 pseudo-tools, §6 Skill Registry, §7.4 prefix invariants), `docs/SKILL-INLINING.md` (full merge design), `docs/ESCALATION.md` (§3.4 inline hard gate), `docs/NAMING.md` (§4 legacy name mapping)
- Contracts: `agent_os/src/agent_os/api/v1/skills.py` (manifest / registry protocol), `api/v1/run.py:36` (ablation), `api/v1/signals.py:85` (`post:context.inline`)
- Implementation: `agent_os/src/agent_os/skills/local_file.py` (registry), `skills/manifest.py` (parse/gates/lints), `skills/loader.py` (materialize/render), `context/manager.py:154-201` (inline assembly and freezing), `runtime/builder.py:190` / `skills/gate.py:222-224` (tier-gate call sites), `kernel/runner.py:550` (pseudo-tool interception)
- Tests: `agent_os/tests/context/test_inline_merge.py` (15 cases), `agent_os/tests/skills/test_loader.py` (15 cases)
- Example: `skillsets/inline_demo/skills.yaml`

---

**Source discrepancies (code wins)**: ① Pseudo-tool naming — DESIGN.md §3.3 and SKILL-INLINING.md prose write `skill__<name>`, but the code generates `skill.<name>` (`local_file.py:272`), and the runner intercepts both prefixes (`runner.py:550, 844-845`); this chapter follows the code. ② DESIGN.md §6.3 describes directory packages as a future `DirectorySkillSource`, but the code already supports the directory form (sorted multi-`*.yaml` merge, `local_file.py:173-180`). ③ Version-constraint solving in DESIGN.md §6.1 is a design promise; the code explicitly does not do it (`local_file.py:3`).
