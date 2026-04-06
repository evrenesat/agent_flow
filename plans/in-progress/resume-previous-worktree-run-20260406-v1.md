# Resume Previous Worktree Run

## Summary

Implement interactive resume for unfinished worktree-based `aflow run` invocations by reusing the previous run's recorded feature branch and linked worktree instead of always creating a fresh pair. The resume path must be keyed off the existing `AFLOW_LAST_RUN_ID` / `.aflow/last_run_id` mechanism that is being introduced by `plans/in-progress/optional-skill-selection-and-aflow-assistant.md`; assume that baseline is already present before this handoff starts.

This handoff is intentionally scoped to lifecycle workflows whose `setup` includes `worktree` and `branch`. Branch-only and no-lifecycle workflows keep their current behavior in this handoff.

Files expected to change in this handoff:

- `aflow/cli.py`
- `aflow/run_state.py`
- `aflow/runlog.py`
- `aflow/workflow.py`
- `aflow/api/startup.py` (only if resume detection is wired through the startup API; otherwise keep untouched)
- `README.md`
- `ARCHITECTURE.md`
- `devlog/DEVLOG.md`
- `tests/test_assistant.py`
- `tests/test_cli.py`
- `tests/test_config.py`
- `tests/test_runtime.py`

Files that must stay untouched in this handoff:

- Root `AGENTS.md`
- `aflow/aflow.toml`
- `aflow/workflows.toml`
- `aflow/skill_installer.py`
- `aflow/bundled_skills/**`
- `pyproject.toml`
- `tests/test_docs.py`
- `tests/test_retry.py`

## Git Tracking

- Plan Branch: `aflow-resume-previous-worktree-run-20260406-v1-20260406-172959`
- Pre-Handoff Base HEAD: `e3ba744da4a231c4386554d84cf3131b74fd6f27`
- Last Reviewed HEAD: `none`
- Review Log:
  - None yet.

## Done Means

- Given an unfinished worktree workflow run recorded by `AFLOW_LAST_RUN_ID` or `.aflow/last_run_id`, rerunning the same effective `aflow run ...` command in an interactive terminal asks whether to resume from that previous run instead of silently starting fresh.
- The resume prompt is offered only when the current resolved invocation exactly matches the previous run on all of these fields: `repo_root`, `workflow_name`, absolute `plan_path`, effective team, resolved `selected_start_step`, effective `max_turns`, and exact `extra_instructions` tuple.
- Answering `y`, `yes`, or pressing Enter reuses the previous run's `feature_branch` and `worktree_path`; the engine must not generate a new timestamped branch or a new linked worktree in that path.
- Answering `n` or `no` skips resume and preserves current fresh-run behavior, including creation of a new timestamped branch/worktree pair.
- Non-interactive invocations never block on a resume prompt and never auto-resume. They continue with the current fresh-run behavior.
- Resume is offered only for incomplete prior runs whose lifecycle setup included `worktree` and whose recorded execution context is still valid. Completed runs, already-merged runs, and merge-failure-after-complete runs are not resumable through this feature.
- A resumed run still creates its own new `.aflow/runs/<run-id>/` directory and leaves the previous run's artifacts untouched. Reuse applies only to the execution context, not to run-log directories.
- The new run's `run.json` stores enough data to audit the resume decision, including the effective team and a `resumed_from_run_id` field when reuse actually happened.
- Existing `AFLOW_LAST_RUN_ID` / `.aflow/last_run_id` behavior for `aflow analyze` remains unchanged.
- Existing worktree-plan sync behavior remains unchanged: the plan file on disk stays the source of truth for checkpoint progress and restart behavior.

## Critical Invariants

- Resume detection must stay opt-in at the terminal prompt. A valid candidate alone is not permission to auto-resume.
- This handoff must not change startup behavior for branch-only or no-lifecycle workflows.
- Resume eligibility must be based on the current plan file and current resolved CLI inputs, not on stale checkpoint state cached in a previous `run.json`.
- If resume is accepted, the runner must reuse the exact recorded `feature_branch` and `worktree_path`; it must not allocate a second execution context alongside the old one.
- If resume is declined, or no valid candidate exists, current fresh-run lifecycle behavior must remain intact.
- The precedence for finding the previous run id remains: `AFLOW_LAST_RUN_ID` environment variable first, then `.aflow/last_run_id`.
- Invalid or stale resume candidates must not mutate git state before the engine falls back to a fresh run.
- A prior run with `last_snapshot.is_complete == true` is not resumable through this feature, even if merge teardown failed afterward.
- If the current workflow's effective `lifecycle_setup` tuple does not match the recorded run's `lifecycle_setup`, the candidate is not resumable. This guards against config drift where a user changed their workflow definition between runs.

