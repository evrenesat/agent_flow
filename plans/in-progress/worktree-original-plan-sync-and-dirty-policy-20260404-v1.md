# Worktree Original Plan Sync And Dirty Policy

## Summary

Refactor worktree lifecycle so the original handoff plan may remain untracked or gitignored under `plans/`, while still allowing worktree workflows to run safely and predictably.

The implementation must:

- allow startup dirtiness only when every dirty path in the primary checkout is under `plans/`
- reject unrelated primary-checkout dirtiness outside `plans/`
- stop requiring the original plan file to be git-tracked for worktree workflows
- copy the original plan into the linked worktree before worktree turns render prompts or run harnesses
- sync the original plan back from the linked worktree to the primary checkout after each successful harness turn before post-turn parsing finalizes state
- preserve the current transient behavior for follow-up plans created via `NEW_PLAN_EXISTS`; do not make them durable or restartable in this handoff
- remove the current contradiction where the CLI asks “Start anyway?” and lifecycle preflight then hard-fails on the same dirty checkout

## Git Tracking

- Plan Branch: `main`
- Pre-Handoff Base HEAD: `48c75170033c315b320b43c1ef9fa5277767d257`
- Last Reviewed HEAD: `none`
- Review Log:
  - None yet.

## Done Means

- Running `aflow run` with a worktree workflow succeeds when the only dirty files in the primary checkout are under `plans/` and the original plan itself is untracked or gitignored.
- Running the same command still fails before setup when any dirty file outside `plans/` exists in the primary checkout.
- The original plan is available at the translated worktree path during worktree prompt rendering and harness execution, even when that file is absent from git history.
- When a worktree turn edits the original plan, the primary checkout copy reflects those edits before post-turn snapshot parsing and before later turns or restart logic read it.
- Follow-up plans remain worktree-local/transient within the run; this handoff does not introduce restart/resume support for them.
- Existing tracked-plan worktree flows, branch-only flows, merge verification, run-log placement, and same-step cap behavior remain intact except for the intended dirty-policy/original-plan transport changes.

## Critical Invariants

- The primary checkout copy of the original plan is the long-lived source of truth for the handoff ledger; the worktree copy is a derived execution copy.
- Worktree lifecycle must never allow unrelated dirty paths outside `plans/` in the primary checkout at startup.
- This handoff must not silently widen sync scope beyond the original plan file.
- Follow-up plans created via `NEW_PLAN_EXISTS` remain transient and are not upgraded to durable restart state in this handoff.
- Run artifacts remain under the primary checkout’s `.aflow/runs/` tree, not under the linked worktree.
- Merge verification must stay strict for real code changes; the refactor must not weaken merge cleanliness checks for non-plan files.
- "Under `plans/`" means the git-status porcelain path starts with the literal prefix `plans/` (repo-relative). Substring matching (e.g. `my-plans/` or `templates/plans/`) must not be treated as allowed dirtiness. Both the CLI and preflight must use the same classification helper to enforce this rule.

## Forbidden Implementations

- Do not keep the current “plan must be tracked in git” requirement for worktree workflows.
- Do not allow arbitrary dirty primary-checkout files outside `plans/` just because a worktree workflow is selected.
- Do not sync the entire `plans/` subtree, `plans/backups/`, or `plans/done/`.
- Do not silently make follow-up plans durable, restartable, or primary-root-synced as part of this handoff.
- Do not leave the CLI dirty prompt and lifecycle preflight using incompatible rules.
- Do not modify the root `AGENTS.md`.
- Do not use substring or `in` matching on paths to classify `plans/` dirtiness; always check that the repo-relative path starts with the exact prefix `plans/`.
- Do not remove the existing preflight check that validates the plan file is under the primary repo root (`plan_path.resolve().relative_to(primary_root.resolve())`); only remove the `_is_git_tracked` gate.

## Checkpoints

### [x] Checkpoint 1: Classify allowed startup dirtiness and align CLI with lifecycle policy

**Goal:**

- Replace the blanket dirty-worktree gate with a policy that treats `plans/` dirtiness as allowed workflow state for worktree lifecycle runs and still blocks unrelated primary-checkout dirtiness.

