from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class HarnessInvocation:
    label: str
    argv: tuple[str, ...]
    env: dict[str, str]
    prompt_mode: str
    system_prompt: str
    user_prompt: str
    effective_prompt: str


class HarnessAdapter(Protocol):
    name: str
    supports_effort: bool

    def build_invocation(
        self,
        *,
        repo_root: Path,
        model: str,
        system_prompt: str,
        user_prompt: str,
        effort: str | None = None,
    ) -> HarnessInvocation:
        ...
