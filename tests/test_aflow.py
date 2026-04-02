from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from importlib import resources
from unittest.mock import patch
from aflow.config import AflowSection, ConfigError, GoTransition, HarnessProfileConfig, WorkflowConfig, WorkflowHarnessConfig, WorkflowStepConfig, WorkflowUserConfig, bootstrap_config, find_placeholders, load_workflow_config, validate_workflow_config
from aflow.workflow import WorkflowError, _backup_original_plan, evaluate_condition, generate_new_plan_path, pick_transition, render_prompt, render_step_prompts, resolve_profile, run_workflow
from aflow.cli import _parse_run_args, build_parser, main
from aflow.harnesses.claude import ClaudeAdapter
from aflow.harnesses.codex import CodexAdapter
from aflow.harnesses.gemini import GeminiAdapter
from aflow.harnesses.kiro import KiroAdapter
from aflow.harnesses.opencode import OpencodeAdapter
from aflow.harnesses.pi import PiAdapter
from aflow.harnesses.base import HarnessInvocation
from aflow.plan import PlanParseError, PlanSnapshot, load_plan
from aflow.run_state import ControllerConfig, ControllerState, TurnRecord
from aflow.runlog import prune_old_runs
from aflow.status import build_banner
import pytest

def _write_plan(path: Path, text: str) -> None:
    path.write_text(text, encoding='utf-8')

def _write_config(home_dir: Path, text: str) -> Path:
    config_path = home_dir / '.config' / 'aflow' / 'aflow.toml'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding='utf-8')
    return config_path

class WorkflowCliTests(unittest.TestCase):

    def test_prog_name_is_aflow(self) -> None:
        parser = build_parser()
        assert parser.prog == 'aflow'

    def test_run_args_workflow_and_plan(self) -> None:
        workflow, plan, extra = _parse_run_args(['ralph', 'plan.md'])
        assert workflow == 'ralph'
        assert plan == 'plan.md'
        assert extra == ()

    def test_run_args_plan_only(self) -> None:
        workflow, plan, extra = _parse_run_args(['plan.md'])
        assert workflow is None
        assert plan == 'plan.md'
        assert extra == ()

    def test_run_args_extra_instructions(self) -> None:
        workflow, plan, extra = _parse_run_args(['plan.md', '--', 'keep edits small'])
        assert workflow is None
        assert plan == 'plan.md'
        assert extra == ('keep edits small',)

    def test_run_args_workflow_plan_extra(self) -> None:
        workflow, plan, extra = _parse_run_args(['ralph', 'plan.md', '--', 'be careful'])
        assert workflow == 'ralph'
        assert plan == 'plan.md'
        assert extra == ('be careful',)

    def test_run_args_empty(self) -> None:
        workflow, plan, extra = _parse_run_args([])
        assert workflow is None
        assert plan is None
        assert extra == ()

    def test_run_parser_max_turns_short_flag(self) -> None:
        args = build_parser().parse_args(['run', '-mt', '5', 'plan.md'])
        assert args.max_turns == 5

    def test_run_parser_max_turns_long_flag(self) -> None:
        args = build_parser().parse_args(['run', '--max-turns', '10', 'plan.md'])
        assert args.max_turns == 10

    def test_parser_no_legacy_flags(self) -> None:
        parser = build_parser()
        subparsers_action = next(a for a in parser._actions if hasattr(a, 'choices') and isinstance(a.choices, dict))
        run_subparser = subparsers_action.choices['run']
        run_actions = {a.dest for a in run_subparser._actions}
        assert 'harness' not in run_actions
        assert 'model' not in run_actions
        assert 'effort' not in run_actions
        assert 'profile' not in run_actions
        assert 'stagnation_limit' not in run_actions
        assert 'keep_runs' not in run_actions
        assert 'workflow' not in run_actions

    def test_install_subcommand_exposes_destination_and_yes(self) -> None:
        args = build_parser().parse_args(['install-skills', '--yes'])
        assert args.destination is None
        assert args.yes is True

    def test_root_help_mentions_install_skills_command(self) -> None:
        help_text = build_parser().format_help()
        assert "install-skills" in help_text

    def test_cli_bootstraps_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir)
            original_home = os.environ.get('HOME')
            try:
                os.environ['HOME'] = str(home_dir)
                result = main(['run', 'plan.md'])
            finally:
                if original_home is None:
                    os.environ.pop('HOME', None)
                else:
                    os.environ['HOME'] = original_home
            config_file = home_dir / '.config' / 'aflow' / 'aflow.toml'
            assert config_file.exists()
            assert result == 1

    def test_cli_rejects_missing_default_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir)
            _write_config(home_dir, '[aflow]\n\n[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            original_home = os.environ.get('HOME')
            try:
                os.environ['HOME'] = str(home_dir)
                result = main(['run', 'plan.md'])
            finally:
                if original_home is None:
                    os.environ.pop('HOME', None)
                else:
                    os.environ['HOME'] = original_home
            assert result == 1

    def test_cli_workflow_override(self) -> None:
        import aflow.cli as cli_module
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir)
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[workflow.other.steps.review]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
            original_home = os.environ.get('HOME')
            original_probe = cli_module.probe_worktree
            try:
                os.environ['HOME'] = str(home_dir)
                cli_module.probe_worktree = lambda _: None
                result = main(['run', 'other', str(plan_path)])
            finally:
                cli_module.probe_worktree = original_probe
                if original_home is None:
                    os.environ.pop('HOME', None)
                else:
                    os.environ['HOME'] = original_home
            assert result == 0

    def test_cli_install_skills_runs_without_config_bootstrap(self) -> None:
        import aflow.cli as cli_module

        calls: list[tuple[str | None, bool]] = []
        original = cli_module.install_skills
        try:
            def fake_install_skills(destination: str | None = None, *, yes: bool = False) -> None:
                calls.append((destination, yes))

            cli_module.install_skills = fake_install_skills
            result = main(['install-skills', '/tmp/dest', '--yes'])
        finally:
            cli_module.install_skills = original
        assert result == 0
        assert calls == [('/tmp/dest', True)]

    def test_cli_rejects_unknown_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir)
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            original_home = os.environ.get('HOME')
            try:
                os.environ['HOME'] = str(home_dir)
                result = main(['run', 'nonexistent', 'plan.md'])
            finally:
                if original_home is None:
                    os.environ.pop('HOME', None)
                else:
                    os.environ['HOME'] = original_home
            assert result == 1

    def test_run_parser_accepts_start_step(self) -> None:
        args = build_parser().parse_args(['run', '--start-step', 'implement_plan', 'plan.md'])
        assert args.start_step == 'implement_plan'

    def test_run_parser_start_step_defaults_to_none(self) -> None:
        args = build_parser().parse_args(['run', 'plan.md'])
        assert args.start_step is None

    def test_run_parser_start_step_with_workflow_name_and_plan(self) -> None:
        args = build_parser().parse_args(['run', '--start-step', 'implement_plan', 'my_workflow', 'plan.md'])
        assert args.start_step == 'implement_plan'
        assert 'my_workflow' in args.run_args
        assert 'plan.md' in args.run_args

    def test_run_parser_start_step_with_extra_instructions(self) -> None:
        args = build_parser().parse_args(['run', '--start-step', 'implement_plan', 'plan.md', '--', 'be careful'])
        assert args.start_step == 'implement_plan'
        assert 'plan.md' in args.run_args
        assert '--' in args.run_args
        assert 'be careful' in args.run_args

    def test_cli_start_step_must_be_valid_workflow_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir)
            _write_config(home_dir, '[aflow]\ndefault_workflow = "multi_step"\n\n[workflow.multi_step.steps.review_plan]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "implement_plan" }]\n\n[workflow.multi_step.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: Done\n- [x] step one\n')
            original_home = os.environ.get('HOME')
            try:
                os.environ['HOME'] = str(home_dir)
                result = main(['run', '--start-step', 'nonexistent', str(plan_path)])
            finally:
                if original_home is None:
                    os.environ.pop('HOME', None)
                else:
                    os.environ['HOME'] = original_home
            assert result == 1

class PlanParserTests(unittest.TestCase):

    def test_parser_counts_only_checkpoint_section_checkboxes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n- [ ] ignored outside sections\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n- [x] step two\n\n### [x] Checkpoint 2: Done\n- [x] step three\n\n')
            parsed = load_plan(plan_path)
            assert parsed.snapshot.current_checkpoint_name == 'Checkpoint 1: First'
            assert parsed.snapshot.unchecked_checkpoint_count == 1
            assert parsed.snapshot.current_checkpoint_unchecked_step_count == 1
            assert not parsed.snapshot.is_complete

    def test_parser_rejects_checked_checkpoint_with_unchecked_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: Broken\n- [ ] step one\n')
            with pytest.raises(PlanParseError) as exc_info:
                load_plan(plan_path)
            exc = exc_info.value
            assert exc.checkpoint_name == 'Checkpoint 1: Broken'
            assert exc.unchecked_step_count == 1
            assert exc.checkpoint_index == 1
            assert exc.total_checkpoint_count == 1

    def test_parser_rejects_files_without_checkpoint_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# No checkpoints\n- [ ] ignored\n')
            with pytest.raises(PlanParseError):
                load_plan(plan_path)

    def test_parser_total_checkpoint_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n\n### [ ] Checkpoint 2: Second\n- [ ] step two\n\n### [x] Checkpoint 3: Done\n- [x] step three\n')
            parsed = load_plan(plan_path)
            assert parsed.snapshot.total_checkpoint_count == 3

    def test_parser_current_checkpoint_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: Done\n- [x] step one\n\n### [ ] Checkpoint 2: Current\n- [ ] step two\n\n### [ ] Checkpoint 3: Pending\n- [ ] step three\n')
            parsed = load_plan(plan_path)
            assert parsed.snapshot.current_checkpoint_index == 2

    def test_parser_current_checkpoint_index_none_when_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: Done\n- [x] step one\n\n### [x] Checkpoint 2: Done\n- [x] step two\n')
            parsed = load_plan(plan_path)
            assert parsed.snapshot.is_complete
            assert parsed.snapshot.current_checkpoint_index is None

    def test_parser_global_section_after_last_checkpoint_does_not_affect_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(
                plan_path,
                '# Plan\n\n'
                '### [x] Checkpoint 1: First\n'
                '- [x] step one\n\n'
                '## Final Checklist\n'
                '- [ ] cleanup item one\n'
                '- [ ] cleanup item two\n',
            )
            parsed = load_plan(plan_path)
            assert parsed.snapshot.is_complete
            assert parsed.snapshot.current_checkpoint_name is None
            assert parsed.snapshot.unchecked_checkpoint_count == 0

    def test_parser_non_checkpoint_heading_ends_step_counting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(
                plan_path,
                '# Plan\n\n'
                '### [ ] Checkpoint 1: First\n'
                '- [ ] real step\n\n'
                '## Constraints\n'
                '- [ ] global constraint one\n'
                '- [ ] global constraint two\n',
            )
            parsed = load_plan(plan_path)
            assert parsed.snapshot.current_checkpoint_unchecked_step_count == 1

    def test_parser_unchecked_items_between_checkpoints_under_global_heading_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(
                plan_path,
                '# Plan\n\n'
                '### [x] Checkpoint 1: Done\n'
                '- [x] step one\n\n'
                '## Global Notes\n'
                '- [ ] global note\n\n'
                '### [ ] Checkpoint 2: Current\n'
                '- [ ] step two\n',
            )
            parsed = load_plan(plan_path)
            assert parsed.sections[0].unchecked_step_count == 0
            assert parsed.sections[1].unchecked_step_count == 1
            assert parsed.snapshot.current_checkpoint_unchecked_step_count == 1

