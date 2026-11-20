# Setu-mobile Actions and Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Setu-mobile Actions/config contract that runs secret-free PR CI and queues only eligible deterministic failures for local repair.

**Architecture:** Add PR CI with `pull_request` triggers and a separate trusted `workflow_run` state workflow on the protected default branch. The state workflow reads the current run/check metadata and default-branch JSON config through GitHub APIs, classifies only explicit deterministic check failures as repair work, and writes the three queue labels without checking out or executing PR code or consuming artifacts.

**Tech Stack:** GitHub Actions YAML, Kotlin/Gradle, JSON, Node.js built-in test runner, actionlint.

**Spec:** `docs/superpowers/specs/2026-11-20-gitkeeper-setu-mobile-pilot-design.md`

**Implementation target:** `SumukhaK/Setu-mobile`; all `.github/...` and `tests/...` paths below are relative to that repository, not the current docs-only GitKeeper checkout.

## Global Constraints

- GitKeeper application, companion, and worker code belong only in the private GitKeeper repository. Setu-mobile receives only the GitHub Actions integration and its configuration.
- CI uses `pull_request`, never `pull_request_target`.
- GitHub Actions is the sole test gate and runs `./gradlew testDebugUnitTest detekt lint` on PR `opened` and `synchronize`, and again for every companion push to the PR branch.
- CI has no repository secrets and only read permissions. Workflow jobs must not use a token to write PR/repository data; GitHub's built-in check-run reporting is expected.
- GitHub labels are `gitkeeper/worker-queued`, `gitkeeper/worker-running`, and `gitkeeper/worker-blocked`.
- The PR author is eligible for automatic worker execution only when the GitHub login is exactly `SumukhaK` and the PR head branch is in `SumukhaK/Setu-mobile` itself. Other authors and all fork PRs receive secret-free CI only.
- The automatic invocation budget is three per open/reopen cycle across head-SHA changes; reopen resets the budget while preserving history. Explicit retries after escalation are user-triggered; no automatic model fallback is used.
- Worker execution remains disabled until the Windows OS-isolation prototype passes; the default merged config keeps it disabled until the user enables it after that gate.
- The exact OpenCode model ID is user-selected; the plan does not guess or hard-code one.
- An explicitly identified deterministic check failure queues repair for that SHA; passing checks mean ready/no worker; infrastructure, cancellation, missing, or unknown outcomes block for human action.
- The state workflow must not check out PR code, download or consume PR-produced artifacts, or execute PR-controlled scripts.
- Configuration is read only from the default branch. Invalid or unmerged config cannot alter Actions or worker behavior.
- No PR queue label alone can authorize worker execution; the companion independently checks PR, author, head SHA, merged config, and repair reason.

## Review Focus

- Fork PR or author mismatch must receive CI but receive no GitKeeper worker label: Task 2 policy tests assert no worker label for both cases.
- A known deterministic check failure versus a runner/setup failure must classify differently: Task 2 classifier tests assert queued only for the former and blocked for the latter.
- Completion for a superseded head SHA must not change labels: Task 2 stale-SHA test asserts no queue transition.
- Config from an unmerged PR or with unknown/permission-bearing keys must not influence worker eligibility: Task 1 rejects bad values; Task 2 workflow-contract tests assert config is read from the default branch only.
- The privileged workflow must not run or import PR-controlled content: Task 2 workflow-contract test and `actionlint` checks pin the no-checkout/no-artifact rule.

---

## Planned file inventory (Setu-mobile repository)

- Create `.github/workflows/gitkeeper-pr-ci.yml` — secret-free read-only Android PR CI on `opened` and `synchronize` (including companion PR-branch pushes).
- Create `.github/workflows/gitkeeper-state.yml` — default-branch `workflow_run` classifier and narrow label writer.
- Create `.github/gitkeeper.json` — merged-default-branch-only worker enablement, user-selected model ID, polling/motion preferences, and fixed eligibility/check identifiers.
- Create `.github/scripts/classify-run.mjs` — pure allowlisted run-result and PR-metadata classifier.
- Create `.github/scripts/validate-config.mjs` — strict config schema validator.
- Create `tests/workflows/classify-run.test.mjs` — actor/fork/SHA/outcome/label classifier tests.
- Create `tests/workflows/validate-config.test.mjs` — valid, invalid, unknown-key, and privilege-key config tests.
- Create `tests/workflows/workflow-contract.test.mjs` — workflow event, permission, default-config source, and no-PR-execution assertions.
- No existing `.github/workflows` directory or GitKeeper source module is assumed.

