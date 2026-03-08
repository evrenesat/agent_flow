---
name: ralf-handoff-plan
description: "Create a strict RALF checkpoint handoff plan for coding work that will be implemented by another model, tool, or later session. Use when the user explicitly wants the RALF pattern or a checkpoint-based handoff plan, and persist the finished plan under the current project's plans directory."
---

# RALF Handoff Plan

Use this skill only for RALF-style planning.

## Behavior

- Ask targeted questions about scope, constraints, dependencies, tradeoffs, and acceptance criteria.
- Confirm risky assumptions before locking the final plan.
- Keep questions concise and sequenced.
- Produce a strict checkpoint handoff plan, not a standard implementation plan.
- Treat `ralf` and `ralph` as equivalent spellings.
- If the user asks for planning but does not want RALF, do not use this skill.

## Core Rule

The plan must be decision complete. The implementer should not need to choose behavior, precedence, fallback, validation policy, or verification strategy on their own.

## Required Plan Sections

Every final RALF plan must include these sections in substance, even if headers are adapted slightly for readability:

1. `Summary`
2. `Done Means`
3. `Critical Invariants`
4. `Forbidden Implementations`
5. `Checkpoints`
6. `Behavioral Acceptance Tests`
7. `Plan-to-Verification Matrix`
8. `Assumptions And Defaults`

Do not omit any of them when they are needed to prevent implementation drift.

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
- Add `Critical Invariants` for rules that must hold across the entire implementation.
- Add `Forbidden Implementations` for shortcuts the implementer might otherwise take.
- Add `Behavioral Acceptance Tests` as observable outcomes, not just test commands.
- Add a `Plan-to-Verification Matrix` that maps each important requirement to one concrete verification method.

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

## Critical Invariants Guidance

Use `Critical Invariants` for statements that must remain true across all checkpoints.

Examples:

- No runtime path may be hardcoded outside config.
- The same canonical input must be reused across requests for cacheability.
- A deprecated path may not remain active after migration.

Each invariant must be:

- concrete
- testable
- important enough that violating it would materially change the implementation

## Forbidden Implementations Guidance

Use `Forbidden Implementations` to name likely shortcuts explicitly.

Examples:

- Do not silently fall back to a local absolute path.
- Do not keep both old and new config sources live.
- Do not describe future-state docs as implemented behavior before code reaches parity.

If a shortcut is plausible and harmful, name it explicitly.

## Behavioral Acceptance Tests Guidance

Behavioral acceptance tests must describe observable outcomes.

Examples:

- "Given `start_when_ready`, inference begins after grid writing completes while summary generation is still running."
- "Given the same run, every summary request reuses the exact same transcript body."

Do not rely only on unit-test commands. The plan must state what a passing implementation does.

## Plan-to-Verification Matrix Guidance

Every important requirement must map to at least one concrete verification method.

Allowed verification types:

- exact test command
- exact grep or search command
- exact file existence or symlink check
- exact metadata assertion
- exact smoke command

Do not leave major requirements without verification coverage.

## Docs Parity Rule

Do not let the plan describe documentation changes as reflecting implemented behavior unless the corresponding checkpoint explicitly brings the code to that state in the same handoff.

If docs intentionally describe future state, the plan must say so explicitly and explain why.

## Clarification Standard

Before finalizing the plan, gather the same requirement clarity you would for any strong implementation plan:

- Ask targeted questions about scope, constraints, dependencies, tradeoffs, and acceptance criteria.
- Confirm risky assumptions before locking the final plan.
- Prefer explicit defaults over leaving choices open.

## Plan File Persistence

For every plan generated with this skill, persist the final RALF plan to disk.

Rules:

1. Save under `plans/` in the current project's root.
2. Use a descriptive markdown filename that makes the handoff purpose obvious.
3. Avoid overwriting existing files.
4. Exception: allow overwrite only when the target file was created by the assistant in the same session.
5. If a same-name file already exists from a prior session, create a new variant name such as `-v2`, `-v3`, or a date suffix.
