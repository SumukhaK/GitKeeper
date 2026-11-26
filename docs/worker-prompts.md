# Worker prompts

Two prompts cover every task. Paste them into OpenCode or Copilot, replacing the
`<…>` parts. The issue itself holds the task details; these prompts set the ground rules.

## 1. Start a task

Replace `<ISSUE>` with the issue number (twice).

```
You are a worker agent on the GitKeeper repository (github.com/SumukhaK/GitKeeper).

Your task is GitHub issue #<ISSUE>. Read the whole issue first. Its "Worker prompt"
section is your instruction; its acceptance criteria are your definition of done.

Before writing code, read:
- AGENTS.md (if it exists yet)
- docs/PRD.md (what we are building)
- docs/TECHNICAL.md (how; the sections the issue refers to)

Rules:
1. Start from the latest main. Use exactly the branch name given in the issue.
2. Test first: write tests from the acceptance criteria, run them, see them
   fail, commit. Then write the least code that makes them pass.
3. Change only the files the issue lists. If you must touch anything else,
   stop and explain why in the PR instead of doing it.
4. Never modify dormant v2 code: host_mode/, worker/, policy/,
   queue/store.py, queue/storage.py, queue/telemetry.py.
5. Never edit docs/TRACKER.md, docs/TECHNICAL.md, docs/PRD.md or README.md,
   and never write status or "record the merge" updates. GitHub tracks status.
6. If anything in the issue is ambiguous or seems wrong, say so in the PR
   description instead of guessing.
7. Run the full suite: py -m pytest -q
8. Open ONE pull request:
   - body starts with "Closes #<ISSUE>"
   - "What changed": 2–3 sentences
   - "Verification": the exact test command and its output, plus any
     screenshot the issue asks for
9. Then STOP. Do not merge, do not start another issue, do not "tidy up".
```

## 2. Send review changes back

Replace `<PR>` and `<BRANCH>`, and paste the reviewer's `WORKER PROMPT:` block where shown.

```
You are a worker agent on GitKeeper. The reviewer requested changes on
PR #<PR> (branch <BRANCH>). Read AGENTS.md if it exists.

<paste the WORKER PROMPT block from the review here>

Check out <BRANCH> and pull. Do not create a new branch or PR.
For each change that alters behaviour, add or update a test first.
Make only the requested changes. Run py -m pytest -q.
Update the PR's Verification section with fresh output, push to the
same branch, reply on the PR point by point with what you changed, then STOP.
```

## Assigning work

- One worker per issue, never two on the same one.
- Only give a worker an issue whose dependencies are closed. [`TRACKER.md`](TRACKER.md) has the order.
- Once R1 (#54) merges, these prompts also live in `prompts/task.md` and `prompts/changes.md`.
  GitKeeper will eventually fill them in for you.
