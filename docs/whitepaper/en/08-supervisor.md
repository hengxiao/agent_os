# Supervisor: Ruling Router & Human-in-the-Loop

> Chapter 08 · Status: Implemented (S1 kernel mechanism / S2 host channels / S3 policy completion; a few doc statements diverge from the implementation — noted in-line, code wins) · Sources: `docs/SUPERVISOR.md` (v2), `agent_os/src/agent_os/supervisor/{manager,inbox}.py`, `agent_os/src/agent_os/api/v1/supervisor.py`, `agent_os/src/agent_os/kernel/runner.py`

## 1. Overview

The supervisor is Agent OS's ruling-routing subsystem, oriented toward the **caller's
caller** — the entity that started the run. Any frame can request a ruling from its
superior via the `ask_supervisor` pseudo-tool; the question is routed *out of the agent*
to the host that launched the run (a Web user, a CLI user, an embedding application, or
even another agent). The asking frame suspends in place until the answer is written back
as a tool result and the frame re-enters its loop. It is one of the subsystems outside
the microkernel (executive summary §3.1), sharing the "supervision" duty with sidecars:
sidecars are *rule-based* supervision (deterministic, fail-closed), while the supervisor
is *decision routing* (authority lives outside the agent). The kernel's escalation
confirmation (chapter 07) reuses the same loop and is its most important consumer.

## 2. Motivation and Background (Why)

**Why `ask_human` was not enough.** STDLIB W5 had sketched an `ask_human` channel of the
form "suspend run → human answers → resume", with two structural limitations
(`docs/SUPERVISOR.md` §1.1): the superior was hard-wired to "a human", unable to express
"the program that called me"; and the implementation was misread as **run-level**
suspension, whereas the commonly needed granularity is **frame-level** — only the asking
frame should wait; sibling branches must not freeze.

**The v1 → v2 correction of "caller".** v1 designed the default superior as the *parent
frame's LLM*, with a YIELD hand-off inside the call stack. v2, after correction,
redefines the caller as **the caller of the whole agent** (the host that started the run)
and deletes YIELD, simplifying the semantics considerably (§4.2). The trade is symmetric:
no authority chain exists inside the kernel — only a single outbound routing hop;
cascading across nested agents is left entirely to the callers.

**Why this is not in the kernel.** By the microkernel criterion (executive summary §2.2),
the kernel keeps only the flow-control parts: in-place suspend/resume of frames, pairing
of pending asks, run-completion judgment — the loop cannot advance without them. "Yield
to *whom*" (channels) and "how long to wait, what on timeout" (policy) are externalized
to the subsystem (`docs/SUPERVISOR.md` §1.3): the kernel provides the mechanism by which
a frame *can* yield; the subsystem decides *to whom*. The three channels — Web inbox,
CLI protocol, embedded handler — thus evolve without touching kernel code.

## 3. Problem Statement (What It Solves)

1. **No "ask your superior" primitive**: an expense-approval skill facing a ¥5000 order
   above its automatic limit must either hard-code thresholds in the prompt or let the
   LLM decide on its own — neither is auditable. What is needed is one line:
   `ask_supervisor({"question": "Approve the ¥5000 expense?", "options": ["approve","reject"]})`.
2. **Over-freezing of run-level suspension**: when a background spawned branch asks a
   question, the main stack is still making progress; run-granularity suspension would
   freeze the whole frame tree on a single question.
3. **Power loss inside the waiting window**: the process crashes after the question is
   sent but before the answer returns. On resume, that `ask_supervisor` call has no tool
   result — if the generic "crashed mid-dispatch" rule filled in an interrupted
   placeholder, the frame would observe a "failure" that never happened.
4. **Authority chains of nested agents**: a child agent's superior is the agent that
   called it, which may itself escalate further; modeling the chain depth inside the
   kernel would force the kernel to understand arbitrary external organization.
5. **Answers that miss the spec**: the caller replies "maybe" while options are
   `["approve","reject"]` — a format error must neither crash the child frame nor be
   silently accepted.
## 4. Design and Mechanism (How)

### 4.1 The contract: Question / Answer / SupervisorHandler

