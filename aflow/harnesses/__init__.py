from __future__ import annotations

from .base import HarnessAdapter, HarnessInvocation
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .gemini import GeminiAdapter
from .opencode import OpencodeAdapter
from .pi import PiAdapter


ADAPTERS: dict[str, HarnessAdapter] = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "gemini": GeminiAdapter(),
    "opencode": OpencodeAdapter(),
    "pi": PiAdapter(),
}


def get_adapter(name: str) -> HarnessAdapter:
    try:
        return ADAPTERS[name]
    except KeyError as exc:
        raise KeyError(f"unsupported harness '{name}'") from exc
