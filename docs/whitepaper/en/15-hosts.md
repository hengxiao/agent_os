# Hosts: Thin CLI/Web Hosts & the Artifact Contract

> Chapter: 15 · Status: Implemented (RUNNERS.md R1–R4 all landed, plus the D3/D4/D6, S2, P3/P5 and L1–L5 increments; a few designed items remain unimplemented and are marked as such in §6) · Sources: `docs/RUNNERS.md`; `agent_os/src/agent_os/host/{cli,shared,web}/`; `agent_os/tests/{cli,web}/`

## 1. Overview

The host layer is the outermost ring of the architecture (the "host layer" in §3.1 of the executive summary): two **thin hosts** — a CLI aimed at coding agents and a Web UI aimed at humans — plus the shared artifact organization and read layer in `host/shared`. The kernel is a library, not a service; a host does exactly three things: **assemble** (config → `build_kernel`), **invoke** (`kernel.run`/`resume`), and **present** (read run artifacts) (docs/RUNNERS.md §1). The two hosts do not talk to each other through any IPC; they interoperate through a single **artifact contract**: the four files under `.agent-os/runs/<run_id>/` are the only persistent form of a run, and a run produced by the CLI can be opened in the Web UI for RCA as-is.

## 2. Motivation & Background

**Why hosts stay out of the kernel.** The microkernel admission test reserves the kernel for flow control, permission control, and IPC (executive summary §2.2). None of the host's three duties qualifies: assembly is not required for loop advancement (an embedder can construct a kernel directly), presentation is not an arbitration point, and neither host is the sole shared channel between subsystems — the signal bus is. RUNNERS.md §1 states this as a hard rule: "if a feature requires kernel changes, file it as a kernel project; do not patch around it in the host." The host layer is therefore the litmus test for whether the microkernel boundary held: every datum it consumes (the `kernel.run` return value, bus signals, the trace WAL, checkpoints) must already be part of a kernel contract.

**Why two hosts instead of one.** The consumers differ, and their output contracts pull in opposite directions. A coding agent wants machine-assertable JSON, semantic exit codes, and file paths — every line of human-readable output is noise. A developer wants live push, frame-tree/timeline visualization, and one-click RCA — a JSON stream is unreadable. Merging the two would deform both contracts. What is genuinely shared — the artifact layout, the RunRecord schema, replay/diff — sinks into `host/shared`, while each presentation layer stays minimal (RUNNERS.md §2.4).

**Why artifacts first.** Making files — not an API or a database — the carrier of debug data yields three chained properties: the hosts interoperate for free (same layout); historical runs can be rebuilt from the artifact directory after a process restart (cold data, §4.2); and downstream tools (replay, diff, RCA) all read offline, with no live service required. This is the "determinism is an engineering goal" axiom (executive summary §2.2) expressed at the host layer.

## 3. Problem Statement

1. **Scriptable runs and assertions**: after editing `skills.yaml`, a coding agent must run a skill and assert on the outcome. Human-readable output cannot be scripted — `agent-os run demo.fib --input '{"n":4}' --json` must emit exactly one JSON document on stdout, and exit codes must distinguish "your input was wrong" (2) from "the run failed" (3) (`host/cli/main.py:141`).
2. **Scattered failure data**: after a failure, "what the model saw at that step" lives in the checkpoint, signal ordering lives in the trace, and the final status lives in the result file. Without a unified layout and read layer, every RCA session is manual archaeology (`host/shared/artifacts.py:192`).
3. **Non-deterministic reproduction**: LLM calls cannot simply be re-issued. A run that failed online must be replayable locally without touching the real API, and must answer "did my fix change the behavior?" (`agent-os replay` + `agent-os diff`, RUNNERS.md §3.4).
4. **Live observation must not block the run**: the browser watches a run advance frame by frame while the kernel is executing and signals flow on the bus; fan-out to N SSE clients must never let one slow client drag down the run (`host/web/run_manager.py:244`).
5. **Host processes are ephemeral**: after a Web process restart, historical runs must survive, and a run lost mid-flight must still be recognizable as "died in progress" from its artifacts (`host/web/app.py:317`).
6. **Error classification**: "run never started" (unknown skill, input fails schema) and "run failed after starting" are different accident classes. The host must re-raise the former and capture the latter into a RunRecord — not collapse both into 500 or exit code 1 (`host/shared/artifacts.py:120`).

## 4. Design & Mechanism

### 4.1 The artifact contract: four files + RunRecord

