# Architecture

AFlow is a plan-driven workflow orchestrator that runs coding tasks through existing AI agent CLIs (Claude, Codex, Gemini, Kiro, OpenCode, Pi). It reads a checkpoint-based Markdown plan, dispatches steps to configurable harness profiles, evaluates condition-based transitions between steps, and logs every turn to disk.

## High-Level Data Flow

```mermaid
flowchart TD
    User["User runs: aflow run [workflow] plan.md"]
    CLI["cli.py — parse args, resolve repo root"]
    Config["config.py — load & validate aflow.toml + workflows.toml"]
    PlanParse["plan.py — parse Markdown plan into checkpoints"]
    Backup["workflow.py — back up original plan to plans/backups/"]
    WorkflowLoop["workflow.py — main turn loop"]
    PromptRender["workflow.py — render_step_prompts()"]
    Role["workflow.py — resolve_role_selector()"]
    Adapter["harnesses/ — build CLI invocation"]
    Subprocess["workflow.py — _run_process() via subprocess.Popen"]
    PlanReload["plan.py — reload plan, compute post-snapshot"]
    Transition["workflow.py — evaluate_condition() + pick_transition()"]
    RunLog["runlog.py — write run metadata & turn artifacts"]
    Banner["status.py — Rich live banner on stderr"]

    User --> CLI
    CLI --> Config
    Config --> CLI
    CLI --> PlanParse
    CLI --> WorkflowLoop
    WorkflowLoop --> PlanParse
    PlanParse --> Backup
    Backup --> WorkflowLoop
    WorkflowLoop --> PromptRender
    WorkflowLoop --> Role
    Role --> Adapter
    PromptRender --> Subprocess
    Adapter --> Subprocess
    Subprocess --> PlanReload
    PlanReload --> Transition
    Transition -->|"to = step_name"| WorkflowLoop
    Transition -->|"to = END"| Done["Return result"]
    WorkflowLoop --> RunLog
    WorkflowLoop --> Banner
```

## Module Breakdown

### `cli.py`
Entry point. Exposes two subcommands:
- **`aflow run [workflow_name] plan.md [-- extra instructions]`** -- runs a workflow.
- **`aflow run [workflow_name] --start-step STEP_NAME plan.md [-- extra instructions]`** -- starts a workflow from a named step.
- **`aflow install-skills [destination]`** -- copies bundled skills into harness skill directories.

Resolves the repo root via `git rev-parse`, loads and validates the TOML config, resolves the selected workflow and optional team, and handles startup step selection or recovery before calling `run_workflow()`.

Startup resolution happens in `main()` before the workflow loop starts:

1. Ensure `~/.config/aflow/aflow.toml` and sibling `workflows.toml` exist. If either file was created, print both paths and exit so the user can edit them first.
2. Resolve config and workflow.
3. Load the original plan strictly.
4. If the plan is complete and `--start-step` was given, fail with a clear error.
5. If the plan is half-done and the workflow has more than one step, require a TTY and prompt for an explicit step unless `--start-step` was given.
6. If strict plan loading fails with `inconsistent_checkpoint_state`, require a TTY and ask whether to recover.
7. When recovery is accepted, load a tolerant snapshot from the invalid plan, seed startup retry state, and pass both the parsed plan and retry context into `run_workflow()`.

