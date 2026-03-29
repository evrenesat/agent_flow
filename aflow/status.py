from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.text import Text

from .plan import PlanSnapshot
from .run_state import ControllerState


_STDERR_CONSOLE = Console(file=None, stderr=True)


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


def build_banner(
    *,
    config_harness: str,
    config_model: str,
    config_effort: str | None,
    config_max_turns: int,
    config_plan_path: Path,
    state: ControllerState,
) -> Panel:
    snapshot = state.last_snapshot
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan")
    table.add_column()

    table.add_row("Elapsed", _elapsed(state.run_started_at))
    table.add_row("Harness", config_harness)
    table.add_row("Model", config_model)
    table.add_row("Effort", config_effort or "none")
    table.add_row("Checkpoint", _checkpoint_display(snapshot))
    name = snapshot.current_checkpoint_name or "-"
    table.add_row("Name", name)
    table.add_row("Turn", f"{state.active_turn}/{config_max_turns}")
    table.add_row("Issues", str(state.issues_accumulated))
    table.add_row("Plan", str(config_plan_path))
    table.add_row("Status", state.status_message)

    title = Text("aflow", style="bold magenta")
    return Panel(table, title=title, border_style="blue")


class BannerRenderer:
    def __init__(
        self,
        *,
        config_harness: str,
        config_model: str,
        config_effort: str | None,
        config_max_turns: int,
        config_plan_path: Path,
        console: Console | None = None,
    ) -> None:
        self._config_harness = config_harness
        self._config_model = config_model
        self._config_effort = config_effort
        self._config_max_turns = config_max_turns
        self._config_plan_path = config_plan_path
        self._console = console or _STDERR_CONSOLE
        self._live: Live | None = None

    def start(self, state: ControllerState) -> None:
        panel = build_banner(
            config_harness=self._config_harness,
            config_model=self._config_model,
            config_effort=self._config_effort,
            config_max_turns=self._config_max_turns,
            config_plan_path=self._config_plan_path,
            state=state,
        )
        self._live = Live(panel, console=self._console, refresh_per_second=1)
        self._live.start()

    def update(self, state: ControllerState) -> None:
        if self._live is None:
            return
        panel = build_banner(
            config_harness=self._config_harness,
            config_model=self._config_model,
            config_effort=self._config_effort,
            config_max_turns=self._config_max_turns,
            config_plan_path=self._config_plan_path,
            state=state,
        )
        self._live.update(panel)

    def stop(self, state: ControllerState) -> None:
        if self._live is None:
            return
        panel = build_banner(
            config_harness=self._config_harness,
            config_model=self._config_model,
            config_effort=self._config_effort,
            config_max_turns=self._config_max_turns,
            config_plan_path=self._config_plan_path,
            state=state,
        )
        self._live.update(panel)
        self._live.stop()
        self._live = None
