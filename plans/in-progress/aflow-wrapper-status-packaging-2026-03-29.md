# Aflow wrapper identity cleanup, live Rich status banner, and uv packaging

## Summary

Done means all wrapper-owned `ralph` naming is removed in favor of `aflow`, the controller shows a persistent live Rich status banner for the active run, and the project can be installed as a system-wide `uv` tool that exposes a single `aflow` command.

Observable end state:

- Running `aflow/aflow --harness codex --model gpt-5.4 path/to/plan.md` uses `aflow` as the program identity, imports `aflow.cli`, and writes run artifacts under `.aflow/runs/`.
- While a harness turn is running, the terminal shows a persistent Rich banner that continuously refreshes elapsed time and the current run state without streaming raw harness stdout/stderr.
- The banner shows: total elapsed runtime, harness, model, effort level or `none`, current checkpoint index and total checkpoint count, current checkpoint name, current turn number and max turns, accumulated issue count, and the active plan file.
- The controller continues to store raw prompts, argv, stdout/stderr, and per-turn metadata on disk, but those details stay out of the live terminal banner.
- `uv tool install /Users/evren/code/agent_flow` installs a single `aflow` command, and `aflow --help` resolves into the packaged CLI.

## Files

Modify:

- `README.md`
- `.gitignore`
- `aflow/README.md`
- `aflow/__init__.py`
- `aflow/aflow`
- `aflow/cli.py`
- `aflow/controller.py`
- `aflow/plan.py`
- `aflow/run_state.py`
- `aflow/runlog.py`
- `aflow/tests/test_ralph.py` if kept temporarily during the rename step

Create:

- `pyproject.toml`
- `aflow/status.py`
- `aflow/tests/test_aflow.py` if the old test module is renamed instead of edited in place

Delete:

- `aflow/tests/test_ralph.py` after the renamed `aflow/tests/test_aflow.py` exists and is green

If any other wrapper-only file still contains `ralph` after the initial search, update it in the same change. Do not rename unrelated RALF skills, plugin trees, or historical plan files outside the wrapper-owned surface.

## Public Interfaces And Data Shape Changes

- CLI identity changes from `prog="ralph"` to `prog="aflow"`.
- The repo-local shell launcher remains `aflow/aflow`, but it must execute `python3 -m aflow.cli`.
- Packaging adds one console script only:
  - `aflow = aflow.cli:main`
- Run storage moves from `.ralf/runs/` to `.aflow/runs/`. Do not keep a fallback write path to `.ralf`.
- `PlanSnapshot` must grow enough state for the live banner:
  - `total_checkpoint_count: int`
  - `current_checkpoint_index: int | None`
- Controller runtime state / persisted run metadata must add enough data to render and audit banner state:
  - run start timestamp
  - active turn number
  - issue count
  - current phase / status message
  - last snapshot with total checkpoint information

## Sequential Implementation Steps

1. Finish the wrapper rename to `aflow`.
   - Update user-facing names and import targets in `aflow/cli.py`, `aflow/aflow`, `aflow/__init__.py`, `aflow/README.md`, wrapper tests, and the wrapper summary in `README.md`.
   - Rename wrapper test imports from `ralph.*` to `aflow.*`.
   - Rename the wrapper test module to `aflow/tests/test_aflow.py` so no wrapper-owned `ralph` filename remains.
   - Keep the underlying RALF concept names in unrelated skills and plugin docs untouched.

2. Rename wrapper-owned runtime paths from `.ralf` to `.aflow`.
   - Update `aflow/runlog.py` to create and prune `.aflow/runs/`.
   - Update `.gitignore`, wrapper docs, and tests to assert `.aflow/runs/`.
   - Do not implement automatic migration logic in v1. Old `.ralf/` directories may remain on disk but new runs must not write there.

3. Extend plan parsing so banner-relevant checkpoint metadata exists.
   - In `aflow/plan.py`, keep the existing checkpoint parsing rules, but also compute:
     - total checkpoint count
     - current checkpoint index as a 1-based index for the first incomplete checkpoint, or `None` when complete
   - Preserve the current invalid-plan behavior for checked headings with unchecked steps.
   - Ensure `PlanSnapshot.signature` continues to reflect progress semantics only. Do not include purely informational fields if that would break stagnation detection.

4. Add explicit runtime status models for live display and persisted metadata.
   - In `aflow/run_state.py`, add fields to track:
     - `run_started_at`
     - `active_turn`
     - `issues_accumulated`
     - `status_message`
   - Define issue counting exactly:
     - increment by 1 when a completed turn leaves checkpoint progress unchanged
     - increment by 1 for a fatal non-zero harness exit
     - increment by 1 for a fatal post-turn plan parse / missing-plan failure
     - do not increment for normal incomplete turns that made progress
   - Keep `stagnation_turns` separate from issue count. The banner must show issue count; the controller may still use `stagnation_turns` to enforce the stagnation limit.

5. Introduce a Rich-based live banner renderer.
   - Create `aflow/status.py` to isolate Rich rendering and avoid bloating controller logic.
   - Use the `rich` Python library directly, with a persistent `Live` view built from `Panel` plus a compact grid/table layout. The referenced `rich-cli` README is a visual/style reference only, not a runtime dependency.
   - Render these fields in the banner on every refresh:
     - elapsed runtime since `run_started_at`
     - harness
     - model
     - effort
     - checkpoint `current/total`
     - checkpoint name
     - turn `current/max`
     - issue count
     - plan file path
     - current status text such as `initializing`, `running turn 3`, `completed`, or `failed`
   - Keep banner output to stderr so stdout remains available for future scripting if needed.

