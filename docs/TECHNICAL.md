# GitKeeper — Technical Design

**Status:** current, 2026-11-26. Implements [`PRD.md`](PRD.md). Work is tracked in
[`TRACKER.md`](TRACKER.md) and GitHub issues. This document replaces the pilot design spec
and the seven plans formerly under `docs/superpowers/`; their safety rules that still apply
are carried over below, and the rest is in git history.

## 1. Scope

| | What | State |
| --- | --- | --- |
| **v1** | The **review relay**: watch PRs, prepare review packets, read verdicts, prepare worker prompts, let the owner merge | Being built (R1–R16) |
| **v2** | **Automatic worker launch**: GitKeeper runs OpenCode itself behind per-command approval | Code exists, dormant, parked in [#70](https://github.com/SumukhaK/GitKeeper/issues/70) |

v1 never runs a model, never executes repository code, and never merges by itself. That
removes the host-execution risk the v2 safety machinery exists for, so v1 needs none of it.

## 2. Architecture at a glance

```
            ┌──────────── GitHub (REST, outbound only) ────────────┐
            │  PRs · checks · annotations · diff · issues          │
            │  comment on PR · squash-merge (owner click only)     │
            └───────────────▲──────────────────────────┬───────────┘
                            │                          │
                   relay/poller.py  ──advance()──►  relay/state.py
                            │                          │
                            ▼                          ▼
                     relay/store.py            relay/packet.py
                   (one SQLite file)           relay/verdict.py
                            │                  relay/prompts.py
                            ▼                          │
                 ui/viewmodels/reviews.py ◄────────────┘
                            │
                 QML: fox pet badge · Reviews page
                            │
                     owner (clipboard ⇄ reviewer / worker)
```

One process, one poller thread and the Qt UI thread. No inbound listener, no webhooks.

## 3. Module map

| Module | v1 role |
| --- | --- |
| `relay/state.py` | **New (R3).** Pure state machine (§4) |
| `relay/verdict.py` | **New (R4).** Strict verdict parser (§6) |
| `relay/packet.py` | **New (R5).** Review packet builder (§5) |
| `relay/prompts.py` | **New (R6).** Changes, CI-fix and next-task prompts (§7) |
| `relay/token.py` | **New (R10).** GitHub token from `gh auth token` (§9) |
| `relay/store.py` | **New (R11).** One small SQLite file (§10) |
| `relay/poller.py` | **New (R12).** The loop (§11) |
| `relay/config.py`, `__main__.py` | **New (R15).** Local config and entry point |
| `queue/github.py` | **Reused.** Made synchronous (R7), extended with reads (R8) and writes (R9) |
| `queue/models.py`, `errors.py`, `readiness_adapter.py` | Reused where the client needs them (`PullRequestSnapshot`, `CheckState`, error categories) |
| `ui/` | **Reused.** Fox pet and handscroll panel; new Reviews view model and page (R13, R14) |
| `ui/services/config_publisher.py` + Settings publish | **Removed (R16).** v1 reads no `.github/gitkeeper.json` |
| `host_mode/`, `worker/`, `policy/`, `queue/store.py`, `storage.py`, `telemetry.py`, `ports.py` | **Dormant v2.** Tested, not wired, not changed in v1 (§14) |
| `isolation/` | Historical; deleted with v2 cleanup |

Dependency rule: `relay/` may import `queue/github.py`, `queue/models.py` and `queue/errors.py`.
It never imports `worker/`, `host_mode/` or the v2 queue store.

## 4. State machine

`advance(record, facts) -> record` is pure. `facts` comes from GitHub on every poll; owner
actions are separate pure functions. Caps: `MAX_CI_ATTEMPTS = 3` fix attempts,
`MAX_REVIEW_ROUNDS = 3` reviews.

| From | Event | To | Effect |
| --- | --- | --- | --- |
| *(any)* | PR merged or closed | `done` | Poller offers the next task |
| *(none)* | first seen, checks `pending` | `ci_pending` | |
| *(none)* | first seen, checks `passed` | `review_needed` | |
| *(none)* / `ci_pending` | checks `failed` | `ci_failed` | `ci_attempts += 1` |
| `ci_pending` | checks `passed` | `review_needed` | |
| `ci_pending` | checks `failed` and `ci_attempts == 3` | `blocked` | reason `ci_attempts_exhausted` |
| *(any but done)* | checks `infra` (cancelled, timed out, startup failure, missing) | `blocked` | reason `ci_infrastructure` — never a code failure |
| `review_needed`, `awaiting_verdict` | owner copies packet | `awaiting_verdict` | |
| `awaiting_verdict` | verdict APPROVE | `ready_to_merge` | `reviewed_sha = head_sha`; verdict posted as PR comment |
| `awaiting_verdict` | verdict CHANGES_REQUESTED | `changes_requested` | `review_rounds += 1`; worker prompt stored; comment posted |
| `awaiting_verdict` | 3rd CHANGES_REQUESTED | `blocked` | reason `review_rounds_exhausted` |
| `changes_requested`, `ci_failed`, `ready_to_merge`, `review_needed`, `awaiting_verdict` | **new head SHA** | from checks: `ci_pending` / `review_needed` / `ci_failed` | any verdict for the old SHA is void; a failure counts as an attempt and the CI cap applies (4th failure → `blocked`) |
| `ready_to_merge` | owner clicks Merge | (merge call) → `done` on next poll | merge pinned with `sha = reviewed_sha` |
| `blocked` | new head SHA | `blocked` | head updated; still needs the owner |
| `blocked` | owner Reset | `ci_pending` | counters reset to 0; recomputed next poll |

Illegal owner actions raise `ValueError` and the UI shows why. Merge is allowed only in
`ready_to_merge` **and** when the current head equals `reviewed_sha`.

## 5. Review packet

The packet is the watched repo's `prompts/review.md` with its `<REVIEW PACKET>` placeholder
replaced by:

```
## Issue #<n>: <title>
<issue body>                      (or: _No linked issue — check the PR body._)

## Pull request #<n>: <title>
<url> · head <sha>
<PR body>

## CI results
- <check name>: <conclusion>

## Diff
```diff
<unified diff, cut at a line boundary after 60,000 characters>
```
[diff truncated: N of M characters shown — review the rest on GitHub: <url>/files]
```

The CI section and the PR body's **Verification** section carry the proof; the reviewer
judges it. GitKeeper never summarises or edits the diff.

## 6. Verdict format

The reviewer ends its reply with exactly one block (defined in `prompts/review.md`):

```
VERDICT: APPROVE
```
```
VERDICT: CHANGES_REQUESTED
WORKER PROMPT:
<self-contained numbered instructions>
```

Parsing (R4) takes the **last** `VERDICT:` line, is case-sensitive, tolerates code fences
and CRLF, and fails closed: no verdict, unknown value, conflicting verdicts, an empty
worker prompt, or text after APPROVE are all errors. A failed parse changes nothing.

## 7. Prompts

Templates are read from the watched repo's `prompts/` folder on its default branch, so each
project's own wording is used; copies bundled in `relay/default_prompts/` are the fallback.

| Prompt | When | Built from |
| --- | --- | --- |
| Review packet | `review_needed` | `prompts/review.md` + §5 |
| Changes | `changes_requested` | `prompts/changes.md` + the verdict's worker prompt |
| CI fix | `ci_failed` | failing checks + up to 20 annotations each + summary (≤ 4,000 chars), "attempt N of 3" |
| Next task | after `done` | `prompts/task.md` + the oldest open `task` issue no open PR links to |

## 8. GitHub API

All calls go through `GitHubRestClient` (synchronous, `urllib`, 20 s timeout, no hidden
retries; the poller owns backoff; errors are fixed categories with no response text).

| Need | Endpoint |
| --- | --- |
| Open PRs | `GET /repos/{o}/{r}/pulls?state=open&per_page=100` (paginated) |
| PR details | `GET /repos/{o}/{r}/pulls/{n}`: title, body, head sha/ref/repo, author, draft, merged |
| PR diff | same URL, `Accept: application/vnd.github.diff`, refused above 2 MB |
| Checks | `GET /repos/{o}/{r}/commits/{sha}/check-runs` |
| Annotations | `GET /repos/{o}/{r}/check-runs/{id}/annotations` (max 50) |
| Issue | `GET /repos/{o}/{r}/issues/{n}` (rejects PRs) |
| Next task | `GET /repos/{o}/{r}/issues?state=open&labels=task&sort=created&direction=asc` |
| Prompts | `GET /repos/{o}/{r}/contents/prompts/{name}?ref={default_branch_sha}` |
| Comment | `POST /repos/{o}/{r}/issues/{n}/comments` |
| Merge | `PUT /repos/{o}/{r}/pulls/{n}/merge` with `merge_method=squash`, `sha=reviewed_sha` |

v1 writes no labels. The v2 label projection stays dormant.

## 9. Credentials

The token comes from `gh auth token`, run without a shell for each client call (R10). Nothing
stores it: not config, SQLite, logs, exceptions or QML. A missing or logged-out `gh` is a
`GitHubAuthorizationError` with a fixed message telling the owner to run `gh auth login`.

## 10. Storage

`%LOCALAPPDATA%\GitKeeper\relay.sqlite3` (or `~/.gitkeeper/` elsewhere), WAL, `user_version = 1`.
The path must not be inside a git checkout.

- `pull_requests(repository, number, head_sha, phase, review_rounds, ci_attempts,
  reviewed_sha, blocked_reason, worker_prompt, updated_at)`, primary key repository + number.
- `events(repository, number, at, kind, detail)`, append-only, for the PRD metrics (review
  rounds, CI attempts, and later revert detection).

It holds metadata only: no diffs or tokens. Reviewer text is posted to the PR, not stored.

## 11. Polling

- One loop on a daemon thread; waits use `threading.Event.wait`, never `sleep`.
- Every 15 s while any PR is active, 60 s when idle; failures back off from 30 s, doubling
  to 300 s.
- Each pass: list open PRs per configured repo → keep **eligible** ones → read details and
  checks → `advance()` → persist → notify the UI of changed PRs. A PR that vanished from
  the open list is re-read once: merged or closed → `done`.
- Offline: keep last state, show "offline", make no transitions.

**Eligible** (unchanged from the pilot rules): repo in the local config, author equals the
configured owner, head repo equals base repo (no forks), not a draft.

## 12. UI

- **Fox pet:** a badge with an accessible name when any PR waits on the owner. Works with
  reduced or disabled motion; state is always text, never colour alone.
- **Reviews page** (first tab of the handscroll): per PR — Copy review packet · paste box
  + Record verdict · Copy worker prompt · Merge (confirm dialog showing the head SHA) ·
  Reset (when blocked) · Copy next task prompt.
- The Command approval dialog, Queue page and Settings publish controls are v2 or removed.

## 13. Testing and CI

- TDD for every slice: tests first, from the issue's acceptance criteria.
- Pure modules (`state`, `verdict`, `packet`, `prompts`) need no fakes. I/O modules use
  injected fakes (`opener`, `run`, store, clock). No test touches the network.
- CI (R2): ruff on `relay/` and `ui/`, the full pytest suite. Windows-only tests are
  skipped on Linux CI.
- PR guard and generated code map come from Vibe-code-Architect (R1).

## 14. v2: automatic worker launch (dormant)

Kept because it's real, tested safety work; not extended until v2 opens ([#70](https://github.com/SumukhaK/GitKeeper/issues/70)).

**What exists:** typed launch readiness (`host_mode/readiness.py`); a one-shot command
approval broker with digest binding, TTL and stale-fact checks (`command_broker.py`); the
OpenCode `tool.execute.before` plugin and capability gate; a loopback broker bridge with an
owner-only token; a Windows process adapter (suspended create, kill-on-close Job Object,
bounded output); preserved per-PR worktrees; git publication (fast-forward only); the patch
scope rule (10 files, 400 lines, 150 per file, path allow/deny lists, frozen Android manifest
capabilities, secret scan); a fenced coordinator; and the queue store (leases, latches,
publication intents).

**Evidence carried over:** on OpenCode CLI 1.19.6 a three-arm live probe showed approve ran
the exact command, deny blocked it, and an approval for a different request id blocked it.
Limitations: `.opencode/` in a worktree loads plugins outside the project-config guard
(refused by `require_inert_worktree`); the gate pins the CLI version; only shell-shaped tool
calls are mediated — file-edit and web tools are not; one model was exercised; MCP servers
are refused. Accepted host mode is a risk decision, not isolation.

**Must fix before enabling** (all in #70): push cannot authenticate (credential helper
disabled), no queue service exists for the coordinator, the worker prompt lacks failure
details, plus #38, #39, #40, the duplicated model ladder and duplicated SQLite boilerplate.

## 15. Decisions

1. **v1 is a relay, not an executor.** The value is removing manual plumbing; executing
   model output on the owner's machine is the riskiest part and is deferred.
2. **The owner carries text to and from the reviewer.** It keeps model usage under the
   owner's control on a subscription plan; the reviewer is swappable because the packet and
   verdict formats are fixed.
3. **GitHub is the tracker and the record.** No hand-written status documents; issues,
   PRs and git history are the source of truth.
4. **A small new store instead of the v2 queue store.** v1 needs per-PR phase and counters,
   not leases and publication fencing.
5. **Prompts live in the watched repo**, so the template (Vibe-code-Architect) and GitKeeper
   share one contract.
6. **The Setu-mobile label workflow is not needed by v1.** v1 reads ordinary check runs;
   the trusted `workflow_run` label writer from the pilot (Setu-mobile PR #19) can be
   dropped or kept independently.
