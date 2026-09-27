# Context: Assembly, Compression & Prefix Stability

> Chapter: 05 · Status: implemented (including the spill/summarize/hierarchical chain; narrate is not implemented — the multimodal contract is still open) · Basis: `docs/DESIGN.md` §7, `agent_os/src/agent_os/context/manager.py`, `agent_os/src/agent_os/context/rolling_window.py`, `agent_os/src/agent_os/context/spill.py`, `agent_os/src/agent_os/context/summarize.py`, `agent_os/src/agent_os/context/chain.py`, `agent_os/src/agent_os/context/estimator.py`, `agent_os/src/agent_os/api/v1/context.py`

## 1. Overview

The Context subsystem is the sole owner of the frame context (`FrameContext`). Before every LLM call it **assembles** the `ChatRequest` from the skill prompt, frame messages, visible tool schemas, and kernel bookkeeping; when the context exceeds its soft cap it **compresses** it according to a strategy; and it guards prefix-cache hit rate through a hard invariant of byte-identical request prefixes. In the OS mapping it plays the role of memory management / GC (executive summary §2.1). The kernel knows nothing about assembly internals — the agent loop simply calls `maintain` and then `build` at a fixed point each step (`agent_os/src/agent_os/kernel/runner.py:491-493`); all policy lives outside the kernel.

## 2. Motivation and Background (Why)

**Why assembly and compression must be one subsystem.** The two duties look opposed — one puts things into the request, the other removes things from the context — but they are two faces of the same invariant: compression is the biggest destroyer of the prefix cache, assembly is the only maintainer of prefix stability, and splitting them across two subsystems guarantees conflicting accounting (`docs/DESIGN.md` §7, opening paragraph). Hence the `ContextManager` protocol puts `build` and `maintain` in one interface (`api/v1/context.py:20-29`).

**Why this is not in the kernel.** By the microkernel criterion (executive summary §2.2), assembly and compression are neither flow control, nor a permission/veto arbitration point, nor the sole public channel between subsystems — they are **policy**. The corollary "the kernel owns the loop, the Skill owns the policy" is realized here as: the kernel calls the protocol at fixed points; the estimation calculus, the compression algorithm, and the status-bar wording are all replaceable.

**Why prefix stability is a hard invariant, not an optimization.** LLM billing and latency scale with prompt tokens, and prefix-cache hits determine the shape of a long run's cost curve. An implementation that reshuffles tool schemas every step, or injects changing data into SYSTEM, invalidates the entire cache and degrades billing from "pay the delta" to "pay the full prompt every step."

**Why injected state trusts only kernel bookkeeping.** The status bar is trusted unconditionally by the model — it is the model's only channel for perceiving budget and step count. If its data came from tool content, a single poisoning (a tool result claiming "budget is fine") could steer the run (§7.3).

## 3. Problem Statement (What It Solves)

1. **Monotonically growing context vs. a finite window**: the agent loop appends assistant messages and tool results every step, so any long run eventually hits the model window. Reproduced in tests: 40 atomic groups × 400 chars ≈ 16k+ tokens against a manifest cap of 4000 (`tests/context/test_context.py:244-260`).
2. **Naive truncation breaks the pairing protocol**: an assistant message carrying `tool_calls` and its tool results are a pairing constraint in provider APIs; cutting in the middle produces orphan tool results that the API rejects outright.
3. **Rebuilding the request every step kills the prefix cache**: any jitter in the ordering or serialization of SYSTEM or tool schemas turns that step into full-price billing.
4. **Hot reload punches through a live frame's prefix**: if the inline-capability section were re-read from the registry every step, one hot reload would change the SYSTEM of a running frame mid-flight, taking down both prefix caching and resume determinism (`docs/SKILL-INLINING.md` §4.2).
5. **The model is blind to budget and step count**: without readings there is no self-convergence — but bare readings do not change behavior; "20% budget left" only works when paired with an operating strategy (§7.3).
6. **No external handle on a bloated context**: when a sidecar (supervisor) sees a frame's context spiraling, it needs a forced-compression channel that does not wait for the cap to trip (§7.1, external trigger).

