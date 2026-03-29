from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import os
import subprocess
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
from aflow.harnesses.claude import ClaudeAdapter
from aflow.harnesses.codex import CodexAdapter
from aflow.harnesses.pi import PiAdapter
from aflow.plan import PlanParseError, load_plan
from aflow.run_state import ControllerConfig
from aflow.runlog import create_run_paths, prune_old_runs
from aflow.cli import build_parser
from aflow.status import build_banner


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_plan(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


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
    completed_plan_path: Path | None = None,
    alpha_plan_path: Path | None = None,
    beta_plan_path: Path | None = None,
    mutated_plan_path: Path | None = None,
    exit_code: int | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{repo_root / 'bin'}:{env['PATH']}"
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
        [str(repo_root / "aflow" / "aflow"), *args],
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
            for harness in ("codex", "pi", "claude"):
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
            for harness in ("codex", "pi", "claude"):
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
            for harness in ("codex", "pi", "claude"):
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
            for harness in ("codex", "pi", "claude"):
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
            for harness in ("codex", "pi", "claude"):
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
            for harness in ("codex", "pi", "claude"):
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
            for harness in ("codex", "pi", "claude"):
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
            for harness in ("codex", "pi", "claude"):
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


if __name__ == "__main__":
    unittest.main()
