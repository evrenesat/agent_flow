from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path
import shutil
import sys
from typing import Callable


BUNDLED_SKILL_NAMES = (
    "aflow-plan",
    "aflow-execute-plan",
    "aflow-execute-checkpoint",
    "aflow-review-squash",
    "aflow-review-checkpoint",
    "aflow-review-final",
)


@dataclass(frozen=True)
class HarnessInstallSpec:
    harness: str
    executable: str
    destination_template: str


SUPPORTED_HARNESS_INSTALL_SPECS = (
    HarnessInstallSpec("claude", "claude", "~/.claude/skills"),
    HarnessInstallSpec("codex", "codex", "~/.codex/skills"),
    HarnessInstallSpec("gemini", "gemini", "~/.agents/skills"),
    HarnessInstallSpec("kiro", "kiro-cli", "~/.kiro/skills"),
    HarnessInstallSpec("opencode", "opencode", "~/.config/opencode/skills"),
    HarnessInstallSpec("pi", "pi", "~/.agents/skills"),
)


class InstallerError(RuntimeError):
    pass


@dataclass(frozen=True)
class BundledSkill:
    name: str
    source: Traversable


@dataclass(frozen=True)
class InstallTarget:
    harness: str
    executable: str
    destination: Path


@dataclass(frozen=True)
class PreviewRow:
    harness: str
    destination: Path
    skill_name: str


@dataclass(frozen=True)
class InstallPlan:
    mode: str
    skills: tuple[BundledSkill, ...]
    targets: tuple[InstallTarget, ...]
    preview_rows: tuple[PreviewRow, ...]


def bundled_skills_root() -> Traversable:
    return resources.files("aflow").joinpath("bundled_skills")


def discover_bundled_skills() -> tuple[BundledSkill, ...]:
    root = bundled_skills_root()
    skills: list[BundledSkill] = []
    missing: list[str] = []
    for skill_name in BUNDLED_SKILL_NAMES:
        skill_dir = root.joinpath(skill_name)
        skill_md = skill_dir.joinpath("SKILL.md")
        if not skill_dir.is_dir() or not skill_md.is_file():
            missing.append(skill_name)
            continue
        skills.append(BundledSkill(name=skill_name, source=skill_dir))
    if missing:
        missing_text = ", ".join(missing)
        raise InstallerError(f"Missing bundled skill resources: {missing_text}")
    return tuple(skills)


def detect_auto_targets() -> tuple[InstallTarget, ...]:
    targets: list[InstallTarget] = []
    for spec in SUPPORTED_HARNESS_INSTALL_SPECS:
        if shutil.which(spec.executable) is None:
            continue
        targets.append(
            InstallTarget(
                harness=spec.harness,
                executable=spec.executable,
                destination=Path(spec.destination_template).expanduser(),
            )
        )
    if not targets:
        raise InstallerError(
            "No supported aflow harness CLIs were found on PATH. "
            "Rerun with --yes and a destination path, or install a supported harness first."
        )
    return tuple(targets)


def build_install_plan(destination: str | Path | None = None) -> InstallPlan:
    skills = discover_bundled_skills()
    if destination is None:
        targets = detect_auto_targets()
        mode = "auto"
    else:
        targets = (InstallTarget(harness="manual", executable="", destination=Path(destination).expanduser()),)
        mode = "manual"
    preview_rows = tuple(
        PreviewRow(harness=target.harness, destination=target.destination, skill_name=skill.name)
        for target in targets
        for skill in skills
    )
    return InstallPlan(mode=mode, skills=skills, targets=targets, preview_rows=preview_rows)


def render_preview(plan: InstallPlan) -> str:
    lines: list[str] = []
    if plan.mode == "auto":
        lines.append("Auto install mode")
        lines.append("Detected harness destinations:")
        for target in plan.targets:
            lines.append(f"- {target.harness} ({target.executable}) -> {target.destination}")
    else:
        lines.append("Manual install mode")
        lines.append(f"Destination root: {plan.targets[0].destination}")
    lines.append("Bundled skills:")
    for skill in plan.skills:
        lines.append(f"- {skill.name}")
    lines.append(f"Total copy operations: {len(plan.preview_rows)}")
    return "\n".join(lines)


def _format_success_summary(plan: InstallPlan, copied_count: int) -> str:
    target_count = len(plan.targets)
    target_label = "destination" if target_count == 1 else "destinations"
    skill_label = "skill" if len(plan.skills) == 1 else "skills"
    return f"Installed {len(plan.skills)} bundled {skill_label} into {target_count} {target_label} ({copied_count} copies total)."


def _ensure_valid_targets(plan: InstallPlan) -> None:
    if not plan.targets:
        raise InstallerError("No install targets selected.")
    for skill in plan.skills:
        skill_md = skill.source.joinpath("SKILL.md")
        if not skill.source.is_dir() or not skill_md.is_file():
            raise InstallerError(f"Bundled skill '{skill.name}' is missing SKILL.md.")
    for target in plan.targets:
        if target.destination.exists() and not target.destination.is_dir():
            raise InstallerError(f"Destination path is a file: {target.destination}")
        for ancestor in target.destination.parents:
            if ancestor.exists() and not ancestor.is_dir():
                raise InstallerError(f"Destination path has a file in its parent chain: {ancestor}")
        for skill in plan.skills:
            skill_destination = target.destination / skill.name
            if skill_destination.exists() and not skill_destination.is_dir():
                raise InstallerError(
                    f"Destination path collides with an existing file: {skill_destination}"
                )


def _copy_traversable_tree(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        child_destination = destination / child.name
        try:
            if child.is_dir():
                _copy_traversable_tree(child, child_destination)
                continue
            child_destination.parent.mkdir(parents=True, exist_ok=True)
            with child.open("rb") as source_file, child_destination.open("wb") as destination_file:
                shutil.copyfileobj(source_file, destination_file)
        except OSError as exc:
            raise InstallerError(f"Failed to copy '{child_destination}': {exc}") from exc


def _copy_plan(plan: InstallPlan) -> int:
    copied = 0
    for target in plan.targets:
        target.destination.mkdir(parents=True, exist_ok=True)
    for target in plan.targets:
        for skill in plan.skills:
            try:
                _copy_traversable_tree(skill.source, target.destination / skill.name)
            except InstallerError as exc:
                raise InstallerError(f"Failed while copying into {target.destination}: {exc}") from exc
            copied += 1
    return copied


def install_skills(
    destination: str | Path | None = None,
    *,
    yes: bool = False,
    stdin=None,
    input_fn: Callable[[str], str] = input,
    stdout=None,
) -> int:
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout
    plan = build_install_plan(destination)
    _ensure_valid_targets(plan)
    print(render_preview(plan), file=stdout)
    if not yes:
        if not stdin.isatty():
            raise InstallerError("stdin is not interactive, rerun with --yes.")
        response = input_fn("Proceed with installation? [y/N]: ").strip().lower()
        if response not in {"y", "yes"}:
            print("Installation cancelled.", file=stdout)
            return 0
    copied_count = _copy_plan(plan)
    print(_format_success_summary(plan, copied_count), file=stdout)
    return copied_count
