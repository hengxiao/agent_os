# Tools: Dispatch Pipeline & Permission Intersection

> Chapter 03 · Status: core implemented (dispatch pipeline, three-layer permission intersection, data gate, path sandbox — all with code and tests); reserved/extension surfaces partially implemented (credential injection, two-phase confirm, MCP adapter, pipeline-level result normalization) · Sources: `agent_os/src/agent_os/tools/local_registry.py`, `agent_os/src/agent_os/api/v1/tools.py`, `agent_os/src/agent_os/kernel/runner.py`, `docs/DESIGN.md` §8, `docs/DATA-AUTHZ.md`

## 1. Overview

A Tool is the only atomic capability in Agent OS allowed to touch the outside world: no call stack, no LLM loop, a single in-and-out (`docs/DESIGN.md` §2.2). The Tool Registry owns registration, validation, authorization, dispatch, and result normalization — the syscall table + seccomp + vfs of the OS metaphor (§8 preamble). It is deliberately *not* in the kernel: the kernel holds only the arbitration point at `_dispatch_call`, while the pipeline itself runs in a replaceable baseline implementation, `LocalPythonToolRegistry`, beneath the contract layer. Every tool call the model makes — whether it arrives as an LLM `tool_calls` entry, a syscall from inside the sandbox, or a dynamic dispatch from orchestration code — funnels into the same pipeline and the same gates.

## 2. Motivation and Background (Why)

**Why not in the kernel.** The microkernel admission test (flow control / permission control / IPC) excludes tool *execution* from the kernel — execution is a replaceable capability surface: local functions, MCP services, remote APIs should all be attachable. The kernel's `_dispatch_call` (`kernel/runner.py:547`) therefore does only three things: route pseudo-tools (`skill.*` / `python_orchestrate` / `ask_supervisor`), enforce the manifest whitelist gate, and emit `pre/post:tool.call` signals — then hands the call, wrapped in a `ToolDispatchContext`, to the registry (`runner.py:584-594`). This is the "contract first, baseline replaceable" axiom applied to tools: `Tool` is a Protocol with exactly two members, `spec` and `async __call__(args, ctx)` (`api/v1/tools.py:171-177`), and any implementation satisfying it can replace the baseline.

**Why "functions are tools".** The tool's author knows the tool best, but forcing them to hand-write JSON Schema, permission declarations, and timeout policy doubles the cost of every change, and schema/signature drift is only a matter of time. `LocalPythonToolRegistry` therefore registers via decorator and derives the schema from the signature (§8.4): type annotations map to JSON primitives, defaults decide `required`, the docstring becomes the description. Documentation, contract, and implementation share a single source of truth.

**Why a three-layer intersection rather than a single switch.** Each layer answers a different question, and no single layer can arbitrate alone. The tool's self-declared level answers "how dangerous is this operation by nature" (the author knows); the frame's manifest whitelist answers "which capabilities does this skill's business need" (the skill author's intent); the RunConfig ceiling answers "what will this run, on this host, tolerate" (the deployer's floor). The three intersect, and any one of them can deny (§8.2): a skill author cannot gain capability by omitting the whitelist, and a host cannot sneak dangerous tools into an untrusted skill by loosening the ceiling.

## 3. Problem Statement (What It Solves)

The pipeline addresses concrete, reproducible failure scenarios, not abstract "safety":

1. **The model emits off-contract arguments.** LLMs routinely produce type errors like `{"path": 123}`. If the dispatcher "helpfully corrects" them (silent coercion, guessed intent), the error surfaces later, deeper, and irreproducibly. The pipeline fails fast: off-schema input returns `INVALID_ARGS` and never executes (§8.1 — "no smart correction").
2. **The model hallucinates a tool outside the whitelist.** A skill manifest lists only `system.file.read`, yet the model attempts `system.shell.exec` — through hallucination or injected instruction. This must be refused structurally, not by pleading in the prompt.
3. **A skill probes the run-level floor.** The host starts a run with `ToolPolicy(max_permission=WRITE)` (the default, `api/v1/tools.py:45`); a skill declares an EXEC-tier tool and whitelists it. Two layers have consented; the third (the RunConfig ceiling) must still block.
4. **Path escape.** The model hands an fs tool `../../etc/passwd`, or tries to write into a `read_paths` read-only zone — the path sandbox must resolve and refuse before the tool runs.
5. **Over-clearance reads, with the error message itself leaking.** When a principal's clearance is below a `confidential` domain's sensitivity, a refusal that echoes the path or domain content turns the error channel into an exfiltration channel.
6. **Tools time out, crash, or get cancelled.** If exceptions cross the dispatch boundary into the agent loop, the frame state machine sees an unstructured failure; if a tool's own catch-all swallows `CancelledError`, safe-point semantics break.
7. **Schema ordering drift in the injected request.** Tool schemas sit in the prompt's static prefix; unstable ordering breaks the KV cache (§7.4, invariant 5) — the registry must guarantee fixed order.