class AdaptersTests(unittest.TestCase):

    def test_codex_without_effort(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gpt-5.4', system_prompt='SYSTEM', user_prompt='USER')
        assert invocation.argv == ('codex', 'exec', '--dangerously-bypass-approvals-and-sandbox', '-C', '/repo', '--model', 'gpt-5.4', 'SYSTEM\n\nUSER')
        assert invocation.prompt_mode == 'prefix-system-into-user-prompt'
        assert invocation.effective_prompt == 'SYSTEM\n\nUSER'

    def test_codex_with_effort(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gpt-5.4', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        argv = invocation.argv
        assert '-c' in argv
        assert 'model_reasoning_effort=\'"high"\'' in argv
        prompt_index = argv.index('SYSTEM\n\nUSER')
        assert argv[prompt_index - 2] == '-c'
        assert argv[-1] == 'SYSTEM\n\nUSER'

    def test_codex_effort_preserves_prompt_as_final_element(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gpt-5.4', system_prompt='PROMPT', user_prompt='INSTRUCTIONS', effort='low')
        assert invocation.argv[-1] == 'PROMPT\n\nINSTRUCTIONS'

    def test_codex_without_model_omits_model_flag(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv[-1] == 'SYSTEM\n\nUSER'

    def test_pi_without_effort(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='sonnet', system_prompt='SYSTEM', user_prompt='USER')
        assert invocation.argv == ('pi', '--print', '--system-prompt', 'SYSTEM', '--model', 'sonnet', '--tools', 'read,bash,edit,write,grep,find,ls', 'USER')
        assert invocation.prompt_mode == 'system-prompt-flag'

    def test_pi_with_effort(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='sonnet', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        argv = invocation.argv
        assert '--models' in argv
        assert 'sonnet:high' in argv
        assert '--model' not in argv
        models_index = argv.index('--models')
        assert argv[models_index + 1] == 'sonnet:high'

    def test_pi_with_effort_does_not_pass_both_model_flags(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='sonnet', system_prompt='S', user_prompt='U', effort='high')
        assert '--models' in invocation.argv
        assert '--model' not in invocation.argv

    def test_pi_without_model_and_with_effort_uses_thinking_flag(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER', effort='high')
        argv = invocation.argv
        assert '--thinking' in argv
        assert 'high' in argv
        assert '--models' not in argv
        assert '--model' not in argv

    def test_pi_without_model_and_without_effort_omits_model_flags(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert '--models' not in invocation.argv
        assert '--thinking' not in invocation.argv

    def test_claude_without_effort(self) -> None:
        adapter = ClaudeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='claude-sonnet-4-6', system_prompt='SYSTEM', user_prompt='USER')
        assert '--effort' not in invocation.argv
        assert invocation.argv == ('claude', '-p', '--system-prompt', 'SYSTEM', '--model', 'claude-sonnet-4-6', '--permission-mode', 'bypassPermissions', '--dangerously-skip-permissions', '--tools', 'default', 'USER')

    def test_claude_with_effort(self) -> None:
        adapter = ClaudeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='claude-sonnet-4-6', system_prompt='SYSTEM', user_prompt='USER', effort='low')
        argv = invocation.argv
        assert '--effort' in argv
        assert 'low' in argv
        effort_index = argv.index('--effort')
        assert argv[effort_index + 1] == 'low'

    def test_claude_without_model_omits_model_flag(self) -> None:
        adapter = ClaudeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv[0] == 'claude'

    def test_opencode_without_effort(self) -> None:
        adapter = OpencodeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='glm-5-turbo', system_prompt='SYSTEM', user_prompt='USER')
        assert invocation.argv == ('opencode', 'run', '--dangerously-skip-permissions', '--model', 'glm-5-turbo', '--format', 'default', '--dir', '/repo', 'SYSTEM\n\nUSER')
        assert invocation.prompt_mode == 'prefix-system-into-user-prompt'
        assert invocation.effective_prompt == 'SYSTEM\n\nUSER'

    def test_opencode_with_effort_ignores_effort(self) -> None:
        adapter = OpencodeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='glm-5-turbo', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        assert not adapter.supports_effort
        argv = invocation.argv
        assert 'effort' not in ' '.join(argv).lower()
        assert argv == ('opencode', 'run', '--dangerously-skip-permissions', '--model', 'glm-5-turbo', '--format', 'default', '--dir', '/repo', 'SYSTEM\n\nUSER')

    def test_opencode_without_model_omits_model_flag(self) -> None:
        adapter = OpencodeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv[0] == 'opencode'

    def test_gemini_without_effort(self) -> None:
        adapter = GeminiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gemini-2.5-pro', system_prompt='SYSTEM', user_prompt='USER')
        assert invocation.argv == ('gemini', '--prompt', 'SYSTEM\n\nUSER', '--model', 'gemini-2.5-pro', '--approval-mode', 'yolo', '--sandbox=false', '--output-format', 'text')
        assert invocation.prompt_mode == 'prefix-system-into-user-prompt'
        assert invocation.effective_prompt == 'SYSTEM\n\nUSER'

    def test_gemini_with_effort_ignores_effort(self) -> None:
        adapter = GeminiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gemini-2.5-pro', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        assert not adapter.supports_effort
        argv = invocation.argv
        assert 'effort' not in ' '.join(argv).lower()
        assert argv == ('gemini', '--prompt', 'SYSTEM\n\nUSER', '--model', 'gemini-2.5-pro', '--approval-mode', 'yolo', '--sandbox=false', '--output-format', 'text')

    def test_gemini_without_model_omits_model_flag(self) -> None:
        adapter = GeminiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv[0] == 'gemini'

    def test_kiro_without_effort(self) -> None:
        adapter = KiroAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='kiro-model', system_prompt='SYSTEM', user_prompt='USER')
        assert not adapter.supports_effort
        assert invocation.argv == ('kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', '--model', 'kiro-model', 'SYSTEM\n\nUSER')
        assert invocation.prompt_mode == 'prefix-system-into-user-prompt'
        assert invocation.effective_prompt == 'SYSTEM\n\nUSER'

    def test_kiro_without_model_omits_model_flag(self) -> None:
        adapter = KiroAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv == ('kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', 'SYSTEM\n\nUSER')

    def test_kiro_ignores_effort(self) -> None:
        adapter = KiroAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='kiro-model', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        assert not adapter.supports_effort
        assert 'effort' not in ' '.join(invocation.argv).lower()
        assert invocation.argv == ('kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', '--model', 'kiro-model', 'SYSTEM\n\nUSER')

class LazyBannerTests(unittest.TestCase):

    def test_banner_is_noop_when_rich_unavailable(self) -> None:
        import aflow.status as status_mod
        original = status_mod._RICH_AVAILABLE
        try:
            status_mod._RICH_AVAILABLE = False
            renderer = status_mod.BannerRenderer(config_harness='codex', config_model='gpt-5.4', config_effort=None, config_max_turns=15, config_plan_path=Path('/fake/plan.md'))
            state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
            renderer.start(state)
            renderer.update(state)
            renderer.stop(state)
            result = status_mod.build_banner(config_harness='codex', config_model='gpt-5.4', config_effort=None, config_max_turns=15, config_plan_path=Path('/fake/plan.md'), state=state)
            assert result is None
        finally:
            status_mod._RICH_AVAILABLE = original

    def test_banner_renders_default_model_label(self) -> None:
        from rich.console import Console
        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        panel = build_banner(config_harness='codex', config_model=None, config_effort=None, config_max_turns=15, config_plan_path=Path('/fake/plan.md'), state=state)
        assert panel is not None
        console = Console(record=True, width=80)
        console.print(panel)
        text = console.export_text()
        assert 'Harness/Model' in text
        assert 'codex / default' in text

    def test_banner_hides_speculative_followup_plan_path(self) -> None:
        from rich.console import Console
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            original = root / 'plan.md'
            original.write_text('# Plan\n\n### [ ] Checkpoint 1\n- [ ] step one\n', encoding='utf-8')
            followup = root / 'plan-cp01-v01.md'
            state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
            panel = build_banner(
                config_harness='codex',
                config_model='gpt-5.4',
                config_effort=None,
                config_max_turns=15,
                config_plan_path=original,
                original_plan_path=original,
                active_plan_path=original,
                new_plan_path=followup,
                state=state,
            )
            assert panel is not None
            console = Console(record=True, width=100)
            console.print(panel)
            text = console.export_text()
            assert followup.name not in text
            assert 'Active Plan' in text

    def test_banner_shows_active_followup_plan_when_file_exists(self) -> None:
        from rich.console import Console
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            original = root / 'plan.md'
            original.write_text('# Plan\n\n### [ ] Checkpoint 1\n- [ ] step one\n', encoding='utf-8')
            followup = root / 'plan-cp01-v01.md'
            followup.write_text('# Generated\n', encoding='utf-8')
            state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
            panel = build_banner(
                config_harness='codex',
                config_model='gpt-5.4',
                config_effort=None,
                config_max_turns=15,
                config_plan_path=original,
                original_plan_path=original,
                active_plan_path=followup,
                new_plan_path=followup,
                state=state,
            )
            assert panel is not None
            console = Console(record=True, width=100)
            console.print(panel)
            text = console.export_text()
            assert followup.name in text
            assert 'Active Plan' in text

    def test_banner_renders_workflow_graph_and_turn_history(self) -> None:
        from rich.console import Console
        state = ControllerState(last_snapshot=PlanSnapshot('Checkpoint 1', 1, 0, False))
        state.active_turn = 2
        state.current_turn_started_at = datetime.now(timezone.utc)
        state.turn_history.extend([
            TurnRecord(
                turn_number=1,
                step_name='review',
                resolved_harness_name='codex',
                resolved_model_display='codex / gpt-5.4',
                outcome='completed',
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                duration_seconds=12.0,
            ),
            TurnRecord(
                turn_number=2,
                step_name='implement',
                resolved_harness_name='opencode',
                resolved_model_display='opencode / glm-5-turbo',
                outcome='running',
                started_at=datetime.now(timezone.utc),
            ),
        ])
        panel = build_banner(
            workflow_name='loop',
            current_step_name='implement',
            workflow_steps={
                'review': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='implement'),)),
                'implement': WorkflowStepConfig(profile='opencode.default', prompts=('p',), go=(GoTransition(to='END'),)),
            },
            config_harness='opencode',
            config_model='glm-5-turbo',
            config_effort=None,
            config_max_turns=5,
            config_plan_path=Path('/fake/plan.md'),
            state=state,
        )
        assert panel is not None
        console = Console(record=True, width=140)
        console.print(panel)
        text = console.export_text()
        assert 'review' in text
        assert 'implement' in text
        assert 'go→' in text
        assert 'Turn 001' in text
        assert 'Turn 002' in text

class RetentionTests(unittest.TestCase):

    def test_retention_prune_old_runs_keeps_newest_twenty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_root = Path(tmpdir)
            for index in range(23):
                run_dir = runs_root / f'20260329T120000Z-{22 - index:08x}'
                run_dir.mkdir()
                mtime_ns = 1700000000000000000 + index * 1000000
                os.utime(run_dir, ns=(mtime_ns, mtime_ns))
            prune_old_runs(runs_root, keep_runs=20)
            remaining = sorted((path.name for path in runs_root.iterdir()))
            assert len(remaining) == 20
            assert remaining == sorted((f'20260329T120000Z-{22 - index:08x}' for index in range(3, 23)))

