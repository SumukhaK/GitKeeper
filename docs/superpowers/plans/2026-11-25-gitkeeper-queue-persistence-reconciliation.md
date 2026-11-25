# Queue Persistence and Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. A fresh implementer and a fresh reviewer are required for every code slice.

**Goal:** Implement the local queue/persistence and GitHub reconciliation portions of the approved pilot without enabling worker execution.

**Architecture:** A standard-library Python domain layer records repair cycles, queue projections, leases, attempts, and operational telemetry in SQLite outside both repositories. A GitHub port reads authoritative PR/check/config facts and projects at most one worker label; a queue service applies the readiness policy before any atomic local claim. A restart reconciler and single-flight adaptive poller recover from missed events and outages without webhooks or inbound listeners.

**Tech Stack:** Python 3.10+, standard-library `sqlite3`, `urllib`, `dataclasses`, and `unittest`/`pytest`; no new runtime dependency.

**Spec:** [approved pilot design spec](../specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md)

**Existing detailed source plan:** [local queue and worker plan](2026-11-20-gitkeeper-local-queue-worker.md)

**Durable handover:** [pilot handover tracker](2026-11-20-gitkeeper-pilot-handover.md)

**Base SHA:** `2bf5a48aeb4018019da905f5dbf07639bf46ad21`

## Global Constraints

- The automatic worker remains disabled. This plan adds no worktree creation, OpenCode launch, patch publication, model fallback, QML/UI integration, settings or workflow changes, or Setu-mobile changes.
- GitHub is authoritative for remote PR state, current head SHA, check results, merged/default-branch config, and labels. Local SQLite is the execution/recovery record; labels are projections, never locks.
- SQLite files live outside every watched repository and the GitKeeper repository. Construction must reject a state path equal to or beneath either resolved root, including through symlinks/junctions.
- The lease identity is `(repository, pr_number, head_sha)`. Only one active local lease may exist in the single-worker pilot. Claims are atomic and duplicate-safe across connections/processes.
- A new head SHA cancels stale work. Closing a PR cancels it. Reopening starts a fresh repair cycle and automatic budget while preserving history. Stale completions cannot publish or overwrite current labels.
- At most **3 automatic invocations per open/reopen cycle**, across head-SHA changes. Explicit owner retries are separate records and never replenish that budget.
- The only remote queue labels are `gitkeeper/worker-queued`, `gitkeeper/worker-running`, and `gitkeeper/worker-blocked`; at most one may exist. Ineligible/fork/external PRs never become worker-eligible because a label exists.
- Automatic eligibility requires exact `SumukhaK` authorship, a same-repository head, repository allowlist and privacy verification, an open non-draft PR, merged/default-branch config, a valid current-SHA repair reason, and available budget.
- Current-SHA required checks all passing mean ready/no worker. A repair reason exists only for an explicit deterministic check failure or explicitly submitted Claude feedback. Missing, stale, cancelled, skipped, timed-out, action-required, neutral, or unknown required-check outcomes block; infrastructure and unknown outcomes are never code failures.
- Only `pull_request` secret-free/read-only CI and label-only jobs that never check out or execute PR code may exist. The control plane does not add workflows, change permissions, or read secrets into QML, SQLite, telemetry, labels, or worker subprocesses.
- Offline GitHub means no new claims or authoritative transitions. Preserve local state, show a stale/offline condition, and retry with capped backoff. A failed label write preserves the lease/run record and never displays false remote success.
- Polling is outbound and adaptive. There are no inbound listeners. Use the implementation rulings below for concrete timing.
- Telemetry retention defaults to 90 days and can only be shortened. It stores timestamps/durations, PR/revision identifiers, transitions, attempts, outcomes, selected model ID, and categorized errors only. Never store source, diffs, prompts, credentials, screenshots, raw commands/scripts, environment values, or full OpenCode input/output.
- Claude feedback is restricted work input, not telemetry. Its UI submission and worker handoff belong to Task 3 of the source plan, not this plan.
- Corrupt or unavailable SQLite prevents claim/continuation and must not replace, delete, or rewrite existing database/sidecar files.
- Every commit includes `Co-authored-by: Copilot App <223556219+Copilot@users.noreply.github.com>`. Every task gets its own PR, fresh independent review, merged PR evidence, and tracker/README update before the next task starts.
- The README must continue saying plainly that worker execution is disabled. GitHub About changes only if project scope/description changes; this plan does not change either.

