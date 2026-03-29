# aflow

`aflow` is a plan checkpoint controller for `codex`, `pi`, `claude`, `opencode`, and `gemini`.

## Install

Install it as a tool with `uv`:

```bash
uv tool install /Users/evren/code/agent_flow
```

That gives you the `aflow` command on your `PATH`.

## Usage

From an installed tool:

```bash
aflow --harness codex --model gpt-5.4 path/to/plan.md [extra instructions ...]
aflow --harness codex --model gpt-5.4 --effort high path/to/plan.md
aflow --harness opencode --model zai-coding-plan/glm-5-turbo path/to/plan.md
aflow --harness gemini --model gemini-2.5-pro path/to/plan.md
```

From a source checkout:

```bash
python3 -m aflow --harness codex --model gpt-5.4 path/to/plan.md
```

`--harness` and `--model` are required. Supported harnesses are `claude`, `codex`, `gemini`, `opencode`, and `pi`.

## What It Does

`aflow` reads a checkpoint plan from disk, runs one fresh harness process per turn, and keeps going until the plan is complete or the controller hits a clear stop condition.

It treats the plan file on disk as the source of truth. The controller does not rely on harness session resume state or hidden completion signals.

## Live Status Banner

While a harness turn is running, `aflow` shows a persistent Rich banner on stderr with:

- elapsed runtime
- harness, model, and effort level
- checkpoint progress (`current/total`) and checkpoint name
- turn progress (`current/max`)
- accumulated issue count
- plan file path
- current status such as `initializing`, `running turn N`, `completed`, or `failed`

Raw harness stdout and stderr are not streamed live. They are captured in the run artifacts.

## Optional `--effort`

Pass `--effort <level>` to request a specific reasoning effort from the underlying harness. When omitted, `aflow` does not add any effort-specific flag.

| Harness | Without `--effort` | With `--effort high` |
|---------|-------------------|---------------------|
| codex | `--model gpt-5.4` | `--model gpt-5.4 -c model_reasoning_effort='"high"'` |
| pi | `--model sonnet` | `--models sonnet:high` |
| claude | no extra flag | `--effort high` |
| opencode | no extra flag | ignored, warning emitted |
| gemini | no extra flag | ignored, warning emitted |

Effort values are passed through as-is. Validation is left to the harness CLI. The `opencode` and `gemini` harnesses do not support effort tuning; when `--effort` is passed for those harnesses, `aflow` prints a single warning to stderr and continues without an effort flag.

## Non-interactive and Full-Permission Mode

Each harness uses a documented headless mode with full permissions:

- `opencode` uses `opencode run` with the prefixed prompt and `--dir` for the repo root
- `gemini` uses `--prompt` (not positional query), `--approval-mode yolo`, and `--sandbox=false`
- `codex` uses `exec` with `--dangerously-bypass-approvals-and-sandbox`
- `claude` uses `-p` with `--permission-mode bypassPermissions` and `--dangerously-skip-permissions`
- `pi` uses `--print` with a `--tools` list

## Limits

- max turns: `15` via `--max-turns`
- stagnation limit: `5` completed turns with no checkpoint-progress change via `--stagnation-limit`
- retained runs: `20` via `--keep-runs`

## Run Logs

Run data lives under `.aflow/runs/<run-id>/`.

Each run stores:

- prompts
- argv
- stdout and stderr
- per-turn result metadata
- live status state such as `run_started_at`, `active_turn`, `issues_accumulated`, and `status_message`

Older run directories are pruned automatically.

## Shipped Skills

This repo still ships a few skills, but for the current `aflow` workflow the ones worth documenting are:

- `ralf-handoff-plan`: writes strict checkpoint handoff plans that `aflow` can execute from disk
- `ralf-review-squash`: reviews a completed handoff and either squashes it or produces a focused fix plan

Those skills live under `skills/` and are repo assets that support the tool workflow. They are not required to run the package itself.

## Repository Layout

From the perspective of a Python tool package, the important paths are:

- `aflow/`: the Python package
- `pyproject.toml`: package metadata, dependencies, and console-script entrypoint
- `skills/`: optional workflow assets shipped alongside the tool
- `plans/`: saved working plans and handoff artifacts

The source entrypoint should not be a root-level executable named `aflow`, because that path is already occupied by the `aflow/` package directory. For source checkouts, `python3 -m aflow` is the clean package-native entrypoint. For installed use, the console script from `pyproject.toml` is the real entrypoint.

## Troubleshooting

- If the harness exits non-zero, `aflow` stops and prints the run log directory.
- If the plan is inconsistent, for example a checkpoint heading is marked complete while a step is still unchecked, `aflow` stops before continuing.
- If a run stalls, inspect the saved data under `.aflow/runs/<run-id>/`.
