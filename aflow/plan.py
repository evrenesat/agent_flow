from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from pathlib import Path


SECTION_RE = re.compile(r"^###\s+\[([ xX])\]\s+(Checkpoint\b.*)$")
STEP_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+")
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
GIT_TRACKING_RE = re.compile(r"^##\s+Git Tracking\b")


@dataclass(frozen=True)
class CheckpointSection:
    line_number: int
    name: str
    heading_checked: bool
    unchecked_step_count: int


@dataclass(frozen=True)
class PlanSnapshot:
    current_checkpoint_name: str | None
    unchecked_checkpoint_count: int
    current_checkpoint_unchecked_step_count: int
    is_complete: bool
    total_checkpoint_count: int = 0
    current_checkpoint_index: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedPlan:
    path: Path
    sections: tuple[CheckpointSection, ...]
    snapshot: PlanSnapshot


class PlanParseError(ValueError):
    pass


def _build_error(path: Path, message: str) -> PlanParseError:
    return PlanParseError(f"{path}: {message}")


def plan_has_git_tracking(text: str) -> bool:
    """Return True when the text contains a ## Git Tracking heading outside fenced blocks."""
    in_fence = False
    fence_char: str | None = None
    fence_len = 0
    for line in text.splitlines():
        fence_match = FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
                fence_char = None
                fence_len = 0
            continue
        if in_fence:
            continue
        if GIT_TRACKING_RE.match(line):
            return True
    return False


def parse_plan_text(text: str, *, source_path: Path) -> ParsedPlan:
    sections: list[CheckpointSection] = []
    current_section: dict[str, object] | None = None
    in_fence = False
    fence_char: str | None = None
    fence_len = 0

    for line_number, line in enumerate(text.splitlines(), start=1):
        fence_match = FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len:
                in_fence = False
                fence_char = None
                fence_len = 0
            continue

        if in_fence:
            continue

        section_match = SECTION_RE.match(line)
        if section_match:
            if current_section is not None:
                sections.append(
                    CheckpointSection(
                        line_number=int(current_section["line_number"]),
                        name=str(current_section["name"]),
                        heading_checked=bool(current_section["heading_checked"]),
                        unchecked_step_count=int(current_section["unchecked_step_count"]),
                    )
                )
            current_section = {
                "line_number": line_number,
                "name": section_match.group(2).strip(),
                "heading_checked": section_match.group(1).lower() == "x",
                "unchecked_step_count": 0,
            }
            continue

        if current_section is None:
            continue

        step_match = STEP_RE.match(line)
        if step_match and step_match.group(1).lower() == " ":
            current_section["unchecked_step_count"] = int(current_section["unchecked_step_count"]) + 1

    if current_section is not None:
        sections.append(
            CheckpointSection(
                line_number=int(current_section["line_number"]),
                name=str(current_section["name"]),
                heading_checked=bool(current_section["heading_checked"]),
                unchecked_step_count=int(current_section["unchecked_step_count"]),
            )
        )

    if not sections:
        raise _build_error(source_path, "no checkpoint sections were found")

    for section in sections:
        if section.heading_checked and section.unchecked_step_count > 0:
            raise _build_error(
                source_path,
                f"inconsistent checkpoint state at line {section.line_number}: "
                f"'{section.name}' is marked complete but still has "
                f"{section.unchecked_step_count} unchecked step(s)",
            )

    unchecked_checkpoint_count = sum(not section.heading_checked for section in sections)
    total_checkpoint_count = len(sections)
    current_checkpoint = next((section for section in sections if not section.heading_checked), None)

    if current_checkpoint is None:
        snapshot = PlanSnapshot(
            current_checkpoint_name=None,
            unchecked_checkpoint_count=0,
            current_checkpoint_unchecked_step_count=0,
            is_complete=True,
            total_checkpoint_count=total_checkpoint_count,
            current_checkpoint_index=None,
        )
    else:
        current_checkpoint_index = sections.index(current_checkpoint) + 1
        snapshot = PlanSnapshot(
            current_checkpoint_name=current_checkpoint.name,
            unchecked_checkpoint_count=unchecked_checkpoint_count,
            current_checkpoint_unchecked_step_count=current_checkpoint.unchecked_step_count,
            is_complete=False,
            total_checkpoint_count=total_checkpoint_count,
            current_checkpoint_index=current_checkpoint_index,
        )

    return ParsedPlan(path=source_path, sections=tuple(sections), snapshot=snapshot)


def load_plan(path: Path) -> ParsedPlan:
    if not path.is_file():
        raise _build_error(path, "plan file does not exist")
    return parse_plan_text(path.read_text(encoding="utf-8"), source_path=path)
