# DEVLOG

## 2026-04-02 — Runlog one-run-dir-per-invocation invariant verification

### What changed

- **`tests/test_aflow.py`**: Added `RunlogSingleRunDirTests` with three tests that verify the one-run-dir-per-invocation invariant: (1) a multi-step workflow with multiple turns produces exactly one run directory, (2) turn-start artifacts (user-prompt.txt etc.) exist before the harness completes under the current run's turns/ directory, and (3) no sibling run directory with an empty turns/ appears at any point during the run.

- **`README.md`**: Updated the "Run Logs" section to explicitly state the one-run-dir-per-invocation invariant and that turn directories are created before harness launch and finalized in-place.

### Gotchas

- No code bug was found in the current implementation. `create_run_paths()` is called exactly once in `run_workflow()`, and `write_turn_artifacts_start()` always uses the existing `RunPaths` object. The reported symptom (sibling run dirs with empty turns/ and only run.json) could not be reproduced from the current codebase. The tests were added to document and protect the invariant going forward.

## 2026-04-02 — AFLOW_STOP sentinel and same-step cap guardrails

### What changed

- **`aflow/workflow.py`**: Added `_detect_stop_marker(stdout, stderr)` which scans both output streams line-by-line for a line starting with the exact prefix `AFLOW_STOP:`. When found, `run_workflow()` fails the current turn immediately before plan reload or transition selection, writes a failed `run.json`, and raises `WorkflowError` with the extracted reason. A blank reason falls back to a fixed message.

  Also added the multi-step consecutive same-step cap. After each successful turn selects its next transition target, the engine checks whether the same step has been selected `max_same_step_turns` times in a row. On limit, it fails with a clear message naming the step and the count. The check is skipped for single-step workflows, for `END` transitions, and when `max_same_step_turns = 0`. Streak tracking fields (`consec_step_name`, `consec_step_count`) live on `ControllerState`.

- **`aflow/config.py`**: Added `max_same_step_turns: int = 5` to `AflowSection`. Parser validates non-negative integers and rejects booleans. Added `DEFAULT_MAX_SAME_STEP_TURNS = 5` constant.

- **`aflow/run_state.py`**: Added `consec_step_name: str | None = None` and `consec_step_count: int = 0` to `ControllerState` for same-step streak tracking.

- **`aflow/aflow.toml`**: Added `max_same_step_turns = 5` with an explanatory comment under `[aflow]`.

- **`aflow/bundled_skills/aflow-execute-plan/SKILL.md`** and **`aflow/bundled_skills/aflow-execute-checkpoint/SKILL.md`**: Added documentation that irrecoverable blockers must emit `AFLOW_STOP: <reason>` on its own line so the engine stops immediately instead of looping.

- **`aflow/bundled_skills/aflow-plan/SKILL.md`**: Updated the checkpoint skeleton's "Stop and Escalate If" instruction to tell plan authors to document the `AFLOW_STOP: <reason>` contract for implementers.

- **`tests/test_aflow.py`**: Added `SameStepCapConfigTests` (5 tests), `SameStepCapWorkflowTests` (5 tests), and `StopMarkerTests` (7 tests). 17 new tests total.

- **`README.md`** and **`ARCHITECTURE.md`**: Documented `max_same_step_turns`, the same-step cap behavior, and updated the workflow engine description.

### Gotchas

- The same-step cap check happens AFTER transition selection and BEFORE the next turn starts. If the cap triggers, the turn that caused the failure has already been finalized as completed, so the failure appears as a run-level error, not a turn-level artifact.
- Single-step workflows (like `ralph`) are intentionally excluded from the cap regardless of `max_same_step_turns`. The check uses `len(wf.steps) > 1`.
- The streak resets to zero when a DIFFERENT step is selected, not just when it executes. If the next turn picks a different step, `consec_step_count` resets before that turn runs.

## 2026-04-02 — Retry inconsistent checkpoint state

### What changed

- **`aflow/plan.py`**: Added `error_kind: str | None = None` to `PlanParseError`. Set `error_kind="inconsistent_checkpoint_state"` on the inconsistent-heading failure path so the workflow engine can detect this specific error class without string-matching.

- **`aflow/config.py`**: Added `retry_inconsistent_checkpoint_state: int = 0` to `AflowSection` and `retry_inconsistent_checkpoint_state: int | None = None` to `WorkflowConfig`. Both parsers validate non-negative integers and reject booleans. The workflow-level key is parsed directly under `[workflow.<name>]` alongside `steps`.

- **`aflow/run_state.py`**: Added `RetryContext` frozen dataclass holding everything needed to replay a failed turn (step name, profile, resolved harness/model/effort, pre-failure snapshot, saved plan paths, base prompt, parse error string, attempt, retry limit). Added `pending_retry: RetryContext | None = None` to `ControllerState`.

