# Universal RALF CLI Controller For Codex, Pi, And Claude

## Summary

Build a new Python-based, plan-only RALF controller that drives `codex`, `pi`, and `claude` from the command line without modifying any of the existing plugin implementations in this repository. The controller must treat the checkpoint plan on disk as the source of truth, run one fresh non-interactive harness turn per loop iteration, stop automatically when the plan is fully complete, and stop with a clear error when progress stalls or the run exceeds the maximum allowed turn count.

The new public entry point is a thin launcher script plus Python modules, not a harness plugin. The wrapper must:

- require a real checkpoint plan file
- require a harness name and model name
- use repo-local hidden run logs with retention
- run each harness with the highest autonomy flags discoverable from `--help`
- keep the harness-specific differences inside adapter modules only

## Git Tracking

- Plan Branch: `main`
- Pre-Handoff Base HEAD: `8db8f5f3e3de0287a94743225f3964a119fae59b`
- Last Reviewed HEAD: `none`
- Review Log:
  - 2026-03-29: Reviewed the uncommitted worktree state against the plan, outcome `changes-requested`. Verified gaps: same-second run retention currently falls back to run-directory name ordering instead of true creation order, and `run.json` records a stale pre-turn snapshot on non-zero harness exit after the plan changes. Unrelated dirty files outside this handoff are also present in the worktree and should be kept out of the final controller commit.

## Done Means

- Running `scripts/ralf-universal --harness <codex|pi|claude> --model <MODEL> path/to/plan.md [extra instructions ...]` starts a whole-plan checkpoint loop without touching any existing plugin runtime in this repo.
- The controller uses the plan file on disk as the only checkpoint-progress authority. It does not require or expose a completion-promise flag.
- Each loop turn is a fresh non-interactive CLI invocation of the selected harness. The controller does not depend on harness session resume features.
- The controller stops successfully only when:
  - no unchecked checkpoint heading remains
  - no unchecked step checkbox remains inside any checkpoint section
- The controller stops with a clear failure summary when any of the following happens:
  - the same effective checkpoint-progress snapshot persists for `5` completed turns
  - total turns reach `15`
  - the harness exits non-zero
  - the plan file is missing or structurally inconsistent
- The controller writes durable per-run logs under a repo-local hidden directory and prunes old run directories so only the newest `20` runs remain by default.
- Root documentation explains the new wrapper, the supported harnesses, the default limits, the log location, and the fact that existing harness plugins in this repo remain unchanged.

## Critical Invariants

- The plan file is the sole source of truth for checkpoint completion. The controller may read it and validate it, but it must never modify it.
- Only checkpoint sections count for step tracking. The parser must ignore checkboxes that appear outside checkpoint sections.
- A checkpoint section begins at a heading matching `### [ ] Checkpoint ...` or `### [x] Checkpoint ...` and ends at the next checkpoint heading or end of file.
- A run is complete only when both checkpoint headings and step checkboxes inside checkpoint sections are fully complete.
- A checked checkpoint heading that still contains unchecked step checkboxes is an invalid plan state. The controller must stop with an explicit inconsistency error instead of guessing.
- Each loop iteration is a fresh harness process. No v1 behavior may rely on harness-native resume, continue, fork, session IDs, or repo-local hook/plugin state.
- Existing repository assets that must remain untouched:
  - `codex-ralph-loop-plugin/**`
  - `opencode-ralph/**`
  - `scripts/ralf`
  - `scripts/ralf_offf.sh`
- The Codex adapter must not depend on an undocumented system-prompt CLI flag. If separate system-prompt injection is unavailable from `codex ... --help`, the controller must emulate it by prepending the controller instruction block to the user prompt.
- Log rotation must delete whole historical run directories only. It must never truncate the active run’s files.

## Forbidden Implementations

- Do not retrofit the old Claude/OpenCode/Codex plugin implementations instead of building the new wrapper.
- Do not keep or reintroduce a public `--completion-promise` flag in the new wrapper.
- Do not parse every markdown checkbox in the file. Restrict step counting to checkpoint sections.
- Do not silently continue after a non-zero harness exit, missing plan file, or invalid checked-heading-with-unchecked-step plan state.
- Do not implement harness-specific session persistence or resume logic in v1.
- Do not store logs in visible repo root files. Use a hidden repo-local run directory and ignore it in `.gitignore`.
- Do not add a short inactivity timeout that can kill a legitimate long-running implementation turn by default.
- Do not require undocumented CLI flags or private APIs that are not visible from the discovered `--help` output.

