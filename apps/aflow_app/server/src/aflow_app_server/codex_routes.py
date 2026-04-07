"""API routes for Codex session management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .codex_backend import CodexBackend, CodexBackendError, HttpCodexBackend
from .config import ServerConfig
from .plan_store import PlanStore, PlanStoreError
from .repo_registry import RepoRegistry


router = APIRouter(prefix="/api/codex", tags=["codex"])


# Request/Response models
class SendMessageRequest(BaseModel):
    content: str


class SaveDraftRequest(BaseModel):
    name: str
    content: str


class PromotePlanRequest(BaseModel):
    draft_name: str
    target_name: str | None = None


# Dependency injection - these will be overridden by main.py
def _get_config() -> ServerConfig:
    """Get config - to be overridden by main app."""
    raise RuntimeError("Config dependency not initialized")


def _get_registry() -> RepoRegistry:
    """Get registry - to be overridden by main app."""
    raise RuntimeError("Registry dependency not initialized")


def get_codex_backend(config: ServerConfig = Depends(_get_config)) -> CodexBackend:
    """Get or create a Codex backend instance."""
    if not config.codex_server_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Codex server not configured",
        )

    return HttpCodexBackend(
        server_url=config.codex_server_url,
        auth_token=config.codex_server_token,
    )


def get_plan_store_for_repo(
    repo_id: str,
    registry: RepoRegistry = Depends(_get_registry),
) -> PlanStore:
    """Get a plan store for a repository."""
    repo = registry.get_repo(repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )
    return PlanStore(repo.path)


# Codex session endpoints
@router.get("/sessions")
async def list_sessions(
    repo_id: str | None = None,
    backend: CodexBackend = Depends(get_codex_backend),
) -> list[dict[str, Any]]:
    """List available Codex sessions.

    Args:
        repo_id: Optional filter by repository ID.
    """
    try:
        sessions = backend.list_sessions(repo_path=None)
        return [
            {
                "id": s.id,
                "name": s.name,
                "repo_path": s.repo_path,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
                "message_count": s.message_count,
            }
            for s in sessions
        ]
    except CodexBackendError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Codex backend error: {e}",
        )


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    backend: CodexBackend = Depends(get_codex_backend),
) -> dict[str, Any]:
    """Get a specific Codex session."""
    try:
        session = backend.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )

        return {
            "id": session.id,
            "name": session.name,
            "repo_path": session.repo_path,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "message_count": session.message_count,
        }
    except CodexBackendError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Codex backend error: {e}",
        )


@router.get("/sessions/{session_id}/messages")
async def fetch_messages(
    session_id: str,
    limit: int | None = None,
    backend: CodexBackend = Depends(get_codex_backend),
) -> list[dict[str, Any]]:
    """Fetch messages from a Codex session."""
    try:
        messages = backend.fetch_messages(session_id, limit=limit)
        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp.isoformat(),
            }
            for m in messages
        ]
    except CodexBackendError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Codex backend error: {e}",
        )


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    request: SendMessageRequest,
    backend: CodexBackend = Depends(get_codex_backend),
) -> dict[str, Any]:
    """Send a message to a Codex session."""
    try:
        response = backend.send_message(session_id, request.content)
        return {
            "id": response.id,
            "role": response.role,
            "content": response.content,
            "timestamp": response.timestamp.isoformat(),
        }
    except CodexBackendError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Codex backend error: {e}",
        )


# Plan draft endpoints
@router.post("/repos/{repo_id}/plans/drafts", status_code=status.HTTP_201_CREATED)
async def save_draft(
    repo_id: str,
    request: SaveDraftRequest,
    registry: RepoRegistry = Depends(_get_registry),
) -> dict[str, Any]:
    """Save a plan as a draft."""
    repo = registry.get_repo(repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    store = PlanStore(repo.path)
    try:
        path = store.save_draft(request.name, request.content)
        return {
            "name": request.name,
            "path": str(path),
            "status": "draft",
        }
    except PlanStoreError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("/repos/{repo_id}/plans/drafts")
async def list_drafts(
    repo_id: str,
    registry: RepoRegistry = Depends(_get_registry),
) -> list[str]:
    """List all draft plans for a repository."""
    repo = registry.get_repo(repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    store = PlanStore(repo.path)
    return store.list_drafts()


@router.get("/repos/{repo_id}/plans/drafts/{name}")
async def load_draft(
    repo_id: str,
    name: str,
    registry: RepoRegistry = Depends(_get_registry),
) -> dict[str, str]:
    """Load a draft plan."""
    repo = registry.get_repo(repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    store = PlanStore(repo.path)
    try:
        content = store.load_draft(name)
        return {"name": name, "content": content}
    except PlanStoreError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.delete("/repos/{repo_id}/plans/drafts/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft(
    repo_id: str,
    name: str,
    registry: RepoRegistry = Depends(_get_registry),
) -> None:
    """Delete a draft plan."""
    repo = registry.get_repo(repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    store = PlanStore(repo.path)
    if not store.delete_draft(name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Draft not found",
        )


@router.post("/repos/{repo_id}/plans/promote")
async def promote_plan(
    repo_id: str,
    request: PromotePlanRequest,
    registry: RepoRegistry = Depends(_get_registry),
) -> dict[str, Any]:
    """Promote a draft plan to in-progress status."""
    repo = registry.get_repo(repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    store = PlanStore(repo.path)
    try:
        path = store.promote_to_in_progress(request.draft_name, request.target_name)
        return {
            "name": path.stem,
            "path": str(path),
            "status": "in_progress",
        }
    except PlanStoreError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get("/repos/{repo_id}/plans/in-progress")
async def list_in_progress(
    repo_id: str,
    registry: RepoRegistry = Depends(_get_registry),
) -> list[str]:
    """List all in-progress plans for a repository."""
    repo = registry.get_repo(repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository not found",
        )

    store = PlanStore(repo.path)
    return store.list_in_progress()