## Forbidden Implementations

- Do not expand this handoff to branch-only resume behavior.
- Do not auto-resume in interactive terminals without asking, and do not auto-resume in non-interactive mode.
- Do not treat "same command" as matching only on plan path or only on workflow name. Use the full resolved invocation match defined in `Done Means`.
- Do not restore checkpoint progress from old `run.json.last_snapshot` when the current plan file on disk says otherwise.
- Do not silently reuse a completed prior run or a merge-failed-after-complete run.
- Do not delete or rewrite stale worktrees or stale branches as part of candidate detection.
- Do not change `.aflow/last_run_id` semantics to point back at the old run after a resumed run starts. The new run still owns the current last-run-id file entry once its run directory is created.
- Do not add a new config knob or a new CLI flag for resume in this handoff unless the planned prompt-based behavior proves impossible.

## Checkpoints

### [x] Checkpoint 1: Detect And Offer Resume For Matching Unfinished Worktree Runs

**Goal:**

- Detect whether the last recorded run is a valid unfinished worktree-run candidate for the current resolved invocation, and ask the user whether to reuse it before workflow execution starts.

**Context Bootstrapping:**

- Run these commands before editing:
- `git status --short`
- `git branch --show-current`
- `git rev-parse HEAD`
- `rg -n "AFLOW_LAST_RUN_ID|last_run_id|create_run_paths|write_run_metadata|selected_start_step|extra_instructions|startup_recovery|workflow_name|plan_path" aflow/cli.py aflow/runlog.py aflow/run_state.py aflow/workflow.py tests/test_cli.py tests/test_assistant.py tests/test_runtime.py README.md ARCHITECTURE.md`
- `bat --paging=never aflow/cli.py`
- `bat --paging=never aflow/runlog.py`

**Scope & Blast Radius:**

- May create/modify:
  - `aflow/cli.py`
  - `aflow/run_state.py`
  - `aflow/runlog.py`
  - `aflow/analyzer.py` (only if extracting the shared last-run-id lookup helper requires updating the import)
  - `tests/test_assistant.py`
  - `tests/test_cli.py`
- Must not touch:
  - `aflow/workflow.py`
  - `README.md`
  - `ARCHITECTURE.md`
  - `devlog/DEVLOG.md`
  - `plans/**` except this handoff plan's checkbox state as owned by the consuming workflow
- Constraints:
  - Assume the baseline `.aflow/last_run_id` support already exists when this checkpoint starts.
  - Resume detection must happen after the CLI has resolved workflow, plan path, effective team, `max_turns`, startup recovery, and `selected_start_step`, but before `run_workflow()` creates a fresh lifecycle context.
  - The comparison must use resolved values, not raw argv strings.
  - If the candidate does not match, or is obviously not resumable from metadata alone, do not prompt and continue with the current fresh-run path.
  - If stdin/stdout are not TTYs, do not prompt and do not fail; proceed fresh.

**Steps:**