class AflowSectionConfigTests(unittest.TestCase):

    def _write_workflow_config(self, tmpdir: str, text: str) -> Path:
        home_dir = Path(tmpdir)
        config_path = home_dir / '.config' / 'aflow' / 'aflow.toml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(text, encoding='utf-8')
        return config_path

    def test_keep_runs_defaults_to_twenty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\ndefault_workflow = "simple"\n\n[workflow.simple.steps.s]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            config = load_workflow_config(config_path)
            assert config.aflow.keep_runs == 20

    def test_keep_runs_reads_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\ndefault_workflow = "simple"\nkeep_runs = 5\n\n[workflow.simple.steps.s]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            config = load_workflow_config(config_path)
            assert config.aflow.keep_runs == 5

    def test_keep_runs_rejects_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\nkeep_runs = 0\n')
            with pytest.raises(ConfigError):
                load_workflow_config(config_path)

    def test_keep_runs_rejects_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\nkeep_runs = -1\n')
            with pytest.raises(ConfigError):
                load_workflow_config(config_path)

    def test_keep_runs_rejects_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\nkeep_runs = true\n')
            with pytest.raises(ConfigError):
                load_workflow_config(config_path)

    def test_banner_files_limit_defaults_to_ten(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\ndefault_workflow = "simple"\n\n[workflow.simple.steps.s]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            config = load_workflow_config(config_path)
            assert config.aflow.banner_files_limit == 10

    def test_banner_files_limit_reads_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\nbanner_files_limit = 7\n\n[workflow.simple.steps.s]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            config = load_workflow_config(config_path)
            assert config.aflow.banner_files_limit == 7

    def test_banner_files_limit_rejects_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\nbanner_files_limit = 0\n')
            with pytest.raises(ConfigError):
                load_workflow_config(config_path)

    def test_banner_files_limit_rejects_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\nbanner_files_limit = -1\n')
            with pytest.raises(ConfigError):
                load_workflow_config(config_path)

    def test_banner_files_limit_rejects_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\nbanner_files_limit = true\n')
            with pytest.raises(ConfigError):
                load_workflow_config(config_path)


class WorkflowConfigTests(unittest.TestCase):

    def _write_workflow_config(self, tmpdir: str, text: str) -> Path:
        home_dir = Path(tmpdir)
        config_path = home_dir / '.config' / 'aflow' / 'aflow.toml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(text, encoding='utf-8')
        return config_path

    def test_parse_canonical_workflow_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\ndefault_workflow = "simple"\n\n[harness.opencode.profiles.default]\nmodel = "glm-5-turbo"\n\n[harness.codex.profiles.high]\nmodel = "gpt-5.4"\neffort = "high"\n\n[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["implementation_prompt"]\ngo = [\n  { to = "END", when = "DONE || MAX_TURNS_REACHED" },\n  { to = "implement_plan" },\n]\n\n[prompts]\nimplementation_prompt = "Work from {ACTIVE_PLAN_PATH}."\n')
            config = load_workflow_config(config_path)
            assert config.aflow.default_workflow == 'simple'
            assert 'opencode' in config.harnesses
            assert config.harnesses['opencode'].profiles['default'].model == 'glm-5-turbo'
            assert config.harnesses['codex'].profiles['high'].effort == 'high'
            assert 'simple' in config.workflows
            assert config.workflows['simple'].first_step == 'implement_plan'
            step = config.workflows['simple'].steps['implement_plan']
            assert step.profile == 'opencode.default'
            assert step.prompts == ('implementation_prompt',)
            assert len(step.go) == 2
            assert step.go[0].to == 'END'
            assert step.go[0].when == 'DONE || MAX_TURNS_REACHED'
            assert step.go[1].to == 'implement_plan'
            assert step.go[1].when is None
            assert config.prompts['implementation_prompt'] == 'Work from {ACTIVE_PLAN_PATH}.'

    def test_parse_multi_step_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\ndefault_workflow = "review_loop"\n\n[harness.claude.profiles.opus]\nmodel = "claude-opus-4"\n\n[harness.opencode.profiles.turbo]\nmodel = "glm-5-turbo"\n\n[harness.codex.profiles.high]\nmodel = "gpt-5.4"\neffort = "high"\n\n[workflow.review_loop.steps.review_plan]\nprofile = "claude.opus"\nprompts = ["review_prompt"]\ngo = [{ to = "implement_plan" }]\n\n[workflow.review_loop.steps.implement_plan]\nprofile = "opencode.turbo"\nprompts = ["implementation_prompt"]\ngo = [{ to = "review_implementation" }]\n\n[workflow.review_loop.steps.review_implementation]\nprofile = "codex.high"\nprompts = ["review_prompt", "fix_plan_prompt"]\ngo = [\n  { to = "END", when = "DONE || MAX_TURNS_REACHED" },\n  { to = "implement_plan" },\n]\n\n[prompts]\nreview_prompt = "Review the plan."\nimplementation_prompt = "Implement from {ACTIVE_PLAN_PATH}."\nfix_plan_prompt = "Write new plan to {NEW_PLAN_PATH}."\n')
            config = load_workflow_config(config_path)
            wf = config.workflows['review_loop']
            assert wf.first_step == 'review_plan'
            assert len(wf.steps) == 3
            assert wf.steps['review_plan'].profile == 'claude.opus'
            assert wf.steps['review_implementation'].prompts == ('review_prompt', 'fix_plan_prompt')

    def test_parse_rejects_legacy_default_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, 'default_harness = "codex"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'default_harness' in str(ctx.value)

    def test_parse_rejects_legacy_default_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\ndefault_model = "gpt-5.4"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'default_model' in str(ctx.value)

    def test_parse_rejects_bare_harness_step_selector(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.implement_plan]\nprofile = "opencode"\nprompts = ["p1"]\ngo = [{ to = "END" }]\n\n[prompts]\np1 = "do it"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'fully qualified' in str(ctx.value)

    def test_parse_rejects_harness_level_model_and_effort(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[harness.opencode]\nmodel = "glm-5-turbo"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'model' in str(ctx.value)

    def test_parse_rejects_invalid_condition_not_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p1"]\ngo = [{ to = "END", when = "NOT_DONE" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np1 = "do it"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'NOT_DONE' in str(ctx.value)

    def test_parse_rejects_invalid_condition_max_iterations_not_reached(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p1"]\ngo = [{ to = "END", when = "MAX_ITERATIONS_NOT_REACHED" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np1 = "do it"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'MAX_ITERATIONS_NOT_REACHED' in str(ctx.value)

    def test_parse_rejects_invalid_transition_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p1"]\ngo = [{ to = "nonexistent_step" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np1 = "do it"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'nonexistent_step' in str(ctx.value)

    def test_parse_rejects_empty_prompts_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = []\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'prompts' in str(ctx.value)
            assert 'empty' in str(ctx.value)

    def test_parse_rejects_missing_go(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'go' in str(ctx.value)

    def test_parse_rejects_empty_go_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = []\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'go' in str(ctx.value)
            assert 'empty' in str(ctx.value)

    def test_parse_accepts_unconditional_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p1"]\ngo = [{ to = "implement_plan" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np1 = "do it"\n')
            config = load_workflow_config(config_path)
            step = config.workflows['simple'].steps['implement_plan']
            assert len(step.go) == 1
            assert step.go[0].to == 'implement_plan'
            assert step.go[0].when is None

    def test_parse_accepts_complex_condition_expressions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p1"]\ngo = [\n  { to = "END", when = "(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS" },\n  { to = "s1" },\n]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np1 = "do it"\n')
            config = load_workflow_config(config_path)
            step = config.workflows['simple'].steps['s1']
            assert step.go[0].when == '(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS'

    def test_prompts_preserve_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["alpha", "beta", "gamma"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\ngamma = "third"\nalpha = "first"\nbeta = "second"\n')
            config = load_workflow_config(config_path)
            step = config.workflows['simple'].steps['s1']
            assert step.prompts == ('alpha', 'beta', 'gamma')

    def test_go_transitions_preserve_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [\n  { to = "END", when = "DONE" },\n  { to = "END", when = "MAX_TURNS_REACHED" },\n  { to = "s2" },\n]\n\n[workflow.simple.steps.s2]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            config = load_workflow_config(config_path)
            step = config.workflows['simple'].steps['s1']
            assert len(step.go) == 3
            assert step.go[0].to == 'END'
            assert step.go[0].when == 'DONE'
            assert step.go[1].to == 'END'
            assert step.go[1].when == 'MAX_TURNS_REACHED'
            assert step.go[2].to == 's2'
            assert step.go[2].when is None

    def test_placeholder_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\ndefault_workflow = "simple"\n\n[harness.opencode.profiles.default]\nmodel = "FILL_IN_MODEL"\n\n[harness.codex.profiles.high]\nmodel = "gpt-5.4"\neffort = "high"\n\n[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[prompts]\np = "do it"\n')
            config = load_workflow_config(config_path)
            placeholders = find_placeholders(config)
            assert placeholders == ['harness.opencode.profiles.default.model']

    def test_placeholder_settings_report_exact_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[aflow]\ndefault_workflow = "simple"\n\n[harness.opencode.profiles.default]\nmodel = "FILL_IN_MODEL"\n\n[harness.codex.profiles.high]\nmodel = "FILL_IN_MODEL"\neffort = "high"\n\n[harness.claude.profiles.opus]\nmodel = "FILL_IN_MODEL"\neffort = "medium"\n\n[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[prompts]\np = "do it"\n')
            config = load_workflow_config(config_path)
            placeholders = find_placeholders(config)
            assert len(placeholders) == 3
            assert 'harness.claude.profiles.opus.model' in placeholders
            assert 'harness.codex.profiles.high.model' in placeholders
            assert 'harness.opencode.profiles.default.model' in placeholders

    def test_bundled_config_matches_canonical_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'starter.toml'
            config_path.write_text(resources.files('aflow').joinpath('aflow.toml').read_text(encoding='utf-8'), encoding='utf-8')
            config = load_workflow_config(config_path)
            assert config.aflow.default_workflow == 'ralph'
            assert 'opencode' in config.harnesses
            assert 'codex' in config.harnesses
            assert 'claude' in config.harnesses
            assert config.harnesses['opencode'].profiles['turbo'].model == 'zai-coding-plan/glm-5-turbo'
            assert config.harnesses['codex'].profiles['high'].model == 'GPT-5.4'
            assert config.harnesses['codex'].profiles['high'].effort == 'high'
            assert 'ralph' in config.workflows
            assert 'review_implement_review' in config.workflows
            assert 'review_implement_cp_review' in config.workflows
            step = config.workflows['ralph'].steps['implement_plan']
            assert step.profile == 'opencode.turbo'
            assert step.prompts == ('input_vars', 'simple_implementation')
            assert len(step.go) == 2
            assert step.go[0].to == 'END'
            assert step.go[0].when == 'DONE || MAX_TURNS_REACHED'
            assert step.go[1].to == 'implement_plan'
            assert step.go[1].when is None
            assert config.aflow.banner_files_limit == 10
            assert config.prompts['simple_implementation'] == "Work from {ACTIVE_PLAN_PATH}. Use 'aflow-execute-plan' skill."
            assert config.prompts['followup_implementation'] == "Use 'aflow-execute-plan' skill."
            assert config.prompts['cp_loop_implementation'] == "Use 'aflow-execute-checkpoint' skill."
            assert config.prompts['review_squash'] == "Use 'aflow-review-squash' skill."
            assert config.prompts['review_cp'] == "Use 'aflow-review-checkpoint' skill."
            assert config.prompts['final_review'] == "Use 'aflow-review-final' skill."

    def test_bundled_config_validates_without_errors(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        config = load_workflow_config(repo_root / 'aflow' / 'aflow.toml')
        assert validate_workflow_config(config) == []

    def test_bootstrap_creates_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'aflow' / 'aflow.toml'
            result = bootstrap_config(config_path)
            assert result.exists()
            assert result == config_path
            packaged_text = resources.files('aflow').joinpath('aflow.toml').read_text(encoding='utf-8')
            assert result.read_text(encoding='utf-8') == packaged_text

    def test_bootstrap_does_not_overwrite_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'aflow' / 'aflow.toml'
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text('existing', encoding='utf-8')
            result = bootstrap_config(config_path)
            assert result.read_text(encoding='utf-8') == 'existing'

    def test_parse_rejects_unsupported_workflow_level_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple]\nstart = "review"\n\n[workflow.simple.steps.review]\nprofile = "opencode.default"\nprompts = ["p"]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "x"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'workflow.simple' in str(ctx.value)
            assert 'start' in str(ctx.value)

    def test_parse_rejects_invalid_condition_operator_eq(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END", when = "DONE == NEW_PLAN_EXISTS" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "x"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert '==' in str(ctx.value)

    def test_parse_rejects_invalid_condition_operator_plus(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END", when = "DONE + NEW_PLAN_EXISTS" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "x"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert '+' in str(ctx.value)

    def test_validate_workflow_config_default_workflow_missing_reports_exact_path(self) -> None:
        config = WorkflowUserConfig(aflow=AflowSection(default_workflow='nonexistent'), workflows={'simple': WorkflowConfig()})
        errors = validate_workflow_config(config)
        assert any(('aflow.default_workflow' in e for e in errors))
        assert any(('nonexistent' in e for e in errors))

    def test_validate_workflow_config_unknown_harness_reports_exact_path(self) -> None:
        wf = WorkflowConfig(steps={'s1': WorkflowStepConfig(profile='unknown_harness.p1', prompts=('p1',))})
        config = WorkflowUserConfig(workflows={'w': wf}, prompts={'p1': 'text'})
        errors = validate_workflow_config(config)
        assert any(('workflow.w.steps.s1.profile' in e for e in errors))

    def test_validate_workflow_config_unknown_profile_reports_exact_path(self) -> None:
        wf = WorkflowConfig(steps={'s1': WorkflowStepConfig(profile='opencode.missing', prompts=('p1',))})
        config = WorkflowUserConfig(harnesses={'opencode': WorkflowHarnessConfig(profiles={})}, workflows={'w': wf}, prompts={'p1': 'text'})
        errors = validate_workflow_config(config)
        assert any(('workflow.w.steps.s1.profile' in e for e in errors))

    def test_validate_workflow_config_unknown_prompt_reports_exact_path(self) -> None:
        wf = WorkflowConfig(steps={'s1': WorkflowStepConfig(profile='opencode.default', prompts=('missing_prompt',))})
        config = WorkflowUserConfig(harnesses={'opencode': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='m')})}, workflows={'w': wf})
        errors = validate_workflow_config(config)
        assert any(('workflow.w.steps.s1.prompts[0]' in e for e in errors))

    def test_parse_accepts_complex_condition_with_negation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [\n  { to = "END", when = "!(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS" },\n  { to = "s1" },\n]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            config = load_workflow_config(config_path)
            step = config.workflows['simple'].steps['s1']
            assert step.go[0].when == '!(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS'

    def test_validate_workflow_config_default_workflow_missing(self) -> None:
        config = WorkflowUserConfig(aflow=AflowSection(default_workflow='nonexistent'), workflows={'simple': WorkflowConfig()})
        errors = validate_workflow_config(config)
        assert any(('nonexistent' in e for e in errors))

    def test_validate_workflow_config_passes_for_valid_config(self) -> None:
        wf = WorkflowConfig(steps={'s1': WorkflowStepConfig(profile='opencode.default', prompts=('p1',))})
        config = WorkflowUserConfig(aflow=AflowSection(default_workflow='w'), harnesses={'opencode': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='m')})}, workflows={'w': wf}, prompts={'p1': 'text'})
        errors = validate_workflow_config(config)
        assert errors == []

    def test_load_returns_empty_config_for_missing_file(self) -> None:
        config = load_workflow_config(Path('/nonexistent/aflow.toml'))
        assert config.aflow.default_workflow is None
        assert config.harnesses == {}
        assert config.workflows == {}
        assert config.prompts == {}

    def test_parse_rejects_unsupported_top_level_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[server]\nport = 8080\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'server' in str(ctx.value)

    def test_parse_rejects_unsupported_condition_in_when(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END", when = "DONE && STALEMATE" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'STALEMATE' in str(ctx.value)

    def test_first_step_is_first_declared(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.review]\nprofile = "claude.opus"\nprompts = ["p1"]\ngo = [{ to = "implement" }]\n\n[workflow.simple.steps.implement]\nprofile = "opencode.default"\nprompts = ["p2"]\ngo = [{ to = "END" }]\n\n[harness.claude.profiles.opus]\nmodel = "m"\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np1 = "review"\np2 = "implement"\n')
            config = load_workflow_config(config_path)
            wf = config.workflows['simple']
            assert wf.first_step == 'review'

    def test_missing_steps_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple]\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'steps' in str(ctx.value)

    def test_step_missing_profile_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprompts = ["p"]\n\n[prompts]\np = "do it"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'profile' in str(ctx.value)

    def test_step_missing_prompts_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\n\n[harness.opencode.profiles.default]\nmodel = "m"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'prompts' in str(ctx.value)

    def test_go_missing_to_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, '[workflow.simple.steps.s1]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ when = "DONE" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'to' in str(ctx.value)

class WorkflowRuntimeTests(unittest.TestCase):

    def test_prompt_rendering_supports_inline_and_file_uri_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_dir = root / 'config'
            working_dir = root / 'cwd'
            config_dir.mkdir()
            working_dir.mkdir()
            config_prompt = config_dir / 'relative.txt'
            config_prompt.write_text('Config content with {ACTIVE_PLAN_PATH}', encoding='utf-8')
            absolute_prompt = root / 'absolute' / 'path.txt'
            absolute_prompt.parent.mkdir()
            absolute_prompt.write_text('Absolute content with {ORIGINAL_PLAN_PATH}', encoding='utf-8')
            cwd_prompt = working_dir / 'relative.txt'
            cwd_prompt.write_text('Cwd content with {NEW_PLAN_PATH}', encoding='utf-8')
            original = root / 'plan.md'
            new_plan = root / 'plan-cp01-v01.md'
            active = root / 'active.md'
            result = render_prompt('file://relative.txt', config_dir=config_dir, working_dir=working_dir, original_plan_path=original, new_plan_path=new_plan, active_plan_path=active)
            assert result == f'Config content with {active}'
            absolute_result = render_prompt(f'file://{absolute_prompt}', config_dir=config_dir, working_dir=working_dir, original_plan_path=original, new_plan_path=new_plan, active_plan_path=active)
            assert absolute_result == f'Absolute content with {original}'
            cwd_result = render_prompt('file://./relative.txt', config_dir=config_dir, working_dir=working_dir, original_plan_path=original, new_plan_path=new_plan, active_plan_path=active)
            assert cwd_result == f'Cwd content with {new_plan}'
            result_inline = render_prompt('Work from {ACTIVE_PLAN_PATH}. New: {NEW_PLAN_PATH}. Original: {ORIGINAL_PLAN_PATH}', config_dir=config_dir, working_dir=working_dir, original_plan_path=original, new_plan_path=new_plan, active_plan_path=active)
            assert result_inline == f'Work from {active}. New: {new_plan}. Original: {original}'

    def test_prompt_rendering_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_dir = root / 'config'
            working_dir = root / 'cwd'
            config_dir.mkdir()
            working_dir.mkdir()
            with pytest.raises(WorkflowError) as ctx:
                render_prompt('file://./nonexistent.txt', config_dir=config_dir, working_dir=working_dir, original_plan_path=Path('/fake/plan.md'), new_plan_path=Path('/fake/new.md'), active_plan_path=Path('/fake/plan.md'))
            assert str(working_dir / 'nonexistent.txt') in str(ctx.value)

    def test_render_step_prompts_unknown_key_raises(self) -> None:
        step = WorkflowStepConfig(profile='opencode.default', prompts=('missing_key',))
        config = WorkflowUserConfig(prompts={})
        with pytest.raises(WorkflowError) as ctx:
            render_step_prompts(step, config, config_dir=Path('/cfg'), working_dir=Path('/cwd'), original_plan_path=Path('/p.md'), new_plan_path=Path('/n.md'), active_plan_path=Path('/a.md'))
        assert 'missing_key' in str(ctx.value)

    def test_render_step_prompts_joins_multiple_prompts(self) -> None:
        step = WorkflowStepConfig(profile='opencode.default', prompts=('p1', 'p2'))
        config = WorkflowUserConfig(prompts={'p1': 'First {ORIGINAL_PLAN_PATH}', 'p2': 'Second {ACTIVE_PLAN_PATH}'})
        result = render_step_prompts(step, config, config_dir=Path('/cfg'), working_dir=Path('/cwd'), original_plan_path=Path('/orig.md'), new_plan_path=Path('/new.md'), active_plan_path=Path('/active.md'))
        assert result == 'First /orig.md\n\nSecond /active.md'

    def test_new_plan_path_increments_version_for_checkpoint_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            original = parent / 'plan.md'
            original.write_text('dummy', encoding='utf-8')
            p1 = generate_new_plan_path(original, checkpoint_index=1)
            assert p1.name == 'plan-cp01-v01.md'
            p1.touch()
            p2 = generate_new_plan_path(original, checkpoint_index=1)
            assert p2.name == 'plan-cp01-v02.md'
            p2.touch()
            p3 = generate_new_plan_path(original, checkpoint_index=1)
            assert p3.name == 'plan-cp01-v03.md'
            p4 = generate_new_plan_path(original, checkpoint_index=2)
            assert p4.name == 'plan-cp02-v01.md'

    def test_new_plan_path_uses_correct_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            original = parent / 'plan.markdown'
            original.write_text('dummy', encoding='utf-8')
            p1 = generate_new_plan_path(original, checkpoint_index=1)
            assert p1.name == 'plan-cp01-v01.markdown'

    def test_new_plan_path_none_checkpoint_uses_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            original = parent / 'plan.md'
            original.write_text('dummy', encoding='utf-8')
            p1 = generate_new_plan_path(original, checkpoint_index=None)
            assert p1.name == 'plan-cp01-v01.md'

    def test_original_plan_backup_creates_repo_root_backup_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / 'repo'
            repo_root.mkdir()
            original = root / 'plan.md'
            original.write_text('# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n', encoding='utf-8')

            backup_path = _backup_original_plan(repo_root, original)

            expected = repo_root / 'plans' / 'backups' / 'plan.md'
            assert backup_path == expected
            assert expected.read_text(encoding='utf-8') == original.read_text(encoding='utf-8')
            assert len(list((repo_root / 'plans' / 'backups').iterdir())) == 1

    def test_original_plan_backup_reuses_identical_existing_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / 'repo'
            repo_root.mkdir()
            original = root / 'plan.md'
            text = '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n'
            original.write_text(text, encoding='utf-8')
            backup_dir = repo_root / 'plans' / 'backups'
            backup_dir.mkdir(parents=True)
            (backup_dir / 'plan.md').write_text(text, encoding='utf-8')

            first = _backup_original_plan(repo_root, original)
            second = _backup_original_plan(repo_root, original)

            assert first == backup_dir / 'plan.md'
            assert second == backup_dir / 'plan.md'
            assert sorted(child.name for child in backup_dir.iterdir()) == ['plan.md']

    def test_original_plan_backup_reuses_identical_versioned_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / 'repo'
            repo_root.mkdir()
            original = root / 'plan.md'
            text = '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n'
            original.write_text(text, encoding='utf-8')
            backup_dir = repo_root / 'plans' / 'backups'
            backup_dir.mkdir(parents=True)
            (backup_dir / 'plan.md').write_text('different\n', encoding='utf-8')
            (backup_dir / 'plan_v02.md').write_text(text, encoding='utf-8')

            backup_path = _backup_original_plan(repo_root, original)

            assert backup_path == backup_dir / 'plan_v02.md'
            assert sorted(child.name for child in backup_dir.iterdir()) == ['plan.md', 'plan_v02.md']

    def test_original_plan_backup_versions_conflicting_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / 'repo'
            repo_root.mkdir()
            original = root / 'plan.md'
            backup_dir = repo_root / 'plans' / 'backups'
            backup_dir.mkdir(parents=True)
            original.write_text('first version\n', encoding='utf-8')
            (backup_dir / 'plan.md').write_text('different base\n', encoding='utf-8')

            first_backup = _backup_original_plan(repo_root, original)
            assert first_backup == backup_dir / 'plan_v02.md'

            original.write_text('second version\n', encoding='utf-8')
            second_backup = _backup_original_plan(repo_root, original)
            assert second_backup == backup_dir / 'plan_v03.md'
            assert sorted(child.name for child in backup_dir.iterdir()) == ['plan.md', 'plan_v02.md', 'plan_v03.md']

    def test_condition_parsing_simple_symbols(self) -> None:
        assert evaluate_condition('DONE', done=True, new_plan_exists=False, max_turns_reached=False)
        assert not evaluate_condition('DONE', done=False, new_plan_exists=False, max_turns_reached=False)
        assert evaluate_condition('NEW_PLAN_EXISTS', done=False, new_plan_exists=True, max_turns_reached=False)
        assert evaluate_condition('MAX_TURNS_REACHED', done=False, new_plan_exists=False, max_turns_reached=True)

    def test_condition_parsing_or(self) -> None:
        assert evaluate_condition('DONE || MAX_TURNS_REACHED', done=True, new_plan_exists=False, max_turns_reached=False)
        assert evaluate_condition('DONE || MAX_TURNS_REACHED', done=False, new_plan_exists=False, max_turns_reached=True)
        assert not evaluate_condition('DONE || MAX_TURNS_REACHED', done=False, new_plan_exists=False, max_turns_reached=False)

    def test_condition_parsing_and(self) -> None:
        assert evaluate_condition('DONE && NEW_PLAN_EXISTS', done=True, new_plan_exists=True, max_turns_reached=False)
        assert not evaluate_condition('DONE && NEW_PLAN_EXISTS', done=True, new_plan_exists=False, max_turns_reached=False)

    def test_condition_parsing_negation(self) -> None:
        assert evaluate_condition('!DONE', done=False, new_plan_exists=False, max_turns_reached=False)
        assert not evaluate_condition('!DONE', done=True, new_plan_exists=False, max_turns_reached=False)

    def test_condition_parsing_parentheses(self) -> None:
        assert evaluate_condition('(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS', done=True, new_plan_exists=True, max_turns_reached=False)
        assert not evaluate_condition('(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS', done=False, new_plan_exists=False, max_turns_reached=False)

    def test_condition_parsing_complex(self) -> None:
        expr = '!(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS'
        assert evaluate_condition(expr, done=False, new_plan_exists=True, max_turns_reached=False)
        assert not evaluate_condition(expr, done=True, new_plan_exists=True, max_turns_reached=False)

    def test_ordered_transitions_first_match_wins(self) -> None:
        transitions = (GoTransition(to='END', when='DONE'), GoTransition(to='END', when='MAX_TURNS_REACHED'), GoTransition(to='step2'))
        assert pick_transition(transitions, step_path='workflow.w.steps.s', done=True, new_plan_exists=False, max_turns_reached=False) == 'END'
        assert pick_transition(transitions, step_path='workflow.w.steps.s', done=False, new_plan_exists=False, max_turns_reached=True) == 'END'
        assert pick_transition(transitions, step_path='workflow.w.steps.s', done=False, new_plan_exists=False, max_turns_reached=False) == 'step2'

    def test_ordered_transitions_unconditional_fallback(self) -> None:
        transitions = (GoTransition(to='END', when='DONE'), GoTransition(to='step2'))
        assert pick_transition(transitions, step_path='workflow.w.steps.s', done=False, new_plan_exists=False, max_turns_reached=False) == 'step2'
        assert pick_transition(transitions, step_path='workflow.w.steps.s', done=True, new_plan_exists=False, max_turns_reached=False) == 'END'

    def test_pick_transition_no_match_raises(self) -> None:
        transitions = (GoTransition(to='END', when='DONE'), GoTransition(to='END', when='NEW_PLAN_EXISTS'))
        with pytest.raises(WorkflowError) as ctx:
            pick_transition(transitions, step_path='workflow.w.steps.s', done=False, new_plan_exists=False, max_turns_reached=False)
        assert 'no transition matched' in str(ctx.value)

    def test_resolve_profile_success(self) -> None:
        config = WorkflowUserConfig(harnesses={'opencode': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='m', effort='high')})})
        result = resolve_profile('opencode.default', config, step_path='workflow.w.steps.s')
        assert result.harness_name == 'opencode'
        assert result.profile_name == 'default'
        assert result.model == 'm'
        assert result.effort == 'high'

    def test_resolve_profile_unknown_harness_raises(self) -> None:
        config = WorkflowUserConfig()
        with pytest.raises(WorkflowError) as ctx:
            resolve_profile('unknown.default', config, step_path='workflow.w.steps.s')
        assert 'unknown harness' in str(ctx.value)

    def test_resolve_profile_unknown_profile_raises(self) -> None:
        config = WorkflowUserConfig(harnesses={'opencode': WorkflowHarnessConfig(profiles={})})
        with pytest.raises(WorkflowError) as ctx:
            resolve_profile('opencode.missing', config, step_path='workflow.w.steps.s')
        assert 'unknown profile' in str(ctx.value)

    def test_resolve_profile_bare_selector_raises(self) -> None:
        config = WorkflowUserConfig()
        with pytest.raises(WorkflowError) as ctx:
            resolve_profile('opencode', config, step_path='workflow.w.steps.s')
        assert 'fully qualified' in str(ctx.value)

    def test_workflow_ends_only_via_end_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('implementation_prompt',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='implement_plan')))}, first_step='implement_plan')}, prompts={'implementation_prompt': 'Work from {ACTIVE_PLAN_PATH}.'})
            call_count = 0

            def runner(argv, **kwargs):
                nonlocal call_count
                call_count += 1
                _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert result.turns_completed == 1
            assert result.final_snapshot.is_complete
            assert call_count == 1

    def test_workflow_loops_implementer_steps_without_stagnation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n- [ ] step two\n')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('implementation_prompt',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='implement_plan')))}, first_step='implement_plan')}, prompts={'implementation_prompt': 'Work from {ACTIVE_PLAN_PATH}.'})
            call_count = 0

            def runner(argv, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [x] step one\n- [ ] step two\n')
                elif call_count == 2:
                    _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n- [x] step two\n')
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert result.turns_completed == 2
            assert result.final_snapshot.is_complete
            assert call_count == 2

    def test_active_plan_updates_only_when_generated_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'loop': WorkflowConfig(steps={'review': WorkflowStepConfig(profile='codex.default', prompts=('review_prompt',), go=(GoTransition(to='implement'),)), 'implement': WorkflowStepConfig(profile='codex.default', prompts=('impl_prompt',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='review')))}, first_step='review')}, prompts={'review_prompt': 'Review. New plan: {NEW_PLAN_PATH}. Active: {ACTIVE_PLAN_PATH}.', 'impl_prompt': 'Implement. New plan: {NEW_PLAN_PATH}. Active: {ACTIVE_PLAN_PATH}.'})
            turn_number = [0]

            def capturing_runner(argv, **kwargs):
                turn_number[0] += 1
                if turn_number[0] == 1:
                    _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            run_workflow(controller_config, wf_config, 'loop', config_dir=config_dir, adapter=CodexAdapter(), runner=capturing_runner)

    def test_active_plan_remains_unchanged_when_review_does_not_create_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n- [ ] step two\n')
            captured_active_paths: list[str] = []

            def capturing_runner(argv, **kwargs):
                prompt_text = ' '.join(argv)
                import re
                match = re.search('Active: (\\S+)', prompt_text)
                if match:
                    captured = match.group(1).rstrip('.')
                    captured_active_paths.append(captured)
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'loop': WorkflowConfig(steps={'review': WorkflowStepConfig(profile='codex.default', prompts=('review_prompt',), go=(GoTransition(to='implement'),)), 'implement': WorkflowStepConfig(profile='codex.default', prompts=('impl_prompt',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='review')))}, first_step='review')}, prompts={'review_prompt': 'Active: {ACTIVE_PLAN_PATH}. New: {NEW_PLAN_PATH}.', 'impl_prompt': 'Active: {ACTIVE_PLAN_PATH}. New: {NEW_PLAN_PATH}.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=4)
            run_workflow(controller_config, wf_config, 'loop', config_dir=config_dir, adapter=CodexAdapter(), runner=capturing_runner)
            for p in captured_active_paths:
                assert str(plan_path) == p

    def test_active_plan_updates_when_generated_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            captured_active_paths: list[str] = []
            turn_counter = [0]

            def capturing_runner(argv, **kwargs):
                turn_counter[0] += 1
                prompt_text = ' '.join(argv)
                import re as re_mod
                match = re_mod.search('Active: (\\S+)', prompt_text)
                if match:
                    captured_active_paths.append(match.group(1).rstrip('.'))
                if turn_counter[0] == 1:
                    new_path = repo_root / 'plan-cp01-v01.md'
                    new_path.write_text('# Generated plan', encoding='utf-8')
                    _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'loop': WorkflowConfig(steps={'review': WorkflowStepConfig(profile='codex.default', prompts=('review_prompt',), go=(GoTransition(to='implement'),)), 'implement': WorkflowStepConfig(profile='codex.default', prompts=('impl_prompt',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='review')))}, first_step='review')}, prompts={'review_prompt': 'Active: {ACTIVE_PLAN_PATH}. New: {NEW_PLAN_PATH}.', 'impl_prompt': 'Active: {ACTIVE_PLAN_PATH}.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            run_workflow(controller_config, wf_config, 'loop', config_dir=config_dir, adapter=CodexAdapter(), runner=capturing_runner)
            assert len(captured_active_paths) == 2
            assert captured_active_paths[0] == str(plan_path)
            expected_new = str(repo_root / 'plan-cp01-v01.md')
            assert captured_active_paths[1] == expected_new

    def test_workflow_multistep_review_and_implement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            call_order: list[str] = []

            def capturing_runner(argv, **kwargs):
                call_order.append(argv[0])
                _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            wf_config = WorkflowUserConfig(harnesses={'claude': WorkflowHarnessConfig(profiles={'opus': HarnessProfileConfig(model='claude-opus-4')}), 'opencode': WorkflowHarnessConfig(profiles={'turbo': HarnessProfileConfig(model='glm-5-turbo')})}, workflows={'review_loop': WorkflowConfig(steps={'review_plan': WorkflowStepConfig(profile='claude.opus', prompts=('review_prompt',), go=(GoTransition(to='implement_plan'),)), 'implement_plan': WorkflowStepConfig(profile='opencode.turbo', prompts=('impl_prompt',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='review_plan')))}, first_step='review_plan')}, prompts={'review_prompt': 'Review the plan.', 'impl_prompt': 'Implement from {ACTIVE_PLAN_PATH}.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            result = run_workflow(controller_config, wf_config, 'review_loop', config_dir=config_dir, runner=capturing_runner)
            assert result.turns_completed == 2
            assert result.final_snapshot.is_complete
            assert call_order == ['claude', 'opencode']

    def test_workflow_max_turns_routing_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout='noop', stderr='')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='implement_plan')))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=3)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert result.turns_completed == 3
            assert not result.final_snapshot.is_complete

    def test_workflow_no_matching_transition_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='DONE'),))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=3)
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert 'no transition matched' in str(ctx.value)
            assert 'workflow.simple.steps.implement_plan' in str(ctx.value)
            assert 'DONE=False' in str(ctx.value)

    def test_workflow_no_matching_transition_writes_failed_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n- [ ] step two\n')
            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [x] step one\n- [ ] step two\n')
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'loop': WorkflowConfig(steps={'review': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='implement'),)), 'implement': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='DONE'),))}, first_step='review')}, prompts={'p': 'Work.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(controller_config, wf_config, 'loop', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert 'workflow.loop.steps.implement' in str(ctx.value)
            run_dir = ctx.value.run_dir
            assert run_dir is not None
            assert run_dir is not None
            run_json = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
            assert run_json['status'] == 'failed'
            assert run_json['failure_reason'] in str(ctx.value)
            assert run_json['turns_completed'] == 2
            assert run_json['last_snapshot']['current_checkpoint_name'] == 'Checkpoint 1: First'

    def test_workflow_done_reflects_original_plan_not_fix_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            fix_plan = repo_root / 'plan-cp01-v01.md'
            _write_plan(fix_plan, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
            turn_counter = [0]
            ended_at_turn = [0]

            def runner(argv, **kwargs):
                turn_counter[0] += 1
                ended_at_turn[0] = turn_counter[0]
                return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='DONE'), GoTransition(to='implement_plan')))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            with pytest.raises(WorkflowError):
                run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert ended_at_turn[0] == 5

    def test_workflow_missing_workflow_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=1)
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(controller_config, WorkflowUserConfig(), 'nonexistent', config_dir=repo_root)
            assert 'not found' in str(ctx.value)

    def test_workflow_extra_instructions_appended(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            captured_user_prompts: list[str] = []

            class CapturingAdapter:
                name = 'codex'
                supports_effort = False

                def build_invocation(self, *, repo_root, model, system_prompt, user_prompt, effort=None):
                    captured_user_prompts.append(user_prompt)
                    return HarnessInvocation(label='codex', argv=('codex', 'run', user_prompt), env={}, prompt_mode='prefix-system-into-user-prompt', system_prompt=system_prompt, user_prompt=user_prompt, effective_prompt=f'{system_prompt}\n\n{user_prompt}' if system_prompt else user_prompt)
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='implement_plan')))}, first_step='implement_plan')}, prompts={'p': 'Work from {ACTIVE_PLAN_PATH}.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=1, extra_instructions=('be careful', 'use tests'))
            run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CapturingAdapter(), runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, '', ''))
            assert len(captured_user_prompts) == 1
            assert 'Work from' in captured_user_prompts[0]
            assert 'be careful use tests' in captured_user_prompts[0]

    def test_workflow_harness_failure_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 1, stdout='bad', stderr='err')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='DONE'), GoTransition(to='implement_plan')))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=3)
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert 'exited with code 1' in str(ctx.value)

    def test_workflow_already_complete_returns_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                return subprocess.CompletedProcess(argv, 0, 'ok', '')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END'),))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=3)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert result.turns_completed == 0
            assert result.final_snapshot.is_complete
            assert result.end_reason == 'already_complete'
            assert result.to_dict()['end_reason'] == 'already_complete'
            assert call_count[0] == 0

    def test_workflow_unconditional_end_uses_transition_end_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, 'ok', '')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END'),))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=3)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert result.turns_completed == 1
            assert result.end_reason == 'transition_end'
            run_json = json.loads((result.run_dir / 'run.json').read_text(encoding='utf-8'))
            assert run_json['end_reason'] == 'transition_end'
            turn_result = json.loads((result.run_dir / 'turns' / 'turn-001' / 'result.json').read_text(encoding='utf-8'))
            assert turn_result['end_reason'] == 'transition_end'
            assert turn_result['status'] == 'running'

    def test_workflow_end_reason_prefers_done_when_plan_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            completed_plan_path = repo_root / 'completed.md'
            new_plan_path = repo_root / 'plan-cp01-v01.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            _write_plan(completed_plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')

            def runner(argv, **kwargs):
                shutil.copyfile(completed_plan_path, plan_path)
                new_plan_path.write_text('# Generated\n', encoding='utf-8')
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='NEW_PLAN_EXISTS'),))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=3)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert result.turns_completed == 1
            assert result.end_reason == 'done'
            run_json = json.loads((result.run_dir / 'run.json').read_text(encoding='utf-8'))
            assert run_json['end_reason'] == 'done'
            turn_result = json.loads((result.run_dir / 'turns' / 'turn-001' / 'result.json').read_text(encoding='utf-8'))
            assert turn_result['end_reason'] == 'done'
            assert turn_result['status'] == 'completed'

    def test_workflow_completes_when_all_checkpoints_done_despite_unchecked_final_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            initial_plan = (
                '# Plan\n\n'
                '### [x] Checkpoint 1: Setup\n- [x] step a\n\n'
                '### [x] Checkpoint 2: Core\n- [x] step b\n\n'
                '### [x] Checkpoint 3: Tests\n- [x] step c\n\n'
                '### [ ] Checkpoint 4: Cleanup\n'
                '- [ ] cleanup step one\n'
                '- [ ] cleanup step two\n'
                '- [ ] cleanup step three\n'
                '- [ ] cleanup step four\n'
                '- [ ] cleanup step five\n'
                '- [ ] cleanup step six\n'
                '- [ ] cleanup step seven\n'
                '- [ ] cleanup step eight\n\n'
                '## Final Checklist\n'
                '- [ ] final item one\n'
                '- [ ] final item two\n'
                '- [ ] final item three\n'
                '- [ ] final item four\n'
                '- [ ] final item five\n'
                '- [ ] final item six\n'
                '- [ ] final item seven\n'
            )
            completed_plan = (
                '# Plan\n\n'
                '### [x] Checkpoint 1: Setup\n- [x] step a\n\n'
                '### [x] Checkpoint 2: Core\n- [x] step b\n\n'
                '### [x] Checkpoint 3: Tests\n- [x] step c\n\n'
                '### [x] Checkpoint 4: Cleanup\n'
                '- [x] cleanup step one\n'
                '- [x] cleanup step two\n'
                '- [x] cleanup step three\n'
                '- [x] cleanup step four\n'
                '- [x] cleanup step five\n'
                '- [x] cleanup step six\n'
                '- [x] cleanup step seven\n'
                '- [x] cleanup step eight\n\n'
                '## Final Checklist\n'
                '- [ ] final item one\n'
                '- [ ] final item two\n'
                '- [ ] final item three\n'
                '- [ ] final item four\n'
                '- [ ] final item five\n'
                '- [ ] final item six\n'
                '- [ ] final item seven\n'
            )
            _write_plan(plan_path, initial_plan)
            wf_config = WorkflowUserConfig(
                harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})},
                workflows={'simple': WorkflowConfig(
                    steps={'implement_plan': WorkflowStepConfig(
                        profile='codex.default',
                        prompts=('p',),
                        go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='implement_plan')),
                    )},
                    first_step='implement_plan',
                )},
                prompts={'p': 'Work.'},
            )

            def runner(argv, **kwargs):
                _write_plan(plan_path, completed_plan)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            assert result.end_reason == 'done'
            assert result.final_snapshot.is_complete

    def test_workflow_invalid_plan_failure_reports_parse_error_counts_not_stale_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            initial_plan = (
                '# Plan\n\n'
                '### [x] Checkpoint 1: Done\n- [x] step a\n\n'
                '### [ ] Checkpoint 2: Current\n'
                '- [ ] real step one\n'
                '- [ ] real step two\n'
                '- [ ] real step three\n'
                '- [ ] real step four\n'
                '- [ ] real step five\n'
                '- [ ] real step six\n'
                '- [ ] real step seven\n'
                '- [ ] real step eight\n'
                '- [ ] real step nine\n'
                '- [ ] real step ten\n'
                '- [ ] real step eleven\n'
                '- [ ] real step twelve\n'
                '- [ ] real step thirteen\n'
                '- [ ] real step fourteen\n'
                '- [ ] real step fifteen\n'
            )
            broken_plan = (
                '# Plan\n\n'
                '### [x] Checkpoint 1: Done\n- [x] step a\n\n'
                '### [x] Checkpoint 2: Current\n'
                '- [x] real step one\n'
                '- [ ] real step two\n'
                '- [ ] real step three\n'
            )
            _write_plan(plan_path, initial_plan)
            wf_config = WorkflowUserConfig(
                harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})},
                workflows={'simple': WorkflowConfig(
                    steps={'implement_plan': WorkflowStepConfig(
                        profile='codex.default',
                        prompts=('p',),
                        go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='implement_plan')),
                    )},
                    first_step='implement_plan',
                )},
                prompts={'p': 'Work.'},
            )

            def runner(argv, **kwargs):
                _write_plan(plan_path, broken_plan)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(controller_config, wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            error_msg = str(ctx.value)
            assert 'Checkpoint 2: Current' in error_msg
            assert 'current checkpoint unchecked step count: 2' in error_msg
            assert 'current checkpoint unchecked step count: 15' not in error_msg
            run_dir = ctx.value.run_dir
            assert run_dir is not None
            run_json = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
            assert 'current checkpoint unchecked step count: 2' in run_json['failure_reason']
            assert 'current checkpoint unchecked step count: 15' not in run_json['failure_reason']

class WorkflowArtifactTests(unittest.TestCase):

    def test_run_json_includes_workflow_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END'),))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            result = run_workflow(ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=3), wf_config, 'simple', config_dir=config_dir)
            run_dir = result.run_dir
            run_json = json.loads((run_dir / 'run.json').read_text(encoding='utf-8'))
            assert run_json['workflow_name'] == 'simple'
            assert run_json['original_plan_path'] == str(plan_path)
            assert run_json['status'] == 'completed'
            assert run_json['end_reason'] == 'already_complete'

    def test_turn_artifacts_include_workflow_step_and_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')

            def runner(argv, **kwargs):
                _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
                return subprocess.CompletedProcess(argv, 0, 'ok', '')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='implement_plan')))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            result = run_workflow(ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5), wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            turn_dir = result.run_dir / 'turns' / 'turn-001'
            result_json = json.loads((turn_dir / 'result.json').read_text(encoding='utf-8'))
            assert result_json['step_name'] == 'implement_plan'
            assert result_json['selector'] == 'codex.default'
            assert result_json['conditions']['DONE'] == True
            assert result_json['conditions']['NEW_PLAN_EXISTS'] == False
            assert result_json['chosen_transition'] == 'END'
            assert result_json['end_reason'] == 'done'

    def test_turn_artifacts_include_plan_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n- [ ] step two\n')

            def runner(argv, **kwargs):
                _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n- [x] step two\n')
                return subprocess.CompletedProcess(argv, 0, 'ok', '')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='implement_plan')))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            result = run_workflow(ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5), wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            turn_dir = result.run_dir / 'turns' / 'turn-001'
            result_json = json.loads((turn_dir / 'result.json').read_text(encoding='utf-8'))
            assert result_json['original_plan_path'] == str(plan_path)
            assert 'active_plan_path' in result_json
            assert 'new_plan_path' in result_json

    def test_turn_directory_exists_before_harness_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            wf_config = WorkflowUserConfig(
                harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})},
                workflows={'simple': WorkflowConfig(
                    steps={'implement_plan': WorkflowStepConfig(
                        profile='codex.default',
                        prompts=('p',),
                        go=(GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'), GoTransition(to='implement_plan')),
                    )},
                    first_step='implement_plan',
                )},
                prompts={'p': 'Work.'},
            )

            def runner(argv, **kwargs):
                runs_root = repo_root / '.aflow' / 'runs'
                run_dirs = sorted(runs_root.iterdir())
                assert len(run_dirs) == 1
                turn_dir = run_dirs[0] / 'turns' / 'turn-001'
                assert turn_dir.is_dir()
                for filename in ('system-prompt.txt', 'user-prompt.txt', 'effective-prompt.txt', 'argv.json', 'env.json', 'result.json'):
                    assert (turn_dir / filename).exists()
                start_result = json.loads((turn_dir / 'result.json').read_text(encoding='utf-8'))
                assert start_result['status'] == 'starting'
                assert start_result['snapshot_after'] is None
                assert 'stdout' not in start_result
                _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            result = run_workflow(
                ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5),
                wf_config,
                'simple',
                config_dir=config_dir,
                adapter=CodexAdapter(),
                runner=runner,
            )
            assert result.turns_completed == 1
            turn_dir = result.run_dir / 'turns' / 'turn-001'
            final_result = json.loads((turn_dir / 'result.json').read_text(encoding='utf-8'))
            assert final_result['status'] == 'completed'
            assert final_result['returncode'] == 0
            assert final_result['stdout'] == 'ok'
            assert final_result['stderr'] == ''
            assert (turn_dir / 'stdout.txt').read_text(encoding='utf-8') == 'ok'
            assert (turn_dir / 'stderr.txt').read_text(encoding='utf-8') == ''

    def test_turn_artifacts_finalize_on_harness_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            wf_config = WorkflowUserConfig(
                harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})},
                workflows={'simple': WorkflowConfig(
                    steps={'implement_plan': WorkflowStepConfig(
                        profile='codex.default',
                        prompts=('p',),
                        go=(GoTransition(to='END', when='DONE'), GoTransition(to='implement_plan')),
                    )},
                    first_step='implement_plan',
                )},
                prompts={'p': 'Work.'},
            )

            def runner(argv, **kwargs):
                runs_root = repo_root / '.aflow' / 'runs'
                run_dirs = sorted(runs_root.iterdir())
                assert len(run_dirs) == 1
                turn_dir = run_dirs[0] / 'turns' / 'turn-001'
                assert turn_dir.is_dir()
                return subprocess.CompletedProcess(argv, 1, 'bad', 'err')

            with pytest.raises(WorkflowError) as ctx:
                run_workflow(
                    ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5),
                    wf_config,
                    'simple',
                    config_dir=config_dir,
                    adapter=CodexAdapter(),
                    runner=runner,
                )
            turn_dir = ctx.value.run_dir / 'turns' / 'turn-001'
            result_json = json.loads((turn_dir / 'result.json').read_text(encoding='utf-8'))
            assert result_json['status'] == 'harness-failed'
            assert result_json['returncode'] == 1
            assert result_json['stdout'] == 'bad'
            assert result_json['stderr'] == 'err'
            assert (turn_dir / 'stdout.txt').read_text(encoding='utf-8') == 'bad'
            assert (turn_dir / 'stderr.txt').read_text(encoding='utf-8') == 'err'

    def test_run_json_records_workflow_step_on_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, 'noop', '')
            wf_config = WorkflowUserConfig(harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})}, workflows={'simple': WorkflowConfig(steps={'implement_plan': WorkflowStepConfig(profile='codex.default', prompts=('p',), go=(GoTransition(to='END', when='DONE'), GoTransition(to='implement_plan')))}, first_step='implement_plan')}, prompts={'p': 'Work.'})
            with pytest.raises(WorkflowError):
                run_workflow(ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=2), wf_config, 'simple', config_dir=config_dir, adapter=CodexAdapter(), runner=runner)
            run_dir = repo_root / '.aflow' / 'runs'
            run_dirs = sorted(run_dir.iterdir())
            assert len(run_dirs) == 1
            run_json = json.loads((run_dirs[0] / 'run.json').read_text(encoding='utf-8'))
            assert run_json['workflow_name'] == 'simple'
            assert run_json['current_step_name'] == 'implement_plan'

