# GitKeeper Setu-mobile Pilot — Handover Tracker

**Last updated:** 2026-11-20  
**Purpose:** Give a new engineer or AI agent enough durable context to continue the pilot without reconstructing decisions from chat history.

## Current state

- The approved architecture and scope are in [the pilot design spec](../specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md).
- The original design-only PR, [GitKeeper #1](https://github.com/SumukhaK/GitKeeper/pull/1), is merged; it contained only the design spec.
- The GitKeeper implementation slice is merged: [GitKeeper #2](https://github.com/SumukhaK/GitKeeper/pull/2), merge commit `d7ea534e04bf9b641ce040da2c4cff660baabce9` on `main`, from branch `sumukhak-gitkeeper-pilot-design`. It adds the companion UI foundation, the fail-closed worker/config gate, and this tracker. Treat `main` as the source of truth; branch tips may drift.
- The model-ladder and patch-scope policy slice plus `README.md` are merged: [GitKeeper #4](https://github.com/SumukhaK/GitKeeper/pull/4), merge commit `9b3213fdca9f618e018fa2dbc797be067d2dc47f`.
- The repository housekeeping rule (README/About/tracker updates on every push) is merged: [GitKeeper #5](https://github.com/SumukhaK/GitKeeper/pull/5), merge commit `cab09d8918468bd4a18101fba94f9f626b5f4eae`.
- Typed host-mode readiness is merged: [GitKeeper #6](https://github.com/SumukhaK/GitKeeper/pull/6), merge commit `ca34a45`, which adds `src/gitkeeper/host_mode/readiness.py` and `tests/host_mode/test_readiness.py` and retires the historical isolation report as an authorization input.
- Setu-mobile Actions/config implementation is in [Setu-mobile #19](https://github.com/SumukhaK/Setu-mobile/pull/19), head `a153271a18241b3cf5b9d5ed7025916c6b11e9f8`. Its `Android CI` and `GitGuardian Security Checks` passed on that head; the PR is open and unmerged.
- **Worker execution is not ready and must remain disabled.** `ACCEPTED_HOST_MODE` records the owner's acceptance of host risk; it is not proof of isolation and does not by itself authorize a worker launch.

## Completion tracker

| Area | Status | Evidence / remaining work |
|---|---|---|
| Pilot architecture and boundaries | Complete | The accepted spec defines GitHub Actions, local companion/worker responsibilities, trusted/untrusted boundaries, state transitions, telemetry, and pilot acceptance criteria. |
| Companion UI foundation | Complete, integration pending | The branch contains queue/status view models, fox/handscroll UI, settings/config-PR UI, manual feedback, telemetry view, and focused tests. Current working-tree verification: `py -m pytest -q` — 220 passed. Real queue/worker and host-readiness wiring is still pending. |
| Setu-mobile Actions/config | Implemented; remote runtime partly verified | PR #19 contains strict config validation, secret-free PR CI, and a default-branch state classifier/label writer. Both current PR checks pass. The state workflow's label behavior and fork-PR runtime behavior still need live verification after the workflow is on the default branch. Do not merge this PR without the owner's review/authorization. |
| Accepted Windows host mode | Decision accepted; typed readiness implemented | The owner accepted direct execution on their Windows 10 Home account and waived VM/WSL2/Sandbox and separate-user boundaries. `src/gitkeeper/host_mode/readiness.py` now computes readiness from typed facts and names every refusal: allowlist, privacy, fork head, exact author, open/non-draft, merged config, repair reason, stale check SHA, attempt budget, local model ladder, CLI 1.19.6, and proven command broker. Config publication still rejects `worker_enabled: true`, the historical isolation report is documented as evidence only, and `gitkeeper.isolation` cannot gate host mode. Still pending: wiring this status into the UI/queue, the broker, and the CLI capability probe. Never call this isolation or a sandbox. |
| OpenCode command mediation | Not proven; blocking | The installed CLI is OpenCode `1.19.6`. `opencode run` is not an acceptable approval path: it rejects permission requests by default, while `--auto` auto-approves them. A companion-owned `opencode serve` permission-event/reply path is only a candidate until it is exercised through the UI and shown to fail closed. |
| Local queue and worker | Not implemented | Build SQLite-backed leases/telemetry, GitHub reconciliation, label-state transitions, per-PR/SHA worktrees, cancellation, stale-head handling, and the three-invocation budget per open/reopen cycle. Keep stale worktrees for user recovery. |
| Worker publication | Policy resolved; enforcement implemented | The owner delegated and approved a machine-checkable patch-scope rule, now recorded in the spec section "Patch scope rule for publication" and enforced by `src/gitkeeper/policy/patch_scope.py`. It fails closed with enumerated reason codes: worktree identity, 10 files / 400 total lines / 150 per-file limits, no deletions/renames/submodules/symlinks/binary/mode changes, allowlisted paths with a denylist that wins, a manifest capability rule, test-change and secret-scan intent checks, then a remote re-query before a fast-forward-only push. The thresholds are code constants so a config PR cannot widen them. Still missing: the git adapter that builds these decisions from a real worktree, and the control-plane commit/push step. |
| Local OpenCode model policy | Policy resolved; persistence implemented | The owner supplied the ladder: default `opencode/deepseek-v4.1-flash-free`, then `opencode/glm-5.3`, `opencode/glm-5.3-flash`, `opencode/hy4-flash-free`, `opencode/deepseek-v4-pro`. `src/gitkeeper/policy/models.py` holds the ladder, validates every ID, persists it atomically to `%LOCALAPPDATA%\GitKeeper\models.json` (outside both repositories), and classifies quota/rate-limit output as `model-unavailable` without consuming the invocation budget. The settings panel exposes the ladder, the active ID, and the next-entry suggestion; switching is owner-confirmed only. Still missing: the launch path that reads this policy and the `models.json` bootstrap on a fresh host. |
| GitHub credential provider | Undecided | Use an injected least-privilege GitHub service. Do not invent a token location or expose credentials to QML. In accepted host mode, credentials accessible to the logged-in user may also be accessible to OpenCode or its child processes. |
| End-to-end/manual verification | Pending | Verify UI behavior on Windows, command approval/denial/replay, worktree invariants, GitHub label transitions, config PR publication, and Actions behavior. Do not run Android Gradle checks locally; GitHub Actions is the test gate. |

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
- Still unproven: the git adapter that builds scope decisions from a real worktree, and the
  control-plane commit/push path. No worker may launch until OpenCode command mediation is
  proven; this slice adds policy only.

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
   README must keep saying plainly that worker execution is disabled until the OpenCode
   command broker is proven.
3. Keep this tracker's "Current state" PR list current, so a new agent knows which slice
   `main` already contains.

## Ordered handoff plan

Follow the detailed task lists in the linked plans, in this order:

1. **Host readiness and local policy:** implement typed readiness that distinguishes accepted risk from readiness. Use the now-resolved model ladder (`gitkeeper.policy.models`) and the resolved patch-scope rule (`gitkeeper.policy.patch_scope`). Enforce the exact repository allowlist, ladder membership, same-repository head, exact author, open/non-draft state, merged config, current SHA, attempt budget, CLI version, and broker capability. **Done:** `src/gitkeeper/host_mode/readiness.py` plus `tests/host_mode/test_readiness.py`; wiring into the UI and queue is item 6.
2. **Approval broker and UI:** add immutable request display and one-use approve/deny handling. Deny by default on timeout, disconnect, stale PR/SHA, request changes, replay, or incomplete rendering.
3. **OpenCode capability proof:** test the exact installed `1.19.6` path with the companion UI in a disposable non-repository directory. Verify approval, denial, request mutation, and bypass resistance. If any shell path can bypass approval, record `UNSUPPORTED`/NO-GO and keep worker launch disabled.
4. **Queue/worker:** implement SQLite leases and recovery, GitHub polling/reconciliation, label states, per-PR/SHA worktrees, stale-head/close cancellation, bounded attempts, and explicit retry. Preserve worktrees on crash/rate-limit/escalation.
5. **Publication policy:** the rule is defined and enforced by `PatchScopePolicy`. Build the git adapter that feeds it (staged raw/numstat diff, both blob revisions, unstaged/untracked detection), then the control-plane commit and fast-forward-only push to the existing PR branch. On any violation, preserve the worktree, block, and require explicit retry.
6. **Companion integration:** wire real services into the existing UI, retain accessible/reduced-motion behavior, and surface blocked/error state without false success.
7. **Verification:** run focused tests during each slice, then `py -m pytest -q`; run `node --test` and `actionlint` in the Setu-mobile repository as appropriate. Verify GitHub event/label behavior and manually exercise the UI on Windows. Do not claim end-to-end completion until those checks pass.

## Repository map

- `docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md` — approved product/security contract.
- `docs/superpowers/plans/2026-11-20-gitkeeper-host-mode-readiness.md` — host-risk, readiness, command broker, and OpenCode capability tasks.
- `docs/superpowers/plans/2026-11-20-gitkeeper-local-queue-worker.md` — queue, SQLite, worktree, worker, lifecycle, and publication tasks.
- `docs/superpowers/plans/2026-11-20-gitkeeper-companion-ui.md` — companion view models, pet/panel, config UI, telemetry, and integration tasks.
- `docs/superpowers/plans/2026-11-20-setu-mobile-actions-config.md` — Actions/config implementation and live verification tasks.
- `docs/superpowers/isolation/windows-prototype-result.md` — original isolation NO-GO evidence and its accepted-host-mode interpretation. Preserve the NO-GO result; do not rewrite it as a pass.
- `src/gitkeeper/host_mode/readiness.py` — the single launch gate for `ACCEPTED_HOST_MODE`; read this before touching worker startup.
- `.superpowers/sdd/` — local, ignored execution ledgers may contain more detailed per-task notes, but are not the durable handoff source. Keep this tracked document and the plans accurate for remote handoff.
