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
    AflowSection,
    GoTransition,
    VALID_CONDITION_SYMBOLS,
    WorkflowConfig,
    WorkflowStepConfig,
    WorkflowUserConfig,
)
from .git_status import classify_dirtiness_by_prefix
from .harnesses import get_adapter
from .harnesses.base import HarnessAdapter, HarnessInvocation
from .plan import FENCE_RE, ParsedPlan, PlanParseError, PlanSnapshot, load_plan, load_plan_tolerant, plan_has_git_tracking
from .run_state import ControllerConfig, ControllerRunResult, ControllerState, ExecutionContext, RetryContext, TurnRecord, WorkflowEndReason, format_harness_model_display
from .runlog import create_run_paths, finalize_turn_artifacts, prune_old_runs, write_run_metadata, write_turn_artifacts_start
from .status import BannerRenderer


PROCESS_POLL_INTERVAL_SECONDS = 0.05
BANNER_REFRESH_INTERVAL_SECONDS = 1.0

_REVIEW_SKILL_NAMES = frozenset({
    "aflow-review-squash",
    "aflow-review-checkpoint",
    "aflow-review-final",
})
_PLAN_BRANCH_LINE_RE = re.compile(r"^(\s*-\s+Plan Branch:\s+`)([^`]+)(`.*)$", re.MULTILINE)


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


@dataclass(frozen=True)
class _PreparedPrimaryPlanForMerge:
    plan_path: Path
    original_text: str | None


def _turn_artifact_display_path(repo_root: Path, turn_dir: Path, filename: str) -> str | None:
    artifact_path = turn_dir / filename
    if not artifact_path.is_file():
        return None
    if not artifact_path.read_text(encoding="utf-8").strip():
        return None
    return str(artifact_path.relative_to(repo_root))


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

    next_checkpoint = "-"
    work_on_next_checkpoint_cmd = ""
    if (
        "{NEXT_CP}" in prompt_text
        or "{WORK_ON_NEXT_CHECKPOINT_CMD}" in prompt_text
    ):
        try:
            active_plan = load_plan_tolerant(active_plan_path)
        except PlanParseError as exc:
            if "no checkpoint sections were found" not in str(exc):
                raise WorkflowError(str(exc)) from exc
        else:
            checkpoint_index = active_plan.parsed_plan.snapshot.current_checkpoint_index
            if checkpoint_index is not None:
                next_checkpoint = str(checkpoint_index)
                work_on_next_checkpoint_cmd = (
                    f"Work only on Checkpoint #{checkpoint_index}. "
                    "Do not repeat earlier checkpoints, and do not skip ahead."
                )
    prompt_text = prompt_text.replace("{ORIGINAL_PLAN_PATH}", str(original_plan_path))
    prompt_text = prompt_text.replace("{NEW_PLAN_PATH}", str(new_plan_path))
    prompt_text = prompt_text.replace("{ACTIVE_PLAN_PATH}", str(active_plan_path))
    prompt_text = prompt_text.replace("{NEXT_CP}", next_checkpoint)
    prompt_text = prompt_text.replace("{WORK_ON_NEXT_CHECKPOINT_CMD}", work_on_next_checkpoint_cmd)
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


def _update_plan_branch(path: Path, branch_name: str) -> bool:
    try:
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8")
        updated = _PLAN_BRANCH_LINE_RE.sub(
            lambda match: f"{match.group(1)}{branch_name}{match.group(3)}",
            text,
            count=1,
        )
        if updated == text:
            return False
        path.write_text(updated, encoding="utf-8")
        return True
    except OSError as exc:
        raise WorkflowError(
            f"failed to update Plan Branch in original plan '{path}' to '{branch_name}': {exc}"
        ) from exc


def _sync_plan_branch_for_execution(
    original_plan_path: Path,
    exec_ctx: ExecutionContext | None,
) -> None:
    if exec_ctx is None:
        return
    _update_plan_branch(original_plan_path, exec_ctx.feature_branch)


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


