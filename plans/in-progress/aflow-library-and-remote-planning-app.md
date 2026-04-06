# aflow Library And Remote Planning App

## Summary

Turn `aflow` into a stable importable Python library for workflow execution and startup decisioning, while keeping the CLI focused on execution. In the same repo, add a separate mobile-first remote app under `apps/` that uses the `aflow` library for execution management and uses a configurable Codex server for session reuse and plan-creation chat. The new app must be ready to split into its own repo later and must not become part of the published `aworkflow` wheel.

This handoff does **not** add plan-creation UX to the `aflow` CLI. Plan creation lives in the new app. The CLI remains an execution-oriented shell over library APIs.

## Git Tracking

- Plan Branch: `main`
- Pre-Handoff Base HEAD: `f22872cd7c14afce9253a90fdaff98260b26c9a9`
- Last Reviewed HEAD: `e36e95af56bad3aa6113bebda9c4fcd6adee27a0`
- Review Log:
  - 2026-04-06: Reviewed against `e36e95af56bad3aa6113bebda9c4fcd6adee27a0` on `main`. Updated Checkpoint 1 and 2 bootstrap commands for current file layout, and replaced stale `tests/test_aflow.py` references with the split test modules present in the current worktree.

## Done Means

- Python callers can load config, inspect startup requirements, answer required startup questions, and launch or resume workflow execution without invoking `aflow.cli.main()` and without hidden `input()` calls inside reusable execution paths.
- The CLI still supports the current execution flow, but it becomes a thin shell over the new library API rather than owning execution logic itself.
- The library exposes structured run events so a server can stream execution state without scraping terminal output.
- A separate in-repo app exists under `apps/aflow_app/` with:
  - a Python server package that imports `aflow` as a library,
  - a mobile-first web client,
  - repo switching for known local repositories,
  - plan draft save/load for selected repos,
  - Codex session listing/attach/send flows through a configurable Codex server adapter,
  - `aflow` workflow start and status management through the library API,
  - browser-recorded audio clip upload to a configurable transcription backend.
- The remote app can save plan drafts into repo-local `plans/drafts/`, promote approved plans into repo-local `plans/in-progress/`, and start an `aflow` workflow on an approved plan.
- The remote app is not included in the root wheel build. Root package publishing behavior for `aworkflow` remains unchanged.
- Existing `aflow` execution behavior covered by current tests remains intact unless this plan explicitly changes that behavior.

## Critical Invariants

- `aflow` remains the single source of truth for workflow execution, transition evaluation, lifecycle setup, retry behavior, and run logging. The remote app must call the library, not reimplement the engine.
- The plan file on disk remains the authoritative state for checkpoint progress and restart behavior.
- All reusable startup questions must be surfaced as structured library questions or results, not hidden terminal prompts in library-owned code paths.
- The CLI may still ask questions, but only by rendering and answering the structured library questions.
- Codex-specific behavior stays outside `aflow` core execution logic. Codex integration lives behind an adapter in the remote app server.
- The remote app lives under `apps/aflow_app/` and must remain excluded from the root `aworkflow` wheel and root runtime dependency set.
- Remote control endpoints must require explicit token-based authentication. Do not expose repo control, Codex messaging, or workflow execution over an unauthenticated LAN service.
- Audio support in this handoff is upload-based only. No streaming microphone pipeline, no live incremental transcription protocol, and no always-on audio session state.
- The remote app must support text-only usage even when audio configuration is missing or disabled.

## Forbidden Implementations

