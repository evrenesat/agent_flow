---
name: aflow-plan
description: "Create a strict AFlow checkpoint handoff plan for coding work that will be implemented by another model, tool, or later session. Use when the user explicitly wants the aflow pattern or a checkpoint-based handoff plan."
---

# AFlow Handoff Plan

Use this skill only for aflow-style planning. It is designed to be installed as a static skill and driven by prompt context from the workflow engine or harness.

## Behavior

- Treat prompt-supplied concrete plan context as authoritative when it is present.
- If the prompt does not give a concrete path and there is exactly one safe target in the repo, use that narrow fallback. Otherwise stop and ask.
- Ask targeted questions about scope, constraints, dependencies, tradeoffs, and acceptance criteria.
- Confirm risky assumptions before locking the final plan.
- Keep questions concise and sequenced.
- Produce a strict checkpoint handoff plan, not a standard implementation plan.
- Keep the plan execution-model agnostic. The plan must work for checkpoint-scoped CP execution and for autonomous non-CP execution without declaring a mode field.
- Make the plan durable under crash, rerun, and later-session handoff. A fresh agent or thread must be able to resume from disk without relying on prior chat context.
- Treat the original handoff plan as the long-lived ledger under `plans/in-progress/` until the handoff is complete.
- Treat reviewer-created fix plans as temporary overlays for rejected work, not replacements for the original plan's long-lived state.
- Make the final plan self-sufficient. It should not rely on a separate heavy executor skill to supply missing workflow details later.
- Treat `aflow` as the canonical spelling.
- If the user asks for planning but does not want aflow, do not use this skill.

## Core Rule

The plan must be decision complete. The implementer should not need to choose behavior, precedence, fallback, validation policy, or verification strategy on their own.

## Required Plan Sections

Every final aflow plan must include these sections in substance, even if headers are adapted slightly for readability:

1. `Summary`
2. `Git Tracking`
3. `Done Means`
4. `Critical Invariants`
5. `Forbidden Implementations`
6. `Checkpoints`
7. `Behavioral Acceptance Tests`
8. `Plan-to-Verification Matrix`
9. `Assumptions And Defaults`

Do not omit any of them when they are needed to prevent implementation drift.

## aflow Output Contract

Produce a strict checkpoint plan for cross-model or later-session handoff.

Requirements:

- Use Markdown task lists (`- [ ]`) for checkpoints and internal steps.
- Define done at project and checkpoint level.
- Keep checkpoints atomic and independently verifiable.
- Write each checkpoint so a fresh agent or thread can resume from disk with no hidden chat context.
- Include explicit context bootstrapping commands before edits.
- List allowed files and forbidden files or systems per checkpoint.
- Include anti-shortcut constraints and preserved behaviors.
- Include scoped and non-regression verification commands per checkpoint.
- Require a git commit boundary per checkpoint.
- Define a commit message format per checkpoint that starts with the checkpoint/version prefix `cpN vNN` on the first line, followed by the rest of the commit message body. Every checkpoint commit, including the first commit for that checkpoint, must use an explicit version starting at `v01`. Example first line: `cp1 v01`
- Make checkpoint state durable enough for restart. Step checkboxes should reflect meaningful progress inside the checkpoint and help a later rerun or reviewer understand what is already done.
- Do not encode whether plan checkboxes are updated by the executor or the reviewer. The consuming execution or review skill owns that policy.
- Include explicit stop-and-escalate conditions.
- Require an explicit documentation impact review that updates relevant existing docs when the change warrants it.
- Require a `Git Tracking` section that captures the current branch, the immutable pre-handoff base commit, an optional last-reviewed commit field, and a review log for later review passes. Review state should be understandable from checkpoint/version labels even when exact reviewed SHAs are omitted or become stale.
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
- If this is Checkpoint 1, capture the git tracking values before any edits:
- `git branch --show-current`
- `git rev-parse HEAD`

**Scope & Blast Radius:**

- May create/modify: [files]
- Must not touch: [files/systems, including `plans/**` except read-only access to the assigned plan file and the minimal progress-tracking edits performed by the consuming execution or review workflow]
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
- A git commit is created with message starting with:
  ```text
  cpN vNN
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp4 v02`, `cp4 v03`, and so on.

