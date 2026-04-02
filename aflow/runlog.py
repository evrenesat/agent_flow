from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
from uuid import uuid4

from .plan import PlanSnapshot
from .run_state import ControllerConfig, ControllerState, RetryContext, WorkflowEndReason
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
    runs_root = config.repo_root / ".aflow" / "runs"
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


def _turn_result_payload(
    *,
    turn_number: int,
    invocation: HarnessInvocation,
    snapshot_before: PlanSnapshot,
    snapshot_after: PlanSnapshot | None,
    status: str,
    started_at: datetime,
    finished_at: datetime | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    returncode: int | None = None,
    step_name: str | None = None,
    selector: str | None = None,
    original_plan_path: Path | None = None,
    active_plan_path: Path | None = None,
    new_plan_path: Path | None = None,
    conditions: dict[str, bool] | None = None,
    chosen_transition: str | None = None,
    end_reason: WorkflowEndReason | None = None,
    error: str | None = None,
    retry_attempt: int | None = None,
    retry_limit: int | None = None,
    retry_reason: str | None = None,
    retry_next_turn: bool | None = None,
    was_retry: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "turn_number": turn_number,
        "label": invocation.label,
        "status": status,
        "snapshot_before": snapshot_before.to_dict(),
        "snapshot_after": _snapshot_payload(snapshot_after),
        "started_at": started_at.isoformat(),
    }
    if stdout is not None:
        payload["stdout"] = stdout
    if stderr is not None:
        payload["stderr"] = stderr
    if returncode is not None:
        payload["returncode"] = returncode
    if finished_at is not None:
        payload["finished_at"] = finished_at.isoformat()
    if step_name is not None:
        payload["step_name"] = step_name
    if selector is not None:
        payload["selector"] = selector
    if original_plan_path is not None:
        payload["original_plan_path"] = str(original_plan_path)
    if active_plan_path is not None:
        payload["active_plan_path"] = str(active_plan_path)
    if new_plan_path is not None:
        payload["new_plan_path"] = str(new_plan_path)
    if conditions is not None:
        payload["conditions"] = conditions
    if chosen_transition is not None:
        payload["chosen_transition"] = chosen_transition
    if end_reason is not None:
        payload["end_reason"] = end_reason
    if error is not None:
        payload["error"] = error
    if retry_attempt is not None:
        payload["retry_attempt"] = retry_attempt
    if retry_limit is not None:
        payload["retry_limit"] = retry_limit
    if retry_reason is not None:
        payload["retry_reason"] = retry_reason
    if retry_next_turn is not None:
        payload["retry_next_turn"] = retry_next_turn
    if was_retry is not None:
        payload["was_retry"] = was_retry
    return payload


def write_run_metadata(
    paths: RunPaths,
    config: ControllerConfig,
    state: ControllerState | None,
    *,
    status: str,
    end_reason: WorkflowEndReason | None = None,
    failure_reason: str | None = None,
    last_snapshot: PlanSnapshot | None = None,
    turns_completed: int | None = None,
    workflow_name: str | None = None,
    current_step_name: str | None = None,
    original_plan_path: Path | None = None,
    active_plan_path: Path | None = None,
    new_plan_path: Path | None = None,
    pending_retry: RetryContext | None = None,
) -> None:
    payload: dict[str, object] = {
        "repo_root": str(paths.repo_root),
        "run_dir": str(paths.run_dir),
        "status": status,
        "plan_path": str(config.plan_path),
        "max_turns": config.max_turns,
        "keep_runs": config.keep_runs,
        "extra_instructions": list(config.extra_instructions),
        "turns_completed": turns_completed if turns_completed is not None else (state.turns_completed if state else 0),
        "last_snapshot": _snapshot_payload(last_snapshot if last_snapshot is not None else (state.last_snapshot if state else None)),
    }
    if workflow_name is not None:
        payload["workflow_name"] = workflow_name
    if current_step_name is not None:
        payload["current_step_name"] = current_step_name
    if original_plan_path is not None:
        payload["original_plan_path"] = str(original_plan_path)
    if active_plan_path is not None:
        payload["active_plan_path"] = str(active_plan_path)
    if new_plan_path is not None:
        payload["new_plan_path"] = str(new_plan_path)
    if state is not None:
        payload["run_started_at"] = state.run_started_at.isoformat()
        payload["active_turn"] = state.active_turn
        payload["status_message"] = state.status_message
        if end_reason is None:
            end_reason = state.end_reason
    if end_reason is not None:
        payload["end_reason"] = end_reason
    if failure_reason is not None:
        payload["failure_reason"] = failure_reason
    effective_retry = pending_retry if pending_retry is not None else (state.pending_retry if state is not None else None)
    if effective_retry is not None:
        payload["pending_retry_step_name"] = effective_retry.step_name
        payload["pending_retry_attempt"] = effective_retry.attempt
        payload["pending_retry_limit"] = effective_retry.retry_limit
        payload["pending_retry_reason"] = "inconsistent_checkpoint_state"
    _write_json(paths.run_json, payload)


