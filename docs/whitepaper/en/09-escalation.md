# Escalation: Three Trust Tiers & Clean Context

> Chapter: 09 · Status: implemented (E1 escalation gate + clean-context invariant; E2 approve-run Grants,
> three signals, spawn gate; E3 partial) · Sources: docs/ESCALATION.md (v0.3), agent_os/src/agent_os/
> api/v1/escalation.py, kernel/runner.py, tests/kernel/test_escalation.py, examples/workspace_janitor/

## 1. Overview

The escalation system is Agent OS's **runtime side-effect arbiter**. It classifies skills into three trust
tiers by side-effect reversibility (L1 `none` / L2 `reversible` / L3 `irreversible`) and mandates that any
call from a lower-tier frame into a higher-tier skill first suspend, present structured parameters to a
human (or an explicit policy) for approval, and only then execute in a clean context physically isolated
from the parent frame. It sits on the microkernel's permission-control plane: the gate is embedded in the
runner's sub-skill dispatch (`kernel/runner.py:865`), confirmation reuses the supervisor adjudication loop,
and the authorization ledger lives on the kernel `Run`, serialized with checkpoints (`kernel/run.py:20`).
It gates "may a lower tier come in and change the world", not "what may be seen" — the latter is the
data-layer authZ's job; with "exfiltration = write gate as backstop" they form the three-gate model
(docs/ESCALATION.md §1).

## 2. Motivation and Background

**The static whitelist is necessary but insufficient.** A manifest's whitelist answers "which capabilities
did this skill declare", not "should it use them right now". Injection happens at runtime: an injected L1
skill that can merely name an L3 skill can borrow its whitelist to run high-risk tools (ESCALATION.md §1).

**Confirmation and grants had the wrong shape.** The pre-existing `ask_supervisor` is model-initiated, and
an attacker's optimal strategy is precisely not to ask — so escalation confirmation must be kernel-initiated,
not a model elective (comment at `runner.py:917-922`). And even when a human did approve, `allowed_tools`
was re-read from the manifest at dispatch time; the approval had no write-back point, which demanded a
run-scoped ledger (`Grant`) consumed at the decision point (ESCALATION.md §4). The gate must live in the
kernel because the decision needs the caller frame's tier, the target's derived tier, the whitelist, and the
supervisor channel at once — all kernel-held; an external gate would veto after execution.

A methodological decision underlies the tiers: they are graded **not by subjective "dangerousness" but by
the objective semantics of side effects** — L1/L2 is "does it change the world", L2/L3 is "can the change
be undone". Both lines are decidable, so three tiers are exactly enough (§8). Appendix A's study of mobile
OS permission models (Android, iOS TCC) gives each trade-off a precedent; the clean-context invariant is
the one gate mobile OSs never needed (§4.6).

## 3. Problem Statement

Every scenario below is reproducible and has a corresponding test (§5).

- **P1 Escalation by name**: an L1 inspection skill reads a log carrying an injection payload that induces
  it to call `ops.cleanup.execute({targets: [...]})`; without the gate the deletion really happens.
- **P2 Approve one thing, run another**: if the parameters shown at confirmation are not the same JSON
  later injected into the child frame, human review is theatre.
- **P3 Nag-until-approved**: after a denial the LLM retries the same call forever, flooding the inbox.
- **P4 Power loss at the gate**: killed while suspended awaiting a verdict — on resume, did the call happen or not?
- **P5 Thin-orchestrator tier laundering**: an L1 shell delegates to an L3 implementation one hop down; if
  tier only counted a skill's own tools, delegation would launder the tier away.
- **P6 The background-frame bypass**: a code skill's `spawn_frame` does not go through LLM dispatch and was
  an undefended side channel in E1 (gated in E2, `runner.py:1153`).
- **P7 Nobody to ask**: a headless embedding without a supervisor channel — fail open or fail closed?

## 4. Design and Mechanism

### 4.1 The three trust tiers

| Tier | Value | Side-effect semantics | Confirmation strength |
|---|---|---|---|
| L1 | `none` | No side effects: pure reads, pure computation | None (pre-existing behavior) |
| L2 | `reversible` | Reversible/tolerable effects (writes, retractable messages, config) | First-call confirm; approve-run allowed |
| L3 | `irreversible` | Irreversible, intolerable (deletion, killing processes, transfers, publishing) | **Every call human-reviewed; no approve-run** |

