# Data-Layer authN+Z: Principal & Data Domains

> Chapter 10 · Status: D1 implemented (Principal model, CLI/Web single-user sources, fs domains, dispatch enforcement point, identity invariant with checkpoint round-trip); D2/D3 designed but not implemented (config section, per-subject domain whitelist, db/net domains, audit signals, delegation chain, multi-user session mapping) ·
> Sources: `docs/DATA-AUTHZ.md`, `agent_os/src/agent_os/api/v1/principal.py`, `agent_os/src/agent_os/tools/local_registry.py`, `agent_os/tests/tools/test_data_authz.py`

## 1. Overview

Data-layer authN+Z is the "read" gate of Agent OS's three-gate model: it authorizes read-class actions by principal (who started the run) against data domains (the unit of authorization), defaulting to denial. It is orthogonal to the tier-escalation system — the escalation gate judges side effects ("may this call change the world"), the data gate judges confidentiality ("may this principal touch this data"), and the only field the two systems share is the principal (`docs/ESCALATION.md` §1 explicitly assigns confidentiality to this system as a non-goal of escalation). D1 is shipped: the contract lives in `api/v1/principal.py`, the enforcement point in `LocalPythonToolRegistry.dispatch`'s `_check_data_access`.

## 2. Motivation and Background (Why)

**Why the escalation system does not gate reads.** The three trust tiers are derived from objective side-effect semantics (none / reversible / irreversible) and deliberately exclude any confidentiality dimension (`docs/ESCALATION.md` §1, non-goals). The reasoning is architectural: "how sensitive is this data" and "does this call change the world" are independent variables — reading a confidential file is L1 (no side effect) yet may require top clearance; `system.shell.exec` is L3 yet may touch nothing of value. Folding confidentiality into the tiers would contaminate both criteria: either read-only skills get dragged into human review for no reason, or dangerous tools argue their tier down by "not touching secrets". The result of this boundary is the three-gate model: read = data-layer authZ; write = tier escalation; exfiltration = the write gate as backstop (`docs/DATA-AUTHZ.md`, preamble).

**Why domains instead of per-file ACLs.** Maintaining an ACL per file or table scales linearly with the data, while an agent's protection targets are naturally contiguous: directory trees, database schemas, network segments. The domain is deliberately the finest granularity (`docs/DATA-AUTHZ.md` §3.1); the host binds a handful of boundaries and trades granularity for a configuration surface it can actually keep correct.

**Why identity lives on the frame, not on RunContext.** The design text of §2.3 said "stored in RunContext"; the D1 implementation stores it in `SkillFrame.principal` instead (`docs/DATA-AUTHZ.md` §8, implementation note 2): dispatch only ever sees frames, frame-level storage makes "child and escalated frames inherit verbatim" a by-product of frame construction (`make_frame` copies it, `skills/local_file.py:146-148`), and checkpoint serialization covers identity for free. This is one explicitly documented case of code superseding the design text.

**Prior state.** Before D1, `ToolContext.principal` / `credentials` were reserved contract fields, always None/empty; the `resolve_work_path` path sandbox was the fs-dimension prototype of authorization — it answers "may this path leave the workdir", never "who is reading".

## 3. Problem Statement (What It Solves)

Without data-layer authN+Z (`docs/DATA-AUTHZ.md` §1):

- **Read access is all-or-nothing**: any skill with `system.file.read` in its whitelist can read everything inside the sandbox whose path it can spell. One-line example: a support-desk skill's permission to read the tickets directory is simultaneously permission to read the finance directory on the same machine — the whitelist grants by tool name, never by data.
- **No isolation criterion for multi-tenancy**: a web service running runs for several people reads one filesystem under one process identity; nothing at runtime can tell "on whose behalf this run reads".
- **Audit cannot attribute**: with principal always None, no signal carries an identity, so "who read what" is unanswerable from the trace.
- **Confused deputy (designed, D3)**: one run delegating a read to another, more privileged run — unless identity degrades along the call chain, this is "asking a high-clearance agent to read for you".

## 4. Design and Mechanism (How)

### 4.1 Principal: the identity model

