# Microkernel & Execution Model

> Chapter: 01 · Status: Implemented (the fork/join primitive `parallel_invoke` and true pause semantics are designed but not implemented; see §6) · Sources: `agent_os/src/agent_os/kernel/runner.py`, `agent_os/src/agent_os/kernel/checkpoint.py`, `docs/DESIGN.md` §1-3

## 1. Overview

The Kernel Runner is the microkernel of Agent OS — the concrete landing point of the mapping-table row "microkernel (scheduling + permissions + IPC) → Kernel Runner" — and it does exactly three things: **flow control**, **permission control**, and **IPC** (docs/DESIGN.md §1, axiom 1; the `Kernel` class at `kernel/runner.py:156-200`). All nine functional subsystems (Providers, Tools, Skills, Context, Logic, Sidecars, Telemetry, Memory, Blackboard) live outside the kernel; the kernel holds only their contract handles, wired together by `KernelBuilder` (`runtime/builder.py:149-247`). In the execution model, a Run is a process, a SkillFrame is a stack frame, and the agent loop is the per-frame executor; checkpointing and power-cut recovery (`kernel/checkpoint.py`) belong to the kernel's reliability mandate. This chapter deepens §3.1-3.2 of the executive summary: rather than restating the layering, it pins down the loop's exact decision order, where each arbitration point sits, and how the recovery algorithm works.

## 2. Motivation & Background (Why)

Agent loops have a structural gravity: every new capability — compression, supervision, retries, persistence, human-in-the-loop — is cheapest to add by dropping it into the loop body. A few iterations later the loop is an indivisible lump: mechanisms entangle, swapping a compressor means touching dispatch code, and there is nothing left to remove when you want a bare-model ablation baseline. Agent OS counters this by making "what may stay in the kernel" an executable criterion (docs/DESIGN.md §1, axiom 1):

- **F**: without it the loop cannot advance (frame stack, dispatch, cancellation);
- **P**: it is a permission/veto arbitration point (whitelist checks, verdict arbitration);
- **I**: it is the only common channel between subsystems (signal bus, RunControl).

Only what satisfies at least one criterion stays; everything else is pushed into a subsystem. The other half of the corollary matters just as much: the kernel does **not** execute logic code (Logic Kernel — a single execution point is what makes execution arbitrable), does **not** assemble prompts (Context — a single token-accounting dialect), and does **not** persist (Telemetry — WAL durability semantics are incompatible with the sidecar fire-and-forget model, §10). This is not aesthetics: a scattered arbitration point is no arbitration point, and the audit surface grows linearly with kernel size.

The second key decision is to **implement the call stack directly as Python await chains** instead of building an explicit scheduler (docs/DESIGN.md §3.1): a suspended parent frame is an `await` point, a completed child frame is a coroutine return, and asyncio cancellation propagates down the stack for free. The cost is that coroutine stacks are opaque — sidecars cannot inspect them and RunControl cannot address into them. Frames are therefore still registered explicitly in a `FrameStack` (`kernel/stack.py:15-27`): three views (active frames, by-id index, parent-child adjacency) serving `pre:frame.pop` vetoes, frame-tree inspection, and checkpoint serialization. The implicit stack owns control flow; the explicit stack owns arbitration and persistence — the execution model's central "best of both" trade.

## 3. Problem Statement (What It Solves)

1. **Privileged operations scatter, leaving nothing to arbitrate.** A model that can call tools, invoke sub-skills, and generate code gives supervision nowhere to attach if each path reaches the outside world on its own. All privileged operations must converge on a single dispatch gate.
2. **Sub-task internals pollute the parent frame.** A sub-skill that runs 20 steps and ingests thousands of lines of tool output makes the parent both expensive and diluted if everything flows back. A child's whole transcript must collapse into one return value on pop ("isolation over compression", docs/DESIGN.md §2.3).
3. **Interruption creates orphan tool_calls.** If cancellation lands between "assistant emitted tool_calls" and "tool_result written", the next request carries an unpaired call and most provider APIs reject it outright. Pairing atomicity (§7.4 invariant 2) must hold on the interrupt path too.
4. **Recovery after a power cut equals re-running from scratch.** If a long run dies at step N, and recovery means repaying N-1 LLM calls and replaying tool side effects, the reliability mechanism has no economic point.
5. **A runaway run cannot stop at a clean boundary.** When budget runs out or a loop spins, cutting at an arbitrary instruction boundary leaves the frame tree half-mutated. Stops must happen only at dispatch boundaries (safe points).
6. **Failure propagation needs lethality tiers.** An ordinary child failure (say, a tool timeout) must not bring down the whole tree; conversely, a run-level verdict like budget exhaustion must never be swallowed by some frame's `try/except`.

