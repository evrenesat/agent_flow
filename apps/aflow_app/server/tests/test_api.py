"""Tests for the API endpoints."""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from aflow_app_server.config import ServerConfig
from aflow_app_server.main import AccessLogPathFilter, app
from aflow_app_server.repo_registry import RepoRegistry
from aflow_app_server.transcription import TranscriptionError


@pytest.fixture
def test_token() -> str:
    """Test auth token."""
    return "test-token-12345"


@pytest.fixture
def test_config(tmp_path: Path, test_token: str) -> ServerConfig:
    """Create a test configuration."""
    return ServerConfig(
        bind_host="127.0.0.1",
        bind_port=8765,
        auth_token=test_token,
        repo_registry_path=tmp_path / "repos.json",
        codex_server_url=None,
        codex_server_token=None,
        transcription_url=None,
        transcription_token=None,
    )


@pytest.fixture
def client_with_config(test_config: ServerConfig, test_token: str) -> TestClient:
    """Create a test client with proper config injection."""
    from aflow_app_server import main as main_module
    from aflow_app_server.aflow_service import AflowService

    # Set up the global state
    main_module._config = test_config
    main_module._registry = RepoRegistry(test_config.repo_registry_path)
    main_module._service = AflowService()

    try:
        client = TestClient(app, raise_server_exceptions=True)
        client.headers["Authorization"] = f"Bearer {test_token}"
        yield client
    finally:
        main_module._config = None
        main_module._registry = None
        main_module._service = None


class TestHealthEndpoint:
    """Tests for the health endpoint."""

    def test_health_check_no_auth(self, test_config: ServerConfig) -> None:
        """Test that health check works without auth."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        main_module._config = test_config
        main_module._registry = RepoRegistry(test_config.repo_registry_path)
        main_module._service = AflowService()

        try:
            client = TestClient(app)
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None


class TestAuth:
    """Tests for authentication."""

    def test_missing_auth_header(self, test_config: ServerConfig) -> None:
        """Test that requests without auth are rejected."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        main_module._config = test_config
        main_module._registry = RepoRegistry(test_config.repo_registry_path)
        main_module._service = AflowService()

        try:
            client = TestClient(app)
            response = client.get("/api/repos")
            # FastAPI returns 401 for missing auth, not 403
            assert response.status_code == 401
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None

    def test_invalid_token(self, test_config: ServerConfig, test_token: str) -> None:
        """Test that invalid tokens are rejected."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        main_module._config = test_config
        main_module._registry = RepoRegistry(test_config.repo_registry_path)
        main_module._service = AflowService()

        try:
            client = TestClient(app)
            client.headers["Authorization"] = "Bearer wrong-token"
            response = client.get("/api/repos")
            assert response.status_code == 401
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None

    def test_query_token_is_accepted(self, test_config: ServerConfig, test_token: str) -> None:
        """Test that token query param works for clients like EventSource."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        main_module._config = test_config
        main_module._registry = RepoRegistry(test_config.repo_registry_path)
        main_module._service = AflowService()

        try:
            client = TestClient(app)
            response = client.get(f"/api/repos?token={test_token}")
            assert response.status_code == 200
            assert response.json() == []
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None


