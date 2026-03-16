# agent_flow

Generic home for shareable agent workflow assets and the source of truth for custom items shared across coding agents.

## Layout

- `skills/`: Versioned custom skills that can be linked into agent runtimes.
- `commands/`: Placeholder for future reusable command wrappers.
- `scripts/`: Generic helper scripts, including the `ralf` runner.

## Skills

### `ralf-handoff-plan`
Creates a strict RALF checkpoint handoff plan for coding work that will be implemented by another agent or session. Use this to pause work safely and leave precise instructions on what remains to be done, including verification commands and git tracking.

### `ralf-review-squash`
Reviews sub-agent commits after a RALF handoff. It compares the new batch of commits against the plan's expected outcome, and either creates a follow-up fix plan or squashes the entire handoff history into a single clean commit.

### `ralph-execute`
Guides the agent through executing an existing RALF or Ralph plan. The agent resumes from the first unchecked checkpoint, keeps the plan file's checkboxes in sync with verified progress, and creates git commits at the required checkpoint or final-plan boundaries.

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

## Source Of Truth

This repository is the source of truth for these skills and scripts. Live paths in local agent runtimes point back here via symlinks.

## Live Links

Sample symlink targets on local developer machines:
- `/Users/evren/.codex/skills/ralf-handoff-plan`
- `/Users/evren/bin/ralf`
