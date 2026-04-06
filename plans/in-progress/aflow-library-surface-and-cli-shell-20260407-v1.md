# aflow Library Surface And CLI Thin Shell

## Summary

This plan supersedes the `aflow` engine portion of `plans/in-progress/aflow-library-and-remote-planning-app.md`.

The scope of this handoff is only the reusable `aflow` package and the `aflow`/`aworkflow` CLI that ships from this repo. The target end state is a stable importable library for startup preparation and workflow execution, with the CLI reduced to a terminal-oriented shell over that library surface.

This handoff must not create the daemon server package, the web package, or any Codex-specific remote-control code. Those belong to the separate dependent handoff.

## Git Tracking

- Plan Branch: `aflow-aflow-library-surface-and-cli-shell-20260407-v1-20260406-225401`
- Pre-Handoff Base HEAD: `92b85aef53e8424d4b6129dd16ba5207a31b3c0d`
- Last Reviewed HEAD: `6a6189e`
- Final Approval: 2026-04-07
- Review Log:
  - 2026-04-07: Reviewed cp1 v01 (`f1ee014`). Checkpoint 1 approved. Notes: dead `_resolve_numeric_start_step` in cli.py should be removed; double `_resolve_start_step` call in `prepare_startup`; CLI still resolves workflow name before passing to library.
  - 2026-04-07: Reviewed cp2 v01 (`5a08492`). Checkpoint 2 changes-requested. Events emitted as raw dicts instead of typed dataclasses; no StatusChangedEvent or RunFailedEvent emissions; observer typed as `object` instead of `ExecutionObserver`; silent exception swallowing; lowercase `callable` type hint; useless try/except in runner; plan tickboxes modified by implementer.
  - 2026-04-07: Reviewed cp2 v02 (`30ad24a`). Checkpoint 2 approved. All prior findings addressed. Reviewer cleaned up redundant TYPE_CHECKING imports and unused _events field.
  - 2026-04-07: Reviewed cp3 v01 (`e58bf6c`). Checkpoint 3 changes-requested. Test `test_resolve_run_args_digit_like_step_name_not_numeric_index` patches removed `aflow.cli.run_workflow`; `keep_runs` not propagated from workflow config to `ControllerConfig` in runner; plan files modified by implementer again.
  - 2026-04-07: Reviewed cp3 v02 (`0fcddfc`). Checkpoint 3 approved. Both fix plan items addressed: keep_runs propagation restored, test patch target corrected. Full suite 473 passed.
  - 2026-04-07: Reviewed cp4 v01 (worktree fallback, no commit). Checkpoint 4 changes-requested. README code example uses wrong field name (`prompt` vs `message`); missing imports in code examples; misleading TTY claim; no devlog entry; no doc-parity tests; no wheel verification; plan checkboxes modified by implementer again.
  - 2026-04-07: Reviewed cp4 v02 (`7e39c2b`). Checkpoint 4 approved. All cp4 v01 findings addressed in README/devlog/tests. ARCHITECTURE.md updates were left unstaged by implementer; reviewer committed them with field name fix (`prompt` -> `message`). Full suite 474 passed. Wheel verified clean.
  - 2026-04-07: Final review (`6a6189e`). All 4 checkpoints approved. 15 total commits, 474 tests pass, wheel clean. Handoff approved.

## Done Means

- Python callers can prepare startup, answer structured startup questions, and execute or resume a workflow without invoking `aflow.cli.main()` and without hidden `input()` calls in library-owned control flow.
- The public import surface for reusable behavior lives under `aflow.api` and is re-exported from `aflow` for stable imports.
- Workflow execution emits structured library events or callbacks that a non-TTY caller can consume without scraping Rich output or stderr.
- The CLI still supports the current user-facing execution flow, but it becomes a thin terminal adapter over the same startup and runner APIs used by non-CLI callers.
- The root wheel still packages only `aflow`, and this handoff does not add daemon or web package code to the published `aworkflow` artifact.
- Existing plan-on-disk behavior, retry semantics, lifecycle behavior, and run-log behavior remain intact unless this plan explicitly changes them.

## Critical Invariants

- The plan file on disk remains the authoritative state for checkpoint progress, restart behavior, and completed-plan movement.
- Structured startup questions are the only library-owned way to request interactive decisions. Library code must not call `input()` directly.
- The CLI may still prompt the user, but only by rendering library-provided startup questions and library execution state.
- Reusable execution APIs must be importable and callable without requiring a TTY, a Rich live renderer, or server-specific frameworks.
- Public reusable behavior must live under `aflow.api` and be re-exported from `aflow`; daemon- or web-specific contracts must not be embedded into core `aflow`.
- This handoff must not create or modify `apps/**` package roots except for read-only discovery during verification.
- The root wheel package list remains `["aflow"]` unless the user explicitly starts a separate packaging handoff later.

