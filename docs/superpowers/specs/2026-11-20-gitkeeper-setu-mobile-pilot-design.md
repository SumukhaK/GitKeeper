# GitKeeper Setu-mobile Pilot Design

**Status:** Design specification
**Date:** 2026-11-20
**Target repository:** `SumukhaK/Setu-mobile` (public Kotlin/Android repository)
**GitKeeper implementation repository:** `SumukhaK/GitKeeper` (private)

## Purpose and scope

This specification defines a single-repository pilot that lets GitKeeper track pull requests in Setu-mobile, route deterministic preflight failures and user-transferred review feedback to a local OpenCode worker, and expose its state through a Windows desktop companion and original desktop pet. The pilot includes GitHub Actions integration, a queued local worker, the pet and handscroll panel, and configuration UI.

GitKeeper application, companion, and worker code belong only in the private GitKeeper repository. Setu-mobile receives only the GitHub Actions integration and its configuration. This document is the sole design source of truth for the Setu-mobile pilot; it does not authorize implementation.

The pilot does not use a Kaggle-hosted model, AMD Token Lab, multiple worker CLIs, automatic model failover, automated Claude app access/review/approval/merge, cross-repository avatars, or automatic analytics-driven prompt changes. A user may manually transfer Claude's `CHANGES_REQUESTED` feedback through the companion to enqueue worker repair; Claude and the user retain final architectural judgment.

## Goals

- Detect Setu-mobile pull-request lifecycle events through GitHub Actions.
- Keep work queued while the local companion or worker is offline, and recover it safely when the companion returns.
- Automatically enqueue OpenCode repair only for eligible pull requests authored by `SumukhaK`: after a deterministic check failure or after the user submits Claude review feedback. Passing checks alone make a PR ready and do not start a worker.
- Limit automatic invocations to three per open/reopen cycle across head-SHA changes; closing and reopening starts a fresh budget while preserving historical attempts.
- Keep PR code untrusted: CI has no secrets and read-only permissions; privileged label changes never check out or execute PR code.
- Make queue and worker status understandable and controllable from a Windows desktop pet and handscroll panel.
- Keep GitHub authoritative for remote PR/queue state and local SQLite authoritative for worker execution and recovery.
- Publish configuration through a validated config pull request, and let Actions consume only configuration merged to the default branch.

## Non-goals

- Implementing the companion, worker, Actions workflow, or repository configuration in this task.
- Running the worker inside GitHub Actions.
- Running models locally or maintaining a worker fallback chain.
- Automating PR approval, merging, release, or final architectural review.
- Giving GitKeeper automated access to Claude or having it submit, retrieve, or interpret Claude reviews.
- Automatically changing prompts, rules, or product behavior based on telemetry.
- Supporting repositories other than Setu-mobile in the first pilot.

## System boundaries

```text
Setu-mobile pull request
       |
       v
GitHub Actions (pull_request; secret-free CI)
       |
       +--> trusted metadata/state workflow --> GitHub PR labels
       |                                         (remote queue status)
       v
GitHub API polling <------------------------ Windows companion
                                                   ^    |
                                user transfers Claude  |
                                CHANGES_REQUESTED -------+
                                                   |
                                          local SQLite queue/lease
                                                   |
                                          dedicated PR worktree
                                                   |
                                             OpenCode CLI
```

GitHub is the event source and authority for remote PR identity, head SHA, and queue labels. The companion polls GitHub outbound; no inbound listener or public companion endpoint is required. The companion is optional for GitHub event capture and CI, but must be online for local execution, interactive controls, and local progress display. Work remains queued remotely while it is offline.

### Components and responsibilities

| Component | Responsibility |
|---|---|
| Setu-mobile Actions integration | Run deterministic Android CI on `pull_request`; derive eligible PR metadata; queue repair for explicit deterministic-check failures through a trusted, non-checkout state path; mark infrastructure/unknown outcomes blocked. |
| GitKeeper companion | Poll GitHub with adaptive backoff; reconcile remote PR/label state with local SQLite; accept user-transferred Claude `CHANGES_REQUESTED` feedback; validate settings; create config PRs; display status; manage local worker lifecycle. |
| Local queue and lease store | Persist worker claims, invocation counters, execution state, cancellation/recovery metadata, and operational telemetry outside both repositories. |
| OpenCode worker | Make scoped changes in an isolated worktree for a claimed PR revision. The configured OpenCode model ID is user-selectable; this design does not guess an ID. |
| Desktop pet and panel | Show aggregate activity and provide PR/status, queue, settings, and local telemetry views and controls. |

