from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
import shutil
import textwrap

from aflow.controller import (
    ControllerError,
    build_system_prompt,
    build_user_prompt,
    run_controller,
)
from aflow.config import (
    AflowSection,
    ConfigError,
    GoTransition,
    HarnessConfig,
    HarnessProfileConfig,
    UserConfig,
    WorkflowConfig,
    WorkflowHarnessConfig,
    WorkflowStepConfig,
    WorkflowUserConfig,
    bootstrap_config,
    find_placeholders,
    load_user_config,
    load_workflow_config,
    resolve_launch_config,
    validate_workflow_config,
)
from aflow.workflow import (
    WorkflowError,
    evaluate_condition,
    generate_new_plan_path,
    pick_transition,
    render_prompt,
    render_step_prompts,
    resolve_profile,
    run_workflow,
)
from aflow.harnesses.claude import ClaudeAdapter
from aflow.harnesses.codex import CodexAdapter
from aflow.harnesses.gemini import GeminiAdapter
from aflow.harnesses.opencode import OpencodeAdapter
from aflow.harnesses.pi import PiAdapter
from aflow.harnesses.base import HarnessInvocation
from aflow.plan import PlanParseError, PlanSnapshot, load_plan
from aflow.run_state import ControllerConfig, ControllerState
from aflow.runlog import create_run_paths, prune_old_runs
from aflow.cli import build_parser
from aflow.status import build_banner, BannerRenderer


REPO_ROOT = Path(__file__).resolve().parents[2]

ALL_HARNESSES = ("claude", "codex", "gemini", "opencode", "pi")