class TestLogging:
    """Tests for access log suppression."""

    def test_plugin_probe_access_log_is_suppressed(self) -> None:
        """Suppress noisy local plugin probe requests."""
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:1234", "POST", "/api/plugin/events", "1.1", 405),
            exc_info=None,
        )
        assert AccessLogPathFilter().filter(record) is False

    def test_normal_access_log_is_kept(self) -> None:
        """Keep normal access logs visible."""
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:1234", "GET", "/health", "1.1", 200),
            exc_info=None,
        )
        assert AccessLogPathFilter().filter(record) is True

    def test_plugin_probe_is_intercepted_quietly(
        self,
        test_config: ServerConfig,
        tmp_path: Path,
    ) -> None:
        """Short-circuit known localhost plugin probes."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        main_module._config = test_config
        main_module._registry = RepoRegistry(test_config.repo_registry_path)
        main_module._service = AflowService()
        main_module._seen_plugin_probe_fingerprints.clear()

        try:
            client = TestClient(app)
            response = client.post(
                "/api/plugin/events",
                headers={"User-Agent": "suspicious-local-plugin/1.0"},
                json={"hello": "world"},
            )
            assert response.status_code == 204
            assert response.text == ""
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None
            main_module._seen_plugin_probe_fingerprints.clear()

    def test_plugin_probe_logging_can_be_enabled_once(
        self,
        test_config: ServerConfig,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Log one fingerprint once when debug logging is enabled."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        monkeypatch.setenv("AFLOW_APP_LOG_PLUGIN_PROBES", "1")
        main_module._config = test_config
        main_module._registry = RepoRegistry(test_config.repo_registry_path)
        main_module._service = AflowService()
        main_module._seen_plugin_probe_fingerprints.clear()

        try:
            client = TestClient(app)
            with caplog.at_level(logging.WARNING, logger="aflow_app_server.plugin_probe"):
                response1 = client.post(
                    "/api/plugin/events",
                    headers={"User-Agent": "suspicious-local-plugin/1.0"},
                    content=b"abc123",
                )
                response2 = client.post(
                    "/api/plugin/events",
                    headers={"User-Agent": "suspicious-local-plugin/1.0"},
                    content=b"abc123",
                )

            assert response1.status_code == 204
            assert response2.status_code == 204
            matching = [
                record.message for record in caplog.records
                if "Blocked localhost probe:" in record.message
            ]
            assert len(matching) == 1
            assert "suspicious-local-plugin/1.0" in matching[0]
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None
            main_module._seen_plugin_probe_fingerprints.clear()


class TestWebAppServing:
    """Tests for serving the built frontend."""

    def test_root_serves_built_index(self, test_config: ServerConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that / serves the built frontend when dist exists."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<!doctype html><html><body>aflow ui</body></html>")

        monkeypatch.setenv("AFLOW_APP_WEB_DIST", str(dist_dir))
        main_module._config = test_config
        main_module._registry = RepoRegistry(test_config.repo_registry_path)
        main_module._service = AflowService()

        try:
            client = TestClient(app)
            response = client.get("/")
            assert response.status_code == 200
            assert "aflow ui" in response.text
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None

    def test_unknown_frontend_route_falls_back_to_index(
        self,
        test_config: ServerConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that SPA routes fall back to index.html."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()
        (dist_dir / "index.html").write_text("<!doctype html><html><body>spa shell</body></html>")

        monkeypatch.setenv("AFLOW_APP_WEB_DIST", str(dist_dir))
        main_module._config = test_config
        main_module._registry = RepoRegistry(test_config.repo_registry_path)
        main_module._service = AflowService()

        try:
            client = TestClient(app)
            response = client.get("/plans/demo")
            assert response.status_code == 200
            assert "spa shell" in response.text
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None


class TestRepoEndpoints:
    """Tests for repository endpoints."""

    def test_list_repos_empty(self, client_with_config: TestClient) -> None:
        """Test listing repos when empty."""
        response = client_with_config.get("/api/repos")
        assert response.status_code == 200
        assert response.json() == []

    def test_add_repo(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test adding a repository."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path), "name": "test-repo"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-repo"
        assert data["is_git_root"] is True

    def test_add_repo_nonexistent(self, client_with_config: TestClient) -> None:
        """Test adding a non-existent repo."""
        response = client_with_config.post(
            "/api/repos",
            json={"path": "/nonexistent/path", "name": "test"},
        )
        assert response.status_code == 400

    def test_get_repo(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test getting a specific repo."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        response = client_with_config.get(f"/api/repos/{repo_id}")
        assert response.status_code == 200
        assert response.json()["id"] == repo_id

    def test_get_repo_not_found(self, client_with_config: TestClient) -> None:
        """Test getting a non-existent repo."""
        response = client_with_config.get("/api/repos/nonexistent")
        assert response.status_code == 404

    def test_update_repo(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test updating a repo."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        response = client_with_config.patch(
            f"/api/repos/{repo_id}",
            json={"name": "updated-name"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "updated-name"

    def test_delete_repo(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test deleting a repo."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        response = client_with_config.delete(f"/api/repos/{repo_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_response = client_with_config.get(f"/api/repos/{repo_id}")
        assert get_response.status_code == 404


class TestPlanEndpoints:
    """Tests for plan endpoints."""

    def test_list_plans_empty(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test listing plans when none exist."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        response = client_with_config.get(f"/api/repos/{repo_id}/plans")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_plans_with_drafts(
        self, client_with_config: TestClient, tmp_path: Path
    ) -> None:
        """Test listing plans with draft plans."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        # Create a draft plan
        drafts_dir = repo_path / "plans" / "drafts"
        drafts_dir.mkdir(parents=True)
        plan_content = """# Test Plan

## Summary
Test plan for testing.

### [ ] Checkpoint 1: First checkpoint

- [ ] Step 1
- [ ] Step 2
"""
        (drafts_dir / "test-plan.md").write_text(plan_content)

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        response = client_with_config.get(f"/api/repos/{repo_id}/plans")
        assert response.status_code == 200
        plans = response.json()
        assert len(plans) == 1
        assert plans[0]["name"] == "test-plan"
        assert plans[0]["status"] == "draft"

    def test_list_plans_repo_not_found(self, client_with_config: TestClient) -> None:
        """Test listing plans for non-existent repo."""
        response = client_with_config.get("/api/repos/nonexistent/plans")
        assert response.status_code == 404


