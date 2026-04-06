## 2026-04-06 — Startup refresh guard for stale pre-handoff base HEAD

- `aflow` now checks live `## Git Tracking` metadata before startup prompts when the original plan contains it. A pristine plan with an empty or stale `Pre-Handoff Base HEAD` can ask for confirmation and refresh that field to the current `git rev-parse HEAD` value.
- The approved rewrite is deferred until after lifecycle setup creates the execution context, then applied before the first prompt is rendered. Started handoffs do not auto-refresh, and non-TTY startup still fails instead of guessing.

## 2026-04-06 — Interactive worktree resume

- `aflow run` now offers to resume the last unfinished matching worktree run when the same resolved invocation is rerun in a TTY. It resolves the prior run through `AFLOW_LAST_RUN_ID` first, then `.aflow/last_run_id`, and reuses the recorded feature branch and worktree path only when the workflow, plan path, team, step, turn limit, extra instructions, and lifecycle setup all match.

## 2026-04-06 — Optional bundled skill selection and analyzer docs parity

### What changed

- `aflow install-skills` now documents the default eight-skill install, optional bundled skill selection via `--include-optional`, and exact selection via repeatable `--only`. `BUNDLED_SKILL_NAMES` is now the full sorted inventory of bundled skill names, while the default install set stays unchanged.

- `aflow-assistant` is now documented as an optional bundled skill for setup help, aflow concepts, and evidence-first run debugging instead of being implied as part of the default install.

- `aflow analyze [RUN_ID] [--all]` is now documented in the top-level README and architecture reference as the supported analyzer entrypoint, and the run-id fallback chain is spelled out as explicit `RUN_ID`, `AFLOW_LAST_RUN_ID`, then `.aflow/last_run_id`.

- `runlog.py` persists `.aflow/last_run_id` immediately when run paths are created, so the latest run remains discoverable even if the workflow later fails.

## 2026-04-06 — Auto-bootstrap empty repos from plan preamble

### What changed

- **Repo-state detection** — `aflow/git_status.py` gained `RepoState` (enum: `NO_GIT_BINARY`, `NOT_A_REPO`, `UNBORN`, `READY`) and `probe_repo_state(repo_root)` to classify the git state at startup without side effects. The lifecycle engine uses this before any preflight to decide whether bootstrap is needed.

- **Team-lead bootstrap handoff** — When a lifecycle workflow starts against a directory with no `.git/` or a repo with no commits, `run_workflow()` now invokes a bootstrap handoff before git-dependent preflight. The handoff reuses the same `[aflow].team_lead` resolution path as merge teardown. The agent is given the built-in `aflow-init-repo` skill instruction plus a derived `README.md` title and body, and runs from the primary checkout.

- **Deterministic README derivation** — `derive_readme_content(plan_text, file_stem)` extracts the README title from the first `# ...` heading (falling back to a humanized file stem) and the body from the `## Summary` section if present, otherwise the first prose paragraph after the title and before any checkpoint heading. Fenced code blocks, `## Git Tracking`, `## Done Means`, `## Critical Invariants`, and `## Forbidden` sections are skipped. If no usable paragraph is found, the fallback body is a single sentence naming the plan title.

- **Post-bootstrap verification** — After the init-repo agent returns, the engine checks: `HEAD` resolves to a commit, `HEAD` is on `main_branch`, `README.md` exists and is git-tracked, and the working tree has no tracked-file dirtiness. Untracked files from the pre-existing directory contents are acceptable. Only after all checks pass does normal lifecycle preflight and branch/worktree setup continue.

- **Removed obsolete unborn-branch guard** — The `_lifecycle_preflight_git` function no longer contains the "no commits yet; create an initial commit" error. That code path is now unreachable because bootstrap handles unborn repos before git-dependent preflight runs.

- **New bundled skill** — `aflow/bundled_skills/aflow-init-repo/SKILL.md` contains the contract for the bootstrap agent: initialize git if needed, repoint HEAD to `main_branch` if on a different unborn branch, write the provided `README.md`, stage only that file, commit with `Initial commit`, and emit `AFLOW_STOP:` on any ambiguity.

- **Documentation** — `README.md` now describes auto-bootstrap in the lifecycle startup section. `ARCHITECTURE.md` describes the new startup order: detect repo state, optionally bootstrap via team lead, then run normal lifecycle preflight and setup.

### Why

- Lifecycle workflows previously required the target repo to already have at least one commit. This was a friction point for new-project bootstrapping from a plan file, since users had to manually `git init` and commit before invoking `aflow`.
- The bootstrap reuses the team-lead agent rather than generating the initial commit inside engine code, which keeps the commit subject to the same model-driven process and avoids hardcoded README content.

### Gotchas