def write_turn_artifacts_start(
    paths: RunPaths,
    *,
    turn_number: int,
    invocation: HarnessInvocation,
    snapshot_before: PlanSnapshot,
    started_at: datetime | None = None,
    status: str,
    step_name: str | None = None,
    selector: str | None = None,
    original_plan_path: Path | None = None,
    active_plan_path: Path | None = None,
    new_plan_path: Path | None = None,
) -> Path:
    turn_dir = paths.turns_dir / f"turn-{turn_number:03d}"
    turn_dir.mkdir(parents=False, exist_ok=False)
    (turn_dir / "system-prompt.txt").write_text(invocation.system_prompt, encoding="utf-8")
    (turn_dir / "user-prompt.txt").write_text(invocation.user_prompt, encoding="utf-8")
    (turn_dir / "effective-prompt.txt").write_text(invocation.effective_prompt, encoding="utf-8")
    _write_json(turn_dir / "argv.json", {"argv": list(invocation.argv), "label": invocation.label, "prompt_mode": invocation.prompt_mode})
    _write_json(turn_dir / "env.json", {"env": dict(invocation.env)})
    result_payload = _turn_result_payload(
        turn_number=turn_number,
        invocation=invocation,
        snapshot_before=snapshot_before,
        snapshot_after=None,
        status=status,
        started_at=started_at or datetime.now(timezone.utc),
        step_name=step_name,
        selector=selector,
        original_plan_path=original_plan_path,
        active_plan_path=active_plan_path,
        new_plan_path=new_plan_path,
    )
    _write_json(turn_dir / "result.json", result_payload)
    return turn_dir


def finalize_turn_artifacts(
    turn_dir: Path,
    *,
    turn_number: int,
    invocation: HarnessInvocation,
    stdout: str,
    stderr: str,
    returncode: int,
    snapshot_before: PlanSnapshot,
    snapshot_after: PlanSnapshot | None,
    status: str,
    started_at: datetime,
    error: str | None = None,
    step_name: str | None = None,
    selector: str | None = None,
    original_plan_path: Path | None = None,
    active_plan_path: Path | None = None,
    new_plan_path: Path | None = None,
    conditions: dict[str, bool] | None = None,
    chosen_transition: str | None = None,
    end_reason: WorkflowEndReason | None = None,
    retry_attempt: int | None = None,
    retry_limit: int | None = None,
    retry_reason: str | None = None,
    retry_next_turn: bool | None = None,
    was_retry: bool | None = None,
) -> Path:
    finished_at = datetime.now(timezone.utc)
    (turn_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (turn_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    result_payload = _turn_result_payload(
        turn_number=turn_number,
        invocation=invocation,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        step_name=step_name,
        selector=selector,
        original_plan_path=original_plan_path,
        active_plan_path=active_plan_path,
        new_plan_path=new_plan_path,
        conditions=conditions,
        chosen_transition=chosen_transition,
        end_reason=end_reason,
        error=error,
        retry_attempt=retry_attempt,
        retry_limit=retry_limit,
        retry_reason=retry_reason,
        retry_next_turn=retry_next_turn,
        was_retry=was_retry,
    )
    _write_json(turn_dir / "result.json", result_payload)
    return turn_dir