**Context Bootstrapping:**

- Run these commands before editing:
- `git status --short --branch`
- `rg -n "probe_worktree|Worktree is dirty|_lifecycle_preflight|status --porcelain" aflow tests/test_aflow.py -S`
- `sed -n '499,535p' aflow/cli.py`
- `sed -n '859,905p' aflow/workflow.py`
- If this is Checkpoint 1, capture the git tracking values before any edits:
- `git branch --show-current`
- `git rev-parse HEAD`

**Scope & Blast Radius:**

- May create/modify: `aflow/git_status.py`, `aflow/cli.py`, `aflow/workflow.py`, `tests/test_aflow.py`
- Must not touch: `aflow/bundled_skills/**`, `plans/**` except read-only access to this assigned plan file and the minimal progress-tracking edits performed by the consuming workflow, merge-verification logic unrelated to startup dirtiness
- Constraints: keep branch-only and no-lifecycle behavior unchanged unless a selected workflow actually uses `setup = ["worktree", "branch"]`; prefer a shared dirtiness-classification helper rather than duplicating path filtering rules in CLI and workflow preflight

**Steps:**

- [x] Step 1: Add a dirtiness-classification helper to `aflow/git_status.py` that takes a `WorktreeProbe` (or the raw porcelain output) and a prefix string (default `"plans/"`) and returns a pair: (plan-only dirty paths, non-plan dirty paths). The helper must compare each repo-relative path with `path.startswith("plans/")`, not substring matching. Preserve the existing summary counts and sample paths in `WorktreeProbe` — the classification is an additional layer on top, not a replacement.
- [x] Step 2: Update CLI startup gating (around `cli.py:499–517`) so worktree workflows skip the generic dirty confirmation when the classification helper reports zero non-plan dirty paths. The CLI dirty check runs AFTER workflow resolution, so the selected workflow type is already known at this point. For non-worktree workflows, keep the existing behavior unchanged.
- [x] Step 3: Update lifecycle preflight (`workflow.py:859–868`) so it applies the same classification helper to filter `git status --porcelain=v1 --untracked-files=all` output. The preflight's `--untracked-files=all` flag will surface the untracked plan file itself, so the filter must exclude that path from rejection. Reject only when non-plan dirty paths remain. Both CLI and preflight must call the same helper to guarantee identical classification rules.
- [x] Step 4: Add or update focused tests covering: (a) plan-only dirtiness is accepted for worktree workflows, (b) non-plan dirtiness is rejected, (c) mixed dirtiness (plan + non-plan) is rejected, (d) paths like `plans_backup/` or `my-plans/foo` are NOT treated as plan paths, (e) the old contradictory CLI-prompt-then-preflight-fail sequence no longer occurs.

**Dependencies:**

- Depends on no earlier checkpoint.

**Verification:**

- Run scoped tests: `uv run python -m pytest -q tests/test_aflow.py -k 'dirty and (cli or preflight or worktree)'`
- Run non-regression tests: `uv run python -m pytest -q tests/test_aflow.py -k 'branch_only_setup_creates_feature_branch_and_uses_primary_as_exec_root or worktree_setup_creates_worktree_and_uses_it_as_exec_root'`

**Done When:**

- Plan-only dirtiness under `plans/` is accepted for worktree workflows without the old contradictory prompt-then-fail behavior.
- Non-plan dirtiness still blocks worktree lifecycle before setup begins.
- Verification commands pass cleanly.
- A git commit is created with message starting with:
  ```text
  cp1 v01
  Align dirty-worktree policy with worktree lifecycle startup rules
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp4 v02`, `cp4 v03`, and so on.

**Stop and Escalate If:**

- Dirty-path filtering for `plans/` would accidentally suppress unrelated non-plan dirtiness because git porcelain output shape is ambiguous (e.g. rename arrows, quoted paths with special characters); emit `AFLOW_STOP: dirty-path classification cannot safely distinguish plans-only changes`

### [x] Checkpoint 2: Sync the original plan between primary checkout and worktree

**Goal:**

- Make the original plan file available inside linked worktrees even when it is untracked, and copy it back to the primary checkout after worktree turns update it.

