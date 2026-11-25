# OpenCode command mediation result (Task 3)

This record answers one question: can the installed OpenCode CLI be made to route
*every* shell request through the GitKeeper companion approval broker, so that a
worker cannot run a command the owner has not approved? It is the Step 7
deliverable of `docs/superpowers/plans/2026-11-20-gitkeeper-host-mode-readiness.md`.

**Result: GO for the broker hook, with the limitations listed below.** A real
`bash` tool call from a real OpenCode run was blocked when the companion denied
it and ran when the companion approved it. Nothing here claims OS isolation: the
CLI still runs as the logged-in user, and the hook is a control-flow gate inside
that process, not a sandbox.

## What was tested

| Item | Value |
| --- | --- |
| CLI | `opencode` `1.19.6` (`opencode-ai` npm package, Windows) |
| Hook | `tool.execute.before`, awaited before the tool implementation runs |
| Broker plugin | `src/gitkeeper/host_mode/broker_plugin/gitkeeper-command-broker.mjs` |
| Controlled config | `%LOCALAPPDATA%\GitKeeper\opencode-broker.json` (`permission.bash: "ask"`, broker as the only plugin) |
| Model used for the probe | `opencode/beam-free` |
| Probe directory | a disposable directory outside both repositories |
| Probe record | `%LOCALAPPDATA%\GitKeeper\broker-probe.json` |

The plugin is loaded from the launcher-owned configuration, and the launcher sets
`OPENCODE_DISABLE_PROJECT_CONFIG=1` so the untrusted PR worktree cannot supply its
own `opencode.json`, plus `OPENCODE_CONFIG` pointing at the controlled file. It
also clears `OPENCODE_PURE` and `OPENCODE_PERMISSION`, which would otherwise
move the decision away from the broker, and `--auto`/`--pure` are refused
outright.

## Command shapes covered

Mediation is applied to any tool whose id looks like a shell tool (`bash`, `sh`,
`zsh`, `fish`, `shell`, `exec`, `execute`, `command`, `cmd`, `powershell`, `pwsh`,
`terminal`, `run`, `spawn`, including `namespace.tool` forms) and to any tool whose
arguments carry a `command`, `cmd`, `script` or `commandLine` field, even under an
unknown tool id. A shell-shaped call with no command text to show the owner is
blocked without asking, because approving an empty command would authorise
something the owner never saw.

## Live probe evidence

Three arms, identical except for the companion's reply, run against the real CLI
and the production plugin:

| Arm | Companion reply | Command ran | Tool error |
| --- | --- | --- | --- |
| approve | `{decision: "approve"}` for this request | yes (proof file written) | none |
| deny | `{decision: "deny"}` | no | `GitKeeper blocked this tool call: the owner replied deny` |
| approve-stale | `{decision: "approve"}` naming a *different* request id | no | `GitKeeper blocked this tool call: the companion replied about a different request` |

Every arm sent the exact command text (`echo GITKEEPER_EXECUTED > proof.txt`), the
worktree path as `cwd`, the session and call ids, and never the bearer token in
the body. The request count per arm was 1–2, matching the number of `bash`
attempts the model made.

The gate itself agrees. Running `evaluate_capability` with the installed version,
the resolved configuration and the persisted record produced `gaps: []` and
`verified: true`, which is what `HostModeStatus` consumes to unblock
`Blocker.BROKER_UNVERIFIED`.

## Verification commands

```powershell
py -m pytest tests/host_mode/test_opencode_capability.py -q   # 29 passed
py -m pytest tests/host_mode/test_broker_plugin.py -q          # 18 passed
```

`tests/host_mode/test_broker_plugin.py` drives the real module through a Node
subprocess against a real HTTP broker, so the request payload, the timeout, the
token handling and each rejection path are exercised rather than mocked.

## Known limitations

1. **The `.opencode` directory is outside the project-config guard.** The CLI
   scans `<worktree>/.opencode` and auto-loads `.opencode/plugin/*.{ts,js}` even
   with `OPENCODE_DISABLE_PROJECT_CONFIG=1`. A PR could therefore try to add code
   to the mediating process. Mitigation: `require_inert_worktree` refuses to
   launch in a worktree containing `.opencode`; Task 4 must call it before every
   launch. Separately, the capability gate now reports `unexpected-plugins` if the
   resolved configuration lists any plugin besides the broker.
2. **The resolved plugin entry is a `file://` URL.** `opencode debug config`
   prints absolute plugins as URLs, so the gate matches the module name rather
   than the bare `gitkeeper-command-broker` string. Pinning it to the bare name
   would have reported a gap on a correctly configured host.
3. **The hook is a Python-free JS module inside a third-party process.** A CLI
   upgrade that renames or stops awaiting `tool.execute.before` would silently
   remove mediation, so the gate pins the exact CLI version and treats any
   mismatch as a blocking gap.
4. **Only one model was exercised.** `opencode/claude-*` returned 403 (“Model
   access is disabled”) for this OpenCode Go organisation, so the probe used
   `opencode/beam-free`. The hook behaves the same for every model, but the
   selected worker model must still be verified as entitled separately.
5. **No OS-level isolation.** Approving a command lets it run with the user's
   full privileges. The broker is an authorisation gate, not a sandbox.
6. **MCP servers are treated as a gap.** A configured MCP server could spawn
   processes outside the broker, so the gate requires an empty MCP server list
   rather than trying to mediate MCP tools.