def _write_plan(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _write_config(home_dir: Path, text: str) -> Path:
    config_path = home_dir / ".config" / "aflow" / "aflow.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    return config_path


def _copy_aflow_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    aflow_src = REPO_ROOT / "aflow"
    aflow_dst = repo_root / "aflow"
    shutil.copytree(
        aflow_src,
        aflow_dst,
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    return repo_root


def _write_fake_harness(repo_root: Path, name: str) -> Path:
    bin_dir = repo_root / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            from __future__ import annotations

            import os
            import shutil
            import sys
            from pathlib import Path


            def _read_turn_count(path: Path) -> int:
                if not path.exists():
                    return 0
                return int(path.read_text(encoding="utf-8"))


            scenario = os.environ["FAKE_SCENARIO"]
            plan_path = Path(os.environ["FAKE_PLAN_PATH"])
            count_file = Path(os.environ["FAKE_COUNT_FILE"])
            count = _read_turn_count(count_file) + 1
            count_file.write_text(str(count), encoding="utf-8")

            print(f"{Path(sys.argv[0]).name} turn {count}")

            if scenario == "success":
                shutil.copyfile(os.environ["FAKE_COMPLETED_PLAN_PATH"], plan_path)
                sys.exit(0)

            if scenario == "stagnation":
                sys.exit(0)

            if scenario == "max-turns":
                source = os.environ["FAKE_ALPHA_PLAN_PATH"] if count % 2 else os.environ["FAKE_BETA_PLAN_PATH"]
                shutil.copyfile(source, plan_path)
                sys.exit(0)

            if scenario == "fail":
                mutated_plan_path = os.environ.get("FAKE_MUTATED_PLAN_PATH")
                if mutated_plan_path:
                    shutil.copyfile(mutated_plan_path, plan_path)
                print(f"{Path(sys.argv[0]).name} failing turn {count}", file=sys.stderr)
                sys.exit(int(os.environ.get("FAKE_EXIT_CODE", "7")))

            raise SystemExit(f"unknown FAKE_SCENARIO {scenario}")
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _launcher_environment(
    repo_root: Path,
    *,
    scenario: str,
    plan_path: Path,
    count_file: Path,
    home_dir: Path | None = None,
    completed_plan_path: Path | None = None,
    alpha_plan_path: Path | None = None,
    beta_plan_path: Path | None = None,
    mutated_plan_path: Path | None = None,
    exit_code: int | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{repo_root / 'bin'}:{env['PATH']}"
    if home_dir is not None:
        env["HOME"] = str(home_dir.resolve())
    env["FAKE_SCENARIO"] = scenario
    env["FAKE_PLAN_PATH"] = str(plan_path.resolve())
    env["FAKE_COUNT_FILE"] = str(count_file.resolve())
    if completed_plan_path is not None:
        env["FAKE_COMPLETED_PLAN_PATH"] = str(completed_plan_path.resolve())
    if alpha_plan_path is not None:
        env["FAKE_ALPHA_PLAN_PATH"] = str(alpha_plan_path.resolve())
    if beta_plan_path is not None:
        env["FAKE_BETA_PLAN_PATH"] = str(beta_plan_path.resolve())
    if mutated_plan_path is not None:
        env["FAKE_MUTATED_PLAN_PATH"] = str(mutated_plan_path.resolve())
    if exit_code is not None:
        env["FAKE_EXIT_CODE"] = str(exit_code)
    return env


def _run_launcher(repo_root: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "aflow", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class CLITests(unittest.TestCase):
    def test_prog_name_is_aflow(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.prog, "aflow")

    def test_parser_allows_optional_harness_model_and_profile(self) -> None:
        args = build_parser().parse_args(["plan.md"])
        self.assertIsNone(args.harness)
        self.assertIsNone(args.model)
        self.assertIsNone(args.profile)


class PlanParserTests(unittest.TestCase):
    def test_parser_counts_only_checkpoint_section_checkboxes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

- [ ] ignored outside sections

### [ ] Checkpoint 1: First
- [ ] step one
- [x] step two

### [x] Checkpoint 2: Done
- [x] step three

""",
            )

            parsed = load_plan(plan_path)

            self.assertEqual(parsed.snapshot.current_checkpoint_name, "Checkpoint 1: First")
            self.assertEqual(parsed.snapshot.unchecked_checkpoint_count, 1)
            self.assertEqual(parsed.snapshot.current_checkpoint_unchecked_step_count, 1)
            self.assertFalse(parsed.snapshot.is_complete)

    def test_parser_rejects_checked_checkpoint_with_unchecked_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [x] Checkpoint 1: Broken
- [ ] step one
""",
            )

            with self.assertRaises(PlanParseError):
                load_plan(plan_path)

    def test_parser_rejects_files_without_checkpoint_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.md"
            _write_plan(plan_path, "# No checkpoints\n- [ ] ignored\n")

            with self.assertRaises(PlanParseError):
                load_plan(plan_path)

    def test_parser_total_checkpoint_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one

### [ ] Checkpoint 2: Second
- [ ] step two

### [x] Checkpoint 3: Done
- [x] step three
""",
            )

            parsed = load_plan(plan_path)
            self.assertEqual(parsed.snapshot.total_checkpoint_count, 3)

    def test_parser_current_checkpoint_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [x] Checkpoint 1: Done
- [x] step one

### [ ] Checkpoint 2: Current
- [ ] step two

### [ ] Checkpoint 3: Pending
- [ ] step three
""",
            )

            parsed = load_plan(plan_path)
            self.assertEqual(parsed.snapshot.current_checkpoint_index, 2)

    def test_parser_current_checkpoint_index_none_when_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [x] Checkpoint 1: Done
- [x] step one

### [x] Checkpoint 2: Done
- [x] step two
""",
            )

            parsed = load_plan(plan_path)
            self.assertTrue(parsed.snapshot.is_complete)
            self.assertIsNone(parsed.snapshot.current_checkpoint_index)


class EffortParsingTests(unittest.TestCase):
    def test_parse_with_effort(self) -> None:
        from aflow.cli import parse_args
        args = parse_args(["--harness", "codex", "--model", "gpt-5.4", "--effort", "high", "plan.md"])
        self.assertEqual(args.effort, "high")

    def test_parse_without_effort(self) -> None:
        from aflow.cli import parse_args
        args = parse_args(["--harness", "codex", "--model", "gpt-5.4", "plan.md"])
        self.assertIsNone(args.effort)


class ConfigResolutionTests(unittest.TestCase):
    def test_configless_invocation_without_harness_fails(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            resolve_launch_config(
                UserConfig(),
                harness=None,
                model="gpt-5.4",
                effort=None,
                profile=None,
            )

        self.assertIn("--harness is required unless default_harness is configured", str(ctx.exception))

    def test_configless_invocation_without_model_with_explicit_harness_keeps_model_unresolved(self) -> None:
        resolved = resolve_launch_config(
            UserConfig(),
            harness="codex",
            model=None,
            effort=None,
            profile=None,
        )

        self.assertEqual(resolved.harness, "codex")
        self.assertIsNone(resolved.model)
        self.assertIsNone(resolved.effort)

    def test_profile_overrides_harness_defaults(self) -> None:
        config = UserConfig(
            default_harness="codex",
            harnesses={
                "codex": HarnessConfig(
                    model="gpt-5.4",
                    effort="low",
                    profiles={
                        "turbo": HarnessProfileConfig(
                            model="gpt-5.4-turbo",
                            effort="high",
                        )
                    },
                )
            },
        )

        resolved = resolve_launch_config(
            config,
            harness=None,
            model=None,
            effort=None,
            profile="turbo",
        )

        self.assertEqual(resolved.harness, "codex")
        self.assertEqual(resolved.model, "gpt-5.4-turbo")
        self.assertEqual(resolved.effort, "high")

    def test_cli_model_overrides_profile_and_harness_defaults(self) -> None:
        config = UserConfig(
            default_harness="codex",
            harnesses={
                "codex": HarnessConfig(
                    model="gpt-5.4",
                    effort="low",
                    profiles={
                        "turbo": HarnessProfileConfig(
                            model="gpt-5.4-turbo",
                            effort="high",
                        )
                    },
                )
            },
        )

        resolved = resolve_launch_config(
            config,
            harness=None,
            model="gpt-5.4-explicit",
            effort=None,
            profile="turbo",
        )

        self.assertEqual(resolved.model, "gpt-5.4-explicit")
        self.assertEqual(resolved.effort, "high")

    def test_unknown_profile_raises_clear_error(self) -> None:
        config = UserConfig(
            default_harness="codex",
            harnesses={
                "codex": HarnessConfig(
                    model="gpt-5.4",
                )
            },
        )

        with self.assertRaises(ConfigError) as ctx:
            resolve_launch_config(
                config,
                harness=None,
                model=None,
                effort=None,
                profile="missing",
            )

        self.assertIn("unknown profile 'missing' for harness 'codex'", str(ctx.exception))

    def test_invalid_config_file_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home_dir = Path(tmpdir)
            original_home = os.environ.get("HOME")
            try:
                _write_config(home_dir, "default_harness = [not valid toml")
                os.environ["HOME"] = str(home_dir)
                with self.assertRaises(ConfigError) as ctx:
                    load_user_config()
            finally:
                if original_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = original_home

        self.assertIn(str(home_dir / ".config" / "aflow" / "aflow.toml"), str(ctx.exception))


class AdaptersTests(unittest.TestCase):
    def test_codex_without_effort(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="gpt-5.4",
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertEqual(
            invocation.argv,
            (
                "codex",
                "exec",
                "--dangerously-bypass-approvals-and-sandbox",
                "-C",
                "/repo",
                "--model",
                "gpt-5.4",
                "SYSTEM\n\nUSER",
            ),
        )
        self.assertEqual(invocation.prompt_mode, "prefix-system-into-user-prompt")
        self.assertEqual(invocation.effective_prompt, "SYSTEM\n\nUSER")

    def test_codex_with_effort(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="gpt-5.4",
            system_prompt="SYSTEM",
            user_prompt="USER",
            effort="high",
        )

        argv = invocation.argv
        self.assertIn("-c", argv)
        self.assertIn("model_reasoning_effort='\"high\"'", argv)
        prompt_index = argv.index("SYSTEM\n\nUSER")
        self.assertEqual(argv[prompt_index - 2], "-c")
        self.assertEqual(argv[-1], "SYSTEM\n\nUSER")

    def test_codex_effort_preserves_prompt_as_final_element(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="gpt-5.4",
            system_prompt="PROMPT",
            user_prompt="INSTRUCTIONS",
            effort="low",
        )
        self.assertEqual(invocation.argv[-1], "PROMPT\n\nINSTRUCTIONS")

    def test_codex_without_model_omits_model_flag(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model=None,
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertNotIn("--model", invocation.argv)
        self.assertEqual(invocation.argv[-1], "SYSTEM\n\nUSER")

    def test_pi_without_effort(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="sonnet",
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertEqual(
            invocation.argv,
            (
                "pi",
                "--print",
                "--system-prompt",
                "SYSTEM",
                "--model",
                "sonnet",
                "--tools",
                "read,bash,edit,write,grep,find,ls",
                "USER",
            ),
        )
        self.assertEqual(invocation.prompt_mode, "system-prompt-flag")

    def test_pi_with_effort(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="sonnet",
            system_prompt="SYSTEM",
            user_prompt="USER",
            effort="high",
        )

        argv = invocation.argv
        self.assertIn("--models", argv)
        self.assertIn("sonnet:high", argv)
        self.assertNotIn("--model", argv)
        models_index = argv.index("--models")
        self.assertEqual(argv[models_index + 1], "sonnet:high")

    def test_pi_with_effort_does_not_pass_both_model_flags(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="sonnet",
            system_prompt="S",
            user_prompt="U",
            effort="high",
        )
        self.assertIn("--models", invocation.argv)
        self.assertNotIn("--model", invocation.argv)

    def test_pi_without_model_and_with_effort_uses_thinking_flag(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model=None,
            system_prompt="SYSTEM",
            user_prompt="USER",
            effort="high",
        )

        argv = invocation.argv
        self.assertIn("--thinking", argv)
        self.assertIn("high", argv)
        self.assertNotIn("--models", argv)
        self.assertNotIn("--model", argv)

    def test_pi_without_model_and_without_effort_omits_model_flags(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model=None,
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertNotIn("--model", invocation.argv)
        self.assertNotIn("--models", invocation.argv)
        self.assertNotIn("--thinking", invocation.argv)

    def test_claude_without_effort(self) -> None:
        adapter = ClaudeAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="claude-sonnet-4-6",
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertNotIn("--effort", invocation.argv)
        self.assertEqual(
            invocation.argv,
            (
                "claude",
                "-p",
                "--system-prompt",
                "SYSTEM",
                "--model",
                "claude-sonnet-4-6",
                "--permission-mode",
                "bypassPermissions",
                "--dangerously-skip-permissions",
                "--tools",
                "default",
                "USER",
            ),
        )

    def test_claude_with_effort(self) -> None:
        adapter = ClaudeAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="claude-sonnet-4-6",
            system_prompt="SYSTEM",
            user_prompt="USER",
            effort="low",
        )

        argv = invocation.argv
        self.assertIn("--effort", argv)
        self.assertIn("low", argv)
        effort_index = argv.index("--effort")
        self.assertEqual(argv[effort_index + 1], "low")

    def test_claude_without_model_omits_model_flag(self) -> None:
        adapter = ClaudeAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model=None,
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertNotIn("--model", invocation.argv)
        self.assertEqual(invocation.argv[0], "claude")

    def test_opencode_without_effort(self) -> None:
        adapter = OpencodeAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="glm-5-turbo",
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertEqual(
            invocation.argv,
            (
                "opencode",
                "run",
                "--model",
                "glm-5-turbo",
                "--format",
                "default",
                "--dir",
                "/repo",
                "SYSTEM\n\nUSER",
            ),
        )
        self.assertEqual(invocation.prompt_mode, "prefix-system-into-user-prompt")
        self.assertEqual(invocation.effective_prompt, "SYSTEM\n\nUSER")

    def test_opencode_with_effort_ignores_effort(self) -> None:
        adapter = OpencodeAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="glm-5-turbo",
            system_prompt="SYSTEM",
            user_prompt="USER",
            effort="high",
        )

        self.assertFalse(adapter.supports_effort)
        argv = invocation.argv
        self.assertNotIn("effort", " ".join(argv).lower())
        self.assertEqual(
            argv,
            (
                "opencode",
                "run",
                "--model",
                "glm-5-turbo",
                "--format",
                "default",
                "--dir",
                "/repo",
                "SYSTEM\n\nUSER",
            ),
        )

    def test_opencode_without_model_omits_model_flag(self) -> None:
        adapter = OpencodeAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model=None,
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertNotIn("--model", invocation.argv)
        self.assertEqual(invocation.argv[0], "opencode")

    def test_gemini_without_effort(self) -> None:
        adapter = GeminiAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="gemini-2.5-pro",
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertEqual(
            invocation.argv,
            (
                "gemini",
                "--prompt",
                "SYSTEM\n\nUSER",
                "--model",
                "gemini-2.5-pro",
                "--approval-mode",
                "yolo",
                "--sandbox=false",
                "--output-format",
                "text",
            ),
        )
        self.assertEqual(invocation.prompt_mode, "prefix-system-into-user-prompt")
        self.assertEqual(invocation.effective_prompt, "SYSTEM\n\nUSER")

    def test_gemini_with_effort_ignores_effort(self) -> None:
        adapter = GeminiAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model="gemini-2.5-pro",
            system_prompt="SYSTEM",
            user_prompt="USER",
            effort="high",
        )

        self.assertFalse(adapter.supports_effort)
        argv = invocation.argv
        self.assertNotIn("effort", " ".join(argv).lower())
        self.assertEqual(
            argv,
            (
                "gemini",
                "--prompt",
                "SYSTEM\n\nUSER",
                "--model",
                "gemini-2.5-pro",
                "--approval-mode",
                "yolo",
                "--sandbox=false",
                "--output-format",
                "text",
            ),
        )

    def test_gemini_without_model_omits_model_flag(self) -> None:
        adapter = GeminiAdapter()
        invocation = adapter.build_invocation(
            repo_root=Path("/repo"),
            model=None,
            system_prompt="SYSTEM",
            user_prompt="USER",
        )

        self.assertNotIn("--model", invocation.argv)
        self.assertEqual(invocation.argv[0], "gemini")

    def test_parser_accepts_opencode_and_gemini(self) -> None:
        from aflow.cli import parse_args
        for harness in ("opencode", "gemini"):
            args = parse_args(["--harness", harness, "--model", "m", "plan.md"])
            self.assertEqual(args.harness, harness)


class ControllerTests(unittest.TestCase):
    def test_controller_stops_when_plan_completes_on_first_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            calls: list[tuple[list[str], dict[str, str]]] = []

            def runner(argv, **kwargs):
                calls.append((list(argv), dict(kwargs["env"])))
                _write_plan(
                    plan_path,
                    """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
                )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            result = run_controller(
                ControllerConfig(
                    repo_root=repo_root,
                    plan_path=plan_path,
                    harness="codex",
                    model="gpt-5.4",
                ),
                adapter=CodexAdapter(),
                runner=runner,
            )

            self.assertEqual(result.turns_completed, 1)
            self.assertTrue(result.final_snapshot.is_complete)
            self.assertEqual(len(calls), 1)
            self.assertTrue((repo_root / ".aflow" / "runs").exists())

    def test_controller_passes_effort_to_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            captured_argv: list[str] = []

            def runner(argv, **kwargs):
                captured_argv.extend(argv)
                _write_plan(
                    plan_path,
                    """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
                )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            run_controller(
                ControllerConfig(
                    repo_root=repo_root,
                    plan_path=plan_path,
                    harness="codex",
                    model="gpt-5.4",
                    effort="high",
                ),
                adapter=CodexAdapter(),
                runner=runner,
            )

            self.assertIn("-c", captured_argv)
            self.assertIn("model_reasoning_effort='\"high\"'", captured_argv)

    def test_controller_stops_after_stagnation_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            calls = 0

            def runner(argv, **kwargs):
                nonlocal calls
                calls += 1
                return subprocess.CompletedProcess(argv, 0, stdout="noop", stderr="")

            with self.assertRaises(ControllerError) as ctx:
                run_controller(
                    ControllerConfig(
                        repo_root=repo_root,
                        plan_path=plan_path,
                        harness="pi",
                        model="sonnet",
                        stagnation_limit=3,
                        max_turns=10,
                    ),
                    adapter=PiAdapter(),
                    runner=runner,
                )

            self.assertIn("checkpoint progress did not change for 3 completed turns", ctx.exception.summary)
            self.assertEqual(calls, 3)

    def test_controller_stops_after_max_turns_when_progress_keeps_changing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: Alpha
- [ ] step one
""",
            )

            calls = 0

            def runner(argv, **kwargs):
                nonlocal calls
                calls += 1
                if calls % 2:
                    title = "Alpha"
                else:
                    title = "Beta"
                _write_plan(
                    plan_path,
                    f"""# Plan

### [ ] Checkpoint 1: {title}
- [ ] step one
""",
                )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            with self.assertRaises(ControllerError) as ctx:
                run_controller(
                    ControllerConfig(
                        repo_root=repo_root,
                        plan_path=plan_path,
                        harness="claude",
                        model="claude-sonnet-4-6",
                        stagnation_limit=10,
                        max_turns=4,
                    ),
                    adapter=ClaudeAdapter(),
                    runner=runner,
                )

            self.assertIn("reached max turns limit of 4", ctx.exception.summary)
            self.assertEqual(calls, 4)

    def test_controller_stops_on_non_zero_harness_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            mutated_plan_path = repo_root / "mutated-plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                mutated_plan_path,
                """# Plan

### [ ] Checkpoint 1: Updated
- [ ] step one
""",
            )

            def runner(argv, **kwargs):
                shutil.copyfile(mutated_plan_path, plan_path)
                return subprocess.CompletedProcess(argv, 2, stdout="bad", stderr="boom")

            with self.assertRaises(ControllerError) as ctx:
                run_controller(
                    ControllerConfig(
                        repo_root=repo_root,
                        plan_path=plan_path,
                        harness="codex",
                        model="gpt-5.4",
                        max_turns=1,
                    ),
                    adapter=CodexAdapter(),
                    runner=runner,
                )

            self.assertIn("exited with code 2", ctx.exception.summary)
            self.assertIn("current checkpoint: Checkpoint 1: Updated", ctx.exception.summary)

            run_dir = ctx.exception.run_dir
            self.assertIsNotNone(run_dir)
            assert run_dir is not None
            run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["status"], "failed")
            self.assertEqual(run_json["last_snapshot"]["current_checkpoint_name"], "Checkpoint 1: Updated")
            self.assertEqual(run_json["last_snapshot"]["unchecked_checkpoint_count"], 1)
            result_json = json.loads((run_dir / "turns" / "turn-001" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result_json["status"], "harness-failed")
            self.assertEqual(result_json["snapshot_after"]["current_checkpoint_name"], "Checkpoint 1: Updated")

    def test_run_json_records_effort_when_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout="noop", stderr="")

            with self.assertRaises(ControllerError):
                run_controller(
                    ControllerConfig(
                        repo_root=repo_root,
                        plan_path=plan_path,
                        harness="codex",
                        model="gpt-5.4",
                        effort="high",
                        stagnation_limit=1,
                        max_turns=2,
                    ),
                    adapter=CodexAdapter(),
                    runner=runner,
                )

            run_dir = (repo_root / ".aflow" / "runs")
            run_dirs = list(run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_json = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["effort"], "high")

    def test_run_json_records_null_effort_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout="noop", stderr="")

            with self.assertRaises(ControllerError):
                run_controller(
                    ControllerConfig(
                        repo_root=repo_root,
                        plan_path=plan_path,
                        harness="codex",
                        model="gpt-5.4",
                        stagnation_limit=1,
                        max_turns=2,
                    ),
                    adapter=CodexAdapter(),
                    runner=runner,
                )

            run_dir = (repo_root / ".aflow" / "runs")
            run_dirs = list(run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_json = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertIsNone(run_json["effort"])

    def test_run_json_records_null_model_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )

            result = run_controller(
                ControllerConfig(
                    repo_root=repo_root,
                    plan_path=plan_path,
                    harness="codex",
                    model=None,
                ),
                adapter=CodexAdapter(),
                runner=None,
            )

            self.assertEqual(result.turns_completed, 0)
            run_dir = repo_root / ".aflow" / "runs"
            run_dirs = list(run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_json = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertIsNone(run_json["model"])

    def test_unchanged_turns_increment_issues_accumulated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout="noop", stderr="")

            with self.assertRaises(ControllerError):
                run_controller(
                    ControllerConfig(
                        repo_root=repo_root,
                        plan_path=plan_path,
                        harness="codex",
                        model="gpt-5.4",
                        stagnation_limit=3,
                        max_turns=4,
                    ),
                    adapter=CodexAdapter(),
                    runner=runner,
                )

            run_dir = (repo_root / ".aflow" / "runs")
            run_dirs = list(run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_json = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["issues_accumulated"], 3)

    def test_fatal_nonzero_exit_increments_issues_accumulated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 1, stdout="bad", stderr="err")

            with self.assertRaises(ControllerError):
                run_controller(
                    ControllerConfig(
                        repo_root=repo_root,
                        plan_path=plan_path,
                        harness="codex",
                        model="gpt-5.4",
                    ),
                    adapter=CodexAdapter(),
                    runner=runner,
                )

            run_dir = (repo_root / ".aflow" / "runs")
            run_dirs = list(run_dir.iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_json = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["issues_accumulated"], 1)

    def test_run_metadata_includes_started_at_and_active_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout="noop", stderr="")

            with self.assertRaises(ControllerError):
                run_controller(
                    ControllerConfig(
                        repo_root=repo_root,
                        plan_path=plan_path,
                        harness="codex",
                        model="gpt-5.4",
                        stagnation_limit=1,
                        max_turns=2,
                    ),
                    adapter=CodexAdapter(),
                    runner=runner,
                )

            run_dir = (repo_root / ".aflow" / "runs")
            run_dirs = list(run_dir.iterdir())
            run_json = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertIn("run_started_at", run_json)
            self.assertIn("active_turn", run_json)
            self.assertIn("issues_accumulated", run_json)
            self.assertIn("status_message", run_json)
            self.assertEqual(run_json["active_turn"], 1)

    def test_run_json_updated_at_turn_start_before_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one