## 4. Design and Mechanism (How)

### 4.1 The Dispatch Pipeline: The Full Path of Every Call

The complete path of one `ToolCall{id, name, args}` (`api/v1/messages.py:35-40`), with code anchors:

```
LLM tool_calls / sandbox syscall / orchestration dispatch
        │
        ▼
Kernel._dispatch_call                     kernel/runner.py:547
  ├─ skill.* / skill__*      → _invoke_skill (sub-skill push; out of scope)
  ├─ python_orchestrate      → orchestration sandbox (its calls re-enter here, :675-716)
  ├─ manifest whitelist gate → not in permissions.tools ⇒ PERMISSION_DENIED  :554-562
  ├─ pre:tool.call (SYNC)    → Veto / Stop / Pause / Modify (may patch args) :567-583
  ▼
LocalPythonToolRegistry.dispatch            tools/local_registry.py:181
  ① lookup         → unregistered: NOT_FOUND (retryable=False)              :189-198
  ② schema check   → jsonschema.validate, fail fast: INVALID_ARGS           :200-210
  ③ data-layer authZ → DATA_ACCESS_DENIED (first of two gates, see 4.3)     :213, :306-351
  ④ 3-layer permission intersection → PERMISSION_DENIED (see 4.2)           :219-238
  ⑤ replay short-circuit → replayable + records: pop recorded value in
                     call order and return without executing                :242-246
  ⑥ build ToolContext → resolve workdir/read_paths; principal passes
                     through with the frame                                 :247-260
  ⑦ wait_for timeout → TimeoutError: TIMEOUT (retryable=True)               :262-271
  ⑧ exception floor  → CancelledError re-raised as-is (:272-273);
                     any other Exception → INTERNAL, hint = last 500 chars
                     of the traceback                                       :274-283
  ⑨ normalization  → ToolResult passes through; bare values wrap ok=True    :284-286
  ▼
post:tool.call → _result_payload → observation written back to the frame   runner.py:595-598, :1217
```

Two ordering decisions are worth stating. **First, schema validation precedes everything**: when arguments are ill-formed, neither the data gate nor the permission gate needs to run — they presuppose a decidable call. **Second, the data gate precedes the permission gate** (comment at `local_registry.py:211-212`): the data gate decides "can it be touched" (confidentiality), the permission gate decides "is it allowed" (side effects); they fail independently, and checking confidentiality first terminates over-clearance reads earlier, with less information disclosed. The replay short-circuit is deliberately placed *after* every security gate (:239-241 comment): replay replaces only the "execute" step — crash recovery must not become a permission bypass.

### 4.2 The Three-Layer Permission Intersection

The decision lives in one boolean expression at `local_registry.py:219-238`:

| Layer | Criterion | Declared by | Nature |
|---|---|---|---|
| Tool's self-reported level | `spec.permission ∈ {READ<WRITE<NET<EXEC}` | tool author (`api/v1/tools.py:32-38`) | the compared value, not a grant |
| Frame whitelist | `call.name ∈ frame_ctx.allowed_tools` (manifest `permissions.tools`) | skill author, passed in by the kernel via the runner (`runner.py:588`) | WRITE and above must be named explicitly |
| RunConfig ceiling | `spec.permission ≤ tool_policy.max_permission` | host/deployer (default WRITE, `tools.py:45`) | run-wide floor |

Three details matter:

