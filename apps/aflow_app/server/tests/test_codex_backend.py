"""Tests for Codex backend adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock, patch

import httpx
import pytest

from aflow_app_server.codex_backend import (
    CodexBackendError,
    CodexMessage,
    CodexSession,
    HttpCodexBackend,
)


@pytest.fixture
def mock_httpx_client():
    """Mock httpx client for testing."""
    with patch("aflow_app_server.codex_backend.httpx.Client") as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        yield mock_client


def test_list_sessions_success(mock_httpx_client):
    """Test listing sessions successfully."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "sessions": [
            {
                "id": "session-1",
                "name": "Test Session",
                "repo_path": "/path/to/repo",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
                "message_count": 5,
            }
        ]
    }
    mock_httpx_client.get.return_value = mock_response

    backend = HttpCodexBackend("http://localhost:8000", "test-token")
    sessions = backend.list_sessions()

    assert len(sessions) == 1
    assert sessions[0].id == "session-1"
    assert sessions[0].name == "Test Session"
    assert sessions[0].repo_path == "/path/to/repo"
    assert sessions[0].message_count == 5


def test_list_sessions_with_repo_filter(mock_httpx_client):
    """Test listing sessions with repo path filter."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"sessions": []}
    mock_httpx_client.get.return_value = mock_response

    backend = HttpCodexBackend("http://localhost:8000")
    backend.list_sessions(repo_path="/path/to/repo")

    mock_httpx_client.get.assert_called_once()
    call_args = mock_httpx_client.get.call_args
    assert call_args.kwargs["params"] == {"repo_path": "/path/to/repo"}


def test_list_sessions_http_error(mock_httpx_client):
    """Test listing sessions with HTTP error."""
    mock_httpx_client.get.side_effect = httpx.HTTPError("Connection failed")

    backend = HttpCodexBackend("http://localhost:8000")

    with pytest.raises(CodexBackendError, match="Failed to list sessions"):
        backend.list_sessions()


def test_get_session_success(mock_httpx_client):
    """Test getting a specific session."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "session-1",
        "name": "Test Session",
        "repo_path": None,
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "message_count": 3,
    }
    mock_httpx_client.get.return_value = mock_response

    backend = HttpCodexBackend("http://localhost:8000")
    session = backend.get_session("session-1")

    assert session is not None
    assert session.id == "session-1"
    assert session.name == "Test Session"
    assert session.repo_path is None


def test_get_session_not_found(mock_httpx_client):
    """Test getting a non-existent session."""
    mock_response = Mock()
    mock_response.status_code = 404
    mock_httpx_client.get.return_value = mock_response

    backend = HttpCodexBackend("http://localhost:8000")
    session = backend.get_session("nonexistent")

    assert session is None


def test_fetch_messages_success(mock_httpx_client):
    """Test fetching messages from a session."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messages": [
            {
                "id": "msg-1",
                "role": "user",
                "content": "Hello",
                "timestamp": "2024-01-01T00:00:00Z",
            },
            {
                "id": "msg-2",
                "role": "assistant",
                "content": "Hi there!",
                "timestamp": "2024-01-01T00:00:01Z",
            },
        ]
    }
    mock_httpx_client.get.return_value = mock_response

    backend = HttpCodexBackend("http://localhost:8000")
    messages = backend.fetch_messages("session-1")

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Hello"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hi there!"


def test_fetch_messages_with_limit(mock_httpx_client):
    """Test fetching messages with a limit."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"messages": []}
    mock_httpx_client.get.return_value = mock_response

    backend = HttpCodexBackend("http://localhost:8000")
    backend.fetch_messages("session-1", limit=10)

    mock_httpx_client.get.assert_called_once()
    call_args = mock_httpx_client.get.call_args
    assert call_args.kwargs["params"] == {"limit": 10}


def test_send_message_success(mock_httpx_client):
    """Test sending a message to a session."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": {
            "id": "msg-3",
            "role": "assistant",
            "content": "Response text",
            "timestamp": "2024-01-01T00:00:02Z",
        }
    }
    mock_httpx_client.post.return_value = mock_response

    backend = HttpCodexBackend("http://localhost:8000")
    response = backend.send_message("session-1", "Test message")

    assert response.role == "assistant"
    assert response.content == "Response text"
    mock_httpx_client.post.assert_called_once()


def test_send_message_http_error(mock_httpx_client):
    """Test sending a message with HTTP error."""
    mock_httpx_client.post.side_effect = httpx.HTTPError("Connection failed")

    backend = HttpCodexBackend("http://localhost:8000")

    with pytest.raises(CodexBackendError, match="Failed to send message"):
        backend.send_message("session-1", "Test message")


def test_auth_headers():
    """Test that auth headers are included when token is provided."""
    with patch("aflow_app_server.codex_backend.httpx.Client") as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"sessions": []}
        mock_client.get.return_value = mock_response

        backend = HttpCodexBackend("http://localhost:8000", "secret-token")
        backend.list_sessions()

        call_args = mock_client.get.call_args
        headers = call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret-token"


def test_no_auth_headers():
    """Test that no auth headers are included when token is not provided."""
    with patch("aflow_app_server.codex_backend.httpx.Client") as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"sessions": []}
        mock_client.get.return_value = mock_response

        backend = HttpCodexBackend("http://localhost:8000")
        backend.list_sessions()

        call_args = mock_client.get.call_args
        headers = call_args.kwargs["headers"]
        assert "Authorization" not in headers