def _copy_aflow_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / 'repo'
    repo_root.mkdir()
    aflow_src = Path(__file__).resolve().parents[1] / 'aflow'
    aflow_dst = repo_root / 'aflow'
    shutil.copytree(aflow_src, aflow_dst, ignore=shutil.ignore_patterns('__pycache__', 'tests'))
    return repo_root

def _write_workflow_harness_script(repo_root: Path, harness_name: str) -> Path:
    bin_dir = repo_root / 'bin'
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / harness_name
    script.write_text(textwrap.dedent('            #!/usr/bin/env python3\n            from __future__ import annotations\n            import os, shutil, sys\n            from pathlib import Path\n\n            plan_path = Path(os.environ["AFLOW_TEST_PLAN_PATH"])\n            scenario = os.environ.get("AFLOW_TEST_SCENARIO", "noop")\n            count_file = Path(os.environ["AFLOW_TEST_COUNT_FILE"])\n            count = int(count_file.read_text(encoding="utf-8")) + 1 if count_file.exists() else 1\n            count_file.write_text(str(count), encoding="utf-8")\n\n            print(f"{harness_name} turn {count}")\n\n            if scenario == "complete":\n                shutil.copyfile(os.environ["AFLOW_TEST_COMPLETED_PLAN"], plan_path)\n                sys.exit(0)\n\n            if scenario == "noop":\n                sys.exit(0)\n\n            if scenario == "create_plan":\n                new_plan = os.environ.get("AFLOW_TEST_NEW_PLAN_PATH", "")\n                if new_plan:\n                    Path(new_plan).write_text("# Generated\\n", encoding="utf-8")\n                shutil.copyfile(os.environ["AFLOW_TEST_COMPLETED_PLAN"], plan_path)\n                sys.exit(0)\n\n            if scenario == "fail":\n                print(f"{harness_name} failing", file=sys.stderr)\n                sys.exit(int(os.environ.get("AFLOW_TEST_EXIT_CODE", "1")))\n\n            raise SystemExit(f"unknown AFLOW_TEST_SCENARIO {scenario}")\n            ').replace('{harness_name}', harness_name), encoding='utf-8')
    script.chmod(493)
    return script

