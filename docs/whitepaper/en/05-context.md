# Context: Assembly, Compression & Prefix Stability

> Chapter: 05 · Status: implemented (baseline; the higher-order chain strategies spill/narrate/summarize/hierarchical are designed but not yet implemented) · Basis: `docs/DESIGN.md` §7, `agent_os/src/agent_os/context/manager.py`, `agent_os/src/agent_os/context/rolling_window.py`, `agent_os/src/agent_os/context/estimator.py`, `agent_os/src/agent_os/api/v1/context.py`

## 1. Overview

The Context subsystem is the sole owner of the frame context (`FrameContext`). Before every LLM call it **assembles** the `ChatRequest` from the skill prompt, frame messages, visible tool schemas, and kernel bookkeeping; when the context exceeds its soft cap it **compresses** it according to a strategy; and it guards prefix-cache hit rate through a hard invariant of byte-identical request prefixes. In the OS mapping it plays the role of memory management / GC (executive summary §2.1). The kernel knows nothing about assembly internals — the agent loop simply calls `maintain` and then `build` at a fixed point each step (`agent_os/src/agent_os/kernel/runner.py:409-410`); all policy lives outside the kernel.

## 2. Motivation and Background (Why)

**Why assembly and compression must be one subsystem.** The two duties look opposed — one puts things into the request, the other removes things from the context — but they are two faces of the same invariant: compression is the biggest destroyer of the prefix cache, assembly is the only maintainer of prefix stability, and splitting them across two subsystems guarantees conflicting accounting (`docs/DESIGN.md` §7, opening paragraph). Hence the `ContextManager` protocol puts `build` and `maintain` in one interface (`api/v1/context.py:20-29`).

**Why this is not in the kernel.** By the microkernel criterion (executive summary §2.2), assembly and compression are neither flow control, nor a permission/veto arbitration point, nor the sole public channel between subsystems — they are **policy**. The corollary "the kernel owns the loop, the Skill owns the policy" is realized here as: the kernel calls the protocol at fixed points; the estimation calculus, the compression algorithm, and the status-bar wording are all replaceable.

**Why prefix stability is a hard invariant, not an optimization.** LLM billing and latency scale with prompt tokens, and prefix-cache hits determine the shape of a long run's cost curve. An implementation that reshuffles tool schemas every step, or injects changing data into SYSTEM, invalidates the entire cache and degrades billing from "pay the delta" to "pay the full prompt every step."

**Why injected state trusts only kernel bookkeeping.** The status bar is trusted unconditionally by the model — it is the model's only channel for perceiving budget and step count. If its data came from tool content, a single poisoning (a tool result claiming "budget is fine") could steer the run (§7.3).

## 3. Problem Statement (What It Solves)

1. **Monotonically growing context vs. a finite window**: the agent loop appends assistant messages and tool results every step, so any long run eventually hits the model window. Reproduced in tests: 40 atomic groups × 400 chars ≈ 16k+ tokens against a manifest cap of 4000 (`tests/context/test_context.py:234-250`).
2. **Naive truncation breaks the pairing protocol**: an assistant message carrying `tool_calls` and its tool results are a pairing constraint in provider APIs; cutting in the middle produces orphan tool results that the API rejects outright.
3. **Rebuilding the request every step kills the prefix cache**: any jitter in the ordering or serialization of SYSTEM or tool schemas turns that step into full-price billing.
4. **Hot reload punches through a live frame's prefix**: if the inline-capability section were re-read from the registry every step, one hot reload would change the SYSTEM of a running frame mid-flight, taking down both prefix caching and resume determinism (`docs/SKILL-INLINING.md` §4.2).
5. **The model is blind to budget and step count**: without readings there is no self-convergence — but bare readings do not change behavior; "20% budget left" only works when paired with an operating strategy (§7.3).
6. **No external handle on a bloated context**: when a sidecar (supervisor) sees a frame's context spiraling, it needs a forced-compression channel that does not wait for the cap to trip (§7.1, external trigger).

## 4. Design and Mechanism (How)

### 4.1 Contract Surface