### [ ] Checkpoint 2: Second
- [ ] step two
""",
            )

            call_count = 0
            run_json_at_call: list[dict] = []

            def runner(argv, **kwargs):
                nonlocal call_count
                call_count += 1
                run_dir = (repo_root / ".aflow" / "runs")
                run_dirs = sorted(run_dir.iterdir())
                run_json_at_call.append(
                    json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
                )
                if call_count == 1:
                    _write_plan(
                        plan_path,
                        """# Plan

### [x] Checkpoint 1: First
- [x] step one

### [ ] Checkpoint 2: Second
- [ ] step two
""",
                    )
                    return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")
                _write_plan(
                    plan_path,
                    """# Plan

### [x] Checkpoint 1: First
- [x] step one

### [x] Checkpoint 2: Second
- [x] step two
""",
                )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            result = run_controller(
                ControllerConfig(
                    repo_root=repo_root,
                    plan_path=plan_path,
                    harness="codex",
                    model="gpt-5.4",
                    max_turns=5,
                ),
                adapter=CodexAdapter(),
                runner=runner,
            )

            self.assertEqual(result.turns_completed, 2)
            self.assertTrue(result.final_snapshot.is_complete)
            self.assertEqual(len(run_json_at_call), 2)
            self.assertEqual(run_json_at_call[0]["active_turn"], 1)
            self.assertEqual(run_json_at_call[0]["status_message"], "running turn 1")
            self.assertEqual(run_json_at_call[1]["active_turn"], 2)
            self.assertEqual(run_json_at_call[1]["status_message"], "running turn 2")


class LazyBannerTests(unittest.TestCase):
    def test_banner_is_noop_when_rich_unavailable(self) -> None:
        import aflow.status as status_mod

        original = status_mod._RICH_AVAILABLE
        try:
            status_mod._RICH_AVAILABLE = False
            renderer = status_mod.BannerRenderer(
                config_harness="codex",
                config_model="gpt-5.4",
                config_effort=None,
                config_max_turns=15,
                config_plan_path=Path("/fake/plan.md"),
            )
            state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
            renderer.start(state)
            renderer.update(state)
            renderer.stop(state)
            result = status_mod.build_banner(
                config_harness="codex",
                config_model="gpt-5.4",
                config_effort=None,
                config_max_turns=15,
                config_plan_path=Path("/fake/plan.md"),
                state=state,
            )
            self.assertIsNone(result)
        finally:
            status_mod._RICH_AVAILABLE = original

    def test_banner_renders_default_model_label(self) -> None:
        from rich.console import Console

        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        panel = build_banner(
            config_harness="codex",
            config_model=None,
            config_effort=None,
            config_max_turns=15,
            config_plan_path=Path("/fake/plan.md"),
            state=state,
        )

        self.assertIsNotNone(panel)
        console = Console(record=True, width=80)
        console.print(panel)
        self.assertIn("default", console.export_text())


class RetentionTests(unittest.TestCase):
    def test_retention_prune_old_runs_keeps_newest_twenty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_root = Path(tmpdir)
            for index in range(23):
                run_dir = runs_root / f"20260329T120000Z-{22 - index:08x}"
                run_dir.mkdir()
                mtime_ns = 1_700_000_000_000_000_000 + index * 1_000_000
                os.utime(run_dir, ns=(mtime_ns, mtime_ns))

            prune_old_runs(runs_root, keep_runs=20)

            remaining = sorted(path.name for path in runs_root.iterdir())
            self.assertEqual(len(remaining), 20)
            self.assertEqual(
                remaining,
                sorted(f"20260329T120000Z-{22 - index:08x}" for index in range(3, 23)),
            )


class EndToEndLauncherTests(unittest.TestCase):
    def test_launcher_completes_with_fake_codex_and_writes_turn_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            resolved_repo_root = repo_root.resolve()
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            resolved_plan_path = plan_path.resolve()
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "codex",
                "--model",
                "gpt-5.4",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(plan_path.read_text(encoding="utf-8"), completed_plan_path.read_text(encoding="utf-8"))

            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_dir = run_dirs[0]
            run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["status"], "completed")
            self.assertEqual(run_json["turns_completed"], 1)
            self.assertEqual(run_json["keep_runs"], 20)
            self.assertIsNone(run_json["effort"])

            turn_dir = run_dir / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            effective_prompt = (turn_dir / "effective-prompt.txt").read_text(encoding="utf-8")
            self.assertEqual(argv["label"], "codex")
            self.assertEqual(argv["prompt_mode"], "prefix-system-into-user-prompt")
            self.assertEqual(
                argv["argv"],
                [
                    "codex",
                    "exec",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "-C",
                    str(resolved_repo_root),
                    "--model",
                    "gpt-5.4",
                    effective_prompt,
                ],
            )
            self.assertIn("Current checkpoint: Checkpoint 1: First", (turn_dir / "system-prompt.txt").read_text(encoding="utf-8"))
            self.assertIn(f"Plan file: {resolved_plan_path}", (turn_dir / "user-prompt.txt").read_text(encoding="utf-8"))
            self.assertEqual((turn_dir / "stdout.txt").read_text(encoding="utf-8").strip(), "codex turn 1")
            self.assertTrue((turn_dir / "result.json").is_file())

    def test_launcher_uses_config_default_harness_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            _write_config(
                home_dir,
                """default_harness = "codex"

