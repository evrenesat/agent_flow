# RALF Gemini Headless Hardening And Extra Prompt Support

## Objective

Make `scripts/ralf` reliable for Gemini headless runs by avoiding the Ralph extension prompt-mismatch failure, and support freeform extra instructions after the plan path. The finished behavior is:

- `ralf path/to/plan.md` still runs the standard checkpoint prompt against that plan.
- `ralf path/to/plan.md extra guidance here` appends the extra guidance to the generated planner prompt and passes that exact final prompt into the Ralph loop.
- Gemini no longer starts the loop by sending `/ralph:loop ...` through `gemini -p`; instead the wrapper initializes Ralph state first, then runs Gemini with the exact task prompt so the hook sees the same prompt on later iterations.
- `--dry-run` shows the actual setup and Gemini commands that would be used.
- Failures for missing plan files, missing `gemini`, or missing Ralph setup script are explicit and actionable.

## Files

Modify:

1. `scripts/ralf`
2. `README.md`

Create:

3. `tests/ralf.sh`

Do not modify:

4. `scripts/ralf_offf.sh`
5. `opencode-ralph/**`

## Assumptions And Defaults

- Runtime remains Gemini-only. Do not add OpenCode logic back into `scripts/ralf`.
- Default completion promise stays `PLAN_COMPLETE`.
- Default max iteration count stays `20`.
- Default plan file remains `IMPLEMENTATION_PLAN.md`.
- The active Gemini Ralph extension setup script is available at `~/.gemini/extensions/ralph/scripts/setup.sh` unless overridden by a new env var.
- Extra prompt support is only defined when the user explicitly supplies a plan path. We are not redefining `ralf "prompt text"` to mean “use default plan plus prompt”.
- Remaining positional arguments after the explicit plan path are joined with single spaces and appended verbatim to the generated planner prompt after a blank line, with no wrapper heading.

## Constraints

- Do not call `/ralph:loop` through `gemini -p` anymore.
- Do not silently fall back when the Ralph setup script is missing. Exit with a clear error.
- Do not treat unknown options as plan files.
- Do not change `scripts/ralf_offf.sh`.
- Do not remove the existing base planner instructions about reading the first unchecked checkpoint, verifying, marking the plan, and committing.

## Edge Cases That Must Be Handled

- `--dry-run` with only the default plan file.
- `--dry-run` with an explicit plan and appended extra prompt text.
- Missing plan file.
- Missing `gemini` binary.
- Missing Ralph setup script.
- Extra prompt text containing spaces because the user quoted it in the shell.
- Extra prompt omitted entirely.
- Unknown flag input such as `--foo`.

## Sequential Steps

1. Update `scripts/ralf` argument parsing and usage output.
   Before:
   - The script loops over all args, treats any non-`--dry-run` token as the plan file, and silently lets the last positional win.
   After:
   - Add `set -euo pipefail`.
   - Add a `usage()` function for `--help`.
   - Parse only supported flags: `--dry-run`, `-h`, `--help`.
   - Capture positionals in order.
   - Resolve the plan file as:
     - explicit first positional if any were provided
     - otherwise `IMPLEMENTATION_PLAN.md`
   - Treat all remaining positionals after the explicit plan file as extra prompt text.
   - Exit on unknown flags instead of reinterpreting them as file paths.

2. Replace the current `/ralph:loop` prompt packing with direct Ralph state initialization.
   Before:
   - `PROMPT` is a single `/ralph:loop "..." --completion-promise ... --max-iterations ...` string.
   - The script runs `gemini -y -p "$PROMPT"`.
   After:
   - Build `BASE_PROMPT` from the existing checkpoint instructions, still referencing the absolute plan path and `PLAN_COMPLETE`.
   - If extra prompt text exists, build `FINAL_PROMPT="${BASE_PROMPT}\n\n${EXTRA_PROMPT}"`.
   - Resolve `RALPH_SETUP_SCRIPT` as:
     - `RALPH_SETUP_SCRIPT` env var if set
     - otherwise `"$HOME/.gemini/extensions/ralph/scripts/setup.sh"`
   - Validate that the plan file exists, `gemini` is on `PATH`, and the setup script file exists before launching.
   - Run `bash "$RALPH_SETUP_SCRIPT" "$FINAL_PROMPT" --completion-promise "PLAN_COMPLETE" --max-iterations 20`.
   - Then run `gemini -y -p "$FINAL_PROMPT"`.
   - This keeps Gemini’s current prompt equal to the stored Ralph `original_prompt`, which avoids the stop-hook mismatch.

3. Make dry-run output reflect the real launch sequence.
   Before:
   - Dry-run prints one synthetic Gemini command that still embeds `/ralph:loop`.
   After:
   - Dry-run prints both commands in execution order:
     - the Ralph setup script invocation
     - the Gemini invocation with the final planner prompt
   - Quote both commands with `printf '%q '` so spaces and quotes are inspectable.

4. Add regression coverage in `tests/ralf.sh`.
   Create a shell test script that uses `mktemp`, a fake `gemini` executable, and a fake Ralph `setup.sh` so the wrapper can be tested without invoking the real model.
   The test script should verify:
   - default plan path is used when no explicit plan path is provided
   - explicit plan path is honored
   - trailing args after the explicit plan path are appended to the prompt
   - dry-run prints setup and Gemini commands, not `/ralph:loop`
   - non-dry-run calls setup first and Gemini second with the same final prompt
   - missing setup script and missing plan file fail with non-zero exit codes

5. Rewrite the `README.md` `ralf` section to match the real script.
   Before:
   - The README claims `ralf` defaults to OpenCode, mentions JSON output, and shows runtime selection flags that do not exist in `scripts/ralf`.
   After:
   - Document `ralf` as the Gemini headless runner.
   - Show the actual usage:
     - `ralf [--dry-run] [path/to/plan.md]`
     - `ralf [--dry-run] path/to/plan.md [extra instructions ...]`
   - State that it initializes Gemini Ralph state via the installed Ralph extension setup script and then runs Gemini on the exact same task prompt.
   - Mention the optional `RALPH_SETUP_SCRIPT` override.
   - Clarify that `scripts/ralf_offf.sh` is not the active runner.

## Verification

Run these commands from `/home/evren/code/agent_flow`:

1. `shellcheck scripts/ralf tests/ralf.sh`
2. `bash tests/ralf.sh`
3. `tmpdir=$(mktemp -d) && printf '### [ ] Checkpoint 1\n' > "$tmpdir/PLAN.md" && ./scripts/ralf --dry-run "$tmpdir/PLAN.md" continue from the next checkpoint only`

Expected checks:

- `shellcheck` returns cleanly.
- `bash tests/ralf.sh` passes.
- Dry-run output shows:
  - a `setup.sh` invocation with the final prompt text
  - a `gemini -y -p ...` invocation with the same final prompt text
  - no `/ralph:loop` wrapper string

## Final Checklist

- [ ] `scripts/ralf` is Gemini-only and no longer shells `/ralph:loop` through `gemini -p`
- [ ] trailing prompt text after an explicit plan path is appended verbatim to the planner prompt
- [ ] dry-run output matches real execution order
- [ ] missing dependency errors are explicit
- [ ] `tests/ralf.sh` passes
- [ ] `shellcheck` passes
- [ ] README matches the real CLI
- [ ] `scripts/ralf_offf.sh` remains unchanged
