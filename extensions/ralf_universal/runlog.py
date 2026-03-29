from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
from uuid import uuid4

from .plan import PlanSnapshot
from .run_state import ControllerConfig, ControllerState
from .harnesses.base import HarnessInvocation


@dataclass(frozen=True)
class RunPaths:
    repo_root: Path
    runs_root: Path
    run_dir: Path
    turns_dir: Path
    run_json: Path


def _utc_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


def _json_dump(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(_json_dump(payload), encoding="utf-8")


def create_run_paths(config: ControllerConfig) -> RunPaths:
    runs_root = config.repo_root / ".ralf" / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dir = runs_root / _utc_run_id()
    turns_dir = run_dir / "turns"
    turns_dir.mkdir(parents=True, exist_ok=False)
    run_json = run_dir / "run.json"
    paths = RunPaths(
        repo_root=config.repo_root,
        runs_root=runs_root,
        run_dir=run_dir,
        turns_dir=turns_dir,
        run_json=run_json,
    )
    prune_old_runs(runs_root, config.keep_runs)
    return paths


def _run_dir_sort_key(path: Path) -> tuple[int, str]:
    stat_result = path.stat()
    return (stat_result.st_mtime_ns, path.name)


def prune_old_runs(runs_root: Path, keep_runs: int) -> None:
    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    run_dirs.sort(key=_run_dir_sort_key)
    while len(run_dirs) > keep_runs:
        doomed = run_dirs.pop(0)
        shutil.rmtree(doomed)


def _snapshot_payload(snapshot: PlanSnapshot | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return snapshot.to_dict()


def write_run_metadata(
    paths: RunPaths,
    config: ControllerConfig,
    state: ControllerState | None,
    *,
    status: str,
    failure_reason: str | None = None,
    last_snapshot: PlanSnapshot | None = None,
    turns_completed: int | None = None,
) -> None:
    payload: dict[str, object] = {
        "repo_root": str(paths.repo_root),
        "run_dir": str(paths.run_dir),
        "status": status,
        "plan_path": str(config.plan_path),
        "harness": config.harness,
        "model": config.model,
        "max_turns": config.max_turns,
        "stagnation_limit": config.stagnation_limit,
        "keep_runs": config.keep_runs,
        "extra_instructions": list(config.extra_instructions),
        "turns_completed": turns_completed if turns_completed is not None else (state.turns_completed if state else 0),
        "stagnation_turns": state.stagnation_turns if state else 0,
        "last_snapshot": _snapshot_payload(last_snapshot if last_snapshot is not None else (state.last_snapshot if state else None)),
    }
    if failure_reason is not None:
        payload["failure_reason"] = failure_reason
    _write_json(paths.run_json, payload)


def write_turn_artifacts(
    paths: RunPaths,
    *,
    turn_number: int,
    invocation: HarnessInvocation,
    stdout: str,
    stderr: str,
    returncode: int,
    snapshot_before: PlanSnapshot,
    snapshot_after: PlanSnapshot | None,
    status: str,
    error: str | None = None,
) -> Path:
    turn_dir = paths.turns_dir / f"turn-{turn_number:03d}"
    turn_dir.mkdir(parents=False, exist_ok=False)
    (turn_dir / "system-prompt.txt").write_text(invocation.system_prompt, encoding="utf-8")
    (turn_dir / "user-prompt.txt").write_text(invocation.user_prompt, encoding="utf-8")
    (turn_dir / "effective-prompt.txt").write_text(invocation.effective_prompt, encoding="utf-8")
    _write_json(turn_dir / "argv.json", {"argv": list(invocation.argv), "label": invocation.label, "prompt_mode": invocation.prompt_mode})
    _write_json(turn_dir / "env.json", {"env": dict(invocation.env)})
    (turn_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (turn_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    result_payload: dict[str, object] = {
        "turn_number": turn_number,
        "label": invocation.label,
        "returncode": returncode,
        "status": status,
        "snapshot_before": snapshot_before.to_dict(),
        "snapshot_after": _snapshot_payload(snapshot_after),
    }
    if error is not None:
        result_payload["error"] = error
    _write_json(turn_dir / "result.json", result_payload)
    return turn_dir
