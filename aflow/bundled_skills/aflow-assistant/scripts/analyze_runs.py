#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


AFLOW_STOP_RE = re.compile(r"^AFLOW_STOP:\s*(.+?)\s*$", re.MULTILINE)

TEXT_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("blocked_review_precondition", re.compile(r"Blocked on a required `aflow-review-checkpoint` precondition\.", re.IGNORECASE)),
    ("branch_mismatch_review_block", re.compile(r"current checkout is .*requires me to stop and escalate|Plan Branch.*current checkout is", re.IGNORECASE | re.DOTALL)),
    ("needs_human_direction", re.compile(r"Need your direction on one point|Choose one:\s*$", re.IGNORECASE | re.MULTILINE)),
    ("original_plan_missing", re.compile(r"original plan file is missing after the turn", re.IGNORECASE)),
    ("dirty_merge_verification", re.compile(r"merge verification cannot be completed safely|feature worktree and primary checkout are dirty", re.IGNORECASE)),
)

HIGHLIGHT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^AFLOW_STOP:\s*.+$", re.MULTILINE),
    re.compile(r"^Blocked on a required .+$", re.MULTILINE),
    re.compile(r"^Need your direction on one point.*$", re.MULTILINE),
    re.compile(r"^The original plan .*current checkout is .*$", re.MULTILINE),
    re.compile(r"^.*original plan file is missing after the turn.*$", re.MULTILINE),
    re.compile(r"^.*merge verification cannot be completed safely.*$", re.MULTILINE),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministically extract high-signal facts from aflow run logs. "
            "Repo-root and runs-root input analyze one run by default."
        )
    )
    root = parser.add_mutually_exclusive_group()
    root.add_argument("--repo-root", type=Path, help="Repository root containing .aflow/runs")
    root.add_argument("--runs-root", type=Path, help="Path to a .aflow/runs directory")
    parser.add_argument("--run", type=Path, help="Analyze exactly one run directory by path")
    parser.add_argument("--run-id", help="Analyze exactly one run by run-id inside the selected runs root")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Analyze a run corpus instead of a single run. Without this flag, the latest substantive run is selected.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of run directories to include in corpus mode (default: 20).",
    )
    parser.add_argument(
        "--include-noise",
        action="store_true",
        help="Include low-signal test noise runs instead of filtering them out.",
    )
    args = parser.parse_args()
    if args.run is not None and args.run_id is not None:
        parser.error("--run and --run-id cannot be combined")
    if args.run is not None and (args.repo_root is not None or args.runs_root is not None):
        parser.error("--run cannot be combined with --repo-root or --runs-root")
    if args.all and args.run is not None:
        parser.error("--all cannot be combined with --run")
    if args.all and args.run_id is not None:
        parser.error("--all cannot be combined with --run-id")
    return args


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def relpath(path: Path, base: Path | None) -> str:
    if base is None:
        return str(path)
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def extract_aflow_stop(text: str) -> list[str]:
    return [match.group(1).strip() for match in AFLOW_STOP_RE.finditer(text)]


def extract_text_signals(text: str) -> list[str]:
    found: list[str] = []
    for name, pattern in TEXT_SIGNAL_PATTERNS:
        if pattern.search(text):
            found.append(name)
    return found


