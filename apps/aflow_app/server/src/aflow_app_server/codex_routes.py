"""API routes for Codex thread management and plan drafts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .codex_app_server_client import CodexAppServerClient
from .codex_thread_gateway import (
    CodexThreadGateway,
    CodexThreadGatewayError,
    UserInput,
)
from .config import ServerConfig
from .project_catalog import ProjectCatalog
from .plan_store import PlanStore, PlanStoreError


router = APIRouter(prefix="/api/projects/{project_id}", tags=["projects"])


class StartThreadRequest(BaseModel):
    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    service_tier: str | None = None
    approval_policy: str | None = None
    experimental_raw_events: bool = False
    persist_extended_history: bool = True


class ResumeThreadRequest(BaseModel):
    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    service_tier: str | None = None
    approval_policy: str | None = None
    persist_extended_history: bool = True


class ForkThreadRequest(BaseModel):
    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    service_tier: str | None = None
    approval_policy: str | None = None
    persist_extended_history: bool = True


class SetThreadNameRequest(BaseModel):
    name: str = Field(min_length=1)


class StartTurnRequest(BaseModel):
    input: list[UserInput]
    cwd: str | None = None
    approval_policy: str | None = None
    model: str | None = None
    service_tier: str | None = None
    effort: str | None = None
    summary: str | None = None
    personality: str | None = None


class SaveDraftRequest(BaseModel):
    name: str
    content: str


class PromotePlanRequest(BaseModel):
    draft_name: str
    target_name: str | None = None


def _get_config() -> ServerConfig:
    """Get config - to be overridden by main app."""
    raise RuntimeError("Config dependency not initialized")


def _get_project_catalog() -> ProjectCatalog:
    """Get project catalog - to be overridden by main app."""
    raise RuntimeError("Project catalog dependency not initialized")


def get_codex_backend(config: ServerConfig = Depends(_get_config)) -> CodexThreadGateway:
    """Get or create a Codex thread gateway instance."""
    if not config.codex_app_server_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Codex app-server not configured",
        )

    return CodexAppServerClient(
        server_url=config.codex_app_server_url,
        auth_token=config.codex_app_server_token,
    )


def get_plan_store_for_project(
    project_id: str,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
) -> PlanStore:
    """Get a plan store for a project."""
    project = project_catalog.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return PlanStore(project.current_path)


def _codex_error_to_http_error(error: CodexThreadGatewayError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Codex app-server error: {error}",
    )


def _list_all_threads_for_project(
    backend: CodexThreadGateway,
    *,
    search_term: str | None,
    limit: int | None,
    cursor: str | None,
    source_kinds: list[str] | None,
    archived: bool | None,
) -> list[Any]:
    """Fetch every matching thread page so ownership filtering stays correct."""
    threads: list[Any] = []
    current_cursor = cursor
    while True:
        page = backend.list_threads(
            search_term=search_term,
            limit=limit,
            cursor=current_cursor,
            source_kinds=source_kinds,
            archived=archived,
        )
        threads.extend(page.threads)
        if not page.next_cursor:
            break
        current_cursor = page.next_cursor
    return threads


@router.get("/threads")
async def list_threads(
    project_id: str,
    cwd: str | None = None,
    search_term: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    source_kinds: list[str] | None = None,
    archived: bool | None = None,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
    backend: CodexThreadGateway = Depends(get_codex_backend),
) -> dict[str, Any]:
    """List available Codex threads."""
    projects = project_catalog.list_projects(thread_gateway=backend)
    project = next((item for item in projects if item.id == project_id), None)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    try:
        threads = []
        for thread in _list_all_threads_for_project(
            backend,
            search_term=search_term,
            limit=limit,
            cursor=cursor,
            source_kinds=source_kinds,
            archived=archived,
        ):
            thread_cwd = getattr(thread, "cwd", None)
            if not thread_cwd:
                continue
            if project_catalog.project_owns_path(
                project,
                thread_cwd,
                projects=projects,
            ):
                threads.append(thread)
                if limit is not None and len(threads) >= limit:
                    break
        return {
            "threads": [thread.to_dict() for thread in threads],
            "next_cursor": None,
        }
    except CodexThreadGatewayError as error:
        raise _codex_error_to_http_error(error) from error


@router.get("/threads/{thread_id}")
async def read_thread(
    project_id: str,
    thread_id: str,
    include_turns: bool = True,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
    backend: CodexThreadGateway = Depends(get_codex_backend),
) -> dict[str, Any]:
    """Read a specific Codex thread."""
    project = project_catalog.get_project(project_id, thread_gateway=backend)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    try:
        thread = backend.read_thread(thread_id, include_turns=include_turns)
        return thread.to_dict()
    except CodexThreadGatewayError as error:
        raise _codex_error_to_http_error(error) from error


@router.post("/threads")
async def start_thread(
    project_id: str,
    request: StartThreadRequest,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
    backend: CodexThreadGateway = Depends(get_codex_backend),
) -> dict[str, Any]:
    """Start a new Codex thread."""
    project = project_catalog.get_project(project_id, thread_gateway=backend)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    try:
        result = backend.start_thread(
            cwd=request.cwd or str(project.current_path),
            model=request.model,
            model_provider=request.model_provider,
            service_tier=request.service_tier,
            approval_policy=request.approval_policy,
            experimental_raw_events=request.experimental_raw_events,
            persist_extended_history=request.persist_extended_history,
        )
        return result.to_dict()
    except CodexThreadGatewayError as error:
        raise _codex_error_to_http_error(error) from error


@router.post("/threads/{thread_id}/resume")
async def resume_thread(
    project_id: str,
    thread_id: str,
    request: ResumeThreadRequest,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
    backend: CodexThreadGateway = Depends(get_codex_backend),
) -> dict[str, Any]:
    """Resume an existing Codex thread."""
    project = project_catalog.get_project(project_id, thread_gateway=backend)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    try:
        result = backend.resume_thread(
            thread_id,
            cwd=request.cwd or str(project.current_path),
            model=request.model,
            model_provider=request.model_provider,
            service_tier=request.service_tier,
            approval_policy=request.approval_policy,
            persist_extended_history=request.persist_extended_history,
        )
        return result.to_dict()
    except CodexThreadGatewayError as error:
        raise _codex_error_to_http_error(error) from error


@router.post("/threads/{thread_id}/fork")
async def fork_thread(
    project_id: str,
    thread_id: str,
    request: ForkThreadRequest,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
    backend: CodexThreadGateway = Depends(get_codex_backend),
) -> dict[str, Any]:
    """Fork an existing Codex thread."""
    project = project_catalog.get_project(project_id, thread_gateway=backend)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    try:
        result = backend.fork_thread(
            thread_id,
            cwd=request.cwd or str(project.current_path),
            model=request.model,
            model_provider=request.model_provider,
            service_tier=request.service_tier,
            approval_policy=request.approval_policy,
            persist_extended_history=request.persist_extended_history,
        )
        return result.to_dict()
    except CodexThreadGatewayError as error:
        raise _codex_error_to_http_error(error) from error


@router.patch("/threads/{thread_id}/name")
async def set_thread_name(
    project_id: str,
    thread_id: str,
    request: SetThreadNameRequest,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
    backend: CodexThreadGateway = Depends(get_codex_backend),
) -> dict[str, str]:
    """Set a thread's display name."""
    project = project_catalog.get_project(project_id, thread_gateway=backend)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    try:
        backend.set_thread_name(thread_id, request.name)
        return {"status": "ok"}
    except CodexThreadGatewayError as error:
        raise _codex_error_to_http_error(error) from error


