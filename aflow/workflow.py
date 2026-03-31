from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import (
    GoTransition,
    WorkflowStepConfig,
    WorkflowUserConfig,
)
from .harnesses import get_adapter
from .harnesses.base import HarnessAdapter, HarnessInvocation
from .plan import PlanParseError, PlanSnapshot, load_plan
from .run_state import ControllerConfig, ControllerRunResult, ControllerState
from .runlog import create_run_paths, prune_old_runs, write_run_metadata, write_turn_artifacts
from .status import BannerRenderer


VALID_CONDITION_SYMBOLS = frozenset({"DONE", "NEW_PLAN_EXISTS", "MAX_TURNS_REACHED"})
PROCESS_POLL_INTERVAL_SECONDS = 0.05
BANNER_REFRESH_INTERVAL_SECONDS = 1.0


class WorkflowError(RuntimeError):
    def __init__(self, summary: str, *, run_dir: Path | None = None) -> None:
        super().__init__(summary)
        self.summary = summary
        self.run_dir = run_dir


@dataclass(frozen=True)
class ResolvedProfile:
    harness_name: str
    profile_name: str
    model: str | None
    effort: str | None


def resolve_profile(
    selector: str,
    config: WorkflowUserConfig,
    *, step_path: str,
) -> ResolvedProfile:
    if "." not in selector:
        raise WorkflowError(
            f"step profile must be fully qualified (harness.profile) "
            f"in {step_path}, got '{selector}'"
        )
    harness_name, _, profile_name = selector.partition(".")
    if not harness_name or not profile_name:
        raise WorkflowError(
            f"invalid profile selector '{selector}' in {step_path}"
        )
    harness_config = config.harnesses.get(harness_name)
    if harness_config is None:
        raise WorkflowError(
            f"workflow step references unknown harness '{harness_name}' "
            f"in {step_path}"
        )
    profile_config = harness_config.profiles.get(profile_name)
    if profile_config is None:
        raise WorkflowError(
            f"workflow step references unknown profile '{profile_name}' "
            f"for harness '{harness_name}' in {step_path}"
        )
    return ResolvedProfile(
        harness_name=harness_name,
        profile_name=profile_name,
        model=profile_config.model,
        effort=profile_config.effort,
    )


def _resolve_prompt_file_path(
    prompt_text: str,
    *,
    config_dir: Path,
    working_dir: Path,
) -> Path | None:
    if not prompt_text.startswith("file://"):
        return None

    location = prompt_text[len("file://") :]
    if prompt_text.startswith("file:///"):
        file_path = Path(location)
        if not file_path.is_absolute():
            raise WorkflowError(
                f"prompt file path must be absolute: {file_path}"
            )
        return file_path

    if prompt_text.startswith("file://./"):
        return working_dir / location

    return config_dir / location


def render_prompt(
    prompt_text: str,
    *,
    config_dir: Path,
    working_dir: Path,
    original_plan_path: Path,
    new_plan_path: Path,
    active_plan_path: Path,
) -> str:
    file_path = _resolve_prompt_file_path(
        prompt_text,
        config_dir=config_dir,
        working_dir=working_dir,
    )
    if file_path is not None:
        if not file_path.is_file():
            raise WorkflowError(f"prompt file not found: {file_path}")
        prompt_text = file_path.read_text(encoding="utf-8")

    prompt_text = prompt_text.replace("{ORIGINAL_PLAN_PATH}", str(original_plan_path))
    prompt_text = prompt_text.replace("{NEW_PLAN_PATH}", str(new_plan_path))
    prompt_text = prompt_text.replace("{ACTIVE_PLAN_PATH}", str(active_plan_path))
    return prompt_text


