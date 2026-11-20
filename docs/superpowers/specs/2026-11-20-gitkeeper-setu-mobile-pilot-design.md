# GitKeeper Setu-mobile Pilot Design

**Status:** Design specification
**Date:** 2026-11-20
**Target repository:** `SumukhaK/Setu-mobile` (public Kotlin/Android repository)
**GitKeeper implementation repository:** `SumukhaK/GitKeeper` (private)

## Purpose and scope

This specification defines a single-repository pilot that lets GitKeeper track pull requests in Setu-mobile, run eligible coding work through a local OpenCode worker, and expose its state through a Windows desktop companion and original desktop pet. The pilot includes GitHub Actions integration, a queued local worker, the pet and handscroll panel, and configuration UI.

GitKeeper application, companion, and worker code belong only in the private GitKeeper repository. Setu-mobile receives only the GitHub Actions integration and its configuration. This document is the sole design source of truth for the Setu-mobile pilot; it does not authorize implementation.

The pilot does not use a Kaggle-hosted model, AMD Token Lab, multiple worker CLIs, automatic model failover, Claude review orchestration, cross-repository avatars, or automatic analytics-driven prompt changes.

## Goals

- Detect Setu-mobile pull-request lifecycle events through GitHub Actions.
- Keep work queued while the local companion or worker is offline, and recover it safely when the companion returns.
- Automatically run the OpenCode worker only for eligible pull requests authored by `SumukhaK`, with a maximum of three automatic invocations per PR across head-SHA changes.
- Keep PR code untrusted: CI has no secrets and read-only permissions; privileged label changes never check out or execute PR code.
- Make queue and worker status understandable and controllable from a Windows desktop pet and handscroll panel.
- Keep GitHub authoritative for remote PR/queue state and local SQLite authoritative for worker execution and recovery.
- Publish configuration through a validated config pull request, and let Actions consume only configuration merged to the default branch.

## Non-goals

- Implementing the companion, worker, Actions workflow, or repository configuration in this task.
- Running the worker inside GitHub Actions.
- Running models locally or maintaining a worker fallback chain.
- Automating PR approval, merging, release, or final architectural review.
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
| Setu-mobile Actions integration | Run deterministic Android CI on `pull_request`; derive eligible PR metadata; set the queued label through a trusted, non-checkout state path. |
| GitKeeper companion | Poll GitHub with adaptive backoff; reconcile remote PR/label state with local SQLite; validate settings; create config PRs; display status; manage local worker lifecycle. |
| Local queue and lease store | Persist worker claims, invocation counters, execution state, cancellation/recovery metadata, and operational telemetry outside both repositories. |
| OpenCode worker | Make scoped changes in an isolated worktree for a claimed PR revision. The configured OpenCode model ID is user-selectable; this design does not guess an ID. |
| Desktop pet and panel | Show aggregate activity and provide PR/status, queue, settings, and local telemetry views and controls. |

## GitHub Actions and trust boundary

### Pull-request CI

- CI uses `pull_request`, never `pull_request_target`.
- For Kotlin/Android changes, run the repository-documented checks: `./gradlew testDebugUnitTest detekt lint`.
- CI has no repository secrets and only read permissions. Workflow jobs must not use a token to write PR/repository data; GitHub's built-in check-run reporting is expected.
- CI may build and test untrusted PR code, but only in the ordinary isolated GitHub Actions job with no secrets or write-capable token.
- The workflow reports completion and the tested head SHA so the companion can distinguish a result for the current revision from stale CI.

### Privileged state and labels

GitHub labels `gitkeeper/worker-queued`, `gitkeeper/worker-running`, and `gitkeeper/worker-blocked` represent remote queue state. Use a separate trusted state workflow triggered after the `pull_request` CI workflow completes (`workflow_run`); keep that state workflow on the default branch. It reads event/API metadata only and must not check out PR code, download or consume PR-produced artifacts, or execute PR-controlled scripts. It validates the source workflow, repository, PR association, actor, and head SHA before any write. This workflow may add the queued label only after CI passes for the current SHA, or reconcile queue labels when the PR is no longer eligible.

