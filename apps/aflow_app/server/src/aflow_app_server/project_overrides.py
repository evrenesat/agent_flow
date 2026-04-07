"""Persistent project override storage for moved paths and display names."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any


def _normalize_path(path: Path | str) -> Path:
    """Normalize a path without requiring it to exist."""
    return Path(path).expanduser().absolute()


def _parse_datetime(value: str | None) -> datetime:
    """Parse an ISO 8601 timestamp or fall back to now."""
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value)


@dataclass
class ProjectOverrideRecord:
    """Persisted user-controlled project metadata."""

    id: str
    display_name: str | None
    current_path: Path
    historical_aliases: list[Path] = field(default_factory=list)
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record to JSON."""
        aliases = [str(alias) for alias in self.historical_aliases]
        return {
            "id": self.id,
            "display_name": self.display_name,
            "current_path": str(self.current_path),
            "historical_aliases": aliases,
            "aliases": aliases,
            "registered_at": self.registered_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectOverrideRecord:
        """Deserialize a record from JSON."""
        aliases = data.get("historical_aliases") or data.get("aliases") or []
        return cls(
            id=data["id"],
            display_name=data.get("display_name") or data.get("name"),
            current_path=_normalize_path(data.get("current_path") or data.get("path")),
            historical_aliases=[_normalize_path(alias) for alias in aliases],
            registered_at=_parse_datetime(data.get("registered_at")),
        )


class ProjectOverridesStoreError(Exception):
    """Raised when override storage cannot be loaded or updated."""


class ProjectOverridesStore:
    """Persistent storage for project override records."""

    def __init__(self, path: Path, *, legacy_registry_path: Path | None = None) -> None:
        self._path = path.expanduser()
        self._legacy_registry_path = legacy_registry_path.expanduser() if legacy_registry_path else None
        self._records: dict[str, ProjectOverrideRecord] = {}
        self._load()

    def _load(self) -> None:
        """Load the store from disk or migrate legacy registry data."""
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            for record_data in payload.get("projects", []):
                record = ProjectOverrideRecord.from_dict(record_data)
                self._records[record.id] = record
            return

        if self._legacy_registry_path and self._legacy_registry_path.exists():
            self._migrate_from_legacy_registry()

    def _migrate_from_legacy_registry(self) -> None:
        """Copy the old repo registry intent into the new overrides store."""
        with open(self._legacy_registry_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        for repo_data in payload.get("repos", []):
            record = ProjectOverrideRecord(
                id=repo_data["id"],
                display_name=repo_data.get("name"),
                current_path=_normalize_path(repo_data["path"]),
                historical_aliases=[],
                registered_at=_parse_datetime(repo_data.get("registered_at")),
            )
            self._records[record.id] = record

        self._save()

    def _save(self) -> None:
        """Persist the store to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "projects": [record.to_dict() for record in sorted(self._records.values(), key=lambda record: record.id)],
        }
        with open(self._path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def list_records(self) -> list[ProjectOverrideRecord]:
        """Return all override records."""
        return list(self._records.values())

    def get(self, project_id: str) -> ProjectOverrideRecord | None:
        """Get one project by id."""
        return self._records.get(project_id)

    def resolve_current_path(self, path: Path | str) -> ProjectOverrideRecord | None:
        """Find a record whose current path exactly matches the given path."""
        normalized = _normalize_path(path)
        for record in self._records.values():
            if record.current_path == normalized:
                return record
        return None

    def resolve_historical_alias(self, path: Path | str) -> ProjectOverrideRecord | None:
        """Find a record that tracks the given path as a historical alias."""
        normalized = _normalize_path(path)
        for record in self._records.values():
            if normalized in record.historical_aliases:
                return record
        return None

    def resolve_by_path(self, path: Path | str) -> ProjectOverrideRecord | None:
        """Find a record by current path or any historical alias."""
        return self.resolve_current_path(path) or self.resolve_historical_alias(path)

    def ensure_current_project(self, path: Path | str, *, display_name: str | None = None) -> ProjectOverrideRecord:
        """Create a project record for a current path if one does not already exist."""
        normalized = _normalize_path(path)
        record = self.resolve_current_path(normalized)
        if record is not None:
            if display_name and not record.display_name:
                record.display_name = display_name
                self._save()
            return record

        record = ProjectOverrideRecord(
            id=uuid4().hex[:8],
            display_name=display_name or normalized.name,
            current_path=normalized,
        )
        self._records[record.id] = record
        self._save()
        return record

    def ensure_project(self, path: Path | str, *, display_name: str | None = None) -> ProjectOverrideRecord:
        """Create a project record, reusing current-path or alias matches when present."""
        normalized = _normalize_path(path)
        record = self.resolve_current_path(normalized) or self.resolve_historical_alias(normalized)
        if record is not None:
            if display_name and not record.display_name:
                record.display_name = display_name
                self._save()
            return record

        return self.ensure_current_project(normalized, display_name=display_name)

    def rename(self, project_id: str, display_name: str) -> ProjectOverrideRecord | None:
        """Update a project's display name."""
        record = self._records.get(project_id)
        if record is None:
            return None
        record.display_name = display_name
        self._save()
        return record

    def move(self, project_id: str, current_path: Path | str) -> ProjectOverrideRecord | None:
        """Move a project to a new current path and keep the old path as an alias."""
        record = self._records.get(project_id)
        if record is None:
            return None

        normalized = _normalize_path(current_path)
        if record.current_path != normalized:
            if record.current_path not in record.historical_aliases:
                record.historical_aliases.append(record.current_path)
            record.current_path = normalized
            self._save()
        return record

    def add_alias(self, project_id: str, alias: Path | str) -> ProjectOverrideRecord | None:
        """Register an extra historical alias for a project."""
        record = self._records.get(project_id)
        if record is None:
            return None

        normalized = _normalize_path(alias)
        if normalized != record.current_path and normalized not in record.historical_aliases:
            record.historical_aliases.append(normalized)
            self._save()
        return record

    def remove(self, project_id: str) -> bool:
        """Remove a project record."""
        if project_id not in self._records:
            return False
        del self._records[project_id]
        self._save()
        return True
