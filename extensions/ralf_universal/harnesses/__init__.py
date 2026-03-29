from __future__ import annotations

from .base import HarnessAdapter, HarnessInvocation
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .pi import PiAdapter


ADAPTERS: dict[str, HarnessAdapter] = {
    "codex": CodexAdapter(),
    "pi": PiAdapter(),
    "claude": ClaudeAdapter(),
}


def get_adapter(name: str) -> HarnessAdapter:
    try:
        return ADAPTERS[name]
    except KeyError as exc:
        raise KeyError(f"unsupported harness '{name}'") from exc