6. Refactor turn execution so the banner can refresh while a harness subprocess is still running.
   - Replace the single blocking `subprocess.run(...)` call in `aflow/controller.py` with a helper built around `subprocess.Popen`.
   - Use a loop that waits with short timeouts, refreshes the Rich `Live` banner, and then collects stdout/stderr when the process finishes.
   - Do not stream harness stdout/stderr to the terminal. Continue writing full stdout/stderr to the turn artifact files only.
   - Preserve existing behavior around return codes, prompt capture, and per-turn artifact writing.

7. Persist the live-status fields into run metadata.
   - Update `aflow/runlog.py` so `run.json` captures the same durable status information that drives the banner:
     - `run_started_at`
     - `active_turn`
     - `issues_accumulated`
     - `status_message`
     - `last_snapshot` including total checkpoint information
   - Write metadata at initialization, turn start, turn completion, and final completion/failure so the on-disk run state matches what the live banner showed.
   - Keep existing per-turn artifact files and schemas unless a small additive field is required for consistency.

8. Add standard Python packaging for `uv`.
   - Create `pyproject.toml` using a standard build backend, with distribution name `aflow`.
   - Declare the Rich dependency in project dependencies.
   - Configure package discovery for `aflow` and its subpackages.
   - Expose the console script entry point `aflow = aflow.cli:main`.
   - Point package metadata at an existing Markdown readme, and keep the version as an explicit static placeholder suitable for later registry publishing.
   - Do not add `setup.py`, `setup.cfg`, or extra packaging files unless the chosen backend truly requires them.

9. Update wrapper documentation and install guidance.
   - Update `aflow/README.md` to document:
     - the `aflow` identity
     - `.aflow/runs/`
     - the live banner fields
     - local repo usage via `aflow/aflow`
     - packaged usage via `uv tool install /Users/evren/code/agent_flow`
   - Update the wrapper section in root `README.md` to point to `aflow/` and the new install command without disturbing unrelated RALF sections.

10. Expand tests to cover rename, live-status state, and packaging-visible behavior.
   - Update all wrapper tests to import `aflow`.
   - Add parser tests for `total_checkpoint_count` and `current_checkpoint_index`.
   - Add controller tests that assert:
     - unchanged turns increment `issues_accumulated`
     - fatal non-zero exits increment `issues_accumulated`
     - run metadata includes `run_started_at`, `active_turn`, `issues_accumulated`, and the expanded snapshot fields
   - Add launcher tests that assert `.aflow/runs/` is used.
   - Add CLI tests that assert `build_parser().prog == "aflow"`.
   - Add a minimal packaging smoke test by invoking the installed entry point through `uv`.

## Constraints And Non-Goals

- Do not rename unrelated RALF skills, plugin directories, or legacy docs that are not part of the wrapper surface.
- Do not shell out to the `rich` CLI tool. Use the `rich` Python library inside the controller.
- Do not stream harness stdout/stderr live in this change.
- Do not weaken current failure handling, checkpoint parsing rules, or artifact capture.
- Do not preserve a `ralph` or `ralf` console-script alias in package metadata.
- Do not keep writing new run state under `.ralf/`.

## Test Plan

- `python3 -m py_compile /Users/evren/code/agent_flow/aflow/*.py /Users/evren/code/agent_flow/aflow/harnesses/*.py`
- `shellcheck /Users/evren/code/agent_flow/aflow/aflow`
- `python3 -m unittest /Users/evren/code/agent_flow/aflow/tests/test_aflow.py`
- `python3 -m unittest /Users/evren/code/agent_flow/aflow/tests/test_aflow.py -k parser`
- `python3 -m unittest /Users/evren/code/agent_flow/aflow/tests/test_aflow.py -k launcher`
- `rg -n '\\bralph\\b' /Users/evren/code/agent_flow/aflow /Users/evren/code/agent_flow/README.md /Users/evren/code/agent_flow/pyproject.toml`
- `uv build /Users/evren/code/agent_flow`
- `uv tool run --from /Users/evren/code/agent_flow aflow --help`

Manual end-to-end check:

- `cd /Users/evren/code/agent_flow && aflow/aflow --harness codex --model gpt-5.4 path/to/plan.md`
  - confirm the Rich banner stays visible during the turn
  - confirm raw harness output is not streamed live
  - confirm run artifacts land under `.aflow/runs/`

Optional final install verification on the host:

- `uv tool install --force /Users/evren/code/agent_flow`
- `aflow --help`

## Assumptions

- Full wrapper-owned identity should move to `aflow`, including hidden run storage, with no backward-compatibility alias.
- The only installed command should be `aflow`.
- The requested banner is a live terminal status panel for the current run, not a historical dashboard over prior runs.
- The “errors accumulated” field should count controller-detected issues during the current run, with unchanged-progress turns treated as issues because that matches the user’s example.
- Using `rich` as a Python dependency is acceptable; `rich-cli` itself is not needed at runtime.