## Checkpoints

### [ ] Checkpoint 1: Scaffold The Universal Controller Core

**Goal:**

- Create the new launcher, Python package, controller state model, run-directory layout, plan parser, and checkpoint-progress snapshot logic without invoking real harness CLIs yet.

**Context Bootstrapping:**

- Run these commands before editing:
- `cd /Users/evren/code/agent_flow`
- `git branch --show-current`
- `git rev-parse HEAD`
- `eza -la scripts extensions tests`
- `bat --paging=never README.md`
- If this is Checkpoint 1, capture the git tracking values before any edits:
- `git branch --show-current`
- `git rev-parse HEAD`

**Scope & Blast Radius:**

- May create/modify: `scripts/ralf-universal`, `extensions/__init__.py`, `extensions/ralf_universal/**`, `tests/test_ralf_universal.py`, `.gitignore`
- Must not touch: `codex-ralph-loop-plugin/**`, `opencode-ralph/**`, `scripts/ralf`, `scripts/ralf_offf.sh`, `plans/**` except read-only access to the assigned plan file and the minimal progress-tracking edits performed by the consuming execution or review workflow
- Constraints: keep the controller package reusable for future harness additions; keep all checkpoint parsing deterministic and file-driven; do not add any harness-specific branching outside the adapter layer

**Steps:**

- [ ] Step 1: Add the new thin launcher at `scripts/ralf-universal` that dispatches to a Python module under `extensions/ralf_universal`.
- [ ] Step 2: Create the Python package structure under `extensions/ralf_universal/`, including separate modules for CLI parsing, controller orchestration, plan parsing, run-state persistence, and logging/retention helpers.
- [ ] Step 3: Define the public CLI shape as `scripts/ralf-universal --harness <codex|pi|claude> --model <MODEL> [--max-turns N] [--stagnation-limit N] [--keep-runs N] path/to/plan.md [extra instructions ...]`.
- [ ] Step 4: Implement repo-local hidden run storage under `.ralf/runs/<run-id>/` with at least `run.json`, per-turn prompt captures, stdout/stderr logs, and per-turn result metadata.
- [ ] Step 5: Implement plan parsing that identifies checkpoint sections, counts unchecked checkpoints, counts unchecked step checkboxes within each checkpoint section, and flags inconsistent checked-heading-with-unchecked-step states.
- [ ] Step 6: Implement the controller snapshot model using:
- [ ] `current_checkpoint_name`
- [ ] `unchecked_checkpoint_count`
- [ ] `current_checkpoint_unchecked_step_count`
- [ ] Step 7: Implement the controller state transitions so a changed snapshot resets stagnation, while an unchanged snapshot increments stagnation.
- [ ] Step 8: Set defaults to `15` max turns, `5` stagnation attempts, `20` retained run directories, and no default inactivity kill timer in v1.

**Dependencies:**

- Depends on no earlier checkpoint.

**Verification:**

- Run scoped tests: `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k parser`
- Run non-regression tests: `python3 -m py_compile /Users/evren/code/agent_flow/extensions/ralf_universal/*.py /Users/evren/code/agent_flow/extensions/ralf_universal/harnesses/*.py && shellcheck /Users/evren/code/agent_flow/scripts/ralf-universal`

**Done When:**

- Verification commands pass cleanly.
- The controller can parse a valid checkpoint plan, reject an inconsistent one, and create a new hidden run directory layout without invoking a real harness.
- A git commit is created with message starting with:
  ```text
  cp1 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp1 v02`, `cp1 v03`, and so on.

**Stop and Escalate If:**

- The repo already contains an existing hidden run directory convention that conflicts with `.ralf/`.
- The chosen package path under `extensions/` cannot be imported reliably from a thin launcher without adding unsupported Python path hacks.

### [ ] Checkpoint 2: Add Harness Adapters And Turn Execution

**Goal:**

- Implement adapter modules for `codex`, `pi`, and `claude`, build consistent controller prompts, run one fresh turn per loop, and enforce success, stagnation, and max-turn stop rules.

**Context Bootstrapping:**

