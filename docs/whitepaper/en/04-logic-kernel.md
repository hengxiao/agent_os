# Logic Kernel & Orchestration Sandbox

> Chapter: 04 · Status: Implemented (all three backends — in-process, subprocess sandbox, Docker sandbox — plus the orchestration syscall channel are landed and tested; sandbox-side `spawn`/`blob` bridging, concurrent syscalls, and Docker-backed orchestration are designed but not implemented) · Sources: `docs/DESIGN.md` §9; `docs/CODE-ORCHESTRATION.md`; `agent_os/src/agent_os/api/v1/logic.py`, `logic/`, `kernel/runner.py`, `kernel/logic_router.py`, `tools/builtins.py`

## 1. Overview

The Logic Kernel is the **single execution point for logic code** in the system — the CPU/ALU of the OS metaphor: the kernel runner schedules and arbitrates but never executes an instruction itself (docs/DESIGN.md §9). Two kinds of code pass through this choke point: **code skills** (`kind: code`, author-written and version-controlled) and **LLM-generated dynamic code** (submitted via the built-in tool `system.python.exec` or the orchestration pseudo-tool `python_orchestrate`). Backends are routed by trust level (TRUSTED / SANDBOX): in-process, subprocess+rlimits, and Docker container implementations coexist behind one contract and are interchangeable. On top of this, the orchestration sandbox models tool calls as *system calls* made by sandboxed code, completing the microkernel analogy of "sandbox = user-space process, tool = syscall" (docs/CODE-ORCHESTRATION.md §1.2).

## 2. Motivation & Background (Why)

**Why not inside the kernel.** The microkernel axiom restricts the kernel to flow control, permission control, and IPC (docs/DESIGN.md §1, axiom 1). Executing logic code is neither necessary for loop progress nor an arbitration point — it is policy, not mechanism, and belongs outside for the same reason the kernel does not assemble prompts or persist state. The kernel only builds an `ExecRequest` and routes it at dispatch time (`kernel/runner.py:491-541`).

**Why one choke point instead of two.** Code skills and dynamic code have opposite trust profiles: the former are versioned and statically reviewable at load time, hence trusted by default; the latter are generated live by the model and must be treated as untrusted input. Giving each its own execution facility would fork the policy for resource limits, supervision signals, and isolation into two divergent copies. Converging on a single `LogicKernel` contract (`api/v1/logic.py:181-188`) buys three unifications: uniform limits, uniform signals (`pre/post:logic.exec`, auditable and vetoable), and a uniform isolation policy — each instance self-reports its `trust` level and the router selects by tier.

**Token economics is the second driver.** Every tool call a prompt frame makes lands in the frame context and is re-billed on every subsequent LLM request. Batch workloads (reading 100 files one by one with `fs_read`, then aggregating) mean 100 tool round-trips with all intermediate results resident in context. Letting the LLM write a one-off orchestration script that loops inside the sandbox keeps intermediates in the execution environment; only the aggregate returns to context — the design doc cites the book's estimate of roughly two orders of magnitude in token savings (docs/CODE-ORCHESTRATION.md §1.1).

**Isolation and orchestration used to be mutually exclusive.** Before the orchestration channel landed, enabling `logic: {mode: sandbox}` or `RunConfig.logic_policy.force_sandbox` set a code skill's `ctx` to `None`, degrading it to pure computation — multi-tenant hosts paid for isolation with castrated functionality. Closing the "sandboxed code → tools" edge makes both available at once (docs/CODE-ORCHESTRATION.md §1.1, capability matrix).

A directional contrast is worth noting here: the escalation system governs "may a lower trust tier come in" (trust boundaries between frames), while the Logic Kernel's sandbox governs "may the code get out" (capability boundaries of the executing body). The two point in opposite directions but complement each other, jointly closing the loop of least privilege across the two dimensions of *invocation* and *execution*.

## 3. Problem Statement (What It Solves)