Every run lands in one directory, written by `execute_run`/`execute_resume` (`host/shared/artifacts.py:50`):

```
.agent-os/runs/<run_id>/
├── meta.json         # {run_id, skill, input, host: "cli"|"web", started_at, ...}
├── trace.jsonl       # signal WAL (archived from the telemetry dir by run_id; empty if absent)
├── checkpoint.json   # kernel.checkpoint snapshot at finish/abort (frames carry full context)
└── result.json       # {status, result, error, usage summary} (usage taken from the checkpoint)
```

This division of labor is the "debug data taxonomy" (RUNNERS.md §2.3): result answers success/failure, trace answers ordering, checkpoint answers "who called whom, and what did the model see." **The checkpoint is the shared data source for RCA and resume** — it is snapshotted even when the run fails, as long as the run object exists (`artifacts.py:63`), because frame context is precisely the failure scene.

The machine-facing contract is the **RunRecord** (`host/shared/runrecord.py:22`): `{"v": 1, run_id, status, result, error, usage, frames, artifacts{dir,trace,checkpoint}}`. Every CLI subcommand serializes through the same `dumps`, so a coding agent depends on exactly one versioned schema (RUNNERS.md §3.5).

**The error-classification rule** is the densest few lines in this chapter (`artifacts.py:120-129`): `execute_run` subscribes to `run.started` to capture the run_id; when an exception arrives, if a run_id exists (frames had begun advancing) it is captured into the RunRecord (`failed`/`aborted`), otherwise it propagates to the host for classification. The same decision maps to CLI exit codes and to HTTP semantics:

| Class | Example | CLI (RUNNERS.md §3.3) | Web (`app.py`) |
|---|---|---|---|
| Success | status=done | 0 | 200 + RunRecord |
| Never started (validation) | SkillLoadError, input fails schema | 2 | 200 + `{"status":"failed","error":...}` (`app.py:546`) |
| Failed/aborted after start | skill returned an error, RunAborted | 3 | (inside the RunRecord) |
| Host/infrastructure | missing config, provider assembly failure | 4 (`_InfraError`, `main.py:46`) | 500 / assembly-time exception |
| Request itself invalid | unknown skill_set, unknown breakpoint kind | 2 | 400/404/409 |

### 4.2 CLI: the machine surface

Entry point `agent-os = agent_os.host.cli.main:main` (`pyproject.toml:31`), built on argparse with zero new dependencies (RUNNERS.md §3.5). Subcommands: `run / trace / inspect / resume / replay / diff / skills {validate,list} / lab validate / debug` (`main.py:447`). Output contract (`main.py:125`): with `--json`, stdout is exactly one RunRecord line; by default it is a human summary plus a **final JSON line** — humans and machines both parse by convention, and the convention is "read the last line."

The CLI also serves as one of the supervisor's **adjudication channels** (`main.py:57`): when a run suspends, the question is written to stderr as a single JSON line (a protocol line the coding agent can parse, including `kind` to distinguish escalation confirmations), and one line read from stdin is the answer — the loop closes inside the single-command process. `replay` deliberately does not inject this handler: a replay follows the trace's recorded values and never asks twice (`main.py:276`).

### 4.3 replay & diff: deterministic reproduction

Replay's alignment rule exploits a kernel property: the runner only **appends** LLM responses to frame context, so the N-th `post:llm.response` signal belonging to frame F in the trace corresponds to the N-th `role=="assistant"` message of frame F in the checkpoint (`host/shared/replay.py` module docstring). `build_mock_script` (`replay.py:49`) pulls those messages in signal order to rebuild a MockProvider script; `finish_reason` is derived from the presence of tool_calls, and usage comes from the signal payload. `replace_providers` (`replay.py:96`) registers the MockProvider under the original model's prefix (`"mock/fib"` → `"mock"`), so model routing matches the original run naturally. **Trade-off**: the script is taken from the checkpoint's assistant messages rather than trace payloads — the payload is a summary, while the context message is the complete response (with tool_calls structure). The cost is that a trace/checkpoint mismatch fails replay outright (`ValueError` → exit code 2) instead of guessing.

`diff_runs` (`replay.py:115`) compares two signal sequences element-wise on `(name, payload.skill, payload.tool, payload.ok, payload.depth)`, ignoring dynamic values such as frame_id/ts; `first_divergence` reports the first diverging index (or the shorter length when one side is a prefix). It is a **query, not a verdict**: producing the report always exits 0 (`main.py:312`). Replay's boundary is explicit: it covers only the LLM face — tool side effects re-execute against the real environment (RUNNERS.md §3.4).

