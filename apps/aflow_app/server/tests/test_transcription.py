"""Tests for transcription functionality."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from aflow_app_server.transcription import (
    OpenAICompatibleTranscriptionClient,
    TranscriptionError,
    create_transcription_client,
)


@pytest.fixture
def mock_audio_file():
    """Create a temporary audio file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(b"fake audio data")
        temp_path = Path(f.name)

    yield temp_path

    temp_path.unlink(missing_ok=True)


async def test_transcribe_success(mock_audio_file):
    """Test successful transcription."""
    client = OpenAICompatibleTranscriptionClient(
        server_url="https://api.example.com",
        auth_token="test-token",
    )

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "Hello world"}

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        result = await client.transcribe(mock_audio_file)

    assert result == "Hello world"
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "https://api.example.com/v1/audio/transcriptions"
    assert "Authorization" in call_args[1]["headers"]
    assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"


async def test_transcribe_without_auth(mock_audio_file):
    """Test transcription without authentication token."""
    client = OpenAICompatibleTranscriptionClient(
        server_url="https://api.example.com",
        auth_token=None,
    )

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"text": "No auth needed"}

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        result = await client.transcribe(mock_audio_file)

    assert result == "No auth needed"
    call_args = mock_client.post.call_args
    assert "Authorization" not in call_args[1]["headers"]


async def test_transcribe_file_not_found():
    """Test transcription with non-existent file."""
    client = OpenAICompatibleTranscriptionClient(
        server_url="https://api.example.com",
    )

    with pytest.raises(TranscriptionError, match="Audio file not found"):
        await client.transcribe(Path("/nonexistent/file.webm"))


async def test_transcribe_server_error(mock_audio_file):
    """Test transcription with server error."""
    client = OpenAICompatibleTranscriptionClient(
        server_url="https://api.example.com",
    )

    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.text = "Internal server error"
    mock_response.json.side_effect = Exception("Not JSON")

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        with pytest.raises(TranscriptionError, match="Transcription failed with status 500"):
            await client.transcribe(mock_audio_file)


async def test_transcribe_timeout(mock_audio_file):
    """Test transcription with timeout."""
    client = OpenAICompatibleTranscriptionClient(
        server_url="https://api.example.com",
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("Request timed out")
        mock_client_class.return_value = mock_client

        with pytest.raises(TranscriptionError, match="Transcription request timed out"):
            await client.transcribe(mock_audio_file)


async def test_transcribe_request_error(mock_audio_file):
    """Test transcription with request error."""
    client = OpenAICompatibleTranscriptionClient(
        server_url="https://api.example.com",
    )

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.RequestError("Connection failed")
        mock_client_class.return_value = mock_client

        with pytest.raises(TranscriptionError, match="Transcription request failed"):
            await client.transcribe(mock_audio_file)


def test_create_transcription_client_with_config():
    """Test creating transcription client with configuration."""
    client = create_transcription_client(
        server_url="https://api.example.com",
        auth_token="test-token",
    )

    assert client is not None
    assert isinstance(client, OpenAICompatibleTranscriptionClient)
    assert client.server_url == "https://api.example.com"
    assert client.auth_token == "test-token"


def test_create_transcription_client_without_config():
    """Test creating transcription client without configuration."""
    client = create_transcription_client(server_url=None)

    assert client is None


def test_create_transcription_client_strips_trailing_slash():
    """Test that server URL trailing slash is stripped."""
    client = create_transcription_client(
        server_url="https://api.example.com/",
    )

    assert client is not None
    assert client.server_url == "https://api.example.com"