1. **Where dynamic code runs.** If LLM-generated code executed in the host process, one line — `os.environ["ANTHROPIC_API_KEY"]` — would exfiltrate credentials over the network (this exact example appears in `logic/python_sandbox.py:271-273`). Problem: give the model a "code interpreter" without exposing the host process to it.
2. **Context bloat from batch operations.** Per-file processing produces N tool round-trips whose N intermediate results live in frame context forever and are billed per turn. Problem: intermediate variables should stay in the execution environment; only conclusions should return to context.
3. **Sandbox-tier capability castration.** Under forced sandboxing, code skills lose `ctx.invoke`/`call_tool`, and the orchestrator pattern (deterministic control flow + on-demand LLM skills) becomes unavailable. Problem: isolation strength and composability should not be mutually exclusive.
4. **The risk of scripts bypassing the authorization plane.** Handing a script the tool registry or a kernel handle directly would bypass the manifest whitelist, ToolGuard vetoes, and accounting — precisely why STDLIB v2 §9 originally rejected "live code orchestration" (docs/CODE-ORCHESTRATION.md §8). Problem: open up orchestration without weakening the audit posture relative to a prompt frame's per-call invocation.
5. **CPU-heavy code blocking the event loop.** Coroutine-level parallelism shares one event loop, so a compute-heavy code skill starves its sibling branches (docs/DESIGN.md §3.4 note). Problem: a process-level execution tier is needed to absorb such workloads.

## 4. Design & Mechanism (How)

### 4.1 Contract and trust routing

The contract layer freezes four elements (`api/v1/logic.py`): the `LogicKernel` Protocol (`name` / `trust` / `execute`, :181-188), `ExecRequest` (`source/language/entry/args/ctx/limits/network/dispatch_fn`, :149-167), `ExecResult` (`value/error/stdout/stderr/usage`, :170-178), and the three-valued `LogicError` (`RUNTIME_ERROR | LIMIT_EXCEEDED | REJECTED`). Structured errors are first-class: when a code skill raises `SkillError`, its `kind/hint/retryable` survive all the way to the model and to orchestration scripts instead of being flattened into a prose sentence (:67-102).

Routing lives in `LogicKernelRouter` (`kernel/logic_router.py:22-37`): a manifest declaring `logic: {mode: sandbox}` or the global `force_sandbox` selects SANDBOX, otherwise TRUSTED; an unassembled target tier is an assembly error. Dynamic code **bypasses this router entirely** — the `system.python.exec` tool holds a SANDBOX instance directly at assembly time (`tools/builtins.py:504-595`), so the axiom "LLM dynamic code is always sandboxed, with no configuration to disable it" is structurally unviolable.

### 4.2 The three execution backends

| Backend | Implementation highlights | Isolation | Used for |
|---|---|---|---|
| `InProcessLogicKernel` | `asyncio.wait_for` coroutine timeout; stdout/stderr redirect capture; JSON-serializability check on the return value; `RunAborted`/`MaxDepthExceeded`/`SkillLoadError` are re-raised, never folded into a result (`logic/inprocess.py:84-85`) | None, coroutine-level only | Trusted code skills (default), debugging |
| `PythonSandboxLogicKernel` | `sys.executable -I -c` subprocess; `preexec_fn` applies rlimits (CPU/address space/file size/fd count, `logic/limits.py:18-29`); **env allowlist** (`_ENV_ALLOWLIST` — no `HOME`, no `*_API_KEY`, `logic/python_sandbox.py:152`); fresh empty temp cwd per execution; parent-side wall watchdog kills the process | Process-level (partial) | LLM dynamic code (mandatory), code skills declaring sandbox |
| `DockerPythonSandboxLogicKernel` | `docker run --rm`: `--network none` system-level network cut, `--memory/--cpus/--pids-limit` cgroup limits, read-only root fs, `--cap-drop ALL`; exit code 137 → `LIMIT_EXCEEDED`; `docker kill` as wall-timeout backstop (`logic/docker_sandbox.py`) | Container-level | Hosts with docker (`python_exec = "docker"`) |

The subprocess backend's module docstring itemizes what v1 isolation actually provides and **explicitly lists three non-goals**: no network isolation, no filesystem isolation (absolute paths still reachable), no process isolation (`logic/python_sandbox.py:7-18`). Conclusion: v1 stops "casually grabbing credentials / browsing the project tree", not a determined attacker; use the Docker backend for real isolation. The isolation ladder (seccomp/nsjail → microVM) arrives as backend replacements with the contract unchanged; the docs warn explicitly that **a venv is not a sandbox**.

