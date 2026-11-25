# GitKeeper Setu-mobile Pilot — Handover Tracker

**Last updated:** 2026-11-26

**Purpose:** Give a new engineer or AI agent enough durable context to continue the pilot without reconstructing decisions from chat history.

## Current state

- The approved architecture and scope are in [the pilot design spec](../specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md).
- The original design-only PR, [GitKeeper #1](https://github.com/SumukhaK/GitKeeper/pull/1), is merged; it contained only the design spec.
- The GitKeeper implementation slice is merged: [GitKeeper #2](https://github.com/SumukhaK/GitKeeper/pull/2), merge commit `d7ea534e04bf9b641ce040da2c4cff660baabce9` on `main`, from branch `sumukhak-gitkeeper-pilot-design`. It adds the companion UI foundation, the fail-closed worker/config gate, and this tracker. Treat `main` as the source of truth; branch tips may drift.
- The model-ladder and patch-scope policy slice plus `README.md` are merged: [GitKeeper #4](https://github.com/SumukhaK/GitKeeper/pull/4), merge commit `9b3213fdca9f618e018fa2dbc797be067d2dc47f`.
- The repository housekeeping rule (README/About/tracker updates on every push) is merged: [GitKeeper #5](https://github.com/SumukhaK/GitKeeper/pull/5), merge commit `cab09d8918468bd4a18101fba94f9f626b5f4eae`.
- Typed host-mode readiness is merged: [GitKeeper #6](https://github.com/SumukhaK/GitKeeper/pull/6), merge commit `ca34a45`, which adds `src/gitkeeper/host_mode/readiness.py` and `tests/host_mode/test_readiness.py` and retires the historical isolation report as an authorization input.
- The one-shot command approval broker and its companion overlay are merged: [GitKeeper #8](https://github.com/SumukhaK/GitKeeper/pull/8), merge commit `9f9603dc234c7d746541e06171bb10b10548efcd`. It adds `src/gitkeeper/host_mode/command_broker.py`, `src/gitkeeper/ui/viewmodels/command_approval.py`, and `src/gitkeeper/ui/qml/CommandApprovalDialog.qml`, with the readiness plan's Task 2 Steps 1-7 ticked. Full suite at merge: `py -m pytest -q` — 318 passed.
- Setu-mobile Actions/config implementation is in [Setu-mobile #19](https://github.com/SumukhaK/Setu-mobile/pull/19), head `a153271a18241b3cf5b9d5ed7025916c6b11e9f8`. Its `Android CI` and `GitGuardian Security Checks` passed on that head; the PR is open and unmerged.
- OpenCode command mediation is proven for CLI `1.19.6` and merged: [GitKeeper #10](https://github.com/SumukhaK/GitKeeper/pull/10), merge commit `6a6415f56b4575b9784309a2be0fc4e5f7cd3f01`. It adds `src/gitkeeper/host_mode/opencode_capability.py` (29 tests), `src/gitkeeper/host_mode/broker_plugin/gitkeeper-command-broker.mjs` (18 tests), and the evidence record `docs/superpowers/host-mode-command-broker-result.md`, with the readiness plan's Task 3 Steps 1-7 ticked. A three-arm live probe against the real CLI showed approval runs the exact command, denial blocks it, and an approval naming a different request id blocks it. Full suite at merge: `py -m pytest -q` — 365 passed.
- The README workflow diagram was restyled and merged: [GitKeeper #12](https://github.com/SumukhaK/GitKeeper/pull/12), merge commit `f24c7932ed40d754778992401eb01cb0f0ea7cc8`. The old diagram rendered at 3174x479 and GitHub scaled its text to unreadable size; it is now a 619x1174 vertical staircase with a warm semantic palette, zone tags 1/2/3, the `worker-running` announce step, and the owner-retry return edge. Docs only: no code, test, workflow, or scope change, so the About description was left as is.
- The approved queue implementation plan is merged in [GitKeeper #14](https://github.com/SumukhaK/GitKeeper/pull/14), merge commit `45a16255e694c372f1b970ad532a72d352c8d3a0`, after independent approval of `e836ab513b2cab5a74ec4f1508045d9efbb1b6b8`. Slice 1 (domain contracts and readiness-reason adapter) is implemented at `6be6122` in the isolated `D:\GitKeeper-worktrees\queue-slice-1-domain` worktree on branch `queue-slice-1-domain`, with the reviewed telemetry projection corrected at `f44ed8b`: 199 queue tests, 236 combined queue/readiness tests, and 564 full-suite tests pass. It awaits scoped re-review and merge. Slices 2–7 and original queue-plan Tasks 1–2 remain pending; no queue code is merged yet. Source-plan Tasks 3–5 are detailed in the merged [worktree, runner, and service plan](2026-11-25-gitkeeper-worktree-runner-service.md) and do not authorize implementation.
- **Worker execution is not ready and must remain disabled.** `ACCEPTED_HOST_MODE` records the owner's acceptance of host risk; it is not proof of isolation and does not by itself authorize a worker launch. The broker's default executor, `execution_disabled`, still refuses every launch: mediation is proven, but nothing wires a real executor. Queue/persistence/reconciliation must land first; source-plan Task 4 is later, out-of-scope worker work and must consume the typed capability and call `require_inert_worktree` before any launch.

## Completion tracker

| Area | Status | Evidence / remaining work |
|---|---|---|
| Pilot architecture and boundaries | Complete | The accepted spec defines GitHub Actions, local companion/worker responsibilities, trusted/untrusted boundaries, state transitions, telemetry, and pilot acceptance criteria. |
| Companion UI foundation | Complete, integration pending | The branch contains queue/status view models, fox/handscroll UI, settings/config-PR UI, manual feedback, telemetry view, the command approval overlay, and focused tests. Slice 1 working-tree verification: `py -m pytest -q` — 564 passed. Real queue/worker and host-readiness wiring is still pending. |
| Setu-mobile Actions/config | Implemented; remote runtime partly verified | PR #19 contains strict config validation, secret-free PR CI, and a default-branch state classifier/label writer. Both current PR checks pass. The state workflow's label behavior and fork-PR runtime behavior still need live verification after the workflow is on the default branch. Do not merge this PR without the owner's review/authorization. |
| Accepted Windows host mode | Decision accepted; typed readiness implemented | The owner accepted direct execution on their Windows 10 Home account and waived VM/WSL2/Sandbox and separate-user boundaries. `src/gitkeeper/host_mode/readiness.py` now computes readiness from typed facts and names every refusal: allowlist, privacy, fork head, exact author, open/non-draft, merged config, repair reason, stale check SHA, attempt budget, local model ladder, CLI 1.19.6, and proven command broker. Config publication still rejects `worker_enabled: true`, the historical isolation report is documented as evidence only, and `gitkeeper.isolation` cannot gate host mode. Still pending: wiring this status into the UI/queue, and the mediated executor behind the command broker. The CLI capability probe now exists as `opencode_capability.py` and returns verified against the real 1.19.6 install. Never call this isolation or a sandbox. |
| OpenCode command mediation | Proven for CLI 1.19.6; executor not wired | `src/gitkeeper/host_mode/command_broker.py` does everything except launch: canonical digest over executable/argv-or-script/env *names*/cwd/repo/PR/SHA/purpose, full script rendering when an interpreter is used, one-use approval keyed by an unforgeable `decision_id`, digest + request identity + replay + TTL + stale-fact re-checks in that order, and audit metadata with no argv, script, env name, or cwd. `opencode_capability.py` turns the version, the resolved config and a persisted probe record into a typed verdict, and `broker_plugin/gitkeeper-command-broker.mjs` is the `tool.execute.before` hook that forwards the exact command and throws unless the companion approves that request id. Three live arms against the real CLI: approval ran the command, denial blocked it, a stale approval blocked it. Evidence: `docs/superpowers/host-mode-command-broker-result.md`. Still missing: an executor behind `execution_disabled`, and Task 4 must refuse a worktree containing `.opencode` via `require_inert_worktree` because the CLI auto-loads plugins from it outside the project-config guard. |
| Local queue and worker | Plan merged; Slice 1 awaiting scoped re-review; no queue code merged | Follow the merged [detailed queue plan](2026-11-25-gitkeeper-queue-persistence-reconciliation.md) for domain contracts, SQLite queue/lease persistence, operational-only telemetry, GitHub reads/labels, queue transitions, restart reconciliation, and adaptive polling. Planning PR [#14](https://github.com/SumukhaK/GitKeeper/pull/14) is merged at `45a16255e694c372f1b970ad532a72d352c8d3a0`. Slice 1 is implemented at `6be6122`, telemetry projection corrected at `f44ed8b`, with 564 full-suite tests passing, and awaits scoped re-review and merge. Per-PR/SHA worktrees, worker execution, publication, and UI integration are planned separately in the merged [worktree, runner, and service plan](2026-11-25-gitkeeper-worktree-runner-service.md). |
| Worker publication | Policy resolved; enforcement implemented | The owner delegated and approved a machine-checkable patch-scope rule, now recorded in the spec section "Patch scope rule for publication" and enforced by `src/gitkeeper/policy/patch_scope.py`. It fails closed with enumerated reason codes: worktree identity, 10 files / 400 total lines / 150 per-file limits, no deletions/renames/submodules/symlinks/binary/mode changes, allowlisted paths with a denylist that wins, a manifest capability rule, test-change and secret-scan intent checks, then a remote re-query before a fast-forward-only push. The thresholds are code constants so a config PR cannot widen them. Still missing: the git adapter that builds these decisions from a real worktree, and the control-plane commit/push step. |
| Local OpenCode model policy | Policy resolved; persistence implemented | The owner supplied the ladder: default `opencode/deepseek-v4.1-flash-free`, then `opencode/glm-5.3`, `opencode/glm-5.3-flash`, `opencode/hy4-flash-free`, `opencode/deepseek-v4-pro`. `src/gitkeeper/policy/models.py` holds the ladder, validates every ID, persists it atomically to `%LOCALAPPDATA%\GitKeeper\models.json` (outside both repositories), and classifies quota/rate-limit output as `model-unavailable` without consuming the invocation budget. The settings panel exposes the ladder, the active ID, and the next-entry suggestion; switching is owner-confirmed only. Still missing: the launch path that reads this policy and the `models.json` bootstrap on a fresh host. |
| GitHub credential provider | Undecided | Use an injected least-privilege GitHub service. Do not invent a token location or expose credentials to QML. In accepted host mode, credentials accessible to the logged-in user may also be accessible to OpenCode or its child processes. |
| End-to-end/manual verification | Pending | Verify UI behavior on Windows, command approval/denial/replay, worktree invariants, GitHub label transitions, config PR publication, and Actions behavior. Do not run Android Gradle checks locally; GitHub Actions is the test gate. |

## Queue execution slices

Statuses and evidence mirror the [detailed queue plan](2026-11-25-gitkeeper-queue-persistence-reconciliation.md). Original Task 1 maps to slices 1–3; original Task 2 maps to slices 4–7. Planning PR [#14](https://github.com/SumukhaK/GitKeeper/pull/14) is merged at `45a16255e694c372f1b970ad532a72d352c8d3a0`. Slice 1 is implemented at `6be6122`, telemetry projection corrected at `f44ed8b`, and awaits scoped re-review; no queue code is merged. Source-plan Tasks 3–5 have a separate merged [handoff plan](2026-11-25-gitkeeper-worktree-runner-service.md); implementation remains pending and worker execution remains disabled.

| Slice | Scope | Status | PR | Merge commit |
| --- | --- | --- | --- | --- |
| P0 | Publish plan, status tracking, and README/handover correction | merged | [#14](https://github.com/SumukhaK/GitKeeper/pull/14) | `45a16255e694c372f1b970ad532a72d352c8d3a0` |
| 1 | Domain contracts and readiness-reason adapter | review | — | — |
| 2 | SQLite queue/cycle/lease persistence | pending | — | — |
| 3 | Operational-only telemetry persistence | pending | — | — |
| 4 | GitHub read/config/label REST adapter | pending | — | — |
| 5 | Queue service and label/budget transitions | pending | — | — |
| 6 | Restart/close/reopen reconciliation | pending | — | — |
| 7 | Adaptive outbound poller | pending | — | — |
| Final | Combined whole-branch review | pending | — | — |

## Documentation review — PR #14

- Initial P0 review passed at `9e712a05`. Independent review of combined docs head `e47b090` requested changes with five Required findings; that head must not be merged.
- Observation freshness: older observations and conflicting equal-time observations are rejected before mutation; delayed responses use request-start time. Tests cover stale reconciliation and post-claim transport/storage failure.
- Reservation-only publication uncertainty: an already-published preserved commit cancels the reserved manual retry without error or budget, leaves the cycle blocked, preserves any existing latch, and never relaunches or auto-queues stale repair evidence; it is not recorded as a running/failed launch. Only a later valid manual launch's durable start can clear a latch. A remote mismatch or publisher validation refusal is a fixed worker failure.
- Disabled executor: returns `cancelled` with `process_started=False`; Task 5 routes it through Task 4's `block_reserved`, preserving the worktree without a failed attempt or budget use.
- Interface ownership: Task 4 produces `QueueService.block_reserved`; Task 5 only consumes it. Its fencing, projection, budget, and storage-failure behavior is tested in Task 4.
- Offline UI: successful local reads reapply composed remote health; queue controls and feedback submission refuse while stale/offline/degraded, snapshots remain visible, and QML changes only the feedback button's `is_stale` binding.
- A second independent review of `a088da6` requested five further changes: close/reopen was not serialized with publication; delayed pre-close observations could recreate a closed cycle; unknown-publication reconciliation could be skipped or select the wrong worktree; escalation lacked a durable explicit-retry latch; and close detection lacked a PR-404-only adapter distinction.
- Second-round corrections add one shared process-wide lifecycle guard (global before per-PR) across coordinator/publisher/close reconciliation, a persisted `closed_at` observation tombstone, durable `UncertainPublication` worktree/commit associations with publisher-bound `finish_reconciliation`, a cycle-scoped `ManualRetryLatch` cleared only by durable manual launch, and typed `PullRequestNotFound` only for single-PR 404s. Reconciliation has no queue-side bare remote-comparison shortcut: exact publisher validation distinguishes `remote_equal`, `remote_diverged`, and `validation_failed`; equality preserves any latch and never reclassifies to ready, while transport-unknown preserves the association. Pre-association crash recovery scans current-cycle intents before generic lease recovery, uses exact UUID4 worktree identity, and records uncertainty without a claim or launch gates; later read-only reconciliation validates the preserved worktree/commit/scope and current target under its own exact claim but never authorizes a launch.
- Self-review then removed a lock-scope contradiction: the lifecycle guard never spans OpenCode execution, approval waits, heartbeat loops, or other unbounded work. Every launch path binds an exact cancellation event and pending registry entry before approval/process creation; the queue and worker plans share `CancellationPort.cancel(lease)` so changed-head and typed-404 close reconciliation cancel that exact pending/active identity before observation/closure persistence. Ordinary reconciliation is two-phase: authoritative observation under the guard, label I/O outside it, then guarded current-state projection finalization. Exact UUID4 worktree IDs and no-fallback lookup prevent reconciliation from selecting a different preserved worktree. False/unconfirmed process cancellation still leaves the durable publication fence in force and reports blocked/preserved.
- A third independent review of `a6f670d` found three Required recovery/projection races: a successful unknown push could not reconcile after polling advanced the remote head; crash-before-association recovery was blocked by launch gates; and two-phase reconciliation could overwrite newer state or labels.
- Fourth-round corrections persist `PublicationIntent` after local commit/scope validation and before the final target read/push. Startup/idle scanning associates a differing exact worktree HEAD before generic orphan recovery, without a claim or launch gates. Reconciliation uses the current head for its reserved claim while retaining the original key/worktree/commit anchor, and succeeds even when a later poll has advanced from `S0` to `C1`. Exact `ProjectionFence` compare-and-commit rejects stale label/state writes; canonical lock order is projection -> lifecycle -> per-PR, with no lifecycle-held projection acquisition. Generic recovery preserves unresolved intents when dependencies are unavailable.
- A fresh independent review of `f2e310f` found three Required issues: terminalizing unknown publication made its later reconciliation item unclaimable; the publisher interface did not receive the exact attempt claim required by intent recording; and the source plan still described P0 as reviewed/pending rather than in current correction/re-review.
- Fifth-round corrections add one narrow reconciliation-only reservation for the exact blocked item carrying a matching unresolved same-cycle association. It preserves blocked state, latch/labels, grants no launch eligibility, and makes no unrelated blocked item claimable. `PatchPublisher.validate_and_publish` now takes the exact per-invocation `ClaimResult`, records its intent while that attempt remains running and before terminal completion/final target mutation, and has a sequential-attempt regression preventing shared-claim identity leakage. The source plan distinguishes the initial P0 approval from the current PR #14 fresh-approval gate.
- A fresh independent review of fifth correction head `f12df43` found two Required issues: a changed-head cancellation could terminally remove an unknown-publication intent before association persistence, and both detailed plans retained obsolete review trackers.
- Sixth-round corrections persist normal uncertain-publication association under the shared lifecycle guard before release and atomically set/preserve the latch. Intent recovery includes exact same-cycle attempts terminally cancelled by changed-head/owner cancellation, so association-storage failure or the former race cannot erase recovery identity. Recovery runs before normal launch selection, preserves original `S0`/current `C1` identity, never rewrites terminal cancellation, and excludes close/reopen cycles. Forced race and association-failure regressions are specified.
- A fresh independent review of sixth correction head `fec5b541e0f64382499eb4e34c3ae683207b586b` found four High-severity Required issues: intent-discovery tests rejected every terminal attempt despite same-cycle cancelled recovery; the publisher committed before fresh authoritative validation; owner cancellation did not serialize with the publisher's shared guard; and missing/mismatched worktrees could leave unresolved intents without a durable launch-blocking latch.
- Seventh-round corrections align discovery tests with current-open-cycle cancelled-intent recovery, require fresh authoritative PR/target/head validation before committing and again before push, pass the identical process-wide lifecycle guard to every composed `QueueService` (rejecting a different guard before side effects), and latch/block every unassociable current-cycle intent before launch selection. Retry creates no authorization while unresolved; only exact owner restoration of the registered worktree/commit permits later association and reconciliation-only retry. Both detailed plans record all six prior review rounds and the seventh correction. Corrections remain documentation-only; these prose checks do not enable execution or replace later code tests.
- Seventh correction head `e836ab513b2cab5a74ec4f1508045d9efbb1b6b8` received fresh independent approval with no unresolved Critical/Important findings. [GitKeeper #14](https://github.com/SumukhaK/GitKeeper/pull/14) merged at `45a16255e694c372f1b970ad532a72d352c8d3a0`; `main` is authoritative.

## Current PR slice verification

- A pre-merge review found and this slice addressed three issues: historical isolation evidence could authorize worker-enabled config; model IDs were included in remote config; and the UI described OS isolation as the pending gate.
- Regression tests were run RED before the fixes: 11 failed, exercising the unsafe report-based gate, remote model field, and inaccurate UI gate text.
- After the fixes, the focused UI tests passed: `py -m pytest tests/ui/test_config_publisher.py tests/ui/test_settings_viewmodel.py tests/ui/test_app.py tests/ui/test_qml_accessibility.py -q` — 28 passed.
- Full current suite: `py -m pytest -q` — 77 passed. `git diff --check` passed.
- Worker enablement is deliberately rejected by both the view model and config publisher until a future implementation proves companion-mediated OpenCode command approval. This slice does not implement that broker or authorize worker execution.
- This slice is merged as [GitKeeper #2](https://github.com/SumukhaK/GitKeeper/pull/2) at merge commit `d7ea534e04bf9b641ce040da2c4cff660baabce9`. The checks above ran on the pre-merge head; `main` is authoritative from here on, and nothing in this slice authorizes a worker launch.

## Model policy and patch-scope slice verification

- Tests were written first and observed failing before either module existed:
  `ModuleNotFoundError: No module named 'gitkeeper.policy'` for `tests/policy/`, and ladder
  assertions failing against the previous in-memory model field in
  `tests/ui/test_settings_viewmodel.py`.
- `tests/policy/test_models.py` (19 tests) covers ladder construction, exact-ID validation,
  non-`opencode/` rejection, duplicate and empty ladders, unknown/missing/unparseable policy
  files, `next_after` at the end of the ladder, and quota classification that never consumes
  the repair budget.
- `tests/policy/test_patch_scope.py` covers one case per reason code plus the exact 10-file,
  400-line, and 150-per-file boundaries, malformed paths, denylist precedence over the
  allowlist, and that the thresholds are not constructor-configurable.
- `tests/policy/test_manifest_scope.py` allows an attribute-value edit and rejects new
  permissions, new components, an added `<intent-filter>`, `android:exported`,
  `usesCleartextTraffic`, `debuggable`, `allowBackup`, and unparseable manifests.
- `tests/ui/test_settings_viewmodel.py` and `tests/ui/test_qml_accessibility.py` cover ladder
  exposure, owner-confirmed switching, rejection of off-ladder and other-provider IDs, the
  suggestion text, and that `can_enable_worker` stays `False`.
- `README.md` documents the current state, commands, the two code-enforced policy gates, the
  repository layout, and a reading order for the next contributor, so the repository is
  self-explanatory without this tracker.
- Full current suite: `py -m pytest -q` — 220 passed.
- Still unproven at that time: the git adapter that builds scope decisions from a real worktree, and the
  control-plane commit/push path. This slice adds policy only. (OpenCode command mediation has
  since been proven — see the Task 3 entry above.)

## Host readiness slice verification

- Tests were written first and observed failing without the implementation: with
  `src/gitkeeper/host_mode/` renamed away, `py -m pytest tests/host_mode -q` reported
  37 failed / 37 collected (`ModuleNotFoundError: No module named 'gitkeeper.host_mode'`);
  with it restored, 37 passed.
- `tests/host_mode/test_readiness.py` covers: `ACCEPTED_HOST_MODE` never reported as
  isolation; rejection of any other mode label; empty allowlist; unknown and non-`opencode/`
  models; unmapped policy keys; public exceptions and verified private repositories that are
  not allowlisted, and overlap between the two; unknown, case-mismatched, and
  privacy-unverified repositories; fork heads; non-owner and case-mismatched authors; closed
  and draft PRs; unmerged and disabled config; a remote config that tries to define a
  repository allowlist or model ladder; missing repair reason; unknown repair reason values;
  stale and missing check SHAs; exhausted budget and its explicit-retry override; missing
  ladder; wrong and missing CLI versions; unproven broker; a capability that self-reports a
  broker gap; aggregated blockers; `require_worker_ready`; and that no boolean or
  `IsolationReport` can be substituted for typed capability.
- `tests/isolation/test_report.py` gained two checks: the retired report module is documented
  as unable to authorize a launch, and a passing `IsolationReport` is rejected by
  `evaluate_worker_readiness` with `TypeError` in every argument position.
- Full current suite: `py -m pytest -q` — 220 passed. `git diff --check` passed.
- Deliberately not in this slice: the command broker, the approved-command UI, the OpenCode
  1.19.6 capability probe, and wiring `HostModeStatus` into `queue.py`/`settings.py`. Worker
  launch therefore stays disabled.

## Command approval broker slice verification

- Contract first: `tests/host_mode/test_command_broker.py` was written and observed failing
  (`ModuleNotFoundError: No module named 'gitkeeper.host_mode.command_broker'`) before
  `command_broker.py` existed. It now holds 66 tests covering approve-once, deny, timeout,
  UI disconnect, partial or empty presentation, changed argv/script/cwd/repo/PR/SHA/env
  names, a hand-built or foreign decision, replay, token `TTL`, stale remote facts before
  presentation, after approval, and at execution, `ExecutionUnavailableError` from the
  disabled default executor, and audit metadata that contains no argv, script, env name, or
  cwd.
- An implementation defect was found and closed while writing the UI tests: remote-state
  matching did not compare `head_repository`, so a fork head with a matching SHA could have
  been approved. Approval now requires `observed.repository == observed.head_repository ==
  request.repository`, and `test_a_fork_head_is_never_approved_or_executed` pins it.
- `tests/ui/test_command_approval.py` (26 tests) covers the `ApprovalPort` conformance,
  every rendered line shown as plain text, header parsing from the rendered lines only,
  explicit-action-only decisions, no-answer denial, never claiming execution, refusing to
  overwrite a pending request, refusing an empty presentation, screen-reader text, motion
  preferences, and two broker/view-model interlock tests.
- Six QML tests were added to `tests/ui/test_qml_accessibility.py` and observed failing
  against the previous `HandscrollPanel.qml`/`Main.qml` (5 failed, `commandApprovalDialog`
  absent); after wiring the dialog and the `appCommandApprovalViewModel` context property,
  `py -m pytest tests/ui/test_qml_accessibility.py -q` — 17 passed.
- Full current suite: `py -m pytest -q` — 318 passed at the approval-broker merge; `365 passed`
  after the Task 3 mediation work. `py -m ruff check` on the new and
  changed files reports only the file's pre-existing findings (the repository still has no
  ruff configuration, so these are not merge gates).
- Deliberately not in this slice: any executor. `execution_disabled` is the only executor and
  refuses every launch, so an approval can never be turned into a process run here.
- This slice is merged as [GitKeeper #8](https://github.com/SumukhaK/GitKeeper/pull/8) at
  merge commit `9f9603dc234c7d746541e06171bb10b10548efcd`; `main` is authoritative from here
  on, and branch tips may drift.

## Approved decisions that must not be re-litigated

1. GitKeeper application, companion, and worker code stay in private `SumukhaK/GitKeeper`. Setu-mobile receives only the Actions integration and its config.
2. Setu-mobile is public. PR code is untrusted: use `pull_request`, not `pull_request_target`; CI is secret-free/read-only; a label-writing job must not check out, download, or execute PR-controlled content.
3. Only owner-allowlisted repositories and PRs authored by `SumukhaK` are worker-eligible. Forks/external PRs receive CI only.
4. OpenCode is the first worker CLI. Only exact `opencode/...` subscription IDs on the local ladder are allowed; the ladder and active ID are local settings and never published. Quota exhaustion blocks the PR as `model-unavailable` and only the owner switches models - never an automatic fallback.
5. Each PR/SHA gets its own Git worktree so the active working tree is not overwritten. This is repository-state hygiene, not a host security boundary.
6. Every worker-requested host shell command requires fresh, explicit desktop approval bound to the complete command/script, working directory, repository, PR/head SHA, purpose, and environment variable names. Any mismatch, timeout, UI disconnect, or incomplete display denies execution.
7. GitHub is authoritative for remote PR/queue state. SQLite state and telemetry live outside both repositories. Telemetry stores operational metadata only, defaults to 90-day retention, and never drives automatic self-modification.
8. PR close cancels work; reopening starts a fresh automatic-attempt cycle; a new head SHA cancels stale work and queues the new revision. Allow at most three automatic invocations per open/reopen cycle; escalation requires explicit user retry.
9. Configuration changes are validated and published as a config PR. Actions reads only merged/default-branch config. Local host/repository/model policy is never published in Setu-mobile config.

## Repository housekeeping on every push

Standing owner instruction: whenever work is pushed to `SumukhaK/GitKeeper`, also keep the
repository front page accurate.

1. Update the GitHub About section (`gh repo edit --description ... --add-topic ...`) when the
   project description or scope changed.
2. Update `README.md` so its status table, commands, and layout still match the code. The
   README must keep saying plainly that worker execution is disabled: mediation is proven, but
   no executor is wired, and source-plan Tasks 3–5 remain outside the active queue slice.
3. Keep this tracker's "Current state" PR list current, so a new agent knows which slice
   `main` already contains.

## Ordered handoff plan

Follow the detailed task lists in the linked plans, in this order. The active queue scope is the [queue persistence and reconciliation plan](2026-11-25-gitkeeper-queue-persistence-reconciliation.md):

1. **Host readiness and local policy:** implement typed readiness that distinguishes accepted risk from readiness. Use the now-resolved model ladder (`gitkeeper.policy.models`) and the resolved patch-scope rule (`gitkeeper.policy.patch_scope`). Enforce the exact repository allowlist, ladder membership, same-repository head, exact author, open/non-draft state, merged config, current SHA, attempt budget, CLI version, and broker capability. **Done:** `src/gitkeeper/host_mode/readiness.py` plus `tests/host_mode/test_readiness.py`; wiring into the UI and queue is item 6.
2. **Approval broker and UI:** add immutable request display and one-use approve/deny handling. Deny by default on timeout, disconnect, stale PR/SHA, request changes, replay, or incomplete rendering. **Done:** `src/gitkeeper/host_mode/command_broker.py`, `src/gitkeeper/ui/viewmodels/command_approval.py`, `src/gitkeeper/ui/qml/CommandApprovalDialog.qml`, with 98 focused tests. Execution itself is still unimplemented on purpose.
3. **OpenCode capability proof:** test the exact installed `1.19.6` path with the companion UI in a disposable non-repository directory. Verify approval, denial, request mutation, and bypass resistance. If any shell path can bypass approval, record `UNSUPPORTED`/NO-GO and keep worker launch disabled. **Done:** merged in [GitKeeper #10](https://github.com/SumukhaK/GitKeeper/pull/10); execution itself remains disabled.
4. **Queue domain, persistence, telemetry, and GitHub reconciliation — active next:** finish P0's docs PR, then execute slices 1–7 sequentially with a fresh implementer and reviewer per code slice. Implement domain contracts, SQLite queue/cycle/lease persistence, operational-only telemetry, GitHub reads/labels, queue service and budget transitions, restart/close/reopen reconciliation, and adaptive outbound polling. Merge only after clean review and passing tests, recording PR/merge evidence before the next slice. P0's initial independent review passed; combined [#14](https://github.com/SumukhaK/GitKeeper/pull/14) has corrections pending fresh approval, and no queue code is merged. After the queue slices, independently review and execute the separate [worktree, runner, and service plan](2026-11-25-gitkeeper-worktree-runner-service.md) for source-plan Tasks 3–5. Worktrees, worker execution, publication, and UI integration remain outside the active slice and disabled.
5. **Publication policy:** the rule is defined and enforced by `PatchScopePolicy`. Build the git adapter that feeds it (staged raw/numstat diff, both blob revisions, unstaged/untracked detection), then the control-plane commit and fast-forward-only push to the existing PR branch. On any violation, preserve the worktree, block, and require explicit retry.
6. **Companion integration:** wire real services into the existing UI, retain accessible/reduced-motion behavior, and surface blocked/error state without false success.
7. **Verification:** run focused tests during each slice, then `py -m pytest -q`; run `node --test` and `actionlint` in the Setu-mobile repository as appropriate. Verify GitHub event/label behavior and manually exercise the UI on Windows. Do not claim end-to-end completion until those checks pass.

## Repository map

- `docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md` — approved product/security contract.
- `docs/superpowers/plans/2026-11-20-gitkeeper-host-mode-readiness.md` — host-risk, readiness, command broker, and OpenCode capability tasks.
- `docs/superpowers/plans/2026-11-20-gitkeeper-local-queue-worker.md` — original queue, SQLite, worktree, worker, lifecycle, and publication tasks.
- `docs/superpowers/plans/2026-11-25-gitkeeper-queue-persistence-reconciliation.md` — active queue domain, persistence, telemetry, and GitHub reconciliation slices; each code slice requires fresh independent review before merge.
- `docs/superpowers/plans/2026-11-25-gitkeeper-worktree-runner-service.md` — detailed source-plan Tasks 3–5 handoff for worktree lifecycle, gated runner/publication, and integrated UI service; planning only; [#14](https://github.com/SumukhaK/GitKeeper/pull/14) corrections await fresh independent approval.
- `docs/superpowers/plans/2026-11-20-gitkeeper-companion-ui.md` — companion view models, pet/panel, config UI, telemetry, and integration tasks.
- `docs/superpowers/plans/2026-11-20-setu-mobile-actions-config.md` — Actions/config implementation and live verification tasks.
- `docs/superpowers/isolation/windows-prototype-result.md` — original isolation NO-GO evidence and its accepted-host-mode interpretation. Preserve the NO-GO result; do not rewrite it as a pass.
- `src/gitkeeper/host_mode/readiness.py` — the single launch gate for `ACCEPTED_HOST_MODE`; read this before touching worker startup.
- `.superpowers/sdd/` — local, ignored execution ledgers may contain more detailed per-task notes, but are not the durable handoff source. Keep this tracked document and the plans accurate for remote handoff.