## GitHub Actions and trust boundary

### Pull-request CI

- CI uses `pull_request`, never `pull_request_target`.
- GitHub Actions is the sole test gate and runs `./gradlew testDebugUnitTest detekt lint` on PR `opened` and `synchronize`, and again for every companion push to the PR branch. The companion and worker do not run these tests locally as a prerequisite to publishing.
- CI has no repository secrets and only read permissions. Workflow jobs must not use a token to write PR/repository data; GitHub's built-in check-run reporting is expected.
- CI may build and test untrusted PR code, but only in the ordinary isolated GitHub Actions job with no secrets or write-capable token.
- The workflow reports completion and the tested head SHA so the companion can distinguish a result for the current revision from stale CI. CI results have three outcomes for GitKeeper: all required checks pass (ready, no worker); an explicitly identified deterministic test/lint check fails (enqueue repair for that SHA); or the result is infrastructure-related, cancelled, missing, or unknown (blocked for human action). A workflow/job failure is not automatically treated as a code failure.

### Privileged state and labels

GitHub labels `gitkeeper/worker-queued`, `gitkeeper/worker-running`, and `gitkeeper/worker-blocked` represent remote queue state. Use a separate trusted state workflow triggered after the `pull_request` CI workflow completes (`workflow_run`); keep that state workflow on the default branch. It reads only event/API metadata and must not check out PR code, download or consume PR-produced artifacts, or execute PR-controlled scripts. It validates the source workflow, repository, PR association, actor, and head SHA before any write. It may add the queued label only when an allowlisted deterministic check explicitly fails for the current SHA; it marks infrastructure, cancellation, missing, or unknown outcomes blocked instead. If all required checks pass, the PR is ready and receives no worker queue label.

The state workflow receives only the minimum permission needed to add/remove PR labels (`issues: write`); it has no contents write permission and no long-lived personal token. Do not use `pull_request_target`. State changes must be idempotent and scoped to the validated PR in `SumukhaK/Setu-mobile`. Its failure classifier accepts only known check identities and explicit deterministic-check outcomes from the run API; setup/runner errors, job/workflow failures outside those checks, timeouts, cancellations, absent results, or unrecognized conclusions are blocked, never presumed to be code failures. The privileged state workflow itself must remain on the protected default branch.

After claiming or blocking work, the companion's control plane updates the running/blocked label and removes obsolete queue labels through the user's authenticated GitHub connection. This control plane does not execute PR code; worker subprocesses receive no GitHub credentials and cannot write labels. The companion requests only the repository access needed for PR-label and config-PR operations.

The PR author is eligible for automatic worker execution only when the GitHub login is exactly `SumukhaK` and the PR head branch is in `SumukhaK/Setu-mobile` itself. Other authors and all fork PRs receive secret-free CI only and must never be auto-enqueued for local worker execution. Label updates cannot grant worker eligibility by themselves; the companion independently checks author, head repository, PR status, current head SHA, configured enablement, and a valid repair reason before claiming work.

CI results for any prior SHA are stale and cannot authorize a run. Passing deterministic checks alone never starts a worker; a deterministic failure or user-submitted review feedback is required. Ineligible PRs never start a worker.

### Local worker execution safety gate

The worker can run project-defined tools and tests on a developer machine; a PR author's identity does not make repository contents safe. Every invocation therefore uses a dedicated PR worktree, never the companion's installation directory, the user's unrelated checkout, or either repository's GitKeeper source checkout. The worker must not inherit GitHub credentials, desktop sign-in tokens, or unrelated environment secrets.

**Implementation gate:** build a Windows OS-level isolation prototype for executing PR-controlled build/test commands and verify its isolation properties before enabling any worker execution. Until that prototype passes, neither automatic queue processing nor user-triggered worker execution may launch OpenCode. A dedicated worktree alone is not a security sandbox. Prompt instructions and the PR-author allowlist are never security controls and do not satisfy this gate.