[harness.codex]
model = "gpt-5.4"
""",
            )
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                    home_dir=home_dir,
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            run_dir = run_dirs[0]
            run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["harness"], "codex")
            self.assertEqual(run_json["model"], "gpt-5.4")

            turn_dir = run_dir / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertIn("--model", argv["argv"])
            self.assertIn("gpt-5.4", argv["argv"])

    def test_launcher_with_explicit_harness_and_no_model_omits_model_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "codex",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                    home_dir=home_dir,
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            run_dir = run_dirs[0]
            run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertIsNone(run_json["model"])

            turn_dir = run_dir / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertNotIn("--model", argv["argv"])

    def test_launcher_succeeds_with_profile_turbo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            _write_config(
                home_dir,
                """default_harness = "codex"

[harness.codex]
model = "gpt-5.4"
effort = "low"

[harness.codex.profiles.turbo]
model = "gpt-5.4-turbo"
effort = "high"
""",
            )
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--profile",
                "turbo",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                    home_dir=home_dir,
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            run_dir = run_dirs[0]
            run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["harness"], "codex")
            self.assertEqual(run_json["model"], "gpt-5.4-turbo")
            self.assertEqual(run_json["effort"], "high")

            turn_dir = run_dir / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertIn("--model", argv["argv"])
            self.assertIn("gpt-5.4-turbo", argv["argv"])
            self.assertIn("-c", argv["argv"])
            self.assertIn("model_reasoning_effort='\"high\"'", argv["argv"])

    def test_launcher_fails_when_profile_missing_for_resolved_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            home_dir = tmp_path / "home"
            home_dir.mkdir()
            _write_config(
                home_dir,
                """default_harness = "codex"

[harness.codex]
model = "gpt-5.4"
""",
            )
            plan_path = tmp_path / "plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--profile",
                "turbo",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    home_dir=home_dir,
                ),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("unknown profile 'turbo' for harness 'codex'", result.stderr)
            self.assertFalse((repo_root / ".aflow" / "runs").exists())

    def test_launcher_with_effort_writes_effort_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "codex",
                "--model",
                "gpt-5.4",
                "--effort",
                "high",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            run_dir = run_dirs[0]
            run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["effort"], "high")

            turn_dir = run_dir / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertIn("-c", argv["argv"])
            self.assertIn("model_reasoning_effort='\"high\"'", argv["argv"])

    def test_launcher_effort_pi_uses_models_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "pi",
                "--model",
                "sonnet",
                "--effort",
                "high",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            turn_dir = run_dirs[0] / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertIn("--models", argv["argv"])
            self.assertIn("sonnet:high", argv["argv"])
            self.assertNotIn("--model", argv["argv"])

    def test_launcher_effort_claude_adds_effort_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "claude",
                "--model",
                "sonnet",
                "--effort",
                "low",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)

            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            turn_dir = run_dirs[0] / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertIn("--effort", argv["argv"])
            self.assertIn("low", argv["argv"])

    def test_launcher_stops_after_five_unchanged_snapshots_with_fake_pi(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            plan_path = tmp_path / "plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "pi",
                "--model",
                "sonnet",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="stagnation",
                    plan_path=plan_path,
                    count_file=count_file,
                ),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("checkpoint progress did not change for 5 completed turns", result.stderr)
            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_json = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["status"], "failed")
            self.assertEqual(run_json["turns_completed"], 5)
            self.assertEqual(run_json["stagnation_turns"], 5)
            self.assertIn(str(run_dirs[0]), run_json["failure_reason"])
            self.assertEqual(len(list((run_dirs[0] / "turns").iterdir())), 5)

    def test_launcher_stops_after_fifteen_turns_with_fake_claude(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            plan_path = tmp_path / "plan.md"
            alpha_plan_path = tmp_path / "alpha-plan.md"
            beta_plan_path = tmp_path / "beta-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: Alpha
- [ ] step one
""",
            )
            _write_plan(
                alpha_plan_path,
                """# Plan

### [ ] Checkpoint 1: Alpha
- [ ] step one
""",
            )
            _write_plan(
                beta_plan_path,
                """# Plan

### [ ] Checkpoint 1: Beta
- [ ] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "claude",
                "--model",
                "claude-sonnet-4-6",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="max-turns",
                    plan_path=plan_path,
                    count_file=count_file,
                    alpha_plan_path=alpha_plan_path,
                    beta_plan_path=beta_plan_path,
                ),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("reached max turns limit of 15", result.stderr)
            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_json = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["status"], "failed")
            self.assertEqual(run_json["turns_completed"], 15)
            self.assertEqual(len(list((run_dirs[0] / "turns").iterdir())), 15)

            turn_dir = run_dirs[0] / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertEqual(argv["label"], "claude")
            self.assertEqual(argv["prompt_mode"], "system-prompt-flag")
            self.assertEqual(
                argv["argv"][:11],
                [
                    "claude",
                    "-p",
                    "--system-prompt",
                    (turn_dir / "system-prompt.txt").read_text(encoding="utf-8"),
                    "--model",
                    "claude-sonnet-4-6",
                    "--permission-mode",
                    "bypassPermissions",
                    "--dangerously-skip-permissions",
                    "--tools",
                    "default",
                ],
            )

    def test_launcher_stops_immediately_on_non_zero_fake_harness_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            plan_path = tmp_path / "plan.md"
            mutated_plan_path = tmp_path / "mutated-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                mutated_plan_path,
                """# Plan

### [ ] Checkpoint 1: Updated
- [ ] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "codex",
                "--model",
                "gpt-5.4",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="fail",
                    plan_path=plan_path,
                    count_file=count_file,
                    mutated_plan_path=mutated_plan_path,
                    exit_code=7,
                ),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("harness 'codex' exited with code 7", result.stderr)
            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            self.assertEqual(len(run_dirs), 1)
            run_json = json.loads((run_dirs[0] / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["status"], "failed")
            self.assertEqual(run_json["turns_completed"], 0)
            self.assertIn("run log directory:", run_json["failure_reason"])
            self.assertEqual(run_json["last_snapshot"]["current_checkpoint_name"], "Checkpoint 1: Updated")
            turn_dir = run_dirs[0] / "turns" / "turn-001"
            self.assertEqual((turn_dir / "stderr.txt").read_text(encoding="utf-8").strip(), "codex failing turn 1")
            result_json = json.loads((turn_dir / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result_json["status"], "harness-failed")
            self.assertEqual(result_json["returncode"], 7)
            self.assertEqual(result_json["snapshot_after"]["current_checkpoint_name"], "Checkpoint 1: Updated")

    def test_launcher_uses_aflow_runs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            _run_launcher(
                repo_root,
                "--harness",
                "codex",
                "--model",
                "gpt-5.4",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                ),
            )

            self.assertTrue((repo_root / ".aflow" / "runs").exists())
            self.assertFalse((repo_root / ".ralf" / "runs").exists())

    def test_launcher_opencode_with_effort_warns_and_ignores_effort(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            resolved_repo_root = repo_root.resolve()
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "opencode",
                "--model",
                "glm-5-turbo",
                "--effort",
                "high",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("warning: harness 'opencode' ignores --effort", result.stderr)
            self.assertEqual(result.stderr.count("warning"), 1)

            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            run_dir = run_dirs[0]
            run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["effort"], "high")

            turn_dir = run_dir / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertEqual(argv["label"], "opencode")
            self.assertNotIn("effort", " ".join(argv["argv"]).lower())
            self.assertEqual(argv["argv"][0], "opencode")
            self.assertIn("--dir", argv["argv"])
            self.assertIn(str(resolved_repo_root), argv["argv"])

    def test_launcher_gemini_with_effort_warns_and_ignores_effort(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            repo_root = _copy_aflow_repo(tmp_path)
            plan_path = tmp_path / "plan.md"
            completed_plan_path = tmp_path / "completed-plan.md"
            count_file = tmp_path / "count.txt"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )
            _write_plan(
                completed_plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )
            for harness in ALL_HARNESSES:
                _write_fake_harness(repo_root, harness)

            result = _run_launcher(
                repo_root,
                "--harness",
                "gemini",
                "--model",
                "gemini-2.5-pro",
                "--effort",
                "high",
                str(plan_path),
                env=_launcher_environment(
                    repo_root,
                    scenario="success",
                    plan_path=plan_path,
                    count_file=count_file,
                    completed_plan_path=completed_plan_path,
                ),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("warning: harness 'gemini' ignores --effort", result.stderr)
            self.assertEqual(result.stderr.count("warning"), 1)

            run_dirs = sorted((repo_root / ".aflow" / "runs").iterdir())
            run_dir = run_dirs[0]
            run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["effort"], "high")

            turn_dir = run_dir / "turns" / "turn-001"
            argv = json.loads((turn_dir / "argv.json").read_text(encoding="utf-8"))
            self.assertEqual(argv["label"], "gemini")
            self.assertNotIn("effort", " ".join(argv["argv"]).lower())
            self.assertIn("--approval-mode", argv["argv"])
            self.assertIn("--sandbox=false", argv["argv"])


class WorkflowConfigTests(unittest.TestCase):
    def _write_workflow_config(
        self, tmpdir: str, text: str
    ) -> Path:
        home_dir = Path(tmpdir)
        config_path = home_dir / ".config" / "aflow" / "aflow.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(text, encoding="utf-8")
        return config_path

    def _load_with_home(self, tmpdir: str, config_path: Path) -> WorkflowUserConfig:
        original_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(Path(tmpdir))
            return load_workflow_config()
        finally:
            if original_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = original_home

    def test_parse_canonical_workflow_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[aflow]
default_workflow = "simple"

[harness.opencode.profiles.default]
model = "glm-5-turbo"

[harness.codex.profiles.high]
model = "gpt-5.4"
effort = "high"

[workflow.simple.steps.implement_plan]
profile = "opencode.default"
prompts = ["implementation_prompt"]
go = [
  { to = "END", when = "DONE || MAX_TURNS_REACHED" },
  { to = "implement_plan" },
]

[prompts]
implementation_prompt = "Work from {ACTIVE_PLAN_PATH}."
""",
            )
            config = load_workflow_config(config_path)

            self.assertEqual(config.aflow.default_workflow, "simple")
            self.assertIn("opencode", config.harnesses)
            self.assertEqual(
                config.harnesses["opencode"].profiles["default"].model,
                "glm-5-turbo",
            )
            self.assertEqual(
                config.harnesses["codex"].profiles["high"].effort,
                "high",
            )
            self.assertIn("simple", config.workflows)
            self.assertEqual(config.workflows["simple"].first_step, "implement_plan")
            step = config.workflows["simple"].steps["implement_plan"]
            self.assertEqual(step.profile, "opencode.default")
            self.assertEqual(step.prompts, ("implementation_prompt",))
            self.assertEqual(len(step.go), 2)
            self.assertEqual(step.go[0].to, "END")
            self.assertEqual(step.go[0].when, "DONE || MAX_TURNS_REACHED")
            self.assertEqual(step.go[1].to, "implement_plan")
            self.assertIsNone(step.go[1].when)
            self.assertEqual(config.prompts["implementation_prompt"], "Work from {ACTIVE_PLAN_PATH}.")

    def test_parse_multi_step_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[aflow]
default_workflow = "review_loop"

[harness.claude.profiles.opus]
model = "claude-opus-4"

[harness.opencode.profiles.turbo]
model = "glm-5-turbo"

[harness.codex.profiles.high]
model = "gpt-5.4"
effort = "high"

[workflow.review_loop.steps.review_plan]
profile = "claude.opus"
prompts = ["review_prompt"]
go = [{ to = "implement_plan" }]

[workflow.review_loop.steps.implement_plan]
profile = "opencode.turbo"
prompts = ["implementation_prompt"]
go = [{ to = "review_implementation" }]

[workflow.review_loop.steps.review_implementation]
profile = "codex.high"
prompts = ["review_prompt", "fix_plan_prompt"]
go = [
  { to = "END", when = "DONE || MAX_TURNS_REACHED" },
  { to = "implement_plan" },
]

[prompts]
review_prompt = "Review the plan."
implementation_prompt = "Implement from {ACTIVE_PLAN_PATH}."
fix_plan_prompt = "Write new plan to {NEW_PLAN_PATH}."
""",
            )
            config = load_workflow_config(config_path)

            wf = config.workflows["review_loop"]
            self.assertEqual(wf.first_step, "review_plan")
            self.assertEqual(len(wf.steps), 3)
            self.assertEqual(wf.steps["review_plan"].profile, "claude.opus")
            self.assertEqual(
                wf.steps["review_implementation"].prompts,
                ("review_prompt", "fix_plan_prompt"),
            )

    def test_parse_rejects_legacy_default_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                'default_harness = "codex"\n',
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("default_harness", str(ctx.exception))

    def test_parse_rejects_legacy_default_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[aflow]