def render_step_prompts(
    step: WorkflowStepConfig,
    config: WorkflowUserConfig,
    *,
    config_dir: Path,
    working_dir: Path,
    original_plan_path: Path,
    new_plan_path: Path,
    active_plan_path: Path,
) -> str:
    parts: list[str] = []
    for prompt_key in step.prompts:
        if prompt_key not in config.prompts:
            raise WorkflowError(
                f"step references unknown prompt '{prompt_key}'"
            )
        raw = config.prompts[prompt_key]
        rendered = render_prompt(
            raw,
            config_dir=config_dir,
            working_dir=working_dir,
            original_plan_path=original_plan_path,
            new_plan_path=new_plan_path,
            active_plan_path=active_plan_path,
        )
        parts.append(rendered)
    return "\n\n".join(parts)


def generate_new_plan_path(
    original_plan_path: Path,
    checkpoint_index: int | None,
) -> Path:
    stem = original_plan_path.stem
    parent = original_plan_path.parent
    suffix = original_plan_path.suffix or ".md"
    cp = checkpoint_index or 1
    pattern = re.compile(
        re.escape(f"{stem}-cp{cp:02d}-v") + r"(\d+)" + re.escape(suffix)
    )
    existing_versions: set[int] = set()
    if parent.is_dir():
        for child in parent.iterdir():
            m = pattern.match(child.name)
            if m:
                existing_versions.add(int(m.group(1)))
    next_version = max(existing_versions, default=0) + 1
    return parent / f"{stem}-cp{cp:02d}-v{next_version:02d}{suffix}"


def _plan_backup_base_name(original_plan_path: Path) -> tuple[str, str]:
    suffix = original_plan_path.suffix
    if suffix:
        return original_plan_path.name[:-len(suffix)], suffix
    return original_plan_path.name, ""


def _file_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _same_file_contents(
    source_path: Path,
    candidate_path: Path,
    *,
    source_identity: tuple[int, str] | None = None,
) -> bool:
    if source_identity is None:
        source_identity = _file_identity(source_path)
    source_size, source_hash = source_identity
    if candidate_path.stat().st_size != source_size:
        return False
    candidate_identity = _file_identity(candidate_path)
    return candidate_identity[1] == source_hash


def _backup_original_plan(repo_root: Path, original_plan_path: Path) -> Path:
    if not original_plan_path.is_file():
        raise WorkflowError(f"original plan file does not exist: {original_plan_path}")

    backup_dir = repo_root / "plans" / "backups"
    base_name, suffix = _plan_backup_base_name(original_plan_path)
    base_backup_path = backup_dir / f"{base_name}{suffix}"
    version_pattern = re.compile(
        rf"^{re.escape(base_name)}_v(\d+){re.escape(suffix)}$"
    )
    source_identity = _file_identity(original_plan_path)
    highest_version = 1

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)

        if base_backup_path.is_file():
            if _same_file_contents(
                original_plan_path,
                base_backup_path,
                source_identity=source_identity,
            ):
                return base_backup_path

        for child in backup_dir.iterdir():
            if not child.is_file() or child == base_backup_path:
                continue
            match = version_pattern.match(child.name)
            if match is None:
                continue
            highest_version = max(highest_version, int(match.group(1)))
            if _same_file_contents(
                original_plan_path,
                child,
                source_identity=source_identity,
            ):
                return child

        if not base_backup_path.exists():
            target_path = base_backup_path
        else:
            version = max(highest_version, 1) + 1
            target_path = backup_dir / f"{base_name}_v{version:02d}{suffix}"
            while target_path.exists():
                version += 1
                target_path = backup_dir / f"{base_name}_v{version:02d}{suffix}"

        shutil.copyfile(original_plan_path, target_path)
        return target_path
    except OSError as exc:
        raise WorkflowError(
            f"failed to back up original plan {original_plan_path} into {backup_dir}: {exc}"
        ) from exc


def _evaluate_condition_token(
    token: str,
    *,
    done: bool,
    new_plan_exists: bool,
    max_turns_reached: bool,
) -> bool:
    if token == "DONE":
        return done
    if token == "NEW_PLAN_EXISTS":
        return new_plan_exists
    if token == "MAX_TURNS_REACHED":
        return max_turns_reached
    raise WorkflowError(f"unknown condition symbol: {token}")


