# Aflow Opencode And Gemini Harness Support

## Summary

Done means `aflow` accepts `--harness opencode` and `--harness gemini`, launches each CLI in non-interactive full-permission mode, and keeps the existing controller semantics unchanged: one fresh subprocess per turn, prompts and argv captured in `.aflow/runs/`, and plan progress determined only from the on-disk plan file.

When `--effort` is passed with either new harness, the run must continue, `aflow` must print one warning to stderr that the selected harness ignores effort, and the adapter must not add any effort-specific CLI flag. Existing harness behavior for `codex`, `pi`, and `claude` must remain unchanged.

Files to modify or create:

- `aflow/harnesses/base.py`
- `aflow/harnesses/__init__.py`
- `aflow/harnesses/opencode.py` (new)
- `aflow/harnesses/gemini.py` (new)
- `aflow/cli.py`
- `aflow/tests/test_aflow.py`
- `README.md`
- `pyproject.toml`

Must not modify:

- controller logic in `aflow/controller.py`
- run-log schema in `aflow/runlog.py`
- status banner rendering in `aflow/status.py`

## Public Interface Changes

- `aflow --harness` choices expand from `codex|pi|claude` to `codex|pi|claude|opencode|gemini`.
- `HarnessAdapter` gains a `supports_effort: bool` attribute so the CLI can warn generically for adapters that ignore effort.
- README and package metadata must document the two new harness names and the fact that `--effort` is ignored, with a warning, for those harnesses.

## Implementation Changes

1. Extend the adapter contract and registry.
   Before: `HarnessAdapter` only requires `name` and `build_invocation()`, and `ADAPTERS` only registers `codex`, `pi`, and `claude`.
   After: add `supports_effort: bool` to the protocol, set `True` on existing adapters, import/register `OpencodeAdapter` and `GeminiAdapter`, and let `build_parser()` pick up the new harness names through `sorted(ADAPTERS)`.

2. Add `aflow/harnesses/opencode.py`.
   Before: no opencode adapter exists.
   After: create `OpencodeAdapter` with:
   - `name = "opencode"`
   - `supports_effort = False`
   - `prompt_mode = "prefix-system-into-user-prompt"` because `opencode run --help` exposes no system-prompt flag
   - `effective_prompt = system_prompt + "\\n\\n" + user_prompt`
   - argv exactly:
     `("opencode", "run", "--model", model, "--format", "default", "--dir", str(repo_root), effective_prompt)`
   - no invented sandbox, approval, or effort flags, because `opencode run --help` does not document them

3. Add `aflow/harnesses/gemini.py`.
   Before: no gemini adapter exists.
   After: create `GeminiAdapter` with:
   - `name = "gemini"`
   - `supports_effort = False`
   - `prompt_mode = "prefix-system-into-user-prompt"` because `gemini --help` exposes no system-prompt flag
   - `effective_prompt = system_prompt + "\\n\\n" + user_prompt`
   - argv exactly:
     `("gemini", "--prompt", effective_prompt, "--model", model, "--approval-mode", "yolo", "--sandbox=false", "--output-format", "text")`
   - use `--prompt` rather than positional query so the harness is explicitly headless
   - use `--approval-mode yolo` plus `--sandbox=false` for the full-permission workflow surfaced by help output
   - do not add any effort-specific flag

4. Add one warning path in `aflow/cli.py`.
   Before: `main()` passes `args.effort` straight into `ControllerConfig` and prints nothing special per harness.
   After:
   - resolve the selected adapter once after parsing
   - if `args.effort` is set and `adapter.supports_effort` is `False`, print one stderr line before `run_controller()` starts:
     `warning: harness '<name>' ignores --effort; continuing without an effort flag`
   - continue passing the original `args.effort` into `ControllerConfig` so run metadata and the status banner still show the requested config value
   - do not alter behavior for harnesses with `supports_effort = True`

5. Expand automated tests in `aflow/tests/test_aflow.py`.
   Add or update:
   - adapter imports for `OpencodeAdapter` and `GeminiAdapter`
   - parser/choices coverage that proves `opencode` and `gemini` are accepted harness values
   - adapter argv tests for both new adapters without effort
   - adapter argv tests for both new adapters with `effort="high"` proving the argv is unchanged and contains no effort flag
   - a warning-path CLI test for each new harness that runs the launcher with `--effort high`, succeeds with a fake harness, and asserts:
     - exit code `0`
     - stderr contains the warning once
     - turn `argv.json` contains the expected headless/full-permission command
     - no effort-specific argv element is present
   - update any helper loops that currently create fake binaries for only `("codex", "pi", "claude")` so they also create `opencode` and `gemini`, ideally by switching to one shared tuple constant to avoid future drift

6. Update docs and package metadata.
   In `README.md`:
   - change the intro and support list from three harnesses to five
   - add at least one usage example for `opencode` or `gemini`
   - expand the `--effort` table with two new rows:
     - `opencode`: ignored, warning emitted, no extra flag
     - `gemini`: ignored, warning emitted, no extra flag
   - briefly state the exact non-interactive/full-permission strategy:
     - `opencode` uses `opencode run` with the prefixed prompt and documented `--dir`
     - `gemini` uses `--prompt`, `--approval-mode yolo`, and `--sandbox=false`
   In `pyproject.toml`:
   - update the package description so it reflects the new supported harness set

## Edge Cases And Constraints

- Do not add adapter-specific branching inside the controller; keep harness differences isolated to adapter classes and the one CLI warning path.
- Do not pass undocumented `opencode` flags such as guessed permission or effort flags.
- Do not map `--effort` onto `opencode --variant`; the user explicitly wants effort ignored for `opencode`.
- Do not use Gemini interactive mode (`query` positional or `--prompt-interactive`) for the adapter.
- Do not suppress or drop the configured `effort` value from `run.json`; warning plus ignored argv is the chosen behavior.
- Keep `effective_prompt` as the final user payload for both new adapters so prompt artifacts stay readable and consistent with `codex`.

## Verification

Run after implementation:

1. `python3 -m py_compile /Users/evren/code/agent_flow/aflow/*.py /Users/evren/code/agent_flow/aflow/harnesses/*.py`
2. `python3 -m unittest /Users/evren/code/agent_flow/aflow/tests/test_aflow.py`

Manual smoke checks if needed after tests:

1. `opencode run --help`
2. `gemini --help`
3. `python3 -m aflow --help`

Final checklist:

- [ ] `aflow --harness` accepts `opencode` and `gemini`
- [ ] `OpencodeAdapter` and `GeminiAdapter` build the exact argv shapes above
- [ ] `--effort` on `opencode` or `gemini` warns once and does not change argv
- [ ] existing `codex`, `pi`, and `claude` tests still pass unchanged
- [ ] README and `pyproject.toml` reflect the expanded harness support
- [ ] no controller, run-log, or banner behavior regresses outside the intentional warning