```python
# agent_os/src/agent_os/api/v1/principal.py:40-46
@dataclass(frozen=True)
class Principal:
    subject: str   # "user:hengxiao" | "service:ci-bot" | "agent:run-..."
    issuer: str    # who authenticated: "cli" | "web-session" | "api-token" | "host-embedded"
    attrs: Mapping[str, str]  # attributes (clearance, etc.) for ABAC verdicts
```

D1 implements two sources (`principal.py:83-105`): CLI takes the local user (`user:$USER`, issuer=cli); Web single-user mode takes the deployer's login (host config `[web].user`, defaulting to the local user; `host/web/run_manager.py:430-440`). Both grant confidential clearance — a single user owns the machine, so interception only bites for explicitly constructed low-clearance identities (tests, embedding hosts) (§8 note 3). api-token and host-embedded are sources whose protocol surface is frozen but whose wiring belongs to D3.

### 4.2 The identity invariant: no self-elevation

- A run's principal is its starter's principal, injected into the root frame via `Kernel.run(principal=)` (`kernel/runner.py:206-228`) and serialized with checkpoints (`kernel/checkpoint.py:156` writes, `:226-234` rebuilds);
- Child frames, escalated frames, and code-sandbox frames **inherit the parent frame's principal verbatim** (`skills/local_file.py:146-148`). **Escalation changes side-effect clearance, not identity**: a run started by an ordinary user, even after escalating into an L3 skill, can still read only what that user may read;
- An agent acting as caller may be degraded to `agent:<run_id>` carrying the upstream chain (`attrs["via"]`), judged by the **weakest link** of the chain — defeating "borrow a high-clearance agent to read for you" (§2.3; **designed, not implemented — D3**).

### 4.3 authZ: data domains and default denial

The unit of authorization is the data domain (`principal.py:49-54`): a `name` plus a `sensitivity` (public < internal < confidential; the three-level total order is `_LEVEL_RANK`, `principal.py:32-37`). Tools declare which domains they touch in `ToolSpec.data_domains` (`api/v1/tools.py:119-121`); all built-in fs tools (read/write/edit/list/search/stat/delete/mkdir) declare `["fs.*"]` (`with_builtins` in `local_registry.py`). The host binds boundaries via `register_fs_domain(domain, path_prefix)`, longest prefix first, nested domains judged by the most specific match (`local_registry.py:288-294`); paths under the workdir fall back to the built-in default domain `fs.workdir = public` (`local_registry.py:296-304`).

The verdict (`principal.py:62-75`):

```
allow(principal, domain, action) ⟺ clearance_of(principal) ≥ domain.sensitivity
```

- Default deny: insufficient clearance has no fallback pass; the single exception is `principal is None` → allow (v1 single-user semantics: a host that injected no identity has not enabled the data layer, and behavior is identical to before this system existed);
- Fail closed in both directions: unknown clearance counts as public, unknown sensitivity counts as confidential (`principal.py:73-74`); attrs lacking a clearance key also default to the lowest level (`principal.py:57-59`);
- The second criterion of §3.2 (a per-subject domain whitelist) depends on the host config section and is **not implemented (D2)**; the `action` parameter is a protocol placeholder and takes no part in D1 verdicts.

### 4.4 Enforcement: two gates in series inside dispatch

```
ToolCall
  │
  ▼
schema validation (jsonschema, fail fast, no "smart correction")
  │
  ▼
data-layer authZ ── _check_data_access (local_registry.py:306-351)
  │  ① tool declares no data_domains → skip (touches no data)
  │  ② principal is None             → skip (single-user semantics)
  │  ③ declaration lacks "fs.*"      → skip (db/net verdicts are D2)
  │  ④ no resolvable path argument   → skip (D1 parses path-shaped args)
  │  ⑤ resolve_work_path rejects     → tool reports sandbox error; no re-judgment
  │  ⑥ domain resolves to None       → skip (D1 compatibility, see below)
  │  ⑦ allow() denies → DATA_ACCESS_DENIED (domain/sensitivity/clearance only;
  │                     never echoes the path, never leaks domain content)
  ▼
three-layer permission intersection (frame whitelist ∩ RunConfig ceiling;
  READ tier does not consume the frame whitelist)
  │
  ▼
wait_for with timeout → result normalization
```