**Context Bootstrapping:**

- Run these commands before editing:
- `git status --short --branch`
- `rg -n "_exec_plan_path|original_plan_path|active_plan_path|new_plan_path|render_step_prompts|_resolve_post_turn_original_plan_path" aflow/workflow.py -S`
- `sed -n '180,245p' aflow/workflow.py`
- `sed -n '1520,1845p' aflow/workflow.py`
- `sed -n '5410,5595p' tests/test_aflow.py`

**Scope & Blast Radius:**

- May create/modify: `aflow/workflow.py`, `tests/test_aflow.py`
- Must not touch: `aflow/cli.py` except for imports or wiring strictly required by this checkpoint, merge prompt semantics, follow-up plan lifetime semantics, `plans/**` except read-only access to this assigned plan file and the minimal progress-tracking edits performed by the consuming workflow
- Constraints: sync only the original plan file; keep `NEW_PLAN_EXISTS` detection worktree-local; preserve primary-root run logs and current branch-only behavior; ensure sync happens before prompt rendering and before post-turn parsing relies on primary-root state

**Steps:**

- [x] Step 1: Add two helpers in `aflow/workflow.py`: `_sync_plan_to_worktree(primary_path, exec_ctx)` and `_sync_plan_from_worktree(primary_path, exec_ctx)`. Both use `_exec_plan_path` (line 1001) to translate between primary and worktree paths. Both raise `WorkflowError` with a message identifying which direction failed and which path was missing/unreadable. `_sync_plan_to_worktree` must create parent directories in the worktree if they don't exist.
- [x] Step 2: Remove the `_is_git_tracked` gate in `_lifecycle_preflight` (lines 893–898). Keep the `plan_path.resolve().relative_to(primary_root.resolve())` check that validates the plan is under the repo root — that check is still needed for path translation correctness. Replace the git-tracked requirement with a simple `plan_path.is_file()` existence check.
- [x] Step 3: Call `_sync_plan_to_worktree` BEFORE `render_step_prompts` at line 1582. This is the point where `_exec_plan_path` is first used to translate `original_plan_path` for prompt rendering. The plan file must exist at the worktree path before `load_plan_tolerant` is called inside `render_prompt` (line 200). Also call it before `load_plan(original_plan_path)` at line 1546, since that reads the plan for checkpoint detection.
- [x] Step 4: Call `_sync_plan_from_worktree` AFTER `load_plan(resolved_exec_plan_path)` at line 1680 and BEFORE any code that reads the primary-root plan path. Sync-back must happen on BOTH successful and failed turns — if the harness failed but still edited the plan (e.g. marked a checkpoint as failed), the primary copy must reflect that for restart correctness. Sync-back failure should raise `WorkflowError` (not silently swallow), since an inconsistent primary copy would corrupt restart state.
- [x] Step 5: Keep follow-up plans (`new_plan_path`) transient/worktree-local. Do not call `_sync_plan_from_worktree` for `new_plan_path` or `active_plan_path` when they differ from `original_plan_path`. The existing `NEW_PLAN_EXISTS` check at line 1833 already reads from the worktree via `_exec_plan_path`, which is correct.
- [x] Step 6: Add regression tests covering: (a) untracked plan is available at worktree path after sync-in, (b) worktree edits to the plan are reflected in primary checkout after sync-back, (c) sync-back happens even when the harness exits non-zero, (d) sync-in creates parent directories if needed, (e) sync failure raises `WorkflowError` with actionable message.

**Dependencies:**

- Depends on Checkpoint 1.

**Verification:**

- Run scoped tests: `uv run python -m pytest -q tests/test_aflow.py -k 'worktree and (not_git_tracked or sync or original_plan or prompt_render_failure)'`
- Run non-regression tests: `uv run python -m pytest -q tests/test_aflow.py -k 'worktree_run_json_records_lifecycle_context_with_worktree_path or worktree_adapter_invocation_uses_worktree_path or worktree_merge_handoff_removes_worktree_after_success or merge_verification_failure_preserves_worktree'`

**Done When:**

