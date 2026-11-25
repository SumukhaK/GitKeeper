# GitKeeper Accepted Host-Mode Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish `ACCEPTED_HOST_MODE` readiness and enforce the owner's repository/model allowlists plus per-command companion approval before any worker-requested host shell command runs.

**Architecture:** OpenCode CLI `1.19.6` runs as the owner on Windows 10 Home. This is not a sandbox and does not provide a separate-user credential boundary. A host-mode readiness service checks local owner policy, exact PR eligibility, worktree location, CLI/model configuration, and a tested command-broker capability. Every shell request must pass through the companion for one-time approval. If CLI `1.19.6` can bypass or evade the broker, the readiness result remains blocked and no worker launch is permitted.

**Tech Stack:** Python, PySide6/QML, OpenCode CLI `1.19.6`, Git worktrees, pytest.

**Spec:** `docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md`

## Global Constraints

- The owner has accepted host-execution risk and waived Windows Sandbox/VM/WSL2 and separate-user credential-boundary requirements. Record this as `ACCEPTED_HOST_MODE`, never as isolation or sandbox verification.
- OpenCode and approved commands run as the owner's logged-in Windows user and may access host files and credentials available to that user. A worktree, repository allowlist, model allowlist, prompt, or confirmation UI does not confine that access.
- The target public repository `SumukhaK/Setu-mobile` is the explicit public-repository exception. Additional repositories require an explicit owner-managed allowlist entry and verification that the repository is private and owner-controlled; only repositories with the GitKeeper Actions/config contract are supported. Forks and external PR heads are never worker-eligible.
- The PR author must be exactly `SumukhaK`; the PR must be open/non-draft, same-repository head/base, enabled by merged configuration, current-SHA eligible, and within the retry policy.
- Only exact, locally configured OpenCode subscription IDs with the `opencode/` prefix are allowed. The allowlist is empty until explicitly configured; do not guess IDs or accept other providers. This policy and selected ID are local-only, not Setu-mobile config.
- Require OpenCode CLI version `1.19.6`; do not install, upgrade, or substitute a CLI automatically.
- Every worker-requested host shell command requires a fresh, explicit companion confirmation tied to its exact executable/arguments (or complete script), cwd, repository, PR/head SHA, purpose, and environment variable names. Approval is one-use and expires; any change requires a new approval.
- If command interception is incomplete, bypassable, the approval UI is unavailable, or the request cannot be displayed completely, deny and block. No background/unattended command execution, command chaining, or approval carry-over.
- Use a PR/SHA-specific Git worktree outside both repositories and verify the user's active working tree is not modified. This is repository-state hygiene only, not host isolation.
- GitHub Actions alone runs `./gradlew testDebugUnitTest detekt lint` after PR open/synchronize and after the companion push. Do not run these Gradle tests locally as a publishing prerequisite.
- Preserve the approved queue semantics, manual Claude feedback flow, three-automatic-invocation budget per open/reopen cycle, exact-SHA publication check, patch structure/scope validation, normal fast-forward push to the existing PR branch, and prohibition on force-push/main.
- The control plane does not intentionally inject GitHub credentials into OpenCode, but host mode cannot guarantee user-accessible GitHub credentials remain inaccessible to OpenCode or child processes.

## Review Focus

- A fork, external PR, non-allowlisted repo, other author, or public non-exception repo cannot become claimable: Task 1 tests require an ineligible result.
- An empty model allowlist, provider ID outside `opencode/`, or ID absent from the exact list cannot launch OpenCode: Task 1 tests require denial.
- `ACCEPTED_HOST_MODE` never reports isolation and does not itself permit launch: Task 1 tests distinguish acceptance from readiness.
- Every shell request requires exact one-shot approval and direct OpenCode execution cannot bypass it: Tasks 2â€“3 test request binding/replay and the actual CLI hook; inability to prove mediation is an explicit no-go, not a skipped test.
- Worktree lifecycle preserves the active checkout, and publication blocks on stale SHA or failed scope validation: Task 4 tests branch/HEAD/dirty-file snapshots and publication rejection.

---

## Planned file inventory (GitKeeper repository)

