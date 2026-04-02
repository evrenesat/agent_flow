from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .plan import PlanSnapshot


WorkflowEndReason = Literal[
    "already_complete",
    "done",
    "max_turns_reached",
    "transition_end",
]


def describe_end_reason(end_reason: WorkflowEndReason) -> str:
    if end_reason == "already_complete":
        return "the original plan was already complete"
    if end_reason == "done":
        return "DONE evaluated true"
    if end_reason == "max_turns_reached":
        return "MAX_TURNS_REACHED matched"
    return "the workflow selected END"


@dataclass(frozen=True)
class ControllerConfig:
    repo_root: Path
    plan_path: Path
    max_turns: int = 15
    keep_runs: int = 20
    extra_instructions: tuple[str, ...] = ()
    start_step: str | None = None


@dataclass(frozen=True)
class RetryContext:
    step_name: str
    step_profile: str
    resolved_harness_name: str
    resolved_model: str | None
    resolved_effort: str | None
    snapshot_before: PlanSnapshot
    active_plan_path: Path
    new_plan_path: Path
    base_user_prompt: str
    parse_error_str: str
    attempt: int
    retry_limit: int


@dataclass
class TurnRecord:
    turn_number: int
    step_name: str
    resolved_harness_name: str
    resolved_model_display: str
    outcome: str = "running"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    duration_seconds: float | None = None


def format_harness_model_display(
    harness_name: str,
    model: str | None,
    effort: str | None = None,
) -> str:
    model_text = model or "default"
    if effort is not None:
        return f"{harness_name} / {model_text} / {effort}"
    return f"{harness_name} / {model_text}"


@dataclass
class ControllerState:
    last_snapshot: PlanSnapshot
    turns_completed: int = 0
    issues_accumulated: int = 0
    run_started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    active_turn: int = 0
    current_turn_started_at: datetime | None = None
    status_message: str = "initializing"
    selected_start_step: str | None = None
    startup_recovery_used: bool = False
    startup_recovery_reason: str | None = None
    end_reason: WorkflowEndReason | None = None
    pending_retry: RetryContext | None = None
    turn_history: list[TurnRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ControllerRunResult:
    run_dir: Path
    turns_completed: int
    final_snapshot: PlanSnapshot
    status: str = "completed"
    issues_accumulated: int = 0
    end_reason: WorkflowEndReason = "transition_end"

    def to_dict(self) -> dict[str, object]:
        from dataclasses import asdict
        return {
            "run_dir": str(self.run_dir),
            "turns_completed": self.turns_completed,
            "final_snapshot": asdict(self.final_snapshot),
            "status": self.status,
            "issues_accumulated": self.issues_accumulated,
            "end_reason": self.end_reason,
        }
