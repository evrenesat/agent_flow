---
name: review-aflow-checkpoint
description: "Checkpoint-scoped review for AFlow plans. Use when an agent should review the latest checkpoint attempt, compare it against the original plan, and either approve that checkpoint or create a focused fix plan for that checkpoint only."
---

# Review AFlow Checkpoint

Use this skill only for checkpoint-scoped review of work produced under an aflow plan that includes `Git Tracking`. It is meant to be installed as a static skill and driven by prompt context from the workflow engine.

## Behavior

- Load the active original aflow plan before reviewing code or history.
- If the prompt already names the original plan, use that directly. Otherwise fall back to the repo's original-plan selection rules.
- Review one checkpoint at a time, not the whole accumulated handoff.
- Treat checkpoint/version commit prefixes such as `cp4 v01`, `cp4 v02`, and `cp5 v01` as the primary review target. Use exact SHAs as supporting evidence, not as the only way to understand state.
- If the latest checkpoint commit boundary is missing or ambiguous, review the current worktree state and say that the fallback was used.
- Treat files under `plans/` as architect or reviewer-owned artifacts. If an implementation commit modifies plan files unexpectedly, reject that work unless the user explicitly asked for plan-file commits from the implementer.
- Treat prompt-supplied concrete review context as authoritative when it is present. Use repo discovery only when the prompt leaves a target ambiguous.
- If the active original plan is already effectively complete, do not repurpose this skill for whole-plan review. Use the whole-plan review workflow instead.
- If the checkpoint looks correct, approve that checkpoint and advance the original plan's review state.
- If the checkpoint is not acceptable, do not approve it. Create a focused fix plan for the failed checkpoint or behaviors instead of a whole-plan redo.
- Treat `aflow` as the canonical spelling.

## Core Rule

The original plan file is the source of truth for long-lived review state. Fix plans are temporary overlays for rejected checkpoint work, not replacements for the original plan.

## Required Inputs

Before reviewing, identify the active original aflow plan under `plans/in-progress/`.

Selection rules:

1. If the user names a plan file, use it.
2. Otherwise search `plans/in-progress/` for original plan files containing `Pre-Handoff Base HEAD`.
3. Filter candidates to the current branch recorded in `Plan Branch`.
4. Ignore temporary fix plans when choosing the original long-lived plan.
5. If exactly one original plan remains, use it.
6. If multiple original plans remain, stop and ask the user which plan to use.

## Review Workflow

1. Read the original plan's `Git Tracking` section and checkpoint state.
2. Confirm the current branch matches `Plan Branch`.
3. Confirm the checkpoint under review is the next checkpoint to validate or that the user explicitly asked for a non-standard review.
4. Determine the review target in this order:
   - honor an explicit user instruction such as "latest checkpoint commit"
   - otherwise use the latest checkpoint commit recorded by the `cpN vNN` prefix
   - otherwise review the current worktree state as a fallback
5. Review the checkpoint scope from that target through the current code state.
6. Report:
   - the checkpoint/version reviewed
   - whether the review used a commit boundary or worktree fallback
   - the checkpoints and behaviors covered by the reviewed slice
7. Review the actual code state, not just commit messages.
8. When reporting or updating review state, prefer checkpoint/version labels such as "reviewed through `cp5 v01`". Include exact SHAs only when they materially help disambiguate the history.

## Approval Path

If the checkpoint looks correct:

1. Advance the original plan's review state for that checkpoint.
2. Update the original plan's `Git Tracking` and `Review Log` to capture the approved checkpoint review.
3. Do not squash the whole handoff.
4. Leave the checkpoint commit structure intact unless a later workflow explicitly asks for a different history action.
5. If later checkpoints remain unchecked, keep the original plan in progress for the next checkpoint review or execution pass.
6. Treat dirty changes in plan files that are intentionally part of checkpoint bookkeeping as part of finalization, not as unrelated worktree noise. Still stop if truly unrelated dirty changes remain and make the checkpoint review ambiguous.

## Rejection Path

If the checkpoint is not acceptable:

1. Do not approve it.
2. Create a new aflow fix plan that covers only the failed checkpoint or behaviors against the current `HEAD`.
3. Ensure `plans/in-progress/` exists before writing the fix plan. Create it if it does not exist.
4. Use a focused filename that names the rejected checkpoint or behavior range.
5. The fix plan must be self-contained and must not require the implementer to read prior chat context.
6. When creating a new fix plan, delete older superseded fix plans for the same checkpoint by default unless the user explicitly asks to keep them.
7. After creating the new fix plan, `plans/in-progress/` should contain only the original handoff plan plus that newest fix plan for the same checkpoint.
8. Update the original plan:
   - append a `Review Log` entry with the review date, the reviewed checkpoint or behavior range, and outcome `changes-requested`
   - update `Last Reviewed HEAD` only when it clearly helps and does not create brittle bookkeeping pressure
9. Keep `Pre-Handoff Base HEAD` unchanged.
10. Do not compact `DEVLOG.md` and do not squash the whole handoff.

## Stop And Escalate If

- no original aflow plan with `Git Tracking` can be found
- the current branch does not match the plan's `Plan Branch`
- `Pre-Handoff Base HEAD` is missing or no longer reachable
- the worktree has unrelated dirty changes that make the checkpoint review ambiguous
- multiple original plan files are plausible and the active one cannot be determined safely

## Verification

Before finishing, verify:

- the reviewed range is correct relative to the original plan's checkpoint/version review history, `Last Reviewed HEAD`, or `Pre-Handoff Base HEAD`
- the reviewed target was the latest checkpoint commit when that boundary was available
- the fallback to current worktree state was only used when the checkpoint commit boundary was missing or ambiguous
- the reported commit counts match git history when a commit boundary is available
- after approval, the original plan's review state advances only for the reviewed checkpoint
- after rejection, no history rewrite occurred
- after rejection, `plans/in-progress/` contains only the original handoff plan plus the newest focused fix plan for that checkpoint
- after rejection, superseded older fix plans were deleted unless the user asked to keep them
- `DEVLOG.md` was not compacted by this workflow
