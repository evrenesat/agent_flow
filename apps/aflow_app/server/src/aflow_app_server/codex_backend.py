"""Compatibility exports for the Codex thread gateway."""

from __future__ import annotations

from .codex_app_server_client import CodexAppServerClient
from .codex_thread_gateway import CodexThreadGateway, CodexThreadGatewayError, CodexThreadPage

CodexBackend = CodexThreadGateway
HttpCodexBackend = CodexAppServerClient

