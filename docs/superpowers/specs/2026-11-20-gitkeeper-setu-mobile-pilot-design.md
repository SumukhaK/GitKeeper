# GitKeeper Setu-mobile Pilot Design

> **Product scope superseded (2026-11-26).** GitKeeper's primary goal is now the review relay in
> [`docs/PRD.md`](../../PRD.md). The trust, eligibility, command-approval and patch-scope rules
> below stay in force until `docs/TECHNICAL.md` replaces them. Non-goals here that conflict with the
> PRD (for example, transferring Claude feedback only by manual paste) are replaced by the PRD loop.


**Status:** Design specification
**Date:** 2026-11-20
**Target repository:** `SumukhaK/Setu-mobile` (public Kotlin/Android repository)
**GitKeeper implementation repository:** `SumukhaK/GitKeeper` (private)

## Purpose and scope

This specification defines a single-repository pilot that lets GitKeeper track pull requests in Setu-mobile, route deterministic preflight failures and user-transferred review feedback to a local OpenCode worker, and expose its state through a Windows desktop companion and original desktop pet. The pilot includes GitHub Actions integration, a queued local worker, the pet and handscroll panel, and configuration UI.

GitKeeper application, companion, and worker code belong only in the private GitKeeper repository. Setu-mobile receives only the GitHub Actions integration and its configuration. This document is the sole design source of truth for the Setu-mobile pilot; it does not authorize implementation.

The owner explicitly accepts host execution on their Windows 10 Home workstation and waives the Windows Sandbox/VM/WSL2 and separate-user credential-boundary requirements for this pilot. The readiness value is `ACCEPTED_HOST_MODE`; it records the owner's risk decision, not a claim that host isolation exists. OpenCode and approved child commands run with the owner's logged-in Windows access. PR-controlled code or a command can access host files and credentials available to that account; a worktree and command confirmation do not prevent that access. This risk is documented and accepted, not represented as eliminated.

The pilot does not use a Kaggle-hosted model, AMD Token Lab, multiple worker CLIs, automatic model failover, automated Claude app access/review/approval/merge, cross-repository avatars, or automatic analytics-driven prompt changes. A user may manually transfer Claude's `CHANGES_REQUESTED` feedback through the companion to enqueue worker repair; Claude and the user retain final architectural judgment.

## Goals

- Detect Setu-mobile pull-request lifecycle events through GitHub Actions.
- Keep work queued while the local companion or worker is offline, and recover it safely when the companion returns.
- Automatically enqueue OpenCode repair only for eligible pull requests authored by `SumukhaK`: after a deterministic check failure or after the user submits Claude review feedback. Passing checks alone make a PR ready and do not start a worker.
- Limit automatic invocations to three per open/reopen cycle across head-SHA changes; closing and reopening starts a fresh budget while preserving historical attempts.
- Keep PR code untrusted: CI has no secrets and read-only permissions; privileged label changes never check out or execute PR code.
- Restrict local host-mode worker eligibility to the owner's explicit local repository allowlist. The pilot's sole public-repository exception is `SumukhaK/Setu-mobile`; any additional allowlisted repository must be verified private and owner-controlled. Forks and external PR heads are never eligible.
- Require a companion confirmation for each worker-requested host shell command, with the exact command and execution context shown before it runs; an unavailable or bypassable approval broker blocks worker launch.
- Restrict model selection to exact, owner-configured OpenCode subscription model IDs beginning with `opencode/`; no other provider or pay-as-you-go model is eligible.
- Make queue and worker status understandable and controllable from a Windows desktop pet and handscroll panel.
- Keep GitHub authoritative for remote PR/queue state and local SQLite authoritative for worker execution and recovery.
- Publish configuration through a validated config pull request, and let Actions consume only configuration merged to the default branch.

## Non-goals

- Implementing the companion, worker, Actions workflow, or repository configuration in this task.
- Running the worker inside GitHub Actions.
- Running models locally or maintaining a worker fallback chain.
- Providing OS-level isolation or claiming that a Git worktree or command-approval UI confines host access.
- Automating PR approval, merging, release, or final architectural review.
- Giving GitKeeper automated access to Claude or having it submit, retrieve, or interpret Claude reviews.
- Automatically changing prompts, rules, or product behavior based on telemetry.
- Supporting arbitrary repositories or any repository without the GitKeeper Actions/config contract. The first configured integration is Setu-mobile; additional owner-controlled private repositories require explicit local allowlisting and that repository's approved Actions/config integration.

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
                                                   |
                                      companion shell-command broker
                                      (confirm each exact request)
