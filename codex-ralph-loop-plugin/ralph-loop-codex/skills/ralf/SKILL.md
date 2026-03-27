---
name: ralf
description: "Front door for Codex-native RALF workflows. Route the user to planning, execution, checkpoint execution, checkpoint review, or final review."
---

# RALF

Use this skill as the entry point for the packaged RALF workflows in this plugin.

## Routing Rules

- If the user needs a new checkpoint handoff plan, route to `$ralf-handoff-plan`.
- If the user names an existing plan or asks to continue execution, route to `$ralf-execute`.
- If the user explicitly asks for one checkpoint only, route to `$ralf-cp-execute`.
- If the user asks to review checkpoint-sized work, route to `$ralf-cp-review`.
- If the user asks for the final accumulated review and squash path, route to `$ralf-review-squash`.
- If the intent is ambiguous, ask one targeted clarification question instead of guessing.

## What This Skill Is

This is a router, not a second implementation of the workflow. Keep the answer short, name the correct packaged skill, and then hand off.

## v1 Boundaries

This plugin does not promise:

- Claude-style stop-hook interception
- same-session prompt reinjection
- marketplace integration
- external runner scripts that shell out to the Codex CLI

Those behaviors belonged to the Claude version of Ralph Loop, not to this Codex-native plugin.