### `config.py`
Loads `~/.config/aflow/aflow.toml` plus sibling `workflows.toml` (bootstrapped from the bundled defaults on first run). Parses and validates:
- **`[aflow]`** section: `default_workflow`, `keep_runs`, `max_turns`, `retry_inconsistent_checkpoint_state`, `banner_files_limit`, `max_same_step_turns`, `team_lead`, `branch_prefix`, `worktree_prefix`, `worktree_root`.
- **`[harness.<name>.profiles.<profile>]`** tables: `model`, optional `effort` per harness profile.
- **`[roles]`** and **`[teams.<name>]`** tables: role-to-selector mappings, with team tables allowed to override a subset of the global map.
- **`[prompts]`** section: named prompt templates.
- Bare **`[workflow]`** table in `workflows.toml`: lifecycle defaults (`setup`, `teardown`, `main_branch`, `merge_prompt`) inherited by all workflows that don't override them. Not a runnable workflow.
- **`[workflow.<name>]`** tables in `workflows.toml`: concrete workflows define `steps`, alias workflows use `extends` and optional `team`. Both may override lifecycle defaults with `setup`, `teardown`, `main_branch`, and `merge_prompt`.
- **`[workflow.<name>.steps.<step>]`** tables: `role` (global role key), `prompts` (list of prompt keys), `go` (transition array with `to` and optional `when` condition).

Lifecycle validation enforces that `(setup, teardown)` is one of three accepted tuples: `([], [])`, `(["branch"], ["merge"])`, or `(["worktree", "branch"], ["merge", "rm_worktree"])`. Any other combination is rejected at load time with the exact workflow path.

When a workflow's effective `teardown` includes `merge`, validation also checks that `[aflow].team_lead` is set and, for config-defined teams, that the role can be resolved through team overrides or global `[roles]`.

Cross-validates that harness profiles, roles, teams, prompts, aliases, and transition targets all reference things that exist.

### `plan.py`
Parses a Markdown plan file into structured checkpoint data. Expects `### [x] Checkpoint ...` headings (h3 with checkbox) and `- [ ] step` items underneath. Produces a `PlanSnapshot` with:
- `current_checkpoint_name`, `current_checkpoint_index`
- `unchecked_checkpoint_count`, `current_checkpoint_unchecked_step_count`
- `is_complete` (all checkpoints checked)
- `total_checkpoint_count`

Also detects `## Git Tracking` sections required by review skills.

### `workflow.py`
The core engine. `run_workflow()` executes the turn loop:

1. Back up the original plan to `plans/backups/`.
2. Parse the plan unless `main()` already provided a pre-loaded `ParsedPlan`.
3. If the workflow's `setup` is non-empty, run lifecycle preflight checks and then create the execution environment. Preflight validates: branch name collision, worktree path collision, correct startup branch, that `main_branch` points to a local commit (an unborn branch in a brand-new repo is rejected), and (for worktree workflows only) that any dirty files in the primary checkout are confined to `plans/` (untracked or gitignored plan files are allowed). For non-worktree workflows, the working tree must be clean. Branch-only setup creates a local feature branch from `main_branch` in the primary checkout. Worktree setup creates a linked worktree from `main_branch` under `worktree_root` and creates the feature branch inside that worktree. The primary checkout remains the control root for run artifacts; the worktree is the execution root for normal steps.
4. For each turn (up to `max_turns`):
   a. Reload the plan from disk (the agent may have modified it). For worktree flows, plan path placeholders (`{ORIGINAL_PLAN_PATH}`, `{ACTIVE_PLAN_PATH}`, `{NEW_PLAN_PATH}`) are translated from primary-root-relative to worktree-root-relative before being handed to the agent; they are translated back after the turn.
   b. For worktree flows, sync the original plan into the worktree before rendering prompts (so untracked plans under `plans/` are available for the agent to read and modify).
   c. Resolve the step's role through the selected team and global role map to get the concrete harness selector.
   d. Render prompt templates with path placeholders.
   e. Build a `HarnessInvocation` via the adapter, using `execution_repo_root` as the subprocess cwd.
   f. Run the agent CLI as a subprocess, streaming stdout/stderr.
   g. For worktree flows, sync the original plan back from the worktree to the primary checkout immediately after the harness returns (before parsing post-turn state). This ensures the primary copy reflects any edits the harness made, even if the harness exited with non-zero status.
   h. Before reloading the plan, scan stdout and stderr for a line starting with `AFLOW_STOP:`. If found, fail the run immediately with the extracted reason without entering the plan-reload or transition path.
   i. Reload the plan again to get the post-turn snapshot. If the plan is left in an inconsistent checkpoint state (heading marked complete but unchecked steps remain) and the harness exited cleanly, a retry may be scheduled instead of failing immediately (see `retry_inconsistent_checkpoint_state`).
   j. Evaluate `go` transitions using condition symbols (`DONE`, `NEW_PLAN_EXISTS`, `MAX_TURNS_REACHED`).
   k. Log turn artifacts and update run metadata.
   l. If transition target is `END`, return. For multi-step workflows, check the same-step cap: if the same step has been selected consecutively `max_same_step_turns` times, fail the run before starting the next turn. Otherwise, advance to the next step.