- Run these commands before editing:
- `cd /Users/evren/code/agent_flow`
- `codex --help`
- `codex exec --help`
- `pi --help`
- `claude --help`
- `rg -n "codex --help|pi --help|claude --help|dangerously|system-prompt|permission-mode" /Users/evren/code/agent_flow/plans /Users/evren/code/agent_flow/README.md`

**Scope & Blast Radius:**

- May create/modify: `extensions/ralf_universal/controller.py`, `extensions/ralf_universal/harnesses/base.py`, `extensions/ralf_universal/harnesses/codex.py`, `extensions/ralf_universal/harnesses/pi.py`, `extensions/ralf_universal/harnesses/claude.py`, `extensions/ralf_universal/cli.py`, `tests/test_ralf_universal.py`
- Must not touch: `codex-ralph-loop-plugin/**`, `opencode-ralph/**`, `scripts/ralf`, `scripts/ralf_offf.sh`, `plans/**` except read-only access to the assigned plan file and the minimal progress-tracking edits performed by the consuming execution or review workflow
- Constraints: keep one shared adapter interface; keep the controller’s prompt semantics consistent across harnesses; use plain text non-interactive mode for all harnesses by default

**Steps:**

- [ ] Step 1: Define a harness adapter interface that returns argv, environment overrides, prompt-mapping behavior, and a harness label for logging.
- [ ] Step 2: Implement the Codex adapter using `codex exec`, `--dangerously-bypass-approvals-and-sandbox`, `-C <repo-root>`, the required `--model`, and user-prompt prefixing for controller instructions because the discovered help does not expose a dedicated system-prompt flag.
- [ ] Step 3: Implement the Pi adapter using `pi --print`, `--system-prompt`, the required `--model`, explicit full tool list `read,bash,edit,write,grep,find,ls`, and fresh-turn execution without session reuse.
- [ ] Step 4: Implement the Claude adapter using `claude -p`, `--system-prompt`, the required `--model`, `--permission-mode bypassPermissions`, `--dangerously-skip-permissions`, and `--tools default`.
- [ ] Step 5: Build one shared controller system instruction block that:
- [ ] requires the harness to re-read the plan from disk every turn
- [ ] limits work to the first incomplete checkpoint
- [ ] requires completed steps to be checked before the checkpoint heading is marked done
- [ ] forbids claiming completion unless the plan on disk is truly complete
- [ ] Step 6: Build one shared user prompt template that references the absolute plan path and appends any extra freeform caller instructions after a blank line.
- [ ] Step 7: Add reinforcement behavior so that when the snapshot does not change after a turn, the next turn’s controller instructions explicitly call out the lack of checkpoint/step progress and restate the plan-marking requirement.
- [ ] Step 8: Stop the run with a clear error summary when the same snapshot persists for `5` completed turns, including the checkpoint name, unchecked checkpoint count, unchecked step count, and run-log directory.
- [ ] Step 9: Stop the run with a clear error summary when total turns reach `15`, and allow both limits to be overridden via `--max-turns` and `--stagnation-limit`.
- [ ] Step 10: Treat any non-zero harness exit as an immediate controller failure after logging stdout/stderr and writing the terminal summary into the run metadata.

**Dependencies:**

- Depends on Checkpoint 1.

**Verification:**

- Run scoped tests: `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k adapters`
- Run non-regression tests: `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k controller`

**Done When:**

- Verification commands pass cleanly.
- The controller can build deterministic commands for all three harnesses from the discovered CLI flags, execute one turn, and decide whether to continue or stop based solely on plan-file state and controller state.
- A git commit is created with message starting with:
  ```text
  cp2 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp2 v02`, `cp2 v03`, and so on.

**Stop and Escalate If:**

- Any discovered harness refuses the documented autonomy flags in non-interactive mode.
- The implementer cannot make Codex honor controller instructions without relying on undocumented prompt/system configuration beyond the visible `--help` surface.

### [ ] Checkpoint 3: Add End-To-End Fake-Harness Coverage

**Goal:**

- Prove the controller behavior end-to-end with fake harness executables so the real harness CLIs do not need to be invoked in automated tests.

**Context Bootstrapping:**

- Run these commands before editing:
- `cd /Users/evren/code/agent_flow`
- `eza -la tests`
- `bat --paging=never tests/test_ralf_codex_install.py`
- `bat --paging=never tests/ralf.sh`

**Scope & Blast Radius:**

