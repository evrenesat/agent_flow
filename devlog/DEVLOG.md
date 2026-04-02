# DEVLOG

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