5. After normal workflow completion, if `teardown` includes `merge`, execute the merge handoff: resolve `[aflow].team_lead` through the effective team, build a merge prompt (built-in `aflow-merge` instruction plus rendered `merge_prompt` entries), and run the `team_lead` agent from the primary checkout. After the agent returns, verify: no unmerged index entries, clean working tree, HEAD on `main_branch`, and feature branch is an ancestor of the target. Only after all checks pass does `rm_worktree` (if configured) remove the linked worktree. Any verification failure leaves the feature branch and worktree intact and fails the run with the specific failed check.

A scheduled retry skips the pre-turn plan reload and reuses the last valid snapshot and saved prompt context. The same `ACTIVE_PLAN_PATH`, `NEW_PLAN_PATH`, and resolved step selector are reused; the retry appendix (containing the exact parse error) is added to the prompt. Startup recovery seeds that same retry machinery by passing a `RetryContext` into `run_workflow()`, which stores the step name, role, resolved selector, and prompt context in `state.pending_retry` before turn 1. Retry turns still count toward `max_turns`.

The condition evaluator is a full recursive-descent parser supporting `&&`, `||`, `!`, and parentheses over the three condition symbols.

Prompt templates support `file://` references (absolute, config-relative, or cwd-relative).

### `harnesses/`
Adapter layer. Each harness implements `HarnessAdapter.build_invocation()` to produce a `HarnessInvocation` (argv, env, prompt texts). Seven adapters:

| Harness    | CLI binary  | Prompt mode                    | Effort support |
|------------|-------------|--------------------------------|----------------|
| `claude`   | `claude`    | `--system-prompt` flag         | Yes            |
| `codex`    | `codex`     | system prefixed into user prompt | Yes          |
| `copilot`  | `copilot`   | system prefixed into user prompt | Yes          |
| `gemini`   | `gemini`    | system prefixed into user prompt | No           |
| `kiro`     | `kiro-cli`  | system prefixed into user prompt | No           |
| `opencode` | `opencode`  | system prefixed into user prompt | No           |
| `pi`       | `pi`        | `--system-prompt` flag         | Yes            |

All harnesses run in non-interactive, auto-approve mode with full tool access.

### `run_state.py`
Data classes for runtime state:
- `ControllerConfig` -- immutable run parameters (repo root, plan path, max turns, keep runs, extra instructions).
- `ControllerState` -- mutable per-run state (snapshot, turn count, issues, timing, status, pending retry context, consecutive same-step streak tracking).
- `RetryContext` -- frozen dataclass holding everything needed to rerun the same step on the next turn without re-parsing the broken plan (step name, role, resolved selector, pre-failure snapshot, saved plan paths, base prompt, parse error string, attempt counter, retry limit).
- `ExecutionContext` -- frozen dataclass holding lifecycle execution state: `primary_repo_root`, `execution_repo_root` (worktree path for worktree flows, same as primary for branch-only), `main_branch`, `feature_branch`, `worktree_path` (or `None` for branch-only), `setup`, `teardown`.
- `ControllerConfig` also carries the selected startup step, if any, so the workflow loop can start from a non-default step without re-parsing CLI arguments.
- `ControllerRunResult` -- final result with end reason.
- `WorkflowEndReason` -- literal type: `already_complete`, `done`, `max_turns_reached`, `transition_end`.

