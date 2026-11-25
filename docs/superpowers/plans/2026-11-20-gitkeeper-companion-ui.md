# GitKeeper Companion, Pet, Settings, and Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Windows PySide6/QML companion UI with the original fox pet, handscroll panel, queue controls, validated config-PR settings, and privacy-limited local telemetry views.

**Architecture:** Extend the existing Python/PySide6/QML companion prototype. Keep view models tested against fake queue/config services first, then connect to the local queue service, host-mode readiness, command-approval broker, and GitHub control-plane ports from the other plans. Keep the UI independent from worker internals; expose no credential values to QML, but clearly disclose that OpenCode/approved commands run as the owner and may access same-user host credentials. Retain all functionality when motion is reduced or disabled.

**Tech Stack:** Python, PySide6, QML, pytest, pytest-qt, SQLite-backed queue/telemetry service.

**Spec:** `docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md`

## Global Constraints

- GitKeeper application, companion, and worker code belong only in the private GitKeeper repository. Setu-mobile receives only the GitHub Actions integration and its configuration.
- PR code is untrusted. Actions uses `pull_request`, never `pull_request_target`; the privileged label-writing workflow does not check out PR code, download PR artifacts, or execute PR-controlled content.
- The companion is a Windows Python application using PySide6/QML and outbound/adaptive GitHub polling; it does not host a public inbound service.
- GitHub Actions is the sole test gate and runs `./gradlew testDebugUnitTest detekt lint` on PR `opened` and `synchronize`, and again for every companion push to the PR branch.
- The companion and worker do not run the required test suite locally as a prerequisite to publishing.
- The owner has accepted Windows 10 Home host execution as `ACCEPTED_HOST_MODE`, waiving OS isolation and a separate-user credential boundary. Do not describe the mode or a worktree/allowlist/approval UI as a sandbox.
- The host-mode readiness plan owns local `ACCEPTED_HOST_MODE`, repository allowlist, and exact `opencode/...` model allowlist controls. The model list begins empty; do not guess IDs or put local policy/model IDs in remote Setu-mobile config.
- OpenCode CLI `1.19.6` must be detected and proven to route every shell request through the companion broker. If not, worker execution remains disabled.
- Every shell request requires a fresh explicit approval in the desktop UI, bound to the complete command/script, cwd, repo, PR/SHA, purpose, and environment variable names. Any missing, changed, expired, or incompletely displayed request is denied.
- The automatic invocation budget is three per open/reopen cycle across head-SHA changes. Reopen starts a fresh budget while preserving history; explicit retries after escalation are user-triggered and separate.
- Queue labels are `gitkeeper/worker-queued`, `gitkeeper/worker-running`, and `gitkeeper/worker-blocked`; GitHub is authoritative for remote PR/queue state.
- The UI offers PR/status, queue, settings, and local telemetry areas; controls include pause/resume, cancel current run, and explicit retry after escalation.
- The pet is an original nine-tailed fox-spirit design, not a Naruto/Kurama replica; clicking it opens a custom Japanese handscroll-inspired panel.
- Reduced and disabled motion are supported, and status is available as text rather than color/animation alone.
- Configuration changes are validated and published as a config PR; Actions reads only merged/default-branch config.
- Other authors and all fork PRs receive secret-free CI only; worker controls must remain unavailable for ineligible PRs.
- User-transferred Claude feedback is bound to the selected PR/head SHA, is not automated, and is not telemetry.
- Telemetry retains operational metadata for 90 days by default; it stores no source code, diffs, prompts, credentials, screenshots, or full OpenCode input/output.
- Analytics never automatically modify prompts, rules, or product behavior.
- SQLite, worktrees, and local telemetry are outside both repositories. The control plane does not intentionally inject its GitHub token into OpenCode; same-user accessible credentials may still be reachable in accepted host mode.
- Worker publication occurs only after patch structure/scope validation and confirmation that remote head equals the worker's starting SHA; the companion commits and fast-forward pushes to the existing PR branch only. No force-push or push to `main`.

## Review Focus

- Reduced/disabled motion must not hide queue state or controls: Task 2 QML tests run with both preferences and assert text/control visibility.
- Feedback associated with an old SHA must not queue work: Task 3 view-model test asserts explicit re-association is required.
- A config validation error must not create a config PR or alter merged settings: Task 3 fake publisher test asserts no publish call.
- Telemetry must not display or persist source/prompt/credential data and must expire after 90 days: Task 4 tests assert exact allowed fields and expiry.
- A disconnected service, delayed event, accepted-host-only state, or unavailable approval broker must not show false success or enable a worker: Task 1 and Task 5 integration tests assert stale/offline status and fail-closed readiness; the host-mode plan's Task 2 tests approval display, deny, one-use, and changed requests.

