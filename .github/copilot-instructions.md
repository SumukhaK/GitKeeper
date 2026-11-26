# GitHub Copilot Atomic PR & Commit Guidelines

When implementing features, bug fixes, or refactors, you must work in small, incremental, reviewable pull requests. Never generate large, sweeping multi-file changes in a single response or PR.

## 1. Scope & Size Constraints

- **Atomic Vertical Slices:** Break any multi-step request into the smallest independently working units (e.g., 1. types/schema -> 2. core logic -> 3. endpoint/UI -> 4. tests).
- **Single-Slice Execution:** When given a large task, first outline the numbered list of small PRs, then implement **ONLY PR #1**. Stop immediately after PR #1 is complete and wait for user approval before proceeding to PR #2.
- **Hard Size Limits:** Keep each PR under **\~150 lines of changed code** and **3–4 files maximum**. If a step exceeds this, split it further.
- **Zero Drive-By Changes:** Do not reformat unrelated code, reorder untouched imports, or refactor neighboring functions outside the immediate PR scope.
- **Always Buildable:** Every individual slice must compile, pass existing linters/tests, and leave the codebase in a non-broken state.

## 2. Mandatory Output After Every Slice

At the end of every implementation, provide the exact commit and PR details in this format:

### Commit Message

```text
<type>(<scope>): <imperative summary, max 50 chars>

- <Bullet explaining WHY this change is needed, not just what changed>
```

### Pull Request Template

* **PR Title:** `<type>(<scope>): <concise, specific title>`
* **Task Summary:** One sentence explaining the exact problem this small PR solves.
* **Files Changed:**
    * `path/to/file` — specific responsibility added/updated.
* **Verification:** Exact command or steps to test this isolated change.
* **Next Slice:** 1-line summary of the next small PR in the queue.
