from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitBaseline:
    head_sha: str | None
    tree_oid: str


@dataclass(frozen=True)
class GitSummary:
    modified_count: int
    added_count: int
    removed_count: int
    lines_added: int
    lines_removed: int
    commit_count: int
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class WorktreeProbe:
    is_dirty: bool
    modified_count: int
    added_count: int
    removed_count: int
    sample_paths: tuple[str, ...]


def probe_worktree(repo_root: Path) -> WorktreeProbe | None:
    """Return dirty-state summary, or None when git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    modified_count = 0
    added_count = 0
    removed_count = 0
    sample_paths: list[str] = []

    for line in result.stdout.splitlines():
        if len(line) < 3:
            continue
        xy = line[:2]
        path = line[3:]

        if len(sample_paths) < 3:
            sample_paths.append(path)

        if "?" in xy:
            added_count += 1
        elif "D" in xy:
            removed_count += 1
        elif "A" in xy:
            added_count += 1
        else:
            modified_count += 1

    is_dirty = bool(result.stdout.strip())
    return WorktreeProbe(
        is_dirty=is_dirty,
        modified_count=modified_count,
        added_count=added_count,
        removed_count=removed_count,
        sample_paths=tuple(sample_paths),
    )


def _create_tree_snapshot(repo_root: Path) -> str | None:
    """Create a tree OID from the full working tree using a temporary index file."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_index = os.path.join(tmp_dir, "index")
            env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
            add_result = subprocess.run(
                ["git", "add", "-A"],
                cwd=str(repo_root),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if add_result.returncode != 0:
                return None

            tree_result = subprocess.run(
                ["git", "write-tree"],
                cwd=str(repo_root),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if tree_result.returncode != 0:
                return None

            return tree_result.stdout.strip()
    except (OSError, FileNotFoundError):
        return None


def capture_baseline(repo_root: Path) -> GitBaseline | None:
    """Capture the current HEAD SHA and working-tree OID as a workflow-start baseline."""
    try:
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        head_sha: str | None = None
        if head_result.returncode == 0:
            head_sha = head_result.stdout.strip()

        tree_oid = _create_tree_snapshot(repo_root)
        if tree_oid is None:
            return None

        return GitBaseline(head_sha=head_sha, tree_oid=tree_oid)
    except (OSError, FileNotFoundError):
        return None


def classify_dirtiness_by_prefix(
    porcelain_output: str,
    prefix: str = "plans/",
) -> tuple[list[str], list[str]]:
    """Classify repo-relative paths from git porcelain output by prefix.

    Returns (paths_under_prefix, paths_outside_prefix).
    Both lists contain repo-relative paths from the porcelain output.
    """
    plan_paths: list[str] = []
    non_plan_paths: list[str] = []

    for line in porcelain_output.splitlines():
        if len(line) < 3:
            continue
        path = line[3:].lstrip()

        if path.startswith(prefix):
            plan_paths.append(path)
        else:
            non_plan_paths.append(path)

    return plan_paths, non_plan_paths


def summarize_since_baseline(repo_root: Path, baseline: GitBaseline) -> GitSummary | None:
    """Compare current working-tree state to baseline and return a delta summary."""
    try:
        current_tree = _create_tree_snapshot(repo_root)
        if current_tree is None:
            return None

        if current_tree == baseline.tree_oid:
            return GitSummary(
                modified_count=0,
                added_count=0,
                removed_count=0,
                lines_added=0,
                lines_removed=0,
                commit_count=0,
                changed_paths=(),
            )

        name_status = subprocess.run(
            ["git", "diff", "--name-status", "--no-renames", baseline.tree_oid, current_tree],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if name_status.returncode != 0:
            return None

        numstat = subprocess.run(
            ["git", "diff", "--numstat", "--no-renames", baseline.tree_oid, current_tree],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if numstat.returncode != 0:
            return None

        modified_count = 0
        added_count = 0
        removed_count = 0
        changed_paths: list[str] = []

        for line in name_status.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            status, path = parts
            changed_paths.append(path)
            status = status.strip()
            if status == "M":
                modified_count += 1
            elif status == "A":
                added_count += 1
            elif status == "D":
                removed_count += 1

        lines_added = 0
        lines_removed = 0
        for line in numstat.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            try:
                if parts[0] != "-":
                    lines_added += int(parts[0])
                if parts[1] != "-":
                    lines_removed += int(parts[1])
            except ValueError:
                pass

        commit_count = 0
        if baseline.head_sha is not None:
            rev_list = subprocess.run(
                ["git", "rev-list", "--count", f"{baseline.head_sha}..HEAD"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
            )
            if rev_list.returncode == 0:
                try:
                    commit_count = int(rev_list.stdout.strip())
                except ValueError:
                    pass

        return GitSummary(
            modified_count=modified_count,
            added_count=added_count,
            removed_count=removed_count,
            lines_added=lines_added,
            lines_removed=lines_removed,
            commit_count=commit_count,
            changed_paths=tuple(changed_paths),
        )
    except (OSError, FileNotFoundError):
        return None