## 4. Design and Mechanism (How)

### 4.1 Contract Surface

`api/v1/context.py` freezes four symbols: the `ContextManager` protocol (`build`/`maintain`, :20-29), the `Compressor` protocol (`name` + `compress(ctx, target_tokens, svc)`, :33-40), `CompressionReport` (evicted / before / after / cache_invalidation_estimate / marker, :44-54), and `KernelServices` (estimator / providers / blob, :58-67). New strategies are meant to register through the `agent_os.compressors` entry point — contract first, baseline replaceable. The group is now declared in `pyproject.toml` and loaded by the builder's `_compressor_plugins()` (2026-09-28: classes are instantiated with no args, instances used as-is, plugins override built-ins by `.name`, broken entry points are skipped with a warning); custom mode names are not available in manifests (the mode table is four built-ins plus off), so third-party strategies override built-ins by name.

### 4.2 build: The Assembly Pipeline

```
┌─ ChatRequest ────────────────────────────────────────────────┐
│ SYSTEM  render_prompt(skill.prompt, frame.input)             │
│         + inline-capability section (snapshot frozen on the  │
│           frame's first build, see 4.3)                      │
│ msgs    frame.context.messages (frame-private, verbatim)     │
│ USER    status meta-message (ephemeral, meta={"kind":"status"}│
│         — never written back into the frame)                 │
│ tools   whitelisted tool schemas                             │
│         + python_orchestrate (declared AND RunConfig.on)     │
│         + ask_supervisor     (declared AND channel installed)│
│         + skill.<name> pseudo-tools (minus inline-hidden)    │
│ model   manifest.model.prefer[0] → RunConfig.model           │
└──────────────────────────────────────────────────────────────┘
```

Key decision logic (`context/manager.py:99-152`):

- Both pseudo-tools are **absent from the tool registry** (the kernel intercepts them at dispatch); build adds them to the visible surface per "declaration + ablation/assembly switch" (:112-126). The trade-off for `ask_supervisor` is written into the comment: without a supervisor channel installed, the model would only get `not_found` for calling it, so it is better not to show it at all (:118-119).
- Model resolution order: manifest `prefer[0]` wins, falling back to `RunConfig.model`; same for temperature (:133-141). Skill authors can pin a model; the host keeps the fallback.
- The status message is only **appended at the request tail** and never written back to `frame.context.messages` (:143-146) — dynamic data goes at the end, the static prefix stays untouched. That is how invariant 5 is implemented.

### 4.3 Inline-Capability Section: Snapshot Frozen on First build

Skills declared `inline: true` (pure manual skills) are never framed; their prompts merge into the caller's SYSTEM. Assembly rules (`_inline_caps`, manager.py:154-201): the ablation setting `inline == "off"` returns `None` outright (merge skills degrade to ordinary framed calls); otherwise, on the frame's **first** build, the section is assembled from current registry values and the snapshot is stored in `frame.context.working["_inline_caps"]` (:46, :194); every later build reuses it. The snapshot rides along in the checkpoint, solving three problems at once: prefix stability (invariant 5), hot reload semantics ("running frames pin the old version"), and byte-identical inline sections after resume (`docs/SKILL-INLINING.md` §4.2). Inlined skills are simultaneously hidden from the pseudo-tool surface (:127-132), so the model never attempts to call a call that does not exist.

### 4.4 State Injection (Status Bar)

Each build appends a key-value meta-message with `role=USER, source=INJECTED, meta={"kind": "status"}` (`_status_message`, manager.py:203-224): bare readings (step / tokens / cost / budget_remaining) plus an operating strategy in `hint`. The decision logic is one line: when remaining budget drops below `LOW_BUDGET_RATIO = 0.2` (:35), the hint switches to "converge on a directly deliverable plan"; otherwise "proceed normally." When the run has TODOs, a progress summary is merged in (`_todo_status`, :226-265), again sourced only from kernel bookkeeping. The deliberate trade-off: bare readings do not change behavior, readings plus strategy do (§7.3) — the hint is not decoration, it is part of the contract.

### 4.5 maintain: Triggering and the Compression Path

