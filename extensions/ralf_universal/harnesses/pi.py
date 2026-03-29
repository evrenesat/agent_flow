from __future__ import annotations

from pathlib import Path

from .base import HarnessInvocation


class PiAdapter:
    name = "pi"

    def build_invocation(
        self,
        *,
        repo_root: Path,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> HarnessInvocation:
        argv = (
            "pi",
            "--print",
            "--system-prompt",
            system_prompt,
            "--model",
            model,
            "--tools",
            "read,bash,edit,write,grep,find,ls",
            user_prompt,
        )
        return HarnessInvocation(
            label=self.name,
            argv=argv,
            env={},
            prompt_mode="system-prompt-flag",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            effective_prompt=user_prompt,
        )