def _workflow_test_env(repo_root: Path, *, scenario: str, plan_path: Path, count_file: Path, home_dir: Path | None=None, completed_plan_path: Path | None=None, new_plan_path: Path | None=None, exit_code: int | None=None) -> dict[str, str]:
    env = os.environ.copy()
    env['PATH'] = f"{repo_root / 'bin'}:{env['PATH']}"
    if home_dir is not None:
        env['HOME'] = str(home_dir.resolve())
    env['AFLOW_TEST_SCENARIO'] = scenario
    env['AFLOW_TEST_PLAN_PATH'] = str(plan_path.resolve())
    env['AFLOW_TEST_COUNT_FILE'] = str(count_file.resolve())
    if completed_plan_path is not None:
        env['AFLOW_TEST_COMPLETED_PLAN'] = str(completed_plan_path.resolve())
    if new_plan_path is not None:
        env['AFLOW_TEST_NEW_PLAN_PATH'] = str(new_plan_path.resolve())
    if exit_code is not None:
        env['AFLOW_TEST_EXIT_CODE'] = str(exit_code)
    return env

def _run_workflow_launcher(repo_root: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, '-m', 'aflow', 'run', *args], cwd=repo_root, env=env, capture_output=True, text=True, check=False)

class WorkflowEndToEndTests(unittest.TestCase):

    def test_already_complete_workflow_reports_success_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / 'home'
            home_dir.mkdir()
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[harness.codex.profiles.default]\nmodel = "gpt-5.4"\n\n[workflow.simple.steps.implement_plan]\nprofile = "codex.default"\nprompts = ["p"]\ngo = [{ to = "END", when = "DONE || MAX_TURNS_REACHED" }]\n\n[prompts]\np = "Work."\n')
            plan_path = tmp_path / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
            count_file = tmp_path / 'count.txt'
            result = _run_workflow_launcher(repo_root, str(plan_path), env=_workflow_test_env(repo_root, scenario='noop', plan_path=plan_path, count_file=count_file, home_dir=home_dir))
            assert result.returncode == 0
            assert result.stdout.strip() == "Workflow 'simple' completed after 0 turns because the original plan was already complete."
            assert not count_file.exists()
            run_dirs = sorted((repo_root / '.aflow' / 'runs').iterdir())
            assert len(run_dirs) == 1
            run_json = json.loads((run_dirs[0] / 'run.json').read_text(encoding='utf-8'))
            assert run_json['end_reason'] == 'already_complete'
            assert run_json['turns_completed'] == 0

    def test_simple_workflow_completion_on_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / 'home'
            home_dir.mkdir()
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[harness.codex.profiles.default]\nmodel = "gpt-5.4"\n\n[workflow.simple.steps.implement_plan]\nprofile = "codex.default"\nprompts = ["p"]\ngo = [\n  { to = "END", when = "DONE || MAX_TURNS_REACHED" },\n  { to = "implement_plan" },\n]\n\n[prompts]\np = "Work from {ACTIVE_PLAN_PATH}."\n')
            plan_path = tmp_path / 'plan.md'
            completed_plan_path = tmp_path / 'completed.md'
            count_file = tmp_path / 'count.txt'
            original_plan_text = '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n'
            _write_plan(plan_path, original_plan_text)
            _write_plan(completed_plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
            _write_workflow_harness_script(repo_root, 'codex')
            result = _run_workflow_launcher(repo_root, '--max-turns', '1', str(plan_path), env=_workflow_test_env(repo_root, scenario='complete', plan_path=plan_path, count_file=count_file, home_dir=home_dir, completed_plan_path=completed_plan_path))
            assert result.returncode == 0
            assert result.stdout.strip() == "Workflow 'simple' completed after 1 turn because DONE evaluated true."
            backup_path = repo_root / 'plans' / 'backups' / 'plan.md'
            assert backup_path.exists()
            assert backup_path.read_text(encoding='utf-8') == original_plan_text
            run_dirs = sorted((repo_root / '.aflow' / 'runs').iterdir())
            assert len(run_dirs) == 1
            run_json = json.loads((run_dirs[0] / 'run.json').read_text(encoding='utf-8'))
            assert run_json['status'] == 'completed'
            assert run_json['workflow_name'] == 'simple'
            assert run_json['turns_completed'] == 1
            assert run_json['end_reason'] == 'done'
            turn_result = json.loads((run_dirs[0] / 'turns' / 'turn-001' / 'result.json').read_text(encoding='utf-8'))
            assert turn_result['end_reason'] == 'done'

    def test_kiro_workflow_invokes_chat_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / 'home'
            home_dir.mkdir()
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[harness.kiro.profiles.default]\nmodel = "kiro-model"\n\n[workflow.simple.steps.implement_plan]\nprofile = "kiro.default"\nprompts = ["p"]\ngo = [\n  { to = "END", when = "DONE || MAX_TURNS_REACHED" },\n  { to = "implement_plan" },\n]\n\n[prompts]\np = "Work from {ACTIVE_PLAN_PATH}."\n')
            plan_path = tmp_path / 'plan.md'
            completed_plan_path = tmp_path / 'completed.md'
            count_file = tmp_path / 'count.txt'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            _write_plan(completed_plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
            _write_workflow_harness_script(repo_root, 'kiro-cli')
            result = _run_workflow_launcher(repo_root, str(plan_path), env=_workflow_test_env(repo_root, scenario='complete', plan_path=plan_path, count_file=count_file, home_dir=home_dir, completed_plan_path=completed_plan_path))
            assert result.returncode == 0
            run_dirs = sorted((repo_root / '.aflow' / 'runs').iterdir())
            assert len(run_dirs) == 1
            run_json = json.loads((run_dirs[0] / 'run.json').read_text(encoding='utf-8'))
            assert run_json['status'] == 'completed'
            assert run_json['turns_completed'] == 1
            assert run_json['end_reason'] == 'done'
            turn_dir = run_dirs[0] / 'turns' / 'turn-001'
            turn_result = json.loads((turn_dir / 'result.json').read_text(encoding='utf-8'))
            assert turn_result['selector'] == 'kiro.default'
            assert turn_result['end_reason'] == 'done'
            argv_json = json.loads((turn_dir / 'argv.json').read_text(encoding='utf-8'))
            assert argv_json['argv'][:4] == ['kiro-cli', 'chat', '--no-interactive', '--trust-all-tools']

    def test_reviewer_created_plan_becomes_active_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / 'home'
            home_dir.mkdir()
            _write_config(home_dir, '[aflow]\ndefault_workflow = "loop"\n\n[harness.codex.profiles.default]\nmodel = "gpt-5.4"\n\n[workflow.loop.steps.review]\nprofile = "codex.default"\nprompts = ["review_p"]\ngo = [{ to = "implement" }]\n\n[workflow.loop.steps.implement]\nprofile = "codex.default"\nprompts = ["impl_p"]\ngo = [\n  { to = "END", when = "DONE || MAX_TURNS_REACHED" },\n  { to = "review" },\n]\n\n[prompts]\nreview_p = "Active: {ACTIVE_PLAN_PATH}. New: {NEW_PLAN_PATH}."\nimpl_p = "Active: {ACTIVE_PLAN_PATH}."\n')
            plan_path = tmp_path / 'plan.md'
            completed_plan_path = tmp_path / 'completed.md'
            count_file = tmp_path / 'count.txt'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            _write_plan(completed_plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
            _write_workflow_harness_script(repo_root, 'codex')
            call_count = [0]

            def count_env():
                nonlocal call_count
                call_count[0] += 1
                new_plan = plan_path.parent / 'plan-cp01-v01.md'
                scenario = 'create_plan' if call_count[0] == 1 else 'complete'
                return _workflow_test_env(repo_root, scenario=scenario, plan_path=plan_path, count_file=count_file, home_dir=home_dir, completed_plan_path=completed_plan_path, new_plan_path=new_plan if call_count[0] == 1 else None)
            result = _run_workflow_launcher(repo_root, '--max-turns', '5', str(plan_path), env=count_env())
            assert result.returncode == 0
            run_dirs = sorted((repo_root / '.aflow' / 'runs').iterdir())
            assert len(run_dirs) == 1
            run_json = json.loads((run_dirs[0] / 'run.json').read_text(encoding='utf-8'))
            assert run_json['status'] == 'completed'
            assert run_json['turns_completed'] == 2
            assert run_json['end_reason'] == 'done'
            turn2_result = json.loads((run_dirs[0] / 'turns' / 'turn-002' / 'result.json').read_text(encoding='utf-8'))
            assert Path(turn2_result['active_plan_path']).resolve() == (plan_path.parent / 'plan-cp01-v01.md').resolve()
            assert turn2_result['end_reason'] == 'done'

    def test_reviewer_without_generated_plan_keeps_active_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / 'home'
            home_dir.mkdir()
            _write_config(home_dir, '[aflow]\ndefault_workflow = "loop"\n\n[harness.codex.profiles.default]\nmodel = "gpt-5.4"\n\n[workflow.loop.steps.review]\nprofile = "codex.default"\nprompts = ["review_p"]\ngo = [{ to = "implement" }]\n\n[workflow.loop.steps.implement]\nprofile = "codex.default"\nprompts = ["impl_p"]\ngo = [\n  { to = "END", when = "DONE || MAX_TURNS_REACHED" },\n  { to = "review" },\n]\n\n[prompts]\nreview_p = "Active: {ACTIVE_PLAN_PATH}."\nimpl_p = "Active: {ACTIVE_PLAN_PATH}."\n')
            plan_path = tmp_path / 'plan.md'
            completed_plan_path = tmp_path / 'completed.md'
            count_file = tmp_path / 'count.txt'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n- [ ] step two\n')
            _write_plan(completed_plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n- [x] step two\n')
            _write_workflow_harness_script(repo_root, 'codex')
            result = _run_workflow_launcher(repo_root, '--max-turns', '4', str(plan_path), env=_workflow_test_env(repo_root, scenario='noop', plan_path=plan_path, count_file=count_file, home_dir=home_dir, completed_plan_path=completed_plan_path))
            assert result.returncode == 0
            run_dirs = sorted((repo_root / '.aflow' / 'runs').iterdir())
            run_json = json.loads((run_dirs[0] / 'run.json').read_text(encoding='utf-8'))
            assert run_json['status'] == 'completed'
            assert run_json['turns_completed'] == 4
            assert run_json['end_reason'] == 'max_turns_reached'
            for turn_dir in sorted((run_dirs[0] / 'turns').iterdir()):
                turn_result = json.loads((turn_dir / 'result.json').read_text(encoding='utf-8'))
                assert Path(turn_result['active_plan_path']).resolve() == plan_path.resolve()
            turn_result = json.loads((run_dirs[0] / 'turns' / 'turn-004' / 'result.json').read_text(encoding='utf-8'))
            assert turn_result['end_reason'] == 'max_turns_reached'

    def test_max_turns_routes_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / 'home'
            home_dir.mkdir()
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[harness.codex.profiles.default]\nmodel = "gpt-5.4"\n\n[workflow.simple.steps.implement_plan]\nprofile = "codex.default"\nprompts = ["p"]\ngo = [\n  { to = "END", when = "DONE || MAX_TURNS_REACHED" },\n  { to = "implement_plan" },\n]\n\n[prompts]\np = "Work."\n')
            plan_path = tmp_path / 'plan.md'
            count_file = tmp_path / 'count.txt'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            _write_workflow_harness_script(repo_root, 'codex')
            result = _run_workflow_launcher(repo_root, '--max-turns', '3', str(plan_path), env=_workflow_test_env(repo_root, scenario='noop', plan_path=plan_path, count_file=count_file, home_dir=home_dir))
            assert result.returncode == 0
            run_dirs = sorted((repo_root / '.aflow' / 'runs').iterdir())
            assert len(run_dirs) == 1
            run_json = json.loads((run_dirs[0] / 'run.json').read_text(encoding='utf-8'))
            assert run_json['status'] == 'completed'
            assert run_json['turns_completed'] == 3
            assert run_json['end_reason'] == 'max_turns_reached'
            assert result.stdout.strip() == "Workflow 'simple' completed after 3 turns because MAX_TURNS_REACHED matched."
            turn_result = json.loads((run_dirs[0] / 'turns' / 'turn-003' / 'result.json').read_text(encoding='utf-8'))
            assert turn_result['end_reason'] == 'max_turns_reached'
            assert turn_result['status'] == 'running'

class SkillDocsTests(unittest.TestCase):

    def test_skill_files_do_not_contain_workflow_placeholders(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        placeholders = ('{ORIGINAL_PLAN_PATH}', '{ACTIVE_PLAN_PATH}', '{NEW_PLAN_PATH}')
        for skill_name in ('aflow-plan', 'aflow-execute-plan', 'aflow-execute-checkpoint', 'aflow-review-squash', 'aflow-review-checkpoint', 'aflow-review-final'):
            skill_path = repo_root / 'aflow' / 'bundled_skills' / skill_name / 'SKILL.md'
            text = skill_path.read_text(encoding='utf-8')
            for placeholder in placeholders:
                assert placeholder not in text

    def test_bundled_prompt_skill_names_match_shipped_skills(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        config = load_workflow_config(repo_root / 'aflow' / 'aflow.toml')
        expected = {
            'simple_implementation': 'aflow-execute-plan',
            'cp_loop_implementation': 'aflow-execute-checkpoint',
            'followup_implementation': 'aflow-execute-plan',
            'review_squash': 'aflow-review-squash',
            'review_cp': 'aflow-review-checkpoint',
            'final_review': 'aflow-review-final',
        }
        for prompt_name, skill_name in expected.items():
            prompt = config.prompts[prompt_name]
            assert f"'{skill_name}'" in prompt
            skill_path = repo_root / 'aflow' / 'bundled_skills' / skill_name / 'SKILL.md'
            assert skill_path.exists()

    def test_final_review_skill_is_distinct_and_no_squash(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / 'aflow' / 'bundled_skills' / 'aflow-review-final' / 'SKILL.md').read_text(encoding='utf-8')
        assert 'name: aflow-review-final' in text
        assert 'Do nothing.' not in text
        assert 'Do not squash' in text or 'Do not squash,' in text
        assert 'non-checkpoint' in text

    def test_example_plan_uses_review_squash_spelling(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        example_text = (repo_root / 'plans' / 'example.toml').read_text(encoding='utf-8')
        assert 'aflow-review-squash' in example_text
        assert 'aflow-review-checkpoint' in example_text
        assert 'aflow-execute-checkpoint' in example_text
        assert 'aflow-execute-plan' in example_text
        assert 'aflow-review-final' in example_text
        typo = '-'.join(('revive', 'squash'))
        assert typo not in example_text

    def test_review_checkpoint_skill_has_pre_handoff_selection_rule(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / 'aflow' / 'bundled_skills' / 'aflow-review-checkpoint' / 'SKILL.md').read_text(encoding='utf-8')
        assert 'Pre-Handoff Base HEAD' in text
        # The selection rule about searching plans/in-progress/ must be present
        assert 'plans/in-progress/' in text

    def test_bundled_config_review_implement_review_max_turns_transitions(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        config = load_workflow_config(repo_root / 'aflow' / 'aflow.toml')
        wf = config.workflows['review_implement_review']
        for step_name, step in wf.steps.items():
            assert step.go[0].to == 'END', f"step {step_name} first transition must be END"
            assert step.go[0].when == 'MAX_TURNS_REACHED', f"step {step_name} first transition must be MAX_TURNS_REACHED"

    def test_bundled_config_review_implement_cp_review_max_turns_transitions(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        config = load_workflow_config(repo_root / 'aflow' / 'aflow.toml')
        wf = config.workflows['review_implement_cp_review']
        for step_name, step in wf.steps.items():
            assert step.go[0].to == 'END', f"step {step_name} first transition must be END"
            assert step.go[0].when == 'MAX_TURNS_REACHED', f"step {step_name} first transition must be MAX_TURNS_REACHED"

    def test_bundled_config_ralph_includes_input_vars_prompt(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        config = load_workflow_config(repo_root / 'aflow' / 'aflow.toml')
        step = config.workflows['ralph'].steps['implement_plan']
        assert 'input_vars' in step.prompts

    def test_bundled_config_typos_are_fixed(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        text = (repo_root / 'aflow' / 'aflow.toml').read_text(encoding='utf-8')
        assert 'undispituble' not in text
        assert 'improvementns' not in text

    def test_example_plan_max_turns_transitions(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        import tomllib
        with open(repo_root / 'plans' / 'example.toml', 'rb') as f:
            raw = tomllib.load(f)
        for wf_name in ('my_wf1', 'checkpoint_loop'):
            for step_name, step_table in raw['workflow'][wf_name]['steps'].items():
                first_go = step_table['go'][0]
                assert first_go.get('when') == 'MAX_TURNS_REACHED', (
                    f"{wf_name}.{step_name} first transition must be MAX_TURNS_REACHED"
                )
                assert first_go['to'] == 'END'


class RepoRootTests(unittest.TestCase):

    def test_resolve_repo_root_outside_git_uses_cwd(self) -> None:
        import unittest.mock as mock
        from aflow.cli import _resolve_repo_root
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_cwd = Path(tmpdir)
            with mock.patch('subprocess.run') as mock_run, \
                 mock.patch('pathlib.Path.cwd', return_value=fake_cwd):
                mock_run.return_value = subprocess.CompletedProcess([], 1, stdout='', stderr='fatal: not a git repo\n')
                result = _resolve_repo_root()
                assert result == fake_cwd.resolve()

    def test_resolve_repo_root_cwd_equals_git_root_uses_cwd(self) -> None:
        import unittest.mock as mock
        from aflow.cli import _resolve_repo_root
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_cwd = Path(tmpdir).resolve()
            with mock.patch('subprocess.run') as mock_run, \
                 mock.patch('pathlib.Path.cwd', return_value=fake_cwd):
                mock_run.return_value = subprocess.CompletedProcess([], 0, stdout=str(fake_cwd) + '\n', stderr='')
                result = _resolve_repo_root()
                assert result == fake_cwd

    def test_resolve_repo_root_nested_no_tty_returns_none(self) -> None:
        import unittest.mock as mock
        from aflow.cli import _resolve_repo_root
        with tempfile.TemporaryDirectory() as tmpdir:
            git_root = Path(tmpdir) / 'repo'
            git_root.mkdir()
            subdir = git_root / 'subdir'
            subdir.mkdir()
            fake_cwd = subdir.resolve()
            git_root_resolved = git_root.resolve()
            with mock.patch('subprocess.run') as mock_run, \
                 mock.patch('pathlib.Path.cwd', return_value=fake_cwd), \
                 mock.patch('sys.stdin') as mock_stdin, \
                 mock.patch('sys.stdout') as mock_stdout:
                mock_run.return_value = subprocess.CompletedProcess([], 0, stdout=str(git_root_resolved) + '\n', stderr='')
                mock_stdin.isatty.return_value = False
                mock_stdout.isatty.return_value = False
                result = _resolve_repo_root()
                assert result is None

    def test_resolve_repo_root_nested_tty_accepts_git_root(self) -> None:
        import unittest.mock as mock
        from aflow.cli import _resolve_repo_root
        with tempfile.TemporaryDirectory() as tmpdir:
            git_root = Path(tmpdir) / 'repo'
            git_root.mkdir()
            subdir = git_root / 'subdir'
            subdir.mkdir()
            fake_cwd = subdir.resolve()
            git_root_resolved = git_root.resolve()
            with mock.patch('subprocess.run') as mock_run, \
                 mock.patch('pathlib.Path.cwd', return_value=fake_cwd), \
                 mock.patch('sys.stdin') as mock_stdin, \
                 mock.patch('sys.stdout') as mock_stdout, \
                 mock.patch('builtins.input', return_value='y'):
                mock_run.return_value = subprocess.CompletedProcess([], 0, stdout=str(git_root_resolved) + '\n', stderr='')
                mock_stdin.isatty.return_value = True
                mock_stdout.isatty.return_value = True
                result = _resolve_repo_root()
                assert result == git_root_resolved

    def test_resolve_repo_root_nested_tty_declines_uses_cwd(self) -> None:
        import unittest.mock as mock
        from aflow.cli import _resolve_repo_root
        with tempfile.TemporaryDirectory() as tmpdir:
            git_root = Path(tmpdir) / 'repo'
            git_root.mkdir()
            subdir = git_root / 'subdir'
            subdir.mkdir()
            fake_cwd = subdir.resolve()
            git_root_resolved = git_root.resolve()
            with mock.patch('subprocess.run') as mock_run, \
                 mock.patch('pathlib.Path.cwd', return_value=fake_cwd), \
                 mock.patch('sys.stdin') as mock_stdin, \
                 mock.patch('sys.stdout') as mock_stdout, \
                 mock.patch('builtins.input', return_value='n'):
                mock_run.return_value = subprocess.CompletedProcess([], 0, stdout=str(git_root_resolved) + '\n', stderr='')
                mock_stdin.isatty.return_value = True
                mock_stdout.isatty.return_value = True
                result = _resolve_repo_root()
                assert result == fake_cwd

    def test_nested_subdir_no_tty_run_exits_with_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / 'home'
            home_dir.mkdir()
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[harness.codex.profiles.default]\nmodel = "gpt-5.4"\n\n[workflow.simple.steps.implement_plan]\nprofile = "codex.default"\nprompts = ["p"]\ngo = [{ to = "END", when = "DONE || MAX_TURNS_REACHED" }]\n\n[prompts]\np = "Work."\n')
            # init a real git repo at repo_root and create a subdirectory
            subprocess.run(['git', 'init'], cwd=str(repo_root), check=True, capture_output=True)
            subdir = repo_root / 'nested'
            subdir.mkdir()
            plan_path = tmp_path / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            env = os.environ.copy()
            env['HOME'] = str(home_dir)
            result = subprocess.run(
                [sys.executable, '-m', 'aflow', 'run', str(plan_path)],
                cwd=str(subdir),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 1
            assert 'git' in result.stderr.lower() or 'Rerun' in result.stderr or 'nested' in result.stderr


class PlanParserFenceTests(unittest.TestCase):

    def test_parser_ignores_step_checkboxes_inside_backtick_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] real step\n\n```\n- [ ] fake inside fence\n```\n')
            parsed = load_plan(plan_path)
            assert parsed.snapshot.current_checkpoint_unchecked_step_count == 1

    def test_parser_ignores_step_checkboxes_inside_tilde_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] real step\n\n~~~\n- [ ] fake inside tilde fence\n~~~\n')
            parsed = load_plan(plan_path)
            assert parsed.snapshot.current_checkpoint_unchecked_step_count == 1

    def test_parser_ignores_checkpoint_heading_inside_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: Real\n- [ ] step one\n\n```\n### [ ] Checkpoint 2: Fake\n- [ ] fake step\n```\n')
            parsed = load_plan(plan_path)
            assert parsed.snapshot.total_checkpoint_count == 1
            assert parsed.snapshot.current_checkpoint_name == 'Checkpoint 1: Real'

    def test_parser_reopens_fence_after_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n\n```\n- [ ] fake\n```\n\n- [ ] step two\n')
            parsed = load_plan(plan_path)
            assert parsed.snapshot.current_checkpoint_unchecked_step_count == 2

    def test_plan_has_git_tracking_detects_heading_outside_fence(self) -> None:
        from aflow.plan import plan_has_git_tracking
        assert plan_has_git_tracking('# Plan\n\n## Git Tracking\n\nBase: abc\n')
        assert not plan_has_git_tracking('# Plan\n\n## No Tracking Here\n')

    def test_plan_has_git_tracking_ignores_heading_inside_fence(self) -> None:
        from aflow.plan import plan_has_git_tracking
        text = '# Plan\n\n### [ ] Checkpoint 1\n\n```\n## Git Tracking\n```\n'
        assert not plan_has_git_tracking(text)

    def test_generate_new_plan_path_none_checkpoint_uses_cp01(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / 'plan.md'
            original.write_text('dummy', encoding='utf-8')
            result = generate_new_plan_path(original, checkpoint_index=None)
            assert result.name == 'plan-cp01-v01.md'

    def test_generate_new_plan_path_zero_checkpoint_uses_cp00(self) -> None:
        # Verifies `1 if checkpoint_index is None` - 0 is not None, keeps 0
        with tempfile.TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / 'plan.md'
            original.write_text('dummy', encoding='utf-8')
            result = generate_new_plan_path(original, checkpoint_index=0)
            assert result.name == 'plan-cp00-v01.md'


class WorkflowPreflightTests(unittest.TestCase):

    def _make_review_wf_config(self) -> WorkflowUserConfig:
        return WorkflowUserConfig(
            harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='m')})},
            workflows={'review_wf': WorkflowConfig(
                steps={'step1': WorkflowStepConfig(
                    profile='codex.default',
                    prompts=('review_prompt',),
                    go=(GoTransition(to='END'),),
                )},
                first_step='step1',
            )},
            prompts={'review_prompt': "Use 'aflow-review-squash' skill."},
        )

    def test_preflight_fails_when_review_skill_and_no_git_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=1)
            wf_config = self._make_review_wf_config()
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(config, wf_config, 'review_wf', config_dir=repo_root, runner=lambda *a, **k: None)
            assert 'Git Tracking' in str(ctx.value)

    def test_preflight_passes_when_review_skill_and_git_tracking_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n## Git Tracking\n\nBase: abc\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            wf_config = self._make_review_wf_config()
            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=1)
            result = run_workflow(config, wf_config, 'review_wf', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)
            assert result.turns_completed == 1

    def test_preflight_skipped_for_non_review_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: Done\n- [x] step one\n')
            wf_config = WorkflowUserConfig(
                harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='m')})},
                workflows={'simple': WorkflowConfig(
                    steps={'impl': WorkflowStepConfig(
                        profile='codex.default',
                        prompts=('p',),
                        go=(GoTransition(to='END'),),
                    )},
                    first_step='impl',
                )},
                prompts={'p': "Use 'aflow-execute-plan' skill."},
            )
            config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=1)
            result = run_workflow(config, wf_config, 'simple', config_dir=repo_root)
            assert result.end_reason == 'already_complete'

    def test_preflight_fails_for_git_tracking_only_inside_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n```\n## Git Tracking\n```\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=1)
            wf_config = self._make_review_wf_config()
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(config, wf_config, 'review_wf', config_dir=repo_root, runner=lambda *a, **k: None)
            assert 'Git Tracking' in str(ctx.value)


