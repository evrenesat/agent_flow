from __future__ import annotations

from pathlib import Path

from .base import HarnessInvocation


class CodexAdapter:
    name = "codex"

    def build_invocation(
        self,
        *,
        repo_root: Path,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> HarnessInvocation:
        effective_prompt = "\n\n".join((system_prompt, user_prompt))
        argv = (
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(repo_root),
            "--model",
            model,
            effective_prompt,
        )
        return HarnessInvocation(
            label=self.name,
            argv=argv,
            env={},
            prompt_mode="prefix-system-into-user-prompt",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            effective_prompt=effective_prompt,
        )