### 4.3 LogicContext: orchestrator capability in the TRUSTED tier

In TRUSTED mode a code skill has the same compositional power as a prompt skill, expressed in code. `KernelLogicContext` (`kernel/logic_context.py`) offers `invoke` (push a child frame), `call_tool` (via the Tool Registry), `spawn`/`wait` (§3.4 background frames), `board` (a blackboard namespace proxy arbitrated per manifest whitelist), and `chat` (direct ProviderManager access for self-driven multi-turn loops). The key property: **everything re-enters the kernel's `_dispatch_call` path** — whitelists, signals, and accounting are identical to a prompt frame's calls. A code skill is thus an "orchestrator": deterministic control flow (loops/branches/aggregation) plus on-demand LLM skills.

### 4.4 The orchestration sandbox: tool calls as syscalls

`python_orchestrate` is a kernel-intercepted pseudo-tool (`ORCHESTRATE_TOOL`, `api/v1/logic.py:116`): it never enters the Tool Registry; `_dispatch_call` intercepts it and hands it to `_run_orchestration` (`kernel/runner.py:552-553,718-837`). Two gates: the config flag `[tools] python_orchestrate` defaults to false (Fail-Safe Default, `runtime/config.py:260-262`), and the skill must explicitly declare it in `permissions.tools` — the tool is visible and usable only when both pass.

```
parent-frame LLM ──tool_call: python_orchestrate{code}──▶ kernel runner._run_orchestration
                                                            │ ① config + manifest gates
                                                            │ ② pre:logic.exec (CodeScanner may Veto)
                                                            ▼
                                                SANDBOX Logic Kernel (subprocess)
   ┌───────────────────────────────────────────────────────────────────────────┐
   │ sandbox script                socketpair (single-line JSON, version v1)   │
   │ ctx.call_tool("fs_read",…) ──{"syscall":"tool","id":"s1",…}─────────────▶ │
   │                                  │ _serve_syscalls (pure transport)       │
   │                                  ▼                                        │
   │                       _syscall_dispatcher (kernel-side count, cap 50)     │
   │                                  ▼                                        │
   │                       _dispatch_call (as the CALLER frame):               │
   │                       manifest whitelist ∩ RunConfig → pre:tool.call      │
   │                       (ToolGuard may Veto) → execute → account            │
   │ ◀──────────────── {"id":"s1","ok":true,"value":…} ──────────────────────  │
   │ intermediates stay in the sandbox; only the script's `result`             │
   │ returns to frame context                                                  │
   └───────────────────────────────────────────────────────────────────────────┘
```

Key points:

- **No privilege escalation (the security pivot)**: the script executes with the caller frame's identity; its callable set is that frame's manifest whitelist ∩ RunConfig ceiling, and every syscall emits `pre/post:tool.call` (payload carries `"via": "orchestrate"`) so ToolGuard can veto each one. The same LLM could already call those tools one by one — what changes is only the shape of invocation (batched/programmatic) and its cost (context-free) (docs/CODE-ORCHESTRATION.md §2.3).
- **The service loop is pure transport** (`logic/python_sandbox.py:189-250`): dispatch and the call cap live kernel-side. Hitting the cap returns a structured error (`hint: 收敛脚本` / "converge the script") instead of breaking the pipe, so the script can handle it; runaway scripts are backstopped by `wall_time`. This is a deliberate deviation recorded at landing: the design put the cap in the transport layer, but in practice that surfaced as an unrecoverable `BrokenPipeError` for the script (docs/CODE-ORCHESTRATION.md §12.1).
- **Hard failures are never downgraded**: `RunAborted`/`MaxDepthExceeded` are not flattened into a script-ignorable error inside the service loop — the channel is closed and `_collect` re-raises before any result normalization (`logic/python_sandbox.py:231-237,360-363`). Otherwise a budget overrun or a sidecar `Stop` would be swallowed by the script and the run would finish normally (a real defect observed in testing).
- **Two ctx flavors over one transport**: `_SyncCtx` for straight-line LLM scripts (no async boilerplate), `_AsyncCtx` for code-skill handlers — character-identical to the TRUSTED contract, so the same handler behaves equivalently in both tiers (`logic/python_sandbox.py:86-111`).
- **Crash semantics**: if the script dies midway, the syscalls already executed have real side effects and are in the trace; the result reports `RUNTIME_ERROR` plus the executed-call inventory (`calls/failed/limit_hit`), and the model can decide compensation using the tools' `idempotent` contract (`kernel/runner.py:810-826`).