Comparison consults a single total-order table (`escalation.py:51`); the strings carry no ordering, and
unknown tiers count as lowest (`escalation.py:54-56`).

### 4.2 Declaration vs. derivation: tools declare, skills only derive

- **Tools declare** `ToolSpec.side_effect` (the author knows the tool best); the default derives from
  `Permission`: READ→none, WRITE/NET→reversible, EXEC→irreversible (`api/v1/tools.py:127-136`). A
  read-only diagnostic exec may explicitly downgrade, a read tool may upgrade — always round up.
- **Skills derive, never declare**: `derive_skill_tier` takes the max tier over whitelisted tools and
  (recursively) skills (`escalation.py:92-104`). Self-reports lie or go stale; the derived value always
  reflects the true permission surface. `explain_skill_tier` also returns every source (`escalation.py:107-140`).
- The manifest `trust` block keeps only the override `confirm: always|first`, and it **may only be raised,
  never lowered**: an L3-derived skill with `confirm: first` fails at assembly time.

### 4.3 The escalation event and the two ways a frame gets its tier

```
escalation ⟺ tier(target skill) > tier(caller frame)     (escalation.py:59-61)
```

Same-tier moves need no confirmation (intra-tier trust is guaranteed by the production standards);
**higher-to-lower calls are de-escalation and are never confirmed**. How a frame's tier is assigned is the
subtlest point of the design:

- **Target side / lint side**: the full derived tier (max over tools + skills, recursively) — a thin
  orchestrator cannot launder its tier (P5);
- **Child frames**: inherit the invoked skill's full derived tier (`runner.py:897`) — entering means
  inheriting the whole reviewed envelope, so same-tier moves inside it stay unconfirmed;
- **Root frames**: the root skill's **direct-capability tier** — its own tools only, no recursion through
  skills (`runner.py:238`, `derive_tools_tier`, `escalation.py:76-89`).

Why not give the root frame the full derived tier? If we did, every callable target is by definition inside
the caller's whitelist, so `tier(target) ≤ tier(caller)` always holds, the escalation event can never fire,
and the gate is dead code. Starting a run confirms the root skill's direct capability surface — not the graph
it can indirectly reach (design note, ESCALATION.md §2.2); a host-launched root skill does not pass the gate
at all: the launch action is itself the confirmation.

### 4.4 Gate sequence

```
parent frame (low)      kernel runner                        caller (human/policy)
  │ skill X(params)      │                                    │
  ├─────────────────────▶│ ① whitelist check (runner.py:848)  │
  │                      │ ② tier test (:865); no rise → pass │
  │                      │ ③ pre-validate params (:870);      │
  │                      │    failure → INVALID_ARGS,         │
  │                      │    **no confirmation emitted**     │
  │                      │ ④ Grant consumption (:940);        │
  │                      │    hit → pass directly             │
  │                      │ ⑤ pending persisted (:990) + pre   │
  │                      │ ⑥ EscalationRequest ──────────────▶│ ⑦ sees: who calls
  │                      │    (kind="escalation", structured) │    whom/tier/params
  │                      │ ⑧ verdict                 ◀────────│ approve-once /
  │                      │    approve-run only for L2;        │ approve-run(L2)/
  │                      │    L3 never offers it              │ deny
  │                      │ ⑨a approved → isolated make_frame  │
  │                      │    (:884); ⑨b denied →             │
  │                      │    PERMISSION_DENIED (:1059)       │
  │◀── child result folded into a tool result (:915)          │
```

The key property: **what is reviewed is what executes** — the `params` shown in the confirmation request
are the same JSON that passed the inputs schema and will be injected verbatim into the child frame
(`escalation.py:154-172`); `reason_hint` is machine-generated "caller tier → target tier", not LLM-written.
With no supervisor channel the gate **fails closed** (`runner.py:957-967`). `spawn_frame` passes the same
gate; spawn has no tool-result channel, so denial/invalid args surface as `SkillLoadError` (`runner.py:1163-1174`).

### 4.5 Verdict options and the Grant lifecycle

- `approve-once`: pass this call; **no Grant is registered** — the next identical call hits the gate
  again, defeating P3 (comment at `runner.py:1010-1014`);