- [x] Step 1: Add a small frozen dataclass in `aflow/run_state.py` (e.g., `ResumeContext`) for an accepted resume context. It must carry at minimum: `resumed_from_run_id: str`, `feature_branch: str`, `worktree_path: Path`, `main_branch: str`, `setup: tuple[str, ...]`, `teardown: tuple[str, ...]`. This object is what `cli.py` passes into `run_workflow()` via a new `resume: ResumeContext | None = None` keyword argument (added in CP2 Step 1). In CP1, the structure is defined and the CLI creates it; in CP2, `run_workflow()` consumes it.
- [x] Step 2: Extend `write_run_metadata()` in `aflow/runlog.py` so every run records the effective team (field name: `"team"`, value: the resolved team string or `null`) and, when applicable, `"resumed_from_run_id"`. Keep existing fields intact. Note: `team` is currently NOT written to `run.json` at all — this is a new field, not an extension of an existing one. Add a helper function in `runlog.py` to load a previous run's `run.json` safely (return `None` on missing/corrupt JSON). For resolving the last run id from `AFLOW_LAST_RUN_ID` env var first then `.aflow/last_run_id` file, extract and reuse the existing lookup logic from `analyzer.py:_resolve_run_path()` (lines ~521-543) rather than reimplementing it. If extraction is impractical, import from `analyzer.py` directly or create a shared helper in `runlog.py` that `analyzer.py` also calls.
- [x] Step 3: In `aflow/cli.py`, after current startup parsing and start-step resolution finish (after `prepare_run()` returns), inspect the last run candidate and compare it against the current resolved invocation on `repo_root`, `workflow_name`, absolute `plan_path`, effective team, resolved `selected_start_step`, effective `max_turns`, and exact `extra_instructions`. All path comparisons (`repo_root`, `plan_path`) must use `Path.resolve()` on both sides since `run.json` stores paths as absolute strings. The `extra_instructions` comparison must match the tuple exactly (order-sensitive). The `team` comparison must treat `null`/absent and `None` as equal.
- [x] Step 4: Restrict the prompt path to previous runs whose `run.json` shows: `lifecycle_setup` list includes `"worktree"`, `feature_branch` and `worktree_path` are present and non-empty strings, `status` is `"failed"` or `"running"` (the two non-terminal status values used in the codebase; `"completed"` is the normal terminal status), `last_snapshot.is_complete` is `false`, and `merge_status` key is absent. Completed runs and merge-failed-after-complete runs (those with `merge_status` present) must be ignored without prompting. Also exclude runs where `lifecycle_setup` does not match the current workflow's effective `setup` tuple — this guards against config drift where a workflow's lifecycle changed between the old run and the current invocation.
- [x] Step 5: Add an interactive prompt in `aflow/cli.py` with current yes/no semantics matching the rest of the CLI: empty input, `y`, and `yes` accept resume; `n` and `no` decline; any other response behaves like decline. The prompt text must name the previous run id, feature branch, and worktree path so the user knows what will be reused.
- [x] Step 6: Add focused CLI tests for: accepted resume prompt, declined resume prompt, mismatch suppressing the prompt, non-TTY skipping the prompt, complete prior runs suppressing the prompt, and lifecycle_setup mismatch suppressing the prompt. If a shared last-run-id lookup helper was extracted from `analyzer.py` into `runlog.py`, add a basic round-trip test for it. Add runlog/helper tests in `tests/test_assistant.py` only where that file already covers `last_run_id` behavior and helper compatibility.

**Dependencies:**

- Depends on the `AFLOW_LAST_RUN_ID` / `.aflow/last_run_id` baseline from `plans/in-progress/optional-skill-selection-and-aflow-assistant.md` already being present on the starting branch.

**Verification:**

- Run scoped tests: `uv run python -m pytest -q tests/test_cli.py -k "startup_recovery or resume"`
- Run non-regression tests: `uv run python -m pytest -q tests/test_assistant.py -k "last_run_id"`

**Done When:**

- The CLI can tell whether the current resolved invocation matches the last unfinished worktree run.
- Interactive runs ask to resume only when the candidate is metadata-valid and incomplete.
- Non-interactive runs remain fresh-run only.
- A git commit is created with message starting with:
  ```text
  cp1 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp1 v02`, `cp1 v03`, and so on.

**Stop and Escalate If:**

- The current branch does not yet contain the `.aflow/last_run_id` / `AFLOW_LAST_RUN_ID` baseline assumed by this plan; emit `AFLOW_STOP: last-run-id baseline is missing for resume handoff`.
- Matching the current invocation requires access to CLI data that cannot be reconstructed from existing resolved inputs without adding a new user-visible flag or config surface; emit `AFLOW_STOP: resume matching needs unplanned CLI surface`.

### [x] Checkpoint 2: Reuse The Existing Worktree Execution Context

**Goal:**

- When the user accepts resume, run the workflow inside the previously recorded worktree and feature branch instead of provisioning a new lifecycle context.

**Context Bootstrapping:**

