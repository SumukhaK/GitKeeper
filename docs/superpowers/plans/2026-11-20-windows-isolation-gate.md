# Windows Isolation Feasibility Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a Windows OS-isolation prototype that proves PR-controlled commands cannot access host/other secrets and that the OpenCode Go worker can operate without exposing credentials; produce an explicit GO or NO-GO decision.

**Architecture:** Keep this prototype isolated from production worker activation. Evaluate Windows isolation candidates against a fixed threat test suite, use synthetic host-secret canaries for escape probes, and separately verify a user-selected OpenCode model can be used without making credentials visible to PR-controlled commands. Only a documented passing result unlocks downstream worker implementation; if the boundary cannot be proved, stop with NO-GO and keep the worker disabled.

**Tech Stack:** Windows OS isolation candidate(s) chosen during the prototype, Python harness, pytest, OpenCode CLI using the user-selected model ID.

**Spec:** `docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md`

## Global Constraints

- GitKeeper application, companion, and worker code belong only in the private GitKeeper repository. Setu-mobile receives only the GitHub Actions integration and its configuration.
- GitHub Actions is the sole test gate and runs `./gradlew testDebugUnitTest detekt lint` on PR `opened` and `synchronize`, and again for every companion push to the PR branch.
- The companion and worker do not run the required test suite locally as a prerequisite to publishing.
- Every worker invocation uses a dedicated PR worktree; a worktree alone is not a security sandbox.
- The worker must not inherit GitHub credentials, desktop sign-in tokens, or unrelated environment secrets.
- No automatic or user-triggered worker execution launches until a Windows OS-isolation prototype has been built and its isolation properties verified.
- Prompt instructions and the PR-author allowlist are never security controls and do not satisfy the isolation gate.
- OpenCode CLI is the only pilot worker; the user selects an exact OpenCode model ID in settings; no ID is hard-coded or guessed.
- OpenCode receives no GitHub credentials.
- If isolation or credential separation cannot be proven, return NO-GO and keep all worker execution disabled.

## Review Focus

- Host-file access from an isolated command must fail: Task 2 asserts denied access to a synthetic host-only canary.
- Host process/environment inspection must not reveal canary secrets: Task 2 runs child-process and environment probes.
- Network egress must not permit secrets to leave the isolation boundary: Task 2 uses a controlled local receiver and asserts no secret payload arrives.
- OpenCode Go must operate with the configured model while project-controlled commands cannot read OpenCode/GitHub credentials: Task 3 runs a guarded integration probe; any exposure or inconclusive result is NO-GO.
- A failed, incomplete, or unsupported Windows capability check must not enable execution: Task 4 tests gate evaluation and requires an explicit passing report.

---

## Planned file inventory (GitKeeper repository)

- Create: `prototypes/windows-isolation/README.md` — candidate assumptions, setup, threat model, repeatable manual probe procedure, and result format.
- Create: `prototypes/windows-isolation/runner.py` — prototype-only launch/control harness; not used by production.
- Create: `prototypes/windows-isolation/probe_payload.py` — adversarial command probes using synthetic canary values.
- Create: `pyproject.toml` — initial private Python package and test harness; later plans extend this single project.
- Create: `tests/isolation/test_windows_boundary.py` — host file/process/environment/network boundary tests.
- Create: `tests/isolation/test_opencode_boundary.py` — OpenCode Go credential-separation integration tests, with model selected from user settings.
- Create: `src/gitkeeper/isolation/report.py` — typed `IsolationReport` and explicit pass/fail fields; GO and NO-GO reports both feed a fail-closed gate.
- Create: `docs/superpowers/isolation/windows-prototype-result.md` — evidence, limitations, and GO/NO-GO decision.
- No existing GitKeeper source module or packaging is assumed; the first Python/package scaffolding belongs to this task.

## Interfaces