- May create/modify: `tests/test_ralf_universal.py`
- Must not touch: `codex-ralph-loop-plugin/**`, `opencode-ralph/**`, `scripts/ralf`, `scripts/ralf_offf.sh`, `plans/**` except read-only access to the assigned plan file and the minimal progress-tracking edits performed by the consuming execution or review workflow
- Constraints: tests must fabricate temporary fake `codex`, `pi`, and `claude` binaries on `PATH`; no test may depend on a real model account or internet access

**Steps:**

- [ ] Step 1: Add unit tests for checkpoint parsing, step scoping, and inconsistent checked-heading-with-unchecked-step detection.
- [ ] Step 2: Add unit tests for stagnation counting, stagnation reset on progress, and max-turn stopping.
- [ ] Step 3: Add adapter argv tests that assert the exact harness flags chosen from the discovered help surfaces.
- [ ] Step 4: Add a fake-harness success test where a temporary plan progresses to full completion and the controller stops without any completion promise.
- [ ] Step 5: Add a fake-harness stagnation test where the plan stays unchanged and the controller stops after `5` repeated snapshots with a clear failure summary.
- [ ] Step 6: Add a fake-harness max-turn test where the plan never completes and the controller stops after `15` turns.
- [ ] Step 7: Add a fake-harness non-zero-exit test where the harness process fails and the controller records the failure without continuing.
- [ ] Step 8: Add a retention test that creates more than `20` historical run directories and confirms the oldest ones are pruned while the newest runs remain.

**Dependencies:**

- Depends on Checkpoint 2.

**Verification:**

- Run scoped tests: `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py`
- Run non-regression tests: `python3 -m py_compile /Users/evren/code/agent_flow/extensions/ralf_universal/*.py /Users/evren/code/agent_flow/extensions/ralf_universal/harnesses/*.py`

**Done When:**

- Verification commands pass cleanly.
- The fake-harness suite proves successful completion, stagnation failure, max-turn failure, adapter command generation, and log retention without invoking any real harness CLI beyond the checked `--help` commands used for discovery.
- A git commit is created with message starting with:
  ```text
  cp3 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp3 v02`, `cp3 v03`, and so on.

**Stop and Escalate If:**

- Reliable fake-harness testing requires restructuring the controller into a different package boundary than the plan defined above.

### [ ] Checkpoint 4: Document The New Wrapper And Guardrails

**Goal:**

- Update the repo documentation for the new universal wrapper and hide the repo-local run directory from version control.

**Context Bootstrapping:**

- Run these commands before editing:
- `cd /Users/evren/code/agent_flow`
- `bat --paging=never README.md`
- `bat --paging=never .gitignore`
- `rg -n "ralf|Codex Ralph|OpenCode|Claude" /Users/evren/code/agent_flow/README.md`

**Scope & Blast Radius:**

- May create/modify: `README.md`, `.gitignore`
- Must not touch: `codex-ralph-loop-plugin/**`, `opencode-ralph/**`, any root or subdirectory `AGENTS.md`, `plans/**` except read-only access to the assigned plan file and the minimal progress-tracking edits performed by the consuming execution or review workflow
- Constraints: update only existing relevant README coverage; do not describe plugin changes that are not being made; do not claim harness-specific resume support or existing-plugin integration

**Steps:**

- [ ] Step 1: Update `.gitignore` to ignore the new hidden run directory, including all per-run logs and metadata.
- [ ] Step 2: Update the root `README.md` to document:
- [ ] the new `scripts/ralf-universal` entry point
- [ ] required `--harness` and `--model` flags
- [ ] plan-only execution model
- [ ] automatic stop rules based on checkpoint headings plus step checkboxes
- [ ] stagnation and max-turn defaults
- [ ] repo-local hidden log directory and retention behavior
- [ ] the fact that existing Claude/OpenCode/Codex plugin trees in this repo are not modified by this wrapper
- [ ] Step 3: Add a brief troubleshooting note for non-zero harness exits, inconsistent plan state, and the location of saved logs.
- [ ] Step 4: Explicitly state that `claude` is the supported harness name, even if earlier dictation referred to `cladue`.
- [ ] Step 5: Leave plugin READMEs unchanged because this handoff does not modify those implementations.

**Dependencies:**

- Depends on Checkpoint 3.

**Verification:**

- Run scoped tests: `rg -n "ralf-universal|stagnation|max-turns|\\.ralf/" /Users/evren/code/agent_flow/README.md /Users/evren/code/agent_flow/.gitignore`
- Run non-regression tests: `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py && shellcheck /Users/evren/code/agent_flow/scripts/ralf-universal`