@router.post("/threads/{thread_id}/turns")
async def start_turn(
    project_id: str,
    thread_id: str,
    request: StartTurnRequest,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
    backend: CodexThreadGateway = Depends(get_codex_backend),
) -> dict[str, Any]:
    """Send a user turn into an existing thread."""
    project = project_catalog.get_project(project_id, thread_gateway=backend)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    try:
        turn = backend.start_turn(
            thread_id,
            request.input,
            cwd=request.cwd or str(project.current_path),
            approval_policy=request.approval_policy,
            model=request.model,
            service_tier=request.service_tier,
            effort=request.effort,
            summary=request.summary,
            personality=request.personality,
        )
        return turn.to_dict()
    except CodexThreadGatewayError as error:
        raise _codex_error_to_http_error(error) from error


@router.post("/plans/drafts", status_code=status.HTTP_201_CREATED)
async def save_draft(
    project_id: str,
    request: SaveDraftRequest,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
) -> dict[str, Any]:
    """Save a plan as a draft."""
    project = project_catalog.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    store = PlanStore(project.current_path)
    try:
        path = store.save_draft(request.name, request.content)
        return {
            "name": request.name,
            "path": str(path),
            "status": "draft",
        }
    except PlanStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error


@router.get("/plans/drafts")
async def list_drafts(
    project_id: str,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
) -> list[str]:
    """List all draft plans for a repository."""
    project = project_catalog.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    store = PlanStore(project.current_path)
    return store.list_drafts()


@router.get("/plans/drafts/{name}")
async def load_draft(
    project_id: str,
    name: str,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
) -> dict[str, str]:
    """Load a draft plan."""
    project = project_catalog.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    store = PlanStore(project.current_path)
    try:
        content = store.load_draft(name)
        return {"name": name, "content": content}
    except PlanStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error


@router.delete("/plans/drafts/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(
    project_id: str,
    name: str,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
) -> None:
    """Delete a draft plan."""
    project = project_catalog.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    store = PlanStore(project.current_path)
    if not store.delete_draft(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Draft not found",
        )


@router.post("/plans/promote")
async def promote_plan(
    project_id: str,
    request: PromotePlanRequest,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
) -> dict[str, Any]:
    """Promote a draft plan to in-progress status."""
    project = project_catalog.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    store = PlanStore(project.current_path)
    try:
        path = store.promote_to_in_progress(request.draft_name, request.target_name)
        return {
            "name": path.stem,
            "path": str(path),
            "status": "in_progress",
        }
    except PlanStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error


@router.get("/plans/in-progress")
async def list_in_progress(
    project_id: str,
    project_catalog: ProjectCatalog = Depends(_get_project_catalog),
) -> list[str]:
    """List all in-progress plans for a repository."""
    project = project_catalog.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    store = PlanStore(project.current_path)
    return store.list_in_progress()
