from __future__ import annotations

from dataclasses import dataclass, field, asdict
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


@dataclass
class ControllerState:
    last_snapshot: PlanSnapshot
    stagnation_turns: int = 0
    turns_completed: int = 0

    def record_snapshot(self, snapshot: PlanSnapshot) -> bool:
        changed = snapshot.signature != self.last_snapshot.signature
        if changed:
            self.stagnation_turns = 0
        else:
            self.stagnation_turns += 1
        self.last_snapshot = snapshot
        self.turns_completed += 1
        return changed


@dataclass(frozen=True)
class ControllerRunResult:
    run_dir: Path
    turns_completed: int
    final_snapshot: PlanSnapshot
    status: str = "completed"

    def to_dict(self) -> dict[str, object]:
        return {
            "run_dir": str(self.run_dir),
            "turns_completed": self.turns_completed,
            "final_snapshot": asdict(self.final_snapshot),
            "status": self.status,
        }

