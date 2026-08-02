# Agent OS Technical Whitepaper

> Version: v1.0 (2026-08)
> Form: executive summary (English; per-system deep chapters follow in this directory; 中文: [../zh/00-executive-summary.md](../zh/00-executive-summary.md))
> Scope: prototype, core philosophy, architecture, subsystems, problems solved, status & roadmap
> Basis: every claim herein is grounded in the repository's implemented code and
>   design documents (see Appendix A). "Implemented" and "designed but not yet
>   implemented" are always distinguished.

---

## Abstract

Agent OS is a **microkernel runtime for LLM agents**. It systematically maps
operating-system structure onto the agent domain: a Skill is a function, a Tool
is a system call, a SkillFrame is a stack frame, a Run is a process — and the
kernel keeps exactly three things: flow control, permission control, and IPC.
On top of this skeleton, the project addresses four structural defects of
contemporary agent systems: **actions cannot be arbitrated** (permissions and
trust), **processes cannot be reproduced** (determinism and replay), **state
cannot be isolated** (context pollution and cross-layer prompt injection), and
**quality cannot be guaranteed** (engineering standards and an admission gate
for skills). This document presents the prototype's motivation, the design
axioms, the layered architecture, the subsystem contracts, the trust & safety
model, and the implementation status as of v1.0.

## 1. Prototype and Problem Statement

### 1.1 Prototype

Agent OS began as a minimal runtime answering one question: how do you let an
LLM safely call tools — and other agents? A skill registry, a tool registry, a
kernel driving the agent loop, and a signal bus that records everything. As the
debugger, privilege escalation, data authorization, and the Skill Lab landed
one by one, the prototype grew into a full runtime and development platform —
yet the kernel's boundary never widened. This is direct evidence that the
microkernel criterion (§2.2) has been enforced without exception.

### 1.2 Problem Statement

Four structural problems recur when building production-grade LLM agents:

1. **Actions cannot be arbitrated.** Once an agent can call tools, untrusted
   content (web pages, user input, compromised upstream output) can induce it
   to perform irreversible operations — deleting data, stopping processes,
   publishing externally. Conventional answers are either full access
   (dangerous) or full denial (useless); there is no arbitration graduated by
   the semantics of the operation.
2. **Processes cannot be reproduced.** LLM calls are non-deterministic; when
   something goes wrong there is no way to replay the scene. When token cost
   or step counts blow up, "what did the model see at that moment" is
   unanswerable after the fact.
3. **State cannot be isolated.** When skills nest, untrusted low-level content
   and privileged high-level operations share one context, and prompt
   injection amplifies across the call chain.
4. **Quality cannot be guaranteed.** Skills (prompt assets) are produced
   without engineering standards or an admission gate: missing metadata,
   over-broad permissions, and instruction-injection patterns detonate at
   runtime.

Agent OS answers with: a microkernel that makes arbitration points unique, a
frame model that makes isolation physical, checkpoint/replay that makes
execution reproducible, and a three-tier trust model with an admission gate
that makes safety and quality enforceable.

## 2. Core Philosophy

### 2.1 One Mapping Table Throughout

The system's skeleton is a single mapping table from programming/OS concepts
to Agent OS concepts. Every name, boundary, and mechanism derives from it, so
the conceptual system cannot contradict itself:

| Programming / OS concept | Agent OS | Meaning |
|---|---|---|
| function | **Skill** | named, parameterized, composable unit of behavior |
| system call | **Tool** | atomic privileged operation touching the outside world, arbitrated by the kernel |
| call stack / frame | **SkillFrame stack** | one frame per Skill invocation, holding private context |
| local variables | **FrameContext** | per-frame message history + working memory, isolated from other frames |
| dynamic linker | **Skill Registry** | discovery, validation, dependency resolution, loading, writing of Skills |
| syscall table + seccomp | **Tool Registry + permission model** | name-based dispatch, argument validation, capability whitelists |
| device driver | **Provider** | hides vendor differences among LLM APIs |
| memory management / GC | **Context subsystem** | context assembly + compression + prefix-cache stability |
| interrupt / watchdog | **Sidecar** | signal-triggered supervisor: veto, pause, halt |
| process | **Run** | one complete agent run (root frame and its subtree) |
| CPU / ALU | **Logic Kernel** | the sole execution point of logical code (code skills / dynamic code) |
| journald / auditd | **Telemetry** | signals persisted as a WAL; export and checkpoints |
| file system | **Memory** | cross-run memory and knowledge (permission-filtered retrieval) |
| shared memory / MQ | **Blackboard** | intra-run inter-frame state and messages, with concurrency control |
| microkernel | **Kernel Runner** | flow control and permission control only; everything else external |

