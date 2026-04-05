"""Tests for the public aflow library API."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from aflow.api import (
    PreparedRun,
    StartupError,
    StartupQuestion,
    StartupQuestionKind,
    StartupRequest,
    prepare_startup,
    prepare_startup_with_answer,
)
from aflow.config import (
    AflowSection,
    load_workflow_config,
    WorkflowConfig,
    WorkflowHarnessConfig,
    WorkflowStepConfig,
    WorkflowUserConfig,
)
from aflow.plan import load_plan


def _write_config(home_dir: Path, text: str) -> Path:
    config_path = home_dir / ".config" / "aflow" / "aflow.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    aflow_lines: list[str] = []
    workflow_lines: list[str] = []
    current = aflow_lines
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            header = stripped[1 : stripped.find("]")]
            current = workflow_lines if header.startswith("workflow") else aflow_lines
        current.append(line)
    config_path.write_text("".join(aflow_lines), encoding="utf-8")
    config_path.with_name("workflows.toml").write_text("".join(workflow_lines), encoding="utf-8")
    return config_path


class LibraryStartupTests(unittest.TestCase):
    """Test the public startup library API."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home_dir = Path(self.temp_dir.name)
        self.repo_root = self.home_dir / "repo"
        self.repo_root.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_prepare_startup_requires_valid_workflow(self) -> None:
        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        plan_path.write_text("# Plan\n\n### [ ] Checkpoint 1: Test\n- [ ] step one\n")

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="invalid",
            start_step=None,
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        with self.assertRaises(StartupError) as ctx:
            prepare_startup(request)
        self.assertIn("not found", str(ctx.exception))

    def test_prepare_startup_uses_default_workflow(self) -> None:
        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        plan_path.write_text("# Plan\n\n### [ ] Checkpoint 1: Test\n- [ ] step one\n")

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name=None,
            start_step=None,
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        result = prepare_startup(request)
        self.assertIsInstance(result, PreparedRun)
        self.assertEqual(result.workflow_name, "test")

    def test_prepare_startup_invalid_start_step(self) -> None:
        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "step2"}]\n\n'
            '[workflow.test.steps.step2]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        plan_path.write_text("# Plan\n\n### [ ] Checkpoint 1: Test\n- [ ] step one\n")

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step="invalid_step",
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        with self.assertRaises(StartupError) as ctx:
            prepare_startup(request)
        self.assertIn("not found", str(ctx.exception))

    def test_prepare_startup_plan_complete_rejects_start_step(self) -> None:
        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        plan_path.write_text("# Plan\n\n### [x] Checkpoint 1: Done\n- [x] step one\n")

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step="step1",
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        with self.assertRaises(StartupError) as ctx:
            prepare_startup(request)
        self.assertIn("already complete", str(ctx.exception))

    def test_prepare_startup_multi_step_plan_asks_for_selection(self) -> None:
        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "step2"}]\n\n'
            '[workflow.test.steps.step2]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        plan_path.write_text("# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n\n### [ ] Checkpoint 2: Next\n- [ ] step two\n")

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step=None,
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            result = prepare_startup(request)
        self.assertIsInstance(result, StartupQuestion)
        self.assertEqual(result.kind, StartupQuestionKind.PICK_STEP)
        self.assertIn("step1", result.choices)
        self.assertIn("step2", result.choices)

    def test_prepare_startup_with_answer_pick_step(self) -> None:
        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "step2"}]\n\n'
            '[workflow.test.steps.step2]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        plan_path.write_text("# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n\n### [ ] Checkpoint 2: Next\n- [ ] step two\n")

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step=None,
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            question = prepare_startup(request)
        self.assertIsInstance(question, StartupQuestion)

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            result = prepare_startup_with_answer(question, request, 0)
        self.assertIsInstance(result, PreparedRun)
        self.assertEqual(result.start_step, "step1")

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            result = prepare_startup_with_answer(question, request, 1)
        self.assertIsInstance(result, PreparedRun)
        self.assertEqual(result.start_step, "step2")

    def test_prepare_startup_with_answer_step_by_name(self) -> None:
        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "step2"}]\n\n'
            '[workflow.test.steps.step2]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        plan_path.write_text("# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n\n### [ ] Checkpoint 2: Next\n- [ ] step two\n")

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step=None,
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            question = prepare_startup(request)
        self.assertIsInstance(question, StartupQuestion)

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            result = prepare_startup_with_answer(question, request, "step2")
        self.assertIsInstance(result, PreparedRun)
        self.assertEqual(result.start_step, "step2")

    def test_prepare_startup_recovery_then_step_selection(self) -> None:
        from aflow.plan import PlanParseError

        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "step2"}]\n\n'
            '[workflow.test.steps.step2]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        broken_plan = "# Plan\n\n### [x] Checkpoint 1: Broken\n- [ ] step one\n\n### [ ] Checkpoint 2: Next\n- [ ] step two\n"
        plan_path.write_text(broken_plan)

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step=None,
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            question1 = prepare_startup(request)

        self.assertIsInstance(question1, StartupQuestion)
        self.assertEqual(question1.kind, StartupQuestionKind.CONFIRM_RECOVERY)
        self.assertIsNotNone(question1.continuation_request)

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            question2 = prepare_startup_with_answer(question1, request, True)

        self.assertIsInstance(question2, StartupQuestion)
        self.assertEqual(question2.kind, StartupQuestionKind.PICK_STEP)
        self.assertIsNotNone(question2.continuation_request)
        self.assertIsNotNone(question2.continuation_request.pre_recovered_plan)

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            result = prepare_startup_with_answer(question2, request, "step2")

        self.assertIsInstance(result, PreparedRun)
        self.assertEqual(result.start_step, "step2")
        self.assertIsNotNone(result.startup_retry)

    def test_prepare_startup_recovery_then_dirty_confirmation(self) -> None:
        from unittest.mock import patch as mock_patch

        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        broken_plan = "# Plan\n\n### [x] Checkpoint 1: Broken\n- [ ] step one\n"
        plan_path.write_text(broken_plan)

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step=None,
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            question1 = prepare_startup(request)

        self.assertIsInstance(question1, StartupQuestion)
        self.assertEqual(question1.kind, StartupQuestionKind.CONFIRM_RECOVERY)

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True), \
             mock_patch('aflow.api.startup.probe_worktree') as mock_probe:
            mock_probe.return_value = type('obj', (object,), {'is_dirty': True, 'modified_count': 1, 'added_count': 0, 'removed_count': 0})()
            question2 = prepare_startup_with_answer(question1, request, True)

        self.assertIsInstance(question2, StartupQuestion)
        self.assertEqual(question2.kind, StartupQuestionKind.CONFIRM_WORKTREE_DIRTY)
        self.assertIsNotNone(question2.continuation_request)

        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            result = prepare_startup_with_answer(question2, request, True)

        self.assertIsInstance(result, PreparedRun)
        self.assertIsNotNone(result.startup_retry)
