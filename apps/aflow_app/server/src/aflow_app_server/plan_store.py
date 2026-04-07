"""Plan draft and promotion management."""

from __future__ import annotations

from pathlib import Path
from typing import Literal


class PlanStoreError(Exception):
    """Error in plan store operations."""
    pass


class PlanStore:
    """Manages plan drafts and promotions for a repository."""

    def __init__(self, project_path: Path) -> None:
        """Initialize the plan store.

        Args:
            project_path: Path to the project root.
        """
        self.project_path = project_path
        self.drafts_dir = project_path / "plans" / "drafts"
        self.in_progress_dir = project_path / "plans" / "in-progress"

    def save_draft(self, name: str, content: str) -> Path:
        """Save a plan as a draft.

        Args:
            name: Name for the draft (without .md extension).
            content: Markdown content of the plan.

        Returns:
            Path to the saved draft file.

        Raises:
            PlanStoreError: If save fails.
        """
        if not name or "/" in name or "\\" in name:
            raise PlanStoreError(f"Invalid plan name: {name}")

        # Ensure drafts directory exists
        self.drafts_dir.mkdir(parents=True, exist_ok=True)

        # Normalize name
        if not name.endswith(".md"):
            name = f"{name}.md"

        draft_path = self.drafts_dir / name

        try:
            # Write content verbatim with normal newline normalization
            draft_path.write_text(content, encoding="utf-8")
            return draft_path
        except OSError as e:
            raise PlanStoreError(f"Failed to save draft: {e}")

    def load_draft(self, name: str) -> str:
        """Load a draft plan.

        Args:
            name: Name of the draft (with or without .md extension).

        Returns:
            Content of the draft.

        Raises:
            PlanStoreError: If draft not found or load fails.
        """
        if not name.endswith(".md"):
            name = f"{name}.md"

        draft_path = self.drafts_dir / name

        if not draft_path.exists():
            raise PlanStoreError(f"Draft not found: {name}")

        try:
            return draft_path.read_text(encoding="utf-8")
        except OSError as e:
            raise PlanStoreError(f"Failed to load draft: {e}")

    def list_drafts(self) -> list[str]:
        """List all draft plan names.

        Returns:
            List of draft names (without .md extension).
        """
        if not self.drafts_dir.exists():
            return []

        return sorted([p.stem for p in self.drafts_dir.glob("*.md")])

    def delete_draft(self, name: str) -> bool:
        """Delete a draft plan.

        Args:
            name: Name of the draft (with or without .md extension).

        Returns:
            True if deleted, False if not found.
        """
        if not name.endswith(".md"):
            name = f"{name}.md"

        draft_path = self.drafts_dir / name

        if not draft_path.exists():
            return False

        try:
            draft_path.unlink()
            return True
        except OSError:
            return False

    def promote_to_in_progress(self, draft_name: str, target_name: str | None = None) -> Path:
        """Promote a draft to in-progress status.

        Args:
            draft_name: Name of the draft to promote (with or without .md extension).
            target_name: Optional target name for the in-progress plan. If None, uses draft_name.

        Returns:
            Path to the promoted plan file.

        Raises:
            PlanStoreError: If promotion fails.
        """
        if not draft_name.endswith(".md"):
            draft_name = f"{draft_name}.md"

        draft_path = self.drafts_dir / draft_name

        if not draft_path.exists():
            raise PlanStoreError(f"Draft not found: {draft_name}")

        # Determine target name
        if target_name is None:
            target_name = draft_name
        elif not target_name.endswith(".md"):
            target_name = f"{target_name}.md"

        # Ensure in-progress directory exists
        self.in_progress_dir.mkdir(parents=True, exist_ok=True)

        target_path = self.in_progress_dir / target_name

        try:
            # Copy content verbatim
            content = draft_path.read_text(encoding="utf-8")
            target_path.write_text(content, encoding="utf-8")
            return target_path
        except OSError as e:
            raise PlanStoreError(f"Failed to promote draft: {e}")

    def list_in_progress(self) -> list[str]:
        """List all in-progress plan names.

        Returns:
            List of in-progress plan names (without .md extension).
        """
        if not self.in_progress_dir.exists():
            return []

        return sorted([p.stem for p in self.in_progress_dir.glob("*.md")])

    def load_plan(self, name: str, status: Literal["draft", "in_progress"]) -> str:
        """Load a plan by name and status.

        Args:
            name: Name of the plan (with or without .md extension).
            status: Status of the plan ("draft" or "in_progress").

        Returns:
            Content of the plan.

        Raises:
            PlanStoreError: If plan not found or load fails.
        """
        if not name.endswith(".md"):
            name = f"{name}.md"

        if status == "draft":
            plan_path = self.drafts_dir / name
        elif status == "in_progress":
            plan_path = self.in_progress_dir / name
        else:
            raise PlanStoreError(f"Invalid status: {status}")

        if not plan_path.exists():
            raise PlanStoreError(f"Plan not found: {name} ({status})")

        try:
            return plan_path.read_text(encoding="utf-8")
        except OSError as e:
            raise PlanStoreError(f"Failed to load plan: {e}")
