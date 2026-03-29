# aflow

Self-contained plan checkpoint controller for `codex`, `pi`, and `claude`.

## Usage

```bash
aflow/aflow --harness codex --model gpt-5.4 path/to/plan.md [extra instructions ...]
aflow/aflow --harness codex --model gpt-5.4 --effort high path/to/plan.md
```

Or install as a system tool:

```bash
uv tool install /Users/evren/code/agent_flow
aflow --harness codex --model gpt-5.4 path/to/plan.md
```

Both `--harness` and `--model` are required. Supported harnesses: `codex`, `pi`, `claude`.

## Live Status Banner

While a harness turn is running, aflow shows a persistent Rich banner on stderr with:

- elapsed runtime
- harness, model, and effort level
- checkpoint progress (current/total) and checkpoint name
- turn progress (current/max)
- accumulated issue count
- plan file path
- current status (initializing, running turn N, completed, failed)

Raw harness stdout/stderr is not streamed to the terminal. It's captured to per-turn artifact files under the run directory.

## Optional `--effort`

Pass `--effort <level>` to request a specific reasoning effort from the underlying harness. When omitted, no effort flag is sent to the harness.

| Harness | Without `--effort` | With `--effort high` |
|---------|-------------------|---------------------|
| codex | `--model gpt-5.4` | `--model gpt-5.4 -c model_reasoning_effort='"high"'` |
| pi | `--model sonnet` | `--models sonnet:high` |
| claude | (no effort flag) | `--effort high` |

Effort values are passed through verbatim. Validation is left to the harness CLI.

## Limits

- max turns: `15` (`--max-turns`)
- stagnation limit: `5` completed turns with no checkpoint-progress change (`--stagnation-limit`)
- retained runs: `20` (`--keep-runs`)

## Run logs

Logs live under `.aflow/runs/<run-id>/`. Each run records prompts, argv, stdout/stderr, per-turn result metadata, and live-status state (run_started_at, active_turn, issues_accumulated, status_message). Older directories are pruned automatically.

## Troubleshooting

- If the harness exits non-zero, the controller stops and prints the run log directory.
- If the plan is inconsistent (e.g. checked checkpoint with unchecked steps), the controller reports it before continuing.
- Logs are in `.aflow/runs/<run-id>/`.
