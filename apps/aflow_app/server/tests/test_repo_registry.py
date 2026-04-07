"""Tests for the repo registry."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from aflow_app_server.repo_registry import RepoRegistry, RepoRegistryError


@pytest.fixture
def temp_registry_path(tmp_path: Path) -> Path:
    """Create a temporary registry path."""
    return tmp_path / "repos.json"


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository."""
    project_path = tmp_path / "test_repo"
    project_path.mkdir()
    (project_path / ".git").mkdir()
    return project_path


@pytest.fixture
def temp_non_git_dir(tmp_path: Path) -> Path:
    """Create a temporary non-git directory."""
    dir_path = tmp_path / "non_git_dir"
    dir_path.mkdir()
    return dir_path


class TestRepoRegistry:
    """Tests for RepoRegistry."""

    def test_empty_registry(self, temp_registry_path: Path) -> None:
        """Test that an empty registry starts empty."""
        registry = RepoRegistry(temp_registry_path)
        assert registry.list_repos() == []

    def test_add_repo_git_root(
        self, temp_registry_path: Path, temp_git_repo: Path
    ) -> None:
        """Test adding a git repository."""
        registry = RepoRegistry(temp_registry_path)
        repo = registry.add_repo(str(temp_git_repo))

        assert repo.id
        assert repo.name == temp_git_repo.name
        assert repo.path == temp_git_repo
        assert repo.is_git_root is True
        assert repo.registered_at

    def test_add_repo_non_git(
        self, temp_registry_path: Path, temp_non_git_dir: Path
    ) -> None:
        """Test adding a non-git directory."""
        registry = RepoRegistry(temp_registry_path)
        repo = registry.add_repo(str(temp_non_git_dir))

        assert repo.is_git_root is False

    def test_add_repo_with_name(
        self, temp_registry_path: Path, temp_git_repo: Path
    ) -> None:
        """Test adding a repo with a custom name."""
        registry = RepoRegistry(temp_registry_path)
        repo = registry.add_repo(str(temp_git_repo), name="my-custom-name")

        assert repo.name == "my-custom-name"

    def test_add_repo_nonexistent_path(self, temp_registry_path: Path) -> None:
        """Test adding a non-existent path."""
        registry = RepoRegistry(temp_registry_path)

        with pytest.raises(RepoRegistryError, match="does not exist"):
            registry.add_repo("/nonexistent/path")

    def test_add_repo_file_path(self, temp_registry_path: Path, tmp_path: Path) -> None:
        """Test adding a file path instead of directory."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("test")

        registry = RepoRegistry(temp_registry_path)

        with pytest.raises(RepoRegistryError, match="not a directory"):
            registry.add_repo(str(file_path))

    def test_add_duplicate_repo(
        self, temp_registry_path: Path, temp_git_repo: Path
    ) -> None:
        """Test adding the same repo twice."""
        registry = RepoRegistry(temp_registry_path)
        registry.add_repo(str(temp_git_repo))

        with pytest.raises(RepoRegistryError, match="already registered"):
            registry.add_repo(str(temp_git_repo))

    def test_get_repo(self, temp_registry_path: Path, temp_git_repo: Path) -> None:
        """Test getting a repo by ID."""
        registry = RepoRegistry(temp_registry_path)
        added = registry.add_repo(str(temp_git_repo))

        retrieved = registry.get_repo(added.id)
        assert retrieved is not None
        assert retrieved.id == added.id

    def test_get_repo_not_found(self, temp_registry_path: Path) -> None:
        """Test getting a non-existent repo."""
        registry = RepoRegistry(temp_registry_path)
        assert registry.get_repo("nonexistent") is None

    def test_remove_repo(
        self, temp_registry_path: Path, temp_git_repo: Path
    ) -> None:
        """Test removing a repo."""
        registry = RepoRegistry(temp_registry_path)
        added = registry.add_repo(str(temp_git_repo))

        assert registry.remove_repo(added.id) is True
        assert registry.get_repo(added.id) is None

    def test_remove_repo_not_found(self, temp_registry_path: Path) -> None:
        """Test removing a non-existent repo."""
        registry = RepoRegistry(temp_registry_path)
        assert registry.remove_repo("nonexistent") is False

    def test_update_repo_name(
        self, temp_registry_path: Path, temp_git_repo: Path
    ) -> None:
        """Test updating a repo's name."""
        registry = RepoRegistry(temp_registry_path)
        added = registry.add_repo(str(temp_git_repo))

        updated = registry.update_repo(added.id, name="new-name")
        assert updated is not None
        assert updated.name == "new-name"

    def test_update_repo_not_found(self, temp_registry_path: Path) -> None:
        """Test updating a non-existent repo."""
        registry = RepoRegistry(temp_registry_path)
        assert registry.update_repo("nonexistent", name="new-name") is None

    def test_persistence(
        self, temp_registry_path: Path, temp_git_repo: Path
    ) -> None:
        """Test that registry persists across instances."""
        registry1 = RepoRegistry(temp_registry_path)
        added = registry1.add_repo(str(temp_git_repo))

        # Create a new registry instance
        registry2 = RepoRegistry(temp_registry_path)
        retrieved = registry2.get_repo(added.id)

        assert retrieved is not None
        assert retrieved.id == added.id
        assert retrieved.path == added.path

    def test_list_repos(
        self, temp_registry_path: Path, tmp_path: Path
    ) -> None:
        """Test listing multiple repos."""
        registry = RepoRegistry(temp_registry_path)

        repo1_path = tmp_path / "repo1"
        repo1_path.mkdir()
        (repo1_path / ".git").mkdir()

        repo2_path = tmp_path / "repo2"
        repo2_path.mkdir()
        (repo2_path / ".git").mkdir()

        registry.add_repo(str(repo1_path), name="repo1")
        registry.add_repo(str(repo2_path), name="repo2")

        repos = registry.list_repos()
        assert len(repos) == 2
        assert {r.name for r in repos} == {"repo1", "repo2"}
