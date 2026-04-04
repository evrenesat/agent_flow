## 2026-04-04 — Worktree original-plan sync and plan-only dirtiness policy

### What changed

- **Dirty-policy classification** — `aflow/git_status.py` gained a helper to classify dirty paths into "plan-only" (files under `plans/` prefix) and "unrelated" (everything else). Worktree workflows now allow plan-only dirtiness at startup without prompting, while unrelated dirtiness still triggers the standard prompt/fail behavior.

- **Plan sync in worktree lifecycle** — `aflow/workflow.py` now syncs the original plan file into linked worktrees before prompt rendering (via `_sync_plan_to_worktree`) and back to the primary checkout after each turn, before post-turn state parsing (via `_sync_plan_from_worktree`). This allows untracked or gitignored plans under `plans/` to be available for worktree harness execution without requiring them to be git-tracked.

- **Removed git-tracked requirement** — The lifecycle preflight's `_is_git_tracked` gate for the original plan file is removed. The file must still exist and be under the primary repo root, but it may be untracked or gitignored.

- **Documentation updates** — `README.md` now describes plan-only dirtiness behavior, clarifies which workflows allow it, and documents that the original plan is copied into and synced back from linked worktrees. `ARCHITECTURE.md` now documents the refined preflight rule and sync points in the turn loop.

### Why

- **Untracked plans under `plans/` are a natural workflow state** for checkpoint-based handoffs where each agent iteration produces a new plan variant. Requiring them to be git-tracked was an artificial barrier, especially when `plans/backups/` and `plans/done/` are engine artifacts.
- **Plan-only dirtiness is safe to allow** because changes under `plans/` do not affect compiled code or normal workflows; they only affect `aflow` itself. Unrelated dirtiness (code changes, config, etc.) still blocks startup.
- **Original-plan-only sync is the minimal contract** needed to support untracked plans. Follow-up plans created via `NEW_PLAN_EXISTS` remain transient/worktree-local, so syncing only the original plan is sufficient for restart and state consistency.

### Gotchas

- **Original-plan-only sync:** This handoff syncs only the original plan file, not follow-up plans. If a worktree turn creates a new plan via `NEW_PLAN_EXISTS`, that plan stays in the worktree and does not affect the primary checkout. This is intentional — follow-up plans are not durable or restartable in this version.
- **Transient follow-up plans:** Do not rely on follow-up-plan persistence across `aflow` invocations or restarts. They live only in the worktree for the duration of the run. If you need a follow-up plan to survive a restart or later invocation, implement that as part of the plan-update logic in your harness steps, not as worktree transience.
- **Dirty-path prefix matching:** The `plans/` classification uses exact `startswith("plans/")` checks, not substring matching. Paths like `plans_backup/` or `my-plans/foo` are treated as unrelated dirtiness and will block startup. This strict rule prevents accidental allow-listing of unintended directories.
- **Primary copy is the authority:** The original plan file in the primary checkout is the long-lived source of truth for plan state across runs. The worktree copy is a working copy that is synced back after each turn. If both the primary and worktree copies are edited externally between turns, the worktree copy wins (because it is synced after the harness returns).