## Event and state model

### PR lifecycle inputs

The Actions integration and companion reconcile the following events/state:

- `opened` or `ready_for_review`: evaluate repository, author, draft status, and merged config, then classify deterministic checks for the exact head SHA. Passing checks make the PR ready without queuing a worker; an explicit deterministic check failure queues repair; infrastructure, cancelled, missing, or unknown results become blocked for human action.
- `synchronize` (head SHA changes): cancel the prior SHA's active invocation, mark it stale, and classify checks for the new SHA. Queue only for a deterministic failure on that SHA, or after checks pass for feedback the user explicitly re-associates with that SHA.
- `reopened`: start a fresh repair cycle for the current revision, resetting that cycle's automatic invocation budget to zero while preserving all prior cycle and manual-retry history. Re-evaluate eligibility and checks.
- `review feedback submitted`: after required deterministic checks pass for the current SHA, the user selects the PR/current head SHA and manually transfers Claude's `CHANGES_REQUESTED` feedback through the companion. This creates a repair queue item for that exact SHA when the PR is otherwise eligible. Feedback is not fetched from Claude or accepted from an automated Claude integration.
- `closed` or `merged`: cancel active work, release its lease, and remove all worker-state labels. No new work is started for a closed PR.
- Companion restart or missed event: polling reconciliation compares GitHub's current PR state/labels/head SHA with local records and repairs stale claims and labels.

PR close cancels work and ends the current repair cycle. Reopening starts a new cycle with a fresh automatic budget; it does not resume a canceled process, and prior attempt history remains visible. A changed head SHA cancels old work and invalidates feedback associated with the previous SHA until the user re-submits or explicitly re-associates it. A late worker completion for a closed PR or superseded SHA cannot update the PR or replace current queue state.

### Remote states

| Remote label | Meaning | Entry and exit |
|---|---|---|
| `gitkeeper/worker-queued` | Eligible open PR awaiting repair for the current SHA. The reason is a deterministic check failure or explicitly submitted Claude feedback after checks pass. | Added only after the current-SHA repair reason is validated. Passing checks alone do not add it. Removed when claimed, canceled, no longer eligible, or blocked. |
| `gitkeeper/worker-running` | A local companion holds a valid lease and is executing the current SHA. | Added when a claim is acquired. Removed on completion, cancellation, lease recovery, or block. |
| `gitkeeper/worker-blocked` | Work requires human action, such as infrastructure/cancelled/unknown CI outcome, invocation-cap exhaustion, or OpenCode failure. | Added on escalation. Removed only after the underlying issue is resolved and work is explicitly retried/requeued, or the PR becomes ineligible/closed. |

At most one of these labels may be present on a PR. No worker label means no repair is pending (for example, checks passed and the PR is ready for human review). GitHub labels are a user-visible projection of state; SQLite lease ownership and a fresh GitHub head-SHA check govern whether a local process may continue. If GitHub cannot be reached, the worker does not claim or continue a new operation based on stale remote state; active processes pause at safe checkpoints until reconciliation is possible.

### Automatic invocation budget

Count one invocation when the companion launches OpenCode for a PR. The automatic budget is three invocations per open/reopen repair cycle, accumulated across head-SHA changes within that cycle. Closing and reopening starts a fresh automatic budget but preserves all prior attempt history. A launch counts even if OpenCode exits immediately. Both CI-triggered repairs and user-submitted Claude-feedback repairs count against the cycle's automatic budget.

When the budget is exhausted, the PR becomes blocked and requires a deliberate user action. An explicit retry authorizes one additional invocation; it does not reset or replenish the automatic budget. If that retry also fails or escalates, the PR is blocked again and requires another explicit retry. Every manual retry is recorded separately from automatic attempts.

## Queue claiming, leases, and stale work