The order carries meaning: the data gate runs **before** the permission gate (`local_registry.py:211-215`) — "may it touch this" precedes "may it execute", and each gate fails independently. A test pins this down: a WRITE tool that is both outside the whitelist and short on clearance fails with `DATA_ACCESS_DENIED`, not `PERMISSION_DENIED` (`test_data_authz.py:132-147`). The error kind is a contract-layer addition, `ToolErrorKind.DATA_ACCESS_DENIED` (`api/v1/tools.py:57`).

**D1 compatibility strategy (a documented design/code divergence; code wins).** The design text of §3.3 requires "unresolvable domain → treat as confidential"; D1 deliberately does not implement this (§8, note 1): the `[data]` config section belongs to D2, and treating every unconfigured path as confidential would have broken all existing single-user behavior at once. D1's semantics are therefore "**unconfigured = data layer not enforced**": a target that falls outside every configured domain degrades to the `resolve_work_path` sandbox status quo; default denial applies only to configured domains (the built-in `fs.workdir`=public plus domains registered programmatically via `register_fs_domain`). `resolve_work_path` stays, and is orthogonal to domains: one answers "may this path leave the workdir", the other "may this principal read this region".

### 4.5 The exit side: after secrets enter the context

v1 does **no taint tracking** (marking confidential content and tracing its flow through the context) — the cost is high and enforcement against an LLM is impossible (§4). The defense sits at the two ends: entry authZ guarantees that whatever was read was the principal's to read; the exit is covered by the write gate — NET-sending tools derive to L2+ tiers, so cross-tier calls are stopped by escalation confirmation. In short: "read legally ≠ can exfiltrate". The documented residual risk: no data isolation inside a run of one principal (a low-tier skill that reads a secret can surface it in its output to a higher-tier skill of the same run — it is the same person's data); multi-tenant isolation is carried by the run boundary.

## 5. Effects and Verification (What)

The anchor suite `agent_os/tests/tools/test_data_authz.py` holds **13 cases, all green in this run** (`pytest tests/tools/test_data_authz.py -q` → 13 passed). Key assertions:

| Checkpoint | Case | Asserts |
|---|---|---|
| Verdict matrix | `test_allow_matrix_3x3` (:61) | 3 clearances × 3 sensitivities; pass iff ≥ |
| Fail closed | `test_allow_default_deny_and_fail_closed` (:71) | unknown clearance→public, unknown sensitivity→confidential, missing attrs→public |
| Single-user semantics | `test_allow_none_principal_single_user` (:83), `test_none_principal_not_intercepted` (:218) | None principal passes; even a configured confidential domain is not intercepted |
| Source constructors | `test_cli_principal_source` (:93), `test_web_single_user_principal_source` (:102) | subject/issuer/clearance=confidential |
| Gate ordering | `test_dispatch_order_data_before_permission` (:132) | data denial precedes permission denial |
| Non-leaking denial | `test_configured_confidential_domain_denied_without_leak` (:150) | error carries domain name and sensitivity, **not** the target path or domain content |
| Compatibility degradation | `test_unconfigured_path_degrades_to_sandbox` (:194) | unconfigured paths keep sandbox semantics |
| Identity pass-through | `test_tool_context_principal_filled` (:232) | `ToolContext.principal` goes from reserved to actually filled (`local_registry.py:255`) |
| Identity invariant | `test_principal_identity_invariant_across_frames` (:366) | root frame and an approve-once-admitted L3 escalated child capture the **same principal object** |
| Checkpoint | `test_checkpoint_principal_round_trip` (:395) | all serialized frames carry the principal; frames rebuilt by resume are equal |

Host wiring is live: both CLI entry points inject `cli_principal()` (`host/cli/main.py:163,292`); every Web single-user run injects `web_single_user_principal` (`host/web/run_manager.py:553`).

**Observable behavior**: single-user deployments (CLI, or Web without multi-user config) behave exactly as before the system existed — the principal is confidential or None and nothing is intercepted; an embedding host or test that injects a low-clearance principal gets a structured denial the moment a call crosses a domain, and the model can recover from the hint (pick another path, or ask for an identity with higher clearance).

