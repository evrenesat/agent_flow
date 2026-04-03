from __future__ import annotations

import io
from pathlib import Path

import pytest
from importlib import resources

from aflow.skill_installer import (
    BUNDLED_SKILL_NAMES,
    InstallerError,
    build_install_plan,
    detect_auto_targets,
    discover_bundled_skills,
    install_skills,
)


class _FakeStdin:
    def __init__(self, interactive: bool) -> None:
        self._interactive = interactive

    def isatty(self) -> bool:
        return self._interactive


def _write_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_discover_bundled_skills_uses_package_resources() -> None:
    skills = discover_bundled_skills()
    assert tuple(skill.name for skill in skills) == BUNDLED_SKILL_NAMES
    bundled_root = resources.files("aflow").joinpath("bundled_skills")
    for skill in skills:
        skill_dir = bundled_root.joinpath(skill.name)
        assert skill_dir.is_dir()
        assert skill_dir.joinpath("SKILL.md").is_file()


def test_detect_auto_targets_selects_installed_executables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for executable in ("claude", "codex", "copilot", "gemini", "pi"):
        _write_executable(bin_dir / executable)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    targets = detect_auto_targets()

    assert [target.harness for target in targets] == ["claude", "codex", "copilot", "gemini", "pi"]
    assert targets[0].destination == Path("~/.claude/skills").expanduser()
    assert targets[1].destination == Path("~/.agents/skills").expanduser()
    assert targets[2].destination == Path("~/.agents/skills").expanduser()
    assert targets[3].destination == Path("~/.agents/skills").expanduser()
    assert targets[4].destination == Path("~/.agents/skills").expanduser()


def test_manual_destination_installs_six_child_directories(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    stdout = io.StringIO()

    copied = install_skills(destination=destination, yes=True, stdin=_FakeStdin(True), stdout=stdout)

    assert copied == 6
    for skill_name in BUNDLED_SKILL_NAMES:
        skill_dir = destination / skill_name
        assert skill_dir.is_dir()
        assert skill_dir.joinpath("SKILL.md").is_file()
    assert "Manual install mode" in stdout.getvalue()
    assert "Total copy operations: 6" in stdout.getvalue()


def test_yes_skips_prompt(tmp_path: Path) -> None:
    destination = tmp_path / "skills"

    def explode(_: str) -> str:
        raise AssertionError("input should not be called when --yes is used")

    copied = install_skills(
        destination=destination,
        yes=True,
        stdin=_FakeStdin(False),
        input_fn=explode,
        stdout=io.StringIO(),
    )

    assert copied == 6


def test_confirmation_decline_performs_no_copies(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    stdout = io.StringIO()

    copied = install_skills(
        destination=destination,
        yes=False,
        stdin=_FakeStdin(True),
        input_fn=lambda _: "n",
        stdout=stdout,
    )

    assert copied == 0
    assert not destination.exists()
    assert "Installation cancelled." in stdout.getvalue()


def test_noninteractive_without_yes_returns_clear_error(tmp_path: Path) -> None:
    destination = tmp_path / "skills"

    with pytest.raises(InstallerError, match="rerun with --yes"):
        install_skills(destination=destination, yes=False, stdin=_FakeStdin(False), stdout=io.StringIO())


def test_preflight_rejects_destination_file_collisions(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    destination.write_text("not a directory", encoding="utf-8")

    with pytest.raises(InstallerError, match="Destination path is a file"):
        install_skills(destination=destination, yes=True, stdin=_FakeStdin(True), stdout=io.StringIO())


def test_overwrite_in_place_updates_existing_skill_files_without_pruning_extras(tmp_path: Path) -> None:
    destination = tmp_path / "skills"
    existing_skill_dir = destination / "aflow-plan"
    existing_skill_dir.mkdir(parents=True, exist_ok=True)
    existing_skill_file = existing_skill_dir / "SKILL.md"
    existing_skill_file.write_text("old content\n", encoding="utf-8")
    unrelated_file = destination / "notes.txt"
    unrelated_file.write_text("keep me\n", encoding="utf-8")

    copied = install_skills(destination=destination, yes=True, stdin=_FakeStdin(True), stdout=io.StringIO())

    assert copied == 6
    packaged_skill = resources.files("aflow").joinpath("bundled_skills", "aflow-plan", "SKILL.md").read_text(encoding="utf-8")
    assert existing_skill_file.read_text(encoding="utf-8") == packaged_skill
    assert unrelated_file.read_text(encoding="utf-8") == "keep me\n"


def test_auto_install_plan_uses_shared_agents_directory_for_codex_copilot_gemini_and_pi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for executable in ("codex", "copilot", "gemini", "pi"):
        _write_executable(bin_dir / executable)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    plan = build_install_plan()

    assert [target.harness for target in plan.targets] == ["codex", "copilot", "gemini", "pi"]
    assert plan.targets[0].destination == Path("~/.agents/skills").expanduser()
    assert plan.targets[1].destination == Path("~/.agents/skills").expanduser()
    assert plan.targets[2].destination == Path("~/.agents/skills").expanduser()
    assert plan.targets[3].destination == Path("~/.agents/skills").expanduser()


def test_auto_install_codex_copilot_gemini_and_pi_grouped_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from aflow.skill_installer import render_preview
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for executable in ("codex", "copilot", "gemini", "pi"):
        _write_executable(bin_dir / executable)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    plan = build_install_plan()
    preview = render_preview(plan)

    expanded_dest = str(Path("~/.agents/skills").expanduser())
    assert "codex, copilot, gemini, pi" in preview
    assert "Total copy operations: 6" in preview
    assert preview.count(expanded_dest) == 1


def test_auto_install_codex_copilot_gemini_and_pi_copies_only_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for executable in ("codex", "copilot", "gemini", "pi"):
        _write_executable(bin_dir / executable)
    monkeypatch.setenv("PATH", str(bin_dir))
    agents_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(agents_home))

    copied = install_skills(yes=True, stdin=_FakeStdin(True), stdout=__import__('io').StringIO())

    assert copied == 6
    shared_dest = Path("~/.agents/skills").expanduser()
    for skill_name in BUNDLED_SKILL_NAMES:
        assert (shared_dest / skill_name).is_dir()
        assert (shared_dest / skill_name / "SKILL.md").is_file()
