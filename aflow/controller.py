from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .harnesses import get_adapter
from .harnesses.base import HarnessAdapter, HarnessInvocation
from .plan import PlanParseError, PlanSnapshot, load_plan
from .run_state import ControllerConfig, ControllerRunResult, ControllerState
from .runlog import create_run_paths, prune_old_runs, write_run_metadata, write_turn_artifacts
from .status import BannerRenderer


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


def _run_process(
    invocation: HarnessInvocation,
    repo_root: Path,
    banner: BannerRenderer,
    state: ControllerState,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        list(invocation.argv),
        cwd=str(repo_root),
        env={**os.environ, **invocation.env},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    banner.update(state)

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain(stream, chunks: list[str]) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            chunks.append(chunk)

    assert proc.stdout is not None
    assert proc.stderr is not None
    t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
    t_out.start()
    t_err.start()

    while proc.poll() is None:
        time.sleep(1)
        banner.update(state)

    t_out.join()
    t_err.join()

    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode or 0,
        "".join(stdout_chunks),
        "".join(stderr_chunks),
    )


def _make_banner(config: ControllerConfig) -> BannerRenderer:
    return BannerRenderer(
        config_harness=config.harness,
        config_model=config.model,
        config_effort=config.effort,
        config_max_turns=config.max_turns,
        config_plan_path=config.plan_path,
    )


def _record_snapshot(state: ControllerState, snapshot: PlanSnapshot) -> None:
    changed = snapshot.signature != state.last_snapshot.signature
    if changed:
        state.stagnation_turns = 0
    else:
        state.stagnation_turns += 1
        state.issues_accumulated += 1
    state.last_snapshot = snapshot
    state.turns_completed += 1


def run_controller(
    config: ControllerConfig,
    *,
    adapter: HarnessAdapter | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    banner: BannerRenderer | None = None,
) -> ControllerRunResult:
    adapter = adapter or get_adapter(config.harness)
    run_paths = create_run_paths(config)
    state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
    state.status_message = "initializing"
    write_run_metadata(run_paths, config, state, status="initializing")

    if banner is None:
        banner = _make_banner(config)
    banner.start(state)

    try:
        parsed_plan = load_plan(config.plan_path)
    except (PlanParseError, FileNotFoundError) as exc:
        state.status_message = "failed"
        banner.stop(state)
        summary = _format_failure(
            reason=str(exc),
            run_dir=run_paths.run_dir,
            snapshot=PlanSnapshot(None, 0, 0, False),
        )
        write_run_metadata(run_paths, config, state, status="failed", failure_reason=summary)
        raise ControllerError(summary, run_dir=run_paths.run_dir) from exc

    snapshot = parsed_plan.snapshot
    state.last_snapshot = snapshot
    write_run_metadata(run_paths, config, state, status="running", last_snapshot=snapshot)
    banner.update(state)

    if snapshot.is_complete:
        state.status_message = "completed"
        banner.stop(state)
        result = ControllerRunResult(
            run_dir=run_paths.run_dir,
            turns_completed=0,
            final_snapshot=snapshot,
            issues_accumulated=state.issues_accumulated,
        )
        write_run_metadata(run_paths, config, state, status="completed", last_snapshot=snapshot)
        return result

    use_popen = runner is None

    for turn_number in range(1, config.max_turns + 1):
        state.active_turn = turn_number
        state.status_message = f"running turn {turn_number}"
        banner.update(state)
        write_run_metadata(run_paths, config, state, status="running", last_snapshot=state.last_snapshot)

        system_prompt = build_system_prompt(state.last_snapshot, stagnation_turns=state.stagnation_turns)
        user_prompt = build_user_prompt(config.plan_path, config.extra_instructions)
        invocation = adapter.build_invocation(
            repo_root=config.repo_root,
            model=config.model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            effort=config.effort,
        )

        if use_popen:
            completed = _run_process(invocation, config.repo_root, banner, state)
        else:
            assert runner is not None
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
            state.status_message = "failed"
            state.issues_accumulated += 1
            write_turn_artifacts(
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
            banner.stop(state)
            raise ControllerError(summary, run_dir=run_paths.run_dir) from exc

        if completed.returncode != 0:
            state.status_message = "failed"
            state.issues_accumulated += 1
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
            banner.stop(state)
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

        _record_snapshot(state, post_snapshot)
        write_run_metadata(
            run_paths,
            config,
            state,
            status="running",
            last_snapshot=post_snapshot,
            turns_completed=state.turns_completed,
        )

        if post_snapshot.is_complete:
            state.status_message = "completed"
            result = ControllerRunResult(
                run_dir=run_paths.run_dir,
                turns_completed=state.turns_completed,
                final_snapshot=post_snapshot,
                issues_accumulated=state.issues_accumulated,
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
            banner.stop(state)
            return result

        if state.stagnation_turns >= config.stagnation_limit:
            state.status_message = "failed"
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
            banner.stop(state)
            raise ControllerError(summary, run_dir=run_paths.run_dir)

    state.status_message = "failed"
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
    banner.stop(state)
    raise ControllerError(summary, run_dir=run_paths.run_dir)
