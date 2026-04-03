from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import (
    GoTransition,
    VALID_CONDITION_SYMBOLS,
    WorkflowConfig,
    WorkflowStepConfig,
    WorkflowUserConfig,
)
from .harnesses import get_adapter
from .harnesses.base import HarnessAdapter, HarnessInvocation
from .plan import ParsedPlan, PlanParseError, PlanSnapshot, load_plan, plan_has_git_tracking
from .run_state import ControllerConfig, ControllerRunResult, ControllerState, RetryContext, TurnRecord, WorkflowEndReason, format_harness_model_display
from .runlog import create_run_paths, finalize_turn_artifacts, prune_old_runs, write_run_metadata, write_turn_artifacts_start
from .status import BannerRenderer


PROCESS_POLL_INTERVAL_SECONDS = 0.05
BANNER_REFRESH_INTERVAL_SECONDS = 1.0

_REVIEW_SKILL_NAMES = frozenset({
    "aflow-review-squash",
    "aflow-review-checkpoint",
    "aflow-review-final",
})


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
    return _resolve_selector(selector, config, step_path=step_path)


def _resolve_selector(
    selector: str,
    config: WorkflowUserConfig,
    *,
    step_path: str,
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


def resolve_role_selector(
    role: str,
    team_name: str | None,
    config: WorkflowUserConfig,
    *,
    step_path: str = "<unknown>",
) -> str:
    selector = config.roles.get(role)
    if selector is None:
        if "." in role:
            return role
        raise WorkflowError(
            f"workflow step references unknown role '{role}' in {step_path}"
        )
    if team_name is None:
        return selector
    team_config = config.teams.get(team_name)
    if team_config is None:
        raise WorkflowError(
            f"workflow step references unknown team '{team_name}' in {step_path}"
        )
    return team_config.get(role, selector)


def _resolve_step_runtime(
    step: WorkflowStepConfig,
    config: WorkflowUserConfig,
    *,
    team_name: str | None,
    step_path: str,
) -> tuple[str, ResolvedProfile]:
    selector = resolve_role_selector(
        step.role,
        team_name,
        config,
        step_path=step_path,
    )
    return selector, resolve_profile(selector, config, step_path=step_path)


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
    cp = 1 if checkpoint_index is None else checkpoint_index
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


def _done_plan_path(repo_root: Path, plan_path: Path) -> Path | None:
    plans_root = (repo_root / "plans").resolve()
    in_progress_root = plans_root / "in-progress"
    try:
        relative_plan_path = plan_path.resolve().relative_to(in_progress_root)
    except ValueError:
        return None
    return plans_root / "done" / relative_plan_path


def move_completed_plan_to_done(repo_root: Path, plan_path: Path) -> Path:
    done_plan_path = _done_plan_path(repo_root, plan_path)
    if done_plan_path is None:
        raise WorkflowError(
            f"completed plan is not under '{repo_root / 'plans' / 'in-progress'}': {plan_path}"
        )
    if not plan_path.is_file():
        raise WorkflowError(f"completed plan file does not exist: {plan_path}")

    done_plan_path.parent.mkdir(parents=True, exist_ok=True)
    if done_plan_path.exists():
        if done_plan_path.is_file() and _same_file_contents(plan_path, done_plan_path):
            plan_path.unlink()
            return done_plan_path
        raise WorkflowError(
            f"done plan path already exists: {done_plan_path}"
        )

    try:
        shutil.move(str(plan_path), str(done_plan_path))
    except OSError as exc:
        raise WorkflowError(
            f"failed to move completed plan {plan_path} to {done_plan_path}: {exc}"
        ) from exc
    return done_plan_path


def _resolve_post_turn_original_plan_path(
    repo_root: Path,
    original_plan_path: Path,
    *,
    completed_returncode: int,
) -> Path:
    if original_plan_path.is_file():
        return original_plan_path
    if completed_returncode != 0:
        raise FileNotFoundError(
            f"{original_plan_path}: plan file does not exist"
        )
    done_plan_path = _done_plan_path(repo_root, original_plan_path)
    if done_plan_path is not None and done_plan_path.is_file():
        return done_plan_path
    raise FileNotFoundError(f"{original_plan_path}: plan file does not exist")


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
    return _select_transition(
        transitions,
        step_path=step_path,
        done=done,
        new_plan_exists=new_plan_exists,
        max_turns_reached=max_turns_reached,
    ).to


def _select_transition(
    transitions: tuple[GoTransition, ...],
    *,
    step_path: str,
    done: bool,
    new_plan_exists: bool,
    max_turns_reached: bool,
) -> GoTransition:
    for transition in transitions:
        if transition.when is None:
            return transition
        if evaluate_condition(
            transition.when,
            done=done,
            new_plan_exists=new_plan_exists,
            max_turns_reached=max_turns_reached,
        ):
            return transition
    raise WorkflowError(
        f"no transition matched for {step_path} "
        f"with conditions: DONE={done}, NEW_PLAN_EXISTS={new_plan_exists}, "
        f"MAX_TURNS_REACHED={max_turns_reached}"
    )


def _normalize_end_reason(
    *,
    already_complete: bool = False,
    selected_transition: GoTransition | None = None,
    done: bool = False,
    max_turns_reached: bool = False,
) -> WorkflowEndReason:
    if already_complete:
        return "already_complete"
    if selected_transition is not None and selected_transition.when is None:
        return "transition_end"
    if done:
        return "done"
    if max_turns_reached:
        return "max_turns_reached"
    return "transition_end"


def _format_failure(
    *,
    reason: str,
    run_dir: Path,
    snapshot: PlanSnapshot,
    parse_error: PlanParseError | None = None,
) -> str:
    if parse_error is not None and parse_error.checkpoint_name is not None:
        current = parse_error.checkpoint_name
        unchecked_steps = parse_error.unchecked_step_count or 0
    else:
        current = snapshot.current_checkpoint_name or "none"
        unchecked_steps = snapshot.current_checkpoint_unchecked_step_count
    return (
        f"{reason}\n"
        f"run log directory: {run_dir}\n"
        f"current checkpoint: {current}\n"
        f"unchecked checkpoint count: {snapshot.unchecked_checkpoint_count}\n"
        f"current checkpoint unchecked step count: {unchecked_steps}"
    )


def _run_process(
    invocation: HarnessInvocation,
    repo_root: Path,
    banner: BannerRenderer,
    state: ControllerState,
) -> subprocess.CompletedProcess[str]:
    is_interactive = sys.stdin.isatty() and sys.stdout.isatty()

    proc = subprocess.Popen(
        list(invocation.argv),
        cwd=str(repo_root),
        env={**os.environ, **invocation.env},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if is_interactive:
        banner.pause()
    else:
        banner.update(state)

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def _drain(stream, chunks: list[str], tee_to=None) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if tee_to is not None:
                tee_to.write(chunk)
                tee_to.flush()

    assert proc.stdout is not None
    assert proc.stderr is not None
    t_out = threading.Thread(
        target=_drain,
        args=(proc.stdout, stdout_chunks, sys.stdout if is_interactive else None),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_drain,
        args=(proc.stderr, stderr_chunks, sys.stderr if is_interactive else None),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    while True:
        try:
            proc.wait(timeout=PROCESS_POLL_INTERVAL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            pass

    t_out.join()
    t_err.join()

    if is_interactive:
        banner.resume(state)

    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode or 0,
        "".join(stdout_chunks),
        "".join(stderr_chunks),
    )


def _workflow_requires_git_tracking(
    wf: WorkflowConfig,
    config: WorkflowUserConfig,
) -> bool:
    for step in wf.steps.values():
        for prompt_key in step.prompts:
            prompt_text = config.prompts.get(prompt_key, "")
            for skill_name in _REVIEW_SKILL_NAMES:
                if skill_name in prompt_text:
                    return True
    return False


def _make_banner(
    config: ControllerConfig,
    *,
    workflow_steps: dict[str, WorkflowStepConfig] | None = None,
    workflow_name: str | None = None,
    original_plan_path: Path | None = None,
    banner_files_limit: int = 10,
) -> BannerRenderer:
    return BannerRenderer(
        config_max_turns=config.max_turns,
        config_plan_path=config.plan_path,
        workflow_steps=workflow_steps,
        config_banner_files_limit=banner_files_limit,
        workflow_name=workflow_name,
        original_plan_path=original_plan_path,
        repo_root=config.repo_root,
    )


_RETRY_APPENDIX_INTRO = (
    "The previous attempt left the plan in an invalid checkpoint state: "
    "a checkpoint heading was marked complete while one or more checkpoint-local "
    "steps remained unchecked. Repair the plan file so that any checkpoint "
    "marked complete has all its checkpoint-local steps also checked.\n\n"
    "Parse error from the previous attempt:\n"
)


def _effective_retry_limit(
    wf: WorkflowConfig,
    global_section: object,
) -> int:
    if wf.retry_inconsistent_checkpoint_state is not None:
        return wf.retry_inconsistent_checkpoint_state
    return getattr(global_section, "retry_inconsistent_checkpoint_state", 0)


def _build_retry_appendix(parse_error_str: str) -> str:
    return f"{_RETRY_APPENDIX_INTRO}{parse_error_str}"


_STOP_SENTINEL_PREFIX = "AFLOW_STOP:"
_STOP_SENTINEL_FALLBACK_REASON = "implementer requested stop without a reason"


def _detect_stop_marker(stdout: str, stderr: str) -> str | None:
    for text in (stdout, stderr):
        for line in text.splitlines():
            if line.startswith(_STOP_SENTINEL_PREFIX):
                reason = line[len(_STOP_SENTINEL_PREFIX):].strip()
                return reason or _STOP_SENTINEL_FALLBACK_REASON
    return None


def run_workflow(
    config: ControllerConfig,
    workflow_config: WorkflowUserConfig,
    workflow_name: str,
    *,
    parsed_plan: ParsedPlan | None = None,
    startup_retry: RetryContext | None = None,
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
    current_step_name = config.start_step or wf.first_step
    working_dir = working_dir or Path.cwd()

    run_paths = create_run_paths(config)
    state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
    state.status_message = "initializing"
    state.selected_start_step = config.start_step
    state.startup_recovery_used = startup_retry is not None
    state.startup_recovery_reason = startup_retry.parse_error_str if startup_retry is not None else None
    write_run_metadata(
        run_paths, config, state, status="initializing",
        workflow_name=workflow_name, original_plan_path=original_plan_path,
        active_plan_path=active_plan_path,
    )

    if banner is None:
        banner = _make_banner(
            config,
            workflow_steps=wf.steps,
            workflow_name=workflow_name,
            original_plan_path=original_plan_path,
            banner_files_limit=workflow_config.aflow.banner_files_limit,
        )
    banner.start(state)

    try:
        _backup_original_plan(config.repo_root, original_plan_path)
        if parsed_plan is None:
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

    if _workflow_requires_git_tracking(wf, workflow_config):
        plan_text = original_plan_path.read_text(encoding="utf-8")
        if not plan_has_git_tracking(plan_text):
            state.status_message = "failed"
            banner.stop(state)
            summary = (
                f"workflow '{workflow_name}' requires a '## Git Tracking' section "
                f"in the original plan at '{original_plan_path}'"
            )
            write_run_metadata(
                run_paths, config, state, status="failed", failure_reason=summary,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                active_plan_path=active_plan_path,
            )
            raise WorkflowError(summary, run_dir=run_paths.run_dir)

    original_snapshot = parsed_plan.snapshot
    state.last_snapshot = original_snapshot
    if startup_retry is not None:
        state.pending_retry = startup_retry
    write_run_metadata(
        run_paths, config, state, status="running", last_snapshot=original_snapshot,
        workflow_name=workflow_name, original_plan_path=original_plan_path,
        active_plan_path=active_plan_path,
    )
    banner.update(state)

    done = original_snapshot.is_complete
    if done:
        end_reason = _normalize_end_reason(already_complete=True)
        state.end_reason = end_reason
        state.status_message = "completed"
        banner.stop(state)
        result = ControllerRunResult(
            run_dir=run_paths.run_dir,
            turns_completed=0,
            final_snapshot=original_snapshot,
            issues_accumulated=state.issues_accumulated,
            end_reason=end_reason,
        )
        write_run_metadata(
            run_paths, config, state, status="completed", last_snapshot=original_snapshot,
            end_reason=end_reason,
            workflow_name=workflow_name, original_plan_path=original_plan_path,
            active_plan_path=active_plan_path,
        )
        return result

    use_popen = runner is None
    new_plan_path: Path | None = None
    retry_limit = _effective_retry_limit(wf, workflow_config.aflow)
    team_name = config.team if config.team is not None else wf.team
    if team_name is not None and team_name not in workflow_config.teams:
        raise WorkflowError(
            f"workflow '{workflow_name}' references unknown team '{team_name}'"
        )

    def _start_turn(
        *,
        turn_number: int,
        step_name: str,
        step: WorkflowStepConfig,
        step_role: str,
        resolved_selector: str,
        resolved: ResolvedProfile,
        active_path: Path,
        new_path: Path,
        invocation: HarnessInvocation,
        snapshot_before: PlanSnapshot,
    ) -> tuple[Path, datetime]:
        started_at = datetime.now(timezone.utc)
        state.active_turn = turn_number
        state.current_turn_started_at = started_at
        state.turn_history.append(
            TurnRecord(
                turn_number=turn_number,
                step_name=step_name,
                step_role=step_role,
                resolved_selector=resolved_selector,
                resolved_harness_name=resolved.harness_name,
                resolved_model_display=format_harness_model_display(
                    resolved.harness_name,
                    resolved.model,
                    resolved.effort,
                ),
                started_at=started_at,
            )
        )
        banner.set_context(
            current_step_name=step_name,
            active_plan_path=active_path,
            new_plan_path=new_path if new_path.is_file() else None,
            config_harness=resolved.harness_name,
            config_model=resolved.model,
            config_effort=resolved.effort,
        )
        turn_dir = write_turn_artifacts_start(
            run_paths,
            turn_number=turn_number,
            invocation=invocation,
            snapshot_before=snapshot_before,
            started_at=started_at,
            status="starting",
            step_name=step_name,
            step_role=step_role,
            selector=resolved_selector,
            original_plan_path=original_plan_path,
            active_plan_path=active_path,
            new_plan_path=new_path if new_path.is_file() else None,
        )
        banner.update(state)
        return turn_dir, started_at

    def _finalize_turn_record(
        *,
        status: str,
        started_at: datetime,
        snapshot_before: PlanSnapshot,
        snapshot_after: PlanSnapshot | None,
        invocation: HarnessInvocation,
        turn_dir: Path,
        stdout: str,
        stderr: str,
        returncode: int,
        error: str | None = None,
        step_name: str | None = None,
        step_role: str | None = None,
        selector: str | None = None,
        active_path: Path | None = None,
        new_path: Path | None = None,
        conditions: dict[str, bool] | None = None,
        chosen_transition: str | None = None,
        end_reason: WorkflowEndReason | None = None,
        retry_attempt: int | None = None,
        retry_limit_value: int | None = None,
        retry_reason: str | None = None,
        retry_next_turn: bool | None = None,
        was_retry: bool | None = None,
    ) -> None:
        finalize_turn_artifacts(
            turn_dir,
            turn_number=state.active_turn,
            invocation=invocation,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
            status=status,
            started_at=started_at,
            error=error,
            step_name=step_name,
            step_role=step_role,
            selector=selector,
            original_plan_path=original_plan_path,
            active_plan_path=active_path,
            new_plan_path=new_path,
            conditions=conditions,
            chosen_transition=chosen_transition,
            end_reason=end_reason,
            retry_attempt=retry_attempt,
            retry_limit=retry_limit_value,
            retry_reason=retry_reason,
            retry_next_turn=retry_next_turn,
            was_retry=was_retry,
        )
        record = state.turn_history[-1]
        record.outcome = "completed" if status in {"running", "completed"} else status
        record.finished_at = datetime.now(timezone.utc)
        record.duration_seconds = (record.finished_at - record.started_at).total_seconds()

    for turn_number in range(1, config.max_turns + 1):
        retry_ctx = state.pending_retry

        if retry_ctx is not None:
            state.status_message = (
                f"running turn {turn_number}: step {current_step_name} "
                f"(retry {retry_ctx.attempt}/{retry_ctx.retry_limit})"
            )
            write_run_metadata(
                run_paths, config, state, status="running", last_snapshot=state.last_snapshot,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                current_step_name=current_step_name, active_plan_path=retry_ctx.active_plan_path,
            )
            done = retry_ctx.snapshot_before.is_complete
            active_plan_path = retry_ctx.active_plan_path
            new_plan_path = retry_ctx.new_plan_path
            step = wf.steps[current_step_name]
            step_path = f"workflow.{workflow_name}.steps.{current_step_name}"
            selector, resolved = _resolve_step_runtime(
                step,
                workflow_config,
                team_name=team_name,
                step_path=step_path,
            )
            step_adapter = adapter or get_adapter(resolved.harness_name)
            user_prompt = retry_ctx.base_user_prompt + "\n\n" + _build_retry_appendix(retry_ctx.parse_error_str)
            invocation = step_adapter.build_invocation(
                repo_root=config.repo_root,
                model=resolved.model,
                system_prompt="",
                user_prompt=user_prompt,
                effort=resolved.effort,
            )
            snapshot_before = retry_ctx.snapshot_before
        else:
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
            selector, resolved = _resolve_step_runtime(
                step,
                workflow_config,
                team_name=team_name,
                step_path=step_path,
            )

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

            snapshot_before = state.last_snapshot

        turn_dir, turn_started_at = _start_turn(
            turn_number=turn_number,
            step_name=current_step_name,
            step=step,
            step_role=step.role,
            resolved_selector=selector,
            resolved=resolved,
            active_path=active_plan_path,
            new_path=new_plan_path,
            invocation=invocation,
            snapshot_before=snapshot_before,
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

        stop_reason = _detect_stop_marker(completed.stdout, completed.stderr)
        if stop_reason is not None:
            state.status_message = "failed"
            state.issues_accumulated += 1
            _finalize_turn_record(
                status="harness-failed",
                started_at=turn_started_at,
                snapshot_before=snapshot_before,
                snapshot_after=None,
                invocation=invocation,
                turn_dir=turn_dir,
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
                error=f"AFLOW_STOP: {stop_reason}",
                step_name=current_step_name,
                step_role=step.role,
                selector=selector,
                active_path=active_plan_path,
                new_path=new_plan_path,
            )
            summary = _format_failure(
                reason=f"workflow stopped by explicit AFLOW_STOP marker: {stop_reason}",
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
            raise WorkflowError(summary, run_dir=run_paths.run_dir)

        try:
            resolved_original_plan_path = _resolve_post_turn_original_plan_path(
                config.repo_root,
                original_plan_path,
                completed_returncode=completed.returncode,
            )
            parsed_after = load_plan(resolved_original_plan_path)
            if resolved_original_plan_path != original_plan_path:
                original_plan_path = resolved_original_plan_path
                if active_plan_path == config.plan_path:
                    active_plan_path = resolved_original_plan_path
            post_snapshot = parsed_after.snapshot
        except (PlanParseError, FileNotFoundError) as exc:
            is_retryable = (
                isinstance(exc, PlanParseError)
                and exc.error_kind == "inconsistent_checkpoint_state"
                and completed.returncode == 0
            )
            current_attempt = (retry_ctx.attempt if retry_ctx is not None else 0) + 1
            base_prompt = retry_ctx.base_user_prompt if retry_ctx is not None else user_prompt

            if is_retryable and current_attempt <= retry_limit and turn_number < config.max_turns:
                state.issues_accumulated += 1
                state.turns_completed += 1
                new_retry_ctx = RetryContext(
                    step_name=current_step_name,
                    step_role=step.role,
                    resolved_selector=selector,
                    resolved_harness_name=resolved.harness_name,
                    resolved_model=resolved.model,
                    resolved_effort=resolved.effort,
                    snapshot_before=snapshot_before,
                    active_plan_path=active_plan_path,
                    new_plan_path=new_plan_path,
                    base_user_prompt=base_prompt,
                    parse_error_str=str(exc),
                    attempt=current_attempt,
                    retry_limit=retry_limit,
                )
                state.pending_retry = new_retry_ctx
                _finalize_turn_record(
                    status="retry-scheduled",
                    started_at=turn_started_at,
                    snapshot_before=snapshot_before,
                    snapshot_after=None,
                    invocation=invocation,
                    turn_dir=turn_dir,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    returncode=completed.returncode,
                    error=str(exc),
                    step_name=current_step_name,
                    step_role=step.role,
                    selector=selector,
                    active_path=active_plan_path,
                    new_path=new_plan_path,
                    conditions={"DONE": done, "NEW_PLAN_EXISTS": False, "MAX_TURNS_REACHED": turn_number >= config.max_turns},
                    retry_attempt=current_attempt,
                    retry_limit_value=retry_limit,
                    retry_reason="inconsistent_checkpoint_state",
                    retry_next_turn=True,
                )
                write_run_metadata(
                    run_paths, config, state, status="running",
                    turns_completed=state.turns_completed,
                    last_snapshot=state.last_snapshot,
                    workflow_name=workflow_name, original_plan_path=original_plan_path,
                    current_step_name=current_step_name, active_plan_path=active_plan_path,
                    new_plan_path=new_plan_path,
                )
                banner.update(state)
                continue

            state.pending_retry = None
            state.status_message = "failed"
            state.issues_accumulated += 1
            _finalize_turn_record(
                status="plan-invalid",
                started_at=turn_started_at,
                snapshot_before=snapshot_before,
                snapshot_after=None,
                invocation=invocation,
                turn_dir=turn_dir,
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
                error=str(exc),
                step_name=current_step_name,
                step_role=step.role,
                selector=selector,
                active_path=active_plan_path,
                new_path=new_plan_path,
                conditions={"DONE": done, "NEW_PLAN_EXISTS": False, "MAX_TURNS_REACHED": turn_number >= config.max_turns},
            )
            summary = _format_failure(
                reason=str(exc),
                run_dir=run_paths.run_dir,
                snapshot=snapshot_before,
                parse_error=exc if isinstance(exc, PlanParseError) else None,
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

        state.pending_retry = None

        if completed.returncode != 0:
            state.status_message = "failed"
            state.issues_accumulated += 1
            _finalize_turn_record(
                status="harness-failed",
                started_at=turn_started_at,
                snapshot_before=snapshot_before,
                snapshot_after=post_snapshot,
                invocation=invocation,
                turn_dir=turn_dir,
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
                step_name=current_step_name,
                step_role=step.role,
                selector=selector,
                active_path=active_plan_path,
                new_path=new_plan_path,
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

        selected_transition: GoTransition | None = None
        transition_target: str | None = None
        try:
            selected_transition = _select_transition(
                step.go,
                step_path=step_path,
                done=done,
                new_plan_exists=new_plan_exists,
                max_turns_reached=max_turns_reached,
            )
            transition_target = selected_transition.to
        except WorkflowError as exc:
            state.status_message = "failed"
            state.issues_accumulated += 1
            _finalize_turn_record(
                status="transition-failed",
                started_at=turn_started_at,
                snapshot_before=snapshot_before,
                snapshot_after=post_snapshot,
                invocation=invocation,
                turn_dir=turn_dir,
                stdout=completed.stdout,
                stderr=completed.stderr,
                returncode=completed.returncode,
                step_name=current_step_name,
                step_role=step.role,
                selector=selector,
                active_path=active_plan_path,
                new_path=new_plan_path,
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

        _finalize_turn_record(
            status="completed" if done else "running",
            started_at=turn_started_at,
            snapshot_before=snapshot_before,
            snapshot_after=post_snapshot,
            invocation=invocation,
            turn_dir=turn_dir,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            step_name=current_step_name,
            step_role=step.role,
            selector=selector,
            active_path=active_plan_path,
            new_path=new_plan_path,
            conditions=conditions,
            chosen_transition=transition_target,
            end_reason=(
                _normalize_end_reason(
                    selected_transition=selected_transition,
                    done=done,
                    max_turns_reached=max_turns_reached,
                )
                if transition_target == "END" and selected_transition is not None
                else None
            ),
            was_retry=True if retry_ctx is not None else None,
            retry_attempt=retry_ctx.attempt if retry_ctx is not None else None,
        )

        if not new_plan_exists and active_plan_path != original_plan_path:
            active_plan_path = original_plan_path

        banner.set_context(
            active_plan_path=active_plan_path,
            new_plan_path=new_plan_path if new_plan_exists else None,
        )
        banner.update(state)

        write_run_metadata(
            run_paths, config, state, status="running",
            last_snapshot=post_snapshot,
            turns_completed=state.turns_completed,
            workflow_name=workflow_name, original_plan_path=original_plan_path,
            current_step_name=current_step_name, active_plan_path=active_plan_path,
            new_plan_path=new_plan_path,
        )

        if transition_target == "END":
            end_reason = _normalize_end_reason(
                selected_transition=selected_transition,
                done=done,
                max_turns_reached=max_turns_reached,
            )
            state.end_reason = end_reason
            state.status_message = "completed"
            result = ControllerRunResult(
                run_dir=run_paths.run_dir,
                turns_completed=state.turns_completed,
                final_snapshot=post_snapshot,
                issues_accumulated=state.issues_accumulated,
                end_reason=end_reason,
            )
            write_run_metadata(
                run_paths, config, state, status="completed",
                last_snapshot=post_snapshot,
                turns_completed=state.turns_completed,
                end_reason=end_reason,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                current_step_name=current_step_name, active_plan_path=active_plan_path,
                new_plan_path=new_plan_path,
            )
            prune_old_runs(run_paths.runs_root, config.keep_runs)
            banner.stop(state)
            return result

        if len(wf.steps) > 1:
            max_cap = workflow_config.aflow.max_same_step_turns
            if transition_target == current_step_name:
                new_streak = (
                    state.consec_step_count + 1
                    if state.consec_step_name == current_step_name
                    else 1
                )
                if max_cap > 0 and new_streak >= max_cap:
                    state.status_message = "failed"
                    state.issues_accumulated += 1
                    summary = _format_failure(
                        reason=(
                            f"same-step cap reached: step '{current_step_name}' "
                            f"selected {new_streak} consecutive times (limit: {max_cap})"
                        ),
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
                state.consec_step_name = current_step_name
                state.consec_step_count = new_streak
            else:
                state.consec_step_name = None
                state.consec_step_count = 0

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