### 4.5 Key trade-offs

- **Why not merge into `python_exec(tools=true)`**: permissions are granted by name, so merging would silently widen the authorization surface of every existing manifest that declares `python_exec`; `python_exec` enjoys the pure-function dividends of `cacheable`/`concurrency_safe`, while orchestration is side-effect-laden — one name cannot carry two contracts; the `http_fetch`/`http_request` split sets the precedent; and misuse costs are asymmetric (docs/CODE-ORCHESTRATION.md §2.1).
- **Why a socketpair instead of the design's fd-3 twin pipes**: full-duplex, a single fd, and `asyncio.open_connection(sock=…)` yielding a back-pressured reader/writer so large results `drain()` without blocking the event loop; the fd number reaches the child via the `AGENT_OS_SYSCALL_FD` env var (docs/CODE-ORCHESTRATION.md §12.2).
- **Why kernel-intercepted rather than a registered tool**: a tool function cannot reach the kernel, but syscall arbitration must bind the calling frame and its manifest — the same reason `skill.*` is intercepted; permission-sensitive dispatch stays in the kernel.
- **Why the STDLIB §9 rejection was overturned**: the original premise was that live orchestration necessarily bypasses whitelists and audit. The syscall intermediary routes every call through the same gate with the callable set still statically bounded by the manifest — an audit posture identical to a prompt frame's. What deserved rejection was runtime wildcard whitelisting, which this design does not need (docs/CODE-ORCHESTRATION.md §8). The resulting division of labor: `std/combinators` carries reusable, named, testable patterns; `python_orchestrate` carries one-off glue.

## 5. Effects & Verification (Results)

Test baseline (verified by running `pytest tests/logic/test_orchestration.py tests/logic/test_python_sandbox.py tests/kernel/test_code_skills.py -q`: **38 passed**):

- `agent_os/tests/logic/test_orchestration.py`: 17 test functions (18 cases with parametrization). Key assertions: out-of-whitelist calls fold into a script-visible `PERMISSION_DENIED` instead of crashing the whole orchestration (`test_out_of_whitelist_tool_denied_into_script`); ToolGuard vetoes a syscall and the reason reaches the script; CodeScanner vetoes the entire script at `pre:logic.exec`; after a 100-`fs_read` loop the parent frame gains exactly one tool result while the signal stream shows 100 `via: orchestrate` records (`test_loop_keeps_intermediates_out_of_context`); exceeding `max_tool_calls` reports the executed inventory; the same handler yields identical results in TRUSTED and SANDBOX tiers (`test_trusted_and_sandbox_equivalent`); a run containing orchestration replays with an empty diff (deterministic tools); hard failures escape the orchestration (`test_hard_failure_escapes_orchestration`).
- `agent_os/tests/logic/test_python_sandbox.py`: 12 cases covering wall-timeout kills, stderr-tail error mapping, stdout truncation, module-driver mode, **host environment not inherited** (`test_host_env_is_not_inherited`), and cwd isolation (`test_sandbox_cwd_is_isolated_temp_dir`).
- `agent_os/tests/logic/test_docker_sandbox.py`: 8 cases (whole file skips when docker is unavailable), including `--network none` enforcement and memory-limit OOM.
- `agent_os/tests/kernel/test_code_skills.py`: 8 cases covering code skills orchestrating prompt skills, `ctx.call_tool`, `pre/post:logic.exec` carrying trust, whitelist denial, and `force_sandbox` routing.

Real-world example: `agent_os/examples/research_pipeline/skills.yaml` hands the deterministic stages of a research pipeline (planning, fetching, merging, verifying, counting — ten `kind: code` skills) to the Logic Kernel, leaving only the volatile know-how to the LLM — a full demonstration of the "stable logic goes code, volatile know-how goes prompt" criterion. `instance/agent-os.toml:38` enables the container tier with `python_exec = "docker"` (auto-fallback to subprocess with a warning when docker is unavailable, `runtime/config.py:155-168`).

