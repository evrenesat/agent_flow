# Config-backed defaults and per-harness profiles

## Objective / done state

`aflow` should be able to resolve harness, model, and effort from a user config file so the CLI no longer requires `--model`, and can also omit `--harness` when a default harness exists in config.

Done means:

- `aflow path/to/plan.md` works when `~/.config/aflow/aflow.toml` defines `default_harness` and that harness has enough defaults.
- `aflow --profile turbo path/to/plan.md` resolves `turbo` only under the selected harness and uses its configured `model` and/or `effort`.
- Explicit CLI flags override config values with precedence `CLI > profile > harness defaults > global`.
- If no model resolves after merge, `aflow` omits the harness model flag and lets the underlying CLI use its own default model.
- Run metadata stores `"model": null` when the resolved model is absent, and the status banner shows `default`.
- Missing config file is allowed and does not change current behavior.
- Invalid TOML, unknown harness names in config, or unknown profile names fail fast in the CLI with a clear error before controller execution starts.

## Proposed config shape

Use a single user config file at `~/.config/aflow/aflow.toml`.

Use this TOML structure:

```toml
default_harness = "opencode"

[harness.opencode]
model = "zai-coding-plan/glm-4.7"

[harness.opencode.profiles.turbo]
model = "zai-coding-plan/glm-5-turbo"

[harness.codex]
model = "gpt-5.4"
effort = "high"
```

Rules:

- Keep keys as `model` and `effort`, not `default_model` / `default_effort`.
- Omit `model` or `effort` entirely when unset. Do not use empty strings as sentinels.
- Support only per-harness profiles. Do not add a global profile table.
- Config scope in this change is limited to `default_harness`, harness-level `model` / `effort`, and harness-level `profiles.<name>.model` / `profiles.<name>.effort`.

## Files to change

- `aflow/cli.py`
- `aflow/run_state.py`
- `aflow/controller.py`
- `aflow/status.py`
- `aflow/runlog.py`
- `aflow/harnesses/base.py`
- `aflow/harnesses/codex.py`
- `aflow/harnesses/pi.py`
- `aflow/harnesses/claude.py`
- `aflow/harnesses/opencode.py`
- `aflow/harnesses/gemini.py`
- `aflow/tests/test_aflow.py`
- `README.md`
- `aflow/config.py` (new)

## Before / after by subsystem

### CLI and config resolution

- Before: `aflow/cli.py` requires `--harness` and `--model`, and has no config loading or profile concept.
- After: `aflow/cli.py` accepts optional `--harness`, optional `--model`, and new optional `--profile`. It loads `~/.config/aflow/aflow.toml`, resolves settings with precedence `CLI > profile > harness defaults > global`, and only errors when harness cannot be resolved or when config/profile is invalid.

### Runtime config model

- Before: `ControllerConfig.model` is always `str`.
- After: `ControllerConfig.model` is `str | None`. The resolved harness remains required before controller startup.

### Harness invocation

- Before: every adapter requires a model string and always includes the harness-specific model flag.
- After: all adapters accept `model: str | None` and omit the model flag when `model is None`.
- Pi special case:
  when `model` and `effort` are both present, keep current `--models <model>:<effort>` behavior.
  when `model` is absent and `effort` is present, use `--thinking <effort>` instead of inventing a synthetic model string.

### Status and run metadata

- Before: banner and run metadata always display/store a concrete model string.
- After: banner renders `default` when resolved model is absent, and `run.json` stores `"model": null`.

### Docs

- Before: README says `--harness` and `--model` are required and has no config file section.
- After: README documents the config path, TOML schema, precedence, `--profile`, optional `--harness`, optional `--model`, and omitted-model behavior per harness.

## Sequential implementation steps

1. Add config loading and validation in `aflow/config.py`.
   - Define small dataclasses or typed dict-like structures for:
     - whole config
     - harness defaults
     - harness profile overrides
   - Implement `load_user_config()` reading only `Path.home() / ".config" / "aflow" / "aflow.toml"` via `tomllib`.
   - Missing file returns an empty config object.
   - Invalid TOML raises a dedicated config error with the file path in the message.
   - Validate harness names against `ADAPTERS`.
   - Expose a resolver that takes CLI values `harness`, `model`, `effort`, `profile` and returns fully resolved values.

2. Update CLI parsing and resolution in `aflow/cli.py`.
   - Make `--harness` optional.
   - Make `--model` optional.
   - Add `--profile`.
   - Resolve config before constructing `ControllerConfig`.
   - If no harness resolves, fail with a user-facing error that says `--harness` is required unless `default_harness` is configured.
   - If `--profile` is supplied but the resolved harness does not contain that profile, fail with a clear error naming the harness and profile.
   - Keep existing defaults for `--max-turns`, `--stagnation-limit`, `--keep-runs`, and `--effort`.