### 2.2 Four Design Axioms

1. **Microkernel.** The kernel keeps exactly three things: **flow control**
   (loop advancement, frame stack, dispatch arbitration, safe-point
   cancellation, concurrency primitives, budget-abort adjudication, recovery
   circuit-breaking), **permission control** (three-way intersection checks,
   trust routing, verdict arbitration, credential scoping), and **IPC**
   (signal bus, RunControl, inter-frame addressing). The admission test: the
   loop cannot advance without it (F), or it is a permission/veto arbitration
   point (P), or it is the sole shared channel among subsystems (I).
   Corollary: the kernel owns the loop; Skills own policy.
2. **Subsystems do not know each other.** Cross-subsystem collaboration goes
   only through the contract layer (`api/v1` Protocols) or the signal bus,
   mediated by the kernel. When the compressor needs an LLM summary, it calls
   the `ProviderManager` protocol — never a concrete provider.
3. **Contract first, baselines replaceable.** Every subsystem defines a
   minimal baseline implementation satisfying its contract; third-party
   implementations use the baseline as reference. `api/v1` is a frozen
   surface: new fields are always additive with defaults.
4. **Determinism is an engineering goal, not luck.** Checkpoints serialize
   with frames, signals persist to a WAL, and replay rebuilds a MockProvider
   script from the trace — any run can be deterministically replayed (tool
   side effects excepted; boundary in §7).

### 2.3 Least Privilege and Graduated Trust

Permission is not a boolean but a surface graduated by **side-effect
semantics**: tools self-declare a permission level (READ/WRITE/NET/EXEC) and
a side-effect class (none/reversible/irreversible); a skill's trust tier is
**derived** from its capability surface (self-declaration is not allowed);
crossing tiers upward requires human review. Confidentiality is independently
carried by data-layer authN+Z keyed on the principal (§5.3).

### 2.4 Human-in-the-Loop as a First-Class Primitive

The supervisor subsystem models "ask the caller for a ruling" as a
first-class primitive: any frame may suspend and wait for its caller — a
human, another program, or another agent — with suspend/persist/resume
deterministic end to end. The human is not a post-hoc auditor but an
arbitrator sitting on the execution path.

## 3. Architecture

### 3.1 Layered Overview

```
┌──────────────────────────────────────────────────────────────────┐
│ Host layer (thin hosts; never enters the kernel)                  │
│   CLI (for coding agents)   │   Web (for humans: UI/SSE/inbox/debugger) │
│   ──────────── host/shared: artifacts / RunRecord / replay ────── │
├──────────────────────────────────────────────────────────────────┤
│ Contract layer api/v1 (frozen: Skill/Tool/Frame/Signal/Principal/…)│
├──────────────────────────────────────────────────────────────────┤
│ Microkernel Kernel Runner = flow control + permission control + IPC │
│   agent loop │ frame stack │ dispatch arbitration │ safe-points    │
├───────────┬───────────┬───────────┬───────────┬──────────────────┤
│ Providers │ Sidecars  │ Skills    │ Tools     │ Context          │
│ (drivers) │(supervisor)│ Registry │ Registry │ (assemble/compress)│
├───────────┼───────────┼───────────┼───────────┼──────────────────┤
│ Logic     │ Telemetry │ Memory    │ Blackboard│ Supervisor       │
│ Kernel    │ (WAL)     │ (memory)  │ (shared)  │ (ruling router)  │
└───────────┴───────────┴───────────┴───────────┴──────────────────┘
```

### 3.2 The Kernel Agent Loop (Single Frame)

```
loop:
  1. hand context to the Context subsystem (compress + assemble + status bar)
  2. call ProviderManager → LLM response
  3. no tool calls → frame ends (outputs schema validated)
  4. tool calls → dispatch arbitration, one by one:
       a. pseudo-tools (skill.* / ask_supervisor / python_orchestrate) → dedicated paths
       b. real tools → schema validation → data-layer authZ → three-way
          permission intersection → timed execution
       c. sub-skills → whitelist check → escalation verdict (§5.2) → push frame
  5. observations written back into frame context; goto 1
```

Signals are emitted at every step (§4.5); safe-points make cancel/pause
happen only at dispatch boundaries — never "deleted half, then interrupted".

### 3.3 The Frame Model and Isolation