The state workflow receives only the minimum permission needed to add/remove PR labels (`issues: write`); it has no contents write permission and no long-lived personal token. Do not use `pull_request_target`. State changes must be idempotent and scoped to the validated PR in `SumukhaK/Setu-mobile`.

After claiming or blocking work, the companion's control plane updates the running/blocked label and removes obsolete queue labels through the user's authenticated GitHub connection. This control plane does not execute PR code; worker subprocesses receive no GitHub credentials and cannot write labels. The companion requests only the repository access needed for PR-label and config-PR operations.

The PR author is eligible for automatic worker execution only when the GitHub login is exactly `SumukhaK` and the PR head branch is in `SumukhaK/Setu-mobile` itself. Other authors and all fork PRs receive secret-free CI only and must never be auto-enqueued for local worker execution. Label updates cannot grant worker eligibility by themselves; the companion independently checks author, head repository, PR status, current head SHA, and configured enablement before claiming work.

An eligible open PR is not worker-ready until the required deterministic CI for its current head SHA succeeds. CI results for any prior SHA are stale and cannot authorize a run. Ineligible PRs and failed CI do not start a worker.

### Local worker execution safety gate

The worker can run project-defined tools and tests on a developer machine; a PR author's identity does not make repository contents safe. Every invocation therefore uses a dedicated PR worktree, never the companion's installation directory, the user's unrelated checkout, or either repository's GitKeeper source checkout. The worker must not inherit GitHub credentials, desktop sign-in tokens, or unrelated environment secrets.

**Blocking implementation decision:** select and validate the Windows OS-level isolation mechanism for executing PR-controlled build/test commands (for example, an appropriately restricted disposable environment). The current design does not assume that a worktree alone is a security sandbox. Automatic worker execution must remain disabled until this decision is implemented and verified. Do not weaken this gate by relying only on prompt instructions or the PR-author allowlist.

## Event and state model

### PR lifecycle inputs

The Actions integration and companion reconcile the following events/state:

- `opened` or `ready_for_review`: evaluate repository, author, draft status, merged config, and CI for the exact head SHA; queue when eligible.
- `synchronize` (head SHA changes): cancel the prior SHA's active invocation, mark it stale, and queue the new SHA if still eligible and under the automatic invocation cap.
- `reopened`: treat as a fresh lifecycle for the current revision; evaluate eligibility and CI again. Preserve the PR-wide automatic invocation count and history.
- `closed` or `merged`: cancel active work, release its lease, and remove queued/running labels. No new work is started for a closed PR.
- Companion restart or missed event: polling reconciliation compares GitHub's current PR state/labels/head SHA with local records and repairs stale claims and labels.

PR close cancels work. Reopening starts fresh work for the current revision; it does not resume a canceled process. A changed head SHA also cancels old work and may queue only the new revision. A late worker completion for a closed PR or superseded SHA cannot update the PR or replace current queue state.

### Remote states

| Remote label | Meaning | Entry and exit |
|---|---|---|
| `gitkeeper/worker-queued` | Eligible open PR waiting for a local worker claim. | Added after CI succeeds for current SHA and eligibility is confirmed. Removed when claimed, canceled, or blocked. |
| `gitkeeper/worker-running` | A local companion holds a valid lease and is executing the current SHA. | Added when a claim is acquired. Removed on completion, cancellation, lease recovery, or block. |
| `gitkeeper/worker-blocked` | Work requires user action, such as invocation-cap exhaustion or OpenCode failure. | Added on escalation. Removed only after the user explicitly retries or the PR becomes ineligible/closed. |

At most one of these labels may be present on a PR. GitHub labels are a user-visible projection of state; SQLite lease ownership and a fresh GitHub head-SHA check govern whether a local process may continue. If GitHub cannot be reached, the worker does not claim or continue a new operation based on stale remote state; active processes pause at safe checkpoints until reconciliation is possible.

### Automatic invocation budget

