# Windows Isolation Prototype Result

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

## Gate outcome

The host cannot establish the required operating-system isolation boundary or prove OpenCode credential separation. The result is therefore NO-GO, not an inconclusive pass. Do not launch automatic or user-triggered worker execution, and do not treat prompt instructions, repository allowlists, or a worktree as security enforcement. Re-evaluate only after a supported isolation prototype and the selected OpenCode credential-boundary probe can be run and produce complete evidence.
