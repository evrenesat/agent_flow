# Ralph self-contained wrapper, effort support, and layout refactor

## Objective
Make the current `ralf-universal` wrapper a self-contained top-level `ralph/` unit. After this change:

- the only supported entrypoint is `ralph/ralph`
- the wrapper accepts optional `--effort <level>`
- `--effort` is mapped into the underlying harness call only when the user supplies it
- all wrapper-specific code, launcher, local README, and wrapper tests live under `ralph/`
- the old split across `scripts/`, `extensions/`, and root `tests/` is removed for this wrapper

Observable done state:

- `ralph/ralph --harness codex --model gpt-5.4 path/to/plan.md` still runs the controller without any reasoning-effort flag in the spawned Codex command
- `ralph/ralph --harness codex --model gpt-5.4 --effort high path/to/plan.md` spawns Codex with `-c model_reasoning_effort='"high"'`
- `ralph/ralph --harness pi --model sonnet --effort high path/to/plan.md` spawns Pi with `--models sonnet:high`
- `ralph/ralph --harness claude --model sonnet --effort low path/to/plan.md` spawns Claude with `--effort low`
- `scripts/ralf-universal` no longer exists and root docs no longer advertise it

## Files
Create or move into one directory:

- create `ralph/README.md`
- create `ralph/ralph`
- create `ralph/__init__.py`
- create `ralph/cli.py`
- create `ralph/controller.py`
- create `ralph/plan.py`
- create `ralph/run_state.py`
- create `ralph/runlog.py`
- create `ralph/harnesses/__init__.py`
- create `ralph/harnesses/base.py`
- create `ralph/harnesses/codex.py`
- create `ralph/harnesses/pi.py`
- create `ralph/harnesses/claude.py`
- create `ralph/tests/test_ralph.py`

Modify:

- modify `README.md`

Delete after the moved copies are in place and tests pass:

- delete `scripts/ralf-universal`
- delete `extensions/ralf_universal/__init__.py`
- delete `extensions/ralf_universal/cli.py`
- delete `extensions/ralf_universal/controller.py`
- delete `extensions/ralf_universal/plan.py`
- delete `extensions/ralf_universal/run_state.py`
- delete `extensions/ralf_universal/runlog.py`
- delete `extensions/ralf_universal/harnesses/__init__.py`
- delete `extensions/ralf_universal/harnesses/base.py`
- delete `extensions/ralf_universal/harnesses/codex.py`
- delete `extensions/ralf_universal/harnesses/pi.py`
- delete `extensions/ralf_universal/harnesses/claude.py`
- delete `tests/test_ralf_universal.py`

## Before / after

### Layout
- Before: launcher is `scripts/ralf-universal`, implementation is under `extensions/ralf_universal/`, and tests live in `tests/test_ralf_universal.py`.
- After: everything specific to this wrapper lives under `ralph/`, including the launcher and wrapper-local tests.

### CLI contract
- Before: parser program name is `ralf-universal`; required args are `--harness` and `--model`; no effort option exists.
- After: parser program name is `ralph`; required args remain `--harness` and `--model`; optional `--effort <level>` is accepted and stored in controller config.

### Harness argv
- Before: no adapter can express reasoning effort.
- After:
  - Codex adds `-c` and `model_reasoning_effort='"<effort>"'` only when `config.effort` is not `None`.
  - Pi uses `--models <model>:<effort>` only when `config.effort` is not `None`; otherwise it continues using `--model <model>`.
  - Claude adds `--effort <effort>` only when `config.effort` is not `None`.

### Logging and metadata
- Before: `run.json` records harness, model, limits, and instructions, but not reasoning effort.
- After: `run.json` also records the selected `effort` value, using `null` when omitted, so the run configuration is auditable.

## Sequential implementation steps

1. Create the new self-contained package layout under `ralph/`.
   - Copy the current wrapper modules into `ralph/` with the same behavior first, then adjust imports from `extensions.ralf_universal` to `ralph`.
   - Add `ralph/__init__.py` and `ralph/harnesses/__init__.py` so `python3 -m ralph.cli` works.
   - Do not change controller logic in this step beyond import paths and obvious rename strings such as `prog="ralph"`.
   - Verification: `python3 -m py_compile /Users/evren/code/agent_flow/ralph/*.py /Users/evren/code/agent_flow/ralph/harnesses/*.py`