## Forbidden Implementations

- Do not add FastAPI, Starlette, React, Vite, or any frontend/runtime web dependency to the root project dependencies.
- Do not keep duplicate startup decision logic in both `aflow.cli` and `aflow.api`.
- Do not expose `workflow.py` internals as the public API without a stable wrapper contract.
- Do not make library execution depend on Rich side effects, banner refresh loops, or terminal rendering to preserve correctness.
- Do not hardcode daemon-oriented transport concepts such as SSE, HTTP request objects, or bearer-token semantics into `aflow`.
- Do not change run-log or plan mutation behavior just to make future app integration easier.
- Do not describe daemon or web behavior in root docs as shipped by this handoff.

## Checkpoints

### [x] Checkpoint 1: Stabilize The Existing Startup Library Contract

**Goal:**

- Bring the already-started `aflow.api` startup surface to feature parity with current CLI startup behavior and remove any remaining CLI-only startup branching that would force non-CLI callers to guess behavior.

**Context Bootstrapping:**

- Run these commands before editing:
- `pwd`
- `fd -HI AGENTS.md .`
- `bat --paging=never pyproject.toml`
- `bat --paging=never aflow/AGENTS.md`
- `rg -n "PreparedRun|StartupQuestion|prepare_startup|prepare_startup_with_answer" aflow/__init__.py aflow/api/__init__.py aflow/api/models.py aflow/api/startup.py aflow/cli.py tests/test_library_api.py -S`
- `rg -n "_resolve_numeric_start_step|_handle_startup_questions|_answer_startup_question|startup_base_head_refresh|dirty_worktree_confirmed" aflow/cli.py -S`
- `sed -n '1,220p' aflow/api/startup.py`
- `sed -n '950,1085p' aflow/cli.py`
- `rg -n "startup_recovery|selected_start_step|startup_base_head_refresh|move_completed_plan_to_done|start_step" tests/test_library_api.py tests/test_cli.py tests/test_plan.py tests/test_retry.py tests/test_runtime.py -S`
- If this is Checkpoint 1, capture the git tracking values before any edits:
- `git branch --show-current`
- `git rev-parse HEAD`

**Scope & Blast Radius:**

- May create/modify: `aflow/__init__.py`, `aflow/api/__init__.py`, `aflow/api/models.py`, `aflow/api/startup.py`, `aflow/cli.py`, `aflow/run_state.py`, `aflow/workflow.py`, `tests/test_library_api.py`, `tests/test_cli.py`, `tests/test_plan.py`, `tests/test_retry.py`, `tests/test_runtime.py`
- Must not touch: `apps/**`, frontend files, daemon server files, Codex adapter code, root `pyproject.toml` dependencies
- Constraints:
- Preserve current CLI flags and current successful CLI startup behavior.
- Reuse current plan/config parsing and lifecycle preflight helpers instead of cloning logic into new files.
- Cover numeric and named `--start-step` handling, startup recovery, dirty-worktree confirmation, and Pre-Handoff Base HEAD refresh through the same public contract.
- If a startup path truly requires human confirmation, return a structured question. Do not invent a silent non-interactive fallback.

**Steps:**

- [x] Step 1: Audit the current `aflow.api` startup models against the existing CLI behavior and enumerate every remaining CLI-only startup branch that is still outside the public contract.
- [x] Step 2: Expand or normalize the startup request, question, and prepared-run models so they fully represent current startup decisions without relying on hidden caller state.
- [x] Step 3: Refactor startup preparation so the CLI and non-CLI callers use the same decision engine for start-step resolution, recovery approval, dirty-worktree confirmation, and base-head refresh approval.
- [x] Step 4: Keep terminal-only prompt rendering inside `aflow.cli`, but make it a pure adapter over structured library questions.
- [x] Step 5: Add or update focused tests proving the library startup surface can drive the same branches that the CLI currently supports.

**Dependencies:**