- **Consumes:** Windows host/runtime capabilities, a disposable PR worktree, synthetic canary secrets, the user's already-configured OpenCode Go subscription, and the model ID selected at runtime by the user.
- **Produces:** `IsolationReport` with `verdict: Literal["GO", "NO_GO"]`, tested boundary assertions, OpenCode operation result, and evidence references. A GO requires every required isolation assertion and the OpenCode operation probe to pass.
- **Downstream contract:** The local queue/worker plan may depend on `IsolationGate.require_verified(report) -> None`, which raises/returns a hard failure unless the report is GO and corresponds to the current prototype version. No other plan may enable worker launch before this result.
- **Stop rule:** If a candidate cannot meet an assertion or the OpenCode credential boundary cannot be proven without exposing credentials to PR-controlled commands, record NO-GO and stop. Do not silently relax the threat model or proceed to worker activation.

### Task 1: Define the isolation threat model and result contract

**Files:**
- Create: `pyproject.toml`
- Create: `prototypes/windows-isolation/README.md`
- Create: `src/gitkeeper/isolation/report.py`
- Test: `tests/isolation/test_report.py`

**Interfaces:**
- Produces `IsolationReport` with `verdict`, `prototype_id`, `host_probe_results`, `credential_probe_result`, `opencode_model_configured`, `opencode_call_succeeded`, and `evidence_paths`.
- `isolation_passes(report: IsolationReport) -> bool` returns true only when every required probe is passed and the OpenCode operation succeeded without any credential exposure.
- `ProbeResult` is `{name: str, passed: bool, failure_category: str | None}`; `IsolationReport` contains `host_probe_results: list[ProbeResult]` and a credential probe with the same shape. Missing assertions are failures, not implicit successes.

- [ ] **Step 1: Write tests** asserting an all-pass report returns GO, any failed/missing probe returns NO_GO, an unset model selection cannot pass, and a report from another prototype ID cannot unlock the gate.
- [ ] **Step 2: Create the root `pyproject.toml`** with the `src` package layout and a `test` extra containing pytest, without assuming any existing package. Add no worker or UI runtime dependencies in this prototype task.
- [ ] **Step 3: Install the test extra** with `py -m pip install -e ".[test]"`.
- [ ] **Step 4: Run `py -m pytest tests/isolation/test_report.py -q`.** Expected: FAIL because the report type and evaluator do not exist.
- [ ] **Step 5: Define `IsolationReport` and `isolation_passes(report: IsolationReport) -> bool`** in `src/gitkeeper/isolation/report.py`; require explicit values for every security assertion and prohibit pass-shaped defaults.
- [ ] **Step 6: Write the threat model and non-negotiable assertions** in the prototype README, including the no-go rule and synthetic-canary-only probing.
- [ ] **Step 7: Run `py -m pytest tests/isolation/test_report.py -q`.** Expected: PASS.
- [ ] **Step 8: Commit** the package scaffold, report contract, tests, and threat-model README.

### Task 2: Probe Windows OS isolation boundaries

**Files:**
- Create: `prototypes/windows-isolation/runner.py`
- Create: `prototypes/windows-isolation/probe_payload.py`
- Test: `tests/isolation/test_windows_boundary.py`

**Interfaces:**
- Consumes: Task 1 threat model/report contract.
- `IsolationCandidate` exposes `prototype_id: str` and `launch(command: Sequence[str], worktree: Path, env: Mapping[str, str]) -> CompletedProcess[str]`.
- `CanarySet` contains only synthetic host-file contents, environment values, process metadata marker, write-denial path, and controlled receiver URL.
- Produces: `run_host_probes(candidate: IsolationCandidate, canaries: CanarySet) -> list[ProbeResult]`.
- Probe results cover access to a host-only file, parent/other process metadata, environment variables, writable paths, and controlled network egress. Probe commands execute only synthetic data and harmless test payloads.

