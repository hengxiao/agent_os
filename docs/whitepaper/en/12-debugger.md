# Debugger: GDB Semantics & Intervention

> Chapter: 12 · Status: implemented (P1 kernel primitives / P2 CLI REPL / P3 Web backend / P4 Web debug console / P5 time travel + periodic checkpoint; §6 lists the items explicitly out of scope for v1) · Basis: `docs/DEBUGGER.md`, `agent_os/src/agent_os/kernel/debug.py`, `agent_os/src/agent_os/host/web/run_manager.py` (P3/P5 sections), `agent_os/src/agent_os/host/web/app.py` (`/api/debug/*`), `agent_os/src/agent_os/host/web/static/js/components/debug-view.js`, `agent_os/tests/web/test_debug_api.py`

## 1. Overview

The debugger treats a run as a process under GDB: breakpoints (glob-matched), stepping (into/over/out), pause-at-any-time (SIGINT semantics), frame inspection, intervention (modify arguments / inject a message), and time travel as "replay + debug session". Architecturally it is an **ordinary subscriber on the signal bus**: the kernel agent loop is untouched, and with no controller attached there is zero behavioral change (`kernel/debug.py:18-20`). When a signal hits a breakpoint, the subscriber blocks the emit — the run suspends in place while the event loop idles, remaining available to the Web/SSE/CLI command channels. The debugger and telemetry share the same signal WAL: telemetry answers "what happened", the debugger answers "what is happening right now — and should we change it".

## 2. Motivation and Background (Why)

**Why it exists.** Agent runs are non-deterministic, long-lived, and burn budget per step. Reading `trace.jsonl` after the fact answers "what happened" but not "what was it about to do", and it certainly cannot catch a wrong argument before it lands. The executive summary lists "runs cannot be observed" among the structural defects; the debugger is telemetry's interactive counterpart — the same signal stream, upgraded from read-only audit to a read-write paused scene.

**Why it is not in the kernel loop.** The debugger satisfies none of the three microkernel criteria (the loop cannot advance without it / it is a permission-verdict arbitration point / it is the only common channel between subsystems) — it is a pure observer plus an occasional intervener. It is therefore attached optionally via `KernelBuilder.debug_controller()`, and subscribes to nothing when absent (`kernel/debug.py:3-21`). The kernel boundary did not widen by an inch for a debugging need.

**Why not through the SidecarSupervisor.** This was the pivotal routing decision. The sidecar SYNC channel has a 2 s timeout and is fail-closed (`sidecars/supervisor.py`; see `docs/DEBUGGER.md:28-30`) — an interactive pause would inevitably time out and be judged a veto. The semantics also differ: a sidecar is rule-based supervision (millisecond-scale, automatic), while the debugger is interactive control (minute-scale, human-driven). The `Pause` verdict is not reused either: today it is a "labeled abort" serving BudgetGuard degradation notices, not a real pause (`docs/DEBUGGER.md:34-35`). So `DebugController` subscribes to the bus directly, **after** Telemetry/SignalHub (subscription order is record order): before any pause, the triggering signal has already landed in `trace.jsonl` and been pushed over SSE, so the frontend's trace always contains the pause point itself (`kernel/debug.py:18-20`).

**Why GDB semantics.** The mapping table already casts a run as a process and the SkillFrame stack as a call stack; GDB is then the natural mental model. Breakpoints, step into/over/out, and SIGINT-style pause are interaction semantics validated over three decades — inventing new ones would only add learning cost.

## 3. Problem Statement (What It Solves)

1. **A wrong argument becomes fact at dispatch.** A run reaches `pre:tool.call` carrying a bad argument (e.g. `code = "result = 0 + 1"`); the postmortem trace shows it, but the tool already ran. We need to suspend the run at that exact moment, inspect the arguments, patch them, and let it through.
2. **"Next step" is undefined inside nested frames.** When F1 invokes sub-skill F2, "step over" must skip the entire F2 subtree and stop at F1's next step, while "step into" must stop at F2's first step. Without frame stack bookkeeping, neither semantic is implementable.
3. **A runaway run can only be killed, not examined.** When budget burns through or behavior goes wrong, the only tool is abort; there is no GDB-style "deliver a signal and stop at the next safe point".
4. **Crash scenes evaporate.** While paused, the checkpoint has not been written yet, so inspection must read the kernel's in-memory state; and if the process dies, even the terminal checkpoint may not exist — recovery used to be "final state" rather than "the last N steps".
5. **Stop points are not reproducible.** You stumble onto the bug at step 7 during live debugging; next time you want to go straight back to step 7 — but the LLM is non-deterministic, so re-running changes the whole stop-point sequence.
6. **Command delivery across host threads.** The Web REST thread and the run worker thread run separate event loops; calling `set()` on the session's `asyncio.Event` from the REST thread trips the `loop.call_soon` thread check (`run_manager.py:822-829`).

