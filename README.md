# aflow

`aflow` is a plan-driven workflow runner for `codex`, `claude`, `opencode`, `pi`, and `gemini`. It reads a checkpoint plan from disk, runs one fresh harness process per workflow step, and follows explicit transition rules to decide what happens next.

## Install

Install it as a tool with `uv`:

```bash
uv tool install /Users/evren/code/agent_flow
```

That gives you the `aflow` command on your `PATH`.

## Usage

```bash
aflow path/to/plan.md [extra instructions ...]
aflow --workflow review_loop path/to/plan.md
aflow --max-turns 10 path/to/plan.md
```

If `--workflow` is omitted, `aflow` uses `default_workflow` from config.

From a source checkout:

```bash
uv run python -m aflow path/to/plan.md
```

## First Run

When `~/.config/aflow/aflow.toml` does not exist, `aflow` creates a starter config with placeholder model values. Fill those in before running:

```bash
aflow path/to/plan.md
# Config bootstrapped. Fill in the following model values before running:
#   harness.codex.profiles.high.model
#   harness.opencode.profiles.default.model
```

## Config

`aflow` reads `~/.config/aflow/aflow.toml`. The config defines harness profiles, named workflows, step transitions, and prompt templates.

```toml
[aflow]
default_workflow = "simple"

[harness.opencode.profiles.default]
model = "zai-coding-plan/glm-4.7"

[harness.codex.profiles.high]
model = "gpt-5.4"
effort = "high"

[harness.claude.profiles.opus]
model = "FILL_IN_MODEL"
effort = "medium"

[workflow.simple.steps.implement_plan]
profile = "opencode.default"
prompts = ["implementation_prompt"]
go = [
  { to = "END", when = "DONE || MAX_TURNS_REACHED" },
  { to = "implement_plan" },
]

[workflow.review_loop.steps.review_plan]
profile = "claude.opus"
prompts = ["review_prompt"]
go = [{ to = "implement_plan" }]

[workflow.review_loop.steps.implement_plan]
profile = "opencode.default"
prompts = ["implementation_prompt"]
go = [{ to = "review_implementation" }]

[workflow.review_loop.steps.review_implementation]
profile = "codex.high"
prompts = ["review_squash", "make_review_plan"]
go = [
  { to = "END", when = "DONE || MAX_TURNS_REACHED" },
  { to = "implement_plan" },
]

[prompts]
implementation_prompt = "Work from {ACTIVE_PLAN_PATH}. Re-read the plan from disk before acting."
review_prompt = "Review the plan at {ORIGINAL_PLAN_PATH} for weak spots."
review_squash = "Review progress against {ORIGINAL_PLAN_PATH}. Write new plan to {NEW_PLAN_PATH}."
make_review_plan = "Create the next plan at {NEW_PLAN_PATH}. Use {ACTIVE_PLAN_PATH} as input."
```

Supported harnesses are `claude`, `codex`, `gemini`, `opencode`, and `pi`.

### Config Rules

- Every step `profile` must be fully qualified: `harness.profile`, not a bare harness name.
- `model` is the only supported model key. `default_model` is not accepted.
- Harness-level `model` and `effort` are not supported. Put them under profiles.
- `prompts` values can be inline text or `file://` URIs relative to the config directory.
- Prompt templates support `{ORIGINAL_PLAN_PATH}`, `{NEW_PLAN_PATH}`, and `{ACTIVE_PLAN_PATH}`.
- `go` transitions are evaluated in declaration order. First match wins.
- Condition symbols: `DONE`, `NEW_PLAN_EXISTS`, `MAX_TURNS_REACHED`.
- Boolean operators: `&&`, `||`, `!`, parentheses.
- A missing `when` is an unconditional fallback.

## What It Does

`aflow` reads a checkpoint plan from disk, evaluates the first step of the selected workflow, runs one harness process for that step, then follows `go` transitions to pick the next step or `END`.

- `ORIGINAL_PLAN_PATH` is always the user-supplied plan file. It never changes.
- `DONE` is computed from the original plan file only.
- `NEW_PLAN_PATH` is generated once per turn (format: `<stem>-cpNN-vNN.<suffix>`).
- `ACTIVE_PLAN_PATH` starts as the original plan. After a turn, it updates to `NEW_PLAN_PATH` only if that file was actually created by the harness step.
- The run ends when a transition selects `END`.
- If the run reaches max turns without an `END` transition, it is a workflow error.

## Live Status Banner

While a harness step is running, `aflow` shows a Rich banner on stderr with:

- elapsed runtime
- workflow name and current step
- harness, model, and effort level
- checkpoint progress and turn count
- original plan, active plan, and generated plan paths
- current status

## Non-interactive and Full-Permission Mode

Each harness uses a documented headless mode with full permissions:

- `opencode` uses `opencode run` with the prefixed prompt and `--dir` for the repo root
- `gemini` uses `--prompt` (not positional query), `--approval-mode yolo`, and `--sandbox=false`
- `codex` uses `exec` with `--dangerously-bypass-approvals-and-sandbox`
- `claude` uses `-p` with `--permission-mode bypassPermissions` and `--dangerously-skip-permissions`
- `pi` uses `--print` with a `--tools` list

## Limits

- max turns: `15` via `--max-turns`
- retained runs: `20` via `--keep-runs`

## Run Logs

Run data lives under `.aflow/runs/<run-id>/`.

Each run stores:

- prompts and argv
- stdout and stderr
- per-turn result metadata including workflow step name, selector, conditions, and chosen transition
- run metadata including workflow name, original/active/generated plan paths
- live status state

Older run directories are pruned automatically.

## Shipped Skills

This repo ships a few skills for the `aflow` workflow:

- `ralf-handoff-plan`: writes strict checkpoint handoff plans that `aflow` can execute from disk
- `ralf-review-squash`: reviews a completed handoff and either squashes it or produces a focused fix plan

Those skills live under `skills/` and are repo assets. They are not required to run the package itself.

## Repository Layout

- `aflow/`: the Python package
- `pyproject.toml`: package metadata, dependencies, and console-script entrypoint
- `skills/`: optional workflow assets shipped alongside the tool
- `plans/`: saved working plans and handoff artifacts

For source checkouts, `uv run python -m aflow` is the clean entrypoint. For installed use, the console script from `pyproject.toml` is the real entrypoint.

## Troubleshooting

- If the harness exits non-zero, `aflow` stops and prints the run log directory.
- If no `go` transition matches the current conditions, `aflow` fails with the step name and condition values.
- If `default_workflow` is missing or the workflow name is unknown, `aflow` exits before starting.
- If a run stalls, inspect the saved data under `.aflow/runs/<run-id>/`.
