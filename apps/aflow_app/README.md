# aflow Remote App

A mobile-first remote management interface for aflow workflows.

## Overview

This app provides a web-based interface for managing aflow workflows across multiple repositories. It consists of:

- **Server** (`server/`): A FastAPI-based backend that uses the `aflow` library for workflow execution
- **Web** (`web/`): A mobile-first React frontend (coming in later checkpoints)

The remote app is a separate subproject from the main `aworkflow` package and is not included in the published wheel.

## Architecture

```
apps/aflow_app/
├── server/                    # Python FastAPI server
│   ├── src/aflow_app_server/
│   │   ├── __init__.py       # Package init
│   │   ├── config.py         # Server configuration
│   │   ├── models.py         # API models
│   │   ├── repo_registry.py  # Repository management
│   │   ├── aflow_service.py  # aflow library integration
│   │   ├── codex_backend.py  # Codex server adapter
│   │   ├── codex_routes.py   # Codex API routes
│   │   ├── plan_store.py     # Plan draft management
│   │   └── main.py           # FastAPI app and endpoints
│   └── tests/                # Server tests
└── web/                      # React web client
    ├── src/
    │   ├── components/       # React components
    │   ├── api.ts            # API client
    │   ├── types.ts          # TypeScript types
    │   ├── App.tsx           # Main app component
    │   └── main.tsx          # Entry point
    └── tests/                # Frontend tests
```

## Server Configuration

The server is configured via environment variables or a TOML config file.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AFLOW_APP_CONFIG_DIR` | Config directory path | `~/.config/aflow-app` |
| `AFLOW_APP_HOST` | Bind host | `127.0.0.1` |
| `AFLOW_APP_PORT` | Bind port | `8765` |
| `AFLOW_APP_TOKEN` | Auth token (required) | - |
| `AFLOW_APP_REGISTRY_PATH` | Repo registry file path | `<config_dir>/repos.json` |
| `AFLOW_CODEX_URL` | Codex server URL | - |
| `AFLOW_CODEX_TOKEN` | Codex server token | - |
| `AFLOW_TRANSCRIPTION_URL` | Transcription service URL | - |
| `AFLOW_TRANSCRIPTION_TOKEN` | Transcription service token | - |

### Config File

Create `~/.config/aflow-app/config.toml`:

```toml
[server]
bind_host = "127.0.0.1"
bind_port = 8765
auth_token = "your-secret-token"

[codex]
server_url = "http://localhost:8080"
server_token = "codex-token"

[transcription]
server_url = "https://api.openai.com/v1"
server_token = "openai-api-key"
```

## Running the Server

```bash
# Build the web app once
cd apps/aflow_app/web
npm install
npm run build

# Then run the backend from the server directory
cd apps/aflow_app/server
uv sync
AFLOW_APP_TOKEN=secret uv run aflow-app-server

# Open the UI from the backend
open http://127.0.0.1:8765/
```

The backend serves the built frontend from `apps/aflow_app/web/dist`, so you do not need a separate frontend server for normal use. The server project depends on the repo-root `aworkflow` package through a local uv source, so it should be started from this checkout without setting `PYTHONPATH`.

## Running the Web Client

```bash
# Separate frontend dev server, only if you want hot reload
cd apps/aflow_app/web
npm install

# Start development server (proxies API to localhost:8765)
npm run dev

# Build static assets for the backend to serve
npm run build

# Preview the built output directly
npm run preview

# Run tests
npm test -- --run
```

`npm run dev` runs a separate Vite dev server on `http://localhost:3000` and proxies API requests to `http://127.0.0.1:8765`.

`npm run preview` serves the already-built `dist/` output. Run `npm run build` first if you want to use preview.

## API Endpoints

All endpoints (except `/health`) require Bearer token authentication.

### Repositories

- `GET /api/repos` - List all registered repositories
- `POST /api/repos` - Add a repository
- `GET /api/repos/{repo_id}` - Get a specific repository
- `PATCH /api/repos/{repo_id}` - Update repository metadata
- `DELETE /api/repos/{repo_id}` - Remove a repository

### Plans

- `GET /api/repos/{repo_id}/plans` - List all plans (drafts and in-progress)

### Codex

- `GET /api/codex/sessions` - List Codex sessions
- `GET /api/codex/sessions/{session_id}` - Get a specific session
- `GET /api/codex/sessions/{session_id}/messages` - Fetch messages from a session
- `POST /api/codex/sessions/{session_id}/messages` - Send a message to a session
- `GET /api/codex/repos/{repo_id}/plans/drafts` - List plan drafts
- `POST /api/codex/repos/{repo_id}/plans/drafts` - Save a plan draft
- `GET /api/codex/repos/{repo_id}/plans/drafts/{filename}` - Load a plan draft
- `POST /api/codex/repos/{repo_id}/plans/drafts/{filename}/promote` - Promote draft to in-progress
- `DELETE /api/codex/repos/{repo_id}/plans/drafts/{filename}` - Delete a plan draft

### Executions

- `POST /api/executions` - Start a workflow execution
- `GET /api/executions/{run_id}` - Get execution status
- `GET /api/executions/{run_id}/events` - Stream execution events (SSE)

### Transcription

- `POST /api/transcribe` - Transcribe an uploaded audio file

### Health

- `GET /health` - Health check (no auth required)

## Audio Transcription

The app supports browser-recorded audio clip transcription for voice input. This feature is optional and degrades gracefully when not configured.

### Configuration

Set the transcription service URL and token:

```bash
export AFLOW_TRANSCRIPTION_URL="https://api.openai.com/v1"
export AFLOW_TRANSCRIPTION_TOKEN="your-openai-api-key"
```

Or in `config.toml`:

```toml
[transcription]
server_url = "https://api.openai.com/v1"
server_token = "your-openai-api-key"
```

### Behavior

- When configured: Audio recording button appears in the composer
- When not configured: Text-only input remains fully functional
- Transcription errors are shown to the user without breaking the app
- Uploaded audio files are automatically cleaned up after transcription

The transcription client supports OpenAI-compatible APIs (Whisper format).

## Development

### Running Tests

```bash
cd apps/aflow_app/server
uv run --extra dev pytest -q
```

### Project Structure

The server is designed to:

1. Use `aflow` as a library, not a CLI
2. Stream execution events via SSE for real-time updates
3. Support multiple repositories with a file-backed registry
4. Require token authentication for all state-changing operations
5. Be deployable for local/LAN use, not internet-facing

## Security Notes

- The server requires a bearer token for all API operations
- It is designed for authenticated desktop-hosted local/LAN use
- Do not expose the server to the internet without additional security measures
- The token is transmitted in the `Authorization` header on every request