---

## Planned file inventory (existing prototype and additions)

- Modify: `src/gitkeeper/ui/app.py` — Windows app entry point and dependency wiring.
- Modify: `src/gitkeeper/ui/viewmodels/status.py` — PR/status and current-SHA state.
- Modify: `src/gitkeeper/ui/viewmodels/queue.py` — queue projections, control commands, and approval state.
- Modify: `src/gitkeeper/ui/viewmodels/settings.py` — validated remote settings/config-PR state plus local owner-only host-mode settings.
- Modify: `src/gitkeeper/ui/viewmodels/telemetry.py` — operational telemetry presentation only.
- Modify: `src/gitkeeper/ui/services/config_publisher.py` — validate settings and create/update a Setu-mobile config PR using the local queue/worker plan's `create_or_update_config_pr(...)` port.
- Modify: existing QML `Main.qml`, `FoxPet.qml`, `HandscrollPanel.qml`, `PRStatusPage.qml`, `QueuePage.qml`, `SettingsPage.qml`, `TelemetryPage.qml`, and `AccessibleStatus.qml`.
- Extend: existing tests `tests/ui/test_status_viewmodel.py`, `test_queue_viewmodel.py`, `test_settings_viewmodel.py`, `test_config_publisher.py`, `test_telemetry_viewmodel.py`, and `test_qml_accessibility.py`; create `tests/ui/test_command_approval.py`.
- Modify: existing `tests/fakes/fake_queue_service.py` and `fake_config_publisher.py`; create `tests/fakes/fake_approval_broker.py`.
- `pyproject.toml` already defines `test` and `ui` extras; preserve and extend only if implementation needs a dependency not already declared.
- Existing UI modules, QML pages, and focused tests are partial prototypes; extend them rather than creating duplicate equivalents.

## Interfaces

- **Consumes from local queue/worker plan:** `QueueService.list_queue() -> list[QueueItem]`; `get_queue_status() -> bool` for the confirmed paused state; `get_item(pr_number: int) -> QueueItem | None`; `pause() -> None`; `resume() -> None`; `cancel(pr_number: int) -> None`; `retry(pr_number: int) -> QueueItem`; `submit_review_feedback(pr_number: int, head_sha: str, feedback: str) -> QueueItem`; `subscribe(listener: Callable[[QueueEvent], None]) -> Callable[[], None]`.
- Queue controls report success only after refreshing the service and confirming the resulting paused state or PR state; failed commands surface an error and disable controls until a fresh queue state is available.
- **Consumes from local queue/worker plan:** `QueueItem` with PR/SHA identity, state, reason, cycle and attempt counts, and categorized last error; telemetry service exposes allowed operational fields only.
- **Config publisher:** Remote `CompanionSettings` is `{worker_enabled: bool, polling_preferences: {mode: "adaptive"}, motion_preference: None | "reduced" | "disabled"}`. `validate_settings(settings: CompanionSettings) -> list[FieldError]`; `publish_settings(settings: CompanionSettings) -> ConfigPullRequest`; `get_config_pr_status(pr_url: str) -> ConfigPullRequest`. It validates remote settings before requesting GitHub operations, creates/updates a PR using the user's authenticated GitHub identity, and never pushes directly to the default branch. The host-mode plan owns local policy settings; they are never published.
- **Telemetry port:** `list_events(after: datetime | None = None) -> list[TelemetryEvent]`; `get_retention_days() -> int`; `set_retention_days(days: int) -> None`, where `0 <= days <= 90`. Events expose operational fields only.
- **UI boundary:** QML receives view models and safe approval/telemetry projections, never credential values, SQLite handles, process handles, or raw OpenCode input/output. The UI accurately states that host-mode OpenCode may still access credentials available to the same user.
- **Service-fake contract:** UI tests may run before the queue/worker implementation; they use the exact methods above, and integration replaces the fake with the real service without changing QML-facing types.

### Task 1: Extend the application and service-backed view models

**Files:**
- Modify: `src/gitkeeper/ui/app.py`
- Modify: `src/gitkeeper/ui/viewmodels/status.py`
- Modify: `src/gitkeeper/ui/viewmodels/queue.py`
- Modify: `tests/fakes/fake_queue_service.py`
- Test: `tests/ui/test_status_viewmodel.py`
- Test: `tests/ui/test_queue_viewmodel.py`

**Interfaces:**
- Produces `StatusViewModel.refresh() -> None`, `QueueViewModel.refresh() -> None`, and control methods that call only the `QueueService` public methods.
- `StatusViewModel` exposes current PR/SHA, eligibility, CI result, remote state, active worker state, last update, and stale/offline state as typed properties/signals.