class ActivePlanLifecycleTests(unittest.TestCase):

    def test_fix_plan_resets_to_original_after_review_without_new_plan(self) -> None:
        # 3-step workflow with all unconditional transitions so turn 3 always runs,
        # even after DONE becomes true at turn 2.  This lets us verify the invariant:
        # when implement completes the original plan but creates no new fix plan,
        # the following step sees original_plan as active (not the previous fix plan).
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            fix_plan = repo_root / 'plan-cp01-v01.md'
            captured_active: list[str] = []
            turn_counter = [0]

            def capturing_runner(argv, **kwargs):
                turn_counter[0] += 1
                prompt = ' '.join(argv)
                import re as _re
                m = _re.search(r'Active: (\S+)', prompt)
                if m:
                    captured_active.append(m.group(1).rstrip('.'))
                if turn_counter[0] == 1:
                    # review: create fix plan, original stays incomplete
                    fix_plan.write_text('# Fix\n\n### [x] CP: done\n- [x] s\n', encoding='utf-8')
                elif turn_counter[0] == 2:
                    # implement: work from fix plan, complete original — no new plan written
                    _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n')
                # turn 3 (second_review): does NOT create a new plan
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            wf_config = WorkflowUserConfig(
                harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='m')})},
                workflows={'loop': WorkflowConfig(
                    steps={
                        'review': WorkflowStepConfig(
                            profile='codex.default',
                            prompts=('rp',),
                            go=(GoTransition(to='implement'),),
                        ),
                        'implement': WorkflowStepConfig(
                            profile='codex.default',
                            prompts=('ip',),
                            go=(GoTransition(to='second_review'),),
                        ),
                        'second_review': WorkflowStepConfig(
                            profile='codex.default',
                            prompts=('rp',),
                            go=(GoTransition(to='END'),),
                        ),
                    },
                    first_step='review',
                )},
                prompts={
                    'rp': 'Active: {ACTIVE_PLAN_PATH}.',
                    'ip': 'Active: {ACTIVE_PLAN_PATH}.',
                },
            )
            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            result = run_workflow(controller_config, wf_config, 'loop', config_dir=config_dir, adapter=CodexAdapter(), runner=capturing_runner)
            assert result.turns_completed == 3
            # Turn 1 (review): active should be original plan
            assert captured_active[0] == str(plan_path)
            # Turn 2 (implement): active should be fix plan (review created it in turn 1)
            assert captured_active[1] == str(fix_plan)
            # Turn 3 (second_review): active must reset to original — not the stale fix plan
            assert captured_active[2] == str(plan_path)