## 4. Design and Mechanism (How)

### 4.1 The pause protocol: blocking emit

The debugger does not modify the runner's agent loop. `DebugController` subscribes directly to the `InProcessSignalBus` at assembly time (the bus awaits every subscriber in subscription order); when a signal hits a breakpoint, the subscriber awaits an `asyncio.Event` — **the emit never returns, and the run suspends in place**, the same pattern as `ask_supervisor` (`kernel/debug.py:5-9`):

```
run worker event loop                     debug frontend (REST thread / CLI)
───────────────────────                   ────────────────────────────────
runner ── emit(pre:tool.call) ──▶ SignalBus (awaits subscribers in order)
                                    ├─ Telemetry  → trace.jsonl flushed
                                    ├─ SignalHub  → SSE pushed
                                    └─ DebugController._on_signal
                                         │ breakpoint hit (_match_breakpoints)
                                         ▼
                                    DebugSession._pause (kernel/debug.py:333)
                                    record pause_point → state=paused
                                    await resume_event.wait() ──────┐
              ◀── emit never returns; run suspended in place ───────┤ resume()
              ◀── event loop idles: SSE/REST/CLI stay usable ───────┤ (delivered over
                                         │ event.set() ◀────────────┘  the cross-thread
                                         ▼                            command bridge)
                                    _resume_verdict: None (release) /
                                    Modify(patch) / Stop(reason)
                                         ▼
                                    emit returns; run continues
```

Three invariants protect this protocol (`docs/DEBUGGER.md:37-47`):

- **Error isolation**: exceptions inside the controller are caught, logged, and treated as release — a debugger failure must not take the run down (`kernel/debug.py:491-492`);
- **Automatic release**: when the run ends (`run.finished`/`run.aborted`) or the frontend detaches, every blocked wait is released — the run never dangles (`kernel/debug.py:481-486`);
- **Verdict arbitration constraint**: a `Stop` verdict is returned only on `pre:step` / `pre:tool.call` (`_STOP_VERDICT_SIGNALS`, `kernel/debug.py:65`), the two points where the runner arbitrates pre verdicts; a stop received at any other pause point is recorded in `_pending_stop` and lands on the next arbitrable pre signal (`kernel/debug.py:262-264`). On `pre:frame.push/pop` the debugger always returns `None` — those verdicts carry corrective semantics, so the debugger only observes (though step_out may **pause** at `pre:frame.pop`; it simply issues no verdict).

### 4.2 The breakpoint model

One breakpoint = **kind + name glob + hit count** (`Breakpoint`, `kernel/debug.py:74-88`; conditional breakpoints are out of scope for v1):

| kind | triggering signal | match semantics |
|---|---|---|
| `step` | `pre:step` (every step of every frame) | ignored |
| `tool_call` | `pre:tool.call` | tool-name glob (fnmatch, e.g. `fs_*`) |
| `skill_invoke` | `pre:skill.invoke` | skill-name glob |
| `error` | `post:tool.call` with `payload.ok=false` | ignored |

> Note: executive summary §6.2 describes breakpoints as "three kinds (pre:tool.call / step / error)"; the code actually defines four (adding `skill_invoke`, `kernel/debug.py:52`). The code wins.

Each hit increments `hits` (the SSE stream emits `bp_hit` differential events); `enabled=False` breakpoints do not participate. P5 adds the **one-shot step-count breakpoint** (`until=N`, `step` kind only): the first N-1 hits only count, and the Nth `pre:step` pauses and self-disables (`kernel/debug.py:299-306`) — the rewind entry point of "go straight to step N".

### 4.3 Stepping semantics and pause-at-any-time

The full resume command set: `continue` / `step_into` / `step_over` / `step_out` / `stop` (`RESUME_COMMANDS`, `kernel/debug.py:60`). Step boundaries rely on the debugger's own **frame stack bookkeeping** (`pre:frame.push` enters, `pre:frame.pop` leaves, `kernel/debug.py:249-260`):