- Modify: `src/gitkeeper/isolation/report.py` â€” retire OS-isolation GO semantics from worker readiness; retain any report type only if still needed for historical records, without allowing it to gate or authorize host execution.
- Create: `src/gitkeeper/host_mode/readiness.py` â€” typed `HostModePolicy`, `HostModeStatus`, and fail-closed eligibility/readiness evaluation.
- Create: `src/gitkeeper/host_mode/command_broker.py` â€” immutable command request, one-time approval token, execution adapter, timeout/deny paths, and audit metadata.
- Create: `src/gitkeeper/host_mode/opencode_capability.py` â€” detects CLI version and verifies that all shell requests pass through the broker; no worker launch when unsupported.
- Modify: `src/gitkeeper/ui/app.py`, `src/gitkeeper/ui/viewmodels/settings.py`, and `src/gitkeeper/ui/services/config_publisher.py` â€” pass typed host-mode readiness instead of a boolean or `IsolationReport`; never publish host/repo/model allowlists remotely.
- Modify: `src/gitkeeper/ui/viewmodels/queue.py` and `src/gitkeeper/ui/qml/QueuePage.qml` â€” surface pending command approvals and deny/block outcomes without showing success before confirmation.
- Create: `src/gitkeeper/ui/viewmodels/command_approval.py` â€” safe presentation and explicit approve/deny actions for immutable broker requests.
- Modify: `src/gitkeeper/ui/qml/SettingsPage.qml` â€” owner-managed repo/model allowlists and explicit accepted-host-mode disclosure.
- Modify: `src/gitkeeper/ui/qml/HandscrollPanel.qml` â€” command confirmation overlay/dialog integrated into the existing four-area panel.
- Create: `src/gitkeeper/ui/qml/CommandApprovalDialog.qml` â€” full immutable request display and explicit approve/deny controls.
- Create: `tests/host_mode/test_readiness.py`, `tests/host_mode/test_command_broker.py`, `tests/host_mode/test_opencode_capability.py`, and `tests/ui/test_command_approval.py`.
- Modify: `tests/ui/test_app.py`, `tests/ui/test_settings_viewmodel.py`, `tests/ui/test_config_publisher.py`, `tests/ui/test_queue_viewmodel.py`, and `tests/ui/test_qml_accessibility.py`.
- Modify: `docs/superpowers/isolation/windows-prototype-result.md` to mark the earlier NO-GO as superseded by the owner's explicit host-risk acceptance, preserving the original evidence and stating that no isolation was achieved.
- No OpenCode model ID, token, or additional repository is assumed or added by this plan.

## Interfaces

- `HostModePolicy` contains the owner-accepted mode marker, exact allowed repository full names, explicit public exceptions, verified-private owner repositories, exact model IDs, and expected CLI version. Policy comes from local owner-controlled state, never merged repository config or PR input.
- `evaluate_worker_readiness(pr, config, policy, cli_capability) -> HostModeStatus` checks repo ownership/privacy/allowlisting, same-repo head, exact author, open/non-draft state, merged worker enablement, current-SHA repair reason, attempt budget, `opencode/` prefix plus exact model membership, expected CLI version, and complete broker capability. `ACCEPTED_HOST_MODE` alone never yields ready.
- `CommandRequest` is immutable and includes executable, argv or full script, cwd, environment variable names (never secret values), repository, PR number, starting SHA, and purpose. Canonical serialization produces a request digest.
- `ApprovalBroker.request_approval(request) -> ApprovalDecision` returns deny on timeout, UI disconnect, incomplete rendering, stale PR/SHA, or a request-digest mismatch. `execute_approved(request, decision)` accepts only a matching one-use decision and invokes the exact reviewed request; no shell-string rewriting or interpolation occurs between confirmation and execution.
- `OpenCodeCapability.verify(cli_path, expected_version, broker_adapter) -> CapabilityResult` proves that every shell invocation is routed through `ApprovalBroker`. If the CLI has no enforceable hook for the installed version, it returns unsupported and blocks worker launch.
- Queue/UI receives typed pending-approval status and safe audit metadata (request digest, approval outcome/time, category); telemetry never stores raw script/command, environment values, source content, or prompts.
- The queue/worker plan consumes `HostModeStatus`; it must not use a bool override, prompt assertion, or historical isolation report to authorize a run.

### Task 1: Model accepted host readiness and owner policy

**Files:**
- Create: `src/gitkeeper/host_mode/readiness.py`
- Modify: `src/gitkeeper/isolation/report.py`
- Test: `tests/host_mode/test_readiness.py`
- Modify: `tests/isolation/test_gate.py`, `tests/isolation/test_report.py`