default_model = "gpt-5.4"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("default_model", str(ctx.exception))

    def test_parse_rejects_bare_harness_step_selector(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.implement_plan]
profile = "opencode"
prompts = ["p1"]
go = [{ to = "END" }]

[prompts]
p1 = "do it"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("fully qualified", str(ctx.exception))

    def test_parse_rejects_harness_level_model_and_effort(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[harness.opencode]
model = "glm-5-turbo"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("model", str(ctx.exception))

    def test_parse_rejects_invalid_condition_not_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.implement_plan]
profile = "opencode.default"
prompts = ["p1"]
go = [{ to = "END", when = "NOT_DONE" }]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p1 = "do it"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("NOT_DONE", str(ctx.exception))

    def test_parse_rejects_invalid_condition_max_iterations_not_reached(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.implement_plan]
profile = "opencode.default"
prompts = ["p1"]
go = [{ to = "END", when = "MAX_ITERATIONS_NOT_REACHED" }]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p1 = "do it"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("MAX_ITERATIONS_NOT_REACHED", str(ctx.exception))

    def test_parse_rejects_invalid_transition_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.implement_plan]
profile = "opencode.default"
prompts = ["p1"]
go = [{ to = "nonexistent_step" }]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p1 = "do it"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("nonexistent_step", str(ctx.exception))

    def test_parse_accepts_unconditional_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.implement_plan]
profile = "opencode.default"
prompts = ["p1"]
go = [{ to = "implement_plan" }]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p1 = "do it"
""",
            )
            config = load_workflow_config(config_path)
            step = config.workflows["simple"].steps["implement_plan"]
            self.assertEqual(len(step.go), 1)
            self.assertEqual(step.go[0].to, "implement_plan")
            self.assertIsNone(step.go[0].when)

    def test_parse_accepts_complex_condition_expressions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["p1"]
go = [
  { to = "END", when = "(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS" },
  { to = "s1" },
]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p1 = "do it"
""",
            )
            config = load_workflow_config(config_path)
            step = config.workflows["simple"].steps["s1"]
            self.assertEqual(
                step.go[0].when,
                "(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS",
            )

    def test_prompts_preserve_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["alpha", "beta", "gamma"]

[harness.opencode.profiles.default]
model = "m"

[prompts]
gamma = "third"
alpha = "first"
beta = "second"
""",
            )
            config = load_workflow_config(config_path)
            step = config.workflows["simple"].steps["s1"]
            self.assertEqual(step.prompts, ("alpha", "beta", "gamma"))

    def test_go_transitions_preserve_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["p"]
go = [
  { to = "END", when = "DONE" },
  { to = "END", when = "MAX_TURNS_REACHED" },
  { to = "s2" },
]

[workflow.simple.steps.s2]
profile = "opencode.default"
prompts = ["p"]
go = [{ to = "END" }]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p = "do it"
""",
            )
            config = load_workflow_config(config_path)
            step = config.workflows["simple"].steps["s1"]
            self.assertEqual(len(step.go), 3)
            self.assertEqual(step.go[0].to, "END")
            self.assertEqual(step.go[0].when, "DONE")
            self.assertEqual(step.go[1].to, "END")
            self.assertEqual(step.go[1].when, "MAX_TURNS_REACHED")
            self.assertEqual(step.go[2].to, "s2")
            self.assertIsNone(step.go[2].when)

    def test_placeholder_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[aflow]
default_workflow = "simple"

[harness.opencode.profiles.default]
model = "FILL_IN_MODEL"

[harness.codex.profiles.high]
model = "gpt-5.4"
effort = "high"

[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["p"]

[prompts]
p = "do it"
""",
            )
            config = load_workflow_config(config_path)
            placeholders = find_placeholders(config)
            self.assertEqual(placeholders, ["harness.opencode.profiles.default.model"])

    def test_placeholder_settings_report_exact_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[aflow]
default_workflow = "simple"

[harness.opencode.profiles.default]
model = "FILL_IN_MODEL"

[harness.codex.profiles.high]
model = "FILL_IN_MODEL"
effort = "high"

[harness.claude.profiles.opus]
model = "FILL_IN_MODEL"
effort = "medium"

[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["p"]

[prompts]
p = "do it"
""",
            )
            config = load_workflow_config(config_path)
            placeholders = find_placeholders(config)
            self.assertEqual(len(placeholders), 3)
            self.assertIn("harness.claude.profiles.opus.model", placeholders)
            self.assertIn("harness.codex.profiles.high.model", placeholders)
            self.assertIn("harness.opencode.profiles.default.model", placeholders)

    def test_bootstrap_template_matches_canonical_schema(self) -> None:
        from aflow.default_config import STARTER_CONFIG

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "starter.toml"
            config_path.write_text(STARTER_CONFIG, encoding="utf-8")
            config = load_workflow_config(config_path)

            self.assertEqual(config.aflow.default_workflow, "simple")
            self.assertIn("opencode", config.harnesses)
            self.assertIn("codex", config.harnesses)
            self.assertEqual(
                config.harnesses["opencode"].profiles["default"].model,
                "FILL_IN_MODEL",
            )
            self.assertEqual(
                config.harnesses["codex"].profiles["high"].model,
                "FILL_IN_MODEL",
            )
            self.assertEqual(
                config.harnesses["codex"].profiles["high"].effort,
                "high",
            )
            self.assertIn("simple", config.workflows)
            step = config.workflows["simple"].steps["implement_plan"]
            self.assertEqual(step.profile, "opencode.default")
            self.assertEqual(step.prompts, ("implementation_prompt",))
            self.assertEqual(len(step.go), 2)
            self.assertEqual(step.go[0].to, "END")
            self.assertEqual(step.go[0].when, "DONE || MAX_TURNS_REACHED")
            self.assertEqual(step.go[1].to, "implement_plan")
            self.assertIsNone(step.go[1].when)
            self.assertIn("implementation_prompt", config.prompts)
            self.assertNotIn("review_plan", config.prompts)

    def test_bootstrap_creates_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aflow" / "aflow.toml"
            result = bootstrap_config(config_path)
            self.assertTrue(result.exists())
            self.assertEqual(result, config_path)

    def test_bootstrap_does_not_overwrite_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "aflow" / "aflow.toml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text("existing", encoding="utf-8")
            result = bootstrap_config(config_path)
            self.assertEqual(result.read_text(encoding="utf-8"), "existing")

    def test_parse_rejects_unsupported_workflow_level_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple]
start = "review"