- [ ] **Step 1: Write view-model tests** for loading queue/PR data, no-data state, stale GitHub state, service errors, and each user control's exact service call.
- [ ] **Step 2: Run `py -m pytest tests/ui/test_status_viewmodel.py tests/ui/test_queue_viewmodel.py -q`.** Expected: FAIL on any unimplemented service-state/control contract while preserving passing prototype behavior.
- [ ] **Step 3: Extend the fake service** with controllable queue events, API errors, offline snapshots, and recorded method calls.
- [ ] **Step 4: Extend status and queue view models** without exposing credential values, SQLite handles, or worker process internals to QML.
- [ ] **Step 5: Run `py -m pytest tests/ui/test_status_viewmodel.py tests/ui/test_queue_viewmodel.py -q`.** Expected: PASS.
- [ ] **Step 6: Extend the application entry point** to wire injected services to QML and keep worker controls disabled until typed host readiness and broker capability both pass.
- [ ] **Step 7: Commit** the view-model, fake-service, and application-wiring changes.

### Task 2: Build the fox pet and accessible handscroll panel

**Files:**
- Modify: `src/gitkeeper/ui/qml/Main.qml`
- Modify: `src/gitkeeper/ui/qml/FoxPet.qml`
- Modify: `src/gitkeeper/ui/qml/HandscrollPanel.qml`
- Modify: `src/gitkeeper/ui/qml/PRStatusPage.qml`
- Modify: `src/gitkeeper/ui/qml/QueuePage.qml`
- Modify: `src/gitkeeper/ui/qml/AccessibleStatus.qml`
- Test: `tests/ui/test_qml_accessibility.py`

**Interfaces:**
- Consumes: Task 1 view-model properties/signals.
- Produces: Clicking the original nine-tailed fox opens the handscroll panel; status/queue screens render accessible text and controls from the view models.

- [ ] **Step 1: Write QML tests** for pet click/open/close, status text, queue state text, keyboard navigation, accessible labels, and no color-only status encoding.
- [ ] **Step 2: Run `py -m pytest tests/ui/test_qml_accessibility.py -q`.** Expected: FAIL on any unimplemented accessibility, motion, and command-approval behaviors.
- [ ] **Step 3: Extend `FoxPet.qml`** as an original fox-spirit visual; include static/reduced/disabled-motion states without copied character likeness or assets.
- [ ] **Step 4: Extend `HandscrollPanel.qml`, `PRStatusPage.qml`, `QueuePage.qml`, and `AccessibleStatus.qml`** bound only to the status/queue view models.
- [ ] **Step 5: Run the QML tests in offscreen mode:** PowerShell `$env:QT_QPA_PLATFORM='offscreen'; py -m pytest tests/ui/test_qml_accessibility.py -q`. Expected: PASS.
- [ ] **Step 6: Commit** the pet/panel components and accessibility tests.

### Task 3: Add settings, manual review feedback, and config-PR publication

**Files:**
- Modify: `src/gitkeeper/ui/viewmodels/settings.py`
- Modify: `src/gitkeeper/ui/services/config_publisher.py`
- Modify: `src/gitkeeper/ui/qml/SettingsPage.qml`
- Modify: `src/gitkeeper/ui/qml/PRStatusPage.qml`
- Test: `tests/fakes/fake_config_publisher.py`
- Test: `tests/ui/test_settings_viewmodel.py`
- Test: `tests/ui/test_config_publisher.py`
- Test: `tests/ui/test_feedback_submission.py`

**Interfaces:**
- Consumes: Task 1 `QueueService`; GitHub/config API interfaces from the local queue/worker plan.
- Produces: typed remote `CompanionSettings` and `ConfigPullRequest` view state; manual feedback action calls `submit_review_feedback` with explicitly selected PR and current SHA.
- Remote config contains no OpenCode model ID or host/repository allowlists. Worker controls require typed `HostModeStatus` and verified broker capability from the host-mode plan; `ACCEPTED_HOST_MODE` alone never enables them.

