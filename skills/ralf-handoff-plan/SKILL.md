---
name: ralf-handoff-plan
description: "Create a strict RALF checkpoint handoff plan for coding work that will be implemented by another model, tool, or later session. Use when the user explicitly wants the RALF pattern or a checkpoint-based handoff plan, and persist the finished plan under the current project's plans directory."
---

# RALF Handoff Plan

Use this skill only for RALF-style planning. Do not route between multiple plan types.

## Behavior

- Ask targeted questions about scope, constraints, dependencies, and acceptance criteria.
- Confirm risky assumptions before locking the final plan.
- Keep questions concise and sequenced.
- Produce a strict checkpoint handoff plan, not a standard implementation plan.
- Treat `ralf` and `ralph` as equivalent spellings.
- If the user asks for planning but does not want RALF, do not use this skill.

## RALF Output Contract

Produce a strict checkpoint plan for cross-model or later-session handoff.

Requirements:
- Use Markdown task lists (`- [ ]`) for checkpoints and internal steps.
- Define done at project and checkpoint level.
- Keep checkpoints atomic and independently verifiable.
- Include explicit context bootstrapping commands before edits.
- List allowed files and forbidden files or systems per checkpoint.
- Include anti-shortcut constraints and preserved behaviors.
- Include scoped and non-regression verification commands per checkpoint.
- Require a git commit boundary per checkpoint.
- Include explicit stop-and-escalate conditions.

Use this checkpoint skeleton:

```markdown
### [ ] Checkpoint N: <name>

**Goal:**
- <narrow checkpoint outcome>

**Context Bootstrapping:**
- Run these commands before editing:
- `<command>`
- `<command>`

**Scope & Blast Radius:**
- May create/modify: [files]
- Must not touch: [files/systems]
- Constraints: [anti-shortcuts + preserved behavior]

**Steps:**
- [ ] Step 1: ...
- [ ] Step 2: ...
- [ ] Step 3: ...

**Dependencies:**
- Depends on Checkpoint N-1.

**Verification:**
- Run scoped tests: `<exact command>`
- Run non-regression tests: `<exact command>`

**Done When:**
- Verification commands pass cleanly.
- <observable condition>
- A git commit is created with message: `...`

**Stop and Escalate If:**
- <explicit failure mode>
```

## Clarification Standard

Before finalizing the plan, gather the same requirement clarity you would for any strong implementation plan:
- Ask targeted questions about scope, constraints, dependencies, and acceptance criteria.
- Confirm risky assumptions before locking the final plan.
- Keep questions concise and sequenced.

## Plan File Persistence

For every plan generated with this skill, persist the final RALF plan to disk.

Rules:
1. Save under `plans/` in the current project's root.
2. Use a descriptive markdown filename that makes the handoff purpose obvious.
3. Avoid overwriting existing files.
4. Exception: allow overwrite only when the target file was created by the assistant in the same session.
5. If a same-name file already exists from a prior session, create a new variant name (for example with `-v2`, `-v3`, or a date suffix).
