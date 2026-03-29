# ralph

Self-contained plan checkpoint controller for `codex`, `pi`, and `claude`.

## Usage

```bash
ralph/ralph --harness codex --model gpt-5.4 path/to/plan.md [extra instructions ...]
ralph/ralph --harness codex --model gpt-5.4 --effort high path/to/plan.md
```

Both `--harness` and `--model` are required. Supported harnesses: `codex`, `pi`, `claude`.

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

Logs live under `.ralf/runs/<run-id>/`. Each run records prompts, argv, stdout/stderr, and per-turn result metadata. Older directories are pruned automatically.

## Troubleshooting

- If the harness exits non-zero, the controller stops and prints the run log directory.
- If the plan is inconsistent (e.g. checked checkpoint with unchecked steps), the controller reports it before continuing.
- Logs are in `.ralf/runs/<run-id>/`.
