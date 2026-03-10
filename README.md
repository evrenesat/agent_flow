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
Guides the agent through an autonomous, test-driven development loop. The agent relies on file state, verifies its work with tests, and auto-corrects based on test failures instead of waiting for conversational feedback.

## Scripts

### `ralf`
A bash runner that executes an autonomous loop over a plan file (default `IMPLEMENTATION_PLAN.md`). It reads untouched checkpoints (`[ ]`), performs the needed steps, verifies them, marks them done (`[x]`), and commits.

**Usage:**
```bash
ralf [path/to/plan.md]
ralf --dry-run [path/to/plan.md]
```

## Source Of Truth

This repository is the source of truth for these skills and scripts. Live paths in local agent runtimes point back here via symlinks.

## Live Links

Sample symlink targets on local developer machines:
- `/Users/evren/.codex/skills/ralf-handoff-plan`
- `/Users/evren/bin/ralf`
