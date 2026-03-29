from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .plan import PlanSnapshot


@dataclass(frozen=True)
class ControllerConfig:
    repo_root: Path
    plan_path: Path
    harness: str
    model: str
    max_turns: int = 15
    stagnation_limit: int = 5
    keep_runs: int = 20
    extra_instructions: tuple[str, ...] = ()
    effort: str | None = None


@dataclass
class ControllerState:
    last_snapshot: PlanSnapshot
    stagnation_turns: int = 0
    turns_completed: int = 0
    issues_accumulated: int = 0
    run_started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    active_turn: int = 0
    status_message: str = "initializing"

    def record_snapshot(self, snapshot: PlanSnapshot) -> bool:
        changed = snapshot.signature != self.last_snapshot.signature
        if changed:
            self.stagnation_turns = 0
        else:
            self.stagnation_turns += 1
            self.issues_accumulated += 1
        self.last_snapshot = snapshot
        self.turns_completed += 1
        return changed


@dataclass(frozen=True)
class ControllerRunResult:
    run_dir: Path
    turns_completed: int
    final_snapshot: PlanSnapshot
    status: str = "completed"
    issues_accumulated: int = 0

    def to_dict(self) -> dict[str, object]:
        from dataclasses import asdict
        return {
            "run_dir": str(self.run_dir),
            "turns_completed": self.turns_completed,
            "final_snapshot": asdict(self.final_snapshot),
            "status": self.status,
            "issues_accumulated": self.issues_accumulated,
        }