The contract is frozen in `api/v1/supervisor.py`: `Question` (:55-73) carries
question_id/question/context/options/urgency/frame_id/run_id, plus the additive field
`kind` (`"question"` for LLM-initiated asks, `"escalation"` for kernel-initiated
escalation confirmations, `docs/ESCALATION.md` §3); `Answer` (:76-80) is a TypedDict of
`{answer, decided_by}`; `SupervisorHandler` (:83-87) is a Protocol of
`async (Question) -> dict`. `ask_supervisor` is a **kernel-intercepted pseudo-tool**
(`api/v1/supervisor.py:22-24`): like `python_orchestrate`, arbitration must bind the
calling frame and its manifest, so it never enters the Tool Registry; a manifest declares
`permissions.tools: [ask_supervisor]`, and declaration is authorization. The LLM-visible
JSON Schema (:29-52) is appended by the ContextManager only when the manifest declares it
*and* a supervisor channel is installed (`runtime/builder.py:208-210`) — with no channel
the tool is not advertised, avoiding an LLM call that is doomed to fail.

### 4.2 The loop: suspend → route-out → answer → resume

```
asking frame              kernel runner              SupervisorManager        caller channel
  │ ask_supervisor(args)    │                          │                      │
  ├────────────────────────▶│ ① whitelist check (:554-562)                     │
  │                         │ ② persist pending ask in working                 │
  │                         │   ["_pending_ask"] (:624-630)                    │
  │                         ├─ supervisor.ask ────────▶│ ③ supervisor.ask signal
  │                         │  (await = in-place suspend)├─ handler(Question) ─▶│ ④ inbox / CLI / handler
  │                         │                          │   asyncio.wait_for    │   (timeout_s)
  │                         │                          │◀── {answer,decided_by}─┤ ⑤ options validation;
  │                         │                          │ ⑥ supervisor.answer   │   re-ask with
  │                         │◀── {"answer","decided_by"}─┤    signal            │   previous_error (≤2 tries)
  │◀─ tool result, re-enter─┤ ⑦ clear _pending_ask     │                      │
  │   frame loop (:638-646) │                          │                      │
```

**In-place suspension (the v2 simplification)**: `await supervisor.ask` naturally blocks
this frame's loop — no YIELD mechanism is needed; the parent frame stays at its own await
point and sibling spawned frames keep running (`kernel/runner.py:604-611`). Run-completion
judgment is therefore structurally safe: a run turns DONE only after the whole frame tree
returns, and its status stays RUNNING while the handler has not answered (comment at the
same location). **Note**: `docs/SUPERVISOR.md` §2.2 states that "when all active frames
are suspended the run turns PAUSED" and the frame's "status = SUSPENDED"; the supervisor
path performs **no status transitions** in code (`RunStatus.PAUSED` and
`FrameStatus.SUSPENDED` exist as enum members at `api/v1/run.py:53` and
`api/v1/frames.py:47`, but only other paths such as the debugger use them). This chapter
follows the code: suspension is await-blocking, not a state-machine migration; "waiting
on the superior" is surfaced via the inbox pending list, not a run status field.

**Channel selection order** (`docs/SUPERVISOR.md` §2.3, implemented in
`host/web/run_manager.py:405-418`): run-level injected handler → assembly-level handler →
host default channel. The three baseline channels:

| Caller | Channel implementation | decided_by / channel label |
|---|---|---|
| Embedding application | Injected `SupervisorHandler` (`runtime/builder.py:100-116`) | default `"handler"` |
| Web user | `InboxChannel` (`supervisor/inbox.py`); `GET /api/supervisor/pending` + `POST /api/supervisor/{id}/answer` (`host/web/app.py:649-669`) | `"host:web-ui"` / `"inbox"` |
| CLI user / coding agent | Single-line JSON `{"type":"supervisor.ask",...}` on stderr + one line from stdin, in-process loop (`host/cli/main.py:57-87`) | `"host:cli"` / `"cli"` |

`InboxChannel` is a suspending inbox: `__call__` *is* the handler contract — the Question
enters the pending table and suspends on an `asyncio.Future`; the Web request thread
calls `answer`, which settles cross-thread via `loop.call_soon_threadsafe`
(`supervisor/inbox.py:88-101`); on timeout or cancellation the question leaves the inbox
via `finally` (:47-51); the pending list sorts urgency=high first, then FIFO (:79). The
CLI channel deliberately has no cross-process pending/answer subcommands — a
single-process CLI has no inbox to query; the asynchronous inbox form is carried by the
Web host (`docs/SUPERVISOR.md` §2.3).