**Ripple effects**: the principal field has become an anchor for other subsystems — Memory's retrieval-layer permission filtering takes a principal (`api/v1/memory.py:60`, reserved); Skill Lab records `promoted_by` on promote (`skills/gate.py:382`); showing the data surface (domains and sensitivities a call will touch) on the escalation confirmation card is a planned D3 input to EscalationRequest (§5.3, designed, not implemented).

## 6. Limitations and Boundaries (Limits)

1. **Unconfigured domains fail open (D1)**. The designed semantics "unconfigured = confidential" is deliberately inert in D1 (§8, note 1): a typo'd prefix or a missing registration silently unprotects instead of silently denying. Until the D2 config section lands, this is the known gap in the strictest posture.
2. **The shipped paths do not actually intercept**. CLI and Web single-user both grant confidential clearance, so the data gate is a no-op for the bundled hosts; protection engages only for embedders who deliberately inject low-clearance identities. D1 delivers the mechanism and the invariants, not out-of-the-box multi-user isolation.
3. **Coverage is limited to fs domains with path-shaped arguments**. `_check_data_access` only parses `args["path"]` (`local_registry.py:324-326`): `system.shell.exec` declares no data_domains and its command string is unparseable, so a low-clearance principal with it whitelisted can `cat` past the data gate (the mitigation: shell is EXEC/L3 and must pass the escalation gate); db/net domain declarations and verdicts are entirely D2.
4. **The second criterion is missing**. The per-subject domain whitelist (§3.2) is unimplemented, leaving clearance as the only verdict dimension; the verdict write-back into `ToolContext.credentials` is likewise D2; the `action` parameter is a placeholder — D1 does not distinguish read/list/metadata.
5. **No isolation inside a run**. A secret read by a low-tier skill can flow through its output to higher-tier skills of the same run (§4, documented residual risk); v1 accepts this under the "same principal = same person" assumption, and containment against injection spread relies on frame isolation and the escalation gate, not the data gate.
6. **Audit signals are unimplemented**. `data.access.denied` / `data.access.granted` (§6) belong to D2; today a denial appears only as a tool error in the trace, with no per-principal aggregation of "who was denied, how often".
7. **Multi-user and delegation chains are unimplemented (D3)**: all Web runs share the deployer's identity; agent-mediated calls get no weakest-link degradation — the caller's identity passes through unchanged.
8. **The denial surface leaks domain existence** (domain name and sensitivity ride in the error message). This is a deliberate usability trade-off (the model needs the criterion to recover), but it does expose domain naming and grading to a low-clearance caller.
9. **Explicit non-goals** (§9): taint tracking, per-file/row-level ACLs, SSO/OIDC, storage-layer encryption. These are out of scope, not omissions.

## 7. References

- Design docs: `docs/DATA-AUTHZ.md` (three-gate boundary, verdict model, D1 implementation notes, phasing); `docs/ESCALATION.md` §1 (scope boundary: escalation does not gate confidentiality)
- Contracts: `agent_os/src/agent_os/api/v1/principal.py` (Principal/DataDomain/allow/clearance_of/cli_principal/web_single_user_principal); `agent_os/src/agent_os/api/v1/tools.py:57,119-121,145` (DATA_ACCESS_DENIED, ToolSpec.data_domains, ToolContext.principal); `agent_os/src/agent_os/api/v1/frames.py:102-105` (SkillFrame.principal)
- Enforcement: `agent_os/src/agent_os/tools/local_registry.py:181-286` (dispatch pipeline), `:288-304` (register_fs_domain/_resolve_fs_domain), `:306-351` (_check_data_access), `:544-588` (resolve_work_path)
- Identity flow: `agent_os/src/agent_os/kernel/runner.py:206-228` (run injection point); `agent_os/src/agent_os/skills/local_file.py:146-148` (child inheritance); `agent_os/src/agent_os/kernel/checkpoint.py:156,226-234` (serialization round-trip); `agent_os/src/agent_os/host/cli/main.py:163,292`; `agent_os/src/agent_os/host/web/run_manager.py:430-440,553`
- Tests: `agent_os/tests/tools/test_data_authz.py` (13 cases)