`api/v1/context.py` freezes four symbols: the `ContextManager` protocol (`build`/`maintain`, :20-29), the `Compressor` protocol (`name` + `compress(ctx, target_tokens, svc)`, :33-40), `CompressionReport` (evicted / before / after / cache_invalidation_estimate / marker, :44-54), and `KernelServices` (estimator / providers / blob, :58-67). New strategies register through the `agent_os.compressors` entry point — contract first, baseline replaceable.

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
Top of each loop iteration (runner.py:408-410):
  _force_compress flag popped from working? ──yes──▶ force_compress (cap ignored)
  maintain(frame):                                   │
    estimate = estimator.estimate(messages)          │
    cap = manifest.context_policy.max_tokens         │
          or default 128_000                         │
    compression=="off" (RunConfig or manifest)? ──yes──▶ short-circuit
    estimate ≤ cap or no compressor? ──yes──▶ update estimate only
    otherwise: pre:compress signal
          → compressor.compress(ctx, int(cap*0.8), svc)
          → post:compress signal (full report fields)
```

`force_compress` is the §7.1 external trigger: a sidecar emits `ForceCompress`, RunControl sets a flag in the frame's working memory (`kernel/control.py:21-22`), and the runner consumes it before maintain (runner.py:408) — it still travels the same `_compress` path (manager.py:303-326), just with `forced=True` in the pre-signal payload. The ablation setting short-circuits forced triggers too (:287-288).

### 4.6 RollingWindowCompressor: Eviction by Atomic Group

The baseline compressor (rolling_window.py) is a pure three-step algorithm with no LLM or blob dependency:

1. **Grouping** (`atomic_groups`, :33-60): an assistant message carrying `tool_calls` is bound with all **consecutive** following TOOL messages whose `tool_call_id` matches, forming one atomic group; every other message forms its own group. Pairing atomicity (invariant 2) is thus guaranteed **structurally**, not by after-the-fact checking — the group is the smallest eviction unit and cannot be split.
2. **Whole-group eviction** (:83-89): groups containing a pinned message (`m.meta["id"] in ctx.pinned`) are never evicted; eviction starts from the oldest non-pinned group until `estimate ≤ target`, but the **last non-pinned group is always kept** — evicting everything would strip the frame of all recent context, and that group is the fallback target of hard truncation.
3. **Hard-truncation fallback** (:91-94, :106-121): if the context still exceeds the target (a single group is too large), the earliest surviving non-pinned group is truncated character by character, message by message, tagged `[truncated]`; only content is touched, structure is untouched, pairing survives. Content shorter than the tag itself is skipped — "truncating it costs more than keeping it" (:117).

The report carries `marker = "[COMPRESSED]"` (:25) — an idempotency mark reserved for multi-strategy chains (see §6).

### 4.7 The Estimator: One Calculus

`TokenEstimator` (estimator.py): char/4 rough estimate + a fixed 4-token overhead per message (:14) + JSON-length accounting for tool_call arguments, with a global `calibration` factor. It shares one calculus with ProviderManager (§4.2) — compression watermarks and billing estimates can never disagree. `per_provider_factor` reserves the entry point for per-vendor tokenizer deviations (:38-43).

### 4.8 Strategy Chain: Designed Panorama vs. Implemented Landing

| §7.2 strategy | Status | Notes |
|---|---|---|
| `collapse_child` | implemented (structural) | a popped child frame's transcript never enters the parent — only the return value (the fib test asserts "the entire child transcript is folded", `tests/kernel/test_fib_slice.py:112`) |
| `truncate` (rolling window) | implemented (baseline) | section 4.6 of this chapter |
| `spill` / `narrate` / `summarize` | designed, not implemented | `KernelServices.providers` is currently always `None` (manager.py:336); the blob channel is pre-wired (:337) |
| `hierarchical` (chain, manifest default) | designed, not implemented | the chain tail must be summarize or spill (§7.6 positioning warning) |

## 5. Effects and Verification (What It Achieves)

Test baseline: `agent_os/tests/context/` — **31 tests, all green** (`pytest tests/context -q`; test_context.py 14 + test_inline_merge.py 15, plus parametrized expansions). Key evidence:

- **Invariant property tests** (hypothesis, `test_context.py:175-199`): over random message histories (1-24 segments, 0-3 tool_calls per group, 50-600 chars per message, 40 examples), compression preserves pairing, keeps pinned messages, and never increases the estimate — the first three §7.4 invariants are directly measurable.
- **Eviction order** (:122-141): oldest groups leave first, newest groups stay, the pinned system message holds its position; **hard truncation of an oversized single group** (:157-167) carries the `[truncated]` tag with pairing intact.
- **Triggering and short-circuit** (:234-265): an over-cap context is compressed to `int(cap × target_ratio)` (within one group's granularity), with exactly one `pre/post:compress` pair; under `compression: "off"` the message count is unchanged and no signal fires.
- **force_compress** (:319-344): runs once even below cap, with `forced is True` in the pre payload; still short-circuits under ablation. With no compressor, maintain silently skips compression but updates the estimate (:347-365).
- **State injection** (:273-299): the status message sits at the request tail with `kind=status` and is ephemeral (never in the frame context); at 5% remaining budget the hint contains "converge."
- **Prefix stability** (:302-311): across two consecutive builds, `(role, content)` of all non-status messages is byte-identical; end to end, the fib slice test asserts byte-identical SYSTEM between adjacent requests of the same frame (`tests/kernel/test_fib_slice.py:114-115`).
- **Inline snapshots**: hot reload leaves a running frame's SYSTEM unchanged while new frames get the new version (`test_inline_merge.py:230`); the snapshot freezes into `working` and resumes deterministically (:254); `post:context.inline` fires exactly once (:281).

Ripple effects: invariant 5 shaped the inlining subsystem in reverse — "freeze into `working` on first build" is not an inlining invention but a consequence of prefix stability (SKILL-INLINING.md §4.2); the three-service shape of `KernelServices` pre-wires hooks for spill/summarize without touching the contract.

## 6. Limitations and Boundaries

1. **A bare rolling window is a known loop inducer**: dropping early tool results makes the model re-issue the dropped calls. Both the source and §7.6 carry a positioning warning — it is only the middle-layer base of the hierarchical chain, whose tail must be summarize or spill, and **must not be read as the recommended practice**; yet the chain itself is unimplemented, so the production shape today is precisely this baseline with a known defect.
2. **Evicted information is unrecoverable**: spill (blob + retrievable preview) is unimplemented, so rolling-window eviction is final; `svc.blob` is wired (manager.py:337) but the baseline compressor does not use it.
3. **char/4 is a rough estimate**: deviations are significant for code-, CJK-, or JSON-heavy contexts, so cap decisions may fire early or late; `per_provider_factor` always returns 1.0 (estimator.py:43) as a placeholder. The "multimodal calculus (image formula by resolution)" mentioned in §7.6 has no implemented branch in the estimator — per the code-wins rule, multimodal estimation is not landed.
4. **The §7.1 hard-cap path is unimplemented**: the designed "near the model window → aggressive compression; if still over, fail the frame upward" has no corresponding code; there is a single cap tier (manifest `max_tokens` or default 128_000, manager.py:76/291-301), and hitting the real model window is currently backstopped by provider errors.
5. **Only the "short trajectory, replace each turn" branch of the status bar exists**: §7.3 also designs a "long trajectory, persistent append (fully cache-preserving)" branch; the code rebuilds the status message each step and appends it ephemerally, which is equivalent to per-turn replacement. The designed "current time, tool-call count" fields are also absent from the status lines (manager.py:209-215). The code wins.
6. **The hint policy is hardcoded**: the 20% threshold (`LOW_BUDGET_RATIO`) and both wordings are fixed in the manager; a skill cannot tune the convergence strategy to its task shape, and the status bar's trust model (unconditional model trust) amplifies the reach of this fixed policy.
7. **The `[COMPRESSED]` idempotency marker has no consumer yet**: it appears only in `CompressionReport` (rolling_window.py:103); chain-level de-duplication is reserved semantics — there is no chain, so today the marker is just a record.
8. **Compression granularity is "before each step's build"**: nothing intervenes mid-step (e.g., when one tool call writes back a huge result); the earliest handling is the next step's maintain, and in between the over-cap context is persisted verbatim into the checkpoint.

## 7. References

- Design docs: `docs/DESIGN.md` §7 (Context subsystem), §4.2 (single token calculus), §3.1 (agent loop); `docs/SKILL-INLINING.md` §4 (assembly / freezing / ablation); `docs/SUPERVISOR.md` §2.1 (ask_supervisor presentation); `docs/CODE-ORCHESTRATION.md` §2.1 (orchestrate pseudo-tool)
- Contracts: `agent_os/src/agent_os/api/v1/context.py`, `agent_os/src/agent_os/api/v1/frames.py` (FrameContext/Usage)
- Implementation: `agent_os/src/agent_os/context/manager.py`, `agent_os/src/agent_os/context/rolling_window.py`, `agent_os/src/agent_os/context/estimator.py`; call sites `agent_os/src/agent_os/kernel/runner.py:408-410`, `agent_os/src/agent_os/kernel/control.py:21-22`
- Tests: `agent_os/tests/context/test_context.py`, `agent_os/tests/context/test_inline_merge.py`, `agent_os/tests/kernel/test_fib_slice.py`
