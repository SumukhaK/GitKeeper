# GitKeeper — Tracker

The plan for v1 (the review relay) as small, execution-ready tasks. **Each task is a
GitHub issue whose body is the full worker prompt**: context, acceptance criteria, the
files the worker may change, how to verify, and the exact branch name.

**Status lives in GitHub, not here.** An issue is done when its PR merges with `Closes #N`.
This file only changes when the plan changes.

## How to run a task

1. Pick the lowest-numbered open issue whose dependencies are all closed
   ([open relay-v1 issues](https://github.com/SumukhaK/GitKeeper/issues?q=is%3Aopen+label%3Arelay-v1)).
2. Paste the "Start a task" prompt from [`worker-prompts.md`](worker-prompts.md) with the issue number into OpenCode or Copilot.
3. The worker opens one PR and stops. Review it with Claude (`prompts/review.md` after R1),
   send changes back with `prompts/changes.md`, merge when approved.

Tasks in the same lane can run in parallel with tasks in other lanes once their
dependencies are merged.

## Phase 0: Foundations

| Task | Issue | Depends on | Lane |
| --- | --- | --- | --- |
| R1 Adopt the Vibe-code-Architect core rules | [#54](https://github.com/SumukhaK/GitKeeper/issues/54) | — | setup |
| R2 Python CI: ruff and tests on every PR | [#55](https://github.com/SumukhaK/GitKeeper/issues/55) | R1 | setup |

## Phase 1: Pure relay logic (no I/O; can run in parallel)

| Task | Issue | Depends on | Lane |
| --- | --- | --- | --- |
| R3 Relay state machine | [#56](https://github.com/SumukhaK/GitKeeper/issues/56) | R1 | core |
| R4 Verdict parser | [#57](https://github.com/SumukhaK/GitKeeper/issues/57) | R1 | core |
| R5 Review packet builder | [#58](https://github.com/SumukhaK/GitKeeper/issues/58) | R1 | core |
| R6 Worker prompt builders | [#59](https://github.com/SumukhaK/GitKeeper/issues/59) | R1 | core |

## Phase 2: GitHub I/O

| Task | Issue | Depends on | Lane |
| --- | --- | --- | --- |
| R7 Make the GitHub client synchronous | [#60](https://github.com/SumukhaK/GitKeeper/issues/60) | R1 | github |
| R8 GitHub reads: PR details, issue, diff, annotations, next task | [#61](https://github.com/SumukhaK/GitKeeper/issues/61) | R7 | github |
| R9 GitHub writes: PR comment, squash merge | [#62](https://github.com/SumukhaK/GitKeeper/issues/62) | R7 | github |
| R10 Token from the gh CLI | [#63](https://github.com/SumukhaK/GitKeeper/issues/63) | R1 | github |

## Phase 3: State and loop

| Task | Issue | Depends on | Lane |
| --- | --- | --- | --- |
| R11 Relay store | [#64](https://github.com/SumukhaK/GitKeeper/issues/64) | R3 | core |
| R12 Relay poller | [#65](https://github.com/SumukhaK/GitKeeper/issues/65) | R3, R8, R11 | core |

## Phase 4: UI and a running app

| Task | Issue | Depends on | Lane |
| --- | --- | --- | --- |
| R13 Reviews view model | [#66](https://github.com/SumukhaK/GitKeeper/issues/66) | R4, R5, R6, R9, R11 | ui |
| R14 Reviews page and pet badge | [#67](https://github.com/SumukhaK/GitKeeper/issues/67) | R13 | ui |
| R15 Composition and `python -m gitkeeper` | [#68](https://github.com/SumukhaK/GitKeeper/issues/68) | R12, R14 | ui |
| R16 Remove remote config publication | [#69](https://github.com/SumukhaK/GitKeeper/issues/69) | R15 | cleanup |

**v1 is done when** `py -m gitkeeper` shows Setu-mobile's open PRs, a review round trip
(packet → verdict → worker prompt → new push → approve → merge) works end to end, and the
PRD's success criteria hold.

## Parked: v2 (automatic worker launch)

[#70](https://github.com/SumukhaK/GitKeeper/issues/70) collects the dormant v2 code, the
defects found in it, and the earlier bug issues
[#38](https://github.com/SumukhaK/GitKeeper/issues/38),
[#39](https://github.com/SumukhaK/GitKeeper/issues/39) and
[#40](https://github.com/SumukhaK/GitKeeper/issues/40).
[#37](https://github.com/SumukhaK/GitKeeper/issues/37) is superseded by R16. Don't work on
anything parked until v2 opens.

## Order at a glance

- **Critical path:** R1 → R7 → R8 → R12 → R15 → R16.
- **In parallel once R1 merges:** R2, R3, R4, R5, R6, R10, and R11 (after R3).
- **UI** starts when its inputs are in: R13 after R4, R5, R6, R9 and R11; then R14.