class WorkflowMaxTurnsEndToEndTests(unittest.TestCase):

    def test_review_implement_review_ends_via_max_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / 'home'
            home_dir.mkdir()
            _write_config(home_dir, '[aflow]\ndefault_workflow = "multi"\n\n[harness.codex.profiles.default]\nmodel = "gpt-5.4"\n\n[workflow.multi.steps.review_plan]\nprofile = "codex.default"\nprompts = ["p"]\ngo = [\n  { to = "END", when = "MAX_TURNS_REACHED" },\n  { to = "implement_plan" },\n]\n\n[workflow.multi.steps.implement_plan]\nprofile = "codex.default"\nprompts = ["p"]\ngo = [\n  { to = "END", when = "MAX_TURNS_REACHED" },\n  { to = "review_implementation", when = "DONE" },\n  { to = "implement_plan" },\n]\n\n[workflow.multi.steps.review_implementation]\nprofile = "codex.default"\nprompts = ["p"]\ngo = [\n  { to = "END", when = "MAX_TURNS_REACHED" },\n  { to = "implement_plan", when = "NEW_PLAN_EXISTS" },\n  { to = "END" },\n]\n\n[prompts]\np = "Work."\n')
            plan_path = tmp_path / 'plan.md'
            count_file = tmp_path / 'count.txt'
            _write_plan(plan_path, '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n')
            _write_workflow_harness_script(repo_root, 'codex')
            result = _run_workflow_launcher(repo_root, '--max-turns', '1', str(plan_path), env=_workflow_test_env(repo_root, scenario='noop', plan_path=plan_path, count_file=count_file, home_dir=home_dir))
            assert result.returncode == 0
            run_dirs = sorted((repo_root / '.aflow' / 'runs').iterdir())
            run_json = json.loads((run_dirs[0] / 'run.json').read_text(encoding='utf-8'))
            assert run_json['end_reason'] == 'max_turns_reached'