3. Make runtime config types nullable where needed.
   - In `aflow/run_state.py`, change `ControllerConfig.model` to `str | None`.
   - In `aflow/harnesses/base.py`, change the protocol signature to `model: str | None`.
   - In `aflow/status.py`, change `config_model` arguments and stored fields to `str | None`.
   - In `aflow/runlog.py`, allow `model` to serialize as JSON null.

4. Update harness adapters to omit model flags safely.
   - `aflow/harnesses/codex.py`: append `--model <value>` only when `model` is present; keep effort handling unchanged.
   - `aflow/harnesses/claude.py`: append `--model <value>` only when present; keep `--effort` independent.
   - `aflow/harnesses/opencode.py`: append `--model <value>` only when present.
   - `aflow/harnesses/gemini.py`: append `--model <value>` only when present.
   - `aflow/harnesses/pi.py`:
     - `model and effort` => `--models <model>:<effort>`
     - `model and no effort` => `--model <model>`
     - `no model and effort` => `--thinking <effort>`
     - `no model and no effort` => omit both model-related flags
   - Do not change tool lists, prompt assembly, or permission/sandbox flags.

5. Update banner/controller plumbing.
   - In `aflow/controller.py`, pass nullable model through unchanged.
   - In `aflow/status.py`, render `default` when `config_model is None`.
   - Do not change effort display logic beyond current `none` fallback.

6. Extend tests in `aflow/tests/test_aflow.py`.
   - Parser/config resolution tests:
     - configless invocation without `--harness` still fails
     - configless invocation without `--model` but with explicit `--harness` succeeds and leaves model unresolved
     - config-backed default harness resolves when `--harness` is omitted
     - CLI `--model` overrides profile/harness defaults
     - profile overrides harness defaults
     - unknown profile raises a clear error
     - invalid config file raises a clear error
   - Adapter tests:
     - each adapter omits `--model` when `model=None`
     - Pi uses `--thinking <effort>` when `model=None` and `effort` is set
   - Banner/runlog tests:
     - `build_banner(..., config_model=None, ...)` still renders
     - run metadata writes JSON `null` for model
   - End-to-end launcher tests:
     - launcher succeeds with config-supplied default harness/model
     - launcher succeeds with explicit harness and no model, and the fake harness argv omits the model flag
     - launcher succeeds with `--profile turbo`
     - launcher fails clearly when `--profile` does not exist for the resolved harness
   - For launcher tests, set `HOME` in `_launcher_environment()` to a temp directory and create `~/.config/aflow/aflow.toml` there.

7. Update README.
   - Replace required-flag wording.
   - Add a config section with the exact TOML example above.
   - Document precedence as `CLI > profile > harness defaults > global`.
   - Document that `--profile` is resolved under the selected harness only.
   - Document that omitted models are passed through to the harness CLI as “use its default.”

## Edge cases and explicit constraints

- Do not add new runtime dependencies. Use stdlib `tomllib`.
- Do not support project-local config, environment-variable config paths, or global cross-harness profiles in this change.
- Do not let profile selection mutate harness choice. Harness resolves first, then profile is looked up under that harness.
- Do not silently ignore invalid TOML or unknown profile names.
- Do not coerce empty strings to meaningful values. Treat empty-string config values as invalid input if encountered.
- Do not change run-directory layout or prompt content.
- Do not add harness-specific model validation tables. Only validate harness names and config shape.

## Verification

Run these commands from the repo root:

1. `python3 -m unittest aflow.tests.test_aflow.CLITests`
2. `python3 -m unittest aflow.tests.test_aflow.AdaptersTests`
3. `python3 -m unittest aflow.tests.test_aflow.LazyBannerTests`
4. `python3 -m unittest aflow.tests.test_aflow.EndToEndLauncherTests`
5. `python3 -m unittest aflow.tests.test_aflow`

Manual spot checks after implementation:

1. Create a temp HOME with `~/.config/aflow/aflow.toml` containing `default_harness` and a harness model, then run:
   `HOME=/tmp/aflow-home python3 -m aflow path/to/plan.md`
2. Verify `run.json` contains `"model": null` when the resolved model is omitted.
3. Verify turn `argv.json` omits `--model` for codex/claude/gemini/opencode and uses `--thinking` for pi when only effort resolves.

## Final checklist

- [ ] `--profile` exists and is optional
- [ ] `--harness` is optional when `default_harness` is configured
- [ ] `--model` is optional everywhere
- [ ] precedence is exactly `CLI > profile > harness defaults > global`
- [ ] profiles are per-harness only
- [ ] missing config file is non-fatal
- [ ] invalid config and unknown profiles fail clearly before controller run
- [ ] adapters omit model flags when model is unresolved
- [ ] Pi uses `--thinking` when effort is set without a model
- [ ] banner shows `default` for omitted model
- [ ] `run.json` stores `null` for omitted model
- [ ] README documents config path, schema, precedence, and new optional flags
- [ ] test suite passes
