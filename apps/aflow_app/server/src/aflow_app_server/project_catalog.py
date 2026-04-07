"""Project discovery and Codex thread association."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .codex_thread_gateway import CodexThreadGateway
from .models import ProjectInfo
from .project_overrides import ProjectOverrideRecord, ProjectOverridesStore


def _normalize_path(path: Path | str) -> Path:
    """Normalize a path without requiring it to exist."""
    return Path(path).expanduser().absolute()


class ProjectCatalogError(Exception):
    """Raised when project catalog operations fail."""


class ProjectCatalog:
    """Discover local projects, Codex thread projects, and persisted overrides."""

    def __init__(
        self,
        projects_home: Path,
        project_overrides_path: Path,
        *,
        legacy_registry_path: Path | None = None,
    ) -> None:
        self._projects_home = _normalize_path(projects_home)
        self._store = ProjectOverridesStore(
            project_overrides_path,
            legacy_registry_path=legacy_registry_path,
        )

    def list_projects(self, thread_gateway: CodexThreadGateway | None = None) -> list[ProjectInfo]:
        """Return the merged project catalog."""
        local_paths = self._discover_local_git_roots()
        thread_counts = self._collect_thread_counts(thread_gateway)

        for local_path in local_paths:
            self._store.ensure_current_project(local_path, display_name=local_path.name)

        for thread_path in thread_counts:
            if self._store.resolve_current_path(thread_path) is None and self._store.resolve_historical_alias(thread_path) is None:
                self._store.ensure_project(thread_path, display_name=thread_path.name)

        records = self._store.list_records()
        linked_thread_counts = self._collect_linked_thread_counts(records, thread_counts)

        projects: list[ProjectInfo] = []
        for record in records:
            projects.append(
                self._to_project_info(
                    record,
                    local_paths=local_paths,
                    thread_counts=linked_thread_counts,
                )
            )

        return sorted(
            projects,
            key=lambda project: (project.display_name.casefold(), str(project.current_path)),
        )

    def get_project(self, project_id: str, thread_gateway: CodexThreadGateway | None = None) -> ProjectInfo | None:
        """Fetch one project by id."""
        for project in self.list_projects(thread_gateway=thread_gateway):
            if project.id == project_id:
                return project
        return None

    def update_project(
        self,
        project_id: str,
        *,
        display_name: str | None = None,
        current_path: Path | str | None = None,
        alias: Path | str | None = None,
    ) -> ProjectInfo | None:
        """Update editable project metadata and return the current snapshot."""
        if display_name is not None:
            if self._store.rename(project_id, display_name) is None:
                return None
        if current_path is not None:
            if self._store.move(project_id, current_path) is None:
                return None
        if alias is not None:
            if self._store.add_alias(project_id, alias) is None:
                return None
        return self.get_project(project_id)

    def ensure_current_project(self, path: Path | str, *, display_name: str | None = None) -> ProjectInfo:
        """Ensure a project exists for an exact current path."""
        record = self._store.ensure_current_project(path, display_name=display_name)
        normalized = _normalize_path(path)
        return self._to_project_info(
            record,
            local_paths={normalized} if (normalized / ".git").exists() else set(),
            thread_counts={},
        )

    def remove_project(self, project_id: str) -> bool:
        """Remove a project from the catalog."""
        return self._store.remove(project_id)

    def resolve_project_for_path(
        self,
        path: Path | str,
        thread_gateway: CodexThreadGateway | None = None,
        *,
        projects: list[ProjectInfo] | None = None,
    ) -> ProjectInfo | None:
        """Resolve a path to a project, preferring historical aliases for moved threads."""
        normalized = _normalize_path(path)
        return self._resolve_project_from_projects(
            projects if projects is not None else self.list_projects(thread_gateway=thread_gateway),
            normalized,
        )

    def project_owns_path(
        self,
        project: ProjectInfo,
        path: Path | str,
        *,
        projects: list[ProjectInfo] | None = None,
        thread_gateway: CodexThreadGateway | None = None,
    ) -> bool:
        """Check whether a path belongs to a project, using alias precedence when needed."""
        owner = self.resolve_project_for_path(
            path,
            thread_gateway=thread_gateway,
            projects=projects,
        )
        return owner is not None and owner.id == project.id

    def _discover_local_git_roots(self) -> set[Path]:
        """Find git roots under the configured projects-home directory."""
        if not self._projects_home.exists():
            return set()

        local_roots: set[Path] = set()
        for git_entry in self._projects_home.rglob(".git"):
            if ".git" in git_entry.parts[:-1]:
                continue
            local_roots.add(git_entry.parent.absolute())
        return local_roots

    def _collect_thread_counts(self, thread_gateway: CodexThreadGateway | None) -> dict[Path, int]:
        """Count threads by cwd so the catalog can surface thread-only projects."""
        if thread_gateway is None:
            return {}

        counts: dict[Path, int] = defaultdict(int)
        cursor: str | None = None

        while True:
            page = thread_gateway.list_threads(cursor=cursor)
            for thread in page.threads:
                cwd = getattr(thread, "cwd", None)
                if not cwd:
                    continue
                counts[_normalize_path(cwd)] += 1
            if not page.next_cursor:
                break
            cursor = page.next_cursor

        return dict(counts)

    def _collect_linked_thread_counts(
        self,
        records: list[ProjectOverrideRecord],
        thread_counts: dict[Path, int],
    ) -> dict[Path, int]:
        """Attribute each stored thread cwd to a single project record."""
        linked_counts: dict[Path, int] = defaultdict(int)
        for thread_path, count in thread_counts.items():
            record = self._resolve_record_for_path(records, thread_path)
            if record is None:
                continue
            linked_counts[record.current_path] += count
        return dict(linked_counts)

    def _resolve_record_for_path(
        self,
        records: list[ProjectOverrideRecord],
        path: Path | str,
    ) -> ProjectOverrideRecord | None:
        """Resolve a stored path to the owning record, preferring historical aliases."""
        normalized = _normalize_path(path)
        for record in records:
            if normalized in record.historical_aliases:
                return record
        for record in records:
            if record.current_path == normalized:
                return record
        return None

    def _resolve_project_from_projects(
        self,
        projects: list[ProjectInfo],
        path: Path,
    ) -> ProjectInfo | None:
        """Resolve a normalized path against an already-built project snapshot."""
        for project in projects:
            if path in project.historical_aliases:
                return project
        for project in projects:
            if project.current_path == path:
                return project
        return None

    def _to_project_info(
        self,
        record: ProjectOverrideRecord,
        *,
        local_paths: set[Path],
        thread_counts: dict[Path, int],
    ) -> ProjectInfo:
        """Convert a persisted record into a response model."""
        current_path = record.current_path
        thread_count = thread_counts.get(current_path, 0)

        is_git_root = current_path in local_paths or (current_path / ".git").exists()
        saw_thread = thread_count > 0
        if is_git_root and saw_thread:
            detection_source = "local_git_root+codex_thread_cwd"
        elif is_git_root:
            detection_source = "local_git_root"
        elif saw_thread:
            detection_source = "codex_thread_cwd"
        else:
            detection_source = "override"

        return ProjectInfo(
            id=record.id,
            display_name=record.display_name or current_path.name,
            current_path=current_path,
            historical_aliases=tuple(record.historical_aliases),
            detection_source=detection_source,
            linked_thread_count=thread_count,
            is_git_root=is_git_root,
            registered_at=record.registered_at,
        )