def _finalize_original_plan_if_complete(
    repo_root: Path,
    original_plan_path: Path,
    *,
    snapshot: PlanSnapshot,
) -> Path:
    if not snapshot.is_complete:
        return original_plan_path
    done_plan_path = _done_plan_path(repo_root, original_plan_path)
    if done_plan_path is None:
        return original_plan_path
    return move_completed_plan_to_done(repo_root, original_plan_path)


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
    raise FileNotFoundError(
        f"{original_plan_path}: original plan file is missing after the turn; "
        "workflow-owned finalization requires agents to keep the original plan "
        "under plans/in-progress until terminal success"
    )


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
_STOP_SENTINEL_PLACEHOLDER_REASON = "<reason>"


def _iter_non_fenced_lines(text: str):
    in_fence = False
    fence_char: str | None = None
    fence_len = 0

    for line in text.splitlines():
        fence_match = FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
                fence_char = None
                fence_len = 0
            continue

        if not in_fence:
            yield line


def _detect_stop_marker(stdout: str, stderr: str) -> str | None:
    for text in (stdout, stderr):
        for line in _iter_non_fenced_lines(text):
            if line.startswith(_STOP_SENTINEL_PREFIX):
                reason = line[len(_STOP_SENTINEL_PREFIX):].strip()
                if reason == _STOP_SENTINEL_PLACEHOLDER_REASON:
                    continue
                return reason or _STOP_SENTINEL_FALLBACK_REASON
    return None


_BRANCH_STEM_MAX_LEN = 50


def _sanitize_plan_stem(stem: str) -> str:
    stem = stem.lower()
    stem = re.sub(r"[^a-z0-9-]", "-", stem)
    stem = re.sub(r"-+", "-", stem)
    stem = stem.strip("-")
    return stem[:_BRANCH_STEM_MAX_LEN] or "plan"