Ripple effects: the STDLIB §9 rejection clause was formally revised (orchestrator/combinator division of labor rewritten); SANDBOX-tier code skills regained orchestration, so `force_sandbox` no longer castrates functionality; the syscall protocol became part of the contract (`SYSCALL_PROTOCOL_VERSION = 1`, `api/v1/logic.py:143`); the assembly-time gate explicitly exempts pseudo-tools (`runtime/builder.py:183-185`).

## 6. Limitations & Boundaries

1. **The subprocess sandbox is not a security boundary.** Network, filesystem (absolute paths), and processes are all unisolated in v1 — the module docstring admits each item (`logic/python_sandbox.py:7-18`). The env allowlist only prevents casual credential reads; hard-coded paths still reach credential files. Adversarial scenarios require the Docker backend, which in turn requires a docker CLI and image on the host.
2. **The Docker backend does not support orchestration.** `docker_sandbox.py` ignores `dispatch_fn` (fd pass-through is open question 5 in the design); with `python_exec = "docker"` configured and `python_orchestrate` enabled, the in-sandbox `ctx` is `None` and the first call raises `NameError`. Orchestration currently belongs only to the subprocess backend — the strongest isolation tier is the one without orchestration.
3. **Script wall clock includes kernel-side syscall time.** The design requires that "script wall_time excludes the kernel's time executing syscalls" (§3), but the implementation is a total `asyncio.wait_for(proc.communicate(), timeout=wall)` (`logic/python_sandbox.py:340`); slow tools burn the script's budget, mitigated only by raising the `timeout` argument. Anchor-test item 6 has no corresponding test case.
4. **The syscall channel is narrow.** Only `call_tool`/`invoke`, serial blocking semantics; `ctx.spawn`/`wait`/`board`/`blob` are not bridged (the design §2.2 `blob` bridging did not land), and concurrent syscalls (ids already reserved in the protocol) are unsupported — no "foreground holding, background deep-thinking" from inside the sandbox.
5. **CodeScanner is auxiliary only.** Regex pattern scanning is ineffective against obfuscated code; the capability ceiling is written into the docs. The protective body is sandbox + permissions, not the scanner.
6. **Coarse accounting.** InProcess reports `mem_peak_mb` as 0; both sandbox backends fill `cpu_ms` with `wall_ms` (`logic/python_sandbox.py:367-368`); `merge_limits` three-level limit tightening is still `NotImplementedError` (`logic/limits.py:32-34`).
7. **The replay boundary survives orchestration.** An orchestrated run replays byte-identically only when its internal syscalls hit deterministic tools; tool side effects still execute against the real environment.
8. **Token savings are opportunistic.** Orchestration is off by default and requires explicit manifest declaration; for one or two calls, calling the tool directly is simpler (the schema description says so itself).

## 7. References

- Design docs: `docs/DESIGN.md` (§9 Logic Kernel; §3.4 coroutine-parallelism note); `docs/CODE-ORCHESTRATION.md` (orchestration sandbox design and §12 landing-deviation record)
- Contract: `agent_os/src/agent_os/api/v1/logic.py` (`LogicKernel`/`ExecRequest`/`ExecResult`/`SkillError`/`ORCHESTRATE_TOOL`/`SYSCALL_PROTOCOL_VERSION`)
- Implementation: `agent_os/src/agent_os/logic/inprocess.py`, `logic/python_sandbox.py`, `logic/docker_sandbox.py`, `logic/limits.py`; `kernel/logic_router.py`, `kernel/logic_context.py`, `kernel/runner.py` (`_run_code_frame`/`_syscall_dispatcher`/`_run_orchestration`); `tools/builtins.py` (`python_exec_tool`); `sidecars/builtins.py` (`CodeScanner`); `runtime/config.py`, `runtime/builder.py`
- Tests: `agent_os/tests/logic/test_orchestration.py`, `tests/logic/test_python_sandbox.py`, `tests/logic/test_docker_sandbox.py`, `tests/kernel/test_code_skills.py`
- Examples & config: `agent_os/examples/research_pipeline/skills.yaml`; `instance/agent-os.toml`