class TestExecutionEndpoints:
    """Tests for execution endpoints."""

    def test_start_execution_repo_not_found(self, client_with_config: TestClient) -> None:
        """Test starting execution for non-existent repo."""
        response = client_with_config.post(
            "/api/executions",
            json={
                "repo_id": "nonexistent",
                "plan_path": "test.md",
            },
        )
        assert response.status_code == 404

    def test_start_execution_plan_not_found(
        self, client_with_config: TestClient, tmp_path: Path
    ) -> None:
        """Test starting execution with non-existent plan."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        response = client_with_config.post(
            "/api/executions",
            json={
                "repo_id": repo_id,
                "plan_path": "nonexistent.md",
            },
        )
        assert response.status_code == 400

    def test_get_execution_not_found(self, client_with_config: TestClient) -> None:
        """Test getting non-existent execution."""
        response = client_with_config.get("/api/executions/nonexistent")
        assert response.status_code == 404



class TestCodexEndpoints:
    """Tests for Codex session endpoints."""

    def test_list_sessions_not_configured(self, client_with_config: TestClient) -> None:
        """Test listing sessions when Codex is not configured."""
        response = client_with_config.get("/api/codex/sessions")
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]

    def test_list_sessions_with_mock_backend(
        self, client_with_config: TestClient, test_config: ServerConfig
    ) -> None:
        """Test listing sessions with mocked backend."""
        from aflow_app_server import main as main_module
        from aflow_app_server.codex_backend import CodexSession
        from datetime import datetime, timezone

        # Update config to include Codex URL
        main_module._config = ServerConfig(
            bind_host=test_config.bind_host,
            bind_port=test_config.bind_port,
            auth_token=test_config.auth_token,
            repo_registry_path=test_config.repo_registry_path,
            codex_server_url="http://localhost:9000",
            codex_server_token="test-codex-token",
            transcription_url=None,
            transcription_token=None,
        )

        mock_session = CodexSession(
            id="session-1",
            name="Test Session",
            repo_path="/path/to/repo",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            message_count=5,
        )

        with patch("aflow_app_server.codex_routes.HttpCodexBackend") as mock_backend_class:
            mock_backend = MagicMock()
            mock_backend.list_sessions.return_value = [mock_session]
            mock_backend_class.return_value = mock_backend

            response = client_with_config.get("/api/codex/sessions")
            assert response.status_code == 200
            sessions = response.json()
            assert len(sessions) == 1
            assert sessions[0]["id"] == "session-1"


class TestPlanDraftEndpoints:
    """Tests for plan draft management endpoints."""

    def test_save_draft(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test saving a draft plan."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        content = "# Test Plan\n\nThis is a test plan."
        response = client_with_config.post(
            f"/api/codex/repos/{repo_id}/plans/drafts",
            json={"name": "test-plan", "content": content},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-plan"
        assert data["status"] == "draft"

        # Verify file was created
        draft_path = repo_path / "plans" / "drafts" / "test-plan.md"
        assert draft_path.exists()
        assert draft_path.read_text() == content

    def test_list_drafts(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test listing draft plans."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        # Save some drafts
        client_with_config.post(
            f"/api/codex/repos/{repo_id}/plans/drafts",
            json={"name": "plan-a", "content": "Content A"},
        )
        client_with_config.post(
            f"/api/codex/repos/{repo_id}/plans/drafts",
            json={"name": "plan-b", "content": "Content B"},
        )

        response = client_with_config.get(f"/api/codex/repos/{repo_id}/plans/drafts")
        assert response.status_code == 200
        drafts = response.json()
        assert drafts == ["plan-a", "plan-b"]

    def test_load_draft(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test loading a draft plan."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        content = "# Test Plan\n\nContent here."
        client_with_config.post(
            f"/api/codex/repos/{repo_id}/plans/drafts",
            json={"name": "test-plan", "content": content},
        )

        response = client_with_config.get(
            f"/api/codex/repos/{repo_id}/plans/drafts/test-plan"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-plan"
        assert data["content"] == content

    def test_delete_draft(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test deleting a draft plan."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        client_with_config.post(
            f"/api/codex/repos/{repo_id}/plans/drafts",
            json={"name": "test-plan", "content": "Content"},
        )

        response = client_with_config.delete(
            f"/api/codex/repos/{repo_id}/plans/drafts/test-plan"
        )
        assert response.status_code == 204

        # Verify it's gone
        list_response = client_with_config.get(
            f"/api/codex/repos/{repo_id}/plans/drafts"
        )
        assert "test-plan" not in list_response.json()

    def test_promote_plan(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test promoting a draft to in-progress."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        content = "# Test Plan\n\nThis will be promoted."
        client_with_config.post(
            f"/api/codex/repos/{repo_id}/plans/drafts",
            json={"name": "test-plan", "content": content},
        )

        response = client_with_config.post(
            f"/api/codex/repos/{repo_id}/plans/promote",
            json={"draft_name": "test-plan"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-plan"
        assert data["status"] == "in_progress"

        # Verify file was created in in-progress
        in_progress_path = repo_path / "plans" / "in-progress" / "test-plan.md"
        assert in_progress_path.exists()
        assert in_progress_path.read_text() == content

    def test_list_in_progress(self, client_with_config: TestClient, tmp_path: Path) -> None:
        """Test listing in-progress plans."""
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        (repo_path / ".git").mkdir()

        add_response = client_with_config.post(
            "/api/repos",
            json={"path": str(repo_path)},
        )
        repo_id = add_response.json()["id"]

        # Save and promote some plans
        client_with_config.post(
            f"/api/codex/repos/{repo_id}/plans/drafts",
            json={"name": "plan-a", "content": "Content A"},
        )
        client_with_config.post(
            f"/api/codex/repos/{repo_id}/plans/promote",
            json={"draft_name": "plan-a"},
        )

        response = client_with_config.get(
            f"/api/codex/repos/{repo_id}/plans/in-progress"
        )
        assert response.status_code == 200
        plans = response.json()
        assert "plan-a" in plans

    def test_save_draft_repo_not_found(self, client_with_config: TestClient) -> None:
        """Test saving draft for non-existent repo."""
        response = client_with_config.post(
            "/api/codex/repos/nonexistent/plans/drafts",
            json={"name": "test", "content": "Content"},
        )
        assert response.status_code == 404


class TestTranscriptionEndpoint:
    """Tests for the transcription endpoint."""

    def test_transcribe_without_config(self, client_with_config: TestClient) -> None:
        """Test transcription when service is not configured."""
        audio_data = b"fake audio data"
        files = {"file": ("test.webm", io.BytesIO(audio_data), "audio/webm")}

        response = client_with_config.post("/api/transcribe", files=files)
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"].lower()

    def test_transcribe_success(self, test_config: ServerConfig, test_token: str) -> None:
        """Test successful transcription."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        config_with_transcription = ServerConfig(
            bind_host=test_config.bind_host,
            bind_port=test_config.bind_port,
            auth_token=test_config.auth_token,
            repo_registry_path=test_config.repo_registry_path,
            codex_server_url=test_config.codex_server_url,
            codex_server_token=test_config.codex_server_token,
            transcription_url="https://api.example.com",
            transcription_token="test-transcription-token",
        )

        main_module._config = config_with_transcription
        main_module._registry = RepoRegistry(config_with_transcription.repo_registry_path)
        main_module._service = AflowService()

        mock_client = AsyncMock()
        mock_client.transcribe = AsyncMock(return_value="Transcribed text")
        main_module._transcription_client = mock_client

        try:
            client = TestClient(app, raise_server_exceptions=True)
            client.headers["Authorization"] = f"Bearer {test_token}"

            audio_data = b"fake audio data"
            files = {"file": ("test.webm", io.BytesIO(audio_data), "audio/webm")}

            response = client.post("/api/transcribe", files=files)
            assert response.status_code == 200
            assert response.json() == {"text": "Transcribed text"}

            mock_client.transcribe.assert_called_once()
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None
            main_module._transcription_client = None

    def test_transcribe_error(self, test_config: ServerConfig, test_token: str) -> None:
        """Test transcription with error."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        config_with_transcription = ServerConfig(
            bind_host=test_config.bind_host,
            bind_port=test_config.bind_port,
            auth_token=test_config.auth_token,
            repo_registry_path=test_config.repo_registry_path,
            codex_server_url=test_config.codex_server_url,
            codex_server_token=test_config.codex_server_token,
            transcription_url="https://api.example.com",
            transcription_token="test-transcription-token",
        )

        main_module._config = config_with_transcription
        main_module._registry = RepoRegistry(config_with_transcription.repo_registry_path)
        main_module._service = AflowService()

        mock_client = AsyncMock()
        mock_client.transcribe = AsyncMock(side_effect=TranscriptionError("Service unavailable"))
        main_module._transcription_client = mock_client

        try:
            client = TestClient(app, raise_server_exceptions=True)
            client.headers["Authorization"] = f"Bearer {test_token}"

            audio_data = b"fake audio data"
            files = {"file": ("test.webm", io.BytesIO(audio_data), "audio/webm")}

            response = client.post("/api/transcribe", files=files)
            assert response.status_code == 500
            assert "Service unavailable" in response.json()["detail"]
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None
            main_module._transcription_client = None

    def test_transcribe_requires_auth(self, test_config: ServerConfig) -> None:
        """Test that transcription requires authentication."""
        from aflow_app_server import main as main_module
        from aflow_app_server.aflow_service import AflowService

        config_with_transcription = ServerConfig(
            bind_host=test_config.bind_host,
            bind_port=test_config.bind_port,
            auth_token=test_config.auth_token,
            repo_registry_path=test_config.repo_registry_path,
            codex_server_url=test_config.codex_server_url,
            codex_server_token=test_config.codex_server_token,
            transcription_url="https://api.example.com",
            transcription_token="test-transcription-token",
        )

        main_module._config = config_with_transcription
        main_module._registry = RepoRegistry(config_with_transcription.repo_registry_path)
        main_module._service = AflowService()

        mock_client = AsyncMock()
        main_module._transcription_client = mock_client

        try:
            client = TestClient(app)

            audio_data = b"fake audio data"
            files = {"file": ("test.webm", io.BytesIO(audio_data), "audio/webm")}

            response = client.post("/api/transcribe", files=files)
            assert response.status_code == 401
        finally:
            main_module._config = None
            main_module._registry = None
            main_module._service = None
            main_module._transcription_client = None
