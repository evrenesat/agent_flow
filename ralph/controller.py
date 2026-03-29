from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from .harnesses import get_adapter
from .harnesses.base import HarnessAdapter, HarnessInvocation
from .plan import PlanParseError, PlanSnapshot, load_plan
from .run_state import ControllerConfig, ControllerRunResult, ControllerState
from .runlog import create_run_paths, prune_old_runs, write_run_metadata, write_turn_artifacts


BASE_SYSTEM_PROMPT = """You are the universal RALF checkpoint controller.
Work from the checkpoint plan on disk as the only source of truth.
Re-read the plan file from disk on every turn before you decide what to do next.
Only work on the first incomplete checkpoint in the file.
Do not claim completion unless the plan on disk is truly complete.
Before marking a checkpoint heading complete, ensure every step checkbox in that checkpoint section is checked.
Ignore checkboxes outside checkpoint sections when reasoning about progress.
"""


def _snapshot_line(snapshot: PlanSnapshot) -> str:
    current = snapshot.current_checkpoint_name or "none"
    return (
        f"Current checkpoint: {current}; "
        f"unchecked checkpoints: {snapshot.unchecked_checkpoint_count}; "
        f"unchecked steps in current checkpoint: {snapshot.current_checkpoint_unchecked_step_count}"
    )


def build_system_prompt(snapshot: PlanSnapshot, *, stagnation_turns: int) -> str:
    parts = [BASE_SYSTEM_PROMPT.strip(), _snapshot_line(snapshot)]
    if stagnation_turns > 0:
        parts.append(
            "The previous turn did not change checkpoint progress. "
            "Re-read the plan from disk, focus on the first incomplete checkpoint, "
            "check completed steps before marking the checkpoint heading done, "
            "and do not claim success until the on-disk plan is actually complete."
        )
    return "\n\n".join(parts)


def build_user_prompt(plan_path: Path, extra_instructions: tuple[str, ...]) -> str:
    base = f"Plan file: {plan_path}"
    if not extra_instructions:
        return base
    extra = " ".join(extra_instructions).strip()
    return "\n\n".join((base, extra))


class ControllerError(RuntimeError):
    def __init__(self, summary: str, *, run_dir: Path | None = None) -> None:
        super().__init__(summary)
        self.summary = summary
        self.run_dir = run_dir


def _format_failure(
    *,
    reason: str,
    run_dir: Path,
    snapshot: PlanSnapshot,
) -> str:
    current = snapshot.current_checkpoint_name or "none"
    return (
        f"{reason}\n"
        f"run log directory: {run_dir}\n"
        f"current checkpoint: {current}\n"
        f"unchecked checkpoint count: {snapshot.unchecked_checkpoint_count}\n"
        f"current checkpoint unchecked step count: {snapshot.current_checkpoint_unchecked_step_count}"
    )


