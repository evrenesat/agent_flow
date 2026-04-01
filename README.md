# aflow

`aflow` runs plan-driven coding workflows through existing agent CLIs such as Codex, Claude, Gemini, Kiro, OpenCode, and Pi.

It does not call provider APIs directly. It shells out to the harnesses you already use and have access to. The main use case is a stricter loop where a stronger model plans or reviews, a cheaper model implements the current checkpoint, and the run keeps moving until the original plan is done or the workflow reaches `END`.

## Install

Requires Python `3.11+`.

Install with `uv`:

```bash
uv tool install git+https://github.com/evrenesat/agent_flow.git
```

That exposes the `aflow` command on your `PATH`.

If you are working from a local checkout, you can also run:

```bash
uv run python -m aflow run path/to/plan.md
```

## Install Skills

`aflow install-skills` copies the six bundled skills into harness skill directories. In auto mode, it only targets supported harness CLIs that are already on `PATH`.

Auto mode:

```bash
aflow install-skills
```

Manual mode:

```bash
aflow install-skills ~/.claude/skills
```

Skip the confirmation prompt:

```bash
aflow install-skills --yes
```

The auto-install destination map is:

- `claude` -> `~/.claude/skills`
- `codex` -> `~/.codex/skills`
- `gemini` -> `~/.agents/skills`
- `kiro` -> `~/.kiro/skills`
- `opencode` -> `~/.config/opencode/skills`
- `pi` -> `~/.agents/skills`

## Usage

```bash
aflow run path/to/plan.md
aflow run review_implement_review path/to/plan.md
aflow run -mt 10 path/to/plan.md
aflow run path/to/plan.md -- keep edits small and update docs if behavior changes
```

If the workflow name is omitted, `aflow` uses `aflow.default_workflow` from config.

## Why This Exists

Some provider subscriptions and free monthly allowances are tied to the provider's own CLI or harness rather than an API budget. `aflow` is for that setup.

Instead of building around direct API calls, `aflow` lets you:

- keep planning and review with a stronger profile
- delegate checkpoint-sized implementation work to a cheaper profile
- carry state through a plan file on disk instead of chat history
- enforce an explicit transition loop instead of an open-ended agent session

## Plan Format

`aflow` reads a Markdown plan from disk and derives progress from checkpoint headings plus unchecked task items inside each checkpoint.

Minimal example:

```md
# Plan

### [ ] Checkpoint 1: Wire The CLI
- [ ] add the command entrypoint
- [ ] cover it with tests

### [ ] Checkpoint 2: Update Docs
- [ ] document the final behavior
```

Current parser rules:

- Checkpoint headings must start with `### [ ] Checkpoint ...` or `### [x] Checkpoint ...`.
- Only task items under a checkpoint section count toward that checkpoint's remaining work.
- A checked checkpoint heading cannot contain unchecked task items.
- If no checkpoint sections are found, the run fails before starting.

## First Run

`aflow` reads `~/.config/aflow/aflow.toml`.

If that file does not exist, `aflow` copies the packaged `aflow/aflow.toml` into place and exits. That file is the default config source, so edit it there if you want different models, profiles, or workflows before the first real run.

Example:

```bash
aflow run path/to/plan.md
# Config bootstrapped at ~/.config/aflow/aflow.toml
# Review the copied profiles and adjust them if needed, then run again
```

## Config

Config is standard TOML. The current schema has four top-level sections:

- `aflow`
- `harness`
- `workflow`
- `prompts`

### `[aflow]` options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `default_workflow` | string | — | Workflow to run when none is specified on the CLI. |
| `keep_runs` | int | `20` | Number of run log directories to retain under `.aflow/runs/`. Older directories are pruned automatically after each run. |

Example:

```toml
[aflow]
default_workflow = "review_implement_review"
keep_runs = 10
```

### Full example

```toml
[aflow]
default_workflow = "review_implement_review"

[harness.opencode.profiles.implement]
model = "zai-coding-plan/glm-4.7"

[harness.codex.profiles.review]
model = "gpt-5.4"
effort = "high"

[workflow.review_implement_review.steps.review_plan]
profile = "codex.review"
prompts = ["review_plan"]
go = [{ to = "implement_plan" }]

[workflow.review_implement_review.steps.implement_plan]
profile = "opencode.implement"
prompts = ["simple_implementation"]
go = [
  { to = "review_plan", when = "NEW_PLAN_EXISTS" },
  { to = "END" },
]

[prompts]
review_plan = "Review the plan at {ORIGINAL_PLAN_PATH}. If tighter follow-up work is needed, write it to {NEW_PLAN_PATH}."
simple_implementation = "Work from {ACTIVE_PLAN_PATH}. Use 'aflow-execute-plan' skill."
```

