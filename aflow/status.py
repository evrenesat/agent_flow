from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .plan import PlanSnapshot
from .run_state import ControllerState, TurnRecord, format_harness_model_display
from .config import WorkflowStepConfig

if TYPE_CHECKING:
    from .git_status import GitSummary

_RICH_AVAILABLE = False
try:
    from rich.console import Console
    from rich.console import Group
    from rich.align import Align
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

_UNSET = object()


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


def _git_row(summary: GitSummary) -> str:
    if summary.modified_count == 0 and summary.added_count == 0 and summary.removed_count == 0:
        return f"clean since start | +{summary.lines_added}/-{summary.lines_removed} | {summary.commit_count} commits"
    return (
        f"M {summary.modified_count}, A {summary.added_count}, D {summary.removed_count}"
        f" | +{summary.lines_added}/-{summary.lines_removed}"
        f" | {summary.commit_count} commits"
    )


def _files_row(changed_paths: tuple[str, ...], *, limit: int) -> str | None:
    if not changed_paths:
        return None
    shown = changed_paths[:limit]
    extra = len(changed_paths) - len(shown)
    text = ", ".join(shown)
    if extra > 0:
        text += f" +{extra} more"
    return text


def _duration_display(started_at: datetime, finished_at: datetime | None = None) -> str:
    end_at = finished_at or datetime.now(timezone.utc)
    delta = end_at - started_at
    total_seconds = max(0, int(delta.total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes > 0:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _turn_outcome_style(outcome: str, *, current: bool = False) -> str:
    if current:
        return "bold green"
    if outcome == "completed":
        return "green"
    if outcome == "retry-scheduled":
        return "magenta"
    if outcome in {"harness-failed", "plan-invalid", "transition-failed", "failed"}:
        return "red"
    if outcome == "running":
        return "yellow"
    return "cyan"


def _render_turn_history(state: ControllerState) -> Group | Text | None:
    if not state.turn_history:
        return None
    panels: list[Panel] = []
    for record in state.turn_history:
        is_current = (
            state.current_turn_started_at is not None
            and record.turn_number == state.active_turn
            and record.outcome == "running"
        )
        border_style = _turn_outcome_style(record.outcome, current=is_current)
        body = Table.grid(padding=(0, 1))
        body.add_column(style="bold cyan", no_wrap=True)
        body.add_column()
        body.add_row("Step", record.step_name)
        body.add_row("Harness/Model", record.resolved_model_display)
        body.add_row("Duration", _duration_display(record.started_at, record.finished_at))
        body.add_row("Outcome", record.outcome)
        panels.append(
            Panel(
                body,
                title=f"Turn {record.turn_number:03d}",
                border_style=border_style,
                padding=(0, 1),
            )
        )
    return Group(*panels)


def _render_workflow_graph(
    *,
    workflow_name: str | None,
    workflow_steps: dict[str, WorkflowStepConfig] | None,
    current_step_name: str | None,
    state: ControllerState,
) -> Group | Text | None:
    if not workflow_steps:
        if workflow_name is None:
            return None
        return Text(workflow_name, style="bold magenta")

    completed_steps = {record.step_name for record in state.turn_history if record.outcome != "running"}
    graph_items: list[object] = []
    step_names = list(workflow_steps)
    for index, (step_name, step) in enumerate(workflow_steps.items()):
        is_current = (
            current_step_name == step_name
            and state.turn_history
            and state.turn_history[-1].turn_number == state.active_turn
            and state.turn_history[-1].outcome == "running"
        )
        is_completed = step_name in completed_steps and not is_current
        if is_current:
            border_style = "bold green"
            title_style = "bold green"
        elif is_completed:
            border_style = "green"
            title_style = "green"
        else:
            border_style = "dim"
            title_style = "dim"
        body = Text()
        body.append(step_name, style=title_style)
        body.append("\n")
        body.append(step.profile, style="dim")
        graph_items.append(
            Panel(
                body,
                border_style=border_style,
                padding=(0, 1),
            )
        )
        if index < len(step_names) - 1:
            arrows = Text()
            for transition in step.go:
                arrows.append("  ├─go→ ", style="dim")
                arrows.append(transition.to, style="bold")
                if transition.when is not None:
                    arrows.append(f" [{transition.when}]", style="dim")
                arrows.append("\n")
            graph_items.append(arrows)
    return Align.right(Group(*graph_items))


def _build_summary_table(
    *,
    workflow_name: str | None,
    current_step_name: str | None,
    config_harness: str | None,
    config_model: str | None,
    config_effort: str | None,
    config_max_turns: int,
    config_plan_path: Path,
    original_plan_path: Path | None,
    active_plan_path: Path | None,
    new_plan_path: Path | None,
    state: ControllerState,
    git_summary: GitSummary | None,
    banner_files_limit: int,
) -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan")
    table.add_column()

    table.add_row("Elapsed", _elapsed(state.run_started_at))

    if workflow_name is not None:
        table.add_row("Workflow", workflow_name)
    if current_step_name is not None:
        table.add_row("Step", current_step_name)

    harness_value = config_harness or "default"
    model_value = config_model or "default"
    table.add_row(
        "Harness/Model",
        format_harness_model_display(
            harness_value,
            model_value if config_model is not None else None,
            config_effort,
        ),
    )

    table.add_row("Checkpoint", _checkpoint_display(state.last_snapshot))
    name = state.last_snapshot.current_checkpoint_name or "-"
    table.add_row("Name", name)
    table.add_row("Turn", f"{state.active_turn}/{config_max_turns}")
    table.add_row("Issues", str(state.issues_accumulated))

    if original_plan_path is not None:
        table.add_row("Original Plan", original_plan_path.name)
    if active_plan_path is not None:
        table.add_row("Active Plan", active_plan_path.name)
    if new_plan_path is not None and new_plan_path.is_file() and new_plan_path != active_plan_path:
        table.add_row("Generated Plan", new_plan_path.name)

    if workflow_name is None:
        table.add_row("Plan", str(config_plan_path))

    if git_summary is not None:
        table.add_row("Git", _git_row(git_summary))
        files_text = _files_row(git_summary.changed_paths, limit=banner_files_limit)
        if files_text is not None:
            table.add_row("Files", files_text)

    table.add_row("Status", _status_display(state))
    return table


def build_banner(
    *,
    workflow_name: str | None = None,
    current_step_name: str | None = None,
    workflow_steps: dict[str, WorkflowStepConfig] | None = None,
    config_harness: str | None = None,
    config_model: str | None = None,
    config_effort: str | None = None,
    config_max_turns: int,
    config_plan_path: Path,
    original_plan_path: Path | None = None,
    active_plan_path: Path | None = None,
    new_plan_path: Path | None = None,
    config_banner_files_limit: int = 10,
    state: ControllerState,
    git_summary: GitSummary | None = None,
) -> Panel | None:
    if not _RICH_AVAILABLE:
        return None
    summary = _build_summary_table(
        workflow_name=workflow_name,
        current_step_name=current_step_name,
        config_harness=config_harness,
        config_model=config_model,
        config_effort=config_effort,
        config_max_turns=config_max_turns,
        config_plan_path=config_plan_path,
        original_plan_path=original_plan_path,
        active_plan_path=active_plan_path,
        new_plan_path=new_plan_path,
        state=state,
        git_summary=git_summary,
        banner_files_limit=config_banner_files_limit,
    )
    turn_history = _render_turn_history(state)
    workflow_graph = _render_workflow_graph(
        workflow_name=workflow_name,
        workflow_steps=workflow_steps,
        current_step_name=current_step_name,
        state=state,
    )
    left_items: list[object] = []
    if turn_history is not None:
        left_items.append(turn_history)
    left_items.append(summary)
    root = Table.grid(expand=True)
    root.add_column(ratio=3)
    root.add_column(ratio=2)
    root.add_row(Group(*left_items), workflow_graph or Text(""))
    title = Text("aflow", style="bold magenta")
    return Panel(root, title=title, border_style="blue")


class BannerRenderer:
    def __init__(
        self,
        *,
        config_harness: str | None = None,
        config_model: str | None = None,
        config_effort: str | None = None,
        workflow_steps: dict[str, WorkflowStepConfig] | None = None,
        config_max_turns: int,
        config_plan_path: Path,
        config_banner_files_limit: int = 10,
        workflow_name: str | None = None,
        current_step_name: str | None = None,
        original_plan_path: Path | None = None,
        active_plan_path: Path | None = None,
        new_plan_path: Path | None = None,
        console: Console | None = None,
        repo_root: Path | None = None,
        refresh_interval_seconds: float = 1.0,
        git_poll_interval_seconds: float = 10.0,
    ) -> None:
        self._config_harness = config_harness
        self._config_model = config_model
        self._config_effort = config_effort
        self._workflow_steps = workflow_steps
        self._config_max_turns = config_max_turns
        self._config_plan_path = config_plan_path
        self._config_banner_files_limit = config_banner_files_limit
        self._workflow_name = workflow_name
        self._current_step_name = current_step_name
        self._original_plan_path = original_plan_path
        self._active_plan_path = active_plan_path
        self._new_plan_path = new_plan_path
        self._console = console or _STDERR_CONSOLE
        self._repo_root = repo_root
        self._refresh_interval_seconds = refresh_interval_seconds
        self._git_poll_interval_seconds = git_poll_interval_seconds
        self._live: Live | None = None
        self._lock = threading.Lock()
        self._state: ControllerState | None = None
        self._git_summary: GitSummary | None = None
        self._stop_event = threading.Event()
        self._refresh_thread: threading.Thread | None = None

    def set_context(
        self,
        *,
        current_step_name: str | object = _UNSET,
        active_plan_path: Path | object = _UNSET,
        new_plan_path: Path | object = _UNSET,
        config_harness: str | object = _UNSET,
        config_model: str | object = _UNSET,
        config_effort: str | object = _UNSET,
    ) -> None:
        with self._lock:
            if current_step_name is not _UNSET:
                self._current_step_name = current_step_name
            if active_plan_path is not _UNSET:
                self._active_plan_path = active_plan_path
            if new_plan_path is not _UNSET:
                self._new_plan_path = new_plan_path
            if config_harness is not _UNSET:
                self._config_harness = config_harness
            if config_model is not _UNSET:
                self._config_model = config_model
            if config_effort is not _UNSET:
                self._config_effort = config_effort

    def _build(self, state: ControllerState, git_summary: GitSummary | None = None) -> Panel | None:
        return build_banner(
            workflow_name=self._workflow_name,
            current_step_name=self._current_step_name,
            workflow_steps=self._workflow_steps,
            config_harness=self._config_harness,
            config_model=self._config_model,
            config_effort=self._config_effort,
            config_max_turns=self._config_max_turns,
            config_plan_path=self._config_plan_path,
            config_banner_files_limit=self._config_banner_files_limit,
            original_plan_path=self._original_plan_path,
            active_plan_path=self._active_plan_path,
            new_plan_path=self._new_plan_path,
            state=state,
            git_summary=git_summary,
        )

    def _refresh_loop(self) -> None:
        from .git_status import capture_baseline, summarize_since_baseline

        baseline = None
        if self._repo_root is not None:
            baseline = capture_baseline(self._repo_root)

        last_git_poll = 0.0

        while not self._stop_event.is_set():
            now = time.monotonic()

            if self._repo_root is not None and baseline is not None:
                if now - last_git_poll >= self._git_poll_interval_seconds:
                    summary = summarize_since_baseline(self._repo_root, baseline)
                    with self._lock:
                        self._git_summary = summary
                    last_git_poll = now

            with self._lock:
                state = self._state
                git_summary = self._git_summary
                live = self._live

            if state is not None and live is not None:
                with self._lock:
                    panel = self._build(state, git_summary)
                if panel is not None:
                    live.update(panel)

            self._stop_event.wait(timeout=self._refresh_interval_seconds)

    def _start_refresh_thread(self) -> None:
        self._stop_event.clear()
        t = threading.Thread(target=self._refresh_loop, daemon=True, name="aflow-banner-refresh")
        self._refresh_thread = t
        t.start()

    def _stop_refresh_thread(self) -> None:
        self._stop_event.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=2.0)
            self._refresh_thread = None

    def start(self, state: ControllerState) -> None:
        if not _RICH_AVAILABLE:
            return
        with self._lock:
            self._state = state
        panel = self._build(state)
        if panel is None:
            return
        self._live = Live(panel, console=self._console, refresh_per_second=4, vertical_overflow="visible")
        self._live.start()
        self._start_refresh_thread()

    def update(self, state: ControllerState) -> None:
        if not _RICH_AVAILABLE:
            return
        with self._lock:
            self._state = state
            git_summary = self._git_summary
            live = self._live
        if live is None:
            return
        panel = self._build(state, git_summary)
        if panel is None:
            return
        live.update(panel)

    def pause(self) -> None:
        if self._live is None or not _RICH_AVAILABLE:
            return
        self._stop_refresh_thread()
        self._live.stop()
        self._live = None

    def resume(self, state: ControllerState) -> None:
        if not _RICH_AVAILABLE:
            return
        with self._lock:
            self._state = state
            git_summary = self._git_summary
        panel = self._build(state, git_summary)
        if panel is None:
            return
        self._live = Live(panel, console=self._console, refresh_per_second=4, vertical_overflow="visible")
        self._live.start()
        self._start_refresh_thread()

    def stop(self, state: ControllerState) -> None:
        if not _RICH_AVAILABLE:
            return
        self._stop_refresh_thread()
        if self._live is None:
            return
        with self._lock:
            git_summary = self._git_summary
        panel = self._build(state, git_summary)
        if panel is None:
            return
        self._live.update(panel)
        self._live.stop()
        self._live = None