**Done When:**

- Verification commands pass cleanly.
- The root README accurately documents the new wrapper without claiming any edits to the existing plugin runtimes.
- A git commit is created with message starting with:
  ```text
  cp4 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp4 v02`, `cp4 v03`, and so on.

**Stop and Escalate If:**

- No existing README section can be updated cleanly without adding misleading new coverage for unchanged plugin behavior.

## Behavioral Acceptance Tests

- Given a valid checkpoint plan with one incomplete checkpoint, running the wrapper against any supported harness starts a fresh non-interactive turn, writes a new run directory under `.ralf/runs/`, and logs the exact system/user prompt text used for that turn.
- Given a plan where the harness finishes the remaining checkpoint work and marks both the step checkboxes and the checkpoint heading complete, the controller exits successfully without relying on or exposing any completion promise.
- Given a plan where the same first incomplete checkpoint and its unchecked step count remain unchanged after a turn, the next turn includes stronger controller guidance reminding the harness to mark completed steps and the checkpoint heading correctly.
- Given a plan where the same snapshot remains unchanged for `5` completed turns, the controller stops with a clear stagnation error that names the current checkpoint, the remaining unchecked checkpoint count, the remaining unchecked step count, and the saved run-log directory.
- Given a plan that never reaches completion, the controller stops with a clear max-turn error after `15` turns by default.
- Given a plan where a checkpoint heading is marked `[x]` but an unchecked step remains in that checkpoint section, the controller stops before continuing and reports the plan as inconsistent.
- Given more than `20` historical run directories under `.ralf/runs/`, starting a new run prunes the oldest historical run directories and leaves the newest `20` intact.
- Given the `codex` harness, the wrapper builds the command from discovered `codex exec --help` flags and injects controller instructions through prompt prefixing rather than an undocumented system-prompt flag.

## Plan-to-Verification Matrix

| Requirement | Verification |
| --- | --- |
| Plan-only controller exists as new launcher plus Python modules | `test -f /Users/evren/code/agent_flow/scripts/ralf-universal && test -f /Users/evren/code/agent_flow/extensions/ralf_universal/cli.py` |
| Existing plugin implementations remain untouched | `git diff --name-only -- codex-ralph-loop-plugin opencode-ralph` |
| Checkpoint headings and in-checkpoint steps both drive completion | `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k parser` |
| Stagnation stops after `5` unchanged snapshots | `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k stagnation` |
| Max-turn stop defaults to `15` | `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k max_turns` |
| Codex adapter uses only discovered CLI flags | `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k codex` |
| Pi adapter uses explicit system prompt and full tool list | `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k pi` |
| Claude adapter uses explicit system prompt and bypass-permissions flags | `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k claude` |
| Repo-local hidden logs are retained and pruned to `20` runs | `python3 -m unittest /Users/evren/code/agent_flow/tests/test_ralf_universal.py -k retention` |
| Root docs reflect the new wrapper and hidden run directory | `rg -n "ralf-universal|\\.ralf/|stagnation|max-turns" /Users/evren/code/agent_flow/README.md /Users/evren/code/agent_flow/.gitignore` |

## Assumptions And Defaults

- `cladue` in the earlier request is treated as dictation for the installed `claude` CLI because `cladue` was not found on `PATH` and `claude` was.
- The wrapper supports exactly these harness names in v1: `codex`, `pi`, `claude`.
- `--model` is required for reproducibility across all harnesses, even if the underlying CLI can fall back to its own default.
- The wrapper does not expose an arbitrary custom system-prompt override in v1. The controller owns the system instruction block; callers may only append extra user-level instructions after the required plan path.
- The wrapper uses plain text non-interactive mode for all harnesses by default to keep behavior uniform and avoid depending on harness-specific JSON event schemas.
- The controller does not enforce a default inactivity timeout in v1. Long-running turns must be allowed to finish unless the harness exits or the operator interrupts the run.
- The repo-local hidden run directory is `.ralf/`, and `.gitignore` must be updated accordingly.
- No `ARCHITECTURE.md`, `DEVLOG.md`, or subdirectory `AGENTS.md` updates are required for this handoff because the repo currently lacks relevant existing docs for this change and the root `AGENTS.md` must not be modified.