Config rules that matter in practice:

- A step `profile` must be fully qualified as `harness.profile`.
- Profile tables support `model` and optional `effort`.
- There is no harness-level `model` or `effort` setting outside profiles.
- A workflow starts at the first declared step in `workflow.<name>.steps`.
- `prompts` must be a non-empty array of prompt keys.
- Prompt values can be inline text or `file://` paths in three forms: absolute (`file:///...`), config-relative (`file://path/to/file.txt`), or cwd-relative (`file://./path/to/file.txt`).
- `go` transitions are checked in declaration order. First match wins.
- Supported condition symbols are `DONE`, `NEW_PLAN_EXISTS`, and `MAX_TURNS_REACHED`.
- Boolean expressions support `&&`, `||`, `!`, and parentheses.
- A transition without `when` is an unconditional fallback.

Condition symbols mean exactly this at transition-evaluation time:

- `DONE` is true when the original user-supplied plan file is complete after the current step finishes. It is based on `ORIGINAL_PLAN_PATH`, not on any generated follow-up plan.
- `NEW_PLAN_EXISTS` is true when the current step actually created the generated candidate file for this turn at `NEW_PLAN_PATH`.
- `MAX_TURNS_REACHED` is true only on the last allowed turn, when the current turn number is equal to the configured `max_turns`.

Prompt templates support these placeholders:

- `{ORIGINAL_PLAN_PATH}`
- `{ACTIVE_PLAN_PATH}`
- `{NEW_PLAN_PATH}`

Those placeholders belong in workflow prompt templates. The bundled skills under `aflow/bundled_skills/` are static guidance files that a harness can inject around those prompts, not places to author unresolved workflow variables.

## How A Run Works

Each workflow step launches one fresh harness process.

At a high level:

1. `aflow` loads the selected workflow and reads the original plan file.
2. It starts at the workflow's first declared step.
3. It renders the step prompts, resolves the selected harness profile, and runs the harness CLI once for that step.
4. After the harness returns, it re-reads the original plan file and evaluates the step's `go` transitions.
5. The next matching transition decides whether to continue with another step or stop at `END`.

Plan-path behavior is strict:

- `ORIGINAL_PLAN_PATH` is always the user-supplied plan file.
- `DONE` is computed from `ORIGINAL_PLAN_PATH`, not from a generated follow-up plan.
- `NEW_PLAN_PATH` is generated once per turn with the format `<stem>-cpNN-vNN.<suffix>`.
- `ACTIVE_PLAN_PATH` starts as the original plan path.
- `ACTIVE_PLAN_PATH` changes only when the current harness step actually writes `NEW_PLAN_PATH`.
- Before the workflow starts, `aflow` copies the original plan into `<repo_root>/plans/backups/`.
- If the matching backup content already exists, `aflow` reuses it.
- If the same backup name already exists with different content, `aflow` writes the next `_vNN` file instead of overwriting anything.

Extra CLI instructions after the plan path are appended to the rendered step prompt.

## Loop Limits

`max_turns` is the only built-in hard cap on turn count. The workflow runner executes turns with a fixed `1..max_turns` loop, so a workflow cannot exceed that number of turns even if its `go` transitions keep routing back to earlier steps.

That hard cap does not end the run by itself in the success path. On the last allowed turn:

- `MAX_TURNS_REACHED` evaluates true for transition matching.
- If one of that step's transitions matches and routes to `END`, the run completes successfully with end reason `max_turns_reached` unless `DONE` is also true, in which case the end reason is `done`.
- If no transition routes to `END` before the loop exhausts, the run fails with "reached max turns limit ... without a transition to END".

Other things can stop a run earlier, but they are not extra turn-limit mechanisms:

- the plan is already complete before any turn starts
- a step transitions to `END`
- no `go` transition matches for the current step
- the harness exits non-zero
- the original plan becomes unreadable or invalid

## Harnesses

