# RALF for Codex

This plugin packages the RALF planning and execution workflows for Codex as skills, not as a Claude stop-hook loop.

## What this plugin does

RALF here means a checkpoint-oriented workflow for planning, autonomous execution, checkpoint-scoped execution, checkpoint review, and final review. The main idea is fresh context boundaries between checkpoints, with the plan on disk as the source of truth.

## Bundled skills

- `ralf`, the front door. Use this when you want the plugin to route you to the right RALF workflow.
- `ralf-handoff-plan`, create a strict checkpoint handoff plan.
- `ralf-execute`, run an existing plan from the first unchecked checkpoint through completion.
- `ralf-cp-execute`, execute one checkpoint only.
- `ralf-cp-review`, review one checkpoint-sized batch and update the original plan if it passes.
- `ralf-review-squash`, review a completed autonomous run and squash approved work at the end.

## When to use each skill

- Use `ralf` when you know you want RALF, but not which packaged skill to invoke.
- Use `ralf-handoff-plan` when you need a durable handoff plan for another agent or later session.
- Use `ralf-execute` when the plan already exists and you want the whole plan worked through in order.
- Use `ralf-cp-execute` when you want exactly one checkpoint implemented and committed.
- Use `ralf-cp-review` when you are reviewing one checkpoint batch and deciding whether it is ready to approve.
- Use `ralf-review-squash` when the whole autonomous handoff is done and you want final review plus squash logic.

## What changed from Claude

The older Ralph Loop plugin used Claude slash commands plus a stop hook. This Codex plugin does not.

- `/ralph-loop` conceptually maps to `ralf` or `ralf-execute` after a plan exists.
- `/cancel-ralph` has no direct plugin-state equivalent in v1, because execution is plan-driven rather than state-file driven.
- `/help` maps to this README and to the skill descriptions.

## v1 boundaries

This plugin does not promise:

- Claude-style stop-hook interception
- same-session prompt reinjection
- marketplace integration
- external runner scripts that shell out to the Codex CLI

That is intentional. The v1 goal is a portable Codex-native workflow, not a faithful reimplementation of Claude's session control model.

## Quick start

If you already have a plan:

```text
Use $ralf-execute to continue the first unchecked checkpoint in the plan.
```

If you need a new plan:

```text
Use $ralf-handoff-plan to create a strict checkpoint handoff plan.
```

If you only want one checkpoint:

```text
Use $ralf-cp-execute for the named checkpoint, or the first unchecked checkpoint by default.
```

## Notes

- This plugin is self-contained under `ralph-loop-codex`.
- It does not add marketplace metadata in this pass.
- It does not copy Claude hook files into the Codex plugin.
