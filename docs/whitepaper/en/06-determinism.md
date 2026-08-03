# 06 Signals, Telemetry & Determinism Engineering

> Chapter: 06 · Status: Implemented (signal bus / signal catalog / JSONL WAL / checkpoint & resume / periodic checkpoint / replay / diff; OTLP and RL-trajectory exporters, PII-redaction hook, MetricsCollector are designed but not implemented) · Sources: `agent_os/src/agent_os/api/v1/signals.py`, `agent_os/src/agent_os/kernel/signals.py`, `agent_os/src/agent_os/kernel/checkpoint.py`, `agent_os/src/agent_os/telemetry/jsonl_exporter.py`, `agent_os/src/agent_os/host/shared/replay.py`, `agent_os/src/agent_os/host/shared/artifacts.py`, `docs/DESIGN.md` §5.1/§10, `docs/RUNNERS.md` §3.4

## 1. Overview

The signal bus is the kernel's IPC channel: the kernel broadcasts a `Signal` at every key point of the agent loop, and sidecars, Telemetry, the debugger, and the Web SSE fan-out are all subscribers. Telemetry persists the signal stream as an append-only JSONL WAL; the checkpoint serializes the run state and the whole frame tree — including every frame's context and accounting — into a versioned snapshot. The three determinism tools (resume, replay, diff) are all built on these two on-disk artifacts. Ownership is layered: the bus lives in the kernel (the IPC clause of the microkernel criterion), the WAL and snapshots belong to the Telemetry subsystem, and replay/diff live in the shared host layer. Determinism here is an engineering stack spanning three layers, not a single mechanism.

## 2. Motivation and Background (Why)

The executive summary lists "non-reproducible processes" as one of the four structural defects of agent systems: LLM calls are inherently non-deterministic, so when something goes wrong there is no way to replay the scene or reconstruct "what the model actually saw." The mechanisms in this chapter answer that defect directly, and their shape was fixed by four concrete architectural decisions.

**Why Telemetry is not a sidecar.** Sidecar semantics are fire-and-forget: an ASYNC sidecar failure is logged and must never take the run down (docs/DESIGN.md §5.3). The WAL and checkpoints, by contrast, require *guaranteed-persistence* durability — checkpoint recovery puts them on the reliability-critical path. Telemetry was therefore promoted to its own subsystem and exists as a "privileged subscriber of the bus" (docs/DESIGN.md §10 preamble, `api/v1/signals.py:4`). One bus, two reliability tiers: the observation plane may fail silently; the recording plane may not.

**Why "recovery is not a re-run."** LLM calls cost money and tool calls have side effects. Restarting from scratch after a crash wastes tokens and, worse, can repeat irreversible side effects (sending the same email twice). Recovery must therefore guarantee that "completed work is not repeated, and the remaining number of LLM calls is exactly countable" (`kernel/checkpoint.py:1-5`). This in turn dictates that a checkpoint must store each frame's full context, not just a frame skeleton.

**Why replay/diff sit in the host layer, not the kernel.** Under the microkernel criterion the kernel keeps only flow control, permission control, and IPC. Replay (rebuilding a MockProvider script) and diff (structured comparison of two runs) *consume* artifact data; they neither advance the loop nor arbitrate anything, so they live in `host/shared/replay.py`, shared by the CLI and Web runners (the thin-host boundary of docs/RUNNERS.md §2.4).

**Why determinism is an axiom, not luck.** Design axiom four states that "determinism is an engineering goal, not luck" (executive summary §2.2; the WAL principle of docs/DESIGN.md §10.2: "the trajectory is the agent's entire state"). The direct corollary: every state the kernel can observe — the signal stream, the frame tree, pending adjudications, the escalation grant ledger, run-level tool state — must have an on-disk form, and every reproduction scenario (resume/replay) must be mechanically rebuildable from it.

## 3. Problem Statement (What It Solves)

Each problem below corresponds to a reproducible failure scenario.

- **P1 Power loss discards progress.** The process is killed at the 6th LLM call of fib(5): the 5 completed calls and all finished child frames are wasted, and a re-run pays the full price again. The test simulates the power cut with an exception (`tests/telemetry/test_trace_checkpoint.py:70-87`).
- **P2 Broken call pairing.** Power dies after the ASSISTANT message issues a tool_call but before the TOOL result is written back: the transcript holds a dangling call, and re-entering the loop directly would ship a malformed history to the provider.
- **P3 Child done, parent unaware.** A child frame is DONE and persisted in the checkpoint, but the parent's context only holds the crash-synthesized error observation for that call: re-running the child duplicates its side effects; keeping the error shows the parent LLM a history that contradicts fact.
- **P4 Crash while suspended.** The process dies after an `ask_supervisor` or escalation confirmation was issued but before the answer returned: this is not "interrupted mid-dispatch," so it must not get an `interrupted` placeholder — that would lie to the model that its adjudication was aborted (`kernel/checkpoint.py:31-37`).
- **P5 Behavioral drift is undecidable.** After changing a skill implementation, there is no structured way to answer "did behavior change?"; regression checking degenerates to reading logs by eye.
- **P6 Production failures are not reproducible locally.** Without the exact LLM inputs, a failed production run cannot be replayed frame-by-frame for RCA.
- **P7 Observer contagion.** If a subscriber (debugger, SSE fan-out) raises and the exception propagates up the bus, a healthy run is taken down by its own observation plane; the fault domains must be isolated.
- **P8 Recovery point too late.** If a checkpoint is written only at run teardown, crashing at step 999 means starting over; the recovery point must be movable to "the last N steps."