### 4.3 Timeout, fallback, and format validation

`SupervisorManager.ask` (`supervisor/manager.py:71-127`) is the policy core:

| Situation | Behavior | Code anchor |
|---|---|---|
| Normal answer | Emit `supervisor.answer`, return `{answer, decided_by}` | manager.py:113-116 |
| `asyncio.wait_for` timeout + `on_timeout="fail"` | Emit `supervisor.timeout`, return `{ok:false, error:{kind:"supervisor_timeout", retryable:true}}`; the frame may degrade on its own | manager.py:129-150 |
| Timeout + `on_timeout="default_answer"` | Close the loop with the configured fallback answer, `decided_by="policy:default"` | manager.py:139-141 |
| Answer outside options | Re-ask the **caller** with `previous_error` (never re-ask the child frame); initial ask + one re-ask, at most 2 handler calls (`_MAX_ASK_ATTEMPTS=2`); still invalid → fail outcome | manager.py:40, :106-127 |
| Web route pre-validation | Answer outside options → 400, question stays pending | run_manager.py:935-953 |

The re-ask cap of 2 (rather than unbounded) is a deliberate trade: a caller that misses
the spec twice has most likely misunderstood the protocol, and further re-asks would just
spin; the structured error is handed to the child frame, which degrades as its prompt
prescribes (the mock brain in the tests demonstrates a `fallback_defer` path).

### 4.4 Persistence: checkpoint / resume / replay

A pending ask is serialized with its frame as `frame.context.working["_pending_ask"] =
{question_id, call_id, question, context, asked_at}` (`kernel/runner.py:624-630`) —
reusing the working-memory structure, no checkpoint schema change. The entry is cleared
only on normal closure (including timeout/fallback); exceptions (power loss,
cancellation) keep it for re-asking on resume (:634-635). During recovery,
`_settle_pending_ask` (:648-673) runs **before** the generic unpaired-call settlement
(checkpoint.py:31-36, :362-364): it locates the unpaired ask call by call_id, re-enters
`_ask_supervisor` (fresh question_id) to ask the caller again, and writes the real answer
as the tool result — pairing atomicity (invariant 2) closes naturally, with no
interrupted placeholder. Under replay, `supervisor.answer` is already in the trace and is
replayed by recorded value without asking a second time (the CLI replay path does not
even install a supervisor channel, `host/cli/main.py:276`).

### 4.5 Signals and the shared escalation loop

Three signals (`api/v1/signals.py:94-96`): `supervisor.ask` (payload carries `channel`
and `kind` labels, manager.py:90-105), `supervisor.answer` (answer truncated to 200
characters, manager.py:179), and `supervisor.timeout`. The channel label is declared by a
`supervisor_channel` attribute on the handler (manager.py:63-65), making "which channel
carried this question" auditable in the trace. Kernel escalation confirmations reuse the
same loop: `_escalate` converts an `EscalationRequest` into a supervisor question with
`kind="escalation"` (`kernel/runner.py:1003`), and is fail-closed when no supervisor
channel is installed and no Grant matches (:957-965) — an escalation nobody can review
is an escalation nobody guards.

## 5. Effects and Verification (What)

**Test evidence** (all green, part of the 812-case Python baseline):

- `tests/kernel/test_supervisor.py`: **12 cases** covering the kernel side of the §9
  anchor list — handler round-trip; in-place suspension (the parent's await point does
  not move while suspended); a pending ask blocks premature run completion; timeout-fail
  returns a retryable structured error; default_answer fallback closes with a policy
  provenance mark; out-of-options answers re-ask the caller; an undeclared manifest gets
  PERMISSION_DENIED; checkpoint + resume re-asks and closes; no orphan tool results
  across suspend/resume (pairing invariant); three cases for the signal channel label.
- `tests/web/test_supervisor_channel.py`: **4 cases** — inbox pending lists the question,
  answering resumes the run with `result == {"decision": "approve"}`; the trace records
  `channel == "inbox"` end-to-end; an out-of-options answer gets HTTP 400 and the
  question stays pending; the LLM-visible schema contains `ask_supervisor`.
- `tests/examples/test_supervision_nested.py`: **4 cases** — nested supervision: the
  outer handler starts another run inside itself, the outer LLM's answer flows back to
  the inner frame, and the `decided_by` chain (`"agent:team_lead"`) is observable.