## Interfaces

- **Consumes:** GitHub `pull_request` check-run results, `workflow_run` metadata, pull request API metadata, and the config JSON read from the default branch only.
- **Produces:** Check conclusions tied to an exact head SHA and at most one label from `gitkeeper/worker-queued`, `gitkeeper/worker-running`, `gitkeeper/worker-blocked`. Explicit deterministic failure may add `worker-queued`; CI pass adds no worker label; eligible infrastructure/cancelled/unknown work adds `worker-blocked`; ineligible PRs get no worker label.
- `RunClassificationInput` contains `repository`, PR number/open/draft state, author login, head-repository full name, current head SHA, source workflow identity, check runs each bound to a SHA and conclusion, and default-branch `PilotConfig`.
- `WorkerLabel = "gitkeeper/worker-queued" | "gitkeeper/worker-running" | "gitkeeper/worker-blocked"`. Only the companion sets `worker-running`; Actions may set queued/blocked or clear an obsolete label.
- `StateDecision` contains `state: "ready" | "queued" | "blocked" | "ineligible" | "stale"`, repository, PR number, current head SHA, `label: WorkerLabel | null`, and a categorized reason. `ineligible` and `stale` have no label and cause no label write. It never trusts a PR-provided label/config value as eligibility input.
- **Configuration contract:** `.github/gitkeeper.json` contains `worker_enabled`, a user-selected `opencode_model_id` (nullable until selected; never guessed), `polling_preferences: {"mode":"adaptive"}`, `motion_preference` (`null`, `reduced`, or `disabled`), and exact recognized check identifiers. Unknown keys and values that imply permissions, credentials, or arbitrary commands are rejected. The config must be merged to the default branch before it can affect Actions or companion behavior.
- **Downstream contract:** The companion treats GitHub labels as a state projection and rechecks author, head repository, PR state, head SHA, merged config, and check/feedback reason before local claim.

### Task 1: Add merged-only pilot config and validator

**Files:**
- Create: `.github/gitkeeper.json`
- Create: `.github/scripts/validate-config.mjs`
- Test: `tests/workflows/validate-config.test.mjs`

**Interfaces:**
- Consumes: JSON object loaded by a caller from the default branch.
- Produces: `validateConfig(value: unknown): { valid: true; config: PilotConfig } | { valid: false; errors: string[] }`.
- `PilotConfig` includes `worker_enabled: boolean`, `opencode_model_id: string | null`, `polling_preferences: { mode: "adaptive" }`, `motion_preference: null | "reduced" | "disabled"`, and an allowlist of exact deterministic check identifiers; it cannot encode tokens, extra Actions permissions, or arbitrary commands.

- [ ] **Step 1: Write config validator tests** for the valid default config, disabled config, null and explicitly selected model IDs (without asserting any guessed ID), polling/motion settings, non-object input, missing/incorrect fields, unknown keys, and a credential/permission/command key.
- [ ] **Step 2: Run the tests and verify failure.** Run in Setu-mobile: `node --test tests/workflows/validate-config.test.mjs`. Expected: FAIL because validator/config are absent.
- [ ] **Step 3: Implement the strict JSON validator** in `.github/scripts/validate-config.mjs`; reject unknown keys and never coerce invalid values.
- [ ] **Step 4: Add the minimal `.github/gitkeeper.json` default** with worker execution disabled and no model ID selected until the user chooses one; include only validated polling/motion preferences and recognized check IDs.
- [ ] **Step 5: Run `node --test tests/workflows/validate-config.test.mjs`.** Expected: PASS for all valid/invalid config cases.
- [ ] **Step 6: Commit** the config, validator, and tests in Setu-mobile.

### Task 2: Add secret-free PR CI and trusted state classification

**Files:**
- Create: `.github/workflows/gitkeeper-pr-ci.yml`
- Create: `.github/workflows/gitkeeper-state.yml`
- Create: `.github/scripts/classify-run.mjs`
- Test: `tests/workflows/classify-run.test.mjs`
- Test: `tests/workflows/workflow-contract.test.mjs`