## Recorded Implementation Rulings

1. **Repair-reason wire values:** the new queue/UI domain owns `deterministic_check_failure`, `claude_feedback`, and `manual_retry`. Task 1 adds an explicit adapter to the existing readiness values `deterministic-check-failure` and `review-feedback`; it does not silently rename either existing API and creates no import cycle.
2. **Adaptive defaults:** active work polls every 15 seconds; idle every 60 seconds; a remote failure starts at 30 seconds and doubles to a 300-second cap. The poller adds no random jitter in this pilot, so timing is deterministic in tests. These are local defaults and do not widen the merged remote schema, whose `polling_preferences` remains exactly `{"mode": "adaptive"}`.
3. **Automatic invocation accounting:** an invocation is recorded when Task 3's future runner actually launches. Claims reserve neither an invocation nor a model selection. Task 2 checks the recorded per-cycle count before claiming, while the revision lease prevents duplicate local launches; the future runner must recheck budget and atomically record the launch before subprocess start.
4. **Database layout:** queue/history/pause state and telemetry use separate SQLite files in the same external state directory. A schema-version mismatch raises `StorageUnavailable`; this plan does not implement migrations or destroy an unknown schema.
5. **PR discovery:** one outbound poll lists open PRs for each configured repository. Local observations not present in a complete open-PR listing are treated as closed; any repository-list failure blocks all authoritative transitions for that repository and cancels none of them speculatively.
6. **Time:** domain timestamps are timezone-aware UTC; SQLite stores UTC ISO-8601 strings. Durations are non-negative integer milliseconds. Tests inject a clock rather than sleeping.
7. **Label reconciliation:** desired-state writes are idempotent but not represented as a distributed transaction. After a partial failure, the next poll reads actual remote labels and retries. The local item is marked stale/offline instead of claiming remote success.
8. **UI service compatibility:** one `QueueService` instance is bound to one repository, matching the existing PR-number-only UI/fake methods and avoiding cross-repository ambiguity. The reconciler maps repositories to their service instances; `get_queue_status()` remains a boolean.

## Execution Status

Statuses: `pending`, `in progress`, `review`, `blocked`, `merged`.

| Slice | Scope | Status | PR | Merge commit |
| --- | --- | --- | --- | --- |
| P0 | This plan, status tracking, and README/handover correction | review | PR pending | — |
| 1 | Domain contracts and readiness-reason adapter | pending | — | — |
| 2 | SQLite queue/cycle/lease persistence | pending | — | — |
| 3 | Operational-only telemetry persistence | pending | — | — |
| 4 | GitHub read/config/label REST adapter | pending | — | — |
| 5 | Queue service and label/budget transitions | pending | — | — |
| 6 | Restart/close/reopen reconciliation | pending | — | — |
| 7 | Adaptive outbound poller | pending | — | — |
| Final | Combined whole-branch review | pending | — | — |