- A worktree turn can render prompts and run against an untracked original plan under `plans/`.
- After a worktree turn edits the original plan, the primary checkout copy is updated before later run state depends on it.
- Follow-up plans are still not treated as durable synced state.
- Verification commands pass cleanly.
- A git commit is created with message starting with:
  ```text
  cp2 v01
  Sync original plan into and out of linked worktrees
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp4 v02`, `cp4 v03`, and so on.

**Stop and Escalate If:**

- Merge handoff or supported workflow paths require follow-up plan sync for correctness, not just the original plan; emit `AFLOW_STOP: original-plan-only sync is insufficient for supported worktree workflows`
- Original-plan sync would require mutating run artifacts or backups to keep state consistent; emit `AFLOW_STOP: plan sync design is leaking into run artifact or backup state`

### [x] Checkpoint 3: Document the new lifecycle contract and lock it with acceptance coverage

**Goal:**

- Update existing docs to reflect the new worktree-plan transport and dirty-policy rules, and finish with a verification matrix backed by stable regression coverage.

**Context Bootstrapping:**

- Run these commands before editing:
- `git status --short --branch`
- `rg -n "Dirty Worktree|worktree|lifecycle|plans/backups|probe_worktree" README.md ARCHITECTURE.md devlog/DEVLOG.md -S`
- `sed -n '346,405p' README.md`
- `sed -n '93,110p' ARCHITECTURE.md`
- `sed -n '60,95p' devlog/DEVLOG.md`

**Scope & Blast Radius:**

- May create/modify: `README.md`, `ARCHITECTURE.md`, `devlog/DEVLOG.md`, `tests/test_aflow.py`
- Must not touch: root `AGENTS.md`, bundled skill content, unrelated README sections that do not already describe dirty-worktree or worktree-lifecycle behavior
- Constraints: update only existing relevant sections in `README.md`; keep docs aligned with the implemented behavior in Checkpoints 1 and 2; if no existing relevant README section covers a point, document it in `ARCHITECTURE.md` or `devlog/DEVLOG.md` instead of creating a new README feature section

**Steps:**

- [x] Step 1: Update the existing README dirty-worktree and lifecycle sections so they say plan-only dirtiness is allowed for worktree workflows, unrelated primary dirtiness is rejected, and the original plan is copied into linked worktrees rather than required to be git-tracked.
- [x] Step 2: Update `ARCHITECTURE.md` to describe the refined preflight rule and the original-plan sync path in the worktree turn loop.
- [x] Step 3: Add a factual `devlog/DEVLOG.md` entry describing the behavior change, the reason for it, and any gotchas around original-plan-only sync and transient follow-up plans.
- [x] Step 4: Run a broader targeted regression slice that covers dirty policy, worktree lifecycle, and existing merge/run-log invariants together.

**Dependencies:**

- Depends on Checkpoint 2.

**Verification:**

- Run scoped tests: `uv run python -m pytest -q tests/test_aflow.py -k 'dirty or worktree or runlog'`
- Run non-regression tests: `uv run python -m pytest -q tests/test_aflow.py`

**Done When:**

- The relevant existing docs describe the actual implemented startup and worktree-plan behavior.
- The targeted and full verification commands pass cleanly.
- A git commit is created with message starting with:
  ```text
  cp3 v01
  Document worktree original-plan sync and dirty-policy rules
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp4 v02`, `cp4 v03`, and so on.

**Stop and Escalate If:**

- The full pytest suite exposes unrelated pre-existing failures that prevent clean confirmation of this handoff; emit `AFLOW_STOP: full test suite has unrelated failures that block final verification`
- Documentation parity would require describing follow-up-plan durability or restart behavior that is still intentionally unchanged; emit `AFLOW_STOP: documentation scope is drifting into unimplemented follow-up-plan lifecycle changes`

## Behavioral Acceptance Tests

