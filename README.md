# GitKeeper

**A desktop companion that runs the pull-request review relay between your AI reviewer and
your AI workers, so you only read and decide.**

I build with one strong model as reviewer (Claude) and cheaper agents as workers (OpenCode,
Copilot). Reviewing is valuable; carrying text back and forth between them on every round
is not. GitKeeper does the carrying. It's a small fox that lives on a Windows desktop,
watches your PRs, and tells you when it's your turn.

<p align="center">
  <img src="https://raw.githubusercontent.com/SumukhaK/Vibe-code-Architect/main/assets/the-loop.svg" alt="The loop: issue, worker, PR, CI, review packet, reviewer, owner merges" width="820">
</p>

GitKeeper is the companion to
[Vibe-code-Architect](https://github.com/SumukhaK/Vibe-code-Architect), the project template
that defines the rules, prompts and verdict format this relay depends on.

> **Status: pre-alpha.** The scope was reset on 2026-11-26 around the review relay
> ([PRD](docs/PRD.md)). v1 is being built from small tasks in the [tracker](docs/TRACKER.md).
> There is no runnable app yet; `py -m gitkeeper` arrives with task R15.

## How a review round works

1. A worker opens a PR that links its issue. GitKeeper notices it.
2. CI runs. If it fails, GitKeeper prepares a fix prompt with the failing checks and their
   annotations (up to 3 attempts).
3. CI passes: the fox shows a badge. One click copies a **review packet**: the issue, the
   PR, the CI results and the diff, wrapped in your review prompt.
4. You paste it into Claude and paste the reply back. GitKeeper reads the `VERDICT:` line
   and records the review on the PR.
5. **Changes requested:** GitKeeper hands you a ready worker prompt for the same branch.
   The worker pushes, and the round starts again (up to 3 rounds).
6. **Approved:** "ready to merge". **You** click Merge; it's pinned to the reviewed commit.
7. After the merge, GitKeeper shows the next open task with its prompt, and goes quiet.

## What stays human

- **Judgement.** GitKeeper never calls a model. You choose when your reviewer's usage is spent.
- **The merge.** Nothing merges without your click, and never a commit that wasn't reviewed.
- **Your PRs only.** Only PRs you authored, from branches in the same repo, in repos you list.

## Documentation

| Doc | What's in it |
| --- | --- |
| [`docs/PRD.md`](docs/PRD.md) | What GitKeeper does and doesn't do (one page, non-technical) |
| [`docs/TECHNICAL.md`](docs/TECHNICAL.md) | Architecture, state machine, packet and verdict formats, GitHub API, storage, and the dormant v2 |
| [`docs/TRACKER.md`](docs/TRACKER.md) | The v1 tasks in order; each links an issue with a ready worker prompt |

## Development

Requires Python 3.10+ and, for the desktop UI, PySide6. Developed on Windows.

```powershell
py -m pip install -e ".[test,ui]"
py -m pytest -q
```

Contributing a task: pick the next issue from the [tracker](docs/TRACKER.md), branch
exactly as the issue says, write the failing tests first, open one PR with verification,
and stop.

## Repository layout

```
src/gitkeeper/
  relay/      v1: state machine, verdicts, packets, prompts, store, poller   (being built)
  queue/      GitHub REST client (reused by v1) and the dormant v2 queue store
  ui/         fox pet, handscroll panel, view models (QML + PySide6)
  host_mode/  v2, dormant: launch readiness and the command-approval broker
  worker/     v2, dormant: worktrees, OpenCode executor, git publication
  policy/     v2, dormant: model ladder, patch-scope rule
tests/        mirrors src/
docs/         PRD, technical design, tracker
```

The v2 code (automatic worker launch) is tested but not wired into the app; it is parked in
[#70](https://github.com/SumukhaK/GitKeeper/issues/70) with the defects to fix before it
is enabled.