```
Top of each loop iteration (runner.py:490-493):
  _force_compress flag popped from working? ──yes──▶ force_compress (cap ignored)
  maintain(frame):                                   │
    estimate = estimator.estimate(messages)          │
    cap = manifest.context_policy.max_tokens         │
          or default 128_000                         │
    compression=="off" (RunConfig or manifest)? ──yes──▶ short-circuit
    estimate ≤ cap or no compressor? ──yes──▶ update estimate only
    otherwise: pre:compress signal (first non-Allow verdict skips this round)
          → compressor.compress(ctx, int(cap*0.8), svc)   # strategy chain per _MODE_CHAINS
          → post:compress signal (full report fields + strategy)
          → re-estimated after > cap → raise ContextOverflowError (frame fails upward)
```

`force_compress` is the §7.1 external trigger: a sidecar emits `ForceCompress`, RunControl sets a flag in the frame's working memory (`kernel/control.py:23-24`), and the runner consumes it before maintain (runner.py:490) — it still travels the same `_compress` path (manager.py:360-424), just with `forced=True` in the pre-signal payload; a pre veto applies to this path too (§7.4 invariant 4). The ablation setting short-circuits forced triggers too (:325).

### 4.6 RollingWindowCompressor: Eviction by Atomic Group

The baseline compressor (rolling_window.py; the chain's `truncate` strategy, `name = "truncate"`, :118) is a pure three-step algorithm with no LLM or blob dependency:

1. **Grouping** (`atomic_groups`, :34-61): an assistant message carrying `tool_calls` is bound with all **consecutive** following TOOL messages whose `tool_call_id` matches, forming one atomic group; every other message forms its own group. Pairing atomicity (invariant 2) is thus guaranteed **structurally**, not by after-the-fact checking — the group is the smallest eviction unit and cannot be split.
2. **Whole-group eviction** (`select_eviction_groups`, :70-93): groups containing a pinned message (`m.meta["id"] in ctx.pinned`) are never evicted; eviction starts from the oldest non-pinned group until `estimate ≤ target`, but the **last non-pinned group is always kept** — evicting everything would strip the frame of all recent context, and that group is the fallback target of hard truncation.
3. **Hard-truncation fallback** (`hard_truncate_group`, :96-111): if the context still exceeds the target (a single group is too large), the earliest surviving non-pinned group is truncated character by character, message by message, tagged `[truncated]`; only content is touched, structure is untouched, pairing survives. Content shorter than the tag itself is skipped — "truncating it costs more than keeping it" (:106-107).

The report carries `marker = "[COMPRESSED]"` (:31) — the idempotency mark shared by all strategies in the chain (see §6). Since 2026-09-28 the three functions live at module level for reuse (summarize imports them; `RollingWindowCompressor` keeps method aliases), with the class name and behavior byte-identical.

### 4.7 The Estimator: One Calculus

`TokenEstimator` (estimator.py): char/4 rough estimate + a fixed 4-token overhead per message (:14) + JSON-length accounting for tool_call arguments, with a global `calibration` factor. It shares one calculus with ProviderManager (§4.2) — compression watermarks and billing estimates can never disagree. `per_provider_factor` reserves the entry point for per-vendor tokenizer deviations (:38-43).

### 4.8 Strategy Chain: Designed Panorama vs. Implemented Landing

| §7.2 strategy | Status | Notes |
|---|---|---|
| `collapse_child` | implemented (structural) | a popped child frame's transcript never enters the parent — only the return value (the fib test asserts "the entire child transcript is folded", `tests/kernel/test_fib_slice.py:112`) |
| `truncate` (rolling window) | implemented | section 4.6 of this chapter; registered as `name = "truncate"` |
| `spill` | implemented | `context/spill.py`: non-pinned TOOL messages over the threshold (default 4000 chars, tunable via `[context]`) are moved into the blob store and replaced in place by a frozen substitution string (`[SPILLED]` + original byte count + `blob://<run_id>/<sha>` ref + 500-char head/tail + blob_get pagination hint); no message is deleted; no-op when svc.blob/run_id is missing |
| `summarize` | implemented | `context/summarize.py`: the evicted span is summarized via `svc.providers.chat` with a cheap-tier model into a `[COMPRESSED]` compact note (context-aware: frame task spec + pinned constraints; retention contract: decisions / modified files / verification status / TODOs / identifiers verbatim); after 3 consecutive failures (default) — or with no providers/model — it degrades to plain truncation marked `[COMPRESSED:truncate]` |
| `hierarchical` (chain, manifest default) | implemented | `ChainCompressor` (chain.py): ordered spill → summarize chain, re-estimating after each stage and short-circuiting once under target; the mode table `_MODE_CHAINS` (manager.py:55-60) with manifest `compress` taking precedence over RunConfig; `KernelServices.providers` is now wired (`_svc(frame)` injects the ProviderManager and run_id, manager.py:437-448) |
| `narrate` | not implemented | the multimodal contract is open (`Message.content` is a str; there is nothing to narrate) |

## 5. Effects and Verification (What It Achieves)

Test baseline: `agent_os/tests/context/` — **63 tests, all green** (`pytest tests/context -q`; 2026-09-28: test_context.py gained 10, plus new test_spill.py 8 / test_summarize.py 11 / test_chain.py 3, the rest being test_inline_merge.py and parametrized expansions). Key evidence:

- **Invariant property tests** (hypothesis, `test_context.py:185-209`): over random message histories (1-24 segments, 0-3 tool_calls per group, 50-600 chars per message, 40 examples), compression preserves pairing, keeps pinned messages, and never increases the estimate — the first three §7.4 invariants are directly measurable.
- **Eviction order** (:132-153): oldest groups leave first, newest groups stay, the pinned system message holds its position; **hard truncation of an oversized single group** (:167-184) carries the `[truncated]` tag with pairing intact.
- **Triggering and short-circuit** (:244-282): an over-cap context is compressed to `int(cap × target_ratio)` (within one group's granularity), with exactly one `pre/post:compress` pair; under `compression: "off"` the message count is unchanged and no signal fires.
- **force_compress** (:329-356): runs once even below cap, with `forced is True` in the pre payload; still short-circuits under ablation. With no compressor, maintain silently skips compression but updates the estimate (:357-375).
- **State injection** (:283-311): the status message sits at the request tail with `kind=status` and is ephemeral (never in the frame context); at 5% remaining budget the hint contains "converge."
- **Prefix stability** (:312-328): across two consecutive builds, `(role, content)` of all non-status messages is byte-identical; end to end, the fib slice test asserts byte-identical SYSTEM between adjacent requests of the same frame (`tests/kernel/test_fib_slice.py:114-115`).
- **Inline snapshots**: hot reload leaves a running frame's SYSTEM unchanged while new frames get the new version (`test_inline_merge.py:230`); the snapshot freezes into `working` and resumes deterministically (:254); `post:context.inline` fires exactly once (:281).
- **Chain strategies** (test_spill.py / test_summarize.py / test_chain.py, 2026-09-28): spill's frozen substitution string is idempotent and deletes nothing (evicted=0), and it is a no-op without blob/run_id; summarize honors the retention contract (identifiers verbatim) and degrades to `[COMPRESSED:truncate]` on breaker-open or missing providers; the chain re-estimates per stage and short-circuits once under target; a first non-Allow verdict on `pre:compress` skips the round, and still-over-cap after the whole chain raises `ContextOverflowError`; compression LLM usage is accounted at frame/run level with a supplementary `post:llm.response` (`"source": "compress"`, `tests/kernel/test_compress_usage.py`).

Ripple effects: invariant 5 shaped the inlining subsystem in reverse — "freeze into `working` on first build" is not an inlining invention but a consequence of prefix stability (SKILL-INLINING.md §4.2); the three-service shape of `KernelServices` pre-wired hooks for spill/summarize, which have now been consumed (estimator/providers/blob all wired) without touching the contract.

## 6. Limitations and Boundaries

1. **A bare rolling window (the truncate-only mode) is a known loop inducer**: dropping early tool results makes the model re-issue the dropped calls. Both the source and §7.6 carry a positioning warning — it is only the middle-layer base of the hierarchical chain, whose tail must be summarize or spill, and **must not be read as the recommended practice**. The chain is implemented now (2026-09-28, §4.8): production shapes should select the spill/summarize/hierarchical chains via the manifest `compress` mode, with `truncate`-only kept as baseline and ablation reference.
2. **Recoverability of evicted information is tiered**: spill is recoverable — the content sits in the blob store and the substitution string carries a `blob://` ref plus a blob_get pagination hint; summarize is lossy — the original span is gone and only the retention-contract essentials survive in the compact note; plain truncate eviction remains unrecoverable.
3. **char/4 is a rough estimate**: deviations are significant for code-, CJK-, or JSON-heavy contexts, so cap decisions may fire early or late; `per_provider_factor` always returns 1.0 (estimator.py:44) as a placeholder. The "multimodal calculus (image formula by resolution)" mentioned in §7.6 has no implemented branch in the estimator — per the code-wins rule, multimodal estimation is not landed.
4. **The §7.1 hard-cap tail path is implemented; the "near the model window" tier remains open**: when the re-estimate after the whole chain is still `after > cap`, a `ContextOverflowError` is raised (manager.py:63/:421-424) and the frame fails upward; but there is still a single cap tier (manifest `max_tokens` or default 128_000, manager.py:111/:355-357) — the model-window tier is unimplemented because the model window is unknowable, and hitting the real window is still backstopped by provider errors.
5. **Only the "short trajectory, replace each turn" branch of the status bar exists**: §7.3 also designs a "long trajectory, persistent append (fully cache-preserving)" branch; the code rebuilds the status message each step and appends it ephemerally, which is equivalent to per-turn replacement. The designed "current time, tool-call count" fields are also absent from the status lines (manager.py:209-215). The code wins.
6. **The hint policy is hardcoded**: the 20% threshold (`LOW_BUDGET_RATIO`) and both wordings are fixed in the manager; a skill cannot tune the convergence strategy to its task shape, and the status bar's trust model (unconditional model trust) amplifies the reach of this fixed policy.
7. **The `[COMPRESSED]` marker still has no independent consumer**: the chain exists now and the compressors are self-idempotent (spill skips messages already containing `[SPILLED]`, spill.py:63; summarize no-ops when the estimate is under target), but nothing outside the chain reads the marker (status bar, debug views, etc.) — the report's marker is still just a record (rolling_window.py:31).
8. **Compression granularity is "before each step's build"**: nothing intervenes mid-step (e.g., when one tool call writes back a huge result); the earliest handling is the next step's maintain, and in between the over-cap context is persisted verbatim into the checkpoint.

## 7. References

- Design docs: `docs/DESIGN.md` §7 (Context subsystem), §4.2 (single token calculus), §3.1 (agent loop); `docs/SKILL-INLINING.md` §4 (assembly / freezing / ablation); `docs/SUPERVISOR.md` §2.1 (ask_supervisor presentation); `docs/CODE-ORCHESTRATION.md` §2.1 (orchestrate pseudo-tool)
- Contracts: `agent_os/src/agent_os/api/v1/context.py`, `agent_os/src/agent_os/api/v1/frames.py` (FrameContext/Usage)
- Implementation: `agent_os/src/agent_os/context/manager.py`, `agent_os/src/agent_os/context/rolling_window.py`, `agent_os/src/agent_os/context/spill.py`, `agent_os/src/agent_os/context/summarize.py`, `agent_os/src/agent_os/context/chain.py`, `agent_os/src/agent_os/context/estimator.py`; call sites `agent_os/src/agent_os/kernel/runner.py:490-493`, `agent_os/src/agent_os/kernel/control.py:23-24`
- Tests: `agent_os/tests/context/test_context.py`, `agent_os/tests/context/test_inline_merge.py`, `agent_os/tests/context/test_spill.py`, `agent_os/tests/context/test_summarize.py`, `agent_os/tests/context/test_chain.py`, `agent_os/tests/kernel/test_fib_slice.py`, `agent_os/tests/kernel/test_compress_usage.py`
