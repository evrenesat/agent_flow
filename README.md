# agent_flow

Generic home for shareable agent workflow assets and the source of truth for custom items shared across coding agents.

## Layout

- `skills/`: Versioned custom skills that can be linked into agent runtimes.
- `commands/`: Placeholder for future reusable command wrappers.
- `aflow/`: Self-contained plan checkpoint controller for codex, pi, and claude.
- `codex-ralph-loop-plugin/`: Source for the Codex RALF plugin, hidden runtime payload, and install scripts.

## Skills

### `ralf-handoff-plan`
Creates a strict RALF checkpoint handoff plan for coding work that will be implemented by another agent or session. The plan stays execution-model agnostic, but it must be durable enough to resume from disk and support later review without relying on prior chat context.

### `ralf-execute`
Runs a RALF plan autonomously from the first unchecked checkpoint through completion. It is the non-CP executor: it uses a fresh context boundary for each checkpoint, keeps the plan file synchronized with verified progress, commits each completed checkpoint, and resumes from the first unchecked checkpoint after crashes or reruns.

### `ralf-cp-execute`
Implements one checkpoint from a RALF plan and stops. It is the CP executor: it treats the plan as read-only, works on the named checkpoint or the first unchecked checkpoint by default, creates the checkpoint commit, and leaves plan updates to the reviewer.

### `ralf-review-squash`
Reviews a completed autonomous RALF run. It is the non-CP review path: it checks the full accumulated implementation against the original plan, then either squashes the whole handoff into one final commit or creates a focused fix plan for the remaining failed checkpoints or behaviors.

### `ralf-cp-review`
Reviews one checkpoint-sized RALF batch. It is the CP review path: it checks the current checkpoint or focused fix pass, updates the original plan when the checkpoint is approved, and creates or replaces one focused fix plan when more work is needed.

## Scripts

### `ralf`
A bash runner for Gemini headless Ralph loops over a checkpoint plan file. It builds the planner prompt from the plan path, initializes Ralph state with the installed Gemini Ralph extension, then runs Gemini with that exact same prompt so later iterations do not trip the prompt-mismatch hook.

**Usage:**
```bash
ralf [--dry-run] [path/to/plan.md]
ralf [--dry-run] path/to/plan.md [extra instructions ...]
```

Everything after an explicit plan path is appended verbatim to the generated planner prompt. `--dry-run` prints both the Ralph setup command and the Gemini command without executing them.

By default the script expects Gemini Ralph's setup script at `~/.gemini/extensions/ralph/scripts/setup.sh`. Override that path with `RALPH_SETUP_SCRIPT` if your extension is installed elsewhere.

`scripts/ralf_offf.sh` is not the active runner.

### `aflow`

Plan checkpoint controller for codex, pi, and claude. Lives under `aflow/`. See `aflow/README.md` for usage, supported harnesses, optional `--effort`, limits, and live status banner details.

Install as a system tool with `uv tool install /Users/evren/code/agent_flow`.

## Codex Ralph Loop

The Codex Ralph source lives in this repo, but the live runtime belongs in Codex's real config locations.

- Source payload lives under `codex-ralph-loop-plugin/ralph-loop-codex/`.
- The hidden runtime payload lives under `codex-ralph-loop-plugin/ralph-loop-codex/.codex-runtime/`.
- The install script wires the runtime into `~/.codex/` and symlinks `ralf-codex` into a bin dir on your `PATH`.
- `ralph-loop.local.md` in the active working project is the durable Ralph state file for Codex runs.
- The loop continues only while the state file is active, the plan still has unchecked checkpoints, and the completion promise has not been emitted.

The loop manager itself does not require any of the repo skills. If you already have a plan file, the installed `ralf-codex` launcher plus the installed Codex hooks are enough.

The repo skills are optional helpers for adjacent tasks:

- `ralf-handoff-plan` when you want Codex to author a strict checkpoint plan
- `ralf-cp-execute` when you want a single-checkpoint workflow instead of the full loop
- `ralf-cp-review` and `ralf-review-squash` when you want review-specific guidance outside the loop manager

## Codex Runtime Install

Install the live Codex runtime from this repo with:

```bash
python3 codex-ralph-loop-plugin/ralph-loop-codex/scripts/install.py
```

That installer:

- enables `codex_hooks = true` in `~/.codex/config.toml`
- merges the Ralph hook entries into `~/.codex/hooks.json`
- symlinks the hidden runtime payload into `~/.codex/ralf-loop-codex`
- symlinks `ralf-codex` into `~/bin` by default

After installation, use:

```bash
ralf-codex path/to/plan.md
```

or:

```bash
ralf-codex --prepare-only path/to/plan.md
```

## Source Of Truth

This repository is the source of truth for these skills and scripts. Live paths in local agent runtimes point back here via symlinks.

## Live Links

Sample symlink targets on local developer machines:
- `/Users/evren/.codex/skills/ralf-handoff-plan`
- `/Users/evren/bin/ralf`
