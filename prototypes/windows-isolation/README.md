# Windows isolation feasibility prototype

This prototype decides whether GitKeeper may launch its local worker on Windows. Until the prototype produces a complete, verified `GO` report for the current prototype ID, automatic and user-triggered worker execution must remain disabled.

## Threat model

PR-controlled source, build scripts, tests, and worker-generated commands are untrusted. A dedicated worktree is not a security boundary. The isolation candidate must prevent these commands from reading host-only files, inspecting unrelated process/environment data, writing outside the disposable worktree, or sending canary values to a controlled network receiver. The OpenCode worker must be usable with the user's selected model without exposing GitHub or desktop credentials to project-controlled commands.

## Non-negotiable probe rules

- Use synthetic canary values only. Never copy real credentials, tokens, private source, or personal data into a probe.
- A missing, unsupported, inconclusive, failed, or stale assertion is a `NO_GO`, not a warning.
- Do not infer safety from prompt instructions, PR authorship, a worktree, or a successful OpenCode call alone.
- Do not enable a production worker, weaken a failed assertion, or use an alternate model to turn a `NO_GO` into `GO`.
- Keep raw evidence outside both repositories and exclude secrets, source, diffs, prompts, and full OpenCode input/output.

## Required host assertions

The report must include explicit results for host-file access, process metadata, environment access, writes outside the worktree, and network egress. Each assertion must pass for the isolation report to be eligible for `GO`.

The OpenCode credential-boundary probe is a separate required assertion. It passes only when an explicitly selected model completes a harmless disposable-worktree task and all credential-read/exfiltration probes fail closed. No model selection or an inconclusive result is `NO_GO`.

## Result

The implementation must record the tested prototype ID, every assertion result, whether the selected OpenCode call succeeded, and evidence references. Task 4 writes the explicit `GO` or `NO_GO` decision. Only a complete `GO` for the expected prototype ID may unlock the downstream worker gate.