- **READ-tier tools are exempt from the frame whitelist** (§W0-1, `local_registry.py:216-218`): read-only, no side effects, and the fs boundary is already enforced by the `resolve_work_path` zones; forcing explicit entries would only bloat whitelists into noise. Note the exemption applies only at the registry layer — on the production path the runner's manifest gate (:554) checks READ tools by name as well, so the exemption's real beneficiaries are embedders calling the registry directly (the comment says as much: "生产路径权限语义不变" — production-path permission semantics unchanged). The manifest whitelist is, by design, checked **twice** on the production path.
- **Levels are an IntEnum**, so `>` is a tier comparison; the denial's `hint` lists the frame's available tools, turning a refusal into a self-repairable observation (:232-236).
- **A fourth, soft gate exists beyond the three**: HumanApproval-style sidecars can front high-tier tools via `pre:tool.call` (§8.2). That is the signal plane's job, not the registry's — the registry only guarantees a vetoed call never reaches execution.

### 4.3 Data-Layer authZ: A Second, Orthogonal Gate

`_check_data_access` (`local_registry.py:306-351`) implements the enforcement point of `docs/DATA-AUTHZ.md` §3.3. A tool declares which data-domain classes it touches via `spec.data_domains` (e.g. `["fs.*"]`); at dispatch time the target path is resolved with `resolve_work_path` and matched against host-registered domain boundaries, longest prefix first (`register_fs_domain`, :288-294), falling back to the built-in default domain `fs.workdir` (public); `allow(principal, domain)` then judges clearance ≥ sensitivity.

D1's compatibility policy is an explicit trade-off: **unconfigured means unenforced**. Three cases pass straight through — the tool declares no `data_domains`, the principal is `None` (v1 single-user semantics), or the target falls into no configured domain — and behavior is identical to before this system existed; default-deny applies only to configured domains (:309-316 comment). Refusal messages carry only the domain name, sensitivity, and clearance — **never the path or domain content** (:344-349), structurally closing the "error channel leaks" hole. Note the divergence from the prose of `DATA-AUTHZ.md` §3.1/§3.3 ("unconfigured domains default to confidential", "unresolvable targets treated as confidential"): the D1 implementation chose the backward-compatible lenient stance, confirmed by both the code comment and the test `test_unconfigured_path_degrades_to_sandbox` — **the code is authoritative**.

### 4.4 The Path Sandbox: One Shared Resolver

`resolve_work_path` (`local_registry.py:544-588`) is the three-way judgment shared by the four fs tools and shell: ① the target lies under a `read_path` → read-only zone; reads allowed (**possibly outside the workdir**), writes refused; ② under the workdir → read-write; ③ anything else → `INVALID_ARGS` escape refusal, with a hint computed from the runtime's actual legal zones. The design trade-off is *zones*, not a single directory: skills routinely need to read the host's project tree while writing only to their own output area, and a single zone forces a choice between opening writes (dangerous) or copying inputs into the workdir (wasteful and lossy). When `RunConfig.workdir` is unset, the registry falls back to a per-run temp directory (`_workdir`, :364-370), reclaimed by `release_run` via `rmtree` (:353-362) — the default stance never silently widens the boundary.

### 4.5 Functions as Tools, and Contract Derivation

`derive_spec` (`local_registry.py:608-634`) derives as follows: a parameter named `ctx` is the `ToolContext` injection point and never enters the schema (:617-618); `str/int/float/bool` map to JSON primitives, `list[X]`/`dict` to array/object, single-arm Unions (i.e. `Optional`) unwrap, everything else degrades to `{}` (unchecked); parameters without defaults go to `required`. Sync functions run under `asyncio.to_thread`, async ones are awaited directly (`_FunctionTool.__call__`, :74-82). The `ToolSpec` contract is frozen verbatim (§14.1), including all v1 reserved fields (`examples/cacheable/confirm/concurrency_safe/...`); additions are always additive with defaults (§W0-2 added `cost_hint/replayable/concurrent_safe`; escalation added `side_effect`; the data layer added `data_domains`). `derive_side_effect` (`tools.py:134-136`) defaults the side-effect class from permission — READ→none, WRITE/NET→reversible, EXEC→irreversible — and this derivation feeds the escalation system's trust tiers, so declarations on the tool surface directly determine the quality of the trust model.

**One divergence from the docs**: `DESIGN.md` §8.4 says "first paragraph of the docstring → description", but the implementation takes the **entire** docstring — the comment at :622-626 explains why ("Use when / Do not use when" guidance and error semantics live in later paragraphs, and those are exactly what the model uses to pick tools; the description sits in the static prefix, so extra length does not break the KV cache). The code is authoritative.

