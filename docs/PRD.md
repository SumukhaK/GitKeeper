# GitKeeper — Product Requirements

**Status:** Approved direction, 2026-11-26. It replaces the original pilot design spec (removed;
see git history). Technical design: [`TECHNICAL.md`](TECHNICAL.md). Tasks: [`TRACKER.md`](TRACKER.md).

## Problem

I build projects with one reviewer (Claude) and cheaper worker agents (OpenCode, Copilot).
Every pull request goes through the same loop:

> worker opens PR → I copy it to Claude → Claude reviews → I copy the requested changes back
> to the worker → worker updates the PR → repeat until approved → merge → Claude updates the
> tracker, docs and memory → Claude writes the next task prompt.

The reviewing is valuable. The relaying and the bookkeeping are not: they cost my attention
on every round and spend Claude's limited usage on clerical work.

## Goal

GitKeeper runs the relay so that, for each review round, my only jobs are to read and to decide.
The judgement stays with the reviewer and with me.

## Who it is for

Me, working alone on my own repositories, on one Windows machine. There are no collaborators.

## The loop GitKeeper runs

1. **PR raised.** A worker opens a PR from a `feature/`, `bugfix/` or `upkeep/` branch that links
   the issue it implements. GitKeeper wakes up for that PR.
2. **CI runs.** If CI fails, GitKeeper hands the failure to the worker to fix (this is the
   *secondary* trigger, capped at 3 attempts). If CI passes, the PR is ready for review.
3. **Review needed.** The desktop pet tells me a review is waiting. One click copies a
   ready-made **review packet**: the PR, its linked issue and acceptance criteria, the diff,
   the CI result, and my standard review instructions.
4. **I review with Claude.** I paste the packet into Claude myself. GitKeeper never calls
   Claude, so Claude's usage stays under my control.
5. **I bring the verdict back.** I paste Claude's reply into GitKeeper. It reads the verdict:
   *approve* or *changes requested*.
6. **Changes requested.** GitKeeper records the review on the PR as a comment and prepares the
   change request for the worker on the same branch. The worker updates the PR and the loop
   returns to step 2. After 3 review rounds without approval, GitKeeper stops and asks me what to do.
7. **Approved.** GitKeeper shows "ready to merge". I click Merge. GitKeeper never merges on its own.
8. **After merge.** The linked issue closes. GitKeeper shows the next open issue with a
   ready-to-copy worker prompt, then goes back to sleep until the next PR.

## How work reaches the worker

- **First version:** GitKeeper prepares the worker prompt and tells me. I paste it into OpenCode
  or Copilot. This works with any worker and any model.
- **Later:** GitKeeper launches OpenCode itself, behind the safety gates already built
  (per-command approval, patch-scope check, attempt cap).

## What I no longer do by hand

- Copy reviews between the reviewer and the worker. GitKeeper prepares both directions.
- Update trackers or status docs after a merge. Status comes from GitHub: issues are the
  tracker, PRs and git history are the record.
- Write the next task prompt. It comes from the next open issue.

Documentation changes only when a decision changes, and always inside the PR that changes it.

## Success criteria

- Each review round costs me at most two pastes and one click.
- A review packet is ready within one minute of CI passing.
- No hand-written status, tracker or "record the merge" updates.
- For every PR I can see the number of review rounds, the CI attempts, and whether it was
  later reverted.

## Not doing

- GitKeeper calling Claude or any model for review. A different reviewer (another model,
  or Claude Code running headless) can be plugged in later; the packet format stays the same.
- Merging without my click.
- Writing docs, trackers or memory updates with AI after a merge.
- Supporting other people, other machines, or repositories I don't own.

## Guardrails that stay

- Only my own PRs, from branches in the same repository, are eligible.
- Workers never merge, approve, force-push, or push to `main`.
- At most 3 automatic fix attempts for CI failures and 3 review rounds per PR before GitKeeper
  stops and asks me.
- When automatic worker launch arrives, every shell command still needs my approval and every
  patch still passes the scope check.

## Open questions

1. The exact verdict format I ask Claude to end its review with, so GitKeeper can read it reliably.
2. When to switch the worker hand-off from "copy the prompt" to "launch automatically".