## 4. Design & Mechanism (How)

### 4.1 Kernel Boundary: Three Things and Where They Live

| Responsibility | Contents | Code anchor |
|---|---|---|
| Flow control | agent loop, frame stack, dispatch arbitration, safe-point cancellation, spawn concurrency, budget abort, output-repair circuit breaker | `runner.py:351-486`, `kernel/stack.py`, `runner.py:1130-1196`, `runner.py:1230-1252` |
| Permission control | manifest whitelist checks, entry to the three-layer intersection, escalation-gate call site, verdict arbitration | `runner.py:547-598`, `runner.py:839-915`, `runner.py:315-336` |
| IPC | signal bus, RunControl (stop flags / injection / force-compress), StatusBoard inter-frame state | `kernel/signals.py:28-57`, `kernel/control.py:25-83`, `runner.py:1202-1214` |

### 4.2 The Single-Frame Agent Loop: Decision Order Is Security Semantics

The step order in `_frame_loop` (`runner.py:395-486`) is deliberate — arbitrate first, act second:

```
run_frame(frame)                       # runner.py:351
  ├─ pre/post:frame.push; depth backstop checked on push (stack.py:29-39)
  └─ loop (each step):
     1. safe point: check _stop_flags[run_id]; if set → RunAborted  (runner.py:400-402)
     2. emit pre:step, arbitrate verdicts: Stop/Veto/Pause → abort;  (runner.py:403-406)
        InjectMessage/ForceCompress land via RunControl, continue    (runner.py:323-336)
     3. consume per-frame force-compress flag → context.maintain/build (runner.py:407-410)
     4. pre:llm.request → providers.chat → post:llm.response         (runner.py:411-431)
        (dict-shaped tool_calls normalized to ToolCall on entry, runner.py:415-424)
     5. response appended + accounting (account; over max_steps/max_cost
        → hard failure)                                              (runner.py:432-433)
     6. no tool_calls → outputs validation: failure writes an error
        observation and retries; 2 consecutive failures
        (_OUTPUT_VALIDATION_MAX_FAILURES) → frame failure            (runner.py:434-453)
     7. dispatch each call; on CancelledError write an interrupted
        placeholder tool_result first, then re-raise — pairing
        atomicity holds on the interrupt path                        (runner.py:454-466)
     8. post:step (call-signature list, LoopDetector's observation
        surface) + StatusBoard                                       (runner.py:468-485)
  ├─ pre:frame.pop is vetoable: Veto → correction observation into
  │  frame context, back to loop; Stop → RunAborted; pop_ok only
  │  with no veto                                                    (runner.py:368-383)
  └─ post:frame.pop; the frame's transcript collapses into one tool
     result in the parent as the coroutine returns
```

