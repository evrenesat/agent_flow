---
name: ralf-review-squash
description: "Review sub-agent commits after a RALF handoff, compare the new batch since the last review, and either create a fix plan or squash the full handoff history into one final commit."
---

# RALF Review Squash

Use this skill only for post-handoff review of work produced under a RALF plan that includes `Git Tracking`.

## Behavior

- Load the active RALF plan before reviewing code or history.
- Review only the commits created since the last recorded review, while keeping the full handoff scope in mind.
- If the newest batch is acceptable, squash the entire handoff history since the original base commit into one final commit.
- If the newest batch is not acceptable, do not squash; create a follow-up RALF fix plan instead.
- Treat `ralf` and `ralph` as equivalent spellings.
- Compact `DEVLOG.md` to one handoff entry only when a squash actually happens and multiple handoff entries exist.

## Core Rule

The plan file is the source of truth for review state. Do not infer `Last Reviewed HEAD` from commit messages or memory when the plan file provides it.

## Required Inputs

Before reviewing or squashing, identify one active RALF plan file under `plans/`.

Selection rules:

1. If the user names a plan file, use it.
2. Otherwise search `plans/` for plan files containing `Pre-Handoff Base HEAD`.
3. Filter candidates to the current branch recorded in `Plan Branch`.
4. If exactly one candidate remains, use it.
5. If multiple candidates remain, stop and ask the user which plan to use.

## Review Workflow

1. Read the plan file's `Git Tracking` section.
2. Confirm the current branch matches `Plan Branch`.
3. Set the review start commit to `Last Reviewed HEAD` when present; otherwise use `Pre-Handoff Base HEAD`.
4. Review the commit range from that start commit to `HEAD`.
5. Report:
   - the number of new commits since the last review
   - the total number of commits since `Pre-Handoff Base HEAD`
   - the key files and behaviors changed in the new batch
6. Review the actual code state, not just commit messages.

## Approval Path

If the new batch looks correct:

1. Decide the final squash scope before rewriting history.
   - If the active plan is the original handoff plan, squash from that plan's `Pre-Handoff Base HEAD`.
   - If the active plan is a follow-up fix plan created after an earlier review rejection, do not automatically assume the final squash scope is only the fix-plan range.
   - When the user wants one final accumulated handoff commit, switch back to the original handoff plan and use its `Pre-Handoff Base HEAD` as the squash anchor.
   - Only squash the fix-plan range by itself when the user explicitly wants the earlier handoff commits preserved separately.
2. If the final squash scope is the full original handoff, treat the original handoff plan as the source of truth for finalization.
   - Update the original plan's `Git Tracking` and `Review Log` to capture both the earlier rejection and the final approved+squashed review.
   - Move the original plan to `plans/in-progress/done` when its checkpoints are complete.
   - Remove the temporary fix plan unless the user explicitly wants to keep it.
   - Include any explicitly requested summary or handoff companion plan files in the final commit.
3. Rewrite history so every commit after the chosen squash base becomes one commit.
4. Use a non-interactive workflow. Prefer `git reset --soft <chosen squash base>` followed by a new commit over interactive rebase.
5. Write a fresh final commit message that covers the full accumulated scope of the handoff, including earlier approved work and the latest fixes.
6. If `DEVLOG.md` exists and multiple handoff-related entries were added or updated during the handoff, compact them to one entry that matches the final squashed change.
7. Update the plan file used for finalization:
   - do not require the finalized plan file to record the exact SHA of the new squashed commit inside that same commit
   - the finalized plan should record the reviewed ranges and outcome `approved+squashed`
   - if `Last Reviewed HEAD` is still useful, use stable non-self-referential wording or omit the exact value
   - the exact final squashed SHA is not needed for future checkpoint comparison on a completed plan
8. If the finalized plan's checkpoints are completely done, move that finalized plan to `plans/in-progress/done/` and include it in the final commit.
9. Treat dirty changes in plan files that are intentionally part of the final handoff state as part of finalization, not as unrelated worktree noise. Still stop if truly unrelated dirty changes remain and make the squash ambiguous.

## Rejection Path

If the new batch is not acceptable:

1. Do not squash commits.
2. Create a new RALF fix plan that addresses the review findings against the current `HEAD`.
3. Update the plan file:
   - set `Last Reviewed HEAD` to the current `HEAD`
   - append a `Review Log` entry with the review date, the reviewed range, and outcome `changes-requested`
4. Keep `Pre-Handoff Base HEAD` unchanged.
5. Do not compact `DEVLOG.md`.

## Stop And Escalate If

- No RALF plan file with `Git Tracking` can be found.
- The current branch does not match the plan's `Plan Branch`.
- `Pre-Handoff Base HEAD` is missing or no longer reachable.
- The worktree has unrelated dirty changes that make the review or squash ambiguous.
- Multiple plan files are plausible and the active one cannot be determined safely.

## Verification

Before finishing, verify:

- the reviewed range is correct relative to `Last Reviewed HEAD` or `Pre-Handoff Base HEAD`
- the reported commit counts match git history
- after approval, the branch contains exactly one accumulated handoff commit after `Pre-Handoff Base HEAD`
- after approval, do not require the finalized plan file to self-reference the final squashed commit hash
- after rejection, no history rewrite occurred
- `DEVLOG.md` was compacted only when a squash occurred and multiple relevant entries existed