def _run_git(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _is_git_tracked(repo_root: Path, path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    rc, _, _ = _run_git(
        ["ls-files", "--error-unmatch", "--", rel.as_posix()],
        cwd=repo_root,
    )
    return rc == 0


@dataclass(frozen=True)
class _LifecyclePlan:
    main_branch: str
    feature_branch: str
    worktree_path: Path | None
    setup: tuple[str, ...]
    teardown: tuple[str, ...]


def _lifecycle_preflight(
    primary_root: Path,
    plan_path: Path,
    wf: WorkflowConfig,
    aflow_section: AflowSection,
) -> _LifecyclePlan | None:
    setup = wf.setup or ()
    teardown = wf.teardown or ()

    if not setup:
        return None

    main_branch = wf.main_branch
    if not main_branch:
        raise WorkflowError(
            "workflow uses lifecycle setup but main_branch is not configured"
        )

    rc, _, _ = _run_git(["show-ref", "--verify", f"refs/heads/{main_branch}"], cwd=primary_root)
    if rc != 0:
        raise WorkflowError(
            f"lifecycle preflight: branch '{main_branch}' does not exist locally in '{primary_root}'"
        )

    rc, current_branch, err = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=primary_root)
    if rc != 0:
        raise WorkflowError(
            f"lifecycle preflight: cannot determine current branch in '{primary_root}': {err}"
        )
    if current_branch != main_branch:
        raise WorkflowError(
            f"lifecycle preflight: current branch is '{current_branch}' "
            f"but workflow requires starting from '{main_branch}'"
        )

    rc, status_out, _ = _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all"], cwd=primary_root
    )
    if rc != 0:
        raise WorkflowError(
            f"lifecycle preflight: cannot check working tree state in '{primary_root}'"
        )

    uses_worktree = "worktree" in setup
    if status_out.strip():
        if uses_worktree:
            _, non_plan_paths = classify_dirtiness_by_prefix(status_out)
            if non_plan_paths:
                raise WorkflowError(
                    f"lifecycle preflight: primary checkout at '{primary_root}' has non-plan dirtiness: "
                    f"{', '.join(non_plan_paths[:3])}{'...' if len(non_plan_paths) > 3 else ''}"
                )
        else:
            raise WorkflowError(
                f"lifecycle preflight: primary checkout at '{primary_root}' has uncommitted changes"
            )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    stem = _sanitize_plan_stem(plan_path.stem)
    branch_prefix = (aflow_section.branch_prefix or "aflow").rstrip("-")
    feature_branch = f"{branch_prefix}-{stem}-{ts}"

    rc, _, _ = _run_git(["show-ref", "--verify", f"refs/heads/{feature_branch}"], cwd=primary_root)
    if rc == 0:
        raise WorkflowError(
            f"lifecycle preflight: branch '{feature_branch}' already exists"
        )

    uses_worktree = "worktree" in setup
    worktree_path: Path | None = None

    if uses_worktree:
        try:
            plan_path.resolve().relative_to(primary_root.resolve())
        except ValueError:
            raise WorkflowError(
                f"lifecycle preflight: plan file '{plan_path}' must be under "
                f"the primary repo root '{primary_root}' for worktree workflows"
            )
        if not plan_path.is_file():
            raise WorkflowError(
                f"lifecycle preflight: plan file '{plan_path}' must exist "
                "for worktree workflows"
            )

        worktree_root_str = aflow_section.worktree_root
        if not worktree_root_str:
            raise WorkflowError(
                "lifecycle preflight: worktree workflow requires [aflow].worktree_root to be set"
            )

        worktree_root = Path(worktree_root_str).expanduser().resolve()

        try:
            worktree_root.relative_to(primary_root.resolve())
            raise WorkflowError(
                f"lifecycle preflight: worktree_root '{worktree_root}' "
                f"must not be inside the primary repo root '{primary_root}'"
            )
        except ValueError:
            pass

        worktree_dir_prefix = (aflow_section.worktree_prefix or "aflow").rstrip("-")
        worktree_dir_name = f"{worktree_dir_prefix}-{stem}-{ts}"
        worktree_path = worktree_root / worktree_dir_name

        if worktree_path.exists():
            raise WorkflowError(
                f"lifecycle preflight: worktree path '{worktree_path}' already exists on disk"
            )

        rc, wt_list, _ = _run_git(["worktree", "list", "--porcelain"], cwd=primary_root)
        if rc == 0:
            for line in wt_list.splitlines():
                if line.startswith("worktree "):
                    registered = line[len("worktree "):]
                    if Path(registered).resolve() == worktree_path.resolve():
                        raise WorkflowError(
                            f"lifecycle preflight: path '{worktree_path}' is already "
                            f"registered as a git worktree"
                        )

    return _LifecyclePlan(
        main_branch=main_branch,
        feature_branch=feature_branch,
        worktree_path=worktree_path,
        setup=setup,
        teardown=teardown,
    )


def _setup_branch_only(
    primary_root: Path,
    main_branch: str,
    feature_branch: str,
) -> None:
    rc, _, err = _run_git(
        ["checkout", "-b", feature_branch, main_branch], cwd=primary_root
    )
    if rc != 0:
        raise WorkflowError(
            f"lifecycle setup: cannot create branch '{feature_branch}' "
            f"from '{main_branch}': {err}"
        )


def _setup_worktree(
    primary_root: Path,
    main_branch: str,
    feature_branch: str,
    worktree_path: Path,
) -> None:
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    rc, _, err = _run_git(
        ["worktree", "add", "-b", feature_branch, str(worktree_path), main_branch],
        cwd=primary_root,
    )
    if rc != 0:
        raise WorkflowError(
            f"lifecycle setup: cannot create worktree at '{worktree_path}' "
            f"with branch '{feature_branch}' from '{main_branch}': {err}"
        )


def _do_lifecycle_setup(
    primary_root: Path,
    plan: _LifecyclePlan,
) -> ExecutionContext:
    if "worktree" in plan.setup:
        assert plan.worktree_path is not None
        _setup_worktree(primary_root, plan.main_branch, plan.feature_branch, plan.worktree_path)
        execution_root = plan.worktree_path
    else:
        _setup_branch_only(primary_root, plan.main_branch, plan.feature_branch)
        execution_root = primary_root
    return ExecutionContext(
        primary_repo_root=primary_root,
        execution_repo_root=execution_root,
        main_branch=plan.main_branch,
        feature_branch=plan.feature_branch,
        worktree_path=plan.worktree_path,
        setup=plan.setup,
        teardown=plan.teardown,
    )