- **Bootstrap is local-only.** No remotes are added, no `git push` is performed. The initial commit exists only in the local repo.
- **Only `README.md` is committed.** Pre-existing files in the target directory are not staged or committed as part of bootstrap. The initial commit is minimal.
- **Committed repos are not affected.** Auto-bootstrap runs only when there are zero commits. A repo with even one commit goes through the normal preflight path without any bootstrap attempt.
- **Non-lifecycle workflows skip bootstrap.** Workflows with an empty `setup` do not trigger bootstrap and behave exactly as before, even if the working directory has no `.git/`.
- **If git is missing, lifecycle workflows fail early** with a clear error that does not mention remotes or network setup.

### Limits

- README derivation uses only the plan preamble (title + Summary section or first prose paragraph). Checkpoint content, step lists, and later sections are not parsed.
- No new configuration keys were introduced. Bootstrap uses the existing `[aflow].team_lead` and `main_branch` settings.

## 2026-04-05 — Explicit run flags and numeric start-step support

### What changed

- **Explicit flag-based CLI** — `aflow run` now accepts explicit `--plan`/`-p`, `--workflow`/`-w`, `--start-step`/`-ss`, and `--team`/`-t` flags in addition to the original positional forms. The `--max-turns`/`-mt` flag continues to work as before.

- **Mixed positional and flag input** — Plan and workflow can be specified via positional arguments, explicit flags, or a combination of both. When both sources provide the same field, they must resolve to the same canonical value or the CLI exits with a clear conflict error.

- **Intelligent two-positional resolution** — When two bare positional arguments are provided, they are resolved by meaning: the token matching an existing plan file is treated as the plan, and the token matching a configured workflow name is treated as the workflow. This allows both `workflow plan` and `plan workflow` forms to work without ambiguity. Single bare positionals remain unambiguous and always mean the plan path.

- **Numeric start-step selection** — `--start-step`/`-ss` now accepts either workflow step names (like `implement_plan`) or 1-based numeric indexes (like `2` for the second declared step). Numeric resolution happens after the workflow config is loaded, using the declared workflow step order. Out-of-range and invalid numeric indexes fail with clear bounds errors listing the valid range.

- **Canonical step name resolution** — All downstream execution receives the canonical workflow step name, never raw numeric tokens. This ensures run logs, status output, and workflow state consistently record and use step names.

- **Documentation updates** — `README.md` now shows all flag forms and mixed-order positional examples. `ARCHITECTURE.md` documents the new argument resolution rules and numeric index mapping.

### Why

- **Backward compatibility** — Single-positional plan-only invocations (`aflow run path.md`) continue to work exactly as before. Existing two-positional `workflow plan` forms remain valid.
- **Explicit is better than implicit** — Flags remove ambiguity when users want to specify both plan and workflow without relying on positional order or filename heuristics.
- **Numeric start-step is ergonomic** — Many workflows declare steps in a meaningful order; numeric selection like `-ss 2` is faster to type and remember than step names in some cases.
- **Fail-closed ambiguity handling** — When two positionals cannot be uniquely classified, the CLI errors instead of guessing, preventing silent mistakes.
- **Numeric to canonical conversion** — Resolving numeric selectors to step names before passing to the workflow engine keeps all downstream state using canonical names, avoiding confusion or inconsistencies.

### Gotchas

- **Positional argument changes are CLI-only** — The workflow engine still receives canonical step names in `ControllerConfig.start_step`. The numeric token is resolved in `cli.py` and never reaches downstream modules.
- **Numeric index is 1-based** — `--start-step 1` starts from the first declared step, `2` from the second, etc. `0`, negative numbers, and out-of-range values fail immediately with a clear error.
- **Ambiguous two-positionals error cleanly** — If both positionals could be plan files, both could be workflow names, or neither can be resolved, the CLI exits with a specific error message naming the problematic tokens and suggesting the use of explicit flags.
- **Duplicate values must match exactly** — If you provide both a positional and a flag for the same field (e.g., `aflow run -p path1 path2`), they must resolve to the same value. A mismatch causes an error naming the conflict.
- **Step order is fixed at config load time** — Numeric indexes map against the workflow's declared step order as loaded from config. Changes to the workflow definition between CLI invocation and execution do not retroactively change what a numeric index maps to during the current run.

### Limits and non-changes

- **Interactive step picking behavior is preserved** — When no explicit start step is given and the plan is partly complete, the interactive startup picker still prompts (only when stdin/stdout are TTYs) and uses the same interactive-only rules as before.
- **Single-positional ergonomics remain unchanged** — One bare positional argument is always treated as a plan path, even if it coincidentally matches a workflow name. This ensures `aflow run workflow_name` (when `workflow_name` is also a valid plan file path) unambiguously runs the plan file, not the workflow.

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