### `runlog.py`
Persists run data under `.aflow/runs/<timestamp>-<uuid>/`:
- `run.json` -- run-level metadata, updated after each turn.
- `turns/turn-NNN/` -- per-turn artifacts: `system-prompt.txt`, `user-prompt.txt`, `effective-prompt.txt`, `argv.json`, `env.json`, `stdout.txt`, `stderr.txt`, `result.json`.

Prunes old run directories to respect `keep_runs`.

### `git_status.py`
Git snapshot helpers used by the banner and CLI. Provides three public data classes (`GitBaseline`, `GitSummary`, `WorktreeProbe`) and three functions:
- `probe_worktree(repo_root)` — checks whether the working tree is dirty at startup.
- `capture_baseline(repo_root)` — snapshots the current HEAD SHA and a working-tree tree OID (using a temporary `GIT_INDEX_FILE`) as a before-run baseline.
- `summarize_since_baseline(repo_root, baseline)` — compares the current working tree against the baseline and returns file-change counts, net line deltas, commit count, and changed paths.

All three functions return `None` when git is unavailable or fails, so the workflow always runs regardless of git state.

### `status.py`
Rich-based live banner rendered to stderr during a run. Shows elapsed time, workflow/step name, harness, model, checkpoint progress, turn count, issues, plan paths, git summary (if available), and status.

`BannerRenderer` owns a background daemon thread that rebuilds and pushes the panel every `refresh_interval_seconds` (default 1 s) and polls for a new `GitSummary` every `git_poll_interval_seconds` (default 10 s). This keeps the elapsed timer alive between step transitions without requiring external pushes. `set_context(...)` is used to update mutable banner fields instead of directly writing private attributes.

### `skill_installer.py`
Discovers the seven bundled skills from package resources and copies them into harness-specific skill directories. Supports auto-detection (looks for harness CLIs on PATH) and manual mode (explicit destination path). Handles duplicate destinations when multiple harnesses share a path (e.g., codex, copilot, gemini, and pi all use `~/.agents/skills`).

### `bundled_skills/`
Seven Markdown-based skill definitions installed into harness skill directories:

| Skill                       | Purpose                                                        |
|-----------------------------|----------------------------------------------------------------|
| `aflow-plan`                | Create a checkpoint handoff plan                               |
| `aflow-execute-plan`        | Execute an entire plan autonomously, checkpoint by checkpoint  |
| `aflow-execute-checkpoint`  | Execute exactly one checkpoint, then stop                      |
| `aflow-review-squash`       | Review completed work; approve+squash or create fix plan       |
| `aflow-review-checkpoint`   | Review one checkpoint; approve or create fix plan              |
| `aflow-review-final`        | Final review without squash; approve or create follow-up plan  |
| `aflow-merge`               | Local-only merge handoff; preserves commits, resolves conflicts, emits `AFLOW_STOP:` for irrecoverable states |

## Workflow Configuration

Workflows are state machines defined in `workflows.toml`. Each step has:
- A `role` key that resolves through the selected team and then global `[roles]`.
- A `prompts` list referencing named prompt templates.
- A `go` array of transitions, each with a `to` target (step name or `END`) and an optional `when` condition expression.

Workflow tables can also use `extends` to alias a concrete base workflow and `team` to override the team for that alias. In v1, aliases inherit the base workflow's steps and cannot redefine them.

Bare `[workflow]` in `workflows.toml` is a lifecycle defaults table. It supplies `setup`, `teardown`, `main_branch`, and `merge_prompt` values that all concrete workflows and aliases inherit unless they override them individually. It is not a runnable workflow.

