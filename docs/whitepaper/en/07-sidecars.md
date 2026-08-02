# Sidecars: Signal-Driven Supervision

> Chapter: 07 · Status: Partially implemented (M4 core landed: SidecarSupervisor, RunControl, five built-in sidecars operational; HumanApproval is a skeleton; entry-point registration and the LLM-driven sidecar facilities are not implemented) · Sources: `agent_os/src/agent_os/sidecars/`, `agent_os/src/agent_os/api/v1/{sidecars,signals,control}.py`, `agent_os/src/agent_os/kernel/{signals,control,runner}.py`, `docs/DESIGN.md` §5

## 1. Overview

A sidecar is a supervisor triggered by signals: it observes the run, watches behavior, and when necessary vetoes, corrects, pauses, or force-stops it (docs/DESIGN.md §5). In the OS mapping table it corresponds to "interrupt handler + watchdog + seccomp" (§1). The signal bus is the kernel's IPC channel, and sidecars carry policy on top of it; the kernel keeps only adjudication (fail-closed, priority, timeout) while policy — what to inspect, when to intervene — lives entirely outside, in sidecars. This is the direct landing point of "policy in sidecars, arbitration in the kernel" (§1, Axiom 3). Sidecars share one signal stream with Telemetry but differ in semantics: Telemetry requires guaranteed persistence and is a privileged subscriber, not a sidecar (§5.1, §10).

## 2. Motivation and Background (Why)

**Why aren't these checks inside the kernel?** Run them through the microkernel admission test (F/P/I, §1 Axiom 1): budget thresholds, loop thresholds, and tool rule tables are not things "without which the loop cannot advance" (fails F); they are not themselves veto arbitration points — there is exactly one arbitration point, the runner's `_arbitrate_pre` (P belongs to the kernel alone); and they are not a common channel between subsystems (fails I). All three fail, so they are subsystem-ized. Conversely, the adjudication logic (first non-Allow verdict wins, 2-second SYNC timeout, fail-closed on error) stays in the kernel because it is precisely that P point: if sidecars could define their own adjudication, a single buggy sidecar could quietly turn fail-closed into fail-open.

**Why signal-driven rather than polling or loop wrapping?** Signals are lifecycle events the kernel already emits so Telemetry can persist them; letting supervisors subscribe to the same stream makes the observation surface and the record surface identical by construction — what a sidecar sees is what a replayed trace shows, with no "the supervisor saw a different world" divergence. Subscription order is therefore a discipline: Telemetry subscribes first (WAL lands first), sidecars second, the debugger last (docs/DEBUGGER.md §1, `runtime/builder.py:216-246`).

**Why is free text withheld by default?** If an approver consumes the main model's free text, an injector gains a rhetoric channel to influence approval (the input-minimization principle, §5.2). Sidecars receive only structured payloads by default: tool name, arguments, call signatures, usage (`sidecars/builtins.py:6-8`); anything needing more context must explicitly declare `needs_free_text = True` and own the injection risk.

**Why two budget mechanisms?** The kernel's accounting carries a hard backstop: `Kernel.account()` checks RunConfig `max_steps`/`max_cost` after every step and raises `BudgetExceeded` on breach (`kernel/runner.py:1230-1252`) — "budget abort verdict" is listed under flow control by Axiom 1. BudgetGuard is the replaceable policy layer: thresholds independent of RunConfig, and stop can be downgraded to pause + notification (§2.4) so a long-running host can decide whether to top up the budget or give up. The test fixture deliberately sets `max_cost` to 100 to leave the budget stage to BudgetGuard (`tests/sidecars/test_builtin_sidecars.py:87`).

## 3. Problem Statement (Problems Solved)