[workflow.simple.steps.review]
profile = "opencode.default"
prompts = ["p"]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p = "x"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("workflow.simple", str(ctx.exception))
            self.assertIn("start", str(ctx.exception))

    def test_parse_rejects_invalid_condition_operator_eq(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["p"]
go = [{ to = "END", when = "DONE == NEW_PLAN_EXISTS" }]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p = "x"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("==", str(ctx.exception))

    def test_parse_rejects_invalid_condition_operator_plus(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["p"]
go = [{ to = "END", when = "DONE + NEW_PLAN_EXISTS" }]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p = "x"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("+", str(ctx.exception))

    def test_validate_workflow_config_default_workflow_missing_reports_exact_path(self) -> None:
        config = WorkflowUserConfig(
            aflow=AflowSection(default_workflow="nonexistent"),
            workflows={"simple": WorkflowConfig()},
        )
        errors = validate_workflow_config(config)
        self.assertTrue(any("aflow.default_workflow" in e for e in errors))
        self.assertTrue(any("nonexistent" in e for e in errors))

    def test_validate_workflow_config_unknown_harness_reports_exact_path(self) -> None:
        wf = WorkflowConfig(
            steps={"s1": WorkflowStepConfig(profile="unknown_harness.p1", prompts=("p1",))}
        )
        config = WorkflowUserConfig(workflows={"w": wf}, prompts={"p1": "text"})
        errors = validate_workflow_config(config)
        self.assertTrue(
            any("workflow.w.steps.s1.profile" in e for e in errors)
        )

    def test_validate_workflow_config_unknown_profile_reports_exact_path(self) -> None:
        wf = WorkflowConfig(
            steps={"s1": WorkflowStepConfig(profile="opencode.missing", prompts=("p1",))}
        )
        config = WorkflowUserConfig(
            harnesses={"opencode": WorkflowHarnessConfig(profiles={})},
            workflows={"w": wf},
            prompts={"p1": "text"},
        )
        errors = validate_workflow_config(config)
        self.assertTrue(
            any("workflow.w.steps.s1.profile" in e for e in errors)
        )

    def test_validate_workflow_config_unknown_prompt_reports_exact_path(self) -> None:
        wf = WorkflowConfig(
            steps={"s1": WorkflowStepConfig(profile="opencode.default", prompts=("missing_prompt",))}
        )
        config = WorkflowUserConfig(
            harnesses={
                "opencode": WorkflowHarnessConfig(
                    profiles={"default": HarnessProfileConfig(model="m")}
                )
            },
            workflows={"w": wf},
        )
        errors = validate_workflow_config(config)
        self.assertTrue(
            any("workflow.w.steps.s1.prompts[0]" in e for e in errors)
        )

    def test_parse_accepts_complex_condition_with_negation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["p"]
go = [
  { to = "END", when = "!(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS" },
  { to = "s1" },
]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p = "do it"
""",
            )
            config = load_workflow_config(config_path)
            step = config.workflows["simple"].steps["s1"]
            self.assertEqual(
                step.go[0].when,
                "!(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS",
            )

    def test_validate_workflow_config_default_workflow_missing(self) -> None:
        config = WorkflowUserConfig(
            aflow=AflowSection(default_workflow="nonexistent"),
            workflows={"simple": WorkflowConfig()},
        )
        errors = validate_workflow_config(config)
        self.assertTrue(any("nonexistent" in e for e in errors))

    def test_validate_workflow_config_passes_for_valid_config(self) -> None:
        wf = WorkflowConfig(
            steps={"s1": WorkflowStepConfig(profile="opencode.default", prompts=("p1",))}
        )
        config = WorkflowUserConfig(
            aflow=AflowSection(default_workflow="w"),
            harnesses={
                "opencode": WorkflowHarnessConfig(
                    profiles={"default": HarnessProfileConfig(model="m")}
                )
            },
            workflows={"w": wf},
            prompts={"p1": "text"},
        )
        errors = validate_workflow_config(config)
        self.assertEqual(errors, [])

    def test_load_returns_empty_config_for_missing_file(self) -> None:
        config = load_workflow_config(Path("/nonexistent/aflow.toml"))
        self.assertIsNone(config.aflow.default_workflow)
        self.assertEqual(config.harnesses, {})
        self.assertEqual(config.workflows, {})
        self.assertEqual(config.prompts, {})

    def test_parse_rejects_unsupported_top_level_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[server]
port = 8080
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("server", str(ctx.exception))

    def test_parse_rejects_unsupported_condition_in_when(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["p"]
go = [{ to = "END", when = "DONE && STALEMATE" }]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p = "do it"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("STALEMATE", str(ctx.exception))

    def test_first_step_is_first_declared(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.review]
profile = "claude.opus"
prompts = ["p1"]
go = [{ to = "implement" }]

[workflow.simple.steps.implement]
profile = "opencode.default"
prompts = ["p2"]
go = [{ to = "END" }]

[harness.claude.profiles.opus]
model = "m"

[harness.opencode.profiles.default]
model = "m"

[prompts]
p1 = "review"
p2 = "implement"
""",
            )
            config = load_workflow_config(config_path)
            wf = config.workflows["simple"]
            self.assertEqual(wf.first_step, "review")

    def test_missing_steps_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple]
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("steps", str(ctx.exception))

    def test_step_missing_profile_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
prompts = ["p"]

[prompts]
p = "do it"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("profile", str(ctx.exception))

    def test_step_missing_prompts_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
profile = "opencode.default"

[harness.opencode.profiles.default]
model = "m"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("prompts", str(ctx.exception))

    def test_go_missing_to_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._write_workflow_config(
                tmpdir,
                """\
[workflow.simple.steps.s1]
profile = "opencode.default"
prompts = ["p"]
go = [{ when = "DONE" }]

[harness.opencode.profiles.default]
model = "m"

[prompts]
p = "do it"
""",
            )
            with self.assertRaises(ConfigError) as ctx:
                load_workflow_config(config_path)
            self.assertIn("to", str(ctx.exception))


