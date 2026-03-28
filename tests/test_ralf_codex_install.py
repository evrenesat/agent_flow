from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import unittest


INSTALLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "codex-ralph-loop-plugin"
    / "ralph-loop-codex"
    / "scripts"
    / "install.py"
)
HOOKS_DIR = (
    Path(__file__).resolve().parents[1]
    / "codex-ralph-loop-plugin"
    / "ralph-loop-codex"
    / ".codex-runtime"
    / "hooks"
)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


installer = _load_module(INSTALLER_PATH, "ralf_codex_install")
ralf_common = _load_module(HOOKS_DIR / "ralf_common.py", "ralf_common_runtime")


class RalfCodexInstallTests(unittest.TestCase):
    def test_merge_config_text_adds_features_block(self) -> None:
        merged = installer.merge_config_text('model = "gpt-5.4"\n')
        self.assertIn("[features]", merged)
        self.assertIn("codex_hooks = true", merged)

    def test_merge_config_text_updates_existing_features(self) -> None:
        merged = installer.merge_config_text(
            'model = "gpt-5.4"\n\n[features]\nplugins = true\ncodex_hooks = false\n'
        )
        self.assertIn("codex_hooks = true", merged)
        self.assertEqual(merged.count("codex_hooks"), 1)

    def test_merge_hooks_file_inserts_managed_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_path = Path(tmpdir) / "hooks.json"
            hooks_path.write_text(
                json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "echo keep"}]}]}}),
                encoding="utf-8",
            )

            session_script = Path("/tmp/session.py")
            stop_script = Path("/tmp/stop.py")
            installer.merge_hooks_file(hooks_path, session_script, stop_script)

            data = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertIn("SessionStart", data["hooks"])
            self.assertEqual(len(data["hooks"]["Stop"]), 2)
            commands = [
                hook["command"]
                for entry in data["hooks"]["Stop"]
                for hook in entry.get("hooks", [])
            ]
            self.assertIn('python3 "/tmp/stop.py"', commands)

    def test_stop_decision_continues_for_incomplete_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            plan_path = repo_root / "plan.md"
            plan_path.write_text(
                "# Test Plan\n\n### [ ] Checkpoint 1: pending\n- [ ] sample step\n",
                encoding="utf-8",
            )
            state = ralf_common.RalphState(
                path=ralf_common.state_file_path(repo_root),
                active=True,
                runtime="codex",
                iteration=1,
                max_iterations=20,
                completion_promise="PLAN_COMPLETE",
                started_at="2026-03-28T00:00:00Z",
                plan_path=str(plan_path),
                prompt="prompt body",
            )
            ralf_common.save_state(state)

            decision = ralf_common.evaluate_stop(
                repo_root,
                stop_hook_active=False,
                last_assistant_message=None,
            )

            self.assertEqual(decision.action, "continue")
            self.assertIn("Checkpoint 1: pending", decision.reason or "")


if __name__ == "__main__":
    unittest.main()
