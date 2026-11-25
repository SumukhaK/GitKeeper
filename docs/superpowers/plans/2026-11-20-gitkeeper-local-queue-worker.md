# GitKeeper Local Queue and Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the private GitKeeper companion's durable local queue, owner-allowlisted PR lifecycle reconciliation, human-confirmed host-mode OpenCode execution, and validated fast-forward publication flow.

**Architecture:** GitKeeper now has a Python/PySide6 package scaffold. Keep the GitHub control plane and SQLite lease/state in the companion process. OpenCode CLI `1.19.6` runs as the owner on Windows 10 Home in the explicitly accepted `ACCEPTED_HOST_MODE`; there is no OS sandbox or separate-user credential boundary. Require an owner allowlist, exact `opencode/...` subscription model allowlist, PR-specific worktree, and a companion-mediated one-shot user approval for every worker-requested shell command. The worker plan cannot launch OpenCode until the host-mode plan proves complete command interception. The companion validates patch scope and unchanged starting head SHA before committing/pushing; GitHub Actions alone runs the Android test gate after the push.

**Tech Stack:** Python, SQLite, OpenCode CLI `1.19.6`, GitHub REST API, pytest, PySide6/QML approval broker.

**Spec:** `docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md`

## Execution status

The active queue/persistence/reconciliation scope is divided into slices that each require fresh independent review in the [detailed queue plan](2026-11-25-gitkeeper-queue-persistence-reconciliation.md). The initial P0 documentation draft passed its original review; the combined Tasks 1–5 documentation in [PR #14](https://github.com/SumukhaK/GitKeeper/pull/14) is now in correction/re-review and must not merge until a fresh reviewer approves the exact current head. No queue code is merged or independently reviewed.

| Original task | Detailed plan / slices | Status | PR | Merge commit |
| --- | --- | --- | --- | --- |
| Task 1 | [Queue plan](2026-11-25-gitkeeper-queue-persistence-reconciliation.md), slices 1–3 | pending | — | — |
| Task 2 | [Queue plan](2026-11-25-gitkeeper-queue-persistence-reconciliation.md), slices 4–7 | pending | — | — |
| Task 3 | [Worktree, runner, and service plan](2026-11-25-gitkeeper-worktree-runner-service.md) | planned; fresh approval required in PR #14 | [#14](https://github.com/SumukhaK/GitKeeper/pull/14) | — |
| Task 4 | [Worktree, runner, and service plan](2026-11-25-gitkeeper-worktree-runner-service.md) | planned; fresh approval required in PR #14 | [#14](https://github.com/SumukhaK/GitKeeper/pull/14) | — |
| Task 5 | [Worktree, runner, and service plan](2026-11-25-gitkeeper-worktree-runner-service.md) | planned; fresh approval required in PR #14 | [#14](https://github.com/SumukhaK/GitKeeper/pull/14) | — |

Original Tasks 1–2 remain unchecked until all mapped slices have merged code and clean review/test evidence. Tasks 3–5 have a detailed handoff plan but remain unchecked and outside the active queue slice. Worker execution stays disabled, with no worktree, OpenCode launch, publication, UI/settings integration, workflow, or Setu-mobile changes authorized.

## Global Constraints

- GitKeeper application, companion, and worker code belong only in the private GitKeeper repository. Setu-mobile receives only the GitHub Actions integration and its configuration.
- PR code is untrusted. Actions uses `pull_request`, never `pull_request_target`; the privileged label-writing workflow does not check out PR code, download PR artifacts, or execute PR-controlled content.
- GitHub Actions is the sole test gate and runs `./gradlew testDebugUnitTest detekt lint` on PR `opened` and `synchronize`, and again for every companion push to the PR branch.
- The companion and worker do not run the required test suite locally as a prerequisite to publishing.
- PR code is untrusted. Every worker invocation uses a dedicated PR worktree outside the GitKeeper source checkout, companion installation, and user's active repository checkout. A worktree protects repository state, not host resources.
- The owner has accepted host execution on Windows 10 Home and waived OS isolation and a separate-user credential boundary. OpenCode and commands run as the owner and may access resources available to that user; do not describe host mode as isolated.
- Readiness requires the exact owner-managed repository allowlist. `SumukhaK/Setu-mobile` is the explicit public exception; other repositories must be verified private and owner-controlled with the required Actions/config contract. PR heads must be in the same repository; forks and external PRs are never eligible.
- Readiness also requires a non-empty local allowlist of exact OpenCode subscription IDs matching `opencode/...`, and detected CLI version `1.19.6`. Do not guess IDs, publish them to Setu-mobile, or fall back to another provider.
- Every shell command requested by OpenCode requires explicit one-shot companion confirmation of the complete command and execution context. The approval UI must be available, complete, and bound to the exact request. No approval carry-over, background execution, or direct bypass is allowed.
- Prompt instructions and allowlists are application policy inputs, not a sandbox or security boundary. The owner accepts the residual host risk.
- The PR author must be exactly `SumukhaK`, the PR head repository must be `SumukhaK/Setu-mobile`, the PR must be open/non-draft, and merged config must enable work.
- GitHub labels are `gitkeeper/worker-queued`, `gitkeeper/worker-running`, and `gitkeeper/worker-blocked`; labels are projections, not locks. Actions classifies CI outcomes and sets/clears queued or CI-blocked state. The companion sets running after an atomic local lease claim and may set blocked for local worker/escalation failures; it clears obsolete labels after completion/cancellation and reconciles with current CI state.
- A deterministic check failure or user-submitted Claude `CHANGES_REQUESTED` feedback after checks pass can queue repair. Passing checks alone mean ready/no worker. Infrastructure, cancellation, missing, or unknown outcomes block for human action.
- The user manually transfers Claude feedback through the companion; there is no automated Claude access, approval, final architectural judgment, or merge.
- The worker prompt includes the user's branching, test-driven-development, and framework-sanity expectations. These are behavioral guidance, not security enforcement.
- The automatic invocation budget is three per open/reopen cycle across head-SHA changes. Reopen starts a fresh budget while preserving history. Explicit retries after escalation are separate and user-triggered.
- OpenCode CLI `1.19.6` is the only worker; the model ID must be an exact entry in the local owner-configured `opencode/...` subscription allowlist. Do not guess IDs. There is no automatic model fallback.
- Before publishing, the companion validates patch structure/scope and confirms remote PR head SHA still equals the worker's starting SHA; it does not run tests locally.
- The companion/control plane commits and makes a normal fast-forward push to the existing PR branch. It does not intentionally inject its GitHub token into OpenCode; same-user accessible credentials may still be reachable in host mode. Never force-push or push to `main`.
- GitHub Actions runs the Android checks after every companion push; deterministic failures requeue within budget, and infrastructure/unknown/cancelled outcomes block.
- SQLite, worktrees, and local worker state live outside both repositories. Telemetry retains operational metadata for 90 days by default and stores no source, diffs, prompts, credentials, screenshots, or full OpenCode input/output.
- Telemetry retention may be shortened by the user but must never exceed 90 days. Analytics do not automatically modify prompts, rules, or product behavior.

## Review Focus

- Duplicate claims after concurrent poll/restart must be impossible: Task 2 tests atomic lease uniqueness and expired lease recovery.
- Stale or closed PR work must not publish: Task 4 tests a head change between worker completion and push; Task 3 covers closure cancellation.
- Control-plane credentials are not intentionally injected, same-user host risk is disclosed, and each shell command waits for exact approval: Task 4 tests token non-injection and approval binding; the host-mode plan tests the CLI mediation boundary.
- Non-allowlisted repos, forks, non-owner authors, unsupported CLI versions, and non-allowlisted models never produce a claim: Task 2 classification tests assert no claim.
- CI outcome classification and per-cycle retry/reopen accounting remain exact: Tasks 2–3 test deterministic versus unknown failures, three attempts, explicit retries, and history-preserving reopen.

---

## Planned file inventory (existing scaffold and additions)

- Modify: `pyproject.toml` — extend the existing package/test scaffold; no duplicate project metadata.
- Create: `src/gitkeeper/domain/models.py` — PR, check, repair, queue, and event types.
- Create: `src/gitkeeper/github/port.py` — typed GitHub-control-plane interface.
- Create: `src/gitkeeper/github/rest.py` — authenticated control-plane REST client; token provider stays outside worker launch.
- Create: `src/gitkeeper/github/poller.py` — outbound adaptive polling and reconciliation scheduler; no inbound listener.
- Create: `src/gitkeeper/queue/store.py` — SQLite schema/transactions outside both repositories.
- Create: `src/gitkeeper/queue/service.py` — eligibility, labels, queue state, leases, retry-cycle policy.
- Create: `src/gitkeeper/worker/worktree.py` — PR-specific worktree lifecycle.
- Create: `src/gitkeeper/worker/opencode.py` — selected-model invocation through typed host-mode readiness and verified command-broker capability.
- Create: `src/gitkeeper/worker/publish.py` — diff/scope validation, head-SHA compare, companion-owned commit and fast-forward push.
- Consume: `src/gitkeeper/host_mode/readiness.py` and `command_broker.py` from the host-mode readiness plan; fail closed when owner policy or full shell mediation is not ready.
- Create: `src/gitkeeper/telemetry/store.py` — operational metadata and 90-day expiry.
- Create: `tests/unit/domain/test_models.py`, `tests/unit/queue/test_store.py`, `tests/unit/queue/test_service.py`, `tests/unit/worker/test_worktree.py`, `tests/unit/worker/test_publish.py`, `tests/unit/worker/test_opencode.py`, `tests/unit/telemetry/test_store.py`, `tests/unit/github/test_poller.py`.
- Create: `tests/integration/test_github_reconciliation.py`, `tests/integration/test_worker_lifecycle.py`.
- Existing baseline: `pyproject.toml`, the `gitkeeper` package, partial companion/isolation modules, and focused UI/isolation tests are present. Queue, SQLite persistence, GitHub polling/API adapters, worktree manager, worker runner, publisher, and telemetry store remain unimplemented; extend existing package metadata and tests rather than scaffolding a duplicate project.

## Interfaces

- **Consumes from Actions/config plan:** GitHub PR snapshot and current head SHA; required check identities/conclusions; the three worker labels; strict merged-default-branch config with `worker_enabled` and recognized check IDs. Model IDs and selected model come only from the companion's local owner policy.
- **Consumes from host-mode readiness plan:** `evaluate_worker_readiness(...) -> HostModeStatus`, `OpenCodeCapability.verify(...)`, and `ApprovalBroker`; no worker launch occurs until all report ready and every shell request obtains exact one-shot approval.
- **GitHub control-plane port:** `get_pull_request(repo: str, number: int) -> PullRequestSnapshot`; `get_check_state(repo: str, sha: str) -> CheckState`; `set_worker_label(repo: str, number: int, label: WorkerLabel | None) -> None`; `commit_and_push(repo: str, branch: str, expected_head_sha: str, commit_message: str) -> str`. The adapter owns authenticated API use and does not intentionally inject its token into subprocess environments; host-mode access to credentials available to the owner remains possible and accepted.
- **Config read port:** `get_default_branch_config(repo: str, path: str) -> PilotConfig` reads only the default branch and applies Task 1's strict schema. It contains no model, repository allowlist, or host-mode field.
- **Config-PR port:** `create_or_update_config_pr(repo: str, branch: str, files: Mapping[str, str], title: str, body: str) -> ConfigPullRequest`; it uses the user's authenticated identity and never writes directly to the default branch.
- **Queue service exposed to UI plan:** `list_queue() -> list[QueueItem]`; `get_queue_status() -> bool` for the confirmed paused state; `get_item(pr_number: int) -> QueueItem | None`; `pause() -> None`; `resume() -> None`; `cancel(pr_number: int) -> None`; `retry(pr_number: int) -> QueueItem`; `submit_review_feedback(pr_number: int, head_sha: str, feedback: str) -> QueueItem`; `subscribe(listener: Callable[[QueueEvent], None]) -> Callable[[], None]`.
- **Queue types:** `WorkerLabel = Literal["gitkeeper/worker-queued", "gitkeeper/worker-running", "gitkeeper/worker-blocked"]`; `WorkerState = Literal["ready", "queued", "running", "blocked"]`; `RepairReason = Literal["deterministic_check_failure", "claude_feedback", "manual_retry"]`; each `QueueItem` includes repository, PR number, current head SHA, author/head repo, eligibility, `CheckState`, remote labels, state, reason, cycle ID, automatic attempt count, manual retry count, last-updated timestamp, and categorized last error. Pending host-command approval is a local execution state and does not create a fourth GitHub worker label.
- **Telemetry port:** `list_events(after: datetime | None = None) -> list[TelemetryEvent]`; `get_retention_days() -> int`; `set_retention_days(days: int) -> None`, where `0 <= days <= 90`. Events expose operational fields only.
- **Publish contract:** Validate scope against the assigned repair and compare the remote head with the worker's starting SHA immediately before commit/push. Return the new SHA only after a normal fast-forward push succeeds. Do not run Gradle tests locally.
- **Scope validation boundary:** The approved spec requires validation against the assigned repair but does not define a machine-checkable scope format or acceptance rule. The publisher must fail closed when scope cannot be validated; do not invent an automatic semantic reviewer or silently weaken validation. Resolve this contract before enabling publication.

### Task 1: Define domain and persistence contracts

**Files:**
- Create: `src/gitkeeper/domain/models.py`
- Create: `src/gitkeeper/queue/store.py`
- Create: `src/gitkeeper/telemetry/store.py`
- Test: `tests/unit/domain/test_models.py`
- Test: `tests/unit/queue/test_store.py`
- Test: `tests/unit/telemetry/test_store.py`

**Interfaces:**
- Produces the `QueueItem`, `RepairRequest`, `PullRequestSnapshot`, `CheckState`, `QueueEvent`, and `WorkerLabel` types defined above.
- `QueueStore.claim(key: tuple[str, int, str], owner: str, now: datetime, lease_seconds: int) -> Lease | None` atomically grants at most one lease per `(repository, PR number, head SHA)`.
- `TelemetryStore.record(event: TelemetryEvent) -> None`; `TelemetryStore.expire(now: datetime, retention_days: int = 90) -> int` removes expired metadata only and rejects retention outside `0..90` days.

- [ ] **Step 1: Write model tests** for supported states/reasons, exact-SHA identity, and rejection of malformed labels/PR metadata.
- [ ] **Step 2: Run `py -m pytest tests/unit/domain/test_models.py -q`.** Expected: FAIL because the planned domain types are absent from the existing package.
- [ ] **Step 3: Implement the typed domain dataclasses/enums** in `src/gitkeeper/domain/models.py`, using the existing package configuration.
- [ ] **Step 4: Run `py -m pytest tests/unit/domain/test_models.py -q`.** Expected: PASS.
- [ ] **Step 5: Write SQLite tests** for atomic duplicate claim rejection, lease expiry, reopen-cycle record creation with history retained, database path outside both repositories, and corrupt/unavailable database failure preventing worker launch.
- [ ] **Step 6: Run `py -m pytest tests/unit/queue/test_store.py -q`.** Expected: FAIL until the schema/transactions exist.
- [ ] **Step 7: Implement `QueueStore` transactions** with uniqueness on repository/PR/head SHA and separate repair-cycle/attempt-history records.
- [ ] **Step 8: Write telemetry tests** for operational-only fields, 90-day expiry, rejection of retention greater than 90 days, and rejection of source/diff/prompt/credential/screenshot fields.
- [ ] **Step 9: Run `py -m pytest tests/unit/queue/test_store.py tests/unit/telemetry/test_store.py -q`.** Expected: PASS.
- [ ] **Step 10: Commit** domain, stores, and tests.

### Task 2: Reconcile GitHub state and maintain queue/lease transitions

**Files:**
- Create: `src/gitkeeper/github/port.py`
- Create: `src/gitkeeper/github/rest.py`
- Create: `src/gitkeeper/github/poller.py`
- Create: `src/gitkeeper/queue/service.py`
- Test: `tests/unit/queue/test_service.py`
- Test: `tests/unit/github/test_poller.py`
- Test: `tests/integration/test_github_reconciliation.py`

**Interfaces:**
- Consumes: Task 1 types/store and Actions/config contract.
- Produces: `QueueService` methods listed above; `reconcile(snapshot: PullRequestSnapshot, checks: CheckState, config: PilotConfig) -> QueueItem | None`.
- `CheckState` binds outcome to a single SHA and is one of `passed`, `deterministic_failure`, `blocked`, or `unknown`.
- Produces `GitHubPoller.poll_once() -> list[QueueItem]` using outbound adaptive polling, with shorter delay when queued/running work exists and longer delay when idle; no polling interval values are guessed in this plan.

- [ ] **Step 1: Write eligibility tests** for the explicit owner repository allowlist, Setu-mobile public exception, additional private/owner-controlled repo policy, exact author, same-repo head, fork rejection, open/non-draft status, merged config enablement, and current-SHA binding.
- [ ] **Step 2: Write classification and label-transition tests** for pass -> ready/no queue, explicit deterministic failure -> queued, infrastructure/cancelled/missing/unknown -> blocked, stale SHA -> no claim/write, atomic lease claim -> running, and only the companion setting `worker-running`.
- [ ] **Step 3: Run `py -m pytest tests/unit/queue/test_service.py -q`.** Expected: FAIL because service/port do not exist.
- [ ] **Step 4: Implement `GitHubPort` and `GitHubRestClient`** with an injected control-plane token provider; do not make the token provider available to worker subprocesses.
- [ ] **Step 5: Implement `QueueService.reconcile(...)`** and idempotent label projection; ensure pass state removes obsolete worker labels. After an atomic local lease claim, the companion sets `worker-running`; local worker/escalation failures may set `worker-blocked`; on completed publish or cancellation it removes obsolete labels and lets current Actions results or reconciliation determine the next remote state. Repository eligibility must be cross-checked against owner-managed local policy; a GitHub queue label never grants it.
- [ ] **Step 6: Run `py -m pytest tests/unit/queue/test_service.py -q`.** Expected: PASS.
- [ ] **Step 7: Write integration tests** for missed events/restart reconciliation, network errors, label API errors, stale completions, and close/reopen cycle accounting using a fake GitHub port.
- [ ] **Step 8: Run `py -m pytest tests/integration/test_github_reconciliation.py -q`.** Expected: FAIL before integration/reconciliation wiring, PASS after.
- [ ] **Step 9: Write poller tests** for idle versus active adaptive delay ordering, offline API errors, and no new claim when GitHub is unreachable.
- [ ] **Step 10: Run `py -m pytest tests/unit/github/test_poller.py -q`.** Expected: FAIL until poller exists.
- [ ] **Step 11: Implement `GitHubPoller.poll_once()`** with outbound adaptive polling and reconciliation; show stale/offline state and avoid claiming from stale remote data.
- [ ] **Step 12: Run `py -m pytest tests/unit/github/test_poller.py tests/integration/test_github_reconciliation.py -q`.** Expected: PASS.
- [ ] **Step 13: Commit** GitHub adapter, queue service, poller, and tests.

### Task 3: Implement worktree lifecycle, feedback input, and invocation budgets

**Files:**
- Create: `src/gitkeeper/worker/worktree.py`
- Modify: `src/gitkeeper/queue/service.py`
- Test: `tests/unit/worker/test_worktree.py`
- Test: `tests/unit/queue/test_service.py`
- Test: `tests/integration/test_worker_lifecycle.py`

**Interfaces:**
- Consumes: Task 1 `QueueStore`/types and Task 2 `QueueService`.
- Produces: `WorktreeManager.create(repo: str, pr_number: int, head_sha: str) -> Path`; `discard_or_preserve(path: Path, outcome: WorkerState) -> None`.
- Feedback entry accepts only `submit_review_feedback(pr_number, head_sha, feedback)` for an open eligible PR whose required checks passed at that SHA; changed SHA requires explicit re-association.

- [ ] **Step 1: Write worktree tests** for unique path per repo/PR/SHA, creation at exact SHA, state directory outside both repositories, and preservation after failure/cancel.
- [ ] **Step 2: Run `py -m pytest tests/unit/worker/test_worktree.py -q`.** Expected: FAIL because the manager does not exist.
- [ ] **Step 3: Implement `WorktreeManager`** with its managed worktree root outside both repository active checkouts; verify the user's current branch, HEAD, and dirty-file snapshot are unchanged after worktree lifecycle operations. Do not claim that path validation confines host commands.
- [ ] **Step 4: Write queue policy tests** for three automatic invocations per open cycle across SHAs, fresh cycle on reopen with history preserved, and separately recorded explicit retries.
- [ ] **Step 5: Write feedback tests** for accepted current-SHA Claude text after CI pass, rejected stale feedback without explicit re-association, and no feedback in telemetry/labels/config PRs.
- [ ] **Step 6: Run `py -m pytest tests/unit/worker/test_worktree.py tests/unit/queue/test_service.py -q`.** Expected: PASS.
- [ ] **Step 7: Write lifecycle tests** for open/ready, synchronize, close/merge cancellation, reopen, late worker completion, offline queue recovery, and active-run pause only at a safe worker checkpoint.
- [ ] **Step 8: Run `py -m pytest tests/integration/test_worker_lifecycle.py -q`.** Expected: PASS.
- [ ] **Step 9: Commit** worktree and lifecycle behavior.

### Task 4: Run OpenCode only behind accepted host-mode readiness and enforce safe publish

**Files:**
- Create: `src/gitkeeper/worker/opencode.py`
- Create: `src/gitkeeper/worker/publish.py`
- Test: `tests/unit/worker/test_opencode.py`
- Test: `tests/unit/worker/test_publish.py`
- Test: `tests/integration/test_worker_lifecycle.py`
- Consumes: `src/gitkeeper/host_mode/readiness.py`, `command_broker.py`, and `opencode_capability.py` from the host-mode readiness plan.

**Interfaces:**
- Consumes: passing `HostModeStatus`, verified CLI broker capability, one-shot approval broker, Task 2 GitHub port, Task 3 worktree and queue contracts.
- Produces: `OpenCodeRunner.run(worktree: Path, model_id: str, prompt: str) -> WorkerResult`; `PatchPublisher.validate_and_publish(worktree: Path, starting_sha: str, assigned_scope: RepairScope, branch: str) -> str`.
- `RepairScope` comes from the assigned deterministic check/review-feedback work item. If the patch cannot be verified against that scope, publication fails closed and the worktree is preserved.

- [ ] **Step 1: Write runner tests** asserting missing/blocked host readiness prevents process launch, unsupported CLI version or broker capability is rejected, empty/unallowlisted/non-`opencode/` model IDs are rejected, selected allowed model ID is passed unchanged, the prompt includes branching/TDD/framework-sanity guidance but is not treated as enforcement, and no control-plane token is explicitly injected.
- [ ] **Step 2: Run `py -m pytest tests/unit/worker/test_opencode.py -q`.** Expected: FAIL because gate/runner do not exist.
- [ ] **Step 3: Require a passing host-mode readiness result and verified command-broker capability** before constructing the OpenCode process adapter. An unsupported or unverified CLI integration is a hard block.
- [ ] **Step 4: Implement `OpenCodeRunner.run(...)`** with a clean explicitly constructed environment and the local allowlisted model. Do not inject a control-plane GitHub token; document that same-user host credentials remain accessible by risk acceptance. Route all shell commands through the broker.
- [ ] **Step 5: Run `py -m pytest tests/unit/worker/test_opencode.py -q`.** Expected: PASS with a fake process adapter; CLI 1.19.6 broker feasibility is covered by the host-mode readiness plan.
- [ ] **Step 6: Write publisher tests** for patch scope/path validation, changed remote SHA, branch mismatch, push rejection/non-fast-forward, and ensuring force-push/main paths are never requested.
- [ ] **Step 7: Run `py -m pytest tests/unit/worker/test_publish.py -q`.** Expected: FAIL until publisher exists.
- [ ] **Step 8: Implement `PatchPublisher.validate_and_publish(...)`**: validate patch structure/scope; fetch remote PR head and require equality to `starting_sha`; stage/commit in control plane; normal fast-forward push only to existing PR branch; return new SHA only after success. No Gradle test invocation occurs here.
- [ ] **Step 9: Run `py -m pytest tests/unit/worker/test_publish.py -q`.** Expected: PASS.
- [ ] **Step 10: Write integration test** asserting a successful push creates a new revision for Actions reconciliation, where deterministic failure queues repair within budget and unknown/cancelled/infrastructure blocks.
- [ ] **Step 11: Run `py -m pytest tests/unit/worker tests/integration/test_worker_lifecycle.py -q`.** Expected: PASS; no local Gradle command is run.
- [ ] **Step 12: Commit** host-mode adapter, runner, publisher, and tests.

### Task 5: Verify integrated service contract for the companion UI

**Files:**
- Modify: `src/gitkeeper/queue/service.py`
- Test: `tests/unit/queue/test_service.py`
- Test: `tests/integration/test_worker_lifecycle.py`

**Interfaces:**
- Produces the exact `QueueService` public methods from the Interfaces section for the PySide6/QML companion plan, including host-command approval status.
- UI receives events and immutable queue snapshots; it never receives GitHub credentials, SQLite connections, or worker process handles.

- [ ] **Step 1: Write contract tests** for each public queue method, event subscription/unsubscribe, pause/resume, cancel, retry, and manual Claude feedback submission.
- [ ] **Step 2: Run `py -m pytest tests/unit/queue/test_service.py -q`.** Expected: FAIL for any missing contract method.
- [ ] **Step 3: Implement only missing service-contract methods** and ensure invalid transitions return surfaced domain errors.
- [ ] **Step 4: Run `py -m pytest tests/unit tests/integration -q`.** Expected: PASS.
- [ ] **Step 5: Run `./gradlew testDebugUnitTest detekt lint` only through the Setu-mobile GitHub Actions run after a worker push.** Expected: Actions reports checks for the exact pushed SHA; do not run this command locally.
- [ ] **Step 6: Commit** any final service contract changes.

## Dependency handoff

This plan depends on the Actions/config plan establishing the PR/check/config/label contract and the host-mode readiness plan proving CLI 1.19.6 command mediation. Do not launch OpenCode until local owner policy, exact model allowlisting, and broker capability all pass. UI Tasks 1–4 can proceed against fakes; the UI's Task 5 integrates this service and approval broker. If the broker cannot prevent shell bypass, keep worker execution disabled; do not substitute an unapproved CLI or provider.
