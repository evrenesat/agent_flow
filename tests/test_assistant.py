from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_turn(
    run_dir: Path,
    turn_number: int,
    *,
    result: dict[str, object],
    stdout: str = "",
    stderr: str = "",
) -> None:
    turn_dir = run_dir / "turns" / f"turn-{turn_number:03d}"
    turn_dir.mkdir(parents=True, exist_ok=True)
    _write_json(turn_dir / "result.json", result)
    (turn_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (turn_dir / "stderr.txt").write_text(stderr, encoding="utf-8")


def _snapshot(
    *,
    checkpoint_index: int = 1,
    checkpoint_name: str = "Checkpoint 1: First",
    unchecked_steps: int = 2,
    unchecked_checkpoints: int = 1,
    is_complete: bool = False,
) -> dict[str, object]:
    return {
        "current_checkpoint_index": checkpoint_index,
        "current_checkpoint_name": checkpoint_name,
        "current_checkpoint_unchecked_step_count": unchecked_steps,
        "is_complete": is_complete,
        "total_checkpoint_count": 1,
        "unchecked_checkpoint_count": unchecked_checkpoints,
    }


class AflowAssistantAnalyzerTests(unittest.TestCase):
    def _script_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "aflow" / "bundled_skills" / "aflow-assistant" / "scripts" / "analyze_runs.py"

    def _run_analyzer(self, *args: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, str(self._script_path()), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_analyzer_defaults_to_latest_substantive_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            runs_root = repo_root / ".aflow" / "runs"

            noise_run = runs_root / "20260401T000200Z-noise"
            _write_json(
                noise_run / "run.json",
                {
                    "status": "completed",
                    "workflow_name": "other",
                    "turns_completed": 0,
                    "end_reason": "already_complete",
                },
            )

            merge_run = runs_root / "20260401T000100Z-merge"
            _write_json(
                merge_run / "run.json",
                {
                    "status": "failed",
                    "workflow_name": "medium",
                    "turns_completed": 8,
                    "merge_failure_reason": "AFLOW_STOP: feature worktree and primary checkout are dirty, so merge verification cannot be completed safely",
                    "current_step_name": "review_implementation",
                },
            )
            _write_turn(
                merge_run,
                1,
                result={
                    "turn_number": 1,
                    "step_name": "review_implementation",
                    "status": "completed",
                    "returncode": 0,
                    "chosen_transition": "END",
                    "snapshot_before": _snapshot(),
                    "snapshot_after": _snapshot(is_complete=True, unchecked_steps=0, unchecked_checkpoints=0),
                },
            )

            payload = self._run_analyzer("--repo-root", str(repo_root))
            assert payload["analysis_scope"]["mode"] == "single_run"
            assert payload["analysis_scope"]["selection"] == "latest_run"
            assert payload["analysis_scope"]["run_count_considered"] == 1
            assert payload["analysis_scope"]["run_count_skipped_as_noise"] == 1
            assert payload["run"]["run_id"] == "20260401T000100Z-merge"
            assert payload["run"]["outcome"]["kind"] == "merge_failure"
            assert payload["run"]["failure"]["signals"] == ["dirty_merge_verification", "merge_failure"]
            assert payload["run"]["focus_turns"][0]["turn_number"] == 1

    def test_analyzer_detects_blocked_review_loop_and_interrupted_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            runs_root = repo_root / ".aflow" / "runs"
            blocked_run = runs_root / "20260401T000300Z-blocked"

            _write_json(
                blocked_run / "run.json",
                {
                    "status": "running",
                    "workflow_name": "hard",
                    "turns_completed": 4,
                    "current_step_name": "review_cp_implementation",
                    "last_snapshot": _snapshot(),
                },
            )

            blocked_stdout = (
                "Blocked on a required `aflow-review-checkpoint` precondition.\n\n"
                "The original plan records `Plan Branch: main`, but the current checkout is "
                "`aflow-feature-branch` and the skill requires me to stop and escalate.\n\n"
                "Need your direction on one point:\n"
                "1. Review this branch.\n"
                "2. Switch to main.\n"
            )

            for turn_number, step_name in ((1, "implement_plan"), (2, "review_cp_implementation"), (3, "implement_plan"), (4, "review_cp_implementation")):
                _write_turn(
                    blocked_run,
                    turn_number,
                    result={
                        "turn_number": turn_number,
                        "step_name": step_name,
                        "status": "running",
                        "returncode": 0,
                        "chosen_transition": "implement_plan" if "review" in step_name else "review_cp_implementation",
                        "snapshot_before": _snapshot(),
                        "snapshot_after": _snapshot(),
                    },
                    stdout=blocked_stdout if turn_number == 4 else "",
                )

            _write_turn(
                blocked_run,
                5,
                result={
                    "turn_number": 5,
                    "step_name": "review_cp_implementation",
                    "status": "starting",
                    "snapshot_before": _snapshot(),
                    "snapshot_after": None,
                },
            )

            payload = self._run_analyzer("--repo-root", str(repo_root))
            assert payload["run"]["run_id"] == "20260401T000300Z-blocked"
            assert payload["run"]["failure"]["signals"] == [
                "alternating_review_execute_loop",
                "blocked_review_precondition",
                "branch_mismatch_review_block",
                "interrupted_or_abandoned",
                "needs_human_direction",
                "no_progress_tail",
            ]
            assert payload["run"]["outcome"]["kind"] == "interrupted_or_abandoned"
            assert payload["run"]["progress"]["tail_start_turn"] == 1
            focus_turn_numbers = [turn["turn_number"] for turn in payload["run"]["focus_turns"]]
            assert focus_turn_numbers == [1, 4, 5]

    def test_analyzer_supports_run_id_and_extracts_plan_invalid_focus(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            runs_root = repo_root / ".aflow" / "runs"
            run_dir = runs_root / "20260401T000400Z-invalid"
            _write_json(
                run_dir / "run.json",
                {
                    "status": "failed",
                    "workflow_name": "rir",
                    "turns_completed": 6,
                    "failure_reason": "plans/in-progress/original.md: original plan file is missing after the turn",
                    "current_step_name": "implement_plan",
                    "original_plan_path": "plans/in-progress/original.md",
                    "active_plan_path": "plans/in-progress/fix.md",
                },
            )
            _write_turn(
                run_dir,
                6,
                result={
                    "turn_number": 6,
                    "step_name": "implement_plan",
                    "status": "plan-invalid",
                    "returncode": 0,
                    "error": "plans/in-progress/original.md: original plan file is missing after the turn",
                    "snapshot_before": _snapshot(is_complete=True, unchecked_steps=0, unchecked_checkpoints=0),
                    "snapshot_after": None,
                },
            )

            payload = self._run_analyzer("--repo-root", str(repo_root), "--run-id", "20260401T000400Z-invalid")
            assert payload["analysis_scope"]["selection"] == "explicit_run_id"
            assert payload["analysis_scope"]["runs_root"] == str(runs_root.resolve())
            assert payload["analysis_scope"]["run_count_considered"] == 1
            assert payload["run"]["outcome"]["kind"] == "plan_invalid"
            assert payload["run"]["failure"]["signals"] == ["original_plan_missing", "plan_invalid", "workflow_failure"]
            assert payload["run"]["signal_turns"][0]["turn_number"] == 6
            assert payload["run"]["signal_turns"][0]["artifact_paths"]["result_json"].endswith("turn-006/result.json")

    def test_analyzer_supports_corpus_mode_and_filters_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            runs_root = repo_root / ".aflow" / "runs"

            noise_run = runs_root / "20260401T000000Z-noise"
            _write_json(
                noise_run / "run.json",
                {
                    "status": "completed",
                    "workflow_name": "other",
                    "turns_completed": 0,
                    "end_reason": "already_complete",
                },
            )

            merge_run = runs_root / "20260401T000100Z-merge"
            _write_json(
                merge_run / "run.json",
                {
                    "status": "failed",
                    "workflow_name": "medium",
                    "turns_completed": 8,
                    "merge_failure_reason": "AFLOW_STOP: feature worktree and primary checkout are dirty, so merge verification cannot be completed safely",
                },
            )
            _write_turn(
                merge_run,
                8,
                result={
                    "turn_number": 8,
                    "step_name": "review_implementation",
                    "status": "completed",
                    "returncode": 0,
                    "chosen_transition": "END",
                    "snapshot_before": _snapshot(is_complete=True, unchecked_steps=0, unchecked_checkpoints=0),
                    "snapshot_after": _snapshot(is_complete=True, unchecked_steps=0, unchecked_checkpoints=0),
                },
            )

            invalid_run = runs_root / "20260401T000200Z-invalid"
            _write_json(
                invalid_run / "run.json",
                {
                    "status": "failed",
                    "workflow_name": "rir",
                    "turns_completed": 5,
                    "failure_reason": "plans/in-progress/original.md: original plan file is missing after the turn",
                    "current_step_name": "implement_plan",
                },
            )
            _write_turn(
                invalid_run,
                6,
                result={
                    "turn_number": 6,
                    "step_name": "implement_plan",
                    "status": "plan-invalid",
                    "returncode": 0,
                    "error": "plans/in-progress/original.md: original plan file is missing after the turn",
                    "snapshot_before": _snapshot(),
                    "snapshot_after": None,
                },
            )

            payload = self._run_analyzer("--repo-root", str(repo_root), "--all")
            assert payload["analysis_scope"]["mode"] == "corpus"
            assert payload["analysis_scope"]["run_count_considered"] == 2
            assert payload["analysis_scope"]["run_count_skipped_as_noise"] == 1
            runs = {run["run_id"]: run for run in payload["runs"]}
            assert runs["20260401T000100Z-merge"]["failure"]["signals"] == ["dirty_merge_verification", "merge_failure"]
            assert runs["20260401T000200Z-invalid"]["failure"]["signals"] == ["original_plan_missing", "plan_invalid", "workflow_failure"]


if __name__ == "__main__":
    unittest.main()