Supported harness adapters are:

- `claude`
- `codex`
- `gemini`
- `kiro`
- `opencode`
- `pi`

`aflow` expects those CLIs to already be installed and authenticated on the machine. It does not manage provider auth or SDK setup.

Current adapter behavior:

- `codex` uses `codex exec --dangerously-bypass-approvals-and-sandbox`
- `claude` uses `claude -p --permission-mode bypassPermissions --dangerously-skip-permissions`
- `gemini` uses `gemini --prompt ... --approval-mode yolo --sandbox=false`
- `kiro` uses `kiro-cli chat --no-interactive --trust-all-tools`
- `opencode` uses `opencode run --format default --dir <repo-root>`
- `pi` uses `pi --print --tools read,bash,edit,write,grep,find,ls`

`effort` is currently passed through only by the `claude`, `codex`, and `pi` adapters.


## Live Status

While a step is running, `aflow` shows a Rich status panel on stderr with:

- elapsed time
- workflow and current step
- harness, model, and effort
- checkpoint progress and turn count
- original, active, and generated plan paths
- current run status

## Success Reporting

When a workflow finishes successfully, `aflow` prints one line on stdout. The message names the workflow, how many turns ran, and why the run stopped.

The machine-readable `end_reason` values are:

- `already_complete`
- `done`
- `max_turns_reached`
- `transition_end`

`transition_end` covers successful `END` transitions when the plan is still incomplete and the chosen transition is not driven by `DONE` or `MAX_TURNS_REACHED`, including an unconditional `go = [{ to = "END" }]`.

## Run Logs

Each run writes structured artifacts under `.aflow/runs/<run-id>/`.

Saved data includes:

- rendered prompts
- argv and environment metadata
- stdout and stderr for each step
- plan snapshots before and after each step
- evaluated conditions and the chosen transition
- `end_reason` on successful runs, both in `run.json` and in the final turn artifact
- top-level run metadata such as workflow name, current step, turns completed, plan paths, and the terminal end reason

If a run reaches the hard loop limit without any transition to `END`, that is still a failure, even if the last turn also satisfies `MAX_TURNS_REACHED`.

Older run directories are pruned automatically. The retention count is controlled by `keep_runs` in `[aflow]` config (default: `20`). The default max turn limit is `15` and can be overridden with `--max-turns` / `-mt`.

## Shipped Workflows

The bundled `aflow.toml` includes three ready-to-use workflows:

- `ralph` - single-step implementation loop, no review
- `review_implement_review` - review, implement, then review again with `aflow-review-squash`. On approval the reviewer squashes all post-handoff commits into one final commit. This squash behavior is specific to this workflow, not an engine-wide invariant.
- `review_implement_cp_review` - checkpoint-scoped review with `aflow-review-checkpoint` and a final no-squash audit with `aflow-review-final`. Checkpoint commit structure is preserved on approval.

## Included Skills

This repo also ships optional skills under `aflow/bundled_skills/`. `aflow install-skills` copies them into the harness-specific skill roots listed above.

- `aflow-plan` - static guidance for writing aflow-compatible checkpoint plans
- `aflow-execute-plan` - lightweight reinforcement for executing an active plan, including review-generated non-checkpoint follow-up plans
- `aflow-execute-checkpoint` - checkpoint-scoped execution for the original handoff plan, with support for focused non-checkpoint follow-up plans when review creates one
- `aflow-review-squash` - final review for completed autonomous runs, including whole-handoff squash or fix-plan creation
- `aflow-review-checkpoint` - checkpoint-scoped review for the latest checkpoint attempt
- `aflow-review-final` - no-squash final auditor for checkpoint workflows after the original plan is complete

The workflow config is where the plan-path placeholders belong. The skills themselves stay free of workflow template variables.

## Repository Layout

- `aflow/` - package code
- `aflow/bundled_skills/` - packaged optional workflow skills
- `tests/` - test suite
- `plans/` - example and in-progress plan artifacts
- `pyproject.toml` - package metadata and console entrypoint

## Troubleshooting

- If the harness exits non-zero, `aflow` stops and prints the run log directory.
- If no `go` transition matches, the run fails with the step name and evaluated condition values.
- If the selected workflow does not exist, `aflow` exits before starting a run.
- If the plan format is invalid, the run fails before or after the step that produced the invalid state.
