# GitKeeper Setu-mobile Pilot — Handover Tracker

**Last updated:** 2026-11-20  
**Purpose:** Give a new engineer or AI agent enough durable context to continue the pilot without reconstructing decisions from chat history.

## Current state

- The approved architecture and scope are in [the pilot design spec](../specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md).
- GitKeeper implementation work is on branch `sumukhak-gitkeeper-pilot-design`. The original design-only PR, [GitKeeper #1](https://github.com/SumukhaK/GitKeeper/pull/1), is merged; it did not include the implementation work described here.
- A separate GitKeeper implementation PR is being prepared from the changes after `6af9283`. Update this tracker with its number, URL, merge commit, and final verification before handing the work off.
- Setu-mobile Actions/config implementation is in [Setu-mobile #19](https://github.com/SumukhaK/Setu-mobile/pull/19), head `a153271a18241b3cf5b9d5ed7025916c6b11e9f8`. Its `Android CI` and `GitGuardian Security Checks` passed on that head; the PR is open and unmerged.
- **Worker execution is not ready and must remain disabled.** `ACCEPTED_HOST_MODE` records the owner's acceptance of host risk; it is not proof of isolation and does not by itself authorize a worker launch.

## Completion tracker

| Area | Status | Evidence / remaining work |
|---|---|---|
| Pilot architecture and boundaries | Complete | The accepted spec defines GitHub Actions, local companion/worker responsibilities, trusted/untrusted boundaries, state transitions, telemetry, and pilot acceptance criteria. |
| Companion UI foundation | Complete, integration pending | The branch contains queue/status view models, fox/handscroll UI, settings/config-PR UI, manual feedback, telemetry view, and focused tests. Current working-tree verification: `py -m pytest -q` — 77 passed. Real queue/worker and host-readiness wiring is still pending. |
| Setu-mobile Actions/config | Implemented; remote runtime partly verified | PR #19 contains strict config validation, secret-free PR CI, and a default-branch state classifier/label writer. Both current PR checks pass. The state workflow's label behavior and fork-PR runtime behavior still need live verification after the workflow is on the default branch. Do not merge this PR without the owner's review/authorization. |
| Accepted Windows host mode | Decision accepted; fail-closed UI gate in progress | The owner accepted direct execution on their Windows 10 Home account and waived VM/WSL2/Sandbox and separate-user boundaries. Config publication now rejects `worker_enabled: true` until OpenCode command mediation is proven; the historical isolation report is no longer an authorization input. Typed readiness, repository allowlist, broker, and end-to-end tests remain pending. Never call this isolation or a sandbox. |
| OpenCode command mediation | Not proven; blocking | The installed CLI is OpenCode `1.19.6`. `opencode run` is not an acceptable approval path: it rejects permission requests by default, while `--auto` auto-approves them. A companion-owned `opencode serve` permission-event/reply path is only a candidate until it is exercised through the UI and shown to fail closed. |
| Local queue and worker | Not implemented | Build SQLite-backed leases/telemetry, GitHub reconciliation, label-state transitions, per-PR/SHA worktrees, cancellation, stale-head handling, and the three-invocation budget per open/reopen cycle. Keep stale worktrees for user recovery. |
| Worker publication | Blocked on owner policy | The owner has not supplied a machine-checkable patch-scope rule. Do not infer one from prompts or changed paths. Publication must fail closed until the rule is defined and tested; retain exact starting-SHA checks and fast-forward-only publication to the existing PR branch. |
| Local OpenCode model policy | Partial; persistence and exact local policy pending | The model field is now local to the companion, rejects non-`opencode/` IDs, and is omitted from remote config PRs. The selected ID is currently in-memory only; wire it to an owner-controlled exact allowlist/persistence before worker launch. Do not guess IDs or permit other providers. |
| GitHub credential provider | Undecided | Use an injected least-privilege GitHub service. Do not invent a token location or expose credentials to QML. In accepted host mode, credentials accessible to the logged-in user may also be accessible to OpenCode or its child processes. |
| End-to-end/manual verification | Pending | Verify UI behavior on Windows, command approval/denial/replay, worktree invariants, GitHub label transitions, config PR publication, and Actions behavior. Do not run Android Gradle checks locally; GitHub Actions is the test gate. |

## Current PR slice verification

- A pre-merge review found and this slice addressed three issues: historical isolation evidence could authorize worker-enabled config; model IDs were included in remote config; and the UI described OS isolation as the pending gate.
- Regression tests were run RED before the fixes: 11 failed, exercising the unsafe report-based gate, remote model field, and inaccurate UI gate text.
- After the fixes, the focused UI tests passed: `py -m pytest tests/ui/test_config_publisher.py tests/ui/test_settings_viewmodel.py tests/ui/test_app.py tests/ui/test_qml_accessibility.py -q` — 28 passed.
- Full current suite: `py -m pytest -q` — 77 passed. `git diff --check` passed.
- Worker enablement is deliberately rejected by both the view model and config publisher until a future implementation proves companion-mediated OpenCode command approval. This slice does not implement that broker or authorize worker execution.

## Approved decisions that must not be re-litigated

1. GitKeeper application, companion, and worker code stay in private `SumukhaK/GitKeeper`. Setu-mobile receives only the Actions integration and its config.
2. Setu-mobile is public. PR code is untrusted: use `pull_request`, not `pull_request_target`; CI is secret-free/read-only; a label-writing job must not check out, download, or execute PR-controlled content.
3. Only owner-allowlisted repositories and PRs authored by `SumukhaK` are worker-eligible. Forks/external PRs receive CI only.
4. OpenCode is the first worker CLI. The exact model ID is a local user-selectable setting; only exact `opencode/...` subscription IDs are allowed.
5. Each PR/SHA gets its own Git worktree so the active working tree is not overwritten. This is repository-state hygiene, not a host security boundary.
6. Every worker-requested host shell command requires fresh, explicit desktop approval bound to the complete command/script, working directory, repository, PR/head SHA, purpose, and environment variable names. Any mismatch, timeout, UI disconnect, or incomplete display denies execution.
7. GitHub is authoritative for remote PR/queue state. SQLite state and telemetry live outside both repositories. Telemetry stores operational metadata only, defaults to 90-day retention, and never drives automatic self-modification.
8. PR close cancels work; reopening starts a fresh automatic-attempt cycle; a new head SHA cancels stale work and queues the new revision. Allow at most three automatic invocations per open/reopen cycle; escalation requires explicit user retry.
9. Configuration changes are validated and published as a config PR. Actions reads only merged/default-branch config. Local host/repository/model policy is never published in Setu-mobile config.

## Ordered handoff plan

Follow the detailed task lists in the linked plans, in this order:

1. **Host readiness and local policy:** implement typed readiness that distinguishes accepted risk from readiness. Enforce exact repository and model allowlists, same-repository head, exact author, open/non-draft state, merged config, current SHA, attempt budget, CLI version, and broker capability.
2. **Approval broker and UI:** add immutable request display and one-use approve/deny handling. Deny by default on timeout, disconnect, stale PR/SHA, request changes, replay, or incomplete rendering.
3. **OpenCode capability proof:** test the exact installed `1.19.6` path with the companion UI in a disposable non-repository directory. Verify approval, denial, request mutation, and bypass resistance. If any shell path can bypass approval, record `UNSUPPORTED`/NO-GO and keep worker launch disabled.
4. **Queue/worker:** implement SQLite leases and recovery, GitHub polling/reconciliation, label states, per-PR/SHA worktrees, stale-head/close cancellation, bounded attempts, and explicit retry. Preserve worktrees on crash/rate-limit/escalation.
5. **Publication policy:** ask the owner to define the machine-checkable patch-scope rule. Until then, do not publish worker changes. Once defined, enforce it alongside exact remote-head matching and fast-forward-only push to the existing PR branch.
6. **Companion integration:** wire real services into the existing UI, retain accessible/reduced-motion behavior, and surface blocked/error state without false success.
7. **Verification:** run focused tests during each slice, then `py -m pytest -q`; run `node --test` and `actionlint` in the Setu-mobile repository as appropriate. Verify GitHub event/label behavior and manually exercise the UI on Windows. Do not claim end-to-end completion until those checks pass.

## Repository map

- `docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md` — approved product/security contract.
- `docs/superpowers/plans/2026-11-20-gitkeeper-host-mode-readiness.md` — host-risk, readiness, command broker, and OpenCode capability tasks.
- `docs/superpowers/plans/2026-11-20-gitkeeper-local-queue-worker.md` — queue, SQLite, worktree, worker, lifecycle, and publication tasks.
- `docs/superpowers/plans/2026-11-20-gitkeeper-companion-ui.md` — companion view models, pet/panel, config UI, telemetry, and integration tasks.
- `docs/superpowers/plans/2026-11-20-setu-mobile-actions-config.md` — Actions/config implementation and live verification tasks.
- `docs/superpowers/isolation/windows-prototype-result.md` — original isolation NO-GO evidence and its accepted-host-mode interpretation. Preserve the NO-GO result; do not rewrite it as a pass.
- `.superpowers/sdd/` — local, ignored execution ledgers may contain more detailed per-task notes, but are not the durable handoff source. Keep this tracked document and the plans accurate for remote handoff.