def _exec_plan_path(path: Path, exec_ctx: ExecutionContext | None) -> Path:
    if exec_ctx is None or exec_ctx.worktree_path is None:
        return path
    try:
        rel = path.resolve().relative_to(exec_ctx.primary_repo_root.resolve())
        return exec_ctx.execution_repo_root / rel
    except ValueError:
        return path


def _sync_plan_to_worktree(primary_plan_path: Path, exec_ctx: ExecutionContext | None) -> None:
    """Copy the original plan from primary checkout to worktree if needed.

    Creates parent directories in the worktree if they don't exist.
    Raises WorkflowError if the source is unreadable or the copy fails.
    """
    if exec_ctx is None or exec_ctx.worktree_path is None:
        return

    exec_plan_path = _exec_plan_path(primary_plan_path, exec_ctx)

    try:
        if not primary_plan_path.is_file():
            raise WorkflowError(
                f"_sync_plan_to_worktree: original plan file not found: {primary_plan_path}"
            )

        exec_plan_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(primary_plan_path, exec_plan_path)
    except (OSError, IOError) as exc:
        raise WorkflowError(
            f"_sync_plan_to_worktree: failed to copy original plan from "
            f"{primary_plan_path} to {exec_plan_path}: {exc}"
        ) from exc


def _sync_plan_from_worktree(primary_plan_path: Path, exec_ctx: ExecutionContext | None) -> None:
    """Copy the original plan from worktree back to primary checkout if it was edited.

    Raises WorkflowError if the copy fails.
    Sync happens regardless of harness success/failure — if the plan was edited,
    the primary copy must reflect those edits for restart correctness.
    """
    if exec_ctx is None or exec_ctx.worktree_path is None:
        return

    exec_plan_path = _exec_plan_path(primary_plan_path, exec_ctx)

    try:
        if not exec_plan_path.is_file():
            raise WorkflowError(
                f"_sync_plan_from_worktree: worktree plan file not found: {exec_plan_path}"
            )

        shutil.copyfile(exec_plan_path, primary_plan_path)
    except (OSError, IOError) as exc:
        raise WorkflowError(
            f"_sync_plan_from_worktree: failed to copy original plan from "
            f"{exec_plan_path} back to {primary_plan_path}: {exc}"
        ) from exc


def _prepare_primary_plan_for_merge(
    primary_root: Path,
    original_plan_path: Path,
) -> _PreparedPrimaryPlanForMerge | None:
    if not original_plan_path.exists():
        return None

    try:
        original_text = original_plan_path.read_text(encoding="utf-8")
        tracked_in_git = _is_git_tracked(primary_root, original_plan_path)
        if tracked_in_git:
            try:
                rel = original_plan_path.resolve().relative_to(primary_root.resolve())
            except ValueError:
                return _PreparedPrimaryPlanForMerge(
                    plan_path=original_plan_path,
                    original_text=original_text,
                )
            rc, _, err = _run_git(["checkout", "--", rel.as_posix()], cwd=primary_root)
            if rc != 0:
                raise WorkflowError(
                    f"lifecycle teardown: failed to reset tracked original plan "
                    f"'{original_plan_path}' before merge: {err}"
                )
        else:
            original_plan_path.unlink()
    except OSError as exc:
        raise WorkflowError(
            f"lifecycle teardown: failed to prepare original plan '{original_plan_path}' "
            f"for merge: {exc}"
        ) from exc

    return _PreparedPrimaryPlanForMerge(
        plan_path=original_plan_path,
        original_text=original_text,
    )


def _restore_primary_plan_after_merge(
    prepared: _PreparedPrimaryPlanForMerge | None,
) -> None:
    if prepared is None:
        return
    if prepared.original_text is None:
        return
    try:
        prepared.plan_path.parent.mkdir(parents=True, exist_ok=True)
        prepared.plan_path.write_text(prepared.original_text, encoding="utf-8")
    except OSError as exc:
        raise WorkflowError(
            f"lifecycle teardown: failed to restore original plan "
            f"'{prepared.plan_path}' after merge: {exc}"
        ) from exc


