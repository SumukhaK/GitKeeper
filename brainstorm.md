# Git-keeper & AI Companion — Architectural Brainstorming Log

**Purpose of Document:** Complete chronological record of the architectural Q&A, constraints, design pivots, and decisions made between the Developer and CTO/Architect. Feed this document into Claude to provide full context on *why* every architectural decision was made before generating implementation tasks.

---

## Q&A Session Chronology

### Turn 1: Kaggle TPU v5e-8 Capacity & Constraints
* **User Query:** What is the capacity of TPU v5e-8 in Kaggle?
* **Architect Findings:**
  * **Accelerator Memory:** 128 GB total HBM distributed across 8 individual chips (16 GB per chip; not a unified pool—models >16 GB require sharding).
  * **Compute & Bandwidth:** 197 TFLOPS (bf16) / 393 TOPS (Int8) per chip (~1.57 PFLOPS total); 819 GB/s HBM bandwidth; 400 GB/s bidirectional ICI.
  * **Host VM:** 224 vCPUs, 384 GB system RAM.
  * **Kaggle Quotas:** Up to **20 hours/week** free usage; **9-hour maximum single session** cutoff. Easily handles up to ~27B parameter models in bf16 when sharded.

---

### Turn 2: Initial Vision — Hosting "Git-keeper" on Kaggle TPU + Laptop Companion
* **User Goal & Constraints:**
  * Wants to build an AI companion/bot ("Git-keeper") with a cute digital pet or Iron Man *Jarvis*-style desktop HUD without slowing down the laptop (avoiding heavy local inference like running big models locally, though open to lightweight agents like Hermes/OpenClaw if non-bloated).
  * Has access to Kaggle TPU v5e-8 and **AMD AI Labs (Token Lab)** models.
  * **Core Coding Methodology:**
    * **Andrej Karpathy's 4 Rules of Coding:**
      1. *Think Before Coding:* State assumptions explicitly, surface trade-offs, present multiple interpretations when ambiguous, stop and ask when unclear.
      2. *Simplicity First:* Minimum code needed for the exact problem—no speculative features, premature abstractions, or unrequested configurability.
      3. *Surgical Changes:* Touch only files/lines needed for the task, match existing style, clean up only orphans created by own changes.
      4. *Goal-Driven Execution:* Define clear, verifiable success criteria (tests/checks) and loop until verified.
    * **TDD & Repo Rules:** Claude is used for brainstorming, repo setup, task breakdown, architectural PR review, merging, updating `README.md`/trackers, and updating a **code memory graph** (so Claude doesn't re-scrape the whole repo repeatedly). Worker models (OpenCode with DeepSeek, GLM, etc.) work on branches (`feature/`, `bugfix/`, `upkeep/`), follow framework rules (e.g., Android `ktlint`, `detekt`, `<500` lines per file), follow TDD, and raise a PR.
  * **Initial Idea:** Host a 20–30B model on Kaggle TPU to power Git-keeper so Claude doesn't waste tokens on repetitive work.
* **Architect Critical Audit & Recommendation:**
  * **Rejected Kaggle TPU for Live Tooling:** 9-hour session limits and 20-hour/week quotas mean the bot is offline ~88% of the week. Cold-booting TPUs, XLA compilation caches, and tunnel setup add severe friction. Keep Kaggle TPU strictly for offline batch jobs (e.g., codebase indexing/embeddings or fine-tuning).
  * **Split Deterministic vs. Probabilistic Checks:** Never pay an LLM to check what a script can verify in 5 seconds. Use **GitHub Actions** for deterministic checks (branch regex, `<500` line limits, `detekt`, unit tests) and **AMD Token Lab** for lightweight log condensation/diff packaging.

---

### Turn 3: Clarifying Git-keeper's True Role (Router/Dispatcher, Not Code Judge)
* **User Clarification:** Git-keeper is **not** meant to perform the final architectural code review on PRs; its job is to enforce pre-flight hygiene and redirect clean PRs to Claude.
* **Architect Alignment:**
  * This eliminates any "smaller model quality trap." Claude remains the sole architectural judge (enforcing Karpathy's 4 rules, merging, updating docs, and syncing the code memory graph).
  * Git-keeper acts as a smart CI/CD traffic controller and bundle packager, ensuring Claude never wastes tokens on broken, un-linted, or failing PRs.

---

### Turn 4: Bidirectional Orchestration — The "Restaurant Expediter" Model
* **User Requirement:** Git-keeper shouldn't just dispatch to Claude once—it must also take Claude's `CHANGES_REQUESTED` feedback and report it back to the worker models. It sits between the **Order Counter (Claude)** and the **Kitchen (OpenCode / workers)** until the order passes inspection and gets served (merged into `main`).
* **Architect Design:**
  * Modeled Git-keeper as an **Event-Driven Finite State Machine (FSM)** acting as the restaurant expediter ("the Pass"):
    * `KITCHEN_COOKING` → `PREFLIGHT_GATE` → `CLAUDE_REVIEW` → `SENT_BACK` (loop with circuit breaker `max_attempts`) → `ORDER_SERVED` (Merged).

---

### Turn 5: Subscription Quotas, Manual Ignition, and Parallel Tracks (Mobile + Desktop)
* **User Constraints:**
  * Not on unlimited API billing—runs on a **\$20/month Claude subscription**, **OpenCode Go**, and desktop apps (Z.ai, GPT/Codex).
  * **No Project-Level Runaway Autonomy:** Once a PR is merged, the companion must **stop completely** and wait until the user manually kicks off the next feature from Claude to the workers.
  * **Parallel Multi-App Development:** Actively building both a **Mobile app** and a **Desktop app** in separate OpenCode windows simultaneously, while using **one single Claude window** to brainstorm and review both.
* **Architect Solution:**
  * **Single-Ticket Lifecycle:** Hard stop on `PR_MERGED` to protect Claude's 5-hour rolling usage limits.
  * **Track Tagging:** Prefix branches/bundles (`[TRACK: MOBILE APP]` vs `[TRACK: DESKTOP APP]`) so the shared Claude brainstorming session never confuses context between the two apps.

---

### Turn 6: Exact Wake-Up and Sleep Triggers
* **User Suggestion:** Make Git-keeper wake up **only** when a new PR is raised in the repository, and go completely back to sleep once that PR is merged.
* **Architect Validation:**
  * Confirmed as the cleanest possible lifecycle (**PR-born, Merge-terminated**). Zero idle CPU/RAM drain while brainstorming or coding; activates strictly on `PR_OPENED` and terminates its active worker state on `PR_MERGED`.

---

### Turn 7: Multi-Repo Avatars & Targeting Specific Desktop Windows
* **User Requirements:**
  * Approved the core stack direction (**GitHub Actions + AMD Token Lab + Python custom desktop companion**).
  * Requested **unique avatars per repository** so parallel work across multiple projects is visually unmistakable on the desktop HUD.
  * Asked how Git-keeper notifies a specific chat/project window inside OpenCode, Z.ai, or GPT desktop apps when multiple workspace windows are open.
* **Architect Solutions:**
  * **Repo-Mapped Avatars:** Config-driven avatar personas (e.g., *Cyber Fox* for `mobile-app`, *Mecha Owl* for `desktop-app`) that appear only while a PR is active in that repo.
  * **Window Targeting vs. CLI:** Electron/Tauri desktop apps lack safe IPC hooks to target specific internal chat tabs. Desktop apps require OS window-title matching + "Click Avatar to Beam" (clipboard copy + window focus), whereas **CLI workers** target exact project directories (`cwd`) deterministically with zero GUI collision.

---

### Turn 8: Switching Workers to CLI + Mid-Task Model Quota Failover
* **User Decision & New Requirement:**
  * Agreed to keep **Claude in the Desktop App** (for unified brainstorming and architectural review) while moving **OpenCode, Z.ai, and GPT/Codex workers to CLI**.
  * Added **Quota-Aware Model Failover:** If the active worker model (e.g., `GLM-5.3` on OpenCode) runs out of allotted quota mid-task, Git-keeper must pause, preserve existing file edits, and seamlessly switch to the next available best model in priority order.
* **Architect Solution:**
  * **Quota-Aware Fallback Supervisor:** Streams CLI `stdout`/`stderr` looking for rate-limit/quota errors (`HTTP 429`, `quota exhausted`, etc.). Because edits exist on disk in the git branch, Git-keeper inspects `git diff`, marks the exhausted model in a cooldown table, and invokes the next model in `worker_fallback_chain` (e.g., `GLM-5.3` → `DeepSeek-V4` → `AMD Token Lab Gemma-4-31B` → `Codex CLI`) with a continuation prompt.

---

### Turn 9: The "Flight Recorder" (Telemetry, Crash Recovery & Self-Optimization)
* **User Requirement:** Record the entire workflow and every action Git-keeper takes for two purposes:
  1. **Analytics & Crash/Error Recovery:** Diagnose failures, hangs, or crashes and resume safely.
  2. **Continuous Process Optimization:** Feed recorded logs/retrospectives back to Claude (or another thinking model) over time to improve prompts, rules, and worker success rates.
* **Architect Solution:**
  * **Dual-Layer Flight Recorder:**
    1. Structured atomic event stream (`events.sqlite` + `pr_<num>.jsonl`) for crash recovery on reboot and bottleneck metrics.
    2. Compact **PR Post-Mortem Markdown Digest (`retro_pr_<num>.md`)** generated upon merge, designed to be fed in batches to Claude Desktop to refine `AGENTS.md` / `CLAUDE.md` and Karpathy rule prompts.

---

## Summary of Agreed Principles for Claude
1. **Zero Autonomous Runaway:** Strictly scoped to `PR_OPENED` → `PR_MERGED` per ticket.
2. **Protect Claude Pro Quota:** Deterministic checks + AMD Token Lab filter out noise before Claude is ever invoked.
3. **Hybrid Ergonomics:** Hands-free headless CLI for the "Kitchen" (workers + quota failover); 1-click Avatar Beam to Claude Desktop for the "Order Counter" (brainstorming, architectural review, merge, docs, and memory graph updates).
4. **Closed-Loop Learning:** Every PR produces telemetry and a concise retrospective for periodic prompt/rule tuning.