## 4. Design and Mechanism (How)

### 4.1 The signal catalog: a frozen event table

Signal names follow `<phase>:<event>`: the `pre:` prefix is synchronous and veto-capable, `post:` is asynchronous observation (`api/v1/signals.py:1-5`). The full catalog `SIGNAL_NAMES` holds **31** names (`api/v1/signals.py:99-131`), covering run lifecycle (3), frame stack (4), steps (2), LLM (3), tools (2), sub-skills (2), escalation (3), logic execution (2), compression (2), inlining (1), blackboard (2), budget (2), and supervisor adjudication (3). The `Signal` struct has only five fields — `name/run_id/frame_id/payload/ts` (`api/v1/signals.py:134-142`) — deliberately thin; all semantics live in the name and payload conventions. Note: the catalog list in docs/DESIGN.md §5.1 predates the code (it lacks the three escalation signals, `post:context.inline`, and the three supervisor signals); the code's `SIGNAL_NAMES` is authoritative.

### 4.2 The bus: faithful broadcast, fault isolation

`InProcessSignalBus` (`kernel/signals.py:28-57`) is the M0 foundation: subscribers are kept in registration order, and `emit` awaits every matching handler in that order (`*` matches all, exact names, and `prefix.*` suffix wildcards — `kernel/signals.py:19-25`), returning a list of results. Two hard rules:

1. **Handler exceptions are swallowed and logged** (`kernel/signals.py:53-56`): signals are an observation channel, and a subscriber failure must never take the run down — the direct answer to P7.
2. **The bus does not arbitrate**: for `pre:*` signals the kernel takes the "first non-Allow verdict" (around `kernel/runner.py:317`); the bus only collects results faithfully. The deterministic order of multiple SYNC sidecars is guaranteed by priority plus subscription order (docs/DESIGN.md §5.2).

### 4.3 The WAL: persisted form of the signal stream

`JsonlTelemetrySink` (`telemetry/jsonl_exporter.py:25-107`) appends every signal of a run to `<traces_dir>/<run_id>.jsonl`, in a versioned line format:

```
{"v": 1, "type": "header", "schema": "agent_os.trace/1"}        # first line of each file
{"v": 1, "type": "signal", "name", "run_id", "frame_id", "ts", "payload"}
```

Durability comes in three parts: line-buffered files (every line written immediately, `jsonl_exporter.py:52`); an explicit `os.fsync` in `flush()` (line buffering only reaches the page cache — WAL semantics demand real disk, `jsonl_exporter.py:81`); and `close_run`, which fsyncs and closes the handle at run teardown (preventing fd leaks and half-line copies during archival, `jsonl_exporter.py:83-96`). At run end the host archives this file as the artifact directory's `trace.jsonl` (`host/shared/artifacts.py:39-47`).

### 4.4 Checkpoint: the trajectory is the entire state