**Interfaces:**
- Produces `HostModePolicy`, `HostModeStatus`, `RepositoryPolicy`, `evaluate_worker_readiness(...)`, and exact model/repository validators.
- Historical isolation reports remain informational only and cannot unlock or block accepted host mode through a hidden boolean path.

- [x] **Step 1: Write failing tests** for accepted mode not equaling readiness, unknown/public repository denied, explicit Setu-mobile public exception, owner-controlled private repo allowed only when allowlisted and integration-enabled, forks denied, author/draft/closed/config/SHA/budget failures denied, and exact `opencode/...` model allowlisting.
- [x] **Step 2: Run `py -m pytest tests/host_mode/test_readiness.py tests/isolation -q`.** Observed: FAIL because host readiness types do not exist and the old gate contract remains.
- [x] **Step 3: Implement typed readiness/policy validation** without any bool bypass. Require the exact public exception and exact case-sensitive owner/repo/model entries.
- [x] **Step 4: Add tests** that an unmerged config, remote config allowlist, arbitrary provider ID, or empty local allowlist never produces ready status.
- [x] **Step 5: Run `py -m pytest tests/host_mode/test_readiness.py tests/isolation -q`.** Observed: PASS; host acceptance remains clearly distinct from isolation.
- [ ] **Step 6: Commit** only after the updated spec and plan have been reviewed and the user selects execution mode.

### Task 2: Implement the one-shot command broker and approval UI

**Files:**
- Create: `src/gitkeeper/host_mode/command_broker.py`
- Create: `src/gitkeeper/ui/viewmodels/command_approval.py`
- Modify: `src/gitkeeper/ui/viewmodels/queue.py`
- Modify: `src/gitkeeper/ui/qml/HandscrollPanel.qml` and `src/gitkeeper/ui/qml/QueuePage.qml`
- Create: `src/gitkeeper/ui/qml/CommandApprovalDialog.qml`
- Test: `tests/host_mode/test_command_broker.py`
- Test: `tests/ui/test_command_approval.py`
- Modify: `tests/ui/test_qml_accessibility.py`

**Interfaces:**
- Consumes immutable worker command requests. Until Task 3 proves CLI mediation, production execution remains disabled.
- Produces a pending-approval state, explicit approve/deny actions, and execution only after an exact matching one-use approval.

**Outcome:** Steps 1-7 are done. `command_broker.py` carries 66 tests, `command_approval.py` 26, and `CommandApprovalDialog.qml` 6 QML tests; full suite is 318 passing. Deviation from the file list above: `viewmodels/queue.py` and `QueuePage.qml` needed no change, because a pending approval is presented in its own overlay and does not alter queue item state. Production execution stays disabled (`execution_disabled`); Task 3 must supply the mediated executor.

- [x] **Step 1: Write failing broker tests** for approve-once, deny, timeout, UI disconnect, partial request display, changed argv/cwd/repo/SHA/env, replay, and token expiry.
- [x] **Step 2: Run `py -m pytest tests/host_mode/test_command_broker.py -q`.** Expected: FAIL before the broker exists.
- [x] **Step 3: Implement canonical request digesting and a one-use approval record.** Never pass arbitrary shell text through string interpolation; show full script when a shell interpreter is requested.
- [x] **Step 4: Write UI tests** asserting request details are visible as plain text, approve/deny require explicit user input, no response denies, and status is accessible by keyboard/screen reader.
- [x] **Step 5: Implement the approval overlay and queue state.** Do not present an action as successful until the broker confirms execution outcome.
- [x] **Step 6: Run `py -m pytest tests/host_mode/test_command_broker.py tests/ui/test_command_approval.py tests/ui/test_qml_accessibility.py -q`.** Expected: PASS.
- [x] **Step 7: Verify reduced and disabled motion preserve the approval controls and command details.**

### Task 3: Prove OpenCode 1.19.6 command interception feasibility

**Files:**
- Create: `src/gitkeeper/host_mode/opencode_capability.py`
- Create: `tests/host_mode/test_opencode_capability.py`
- Create: `docs/superpowers/host-mode-command-broker-result.md`

**Interfaces:**
- Consumes the locally installed OpenCode CLI version, the Task 2 approval broker/UI, and an explicitly user-approved harmless capability probe.
- Produces a capability result that is passing only when all shell requests are mediated and direct bypass paths fail closed.