- Run these commands before editing:
- `git status --short`
- `rg -n "run_workflow|_lifecycle_preflight|_do_lifecycle_setup|_setup_worktree|ExecutionContext|feature_branch|worktree_path|write_run_metadata|resumed_from_run_id" aflow/workflow.py aflow/run_state.py aflow/runlog.py tests/test_runtime.py`
- `bat --paging=never aflow/workflow.py`
- `bat --paging=never -r 2000:2415 tests/test_runtime.py`

**Scope & Blast Radius:**

- May create/modify:
  - `aflow/workflow.py`
  - `aflow/run_state.py`
  - `aflow/runlog.py`
  - `tests/test_runtime.py`
- Must not touch:
  - `aflow/cli.py` except follow-up fixups strictly required by checkpoint integration
  - `README.md`
  - `ARCHITECTURE.md`
  - `devlog/DEVLOG.md`
  - `aflow/aflow.toml`
  - `aflow/workflows.toml`
- Constraints:
  - Scope remains worktree workflows only. Do not retrofit branch-only resume here.
  - Resume validation must be side-effect free until the candidate is accepted and confirmed valid.
  - Reusing an execution context still creates a fresh run directory and fresh turn artifacts for the new run.
  - The reused worktree must continue to participate in normal post-turn plan sync and normal merge teardown.
  - If a resumed run later reaches successful merge teardown, normal `rm_worktree` logic removes the reused worktree exactly once.

**Steps:**