- **`aflow/workflow.py`**: Added `_effective_retry_limit()` (workflow override takes precedence over global), `_build_retry_appendix()` (fixed instruction + exact parse error), and modified the turn loop to support two modes. Normal turns work as before. When the post-turn parse fails with `inconsistent_checkpoint_state`, exit code 0, and retries remain with another turn available, the engine saves a `RetryContext`, increments `turns_completed` and `issues_accumulated`, writes a `retry-scheduled` turn artifact, and continues. The next loop iteration enters retry mode: it skips the pre-turn plan reload, rebuilds the invocation from saved context plus the retry appendix, and on success clears `pending_retry` and resumes normal transition selection.

- **`aflow/runlog.py`**: Extended `write_turn_artifacts` with `retry_attempt`, `retry_limit`, `retry_reason`, `retry_next_turn`, `was_retry` keyword args. Extended `write_run_metadata` with a `pending_retry` param that adds `pending_retry_step_name`, `pending_retry_attempt`, `pending_retry_limit`, `pending_retry_reason` to `run.json` when a retry is pending.

- **`aflow/aflow.toml`**: Added `retry_inconsistent_checkpoint_state = 0` under `[aflow]`.

- **`tests/test_aflow.py`**: Added three new test classes — `RetryInconsistentCheckpointConfigTests` (9 config parsing tests), `RetryInconsistentCheckpointPlanTests` (2 error_kind tests), `RetryInconsistentCheckpointWorkflowTests` (8 workflow behavior tests), `RetryInconsistentCheckpointArtifactTests` (3 artifact tests). 22 new tests total.

- **`README.md`** and **`ARCHITECTURE.md`**: documented the new config key, workflow override, retry semantics, and updated `RetryContext` in the data-classes section.

### Gotchas

- `WorkflowConfig` is a frozen dataclass, so `dataclasses.replace()` was needed to attach the workflow-level retry value after `_parse_workflow_steps()` returned.
- The retry turn does not call `generate_new_plan_path()` again. The saved `new_plan_path` from the failed turn is reused, so the model sees the same path in both the failed and retry prompts.
- When the last allowed turn fails with the retryable error, no retry is scheduled because `turn_number < config.max_turns` is false.

## 2026-04-02 — Live banner refresh and git since-start stats

### What changed

- **`aflow/git_status.py`** (new): git snapshot helpers. `probe_worktree` checks dirty state at startup using `git status --porcelain`. `capture_baseline` snapshots the current HEAD SHA and working-tree OID using a temporary `GIT_INDEX_FILE` (never touches the real index). `summarize_since_baseline` compares the current working tree to the baseline using `git diff --name-status --no-renames` and `git diff --numstat`. All three return `None` on any git error so the workflow always runs.

- **`aflow/status.py`**: `BannerRenderer` now owns a daemon background thread that rebuilds and pushes the panel every `refresh_interval_seconds` (default 1 s) and polls git every `git_poll_interval_seconds` (default 10 s). This means elapsed time advances in real time during long steps without waiting for a step transition. Added `set_context(...)` to replace direct mutation of private fields. Added `git_summary` param to `build_banner()` which renders `Git` and `Files` rows.

- **`aflow/workflow.py`**: passes `config.repo_root` into `BannerRenderer` and replaces the five direct private-field assignments with a single `banner.set_context(...)` call before each turn.

- **`aflow/cli.py`**: calls `probe_worktree` before starting the workflow. In interactive TTY mode, prompts with a default-No confirmation. In non-interactive mode, prints an error and exits with code 1.

- **`README.md`** and **`ARCHITECTURE.md`**: documented new banner behavior, git rows, dirty-worktree gating, and added `git_status.py` to the module breakdown.

- **`tests/test_aflow.py`**: added `GitStatusTests` (13 tests using a real temp git repo), `GitBannerTests` (5 banner render tests), `DirtyWorktreeCliTests` (3 CLI tests). Fixed `test_cli_workflow_override` to patch `probe_worktree` so it doesn't fail against the dirty project repo.

### Gotchas

- `git add -A` with `GIT_INDEX_FILE` pointing to an empty file (from `mkstemp`) fails. Fixed by using `tempfile.TemporaryDirectory()` and pointing to a non-existing file path within it — git creates the index from scratch.
- `BannerRenderer` uses `auto_refresh=False` on `Live` to avoid Rich's own refresh thread fighting with ours.
- The background thread uses `stop_event.wait(timeout=...)` rather than `time.sleep` so `pause()`/`stop()` wake it up immediately.