Two trade-offs are worth stating explicitly. First, **the output-repair breaker is set to 2** (`runner.py:110`): when the final answer fails `outputs` schema validation the model gets one chance to self-correct (error observation written back, §3.2), and consecutive failure fails the frame — fewer gives the model no room to repair, more is gambling tokens on probability and violates the "every recovery path has its own circuit breaker" rule. Second, **accounting and budget verdicts live in the kernel, not in a sidecar** (`runner.py:1230-1252`): `max_steps`/`max_cost` are hard RunConfig boundaries and must propagate as `RunAborted`/`BudgetExceeded` (`kernel/errors.py:25-34`) that no frame can swallow; the BudgetGuard sidecar covers the host-configurable soft-degradation case (stop→pause) instead (§2.4's failure-lethality layering).

### 4.3 Dispatch & Frame Push: One Gate for Every Privileged Operation

`_dispatch_call` (`runner.py:547-598`) is the single entry point for all calls, routed by name:

- `skill.<name>` / `skill__<name>` pseudo-tools → `_invoke_skill` pushes a frame (both prefixes are accepted — docs/DESIGN.md §3.3 writes `skill__<name>`, while the repo's example skills and tests actually use `skill.<name>`; the code wins, see `runner.py:550` and `runner.py:842-847`);
- `python_orchestrate` → sandbox orchestration (`runner.py:718-837`; syscall callbacks re-enter `_dispatch_call`, no privilege gain);
- `ask_supervisor` → suspend in place awaiting adjudication (`runner.py:604-646`);
- everything else → manifest whitelist check (not listed → structured error observation, never executed) → `pre:tool.call` arbitration (Veto skips dispatch and writes the reason back, Modify patches args, Stop/Pause aborts) → Tool Registry pipeline → `post:tool.call`.

`_invoke_skill` (`runner.py:839-915`) is where the frame tree grows: whitelist check → `pre:skill.invoke` → depth backstop (beyond `max_depth` raises `MaxDepthExceeded`) → **escalation gate** (when the callee's derived tier exceeds the caller's, args are schema-validated first, then adjudicated through the supervisor channel, `runner.py:864-882`; the mechanism itself belongs to the escalation chapter) → `make_frame` builds the child and registers the parent's `call_id` (for checkpoint pairing) → the child inherits the callee's derived tier → `await run_frame(child)` pushes, parent suspends. Failure propagation is tiered here: `MaxDepthExceeded`/`RunAborted` re-raise untouched, while every other child exception collapses into an error observation for the parent (`runner.py:900-913`, `hint` carried across) — ordinary failures are left for the parent LLM to repair; run-level verdicts travel the stack straight to the Run boundary.

### 4.4 Verdict Arbitration & RunControl: Policy Outside, Adjudication Inside

When several SYNC sidecars return verdicts on the same `pre:*` signal, the kernel's `_arbitrate_pre` (`runner.py:315-321`) takes the **first non-Allow** (the emit side already ordered by priority; `None` counts as Allow) — a deterministic verdict independent of sidecar count. Sidecars never interrupt the loop directly: everything they do to a run goes through RunControl (`kernel/control.py:25-83`) into kernel state — `stop`/`pause` set the `_stop_flags` table and the runner itself raises `RunAborted` at the next `pre:step` safe point; `inject_message` appends a USER/INJECTED message; `force_compress` sets a flag in the frame's working memory, consumed before `maintain`. This is axiom 3 in engineering form: **policy (what to review) lives in sidecars; adjudication (fail-closed, priority, timeouts) lives in the kernel**. Cancellation happens only at dispatch boundaries — there is no "deleted half of it, then got cut" frame.

### 4.5 Checkpoint & Recovery: The Trajectory Is the Whole State

Checkpoints follow the WAL principle (docs/DESIGN.md §10.2): a fully persisted frame transcript *is* the checkpoint. `dump_checkpoint` (`kernel/checkpoint.py:126-172`) serializes run state (usage, run-level tool state, the escalation grant ledger) and every frame (context, working, pinned, usage, tier, principal, call_id) into version-headed `{"v": 1}` JSON (`checkpoint.py:73`). Recovery is not re-running: `resume_from_checkpoint` (`checkpoint.py:321-388`) rebuilds the Run and frame tree, skips frames that already carry a `result`, settles survivors **deepest-first** (children resume to DONE before parents settle, so parents read real results), and re-enters the loop at the deepest unfinished frame with its full context — the number of remaining LLM calls is exactly countable.

A power cut leaves three kinds of pairing damage in a frame's context, settled one by one by `_settle_unpaired_calls` (`checkpoint.py:281-318`): (1) a successful result already exists → untouched; (2) only the error observation from the crash exists, but the child frame created by that call (matched by `call_id`) is DONE → **rewritten in place** to the child's real result; (3) no result at all and no child frame → an `interrupted` placeholder is appended (same shape as §3.1's interrupt pairing). Two kinds of suspended calls are *not* crash damage and never get placeholders: a frame suspended in `ask_supervisor` re-asks and settles via `working["_pending_ask"]` (`runner.py:648-673`), and one suspended at the escalation gate re-walks the gate via `working["_pending_escalation"]` (`runner.py:1097-1124`) — approval rebuilds and runs the child on the spot, denial writes `PERMISSION_DENIED`, and pairing atomicity closes naturally. `PeriodicCheckpointer` (`checkpoint.py:175-222`), a bus subscriber, overwrites a "latest scene" file every N `post:step` signals, moving the crash-recovery point from end-of-run to the last N steps; persistence failures are logged, never fatal to the run.

### 4.6 Concurrency Primitives: Serial by Default, Background Frames Implemented

Serial `await` is the default. `spawn_frame`/`wait_frame` (`runner.py:1130-1196`) implement §3.4's background frames: whitelist/depth/escalation checks identical to invoke; the child runs as an `asyncio.create_task`; join degrades to reading the terminal state (exceptions re-raise as-is); parent and child exchange rolling state over the StatusBoard. The fork/join fan-out `parallel_invoke` (intra-batch fault isolation, first-success settlement) is designed in docs/DESIGN.md §3.4 but **not implemented** — the only trace in the repo is the `concurrency_safe` contract field at `api/v1/tools.py:105`, reserved ahead of the mechanism.

## 5. Effects & Verification (Results)

Test evidence (this chapter's direct surface): 92 tests across 10 files in `tests/kernel/` plus 3 in `tests/telemetry/test_trace_checkpoint.py` — 95 tests, all passing in a measured run (2.88s); the full repo collects 854 tests via `pytest --collect-only` at the time of writing. Key anchors:

- **Execution-model vertical slice** (`tests/kernel/test_fib_slice.py`, 5 tests): the recursive skill `demo.fib` (`agent_os/skills/skills.yaml`; fib(n) first invokes itself for fib(n-1), then calls the sandbox tool to sum the last two) returns the correct sequence end to end; frame-tree shape assertions — fib(5) pushes exactly 4 frames at depths 1-4, exactly 10 LLM calls, exactly 3 sandbox executions; **frame-isolation assertions** — the parent's second request is `[SYSTEM, USER, ASSISTANT, TOOL]`, the child's whole trajectory collapsed into one `{"ok": true, "value": {"seq": [0,1,1,2]}}` tool result, and the SYSTEM prefix is byte-identical across adjacent requests of the same frame; with `max_depth=2`, fib(4) raises `MaxDepthExceeded`; `bad_brain` triggers `OutputValidationError` after consecutive failures.
- **Power-cut recovery** (`tests/telemetry/test_trace_checkpoint.py`, 3 tests): a power-cut exception is injected at the 6th LLM call of fib(5); a fresh kernel resumes from the checkpoint and needs **exactly 5 more calls** (`len(mock2.recorded) == 5`) to return the full sequence — "recovery is not re-running" pinned to a call count; the checkpoint JSON contains the version header, run usage, mixed running/done frame states, and complete frame contexts.
- **RunControl** (`tests/kernel/test_run_control.py`, 7 tests): pause aborts at the next safe point with a `"paused: "`-prefixed reason; injected messages are wrapped as USER/INJECTED and appear in subsequent LLM requests; injection/force-compress on a missing frame is dropped without crashing the run; `get_frame_tree` returns the nested tree; `get_usage` returns the accounting snapshot.
- **Periodic checkpoint** (`tests/kernel/test_periodic_checkpoint.py`, 3 tests): overwrite-writes on `post:step` counts; counting stops when the run ends.

Ripple effects: the signal catalog plus the explicit frame tree are the shared foundation for later systems — Telemetry subscribes to the same stream as a privileged subscriber and lands it as JSONL WAL (`builder.py:216-218`); the debugger (`kernel/debug.py`) and replay build directly on the bus and frame contexts; the escalation gate and the clean-context invariant both hang off this chapter's dispatch point and frame-isolation semantics — were `_invoke_skill` not the single push site, the escalation model would have nowhere to stand. For ablation, `KernelBuilder` can run a bare loop with only a MockProvider and an empty tool table (the microkernel-purity acceptance of docs/DESIGN.md §14.2; `runtime/builder.py:17-18`), and much of the test suite runs on exactly such minimal assemblies (e.g. `tests/helpers/kernels.py:38-73`).

## 6. Limitations & Boundaries (Limits)

1. **`parallel_invoke` is not implemented.** One of §3.4's three primitives is missing: no fork/join fan-out; parallelism must be hand-assembled from spawn + Blackboard, and first-success / intra-batch fault isolation have no landing point yet.
2. **pause means stop in v1.** `RunControlImpl.pause` merely prefixes the reason with `"paused: "` (`kernel/control.py:39-41`); there is no "suspend a live run and continue in place later". Continuing requires checkpoint serialization + resume, and resume re-enters the loop from the latest checkpoint rather than the exact paused instruction.
3. **The kernel does not enforce `max_wall_time`.** `Kernel.account` checks only `max_steps`/`max_cost` (`runner.py:1245-1252`); the wall-clock cap lives in the BudgetGuard sidecar (`sidecars/builtins.py:72-73`), configured from host TOML rather than the `RunConfig.max_wall_time` field — with no sidecar assembled, a run has no wall-clock breaker.
4. **Spawned background frames do not survive checkpoints.** The `_spawned` task table and `_stop_flags` are in-process state (`runner.py:195-200`); after a power cut the spawned subtree is gone, and the recovery semantics are "the code parent frame re-runs wholesale and re-reaches the spawn gate" (comment at `runner.py:1164-1166`) — side effects already produced by background frames are neither replayed nor settled, so callers must make them idempotent.
5. **Single event loop, coroutine-level parallelism.** Parallel branches share one asyncio loop; a CPU-bound code skill blocks its siblings (noted in docs/DESIGN.md §3.4). Multi-machine distributed execution is an explicit non-goal in §17.
6. **Checkpoint schema v1 has no migration path.** A version mismatch raises `ValueError` outright (`checkpoint.py:329-330`); format evolution survives on additive fields with defaults (e.g. tier/principal default recovery, `checkpoint.py:234-246`), and there is currently no answer for breaking changes.
7. **Safe points cannot govern in-flight side effects.** Stops take effect only at step and dispatch boundaries; when a force-stop hits an orchestration script already inside the sandbox, "the syscalls already executed have already happened" — the kernel can only report the executed list back to the model for compensation decisions (`runner.py:816-826`). No transactional rollback; built-in transactions/compensation are explicitly out of scope in §3.2.
8. **Cost breaking depends on a price table.** Without one, `usage.cost` stays 0 and neither `max_cost` nor BudgetGuard ever fires (fail-open on money); the builder can only warn (`runtime/builder.py:124-132`).
9. **Code-organization leftovers.** `kernel/dispatch.py` is an M0 skeleton (every method body is `NotImplementedError`); the real dispatch is `_dispatch_call` in `runner.py`. The docstring of `builder.build()` still mentions "injecting a Dispatcher", which no longer matches reality — behavior is unaffected, but readers should trust the runner.

## 7. References

- Design docs: `docs/DESIGN.md` §1 (axioms), §2 (core abstractions), §3 (execution model), §10.2 (WAL principle), §14.2 (assembly & ablation), §17 (non-goals)
- Kernel sources: `agent_os/src/agent_os/kernel/runner.py`, `kernel/checkpoint.py`, `kernel/stack.py`, `kernel/signals.py`, `kernel/control.py`, `kernel/errors.py`, `kernel/run.py`, `kernel/dispatch.py` (M0 leftover skeleton)
- Contract layer: `agent_os/src/agent_os/api/v1/frames.py`, `api/v1/run.py`, `api/v1/signals.py`, `api/v1/tools.py:105` (`concurrency_safe` reservation)
- Assembly: `agent_os/src/agent_os/runtime/builder.py`
- Adjacent systems: `docs/ESCALATION.md` (escalation gate), `docs/SUPERVISOR.md` (adjudication channel), `docs/CODE-ORCHESTRATION.md` (orchestration sandbox)
- Tests: `agent_os/tests/kernel/` (92 tests), `agent_os/tests/telemetry/test_trace_checkpoint.py` (3 tests), `agent_os/tests/helpers/brains.py`, `agent_os/tests/helpers/kernels.py`
- Example: `agent_os/skills/skills.yaml` (recursive skill `demo.fib`)