- Do not duplicate workflow execution logic in the app server.
- Do not leave `input()`-driven decisions embedded in code paths that the library API is supposed to own.
- Do not hardcode a single repo path, Codex server URL, auth token, or transcription endpoint.
- Do not make the `aflow` package import the remote app package, frontend assets, FastAPI, or frontend build tooling.
- Do not add the remote app packages to the root `[project.dependencies]` or root wheel package list.
- Do not couple `aflow` library imports to a TTY, Rich banner, or terminal-only UI assumptions.
- Do not store uploaded audio files under tracked repo paths or leave them undeleted after transcription succeeds or fails.
- Do not treat Codex session reuse as “start a new session every time”. The app must expose attach-to-existing-session behavior through the adapter contract.
- Do not describe the remote app as production-secure internet-facing software. This handoff is for authenticated desktop-hosted local/LAN use.

## Checkpoints

### [ ] Checkpoint 1: Extract A Public aflow Library Startup Surface

**Goal:**

- Make startup inspection and run preparation callable from Python without going through CLI prompts.

**Context Bootstrapping:**

- Run these commands before editing:
- `pwd`
- `fd -HI AGENTS.md .`
- `rg -n "def main\\(|_resolve_numeric_start_step|_confirm_startup_recovery|selected_start_step|startup_retry" aflow/cli.py -S`
- `sed -n '520,835p' aflow/cli.py`
- `sed -n '1,180p' aflow/run_state.py`
- `rg -n "startup_recovery|selected_start_step|startup_retry|move_completed_plan_to_done" tests/test_cli.py tests/test_plan.py tests/test_retry.py tests/test_runtime.py -S`
- If this is Checkpoint 1, capture the git tracking values before any edits:
- `git branch --show-current`
- `git rev-parse HEAD`

**Scope & Blast Radius:**

- May create/modify: `aflow/__init__.py`, `aflow/api/__init__.py`, `aflow/api/models.py`, `aflow/api/startup.py`, `aflow/api/runner.py`, `aflow/run_state.py`, `aflow/cli.py`, `tests/test_cli.py`, `tests/test_plan.py`, `tests/test_retry.py`, `tests/test_runtime.py`, `tests/test_library_api.py`
- Must not touch: `apps/**`, frontend files, Codex integration files, `plans/**` except this plan file and minimal progress tracking
- Constraints:
- Preserve current CLI flags and current successful CLI entrypoint behavior.
- Reuse existing config parsing and plan parsing instead of copying logic into new files.
- Keep new library models serializable with plain dataclasses or similarly simple Python structures.
- Do not move workflow loop logic yet. This checkpoint is about startup preparation and public call surfaces.

**Steps:**

- [ ] Step 1: Introduce public library-facing request/result models for startup preparation, startup questions, prepared runs, and post-run completion handling.
- [ ] Step 2: Move the CLI-owned startup branching logic into reusable library functions that can return deterministic results or structured questions instead of reading from stdin.
- [ ] Step 3: Expose a minimal public import surface from `aflow.__init__` or `aflow.api.__init__` for the new startup API.
- [ ] Step 4: Refactor `aflow.cli.main()` to consume the new startup API and preserve current prompt text semantics where practical.
- [ ] Step 5: Add focused tests for library startup preparation and update existing CLI tests to prove no regression.

**Dependencies:**