- The companion polls with adaptive backoff: more frequently while a PR is queued/running and less frequently when idle. Polling is outbound and uses the user's authenticated GitHub connection.
- Before claiming, the companion confirms the PR is open, eligible, not a draft, enabled in merged configuration, and the queue reason is valid for the exact current SHA: an explicit deterministic-check failure, or user-submitted Claude feedback after checks pass. A pass-only result is not a queue reason. Infrastructure, cancellation, missing, or unknown CI outcomes remain blocked until human action resolves them. The cycle must be under the automatic invocation cap (or explicitly retried).
- A local SQLite transaction atomically creates/acquires a lease keyed by `(repository, PR number, head SHA)`. A uniqueness constraint prevents two local worker processes from claiming the same revision. The lease records owner/process identity, start time, heartbeat/expiry, attempt, and cancellation state.
- The companion adds the running label only after acquiring the lease and rechecking the remote head SHA. A lease is renewed by heartbeats during execution. If a heartbeat expires after a crash, startup reconciliation stops/reaps the abandoned process where possible, preserves its worktree and logs, and requires a fresh eligibility/head check before resuming or retrying.
- A new SHA or PR closure marks the prior lease canceled. The worker receives a cancellation signal and must stop at a safe boundary. Its worktree is retained for diagnosis/recovery, but it cannot write results or labels for the stale revision.
- The single pilot companion is the only local claimer. If multiple companion instances are later allowed, the SQLite uniqueness/lease transaction must still guarantee one claimant; labels alone are not a lock.
- Remote transitions are idempotent and retried after transient API errors. SQLite transaction failures are surfaced and do not produce success-shaped remote state.

## Worker behavior

- OpenCode CLI is the only pilot worker. The user selects an exact OpenCode model ID in settings; no ID is hard-coded or inferred from the OpenCode Go subscription.
- Use the user's OpenCode Go subscription through the user's OpenCode installation. Do not store subscription credentials in GitKeeper configuration, SQLite telemetry, PR labels, or the Setu-mobile repository.
- Worker instructions include the user's branching, test-driven-development, and framework-sanity expectations and, when provided, the user's Claude `CHANGES_REQUESTED` feedback and/or deterministic failure details. Passing checks alone do not invoke the worker. Prompt instructions are behavioral guidance only, never security enforcement; deterministic checks and OS-level isolation are separate controls.
- Claude feedback is submitted by the user in the companion and bound to the selected PR head SHA. GitKeeper has no automated Claude app/API access and does not approve, merge, or make final architectural judgments. The user returns worker changes to Claude for review through their existing manual process.
- The worker operates only in its dedicated PR worktree and revision. It must not merge or approve PRs, publish configuration changes, or modify GitKeeper itself as part of the Setu-mobile task.
- Before publishing, the companion validates the patch structure and scope against the assigned repair, then queries GitHub to confirm the PR's remote head SHA still equals the SHA given to the worker. It does not run tests locally.
- Only after both validations pass, the companion/control plane—not OpenCode—stages and commits the worker changes, then pushes the commit to that PR's existing head branch. OpenCode receives no GitHub credentials. Pushes must be ordinary fast-forward pushes; never force-push and never push to `main`.
- If patch structure/scope validation fails or the remote head SHA changed, do not commit or push; preserve the worktree and report a blocked/stale result for user action and reconciliation.
- After a successful push, GitHub Actions runs `./gradlew testDebugUnitTest detekt lint` against the new PR head SHA. Passing checks leave the PR ready with no worker queued. Any deterministic CI check failure after the push enqueues another repair for that SHA if the cycle budget remains; infrastructure, cancelled, or unknown outcomes remain blocked for human action.
- A successful invocation records its local outcome and does not trigger automatic merge or final architectural approval.
- If OpenCode is unavailable, rate-limited, or crashes, preserve the worktree and execution record, transition to blocked, notify the user, and wait for explicit retry. There is no automatic model fallback in this pilot.
- The companion supports pause/resume and cancel-current-run controls. Pausing prevents new claims and pauses/requests a safe pause of active work; cancel signals the active worker and records the outcome. A retry is always explicit after escalation.

## Configuration and publication

- Configuration consumed by Actions resides in Setu-mobile and is versioned with the Actions integration. Actions reads only the default-branch (merged) configuration; config in an open PR never affects CI authorization or worker eligibility.
- Companion settings include worker enablement, selected OpenCode model ID, polling behavior, and presentation/accessibility preferences. Credentials are managed by GitHub/OpenCode authentication providers, not configuration values.
- A settings change is schema-validated locally before publication. Invalid values are rejected with a field-specific explanation and cannot create a PR.
- Publishing settings creates or updates a config branch and pull request in Setu-mobile using the user's GitHub identity and the minimum available repository permissions. It does not push directly to the default branch. The companion shows PR creation, review, merge, and rejection status.
- The Actions integration consumes changed configuration only after it is merged to the default branch. The validated schema must reject unknown execution/permission controls and secrets; no config field can grant itself elevated Actions permissions or bypass author/CI/isolation gates.

