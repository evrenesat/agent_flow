"""Tests for the public aflow library API."""

from __future__ import annotations

from pathlib import Path
import subprocess
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

    def test_prepare_startup_recovery_step_selection_dirty_confirmation(self) -> None:
        """Test the combined recovery -> step selection -> dirty confirmation chain.

        This regression test ensures that the full public API chain works without
        hidden state reconstruction: CONFIRM_RECOVERY -> PICK_STEP -> CONFIRM_WORKTREE_DIRTY -> PreparedRun.
        """
        from unittest.mock import patch as mock_patch

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

        # Step 1: Ask for recovery confirmation
        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            question1 = prepare_startup(request)

        self.assertIsInstance(question1, StartupQuestion)
        self.assertEqual(question1.kind, StartupQuestionKind.CONFIRM_RECOVERY)
        self.assertIsNotNone(question1.continuation_request)

        # Steps 2-4: Keep dirty worktree mock active throughout the chain
        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True), \
             mock_patch('aflow.api.startup.probe_worktree') as mock_probe:
            dirty_probe = type('obj', (object,), {'is_dirty': True, 'modified_count': 1, 'added_count': 0, 'removed_count': 0})()
            mock_probe.return_value = dirty_probe

            # Step 2: Confirm recovery, ask for step selection
            question2 = prepare_startup_with_answer(question1, request, True)
            self.assertIsInstance(question2, StartupQuestion)
            self.assertEqual(question2.kind, StartupQuestionKind.PICK_STEP)
            self.assertIn("step1", question2.choices)
            self.assertIn("step2", question2.choices)
            self.assertIsNotNone(question2.continuation_request)
            self.assertIsNotNone(question2.continuation_request.pre_recovered_plan)

            # Step 3: Select a step, ask for dirty confirmation
            question3 = prepare_startup_with_answer(question2, request, "step2")
            self.assertIsInstance(question3, StartupQuestion)
            self.assertEqual(question3.kind, StartupQuestionKind.CONFIRM_WORKTREE_DIRTY)
            self.assertIsNotNone(question3.continuation_request)

            # Step 4: Confirm dirty worktree, reach final PreparedRun
            result = prepare_startup_with_answer(question3, request, True)
            self.assertIsInstance(result, PreparedRun)
            # Verify selected step survived through the chain
            self.assertEqual(result.start_step, "step2")
            # Verify startup retry context survived
            self.assertIsNotNone(result.startup_retry)

    def test_prepare_startup_numeric_start_step_resolves_to_step_name(self) -> None:
        config_text = (
            '[aflow]\ndefault_workflow = "test"\n\n'
            '[workflow.test.steps.step1]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "step2"}]\n\n'
            '[workflow.test.steps.step2]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "step3"}]\n\n'
            '[workflow.test.steps.step3]\nrole = "architect"\nprompts = ["p"]\ngo = [{to = "END"}]\n\n'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n'
            '[roles]\narchitect = "opencode.default"\n\n'
            '[prompts]\np = "do it"\n'
        )
        config_path = _write_config(self.home_dir, config_text)
        workflow_config = load_workflow_config(config_path)
        plan_path = self.repo_root / "plan.md"
        plan_path.write_text("# Plan\n\n### [ ] Checkpoint 1: Test\n- [ ] step one\n")

        # Test numeric step "1" resolves to "step1"
        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step="1",
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        result = prepare_startup(request)
        self.assertIsInstance(result, PreparedRun)
        self.assertEqual(result.start_step, "step1")

        # Test numeric step "2" resolves to "step2"
        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step="2",
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        result = prepare_startup(request)
        self.assertIsInstance(result, PreparedRun)
        self.assertEqual(result.start_step, "step2")

    def test_prepare_startup_numeric_start_step_out_of_range_raises_error(self) -> None:
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
            workflow_name="test",
            start_step="5",  # Out of range
            max_turns=None,
            team=None,
            extra_instructions=(),
        )

        with self.assertRaises(StartupError) as ctx:
            prepare_startup(request)
        self.assertIn("out of range", str(ctx.exception))


class LibraryRunnerTests(unittest.TestCase):
    """Test the public runner library API with events."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home_dir = Path(self.temp_dir.name)
        self.repo_root = self.home_dir / "repo"
        self.repo_root.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_execute_workflow_with_observer_emits_events(self) -> None:
        """Test that execute_workflow emits events through the observer."""
        from aflow.api import (
            CollectingObserver,
            RunCompletedEvent,
            RunStartedEvent,
            execute_workflow,
        )
        from aflow.harnesses.base import HarnessAdapter

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
        plan_path.write_text("# Plan\n\n## Done When\n- Plan is complete\n\n### [x] Checkpoint 1: Test\n- [x] step one\n")

        request = StartupRequest(
            repo_root=self.repo_root,
            plan_path=plan_path,
            config_path=config_path,
            workflow_config=workflow_config,
            workflow_name="test",
            start_step=None,
            max_turns=5,
            team=None,
            extra_instructions=(),
        )

        result = prepare_startup(request)
        self.assertIsInstance(result, PreparedRun)

        observer = CollectingObserver()

        class FakeAdapter(HarnessAdapter):
            name = "fake"
            supports_effort = False

            def build_invocation(self, repo_root, model, system_prompt, user_prompt, effort=None):
                from aflow.harnesses.base import HarnessInvocation
                return HarnessInvocation(
                    argv=[sys.executable, "-c", "print('done')"],
                    env={},
                )

        execute_workflow(
            result,
            observer=observer,
            adapter=FakeAdapter(),
            runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "done\n", ""),
        )

        events = observer.events
        event_types = [type(e) for e in events if e]

        self.assertIn(RunStartedEvent, event_types)
        self.assertTrue(any(isinstance(e, RunCompletedEvent) for e in events))

    def test_collecting_observer_collects_events(self) -> None:
        """Test that CollectingObserver properly collects events."""
        from aflow.api import CollectingObserver, RunStartedEvent

        observer = CollectingObserver()
        event = RunStartedEvent.create(workflow_name="test")

        observer.on_event(event)

        self.assertEqual(len(observer.events), 1)
        self.assertIsInstance(observer.events[0], RunStartedEvent)
        self.assertEqual(observer.events[0].workflow_name, "test")

    def test_callback_observer_calls_callback(self) -> None:
        """Test that CallbackObserver calls the provided callback."""
        from aflow.api import CallbackObserver, RunStartedEvent

        collected = []

        def callback(event):
            collected.append(event)

        observer = CallbackObserver(callback)
        event = RunStartedEvent.create(workflow_name="test")

        observer.on_event(event)

        self.assertEqual(len(collected), 1)
        self.assertIsInstance(collected[0], RunStartedEvent)
        self.assertEqual(collected[0].workflow_name, "test")