### 4.6 The Built-in Surface and Alias Migration

`with_builtins` (:372-538) assembles 17 canonically named tools (seven fs, shell, two net, blob, time, three todo, skill.search), and the constructor additionally registers `fetch_page` (:99-104 — gated with §6.1, rationale in the `tools/std_web.py` module docstring). Names are hierarchical (`system.file.read`); legacy flat names (`fs_read`) survive as aliases via `register_alias`, one function body under several spec names (:126-138) — existing skills do not break during the naming migration. Contract-field declarations are hard-gated: READ-tier tools must declare `idempotent/cacheable/concurrent_safe`; delete/kill-class tools must explicitly declare `irreversible` (TIER-STANDARDS §1, comment at :518-520); `cost_hint` states magnitude, never absolute seconds.

## 5. Effects and Verification (Results)

Test evidence (the parts directly covering this chapter, under `agent_os/tests/`; measured on this run: **283 passed + 32 xfailed**, 3.11s):

| Test file | Cases | Coverage |
|---|---|---|
| `tools/test_builtins.py` | 14 | built-in assembly, `test_tool_policy_caps_permission` (:189, ceiling gate), `test_fs_path_traversal_rejected` (:97, escape), edit unique-match, alias resolution, shell timeout clamp with no orphan processes |
| `tools/test_data_authz.py` | 13 | `test_dispatch_order_data_before_permission` (:132, gate ordering), `test_configured_confidential_domain_denied_without_leak` (:150, leak-free refusal), unconfigured degradation (:194), principal invariance across frames (:366) and checkpoint round-trip (:395) |
| `tools/test_std_foundation.py` | 9 | workdir three-zone split, read-only zone rejects writes (:82), actionable escape hints (:143), READ-tier contract fields complete (:213) |
| `tools/test_std_tools.py` / `test_blob.py` / `test_builtin_side_effects.py` | 17 / 5 / 2 | std tool behavior, blob ref form, side-effect derivation |
| `test_contracts.py` | 7 | frozen-surface contracts incl. ToolSpec (§14.1) |
| `test_std_gate.py` | parametrized | tool gate: descriptions must state "when to use", parameters must be object schemas, READ ⇒ cacheable, dual spellings kept in sync |

The 32 xfails concentrate on a single item: `test_parameters_are_documented` — `derive_spec` derives schemas from signatures and has no mechanism for per-parameter descriptions (the xfail reason cites a measured effect: this item moves tool-call accuracy from 72% to 90%). A known gap, explicitly marked, not a silent failure.

Real configuration: `instance/agent-os.toml` + `instance/skills.yaml` show the host-side assembly form; example skills such as `agent_os/examples/workspace_janitor` consume this permission model through their whitelists. **Ripple effects**: ① the fixed schema order (registration order, `schemas_for` :153-163) is the data source for §7.4 prefix-cache invariant 5; ② `ToolErrorKind.retryable` lets the LoopDetector and the model distinguish "retry" from "change strategy" (`tools.py:53-62`); ③ the `side_effect` derivation supplies the leaf values for a skill's recursive max-tier computation — declaration quality on tools directly determines escalation judgment quality; ④ the in-sandbox syscall channel reuses the same `_dispatch_call` (`runner.py:690-716`), so orchestration code has no privilege-escalation bypass; ⑤ `specs()` feeds the Web UI's tool browser (:143-145).

## 6. Limitations and Boundaries

