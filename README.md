# agent_flow

Generic home for shareable agent workflow assets and the source of truth for custom items shared across coding agents.

## Layout

- `skills/`: Versioned custom skills that can be linked into agent runtimes.
- `commands/`: Placeholder for future reusable command wrappers.
- `scripts/`: Generic helper scripts, including the current `ralf` runner.

## Current Assets

- `skills/ralf-handoff-plan/`
- `scripts/ralf`

## Source Of Truth

This repo owns the custom `ralf-handoff-plan` skill and the `ralf` helper script. Live paths in local agent runtimes point back here via symlinks.

## Live Links

- `/Users/evren/.codex/skills/ralf-handoff-plan`
- `/Users/evren/bin/ralf`