- [ ] **Step 1: Write failing unit tests** for exact version match, missing CLI, unsupported hook API, unmediated shell attempt, broker disconnect, and successful broker-mediated request using a fake CLI adapter.
- [ ] **Step 2: Run `py -m pytest tests/host_mode/test_opencode_capability.py -q`.** Expected: FAIL before the capability contract exists.
- [ ] **Step 3: Inspect the installed OpenCode 1.19.6 command/tool extension points and document the specific hook that can mediate all shell requests.** Do not run PR code or install/upgrade packages during this inspection.
- [ ] **Step 4: Implement only the supported hook adapter.** If the installed CLI cannot guarantee complete mediation, record `UNSUPPORTED`/NO-GO and stop this task; do not fall back to prompt instructions or direct subprocess launch.
- [ ] **Step 5: Through the Task 2 companion approval UI, explicitly confirm a harmless command in a disposable non-repository directory. Verify the request pauses for approval, exact-request changes invalidate approval, denial prevents execution, and no direct shell tool bypass exists.**
- [ ] **Step 6: Run `py -m pytest tests/host_mode/test_opencode_capability.py -q`.** Expected: PASS for a verified hook, or a durable fail-closed unsupported result if the hook is unavailable.
- [ ] **Step 7: Record the CLI version, tested hook, command shapes covered, result, and known limitations** without recording credentials or command secrets. The result cannot claim OS isolation.

### Task 4: Wire host-mode readiness and verify worktree/publication invariants

**Files:**
- Modify: `src/gitkeeper/ui/app.py`, `src/gitkeeper/ui/viewmodels/settings.py`, `src/gitkeeper/ui/services/config_publisher.py`, and `src/gitkeeper/ui/qml/SettingsPage.qml`
- Modify: `src/gitkeeper/worker/worktree.py`, `src/gitkeeper/worker/opencode.py`, and `src/gitkeeper/worker/publish.py` when those modules are implemented by the worker plan.
- Test: `tests/ui/test_app.py`, `tests/ui/test_settings_viewmodel.py`, `tests/ui/test_config_publisher.py`
- Test: `tests/unit/worker/test_worktree.py`, `tests/unit/worker/test_opencode.py`, `tests/unit/worker/test_publish.py`
- Modify: `docs/superpowers/isolation/windows-prototype-result.md`

**Interfaces:**
- Composition and publication consume typed `HostModeStatus`, not `IsolationReport` or `isolation_verified: bool`.
- Worktree manager establishes a unique PR/SHA worktree under a managed root and validates the owner's active checkout remains unchanged.
- OpenCode launch requires readiness and selected-model membership; all shell requests route through the verified broker.

- [x] **Step 1: Write failing tests** proving composition/config publisher cannot accept a boolean or historical GO report, and only a passing readiness result enables launch.
- [ ] **Step 2: Test worktree isolation as repository-state hygiene:** snapshot active checkout HEAD/branch/status, create/use/cancel a separate worktree, and assert the active checkout is unchanged.
- [ ] **Step 3: Test publication** preserves existing structure/scope checks and exact remote starting-SHA comparison; retain normal fast-forward only, no force push/main, and no local Gradle invocation.
- [ ] **Step 4: Replace UI/config gate wiring** with the typed host-mode readiness contract; keep allowlists and owner acceptance out of remote config PRs.
- [ ] **Step 5: Update the prior isolation result record** to preserve its NO-GO evidence and mark it superseded only by the explicit owner risk decision, not by a GO or a successful isolation probe.
- [ ] **Step 6: Run `py -m pytest tests/host_mode tests/isolation tests/ui -q`.** Expected: PASS; worker launch still blocks unless Task 2 proved broker mediation and required local policy is configured.

## Dependency handoff

Task 1 establishes the readiness contract. Task 2 builds and tests the approval broker/UI while worker launch remains disabled. Task 3 then performs the mandatory CLI feasibility probe through that UI before any live worker integration. Task 4 wires passing readiness into the app and queue/worker implementation. The Actions/config plan remains independently testable, but only owner-allowlisted same-repository PRs with the approved integration can be claimed. A failed or unsupported broker result blocks worker launch while preserving the owner's `ACCEPTED_HOST_MODE` decision and documented residual risk.