2. Add the new entrypoint at `ralph/ralph`.
   - Before: `scripts/ralf-universal` changes into repo root and runs `python3 -m extensions.ralf_universal.cli`.
   - After: `ralph/ralph` changes into the repo root and runs `python3 -m ralph.cli`.
   - Keep it executable and shellcheck-clean.
   - Verification: `shellcheck /Users/evren/code/agent_flow/ralph/ralph`

3. Extend the public CLI and controller config with optional effort.
   - Add `--effort` to `ralph/cli.py` as an optional string argument named exactly `--effort`.
   - Add `effort: str | None = None` to `ControllerConfig`.
   - Thread `args.effort` into `ControllerConfig` and through any metadata helpers that need it.
   - Do not make `--effort` required.
   - Do not normalize or remap user input values in the CLI layer.
   - Verification: add unit assertions in `ralph/tests/test_ralph.py` for parsing with and without `--effort`.

4. Update each harness adapter to map `effort` exactly as requested.
   - Step 4 uses the config field added in Step 3.
   - `ralph/harnesses/base.py`: extend the adapter protocol signature to accept `effort: str | None`.
   - `ralph/controller.py`: pass `config.effort` into `adapter.build_invocation(...)`.
   - `ralph/harnesses/codex.py`: when effort is set, inject `-c` and `model_reasoning_effort='\"<effort>\"'` into argv before the final prompt argument; when absent, omit both tokens entirely.
   - `ralph/harnesses/pi.py`: when effort is set, replace the current `--model <model>` pair with `--models <model>:<effort>`; when absent, keep the existing `--model <model>` behavior and do not add any thinking-related flag.
   - `ralph/harnesses/claude.py`: when effort is set, add `--effort <effort>` next to the existing model flag; when absent, omit it entirely.
   - Do not add harness-specific validation tables. Let each harness reject unsupported levels or unsupported models.
   - Verification: adapter unit tests must assert exact argv tuples for each harness, both with and without effort where the omission behavior matters.

5. Persist the new config field in run metadata and artifact expectations.
   - Update `ralph/runlog.py` so `run.json` includes `"effort": <value-or-null>`.
   - `argv.json` does not need schema changes because it already records the final argv list; adapter tests and end-to-end tests should cover the new argv forms.
   - Verification: end-to-end tests should assert `run_json["effort"]` for at least one run with effort and one without.

6. Move and rename the wrapper tests into the self-contained directory.
   - Move the current wrapper-focused test coverage from `tests/test_ralf_universal.py` into `ralph/tests/test_ralph.py`.
   - Update helper names, launcher paths, copied source paths, and import paths to the new `ralph/` layout.
   - Add new adapter and launcher assertions for `--effort`:
     - Codex launcher with `--effort high` writes argv containing `-c` and `model_reasoning_effort='"high"'`.
     - Pi launcher with `--effort high` writes argv containing `--models` and `sonnet:high`.
     - Claude launcher with `--effort low` writes argv containing `--effort` and `low`.
     - A no-effort run for each harness still omits the effort-specific tokens.
   - Keep the existing stagnation, max-turn, non-zero-exit, and retention coverage intact.
   - Verification: `python3 -m unittest /Users/evren/code/agent_flow/ralph/tests/test_ralph.py`

7. Update documentation so the wrapper is self-contained and the old name disappears.
   - Add `ralph/README.md` as the wrapper-local source of truth. It should cover purpose, usage, supported harnesses, optional `--effort`, limits, and log location.
   - Update root `README.md` to reference the new `ralph/` directory and the new entrypoint `ralph/ralph`, with only a short summary instead of the old dedicated `ralf-universal` section.
   - Remove remaining non-plan references to `ralf-universal`, `extensions/ralf_universal`, and `scripts/ralf-universal`.
   - Verification: `rg -n "ralf-universal|extensions/ralf_universal|scripts/ralf-universal" /Users/evren/code/agent_flow -g '!plans/**'`