Count one invocation when the companion launches OpenCode for a PR. The automatic budget is three invocations total per PR, accumulated across all head SHAs and reopenings; it is not reset by a new commit or closing/reopening. A launch counts even if OpenCode exits immediately.

When the budget is exhausted, the PR becomes blocked and requires a deliberate user action. An explicit retry authorizes one additional invocation; it does not reset or replenish the automatic budget. If that retry also fails or escalates, the PR is blocked again and requires another explicit retry. Every manual retry is recorded separately from automatic attempts.

## Queue claiming, leases, and stale work

- The companion polls with adaptive backoff: more frequently while a PR is queued/running and less frequently when idle. Polling is outbound and uses the user's authenticated GitHub connection.
- Before claiming, the companion confirms the PR is open, eligible, not a draft, enabled in merged configuration, CI passed for the exact current SHA, and under the invocation cap (or explicitly retried).
- A local SQLite transaction atomically creates/acquires a lease keyed by `(repository, PR number, head SHA)`. A uniqueness constraint prevents two local worker processes from claiming the same revision. The lease records owner/process identity, start time, heartbeat/expiry, attempt, and cancellation state.
- The companion adds the running label only after acquiring the lease and rechecking the remote head SHA. A lease is renewed by heartbeats during execution. If a heartbeat expires after a crash, startup reconciliation stops/reaps the abandoned process where possible, preserves its worktree and logs, and requires a fresh eligibility/head check before resuming or retrying.
- A new SHA or PR closure marks the prior lease canceled. The worker receives a cancellation signal and must stop at a safe boundary. Its worktree is retained for diagnosis/recovery, but it cannot write results or labels for the stale revision.
- The single pilot companion is the only local claimer. If multiple companion instances are later allowed, the SQLite uniqueness/lease transaction must still guarantee one claimant; labels alone are not a lock.
- Remote transitions are idempotent and retried after transient API errors. SQLite transaction failures are surfaced and do not produce success-shaped remote state.

## Worker behavior

- OpenCode CLI is the only pilot worker. The user selects an exact OpenCode model ID in settings; no ID is hard-coded or inferred from the OpenCode Go subscription.
- Use the user's OpenCode Go subscription through the user's OpenCode installation. Do not store subscription credentials in GitKeeper configuration, SQLite telemetry, PR labels, or the Setu-mobile repository.
- Worker instructions include the user's branching, test-driven-development, and framework-sanity expectations. Deterministic objective checks remain in preflight/CI and do not rely on prompt compliance.
- The worker operates only in its dedicated PR worktree and revision. It must not merge or approve PRs, publish configuration changes, or modify GitKeeper itself as part of the Setu-mobile task.
- This design does not decide whether worker changes are committed/pushed to the existing PR branch or returned as a local patch for user review. No remote code write is authorized until that publication policy is decided.
- A successful invocation records its local outcome. Worker changes are not published remotely until the publication policy is decided; the worker does not trigger automatic merge or final architectural approval.
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

1. **PR/status:** repository, PR, current head SHA, eligibility, CI result, remote state, active worker state, and last update.
2. **Queue:** queued/running/blocked items, attempt count, and actionable error/escalation reason.
3. **Settings:** worker enabled state, selectable OpenCode model ID, polling preferences, motion preferences, and config-PR publication status.
4. **Local telemetry:** retained operational history and per-run outcome; no source content.

Controls are pause/resume, cancel current run, and explicit retry after escalation. Disabled/reduced motion must be supported throughout the pet and panel; motion preference changes must not affect queue or worker behavior. Status must remain available as text and not depend on animation or color alone.

## Local persistence, recovery, and telemetry