```
Run (process)
└─ root frame (depth=0, root skill)
   ├─ child frame (depth=1, skill A)      each frame holds:
   │  └─ child frame (depth=2, skill B)    · FrameContext.messages (private)
   └─ child frame (depth=1, skill C)       · working (serialized with checkpoints)
                                           · tier (inherited from invoked skill)
The only channels between parent and child: call arguments (parent→child,
JSON-Schema validated) and the return value (child→parent, folded into a
tool result). The parent's dialogue history, tool observations, and injected
payloads are physically absent from the child's context.
```

This model carries the **clean-context invariant** (§5.2): an escalated
frame's context is a whitelist — the system-default prompt, the target
skill's prompt, and schema-validated arguments. There is no fourth door.

## 4. Subsystems

### 4.1 Providers (Device Drivers)

Contract: the `ChatProvider` protocol (chat/stream/usage/tokenizer) plus
`ProviderManager` (kernel-side facade; routing and a single token-estimation
authority). Baselines: `OpenAICompatibleProvider` (covering mainstream
vendors) and `MockProvider` (tests and replay scripts). Estimation prefers
the provider's exact tokenizer, falling back to a unified estimator — the
Context subsystem consumes the same estimate, so the unit is unique.

### 4.2 Skills (Functions and the Dynamic Linker)

A skill is a first-class asset: a frozen manifest contract (name, version,
kind, description, inputs, outputs, permissions, model, context_policy,
limits, trust), with a prompt template or a code handler as its body. The
loading pipeline performs topological sorting, dependency-existence checks,
cycle detection, and hot reload. A sub-skill appears to the parent frame's
LLM as a pseudo-tool `skill.<name>` whose schema is the callee's `inputs`;
the kernel intercepts and pushes a frame. Pure-instruction skills marked
`inline: true` may merge into the caller's SYSTEM at assembly time (purity
gate + snapshot freezing; see SKILL-INLINING.md). Skills whose derived tier
is L2 or above are forbidden from inlining — a hard gate.

### 4.3 Tools (System Calls and Arbitration)

Every tool declares a `permission` (READ < WRITE < NET < EXEC) and a
`side_effect` (none / reversible / irreversible; derived from permission by
default). Every call traverses the full dispatch pipeline: schema validation
→ **data-layer authZ** (§5.3) → three-way permission intersection (tool
self-declared level × frame manifest whitelist × RunConfig ceiling) → timed
execution. The path sandbox (`resolve_work_path`) and data-domain verdicts
(§5.3) are orthogonal: one governs "may this path leave the workdir", the
other "may this principal read this region".

### 4.4 Context (Memory Management)

Assembly and compression of frame contexts: *build* (SYSTEM rendering +
messages + status injection + tool schemas), *maintain* (compression when
the estimate exceeds the cap; chain-of-responsibility strategies; the
rolling-window baseline evicts atomic groups, never compresses pinned items,
and marks idempotently with `[COMPRESSED]`). Prefix-cache stability is a hard
constraint: inline segments freeze into `working` at the frame's first build,
so prefixes are identical across resume and hot reload.

### 4.5 Sidecars and Telemetry (Supervision and the Black Box)

The signal bus is the common channel of the whole system: `pre/post:llm.*`,
`pre/post:tool.call`, `pre/post:skill.invoke`, `pre/post:skill.escalate`,
`run.*`, and more — all persisted to a WAL (`trace.jsonl`). Sidecars are
triggered by signals and may synchronously veto, modify, pause, or halt;
BudgetGuard and ToolGuard are built-in examples. Telemetry is not merely for
auditing: replay (§7) and the debugger (§6.2) are built on the same WAL.

### 4.6 Logic Kernel (ALU and Sandbox)

The sole execution point of logical code: code skills and LLM-authored
dynamic code (`python_orchestrate`) are routed through it. With
`logic: {mode: sandbox}` (or a global force-sandbox policy), code runs in an
isolated environment with no `ctx`, returning to kernel dispatch only through
the syscall channel — documented explicitly as "no privilege elevation".
This is complementary to escalation: escalation governs "may the lower tier
enter", the sandbox governs "may the code get out".

### 4.7 Supervisor (Ruling Router)

The `ask_supervisor` pseudo-tool and kernel-enforced escalation confirmation
share one closed loop: pending persisted (`frame.context.working`) → in-place
suspension → the caller channel answers (Web inbox / CLI stderr-stdin
protocol / embedder handler) → the answer returns as a tool result → the run
resumes. Timeouts close per `on_timeout: fail|default_answer`; answers
outside `options` are re-asked with `previous_error`. Ruling requests are
structured data (with a `kind`), and the Web inbox renders dedicated cards
per kind (e.g., the escalation card).