**Real example**: `agent_os/examples/supervision/` — a `junior_clerk` expense clerk asks,
and a `team_lead` supervisor agent rules by policy (auto-approve ≤ ¥3000):
`outer.py 5000` → reject, `outer.py 500` → approve. The two levels run on **two
independent kernels**; the inner kernel sees only a single handler round-trip.

**Ripple effects**: the escalation system (ESCALATION E1/E2) builds its entire
confirmation gate on the supervisor loop (kind=escalation, the Web inbox escalation card,
CLI kind pass-through) instead of creating a second channel; the Web debugger's
breakpoint suspend-resume reuses the same in-place-suspension pattern (noted in
`kernel/debug.py:9`). The supervisor is thereby the single carrier of the human-in-the-
loop: every point needing authority outside the agent converges on this one routing port.
## 6. Limitations and Boundaries (Limits)

1. **Status migration not implemented (doc/code divergence)**: the "run turns PAUSED /
   frame becomes SUSPENDED" language of `docs/SUPERVISOR.md` §2.2 does not exist in code;
   suspension is expressed purely by await. Cost: runs "waiting on the superior" cannot
   be filtered by run status — UIs must rely on the inbox pending list; the PAUSED and
   SUSPENDED enum members are dead letters for the supervisor path.
2. **The §9 anchor list is not fully covered**: item 7 ("a question from a spawned
   background frame suspends only that branch") has no dedicated test
   (`tests/kernel/test_blackboard_spawn.py` covers spawn itself only); the property rests
   on the structural argument that in-place suspension touches no other frame, not on an
   assertion.
3. **The CLI channel is a blocking single-line protocol**: one question at a time, one
   stdin line per answer; an EOF on stdin reads as an empty string, which normally fails
   options and, after re-asks are exhausted, closes as an invalid answer
   (`host/cli/main.py:61-63`). A cross-process asynchronous inbox is explicitly out of
   scope — a human must be present at the terminal in CLI scenarios.
4. **Handler granularity stops at the run**: no per-frame override (§10 open question 5);
   all frames in a run share one caller channel, so different subtrees cannot be routed
   to different arbiters.
5. **No schema-graded answers**: options are string arrays only, and free-text versus
   structured answers are not graded (§10 open question 3); validation is a bare string
   comparison — case/whitespace normalization is left to the channels.
6. **The HumanApproval sidecar has not been sunk**: rule-based "human answers" and
   decision routing remain two separate systems (§10 open question 1), so hosts must
   understand both the sidecar verdict and the supervisor ruling interaction forms.
7. **Timeout policy is a single point**: `asyncio.wait_for` settles at the deadline with
   no reminder and no escalation (escalation to "one level up" is deliberately not a
   kernel concept; cascading is left to callers); `urgency=high` only sorts first in the
   inbox, with no interruptive presentation (§10 open question 4).
8. **Signals carry only an answer digest**: the `supervisor.answer` payload is truncated
   to 200 characters (manager.py:179); the full form of a long answer lives only in the
   frame context and result.json, so auditing a long ruling means piecing artifacts.

## 7. References

- Design docs: `docs/SUPERVISOR.md` (v2, incl. the §9 anchor test list and §10 open
  questions), `docs/ESCALATION.md` (§3, escalation reusing the loop)
- Contract: `agent_os/src/agent_os/api/v1/supervisor.py`, `api/v1/signals.py:94-96`
- Subsystem: `agent_os/src/agent_os/supervisor/manager.py`, `supervisor/inbox.py`
- Kernel integration: `agent_os/src/agent_os/kernel/runner.py:554-673, :957-1003`,
  `kernel/checkpoint.py:31-36, :362-364`
- Assembly and config: `agent_os/src/agent_os/runtime/builder.py:100-116, :196-210`,
  `runtime/config.py:295-314`
- Host channels: `agent_os/src/agent_os/host/web/app.py:649-669`,
  `host/web/run_manager.py:405-418, :928-953`, `host/cli/main.py:57-87, :276`
- Tests: `agent_os/tests/kernel/test_supervisor.py` (12 cases),
  `agent_os/tests/web/test_supervisor_channel.py` (4), `tests/examples/test_supervision_nested.py` (4)
- Example: `agent_os/examples/supervision/` (nested supervision)
