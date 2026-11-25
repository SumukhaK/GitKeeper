# Windows Isolation Prototype Result

> **Historical status:** This NO-GO result is superseded as the pilot's operating gate by the owner's explicit `ACCEPTED_HOST_MODE` risk decision. It is not a GO, does not claim that isolation was achieved, and the evidence below remains unchanged.

**Decision: NO-GO**

**Recorded:** 2026-11-20
**Prototype:** Not built or verified
**Worker execution:** Disabled

## Evidence and limitations

- The available host is Windows 10 Home.
- `WindowsSandbox.exe` is not present.
- A read-only DISM optional-feature query returned exit code 740, indicating that elevation would be required. No elevation was attempted.
- The OpenCode CLI is unavailable, and no user-selected model ID is configured.
- No host-boundary probes, credential probes, or OpenCode invocation were attempted. No Windows feature or isolation candidate was installed, enabled, or substituted.

This document is the durable record of the capability findings and stop decision. No probe evidence artifacts exist because probing did not begin.

## Historical gate outcome (at time of evaluation)

At the time of this evaluation, the host could not establish the required operating-system isolation boundary or prove OpenCode credential separation. The result was NO-GO, not an inconclusive pass. No automatic or user-triggered worker execution was authorized by this result.

## Subsequent owner decision

The owner later explicitly accepted host execution on Windows 10 Home and waived the OS-isolation and separate-user credential-boundary requirements for this pilot. That decision supersedes the requirement to produce an isolation GO, but it does not change the NO-GO evidence above or create an isolation claim.

Worker readiness is now governed by the accepted host-mode design and remains blocked until its local owner policies and OpenCode command-broker capability are verified. If OpenCode CLI `1.19.6` cannot be proven to mediate every worker-requested shell command through fresh companion approval, worker execution stays disabled. A repository allowlist, model allowlist, prompt, worktree, and approval UI do not confine host access.