def _make_git_repo(path: Path) -> None:
    """Initialize a git repo with an initial commit in path."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True, capture_output=True)
    (path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "init"], check=True, capture_output=True)


class GitStatusTests(unittest.TestCase):

    def setUp(self) -> None:
        from aflow.git_status import capture_baseline, probe_worktree, summarize_since_baseline
        self._capture_baseline = capture_baseline
        self._probe_worktree = probe_worktree
        self._summarize_since_baseline = summarize_since_baseline

    def test_probe_worktree_clean_returns_not_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            result = self._probe_worktree(repo)
            assert result is not None
            assert result.is_dirty is False
            assert result.modified_count == 0
            assert result.added_count == 0
            assert result.removed_count == 0

    def test_probe_worktree_modified_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            result = self._probe_worktree(repo)
            assert result is not None
            assert result.is_dirty is True
            assert result.modified_count == 1

    def test_probe_worktree_added_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
            result = self._probe_worktree(repo)
            assert result is not None
            assert result.is_dirty is True
            assert result.added_count >= 1

    def test_capture_baseline_returns_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            assert baseline.head_sha is not None
            assert len(baseline.tree_oid) == 40

    def test_summarize_clean_baseline_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.modified_count == 0
            assert summary.added_count == 0
            assert summary.removed_count == 0
            assert summary.lines_added == 0
            assert summary.lines_removed == 0
            assert summary.commit_count == 0
            assert summary.changed_paths == ()

    def test_summarize_modified_file_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "README.md").write_text("line1\nline2\n", encoding="utf-8")
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.modified_count == 1
            assert summary.added_count == 0
            assert summary.removed_count == 0
            assert "README.md" in summary.changed_paths

    def test_summarize_added_file_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.added_count == 1
            assert summary.lines_added >= 1
            assert "new.py" in summary.changed_paths

    def test_summarize_deleted_file_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "README.md").unlink()
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.removed_count == 1

    def test_summarize_commit_after_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "add new"], check=True, capture_output=True)
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.commit_count == 1

    def test_summarize_dirty_at_start_reports_only_post_baseline_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            (repo / "pre.py").write_text("pre = 1\n", encoding="utf-8")
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "post.py").write_text("post = 1\n", encoding="utf-8")
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert "post.py" in summary.changed_paths
            assert "pre.py" not in summary.changed_paths

    def test_summarize_returns_to_baseline_shows_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            original_content = (repo / "README.md").read_text(encoding="utf-8")
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            summary1 = self._summarize_since_baseline(repo, baseline)
            assert summary1 is not None
            assert summary1.modified_count == 1
            (repo / "README.md").write_text(original_content, encoding="utf-8")
            summary2 = self._summarize_since_baseline(repo, baseline)
            assert summary2 is not None
            assert summary2.modified_count == 0
            assert summary2.changed_paths == ()

    def test_capture_baseline_no_commits_returns_none_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
            (repo / "f.txt").write_text("x\n", encoding="utf-8")
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            assert baseline.head_sha is None

    def test_probe_returns_none_outside_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            result = self._probe_worktree(repo)
            assert result is None


class GitBannerTests(unittest.TestCase):

    def test_build_banner_renders_git_row_when_clean(self) -> None:
        from rich.console import Console
        from aflow.git_status import GitSummary
        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        summary = GitSummary(
            modified_count=0,
            added_count=0,
            removed_count=0,
            lines_added=0,
            lines_removed=0,
            commit_count=0,
            changed_paths=(),
        )
        panel = build_banner(
            config_max_turns=10,
            config_plan_path=Path("/fake/plan.md"),
            state=state,
            git_summary=summary,
        )
        assert panel is not None
        console = Console(record=True, width=100)
        console.print(panel)
        text = console.export_text()
        assert "Git" in text
        assert "clean since start" in text

    def test_build_banner_renders_git_row_with_changes(self) -> None:
        from rich.console import Console
        from aflow.git_status import GitSummary
        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        summary = GitSummary(
            modified_count=2,
            added_count=1,
            removed_count=0,
            lines_added=10,
            lines_removed=3,
            commit_count=1,
            changed_paths=("foo.py", "bar.py", "baz.py"),
        )
        panel = build_banner(
            config_max_turns=10,
            config_plan_path=Path("/fake/plan.md"),
            state=state,
            git_summary=summary,
        )
        assert panel is not None
        console = Console(record=True, width=120)
        console.print(panel)
        text = console.export_text()
        assert "Git" in text
        assert "M 2" in text
        assert "Files" in text
        assert "foo.py" in text

    def test_build_banner_files_row_respects_config_limit(self) -> None:
        from rich.console import Console
        from aflow.git_status import GitSummary
        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        summary = GitSummary(
            modified_count=5,
            added_count=0,
            removed_count=0,
            lines_added=0,
            lines_removed=0,
            commit_count=0,
            changed_paths=("a.py", "b.py", "c.py", "d.py", "e.py"),
        )
        panel = build_banner(
            config_max_turns=10,
            config_plan_path=Path("/fake/plan.md"),
            config_banner_files_limit=3,
            state=state,
            git_summary=summary,
        )
        assert panel is not None
        console = Console(record=True, width=120)
        console.print(panel)
        text = console.export_text()
        assert "+2 more" in text
        assert "d.py" not in text
        assert "a.py" in text

    def test_build_banner_no_git_summary_omits_git_rows(self) -> None:
        from rich.console import Console
        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        panel = build_banner(
            config_max_turns=10,
            config_plan_path=Path("/fake/plan.md"),
            state=state,
        )
        assert panel is not None
        console = Console(record=True, width=100)
        console.print(panel)
        text = console.export_text()
        assert "Git" not in text
        assert "Files" not in text

    def test_banner_renderer_refresh_thread_updates_live(self) -> None:
        import aflow.status as status_mod
        from unittest.mock import MagicMock, patch
        live_updates: list[object] = []

        class FakeLive:
            def __init__(self, panel, **kwargs: object) -> None:
                live_updates.append(panel)
            def start(self) -> None:
                pass
            def update(self, panel: object) -> None:
                live_updates.append(panel)
            def stop(self) -> None:
                pass

        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        with patch.object(status_mod, "_RICH_AVAILABLE", True), \
             patch.object(status_mod, "Live", FakeLive):
            renderer = status_mod.BannerRenderer(
                config_max_turns=10,
                config_plan_path=Path("/fake/plan.md"),
                refresh_interval_seconds=0.05,
                git_poll_interval_seconds=9999.0,
            )
            renderer.start(state)
            time.sleep(0.2)
            renderer.stop(state)
        assert len(live_updates) >= 3


class DirtyWorktreeCliTests(unittest.TestCase):

    def _make_clean_repo(self, path: Path) -> None:
        _make_git_repo(path)

    def test_dirty_interactive_yes_proceeds(self) -> None:
        import aflow.cli as cli_module
        from aflow.git_status import WorktreeProbe
        dirty_probe = WorktreeProbe(is_dirty=True, modified_count=2, added_count=1, removed_count=0, sample_paths=("a.py",))
        original_probe = cli_module.probe_worktree
        original_resolve = cli_module._resolve_repo_root
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir)
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            plan_path = home_dir / "plan.md"
            _write_plan(plan_path, "# Plan\n\n### [x] Checkpoint 1\n- [x] done\n")
            original_home = os.environ.get("HOME")
            try:
                os.environ["HOME"] = str(home_dir)
                cli_module.probe_worktree = lambda _: dirty_probe
                cli_module._resolve_repo_root = lambda: home_dir
                with patch("builtins.input", return_value="y"), \
                     patch("sys.stdin.isatty", return_value=True), \
                     patch("sys.stdout.isatty", return_value=True):
                    result = cli_module.main(["run", str(plan_path)])
            finally:
                cli_module.probe_worktree = original_probe
                cli_module._resolve_repo_root = original_resolve
                if original_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = original_home
        assert result == 0

    def test_dirty_interactive_no_aborts(self) -> None:
        import aflow.cli as cli_module
        from aflow.git_status import WorktreeProbe
        dirty_probe = WorktreeProbe(is_dirty=True, modified_count=1, added_count=0, removed_count=0, sample_paths=())
        original_probe = cli_module.probe_worktree
        original_resolve = cli_module._resolve_repo_root
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir)
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            plan_path = home_dir / "plan.md"
            _write_plan(plan_path, "# Plan\n\n### [x] Checkpoint 1\n- [x] done\n")
            original_home = os.environ.get("HOME")
            try:
                os.environ["HOME"] = str(home_dir)
                cli_module.probe_worktree = lambda _: dirty_probe
                cli_module._resolve_repo_root = lambda: home_dir
                with patch("builtins.input", return_value=""), \
                     patch("sys.stdin.isatty", return_value=True), \
                     patch("sys.stdout.isatty", return_value=True):
                    result = cli_module.main(["run", str(plan_path)])
            finally:
                cli_module.probe_worktree = original_probe
                cli_module._resolve_repo_root = original_resolve
                if original_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = original_home
        assert result == 1

    def test_dirty_non_interactive_aborts_with_message(self) -> None:
        import aflow.cli as cli_module
        from aflow.git_status import WorktreeProbe
        dirty_probe = WorktreeProbe(is_dirty=True, modified_count=1, added_count=0, removed_count=0, sample_paths=())
        original_probe = cli_module.probe_worktree
        original_resolve = cli_module._resolve_repo_root
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir)
            _write_config(home_dir, '[aflow]\ndefault_workflow = "simple"\n\n[workflow.simple.steps.implement_plan]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n\n[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n')
            plan_path = home_dir / "plan.md"
            _write_plan(plan_path, "# Plan\n\n### [x] Checkpoint 1\n- [x] done\n")
            original_home = os.environ.get("HOME")
            import io
            stderr_capture = io.StringIO()
            try:
                os.environ["HOME"] = str(home_dir)
                cli_module.probe_worktree = lambda _: dirty_probe
                cli_module._resolve_repo_root = lambda: home_dir
                with patch("sys.stdin.isatty", return_value=False), \
                     patch("sys.stdout.isatty", return_value=False), \
                     patch("sys.stderr", stderr_capture):
                    result = cli_module.main(["run", str(plan_path)])
            finally:
                cli_module.probe_worktree = original_probe
                cli_module._resolve_repo_root = original_resolve
                if original_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = original_home
        assert result == 1
        assert "dirty" in stderr_capture.getvalue().lower()


class RetryInconsistentCheckpointConfigTests(unittest.TestCase):

    def _write_workflow_config(self, tmpdir: str, text: str) -> Path:
        home_dir = Path(tmpdir)
        config_path = home_dir / '.config' / 'aflow' / 'aflow.toml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(text, encoding='utf-8')
        return config_path

    def _minimal_config(self, extra_aflow: str = '', extra_workflow: str = '') -> str:
        return (
            f'[aflow]\n{extra_aflow}\n'
            '[workflow.simple.steps.s]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n'
            f'{extra_workflow}'
            '[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n'
        )

    def test_global_retry_defaults_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, self._minimal_config())
            config = load_workflow_config(config_path)
            assert config.aflow.retry_inconsistent_checkpoint_state == 0

    def test_global_retry_reads_positive_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir, self._minimal_config(extra_aflow='retry_inconsistent_checkpoint_state = 3')
            )
            config = load_workflow_config(config_path)
            assert config.aflow.retry_inconsistent_checkpoint_state == 3

    def test_global_retry_accepts_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir, self._minimal_config(extra_aflow='retry_inconsistent_checkpoint_state = 0')
            )
            config = load_workflow_config(config_path)
            assert config.aflow.retry_inconsistent_checkpoint_state == 0

    def test_global_retry_rejects_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir, self._minimal_config(extra_aflow='retry_inconsistent_checkpoint_state = -1')
            )
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'retry_inconsistent_checkpoint_state' in str(ctx.value)

    def test_global_retry_rejects_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir, self._minimal_config(extra_aflow='retry_inconsistent_checkpoint_state = true')
            )
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'retry_inconsistent_checkpoint_state' in str(ctx.value)

    def test_workflow_retry_override_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                '[aflow]\nretry_inconsistent_checkpoint_state = 1\n'
                '[workflow.simple]\nretry_inconsistent_checkpoint_state = 2\n'
                '[workflow.simple.steps.s]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n'
                '[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n',
            )
            config = load_workflow_config(config_path)
            assert config.aflow.retry_inconsistent_checkpoint_state == 1
            assert config.workflows['simple'].retry_inconsistent_checkpoint_state == 2

    def test_workflow_retry_override_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(tmpdir, self._minimal_config())
            config = load_workflow_config(config_path)
            assert config.workflows['simple'].retry_inconsistent_checkpoint_state is None

    def test_workflow_retry_override_rejects_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                '[workflow.simple]\nretry_inconsistent_checkpoint_state = -1\n'
                '[workflow.simple.steps.s]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n'
                '[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n',
            )
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'retry_inconsistent_checkpoint_state' in str(ctx.value)

    def test_workflow_retry_override_rejects_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                '[workflow.simple]\nretry_inconsistent_checkpoint_state = true\n'
                '[workflow.simple.steps.s]\nprofile = "opencode.default"\nprompts = ["p"]\ngo = [{ to = "END" }]\n'
                '[harness.opencode.profiles.default]\nmodel = "m"\n\n[prompts]\np = "do it"\n',
            )
            with pytest.raises(ConfigError) as ctx:
                load_workflow_config(config_path)
            assert 'retry_inconsistent_checkpoint_state' in str(ctx.value)


class RetryInconsistentCheckpointPlanTests(unittest.TestCase):

    def test_error_kind_set_for_inconsistent_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# Plan\n\n### [x] Checkpoint 1: Broken\n- [ ] step one\n')
            with pytest.raises(PlanParseError) as exc_info:
                load_plan(plan_path)
            assert exc_info.value.error_kind == 'inconsistent_checkpoint_state'

    def test_error_kind_none_for_missing_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / 'plan.md'
            _write_plan(plan_path, '# No checkpoints here\n')
            with pytest.raises(PlanParseError) as exc_info:
                load_plan(plan_path)
            assert exc_info.value.error_kind is None


def _make_simple_wf_config(
    *,
    global_retry: int = 0,
    workflow_retry: int | None = None,
) -> WorkflowUserConfig:
    from aflow.config import WorkflowConfig
    wf = WorkflowConfig(
        steps={
            'implement_plan': WorkflowStepConfig(
                profile='codex.default',
                prompts=('p',),
                go=(
                    GoTransition(to='END', when='DONE || MAX_TURNS_REACHED'),
                    GoTransition(to='implement_plan'),
                ),
            )
        },
        first_step='implement_plan',
        retry_inconsistent_checkpoint_state=workflow_retry,
    )
    return WorkflowUserConfig(
        aflow=AflowSection(retry_inconsistent_checkpoint_state=global_retry),
        harnesses={'codex': WorkflowHarnessConfig(profiles={'default': HarnessProfileConfig(model='gpt-5.4')})},
        workflows={'simple': wf},
        prompts={'p': 'Work from {ACTIVE_PLAN_PATH}.'},
    )


_VALID_PLAN = '# Plan\n\n### [ ] Checkpoint 1: First\n- [ ] step one\n'
_COMPLETE_PLAN = '# Plan\n\n### [x] Checkpoint 1: First\n- [x] step one\n'
_BROKEN_PLAN = '# Plan\n\n### [x] Checkpoint 1: First\n- [ ] step one\n'


class RetryInconsistentCheckpointWorkflowTests(unittest.TestCase):

    def test_retry_disabled_fails_immediately_on_invalid_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=0)

            def runner(argv, **kwargs):
                _write_plan(plan_path, _BROKEN_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)
            assert 'inconsistent checkpoint state' in str(ctx.value).lower()

    def test_retry_enabled_succeeds_when_second_attempt_fixes_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=1)
            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    _write_plan(plan_path, _BROKEN_PLAN)
                else:
                    _write_plan(plan_path, _COMPLETE_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)
            assert result.turns_completed == 2
            assert result.final_snapshot.is_complete

    def test_retry_turn_reuses_same_new_plan_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=1)
            captured_new_plan_paths: list[str] = []
            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                prompt = ' '.join(argv)
                import re as re_mod
                match = re_mod.search(r'new_plan_path=(\S+)', prompt)
                if not match:
                    for tok in argv:
                        if 'cp01' in tok:
                            captured_new_plan_paths.append(tok)
                            break
                if call_count[0] == 1:
                    _write_plan(plan_path, _BROKEN_PLAN)
                else:
                    _write_plan(plan_path, _COMPLETE_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)

            run_dirs = sorted((repo_root / '.aflow' / 'runs').iterdir())
            turn1_result = json.loads((run_dirs[0] / 'turns' / 'turn-001' / 'result.json').read_text(encoding='utf-8'))
            turn2_result = json.loads((run_dirs[0] / 'turns' / 'turn-002' / 'result.json').read_text(encoding='utf-8'))
            assert turn1_result['new_plan_path'] == turn2_result['new_plan_path']

    def test_retry_turn_prompt_includes_error_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=1)
            captured_prompts: list[str] = []
            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                captured_prompts.append(' '.join(argv))
                if call_count[0] == 1:
                    _write_plan(plan_path, _BROKEN_PLAN)
                else:
                    _write_plan(plan_path, _COMPLETE_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)
            assert len(captured_prompts) == 2
            assert 'inconsistent checkpoint state' in captured_prompts[1].lower()

    def test_workflow_override_zero_disables_retry_when_global_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=1, workflow_retry=0)

            def runner(argv, **kwargs):
                _write_plan(plan_path, _BROKEN_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            with pytest.raises(WorkflowError):
                run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)

    def test_workflow_override_enables_retry_when_global_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=0, workflow_retry=1)
            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    _write_plan(plan_path, _BROKEN_PLAN)
                else:
                    _write_plan(plan_path, _COMPLETE_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)
            assert result.final_snapshot.is_complete

    def test_retry_exhaustion_fails_on_latest_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=2)

            def runner(argv, **kwargs):
                _write_plan(plan_path, _BROKEN_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=10)
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)
            assert 'inconsistent checkpoint state' in str(ctx.value).lower()

    def test_max_turn_on_failed_turn_does_not_schedule_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=3)

            def runner(argv, **kwargs):
                _write_plan(plan_path, _BROKEN_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=1)
            with pytest.raises(WorkflowError):
                run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)


class RetryInconsistentCheckpointArtifactTests(unittest.TestCase):

    def test_failed_turn_writes_retry_scheduled_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=1)
            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    _write_plan(plan_path, _BROKEN_PLAN)
                else:
                    _write_plan(plan_path, _COMPLETE_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)
            turn1 = json.loads((result.run_dir / 'turns' / 'turn-001' / 'result.json').read_text(encoding='utf-8'))
            assert turn1['status'] == 'retry-scheduled'
            assert turn1['retry_attempt'] == 1
            assert turn1['retry_limit'] == 1
            assert turn1['retry_reason'] == 'inconsistent_checkpoint_state'
            assert turn1['retry_next_turn'] is True
            assert turn1['snapshot_after'] is None
            assert 'error' in turn1

    def test_run_json_exposes_pending_retry_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=1)

            def runner(argv, **kwargs):
                _write_plan(plan_path, _BROKEN_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=1)
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)
            run_json = json.loads((ctx.value.run_dir / 'run.json').read_text(encoding='utf-8'))
            assert run_json.get('pending_retry_step_name') is None

    def test_successful_retry_turn_marks_was_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            wf_config = _make_simple_wf_config(global_retry=1)
            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    _write_plan(plan_path, _BROKEN_PLAN)
                else:
                    _write_plan(plan_path, _COMPLETE_PLAN)
                return subprocess.CompletedProcess(argv, 0, 'ok', '')

            controller_config = ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=5)
            result = run_workflow(controller_config, wf_config, 'simple', config_dir=repo_root, adapter=CodexAdapter(), runner=runner)
            turn2 = json.loads((result.run_dir / 'turns' / 'turn-002' / 'result.json').read_text(encoding='utf-8'))
            assert turn2['was_retry'] is True
            assert turn2['retry_attempt'] == 1


if __name__ == '__main__':
    unittest.main()