P0 is the planning-docs slice and is in `review` with PR creation pending the controller. Slices 1–3 together fulfill original Task 1 of the [local queue and worker plan](2026-11-20-gitkeeper-local-queue-worker.md); slices 4–7 fulfill original Task 2. Original Tasks 1–2 remain pending until their mapped slices have merged code and clean review/test evidence. No queue code is merged yet. Tasks 3–5 of the source plan remain out of scope, and worker execution stays disabled.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/gitkeeper/queue/__init__.py` | Queue package exports only. |
| `src/gitkeeper/queue/models.py` | Immutable validated domain values and enums. |
| `src/gitkeeper/queue/errors.py` | Typed persistence, GitHub, stale-head, and offline failures. |
| `src/gitkeeper/queue/readiness_adapter.py` | Explicit mapping between queue and existing readiness repair reasons. |
| `src/gitkeeper/queue/storage.py` | External path validation and SQLite connection/schema version guard. |
| `src/gitkeeper/queue/store.py` | Atomic cycle/item/lease/attempt/pause persistence. |
| `src/gitkeeper/queue/telemetry.py` | Operational-only event recording, query, and retention expiry. |
| `src/gitkeeper/queue/github.py` | `GitHubControlPlane` protocol and typed REST adapter. |
| `src/gitkeeper/queue/service.py` | Eligibility, state machine, label projection, controls, and claim. |
| `src/gitkeeper/queue/reconciler.py` | Whole-repository reconciliation and missed-event recovery. |
| `src/gitkeeper/queue/poller.py` | Single-flight adaptive outbound loop. |
| `tests/test_queue_models.py` | Domain/security contract tests. |
| `tests/test_queue_store.py` | SQLite behavior and concurrency tests. |
| `tests/test_queue_telemetry.py` | Telemetry whitelist and retention tests. |
| `tests/test_queue_github.py` | REST mapping, config, error, and label tests. |
| `tests/test_queue_service.py` | Eligibility, transitions, claims, and stale-head tests. |
| `tests/test_queue_reconciler.py` | Restart, missed event, close/reopen, and partial-failure tests. |
| `tests/test_queue_poller.py` | Adaptive timing, single-flight, backoff, and stop tests. |

## Review Focus

1. **Unusual/missing check outcomes:** a missing required check, duplicate check name, stale SHA, or GitHub infrastructure conclusion must block rather than queue deterministic repair — pinned in Task 1 and Task 5.
2. **Concurrent processes and restarts:** two workers claiming the same revision, an expired lease, or a restart with an active lease must never launch duplicates; interrupted leases become reclaimable only after expiry — pinned in Task 2.
3. **Adversarial fork/config/label input:** a fork author, remotely supplied policy key, stale config, or preexisting worker label must not bypass the local gate — pinned in Tasks 1, 4, and 5.
4. **Remote failure between local and remote writes:** a partial label update, offline poll, or new SHA between claim and completion must preserve records and prevent false/stale remote success — pinned in Tasks 4, 5, and 6.
5. **Sensitive data and destructive storage handling:** telemetry rejects non-operational payloads, credentials never enter persisted errors, and corrupt/unknown-version databases are preserved — pinned in Tasks 2, 3, and 4.

## Per-Slice Delivery Gate

For every code slice, after its focused tests pass:

- [ ] Run `py -m pytest -q` and require the full suite to pass with pristine output.
- [ ] Review `git diff --check` and `git status --short`; only planned files may be present.
- [ ] Commit with the required trailer, push the dedicated branch, and open a task PR containing test evidence and the tracker status set to `review`.
- [ ] Dispatch a fresh reviewer against the exact base/head diff and implementer report. Do not use the implementer as reviewer.
- [ ] Fix every Critical/Important finding and obtain a fresh approval. A Minor finding may be explicitly deferred in the tracker.
- [ ] Merge only after approval and required checks. Record PR URL, merge SHA, review verdict, and test count; update README/tracker in the merge commit.
- [ ] Fetch `origin/main`, verify clean status, and only then start the next slice.

### Task 0: Publish the Executable Plan

**Files:** this file; `docs/superpowers/plans/2026-11-20-gitkeeper-local-queue-worker.md`; `docs/superpowers/plans/2026-11-20-gitkeeper-pilot-handover.md`; `README.md`.

- [x] Add the execution-status tables and cross-links described above; preserve all existing pilot decisions.
- [x] Correct the handover/README claim about readiness Task 4: readiness code and broker proof exist, but worker launch remains disabled and queue/worker implementation is pending.
- [x] Mark P0 `review` (PR pending) and commit only these four docs with the required trailer.
- [x] Self-review the four docs against the approved spec and task requirements; record the result in the task report.
- [ ] Controller: push `sumukhak-gitkeeper-pilot-design` and open a docs-only PR.
- [ ] Controller: dispatch an independent reviewer, merge only if clean, and replace P0's row with PR/merge evidence.
- [ ] Controller: update the ignored SDD ledger with plan path, `BASE=2bf5a48aeb4018019da905f5dbf07639bf46ad21`, and current slice/PR/review state.

### Task 1: Domain Contracts and Repair-Reason Adapter

**Files:**
- Create: `src/gitkeeper/queue/__init__.py`
- Create: `src/gitkeeper/queue/models.py`
- Create: `src/gitkeeper/queue/errors.py`
- Create: `src/gitkeeper/queue/readiness_adapter.py`
- Test: `tests/test_queue_models.py`

**Interfaces:**
- Consumes: existing `host_mode.readiness.RepairReason`, `Blocker`, `RepositoryPolicy`, `OWNER_LOGIN`, and `MAX_AUTOMATIC_INVOCATIONS`.
- Produces: `PullRequestSnapshot`, `CheckConclusion`, `CheckResult`, `CheckState`, `CheckOutcome`, `PilotConfig`, `RepairReason`, `WorkerState`, `WorkerLabel`, `ErrorCategory`, `QueueKey`, `RepairRequest`, `RepairCycle`, `QueueItem`, `Lease`, `InvocationAttempt`, `InvocationKind`, `AttemptOutcome`, `QueueEvent`, `OperationalError`, `Eligibility`.
- Produces: `parse_pilot_config(payload: Mapping[str, object], default_branch_sha: str) -> PilotConfig` and `to_readiness_reason(reason: RepairReason) -> RepairReason`.

- [ ] **Step 1: Write failing domain tests.** Pin exact enum values, frozen dataclasses, UTC timestamps, non-empty 40/64-hex SHAs, exact `SumukhaK/Setu-mobile`-style repository shape, non-empty distinct check IDs, and rejection of booleans where integers are required.
- [ ] **Step 2: Prove RED.** Run `py -m pytest tests/test_queue_models.py -q`; expect import failure before implementation.
- [ ] **Step 3: Implement models and errors.** `CheckState(head_sha, required_check_ids, results).outcome` returns `passed` only when every required ID has exactly one `success`; `deterministic_failure` only when all required IDs have exactly one result and every conclusion is `success` or `failure` with at least one failure; otherwise `unknown` for missing/duplicate results and `blocked` for every other conclusion. `QueueKey(repository, pr_number, head_sha)` validates all fields. `PullRequestSnapshot` carries repository, PR number, author, head repository, head SHA, open/draft flags, and observed time. `QueueItem` carries `repository`, `pr_number`, `current_head_sha`, `cycle_id`, `eligible`, `check_state`, `state`, `reason`, `remote_labels`, `automatic_attempt_count`, `manual_retry_count`, `last_error`, and `updated_at`; `check_state` exposes `checks.outcome.value`, and counts are non-negative exact integers. `Lease` contains key, `owner`, `started_at`, `expires_at`, and `cancelled_at`. `InvocationAttempt` contains an ID, key, cycle, kind, outcome, timestamps, optional model ID, and optional error category. `OperationalError` accepts only `ErrorCategory`, a short safe message, and occurrence time; no exception body or arbitrary payload.
- [ ] **Step 4: Test config and reason boundaries.** `parse_pilot_config` accepts exactly the four existing remote keys, rejects unknown keys/duplicate or empty check IDs/non-adaptive polling/invalid motion values, and binds the result to a valid `default_branch_sha`. `to_readiness_reason` maps deterministic failure and Claude feedback to the two existing readiness values and rejects `manual_retry` because manual retry is not an automatic launch reason. Add tests for all three mappings/rejection.
- [ ] **Step 5: Prove GREEN and regressions.** Run `py -m pytest tests/test_queue_models.py tests/host_mode/test_readiness.py -q`; then run the full suite and delivery gate.
- [ ] **Step 6: Commit, PR, review, merge.** Use only the listed files plus required tracker/README evidence; follow the per-slice delivery gate.

### Task 2: SQLite Queue, Cycles, Leases, and Attempts

**Files:**
- Create: `src/gitkeeper/queue/storage.py`
- Create: `src/gitkeeper/queue/store.py`
- Test: `tests/test_queue_store.py`

**Interfaces:**
- Consumes: all Task 1 domain types.
- Produces: `QueueStore(state_dir: Path, protected_repository_roots: Iterable[Path])`, `StorageUnavailable`, and `LocalLeaseConflict`.
- Methods: `open_cycle(repository: str, pr_number: int, head_sha: str, now: datetime) -> RepairCycle`; `get_open_cycle(repository: str, pr_number: int) -> RepairCycle | None`; `close_cycle(repository: str, pr_number: int, now: datetime) -> RepairCycle | None`; `put_observation(snapshot: PullRequestSnapshot) -> None`; `get_observation(repository: str, pr_number: int) -> PullRequestSnapshot | None`; `delete_observation(repository: str, pr_number: int) -> None`; `put_item(item: QueueItem) -> None`; `get_item(repository: str, pr_number: int) -> QueueItem | None`; `list_items() -> list[QueueItem]`; `claim(key: QueueKey, cycle_id: str, owner: str, now: datetime, lease_seconds: int) -> Lease | None`; `renew(key: QueueKey, owner: str, now: datetime, lease_seconds: int) -> Lease | None`; `cancel(key: QueueKey, now: datetime) -> None`; `get_active_lease() -> Lease | None`; `record_invocation(attempt: InvocationAttempt) -> None`; `list_invocations(repository: str, pr_number: int, cycle_id: str) -> list[InvocationAttempt]`; `record_retry(key: QueueKey, cycle_id: str, now: datetime) -> None`; `set_paused(paused: bool) -> None`; `is_paused() -> bool`; `close() -> None`.

- [ ] **Step 1: Write failing path/schema tests.** Assert the DB path is `<state_dir>/gitkeeper-queue.sqlite3`; reject state beneath either protected root, equal roots, junction/symlink escapes, empty protected roots, unsupported `user_version`, and unreadable/corrupt existing files without changing their bytes.
- [ ] **Step 2: Prove RED.** Run `py -m pytest tests/test_queue_store.py -q`; expect missing modules.
- [ ] **Step 3: Implement schema and persistence.** Use foreign keys, WAL, a busy timeout, `BEGIN IMMEDIATE` for writes, and `user_version=1`. Tables: `repair_cycles`, `pr_observations`, `queue_items`, `leases`, `invocations`, `retry_requests`, and `preferences`. `queue_items` is unique by repository/PR; revision history remains in `pr_observations` and `invocations`. A partial unique index permits only one non-cancelled lease globally. Validate lease seconds as a positive integer before opening a transaction.
- [ ] **Step 4: Test cycle/recovery behavior.** First observation opens cycle 1; new SHA updates current item and cancels its old lease; close is idempotent and preserves history; reopen creates the next cycle number and leaves prior attempts intact. Expired leases can be atomically reclaimed; owner mismatch on renewal returns `None`; cancel is idempotent. Pause state survives reopen.
- [ ] **Step 5: Test atomic claims.** Use two threads/connections and a barrier: exactly one claim succeeds for the same key, and a claim for another key fails while one global lease is active. Also prove a closed-cycle or non-queued item cannot be claimed. An expired active lease does not block a new atomic claim.
- [ ] **Step 6: Prove GREEN, self-review, and delivery.** Run focused tests, full suite, diff checks, then commit/PR/review/merge using only listed files and status/docs evidence.

### Task 3: Operational Telemetry Store

**Files:**
- Create: `src/gitkeeper/queue/telemetry.py`
- Test: `tests/test_queue_telemetry.py`

**Interfaces:**
- Consumes: Task 1 event/error types and Task 2 `storage.py`.
- Produces: `TelemetryStore(state_dir: Path, protected_repository_roots: Iterable[Path], retention_days: int = 90)`; methods `record(event: QueueEvent, *, duration_ms: int | None = None, invocation_count: int | None = None, outcome: AttemptOutcome | None = None, model_id: str | None = None, error: OperationalError | None = None) -> None`, `list_events(after: datetime | None = None) -> list[dict[str, object]]`, `get_retention_days() -> int`, `set_retention_days(days: int) -> None`, and `expire(now: datetime) -> int`.

- [ ] **Step 1: Write failing telemetry tests.** Reject retention below 0 or above 90, non-UTC timestamps, negative durations/counts, arbitrary event payloads, non-`opencode/` model IDs, raw exception text, and storage beneath protected roots.
- [ ] **Step 2: Prove RED.** Run `py -m pytest tests/test_queue_telemetry.py -q`.
- [ ] **Step 3: Implement the separate telemetry database.** Create `<state_dir>/gitkeeper-telemetry.sqlite3`, `user_version=1`, with one fixed-column `events` table and indexes on timestamp and PR identity. Serialize only enum values, integers, timestamps, validated model ID, and error category. Do not accept a mapping/event payload or exception object.
- [ ] **Step 4: Test retention and reopening.** The 91st-day event is expired at 90-day retention; shortening to 30 days works; increasing above 90 is impossible; expiry is idempotent; events survive close/reopen; no source/diff/prompt/credential/environment/raw-output columns exist. `list_events(after=...)` returns fixed-key mappings compatible with `TelemetryViewModel`, and `get_retention_days()`/`set_retention_days(...)` preserve its existing protocol.
- [ ] **Step 5: Prove GREEN and delivery.** Run focused/full tests and diff checks, then commit/PR/review/merge with tracker/README evidence.

### Task 4: GitHub Control-Plane Port and REST Adapter

**Files:**
- Create: `src/gitkeeper/queue/github.py`
- Test: `tests/test_queue_github.py`

**Interfaces:**
- Consumes: Task 1 models/errors; Task 2 external-path guard is not involved.
- Produces: `GitHubControlPlane` protocol with `list_open_pull_requests(repository: str) -> list[PullRequestSnapshot]`, `get_check_state(repository: str, pr_number: int, head_sha: str, required_check_ids: tuple[str, ...]) -> CheckState`, `get_default_branch_config(repository: str) -> PilotConfig`, and `set_worker_label(repository: str, pr_number: int, label: WorkerLabel | None) -> None`.
- Produces: `GitHubRestClient(api_url: str, token_provider: Callable[[], str], opener: Callable[..., object] = urlopen, timeout_seconds: float = 20.0)`, `GitHubUnavailable`, `GitHubAuthorizationError`, and `GitHubResponseError`.
- Excludes: merge, commit/push, config PR creation, comments, and repository-settings methods; those belong to later source-plan tasks.

- [ ] **Step 1: Write failing fake-transport tests.** Pin GitHub REST paths, headers, timeout, pagination, and absence of token values in exception strings. Cover repository/default-branch/contents/ref reads and issues-label GET/POST/DELETE calls.
- [ ] **Step 2: Prove RED.** Run `py -m pytest tests/test_queue_github.py -q`.
- [ ] **Step 3: Implement reads and strict mapping.** `list_open_pull_requests` follows `Link` pagination for `state=open&per_page=100`, returning exact repository/head repository/author/SHA/open/draft facts. `get_check_state` reads current-SHA check runs and check suites, maps known outcomes without treating workflow/job infrastructure failures as deterministic failures, and rejects missing/ambiguous required check names. `get_default_branch_config` reads repository metadata, then `.github/gitkeeper.json` from that exact default branch, obtains the branch commit SHA, and calls `parse_pilot_config`; an unmerged feature-branch value cannot be returned.
- [ ] **Step 4: Implement label projection.** Read current issue labels, add only the desired missing label, and delete the other two GitKeeper worker labels. Preserve unrelated labels. No repository-content endpoint, PR checkout, secret, or workflow mutation is used. Repeated desired-state calls issue no unnecessary mutation after convergence.
- [ ] **Step 5: Test failures.** Empty/missing token and HTTP 401/403 become typed authorization/unavailable errors; 404, 429, 5xx, malformed JSON, and timeouts become typed errors. Partial label failure propagates and contains no response body or credentials. The client performs no hidden retry; the poller owns backoff.
- [ ] **Step 6: Prove GREEN and delivery.** Run focused/full tests and diff checks, then commit/PR/review/merge with tracker/README evidence.

### Task 5: Queue Service, Eligibility, Labels, and Claims

**Files:**
- Create: `src/gitkeeper/queue/service.py`
- Test: `tests/test_queue_service.py`

**Interfaces:**
- Consumes: Task 1–4 types, `QueueStore`, `GitHubControlPlane`, existing `RepositoryPolicy`, `Blocker`, `OWNER_LOGIN`, and `MAX_AUTOMATIC_INVOCATIONS`.
- Produces: `QueueService(repository: str, store: QueueStore, github: GitHubControlPlane, policy: RepositoryPolicy, clock: Callable[[], datetime], lease_seconds: int = 120)`.
- Methods: `classify(snapshot: PullRequestSnapshot, checks: CheckState, config: PilotConfig) -> Eligibility`; `reconcile(snapshot: PullRequestSnapshot, checks: CheckState, config: PilotConfig) -> QueueItem | None`; `claim(pr_number: int, head_sha: str, owner: str) -> QueueItem | None`; `list_queue() -> list[QueueItem]`; `get_queue_status() -> bool`; `get_item(pr_number: int) -> QueueItem | None`; `pause() -> None`; `resume() -> None`; `cancel(pr_number: int) -> None`; `retry(pr_number: int) -> QueueItem | None`; `subscribe(listener: Callable[[QueueEvent], None]) -> Callable[[], None]`.

- [ ] **Step 1: Write failing eligibility tests.** Table-test closed/draft/fork/external/unallowlisted/unverified/disabled-config/stale-check/missing-check/blocked-check/passing/failing/capped cases. Exact owner/repository matching is case-sensitive. `manual_retry` never bypasses identity, open/non-draft, same-repo-head, merged-config, current-SHA, or policy gates.
- [ ] **Step 2: Prove RED.** Run `py -m pytest tests/test_queue_service.py -q`.
- [ ] **Step 3: Implement classification and projection.** Reject a snapshot whose repository differs from the bound repository. Map each refusal to the existing named `Blocker`; map the two allowed repair reasons through Task 1's adapter. Automatic count is all recorded automatic invocations in the current cycle across SHAs; cap is `>= 3`. Preserve explicit retry records separately. Passing/current checks clear worker labels; deterministic failure queues unless paused/capped; stale/missing/blocked outcomes project blocked.
- [ ] **Step 4: Implement remote-first reconcile.** Persist the local observation, then read actual labels before projection. A remote failure leaves the prior record, emits an offline/stale operational event, and raises without claiming. Label success is recorded only after the desired remote state is observed. Ineligible/closed items cancel leases and clear GitKeeper labels without recording a repair invocation.
- [ ] **Step 5: Implement atomic claim and controls.** Re-read PR/check/config facts, reclassify, require queued state and available budget, atomically claim the exact key/cycle, then set `gitkeeper/worker-running`. A label failure preserves the lease/run record and raises; it must not return a false running item. A changed SHA cancels the stale lease and refuses the old claim. Pause prevents new claims; resume does not retry capped/explicitly blocked work. `cancel` closes/cancels local work and clears labels; `retry` is accepted only for an owner-requested open eligible item and records a manual retry without resetting the cycle budget. List/get/subscribe must continue matching `tests/fakes/fake_queue_service.py` signatures; feedback submission is deliberately out of scope.
- [ ] **Step 6: Test stale and failure paths.** Prove preexisting labels cannot bypass policy, a failed label write preserves the lease, a new head between remote validation and claim prevents stale work, offline performs no claim, unrelated labels survive, and listener exceptions cannot roll back committed state.
- [ ] **Step 7: Prove GREEN and delivery.** Run focused/full tests and diff checks, then commit/PR/review/merge with tracker/README evidence.

### Task 6: Restart, Missed-Event, and Close/Reopen Reconciliation

**Files:**
- Create: `src/gitkeeper/queue/reconciler.py`
- Test: `tests/test_queue_reconciler.py`

**Interfaces:**
- Consumes: Task 5 `QueueService` and `GitHubControlPlane`.
- Produces: `Reconciler(github: GitHubControlPlane, services: Mapping[str, QueueService], store: QueueStore, clock: Callable[[], datetime])`; methods `reconcile_once() -> list[QueueItem]` and `reconcile_repository(repository: str) -> list[QueueItem]`. Each mapping key must equal the bound repository of its service.

- [ ] **Step 1: Write failing recovery tests.** Seed persisted open/closed observations, active/stale leases, attempts, and remote labels; prove restart replays authoritative GitHub facts rather than trusting local state.
- [ ] **Step 2: Prove RED.** Run `py -m pytest tests/test_queue_reconciler.py -q`.
- [ ] **Step 3: Implement whole-repository reconciliation.** For each repository, fetch the complete open-PR list before mutating that repository. Compare persisted observations: a changed SHA updates/cancels stale work; an absent previously-open PR calls `cancel` and removes its observation; a returned open PR with no open cycle is reopened and the service opens the next cycle on reconcile. Then fetch current-SHA checks and default config and call the service. A repository-list failure mutates nothing for that repository; individual PR/config/check failures are recorded and skipped without closing anything or classifying a code failure.
- [ ] **Step 4: Test outages and missed events.** A repository list outage retains all local state and claims nothing; one failed PR check read does not prevent unrelated PRs reconciling; process restart recovers queued/blocked projections; close/reopen preserves cycle/attempt history; a stale completion keyed to an old SHA is rejected by the service and cannot alter the new revision.
- [ ] **Step 5: Prove GREEN and delivery.** Run focused/full tests and diff checks, then commit/PR/review/merge with tracker/README evidence.

### Task 7: Adaptive Outbound Poller

**Files:**
- Create: `src/gitkeeper/queue/poller.py`
- Test: `tests/test_queue_poller.py`

**Interfaces:**
- Consumes: Task 6 `Reconciler` and Task 2 `QueueStore`.
- Produces: `PollingTiming(active_seconds: int = 15, idle_seconds: int = 60, initial_error_seconds: int = 30, max_error_seconds: int = 300)`; `QueuePoller(reconciler: Reconciler, store: QueueStore, timing: PollingTiming = PollingTiming(), sleep: Callable[[float], None] = time.sleep)`; methods `poll_once() -> list[QueueItem]`, `next_delay() -> float`, and `run_until(stop: Callable[[], bool]) -> None`.

- [ ] **Step 1: Write failing timing tests.** With no remote failure, active queued/running items choose 15 seconds and otherwise idle is 60. Remote failure takes precedence: consecutive failures produce 30/60/120/240/300; success resets backoff. Reject zero/negative values or `initial_error_seconds > max_error_seconds`.
- [ ] **Step 2: Prove RED.** Run `py -m pytest tests/test_queue_poller.py -q`.
- [ ] **Step 3: Implement single-flight polling.** A nonblocking lock prevents overlapping repository passes. Each `poll_once` records success/failure, updates a stale/offline status from typed failures, and never launches a worker. `run_until` checks stop before sleeping and after waking; sleeps only the computed delay. Unexpected exceptions are categorized without storing raw exception text and reset only on a subsequent successful pass.
- [ ] **Step 4: Test recovery.** Offline retains queued/running records and performs no claims; the next successful poll restores current projections and idle/active timing. A second invocation while the first is active returns immediately without a second GitHub pass. Stopping never sleeps through a full delay.
- [ ] **Step 5: Prove end-to-end scope.** Run focused tests and `py -m pytest -q`; inspect the tree for accidental worker launcher/worktree/OpenCode/UI/publication changes. Then commit/PR/review/merge with tracker/README evidence.

### Final: Combined Review and Handover

- [ ] Confirm all slice rows contain PR URLs, merge SHAs, approval verdicts, and full-suite counts; source-plan Tasks 1–2 are checked complete only from merged code evidence.
- [ ] Update the handover with exact implemented interfaces, external database locations, polling defaults, recovery behavior, known limitations, and the next Task 3 prerequisites. Keep worker execution marked disabled.
- [ ] Run the full suite once on `origin/main`; record the count and `git status` cleanliness in the tracker.
- [ ] Dispatch a fresh whole-branch reviewer for Tasks 1–7. Resolve every Critical/Important finding in a follow-up PR before declaring this scope complete.
- [ ] Record the final verdict and remaining out-of-scope work in the planner, handover, README, SQL todos, and ignored SDD ledger.

## Self-Review Record

- Spec coverage: slices 1–7 cover domain, persistence, leases, telemetry, GitHub reads/labels, state transitions, budget, close/reopen, stale work, outages, and adaptive polling. Worktree/OpenCode/publication/UI/full launch readiness are explicitly excluded and remain in source-plan Tasks 3–5.
- Security: fork/author/policy/config/current-SHA gates precede claims; credentials stay in the adapter boundary; telemetry has a fixed operational schema; labels never confer trust; no secret-bearing Actions workflow is introduced.
- Type consistency: all later slices consume the exact Task 1/2/4 interfaces above; UI-compatible queue methods are preserved, while readiness reason conversion is explicit.
- Failure behavior: no remote outage, partial label write, stale completion, corrupt database, or unknown schema is converted into success or destructive migration.
- Remaining blockers: none for this slice. The future worker credential provider and execution path remain deliberately undecided for Task 3; they are not prerequisites for queue/reconciliation control-plane work.
