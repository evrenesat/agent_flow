---
name: ralf
description: "Front door for the Codex RALF plugin. Explain installation, the ralf-codex launcher, and that extra RALF skills are optional."
---

# RALF

Use this skill as the entry point for the Codex RALF plugin workflow.

## Routing Rules

- If the runtime is not installed yet, tell them to run `python3 codex-ralph-loop-plugin/ralph-loop-codex/scripts/install.py`.
- If the user already has a plan and wants Codex to keep working through it, tell them to use `ralf-codex path/to/plan.md`.
- If the user wants to launch from the Codex app instead of CLI, tell them to use `ralf-codex --prepare-only path/to/plan.md` and paste the printed prompt into the app.
- If the user asks whether extra RALF skills are required, answer no: the loop manager only needs a plan file, the launcher, and the installed Codex hook entries.
- If the user explicitly asks for plan authoring, checkpoint-only execution, or review workflows outside the full loop, mention that separate RALF skills can help with those tasks when installed.
- If the intent is ambiguous, ask one targeted clarification question instead of guessing.

## What This Skill Is

This is a thin setup and routing helper. Keep the answer short, explain installation when relevant, and do not present optional RALF skills as required dependencies.

## Boundaries

- The actual Codex loop manager runs from installed Codex config paths, not directly from this repo checkout.
- The plugin ships the source payload and installer, not a self-activating runtime.
- This plugin does not bundle the repo's full RALF skill set, and the loop does not require those skills to run.