Lifecycle config controls the git environment created before workflow steps begin and torn down after normal completion. The accepted `(setup, teardown)` pairs are:
- `([], [])` — no lifecycle, engine behaves exactly as before
- `(["branch"], ["merge"])` — create a local feature branch, run steps there, then invoke merge handoff
- `(["worktree", "branch"], ["merge", "rm_worktree"])` — create a linked worktree from `main_branch`, run steps in that worktree, invoke merge handoff from the primary checkout, then remove the worktree after verified merge

Merge is model-driven through the `aflow-merge` skill. The engine resolves the `team_lead` role, prepends the built-in `aflow-merge` instruction, appends rendered `merge_prompt` entries, and runs the agent from the primary checkout. After the agent returns, the engine verifies merge success before removing any worktree.

Transitions are evaluated top-to-bottom; the first match wins. An entry without `when` is an unconditional fallback.

The built-in workflow diagrams live in the README so the default workflow shapes are visible in the main docs without sending readers into the architecture reference first.

## Directory Layout

```
aflow/
  __main__.py          # entrypoint
  cli.py               # argument parsing, main(), dirty-worktree gate
  config.py            # TOML config loading and validation
  plan.py              # Markdown plan parser
  workflow.py          # workflow engine (turn loop, conditions, transitions)
  run_state.py         # runtime data classes
  runlog.py            # run/turn artifact persistence
  status.py            # Rich live banner with background refresh thread
  git_status.py        # git snapshot helpers (probe, baseline, summary)
  skill_installer.py   # bundled skill installer
  aflow.toml           # global config, harness profiles, roles, teams, prompts
  workflows.toml       # workflow definitions and aliases
  harnesses/
    __init__.py        # adapter registry (ADAPTERS dict)
    base.py            # HarnessAdapter protocol, HarnessInvocation dataclass
    claude.py          # Claude Code adapter
    codex.py           # Codex adapter
    gemini.py          # Gemini adapter
    kiro.py            # Kiro adapter
    opencode.py        # OpenCode adapter
    pi.py              # Pi adapter
  bundled_skills/
    aflow-plan/              SKILL.md
    aflow-execute-plan/      SKILL.md
    aflow-execute-checkpoint/ SKILL.md
    aflow-review-squash/     SKILL.md
    aflow-review-checkpoint/ SKILL.md
    aflow-review-final/      SKILL.md
    aflow-merge/             SKILL.md
tests/
  test_aflow.py        # workflow engine tests
  test_skill_install.py # skill installer tests
plans/                 # user plan files and backups
  backups/             # automatic plan backups
  in-progress/         # active handoff plans
.aflow/
  runs/                # per-run logs (gitignored)
```

## Key Design Decisions

- **Plan as source of truth.** The Markdown plan file on disk is authoritative. The engine re-reads it before and after every turn because the agent subprocess may modify it (checking off steps/checkpoints).
- **Harness-agnostic.** The engine doesn't know how any specific agent CLI works. Adapters translate a uniform interface into CLI-specific argv/env. Adding a new harness means one ~30-line adapter file.
- **Interactive startup decisions live in the CLI.** Step picking and startup recovery happen before `run_workflow()` so the workflow loop only deals with an already-chosen step and, when needed, a seeded retry context.
- **Condition-based transitions.** Step transitions use a small expression language over three boolean symbols rather than hardcoded control flow. This keeps workflow definitions declarative.
- **Structured run logging.** Every turn's prompts, outputs, and snapshots are persisted to `.aflow/runs/` for debugging and auditability. Old runs are pruned automatically.
- **Skills as Markdown.** The seven bundled skills are plain SKILL.md files that get copied into each harness's skill directory. They contain behavioral instructions that the agent reads at runtime, not executable code.
- **Local-only lifecycle.** Branch and worktree creation, feature branch setup, and merge handoff all operate on local refs only. The engine never fetches, pulls, or pushes. The primary checkout is the control root for run artifacts and merge verification even when normal steps execute inside a linked worktree.
