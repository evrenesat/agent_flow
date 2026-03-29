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
        effort: str | None = None,
    ) -> HarnessInvocation:
        argv: list[str] = [
            "pi",
            "--print",
            "--system-prompt",
            system_prompt,
        ]
        if effort is not None:
            argv.extend(["--models", f"{model}:{effort}"])
        else:
            argv.extend(["--model", model])
        argv.extend(["--tools", "read,bash,edit,write,grep,find,ls"])
        argv.append(user_prompt)
        return HarnessInvocation(
            label=self.name,
            argv=tuple(argv),
            env={},
            prompt_mode="system-prompt-flag",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            effective_prompt=user_prompt,
        )