### 4.8 Memory and Blackboard

Memory: cross-run memory and knowledge, with permission-filtered retrieval
(same verdict as data authZ). Blackboard: intra-run shared memory for
inter-frame state and messages with concurrency control, used by fork/join
parallel frames to exchange intermediate results. Both are contract-first
with minimal baselines.

## 5. Trust & Safety Model

### 5.1 Three Trust Tiers, by Side-Effect Reversibility

| Tier | Value | Side-effect semantics | Examples | Confirmation |
|---|---|---|---|---|
| L1 | `none` | no side effects: pure reads, pure computation | data reads, retrieval, reasoning | none |
| L2 | `reversible` | side effects reversible or tolerable | DB/file writes, recallable messages | first call; approve-run allowed |
| L3 | `irreversible` | side effects irreversible and intolerable | deleting data/VMs, stopping processes, transfers | **every call; no approve-run** |

Declaration and derivation: **tools declare** `side_effect` (the author knows
their tool best; defaults derive from Permission: READ→none,
WRITE/NET→reversible, EXEC→irreversible); **skills derive, never declare**
(recursive max over whitelisted tools/skills — self-reports can lie, and a
thin orchestrator cannot launder its tier).

### 5.2 The Escalation Flow (Upward Crossing Requires a Human)

```
parent (lower tier)   kernel                        caller (human)
  │ skill X(params)    │                             │
  ├───────────────────▶│ ① whitelist check            │
  │                    │ ② argument schema pre-check  │
  │                    │    (failure → no prompt)     │
  │                    │ ③ tier(target) > tier(frame)?│
  │                    │ ④ suspend + checkpoint       │
  │                    │ ⑤ EscalationRequest ────────▶│ ⑥ shows: who calls what,
  │                    │ ⑦ ruling          ◀──────────│    tier, arguments
  │                    │ ⑧a approve → isolated frame  │ approve-once /
  │                    │    (clean-context invariant) │ approve-run (L2 only) / deny
  │                    │ ⑧b deny → PERMISSION_DENIED  │
  │◀── child result (folded into a tool result)         │
```

> Note: the `[ESCALATED:...]` provenance tag on the return path is designed
> (ESCALATION.md §3) but not yet implemented — the escalated child's result
> currently looks like any other tool result (see chapter 09, §6).

Key properties:

- **What you review is what will run**: the arguments shown are the very JSON
  that passed schema validation and will be injected verbatim;
- **The injector cannot approve itself**: confirmation is initiated by the
  kernel, never by the LLM;
- **Clean-context invariant**: an escalated frame's messages are exactly
  `[USER(arguments)]`, and SYSTEM contains only the system default and the
  target skill's prompt (asserted by tests);
- **Short-lived grants**: approve-run exists only for L2, and grants die with
  the run;
- **Deterministic resume**: crash mid-confirmation, and resume re-walks the
  gate.

### 5.3 The Three-Gate Model (Read / Write / Exfiltrate)

```
read (confidentiality) → data-layer authN+Z: Principal × data domain ×
                          sensitivity; default deny
write (side effects)   → tiered escalation: upward crossing requires a human
exfiltration           → the write gate catches it: NET sends are side
                          effects and must pass the L2/L3 gates
```

