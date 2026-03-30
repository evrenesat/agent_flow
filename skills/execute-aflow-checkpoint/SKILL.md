---
name: execute-aflow-checkpoint
description: "Checkpoint-scoped execution for an existing AFlow plan. Use when an agent should implement exactly one checkpoint, verify it, create the checkpoint commit, and then stop instead of continuing into the next checkpoint."
---

# Execute AFlow Checkpoint

Use this skill only for checkpoint-scoped aflow execution. Treat `aflow` as the canonical spelling. This skill is intentionally lightweight, and the plan itself should already carry the detailed checkpoint contract.

The plan file is the source of truth. Do not rely on chat memory when the plan, repository state, test output, or git history disagree.

## Core Rules

- Execute exactly one checkpoint per invocation.
- Start at the first unchecked checkpoint unless the prompt explicitly names a different checkpoint.
- Re-read the plan from disk before acting on the checkpoint and again after verification.
- Treat the on-disk plan as the source of truth for checkpoint scope, verification, and commit boundaries.
- Do not mark a checkpoint complete before required verification passes.
- Do not claim completion while verified checkpoint work is still uncommitted.
- Stop after the target checkpoint is implemented and verified, even if the original plan still has more unchecked checkpoints.
- Stop and escalate when the plan is ambiguous, contradictory, unsafe, or buried under unrelated dirty changes.

## Required Inputs

Before acting, identify:

- the active plan file
- the checkpoint that will be implemented
- any `Git Tracking`, `Dependencies`, `Verification`, `Done When`, or `Stop and Escalate If` instructions attached to that checkpoint

If the prompt already names a concrete plan file, use it. If not, discover the single active original plan under `plans/in-progress/` when that is unambiguous. If the plan file is missing, multiple candidate plans exist, or the checkpoint cannot be identified safely, stop and ask for clarification.

## Execution Loop

- Read the target checkpoint fully before editing code.
- Implement only that checkpoint's scope.
- Run the exact verification commands from the plan.
- If verification passes, update the plan state and create the required checkpoint commit.
- Stop there, do not move on to the next unchecked checkpoint.

Do not use this skill to invent a second execution spec. The plan should already define the checkpoint details, verification, and commit policy.

## Git Workflow

- Follow the plan's commit policy.
- Before a commit, check `git status --short` and `git diff --stat`.
- Do not leave verified checkpoint work uncommitted.
- Do not rewrite history unless the plan explicitly asks for it.

## Verification Standard

- Run the exact required verification commands for the active checkpoint.
- Treat the checkpoint as incomplete until those commands succeed.
- Use failing output as feedback for the next iteration.
- Do not replace required checks with weaker smoke tests.
- If the plan names observable acceptance criteria in addition to commands, confirm both the commands and the behavior.

## Stop And Escalate If

- the checkpoint to implement is unclear
- the plan conflicts with the repository's actual structure
- required files, commands, or dependencies are missing
- the worktree contains unrelated dirty changes that make the checkpoint or commit ambiguous
- the plan requires a destructive git action you were not explicitly authorized to perform
- verification still fails after reasonable diagnosis and the failure suggests the plan is wrong or incomplete