_MERGE_BUILTIN_INSTRUCTION = "Use the `aflow-merge` skill to merge the feature branch into the target branch."


def render_merge_prompt(
    prompt_text: str,
    *,
    config_dir: Path,
    working_dir: Path,
    exec_ctx: ExecutionContext,
    original_plan_path: Path,
    new_plan_path: Path,
    active_plan_path: Path,
) -> str:
    rendered = render_prompt(
        prompt_text,
        config_dir=config_dir,
        working_dir=working_dir,
        original_plan_path=original_plan_path,
        new_plan_path=new_plan_path,
        active_plan_path=active_plan_path,
    )
    worktree_path_str = str(exec_ctx.worktree_path) if exec_ctx.worktree_path else ""
    rendered = rendered.replace("{MAIN_BRANCH}", exec_ctx.main_branch)
    rendered = rendered.replace("{FEATURE_BRANCH}", exec_ctx.feature_branch)
    rendered = rendered.replace("{PRIMARY_REPO_ROOT}", str(exec_ctx.primary_repo_root))
    rendered = rendered.replace("{EXECUTION_REPO_ROOT}", str(exec_ctx.execution_repo_root))
    rendered = rendered.replace("{FEATURE_WORKTREE_PATH}", worktree_path_str)
    return rendered


def _build_merge_user_prompt(
    wf: WorkflowConfig,
    workflow_config: WorkflowUserConfig,
    *,
    exec_ctx: ExecutionContext,
    config_dir: Path,
    working_dir: Path,
    original_plan_path: Path,
    active_plan_path: Path,
    new_plan_path: Path,
) -> str:
    parts = [_MERGE_BUILTIN_INSTRUCTION]
    for prompt_key in (wf.merge_prompt or ()):
        if prompt_key not in workflow_config.prompts:
            raise WorkflowError(f"merge_prompt references unknown prompt '{prompt_key}'")
        raw = workflow_config.prompts[prompt_key]
        rendered = render_merge_prompt(
            raw,
            config_dir=config_dir,
            working_dir=working_dir,
            exec_ctx=exec_ctx,
            original_plan_path=original_plan_path,
            active_plan_path=active_plan_path,
            new_plan_path=new_plan_path,
        )
        parts.append(rendered)
    return "\n\n".join(parts)


def _verify_merge_success(
    primary_root: Path,
    main_branch: str,
    feature_branch: str,
    *,
    original_plan_path: Path | None = None,
) -> str | None:
    """Returns None on success, or a description of which check failed."""
    rc, out, _ = _run_git(["ls-files", "--unmerged"], cwd=primary_root)
    if rc != 0 or out.strip():
        return "unmerged index entries remain after merge"

    rc, out, _ = _run_git(
        ["status", "--porcelain=v1", "--untracked-files=all"],
        cwd=primary_root,
    )
    if rc != 0:
        return "working tree is not clean after merge"
    dirty_lines = [
        line for line in out.splitlines()
        if line.strip()
        and not _is_ignored_merge_status_line(
            line,
            primary_root=primary_root,
            original_plan_path=original_plan_path,
        )
    ]
    if dirty_lines:
        return "working tree is not clean after merge"

    rc, head_ref, _ = _run_git(["symbolic-ref", "HEAD"], cwd=primary_root)
    if rc != 0 or head_ref.strip() != f"refs/heads/{main_branch}":
        return f"HEAD is not on '{main_branch}' after merge (got '{head_ref.strip()}')"

    rc, _, _ = _run_git(
        ["merge-base", "--is-ancestor", feature_branch, main_branch],
        cwd=primary_root,
    )
    if rc != 0:
        return f"feature branch '{feature_branch}' is not an ancestor of '{main_branch}' after merge"

    return None


