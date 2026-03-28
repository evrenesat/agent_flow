# Codex RALF Hook Manager

## Summary

Replace the current skill-only Codex RALF port with a real repo-local Codex loop manager that uses official Codex hooks. The loop controller must live in this repository's `.codex/` config layer, not inside the plugin manifest, because current official Codex docs document hooks as config-layer files and document plugins as bundles of skills, apps, and MCP servers.

Done means:

- In this repository, Codex CLI and the Codex app can both run a Ralph-style loop without an external shell loop once the session has started.
- The loop is driven by a repo-local `Stop` hook that can continue the session from the first unchecked checkpoint in the active plan until completion or max-iteration exit.
- The loop state is durable on disk and survives crash, restart, and session resume.
- The current `ralph-loop-codex` plugin stops claiming that copied skills alone provide autonomous whole-plan execution.
- The plugin becomes a thin repo-native wrapper and docs surface, not the execution engine.

## Capability Confirmation

Implementation must align with these current product facts that were verified before planning:

- Codex plugins are supported in both the app and CLI, but the documented plugin component model is skills, apps, and MCP servers.
- Codex hooks are officially supported as an experimental config-layer feature via `hooks.json` next to active config layers.
- `Stop` hooks can continue the session by returning a block/continue decision, and Codex automatically turns the hook reason into a new continuation prompt.
- Hooks are repo-local or home-local config artifacts, not a documented first-class plugin component in the current public plugin docs.

Do not plan around undocumented plugin-owned runtime control as the primary architecture.

## Files

Create or modify exactly these repo files:

- `/Users/evren/code/agent_flow/.codex/config.toml`
- `/Users/evren/code/agent_flow/.codex/hooks.json`
- `/Users/evren/code/agent_flow/.codex/hooks/ralf_common.py`
- `/Users/evren/code/agent_flow/.codex/hooks/ralf_session_start.py`
- `/Users/evren/code/agent_flow/.codex/hooks/ralf_stop_continue.py`
- `/Users/evren/code/agent_flow/scripts/ralf-codex`
- `/Users/evren/code/agent_flow/tests/test_ralf_codex_hooks.py`
- `/Users/evren/code/agent_flow/README.md`
- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/.codex-plugin/plugin.json`
- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/README.md`
- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills/ralf/SKILL.md`

Delete these duplicated plugin skill trees:

- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills/ralf-handoff-plan/`
- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills/ralf-execute/`
- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills/ralf-cp-execute/`
- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills/ralf-cp-review/`
- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills/ralf-review-squash/`

Do not modify:

- `/Users/evren/code/agent_flow/skills/**`
- `/Users/evren/code/agent_flow/opencode-ralph/**`
- `/Users/evren/code/agent_flow/scripts/ralf`
- `/Users/evren/.codex/config.toml`

## Public Interfaces

### Repo-local loop state

Reuse `ralph-loop.local.md` in the project root as the durable loop-state file. Keep the state format frontmatter-based so it remains readable and consistent with the older Ralph variants.

The state must include at least:

- `active`
- `runtime` set to `codex`
- `iteration`
- `max_iterations`
- `completion_promise`
- `started_at`
- `plan_path` as an absolute path

The body must store the current Ralph execution prompt text so future restarts can reconstruct intent from disk.

### New launcher

Add a new repo script:

`scripts/ralf-codex [--prepare-only] [--max-iterations N] [--completion-promise TEXT] path/to/plan.md [extra instructions ...]`

Behavior:

- Validate that the plan exists.
- Write or replace `ralph-loop.local.md` for Codex mode.
- Build the initial planner prompt from the absolute plan path plus any extra instructions.
- `--prepare-only` writes state and prints the prompt for use in the Codex app.
- Default mode writes state and launches `codex "<prompt>"` for CLI users.

Do not change the existing `scripts/ralf` Gemini runner in this pass.

### Thin plugin behavior

Keep `ralph-loop-codex` installable, but make it a thin repo-native wrapper:

- `ralf` remains as a discovery/setup helper only.
- It must explain that the real loop manager is the repo-local Codex hook layer.
- It must not claim that the plugin or copied skills alone can autonomously execute a full plan.
- It may point users to the repo-native RALF skills already present in this repository, but it must not vendor duplicate copies of them anymore.

## Sequential Implementation Steps

1. Add repo-local Codex config.
   Before: this repo has no `.codex/` directory, no repo-local config, and no repo-local hooks.
   After: `.codex/config.toml` exists and enables `codex_hooks = true` for this repo only.
   Do not edit home config. Keep the change repo-scoped.

2. Add hook registration.
   Before: no hook file exists for this repo.
   After: `.codex/hooks.json` registers:
   - `SessionStart` hook to inject active Ralph status on startup or resume.
   - `Stop` hook to decide whether Codex should continue the Ralph loop.
   Use git-root-based command paths in the hook definitions, not relative `.codex/hooks/...` paths.

3. Build shared hook logic.
   Before: no shared parser exists for Ralph state or plan status.
   After: `.codex/hooks/ralf_common.py` provides all reusable logic for:
   - locating the git root
   - loading and saving `ralph-loop.local.md`
   - reading the active plan
   - finding the first unchecked checkpoint
   - checking completion-promise presence in the last assistant message
   - enforcing max-iteration limits
   - producing the next continuation prompt text
   Keep this logic deterministic and file-system driven. Do not depend on hidden chat context.

4. Implement startup/resume context hook.
   Before: a resumed Codex session has no automatic Ralph reminder.
   After: `.codex/hooks/ralf_session_start.py` adds developer context only when an active Codex Ralph state file exists.
   The context must name the active plan path, current iteration, max iterations, completion promise, and the first unchecked checkpoint if one still exists.
   If no active Ralph state exists, the hook must no-op.

5. Implement stop-time loop continuation.
   Before: when Codex stops after one checkpoint or one turn, no repo logic can continue the plan.
   After: `.codex/hooks/ralf_stop_continue.py` drives the loop at `Stop`.
   Required behavior:
   - If no active Codex Ralph state exists, return success with no continuation.
   - If `stop_hook_active` is true, do not continue again for the same stop cycle.
   - If the completion promise is present in `last_assistant_message`, mark the state inactive and allow the stop to finish.
   - If max iterations has been reached, mark the state inactive and stop with a clear `systemMessage`.
   - Re-read the plan from disk.
   - If no unchecked checkpoints remain, mark the state inactive and stop without continuation.
   - Otherwise increment the iteration in state and return a continuation reason that tells Codex to resume from the first unchecked checkpoint using the plan on disk as source of truth.
   The continuation prompt must be checkpoint-aware and must not ask Codex to improvise outside the plan.

6. Add the launcher script.
   Before: there is no Codex-specific launcher in this repo.
   After: `scripts/ralf-codex` initializes the state file and either prints the prompt for the app or launches Codex CLI directly.
   Reuse prompt wording from the existing RALF runners where appropriate, but update it for Codex semantics:
   - completion is driven by the stop hook plus completion promise
   - the plan file is the source of truth
   - checkpoint verification and commit rules still apply

7. Add automated tests for the hook logic.
   Before: the repo has no Codex Ralph hook tests.
   After: `tests/test_ralf_codex_hooks.py` covers the pure Python behavior of the state parser and stop decision logic without launching Codex.
   Test at least:
   - inactive or missing state does not continue
   - promise match disables the loop
   - max iterations disables the loop
   - checked-complete plan disables the loop
   - incomplete plan returns a continuation decision and increments iteration
   - `stop_hook_active = true` does not double-continue

8. Rewrite repo docs.
   Before: the repo README only describes the Gemini runner as the active Ralph runner.
   After: `README.md` documents Codex as a supported Ralph runtime and distinguishes:
   - `scripts/ralf` for Gemini
   - `scripts/ralf-codex` for Codex
   - repo-local `.codex/hooks.json` as the actual Codex loop engine
   Include separate quick-start instructions for Codex CLI and Codex app.