- Depends on Checkpoint N-1.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_library_api.py -k "prepare_startup or Startup"`
- Run non-regression tests: `uv run pytest -q tests/test_cli.py tests/test_plan.py tests/test_retry.py tests/test_runtime.py -k "startup or start_step or startup_recovery or move_completed_plan_to_done or base_head_refresh"`

**Done When:**

- Verification commands pass cleanly.
- A Python caller can drive startup from request to `PreparedRun` through explicit question/answer turns for every startup branch currently supported by the CLI.
- `aflow.cli` no longer owns hidden startup state that the future daemon would need to reconstruct.
- A git commit is created with message starting with:
  ```text
  cp1 v01
  Stabilize startup library contract
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp1 v02`, `cp1 v03`, and so on.

**Stop and Escalate If:**

- Existing startup behavior depends on terminal-only state that cannot be represented as structured request/question/result data without changing user-visible semantics. Emit `AFLOW_STOP: startup behavior cannot be expressed as a stable library contract without redefining CLI semantics`.

### [x] Checkpoint 2: Add A Public Runner And Structured Execution Events

**Goal:**

- Expose workflow execution as a reusable library runner with structured events or callbacks suitable for daemon-side streaming later, without scraping terminal output.

**Context Bootstrapping:**

- Run these commands before editing:
- `fd -a . aflow/api`
- `rg -n "def run_workflow\\(|write_turn_artifacts_start|finalize_turn_artifacts|AFLOW_STOP|WorkflowResult" aflow/workflow.py -S`
- `rg -n "BannerRenderer|build_banner|status|live" aflow/status.py aflow/cli.py tests/test_harnesses.py tests/test_runtime.py -S`
- `sed -n '1960,2685p' aflow/workflow.py`
- `sed -n '300,520p' aflow/status.py`
- `rg -n "run_workflow|banner|lifecycle|same_step|AFLOW_STOP" tests/test_runtime.py tests/test_harnesses.py tests/test_retry.py tests/test_library_api.py -S`

**Scope & Blast Radius:**

- May create/modify: `aflow/api/__init__.py`, `aflow/api/events.py`, `aflow/api/models.py`, `aflow/api/runner.py`, `aflow/__init__.py`, `aflow/workflow.py`, `aflow/status.py`, `aflow/cli.py`, `tests/test_library_api.py`, `tests/test_runtime.py`, `tests/test_harnesses.py`, `tests/test_retry.py`
- Must not touch: `apps/**`, daemon/web code, root package metadata except read-only verification
- Constraints:
- Keep plan-on-disk authority and existing run-log files intact.
- Keep event models serializable with plain dataclasses or similarly simple Python structures.
- Keep the core runner transport-agnostic. The library may emit events, but it must not know about SSE, HTTP frameworks, or browser clients.
- Preserve current injection seams used by tests for fake runners, fake adapters, and lifecycle simulation.

**Steps:**

- [x] Step 1: Define stable public event and result types for run started, status changed, turn started, turn finished, question required if applicable, run completed, and run failed.
- [x] Step 2: Thread an observer, callback sink, or equivalent transport-agnostic subscription mechanism through workflow execution.
- [x] Step 3: Add a public runner facade under `aflow.api` that accepts a prepared run plus observer hooks and returns structured execution results.
- [x] Step 4: Preserve current `run_workflow()` behavior internally, but stop forcing callers to infer progress from terminal rendering.
- [x] Step 5: Add focused library-runner tests and update runtime or harness tests to prove lifecycle behavior still works.

**Dependencies:**

- Depends on Checkpoint 1.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_library_api.py -k "event or runner"`
- Run non-regression tests: `uv run pytest -q tests/test_runtime.py tests/test_harnesses.py tests/test_retry.py -k "run_workflow or banner or lifecycle or same_step"`

**Done When:**

- Verification commands pass cleanly.
- A Python caller can execute a workflow and observe structured progress without parsing Rich output, stderr, or run-log files.
- The public execution API remains transport-agnostic and does not mention daemon or web concepts.
- A git commit is created with message starting with:
  ```text
  cp2 v01
  Add public runner and execution events
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp2 v02`, `cp2 v03`, and so on.

**Stop and Escalate If:**

- Correct execution state currently depends on terminal-rendering side effects that cannot be separated from workflow correctness. Emit `AFLOW_STOP: execution observability is still entangled with terminal rendering`.

### [x] Checkpoint 3: Convert The CLI Into A Thin Terminal Adapter

**Goal:**

- Make the CLI consume the public startup and runner APIs end to end, while preserving current terminal UX and exit behavior.

**Context Bootstrapping:**

- Run these commands before editing:
- `rg -n "PreparedRun|prepare_startup|prepare_startup_with_answer|run_workflow|_handle_startup_questions|_format_success_summary|_maybe_move_completed_plan_to_done" aflow/cli.py -S`
- `sed -n '900,1105p' aflow/cli.py`
- `rg -n "BannerRenderer|build_banner" aflow/status.py tests/test_harnesses.py -S`
- `rg -n "_run_workflow_launcher|startup_recovery|selected_start_step|move_completed_plan_to_done" tests/test_cli.py tests/test_runtime.py -S`

**Scope & Blast Radius:**

- May create/modify: `aflow/cli.py`, `aflow/status.py`, `aflow/api/runner.py`, `aflow/api/events.py`, `tests/test_cli.py`, `tests/test_runtime.py`, `tests/test_harnesses.py`
- Must not touch: `apps/**`, daemon/web package roots, Codex integrations, root dependency metadata
- Constraints:
- Keep current CLI flags, output summaries, success/failure exit codes, and TTY-required prompt behavior.
- The CLI may format or buffer events for terminal rendering, but it must not own workflow correctness or plan mutation behavior.
- Keep the existing `aflow` and `aworkflow` console scripts pointing at `aflow.cli:main`.

**Steps:**

- [x] Step 1: Replace any remaining direct startup or execution branching in `aflow.cli` with calls to the public `aflow.api` surface.
- [x] Step 2: Convert terminal status rendering into an observer over structured library events rather than a control-plane dependency.
- [x] Step 3: Preserve current success summary and completed-plan movement behavior while sourcing execution state from the library runner.
- [x] Step 4: Update CLI and launcher tests to lock in parity for exit codes, startup prompts, and run completion behavior.

**Dependencies:**

- Depends on Checkpoint 2.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_cli.py -k "startup or lifecycle or move_completed_plan_to_done"`
- Run non-regression tests: `uv run pytest -q tests/test_runtime.py tests/test_harnesses.py -k "_run_workflow_launcher or banner or lifecycle or move_completed_plan_to_done"`

**Done When:**

- Verification commands pass cleanly.
- The CLI is a terminal-oriented adapter over public startup and runner APIs rather than the only complete execution entrypoint.
- Terminal UX remains intact for interactive use, including prompt gating where a TTY is required.
- A git commit is created with message starting with:
  ```text
  cp3 v01
  Make CLI a thin shell over aflow.api
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp3 v02`, `cp3 v03`, and so on.

**Stop and Escalate If:**

- Preserving current CLI semantics would require the CLI to keep a separate execution state machine outside the public API. Emit `AFLOW_STOP: CLI cannot become a thin adapter without keeping duplicated execution logic`.

### [x] Checkpoint 4: Documentation And Packaging Parity For The Library Split

**Goal:**

- Bring docs and packaging verification into line with the shipped library/CLI boundary, without describing daemon or web work as already implemented.

**Context Bootstrapping:**

- Run these commands before editing:
- `bat --paging=never README.md`
- `bat --paging=never ARCHITECTURE.md`
- `bat --paging=never devlog/DEVLOG.md`
- `rg -n "aflow.api|library|CLI|aworkflow|packages = \\[\"aflow\"\\]" README.md ARCHITECTURE.md devlog/DEVLOG.md pyproject.toml tests/test_docs.py tests/test_config.py -S`

**Scope & Blast Radius:**

- May create/modify: `README.md`, `ARCHITECTURE.md`, `devlog/DEVLOG.md`, `tests/test_docs.py`, `tests/test_config.py`
- Must not touch: root `AGENTS.md`, `apps/**`, root wheel package list, daemon/web docs
- Constraints:
- Update existing relevant sections only. Do not add README marketing sections for future app work.
- Document only behavior that exists after Checkpoints 1 through 3 are implemented.
- If a root README section does not already fit the detail, document it in `ARCHITECTURE.md` or `devlog/DEVLOG.md` instead.

**Steps:**

- [x] Step 1: Review documentation impact and identify only the existing sections that need updates for the new library/CLI boundary.
- [x] Step 2: Update `ARCHITECTURE.md` to describe the public `aflow.api` surface and the CLI-as-adapter boundary.
- [x] Step 3: Update any relevant existing root README or devlog sections so they match shipped behavior and do not promise daemon or web features.
- [x] Step 4: Add or update doc-parity tests where the repo already enforces them.
- [x] Step 5: Verify that the root wheel still contains only `aflow`.

**Dependencies:**

- Depends on Checkpoint 3.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_docs.py tests/test_config.py -k "readme or architecture or docs"`
- Run non-regression tests: `uv build && uv run python - <<'PY'\nimport pathlib, zipfile\nwheel = sorted(pathlib.Path('dist').glob('*.whl'))[-1]\nwith zipfile.ZipFile(wheel) as z:\n    names = [n for n in z.namelist() if n.startswith('apps/') or 'aflowd' in n or 'aflow_web' in n]\n    raise SystemExit(1 if names else 0)\nPY`

**Done When:**

- Verification commands pass cleanly.
- Root docs describe the library and CLI boundary accurately and do not describe daemon or web work as shipped.
- The built wheel contains only the `aflow` package.
- A git commit is created with message starting with:
  ```text
  cp4 v01
  Document library boundary and verify packaging
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp4 v02`, `cp4 v03`, and so on.

**Stop and Escalate If:**

- The current packaging layout cannot expose the needed public API without also pulling daemon or app code into the published wheel. Emit `AFLOW_STOP: library packaging cannot stay isolated under the current repo layout`.

## Behavioral Acceptance Tests

- Given a Python caller with a valid repo, config, workflow, and plan, startup preparation returns either a `PreparedRun` or a structured startup question, never a hidden terminal prompt.
- Given a multi-step workflow and a partially completed plan, the library can require explicit step selection and then continue startup without the caller reconstructing hidden CLI state.
- Given a startup recovery case or a Pre-Handoff Base HEAD mismatch that currently needs approval, the library returns an explicit question and reaches the same prepared-run state after approval that the CLI would have used.
- Given a Python caller executing a prepared run, the caller can observe structured execution progress and final completion without scraping Rich output, stderr, or run-log files.
- Given `aflow run ...` from a TTY, the command still renders prompts and progress in the terminal while using the same startup and runner APIs as a non-CLI caller.
- Given a root wheel build, the resulting artifact still contains only the `aflow` package and no daemon or web packages.

## Plan-to-Verification Matrix

| Requirement | Verification |
| --- | --- |
| Startup API covers current CLI startup branches | `uv run pytest -q tests/test_library_api.py -k "prepare_startup or Startup"` |
| CLI startup behavior stays intact after refactor | `uv run pytest -q tests/test_cli.py tests/test_plan.py tests/test_retry.py tests/test_runtime.py -k "startup or start_step or startup_recovery or move_completed_plan_to_done or base_head_refresh"` |
| Execution is observable without terminal scraping | `uv run pytest -q tests/test_library_api.py -k "event or runner"` |
| Lifecycle and banner behavior remain correct | `uv run pytest -q tests/test_runtime.py tests/test_harnesses.py tests/test_retry.py -k "run_workflow or banner or lifecycle or same_step"` |
| CLI is only a terminal adapter over public APIs | `uv run pytest -q tests/test_cli.py -k "startup or lifecycle or move_completed_plan_to_done"` |
| Root docs match shipped library boundary | `uv run pytest -q tests/test_docs.py tests/test_config.py -k "readme or architecture or docs"` |
| Root wheel stays isolated to `aflow` | `uv build && uv run python - <<'PY'\nimport pathlib, zipfile\nwheel = sorted(pathlib.Path('dist').glob('*.whl'))[-1]\nwith zipfile.ZipFile(wheel) as z:\n    names = [n for n in z.namelist() if n.startswith('apps/') or 'aflowd' in n or 'aflow_web' in n]\n    raise SystemExit(1 if names else 0)\nPY` |

## Assumptions And Defaults

- Python remains `3.11+`.
- `uv run pytest` is the default test entrypoint for this repo.
- The partially implemented startup API currently in `aflow/api/` is the correct base to refine rather than discard and rebuild elsewhere.
- Future daemon or web packages will consume only the public `aflow` surface created by this plan. If they need something not exposed publicly, that is a new `aflow` follow-up, not a reason to reach into private internals from app code.
- Root README changes should update existing relevant sections only. If no suitable section exists, use `ARCHITECTURE.md` or `devlog/DEVLOG.md` instead.
- This handoff does not change the current published package name `aworkflow`, console script names, or root wheel target list.

## Final Checklist

- [x] All checkpoint verification commands pass.
- [x] `aflow` startup decisions are exposed through structured request/question/result models.
- [x] `aflow` execution is callable as a library with structured progress events or callbacks.
- [x] The CLI is a thin terminal adapter over public library APIs.
- [x] No daemon or web package code is added by this handoff.
- [x] The root wheel still packages only `aflow`.
- [x] Root docs describe only shipped library and CLI behavior.
- [x] No debug scaffolding, placeholder API types, or duplicated startup logic remain.
