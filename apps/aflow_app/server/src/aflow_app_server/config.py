"""Server configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]


@dataclass(frozen=True)
class ServerConfig:
    """Configuration for the remote app server."""

    bind_host: str
    bind_port: int
    auth_token: str
    repo_registry_path: Path
    codex_server_url: str | None
    codex_server_token: str | None
    transcription_url: str | None
    transcription_token: str | None

    @classmethod
    def from_env(cls) -> ServerConfig:
        """Load configuration from environment variables and config file."""
        config_dir = Path(os.environ.get("AFLOW_APP_CONFIG_DIR", "~/.config/aflow-app")).expanduser()
        config_file = config_dir / "config.toml"

        # Load from file if exists
        file_config: dict[str, Any] = {}
        if config_file.exists():
            with open(config_file, "rb") as f:
                file_config = tomllib.load(f)

        server_section = file_config.get("server", {})
        codex_section = file_config.get("codex", {})
        transcription_section = file_config.get("transcription", {})

        # Environment overrides file config
        return cls(
            bind_host=os.environ.get("AFLOW_APP_HOST", server_section.get("bind_host", "127.0.0.1")),
            bind_port=int(os.environ.get("AFLOW_APP_PORT", server_section.get("bind_port", 8765))),
            auth_token=os.environ.get("AFLOW_APP_TOKEN", server_section.get("auth_token", "")),
            repo_registry_path=Path(os.environ.get(
                "AFLOW_APP_REGISTRY_PATH",
                server_section.get("repo_registry_path", str(config_dir / "repos.json"))
            )).expanduser(),
            codex_server_url=os.environ.get("AFLOW_CODEX_URL", codex_section.get("server_url")),
            codex_server_token=os.environ.get("AFLOW_CODEX_TOKEN", codex_section.get("server_token")),
            transcription_url=os.environ.get("AFLOW_TRANSCRIPTION_URL", transcription_section.get("server_url")),
            transcription_token=os.environ.get("AFLOW_TRANSCRIPTION_TOKEN", transcription_section.get("server_token")),
        )

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors: list[str] = []
        if not self.auth_token:
            errors.append("auth_token is required (set AFLOW_APP_TOKEN or server.auth_token in config)")
        if self.bind_port < 1 or self.bind_port > 65535:
            errors.append(f"invalid bind_port: {self.bind_port}")
        return errors