- [ ] **Step 1: Write Windows-only tests** for host-only file denial, process/environment canary denial, write denial outside the disposable worktree, and no secret-bearing request reaching a controlled local receiver.
- [ ] **Step 2: Run `py -m pytest tests/isolation/test_windows_boundary.py -q`.** Expected: FAIL until a candidate harness is installed and wired.
- [ ] **Step 3: Document and implement one candidate isolation backend in the prototype harness** without making it a production default; keep candidate selection/evaluation reversible.
- [ ] **Step 4: Implement `run_host_probes(candidate, canaries)`** and collect raw pass/fail evidence without recording actual user secrets.
- [ ] **Step 5: Run `py -m pytest tests/isolation/test_windows_boundary.py -q` on a supported Windows host.** Expected: PASS only when all denied operations are actually blocked; unsupported platform/capability is an explicit failure.
- [ ] **Step 6: Repeat the suite under a fresh disposable worktree and a second process launch** to check that isolation is not dependent on stale state.
- [ ] **Step 7: Commit** the prototype and reproducible host-boundary probes.

### Task 3: Verify OpenCode Go operation without credential exposure

**Files:**
- Modify: `prototypes/windows-isolation/runner.py`
- Create: `tests/isolation/test_opencode_boundary.py`
- Modify: `prototypes/windows-isolation/README.md`

**Interfaces:**
- Consumes: Task 2 passing isolation candidate; user's authenticated OpenCode Go installation; model ID selected in user settings (never hard-coded in test or plan).
- Produces: `probe_opencode_boundary(candidate, selected_model_id) -> OpenCodeProbeResult`, recording whether the selected worker could complete a harmless disposable-worktree task and whether any PR-controlled process could observe authentication material.
- `OpenCodeProbeResult` contains `model_id_configured: bool`, `opencode_call_succeeded: bool`, `credential_probe_results: list[ProbeResult]`, and `evidence_paths: list[Path]`; only all-true/no-exposure results can contribute to GO.

- [ ] **Step 1: Write a guarded integration test** that requires an explicit user-provided model selection, launches a harmless task in a disposable worktree, and asserts authentication material is absent from the task process environment, accessible files, logs, and network probes.
- [ ] **Step 2: Run `py -m pytest tests/isolation/test_opencode_boundary.py -q`.** Expected: SKIP only when no model is configured for this explicit manual gate; otherwise FAIL until the credential boundary is implemented.
- [ ] **Step 3: Implement only the prototype credential boundary needed to make the probe meaningful.** Do not copy tokens into PR-readable environment/files; do not log token values or model request bodies.
- [ ] **Step 4: Run `py -m pytest tests/isolation/test_opencode_boundary.py -q` with the user's selected model.** Expected: PASS only if the task succeeds and all credential-read/exfiltration probes fail closed.
- [ ] **Step 5: Treat an inconclusive, rate-limited, unsupported, or credential-visible result as NO-GO**, record the limitation, and keep production execution disabled.
- [ ] **Step 6: Commit** the probe and evidence-handling changes.

### Task 4: Decide and enforce GO/NO-GO

**Files:**
- Modify: `src/gitkeeper/isolation/report.py`
- Create: `docs/superpowers/isolation/windows-prototype-result.md`
- Test: `tests/isolation/test_gate.py`

**Interfaces:**
- Consumes: Task 2 host probe results and Task 3 OpenCode boundary result.
- Produces: `require_verified(report: IsolationReport, expected_prototype_id: str) -> None`, which returns only for a complete GO report and fails closed otherwise.

- [ ] **Step 1: Write gate tests** for complete GO, any failed assertion, missing evidence, unsupported host, wrong prototype ID, and OpenCode credential exposure; only complete GO permits the gate.
- [ ] **Step 2: Run `py -m pytest tests/isolation/test_gate.py -q`.** Expected: FAIL until the gate is implemented.
- [ ] **Step 3: Implement `require_verified(report, expected_prototype_id)`** with no override flag or silent default.
- [ ] **Step 4: Record the evidence and explicit GO/NO-GO decision** in `windows-prototype-result.md`. A NO-GO is a valid completed outcome and blocks all later worker launch.
- [ ] **Step 5: Run `py -m pytest tests/isolation -q`.** Expected: PASS for all assertions; a NO-GO report still passes the gate-behavior tests but cannot unlock execution.
- [ ] **Step 6: Commit** the gate implementation and result record.

## Dependency handoff

The local queue/worker plan may begin implementation only after this plan's Task 4 produces a verified GO report. A NO-GO result pauses worker implementation/activation; do not substitute another model, run locally without isolation, or weaken a failed assertion.
