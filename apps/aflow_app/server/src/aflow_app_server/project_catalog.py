"""Project discovery and Codex thread association."""

from __future__ import annotations

from collections import defaultdict
import subprocess
from pathlib import Path

from .codex_thread_gateway import CodexThreadGateway, CodexThreadGatewayError
from .models import ProjectInfo
from .project_overrides import ProjectOverrideRecord, ProjectOverridesStore


def _normalize_path(path: Path | str) -> Path:
    """Normalize a path without requiring it to exist."""
    return Path(path).expanduser().absolute()


def _canonicalize_git_root(path: Path | str) -> tuple[Path, bool]:
    """Resolve a linked worktree to its primary checkout when git can identify one."""
    normalized = _normalize_path(path)

    try:
        superproject = subprocess.run(
            ["git", "-C", str(normalized), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError):
        return normalized, False

    if superproject.returncode != 0:
        return normalized, False

    common_dir = superproject.stdout.strip()
    if common_dir:
        common_dir_path = Path(common_dir)
        if not common_dir_path.is_absolute():
            common_dir_path = normalized / common_dir_path
        common_dir_path = common_dir_path.absolute()
        return common_dir_path.parent, common_dir_path.parent != normalized

    return normalized, False


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

        projects_by_path: dict[Path, ProjectInfo] = {}
        for record in records:
            canonical_path, _ = _canonicalize_git_root(record.current_path)
            project = self._to_project_info(
                record,
                current_path=canonical_path,
                local_paths=local_paths,
                thread_counts=linked_thread_counts,
            )
            existing = projects_by_path.get(canonical_path)
            if existing is None:
                projects_by_path[canonical_path] = project
                continue
            if existing.current_path != canonical_path and project.current_path == canonical_path:
                projects_by_path[canonical_path] = project

        return sorted(
            list(projects_by_path.values()),
            key=lambda project: (project.display_name.casefold(), str(project.current_path)),
        )

    def get_project(self, project_id: str, thread_gateway: CodexThreadGateway | None = None) -> ProjectInfo | None:
        """Fetch one project by id."""
        for project in self.list_projects(thread_gateway=thread_gateway):
            if project.id == project_id:
                return project
        return None

    def get_project_fast(self, project_id: str) -> ProjectInfo | None:
        """Fetch one persisted project by id without rescanning every local repository."""
        record = self._store.get(project_id)
        if record is None:
            return None

        canonical_path, _ = _canonicalize_git_root(record.current_path)
        local_paths = {canonical_path} if (canonical_path / ".git").exists() else set()
        return self._to_project_info(
            record,
            current_path=canonical_path,
            local_paths=local_paths,
            thread_counts={},
        )

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
            canonical_path, _ = _canonicalize_git_root(current_path)
            if self._store.move(project_id, canonical_path) is None:
                return None
        if alias is not None:
            if self._store.add_alias(project_id, alias) is None:
                return None
        return self.get_project(project_id)

    def ensure_current_project(self, path: Path | str, *, display_name: str | None = None) -> ProjectInfo:
        """Ensure a project exists for an exact current path."""
        normalized, _ = _canonicalize_git_root(path)
        record = self._store.ensure_current_project(normalized, display_name=display_name)
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
        normalized, _ = _canonicalize_git_root(path)
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
            canonical_root, _ = _canonicalize_git_root(git_entry.parent)
            local_roots.add(canonical_root)
        return local_roots

    def _collect_thread_counts(self, thread_gateway: CodexThreadGateway | None) -> dict[Path, int]:
        """Count threads by cwd so the catalog can surface thread-only projects."""
        if thread_gateway is None:
            return {}

        counts: dict[Path, int] = defaultdict(int)
        cursor: str | None = None

        while True:
            try:
                page = thread_gateway.list_threads(cursor=cursor)
            except CodexThreadGatewayError:
                # Treat Codex thread enumeration as optional enrichment.
                return dict(counts)
            for thread in page.threads:
                cwd = getattr(thread, "cwd", None)
                if not cwd:
                    continue
                canonical_root, _ = _canonicalize_git_root(cwd)
                counts[canonical_root] += 1
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
            canonical_path, _ = _canonicalize_git_root(record.current_path)
            linked_counts[canonical_path] += count
        return dict(linked_counts)

    def _resolve_record_for_path(
        self,
        records: list[ProjectOverrideRecord],
        path: Path | str,
    ) -> ProjectOverrideRecord | None:
        """Resolve a stored path to the owning record, preferring historical aliases."""
        normalized, _ = _canonicalize_git_root(path)
        for record in records:
            if normalized in record.historical_aliases:
                return record
        for record in records:
            canonical_path, _ = _canonicalize_git_root(record.current_path)
            if canonical_path == normalized:
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
        current_path: Path | None = None,
        local_paths: set[Path],
        thread_counts: dict[Path, int],
    ) -> ProjectInfo:
        """Convert a persisted record into a response model."""
        current_path = current_path or record.current_path
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
            display_name=record.display_name if record.current_path == current_path and record.display_name else current_path.name,
            current_path=current_path,
            historical_aliases=tuple(record.historical_aliases),
            detection_source=detection_source,
            linked_thread_count=thread_count,
            is_git_root=is_git_root,
            registered_at=record.registered_at,
        )
