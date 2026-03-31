---
name: execute-aflow-plan
description: "Lightweight execution reinforcement for an existing AFlow plan. Use when an agent must resume from the first unchecked checkpoint and keep the plan synchronized with verified progress."
---

#  Execute AFlow Plan

Use this skill only to execute an existing aflow plan autonomously. Treat `aflow` as the canonical spelling. This skill is intentionally lightweight, the plan itself should already carry the detailed execution contract.

The plan file is the source of truth. Do not rely on chat memory when the plan, repository state, test output, or git history disagree.

## Core Rules

- Execute one checkpoint at a time in order.
- Start at the first unchecked checkpoint unless the prompt explicitly says otherwise.
- Re-read the plan from disk before each checkpoint and again after verification.
- Treat the on-disk plan as the source of truth for progress, commit boundaries, and completion.
- Do not mark a step or checkpoint complete before required verification passes.
- Do not claim completion while verified work is still uncommitted.
- Stop and escalate when the plan is ambiguous, contradictory, unsafe, or buried under unrelated dirty changes.

## Required Inputs

Following plan paths should be provided by the prompt;

ORIGINAL_PLAN: This is the original implementation plan.
ACTIVE_PLAN: This maybe same as the original plan file, or could be a transient follow-up plan focused on fixing of review findings.

Before acting, identify:

- the active plan file
- the first incomplete checkpoint (`### [ ] Checkpoint ...`)
- any `Git Tracking`, `Dependencies`, `Verification`, `Done When`, or `Stop and Escalate If` instructions attached to that checkpoint

If the prompt already names a concrete plan file, use it. If not, discover the single active original plan under `plans/in-progress/` when that is unambiguous. If the plan file is missing, multiple candidate plans exist, or the next checkpoint cannot be identified safely, stop and ask for clarification.

## Execution Loop

- Read the active checkpoint fully before editing code.
- Implement only that checkpoint's scope.
- Run the exact verification commands from the plan.
- If verification passes, update the plan state and create the required commit.
- Only then move on to the next unchecked checkpoint.

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

- the next checkpoint is unclear
- the plan conflicts with the repository's actual structure
- required files, commands, or dependencies are missing
- the worktree contains unrelated dirty changes that make the checkpoint or commit ambiguous
- the plan requires a destructive git action you were not explicitly authorized to perform
- verification still fails after reasonable diagnosis and the failure suggests the plan is wrong or incomplete