def run_controller(
    config: ControllerConfig,
    *,
    adapter: HarnessAdapter | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ControllerRunResult:
    adapter = adapter or get_adapter(config.harness)
    run_paths = create_run_paths(config)
    write_run_metadata(run_paths, config, None, status="initializing")

    try:
        parsed_plan = load_plan(config.plan_path)
    except (PlanParseError, FileNotFoundError) as exc:
        summary = _format_failure(
            reason=str(exc),
            run_dir=run_paths.run_dir,
            snapshot=PlanSnapshot(None, 0, 0, False),
        )
        write_run_metadata(run_paths, config, None, status="failed", failure_reason=summary)
        raise ControllerError(summary, run_dir=run_paths.run_dir) from exc

    snapshot = parsed_plan.snapshot
    state = ControllerState(last_snapshot=snapshot)
    write_run_metadata(run_paths, config, state, status="running", last_snapshot=snapshot)

    if snapshot.is_complete:
        result = ControllerRunResult(run_dir=run_paths.run_dir, turns_completed=0, final_snapshot=snapshot)
        write_run_metadata(run_paths, config, state, status="completed", last_snapshot=snapshot)
        return result

    for turn_number in range(1, config.max_turns + 1):
        system_prompt = build_system_prompt(state.last_snapshot, stagnation_turns=state.stagnation_turns)
        user_prompt = build_user_prompt(config.plan_path, config.extra_instructions)
        invocation = adapter.build_invocation(
            repo_root=config.repo_root,
            model=config.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            effort=config.effort,
        )
        completed = runner(
            list(invocation.argv),
            cwd=str(config.repo_root),
            env={**os.environ, **invocation.env},
            capture_output=True,
            text=True,
            check=False,
        )

        try:
            parsed_after = load_plan(config.plan_path)
            post_snapshot = parsed_after.snapshot
        except (PlanParseError, FileNotFoundError) as exc:
            turn_dir = write_turn_artifacts(
                run_paths,
                turn_number=turn_number,
                invocation=invocation,
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
                snapshot_before=state.last_snapshot,
                snapshot_after=None,
                status="plan-invalid",
                error=str(exc),
            )
            summary = _format_failure(
                reason=str(exc),
                run_dir=run_paths.run_dir,
                snapshot=state.last_snapshot,
            )
            write_run_metadata(
                run_paths,
                config,
                state,
                status="failed",
                failure_reason=summary,
                turns_completed=state.turns_completed,
            )
            raise ControllerError(summary, run_dir=run_paths.run_dir) from exc

        if completed.returncode != 0:
            write_turn_artifacts(
                run_paths,
                turn_number=turn_number,
                invocation=invocation,
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
                snapshot_before=state.last_snapshot,
                snapshot_after=post_snapshot,
                status="harness-failed",
            )
            summary = _format_failure(
                reason=f"harness '{invocation.label}' exited with code {completed.returncode}",
                run_dir=run_paths.run_dir,
                snapshot=post_snapshot,
            )
            write_run_metadata(
                run_paths,
                config,
                state,
                status="failed",
                failure_reason=summary,
                turns_completed=state.turns_completed,
                last_snapshot=post_snapshot,
            )
            raise ControllerError(summary, run_dir=run_paths.run_dir)

        write_turn_artifacts(
            run_paths,
            turn_number=turn_number,
            invocation=invocation,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            snapshot_before=state.last_snapshot,
            snapshot_after=post_snapshot,
            status="completed" if post_snapshot.is_complete else "running",
        )

        state.record_snapshot(post_snapshot)
        write_run_metadata(
            run_paths,
            config,
            state,
            status="running",
            last_snapshot=post_snapshot,
            turns_completed=state.turns_completed,
        )

        if post_snapshot.is_complete:
            result = ControllerRunResult(
                run_dir=run_paths.run_dir,
                turns_completed=state.turns_completed,
                final_snapshot=post_snapshot,
            )
            write_run_metadata(
                run_paths,
                config,
                state,
                status="completed",
                last_snapshot=post_snapshot,
                turns_completed=state.turns_completed,
            )
            prune_old_runs(run_paths.runs_root, config.keep_runs)
            return result

        if state.stagnation_turns >= config.stagnation_limit:
            summary = _format_failure(
                reason=(
                    f"checkpoint progress did not change for {config.stagnation_limit} completed turns"
                ),
                run_dir=run_paths.run_dir,
                snapshot=post_snapshot,
            )
            write_run_metadata(
                run_paths,
                config,
                state,
                status="failed",
                failure_reason=summary,
                last_snapshot=post_snapshot,
                turns_completed=state.turns_completed,
            )
            raise ControllerError(summary, run_dir=run_paths.run_dir)

    summary = _format_failure(
        reason=f"reached max turns limit of {config.max_turns}",
        run_dir=run_paths.run_dir,
        snapshot=state.last_snapshot,
    )
    write_run_metadata(
        run_paths,
        config,
        state,
        status="failed",
        failure_reason=summary,
        last_snapshot=state.last_snapshot,
        turns_completed=state.turns_completed,
    )
    prune_old_runs(run_paths.runs_root, config.keep_runs)
    raise ControllerError(summary, run_dir=run_paths.run_dir)
