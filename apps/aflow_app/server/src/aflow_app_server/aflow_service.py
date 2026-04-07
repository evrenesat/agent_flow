"""Service layer for aflow library integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from aflow.api.events import (
    ExecutionEvent,
    ExecutionEventType,
    ExecutionObserver,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    StatusChangedEvent,
    TurnFinishedEvent,
    TurnStartedEvent,
)
from aflow.api.models import PreparedRun, StartupContext
from aflow.api.runner import execute_workflow
from aflow.api.startup import prepare_startup
from aflow.plan import load_plan

from .models import ExecutionRequest, ExecutionStatus, PlanInfo, PlanStatus


class AflowServiceError(Exception):
    """Error in aflow service operations."""
    pass


@dataclass
class StartupResult:
    """Result of startup preparation."""

    prepared_run: PreparedRun | None = None
    question: dict[str, Any] | None = None
    error: str | None = None


class AflowService:
    """Service for interacting with the aflow library."""

    def __init__(self) -> None:
        self._active_runs: dict[str, ExecutionStatus] = {}
        self._event_queues: dict[str, asyncio.Queue[ExecutionEvent]] = {}

    def list_plans(self, repo_path: Path) -> list[PlanInfo]:
        """List all plan files in a repository.

        Args:
            repo_path: Path to the repository root.

        Returns:
            List of PlanInfo for all found plans.
        """
        plans: list[PlanInfo] = []

        # Check drafts directory
        drafts_dir = repo_path / "plans" / "drafts"
        if drafts_dir.exists():
            for plan_file in drafts_dir.glob("*.md"):
                info = self._get_plan_info(plan_file, PlanStatus.DRAFT)
                if info:
                    plans.append(info)

        # Check in-progress directory
        in_progress_dir = repo_path / "plans" / "in-progress"
        if in_progress_dir.exists():
            for plan_file in in_progress_dir.glob("*.md"):
                info = self._get_plan_info(plan_file, PlanStatus.IN_PROGRESS)
                if info:
                    plans.append(info)

        return sorted(plans, key=lambda p: p.name)

    def _get_plan_info(self, plan_path: Path, status: PlanStatus) -> PlanInfo | None:
        """Get plan info for a single file."""
        try:
            parsed = load_plan(plan_path)
            snapshot = parsed.snapshot
            return PlanInfo(
                name=plan_path.stem,
                path=plan_path,
                status=status,
                checkpoint_count=snapshot.total_checkpoint_count,
                unchecked_count=snapshot.unchecked_checkpoint_count,
                is_complete=snapshot.is_complete,
            )
        except Exception:
            return None

    def prepare_execution(
        self,
        repo_path: Path,
        request: ExecutionRequest,
    ) -> StartupResult:
        """Prepare a workflow execution.

        Args:
            repo_path: Path to the repository root.
            request: Execution request parameters.

        Returns:
            StartupResult with either a PreparedRun or a question.
        """
        plan_path = repo_path / request.plan_path

        if not plan_path.exists():
            return StartupResult(error=f"Plan file not found: {request.plan_path}")

        try:
            from aflow.api.models import StartupRequest
            startup_request = StartupRequest(
                repo_root=repo_path,
                plan_path=plan_path,
                workflow_name=request.workflow_name,
                team=request.team,
                start_step=request.start_step,
                max_turns=request.max_turns,
                extra_instructions=request.extra_instructions,
            )

            result = prepare_startup(startup_request)

            if isinstance(result, PreparedRun):
                return StartupResult(prepared_run=result)

            # It's a StartupQuestion
            return StartupResult(question={
                "kind": result.kind.value if hasattr(result.kind, "value") else str(result.kind),
                "message": result.message,
                "options": result.options,
                "choices": result.choices,
            })

        except Exception as e:
            return StartupResult(error=str(e))

    async def execute_workflow_async(
        self,
        prepared_run: PreparedRun,
        repo_id: str,
    ) -> str:
        """Execute a workflow asynchronously.

        Args:
            prepared_run: The prepared run configuration.
            repo_id: ID of the repository.

        Returns:
            Run ID for tracking.
        """
        run_id = str(uuid4())[:8]
        event_queue: asyncio.Queue[ExecutionEvent] = asyncio.Queue()
        self._event_queues[run_id] = event_queue

        status = ExecutionStatus(
            run_id=run_id,
            repo_id=repo_id,
            plan_path=str(prepared_run.plan_path),
            workflow_name=prepared_run.workflow_name,
            status="starting",
            turns_completed=0,
            current_step=prepared_run.start_step,
            started_at=datetime.now(timezone.utc),
        )
        self._active_runs[run_id] = status

        # Run in background
        asyncio.create_task(self._run_workflow(run_id, prepared_run, event_queue))

        return run_id

    async def _run_workflow(
        self,
        run_id: str,
        prepared_run: PreparedRun,
        event_queue: asyncio.Queue[ExecutionEvent],
    ) -> None:
        """Run the workflow and emit events."""
        status = self._active_runs[run_id]

        class QueueObserver(ExecutionObserver):
            def __init__(self, queue: asyncio.Queue[ExecutionEvent]) -> None:
                self._queue = queue

            def on_event(self, event: ExecutionEvent) -> None:
                try:
                    asyncio.get_event_loop().call_soon_threadsafe(
                        lambda: self._queue.put_nowait(event)
                    )
                except Exception:
                    pass

        observer = QueueObserver(event_queue)

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: execute_workflow(prepared_run, observer=observer)
            )

            status = ExecutionStatus(
                run_id=run_id,
                repo_id=status.repo_id,
                plan_path=status.plan_path,
                workflow_name=status.workflow_name,
                status="completed" if result.end_reason == "done" else "failed",
                turns_completed=result.turns_completed,
                current_step=status.current_step,
                started_at=status.started_at,
                error=None if result.end_reason == "done" else str(result.end_reason),
            )
            self._active_runs[run_id] = status

        except Exception as e:
            status = ExecutionStatus(
                run_id=run_id,
                repo_id=status.repo_id,
                plan_path=status.plan_path,
                workflow_name=status.workflow_name,
                status="failed",
                turns_completed=status.turns_completed,
                current_step=status.current_step,
                started_at=status.started_at,
                error=str(e),
            )
            self._active_runs[run_id] = status

    def get_run_status(self, run_id: str) -> ExecutionStatus | None:
        """Get the status of a run."""
        return self._active_runs.get(run_id)

    def get_event_queue(self, run_id: str) -> asyncio.Queue[ExecutionEvent] | None:
        """Get the event queue for a run."""
        return self._event_queues.get(run_id)