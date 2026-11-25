# GitKeeper

A local-first companion that watches GitHub pull requests, queues repair work, and
shows what a single OpenCode worker is doing — without handing a public repository
any credentials.

GitKeeper is the private application half of a pilot against
[`SumukhaK/Setu-mobile`](https://github.com/SumukhaK/Setu-mobile). The public
repository gets only secret-free GitHub Actions config; every privileged action
happens on the owner's Windows machine.

> **Status: pre-alpha, not safe to launch workers yet.**
> The design contract, the desktop UI foundation, typed host-mode readiness, the
> local model ladder, the patch-scope gate, and the one-shot command approval
> broker are implemented and tested. The queue, GitHub reconciliation, worktree
> lifecycle, and OpenCode command mediation are **not** implemented, so worker
> execution stays disabled on purpose.

## What works today

| Area | State |
| --- | --- |
| Design spec and decisions | Complete — [`docs/superpowers/specs/`](docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md) |
| Handover tracker | Complete — [`docs/superpowers/plans/2026-11-20-gitkeeper-pilot-handover.md`](docs/superpowers/plans/2026-11-20-gitkeeper-pilot-handover.md) |
| Companion UI (fox pet, handscroll, queue/status/settings/telemetry pages) | Built against fakes, integration pending |
| Host-mode readiness policy | Complete (`src/gitkeeper/host_mode/readiness.py`) — typed `ACCEPTED_HOST_MODE` gate, launch blocked |
| Local model ladder and quota classification | Complete (`src/gitkeeper/policy/models.py`) |
| Patch-scope publication rule | Complete (`src/gitkeeper/policy/patch_scope.py`) |
| One-shot command approval broker | Complete (`src/gitkeeper/host_mode/command_broker.py`) — shows every request line, approves once, denies by default |
| Approval overlay in the handscroll | Built and tested against the real view model (`src/gitkeeper/ui/qml/CommandApprovalDialog.qml`) |
| Queue, leases, GitHub label reconciliation, worktrees | Not implemented |
| OpenCode command mediation (real executor) | Not implemented — the real launch gate |

## Quick start

Requirements: Python 3.10+, and for the desktop app PySide6 plus `pytest-qt`.

```powershell
py -m pip install -e ".[test]"
py -m pytest -q
```

For the desktop UI dependencies:

```powershell
py -m pip install -e ".[test,ui]"
```

`gitkeeper.ui.app:main` takes a queue service, so there is no console entry point
yet — the real queue service does not exist, and both it and the config publisher
are faked in `tests/fakes/`. Until then the window is exercised through the Qt
test suite: `py -m pytest tests/ui -q`.

## Commands

| Command | What it does |
| --- | --- |
| `py -m pytest -q` | Full suite (318 tests) |
| `py -m pytest tests/host_mode -q` | Typed host-mode readiness blockers and the command approval broker |
| `py -m pytest tests/policy -q` | Model ladder, patch scope, manifest capability |
| `py -m pytest tests/ui -q` | View models and QML accessibility checks |

There is no lint config in the repository yet; keep the existing style
(frozen dataclasses, `__post_init__` validation, module-level pure predicates).

## How it is meant to work

```mermaid
flowchart LR
    PR[Setu-mobile PR] -->|GitHub Actions labels| Q[Remote queue labels]
    Q -->|adaptive polling| C[GitKeeper companion]
    C -->|SQLite lease per repo/PR/head SHA| W[Local OpenCode worker]
    W -->|every shell request| A{Owner approves once?}
    A -->|no answer, mismatch, or deny| X[Not run]
    A -->|approved| K[One-shot command run]
    K -->|patch-scope gate| G{Allowed?}
    G -->|yes| P[Commit + fast-forward push]
    G -->|no| B[Preserve worktree, block, ask owner]
    P --> PR
```

- **GitHub is authoritative** for PR and queue state; local SQLite only records
  local execution and recovery.
- **Remote queue state is a label**, not a database: `gitkeeper/worker-queued`,
  `gitkeeper/worker-running`, `gitkeeper/worker-blocked`.
- **Claiming** is a SQLite lease keyed by repository, PR number, and head SHA, so
  a new push makes the old claim stale instead of racing it.
- **Budgeting**: at most three automatic invocations per PR across head-SHA
  changes. On the cap the PR is blocked and only an explicit owner retry resumes.
- **Trust boundary**: PR code is untrusted. Public CI is secret-free and
  read-only on `pull_request`; only PRs authored by the owner are worker
  eligible. GitKeeper never accepts credentials from a repository config PR.

### The four code-enforced gates

`gitkeeper.host_mode.readiness` — the launch gate:

- `ACCEPTED_HOST_MODE` is a risk decision, not isolation, and never yields
  readiness on its own.
- Every refusal is named: allowlist, privacy, fork head, author, open/draft,
  merged config, repair reason, stale check SHA, attempt budget, model ladder,
  CLI version, and unproven command broker.
- Readiness is computed from explicit GitHub and local facts; there is no
  boolean bypass, and historical isolation reports cannot authorize a launch.

`gitkeeper.host_mode.command_broker` — the approval gate:

- One immutable request: executable, argv **or** a complete shell script,
  environment variable *names* only, absolute working directory, repository, PR,
  starting SHA, and a single-line purpose. There is no field for a secret, so the
  broker cannot leak or persist one.
- The digest is a SHA-256 over the canonical JSON of that request; any change to
  any field produces a different digest and voids the approval.
- A decision is one-use, unforgeable, time-limited (300 s), and only `approve`
  approves. A decision from another broker, a replay, a changed request, a moved
  PR head, or a fork head all refuse to execute.
- Remote facts are re-checked before presenting, after approval, and again at
  execution, so the owner is never asked to bless stale state.
- Execution itself is deliberately unimplemented: the only executor is
  `execution_disabled`, which raises. An approval cannot become a process run
  until OpenCode mediation is proven.

`gitkeeper.policy.models` — the model ladder:

- Ordered, exact `opencode/` model IDs, default
  `opencode/deepseek-v4.1-flash-free`, then `glm-5.3`, `glm-5.3-flash`,
  `hy4-flash-free`, `deepseek-v4-pro`.
- Stored locally at `%LOCALAPPDATA%\GitKeeper\models.json`, never in a repository.
- Quota, rate-limit, or `429` output is classified `model-unavailable`: the
  worktree is preserved, the PR blocked, and the *next* entry suggested. Only the
  owner switches; the worker never auto-advances, and the invocation budget is
  untouched.

`gitkeeper.policy.patch_scope` — the publication gate:

- Checks in reporting order: worktree identity, structure, paths, manifest
  capability, intent, then publication order.
- Limits of 10 files, 400 changed lines, and 150 lines per file are code
  constants, so a repository config PR cannot widen its own blast radius.
- `AndroidManifest.xml` edits may only change values inside existing elements;
  permissions, features, components, and intent filters are frozen.
- Any violation keeps the worktree, blocks the PR, and waits for an explicit
  retry. Publication is fast-forward only and re-checks the remote head first.

## Repository layout

```
src/gitkeeper/
  host_mode/   typed ACCEPTED_HOST_MODE readiness blockers + command approval broker
  isolation/   historical Windows-probe evidence, not a launch gate
  policy/      model ladder, quota classification, patch-scope rule
  ui/          view models, services, and QML (fox pet + handscroll panel)
tests/         host_mode, policy, isolation, and UI suites plus fakes
docs/          design spec, plans, and the handover tracker
prototypes/    throwaway UI experiments, not shipped
brainstorm.md  original design notes
```

## Where to start as the next contributor

Read, in order:

1. [`brainstorm.md`](brainstorm.md) — the original problem and shape.
2. The [design spec](docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md) — the approved contract.
3. The [handover tracker](docs/superpowers/plans/2026-11-20-gitkeeper-pilot-handover.md) — what is complete, what is pending, and the exact next steps.

The next slice is OpenCode command mediation: prove that CLI 1.19.6 routes every
shell request through the one-shot companion approval that Task 2 now provides and
cannot bypass it. Until that is proven, worker execution must remain disabled —
`command_broker.execution_disabled` refuses every launch on purpose.
