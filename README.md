# aflow

`aflow` runs checkpoint-based coding plans through existing agent CLIs such as Codex, Claude, Gemini, Kiro, OpenCode, and Pi.

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
uv run python -m aflow path/to/plan.md
```

## Usage

```bash
aflow path/to/plan.md
aflow --workflow review_loop path/to/plan.md
aflow --max-turns 10 path/to/plan.md
aflow path/to/plan.md -- keep edits small and update docs if behavior changes
```

If `--workflow` is omitted, `aflow` uses `aflow.default_workflow` from config.

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

If that file does not exist, `aflow` writes a starter config and exits. The starter config includes placeholder model values that must be filled in before the first real run.

Example:

```bash
aflow path/to/plan.md
# Config bootstrapped. Fill in the following model values before running:
#   harness.codex.profiles.high.model
#   harness.opencode.profiles.default.model
```

## Config

Config is standard TOML. The current schema has four top-level sections:

- `aflow`
- `harness`
- `workflow`
- `prompts`

Example:

```toml
[aflow]
default_workflow = "review_loop"

[harness.opencode.profiles.implement]
model = "zai-coding-plan/glm-4.7"

[harness.codex.profiles.review]
model = "gpt-5.4"
effort = "high"

[workflow.review_loop.steps.review_plan]
profile = "codex.review"
prompts = ["review_plan"]
go = [{ to = "implement_plan" }]

[workflow.review_loop.steps.implement_plan]
profile = "opencode.implement"
prompts = ["implementation_prompt"]
go = [
  { to = "END", when = "DONE || MAX_TURNS_REACHED" },
  { to = "review_plan", when = "NEW_PLAN_EXISTS" },
  { to = "implement_plan" },
]

[prompts]
review_plan = "Review the plan at {ORIGINAL_PLAN_PATH}. If tighter follow-up work is needed, write it to {NEW_PLAN_PATH}."
implementation_prompt = "Work from {ACTIVE_PLAN_PATH}. Re-read it from disk before acting."
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

Prompt templates support these placeholders:

- `{ORIGINAL_PLAN_PATH}`
- `{ACTIVE_PLAN_PATH}`
- `{NEW_PLAN_PATH}`

Those placeholders belong in workflow prompt templates. The bundled skills under `skills/` are static guidance files that a harness can inject around those prompts, not places to author unresolved workflow variables.

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

Extra CLI instructions after the plan path are appended to the rendered step prompt.

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

## Run Logs

Each run writes structured artifacts under `.aflow/runs/<run-id>/`.

Saved data includes:

- rendered prompts
- argv and environment metadata
- stdout and stderr for each step
- plan snapshots before and after each step
- evaluated conditions and the chosen transition
- top-level run metadata such as workflow name, current step, turns completed, and plan paths

Older run directories are pruned automatically. The default retention is `20` runs. The default max turn limit is `15`.

## Included Skills

This repo also ships optional skills under `skills/`. They are static guidance files that a harness can inject around workflow prompts.

- `aflow-plan` - static guidance for writing aflow-compatible checkpoint plans
- `review-squash` - static guidance for final review and focused fix-plan creation
- `execute-aflow-plan` - lightweight reinforcement for plan-driven execution

The workflow config is where the plan-path placeholders belong. The skills themselves stay free of workflow template variables.

## Repository Layout

- `aflow/` - package code
- `skills/` - optional workflow skills
- `plans/` - example and in-progress plan artifacts
- `pyproject.toml` - package metadata and console entrypoint

## Troubleshooting

- If the harness exits non-zero, `aflow` stops and prints the run log directory.
- If no `go` transition matches, the run fails with the step name and evaluated condition values.
- If the selected workflow does not exist, `aflow` exits before starting a run.
- If the plan format is invalid, the run fails before or after the step that produced the invalid state.