- Depends on Checkpoint N-1.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_library_api.py`
- Run non-regression tests: `uv run pytest -q tests/test_cli.py tests/test_plan.py tests/test_retry.py tests/test_runtime.py -k "start_step or startup_recovery or move_completed_plan_to_done or selected_start_step or startup_retry or tolerant_loader"`

**Done When:**

- Verification commands pass cleanly.
- A Python caller can inspect whether startup requires step selection, checkpoint recovery confirmation, dirty-worktree confirmation, or no further input.
- `aflow run ...` still works through the CLI with the same flags.
- A git commit is created with message starting with:
  ```text
  cp1 v01
  Extract public startup library surface
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp1 v02`, `cp1 v03`, and so on.

**Stop and Escalate If:**

- Existing CLI tests reveal hidden startup behavior that cannot be represented as structured question/result models without changing user-visible semantics. Emit `AFLOW_STOP: startup flow cannot be represented cleanly without redefining CLI behavior`.

### [ ] Checkpoint 2: Add Evented Execution APIs And Refactor The CLI To Use Them

**Goal:**

- Make workflow execution observable and callable as a library, with structured events suitable for server streaming.

**Context Bootstrapping:**

- Run these commands before editing:
- `rg -n "def run_workflow\\(|write_turn_artifacts_start|finalize_turn_artifacts|AFLOW_STOP" aflow/workflow.py -S`
- `sed -n '1740,2120p' aflow/workflow.py`
- `sed -n '300,430p' aflow/status.py`
- `sed -n '1,220p' aflow/harnesses/base.py`
- `rg -n "run_workflow\\(|BannerRenderer|AFLOW_STOP|write_turn_artifacts_start|finalize_turn_artifacts" aflow tests -S`
- `rg -n "run_workflow|BannerRenderer|build_banner|merge|lifecycle|same_step" tests/test_runtime.py tests/test_harnesses.py tests/test_retry.py -S`

**Scope & Blast Radius:**

- May create/modify: `aflow/api/events.py`, `aflow/api/runner.py`, `aflow/workflow.py`, `aflow/status.py`, `aflow/cli.py`, `aflow/run_state.py`, `tests/test_runtime.py`, `tests/test_harnesses.py`, `tests/test_retry.py`, `tests/test_library_api.py`, `ARCHITECTURE.md`
- Must not touch: `apps/**`, Codex adapter code, transcription code, root `pyproject.toml`
- Constraints:
- Keep the existing run log format and plan-on-disk authority.
- Do not require Rich or a TTY for the library execution path.
- The CLI banner must become an observer over library events, not the owner of execution state.
- Do not break `run_workflow()` test injection patterns for fake runners and fake adapters.

**Steps:**

- [ ] Step 1: Define library event types for run started, turn started, turn finished, status update, question required, run completed, and run failed.
- [ ] Step 2: Thread an observer or callback sink through execution so callers can subscribe without scraping stderr.
- [ ] Step 3: Refactor banner updates to consume event/state callbacks rather than owning implicit side effects in the runner.
- [ ] Step 4: Add a library runner facade that accepts prepared run input plus observer hooks and returns structured results.
- [ ] Step 5: Update CLI execution flow to use the library runner facade and preserve current terminal behavior.
- [ ] Step 6: Update architecture docs for the new boundary between CLI shell and reusable library APIs.

**Dependencies:**

- Depends on Checkpoint 1.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_library_api.py -k "event or runner"`
- Run non-regression tests: `uv run pytest -q tests/test_runtime.py tests/test_harnesses.py tests/test_retry.py -k "run_workflow or banner or merge or lifecycle or same_step"`

**Done When:**

- Verification commands pass cleanly.
- A Python caller can start a run and receive structured status without reading terminal output.
- The CLI still renders a live banner and still exits with the same success/failure behavior.
- `ARCHITECTURE.md` reflects the new library/CLI split and does not describe terminal prompting as the only control surface.
- A git commit is created with message starting with:
  ```text
  cp2 v01
  Add evented execution library API
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp2 v02`, `cp2 v03`, and so on.

**Stop and Escalate If:**

- Run logging or lifecycle correctness depends on banner side effects that cannot be separated without redesigning core state ownership. Emit `AFLOW_STOP: execution observability cannot be separated from terminal UI safely`.

### [ ] Checkpoint 3: Scaffold The Separate Remote App Server And Repo Registry

**Goal:**

- Create an in-repo but separately packaged server app that can manage known repos and invoke `aflow` as a library.

**Context Bootstrapping:**

- Run these commands before editing:
- `bat --paging=never pyproject.toml`
- `fd -tf . aflow`
- `fd -tf "README.md|ARCHITECTURE.md|DEVLOG.md" .`
- `rg -n "packages = \\[\\\"aflow\\\"\\]" pyproject.toml -n`

**Scope & Blast Radius:**

- May create/modify: `apps/aflow_app/README.md`, `apps/aflow_app/server/pyproject.toml`, `apps/aflow_app/server/src/aflow_app_server/__init__.py`, `apps/aflow_app/server/src/aflow_app_server/config.py`, `apps/aflow_app/server/src/aflow_app_server/models.py`, `apps/aflow_app/server/src/aflow_app_server/repo_registry.py`, `apps/aflow_app/server/src/aflow_app_server/aflow_service.py`, `apps/aflow_app/server/src/aflow_app_server/main.py`, `apps/aflow_app/server/tests/test_repo_registry.py`, `apps/aflow_app/server/tests/test_api.py`, `README.md`, `ARCHITECTURE.md`
- Must not touch: root `[project.dependencies]`, root wheel package list, frontend files, Codex adapter implementation, transcription implementation
- Constraints:
- The server must live outside the root wheel package list.
- The server must authenticate requests with a configured bearer token.
- Repo registry must validate that a registered path exists and is a git root or explicit repo root chosen by the user.
- Start with local file-backed registry/config under the user config directory for the server app, not repo-tracked registry files.
- Serve JSON APIs only in this checkpoint. No frontend yet.

**Steps:**

- [ ] Step 1: Create a standalone Python subproject for the server under `apps/aflow_app/server/` with its own dependencies and entrypoint.
- [ ] Step 2: Add server config loading for bind host, port, auth token, default repo list file, Codex server settings placeholder, and transcription settings placeholder.
- [ ] Step 3: Implement repo registry CRUD with validation and tests.
- [ ] Step 4: Implement authenticated API endpoints for listing repos, adding repos, selecting a repo, listing repo-local plan files in `plans/drafts/` and `plans/in-progress/`, and starting a prepared `aflow` execution through the library facade.
- [ ] Step 5: Implement execution event streaming from library events over SSE, not WebSockets.
- [ ] Step 6: Update docs to explain that the remote app server is a separate subproject and is not part of the published `aworkflow` package.

**Dependencies:**

- Depends on Checkpoint 2.

**Verification:**

- Run scoped tests: `uv run --project apps/aflow_app/server pytest -q`
- Run non-regression tests: `uv run pytest -q tests/test_library_api.py -k "server or api or execution"`

**Done When:**

- Verification commands pass cleanly.
- Starting the server does not require modifying root package metadata.
- An authenticated client can add a repo, list draft/in-progress plans for that repo, start an `aflow` run through the library, and subscribe to run events over SSE.
- The root wheel still packages only `aflow`.
- A git commit is created with message starting with:
  ```text
  cp3 v01
  Add remote app server scaffold
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp3 v02`, `cp3 v03`, and so on.

**Stop and Escalate If:**

- The root packaging layout or editable install model prevents the server subproject from importing local `aflow` cleanly without contaminating published package metadata. Emit `AFLOW_STOP: separate server package cannot consume local aflow cleanly in current repo layout`.

### [ ] Checkpoint 4: Add Codex Session Reuse And Plan Draft Persistence

**Goal:**

- Let the server reuse existing Codex sessions for a selected repo and persist plan drafts/promotions from those conversations.

**Context Bootstrapping:**

- Run these commands before editing:
- `fd -tf . apps/aflow_app/server`
- `sed -n '1,260p' apps/aflow_app/server/src/aflow_app_server/main.py`
- `sed -n '1,260p' apps/aflow_app/server/src/aflow_app_server/config.py`
- `rg -n "codex|session|plan draft|plans/drafts|plans/in-progress" apps/aflow_app/server -S`

**Scope & Blast Radius:**

- May create/modify: `apps/aflow_app/server/src/aflow_app_server/codex_backend.py`, `apps/aflow_app/server/src/aflow_app_server/codex_routes.py`, `apps/aflow_app/server/src/aflow_app_server/plan_store.py`, `apps/aflow_app/server/src/aflow_app_server/main.py`, `apps/aflow_app/server/src/aflow_app_server/models.py`, `apps/aflow_app/server/tests/test_codex_backend.py`, `apps/aflow_app/server/tests/test_plan_store.py`, `apps/aflow_app/server/tests/test_api.py`, `ARCHITECTURE.md`, `devlog/DEVLOG.md`
- Must not touch: `aflow/**` core engine logic except import wiring already established, frontend files, transcription implementation
- Constraints:
- Define a Codex adapter interface with `list_sessions`, `attach_session`, `fetch_messages`, and `send_message`.
- Keep the adapter server-specific. `aflow` core must not import or know about Codex.
- Save draft plans under `<repo>/plans/drafts/`.
- Promotion to executable handoff plan must write under `<repo>/plans/in-progress/`.
- Preserve existing file contents verbatim when saving approved assistant plan text, except for normal newline normalization.

**Steps:**

- [ ] Step 1: Implement a configurable Codex server adapter interface plus an HTTP implementation that normalizes external session/message payloads into server-local models.
- [ ] Step 2: Add authenticated API endpoints for listing existing Codex sessions for a repo, attaching to one, loading message history, and sending a message.
- [ ] Step 3: Add server-side plan store helpers for saving assistant-produced plan markdown into `plans/drafts/` and promoting an approved draft into `plans/in-progress/`.
- [ ] Step 4: Add API endpoints for plan draft save, list, load, promote, and delete.
- [ ] Step 5: Add tests using mocked Codex server responses and temp git repos.
- [ ] Step 6: Update architecture/devlog docs with the external Codex adapter boundary and plan draft lifecycle.

**Dependencies:**

- Depends on Checkpoint 3.

**Verification:**

- Run scoped tests: `uv run --project apps/aflow_app/server pytest -q apps/aflow_app/server/tests/test_codex_backend.py apps/aflow_app/server/tests/test_plan_store.py apps/aflow_app/server/tests/test_api.py`
- Run non-regression tests: `uv run --project apps/aflow_app/server pytest -q`

**Done When:**

- Verification commands pass cleanly.
- An authenticated client can attach to an existing Codex session, view its messages, send a new message, save an assistant plan response as a repo-local draft, and promote an approved draft into `plans/in-progress/`.
- The app server can be configured against a different Codex server URL without code changes.
- `devlog/DEVLOG.md` records the Codex adapter boundary and draft-plan behavior.
- A git commit is created with message starting with:
  ```text
  cp4 v01
  Add Codex session reuse and plan drafts
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp4 v02`, `cp4 v03`, and so on.

**Stop and Escalate If:**

- The real Codex server cannot list or attach to existing sessions in a way that supports the required reuse workflow. Emit `AFLOW_STOP: Codex server does not expose reusable session attach/list capabilities required by this handoff`.

### [ ] Checkpoint 5: Build The Mobile-First Web Client For Repos, Plans, Sessions, And Execution

**Goal:**

- Deliver a touch-friendly web UI that can switch repos, interact with Codex sessions, save plans, and start/view `aflow` runs.

**Context Bootstrapping:**

- Run these commands before editing:
- `fd -tf . apps/aflow_app`
- `sed -n '1,260p' apps/aflow_app/server/src/aflow_app_server/main.py`
- `bat --paging=never apps/aflow_app/README.md`
- `rg -n "SSE|/api/|plans/drafts|plans/in-progress|codex" apps/aflow_app/server -S`

**Scope & Blast Radius:**

- May create/modify: `apps/aflow_app/web/package.json`, `apps/aflow_app/web/package-lock.json`, `apps/aflow_app/web/tsconfig.json`, `apps/aflow_app/web/vite.config.ts`, `apps/aflow_app/web/index.html`, `apps/aflow_app/web/src/main.tsx`, `apps/aflow_app/web/src/App.tsx`, `apps/aflow_app/web/src/api.ts`, `apps/aflow_app/web/src/styles.css`, `apps/aflow_app/web/src/components/RepoPicker.tsx`, `apps/aflow_app/web/src/components/SessionPanel.tsx`, `apps/aflow_app/web/src/components/PlanPanel.tsx`, `apps/aflow_app/web/src/components/ExecutionPanel.tsx`, `apps/aflow_app/web/src/components/Composer.tsx`, `apps/aflow_app/web/src/components/AudioRecorder.tsx`, `apps/aflow_app/web/src/App.test.tsx`, `apps/aflow_app/web/src/api.test.ts`, `apps/aflow_app/README.md`
- Must not touch: `aflow/**` engine code except small API shape fixes needed to satisfy app integration tests, transcription backend logic
- Constraints:
- Use a mobile-first SPA with large tap targets, readable dense layout, and no wasted giant gutters.
- Avoid generic default component-library look. Use handcrafted CSS variables and responsive layout.
- Keep the web client same-origin with the server in production. In development, Vite proxy is acceptable.
- Support text-only plan/chat/execution flows without microphone permission.
- Do not make the frontend depend on terminal log scraping. Use structured server APIs and SSE.

**Steps:**

- [ ] Step 1: Scaffold the web app with Vite, React, and TypeScript under `apps/aflow_app/web/`.
- [ ] Step 2: Implement authenticated API client helpers, repo selection state, and Codex session list/detail flows.
- [ ] Step 3: Implement a plan panel that lists drafts and in-progress plans, previews markdown, saves drafts, and promotes approved drafts.
- [ ] Step 4: Implement an execution panel that starts a workflow from an approved plan and shows live event updates from SSE.
- [ ] Step 5: Implement a touch-friendly composer with text input, send action, and reserved audio controls area.
- [ ] Step 6: Add minimal frontend tests for the critical flows and update the app README with run/build instructions.

**Dependencies:**

- Depends on Checkpoint 4.

**Verification:**

- Run scoped tests: `npm --prefix apps/aflow_app/web test -- --run`
- Run non-regression tests: `npm --prefix apps/aflow_app/web run build`

**Done When:**

- Verification commands pass cleanly.
- On a tablet-sized viewport, the user can switch repos, attach to an existing Codex session, send a text message, save a returned plan as a draft, promote it, and start an `aflow` workflow while seeing live status updates.
- The layout remains usable on a phone-sized viewport without horizontal scrolling for the main chat and execution screens.
- `apps/aflow_app/README.md` explains local dev and build flow for both server and web packages.
- A git commit is created with message starting with:
  ```text
  cp5 v01
  Add mobile-first remote web client
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp5 v02`, `cp5 v03`, and so on.

**Stop and Escalate If:**

- The server API contract proves insufficient for plan/session/execution flows without introducing frontend-only shadow state that would drift from the server. Emit `AFLOW_STOP: server contract is insufficient for stable mobile client behavior`.

### [ ] Checkpoint 6: Add Audio Clip Transcription And Final Documentation Parity

**Goal:**

- Complete the first usable speech-to-text path for the remote app and finalize docs for the library/app split.

**Context Bootstrapping:**

- Run these commands before editing:
- `fd -tf . apps/aflow_app/server apps/aflow_app/web`
- `sed -n '1,260p' apps/aflow_app/server/src/aflow_app_server/config.py`
- `sed -n '1,260p' apps/aflow_app/web/src/components/AudioRecorder.tsx`
- `bat --paging=never README.md`

**Scope & Blast Radius:**

- May create/modify: `apps/aflow_app/server/src/aflow_app_server/transcription.py`, `apps/aflow_app/server/src/aflow_app_server/main.py`, `apps/aflow_app/server/src/aflow_app_server/models.py`, `apps/aflow_app/server/tests/test_transcription.py`, `apps/aflow_app/server/tests/test_api.py`, `apps/aflow_app/web/src/components/AudioRecorder.tsx`, `apps/aflow_app/web/src/api.ts`, `apps/aflow_app/web/src/App.test.tsx`, `README.md`, `ARCHITECTURE.md`, `devlog/DEVLOG.md`, `apps/aflow_app/README.md`
- Must not touch: root `AGENTS.md`, root package publish metadata, `aflow` engine logic except minimal compatibility fixes discovered during app integration
- Constraints:
- Use multipart upload of recorded audio clips from browser to server.
- Server transcription integration must be configurable for an OpenAI-compatible file transcription endpoint.
- Missing transcription config must degrade gracefully with a clear API error and no broken text-only path.
- Clean up temp audio files after each request.
- Update only relevant existing sections in root README. Do not add marketing sections.

**Steps:**

- [ ] Step 1: Implement configurable transcription client logic in the server with clear error handling for disabled or misconfigured transcription.
- [ ] Step 2: Add authenticated upload endpoint for browser-recorded audio clips and return normalized transcript text.
- [ ] Step 3: Wire the web audio recorder to record, upload, show progress/error states, and insert transcript text into the composer.
- [ ] Step 4: Add tests for transcription configuration, upload handling, and frontend transcript insertion behavior.
- [ ] Step 5: Perform documentation impact review and update the relevant existing docs for the library split, server/web app layout, auth/config expectations, draft/in-progress plan flow, and audio support.

**Dependencies:**

- Depends on Checkpoint 5.

**Verification:**

- Run scoped tests: `uv run --project apps/aflow_app/server pytest -q apps/aflow_app/server/tests/test_transcription.py apps/aflow_app/server/tests/test_api.py`
- Run non-regression tests: `npm --prefix apps/aflow_app/web test -- --run && npm --prefix apps/aflow_app/web run build && uv run pytest -q tests/test_library_api.py tests/test_cli.py tests/test_plan.py tests/test_retry.py tests/test_runtime.py tests/test_harnesses.py`

**Done When:**

- Verification commands pass cleanly.
- A browser user can record an audio clip, upload it, receive transcript text, edit it if needed, and send it as a normal message.
- If transcription is disabled or misconfigured, the UI still supports text-only planning and execution without crashing.
- `README.md`, `ARCHITECTURE.md`, `devlog/DEVLOG.md`, and `apps/aflow_app/README.md` reflect the implemented behavior and do not claim features that were not shipped.
- A git commit is created with message starting with:
  ```text
  cp6 v01
  Add audio transcription and docs parity
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp6 v02`, `cp6 v03`, and so on.

**Stop and Escalate If:**

- The chosen transcription backend cannot accept browser-recorded file uploads in a way that fits the configured auth and local deployment model. Emit `AFLOW_STOP: transcription backend is incompatible with upload-based browser clips`.

## Behavioral Acceptance Tests

- Given a Python caller with a valid repo, workflow, and plan, startup inspection returns either a prepared run or an explicit structured question, never a hidden terminal prompt.
- Given the CLI `aflow run ...`, the command still executes workflows and surfaces the same startup choices through terminal prompts by rendering library questions.
- Given an authenticated remote client and a registered repo, the server can start an `aflow` workflow as a library call and stream step progress over SSE without scraping terminal output.
- Given a selected repo and a reachable Codex server, the app can list existing Codex sessions, attach to one, load its message history, and send another message into that same session.
- Given an assistant message containing Markdown plan text, the app can save it as a draft under `plans/drafts/`, later promote it into `plans/in-progress/`, and then execute it with `aflow`.
- Given a completed workflow run, the remote client sees the terminal run state and final result without requiring access to stderr or Rich-rendered text.
- Given a tablet viewport, the main repo/session/plan/execution actions are reachable with large tap targets and no horizontal overflow on the primary screen.
- Given a recorded audio clip and valid transcription config, the app returns transcript text and inserts it into the message composer for normal send flow.
- Given missing transcription config, the app rejects audio transcription clearly while preserving all text-based planning and execution flows.

## Plan-to-Verification Matrix

| Requirement | Verification |
| --- | --- |
| Library startup questions replace hidden prompt logic | `uv run pytest -q tests/test_library_api.py` |
| CLI remains execution-capable after refactor | `uv run pytest -q tests/test_cli.py tests/test_runtime.py -k "start_step or startup_recovery or move_completed_plan_to_done"` |
| Library exposes structured execution events | `uv run pytest -q tests/test_library_api.py -k "event or runner"` |
| Remote server remains outside published wheel | `rg -n 'packages = \\[\"aflow\"\\]' pyproject.toml` and `uv build && python - <<'PY'\nimport zipfile, pathlib\nwheel = sorted(pathlib.Path('dist').glob('*.whl'))[-1]\nwith zipfile.ZipFile(wheel) as z:\n    names = [n for n in z.namelist() if n.startswith('apps/') or 'aflow_app' in n]\n    print(names)\n    raise SystemExit(1 if names else 0)\nPY` |
| Repo registry and execution APIs work | `uv run --project apps/aflow_app/server pytest -q apps/aflow_app/server/tests/test_api.py` |
| Codex session reuse works through adapter contract | `uv run --project apps/aflow_app/server pytest -q apps/aflow_app/server/tests/test_codex_backend.py` |
| Plan draft save/promote works in repo-local paths | `uv run --project apps/aflow_app/server pytest -q apps/aflow_app/server/tests/test_plan_store.py` |
| Mobile web app compiles and key flows are covered | `npm --prefix apps/aflow_app/web test -- --run` and `npm --prefix apps/aflow_app/web run build` |
| Audio upload transcription works and degrades cleanly | `uv run --project apps/aflow_app/server pytest -q apps/aflow_app/server/tests/test_transcription.py apps/aflow_app/server/tests/test_api.py` |
| Docs match shipped behavior | `rg -n "library|apps/aflow_app|Codex|transcription|plans/drafts|plans/in-progress" README.md ARCHITECTURE.md devlog/DEVLOG.md apps/aflow_app/README.md` |

## Assumptions And Defaults

- Python version remains `3.11+` for `aflow` and for the server subproject.
- Node `20+` and `npm` are acceptable defaults for the web app toolchain.
- The remote server is desktop-hosted and LAN-accessed, not internet-exposed. Token auth is still required.
- The Codex server is reachable over HTTP(S) and has enough API surface to list or discover existing sessions, attach to a specific session, fetch message history, and send messages. If the actual server surface differs materially, stop at the Codex checkpoint rather than inventing a fake compatibility layer.
- The transcription backend exposes an OpenAI-compatible file upload API or a thin equivalent that can be normalized behind the server transcription client.
- Repo registry stores absolute local repo roots and validates them against git discovery.
- Draft plans live under `<repo>/plans/drafts/`. Approved executable handoff plans live under `<repo>/plans/in-progress/`.
- The remote app serves planning and execution management for personal/local use first. Multi-user tenancy, permissions beyond one shared token, background job queueing beyond one-process server memory, and push-notification/mobile packaging are out of scope for this handoff.
- Root README changes should update relevant existing sections only. If no relevant section exists for a detail, prefer `apps/aflow_app/README.md`, `ARCHITECTURE.md`, or `devlog/DEVLOG.md` instead of adding a new marketing-style root README section.

## Final Checklist

- [ ] All checkpoint verification commands pass.
- [ ] No new app code is included in the root wheel.
- [ ] CLI execution behavior remains covered by tests after library refactor.
- [ ] Library APIs are importable without a TTY.
- [ ] Remote server requires auth on state-changing and session endpoints.
- [ ] Text-only remote usage works with audio disabled.
- [ ] Uploaded audio temp files are cleaned up.
- [ ] Root docs and app docs describe only shipped behavior.
- [ ] No debug endpoints, placeholder secrets, or commented-out scaffolding remain.
