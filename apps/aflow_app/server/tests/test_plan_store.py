"""Tests for plan store."""

from __future__ import annotations

from pathlib import Path

import pytest

from aflow_app_server.plan_store import PlanStore, PlanStoreError


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    """Create a temporary repository directory."""
    return tmp_path / "test_repo"


@pytest.fixture
def plan_store(temp_repo: Path) -> PlanStore:
    """Create a plan store for testing."""
    return PlanStore(temp_repo)


def test_save_draft(plan_store: PlanStore, temp_repo: Path):
    """Test saving a draft plan."""
    content = "# Test Plan\n\nThis is a test plan."
    path = plan_store.save_draft("test-plan", content)

    assert path.exists()
    assert path.parent == temp_repo / "plans" / "drafts"
    assert path.name == "test-plan.md"
    assert path.read_text() == content


def test_save_draft_with_md_extension(plan_store: PlanStore):
    """Test saving a draft with .md extension already included."""
    content = "# Test Plan"
    path = plan_store.save_draft("test-plan.md", content)

    assert path.name == "test-plan.md"
    assert path.read_text() == content


def test_save_draft_invalid_name(plan_store: PlanStore):
    """Test saving a draft with invalid name."""
    with pytest.raises(PlanStoreError, match="Invalid plan name"):
        plan_store.save_draft("", "content")

    with pytest.raises(PlanStoreError, match="Invalid plan name"):
        plan_store.save_draft("path/to/plan", "content")

    with pytest.raises(PlanStoreError, match="Invalid plan name"):
        plan_store.save_draft("path\\to\\plan", "content")


def test_load_draft(plan_store: PlanStore):
    """Test loading a draft plan."""
    content = "# Test Plan\n\nContent here."
    plan_store.save_draft("test-plan", content)

    loaded = plan_store.load_draft("test-plan")
    assert loaded == content


def test_load_draft_with_extension(plan_store: PlanStore):
    """Test loading a draft with .md extension."""
    content = "# Test Plan"
    plan_store.save_draft("test-plan", content)

    loaded = plan_store.load_draft("test-plan.md")
    assert loaded == content


def test_load_draft_not_found(plan_store: PlanStore):
    """Test loading a non-existent draft."""
    with pytest.raises(PlanStoreError, match="Draft not found"):
        plan_store.load_draft("nonexistent")


def test_list_drafts_empty(plan_store: PlanStore):
    """Test listing drafts when none exist."""
    drafts = plan_store.list_drafts()
    assert drafts == []


def test_list_drafts(plan_store: PlanStore):
    """Test listing multiple drafts."""
    plan_store.save_draft("plan-a", "Content A")
    plan_store.save_draft("plan-b", "Content B")
    plan_store.save_draft("plan-c", "Content C")

    drafts = plan_store.list_drafts()
    assert drafts == ["plan-a", "plan-b", "plan-c"]


def test_delete_draft(plan_store: PlanStore):
    """Test deleting a draft."""
    plan_store.save_draft("test-plan", "Content")

    result = plan_store.delete_draft("test-plan")
    assert result is True

    drafts = plan_store.list_drafts()
    assert "test-plan" not in drafts


def test_delete_draft_not_found(plan_store: PlanStore):
    """Test deleting a non-existent draft."""
    result = plan_store.delete_draft("nonexistent")
    assert result is False


def test_promote_to_in_progress(plan_store: PlanStore, temp_repo: Path):
    """Test promoting a draft to in-progress."""
    content = "# Test Plan\n\nThis will be promoted."
    plan_store.save_draft("test-plan", content)

    path = plan_store.promote_to_in_progress("test-plan")

    assert path.exists()
    assert path.parent == temp_repo / "plans" / "in-progress"
    assert path.name == "test-plan.md"
    assert path.read_text() == content


def test_promote_to_in_progress_with_target_name(plan_store: PlanStore, temp_repo: Path):
    """Test promoting a draft with a different target name."""
    content = "# Test Plan"
    plan_store.save_draft("draft-plan", content)

    path = plan_store.promote_to_in_progress("draft-plan", "final-plan")

    assert path.name == "final-plan.md"
    assert path.read_text() == content


def test_promote_to_in_progress_not_found(plan_store: PlanStore):
    """Test promoting a non-existent draft."""
    with pytest.raises(PlanStoreError, match="Draft not found"):
        plan_store.promote_to_in_progress("nonexistent")


def test_list_in_progress_empty(plan_store: PlanStore):
    """Test listing in-progress plans when none exist."""
    plans = plan_store.list_in_progress()
    assert plans == []


def test_list_in_progress(plan_store: PlanStore):
    """Test listing multiple in-progress plans."""
    plan_store.save_draft("plan-a", "Content A")
    plan_store.save_draft("plan-b", "Content B")
    plan_store.promote_to_in_progress("plan-a")
    plan_store.promote_to_in_progress("plan-b")

    plans = plan_store.list_in_progress()
    assert plans == ["plan-a", "plan-b"]


def test_load_plan_draft(plan_store: PlanStore):
    """Test loading a plan by status (draft)."""
    content = "# Draft Plan"
    plan_store.save_draft("test-plan", content)

    loaded = plan_store.load_plan("test-plan", "draft")
    assert loaded == content


def test_load_plan_in_progress(plan_store: PlanStore):
    """Test loading a plan by status (in-progress)."""
    content = "# In Progress Plan"
    plan_store.save_draft("test-plan", content)
    plan_store.promote_to_in_progress("test-plan")

    loaded = plan_store.load_plan("test-plan", "in_progress")
    assert loaded == content


def test_load_plan_invalid_status(plan_store: PlanStore):
    """Test loading a plan with invalid status."""
    with pytest.raises(PlanStoreError, match="Invalid status"):
        plan_store.load_plan("test-plan", "invalid")


def test_load_plan_not_found(plan_store: PlanStore):
    """Test loading a non-existent plan."""
    with pytest.raises(PlanStoreError, match="Plan not found"):
        plan_store.load_plan("nonexistent", "draft")


def test_content_preserved_verbatim(plan_store: PlanStore):
    """Test that content is preserved verbatim during save/load/promote."""
    content = "# Plan\n\nLine 1\n\nLine 2\n\n- Item 1\n- Item 2\n"
    plan_store.save_draft("test-plan", content)

    loaded = plan_store.load_draft("test-plan")
    assert loaded == content

    plan_store.promote_to_in_progress("test-plan")
    promoted = plan_store.load_plan("test-plan", "in_progress")
    assert promoted == content
