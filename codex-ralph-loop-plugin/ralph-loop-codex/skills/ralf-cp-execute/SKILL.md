---
name: ralf-cp-execute
description: "Execute one checkpoint from an existing RALF plan. Use when an agent must implement the named checkpoint, or the first unchecked checkpoint by default, treat the plan as read-only, verify the result, create the checkpoint commit, and stop without advancing the plan."
---

# RALF CP Execute

Use this skill only to execute one checkpoint from an existing RALF plan. Treat `ralf` and `ralph` as equivalent spellings.

The plan file is the source of truth for scope and verification, but it is read-only in this workflow.

## Core Rules

- Execute one checkpoint only.
- If the user names a checkpoint, use that checkpoint. Otherwise execute the first unchecked checkpoint from the active plan.
- Treat the plan as read-only. Do not edit step checkboxes, checkpoint headings, `Git Tracking`, or any other plan content.
- Work only on the current checkpoint unless the plan explicitly says the checkpoint itself spans multiple tightly related edits.
- Stop after the current checkpoint passes verification and its implementation commit is created.
- Do not advance to the next checkpoint, even if nothing appears blocked.
- Stop and escalate when the plan is ambiguous, contradictory, or unsafe to continue.

## Required Inputs

Before acting, identify:

- the active plan file
- the named checkpoint, or the first incomplete checkpoint (`### [ ] Checkpoint ...`) when no checkpoint was named
- any `Git Tracking`, `Dependencies`, `Verification`, `Done When`, or `Stop and Escalate If` instructions attached to that checkpoint

If the plan file is missing, multiple candidate plans exist, or the checkpoint to execute cannot be identified safely, stop and ask for clarification.

## Execution Loop

Follow this loop strictly:

1. Read the active plan file and identify the target checkpoint.
2. Read that checkpoint fully before editing code. Also read any earlier checkpoints it depends on.
3. Run the checkpoint's context-bootstrapping commands and inspect the repo state.
4. If unrelated dirty changes make the checkpoint ambiguous, stop and escalate instead of guessing.
5. Implement only the scope assigned to that checkpoint.
6. Run the exact verification commands from the checkpoint. Add scoped checks only when needed to diagnose a failure, not as a substitute for required verification.
7. If verification fails, diagnose the failure and continue iterating on that same checkpoint.
8. If verification passes, create the required implementation commit immediately.
9. Stop after that checkpoint commit. Do not continue into the next checkpoint.

## Plan Handling Rules

- Treat `plans/**` as read-only input in this workflow.
- Do not mark completed checklist items inside the plan.
- Do not change the checkpoint heading from `### [ ]` to `### [x]`.
- Do not update `Review Log`, `Last Reviewed HEAD`, or any other plan metadata.
- Do not stage or commit plan-file changes as part of CP execution.

## Git Workflow

Default behavior is one implementation commit for the completed checkpoint.

- Before the first edit, inspect `git status --short` and `git branch --show-current`.
- If the active checkpoint or plan specifies a commit message, use that exact message.
- Otherwise derive a concrete message from the checkpoint number and name, for example `Checkpoint 2: wire CLI verification`.
- Commit immediately after the checkpoint passes verification.
- Do not batch multiple checkpoints into one commit.
- A passed checkpoint with no commit is still incomplete.
- Do not amend, squash, or rewrite history unless the user or plan explicitly instructs you to do so.

Before every commit, review what will be included:

- `git status --short`
- `git diff --stat`

If unrelated changes are present and you cannot separate them safely, stop and escalate.

Before any completion response, run `git status --short` again and confirm there are no leftover changes from the checkpoint you are about to report as done.

## Verification Standard

- Run the exact required verification commands for the active checkpoint.
- Treat the checkpoint as incomplete until those commands succeed.
- Use failing output as feedback for the next iteration.
- Do not replace a required test, lint, or build command with a weaker smoke check.
- If the plan names observable acceptance criteria in addition to commands, confirm both the commands and the behavior.

## Stop And Escalate If

- the target checkpoint is unclear
- the plan conflicts with the repository's actual structure
- required files, commands, or dependencies are missing
- the worktree contains unrelated dirty changes that make the checkpoint or commit ambiguous
- the plan requires a destructive git action you were not explicitly authorized to perform
- verification still fails after reasonable diagnosis and the failure suggests the plan is wrong or incomplete

## Completion Contract

The checkpoint is done only when all of the following are true:

- the checkpoint's implementation work is present in the repository
- the checkpoint's required verification passes
- the completed work is committed according to the checkpoint or plan git policy
- the plan file remains unchanged by the CP executor

The full plan is not complete just because this checkpoint finished. Plan advancement belongs to the reviewer in CP mode.
