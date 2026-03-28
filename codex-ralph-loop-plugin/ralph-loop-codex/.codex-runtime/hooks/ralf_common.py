from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import subprocess
from typing import Any


STATE_FILE_NAME = "ralph-loop.local.md"
CHECKPOINT_PATTERN = re.compile(r"^### \[ \] (.+)$", re.MULTILINE)
PROMISE_PATTERN = re.compile(r"<promise>([\s\S]*?)</promise>")


@dataclass
class RalphState:
    path: Path
    active: bool
    runtime: str
    iteration: int
    max_iterations: int
    completion_promise: str
    started_at: str
    plan_path: str
    prompt: str


@dataclass
class StopDecision:
    action: str
    reason: str | None = None
    system_message: str | None = None


def get_repo_root(cwd: str | Path | None = None) -> Path:
    working_dir = Path(cwd or Path.cwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=working_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return Path(result.stdout.strip()).resolve()
    except subprocess.CalledProcessError:
        return working_dir


def state_file_path(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / STATE_FILE_NAME


def _parse_frontmatter_value(raw: str) -> Any:
    value = raw.strip()
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_state_file(path: str | Path) -> RalphState | None:
    state_path = Path(path)
    if not state_path.exists():
        return None

    content = state_path.read_text(encoding="utf-8")
    match = re.match(r"^---\n([\s\S]*?)\n---\n?([\s\S]*)$", content)
    if not match:
        return None

    frontmatter_text, prompt = match.groups()
    frontmatter: dict[str, Any] = {}
    for line in frontmatter_text.splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        frontmatter[key.strip()] = _parse_frontmatter_value(raw_value)

    required_fields = {
        "active",
        "runtime",
        "iteration",
        "max_iterations",
        "completion_promise",
        "started_at",
        "plan_path",
    }
    if not required_fields.issubset(frontmatter):
        return None

    return RalphState(
        path=state_path,
        active=bool(frontmatter["active"]),
        runtime=str(frontmatter["runtime"]),
        iteration=int(frontmatter["iteration"]),
        max_iterations=int(frontmatter["max_iterations"]),
        completion_promise=str(frontmatter["completion_promise"]),
        started_at=str(frontmatter["started_at"]),
        plan_path=str(frontmatter["plan_path"]),
        prompt=prompt.strip(),
    )


def load_state(repo_root: str | Path) -> RalphState | None:
    return parse_state_file(state_file_path(repo_root))


def save_state(state: RalphState) -> None:
    completion_promise = json.dumps(state.completion_promise)
    started_at = json.dumps(state.started_at)
    plan_path = json.dumps(state.plan_path)
    content = (
        "---\n"
        f"active: {'true' if state.active else 'false'}\n"
        f"runtime: {state.runtime}\n"
        f"iteration: {state.iteration}\n"
        f"max_iterations: {state.max_iterations}\n"
        f"completion_promise: {completion_promise}\n"
        f"started_at: {started_at}\n"
        f"plan_path: {plan_path}\n"
        "---\n\n"
        f"{state.prompt.rstrip()}\n"
    )
    state.path.write_text(content, encoding="utf-8")


def completion_promise_found(message: str | None, promise: str) -> bool:
    if not message:
        return False

    match = PROMISE_PATTERN.search(message)
    if match:
        candidate = match.group(1)
    else:
        candidate = message

    normalized_candidate = " ".join(candidate.split())
    normalized_promise = " ".join(promise.split())
    return normalized_candidate == normalized_promise


def read_plan_text(plan_path: str | Path) -> str:
    return Path(plan_path).read_text(encoding="utf-8")


def first_unchecked_checkpoint(plan_text: str) -> str | None:
    match = CHECKPOINT_PATTERN.search(plan_text)
    if not match:
        return None
    return match.group(1).strip()


def deactivate_state(state: RalphState) -> None:
    state.active = False
    save_state(state)


def build_continuation_prompt(state: RalphState, checkpoint_name: str) -> str:
    limit_text = (
        f"{state.iteration}/{state.max_iterations}"
        if state.max_iterations > 0
        else f"{state.iteration}"
    )
    return (
        f"RALF iteration {limit_text}. Continue the active Codex RALF run.\n\n"
        f"Use the plan file at {state.plan_path} as the source of truth. "
        f"Resume from the first unchecked checkpoint: {checkpoint_name}.\n"
        "Re-read the plan from disk before acting. Implement only the current checkpoint scope, "
        "run the required verification commands, update the plan only when the checkpoint passes, "
        "and create the checkpoint commit immediately.\n\n"
        f"If the plan is fully complete, output exactly <promise>{state.completion_promise}</promise>.\n\n"
        f"Original RALF prompt:\n{state.prompt}"
    )


def build_session_context(state: RalphState) -> str | None:
    if not state.active or state.runtime != "codex":
        return None

    plan_path = Path(state.plan_path)
    if not plan_path.exists():
        return (
            "An active Codex RALF state exists, but the recorded plan file is missing. "
            f"State file: {state.path}. Expected plan: {state.plan_path}."
        )

    checkpoint_name = first_unchecked_checkpoint(read_plan_text(plan_path))
    checkpoint_text = checkpoint_name or "no unchecked checkpoints remain"
    limit_text = (
        f"{state.iteration}/{state.max_iterations}"
        if state.max_iterations > 0
        else str(state.iteration)
    )
    return (
        "An active Codex RALF run is loaded from disk.\n"
        f"Plan: {state.plan_path}\n"
        f"Iteration: {limit_text}\n"
        f"Completion promise: {state.completion_promise}\n"
        f"Next checkpoint: {checkpoint_text}\n"
        "Use the plan file and git state as the source of truth."
    )


def evaluate_stop(
    repo_root: str | Path,
    *,
    stop_hook_active: bool,
    last_assistant_message: str | None,
) -> StopDecision:
    state = load_state(repo_root)
    if state is None or not state.active or state.runtime != "codex":
        return StopDecision(action="noop")

    if stop_hook_active:
        return StopDecision(action="noop")

    if completion_promise_found(last_assistant_message, state.completion_promise):
        deactivate_state(state)
        return StopDecision(
            action="stop",
            system_message="RALF completion promise detected. The Codex RALF loop has been deactivated.",
        )

    if state.max_iterations > 0 and state.iteration >= state.max_iterations:
        deactivate_state(state)
        return StopDecision(
            action="stop",
            system_message=(
                f"RALF reached max iterations ({state.max_iterations}). "
                "The Codex RALF loop has been deactivated."
            ),
        )

    plan_path = Path(state.plan_path)
    if not plan_path.exists():
        deactivate_state(state)
        return StopDecision(
            action="stop",
            system_message=(
                "The active RALF plan file is missing, so the Codex RALF loop was deactivated. "
                f"Expected plan: {state.plan_path}"
            ),
        )

    checkpoint_name = first_unchecked_checkpoint(read_plan_text(plan_path))
    if checkpoint_name is None:
        deactivate_state(state)
        return StopDecision(
            action="stop",
            system_message="The active RALF plan has no unchecked checkpoints left. The Codex RALF loop has been deactivated.",
        )

    state.iteration += 1
    save_state(state)
    return StopDecision(
        action="continue",
        reason=build_continuation_prompt(state, checkpoint_name),
    )