class WorkflowRuntimeTests(unittest.TestCase):
    def test_prompt_rendering_supports_inline_and_file_uri_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            prompt_file = config_dir / "custom_prompt.txt"
            prompt_file.write_text("File content with {ACTIVE_PLAN_PATH}", encoding="utf-8")
            original = config_dir / "plan.md"
            new_plan = config_dir / "plan-cp01-v01.md"
            active = config_dir / "plan.md"

            result = render_prompt(
                "file://custom_prompt.txt",
                config_dir=config_dir,
                original_plan_path=original,
                new_plan_path=new_plan,
                active_plan_path=active,
            )
            self.assertEqual(result, f"File content with {active}")

            result_inline = render_prompt(
                "Work from {ACTIVE_PLAN_PATH}. New: {NEW_PLAN_PATH}. Original: {ORIGINAL_PLAN_PATH}",
                config_dir=config_dir,
                original_plan_path=original,
                new_plan_path=new_plan,
                active_plan_path=active,
            )
            self.assertEqual(result_inline, f"Work from {active}. New: {new_plan}. Original: {original}")

    def test_prompt_rendering_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir)
            with self.assertRaises(WorkflowError) as ctx:
                render_prompt(
                    "file://nonexistent.txt",
                    config_dir=config_dir,
                    original_plan_path=Path("/fake/plan.md"),
                    new_plan_path=Path("/fake/new.md"),
                    active_plan_path=Path("/fake/plan.md"),
                )
            self.assertIn("not found", str(ctx.exception))

    def test_render_step_prompts_unknown_key_raises(self) -> None:
        step = WorkflowStepConfig(profile="opencode.default", prompts=("missing_key",))
        config = WorkflowUserConfig(prompts={})
        with self.assertRaises(WorkflowError) as ctx:
            render_step_prompts(
                step,
                config,
                config_dir=Path("/cfg"),
                original_plan_path=Path("/p.md"),
                new_plan_path=Path("/n.md"),
                active_plan_path=Path("/a.md"),
            )
        self.assertIn("missing_key", str(ctx.exception))

    def test_render_step_prompts_joins_multiple_prompts(self) -> None:
        step = WorkflowStepConfig(profile="opencode.default", prompts=("p1", "p2"))
        config = WorkflowUserConfig(prompts={"p1": "First {ORIGINAL_PLAN_PATH}", "p2": "Second {ACTIVE_PLAN_PATH}"})
        result = render_step_prompts(
            step,
            config,
            config_dir=Path("/cfg"),
            original_plan_path=Path("/orig.md"),
            new_plan_path=Path("/new.md"),
            active_plan_path=Path("/active.md"),
        )
        self.assertEqual(result, "First /orig.md\n\nSecond /active.md")

    def test_new_plan_path_increments_version_for_checkpoint_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            original = parent / "plan.md"
            original.write_text("dummy", encoding="utf-8")

            p1 = generate_new_plan_path(original, checkpoint_index=1)
            self.assertEqual(p1.name, "plan-cp01-v01.md")

            p1.touch()
            p2 = generate_new_plan_path(original, checkpoint_index=1)
            self.assertEqual(p2.name, "plan-cp01-v02.md")

            p2.touch()
            p3 = generate_new_plan_path(original, checkpoint_index=1)
            self.assertEqual(p3.name, "plan-cp01-v03.md")

            p4 = generate_new_plan_path(original, checkpoint_index=2)
            self.assertEqual(p4.name, "plan-cp02-v01.md")

    def test_new_plan_path_uses_correct_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            original = parent / "plan.markdown"
            original.write_text("dummy", encoding="utf-8")
            p1 = generate_new_plan_path(original, checkpoint_index=1)
            self.assertEqual(p1.name, "plan-cp01-v01.markdown")

    def test_new_plan_path_none_checkpoint_uses_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            original = parent / "plan.md"
            original.write_text("dummy", encoding="utf-8")
            p1 = generate_new_plan_path(original, checkpoint_index=None)
            self.assertEqual(p1.name, "plan-cp01-v01.md")

    def test_condition_parsing_simple_symbols(self) -> None:
        self.assertTrue(evaluate_condition("DONE", done=True, new_plan_exists=False, max_turns_reached=False))
        self.assertFalse(evaluate_condition("DONE", done=False, new_plan_exists=False, max_turns_reached=False))
        self.assertTrue(evaluate_condition("NEW_PLAN_EXISTS", done=False, new_plan_exists=True, max_turns_reached=False))
        self.assertTrue(evaluate_condition("MAX_TURNS_REACHED", done=False, new_plan_exists=False, max_turns_reached=True))

    def test_condition_parsing_or(self) -> None:
        self.assertTrue(evaluate_condition("DONE || MAX_TURNS_REACHED", done=True, new_plan_exists=False, max_turns_reached=False))
        self.assertTrue(evaluate_condition("DONE || MAX_TURNS_REACHED", done=False, new_plan_exists=False, max_turns_reached=True))
        self.assertFalse(evaluate_condition("DONE || MAX_TURNS_REACHED", done=False, new_plan_exists=False, max_turns_reached=False))

    def test_condition_parsing_and(self) -> None:
        self.assertTrue(evaluate_condition("DONE && NEW_PLAN_EXISTS", done=True, new_plan_exists=True, max_turns_reached=False))
        self.assertFalse(evaluate_condition("DONE && NEW_PLAN_EXISTS", done=True, new_plan_exists=False, max_turns_reached=False))

    def test_condition_parsing_negation(self) -> None:
        self.assertTrue(evaluate_condition("!DONE", done=False, new_plan_exists=False, max_turns_reached=False))
        self.assertFalse(evaluate_condition("!DONE", done=True, new_plan_exists=False, max_turns_reached=False))

    def test_condition_parsing_parentheses(self) -> None:
        self.assertTrue(evaluate_condition("(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS", done=True, new_plan_exists=True, max_turns_reached=False))
        self.assertFalse(evaluate_condition("(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS", done=False, new_plan_exists=False, max_turns_reached=False))

    def test_condition_parsing_complex(self) -> None:
        expr = "!(DONE || MAX_TURNS_REACHED) && NEW_PLAN_EXISTS"
        self.assertTrue(evaluate_condition(expr, done=False, new_plan_exists=True, max_turns_reached=False))
        self.assertFalse(evaluate_condition(expr, done=True, new_plan_exists=True, max_turns_reached=False))

    def test_ordered_transitions_first_match_wins(self) -> None:
        transitions = (
            GoTransition(to="END", when="DONE"),
            GoTransition(to="END", when="MAX_TURNS_REACHED"),
            GoTransition(to="step2"),
        )
        self.assertEqual(pick_transition(transitions, step_path="workflow.w.steps.s", done=True, new_plan_exists=False, max_turns_reached=False), "END")
        self.assertEqual(pick_transition(transitions, step_path="workflow.w.steps.s", done=False, new_plan_exists=False, max_turns_reached=True), "END")
        self.assertEqual(pick_transition(transitions, step_path="workflow.w.steps.s", done=False, new_plan_exists=False, max_turns_reached=False), "step2")

    def test_ordered_transitions_unconditional_fallback(self) -> None:
        transitions = (
            GoTransition(to="END", when="DONE"),
            GoTransition(to="step2"),
        )
        self.assertEqual(pick_transition(transitions, step_path="workflow.w.steps.s", done=False, new_plan_exists=False, max_turns_reached=False), "step2")
        self.assertEqual(pick_transition(transitions, step_path="workflow.w.steps.s", done=True, new_plan_exists=False, max_turns_reached=False), "END")

    def test_pick_transition_no_match_raises(self) -> None:
        transitions = (
            GoTransition(to="END", when="DONE"),
            GoTransition(to="END", when="NEW_PLAN_EXISTS"),
        )
        with self.assertRaises(WorkflowError) as ctx:
            pick_transition(transitions, step_path="workflow.w.steps.s", done=False, new_plan_exists=False, max_turns_reached=False)
        self.assertIn("no transition matched", str(ctx.exception))

    def test_resolve_profile_success(self) -> None:
        config = WorkflowUserConfig(
            harnesses={
                "opencode": WorkflowHarnessConfig(
                    profiles={"default": HarnessProfileConfig(model="m", effort="high")}
                )
            },
        )
        result = resolve_profile("opencode.default", config, step_path="workflow.w.steps.s")
        self.assertEqual(result.harness_name, "opencode")
        self.assertEqual(result.profile_name, "default")
        self.assertEqual(result.model, "m")
        self.assertEqual(result.effort, "high")

    def test_resolve_profile_unknown_harness_raises(self) -> None:
        config = WorkflowUserConfig()
        with self.assertRaises(WorkflowError) as ctx:
            resolve_profile("unknown.default", config, step_path="workflow.w.steps.s")
        self.assertIn("unknown harness", str(ctx.exception))

    def test_resolve_profile_unknown_profile_raises(self) -> None:
        config = WorkflowUserConfig(
            harnesses={"opencode": WorkflowHarnessConfig(profiles={})}
        )
        with self.assertRaises(WorkflowError) as ctx:
            resolve_profile("opencode.missing", config, step_path="workflow.w.steps.s")
        self.assertIn("unknown profile", str(ctx.exception))

    def test_resolve_profile_bare_selector_raises(self) -> None:
        config = WorkflowUserConfig()
        with self.assertRaises(WorkflowError) as ctx:
            resolve_profile("opencode", config, step_path="workflow.w.steps.s")
        self.assertIn("fully qualified", str(ctx.exception))

    def test_workflow_ends_only_via_end_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "simple": WorkflowConfig(
                        steps={
                            "implement_plan": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("implementation_prompt",),
                                go=(
                                    GoTransition(to="END", when="DONE || MAX_TURNS_REACHED"),
                                    GoTransition(to="implement_plan"),
                                ),
                            )
                        },
                        first_step="implement_plan",
                    )
                },
                prompts={"implementation_prompt": "Work from {ACTIVE_PLAN_PATH}."},
            )

            call_count = 0

            def runner(argv, **kwargs):
                nonlocal call_count
                call_count += 1
                _write_plan(
                    plan_path,
                    """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
                )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=5,
            )

            result = run_workflow(
                controller_config,
                wf_config,
                "simple",
                config_dir=config_dir,
                adapter=CodexAdapter(),
                runner=runner,
            )

            self.assertEqual(result.turns_completed, 1)
            self.assertTrue(result.final_snapshot.is_complete)
            self.assertEqual(call_count, 1)

    def test_workflow_loops_implementer_steps_without_stagnation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
- [ ] step two
""",
            )

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "simple": WorkflowConfig(
                        steps={
                            "implement_plan": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("implementation_prompt",),
                                go=(
                                    GoTransition(to="END", when="DONE || MAX_TURNS_REACHED"),
                                    GoTransition(to="implement_plan"),
                                ),
                            )
                        },
                        first_step="implement_plan",
                    )
                },
                prompts={"implementation_prompt": "Work from {ACTIVE_PLAN_PATH}."},
            )

            call_count = 0

            def runner(argv, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    _write_plan(
                        plan_path,
                        """# Plan

### [ ] Checkpoint 1: First
- [x] step one
- [ ] step two
""",
                    )
                elif call_count == 2:
                    _write_plan(
                        plan_path,
                        """# Plan

### [x] Checkpoint 1: First
- [x] step one
- [x] step two
""",
                    )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=5,
            )

            result = run_workflow(
                controller_config,
                wf_config,
                "simple",
                config_dir=config_dir,
                adapter=CodexAdapter(),
                runner=runner,
            )

            self.assertEqual(result.turns_completed, 2)
            self.assertTrue(result.final_snapshot.is_complete)
            self.assertEqual(call_count, 2)

    def test_active_plan_updates_only_when_generated_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            active_plan_paths: list[Path] = []

            def runner(argv, **kwargs):
                user_prompt_text = kwargs.get("capture_output", False)
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "loop": WorkflowConfig(
                        steps={
                            "review": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("review_prompt",),
                                go=(GoTransition(to="implement"),),
                            ),
                            "implement": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("impl_prompt",),
                                go=(
                                    GoTransition(to="END", when="DONE || MAX_TURNS_REACHED"),
                                    GoTransition(to="review"),
                                ),
                            ),
                        },
                        first_step="review",
                    )
                },
                prompts={
                    "review_prompt": "Review. New plan: {NEW_PLAN_PATH}. Active: {ACTIVE_PLAN_PATH}.",
                    "impl_prompt": "Implement. New plan: {NEW_PLAN_PATH}. Active: {ACTIVE_PLAN_PATH}.",
                },
            )

            turn_number = [0]
            captured_prompts: list[str] = []

            def capturing_runner(argv, **kwargs):
                turn_number[0] += 1
                if turn_number[0] == 1:
                    _write_plan(
                        plan_path,
                        """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
                    )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=5,
            )

            run_workflow(
                controller_config,
                wf_config,
                "loop",
                config_dir=config_dir,
                adapter=CodexAdapter(),
                runner=capturing_runner,
            )

    def test_active_plan_remains_unchanged_when_review_does_not_create_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
- [ ] step two
""",
            )

            captured_active_paths: list[str] = []

            def capturing_runner(argv, **kwargs):
                prompt_text = " ".join(argv)
                import re
                match = re.search(r"Active: (\S+)", prompt_text)
                if match:
                    captured = match.group(1).rstrip(".")
                    captured_active_paths.append(captured)
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "loop": WorkflowConfig(
                        steps={
                            "review": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("review_prompt",),
                                go=(GoTransition(to="implement"),),
                            ),
                            "implement": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("impl_prompt",),
                                go=(
                                    GoTransition(to="END", when="DONE || MAX_TURNS_REACHED"),
                                    GoTransition(to="review"),
                                ),
                            ),
                        },
                        first_step="review",
                    )
                },
                prompts={
                    "review_prompt": "Active: {ACTIVE_PLAN_PATH}. New: {NEW_PLAN_PATH}.",
                    "impl_prompt": "Active: {ACTIVE_PLAN_PATH}. New: {NEW_PLAN_PATH}.",
                },
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=4,
            )

            run_workflow(
                controller_config,
                wf_config,
                "loop",
                config_dir=config_dir,
                adapter=CodexAdapter(),
                runner=capturing_runner,
            )

            for p in captured_active_paths:
                self.assertEqual(str(plan_path), p)

    def test_active_plan_updates_when_generated_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            captured_active_paths: list[str] = []
            turn_counter = [0]

            def capturing_runner(argv, **kwargs):
                turn_counter[0] += 1
                prompt_text = " ".join(argv)
                import re as re_mod
                match = re_mod.search(r"Active: (\S+)", prompt_text)
                if match:
                    captured_active_paths.append(match.group(1).rstrip("."))
                if turn_counter[0] == 1:
                    new_path = repo_root / "plan-cp01-v01.md"
                    new_path.write_text("# Generated plan", encoding="utf-8")
                    _write_plan(
                        plan_path,
                        """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
                    )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "loop": WorkflowConfig(
                        steps={
                            "review": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("review_prompt",),
                                go=(GoTransition(to="implement"),),
                            ),
                            "implement": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("impl_prompt",),
                                go=(
                                    GoTransition(to="END", when="DONE || MAX_TURNS_REACHED"),
                                    GoTransition(to="review"),
                                ),
                            ),
                        },
                        first_step="review",
                    )
                },
                prompts={
                    "review_prompt": "Active: {ACTIVE_PLAN_PATH}. New: {NEW_PLAN_PATH}.",
                    "impl_prompt": "Active: {ACTIVE_PLAN_PATH}.",
                },
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=5,
            )

            run_workflow(
                controller_config,
                wf_config,
                "loop",
                config_dir=config_dir,
                adapter=CodexAdapter(),
                runner=capturing_runner,
            )

            self.assertEqual(len(captured_active_paths), 2)
            self.assertEqual(captured_active_paths[0], str(plan_path))
            expected_new = str(repo_root / "plan-cp01-v01.md")
            self.assertEqual(captured_active_paths[1], expected_new)

    def test_workflow_multistep_review_and_implement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            call_order: list[str] = []

            def capturing_runner(argv, **kwargs):
                call_order.append(argv[0])
                _write_plan(
                    plan_path,
                    """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
                )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "claude": WorkflowHarnessConfig(
                        profiles={"opus": HarnessProfileConfig(model="claude-opus-4")}
                    ),
                    "opencode": WorkflowHarnessConfig(
                        profiles={"turbo": HarnessProfileConfig(model="glm-5-turbo")}
                    ),
                },
                workflows={
                    "review_loop": WorkflowConfig(
                        steps={
                            "review_plan": WorkflowStepConfig(
                                profile="claude.opus",
                                prompts=("review_prompt",),
                                go=(GoTransition(to="implement_plan"),),
                            ),
                            "implement_plan": WorkflowStepConfig(
                                profile="opencode.turbo",
                                prompts=("impl_prompt",),
                                go=(
                                    GoTransition(to="END", when="DONE || MAX_TURNS_REACHED"),
                                    GoTransition(to="review_plan"),
                                ),
                            ),
                        },
                        first_step="review_plan",
                    )
                },
                prompts={
                    "review_prompt": "Review the plan.",
                    "impl_prompt": "Implement from {ACTIVE_PLAN_PATH}.",
                },
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=5,
            )

            result = run_workflow(
                controller_config,
                wf_config,
                "review_loop",
                config_dir=config_dir,
                runner=capturing_runner,
            )

            self.assertEqual(result.turns_completed, 2)
            self.assertTrue(result.final_snapshot.is_complete)
            self.assertEqual(call_order, ["claude", "opencode"])

    def test_workflow_max_turns_routing_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout="noop", stderr="")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "simple": WorkflowConfig(
                        steps={
                            "implement_plan": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("p",),
                                go=(
                                    GoTransition(to="END", when="DONE || MAX_TURNS_REACHED"),
                                    GoTransition(to="implement_plan"),
                                ),
                            )
                        },
                        first_step="implement_plan",
                    )
                },
                prompts={"p": "Work."},
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=3,
            )

            result = run_workflow(
                controller_config,
                wf_config,
                "simple",
                config_dir=config_dir,
                adapter=CodexAdapter(),
                runner=runner,
            )

            self.assertEqual(result.turns_completed, 3)
            self.assertFalse(result.final_snapshot.is_complete)

    def test_workflow_no_matching_transition_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "simple": WorkflowConfig(
                        steps={
                            "implement_plan": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("p",),
                                go=(
                                    GoTransition(to="END", when="DONE"),
                                ),
                            )
                        },
                        first_step="implement_plan",
                    )
                },
                prompts={"p": "Work."},
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=3,
            )

            with self.assertRaises(WorkflowError) as ctx:
                run_workflow(
                    controller_config,
                    wf_config,
                    "simple",
                    config_dir=config_dir,
                    adapter=CodexAdapter(),
                    runner=runner,
                )
            self.assertIn("no transition matched", str(ctx.exception))
            self.assertIn("workflow.simple.steps.implement_plan", str(ctx.exception))
            self.assertIn("DONE=False", str(ctx.exception))

    def test_workflow_no_matching_transition_writes_failed_run_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