- Given a worktree workflow and a primary checkout whose only dirty files are under `plans/`, `aflow run` starts without the old contradictory dirty prompt followed by a lifecycle preflight failure.
- Given a worktree workflow and at least one dirty file outside `plans/`, `aflow run` fails before worktree setup with a message that clearly identifies unrelated primary-checkout dirtiness.
- Given a worktree workflow and dirty paths under `plans/` mixed with dirty paths outside `plans/`, `aflow run` rejects the startup (mixed dirtiness is not allowed).
- Given dirty files under paths that look similar but are not `plans/` (e.g. `plans_backup/foo`, `my-plans/bar`), the classification helper treats them as non-plan dirtiness and rejects startup.
- Given an original plan that is untracked or gitignored under `plans/in-progress/`, the first worktree turn can render `{ACTIVE_PLAN_PATH}` and `{WORK_ON_NEXT_CHECKPOINT_CMD}` successfully because the original plan exists at the translated worktree path.
- Given a worktree turn that edits the original plan, the primary checkout copy shows those edits before the next turn or a restart reads workflow state from disk.
- Given a worktree turn where the harness exits non-zero but the plan was edited, the primary checkout copy still reflects those edits (sync-back is not conditional on harness success).
- Given a run that creates a follow-up plan via `NEW_PLAN_EXISTS`, that follow-up plan remains transient/worktree-local; this handoff does not claim restart/resume support for it.
- Given tracked-plan worktree workflows that already pass today, they continue to pass with the same run-log placement, worktree execution root, and merge-teardown semantics.

## Plan-to-Verification Matrix

| Requirement | Verification |
| --- | --- |
| Allow plan-only primary dirtiness for worktree workflows | `uv run python -m pytest -q tests/test_aflow.py -k 'dirty and worktree'` |
| Reject unrelated primary dirtiness outside `plans/` | `uv run python -m pytest -q tests/test_aflow.py -k 'dirty and (cli or preflight)'` |
| Remove git-tracked requirement for original worktree plan | `uv run python -m pytest -q tests/test_aflow.py -k 'not_git_tracked and worktree'` |
| Make original plan available inside linked worktree before prompt rendering | `uv run python -m pytest -q tests/test_aflow.py -k 'worktree and original_plan'` |
| Sync original plan back to primary checkout after worktree turn edits | `uv run python -m pytest -q tests/test_aflow.py -k 'worktree and sync'` |
| Sync-back happens even on harness failure | `uv run python -m pytest -q tests/test_aflow.py -k 'worktree and sync and (fail or nonzero)'` |
| Reject mixed plan + non-plan dirtiness | `uv run python -m pytest -q tests/test_aflow.py -k 'dirty and mixed'` |
| Reject similar-but-wrong path prefixes (plans_backup, etc.) | `uv run python -m pytest -q tests/test_aflow.py -k 'dirty and prefix'` |
| Preserve tracked-plan worktree execution behavior | `uv run python -m pytest -q tests/test_aflow.py -k 'worktree_setup_creates_worktree_and_uses_it_as_exec_root or worktree_adapter_invocation_uses_worktree_path'` |
| Preserve merge teardown/run-log invariants | `uv run python -m pytest -q tests/test_aflow.py -k 'worktree_merge_handoff_removes_worktree_after_success or merge_verification_failure_preserves_worktree or runlog'` |
| Keep docs aligned with actual implementation | `rg -n "Dirty Worktree|worktree|plan is copied|untracked|plans/" README.md ARCHITECTURE.md devlog/DEVLOG.md -S` |

## Assumptions And Defaults

- `plans/` is the only primary-checkout path prefix whose dirtiness is treated as allowed workflow state for worktree lifecycle startup.
- The original handoff plan is the only file that needs durable sync between primary checkout and linked worktree in this handoff.
- Follow-up plans remain transient and are not made restartable or primary-root-synced here.
- Existing relevant README sections already exist and should be updated in place rather than expanded with new feature sections.
- If implementation reveals that supported merge or restart paths require broader follow-up-plan durability, that is out of scope for this handoff and must be escalated rather than added silently.
- The CLI dirty check at `cli.py:499` runs after workflow resolution, so the selected workflow's setup type (worktree vs branch-only vs none) is already known. No additional refactor is needed to make the workflow type available at that point.
- Both `probe_worktree` and the lifecycle preflight use `--untracked-files=all`, so an untracked plan file under `plans/` will appear in both outputs. The classification helper must handle this correctly (classify it as plan-path dirtiness, not reject it).