- [ ] **Step 1: Write settings tests** for valid/invalid remote config fields, secret values, and unchanged merged settings until config PR merge; assert local policy never appears in config PR payloads; test config-PR status transitions through open, merged, and rejected.
- [ ] **Step 2: Write feedback tests** for current-SHA submission and stale-SHA rejection unless the user explicitly re-associates feedback.
- [ ] **Step 3: Run `py -m pytest tests/ui/test_settings_viewmodel.py tests/ui/test_config_publisher.py tests/ui/test_feedback_submission.py -q`.** Expected: FAIL on any unimplemented local-policy/config-publication behavior.
- [ ] **Step 4: Extend `SettingsViewModel` and `ConfigPublisher`** to validate the strict remote config schema before creating/updating a config PR; never write the default branch directly.
- [ ] **Step 5: Implement remote worker-enable/polling/motion settings** and display config PR lifecycle and validation errors. Local repository/model allowlists and accepted-host-risk disclosure are implemented by the host-mode plan.
- [ ] **Step 6: Add the manual Claude feedback form** showing selected PR, head SHA, and text preview; require explicit submit and never connect to Claude automatically.
- [ ] **Step 7: Run `py -m pytest tests/ui/test_settings_viewmodel.py tests/ui/test_config_publisher.py tests/ui/test_feedback_submission.py -q`.** Expected: PASS.
- [ ] **Step 8: Commit** settings, feedback transfer, config-PR UI/service, and tests.

### Task 4: Add local telemetry views and retention presentation

**Files:**
- Modify: `src/gitkeeper/ui/viewmodels/telemetry.py`
- Modify: `src/gitkeeper/ui/qml/TelemetryPage.qml`
- Test: `tests/ui/test_telemetry_viewmodel.py`

**Interfaces:**
- Consumes: local telemetry service from the queue/worker plan.
- Produces: a view of timestamps/durations, PR/revision identifiers, state transitions, attempt count, outcome, selected model ID, and categorized errors; no source content.

- [ ] **Step 1: Write tests** asserting only allowed operational fields render, feedback/source/diffs/prompts/credentials/screenshots/full OpenCode I/O are absent, retention can be shortened, and expired entries are not shown.
- [ ] **Step 2: Run `py -m pytest tests/ui/test_telemetry_viewmodel.py -q`.** Expected: FAIL on any unimplemented retention/privacy presentation behavior.
- [ ] **Step 3: Implement `TelemetryViewModel`** as a read-only projection of the telemetry service and show the 90-day default retention.
- [ ] **Step 4: Implement `TelemetryPage.qml`** with text-first states and categorized errors.
- [ ] **Step 5: Run `py -m pytest tests/ui/test_telemetry_viewmodel.py -q`.** Expected: PASS.
- [ ] **Step 6: Commit** the telemetry view model, QML, and tests.

### Task 5: Integrate the real service and verify complete UI behavior

**Files:**
- Modify: `src/gitkeeper/ui/app.py`
- Modify: `src/gitkeeper/ui/viewmodels/status.py`
- Modify: `src/gitkeeper/ui/viewmodels/queue.py`
- Modify: `src/gitkeeper/ui/viewmodels/settings.py`
- Modify: `src/gitkeeper/ui/viewmodels/telemetry.py`
- Test: `tests/ui/test_companion_integration.py`

**Interfaces:**
- Consumes: implemented `QueueService`, config publisher, telemetry service, `HostModeStatus`, and verified command broker from the local queue/worker and host-mode readiness plans.
- Produces: Windows companion with all four panel areas and working controls. Worker execution remains disabled unless local policy and full CLI command mediation are verified; risk acceptance alone does not authorize launch.

- [ ] **Step 1: Write integration tests** for pause/resume, cancel, explicit retry, GitHub offline/stale status, config PR publication, current-SHA feedback, host acceptance without readiness, and broker approval/deny preventing bypass.
- [ ] **Step 2: Run `py -m pytest tests/ui/test_companion_integration.py -q`.** Expected: FAIL until production service wiring exists.
- [ ] **Step 3: Replace test fakes with injected production services** in the application composition root; keep view-model interface unchanged.
- [ ] **Step 4: Run `py -m pytest tests/ui -q`.** Expected: PASS including no-motion/accessibility cases.
- [ ] **Step 5: Run the UI manually on Windows** and verify pet click opens the panel, all four areas load, no-motion states retain all controls, and GitHub/worker errors are surfaced without credentials or source content.
- [ ] **Step 6: Commit** integration wiring and final UI tests.

## Dependency handoff

Tasks 1–4 extend the existing companion prototype and can be tested against service fakes before the Actions contract or local queue/worker service exists. Task 5 integrates those services. The UI may display queue/config state in accepted host mode, but it must keep worker execution disabled until owner policy, CLI capability, and complete command-broker mediation are verified.

## Authentication implementation note

GitKeeper currently has no established authentication module. The spec requires GitHub operations through the user's authenticated identity but does not choose a credential provider. Implement the UI against the injected GitHub/config service interface; before wiring a concrete provider, select one that satisfies least privilege and keeps credential values out of QML and explicit OpenCode environment injection. Because execution is in accepted same-user host mode, do not claim user-accessible GitHub credentials are inaccessible to OpenCode or child processes. Do not invent a token storage location.