1. **Runaway cost/steps**: the model falls into a "try once more" loop and nobody calls a halt — every step costs 0.02 and never converges, burning through the run's budget.
2. **Repeated-call death spiral**: after the rolling window drops early tool results, the model re-issues the identical call with identical arguments (the call signature `sha1(name+args)` never changes) and spins forever without intervention (experimental evidence in docs/reports/ch02-context-engineering.md).
3. **Frame-level stall**: the gap between two consecutive steps exceeds a wall-clock threshold (e.g. 600s) with no application-layer progress — distinct from provider stream stalls, this is behavioral silence.
4. **Dangerous tool call**: the model (or injected content steering it) issues `system.shell.exec {"command": "rm -rf /"}` — it must be stopped before execution, and the model must learn *why*, not have the call silently vanish.
5. **Dangerous dynamic code**: `python_orchestrate` submits `import os; os.system('echo pwned')`; a static scanning gate must exist before the code ever reaches the sandbox.
6. **Substandard result**: a frame is about to pop, but a reviewer judges "the report lacks citations" — it must be sent back with a corrective observation so the loop continues instead of shipping a defective result.
7. **The supervisor itself fails**: a SYNC sidecar throws or times out on the critical path — silently allowing would void approval after injection, and random crashes are unusable; a deterministic fallback is required.

## 4. Design and Mechanism (How)

### 4.1 The Signal Catalog and the Bus

Signal names follow `<phase>:<event>`; the `pre:` prefix means synchronous and veto-capable, `post:` means asynchronous observation (§5.1). The catalog is frozen in the contract layer (`api/v1/signals.py:49-96`, 31 names in `SIGNAL_NAMES` asserted by contract tests), and the structure is `Signal{name, run_id, frame_id, payload, ts}` (`api/v1/signals.py:134-142`). Three subscription pattern forms: `"*"` for everything, exact names, and `"prefix.*"` suffix wildcards (`kernel/signals.py:19-25`). The bus's `emit` awaits all matching handlers in subscription order and collects their return values; handler exceptions are swallowed and logged — a subscriber failure must never take down the run (`kernel/signals.py:41-56`).

### 4.2 Data Flow and the Adjudication Chain

```
kernel agent loop (kernel/runner.py)
  │ builds a Signal at each key node (structured payload: depth/skill/usage/calls…)
  ▼
InProcessSignalBus.emit — awaits in subscription order, exceptions swallowed
  ├─ Telemetry ("*")        privileged subscriber, WAL lands first (not a sidecar, §5.1)
  ├─ SYNC shared handler (one per pattern, sidecars/supervisor.py:54-75)
  │    └─ waits for each sidecar in ascending priority (sync_timeout=2s)
  │       exception/timeout → Veto("fail-closed: …"); first non-Allow returns
  ├─ ASYNC handler (one per sidecar, supervisor.py:77-91)
  │    └─ create_task and return immediately; exceptions only logged, never kill the run
  └─ DebugController (subscribes last, bypasses the supervisor — see ripples in §5)
  ▼
runner collects verdicts → _arbitrate_pre: first non-Allow wins (runner.py:316-321)
  ├─ Veto   → skip dispatch, reason written back as an error observation / reviewer rejection
  ├─ Modify → patch merged into call.args (still passes Tool Registry schema validation)
  ├─ Stop/Pause → RunAborted, propagates up the stack, cannot be swallowed by a frame (§3.2)
  └─ InjectMessage/ForceCompress → applied via RunControl, then the loop continues
sidecar's reverse control: RunControl (the only privileged channel) → set a flag → takes
effect at the next safe point
```

### 4.3 The Contract: Sidecar, Verdict, RunControl

The sidecar contract is five fields plus one method (`api/v1/sidecars.py:97-107`): `subscriptions`, `mode` (SYNC on the critical path, veto-capable; ASYNC pure observation), `priority` (deterministic adjudication order among multiple SYNC sidecars), `needs_free_text` (default False), and `on_signal(sig, ctl) -> Verdict`. Verdict is a seven-type union (`api/v1/sidecars.py:45-94`): Allow / Modify(patch) / Veto(reason) / InjectMessage / Pause / Stop / ForceCompress.

What matters is not which verdicts exist but **which verdicts each emission point adjudicates** — this is implemented point by point in the runner, and the current reality (code wins) is:

| Emission point | Veto | Modify | Stop/Pause | Inject/ForceCompress |
|---|---|---|---|---|
| `pre:step` (runner.py:323-336) | abort run (RunAborted) | not handled | abort run | applied via ctl, loop continues |
| `pre:tool.call` (runner.py:570-583) | skip dispatch, write back `kind=vetoed`, `retryable=false` | patch merged into args, dispatch proceeds | abort run | not handled |
| `pre:frame.pop` (runner.py:371-383) | corrective observation "reviewer rejected: {reason}" appended, loop resumes | not handled | abort run | not handled |
| `pre:logic.exec` (orchestration path, runner.py:792-800) | `kind=vetoed` written back, sandbox never entered | not handled | abort run | not handled |

**Veto reason write-back** (§5.2) is the core of the behavioral loop: the reason reuses tool-failure semantics and lands in the frame context as an error observation, so the model sees *why it was refused* on the next step and can switch strategy — rather than the reason going only into a trace for post-mortems.

`RunControl` is the only channel through which a sidecar may steer a run (`api/v1/control.py:19-33`, implementation `kernel/control.py`): `stop` sets the run's abort flag, which the runner checks at the next `pre:step` safe point before raising RunAborted (control.py:35-37; runner.py:400-402); `pause` has v1 semantics identical to stop, with a `"paused: "` reason prefix (control.py:39-41); `inject_message` appends a USER/INJECTED message to the target frame, and a missing frame means drop-and-log, never crash the run (control.py:43-51); `force_compress` sets a flag in the frame's `working` memory that the runner consumes before maintain (runner.py:407-408); `get_frame_tree`/`get_usage` are sidecar(code)→kernel pulls that cost no tokens.

### 4.4 Supervision Semantics and the Built-in Sidecars

The `SidecarSupervisor` manages the fleet (`sidecars/supervisor.py`): SYNC sidecars register one shared handler per subscription pattern (a pattern is bound only once), and inside the handler each sidecar is awaited via `wait_for` in stable priority order — **any timeout or exception is fail-closed, treated as a Veto**; ASYNC sidecars get one fire-and-forget handler per (pattern, sidecar); `close()` cancels all in-flight ASYNC tasks at run teardown.

| Sidecar | Subscribes | Mode/Priority | Behavior (implementation notes) |
|---|---|---|---|
| BudgetGuard (builtins.py:31-77) | `post:llm.response` | ASYNC/100 | accumulates cost/steps/wall-clock per run_id, `ctl.stop` on breach; a `_stopped` set deduplicates so each run is stopped once |
| LoopDetector (builtins.py:80-129) | `post:step` | ASYNC/100 | tracks per-frame signatures (first 12 chars of sha1, runner.py:147-153); consecutive repeats reaching threshold → inject a corrective message carrying an operation instruction; max_strikes further repeats → stop; any signature change resets the counters |
| StallDetector (builtins.py:132-171) | `post:step` | ASYNC/100 | inter-step gap beyond `max_idle_seconds` → corrective injection first, stop on recurrence; `clock` is injectable (fake clock in tests) |
| ToolGuard (builtins.py:174-199) | `pre:tool.call` | SYNC/10 | rule table `(tool name, arg regex, reason)`, hit → Veto; self-declared capability ceiling: regexes are useless against shell combinatorial explosion, the real defenses are the sandbox + permissions |
| CodeScanner (builtins.py:202-234) | `pre:logic.exec` | SYNC/10 | default danger set (import os/os.system/ctypes/subprocess/socket) scans `payload["source"]`, hit → Veto; emissions without source (e.g. code-skill frames) have nothing to scan and pass |
| HumanApproval (builtins.py:237-253) | `pre:tool.call` (EXEC tier) | SYNC/20 | **skeleton**: `on_signal` raises `NotImplementedError("M4")` — see §6 |

**Design trade-offs**:

- **First non-Allow wins, not voting or merging**: adjudication must be deterministic and explainable — stable registration order at equal priority means an audit can name "which rule blocked this"; the cost is no deliberation among sidecars, and composed policy must be expressed through priority ordering.
- **Fail-closed by default**: a SYNC sidecar is a security gate; its failure means approval is absent. Better to over-block (availability loss) than under-block (safety loss). ASYNC is the opposite: an observer's failure must never propagate into the run.
- **Correction via injected messages, not prompt rewriting**: LoopDetector/StallDetector corrections reuse the §7.3 status-injection channel and must carry an operation instruction ("stop retrying; switch strategy or declare blockage") — a bare reading changes no behavior; a reading plus a policy does (docs/reports/ch02-context-engineering.md).
- **Modify lands before Registry validation**: the patch is merged into args and then still passes the §8.1 dispatch pipeline's schema validation, so a sidecar editing arguments cannot bypass the type gate.

## 5. Effects and Verification (Results)

**Test evidence** (17 direct anchor tests, plus adjacent suites covering the interaction surface):

- `tests/sidecars/test_builtin_sidecars.py` (8 tests): budget breach force-stops the run (`test_budget_guard_stops_run`, matching RunAborted containing "BudgetGuard"); loop detection corrects first and stops later, and the corrective message provably reaches subsequent LLM requests (`test_loop_detector_injection_reaches_context` asserts "stop retrying" appears in the mock recording); ToolGuard's veto reason is written back (`kind=vetoed`, `retryable is False`, the original reason inside the tool result) and a vetoed call **emits no** `post:tool.call` (`test_tool_guard_veto_skips_dispatch`); a reviewer's first-pop rejection is followed by an accepted second pop, with the rejection reason present in the second request; a SYNC sidecar throwing → fail-closed → RunAborted containing "fail-closed"; StallDetector verified under a fake clock.
- `tests/sidecars/test_code_scanner.py` (2 tests): dangerous code is vetoed with no `post:logic.exec` (never executed); clean code passes (control group).
- `tests/kernel/test_run_control.py` (7 tests): pause carries the "paused: " reason prefix; injected messages are wrapped as USER/INJECTED and appear in subsequent requests; injection/force-compress into a missing frame is dropped without crashing the run; `get_frame_tree`'s nested shape; the `get_usage` snapshot.
- Adjacent coverage: `tests/logic/test_orchestration.py` asserts that syscalls from an orchestration script still emit `pre:tool.call` and that ToolGuard's veto reason reaches the script (:211), and that a RunAborted during orchestration is not degraded into a script-swallowable error (regression test at :424); `tests/test_contracts.py` asserts the frozen signal names (including `pre:frame.pop`).

**Real configuration**: `instance/agent-os.toml:43-45` and the three examples' TOMLs all enable `budget_guard = { max_cost = 2.0 }` and `loop_detector = { threshold = 3, max_strikes = 2 }`; `runtime/config.py:193-224` parses the `[sidecars]` section against a whitelist (only budget_guard / loop_detector / tool_guard_rules; unknown keys raise ConfigError).

**Ripple effects**:

- **The debugger deliberately bypasses the sidecar machinery** (docs/DEBUGGER.md §1): the SYNC channel's 2-second fail-closed timeout is fundamentally incompatible with interactive pauses, so DebugController subscribes to the bus directly, after Telemetry — the pause point itself lands in the trace before the pause. This in turn validates the architectural call that "the signal bus is the common channel and sidecars are just one class of subscriber."
- **The supervisor subsystem takes over the "human answer" channel** (docs/SUPERVISOR.md §1.2): the division of labor is explicit — "sidecars are rule-based supervision (deterministic, fail-closed); the supervisor is decision routing" — and HumanApproval may sink into a supervisor host policy in the future.
- **The orchestration sandbox reuses the same gate** (docs/CODE-ORCHESTRATION.md §2.3): syscalls inside the sandbox come back to the kernel's `_dispatch_call`, inheriting whitelists, ToolGuard vetoes, signals, and accounting — no back door was cut into the supervision surface for the fast path.

## 6. Limitations and Boundaries (Limits)

