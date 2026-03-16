---
name: ralph-execute
description: Execute an existing RALF or Ralph checkpoint plan in a long-running loop. Use when an agent must continue a plan file, keep its checkboxes synchronized with actual progress, run verification after each checkpoint, and create git commits at checkpoint or final-plan boundaries.
---

# Ralph Execute

Use this skill only to execute an existing RALF or Ralph plan. Treat `ralf` and `ralph` as equivalent spellings.

The plan file is the source of truth. Do not rely on chat memory when the plan, repository state, test output, or git history disagree.

## Core Rules

- Execute the first incomplete checkpoint, not an arbitrary task that looks related.
- Work one checkpoint at a time unless the plan explicitly instructs otherwise.
- Keep the plan file synchronized with actual progress while you work.
- Do not mark any step or checkpoint complete before its required verification passes.
- The moment a step is truly done, reflect that state in the plan file. Do not leave finished step checkboxes unchecked "for later".
- A checkpoint that passed verification is not complete until its plan updates are saved and its required commit has been created.
- Never leave a finished checkpoint, especially the last checkpoint in the plan, sitting as uncommitted worktree changes.
- Stop and escalate when the plan is ambiguous, contradictory, or unsafe to continue.

## Required Inputs

Before acting, identify:

- the active plan file
- the first incomplete checkpoint (`### [ ] Checkpoint ...`)
- any `Git Tracking`, `Dependencies`, `Verification`, `Done When`, or `Stop and Escalate If` instructions attached to that checkpoint

If the plan file is missing, multiple candidate plans exist, or the next checkpoint cannot be identified safely, stop and ask for clarification.

## Execution Loop

Follow this loop strictly:

1. Read the plan file and find the first unchecked checkpoint heading.
2. Read that checkpoint fully before editing code. Also read any earlier checkpoints it depends on.
3. Run the checkpoint's context-bootstrapping commands and inspect the repo state.
4. If unrelated dirty changes make the checkpoint ambiguous, stop and escalate instead of guessing.
5. Implement only the scope assigned to that checkpoint.
6. Run the exact verification commands from the checkpoint. Add scoped checks only when needed to diagnose a failure, not as a substitute for required verification.
7. If verification fails, diagnose the failure, keep the checkpoint unchecked, and continue iterating on that same checkpoint.
8. If verification passes, stop all other work, update the plan file immediately, create the required commit immediately, and only then move to the next checkpoint.
9. When no unchecked checkpoints remain, emit the required completion promise if one was provided. Otherwise report that the plan is complete.

Do not reorder step 8. "I'll commit after one more small cleanup", "I'll update the checkboxes at the end", and "I'll report completion first" are all execution mistakes.

## Plan Synchronization Rules

Treat plan updates as part of the implementation, not as an optional summary at the end.

- Mark completed checklist items inside the active checkpoint from `- [ ]` to `- [x]` when the work they describe is actually complete.
- If you complete a listed substep and leave its checkbox unchecked, treat that as incorrect state and fix it before continuing.
- If the checkpoint uses numbered or plain-language substeps instead of checklist bullets, do not rewrite the structure just to add cosmetic checkboxes.
- Change the checkpoint heading from `### [ ]` to `### [x]` only after all checkpoint-specific verification passes.
- If a verification run fails after you already checked an item by mistake, revert that checkmark before continuing.
- Keep edits tight. Do not rewrite unrelated plan sections, renumber checkpoints, or restyle the whole file.
- If the plan includes `Git Tracking` fields that must be populated during execution, update only the fields the plan tells you to maintain.

At minimum, every fully completed checkpoint must leave the plan file showing the checkpoint heading as checked. If the checkpoint includes step-level checkboxes, check those too.

Before you move on from a checkpoint or claim the plan is done, reread the active checkpoint in the plan file and confirm its completed items are visibly checked.

## Git Workflow

Default behavior is one commit per completed checkpoint.

- Before the first edit, inspect `git status --short` and `git branch --show-current`.
- If the active checkpoint or plan specifies a commit message, use that exact message.
- Otherwise derive a concrete message from the checkpoint number and name, for example `Checkpoint 2: wire CLI verification`.
- Include the plan file updates in the same commit as the code, tests, and docs completed for that checkpoint.
- Commit immediately after the checkpoint passes verification. Do not batch multiple completed checkpoints into one commit unless the user or plan explicitly requires a single final commit for the whole plan.
- If the user or plan explicitly requires one final commit for the whole plan, still keep the plan file synchronized between checkpoints, then create exactly one commit after the final checkpoint passes.
- A passed checkpoint with no commit is still incomplete. Do not treat "changes are ready to commit" as equivalent to "checkpoint finished".
- The final checkpoint follows the same rule. If the plan is complete but the last checkpoint's changes are still uncommitted, the task is not finished and you must commit before responding.
- Do not amend, squash, or rewrite history unless the user or plan explicitly instructs you to do so.

Before every commit, review what will be included:

- `git status --short`
- `git diff --stat`

If unrelated changes are present and you cannot separate them safely, stop and escalate.

Before any completion response, run `git status --short` again and confirm there are no leftover changes from the checkpoint you are about to report as done.

## Verification Standard

- Run the exact required verification commands for the active checkpoint.
- Treat a checkpoint as incomplete until those commands succeed.
- Use failing output as feedback for the next iteration.
- Do not replace a required test, lint, or build command with a weaker smoke check.
- If the plan names observable acceptance criteria in addition to commands, confirm both the commands and the behavior.

## Stop And Escalate If

- the next checkpoint is unclear
- the plan conflicts with the repository's actual structure
- required files, commands, or dependencies are missing
- the worktree contains unrelated dirty changes that make the checkpoint or commit ambiguous
- the plan requires a destructive git action you were not explicitly authorized to perform
- verification still fails after reasonable diagnosis and the failure suggests the plan is wrong or incomplete

## Completion Contract

A checkpoint is done only when all of the following are true:

- the checkpoint's implementation work is present in the repository
- the checkpoint's required verification passes
- the checkpoint's relevant plan items are checked in the plan file
- the completed work is committed according to the checkpoint or plan git policy

The full plan is done only when every checkpoint is checked and the required final commit policy has been satisfied.

If you are about to say the work is complete, this must already be true in git and in the plan file, not merely true in your intention.