## Desktop companion and pet

The companion is a Python application using PySide6/QML and is optional for GitHub event capture. It may run on Windows to show progress and publish validated configuration. It uses outbound/adaptive GitHub polling and does not host a public inbound service.

The desktop pet is an original nine-tailed fox-spirit design, not a Naruto/Kurama replica. Clicking it opens a custom Japanese handscroll-inspired panel with four areas:

1. **PR/status:** repository, PR, current head SHA, eligibility, CI result, remote state, active worker state, and last update; allow the user to paste/transfer Claude's `CHANGES_REQUESTED` feedback, select a PR/SHA, review its association, and explicitly submit it as a repair request.
2. **Queue:** queued/running/blocked items, repair reason (deterministic check or user-transferred review feedback), attempt count, and actionable error/escalation reason.
3. **Settings:** worker enabled state, selectable OpenCode model ID, polling preferences, motion preferences, and config-PR publication status.
4. **Local telemetry:** retained operational history and per-run outcome; no source content.

Feedback for an outdated SHA cannot be applied without explicit re-association. Controls are pause/resume, cancel current run, and explicit retry after escalation. Disabled/reduced motion must be supported throughout the pet and panel; motion preference changes must not affect queue or worker behavior. Status must remain available as text and not depend on animation or color alone.

## Local persistence, recovery, and telemetry

- Store queue/lease, worker lifecycle, configuration-publication status, and telemetry metadata in SQLite outside both GitKeeper and Setu-mobile repositories. Store worker worktrees and logs in separate managed local state paths, also outside the two repositories.
- Local SQLite is the recovery record for execution, not the authority for whether a PR remains open or which SHA is current. Reconcile from GitHub before resuming work after restart.
- Retain telemetry for 90 days by default, then delete/expire it automatically. The retention setting may be shortened by the user; changing it must not silently lengthen retention beyond 90 days in this pilot.
- Telemetry contains operational metadata only: timestamps/durations, PR/revision identifiers, state transitions, attempt count, outcome, selected model ID, and categorized errors.
- Do not store source code, diffs, prompts, credentials, screenshots, or full OpenCode input/output in telemetry. Worker logs needed for crash recovery must be access-restricted, kept separate from telemetry, and excluded from config PRs and GitHub labels.
- User-transferred Claude feedback is work input, not telemetry. Keep it only in the local work item as needed for execution/recovery, access-restrict it, never copy it to GitHub labels/config PRs or telemetry, and delete it with the work item when no longer needed.
- No analytics-driven self-modification or automatic prompt/rule updates. A user may inspect operational outcomes, but any future process change is a separately reviewed change.

## Error handling and user feedback

| Failure | Required behavior |
|---|---|
| GitHub API/network unavailable | Keep local data; do not make new claims or authoritative state transitions; show stale/offline status; retry with backoff. |
| Explicit deterministic check failure on current SHA | Queue automatic local repair for that exact SHA if the PR is eligible and under its cycle budget. |
| All deterministic checks pass on current SHA | Mark the PR ready with no worker queued. A user may separately submit Claude `CHANGES_REQUESTED` feedback through the companion to queue repair. |
| Workflow/setup/runner failure, cancellation, missing result, unknown outcome, or stale SHA | Do not treat it as a code failure. Mark blocked or stale, explain the reason, and require human resolution/re-run before worker execution. |
| Label write fails | Preserve lease/run record, surface the API error, retry idempotently; do not show a successful remote transition that GitHub did not accept. |
| OpenCode missing, rate-limited, or crashed | Preserve worktree and execution record, mark blocked, notify user, and await explicit retry. No model fallback. |
| Patch structure/scope validation fails or remote PR head no longer matches the worker's starting SHA | Do not commit or push. Preserve the worktree and execution record; report blocked/stale state for human resolution and reconciliation. |
| GitHub Actions reports a deterministic check failure after the companion push | Enqueue another repair for that SHA if cycle budget remains; otherwise block for explicit retry. |
| GitHub Actions reports infrastructure/unknown/cancelled outcome after the companion push | Do not treat it as a code failure. Mark blocked and require human resolution/re-run. |
| PR closed or head SHA changed during execution | Cancel current work, release/expire its lease, prevent stale completion from changing current state, and reconcile labels. |
| SQLite unavailable/corrupt | Do not launch or continue an untracked worker. Surface the local persistence error and preserve existing database files for recovery. |
| Invalid config or config PR failure | Keep the prior merged configuration in effect; show actionable validation/API error; never apply unmerged values. |