```

GitHub is the event source and authority for remote PR identity, head SHA, and queue labels. The companion polls GitHub outbound; no inbound listener or public companion endpoint is required. The companion is optional for GitHub event capture and CI, but must be online for local execution, interactive controls, and local progress display. Work remains queued remotely while it is offline.

### Components and responsibilities

| Component | Responsibility |
|---|---|
| Setu-mobile Actions integration | Run deterministic Android CI on `pull_request`; derive eligible PR metadata; queue repair for explicit deterministic-check failures through a trusted, non-checkout state path; mark infrastructure/unknown outcomes blocked. |
| GitKeeper companion | Poll GitHub with adaptive backoff; reconcile remote PR/label state with local SQLite; accept user-transferred Claude `CHANGES_REQUESTED` feedback; validate settings; create config PRs; display status; manage local worker lifecycle. |
| Local queue and lease store | Persist worker claims, invocation counters, execution state, cancellation/recovery metadata, and operational telemetry outside both repositories. |
| OpenCode worker | Make scoped changes in a PR-specific Git worktree for a claimed revision. It runs in accepted host mode, not a sandbox. Its exact model ID must be in the owner's local subscription allowlist. |
| Companion command broker | Intercept every worker-requested host shell invocation, present its exact executable/arguments, working directory, PR/revision, and purpose for one-time user approval, and execute only that unchanged request. |
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

After claiming or blocking work, the companion's control plane updates the running/blocked label and removes obsolete queue labels through the user's authenticated GitHub connection. This control plane does not execute PR code and does not intentionally inject GitHub credentials into OpenCode. In accepted host mode, it cannot guarantee that user-accessible GitHub credentials are inaccessible to OpenCode or child processes. Worker subprocesses do not receive label-writing APIs from GitKeeper and cannot write labels through the control plane. The companion requests only the repository access needed for PR-label and config-PR operations.

The PR author is eligible for automatic worker execution only when the GitHub login is exactly `SumukhaK` and the PR head repository is the same allowlisted base repository. Other authors and all fork/external PRs receive secret-free CI only and must never be auto-enqueued for local worker execution. Label updates cannot grant worker eligibility by themselves; the companion independently checks owner-managed repository allowlisting, repository ownership/privacy rules (including the explicit public Setu-mobile exception), author, head repository, PR status, current head SHA, configured enablement, and a valid repair reason before claiming work.

CI results for any prior SHA are stale and cannot authorize a run. Passing deterministic checks alone never queues a worker; a deterministic failure or user-submitted review feedback is required. Queueing does not itself execute a host command: each worker-requested shell command requires a fresh explicit companion approval. Ineligible PRs never start a worker.

### Accepted host-mode execution and readiness gate

The owner explicitly accepts that the Windows 10 Home host does not provide the waived OS isolation or a separate-user credential boundary. OpenCode and approved commands run as the owner and can access host resources available to that account. The application must describe this mode as `ACCEPTED_HOST_MODE`, never as isolated or sandboxed. The owner's acceptance changes the operating decision; it does not change the technical threat model.

GitHub Actions remains the sole test gate, and the worker/companion do not run the required Android tests locally before publishing. Each invocation uses a dedicated per-PR/per-SHA Git worktree outside the GitKeeper source checkout and the user's active Setu-mobile checkout so ordinary branch edits do not overwrite either working tree. This protects repository state against routine Git operations, not malicious or incorrect commands.

Worker readiness requires all of the following local checks:

- The target base repository matches an explicit owner-managed allowlist entry. The pilot entry `SumukhaK/Setu-mobile` is a documented public-repository exception; any other entry must be verified private and owner-controlled before it is added. A PR head must come from the same repository; forks and external PRs are never eligible.
- The PR author is exactly `SumukhaK`, the PR is open/non-draft, the merged/default-branch config enables work, the current SHA has an eligible repair reason, and the cycle budget permits the invocation.
- The selected model ID exactly matches an entry in the owner's local model ladder and has the `opencode/` prefix. The ladder is local-only, cannot be changed by a Setu-mobile config PR, and contains only the owner-approved entries recorded in “OpenCode worker”. No external provider or pay-as-you-go model is allowed.
- The companion can mediate all worker-requested shell invocations through an approval broker. Before each command runs, the UI shows the exact executable and arguments (or complete script text), working directory, repository, PR number, starting head SHA, and request purpose. Each approval authorizes one immutable request only; any change invalidates approval. Commands time out to denied, and no shell chaining, background execution, or unreviewed follow-on command inherits approval. If OpenCode can bypass this broker or the UI is unavailable, the worker stays disabled.
- The selected OpenCode CLI is detected at startup and must satisfy the version/capability contract verified by the command-broker feasibility test. The requested pilot CLI version is `1.19.6`; the application does not silently install, upgrade, or substitute a CLI.

Repository allowlists, the model ladder, patch-scope limits, ownership/privacy checks, worktree paths, and approval decisions are enforced in application code, not by prompt instructions. Even these controls cannot prevent a user-approved command or host-mode OpenCode process from reading or modifying other resources permitted to the logged-in user. That residual risk is part of the owner's acceptance.

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

- OpenCode CLI is the only pilot worker and runs directly on the owner's Windows 10 Home host in `ACCEPTED_HOST_MODE`. The local installation must report version `1.19.6`; the companion does not silently install, upgrade, or substitute a CLI.
- The owner's local model ladder is an ordered list of exact OpenCode subscription model IDs. The default active model is `opencode/deepseek-v4.1-flash-free`; the remaining entries, in order, are `opencode/glm-5.3`, `opencode/glm-5.3-flash`, `opencode/hy4-flash-free`, and `opencode/deepseek-v4-pro`. Only IDs present in that ladder can be selected. An empty ladder or any other provider ID prevents launch. The ladder lives in a local state file outside both repositories (`%LOCALAPPDATA%\GitKeeper\models.json`) and is never published in a Setu-mobile config PR.
- Model changes are owner-confirmed only. When a launch is blocked by quota, rate limiting, or `insufficient`/`exhausted`/`429` output, the companion records `model-unavailable`, preserves the worktree, blocks the PR, and surfaces the *next* ladder entry as a suggestion. The owner clicks to switch; the worker never advances the ladder on its own. A `model-unavailable` launch does not consume the three-invocation repair budget, because it never exercised the model.
- Use the user's OpenCode Go subscription through the user's OpenCode installation. Do not place subscription credentials in GitKeeper configuration, SQLite telemetry, PR labels, or Setu-mobile. This is not a separate-user credential boundary: OpenCode and a child process can access credentials that the logged-in user can access, and that residual risk is accepted.
- Worker instructions include the user's branching, test-driven-development, and framework-sanity expectations and, when provided, the user's Claude `CHANGES_REQUESTED` feedback and/or deterministic failure details. Passing checks alone do not invoke the worker. Prompt instructions are behavioral guidance only, never security enforcement.
- Claude feedback is submitted by the user in the companion and bound to the selected PR head SHA. GitKeeper has no automated Claude app/API access and does not approve, merge, or make final architectural judgments. The user returns worker changes to Claude for review through their existing manual process.
- The worker edits only its dedicated PR worktree and revision by design. The companion verifies that the resolved worktree path is under its managed worktree root and not either repository's active checkout. A worktree protects active branch state from ordinary Git operations but does not confine host access; commands may still access other files available to the user account.
- Every worker-requested shell invocation is routed through the companion command broker. Before execution, the desktop UI shows the exact executable/arguments or full script, working directory, target repository, PR number, starting SHA, and request purpose. The user must approve or deny that single request. Approval is bound to the immutable request and expires; edits, environment changes, cwd changes, new commands, or a later invocation require a new confirmation. Deny by default when the companion is unavailable, the request cannot be displayed completely, or the broker cannot prove the command will execute only after approval. No chaining, background shell, or approval carry-over is allowed.
- The broker must use a supported OpenCode CLI 1.19.6 hook/tool boundary that prevents direct shell bypass. If feasibility tests cannot demonstrate complete interception, mark host worker readiness blocked and do not launch OpenCode for repair. The owner-accepted host risk does not waive this per-command confirmation requirement.
- The worker must not merge or approve PRs, publish configuration changes, or modify GitKeeper itself as part of the Setu-mobile task.
- Before publishing, the companion validates the patch structure and scope against the assigned repair, then queries GitHub to confirm the PR's remote head SHA still equals the SHA given to the worker. It does not run tests locally. The exact rule is in "Patch scope rule for publication" below, and its thresholds are code constants rather than configuration so a config PR cannot loosen them.
- Only after both validations pass, the companion/control plane—not OpenCode—stages and commits the worker changes, then pushes the commit to that PR's existing head branch. The control-plane credential is not intentionally injected into the OpenCode process, but host mode cannot guarantee that user-accessible GitHub credentials are inaccessible to it. Pushes must be ordinary fast-forward pushes; never force-push and never push to `main`.
- If patch structure/scope validation fails or the remote head SHA changed, do not commit or push; preserve the worktree and report a blocked/stale result for user action and reconciliation.
- After a successful push, GitHub Actions runs `./gradlew testDebugUnitTest detekt lint` against the new PR head SHA. Passing checks leave the PR ready with no worker queued. Any deterministic CI check failure after the push enqueues another repair for that SHA if the cycle budget remains; infrastructure, cancelled, or unknown outcomes remain blocked for human action.
- A successful invocation records its local outcome and does not trigger automatic merge or final architectural approval.
- If OpenCode is unavailable, rate-limited, or crashes, preserve the worktree and execution record, transition to blocked, notify the user, and wait for explicit retry. There is no automatic model fallback in this pilot: the companion may only suggest the next ladder entry for an owner-confirmed switch. Quota and rate-limit output is classified as `model-unavailable` and recorded separately from a worker failure, so it does not consume the invocation budget.
- The companion supports pause/resume and cancel-current-run controls. Pausing prevents new claims and pauses/requests a safe pause of active work; cancel signals the active worker and records the outcome. A retry is always explicit after escalation.

## Patch scope rule for publication

A worker result is publishable only when the pure-function check over the worktree and its
starting SHA returns an allowed decision. Every check fails closed, and each violation carries
an enumerated reason code so the panel can explain the block. Patches are computed from the
staged index, so the validated patch is exactly the committed patch.

Repository identity:

1. The resolved worktree real path is under the managed worktree root, and is not the root itself.
2. The worktree is not the active checkout of either repository.
3. `HEAD` equals the starting SHA given to the worker.

Patch structure:

4. The patch is non-empty, with nothing left unstaged or untracked.
5. At most 10 changed files.
6. At most 400 changed lines in total and at most 150 changed lines in any single file.
7. No deletions and no renames or copies.
8. No submodule (`160000`) or symlink (`120000`) entry and no executable-bit change.
9. No binary content, detected as a NUL byte in either revision.

Paths are repository-relative with forward slashes. Absolute paths, drive letters, `..`, `~`,
and backslashes are rejected. Comparison is NFC-normalised and case-insensitive, because the
Windows filesystem is case-insensitive while the Git index is not.

Allowed paths: `app/src/**`, `core/**/src/**`, `feature/**/src/**`, `tests/**`, `docs/**`, and
`app/src/main/AndroidManifest.xml`.

Denylisted regardless of the allowlist: `.github/**`, `scripts/**`, `gradle/**`, `gradlew`,
`gradlew.bat`, `**/*.gradle.kts`, `gradle.properties`, `config/**`, `third_party/**`,
`index.html`, `prototype.*`, `**/network_security_config.xml`, `**/proguard*`, `**/*.keystore`,
`**/*.jks`, `**/*.pem`, `**/*.p12`, `.gitmodules`, and `.gitattributes`.

Manifest capability rule: because the manifest is in scope, a manifest edit may only change
values inside existing elements. Any change to the set of `<uses-permission>`, `<permission>`,
or `<uses-feature>` elements, to a component name (`<activity>`, `<service>`, `<receiver>`,
`<provider>`), to an added or removed `<intent-filter>`, or to `android:exported`,
`android:usesCleartextTraffic`, `android:debuggable`, or `android:allowBackup` is rejected. A
repair that needs a new capability is a human change.

Intent:

10. At least one changed path is under a module `src/` directory other than the manifest.
11. When the repair carries deterministic CI failure detail, at least one changed path is a test path.
12. Added lines are scanned for secret patterns (private-key headers, `AKIA`, `ghp_`,
    `github_pat_`, `gho_`, `password=`, `api_key=`, `secret=`).

Publication order:

13. Only after the checks above pass, re-query GitHub and confirm the remote head still equals
    the starting SHA, the PR is open and non-draft, the head is from the same repository, and
    the author is `SumukhaK`.
14. Commit from the control plane, then fast-forward push only.
15. On any violation, do not commit, never force-push, never push to `main`, preserve the
    worktree, mark the PR blocked, and require an explicit user retry.

The 10 / 400 / 150 thresholds are code constants rather than configuration, for the same
reason as the invocation budget: a Setu-mobile config PR must not be able to widen them.

## Configuration and publication

- Configuration consumed by Actions resides in Setu-mobile and is versioned with the Actions integration. Actions reads only the default-branch (merged) configuration; config in an open PR never affects CI authorization or worker eligibility.
- Companion settings include worker enablement, polling behavior, and presentation/accessibility preferences. The model ladder and the selected model ID are local-only settings and are not published to Setu-mobile. Repository allowlist entries and `ACCEPTED_HOST_MODE` are also local owner-controlled settings, not remotely-configurable by a config PR. Credentials are managed by GitHub/OpenCode authentication providers, not configuration values.
- A settings change is schema-validated locally before publication. Invalid values are rejected with a field-specific explanation and cannot create a PR.
- Publishing settings creates or updates a config branch and pull request in Setu-mobile using the user's GitHub identity and the minimum available repository permissions. It does not push directly to the default branch. The companion shows PR creation, review, merge, and rejection status.
- The Actions integration consumes changed configuration only after it is merged to the default branch. The validated schema must reject unknown execution/permission controls and secrets; no config field can grant itself elevated Actions permissions or bypass author/CI/host-readiness gates.

## Desktop companion and pet

The companion is a Python application using PySide6/QML and is optional for GitHub event capture. It may run on Windows to show progress and publish validated configuration. It uses outbound/adaptive GitHub polling and does not host a public inbound service.

The desktop pet is an original nine-tailed fox-spirit design, not a Naruto/Kurama replica. Clicking it opens a custom Japanese handscroll-inspired panel with four areas:

1. **PR/status:** repository, PR, current head SHA, eligibility, CI result, remote state, active worker state, and last update; allow the user to paste/transfer Claude's `CHANGES_REQUESTED` feedback, select a PR/SHA, review its association, and explicitly submit it as a repair request.
2. **Queue:** queued/running/blocked items, repair reason (deterministic check or user-transferred review feedback), attempt count, and actionable error/escalation reason.
3. **Settings:** worker enabled state, the local model ladder with the selected ID and an owner-confirmed switch control, explicit repository allowlist, `ACCEPTED_HOST_MODE` risk acknowledgement, polling preferences, motion preferences, and config-PR publication status. The model ladder, repository allowlist, and host-mode acceptance are not included in config PRs.
4. **Local telemetry:** retained operational history and per-run outcome; no source content.

Feedback for an outdated SHA cannot be applied without explicit re-association. Controls are pause/resume, cancel current run, and explicit retry after escalation. A dedicated command-approval view/modal is shown for every host shell invocation, including exact command/script, working directory, repository, PR/head SHA, purpose, and environment variable names (values remain hidden). Approve and deny are separate explicit actions; no response or UI failure denies execution. Disabled/reduced motion must be supported throughout the pet and panel; motion preference changes must not affect queue or worker behavior. Status must remain available as text and not depend on animation or color alone.

## Local persistence, recovery, and telemetry

- Store queue/lease, worker lifecycle, configuration-publication status, and telemetry metadata in SQLite outside both GitKeeper and Setu-mobile repositories. Store worker worktrees and logs in separate managed local state paths, also outside the two repositories.
- Local SQLite is the recovery record for execution, not the authority for whether a PR remains open or which SHA is current. Reconcile from GitHub before resuming work after restart.
- Retain telemetry for 90 days by default, then delete/expire it automatically. The retention setting may be shortened by the user; changing it must not silently lengthen retention beyond 90 days in this pilot.
- Telemetry contains operational metadata only: timestamps/durations, PR/revision identifiers, state transitions, attempt count, outcome, selected model ID, and categorized errors.
- Command approval telemetry records the request digest and allow/deny/outcome metadata only, never raw command/script text, environment values, source content, or prompts.
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
| The active model is not on the local ladder, the ladder is empty, or the ladder file is missing/unparseable | Do not launch; report a local configuration error and require owner configuration. |
| OpenCode reports quota or rate limiting | Classify as `model-unavailable`, preserve the worktree, block the PR, and suggest the next ladder entry. Only the owner switches models; the invocation budget is unchanged. |
| Command-approval UI unavailable, request display incomplete, or OpenCode can bypass the broker | Deny the command and block the worker; do not fall back to direct host execution. |
| Patch structure/scope validation fails or remote PR head no longer matches the worker's starting SHA | Do not commit or push. Preserve the worktree and execution record; report blocked/stale state with the enumerated scope reason codes for human resolution and reconciliation. |
| GitHub Actions reports a deterministic check failure after the companion push | Enqueue another repair for that SHA if cycle budget remains; otherwise block for explicit retry. |
| GitHub Actions reports infrastructure/unknown/cancelled outcome after the companion push | Do not treat it as a code failure. Mark blocked and require human resolution/re-run. |
| PR closed or head SHA changed during execution | Cancel current work, release/expire its lease, prevent stale completion from changing current state, and reconcile labels. |
| SQLite unavailable/corrupt | Do not launch or continue an untracked worker. Surface the local persistence error and preserve existing database files for recovery. |
| Invalid config or config PR failure | Keep the prior merged configuration in effect; show actionable validation/API error; never apply unmerged values. |

Errors visible in the panel should identify whether the issue is remote connectivity, CI eligibility, local persistence, worker availability, or configuration publication, without exposing credentials or source content.

## MVP acceptance criteria

1. A pull request is eligible only when its base repository is in the owner's explicit local allowlist, it is open/non-draft, enabled by merged default-branch configuration, and authored by `SumukhaK` from the same repository. The pilot allows public `SumukhaK/Setu-mobile` as its sole explicit public exception; any additional allowlisted repository must be owner-controlled and private. All forks/external PRs receive secret-free/read-only CI only.
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
13. The environment readiness state is `ACCEPTED_HOST_MODE` after the owner's risk acceptance is recorded. Worker launch additionally requires an explicit repository allowlist entry, a local model ladder with a selected `opencode/` model, and an OpenCode CLI 1.19.6 command-broker feasibility test proving every shell request requires a one-shot companion approval. This is explicitly not OS isolation: OpenCode and approved commands run as the owner and can access host resources permitted to that account. If command interception is incomplete or bypassable, worker execution stays disabled.
14. Before publishing a worker patch, the companion validates patch structure/scope and confirms the remote PR head still equals the worker's starting SHA; it does not run tests locally. After validation, the companion/control plane commits and makes a normal fast-forward push to the existing PR branch. GitHub credentials are not intentionally injected into the worker, but host mode cannot guarantee user-accessible credentials are inaccessible to it; force-pushes and pushes to `main` are prohibited. GitHub Actions is the sole test gate and runs `./gradlew testDebugUnitTest detekt lint` on PR open/synchronize and after companion pushes. Any deterministic failure after push requeues repair within the cycle budget; infrastructure/unknown/cancelled results block.
15. The companion displays and requires a separate explicit confirmation before each worker-requested host shell command. Approval binds to the exact executable/arguments or full script, cwd, repo/PR/SHA, and purpose; it cannot be reused or modified. Timeout, offline state, partial display, or broker failure denies the command.

## Readiness and accepted risk

The owner has decided to operate on the Windows 10 Home host without OS-level isolation or a separate-user credential boundary. This replaces the earlier Windows isolation prototype gate and is recorded as `ACCEPTED_HOST_MODE`; it is not evidence that the host is isolated. The owner accepts that OpenCode or a user-approved command can access files and credentials available to the logged-in account. Do not describe the worktree, repository allowlist, model allowlist, prompt, or command-approval UI as a sandbox.

The remaining technical readiness gate is command mediation: before any worker launch, verify that OpenCode CLI 1.19.6 routes every shell invocation through the companion's one-shot approval broker and cannot bypass it. If this cannot be proven, retain `ACCEPTED_HOST_MODE` as the owner's risk choice but keep execution disabled. The local model ladder and any additional private repositories are owner-provided local configuration; the ladder order and default are recorded in "OpenCode worker", and both remain local-only. Worker-output publication remains decided as specified above, with GitHub Actions as the sole test gate.
