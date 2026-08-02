# Agent OS Technical Whitepaper (Chapter Edition)

> Version: v2.0 (2026-08)
> Contents: executive summary (00) + 15 deep-dive chapters (01-15), Chinese & English editions
> Chapter format (seven sections): Overview / Motivation & Background (why) /
>   Problem Statement (what it solves) / Design & Mechanism (how) /
>   Effects & Verification (results) / Limitations & Boundaries / References
> Discipline: every claim is traceable to source code or documents (path or
>   path:line); "implemented" vs "designed but not implemented" is always
>   distinguished; where documents and code disagree, code wins and the
>   discrepancy is annotated (see Errata below)

## Reading Guide

- For the big picture in ~15 minutes: read the [00 Executive Summary](en/00-executive-summary.md);
- For a specific system: jump to its chapter below — chapters are self-contained;
- For a security review: start with 09 (escalation) → 10 (data authZ) → 11 (standards & gate) → 03 (tool arbitration);
- For an engineering-quality review: start with 06 (determinism) → 01 (microkernel) → 12 (debugger) → 15 (hosts);
- 中文版:[../zh/](../zh/) 目录下同名章节(平行版本,非逐句直译)。

## Table of Contents

| Ch. | Topic | Status |
|---|---|---|
| [00](en/00-executive-summary.md) | Executive summary: prototype, axioms, architecture, problem-mechanism map | — |
| [01](en/01-microkernel.md) | Microkernel & execution model: F/P/I criterion, agent loop, frame stack, safe-points, recovery circuit-breaking | implemented |
| [02](en/02-skills.md) | Skills: manifest contract, loading pipeline, pseudo-tools & the inline purity gate | implemented |
| [03](en/03-tools.md) | Tools: dispatch pipeline, three-way permission intersection, side_effect declarations, path sandbox | implemented |
| [04](en/04-logic-kernel.md) | Logic Kernel & orchestration sandbox: trust routing, syscall channel, no privilege elevation | implemented |
| [05](en/05-context.md) | Context: assembly, compression (rolling-window), prefix-cache stability | implemented (baseline) |
| [06](en/06-determinism.md) | Signals, telemetry & determinism engineering: 31-signal catalog, WAL, checkpoint/resume/replay/diff | implemented |
| [07](en/07-sidecars.md) | Sidecars: signal-driven supervision, verdict arbitration matrix, dual budget layers | partial (HumanApproval skeleton) |
| [08](en/08-supervisor.md) | Supervisor: ruling router, suspend-answer-resume loop, three host channels | implemented |
| [09](en/09-escalation.md) | Escalation: three trust tiers, the gate, grants, clean-context invariant | implemented (E1/E2) |
| [10](en/10-data-authz.md) | Data-layer authN+Z: Principal, data domains, dual-gate dispatch, default deny | partial (D1) |
| [11](en/11-tier-standards.md) | Tiered production standards & the admission gate: decision tree, per-tier standards, five gates, promote defenses | implemented |
| [12](en/12-debugger.md) | Debugger: GDB semantics, four breakpoint kinds, stepping/pause, intervention & time travel | implemented |
| [13](en/13-themes.md) | Theme system: three-layer contract, six themes, two mascot instances | implemented (motion layer designed) |
| [14](en/14-skill-lab.md) | Skill Lab: draft store, five-gate pipeline, the assistant (can edit, cannot publish), test panel | implemented |
| [15](en/15-hosts.md) | Hosts: thin CLI/Web hosts, four-artifact contract, RunRecord, thread model | implemented |

## Errata (Document-Code Discrepancies Found While Writing)

Chapter authors resolved every discrepancy in favor of the code; notable items
below — the full list lives in each chapter's §6 and endnotes.

- **Pseudo-tool prefix**: DESIGN.md §3.3 writes `skill__<name>`; the code emits
  `skill.<name>` (the runner accepts both). Chapters follow the code (ch. 01/02).
- **Three inaccuracies in the executive summary (fixed)**: the debugger has four
  breakpoint kinds (`skill.invoke` was missing); `lab validate` exit codes are
  pass/warn=0 and fail=2 (no 4); the `[ESCALATED:...]` return tag is designed
  but not implemented (ch. 09/12/14; ch. 00 corrected).
- **No semver solving**: DESIGN.md §6.1 promises semver constraint solving; the
  loader checks dependency existence only (ch. 02).
- **Sidecar arbitration surface is smaller than designed**: `pre:skill.invoke`,
  `pre:llm.request`, `pre:compress` emit but are not arbitrated; the
  `budget.warning/exceeded` signals have no emitter (ch. 07).
- **D1 deviations in data authZ**: unconfigured domains are not intercepted
  (the design said confidential; D2 will restore); `system.shell.exec` is
  outside the data gate's coverage (ch. 10).
- **Supervisor suspension is not a state-machine transition**: the run stays
  RUNNING; there is no PAUSED transition (ch. 08, per runner.py:607-610).
- **RUNNERS.md is stale in places**: "run as asyncio task" vs. the actual
  thread-per-run model; `stderr.log` is never written; `trace --kind`,
  `resume <run_id>`, `--sandbox` are unimplemented (ch. 15).
- **Docker backend does not support orchestration**: `dispatch_fn` is ignored;
  orchestration belongs to the subprocess backend only (ch. 04).
- **Stale docstrings in gate.py / test_gate.py**: "G4/G5 skip placeholders" is
  an L2-era leftover; both gates are implemented (ch. 11).

> These discrepancies are themselves evidence: every chapter of this
> whitepaper was rewritten against the source code as the ultimate ground
> truth. The list above doubles as a task pool for future document repairs.