Errors visible in the panel should identify whether the issue is remote connectivity, CI eligibility, local persistence, worker availability, or configuration publication, without exposing credentials or source content.

## MVP acceptance criteria

1. A pull request by `SumukhaK` is eligible only when open, non-draft, enabled by merged default-branch configuration, and based in `SumukhaK/Setu-mobile`; all others and all fork PRs receive secret-free/read-only CI only.
2. Passing `testDebugUnitTest`, `detekt`, and `lint` for the current SHA marks an eligible PR ready and does not enqueue a worker.
3. An explicitly identified deterministic check failure enqueues eligible repair for that exact SHA. Workflow/setup/runner failures, cancellation, missing results, and unknown outcomes become blocked/manual and are never classified as code failures.
4. After checks pass, the user can manually transfer Claude's `CHANGES_REQUESTED` feedback through the companion, bind it to a PR/head SHA, and explicitly enqueue worker repair. GitKeeper never accesses Claude automatically; Claude/user retain final architectural judgment and no approval or merge is automated.
5. CI uses `pull_request`; no `pull_request_target` workflow is introduced. Actions-originated label writes run in a trusted default-branch state workflow that neither checks out nor executes PR code or consumes PR artifacts. Companion label transitions use the authenticated control plane, never the worker process.
6. When the companion is offline, eligible repair work remains queued remotely; on return, reconciliation and atomic local lease acquisition prevent duplicate local claims.
7. Closing a PR cancels active work and removes active queue labels. Reopening starts a new cycle with a fresh three-invocation automatic budget while preserving attempt history. A new head SHA cancels stale work and only queues the new revision for its own deterministic failure or explicitly re-associated feedback.
8. No cycle can exceed three automatic OpenCode invocations across SHA changes. Cap exhaustion blocks work until explicit user retry; no automatic model fallback occurs.
9. An unavailable, rate-limited, or crashed OpenCode invocation preserves the worktree and local record, becomes blocked, and requires user action.
10. A config edit is validated and published as a config PR; unmerged or invalid configuration never changes Actions/worker behavior.
11. The UI displays PR/status, queue, settings, local telemetry, and the manual review-feedback transfer; pause/resume, cancel, and explicit retry operate on the corresponding local queue/worker state. Reduced and disabled motion preserve all functionality.
12. SQLite and telemetry are stored outside both repositories; telemetry defaults to 90-day retention and contains only the specified operational metadata.
13. No automatic or user-triggered worker execution launches until a Windows OS-isolation prototype has been built and its isolation properties verified; prompt instructions and a worktree alone are not security controls.
14. Before publishing a worker patch, the companion validates patch structure/scope and confirms the remote PR head still equals the worker's starting SHA; it does not run tests locally. After validation, the companion/control plane commits and makes a normal fast-forward push to the existing PR branch. The worker has no GitHub credentials; force-pushes and pushes to `main` are prohibited. GitHub Actions is the sole test gate and runs `./gradlew testDebugUnitTest detekt lint` on PR open/synchronize and after companion pushes. Any deterministic failure after push requeues repair within the cycle budget; infrastructure/unknown/cancelled results block.

## Deferred decision

Before implementation can safely enable the local worker, build and verify the Windows OS-level isolation prototype described above. Worker-output publication is decided: after both post-run validations pass, the companion commits and pushes to the existing PR branch under the restrictions in Worker behavior. Until the isolation gate passes, all worker execution remains disabled. No other unresolved product decision is required to author this pilot specification.