| command | stop decision (`_step_stop`, `kernel/debug.py:309-331`) |
|---|---|
| `continue` | release; stop again at the next breakpoint |
| `step_into` | the next `pre:step` in **any** frame (enters the child frame's first step) |
| `step_over` | the next `pre:step` in the **same** frame (frame_id naturally filters out child frames); if the frame has no more steps, stop at its `pre:frame.pop` |
| `step_out` | the current frame's `pre:frame.pop` |
| `stop` | `Stop` verdict → `RunAborted` (normal abort path; checkpoint persisted) |

`pause` is not a resume command (issuable only while `running`, the mirror image of resume): GDB signal semantics — it sets `_pause_requested`, and the run suspends at the **next arbitrable signal** with `reason="pause"` (`kernel/debug.py:204-215`). While the run is inside an LLM call or tool execution no signals are emitted, so the pause lands on the first pre signal after that call completes — exactly GDB's "stop at the next safe point". When paused at `pre:frame.pop`, that frame is considered already popped and the "current frame" is the top-of-stack parent (`_current_frame_id`, `kernel/debug.py:385-389`).

Session state machine: `armed` (awaiting run binding) → `running` ⇄ `paused` → `detached` (terminal) (`kernel/debug.py:67-71`).

### 4.4 Intervention (paused state only)

- **modify** (patch tool arguments): valid only when paused at `pre:tool.call`. The blocked emit returns `Modify(patch)`; the runner merges the patch into `call.args` and dispatches as usual (`kernel/debug.py:168-181`) — it changes **this one call's** arguments, not the skill and not the context; the session resumes immediately (internally `resume(continue)`).
- **inject** (inject a message): via `RunControl.inject_message`, appends a `role=user, source=injected` message to the given frame (default: the paused frame), visible to the next LLM call; the session resumes after injecting (`kernel/debug.py:183-191`). Under replay, injection is equally real (subsequent LLM responses still replay from the script).

Both interventions affect only **this run's in-memory state**; the artifacts (trace/checkpoint) faithfully record the post-intervention trace — intervention never forges history.

### 4.5 Web backend: session API, the cross-thread command bridge, and SSE

`POST /api/debug/sessions` opens a session and starts a run; the request body has two mutually exclusive forms: `{skill, input, breakpoints?}` (live) or `{replay_run_id, until_step?, breakpoints?}` (replay, response carries `mode: "replay"`); at most one active session per run, conflicts return 409 (`app.py:1071-1120`). **Breakpoints are registered before the run starts** — a mock brain advances in milliseconds, so adding breakpoints after launch would race and miss early signals; break-on-start is therefore deterministic (`run_manager.py:686-694`). The endpoint family: session snapshot `GET`, breakpoint add/remove, `command` (paused only; `pause` running only), `modify` / `inject` (paused only), `frames/{fid}` (live in-memory state — while paused the checkpoint has not been persisted), `stream` (SSE), `rerun`, and `DELETE` (detach and release).

**The cross-thread command bridge** (`_in_debug_loop`, `run_manager.py:822-846`): the session's `asyncio.Event` is bound to the run worker thread's loop, and a direct `set()` from the REST thread trips the thread check; `_debug_loops` records each session's worker loop (captured at `run.started`), and resume/modify/inject/detach are all delivered via `run_coroutine_threadsafe` and awaited. When the worker loop is gone (run finished) the call runs directly — no wait is blocked then, and an `Event.set()` with no waiters is a pure memory operation.

**The debug SSE stream** (`app.py:1226-1301`): on connect it emits a `state` snapshot (`prev_state` deliberately ignores the current state, so an already-paused session re-emits `paused` immediately for late clients), then polls the session's in-memory state every `_DEBUG_SSE_POLL=0.1 s` (`app.py:78`) and emits differential events: `bp_hit` (on hits growth) → `paused` / `resumed` → `run_end` (after detach it briefly waits, up to 50 rounds, for the terminal status to land on disk); 15 s of idleness yields a keepalive (`_SSE_KEEPALIVE`, `app.py:74`). Why polling instead of awaiting: the `wait_paused` event is bound to the worker loop and the SSE loop cannot await it — polling is the simplest reliable scheme (`app.py:1228-1231`).

**rerun** (`run_manager.py:722-754`): driven by the `origin` backfilled when a live session is created (skill/input/skill_set/startup breakpoints, `kernel/debug.py:122-124`); replay and CLI sessions have no origin → 400, and the snapshot's `rerunnable` flag tells the frontend. A still-active old session is wound down first: if paused, via the `stop` command (normal abort path, checkpoint persisted), waiting for the stop verdict to land (capped by `_RERUN_STOP_TIMEOUT=10 s`, `run_manager.py:115`) before detaching — detaching immediately would let `_resume_verdict` see DETACHED first and swallow the stop, turning the old run's abort into a clean finish (a timing race, `run_manager.py:737-739`).

### 4.6 Time travel and periodic checkpoint (P5)

**Time travel = replay + debug session.** From the artifact directory (`meta.json` + `trace.jsonl` + `checkpoint.json`), `build_mock_script` rebuilds a MockProvider script, `replace_providers` swaps the kernel's provider face, and a **new run** starts with the original skill/input (read from `meta.json`) with a debug session attached (`run_manager.py:756-800`) — breakpoints, stepping, inspection, and intervention behave exactly as in live debugging, and the stop-point sequence reproduces bit-for-bit. `until_step=N` registers a one-shot step-count breakpoint so the run pauses exactly at the Nth `pre:step`. The replay boundary (module docstring of `host/shared/replay.py`): **the LLM never touches a real API — recorded values are replayed; tool side effects genuinely re-run**.

**Periodic checkpoint**: `RunConfig.checkpoint_interval` (default 0 = off) mounts a `PeriodicCheckpointer` that counts `post:step` and every N steps calls `dump_checkpoint`, **overwriting** `checkpoint.json` — the file's semantics are "the latest scene", moving the crash recovery point from "final state" to "the last N steps"; dump failures are caught and logged, never taking the run down (`docs/DEBUGGER.md` §7). Configuration paths: TOML `[run] checkpoint_interval = 20`, CLI `--checkpoint-interval N`, Web `overrides` (this run only).

### 4.7 The debug console (P4)

Pages `#/debug` (home: skill/input/startup-breakpoint form, `debug-home.js`) and `#/debug/<session_id>` (`debug-view.js:1-19`): a top control bar (session state + Continue/Into/Over/Out/Stop, disabled unless paused; a ⟳ rerun button appears when `rerunnable`, `debug-view.js:435-436`), a left column with the call stack (paused frame highlighted) and breakpoint list, a center execution trace (reusing trace.js's row language; clicking the gutter toggles a breakpoint; the paused row is highlighted with ▶), and a right inspector (pause-point payload + the selected frame's messages + Modify/Inject forms, `debug-view.js:686-713`). Data sources: the session snapshot, the run signal stream (the debug SSE carries no run signals, so the trace follows `/api/runs/{run_id}/signals`), and the debug SSE; EventSource is primary with a 2 s snapshot-polling fallback, and a 2 s ticker permanently refreshes the trace (redrawing only when the signal count changes, so scrolling is undisturbed). The modify form pre-fills the current args; edits survive re-renders at the same pause point (fingerprint unchanged) and reset only at a new one (`debug-view.js:649-696`).

## 5. Results and Verification (Effects)

The debugger is covered by **44 tests** across three layers: kernel primitives `tests/kernel/test_debug.py` (14), the CLI REPL `tests/cli/test_debug.py` (12), and the Web API `tests/web/test_debug_api.py` (18) — all green within the project baseline (812 Python tests + 24 frontend test files). Stop-point assertions share one factual anchor: the signal sequence of `demo.fib` with n=3 (F1 step1 → invoke F2 (one step, then pop) → `pre:tool.call(system.python.exec)` inside F1 step2 → F1 step3 final answer → F1 pop) (`tests/web/test_debug_api.py:21-24`). Key assertions:

- **Full flow** (`test_debug_full_flow`): break-on-start (hits accumulate, frame stack non-empty) → step_into enters the child frame (depth=2, new frame_id) → step_out stops at the child's `pre:frame.pop` → continue hits the tool_call breakpoint (`reason="breakpoint"`) → live frame inspection reads in-memory messages → modify rewrites `code` to `"result = 41"`, the run finishes, and **the result follows the patched argument**: `{"seq": [0, 1, 41]}`; the session auto-detaches at run end (:84-145);
- **step_over boundaries**: stops at the same frame's next `pre:step` (skipping the entire child frame in between); at the frame tail it stops at `pre:frame.pop` (:148-174);
- **inject leaves a trace**: the injected message appears in the frame context as `role=user, source=injected`, and frame_id defaults to the paused frame (:182-204);
- **pause semantics**: pausing a free-running session lands at the next arbitrable signal with `reason="pause"`; if the run finished before the pause arrived, the request returns 409 — both branches are correct semantics (:297-314);
- **error-semantics matrix**: unknown session 404, unknown breakpoint kind 400, unknown command 422, command/modify/inject while not paused 409, a second session per run 409, validation errors 200 + `failed` (:337-404);
- **SSE stream**: `state` → `bp_hit(hits=2)` → `paused` → `resumed` → `run_end(done)`; an already-paused session re-emits `paused` immediately on connect (:426-486);
- **time travel**: a replay session's response carries `mode="replay"` and its stop-point sequence matches live; `until_step=2` goes straight to the 2nd `pre:step` (the child frame); the replayed result matches the original run bit-for-bit (`{"seq": [0, 1, 1]}`) (:495-538).

Ripple effects: the debugger is the **signal contract's first interactive consumer** — it proved that "subscription order + blocking emit" suffices for minute-scale human-in-the-loop interaction, and the same pattern went on to carry the escalation confirmation's suspend-resume loop; introducing `rerunnable`/`origin` also made "re-open a run with its creation parameters" a general host-layer capability; and the CLI REPL's pattern — same event loop as the run, commands fed via `asyncio.to_thread(stdin.readline)` so stdin can be scripted — became the regression-testing paradigm for interactive CLI components (`docs/DEBUGGER.md:129-131`).

## 6. Limitations and Boundaries

1. **No conditional breakpoints**: matching is kind + name glob + hit count only (`until` is the sole counting variant); "stop on the 3rd hit only if the args contain X" is not expressible (`docs/DEBUGGER.md` §8).
2. **Tool side effects genuinely re-run under replay**: time travel keeps the "LLM mock replay + real tool re-execution" boundary — tools with side effects (files, shell, network) run again during replay; the `replay_records` wiring is not done. Replay debugging is not a sandbox.
3. **Periodic checkpoint keeps only "the latest scene"**: `checkpoint.json` is overwritten; no `checkpoint.<step>.json` historical series is kept, so "back to step N" can only be achieved by replay (re-execution), never by restoring a historical snapshot (state restoration).
4. **Pause precision is bounded by signal granularity**: while the run is inside an LLM call or tool execution no signals are emitted, and the pause lands only after that call completes — a 30 s LLM call means up to 30 s of pause latency; the call itself cannot be interrupted.
5. **stop lands late at non-arbitrable points**: issuing stop while paused at `post:*` / `pre:frame.pop` / `pre:skill.invoke` defers the verdict to the next `pre:step`/`pre:tool.call`, during which the run advances further (possibly including an LLM call).
6. **A paused run has no watchdog**: `resume_event.wait()` has no timeout; closing the browser tab does not detach (SSE reconnection is a feature), so an abandoned paused session leaves the run suspended indefinitely until an explicit `DELETE` / `rerun` winds it down.
7. **Minimal concurrency control**: one session per run, one debug end (Web or CLI); concurrent resumes from multiple clients are lock-free and last-writer-wins.
8. **A narrow intervention surface**: modify can only patch the current call's arguments at `pre:tool.call` (not the LLM request, not working memory); inject can only append a user message. Neither persists — the skill definition and future runs are unaffected.
9. **No replay form on the console home page**: replay sessions can only be initiated via API/CLI (the console at `#/debug/<session_id>` works normally for replay sessions once opened).

## 7. References

- Design document: `docs/DEBUGGER.md` (pause protocol / breakpoints / stepping / intervention / time travel / known limits)
- Kernel primitives: `agent_os/src/agent_os/kernel/debug.py` (`DebugController` / `DebugSession` / `Breakpoint`)
- CLI frontend: `agent_os/src/agent_os/host/cli/debug.py` (`agent-os debug` REPL)
- Web backend: `agent_os/src/agent_os/host/web/run_manager.py` (P3 session management + cross-thread command bridge; P5 replay/rerun), `agent_os/src/agent_os/host/web/app.py` (`/api/debug/*`, :1071-1301)
- Web frontend: `agent_os/src/agent_os/host/web/static/js/components/debug-view.js`, `debug-home.js`, `breakpoint-list.js`
- Replay: `agent_os/src/agent_os/host/shared/replay.py` (`build_mock_script` / `replace_providers`; the module docstring is the authoritative boundary)
- Periodic checkpoint: `agent_os/src/agent_os/kernel/checkpoint.py` (`PeriodicCheckpointer`)
- Tests: `agent_os/tests/kernel/test_debug.py`, `agent_os/tests/cli/test_debug.py`, `agent_os/tests/web/test_debug_api.py`
- Adjacent documents: `docs/DESIGN.md` (kernel and signal bus), `docs/RUNNERS.md` (hosts), `docs/DEBUG-UI-THEMES.md` (debug console theme tokens)