8. Remove the old split layout after the new path is green.
   - Delete the old launcher and old implementation tree under `extensions/ralf_universal/`.
   - Delete the old root-level wrapper test file after its coverage exists under `ralph/tests/`.
   - Verification:
     - `test ! -e /Users/evren/code/agent_flow/scripts/ralf-universal`
     - `test ! -e /Users/evren/code/agent_flow/extensions/ralf_universal`
     - `test ! -e /Users/evren/code/agent_flow/tests/test_ralf_universal.py`

## Edge cases and requirements

- If `--effort` is omitted, the wrapper must not emit any harness effort flag or codex config override.
- The wrapper must still require `--model` even when `--effort` is supplied.
- The wrapper must not silently translate effort levels between harnesses. `high` stays `high`, `low` stays `low`, and unsupported values are left to the harness CLI to reject.
- Pi’s effort path must not pass both `--model` and `--models` in the same invocation.
- Codex’s effort path must preserve the final prompt as the final argv element.
- Existing controller behavior, checkpoint parsing rules, run retention, and failure handling must remain unchanged aside from the new config field and renamed paths.
- Keep `.ralf/runs/` as the run log location. This rename only changes the wrapper name and layout, not the hidden run directory convention.
- Do not add dependencies, packaging metadata, or installation logic as part of this change.
- Do not keep a compatibility shim at `scripts/ralf-universal`.

## Verification commands

Per-step checks:

1. `python3 -m py_compile /Users/evren/code/agent_flow/ralph/*.py /Users/evren/code/agent_flow/ralph/harnesses/*.py`
2. `shellcheck /Users/evren/code/agent_flow/ralph/ralph`
3. `python3 -m unittest /Users/evren/code/agent_flow/ralph/tests/test_ralph.py -k adapter`
4. `python3 -m unittest /Users/evren/code/agent_flow/ralph/tests/test_ralph.py -k launcher`
5. `python3 -m unittest /Users/evren/code/agent_flow/ralph/tests/test_ralph.py`
6. `rg -n "ralf-universal|extensions/ralf_universal|scripts/ralf-universal" /Users/evren/code/agent_flow -g '!plans/**'`

Final verification:

- `python3 -m unittest /Users/evren/code/agent_flow/ralph/tests/test_ralph.py`
- `python3 -m py_compile /Users/evren/code/agent_flow/ralph/*.py /Users/evren/code/agent_flow/ralph/harnesses/*.py`
- `shellcheck /Users/evren/code/agent_flow/ralph/ralph`
- `test -x /Users/evren/code/agent_flow/ralph/ralph`
- `test ! -e /Users/evren/code/agent_flow/scripts/ralf-universal`
- `test ! -e /Users/evren/code/agent_flow/extensions/ralf_universal`
- `test ! -e /Users/evren/code/agent_flow/tests/test_ralf_universal.py`

## Final checklist

- [ ] Only supported entrypoint is `ralph/ralph`
- [ ] All wrapper-specific code, README, and tests live under `ralph/`
- [ ] `--effort` is optional and omitted from harness argv when not supplied
- [ ] Codex uses `-c model_reasoning_effort='"<effort>"'` when effort is supplied
- [ ] Pi uses `--models <model>:<effort>` when effort is supplied
- [ ] Claude uses `--effort <effort>` when effort is supplied
- [ ] `run.json` records the selected effort
- [ ] Root README points to `ralph/` and no longer documents `ralf-universal`
- [ ] No compatibility shim remains at `scripts/ralf-universal`
- [ ] No debug output or commented-out code remains

## Assumptions

- Public rename scope is the wrapper entrypoint and its self-contained directory, not the underlying RALF checkpoint concept or `.ralf/` run directory name.
- The wrapper should accept effort as pass-through text because harness-supported values differ and may change independently.
- The Pi adapter should follow the exact user-requested style `--models <model>:<effort>` rather than introducing `--thinking`.