1. **HumanApproval is a skeleton**: `on_signal` raises `NotImplementedError` (builtins.py:253), so EXEC-tier tools currently have no human gate; the actual carriers of human adjudication are the supervisor's `ask_supervisor` and the escalation confirmation (docs/ESCALATION.md), neither of which goes through the sidecar contract.
2. **The capability ceiling of rule-based sidecars is self-declared**: ToolGuard/CodeScanner regexes are useless against shell combinatorial explosion and obfuscated code; their docstrings state plainly that "the real defenses are the sandbox (§9.2) + permissions (§8.2)," and a semantic parser is only reserved. Treating them as the security boundary is misuse.
3. **Incomplete adjudication coverage**: `pre:skill.invoke` (runner.py:857), `pre:llm.request` (runner.py:411), `pre:compress` (context/manager.py:310), and the code-skill frame's `pre:logic.exec` (runner.py:523) are currently **emitted but not adjudicated** — DESIGN §5.1's "pre is veto-capable" is contract-ahead-of-implementation at these points (code wins).
4. **ASYNC force-stop has latency, and sidecar state is not checkpointed**: `ctl.stop` takes effect at the next safe point, so the in-flight step/tool call completes; BudgetGuard/LoopDetector accumulators live in process memory while checkpoints serialize only the run and its frames (`kernel/checkpoint.py:12-16`) — a cross-process resume zeroes the sidecar accumulators (the kernel's own max_cost backstop survives because `run.state.usage` is checkpointed).
5. **The Pause verdict is a "labeled abort," not a real pause** (control.py:39-41; verbatim in docs/DEBUGGER.md §1): BudgetGuard's stop→pause downgrade in v1 is effectively "abort with a different reason"; resumable pausing belongs to the debugger/supervisor channels.
6. **`budget.warning`/`budget.exceeded` are in the frozen catalog but emitted by no one**: `account()` raises BudgetExceeded directly (runner.py:1249-1252), and the 80% early warning currently exists only as client-side progress-bar semantics in the Web UI (docs/WEB-UI.md:186).
7. **LoopDetector has two blind spots**: the signature is an exact hash, so semantically identical calls with literally different arguments are invisible; and syscall sequences inside an orchestration script never enter the `post:step` payload, so in-script loops are unobservable to it (listed as to-do in docs/CODE-ORCHESTRATION.md §4).
8. **SYNC sidecars are a critical-path cost**: every `pre:tool.call` may in the worst case walk a priority chain with a 2-second ceiling per link; the §16 risk table requires a load-test budget before registering SYNC. The supervisor's "heartbeat, restart (ASYNC only)" (§5.3) is unimplemented — only register and close exist today.
9. **Input minimization is discipline, not enforcement**: payload trimming for `needs_free_text=False` has no mechanized guarantee (docs/reports/dev-status.md:58); and custom-sidecar entry-point registration is a commented placeholder in pyproject.toml, so third-party sidecars can only be assembled in code.

## 7. References

- Design: `docs/DESIGN.md` §1 (axioms), §2.4 (failure lethality tiers), §3.1-3.2 (loop and recovery), §5 (the Sidecars chapter), §7.3 (status injection), §16 (risk table)
- Contracts: `agent_os/src/agent_os/api/v1/sidecars.py`, `signals.py`, `control.py`
- Implementation: `agent_os/src/agent_os/sidecars/{supervisor,builtins}.py`, `agent_os/src/agent_os/kernel/{signals,control,runner,checkpoint}.py`, `agent_os/src/agent_os/runtime/{builder,config}.py`, `agent_os/src/agent_os/context/manager.py`
- Adjacent docs: `docs/DEBUGGER.md` §1, `docs/SUPERVISOR.md` §1.2/§10, `docs/CODE-ORCHESTRATION.md` §2.3/§4, `docs/ESCALATION.md`, `docs/reports/dev-status.md`, `docs/reports/ch02-context-engineering.md`
- Tests: `agent_os/tests/sidecars/test_builtin_sidecars.py`, `agent_os/tests/sidecars/test_code_scanner.py`, `agent_os/tests/kernel/test_run_control.py`, `agent_os/tests/logic/test_orchestration.py`, `agent_os/tests/test_contracts.py`
- Config samples: `instance/agent-os.toml`, `agent_os/examples/*/agent-os.toml`