- [ ] step two
""",
            )

            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    _write_plan(
                        plan_path,
                        """# Plan

### [ ] Checkpoint 1: First
- [x] step one
- [ ] step two
""",
                    )
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "loop": WorkflowConfig(
                        steps={
                            "review": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("p",),
                                go=(GoTransition(to="implement"),),
                            ),
                            "implement": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("p",),
                                go=(
                                    GoTransition(to="END", when="DONE"),
                                ),
                            ),
                        },
                        first_step="review",
                    )
                },
                prompts={"p": "Work."},
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=5,
            )

            with self.assertRaises(WorkflowError) as ctx:
                run_workflow(
                    controller_config,
                    wf_config,
                    "loop",
                    config_dir=config_dir,
                    adapter=CodexAdapter(),
                    runner=runner,
                )

            self.assertIn("workflow.loop.steps.implement", str(ctx.exception))
            run_dir = ctx.exception.run_dir
            self.assertIsNotNone(run_dir)
            assert run_dir is not None
            run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(run_json["status"], "failed")
            self.assertIn(run_json["failure_reason"], str(ctx.exception))
            self.assertEqual(run_json["turns_completed"], 2)
            self.assertEqual(run_json["last_snapshot"]["current_checkpoint_name"], "Checkpoint 1: First")

    def test_workflow_done_reflects_original_plan_not_fix_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            fix_plan = repo_root / "plan-cp01-v01.md"
            _write_plan(
                fix_plan,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )

            turn_counter = [0]
            ended_at_turn = [0]

            def runner(argv, **kwargs):
                turn_counter[0] += 1
                ended_at_turn[0] = turn_counter[0]
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "simple": WorkflowConfig(
                        steps={
                            "implement_plan": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("p",),
                                go=(
                                    GoTransition(to="END", when="DONE"),
                                    GoTransition(to="implement_plan"),
                                ),
                            )
                        },
                        first_step="implement_plan",
                    )
                },
                prompts={"p": "Work."},
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=5,
            )

            with self.assertRaises(WorkflowError):
                run_workflow(
                    controller_config,
                    wf_config,
                    "simple",
                    config_dir=config_dir,
                    adapter=CodexAdapter(),
                    runner=runner,
                )

            self.assertEqual(ended_at_turn[0], 5)

    def test_workflow_missing_workflow_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=1,
            )

            with self.assertRaises(WorkflowError) as ctx:
                run_workflow(
                    controller_config,
                    WorkflowUserConfig(),
                    "nonexistent",
                    config_dir=repo_root,
                )
            self.assertIn("not found", str(ctx.exception))

    def test_workflow_extra_instructions_appended(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            captured_user_prompts: list[str] = []

            class CapturingAdapter:
                name = "codex"
                supports_effort = False

                def build_invocation(self, *, repo_root, model, system_prompt, user_prompt, effort=None):
                    captured_user_prompts.append(user_prompt)
                    return HarnessInvocation(
                        label="codex",
                        argv=("codex", "run", user_prompt),
                        env={},
                        prompt_mode="prefix-system-into-user-prompt",
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        effective_prompt=f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt,
                    )

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "simple": WorkflowConfig(
                        steps={
                            "implement_plan": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("p",),
                                go=(
                                    GoTransition(to="END", when="DONE || MAX_TURNS_REACHED"),
                                    GoTransition(to="implement_plan"),
                                ),
                            )
                        },
                        first_step="implement_plan",
                    )
                },
                prompts={"p": "Work from {ACTIVE_PLAN_PATH}."},
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=1,
                extra_instructions=("be careful", "use tests"),
            )

            run_workflow(
                controller_config,
                wf_config,
                "simple",
                config_dir=config_dir,
                adapter=CapturingAdapter(),
                runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
            )

            self.assertEqual(len(captured_user_prompts), 1)
            self.assertIn("Work from", captured_user_prompts[0])
            self.assertIn("be careful use tests", captured_user_prompts[0])

    def test_workflow_harness_failure_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [ ] Checkpoint 1: First
- [ ] step one
""",
            )

            def runner(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 1, stdout="bad", stderr="err")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "simple": WorkflowConfig(
                        steps={
                            "implement_plan": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("p",),
                                go=(
                                    GoTransition(to="END", when="DONE"),
                                    GoTransition(to="implement_plan"),
                                ),
                            )
                        },
                        first_step="implement_plan",
                    )
                },
                prompts={"p": "Work."},
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=3,
            )

            with self.assertRaises(WorkflowError) as ctx:
                run_workflow(
                    controller_config,
                    wf_config,
                    "simple",
                    config_dir=config_dir,
                    adapter=CodexAdapter(),
                    runner=runner,
                )
            self.assertIn("exited with code 1", str(ctx.exception))

    def test_workflow_already_complete_returns_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            config_dir = repo_root
            plan_path = repo_root / "plan.md"
            _write_plan(
                plan_path,
                """# Plan

### [x] Checkpoint 1: First
- [x] step one
""",
            )

            call_count = [0]

            def runner(argv, **kwargs):
                call_count[0] += 1
                return subprocess.CompletedProcess(argv, 0, "ok", "")

            wf_config = WorkflowUserConfig(
                harnesses={
                    "codex": WorkflowHarnessConfig(
                        profiles={"default": HarnessProfileConfig(model="gpt-5.4")}
                    )
                },
                workflows={
                    "simple": WorkflowConfig(
                        steps={
                            "implement_plan": WorkflowStepConfig(
                                profile="codex.default",
                                prompts=("p",),
                                go=(GoTransition(to="END"),),
                            )
                        },
                        first_step="implement_plan",
                    )
                },
                prompts={"p": "Work."},
            )

            controller_config = ControllerConfig(
                repo_root=repo_root,
                plan_path=plan_path,
                max_turns=3,
            )

            result = run_workflow(
                controller_config,
                wf_config,
                "simple",
                config_dir=config_dir,
                adapter=CodexAdapter(),
                runner=runner,
            )

            self.assertEqual(result.turns_completed, 0)
            self.assertTrue(result.final_snapshot.is_complete)
            self.assertEqual(call_count[0], 0)


if __name__ == "__main__":
    unittest.main()