- Store queue/lease, worker lifecycle, configuration-publication status, and telemetry metadata in SQLite outside both GitKeeper and Setu-mobile repositories. Store worker worktrees and logs in separate managed local state paths, also outside the two repositories.
- Local SQLite is the recovery record for execution, not the authority for whether a PR remains open or which SHA is current. Reconcile from GitHub before resuming work after restart.
- Retain telemetry for 90 days by default, then delete/expire it automatically. The retention setting may be shortened by the user; changing it must not silently lengthen retention beyond 90 days in this pilot.
- Telemetry contains operational metadata only: timestamps/durations, PR/revision identifiers, state transitions, attempt count, outcome, selected model ID, and categorized errors.
- Do not store source code, diffs, prompts, credentials, screenshots, or full OpenCode input/output in telemetry. Worker logs needed for crash recovery must be access-restricted, kept separate from telemetry, and excluded from config PRs and GitHub labels.
- No analytics-driven self-modification or automatic prompt/rule updates. A user may inspect operational outcomes, but any future process change is a separately reviewed change.

## Error handling and user feedback

| Failure | Required behavior |
|---|---|
| GitHub API/network unavailable | Keep local data; do not make new claims or authoritative state transitions; show stale/offline status; retry with backoff. |
| CI missing, failing, or for stale SHA | Do not start a worker. Explain that current-head CI is required. |
| Label write fails | Preserve lease/run record, surface the API error, retry idempotently; do not show a successful remote transition that GitHub did not accept. |
| OpenCode missing, rate-limited, or crashed | Preserve worktree and execution record, mark blocked, notify user, and await explicit retry. No model fallback. |
| PR closed or head SHA changed during execution | Cancel current work, release/expire its lease, prevent stale completion from changing current state, and reconcile labels. |
| SQLite unavailable/corrupt | Do not launch or continue an untracked worker. Surface the local persistence error and preserve existing database files for recovery. |
| Invalid config or config PR failure | Keep the prior merged configuration in effect; show actionable validation/API error; never apply unmerged values. |

Errors visible in the panel should identify whether the issue is remote connectivity, CI eligibility, local persistence, worker availability, or configuration publication, without exposing credentials or source content.

## MVP acceptance criteria

1. A pull request by `SumukhaK` is eligible only when open, non-draft, enabled by merged default-branch configuration, and `testDebugUnitTest`, `detekt`, and `lint` pass for its current head SHA.
2. A PR by any other author, or any fork PR regardless of author, runs secret-free/read-only CI and never starts the worker.
3. CI uses `pull_request`; no `pull_request_target` workflow is introduced. Actions-originated label writes run in a trusted default-branch state workflow that neither checks out nor executes PR code or consumes PR artifacts. Companion label transitions use the authenticated control plane, never the worker process.
4. When the companion is offline, eligible work remains queued remotely; when it returns, reconciliation and atomic local lease acquisition prevent duplicate local claims.
5. Closing a PR cancels active work and removes active queue labels; reopening queues a fresh eligible run. A new head SHA cancels stale work and queues only that new revision after current-SHA CI passes.
6. No PR can receive more than three automatic OpenCode invocations across SHA changes and reopenings. Cap exhaustion blocks work until explicit user retry; no automatic model fallback occurs.
7. An unavailable, rate-limited, or crashed OpenCode invocation preserves the worktree and local record, becomes blocked, and requires user action.
8. A config edit is validated and published as a config PR; unmerged or invalid configuration never changes Actions/worker behavior.
9. The UI displays PR/status, queue, settings, and local telemetry; pause/resume, cancel, and explicit retry operate on the corresponding local queue/worker state. Reduced and disabled motion preserve all functionality.
10. SQLite and telemetry are stored outside both repositories; telemetry defaults to 90-day retention and contains only the specified operational metadata.
11. Automatic worker execution remains disabled until Windows OS-level isolation for PR-controlled commands is selected and verified; the worktree is not treated as a sandbox.
12. No worker output is pushed to a PR branch until its publication policy is explicitly decided and implemented.

## Deferred decision

Before implementation can safely enable the local worker, (1) choose and validate the Windows OS-level isolation mechanism described above and (2) decide whether worker output is pushed to the existing PR branch by the companion or presented as a local patch for explicit user publication. Until then, automatic execution and end-to-end worker acceptance remain blocked. No other unresolved product decision is required to author this pilot specification.