### 4.4 Web: RunManager and SSE fan-out

```
browser SPA (static files, no build step)
   │  REST /api/*                SSE /api/runs/{id}/stream
   ▼
FastAPI route layer (app.py) ──────── calls only the host/shared read layer + RunManager
   ▼
RunManager (in-process, run_manager.py)
   ├─ one kernel per run: build_kernel (config re-read) → dedicated thread
   │    └─ execute_run starts its own event loop via asyncio.run (decoupled from request loops)
   ├─ signal fan-out: bus subscription "*" → SignalHub (ring buffer 2000 + subscriber queues)
   └─ state: in-memory _active + artifact dir; history rebuilt from artifacts after restart
```

Three key mechanisms:

- **One thread, one kernel per run.** The design doc (RUNNERS.md §4.2) said "run as an asyncio task"; the implementation switched to a dedicated thread with its own `asyncio.run` inside (`run_manager.py:519`), because TestClient creates one portal per request and uvicorn request loops come and go — writing back a run's terminal state must not depend on any request loop staying alive (module docstring, `run_manager.py:1-13`). Code wins over the doc. Re-assembling a kernel per run also yields free isolation: `reload_skills` only affects runs created afterwards; a running run pins the old version (`run_manager.py:979`).
- **SignalHub's atomic snapshot** (`run_manager.py:269`): `subscribe` registers the subscriber and snapshots the buffer under the same lock, so a new client receives replay followed by live signals with **no gap and no duplication**; the publishing side (the worker thread) delivers via `loop.call_soon_threadsafe`, and subscribers whose loops have closed are silently dropped — subscriber failure must never drag down a run. Run termination posts the `HUB_CLOSED` sentinel and the SSE emits `event: end` (`app.py:1058`); a 15-second keepalive defeats proxy timeouts (`app.py:74`).
- **The `_StopBridge` assembly shim** (`run_manager.py:130`): since M4 the kernel only wires `kernel.ctl` when sidecars exist, yet Web stop requires every run to have a ctl. RunManager attaches a no-op ASYNC sidecar that subscribes to nothing, purely to push the builder down the sidecar assembly path — a typical example of the host solving its own problem within kernel contracts rather than asking the kernel to change its rules.

The other cross-thread site is the debug command bridge: a debug session's `asyncio.Event` is bound to the run worker's loop, and a REST thread calling `set()` directly would trip the thread check, so resume/modify/inject are all delivered into the worker loop via `run_coroutine_threadsafe` (`run_manager.py:822`).

**Cold data and half-written windows**: `_list_runs` (`app.py:317`) merges history rebuilt from the artifact directory with in-memory state (a directory with meta.json but no result.json counts as in-flight — the signature of a crash or power cut); SSE for historical runs replays from trace.jsonl (`app.py:1050`). Artifact writes are not atomic; readers degrade on `try/except` to "in flight, retry" (`app.py:281`), a deliberate avoidance of atomic-write plumbing in a dev tool (simplicity first).

**Authentication** (`app.py:497`, RUNNERS.md §4.5): single-user localhost needs no auth; with `--token`, the whole site sits behind a Bearer gate using constant-time comparison (`secrets.compare_digest`), with `?token=` allowed because EventSource cannot set custom headers. `serve.py:67` **refuses to start** when binding a non-loopback address without a token — this service can execute skills carrying shell_exec, and exposing it unauthenticated is an open RCE.

### 4.5 The host as identity and adjudication channel

The data-layer identity (docs/DATA-AUTHZ.md §2.2) is constructed by the host: the CLI uses `cli_principal()` (the local user, `main.py:163`); the Web uses `web_single_user_principal` (login name from the `[web].user` config key, `run_manager.py:430`). The supervisor channel is selected in the order "run-level injection > assembly-level injection > process-shared InboxChannel" (`run_manager.py:409-413`): the Web inbox is the default host channel and comes with assembly; the CLI injects `_cli_supervisor` over the stderr/stdin protocol. Escalation confirmations and human adjudication thus require no kernel awareness of the host's shape — this is where "humans in the loop are first-class" (executive summary §2.4) lands at the host layer.

## 5. Effects & Verification

**Test baseline**: `tests/cli/` — 4 files, 28 cases; `tests/web/` — 12 files, 80 cases; 108 test functions total (116 after parametrization). Full run for this chapter: **116 passed in 12.14s**. Key assertions:

- `tests/cli/test_run.py:46`: successful run → exit code 0, `"v": 1`, all four artifacts present, exact usage.steps and frame counts;
- `tests/cli/test_run.py:61/68`: validation error → 2, run failure → 3; `tests/cli/test_replay.py`: the record–replay–diff loop;
- `tests/web/test_runs_api.py:103`: SSE replays the buffer first and terminates with `event: end`; `test_runs_api.py:65`: the list includes history rebuilt from artifacts;
- `tests/web/test_rca_control.py:59`: RCA on a vetoed run locates the adjudicated frame; `:115/127`: stop/resume behavior;
- 24 frontend `static/tests/*.test.mjs` files (run directly under Node, no build), covering the frame tree, timeline, RCA panel, inbox, and more — these are the "24 frontend test files" cited by the executive summary.

**Live example**: `instance/agent-os.toml` (dual configuration: a real Kimi Code endpoint plus a mock demo profile) and `instance/run-web.sh` (pulls a token from the kimi-code credential store, then starts `agent-os-web`) are the mechanisms of §4 in deployed form.

**Ripple effects**: the artifact contract became shared ground for later systems — the debugger's time travel (P5) consumes trace+checkpoint directly to rebuild a replay script (`run_manager.py:756`); the Skill Lab's test-run/G4 smoke reuse the same assembly line and replay mechanism (`app.py:104`); and `signal_row` keeps SSE frames, ring-buffer rows, and trace.jsonl lines structurally identical (`run_manager.py:214`), so one frontend parser serves three data sources.

## 6. Limitations & Boundaries

1. **Single user on localhost; no multi-user or permissions model.** RUNNERS.md §7 lists these as non-goals; the token gate only prevents accidental exposure and is not a multi-tenant scheme. Persistent queues, distributed workers, and production deployment shapes are all out of scope.
2. **In-flight runs are lost on restart.** In-memory state is not persisted ("dev tool, acceptable" per §4.2); orphaned directories show up as "running" and can only be resolved by manual judgment or resume.
3. **Replay covers only the LLM face.** Tool side effects (fs/shell/docker) re-execute for real, so the external world may differ between two replays; the `--sandbox` enforcement mode from RUNNERS.md §3.4 is **designed but not implemented**.
4. **Ring-buffer truncation.** Hub capacity is 2000 entries (`run_manager.py:122`); SSE replay of a very long run loses the head (trace.jsonl stays complete, but the in-flight fallback path of `/signals` serves only the tail).
5. **Artifact writes are not atomic.** Half-written windows are absorbed by reader-side degradation (§4.4) and can make a finished run look "in flight" under extreme timing; skipping temp-file-plus-rename is a deliberate simplicity trade-off.
6. **The thin-host boundary is kept by discipline, not tooling.** The ruff/import-lint guard of RUNNERS.md §2.4 is marked "optional" and is not configured; host code already imports kernel public pieces such as `kernel.debug`/`kernel.checkpoint`/`supervisor` (beyond the letter of "imports only api.v1"), so the boundary is maintained by review.
7. **Residual doc-vs-code drift (code wins)**: the CLI `trace --kind` filter is unimplemented (the Web side has it, `app.py:363`); `resume` accepts only a checkpoint path, not a run_id; `stderr.log` from the §2.2 layout is never written.
8. **Known multi-set limitation**: code-skill handlers are imported lazily at call time, so same-named handler modules across skill sets are unsupported — rename to avoid (`run_manager.py:47`).
9. **Debug SSE polls** (0.1s) instead of being event-driven (simplicity first, `app.py:77`); browser end-to-end behavior is still hand-tested, and the frontend unit tests do not cover real SSE timing.

## 7. References

- Docs: `docs/RUNNERS.md` (host design baseline); `docs/WEB-UI.md` (presentation layer); `docs/DEBUGGER.md`, `docs/SKILL-DEV.md`, `docs/SUPERVISOR.md`, `docs/DATA-AUTHZ.md` (adjacent system boundaries)
- Source: `agent_os/src/agent_os/host/cli/main.py`, `host/cli/debug.py`; `host/shared/artifacts.py`, `host/shared/replay.py`, `host/shared/runrecord.py`; `host/web/app.py`, `host/web/run_manager.py`, `host/web/rca.py`, `host/web/serve.py`, `host/web/static/`; `agent_os/pyproject.toml` (entry points and the `web` extra)
- Tests: `agent_os/tests/cli/` (4 files), `agent_os/tests/web/` (12 files), `host/web/static/tests/` (24 `*.test.mjs` files)
- Instance: `instance/agent-os.toml`, `instance/run-web.sh`