def snapshot_signature(snapshot: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if snapshot is None:
        return None
    return (
        snapshot.get("current_checkpoint_index"),
        snapshot.get("current_checkpoint_name"),
        snapshot.get("current_checkpoint_unchecked_step_count"),
        snapshot.get("is_complete"),
        snapshot.get("total_checkpoint_count"),
        snapshot.get("unchecked_checkpoint_count"),
    )


def is_noise_run(run_json: dict[str, Any]) -> bool:
    return (
        run_json.get("workflow_name") == "other"
        and run_json.get("turns_completed") == 0
        and run_json.get("end_reason") == "already_complete"
    )


def resolve_runs_root(args: argparse.Namespace) -> Path:
    if args.runs_root is not None:
        runs_root = args.runs_root.resolve()
    else:
        repo_root = (args.repo_root or Path.cwd()).resolve()
        runs_root = repo_root / ".aflow" / "runs"
    if not runs_root.is_dir():
        raise SystemExit(f"Runs root does not exist: {runs_root}")
    return runs_root


def collect_run_dirs(runs_root: Path) -> list[Path]:
    return sorted(path for path in runs_root.iterdir() if path.is_dir())


def find_latest_run_dir(
    run_dirs: list[Path],
    *,
    include_noise: bool,
) -> tuple[Path, int]:
    skipped_noise = 0
    for run_dir in reversed(run_dirs):
        run_json_path = run_dir / "run.json"
        if not run_json_path.is_file():
            continue
        run_json = load_json(run_json_path)
        if is_noise_run(run_json) and not include_noise:
            skipped_noise += 1
            continue
        return run_dir, skipped_noise
    if skipped_noise:
        raise SystemExit("No substantive runs found under the selected runs root; rerun with --include-noise if you want test-noise runs.")
    raise SystemExit("No runs with run.json were found under the selected runs root.")


def resolve_analysis_target(args: argparse.Namespace) -> dict[str, Any]:
    if args.run is not None:
        run_dir = args.run.resolve()
        if not (run_dir / "run.json").is_file():
            raise SystemExit(f"Run directory does not contain run.json: {run_dir}")
        return {
            "mode": "single_run",
            "runs_root": None,
            "run_dirs": [run_dir],
            "selection": "explicit_run_path",
            "skipped_noise": 0,
        }

    runs_root = resolve_runs_root(args)
    run_dirs = collect_run_dirs(runs_root)

    if args.run_id is not None:
        run_dir = runs_root / args.run_id
        if not (run_dir / "run.json").is_file():
            raise SystemExit(f"Run id was not found under {runs_root}: {args.run_id}")
        return {
            "mode": "single_run",
            "runs_root": runs_root,
            "run_dirs": [run_dir],
            "selection": "explicit_run_id",
            "skipped_noise": 0,
        }

    if args.all:
        if args.limit is not None and args.limit > 0:
            run_dirs = run_dirs[-args.limit :]
        return {
            "mode": "corpus",
            "runs_root": runs_root,
            "run_dirs": run_dirs,
            "selection": "corpus",
            "skipped_noise": 0,
        }

    latest_run_dir, skipped_noise = find_latest_run_dir(run_dirs, include_noise=args.include_noise)
    return {
        "mode": "single_run",
        "runs_root": runs_root,
        "run_dirs": [latest_run_dir],
        "selection": "latest_run",
        "skipped_noise": skipped_noise,
    }


def load_turns(run_dir: Path) -> list[dict[str, Any]]:
    turns_dir = run_dir / "turns"
    if not turns_dir.is_dir():
        return []
    turn_dirs = sorted(path for path in turns_dir.iterdir() if path.is_dir())
    turns: list[dict[str, Any]] = []
    for turn_dir in turn_dirs:
        result_path = turn_dir / "result.json"
        if not result_path.is_file():
            continue
        payload = load_json(result_path)
        payload["_turn_dir"] = turn_dir
        turns.append(payload)
    return turns


def read_turn_stream(turn_dir: Path, turn: dict[str, Any], *, filename: str, inline_key: str) -> str:
    text = read_text(turn_dir / filename)
    if text:
        return text
    inline_text = turn.get(inline_key)
    if isinstance(inline_text, str):
        return inline_text
    return ""


def summarize_text_lines(text: str) -> list[str]:
    highlights: list[str] = []
    seen: set[str] = set()
    for pattern in HIGHLIGHT_PATTERNS:
        for match in pattern.finditer(text):
            line = " ".join(match.group(0).strip().split())
            if line and line not in seen:
                highlights.append(line)
                seen.add(line)
    return highlights


def summarize_reason_text(reason: str | None) -> list[str]:
    if not reason:
        return []
    lines = [" ".join(line.strip().split()) for line in str(reason).splitlines() if line.strip()]
    if not lines:
        return []
    return [lines[0]]


def analyze_progress_tail(turns: list[dict[str, Any]]) -> dict[str, Any]:
    finalized_turns = list(turns)
    while finalized_turns and (
        finalized_turns[-1].get("snapshot_after") is None
        or finalized_turns[-1].get("status") == "starting"
    ):
        finalized_turns.pop()

    unchanged_tail: list[dict[str, Any]] = []
    for turn in reversed(finalized_turns):
        before_sig = snapshot_signature(turn.get("snapshot_before"))
        after_sig = snapshot_signature(turn.get("snapshot_after"))
        if before_sig is None or after_sig is None or before_sig != after_sig:
            break
        unchanged_tail.append(turn)
    unchanged_tail.reverse()
    step_names = [turn.get("step_name") for turn in unchanged_tail if turn.get("step_name")]
    turn_numbers = [turn.get("turn_number") for turn in unchanged_tail if turn.get("turn_number") is not None]
    alternating_two_step = len(set(step_names)) == 2 if step_names else False
    return {
        "unchanged_snapshot_turns": len(unchanged_tail),
        "alternating_two_step_tail": alternating_two_step,
        "tail_step_names": step_names,
        "tail_turn_numbers": turn_numbers,
        "tail_start_turn": turn_numbers[0] if turn_numbers else None,
        "tail_end_turn": turn_numbers[-1] if turn_numbers else None,
    }


def analyze_turn(turn: dict[str, Any]) -> dict[str, Any]:
    turn_dir = Path(turn["_turn_dir"])
    stdout_text = read_turn_stream(turn_dir, turn, filename="stdout.txt", inline_key="stdout")
    stderr_text = read_turn_stream(turn_dir, turn, filename="stderr.txt", inline_key="stderr")
    combined_text = "\n".join(part for part in (stdout_text, stderr_text) if part)

    signals = set(extract_text_signals(combined_text))
    if turn.get("status") == "plan-invalid":
        signals.add("plan_invalid")
        if "inconsistent checkpoint state" in str(turn.get("error", "")).lower():
            signals.add("inconsistent_checkpoint_state")
    if turn.get("status") == "retry-scheduled":
        signals.add("retry_scheduled")
        signals.add("inconsistent_checkpoint_state")
    if turn.get("returncode") not in (None, 0):
        signals.add("nonzero_returncode")

    aflow_stop_messages = extract_aflow_stop(stdout_text) + extract_aflow_stop(stderr_text)
    highlights = summarize_text_lines(combined_text)
    if turn.get("error"):
        highlights = summarize_reason_text(str(turn.get("error"))) + highlights

    before_sig = snapshot_signature(turn.get("snapshot_before"))
    after_sig = snapshot_signature(turn.get("snapshot_after"))
    snapshot_changed = before_sig is not None and after_sig is not None and before_sig != after_sig
    snapshot_unchanged = before_sig is not None and before_sig == after_sig

    return {
        "aflow_stop_messages": aflow_stop_messages,
        "artifact_paths": {
            "result_json": str(turn_dir / "result.json"),
            "stderr": str(turn_dir / "stderr.txt"),
            "stdout": str(turn_dir / "stdout.txt"),
        },
        "chosen_transition": turn.get("chosen_transition"),
        "conditions": turn.get("conditions"),
        "error": turn.get("error"),
        "finished_at": turn.get("finished_at"),
        "highlights": highlights,
        "returncode": turn.get("returncode"),
        "selector": turn.get("selector"),
        "signals": sorted(signals),
        "snapshot_changed": snapshot_changed,
        "snapshot_unchanged": snapshot_unchanged,
        "started_at": turn.get("started_at"),
        "status": turn.get("status"),
        "step_name": turn.get("step_name"),
        "turn_number": turn.get("turn_number"),
    }


def classify_outcome(run_json: dict[str, Any], signals: set[str], last_turn_status: str | None) -> str:
    if "merge_failure" in signals:
        return "merge_failure"
    if last_turn_status == "plan-invalid":
        return "plan_invalid"
    if last_turn_status == "retry-scheduled":
        return "retry_scheduled"
    if "workflow_failure" in signals:
        return "workflow_failure"
    if "interrupted_or_abandoned" in signals:
        return "interrupted_or_abandoned"
    if "no_progress_tail" in signals:
        return "running_no_progress"
    status = run_json.get("status")
    if isinstance(status, str) and status:
        return status
    return "unknown"


def summarize_focus_turns(
    analyzed_turns: list[dict[str, Any]],
    progress: dict[str, Any],
    *,
    include_progress_context: bool,
) -> list[dict[str, Any]]:
    if not analyzed_turns:
        return []

    focus_numbers: set[int] = set()
    latest_turn_number = analyzed_turns[-1].get("turn_number")
    finalized_turns = [turn for turn in analyzed_turns if turn.get("status") != "starting"]
    last_finalized_number = finalized_turns[-1].get("turn_number") if finalized_turns else None
    for turn in analyzed_turns:
        turn_number = turn.get("turn_number")
        if turn_number is None:
            continue
        if turn["signals"]:
            focus_numbers.add(turn_number)
        if turn["status"] not in ("running", "completed"):
            focus_numbers.add(turn_number)
        if turn["returncode"] not in (None, 0):
            focus_numbers.add(turn_number)
        if turn["chosen_transition"] == "END":
            focus_numbers.add(turn_number)

    for turn_number in (
        latest_turn_number,
        last_finalized_number,
    ):
        if isinstance(turn_number, int):
            focus_numbers.add(turn_number)

    if include_progress_context or latest_turn_number != last_finalized_number:
        progress_turn_numbers = [turn["turn_number"] for turn in analyzed_turns if turn["snapshot_changed"]]
        last_progress_number = progress_turn_numbers[-1] if progress_turn_numbers else None
        for turn_number in (
            last_progress_number,
            progress.get("tail_start_turn"),
            progress.get("tail_end_turn"),
        ):
            if isinstance(turn_number, int):
                focus_numbers.add(turn_number)

    by_number = {turn["turn_number"]: turn for turn in analyzed_turns if isinstance(turn.get("turn_number"), int)}
    focus_turns = [by_number[number] for number in sorted(focus_numbers) if number in by_number]
    return focus_turns


def compact_turn(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "aflow_stop_messages": turn["aflow_stop_messages"],
        "artifact_paths": turn["artifact_paths"],
        "chosen_transition": turn["chosen_transition"],
        "conditions": turn["conditions"],
        "error": turn["error"],
        "finished_at": turn["finished_at"],
        "highlights": turn["highlights"],
        "returncode": turn["returncode"],
        "selector": turn["selector"],
        "signals": turn["signals"],
        "started_at": turn["started_at"],
        "status": turn["status"],
        "step_name": turn["step_name"],
        "turn_number": turn["turn_number"],
    }


def summarize_run(run_dir: Path, run_json: dict[str, Any], turns: list[dict[str, Any]], base: Path | None) -> dict[str, Any]:
    signals: set[str] = set()
    notes: list[str] = []
    aflow_stop_messages: list[str] = []

    if run_json.get("failure_reason"):
        signals.add("workflow_failure")
        if "original plan file is missing after the turn" in str(run_json["failure_reason"]):
            signals.add("original_plan_missing")

    if run_json.get("merge_failure_reason"):
        signals.add("merge_failure")
        if "dirty" in str(run_json["merge_failure_reason"]).lower():
            signals.add("dirty_merge_verification")

    if "inconsistent checkpoint state" in str(run_json.get("startup_recovery_reason", "")).lower():
        signals.add("inconsistent_checkpoint_state")

    last_turn_status = turns[-1].get("status") if turns else None
    if last_turn_status == "starting":
        signals.add("interrupted_or_abandoned")

    progress = analyze_progress_tail(turns)
    if run_json.get("status") == "running" and progress["unchanged_snapshot_turns"] >= 3:
        signals.add("no_progress_tail")
        if progress["alternating_two_step_tail"]:
            signals.add("alternating_review_execute_loop")

    analyzed_turns = [analyze_turn(turn) for turn in turns]
    for turn in analyzed_turns:
        signals.update(turn["signals"])
        aflow_stop_messages.extend(turn["aflow_stop_messages"])
        if turn["signals"]:
            notes.append(f"turn {turn['turn_number']}: {', '.join(turn['signals'])}")

    last_snapshot = run_json.get("last_snapshot")
    if isinstance(last_snapshot, dict) and last_snapshot.get("is_complete") is False and progress["unchanged_snapshot_turns"] >= 3:
        notes.append("latest snapshots show no checkpoint progress across multiple turns")

    status_counts = Counter(turn.get("status") for turn in turns if turn.get("status"))
    step_counts = Counter(turn.get("step_name") for turn in turns if turn.get("step_name"))
    transition_counts = Counter(turn.get("chosen_transition") for turn in turns if turn.get("chosen_transition"))
    signal_turns = [compact_turn(turn) for turn in analyzed_turns if turn["signals"]]
    focus_turns = [compact_turn(turn) for turn in summarize_focus_turns(
        analyzed_turns,
        progress,
        include_progress_context=(
            (run_json.get("status") == "running" and progress["unchanged_snapshot_turns"] >= 3)
            or last_turn_status == "starting"
        ),
    )]
    changed_snapshot_turns = [turn["turn_number"] for turn in analyzed_turns if turn["snapshot_changed"]]
    outcome = classify_outcome(run_json, signals, last_turn_status)

    summary: dict[str, Any] = {
        "activity": {
            "status_counts": dict(sorted(status_counts.items())),
            "step_counts": dict(sorted(step_counts.items())),
            "transition_counts": dict(sorted(transition_counts.items())),
        },
        "aflow_stop_messages": aflow_stop_messages,
        "current_step_name": run_json.get("current_step_name"),
        "failure": {
            "failure_reason": run_json.get("failure_reason"),
            "failure_reason_highlights": summarize_reason_text(run_json.get("failure_reason")),
            "merge_failure_reason": run_json.get("merge_failure_reason"),
            "merge_failure_reason_highlights": summarize_reason_text(run_json.get("merge_failure_reason")),
            "signals": sorted(signals),
            "startup_recovery_reason": run_json.get("startup_recovery_reason"),
        },
        "focus_turns": focus_turns,
        "last_turn_status": last_turn_status,
        "notes": notes,
        "outcome": {
            "kind": outcome,
            "status": run_json.get("status"),
        },
        "paths": {
            "active_plan_path": run_json.get("active_plan_path"),
            "execution_repo_root": run_json.get("execution_repo_root"),
            "new_plan_path": run_json.get("new_plan_path"),
            "original_plan_path": run_json.get("original_plan_path"),
            "repo_root": run_json.get("repo_root"),
            "run_dir": relpath(run_dir, base),
            "run_json": str(run_dir / "run.json"),
            "worktree_path": run_json.get("worktree_path"),
        },
        "progress": {
            "alternating_two_step_tail": progress["alternating_two_step_tail"],
            "changed_snapshot_turn_count": len(changed_snapshot_turns),
            "last_snapshot_change_turn": changed_snapshot_turns[-1] if changed_snapshot_turns else None,
            "tail_end_turn": progress["tail_end_turn"],
            "tail_start_turn": progress["tail_start_turn"],
            "tail_step_names": progress["tail_step_names"],
            "tail_turn_numbers": progress["tail_turn_numbers"],
            "unchanged_snapshot_turns": progress["unchanged_snapshot_turns"],
        },
        "run_id": run_dir.name,
        "selected_start_step": run_json.get("selected_start_step"),
        "signal_turns": signal_turns,
        "turns_completed": run_json.get("turns_completed"),
        "workflow_name": run_json.get("workflow_name"),
    }
    return summary


def summarize_run_compact(run_dir: Path, run_json: dict[str, Any], turns: list[dict[str, Any]], base: Path | None) -> dict[str, Any]:
    detailed = summarize_run(run_dir, run_json, turns, base)
    return {
        "current_step_name": detailed["current_step_name"],
        "failure": {
            "failure_reason": detailed["failure"]["failure_reason"],
            "merge_failure_reason": detailed["failure"]["merge_failure_reason"],
            "signals": detailed["failure"]["signals"],
        },
        "last_turn_status": detailed["last_turn_status"],
        "outcome": detailed["outcome"],
        "paths": {
            "run_dir": detailed["paths"]["run_dir"],
            "run_json": detailed["paths"]["run_json"],
        },
        "progress": {
            "alternating_two_step_tail": detailed["progress"]["alternating_two_step_tail"],
            "tail_start_turn": detailed["progress"]["tail_start_turn"],
            "unchanged_snapshot_turns": detailed["progress"]["unchanged_snapshot_turns"],
        },
        "run_id": detailed["run_id"],
        "turns_completed": detailed["turns_completed"],
        "workflow_name": detailed["workflow_name"],
    }


def main() -> int:
    args = parse_args()
    target = resolve_analysis_target(args)
    runs_root = target["runs_root"]
    run_dirs = target["run_dirs"]
    base = runs_root.parent.parent if runs_root is not None else None

    if target["mode"] == "single_run":
        run_dir = run_dirs[0]
        run_json = load_json(run_dir / "run.json")
        turns = load_turns(run_dir)
        payload = {
            "analysis_scope": {
                "include_noise": args.include_noise,
                "mode": "single_run",
                "run_count_considered": 1,
                "run_count_skipped_as_noise": target["skipped_noise"],
                "runs_root": str(runs_root) if runs_root is not None else None,
                "selection": target["selection"],
            },
            "run": summarize_run(run_dir, run_json, turns, base),
            "version": 2,
        }
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    analyzed_runs: list[dict[str, Any]] = []
    skipped_noise = 0
    signal_counts: Counter[str] = Counter()

    for run_dir in run_dirs:
        run_json_path = run_dir / "run.json"
        if not run_json_path.is_file():
            continue
        run_json = load_json(run_json_path)
        if is_noise_run(run_json) and not args.include_noise:
            skipped_noise += 1
            continue
        turns = load_turns(run_dir)
        summary = summarize_run_compact(run_dir, run_json, turns, base)
        analyzed_runs.append(summary)
        signal_counts.update(summary["failure"]["signals"])

    payload = {
        "analysis_scope": {
            "include_noise": args.include_noise,
            "mode": "corpus",
            "run_count_considered": len(analyzed_runs),
            "run_count_skipped_as_noise": skipped_noise,
            "runs_root": str(runs_root) if runs_root is not None else None,
            "selection": target["selection"],
        },
        "runs": analyzed_runs,
        "signal_counts": dict(sorted(signal_counts.items())),
        "version": 2,
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
