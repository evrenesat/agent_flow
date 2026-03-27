---
name: ralf-cp-review
description: "Review one checkpoint-sized RALF implementation batch, compare the current work against the active checkpoint or focused fix plan, update the original plan when the checkpoint is approved, and create or replace one focused fix plan when changes are still required."
---

# RALF CP Review

Use this skill only for checkpoint-scoped review of work produced under a RALF plan that includes `Git Tracking`.

## Behavior

- Load the active original RALF plan before reviewing code or history.
- Review only the current checkpoint batch, not the whole accumulated handoff.
- Treat files under `plans/` as reviewer-owned artifacts in this workflow.
- The CP executor must treat the plan as read-only. This reviewer owns plan-state updates after approval.
- Treat checkpoint/version commit prefixes such as `cp4 v01`, `cp4 v02`, and `cp5 v01` as the primary review-tracking mechanism. Use exact SHAs as supporting evidence, not as the only way to understand review state.
- If the reviewed checkpoint batch is acceptable:
  - update the original plan's step and checkpoint checkboxes for the approved checkpoint
  - update any reviewer-owned review-log state
  - commit those reviewer-owned plan updates
  - optionally squash only the current checkpoint's multiple implementation commits when needed
- If the reviewed checkpoint batch is not acceptable, do not finalize the whole plan. Create or replace one focused fix plan for that checkpoint.
- Treat `ralf` and `ralph` as equivalent spellings.
- Keep `plans/in-progress/` constrained to the original handoff plan plus at most one current fix plan for that handoff.

## Core Rule

The original plan file is the durable source of truth for checkpoint status. Fix plans are temporary overlays for changes-requested review passes.

## Required Inputs

Before reviewing, identify:

- the active original plan under `plans/in-progress/`
- the checkpoint to review
- whether there is a current focused fix plan for that same checkpoint

Selection rules:

1. If the user names a checkpoint or plan file, honor that.
2. Otherwise select the original plan file whose `Plan Branch` matches the current branch.
3. If a focused fix plan exists for the next unresolved checkpoint, use that fix plan as the active review instructions while still treating the original plan as the durable ledger.
4. Otherwise review the first unchecked checkpoint from the original plan.
5. If multiple original plans are plausible, stop and ask the user which plan to use.

## Review Workflow

1. Read the original plan's `Git Tracking` section and the active checkpoint.
2. If a focused fix plan exists for that checkpoint, read it fully before reviewing.
3. Confirm the current branch matches `Plan Branch`.
4. Determine the checkpoint batch to review in this order:
   - honor an explicit user instruction such as a named checkpoint or version batch
   - otherwise use the active fix plan for the unresolved checkpoint
   - otherwise use the first unchecked checkpoint from the original plan
5. Review the commit range for that checkpoint batch.
6. Report:
   - the number of new commits in the active checkpoint batch
   - the current checkpoint/version label being reviewed
   - the key files and behaviors changed in that batch
7. Review the actual code state, not just commit messages.
8. When reporting or updating review state, prefer checkpoint/version labels such as `cp3 v02`. Include exact SHAs only when they materially help disambiguate the history.

## Approval Path

If the active checkpoint batch looks correct:

1. Mark the approved step-level items in the original plan from `- [ ]` to `- [x]` when they are satisfied.
2. Mark the checkpoint heading in the original plan from `### [ ]` to `### [x]`.
3. Append a `Review Log` entry describing the approved checkpoint/version batch.
4. Update `Last Reviewed HEAD` only when it clearly helps and does not create brittle bookkeeping pressure.
5. Delete any active fix plan for that checkpoint unless the user explicitly asked to keep it.
6. If the checkpoint batch contains multiple implementation commits, you may squash only that checkpoint's batch into one commit. Do not auto-finalize the whole accumulated plan.
7. Commit the reviewer-owned original-plan updates after approval.
8. If that approval completes the final checkpoint, move the original plan to `plans/done/`, include the move in the reviewer-owned commit, and mention the moved path in the final response.

## Rejection Path

If the active checkpoint batch is not acceptable:

1. Do not finalize the whole plan.
2. Do not rewrite unrelated history.
3. Create a new focused RALF fix plan for the active checkpoint against the current `HEAD`.
4. Ensure `plans/in-progress/` exists before writing the fix plan. Create it if it does not exist.
5. Use the filename format `original-plan-name-fix-cpN-v01.md`.
6. The fix plan must be self-contained and must not rely on the original plan for implementation instructions.
7. Before writing the new fix plan, delete any older superseded fix plan for the same original handoff unless the user explicitly asked to keep it.
8. After creating the new fix plan, `plans/in-progress/` should contain only the original handoff plan plus that newest fix plan for the same handoff.
9. Append a `Review Log` entry to the original plan with outcome `changes-requested`.
10. Update `Last Reviewed HEAD` only when it clearly helps and does not create brittle bookkeeping pressure.
11. Keep `Pre-Handoff Base HEAD` unchanged.

## Stop And Escalate If

- no original RALF plan with `Git Tracking` can be found
- the current branch does not match the plan's `Plan Branch`
- the next checkpoint or active fix plan cannot be determined safely
- `Pre-Handoff Base HEAD` is missing or no longer reachable
- the worktree has unrelated dirty changes that make the review or reviewer-owned commit ambiguous
- multiple original plan files are plausible and the active one cannot be determined safely

## Verification

Before finishing, verify:

- the reviewed range is correct for the active checkpoint/version batch
- the reported commit counts match git history
- after approval, the original plan shows both satisfied step checkboxes and the checkpoint heading as checked
- after approval, reviewer-owned plan updates were committed
- after approval of a completed checkpoint, no stale fix plan remains in `plans/in-progress/`; before the next normal checkpoint starts, only the original handoff plan remains there
- after final checkpoint approval, `plans/done/` exists and contains the completed original handoff plan
- after rejection, no whole-plan history rewrite occurred
- after rejection, `plans/in-progress/` contains only the original handoff plan plus the newest fix plan for that handoff