1. **Credential injection is unimplemented.** §8.1 lists "credential injection (scoped per tool declaration, never global env)" and the contract reserves `ToolContext.credentials`, yet dispatch always fills `{}` (`local_registry.py:259`). Tools that need credentials currently detour through host environment variables — exactly what the contract intends to forbid.
2. **Two-phase confirm is declared, not enforced.** `system.file.delete` declares `confirm=True` (:521-528), but dispatch contains no dry-run/confirmation-token logic; the two-phase semantics of §8.2 remain contract-level. Today's actual defenses are the whitelist plus human-approval sidecars.
3. **Concurrency and cacheability declarations are unenforced.** `cacheable/concurrent_safe/concurrency_safe` are all "declared, not enforced" (comment at `tools.py:91-93`), and nothing yet consumes the concurrency-safety judgment that `parallel_invoke` is meant to depend on. Misdeclaration currently costs nothing.
4. **Pipeline-level result normalization has not landed.** §8.1's "size cap → spill to blob" and "call-count annotation (Tool call #N)" do not exist in the registry; spill is per-tool improvisation (`std_web.py`'s fetch_page, `std.py:376-383`'s fs_search) with unaligned thresholds and retention policies, and the string `"Tool call #"` appears nowhere in the repo except DESIGN.md. Same story for `untrusted_source`: the contract field and http_fetch's declaration exist, the uniform wrapping marker does not.
5. **The READ-tier whitelist exemption is narrow.** As noted in 4.2, the runner gate checks the whitelist first on the production path, so the exemption only helps direct embedders; the two layers deliberately differ (one exempts, one doesn't), which is intended redundancy but also a comprehension cost.
6. **Data-layer authZ is D1 only.** It covers the fs domain exclusively; db/net domain declaration and judgment belong to D2 (:322-323); the `agent-os.toml [data]` config section is unwired, so domain boundaries can only be registered in host code via `register_fs_domain`; the gap between "unconfigured = unenforced" and the document's target stance ("default confidential") remains open, and multi-user isolation is carried by the run boundary.
7. **Type derivation is shallow.** Multi-arm Unions, nested generics, and literal types degrade to `{}` — schema validation is void for such parameters, and the fail-fast promise covers primitives only.
8. **Cancellation of sync tools is fictional.** `asyncio.to_thread` cannot interrupt a thread, so the underlying function may keep running after TIMEOUT returns; shell_exec compensates with timeout clamping and child-process reaping (`test_shell_exec_timeout_clamped_to_spec_no_orphan`), but ordinary sync tools get no such treatment.
9. **MCP and persistent shell are not wired.** §8.3's MCP adapter and supply-chain isolation remain documentary promises; `shell_exec` is a one-shot subprocess, and the "run-scoped persistent session (cwd/env preserved across calls)" described in §8.3 is explicitly marked as a later milestone (`with_builtins` docstring, :380-381) — the doc and the code disagree, and the code is authoritative.
10. **Replay wiring is incomplete.** The `replayable` pop mechanism is implemented and anchor-tested, but the record source (host trace → `replay_records`) is "left to a later milestone" (:11-12, :239-240 comment); today's replay reproduces the LLM side while tool side effects still really happen.

## 7. References

- Design docs: `docs/DESIGN.md` §2.2, §2.4, §7.4, §8 (Tool Registry, whole section), §14.1; `docs/DATA-AUTHZ.md` §2-§3, §5.2; `docs/ESCALATION.md` §2.1; `docs/TIER-STANDARDS.md` §1
- Contracts: `agent_os/src/agent_os/api/v1/tools.py` (Permission / ToolPolicy / ToolErrorKind / ToolSpec / ToolContext / ToolDispatchContext / Tool / BlobStore); `agent_os/src/agent_os/api/v1/messages.py:35-40` (ToolCall)
- Implementation: `agent_os/src/agent_os/tools/local_registry.py` (dispatch :181-286; `_check_data_access` :306-351; `resolve_work_path` :544-588; `derive_spec` :608-634; `with_builtins` :372-538); `agent_os/src/agent_os/tools/blob.py`; `agent_os/src/agent_os/tools/builtins.py`; `agent_os/src/agent_os/tools/std.py`; `agent_os/src/agent_os/tools/std_web.py`
- Kernel gate: `agent_os/src/agent_os/kernel/runner.py:547-598` (`_dispatch_call`), :675-716 (syscall channel)
- Tests: `agent_os/tests/tools/test_builtins.py`, `agent_os/tests/tools/test_data_authz.py`, `agent_os/tests/tools/test_std_foundation.py`, `agent_os/tests/tools/test_std_tools.py`, `agent_os/tests/tools/test_blob.py`, `agent_os/tests/tools/test_builtin_side_effects.py`, `agent_os/tests/test_contracts.py`, `agent_os/tests/test_std_gate.py`
- Examples and config: `agent_os/examples/workspace_janitor`, `instance/agent-os.toml`, `instance/skills.yaml`