`dump_checkpoint` (`kernel/checkpoint.py:126-172`) serializes the run (status/usage/result/run_state/escalation grant ledger) and all frames (frame_id/skill/parent_id/depth/result/error/originating call_id/trust tier/principal/usage/**full context**) into schema-v1 JSON. A frame's `status` field is a progress label from the checkpoint's viewpoint: `"done"` means the frame finished, or never completed a single LLM call (`usage.steps == 0` — no preservable progress); `"running"` means it holds unfinished progress (`kernel/checkpoint.py:119-123`).

The recovery algorithm (`resume_from_checkpoint`, `kernel/checkpoint.py:321-388`):

```
load doc → version check (reject unless v == 1) → rebuild Run (restore usage/grants/run_state)
  → register all frames back into the frame tree
  → frames with a settled result are popped OK and skipped (credential, not label)
  → non-DONE frames, deepest first:
      ① _settle_pending_ask        (pending adjudication: re-ask, no interrupted placeholder)
      ② _settle_pending_escalation (pending escalation: re-walk the gate)
      ③ _settle_unpaired_calls     (settle broken pairings; table below)
      ④ _execute_frame re-enters the loop with full context
  → root frame completes → run.finished (run.started is NOT re-emitted)
```

The three settlement rules for broken pairings (`kernel/checkpoint.py:281-318`, answering P2/P3):

| Post-crash call shape | Basis | Recovery action |
|---|---|---|
| Already has an `ok=True` tool result (or non-JSON result) | Settled | Untouched |
| Only an error observation, and the child frame with matching call_id is DONE | Child recovered first | **Rewrite in place** with the child's real result |
| No tool result and no child frame | Died mid-dispatch | Append an `interrupted` placeholder result |
| `working` holds `_pending_ask` / `_pending_escalation` | Crashed while suspended (P4) | Re-ask / re-walk the gate first; the three rules above do not apply |

Three trade-offs matter:

- **Skipping is decided by `result`, not the `status` label** (`kernel/checkpoint.py:350-352`): the label is only a progress hint; the settlement credential is the idempotence basis, so a repeated recovery is safe.
- **Rewrite in place rather than delete-and-rebuild** (P3): the strict one-to-one tool_call/tool_result pairing and message order are preserved; the parent LLM sees the fact that "the call succeeded," not a doctored history.
- **Deepest-first settlement**: child frames recover to DONE before their parents, so a settling parent can pick up the child's real result (`kernel/checkpoint.py:356-357`).

P8 is answered by `PeriodicCheckpointer` (`kernel/checkpoint.py:175-222`): it subscribes to `post:step` and overwrites `checkpoint.json` every N signals — the file means "latest scene," with no historical snapshots; write failures are caught and logged only (same isolation spirit as the bus); with `checkpoint_interval = 0` the host mounts nothing and behavior is unchanged (`host/shared/artifacts.py:115-118`).

### 4.5 replay and diff: deterministic reproduction and regression evidence

Replay's alignment rule rests on a structural fact: the runner only *appends* LLM responses to a frame's context, so the Nth `post:llm.response` signal belonging to frame F in the trace corresponds to the Nth assistant message of frame F in the checkpoint (`host/shared/replay.py:1-11`). `build_mock_script` pulls those messages in signal order to rebuild a MockProvider script (`replay.py:49-93`); `replace_providers` swaps the kernel's whole provider surface for a single MockProvider whose registration name is the prefix of the configured model (`"mock/fib"` → `"mock"`), so model routing naturally matches the original run (`replay.py:96-103`).

diff compares two runs' signal sequences element-wise on the tuple `(name, payload.skill, payload.tool, payload.ok, payload.depth)`, ignoring dynamic values like frame_id/ts, and adds result equality plus a usage summary, reporting `first_divergence` (the first diverging index; if one sequence is a prefix of the other, the shorter length; `replay.py:106-135`). diff describes itself as "query, not verdict": the report carries no exit semantics — whether a divergence is a regression is a human call.

The overall data flow:

```
kernel loop (runner.py) emits a Signal at every key point
        ▼
InProcessSignalBus (awaits in subscription order; handler errors swallowed)
  ├─ SYNC sidecars: pre:* verdicts → kernel takes first non-Allow
  ├─ JsonlTelemetrySink ── <run_id>.jsonl (version header + line buffering + fsync)
  ├─ PeriodicCheckpointer ── overwrites checkpoint.json every N steps
  └─ host subscribers (CLI run_id capture / Web SSE fan-out / debugger)
        ▼ run teardown, _finalize_run (artifacts.py:50-87)
Artifact quartet: meta.json / trace.jsonl / checkpoint.json / result.json
  ├─ resume: rebuild frame tree → settle broken pairings → re-enter loop (no repeated work)
  ├─ replay: trace response order × checkpoint assistant messages → MockProvider script
  └─ diff: element-wise five-tuple comparison → first_divergence
```

## 5. Effects and Verification (Results)

The core behavior is pinned by 11 anchor tests, all re-run green while writing this chapter (0.78s):

- **WAL and recovery** (`tests/telemetry/test_trace_checkpoint.py`, 3 cases): the trace's first line is the version header and contains the full lifecycle signals; after fib(5) is cut at the 6th call, a fresh kernel resumes from the checkpoint to the correct result `{"seq": [0,1,1,2,3]}`, and the post-resume MockProvider receives exactly **5** new calls (`len(mock2.recorded) == 5`) — "recovery is not a re-run" asserted as an exact count; the checkpoint file contains the version, run usage, done/running frames, and full frame contexts.
- **Periodic checkpoint** (`tests/kernel/test_periodic_checkpoint.py`, 3 cases): with interval=2 the "latest scene" exists by step 2 but not step 1; interval=0 mounts nothing (zero behavior change); the config field passes through `build_kernel`.
- **replay/diff** (`tests/cli/test_replay.py`, 5 cases): a recorded fib(4) replays to the same result with an empty diff against the original run (both `result_equal` and `signals_equal` true), under a new run_id; two runs with different inputs are judged divergent; two runs with identical inputs diff empty.

The real configuration surface: `[run].checkpoint_interval` in `instance/agent-os.toml` enables periodic snapshots; the CLI surface is `agent-os run/resume/replay/diff` (`host/cli/main.py:271-320`). Overall test baseline: `pytest --collect-only` currently collects **854** cases (the executive summary, written earlier, records 812; the live count is authoritative).

Ripple effects: debugger breakpoint hits become pendings and ride the same suspend-persist-resume loop (docs/DEBUGGER.md); the Web SSE fan-out and RCA pages are `"*"` subscribers of the bus (docs/RUNNERS.md §4.2); the escalation grant ledger and run-level tool state ride the checkpoint as additive fields — a live instance of the "schema v1 unchanged, fields may be added" contract discipline (`kernel/checkpoint.py:139-141`); the Skill Registry's pre-publish verification gate makes "replay + evaluator confirmation" the trust precondition of self-evolution (docs/DESIGN.md §6.2, designed but not implemented).

## 6. Limitations and Boundaries

- **Single-process bus.** `InProcessSignalBus` is the M0 foundation with no cross-process semantics; a multi-replica deployment has no shared WAL, and distributed tracing waits on the OTLP exporter (designed, not implemented).
- **Checkpoints do not capture the outside world.** Recovery assumes externally visible tool side effects are still valid; idempotence is a production-standard convention (L2 must be idempotent, docs/TIER-STANDARDS.md), not something the system enforces or rolls back.
- **replay covers only the LLM surface.** Tool side effects (fs/shell/docker) execute against the real environment (the docs/RUNNERS.md §3.4 boundary); if the environment changed since the original run, replay may diverge. `--sandbox` can force shell/python onto the docker backend but cannot freeze the world.
- **The WAL has a durability window.** Line buffering guarantees only the page cache; `fsync` happens in `flush`/`close_run` (`jsonl_exporter.py:81`). A machine-level power loss (not a process crash) can lose the most recent lines.
- **Schema v1 is one-way compatible; there is no migrator.** resume rejects any other version with `ValueError` (`kernel/checkpoint.py:329-330`); old checkpoints missing newer fields recover with conservative defaults (tier defaults to `none` — never amplifying privilege; principal defaults to `None` — v1 single-user semantics, `checkpoint.py:233-246`), at the cost that newer semantics do not apply to old files.
- **Periodic snapshots overwrite.** The "latest scene" keeps no history; progress inside the crash window (fewer than N steps) is still replayed. N is a manual trade between recovery-point freshness and IO cost.
- **diff is not a semantic-equivalence verdict.** It compares only the five-tuple: tool argument values, token counts, and message text changes are not divergence (usage is listed separately for humans); ignoring frame_id/ts also puts concurrency interleaving differences outside the comparison surface.
- **Observer failures are silent.** Handler exceptions go to the log only (`kernel/signals.py:53-56`); if Telemetry persistently fails to write, trace lines go missing with no run-level alarm — the flip side of fault isolation.
- **Designed-but-unimplemented items.** The OTLP/OpenInference mapping, RL-trajectory training export, PII-redaction hook, and MetricsCollector (docs/DESIGN.md §10.2) are doc-level reservations; `TelemetrySink.snapshot` and `JsonlExporter.export` are `NotImplementedError` in code (`jsonl_exporter.py:105-122`), with snapshot semantics actually carried by `Kernel.checkpoint`.

## 7. References

- Docs: `docs/DESIGN.md` §5.1 (signal catalog), §5.2-5.3 (sidecar contract and supervision semantics), §10 (Telemetry), §14.1 (contract freeze); `docs/RUNNERS.md` §2.2 (artifact layout), §3.4 (replay and golden debugging)
- Source: `agent_os/src/agent_os/api/v1/signals.py` (catalog and `Signal`), `agent_os/src/agent_os/kernel/signals.py` (bus), `agent_os/src/agent_os/kernel/checkpoint.py` (checkpoint/resume/periodic snapshots), `agent_os/src/agent_os/kernel/runner.py` (emission points and verdict collection), `agent_os/src/agent_os/telemetry/jsonl_exporter.py` (JSONL WAL), `agent_os/src/agent_os/host/shared/replay.py` (replay/diff), `agent_os/src/agent_os/host/shared/artifacts.py` (artifact quartet and wiring), `agent_os/src/agent_os/host/cli/main.py` (CLI commands)
- Tests: `agent_os/tests/telemetry/test_trace_checkpoint.py` (3 cases), `agent_os/tests/kernel/test_periodic_checkpoint.py` (3 cases), `agent_os/tests/cli/test_replay.py` (5 cases)