**Stop and Escalate If:**

- <explicit failure mode>
```

Use this `Git Tracking` skeleton in the final plan:

```markdown
## Git Tracking

- Plan Branch: `<git branch --show-current>`
- Pre-Handoff Base HEAD: `<git rev-parse HEAD>`
- Last Reviewed HEAD: `none`  <!-- optional support field, not the primary review tracker -->
- Review Log:
  - None yet.
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

## Git Tracking Rule

The `Git Tracking` section is the source of truth for later review workflows.

Requirements:

- Capture `Plan Branch` and `Pre-Handoff Base HEAD` before any implementation checkpoint begins.
- Use the full SHA from `git rev-parse HEAD`, not a short SHA.
- Treat `Pre-Handoff Base HEAD` as immutable for the life of the handoff, even after squashes.
- Initialize `Last Reviewed HEAD` to `none`.
- Treat checkpoint/version commit prefixes such as `cp4 v01`, `cp4 v02`, and `cp5 v01` as the primary human-readable tracking mechanism for review progress.
- Use `Last Reviewed HEAD` only as optional support metadata. Do not make the handoff process depend on updating that field after every review.
- Require later review workflows to append to `Review Log` after each review pass. Review-log entries should name the checkpoint/version batch that was reviewed, and may include exact SHAs when useful.
- If a later plan revision changes scope, preserve the original base SHA unless the user explicitly restarts the handoff from a new baseline.

## Documentation Coverage Rule

Every final aflow plan must evaluate whether the change warrants documentation updates and, when it does, assign those updates to the relevant checkpoint(s).

Required guidance:

- Update `ARCHITECTURE.md` where the implemented change affects architecture, system boundaries, data flow, component responsibilities, or integration contracts already described there.
- Update `DEVLOG.md` where the project uses it to record implementation decisions, notable behavior changes, migrations, or operational follow-ups caused by the work.
- Update `AGENTS.md` only in affected subdirectories when the change alters coding-agent instructions, local workflow constraints, generated artifacts, or directory-specific implementation rules for that subdirectory.
- Do not modify the root `AGENTS.md` as part of the implementation handoff.
- Update relevant existing user-facing documentation when the change modifies user-visible behavior, supported workflows, flags, configuration, setup, or troubleshooting guidance.
- Treat README updates as opt-in to existing coverage only: the plan must instruct implementers to search the root `README.md` and any affected subdirectory `README.md` files for an already relevant section, and update that section if it exists.
- Do not add a new README section, a new README file, or new feature mention in README solely to document the change when no relevant existing section already covers that area.
- If no relevant README section exists, the plan must leave README untouched and direct any necessary documentation updates to a more appropriate existing doc instead, if one exists.
- Documentation updates must be scoped to the behavior actually implemented in the same handoff, not speculative future state.
- When the plan concludes that a documentation file does not need changes, it should say why, so the implementer does not have to guess.

## Clarification Standard

Before finalizing the plan, gather the same requirement clarity you would for any strong implementation plan:

- Ask targeted questions about scope, constraints, dependencies, tradeoffs, and acceptance criteria.
- Confirm risky assumptions before locking the final plan.
- Prefer explicit defaults over leaving choices open.

## Plan File Persistence

For every plan generated with this skill, persist the final aflow plan to disk.

Rules:

1. Ensure `plans/in-progress/` exists in the current project's root before writing the active plan. Create it if it does not exist.
2. Save the active original handoff plan under `plans/in-progress/`. Do not leave active plans directly under `plans/`.
3. Use a descriptive markdown filename that makes the handoff purpose obvious.
4. Avoid overwriting existing files.
5. Exception: allow overwrite only when the target file was created by the assistant in the same session.
6. If a same-name file already exists from a prior session, create a new variant name such as `-v2`, `-v3`, or a date suffix.
7. Architect or reviewer workflows must delete superseded fix plans whenever a newer fix plan is created, and must delete any remaining fix plans once a checkpoint is accepted so that only the original handoff plan remains in `plans/in-progress/` before the next normal checkpoint starts.
8. When every checkpoint is complete, move the original handoff plan to `plans/done/`. Create `plans/done/` if it does not exist, and include the moved plan path in the final response.