def evaluate_condition(
    expression: str,
    *,
    done: bool,
    new_plan_exists: bool,
    max_turns_reached: bool,
) -> bool:
    tokens = _tokenize_condition(expression)
    pos = [0]
    result = _parse_or(tokens, pos, done=done, new_plan_exists=new_plan_exists, max_turns_reached=max_turns_reached)
    if pos[0] < len(tokens):
        raise WorkflowError(
            f"unexpected token '{tokens[pos[0]]}' in condition expression"
        )
    return result


def _tokenize_condition(expression: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    while i < len(expression):
        ch = expression[i]
        if ch.isspace():
            i += 1
            continue
        if expression[i:i+2] == "&&":
            tokens.append("&&")
            i += 2
        elif expression[i:i+2] == "||":
            tokens.append("||")
            i += 2
        elif ch == "!":
            tokens.append("!")
            i += 1
        elif ch == "(":
            tokens.append("(")
            i += 1
        elif ch == ")":
            tokens.append(")")
            i += 1
        elif ch.isalpha() or ch == "_":
            j = i
            while j < len(expression) and (expression[j].isalnum() or expression[j] == "_"):
                j += 1
            tokens.append(expression[i:j])
            i = j
        else:
            raise WorkflowError(
                f"unexpected character '{ch}' in condition expression"
            )
    return tokens


def _parse_or(
    tokens: list[str], pos: list[int], **kwargs: bool,
) -> bool:
    result = _parse_and(tokens, pos, **kwargs)
    while pos[0] < len(tokens) and tokens[pos[0]] == "||":
        pos[0] += 1
        right = _parse_and(tokens, pos, **kwargs)
        result = result or right
    return result


def _parse_and(
    tokens: list[str], pos: list[int], **kwargs: bool,
) -> bool:
    result = _parse_not(tokens, pos, **kwargs)
    while pos[0] < len(tokens) and tokens[pos[0]] == "&&":
        pos[0] += 1
        right = _parse_not(tokens, pos, **kwargs)
        result = result and right
    return result


def _parse_not(
    tokens: list[str], pos: list[int], **kwargs: bool,
) -> bool:
    if pos[0] < len(tokens) and tokens[pos[0]] == "!":
        pos[0] += 1
        return not _parse_not(tokens, pos, **kwargs)
    return _parse_primary(tokens, pos, **kwargs)


def _parse_primary(
    tokens: list[str], pos: list[int], **kwargs: bool,
) -> bool:
    if pos[0] >= len(tokens):
        raise WorkflowError("unexpected end of condition expression")
    token = tokens[pos[0]]
    if token == "(":
        pos[0] += 1
        result = _parse_or(tokens, pos, **kwargs)
        if pos[0] >= len(tokens) or tokens[pos[0]] != ")":
            raise WorkflowError("missing closing parenthesis in condition expression")
        pos[0] += 1
        return result
    if token in VALID_CONDITION_SYMBOLS:
        pos[0] += 1
        return _evaluate_condition_token(token, **kwargs)
    raise WorkflowError(f"unexpected token '{token}' in condition expression")


def pick_transition(
    transitions: tuple[GoTransition, ...],
    *,
    step_path: str,
    done: bool,
    new_plan_exists: bool,
    max_turns_reached: bool,
) -> str:
    for transition in transitions:
        if transition.when is None:
            return transition.to
        if evaluate_condition(
            transition.when,
            done=done,
            new_plan_exists=new_plan_exists,
            max_turns_reached=max_turns_reached,
        ):
            return transition.to
    raise WorkflowError(
        f"no transition matched for {step_path} "
        f"with conditions: DONE={done}, NEW_PLAN_EXISTS={new_plan_exists}, "
        f"MAX_TURNS_REACHED={max_turns_reached}"
    )


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

    next_banner_update_at = time.monotonic() + BANNER_REFRESH_INTERVAL_SECONDS
    while True:
        try:
            proc.wait(timeout=PROCESS_POLL_INTERVAL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() >= next_banner_update_at:
                banner.update(state)
                next_banner_update_at = time.monotonic() + BANNER_REFRESH_INTERVAL_SECONDS

    t_out.join()
    t_err.join()

    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode or 0,
        "".join(stdout_chunks),
        "".join(stderr_chunks),
    )


def _make_banner(
    config: ControllerConfig,
    *,
    workflow_name: str | None = None,
    original_plan_path: Path | None = None,
) -> BannerRenderer:
    return BannerRenderer(
        config_max_turns=config.max_turns,
        config_plan_path=config.plan_path,
        workflow_name=workflow_name,
        original_plan_path=original_plan_path,
    )


def run_workflow(
    config: ControllerConfig,
    workflow_config: WorkflowUserConfig,
    workflow_name: str,
    *,
    config_dir: Path,
    working_dir: Path | None = None,
    adapter: HarnessAdapter | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    banner: BannerRenderer | None = None,
) -> ControllerRunResult:
    if workflow_name not in workflow_config.workflows:
        raise WorkflowError(f"workflow '{workflow_name}' not found in config")

    wf = workflow_config.workflows[workflow_name]
    if wf.first_step is None:
        raise WorkflowError(f"workflow '{workflow_name}' has no steps")

    original_plan_path = config.plan_path
    active_plan_path = original_plan_path
    current_step_name = wf.first_step
    working_dir = working_dir or Path.cwd()

    run_paths = create_run_paths(config)
    state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
    state.status_message = "initializing"
    write_run_metadata(
        run_paths, config, state, status="initializing",
        workflow_name=workflow_name, original_plan_path=original_plan_path,
        active_plan_path=active_plan_path,
    )

    if banner is None:
        banner = _make_banner(config, workflow_name=workflow_name, original_plan_path=original_plan_path)
    banner.start(state)

    try:
        _backup_original_plan(config.repo_root, original_plan_path)
        parsed_plan = load_plan(original_plan_path)
    except WorkflowError as exc:
        state.status_message = "failed"
        banner.stop(state)
        summary = _format_failure(
            reason=exc.summary,
            run_dir=run_paths.run_dir,
            snapshot=PlanSnapshot(None, 0, 0, False),
        )
        write_run_metadata(
            run_paths, config, state, status="failed", failure_reason=summary,
            workflow_name=workflow_name, original_plan_path=original_plan_path,
            active_plan_path=active_plan_path,
        )
        raise WorkflowError(summary, run_dir=run_paths.run_dir) from exc
    except (PlanParseError, FileNotFoundError) as exc:
        state.status_message = "failed"
        banner.stop(state)
        summary = _format_failure(
            reason=str(exc),
            run_dir=run_paths.run_dir,
            snapshot=PlanSnapshot(None, 0, 0, False),
        )
        write_run_metadata(
            run_paths, config, state, status="failed", failure_reason=summary,
            workflow_name=workflow_name, original_plan_path=original_plan_path,
            active_plan_path=active_plan_path,
        )
        raise WorkflowError(summary, run_dir=run_paths.run_dir) from exc

    original_snapshot = parsed_plan.snapshot
    state.last_snapshot = original_snapshot
    write_run_metadata(
        run_paths, config, state, status="running", last_snapshot=original_snapshot,
        workflow_name=workflow_name, original_plan_path=original_plan_path,
        active_plan_path=active_plan_path,
    )
    banner.update(state)

    done = original_snapshot.is_complete
    if done:
        state.status_message = "completed"
        banner.stop(state)
        result = ControllerRunResult(
            run_dir=run_paths.run_dir,
            turns_completed=0,
            final_snapshot=original_snapshot,
            issues_accumulated=state.issues_accumulated,
        )
        write_run_metadata(
            run_paths, config, state, status="completed", last_snapshot=original_snapshot,
            workflow_name=workflow_name, original_plan_path=original_plan_path,
            active_plan_path=active_plan_path,
        )
        return result

    use_popen = runner is None
    new_plan_path: Path | None = None

    for turn_number in range(1, config.max_turns + 1):
        state.active_turn = turn_number
        state.status_message = f"running turn {turn_number}: step {current_step_name}"
        write_run_metadata(
            run_paths, config, state, status="running", last_snapshot=state.last_snapshot,
            workflow_name=workflow_name, original_plan_path=original_plan_path,
            current_step_name=current_step_name, active_plan_path=active_plan_path,
        )

        try:
            current_plan = load_plan(original_plan_path)
        except (PlanParseError, FileNotFoundError) as exc:
            state.status_message = "failed"
            banner.stop(state)
            summary = _format_failure(
                reason=str(exc),
                run_dir=run_paths.run_dir,
                snapshot=state.last_snapshot,
            )
            write_run_metadata(
                run_paths, config, state, status="failed", failure_reason=summary,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                current_step_name=current_step_name, active_plan_path=active_plan_path,
            )
            raise WorkflowError(summary, run_dir=run_paths.run_dir) from exc

        done = current_plan.snapshot.is_complete
        checkpoint_index = current_plan.snapshot.current_checkpoint_index

        new_plan_path = generate_new_plan_path(
            original_plan_path,
            checkpoint_index=checkpoint_index,
        )

        step = wf.steps[current_step_name]
        step_path = f"workflow.{workflow_name}.steps.{current_step_name}"
        resolved = resolve_profile(step.profile, workflow_config, step_path=step_path)

        step_adapter = adapter or get_adapter(resolved.harness_name)

        user_prompt = render_step_prompts(
            step,
            workflow_config,
            config_dir=config_dir,
            working_dir=working_dir,
            original_plan_path=original_plan_path,
            new_plan_path=new_plan_path,
            active_plan_path=active_plan_path,
        )

        if config.extra_instructions:
            extra_text = " ".join(config.extra_instructions).strip()
            user_prompt = "\n\n".join((user_prompt, extra_text))

        invocation = step_adapter.build_invocation(
            repo_root=config.repo_root,
            model=resolved.model,
            system_prompt="",
            user_prompt=user_prompt,
            effort=resolved.effort,
        )

        banner._current_step_name = current_step_name
        banner._active_plan_path = active_plan_path
        banner._new_plan_path = new_plan_path
        banner._config_harness = resolved.harness_name
        banner._config_model = resolved.model
        banner._config_effort = resolved.effort
        banner.update(state)

        snapshot_before = state.last_snapshot

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
            parsed_after = load_plan(original_plan_path)
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
                snapshot_before=snapshot_before,
                snapshot_after=None,
                status="plan-invalid",
                error=str(exc),
                step_name=current_step_name, selector=step.profile,
                original_plan_path=original_plan_path, active_plan_path=active_plan_path,
                new_plan_path=new_plan_path,
                conditions={"DONE": done, "NEW_PLAN_EXISTS": False, "MAX_TURNS_REACHED": turn_number >= config.max_turns},
            )
            summary = _format_failure(
                reason=str(exc),
                run_dir=run_paths.run_dir,
                snapshot=snapshot_before,
            )
            write_run_metadata(
                run_paths, config, state, status="failed", failure_reason=summary,
                turns_completed=state.turns_completed,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                current_step_name=current_step_name, active_plan_path=active_plan_path,
                new_plan_path=new_plan_path,
            )
            banner.stop(state)
            raise WorkflowError(summary, run_dir=run_paths.run_dir) from exc

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
                snapshot_before=snapshot_before,
                snapshot_after=post_snapshot,
                status="harness-failed",
                step_name=current_step_name, selector=step.profile,
                original_plan_path=original_plan_path, active_plan_path=active_plan_path,
                new_plan_path=new_plan_path,
                conditions={"DONE": post_snapshot.is_complete, "NEW_PLAN_EXISTS": False, "MAX_TURNS_REACHED": turn_number >= config.max_turns},
            )
            summary = _format_failure(
                reason=f"harness '{invocation.label}' exited with code {completed.returncode}",
                run_dir=run_paths.run_dir,
                snapshot=post_snapshot,
            )
            write_run_metadata(
                run_paths, config, state, status="failed", failure_reason=summary,
                turns_completed=state.turns_completed,
                last_snapshot=post_snapshot,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                current_step_name=current_step_name, active_plan_path=active_plan_path,
                new_plan_path=new_plan_path,
            )
            banner.stop(state)
            raise WorkflowError(summary, run_dir=run_paths.run_dir)

        state.last_snapshot = post_snapshot
        state.turns_completed += 1

        done = post_snapshot.is_complete
        new_plan_exists = new_plan_path.is_file()

        if new_plan_exists:
            active_plan_path = new_plan_path

        max_turns_reached = turn_number >= config.max_turns

        conditions = {
            "DONE": done,
            "NEW_PLAN_EXISTS": new_plan_exists,
            "MAX_TURNS_REACHED": max_turns_reached,
        }

        transition_target: str | None = None
        try:
            transition_target = pick_transition(
                step.go,
                step_path=step_path,
                done=done,
                new_plan_exists=new_plan_exists,
                max_turns_reached=max_turns_reached,
            )
        except WorkflowError as exc:
            state.status_message = "failed"
            state.issues_accumulated += 1
            write_turn_artifacts(
                run_paths,
                turn_number=turn_number,
                invocation=invocation,
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
                snapshot_before=snapshot_before,
                snapshot_after=post_snapshot,
                status="transition-failed",
                step_name=current_step_name, selector=step.profile,
                original_plan_path=original_plan_path, active_plan_path=active_plan_path,
                new_plan_path=new_plan_path,
                conditions=conditions,
            )
            summary = _format_failure(
                reason=exc.summary,
                run_dir=run_paths.run_dir,
                snapshot=state.last_snapshot,
            )
            write_run_metadata(
                run_paths, config, state, status="failed", failure_reason=summary,
                turns_completed=state.turns_completed,
                last_snapshot=state.last_snapshot,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                current_step_name=current_step_name, active_plan_path=active_plan_path,
                new_plan_path=new_plan_path,
            )
            banner.stop(state)
            raise WorkflowError(summary, run_dir=run_paths.run_dir) from exc

        write_turn_artifacts(
            run_paths,
            turn_number=turn_number,
            invocation=invocation,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            snapshot_before=snapshot_before,
            snapshot_after=post_snapshot,
            status="completed" if done else "running",
            step_name=current_step_name, selector=step.profile,
            original_plan_path=original_plan_path, active_plan_path=active_plan_path,
            new_plan_path=new_plan_path,
            conditions=conditions,
            chosen_transition=transition_target,
        )

        write_run_metadata(
            run_paths, config, state, status="running",
            last_snapshot=post_snapshot,
            turns_completed=state.turns_completed,
            workflow_name=workflow_name, original_plan_path=original_plan_path,
            current_step_name=current_step_name, active_plan_path=active_plan_path,
            new_plan_path=new_plan_path,
        )

        if transition_target == "END":
            state.status_message = "completed"
            result = ControllerRunResult(
                run_dir=run_paths.run_dir,
                turns_completed=state.turns_completed,
                final_snapshot=post_snapshot,
                issues_accumulated=state.issues_accumulated,
            )
            write_run_metadata(
                run_paths, config, state, status="completed",
                last_snapshot=post_snapshot,
                turns_completed=state.turns_completed,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                current_step_name=current_step_name, active_plan_path=active_plan_path,
                new_plan_path=new_plan_path,
            )
            prune_old_runs(run_paths.runs_root, config.keep_runs)
            banner.stop(state)
            return result

        current_step_name = transition_target

    state.status_message = "failed"
    summary = _format_failure(
        reason=f"reached max turns limit of {config.max_turns} without a transition to END",
        run_dir=run_paths.run_dir,
        snapshot=state.last_snapshot,
    )
    write_run_metadata(
        run_paths, config, state, status="failed", failure_reason=summary,
        last_snapshot=state.last_snapshot,
        turns_completed=state.turns_completed,
        workflow_name=workflow_name, original_plan_path=original_plan_path,
        current_step_name=current_step_name, active_plan_path=active_plan_path,
        new_plan_path=new_plan_path,
    )
    prune_old_runs(run_paths.runs_root, config.keep_runs)
    banner.stop(state)
    raise WorkflowError(summary, run_dir=run_paths.run_dir)