def _is_ignored_merge_status_line(
    line: str,
    *,
    primary_root: Path,
    original_plan_path: Path | None,
) -> bool:
    if len(line) < 3:
        return False
    xy = line[:2]
    path = line[3:] if len(line) >= 4 and line[2] == " " else line[2:]
    path = path.strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    path = path.strip('"')
    if xy == "??" and (
        path == ".aflow"
        or path.startswith(".aflow/")
        or path == "plans/backups"
        or path.startswith("plans/backups/")
    ):
        return True
    if original_plan_path is None:
        return False
    try:
        rel = original_plan_path.resolve().relative_to(primary_root.resolve()).as_posix()
    except ValueError:
        return False
    return path == rel


def _rm_worktree_safe(primary_root: Path, worktree_path: Path) -> None:
    rc, _, err = _run_git(
        ["worktree", "remove", "--force", str(worktree_path)],
        cwd=primary_root,
    )
    if rc != 0:
        raise WorkflowError(
            f"lifecycle teardown: failed to remove worktree '{worktree_path}': {err}"
        )


def _execute_merge_handoff(
    exec_ctx: ExecutionContext,
    wf: WorkflowConfig,
    workflow_config: WorkflowUserConfig,
    *,
    team_name: str | None,
    adapter: HarnessAdapter | None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None,
    config_dir: Path,
    working_dir: Path,
    original_plan_path: Path,
    active_plan_path: Path,
    new_plan_path: Path,
    banner: BannerRenderer,
    state: ControllerState,
) -> subprocess.CompletedProcess[str]:
    primary_root = exec_ctx.primary_repo_root
    team_lead_role = workflow_config.aflow.team_lead
    if not team_lead_role:
        raise WorkflowError("merge teardown requires [aflow].team_lead to be configured")

    team_lead_selector = resolve_role_selector(
        team_lead_role, team_name, workflow_config, step_path="merge teardown"
    )
    resolved = resolve_profile(team_lead_selector, workflow_config, step_path="merge teardown")

    user_prompt = _build_merge_user_prompt(
        wf, workflow_config,
        exec_ctx=exec_ctx,
        config_dir=config_dir,
        working_dir=working_dir,
        original_plan_path=original_plan_path,
        active_plan_path=active_plan_path,
        new_plan_path=new_plan_path,
    )

    merge_adapter = adapter or get_adapter(resolved.harness_name)
    invocation = merge_adapter.build_invocation(
        repo_root=primary_root,
        model=resolved.model,
        system_prompt="",
        user_prompt=user_prompt,
        effort=resolved.effort,
    )

    if runner is None:
        return _run_process(invocation, primary_root, banner, state)
    return runner(
        list(invocation.argv),
        cwd=str(primary_root),
        env={**os.environ, **invocation.env},
        capture_output=True,
        text=True,
        check=False,
    )


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

    lifecycle_plan = _lifecycle_preflight(
        config.repo_root, config.plan_path, wf, workflow_config.aflow
    )
    exec_ctx: ExecutionContext | None = None

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
        prior_original_plan_path = original_plan_path
        finalized_original_plan_path = _finalize_original_plan_if_complete(
            config.repo_root,
            original_plan_path,
            snapshot=original_snapshot,
        )
        if finalized_original_plan_path != prior_original_plan_path:
            original_plan_path = finalized_original_plan_path
            if active_plan_path == prior_original_plan_path:
                active_plan_path = original_plan_path
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

    if lifecycle_plan is not None:
        try:
            exec_ctx = _do_lifecycle_setup(config.repo_root, lifecycle_plan)
            _sync_plan_branch_for_execution(original_plan_path, exec_ctx)
        except WorkflowError as exc:
            state.status_message = "failed"
            banner.stop(state)
            summary = _format_failure(
                reason=exc.summary,
                run_dir=run_paths.run_dir,
                snapshot=original_snapshot,
            )
            write_run_metadata(
                run_paths, config, state, status="failed", failure_reason=summary,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                active_plan_path=active_plan_path,
            )
            raise WorkflowError(summary, run_dir=run_paths.run_dir) from exc

    execution_repo_root = exec_ctx.execution_repo_root if exec_ctx else config.repo_root

    def _raise_pre_turn_failure(
        *,
        reason: str,
        snapshot: PlanSnapshot,
        active_path: Path,
        new_path: Path | None,
    ) -> None:
        state.status_message = "failed"
        banner.stop(state)
        summary = _format_failure(
            reason=reason,
            run_dir=run_paths.run_dir,
            snapshot=snapshot,
        )
        write_run_metadata(
            run_paths, config, state, status="failed", failure_reason=summary,
            execution_context=exec_ctx,
            last_snapshot=state.last_snapshot,
            turns_completed=state.turns_completed,
            workflow_name=workflow_name, original_plan_path=original_plan_path,
            current_step_name=current_step_name, active_plan_path=active_path,
            new_plan_path=new_path,
        )
        raise WorkflowError(summary, run_dir=run_paths.run_dir)

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
        state.turn_history[-1].turn_dir = turn_dir
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
        record.turn_dir = turn_dir
        record.stdout_artifact_path = _turn_artifact_display_path(run_paths.repo_root, turn_dir, "stdout.txt")
        record.stderr_artifact_path = _turn_artifact_display_path(run_paths.repo_root, turn_dir, "stderr.txt")
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
            snapshot_before = retry_ctx.snapshot_before
            try:
                user_prompt = retry_ctx.base_user_prompt + "\n\n" + _build_retry_appendix(retry_ctx.parse_error_str)
                invocation = step_adapter.build_invocation(
                    repo_root=execution_repo_root,
                    model=resolved.model,
                    system_prompt="",
                    user_prompt=user_prompt,
                    effort=resolved.effort,
                )
            except Exception as exc:
                _raise_pre_turn_failure(
                    reason=str(exc),
                    snapshot=snapshot_before,
                    active_path=active_plan_path,
                    new_path=new_plan_path,
                )
        else:
            state.status_message = f"running turn {turn_number}: step {current_step_name}"
            write_run_metadata(
                run_paths, config, state, status="running", last_snapshot=state.last_snapshot,
                workflow_name=workflow_name, original_plan_path=original_plan_path,
                current_step_name=current_step_name, active_plan_path=active_plan_path,
            )

            _sync_plan_to_worktree(original_plan_path, exec_ctx)

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
            snapshot_before = state.last_snapshot

            _sync_plan_to_worktree(original_plan_path, exec_ctx)

            try:
                user_prompt = render_step_prompts(
                    step,
                    workflow_config,
                    config_dir=config_dir,
                    working_dir=working_dir,
                    original_plan_path=_exec_plan_path(original_plan_path, exec_ctx),
                    new_plan_path=_exec_plan_path(new_plan_path, exec_ctx),
                    active_plan_path=_exec_plan_path(active_plan_path, exec_ctx),
                )

                if config.extra_instructions:
                    extra_text = " ".join(config.extra_instructions).strip()
                    user_prompt = "\n\n".join((user_prompt, extra_text))

                invocation = step_adapter.build_invocation(
                    repo_root=execution_repo_root,
                    model=resolved.model,
                    system_prompt="",
                    user_prompt=user_prompt,
                    effort=resolved.effort,
                )
            except Exception as exc:
                _raise_pre_turn_failure(
                    reason=str(exc),
                    snapshot=snapshot_before,
                    active_path=active_plan_path,
                    new_path=new_plan_path,
                )

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
            completed = _run_process(invocation, execution_repo_root, banner, state)
        else:
            assert runner is not None
            completed = runner(
                list(invocation.argv),
                cwd=str(execution_repo_root),
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
            exec_original = _exec_plan_path(original_plan_path, exec_ctx)
            resolved_exec_plan_path = _resolve_post_turn_original_plan_path(
                execution_repo_root,
                exec_original,
                completed_returncode=completed.returncode,
            )
            parsed_after = load_plan(resolved_exec_plan_path)

            # Sync the original plan back after every worktree turn so the
            # primary checkout remains the durable source of truth between turns.
            if exec_ctx is not None and exec_ctx.worktree_path is not None:
                _sync_plan_from_worktree(original_plan_path, exec_ctx)

            if resolved_exec_plan_path != exec_original:
                if exec_ctx is not None and exec_ctx.worktree_path is not None:
                    try:
                        rel = resolved_exec_plan_path.relative_to(execution_repo_root)
                        original_plan_path = config.repo_root / rel
                    except ValueError:
                        original_plan_path = resolved_exec_plan_path
                else:
                    original_plan_path = resolved_exec_plan_path
                if active_plan_path == config.plan_path:
                    active_plan_path = original_plan_path
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
        new_plan_exists = _exec_plan_path(new_plan_path, exec_ctx).is_file()

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
            execution_context=exec_ctx,
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

            merge_status: str | None = None
            merge_failure_reason: str | None = None

            if exec_ctx is not None and "merge" in exec_ctx.teardown:
                prepared_primary_plan: _PreparedPrimaryPlanForMerge | None = None
                try:
                    prepared_primary_plan = _prepare_primary_plan_for_merge(
                        config.repo_root,
                        original_plan_path,
                    )
                    merge_completed = _execute_merge_handoff(
                        exec_ctx, wf, workflow_config,
                        team_name=team_name,
                        adapter=adapter,
                        runner=runner,
                        config_dir=config_dir,
                        working_dir=working_dir,
                        original_plan_path=original_plan_path,
                        active_plan_path=active_plan_path,
                        new_plan_path=new_plan_path,
                        banner=banner,
                        state=state,
                    )
                except WorkflowError as exc:
                    _restore_primary_plan_after_merge(prepared_primary_plan)
                    merge_status = "failed"
                    merge_failure_reason = exc.summary
                else:
                    stop_reason = _detect_stop_marker(merge_completed.stdout, merge_completed.stderr)
                    if stop_reason is not None:
                        _restore_primary_plan_after_merge(prepared_primary_plan)
                        merge_status = "failed"
                        merge_failure_reason = f"AFLOW_STOP: {stop_reason}"
                    elif merge_completed.returncode != 0:
                        _restore_primary_plan_after_merge(prepared_primary_plan)
                        merge_status = "failed"
                        merge_failure_reason = f"merge agent exited with code {merge_completed.returncode}"
                    else:
                        _restore_primary_plan_after_merge(prepared_primary_plan)
                        check_failure = _verify_merge_success(
                            config.repo_root,
                            exec_ctx.main_branch,
                            exec_ctx.feature_branch,
                            original_plan_path=original_plan_path,
                        )
                        if check_failure is not None:
                            merge_status = "failed"
                            merge_failure_reason = f"merge verification failed: {check_failure}"
                        else:
                            merge_status = "success"
                            if "rm_worktree" in exec_ctx.teardown and exec_ctx.worktree_path is not None:
                                try:
                                    _rm_worktree_safe(config.repo_root, exec_ctx.worktree_path)
                                except WorkflowError as exc:
                                    merge_status = "failed"
                                    merge_failure_reason = exc.summary

            if merge_status == "failed":
                state.status_message = "failed"
                summary = _format_failure(
                    reason=merge_failure_reason or "merge teardown failed",
                    run_dir=run_paths.run_dir,
                    snapshot=post_snapshot,
                )
                write_run_metadata(
                    run_paths, config, state, status="failed",
                    merge_status=merge_status,
                    merge_failure_reason=merge_failure_reason,
                    execution_context=exec_ctx,
                    last_snapshot=post_snapshot,
                    turns_completed=state.turns_completed,
                    workflow_name=workflow_name, original_plan_path=original_plan_path,
                    current_step_name=current_step_name, active_plan_path=active_plan_path,
                    new_plan_path=new_plan_path,
                )
                prune_old_runs(run_paths.runs_root, config.keep_runs)
                banner.stop(state)
                raise WorkflowError(summary, run_dir=run_paths.run_dir)

            prior_original_plan_path = original_plan_path
            finalized_original_plan_path = _finalize_original_plan_if_complete(
                config.repo_root,
                original_plan_path,
                snapshot=post_snapshot,
            )
            if finalized_original_plan_path != prior_original_plan_path:
                original_plan_path = finalized_original_plan_path
                if active_plan_path == prior_original_plan_path:
                    active_plan_path = original_plan_path

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
                merge_status=merge_status,
                execution_context=exec_ctx,
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
