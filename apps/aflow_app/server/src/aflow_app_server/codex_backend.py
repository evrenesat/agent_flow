"""Codex server adapter for session reuse and messaging."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


class CodexBackendError(Exception):
    """Error in Codex backend operations."""
    pass


@dataclass
class CodexMessage:
    """A message in a Codex session."""

    id: str
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime


@dataclass
class CodexSession:
    """A Codex session."""

    id: str
    name: str
    repo_path: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int


class CodexBackend(ABC):
    """Abstract interface for Codex server adapters."""

    @abstractmethod
    def list_sessions(self, repo_path: str | None = None) -> list[CodexSession]:
        """List available Codex sessions.

        Args:
            repo_path: Optional filter by repository path.

        Returns:
            List of available sessions.
        """
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> CodexSession | None:
        """Get a specific session.

        Args:
            session_id: The session ID.

        Returns:
            Session info or None if not found.
        """
        pass

    @abstractmethod
    def fetch_messages(self, session_id: str, limit: int | None = None) -> list[CodexMessage]:
        """Fetch messages from a session.

        Args:
            session_id: The session ID.
            limit: Optional limit on number of messages to fetch.

        Returns:
            List of messages in chronological order.
        """
        pass

    @abstractmethod
    def send_message(self, session_id: str, content: str) -> CodexMessage:
        """Send a message to a session and get the assistant response.

        Args:
            session_id: The session ID.
            content: The message content to send.

        Returns:
            The assistant's response message.
        """
        pass


class HttpCodexBackend(CodexBackend):
    """HTTP-based Codex server adapter."""

    def __init__(self, server_url: str, auth_token: str | None = None) -> None:
        """Initialize the HTTP adapter.

        Args:
            server_url: Base URL of the Codex server.
            auth_token: Optional authentication token.
        """
        self.server_url = server_url.rstrip("/")
        self.auth_token = auth_token
        self._client = httpx.Client(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        """Get request headers with auth if configured."""
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def list_sessions(self, repo_path: str | None = None) -> list[CodexSession]:
        """List available Codex sessions."""
        try:
            params = {}
            if repo_path:
                params["repo_path"] = repo_path

            response = self._client.get(
                f"{self.server_url}/api/sessions",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            sessions = []
            for item in data.get("sessions", []):
                sessions.append(self._parse_session(item))

            return sessions

        except httpx.HTTPError as e:
            raise CodexBackendError(f"Failed to list sessions: {e}")

    def get_session(self, session_id: str) -> CodexSession | None:
        """Get a specific session."""
        try:
            response = self._client.get(
                f"{self.server_url}/api/sessions/{session_id}",
                headers=self._headers(),
            )

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()
            return self._parse_session(data)

        except httpx.HTTPError as e:
            raise CodexBackendError(f"Failed to get session: {e}")

    def fetch_messages(self, session_id: str, limit: int | None = None) -> list[CodexMessage]:
        """Fetch messages from a session."""
        try:
            params = {}
            if limit:
                params["limit"] = limit

            response = self._client.get(
                f"{self.server_url}/api/sessions/{session_id}/messages",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            messages = []
            for item in data.get("messages", []):
                messages.append(self._parse_message(item))

            return messages

        except httpx.HTTPError as e:
            raise CodexBackendError(f"Failed to fetch messages: {e}")

    def send_message(self, session_id: str, content: str) -> CodexMessage:
        """Send a message to a session and get the assistant response."""
        try:
            response = self._client.post(
                f"{self.server_url}/api/sessions/{session_id}/messages",
                headers=self._headers(),
                json={"content": content},
            )
            response.raise_for_status()
            data = response.json()

            # Return the assistant's response
            return self._parse_message(data.get("response", data))

        except httpx.HTTPError as e:
            raise CodexBackendError(f"Failed to send message: {e}")

    def _parse_session(self, data: dict[str, Any]) -> CodexSession:
        """Parse session data from API response."""
        return CodexSession(
            id=data["id"],
            name=data.get("name", data["id"]),
            repo_path=data.get("repo_path"),
            created_at=self._parse_timestamp(data.get("created_at")),
            updated_at=self._parse_timestamp(data.get("updated_at")),
            message_count=data.get("message_count", 0),
        )

    def _parse_message(self, data: dict[str, Any]) -> CodexMessage:
        """Parse message data from API response."""
        return CodexMessage(
            id=data.get("id", ""),
            role=data.get("role", "assistant"),
            content=data.get("content", ""),
            timestamp=self._parse_timestamp(data.get("timestamp")),
        )

    def _parse_timestamp(self, value: str | None) -> datetime:
        """Parse timestamp from API response."""
        if not value:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return datetime.now(timezone.utc)

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