**Interfaces:**
- Consumes: Task 1 `PilotConfig` and `validateConfig`; GitHub run/check and PR metadata.
- Produces: `classifyRun(input: RunClassificationInput): StateDecision`, where `StateDecision` is `ready`, `queued`, `blocked`, `ineligible`, or `stale`, always associated with `repository`, PR number, and head SHA. Only known test/lint check failure conclusions classify as `queued`.
- The state job reads config/check/PR metadata via GitHub API from the default branch/current event. It does not check out code or consume artifacts from the PR workflow.

- [ ] **Step 1: Write classifier tests** for eligible same-repo `SumukhaK` PRs, forks, other authors, non-open/draft PRs, valid/unknown config, explicit check failure, all-checks pass, workflow/setup failure, timeout, cancellation, missing conclusion, and stale head SHA; assert stale results cause no label write and Actions never sets `worker-running`.
- [ ] **Step 2: Run `node --test tests/workflows/classify-run.test.mjs`.** Expected: FAIL because the classifier is absent.
- [ ] **Step 3: Implement `classifyRun(input: RunClassificationInput): StateDecision`** with a closed allowlist of deterministic check IDs and conclusions. Ineligible PRs return `ineligible` with no label; a result for a superseded SHA returns `stale` with no label write; any absent, cancelled, infrastructure, or unknown result for eligible current work returns `blocked`; any all-pass result returns `ready` with no label.
- [ ] **Step 4: Write a workflow-contract test** asserting PR CI has no secrets/write permissions and the privileged state workflow reads config from the default branch and has no PR checkout, artifact download, PR code execution, or `pull_request_target`.
- [ ] **Step 5: Run `node --test tests/workflows/classify-run.test.mjs tests/workflows/validate-config.test.mjs tests/workflows/workflow-contract.test.mjs`.** Expected: FAIL because workflow files are absent.
- [ ] **Step 6: Implement `gitkeeper-pr-ci.yml`** for `pull_request` opened/synchronize events (including the synchronize event caused by a companion PR-branch push), with no secrets and read permissions, running exactly `./gradlew testDebugUnitTest detekt lint`.
- [ ] **Step 7: Implement `gitkeeper-state.yml`** on protected default-branch `workflow_run`, with only required label-write and config/check read permissions; validate source workflow, repo, PR association, actor, and SHA before an idempotent label update. It may set queued/blocked or clear an obsolete worker label, but only the companion can set running.
- [ ] **Step 8: Run `actionlint .github/workflows/gitkeeper-pr-ci.yml .github/workflows/gitkeeper-state.yml` and `node --test tests/workflows/classify-run.test.mjs tests/workflows/validate-config.test.mjs tests/workflows/workflow-contract.test.mjs`.** Expected: PASS; no PR checkout/artifact dependency in the privileged workflow.
- [ ] **Step 9: Commit** the workflows, classifier, contract tests, and tests in Setu-mobile.

### Task 3: Verify the Android CI gate from GitHub Actions

**Files:**
- Modify: `.github/workflows/gitkeeper-pr-ci.yml`
- Test: GitHub Actions run for a same-repository PR and a fork PR

**Interfaces:**
- Consumes: Task 2 CI workflow.
- Produces: Required checks on each PR head SHA, with built-in check reporting only.

- [ ] **Step 1: Open a PR and verify the Actions run executes `./gradlew testDebugUnitTest detekt lint` on its exact head SHA.** Do not run these Gradle checks locally as a prerequisite to push.
- [ ] **Step 2: Verify a synchronize event reruns all three checks for the new head SHA.**
- [ ] **Step 3: Verify a companion PR-branch push produces a `synchronize` event and reruns the same workflow against the pushed SHA.**
- [ ] **Step 4: Verify a fork PR receives the same secret-free/read-only checks and no GitKeeper worker label.**
- [ ] **Step 5: Verify the state workflow adds only `worker-queued` for eligible deterministic failure, only `worker-blocked` for infrastructure/unknown/cancelled, and no worker label for pass/ready/ineligible/stale; all writes are tied to the current SHA. Verify only the companion sets `worker-running`.**
- [ ] **Step 6: Commit** any workflow corrections independently in Setu-mobile; record successful Actions run IDs in the implementation handoff.