- `approve-run` (present only in L2's options): registers a run-scoped Grant (tier snapshot + decided_by);
  later calls to the same skill in this run pass directly, emitting
  `post:skill.escalate(decision="grant-run")` (`runner.py:1015-1024`);
- `deny`: the parent frame receives a PERMISSION_DENIED error observation, same shape as a whitelist
  refusal, so the LLM can reroute; retrying the same call suspends again.

Grants live on the kernel-side `Run.grants` (`kernel/run.py:20`), serialize with checkpoints (so resume
decides identically), and die with the run — no cross-run standing authorization. The consumption point
is **doubly insured**: even a hand-crafted Grant only takes effect for `reversible` targets (`runner.py:1083-1086`);
once-scoped grants burn on consumption (crash-residue fallback). L3's options never include approve-run;
a hand-typed approve-run is rejected by options validation and re-asked with `previous_error` (`supervisor/manager.py:163`).

### 4.6 The clean-context invariant

The execution environment an approved higher-tier skill enters is whitelist-built, with exactly three doors:

1. SYSTEM = the system default section + `render_prompt(target skill.prompt, input)` — the parent's prompt
   physically never enters;
2. the first USER message = the schema-validated parameter JSON (`source=PARENT_INPUT`);
3. tool schemas = the target skill's own whitelist plus pseudo-tools.

**That is all.** The parent's conversation history, tool observations, and injection payloads are physically
absent from the child's context — not filtering but isolation: the child is a new `FrameContext` whose
messages are exactly `[USER(input)]` (assertions in §5). A companion hard gate: derived tier ≥ L2 forbids
`inline: true` (`skills/manifest.py:120-122`) — inlining merges the instruction section into the caller's frame.

The injection-risk ledger now closes: the only way low-tier content reaches a high tier is the narrow
channel of schema-validated parameters; even if the low-tier LLM is injected, its next call upward hits the
confirmation gate again (for L3, every single time). **An injector cannot approve itself.**

### 4.7 Signals and audit

| Signal | Payload | When |
|---|---|---|
| `pre:skill.escalate` | skill, tier, frame_id, params, requested | confirmation request emitted |
| `post:skill.escalate` | skill, tier, decision, decided_by, scope | verdict received, or Grant hit |
| `skill.escalation.denied` | skill, tier, decided_by | on denial |

`decision ∈ {approve-once, approve-run, grant-run, deny}`; a Grant hit emits no confirmation request, hence
an unpaired post with no pre (`runner.py:941-956`). The Web inbox renders `kind == "escalation"` pendings
as escalation cards (tier badge, parameter JSON, option buttons — no approve-run for L3).

### 4.8 Key trade-offs (why A, not B)

- **Three tiers, not four**: boundary lines must be objectively decidable; subjective "danger level"
  grading is exactly the lesson of Android's permission-group over-granting (Appendix A.3).
- **Derivation, not self-report**: a self-declared skill tier would lie or go stale. The price: a mislabeled
  tool `side_effect` mislabels the tier (see §6).
- **No caching for approve-once**: E1 once defined a once-scoped Grant; the final implementation is "pass
  this call, register nothing" — the shorter the approval's lifetime, the smaller the abuse surface;
  `_consume_grant` keeps burn-on-consume once handling only as a fallback (`runner.py:1069-1089`).
- **Fail closed, not fail open**: default-deny when nobody can review (P7). The price: headless embeddings
  must wire a supervisor channel to use high-tier skills at all.
- **No LLM-as-approver**: the approval surface must be human or explicit policy — otherwise an injector
  merely has to fool two models (ESCALATION.md §8).

## 5. Effects and Verification

**Test evidence.** Two test layers cover the system, all actually executed and green (re-verified 2026-08-02
via `pytest tests/kernel/test_escalation.py tests/examples/test_workspace_janitor.py`: **36 passed in 0.89s**):

- `tests/kernel/test_escalation.py` (**28 tests**): derivation matrix and overrides; the full 3×3 decision
  matrix; the full suspend → approve-once → resume flow (exactly one confirmation, every payload field
  asserted); denial means the child never ran; retry after denial suspends again; invalid params emit no
  confirmation; same-tier/downgrade pass through; fail-closed without a supervisor; checkpoints contain
  `_pending_escalation` and resume re-walks the gate; the clean-context invariant (parent prompt absent
  from child SYSTEM; first USER = parameter JSON); the ≥L2 inline hard gate; L3 rejecting `confirm: first`
  and hand-typed approve-run; approve-run registers a Grant, second call unasked; Grant double-insurance
  and once-burn; signals and checkpoint-restored grants; the spawn gate's four outcomes; CLI passthrough.
- `tests/examples/test_workspace_janitor.py` (**8 tests**): real built-in tools, zero mocks, real filesystem
  assertions (approve-run Grant hit, L3 asks every time, deny preserves files, real process kill, dry_run consistency, clean context).

**Real example.** `examples/workspace_janitor` is a four-skill, three-tier live scenario: an L1 inspection
root → L2 plan-writing (called twice; approve-run skips the second confirmation) → L3 real file deletion
(asked every time, dry_run preview) → L3 stopping a real demo `sleep` process; the blast radius is fenced by
the run's workdir sandbox. CLI, Web inbox cards, and `kill -9` + `agent-os resume` are covered in its README.

**Ripple effects.** The derived tier became an input to other subsystems: the Skill Lab editor shows it and
its provenance in real time, commit gate G3 landed the L2 `reversal` / L3 `blast_radius` required-field
lints (E3, partial), data-layer authZ and escalation form the read/write/exfiltration three-gate model, and
the `spawn_frame` gate closed E1's bypass.

## 6. Limitations and Boundaries

- **The return-path provenance marker is unimplemented.** ESCALATION.md §3 designs an `[ESCALATED:skill@version]`
  result marker (the executive summary's §5.2 repeats it), but the source has no such marker — an escalated
  child result looks like any other tool result; audit must rely on the signal stream. Designed, not implemented.
- **The escalated-frame principal annotation is unimplemented.** §4 designs filling `ToolContext.principal`
  with `{"escalated": true, ...}` in escalation frames; today it only carries the data-layer identity (`tools/local_registry.py:255`).
- **Authorization granularity is the crossing moment, not the parameters.** approve-run passes all later calls
  to the same skill this run regardless of arguments — a "write the plan" approval can write to any path.
- **Confirmation fatigue is the price of a design choice.** L3 asks every time — N deletions, N prompts; the
  design rejects bulk authorization, auto-approval, cross-run standing grants, and LLM approvers (§8), at the
  cost of interactive throughput and a usability bar for headless use.
- **Derivation correctness depends on honest tool labeling.** Unregistered and pseudo tools count as none
  (`escalation.py:64-73`); a tool labeled too low removes one gate layer; lint only nudges by naming patterns.
- **Intra-tier trust is a process convention the kernel does not verify.** It holds only if tier members pass
  the same production standards (TIER-STANDARDS.md); E3's stricter lints are partial (G3), audit panel open.
- **Clean context also blocks useful context.** The parent's findings must travel through parameters, bounded
  by the inputs schema's types and size — a high-tier skill's "situational awareness" ceiling is its schema.
- **Denial does not trip a breaker.** Retrying a denied call suspends again (necessary against nag-until-approved),
  but there is no rate limit, so an injected LLM can keep flooding the inbox.
- **The gate does not decide who may start a run.** A host-launched root skill bypasses the gate; launch authority
  is the host's and data-layer authN's business — abusing the host to launch an L3 root skill is out of scope.

## 7. References

- Design docs: `docs/ESCALATION.md` (v0.3, this chapter's baseline), `docs/TIER-STANDARDS.md`, `docs/SUPERVISOR.md`,
  `docs/DATA-AUTHZ.md`, `docs/SKILL-INLINING.md` §2.3
- Contract layer: `agent_os/src/agent_os/api/v1/escalation.py`, `api/v1/tools.py:118-136`, `api/v1/frames.py:98-101`
- Kernel: `agent_os/src/agent_os/kernel/runner.py:839-1189` (`_invoke_skill`, `_confirm_escalation`, `_consume_grant`,
  `_settle_pending_escalation`, `spawn_frame`), `kernel/run.py:20`, `skills/manifest.py:120-122`, `supervisor/manager.py:163`
- Tests: `agent_os/tests/kernel/test_escalation.py` (28), `agent_os/tests/examples/test_workspace_janitor.py` (8);
  example: `agent_os/examples/workspace_janitor/` (README.md, skills.yaml, make_fixture.py)