Data-layer authN+Z judges by the principal (who started the run), independent
of skill or frame: **escalation changes side-effect clearance, not identity** —
a run started by an ordinary user, even after entering an L3 skill, can still
read only what that user may read. The two systems are orthogonal and share
only the principal field. Documented residual risk: no data isolation inside
a run of one principal (it is the same person's data); multi-tenant isolation
is carried by the run boundary.

### 5.4 Production Standards and the Admission Gate

The trust model stands only if same-tier members pass the same production
standards (TIER-STANDARDS.md): L2 requires a rehearsed reversal mechanism
(`reversal` mandatory) and idempotent implementation; L3 requires dry-run
support, a mandatory `blast_radius`, named targets, and TOCTOU protection.
The Skill Lab's **five-gate admission pipeline** turns the standards into an
enforcement point: G1 metadata (naming / routing-style description), G2
contract (valid schemas, typed parameters for L2+), G3 tier compliance (the
mandatory fields above + the inline hard gate), G4 smoke run (outputs must
validate), G5 prompt hygiene (injection-pattern detection). Any fail blocks
promotion to production.

## 6. Engineering: Determinism, Debugging, Developer Experience

### 6.1 Determinism Engineering (Checkpoint / Resume / Replay)

Every run persists four artifacts: `meta.json`, `trace.jsonl` (signal WAL),
`checkpoint.json` (frame-state snapshot), `result.json`. Checkpoints
serialize with frames (including pending rulings and escalation requests in
`working`); after a crash, resume re-enters from the checkpoint, re-asking
pendings or resuming with answers. Replay exploits the fact that "the runner
only appends LLM responses to frame context": it rebuilds a MockProvider
script from the trace and deterministically replays the whole frame tree
(touching no real API; tool side effects still execute against the real
environment — that is the boundary).

### 6.2 Debugger (GDB Semantics)

The Web debug console treats a run as a process: breakpoints (pre:tool.call /
skill.invoke / step / error, glob-matched), stepping (into/over/out), pause
at any time (SIGINT semantics), frame inspection, and intervention (modify
arguments / inject a message, then resume). Debug sessions use the same
suspend-resume
loop; a breakpoint hit is a pending.

### 6.3 Theme System (Six Themes, Zero Component Branches)

A UI theme = token mapping + copy table + (optional) mascot layer. Six
built-in themes (classic / moe / terminal / blueprint / ink / pixel) all pass
the contract tests: token completeness, WCAG contrast, dual coding (color +
text channels), copy-key completeness, and no theme branches in components
(static scan). The mascot abstraction has two instances (Mochi / sprite8),
proving the layer is replaceable.

### 6.4 Skill Lab (Development Platform)

The skill development loop: DraftStore draft layer (physically separated
from production) → a seven-group full-field editor (live derived-tier badge)
→ the Agent assistant (`skill.dev.assistant`, holding only the five
`lab.draft.*` tools — **it can edit but cannot publish**) → the test panel
(test runs ride the same assembly line as production) → the five-gate
pipeline → a human clicks promote (three defenses: report hash bound to
content, hard fail rejection, server-side re-run of G1-G3) → production hot
reload. The CLI exposes the same gate (`agent-os lab validate`, exit code 0
for pass/warn, 2 for fail), consumable headlessly by coding agents.

## 7. Problems Solved (Mapping Revisited)

| Problem (§1.2) | Mechanism | Status |
|---|---|---|
| Actions cannot be arbitrated | three-tier trust + escalation gate + approve-once/run + tiered production standards | implemented (E1/E2) |
| No data-access verdict | data-layer authN+Z (Principal / domains / default deny) | D1 implemented; config & multi-user (D2/D3) designed |
| Processes cannot be reproduced | checkpoint / resume / replay + signal WAL | implemented |
| State cannot be isolated | frame model + clean-context invariant + inline purity gate | implemented (test-asserted) |
| Quality cannot be guaranteed | five-gate pipeline + tiered standards + Skill Lab | implemented (L1-L5) |
| Runs cannot be observed | signal catalog + debugger + RCA | implemented |
| Trust chain for self-written skills | SkillArtifact / Provenance | contract reserved (M6, not implemented) |

## 8. Status and Roadmap

As of v1.0 (2026-08): all nine kernel subsystems have landed with baseline
implementations; escalation E1/E2 is implemented (including the spawn gate
and the Web escalation card); data authZ D1 is implemented (transparent for
single-user, enforceable for embedded/multi-user principals); Skill Lab
L1-L5 is complete; all six Web themes pass the contract tests; the test
baseline is 812 Python tests plus 24 frontend test files, all green.
Designed but not yet implemented: remaining E3 items (audit panel), D2/D3
(domain config, delegation chains, multi-user), M6 (trust pipeline), the
motion playback layer (theme contract test #5), multi-file skill_set
promotion, and handler source promotion.

Roadmap principles: contract first, replaceable baselines, gates at the
exit. For every new capability, answer first: where is its arbitration
point, how is its determinism guaranteed, and where are its tests.

## Appendix A: Referenced Documents (This Repository)

- Architecture baseline: `DESIGN.md` (kernel & subsystems), `RUNNERS.md`
  (hosts), `CODE-ORCHESTRATION.md` (orchestration sandbox),
  `SKILL-INLINING.md` (inlining)
- Trust & safety: `ESCALATION.md` (escalation), `TIER-STANDARDS.md`
  (production standards), `DATA-AUTHZ.md` (data authorization),
  `SUPERVISOR.md` (ruling channel)
- Experience: `DEBUGGER.md`, `DEBUG-UI-THEMES.md`, `WEB-UI.md`,
  `SKILL-DEV.md`
- Examples: `agent_os/examples/workspace_janitor` (full-fidelity escalation
  demo), `support_desk`, `supervision`, and others
