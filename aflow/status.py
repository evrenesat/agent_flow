from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .plan import PlanSnapshot
from .run_state import ControllerState

_RICH_AVAILABLE = False
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.live import Live
    from rich.text import Text

    _RICH_AVAILABLE = True
except ImportError:
    Console = object  # type: ignore[assignment,misc]
    Live = object  # type: ignore[assignment,misc]

_STDERR_CONSOLE: Console | None = None
if _RICH_AVAILABLE:
    _STDERR_CONSOLE = Console(file=None, stderr=True)  # type: ignore[call-arg]


def _elapsed(started_at: datetime) -> str:
    delta = datetime.now(timezone.utc) - started_at
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _checkpoint_display(snapshot: PlanSnapshot) -> str:
    if snapshot.is_complete:
        return f"done ({snapshot.total_checkpoint_count}/{snapshot.total_checkpoint_count})"
    if snapshot.current_checkpoint_index is not None:
        return f"{snapshot.current_checkpoint_index}/{snapshot.total_checkpoint_count}"
    return f"0/{snapshot.total_checkpoint_count}"


def _status_display(state: ControllerState) -> str:
    if state.status_message == "completed" and state.end_reason is not None:
        if state.end_reason == "already_complete":
            return "completed: already complete"
        if state.end_reason == "done":
            return "completed: done"
        if state.end_reason == "max_turns_reached":
            return "completed: max turns reached"
        return "completed: transition to END"
    return state.status_message


def build_banner(
    *,
    workflow_name: str | None = None,
    current_step_name: str | None = None,
    config_harness: str | None = None,
    config_model: str | None = None,
    config_effort: str | None = None,
    config_max_turns: int,
    config_plan_path: Path,
    original_plan_path: Path | None = None,
    active_plan_path: Path | None = None,
    new_plan_path: Path | None = None,
    state: ControllerState,
) -> Panel | None:
    if not _RICH_AVAILABLE:
        return None
    snapshot = state.last_snapshot
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan")
    table.add_column()

    table.add_row("Elapsed", _elapsed(state.run_started_at))

    if workflow_name is not None:
        table.add_row("Workflow", workflow_name)
    if current_step_name is not None:
        table.add_row("Step", current_step_name)

    if config_harness is not None:
        table.add_row("Harness", config_harness)
    if config_model is not None:
        table.add_row("Model", config_model)
    elif workflow_name is None:
        table.add_row("Model", "default")
    if config_effort is not None:
        table.add_row("Effort", config_effort)
    elif workflow_name is None:
        table.add_row("Effort", "none")

    table.add_row("Checkpoint", _checkpoint_display(snapshot))
    name = snapshot.current_checkpoint_name or "-"
    table.add_row("Name", name)
    table.add_row("Turn", f"{state.active_turn}/{config_max_turns}")
    table.add_row("Issues", str(state.issues_accumulated))

    if original_plan_path is not None:
        table.add_row("Original Plan", original_plan_path.name)
    if active_plan_path is not None:
        table.add_row("Active Plan", active_plan_path.name)
    if new_plan_path is not None:
        table.add_row("Generated Plan", new_plan_path.name)

    if workflow_name is None:
        table.add_row("Plan", str(config_plan_path))

    table.add_row("Status", _status_display(state))

    title = Text("aflow", style="bold magenta")
    return Panel(table, title=title, border_style="blue")


class BannerRenderer:
    def __init__(
        self,
        *,
        config_harness: str | None = None,
        config_model: str | None = None,
        config_effort: str | None = None,
        config_max_turns: int,
        config_plan_path: Path,
        workflow_name: str | None = None,
        current_step_name: str | None = None,
        original_plan_path: Path | None = None,
        active_plan_path: Path | None = None,
        new_plan_path: Path | None = None,
        console: Console | None = None,
    ) -> None:
        self._config_harness = config_harness
        self._config_model = config_model
        self._config_effort = config_effort
        self._config_max_turns = config_max_turns
        self._config_plan_path = config_plan_path
        self._workflow_name = workflow_name
        self._current_step_name = current_step_name
        self._original_plan_path = original_plan_path
        self._active_plan_path = active_plan_path
        self._new_plan_path = new_plan_path
        self._console = console or _STDERR_CONSOLE
        self._live: Live | None = None

    def _build(self, state: ControllerState) -> Panel | None:
        return build_banner(
            workflow_name=self._workflow_name,
            current_step_name=self._current_step_name,
            config_harness=self._config_harness,
            config_model=self._config_model,
            config_effort=self._config_effort,
            config_max_turns=self._config_max_turns,
            config_plan_path=self._config_plan_path,
            original_plan_path=self._original_plan_path,
            active_plan_path=self._active_plan_path,
            new_plan_path=self._new_plan_path,
            state=state,
        )

    def start(self, state: ControllerState) -> None:
        if not _RICH_AVAILABLE:
            return
        panel = self._build(state)
        if panel is None:
            return
        self._live = Live(panel, console=self._console, refresh_per_second=1)
        self._live.start()

    def update(self, state: ControllerState) -> None:
        if self._live is None or not _RICH_AVAILABLE:
            return
        panel = self._build(state)
        if panel is None:
            return
        self._live.update(panel)

    def pause(self) -> None:
        if self._live is None or not _RICH_AVAILABLE:
            return
        self._live.stop()
        self._live = None

    def resume(self, state: ControllerState) -> None:
        if not _RICH_AVAILABLE:
            return
        panel = self._build(state)
        if panel is None:
            return
        self._live = Live(panel, console=self._console, refresh_per_second=1)
        self._live.start()

    def stop(self, state: ControllerState) -> None:
        if self._live is None or not _RICH_AVAILABLE:
            return
        panel = self._build(state)
        if panel is None:
            return
        self._live.update(panel)
        self._live.stop()
        self._live = None