- [x] Step 1: Thread the accepted resume structure from `cli.py` into `run_workflow()` and make it explicit in signatures and `write_run_metadata()` calls. Do not infer resume by rereading `.aflow/last_run_id` inside `run_workflow()`.
- [x] Step 2: Add a dedicated validation helper in `aflow/workflow.py` for previously recorded worktree execution contexts. It must verify at minimum: the recorded `feature_branch` exists locally (via `git rev-parse --verify`), the recorded `worktree_path` exists on disk and is a directory, the path is still registered in `git worktree list --porcelain` for this repo, the recorded `main_branch` still exists locally, the execution root for the resumed run is the recorded `worktree_path`, and no in-progress git operation is active in the worktree (check for `.git/MERGE_HEAD`, `.git/REBASE_HEAD`, or `.git/rebase-merge/` relative to the worktree's git dir — if any exist, the worktree is in a conflicted state and is not safely resumable). The helper must NOT check whether the worktree has uncommitted changes — dirty state is expected from a previously failed run and the agent will handle it normally.
- [x] Step 3: Split lifecycle startup in `run_workflow()` (around line 1780-1800 where `_do_lifecycle_setup()` is called) so accepted resume bypasses the fresh timestamped branch/worktree creation path entirely. Specifically: when a resume context is provided, skip the calls to `_lifecycle_preflight()`, `_do_lifecycle_setup()`, and `_setup_worktree()`. Instead, construct an `ExecutionContext` directly from the validated recorded values (`feature_branch`, `worktree_path`, `main_branch`, `setup`, `teardown`), set `execution_repo_root` to the recorded `worktree_path`, and continue into the turn loop. The branching should be an early `if resume_ctx is not None: ... else: <existing lifecycle path>` guard, not a deeply nested conditional.
- [x] Step 4: Keep `_sync_plan_branch_for_execution()` active on resumed runs so the current plan's `Plan Branch` line is updated to the reused feature branch, and keep all existing plan sync-to-worktree / sync-from-worktree behavior unchanged after startup.
- [x] Step 5: Ensure resumed runs write `resumed_from_run_id` in the new run's `run.json`, keep the previous run artifacts untouched, and continue to write the normal current execution-context fields (`execution_repo_root`, `feature_branch`, `worktree_path`, `lifecycle_setup`, `lifecycle_teardown`) for the new run.
- [x] Step 6: Add runtime tests covering: accepted resume reuses the same feature branch and same worktree path; accepted resume does not create a second linked worktree; declining resume creates a fresh timestamped worktree as before; resumed runs still sync the original plan back to the primary checkout; resumed runs still go through normal merge teardown and worktree removal on success; and validation rejects a worktree with an in-progress git operation (e.g., MERGE_HEAD present).

**Dependencies:**

- Depends on Checkpoint 1.

**Verification:**

- Run scoped tests: `uv run python -m pytest -q tests/test_runtime.py -k "worktree"`
- Run non-regression tests: `uv run python -m pytest -q tests/test_runtime.py -k "run_json or stop_marker"`

**Done When:**

- Accepting resume causes the next workflow turn to execute in the previously recorded worktree.
- No new `git worktree add -b ...` lifecycle path runs when resume is accepted.
- The new run records that it resumed from the earlier run id.
- Existing worktree sync and teardown behavior still passes.
- A git commit is created with message starting with:
  ```text
  cp2 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp2 v02`, `cp2 v03`, and so on.

**Stop and Escalate If:**

- Reusing the recorded worktree requires mutating or replacing git lifecycle behavior outside `aflow/workflow.py` and the listed files; emit `AFLOW_STOP: resumable worktree context needs unplanned lifecycle redesign`.
- The engine cannot validate recorded worktrees safely without a new persistence layer beyond `run.json` and `.aflow/last_run_id`; emit `AFLOW_STOP: resumable worktree validation needs unplanned state storage`.

### [x] Checkpoint 3: Document Resume Behavior And Lock Parity

**Goal:**

- Document the new resume contract and lock doc/test parity so later changes cannot silently reintroduce always-fresh worktree startup.

**Context Bootstrapping:**

- Run these commands before editing:
- `git status --short`
- `rg -n "How A Run Works|startup|resume|worktree|AFLOW_LAST_RUN_ID|last_run_id" README.md ARCHITECTURE.md devlog/DEVLOG.md tests/test_config.py`
- `bat --paging=never README.md`
- `bat --paging=never ARCHITECTURE.md`
- `bat --paging=never devlog/DEVLOG.md`
- `bat --paging=never -r 260:330 tests/test_config.py`

**Scope & Blast Radius:**

- May create/modify:
  - `README.md`
  - `ARCHITECTURE.md`
  - `devlog/DEVLOG.md`
  - `tests/test_config.py`
- Must not touch:
  - Root `AGENTS.md`
  - `aflow/bundled_skills/**`
  - `tests/test_docs.py`
  - `pyproject.toml`
- Constraints:
  - Update only existing relevant sections. Do not add a new README section solely for this feature if an existing lifecycle/startup section already covers it.
  - Docs must describe implemented behavior only: interactive prompt for matching unfinished worktree runs, non-TTY fresh-run fallback, and reuse of the recorded branch/worktree.
  - Do not imply that the engine auto-resumes or that branch-only workflows gained this feature.

**Steps:**

- [x] Step 1: Update `README.md` in its existing startup / lifecycle sections to explain that worktree workflows may offer to resume the last unfinished matching run, what "matching" means at a high level, that the prompt is interactive-only, and that declining resume keeps the existing fresh-run behavior.
- [x] Step 2: Update `ARCHITECTURE.md` to document the new call flow: last-run-id lookup, resume-candidate matching, interactive prompt, recorded worktree validation, and execution-context reuse. Keep the existing statement that the plan file on disk remains the durable checkpoint state.
- [x] Step 3: Add a short factual entry to `devlog/DEVLOG.md` describing the new worktree-resume behavior and its dependence on the last-run-id mechanism.
- [x] Step 4: Update `tests/test_config.py` doc-parity assertions so the documented lifecycle/startup behavior includes resume terminology and no longer assumes worktree workflows always create a fresh execution context on every rerun.

**Dependencies:**

- Depends on Checkpoint 2.

**Verification:**

- Run scoped tests: `uv run python -m pytest -q tests/test_config.py -k "lifecycle"`
- Run non-regression checks: `rg -n "resume|AFLOW_LAST_RUN_ID|last_run_id|worktree" README.md ARCHITECTURE.md devlog/DEVLOG.md`

**Done When:**

- A human reading the existing docs can tell when resume is offered, when it is not, and what is reused.
- Architecture docs and config doc-parity tests describe the implemented resume behavior accurately.
- A git commit is created with message starting with:
  ```text
  cp3 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp3 v02`, `cp3 v03`, and so on.

**Stop and Escalate If:**

- No existing README or architecture sections can document this behavior cleanly without creating speculative new docs structure; emit `AFLOW_STOP: resume documentation needs unplanned doc restructuring`.

## Behavioral Acceptance Tests

- Given a failed or interrupted worktree workflow run recorded by `AFLOW_LAST_RUN_ID` or `.aflow/last_run_id`, rerunning the same resolved `aflow run ...` command in an interactive terminal shows a prompt that names the previous run id, feature branch, and worktree path.
- Given the same scenario and the user answers `y`, the next workflow turn runs in the previously recorded worktree path and on the previously recorded feature branch, and no new timestamped worktree is created.
- Given the same scenario and the user answers `n`, the engine starts a fresh run and creates a new timestamped feature branch/worktree pair exactly as it does today.
- Given the last recorded run differs in workflow, plan path, effective team, selected start step, max turns, extra instructions, or lifecycle setup, the engine does not offer resume and starts fresh.
- Given the last recorded run is already complete, or the plan is complete and the prior failure happened only during merge teardown, the engine does not offer resume.
- Given stdin/stdout are not interactive TTYs, the engine does not prompt and does not auto-resume.
- Given a resumed run starts, the new `.aflow/runs/<new-run-id>/run.json` records `resumed_from_run_id` while the previous run directory remains unchanged.
- Given a resumed run later completes successfully through merge teardown, normal `rm_worktree` logic removes the reused worktree and the merge result is verified exactly as for a fresh run.

## Plan-to-Verification Matrix

| Requirement | Verification |
| --- | --- |
| Last-run-id lookup precedence remains env var first, file second | `uv run python -m pytest -q tests/test_assistant.py -k "last_run_id"` |
| Resume prompt appears only for matching unfinished worktree runs | `uv run python -m pytest -q tests/test_cli.py -k "resume"` |
| Non-interactive runs do not prompt or auto-resume | `uv run python -m pytest -q tests/test_cli.py -k "resume"` |
| Accepted resume reuses existing feature branch and worktree path | `uv run python -m pytest -q tests/test_runtime.py -k "worktree and resume"` |
| Declined resume keeps fresh lifecycle setup behavior | `uv run python -m pytest -q tests/test_cli.py -k "resume"` and `uv run python -m pytest -q tests/test_runtime.py -k "worktree and resume"` |
| Resumed runs still sync plan edits back to the primary checkout | `uv run python -m pytest -q tests/test_runtime.py -k "worktree_syncs_original_plan_back or worktree_syncs_plan_back_even_on_harness_failure"` |
| Resumed runs record `resumed_from_run_id` and current execution context | `uv run python -m pytest -q tests/test_runtime.py -k "run_json and resume"` |
| Existing worktree teardown still removes the reused worktree after successful merge | `uv run python -m pytest -q tests/test_runtime.py -k "worktree and merge"` |
| Docs describe resume behavior without claiming auto-resume or branch-only support | `rg -n "resume|AFLOW_LAST_RUN_ID|last_run_id|worktree" README.md ARCHITECTURE.md devlog/DEVLOG.md` and `uv run python -m pytest -q tests/test_config.py -k "lifecycle"` |

## Assumptions And Defaults

- The starting branch already includes the `AFLOW_LAST_RUN_ID` / `.aflow/last_run_id` work described in `plans/in-progress/optional-skill-selection-and-aflow-assistant.md`.
- This handoff covers only worktree workflows, defined as workflows whose lifecycle setup includes both `worktree` and `branch`.
- "Same command" is defined as the same resolved invocation, not the same raw argv spelling. The resolved match fields are: `repo_root`, `workflow_name`, absolute `plan_path`, effective team, resolved `selected_start_step`, effective `max_turns`, and exact `extra_instructions`.
- Resume candidate lookup uses `AFLOW_LAST_RUN_ID` first and `.aflow/last_run_id` second.
- Resume prompts are interactive-only. In non-interactive contexts, the engine keeps today’s fresh-run behavior instead of adding a new flag or failure mode.
- A new resumed run still creates a new run directory and new turn artifacts; it does not append to the old run’s artifact tree.
- The current plan file on disk remains the source of truth for checkpoint progress. Previous `run.json` snapshots are advisory evidence only.
- Completed previous runs, including runs that only failed during merge after the plan was already complete, are not resumable through this feature.
- Existing docs sections in `README.md`, `ARCHITECTURE.md`, and `devlog/DEVLOG.md` are sufficient to document this behavior without introducing new top-level doc files.