9. Slim and correct the Codex plugin.
   Before: `ralph-loop-codex` is a skill pack that implies whole-plan autonomous execution without a real manager.
   After:
   - `plugin.json` describes the plugin as a repo-native RALF helper for Codex, not as the loop engine itself.
   - `README.md` explicitly states that the actual loop controller lives in repo `.codex/` hooks.
   - `skills/ralf/SKILL.md` becomes a thin setup/discovery skill.
   - duplicated copied skills are removed from the plugin tree.
   Do not keep stale wording that claims fresh-context checkpoint execution is enforced by the plugin bundle alone.

## Constraints

- Do not rely on the undocumented `hooks` field in plugin manifests as the primary mechanism.
- Do not require editing `~/.codex/config.toml`; the implementation must work from repo-local `.codex/` files.
- Do not replace or remove the existing Gemini Ralph runner.
- Do not silently keep duplicate plugin skill copies once the hook manager becomes the real execution path.
- Do not introduce destructive git automation in hooks.
- Do not make the stop hook fire shell commands that mutate the repo.
- Do not continue forever on a plan that is already complete, missing, or inconsistent with state.

## Edge Cases

- Codex may start from a subdirectory, so hook commands must resolve from the git root.
- The repo may be reopened in the app or resumed in CLI after a crash; the state file must be enough to recover.
- `last_assistant_message` may be `null`; treat that as "no promise found", not as completion.
- A human may delete or move the plan file while the loop is active; disable the loop with a visible reason instead of guessing.
- If the plan file and state disagree, prefer the plan file for checkpoint status and stop with a clear message when recovery is unsafe.
- If the user explicitly cancels by removing or deactivating the state file, hooks must no-op immediately.

## Verification

Run these commands after implementation:

1. Config and hook file presence:
   - `test -f /Users/evren/code/agent_flow/.codex/config.toml`
   - `test -f /Users/evren/code/agent_flow/.codex/hooks.json`
   - `test -f /Users/evren/code/agent_flow/.codex/hooks/ralf_stop_continue.py`
   - `test -f /Users/evren/code/agent_flow/scripts/ralf-codex`

2. Python syntax:
   - `python3 -m py_compile /Users/evren/code/agent_flow/.codex/hooks/ralf_common.py /Users/evren/code/agent_flow/.codex/hooks/ralf_session_start.py /Users/evren/code/agent_flow/.codex/hooks/ralf_stop_continue.py`

3. Script lint:
   - `shellcheck /Users/evren/code/agent_flow/scripts/ralf-codex`

4. Automated tests:
   - `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_codex_hooks.py`

5. Launcher dry run:
   - `cd /Users/evren/code/agent_flow && ./scripts/ralf-codex --prepare-only plans/codex-ralf-plugin-port-2026-03-27.md`

6. Docs sanity:
   - `rg -n "autonomous execution|through completion|stop-hook interception|same-session" /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex /Users/evren/code/agent_flow/README.md`
   Review matches and ensure only accurate wording remains.

7. Manual Codex CLI smoke test:
   - `cd /Users/evren/code/agent_flow && ./scripts/ralf-codex plans/codex-ralf-plugin-port-2026-03-27.md`
   Confirm the stop hook continues the session at least once when the plan is still incomplete.

8. Manual Codex app smoke test:
   - `cd /Users/evren/code/agent_flow && ./scripts/ralf-codex --prepare-only plans/codex-ralf-plugin-port-2026-03-27.md`
   - Paste the printed prompt into a Codex app thread in this repo.
   Confirm the stop hook continues the session when the plan remains incomplete.

## Final Checklist

- [ ] Repo-local `.codex/` config exists and enables hooks
- [ ] `Stop` hook continues incomplete Ralph runs
- [ ] `SessionStart` hook restores Ralph context on startup or resume
- [ ] Ralph state is durable on disk and deactivates on completion
- [ ] New `scripts/ralf-codex` launcher works for CLI and app setup
- [ ] Existing Gemini runner remains unchanged
- [ ] Plugin copy no longer claims unsupported autonomy
- [ ] Duplicate plugin skill copies are removed
- [ ] Automated tests pass
- [ ] Manual Codex CLI and app smoke tests pass
