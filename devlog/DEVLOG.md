# DEVLOG

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
