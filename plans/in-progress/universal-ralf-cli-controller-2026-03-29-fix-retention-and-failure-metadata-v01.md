# Universal RALF CLI Controller Fix Plan: Retention Ordering And Failure Metadata

## Objective

Finish the universal RALF controller so it matches the original handoff's logging and reviewability requirements. After this fix, run retention must keep the true newest `20` run directories even when multiple runs start in the same second, and `run.json` must record the same terminal snapshot the failure summary reports when a harness exits non-zero after modifying the plan.

## Done Means

- Starting a new run still creates `.ralf/runs/<run-id>/` with the same artifact layout as the current implementation.
- When more than `20` run directories exist, pruning removes the true oldest historical runs rather than using the random UUID suffix as a tie-breaker for runs created in the same second.
- A non-zero harness exit still fails the controller immediately, but `run.json` now records the post-turn plan snapshot that the failure summary and turn artifact describe.
- Automated coverage proves both behaviors:
  - retention is stable for multiple same-second run IDs created in known order
  - non-zero harness exits after plan mutation produce a `run.json` snapshot that matches the on-disk post-turn plan state
- No unrelated files outside the universal-controller scope are included in the final handoff commit.

## Files

- Modify `/Users/evren/code/agent_flow/extensions/ralf_universal/runlog.py`
- Modify `/Users/evren/code/agent_flow/extensions/ralf_universal/controller.py`
- Modify `/Users/evren/code/agent_flow/tests/test_ralf_universal.py`
- Do not modify `/Users/evren/code/agent_flow/README.md`
- Do not modify `/Users/evren/code/agent_flow/.gitignore`
- Do not modify anything under `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/`
- Do not modify anything under `/Users/evren/code/agent_flow/opencode-ralph/`
- Do not modify `/Users/evren/code/agent_flow/scripts/ralf`
- Do not modify `/Users/evren/code/agent_flow/scripts/ralf_offf.sh`

## Before / After

- `runlog.py` before:
  - retention sorts run directories by name only
  - two runs created in the same second are ordered by the random suffix, not by actual creation order
- `runlog.py` after:
  - retention uses a deterministic oldest-first ordering based on real filesystem age, with name only as a stable fallback when ages match exactly
  - pruning still deletes whole run directories and never truncates files inside a run
- `controller.py` before:
  - on non-zero harness exit, the failure summary is built from the post-turn parsed plan snapshot
  - `run.json` writes `last_snapshot` from the pre-turn controller state instead, so metadata can disagree with the failure summary
- `controller.py` after:
  - the non-zero-exit path writes the post-turn snapshot into `run.json`, keeping the durable metadata aligned with the failure summary and turn artifact
- `test_ralf_universal.py` before:
  - retention coverage only checks lexicographic timestamp ordering
  - non-zero-exit coverage does not mutate the plan before failure, so the stale-snapshot bug stays invisible
- `test_ralf_universal.py` after:
  - one test creates multiple same-second run IDs in known creation order and proves pruning keeps the actual newest ones
  - one test forces a failing harness to update the plan first and then verifies `run.json["last_snapshot"]` matches the post-turn plan state

## Assumptions

- Python 3.11+ is available, matching the current `unittest -k` usage in the original plan.
- We can rely on filesystem metadata for ordering historical run directories created on the same machine during normal controller usage.
- The fix should preserve the current public CLI and run artifact layout.

## Constraints

- Do not add any new third-party dependencies.
- Do not redesign the run directory naming scheme unless required to make ordering deterministic.
- Do not weaken the current failure behavior for non-zero exits, missing plans, or inconsistent plans.
- Do not revert or delete unrelated user changes in `.idea/`, `plans/*.md`, or `opencode-ralph/README.md.zip` unless the user explicitly asks for that cleanup. Keep them out of the final controller commit instead.

## Edge Cases

- Two or more run directories share the same timestamp prefix down to the second.
- A harness exits non-zero after partially or fully updating the plan file.
- A harness exits non-zero without changing the plan file, existing behavior must remain correct.
- Retention must continue deleting whole directories only.
- The active run directory must never be pruned during its own creation/finalization flow.

## Sequential Steps

1. Confirm the current controller behavior before editing.
   Verification:
   - `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py`
   - `python3 - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from extensions.ralf_universal.runlog import prune_old_runs

with TemporaryDirectory() as td:
    root = Path(td)
    names = ["20260329T120000Z-ffffffff", "20260329T120000Z-00000000", "20260329T120000Z-11111111"]
    for name in names:
        (root / name).mkdir()
    prune_old_runs(root, 2)
    print(sorted(path.name for path in root.iterdir()))
PY`

2. Fix retention ordering in `/Users/evren/code/agent_flow/extensions/ralf_universal/runlog.py`.
   - Replace name-only ordering with true oldest-first ordering based on filesystem age.
   - Keep a stable secondary sort key so results are deterministic when filesystem times are equal.
   - Preserve whole-directory deletion behavior.
   Verification:
   - `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k retention`

3. Fix the non-zero-exit metadata path in `/Users/evren/code/agent_flow/extensions/ralf_universal/controller.py`.
   - In the harness-failed branch, write `last_snapshot=post_snapshot` into `run.json`.
   - Do not change how `turns_completed` is counted for failed turns.
   Verification:
   - `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k non_zero`

4. Expand `/Users/evren/code/agent_flow/tests/test_ralf_universal.py` to lock both behaviors down.
   - Add a retention test that proves same-second runs are pruned by true age, not random suffix order.
   - Update or add a non-zero fake-harness test so the harness mutates the plan before returning a failure code, then assert `run.json["last_snapshot"]` matches the post-turn state.
   Verification:
   - `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py`

5. Re-run the original handoff verification relevant to the touched files.
   Verification:
   - `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py`
   - `python3 -m py_compile /Users/evren/code/agent_flow/extensions/ralf_universal/*.py /Users/evren/code/agent_flow/extensions/ralf_universal/harnesses/*.py`
   - `shellcheck /Users/evren/code/agent_flow/scripts/ralf-universal`
   - `git diff --name-only -- /Users/evren/code/agent_flow/codex-ralph-loop-plugin /Users/evren/code/agent_flow/opencode-ralph /Users/evren/code/agent_flow/scripts/ralf /Users/evren/code/agent_flow/scripts/ralf_offf.sh`

6. Isolate the final handoff scope before commit.
   - Review `git status --short` and confirm only the intended controller files are staged for the fix commit.
   - If unrelated dirty files are still present, leave them unstaged or move them into a separate user-owned change.
   Verification:
   - `git status --short`
   - `git diff --cached --name-only`

## Final Checklist

- [ ] Retention keeps the true newest `20` run directories even for same-second run IDs.
- [ ] `run.json` snapshot matches the terminal summary on non-zero harness exit after plan mutation.
- [ ] All universal-controller tests pass.
- [ ] Python files compile cleanly.
- [ ] `scripts/ralf-universal` still passes `shellcheck`.
- [ ] No plugin runtime files were modified.
- [ ] No unrelated worktree noise is included in the final fix commit.
