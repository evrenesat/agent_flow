# Aflow Config Roles/Teams Workflow Split And Team Override

## Summary

Move the app fully onto the new split config shape introduced in `722a541e5123ca388b4179ac0649be75c69add27`.

- `aflow.toml` becomes the home for global aflow settings, harness profiles, global role mappings, team overrides, and prompts.
- `workflows.toml` becomes the home for workflow definitions and workflow aliases.
- Workflow steps must use `role`, not `profile`.
- Workflows may define `extends` only for aliasing plus optional `team` override in v1.
- Runtime must resolve `role -> selector` through the chosen team first, then global roles.
- CLI must accept `--team`, and CLI flags must override config values where both exist. Specifically: `--team` overrides workflow team, and `--max-turns` overrides `[aflow].max_turns`.
- Codex skill installation must use the shared agent skills directory just like Gemini and Pi.
- Bundled config files and docs must explain every config option at its first occurrence and clean up the current workflow descriptions and grammar.

## Git Tracking

- Plan Branch: `main`
- Pre-Handoff Base HEAD: `722a541e5123ca388b4179ac0649be75c69add27`
- Last Reviewed HEAD: `squashed-final-handoff`
- Review Log:
  - `2026-04-03` - Reviewed `cp1 v01` through `cp4 v01` (`4` commits since `Pre-Handoff Base HEAD`). Outcome: `changes-requested`. Follow-up plan: `plans/in-progress/config-roles-teams-workflow-split-20260403-v1-cp01-v01.md`. Findings: legacy single-file/profile workflow config is still accepted, `workflows.toml` still accepts non-`workflow` top-level keys, split config still accepts direct `harness.profile` selectors in step `role`, and the implementation commits touched this reviewer-owned plan file.
  - `2026-04-03` - Reviewed the `config-roles-teams-workflow-split-20260403-v1-cp01-v01.md` follow-up fix against the full accumulated handoff state. Outcome: `approved`. The handoff is squashed into one final accumulated commit after `Pre-Handoff Base HEAD`, and the follow-up fix plan is superseded.

## Done Means

- `load_workflow_config(repo_root / "aflow" / "aflow.toml")` loads `aflow.toml` plus sibling `workflows.toml`, materializes concrete workflows, and validates roles, teams, and aliases without relying on legacy inline workflow tables.
- The runtime never reads step `profile` from shipped config. Steps carry a `role`, and the selected team determines the effective harness/profile selector for each turn.
- `aflow run ... --team TEAM_NAME` overrides any workflow-level team for that run only.
- `[aflow].max_turns` is a supported config key, and `--max-turns` / `-mt` overrides it when provided.
- Bundled workflows in `aflow/workflows.toml` use corrected role names (`architect`, `senior_architect`) and load cleanly.
- Extending workflows work as aliases plus optional `team` override only. They do not redefine steps in v1.
- `aflow install-skills` treats Codex like Gemini and Pi for destination selection and shared-copy deduplication.
- The bundled config files themselves document every config key at first use, and README / ARCHITECTURE describe the implemented split-file, role/team-based model accurately.
- Targeted tests and the relevant full test files pass without reintroducing legacy profile-based bundled config assumptions.

## Critical Invariants

- The canonical shipped config format is the new split-file schema. Do not keep the old bundled single-file workflow schema live in docs or packaged config.
- Role resolution order is fixed: CLI `--team` override, then workflow `team`, then global `[roles]`. A team may override only a subset of roles; missing roles must fall back to `[roles]`.
- Every workflow step must resolve to exactly one fully qualified `harness.profile` selector before invocation.
- Alias workflows in v1 inherit the full base workflow definition and may only override `team`. They must not redefine `steps`, `retry_inconsistent_checkpoint_state`, or any step-level fields.
- `DONE`, `NEW_PLAN_EXISTS`, and transition evaluation semantics must stay unchanged by the config refactor.
- The runtime override flags are ephemeral. `--team` and `--max-turns` must not mutate config files or persisted workflow definitions.
- Codex skill installation must land in the shared agent skills root, and duplicate shared destinations must still copy only once.
- Root `AGENTS.md` must not be modified.

## Forbidden Implementations

- Do not implement the new schema by silently converting `role` back into stored step `profile` strings and leaving team logic unused.
- Do not keep the packaged `aflow/workflows.toml` split while `bootstrap_config()` or `load_workflow_config()` still read only `aflow.toml`.
- Do not accept misspelled public role keys such as `architech` or `senior_architech` as compatibility aliases.
- Do not let alias workflows redefine steps or create recursive/chain alias behavior in v1. Reject those cases clearly.
- Do not hardcode team-specific selector choices in workflow execution code outside the normal role/team resolution path.
- Do not keep Codex on `~/.codex/skills` anywhere in installer logic, CLI help, README, or architecture docs.
- Do not document future-state config behavior before the code and tests support it in the same handoff.
- Do not silently auto-migrate old single-file user configs. If loading fails because the user still has the old format, let the parser error be the migration signal.
- Do not defer bundled config typo fixes to CP4. The parser must be able to load the bundled config at the end of CP1, so typos must be fixed before or alongside parser changes.

## Checkpoints

### [x] Checkpoint 1: Load And Validate The Split Role/Team Workflow Schema

**Goal:**

- Make config loading, bootstrapping, and validation understand the new two-file schema and materialize concrete workflows that runtime code can execute.

**Context Bootstrapping:**

- Run these commands before editing:
- `git branch --show-current`
- `git rev-parse HEAD`
- `rg -n "load_workflow_config|bootstrap_config|WorkflowStepConfig|WorkflowConfig|profile|role|extends|team|max_turns" aflow/config.py aflow/cli.py tests/test_aflow.py`
- `bat --paging=never aflow/config.py`
- `bat --paging=never aflow/aflow.toml`
- `bat --paging=never aflow/workflows.toml`

**Scope & Blast Radius:**

- May create/modify: `aflow/config.py`, `aflow/aflow.toml`, `aflow/workflows.toml`, `tests/test_aflow.py`
- Must not touch: runtime harness adapters, runlog files, `plans/**` except this plan file
- Constraints:
- Preserve current prompt rendering and transition semantics.
- Keep `load_workflow_config(repo_root / "aflow" / "aflow.toml")` as a supported call shape, but make it load the sibling workflow file automatically.
- Treat the new schema as canonical. Rewrite bundled tests instead of preserving old bundled-config expectations.

**Steps:**

- [x] Step 1: Fix bundled config typos that would block parsing. In `aflow/aflow.toml`: rename `architech` to `architect` and `senior_architech` to `senior_architect` in both `[roles]` and all `[teams.*]` tables. In `aflow/workflows.toml`: rename `[worklflow.hard]` and `[worklflow.jr]` to `[workflow.hard]` and `[workflow.jr]`. These fixes must land before any parser changes because the new parser will reject misspelled top-level keys and unknown role names.
- [x] Step 2: Extend config dataclasses and parsing helpers for `[aflow].max_turns`, `[roles]`, `[teams.<name>]`, step `role`, workflow `team`, and workflow `extends`. Add `max_turns` to `AflowSection` (with `DEFAULT_MAX_TURNS = 15` as a module-level constant) and to `_parse_aflow_section`'s allowed keys set. Add `roles` and `teams` fields to `WorkflowUserConfig`. Change `WorkflowStepConfig` to carry a `role: str` field instead of `profile: str`. The `role` field stores a key into `[roles]`, not a fully qualified selector. Add `team: str | None` and `extends: str | None` fields to `WorkflowConfig`.
- [x] Step 3: Make `load_workflow_config()` load `aflow.toml` plus sibling `workflows.toml`, merge them into one `WorkflowUserConfig`, and reject unsupported top-level keys in each file. Allowed top-level keys in `aflow.toml`: `aflow`, `harness`, `roles`, `teams`, `prompts`. Allowed top-level keys in `workflows.toml`: `workflow`. If both files define `workflow`, that is an error.
- [x] Step 4: Materialize workflows during parsing so `WorkflowUserConfig.workflows` contains executable concrete workflows only.
- [x] Step 5: Enforce v1 alias rules: base workflows define `steps`; alias workflows define `extends` plus optional `team`; alias targets must exist; alias chains and cycles are errors; alias workflows may not define `steps` or workflow retry overrides.
- [x] Step 6: Enforce role validation: workflow step roles must exist in global `[roles]`; team override keys must reference known global roles; every role mapping and team override value must be a fully qualified `harness.profile` selector; selector targets must resolve to a defined `[harness.<name>.profiles.<profile>]` entry (reuse existing `validate_workflow_config()` for the cross-validation pass, extending it for role/team checks).
- [x] Step 7: Update bootstrap behavior so first-run config setup copies both packaged config files (`aflow.toml` and `workflows.toml`) into `~/.config/aflow/`.
- [x] Step 8: Replace config tests that currently assert `profile`-based parsing on bundled/default config with split-file, role/team-based expectations.

**Dependencies:**

- Depends on none.

**Verification:**

- Run scoped tests: `uv run pytest tests/test_aflow.py -q -k "WorkflowConfigTests or RetryInconsistentCheckpointConfigTests or SkillDocsTests"`
- Run non-regression tests: `uv run pytest tests/test_aflow.py -q`
- Run schema smoke checks:
  ```bash
  python - <<'PY'
  from pathlib import Path
  from aflow.config import load_workflow_config
  cfg = load_workflow_config(Path('aflow/aflow.toml'))
  print(sorted(cfg.workflows))
  print(cfg.aflow.max_turns)
  print(cfg.workflows['ralph'].steps['implement_plan'].role)
  PY
  ```

**Done When:**

- Verification commands pass cleanly.
- Loading `aflow/aflow.toml` also loads workflows from `aflow/workflows.toml`.
- Bundled workflows resolve with `role`, not `profile`.
- A git commit is created with message starting with:
  ```text
  cp1 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp1 v02`, `cp1 v03`, and so on.

**Stop and Escalate If:**

- A required public call path outside `load_workflow_config(Path(".../aflow.toml"))` also depends on the old single-file loader and cannot be adapted without a broader public API change.
- There is evidence that alias chaining is already relied on elsewhere in the repo.

### [x] Checkpoint 2: Resolve Roles At Runtime And Add CLI Team Override

**Goal:**

- Make CLI and workflow execution use the role/team model at runtime, including `--team` and config-based `max_turns` precedence.

**Context Bootstrapping:**

- Run these commands before editing:
- `rg -n "max_turns|DEFAULT_MAX_TURNS|start-step|resolve_profile|step.profile|selector=|BannerRenderer|ControllerConfig" aflow/cli.py aflow/workflow.py aflow/run_state.py aflow/status.py tests/test_aflow.py`
- `bat --paging=never aflow/cli.py`
- `bat --paging=never aflow/workflow.py`
- `bat --paging=never aflow/run_state.py`

**Scope & Blast Radius:**

- May create/modify: `aflow/cli.py`, `aflow/workflow.py`, `aflow/run_state.py`, `aflow/status.py`, `tests/test_aflow.py`
- Must not touch: harness adapter argv semantics, `aflow/skill_installer.py`, docs files
- Constraints:
- Keep existing plan parsing, retry, same-step cap, and transition behavior intact.
- `--team` must be optional and must fail early with a clear error for unknown team names.
- CLI precedence must be explicit: CLI flag value wins over config; config wins over hardcoded default.

**Steps:**

- [x] Step 1: Add a runtime role resolver function (e.g. `resolve_role_selector(role, team_name, config) -> str`) that maps `(step.role, selected team)` to a concrete `harness.profile` selector string. Resolution order: if a team is selected and that team overrides the role, use the team value; otherwise fall back to global `[roles]`. Update all call sites in `workflow.py` that currently read `step.profile` to call the resolver instead. After CP1, `WorkflowStepConfig` no longer has a `profile` field, only `role`, so any remaining references to `step.profile` are compile errors that must be found and fixed.
- [x] Step 2: Add `--team TEAM_NAME` to `aflow run`, add a `team: str | None` field to `ControllerConfig`, and pass the selected team through to the resolver on every turn. The effective team is: CLI `--team` if provided, else `workflow.team` if set, else `None` (use global roles only). Fail early with a clear error if the team name doesn't exist in `config.teams`.
- [x] Step 3: `AflowSection.max_turns` was added in CP1. Change CLI parsing so `--max-turns` / `-mt` defaults to `None`, and compute effective max turns as: CLI flag if provided, else `config.aflow.max_turns` (which defaults to `DEFAULT_MAX_TURNS = 15`).
- [x] Step 4: Preserve partial team override behavior by falling back from team-specific role mapping to global `[roles]` when a team omits a role.
- [x] Step 5: Update run metadata / turn artifacts / banner text anywhere that currently records `step.profile` so they persist or display the role plus the resolved selector, not a stale profile-only field. Also update `RetryContext` in `run_state.py`, which currently stores a profile string; it must store the role and resolved selector separately so retries resolve correctly under the active team.
- [x] Step 6: Add tests for team selection precedence, workflow-level team defaults, unknown team errors, partial team fallback, config `max_turns`, and CLI `--max-turns` override over config.

**Dependencies:**

- Depends on Checkpoint 1.

**Verification:**

- Run scoped tests: `uv run pytest tests/test_aflow.py -q -k "max_turns or team or start_step or review_implement or same_step"`
- Run non-regression tests: `uv run pytest tests/test_aflow.py -q`
- Run CLI smoke checks: `uv run python -m aflow run --help`

**Done When:**

- Verification commands pass cleanly.
- `aflow run ... --team 7teen` uses team-specific selectors where provided and falls back to global roles where the team omits a role.
- `[aflow].max_turns` is honored when the flag is omitted, and `--max-turns` overrides it when present.
- Run artifacts and the live banner no longer expose a nonexistent step `profile` field as the primary workflow-step selector.
- A git commit is created with message starting with:
  ```text
  cp2 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp2 v02`, `cp2 v03`, and so on.

**Stop and Escalate If:**

- Updating runlog/status output would require incompatible changes to a documented artifact format that should stay stable.
- The CLI parser changes create ambiguity with existing positional parsing that cannot be resolved without a breaking CLI redesign.

### [x] Checkpoint 3: Unify Codex Skill Install Destination With Shared Agent Skills

**Goal:**

- Remove Codex-specific skill install handling and treat Codex like Gemini and Pi for auto-install destination and duplicate-copy behavior.

**Context Bootstrapping:**

- Run these commands before editing:
- `rg -n "~/.codex/skills|~/.agents/skills|install-skills|SUPPORTED_HARNESS_INSTALL_SPECS|codex" aflow/skill_installer.py aflow/cli.py README.md ARCHITECTURE.md tests/test_skill_install.py`
- `bat --paging=never aflow/skill_installer.py`
- `bat --paging=never tests/test_skill_install.py`

**Scope & Blast Radius:**

- May create/modify: `aflow/skill_installer.py`, `aflow/cli.py`, `tests/test_skill_install.py`, `tests/test_aflow.py`
- Must not touch: harness adapter command execution, config parsing, workflow docs except install-skills wording that must stay accurate for tests/help
- Constraints:
- Codex must still be auto-detected by its executable name.
- Shared-destination deduplication must still copy once even when `codex`, `gemini`, and `pi` are all detected.

**Steps:**

- [x] Step 1: Change Codex auto-install destination from `~/.codex/skills` to `~/.agents/skills`.
- [x] Step 2: Update CLI help text and any installer preview expectations that still mention the old Codex destination.
- [x] Step 3: Expand installer tests to cover shared grouping and one-copy behavior when Codex is present with Gemini and/or Pi.
- [x] Step 4: Update any aflow CLI tests that assert the printed auto-target map.

**Dependencies:**

- Depends on none.

**Verification:**

- Run scoped tests: `uv run pytest tests/test_skill_install.py -q`
- Run non-regression tests: `uv run pytest tests/test_skill_install.py tests/test_aflow.py -q -k "install_skills or skill_install"`
- Run grep checks: `rg -n "~/.codex/skills" aflow README.md ARCHITECTURE.md tests`

**Done When:**

- Verification commands pass cleanly.
- Auto-install preview groups Codex with Gemini/Pi under the shared agent skills destination when those executables are present.
- No shipped help text or tests still claim Codex installs into `~/.codex/skills`.
- A git commit is created with message starting with:
  ```text
  cp3 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp3 v02`, `cp3 v03`, and so on.

**Stop and Escalate If:**

- Codex has runtime behavior in this repo that still truly requires a separate `.codex/skills` root despite the user requirement.

### [x] Checkpoint 4: Clean Up Bundled Config Files And Docs To Match The Implemented Model

**Goal:**

- Make the packaged config and docs match the implemented split-file, role/team-based system, with first-occurrence explanations for every config option and cleaner workflow wording.

**Context Bootstrapping:**

- Run these commands before editing:
- `bat --paging=never aflow/aflow.toml`
- `bat --paging=never aflow/workflows.toml`
- `rg -n "profile|profiles|workflow\\.\\w+\\.steps|~/.codex/skills|max_turns|roles|teams|extends|--team" README.md ARCHITECTURE.md aflow/aflow.toml aflow/workflows.toml`
- `bat --paging=never README.md`
- `bat --paging=never ARCHITECTURE.md`

**Scope & Blast Radius:**

- May create/modify: `aflow/aflow.toml`, `aflow/workflows.toml`, `README.md`, `ARCHITECTURE.md`, `tests/test_aflow.py`
- Must not touch: root `AGENTS.md`, bundled skill files unless a test reveals a direct mismatch with the new config model
- Constraints:
- Explain each config option at its first appearance in the bundled config files.
- Keep README edits within existing relevant sections; update rather than expand indiscriminately.
- Documentation must describe the implemented new schema only, not a transitional hybrid model.

**Steps:**

- [x] Step 1: Rewrite bundled `aflow/aflow.toml` comments so the first occurrence of each key is explained: `default_workflow`, `keep_runs`, `max_turns`, `retry_inconsistent_checkpoint_state`, `banner_files_limit`, `max_same_step_turns`, harness profile `model` / `effort`, prompt tables, role mappings, and team overrides.
- [x] Step 2: Rewrite bundled `aflow/workflows.toml` comments so the first occurrence of `extends`, `team`, step `role`, `prompts`, `go`, `to`, and `when` is explained in-place.
- [x] Step 3: Clean up bundled workflow descriptions for grammar and clarity without changing intended control flow. (Role name typos `architech`/`senior_architech` and `worklflow` table key typos were already fixed in CP1 Step 1.)
- [x] Step 4: Update README sections that currently describe a single-file, profile-based schema so they accurately describe the two-file layout, roles/teams, alias workflows, `[aflow].max_turns`, and `--team` precedence.
- [x] Step 5: Update `ARCHITECTURE.md` where it still says config is profile-based or single-file, and explain the role/team resolution stage and split-file bootstrapping.
- [x] Step 6: Update bundled-config documentation tests to assert the new files, comments, and workflow names/wording where appropriate.

**Dependencies:**

- Depends on Checkpoint 1, Checkpoint 2, and Checkpoint 3.

**Verification:**

- Run scoped tests: `uv run pytest tests/test_aflow.py -q -k "SkillDocsTests or bundled_config or help"`
- Run non-regression tests: `uv run pytest tests/test_aflow.py tests/test_skill_install.py -q`
- Run grep checks:
- `rg -n "profile = " aflow/workflows.toml README.md ARCHITECTURE.md`
- `rg -n "architech|senior_architech|worklflow" aflow/workflows.toml aflow/aflow.toml README.md ARCHITECTURE.md`
- `rg -n "~/.codex/skills" README.md ARCHITECTURE.md aflow/cli.py`

**Done When:**

- Verification commands pass cleanly.
- The packaged config files can be read top-to-bottom without any config key appearing first without an explanation nearby.
- README and ARCHITECTURE consistently describe the split-file, role/team, alias-workflow, and shared-skills behavior that the code now implements.
- A git commit is created with message starting with:
  ```text
  cp4 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp4 v02`, `cp4 v03`, and so on.

**Stop and Escalate If:**

- Existing docs outside README / ARCHITECTURE turn out to be authoritative for config format and would become contradictory if left unchanged.

## Behavioral Acceptance Tests

- Given the packaged config files, `load_workflow_config(Path("aflow/aflow.toml"))` returns concrete workflows including `ralph`, `ralph_jr`, `review_implement_review`, `review_implement_cp_review`, `hard`, and `jr`, with step roles resolved later at runtime and no step `profile` field required in TOML.
- Given workflow `ralph_jr`, the workflow inherits `ralph` step structure unchanged and uses team `7teen` for runtime role resolution.
- Given a workflow step with role `senior_architect` and team `7teen`, runtime falls back to the global `[roles].senior_architect` mapping because team `7teen` does not override that role.
- Given `[aflow].max_turns = 9` and no `--max-turns` flag, the run uses nine turns as the hard cap. Given `--max-turns 3`, the same workflow uses three turns for that run only.
- Given `aflow run review_implement_cp_review path/to/plan.md --team codex1`, the selected team for that run is `codex1` even if the workflow table specifies a different team or no team.
- Given an alias workflow that tries to define `steps` in addition to `extends`, config loading fails with a clear validation error instead of guessing merge semantics.
- Given `codex`, `gemini`, and `pi` on `PATH`, `aflow install-skills` previews one shared destination under `~/.agents/skills` and copies each bundled skill there once.
- Given the shipped config files, a reader can understand every config key from comments at its first occurrence without needing README first.

## Plan-to-Verification Matrix

| Requirement | Verification |
| --- | --- |
| Split-file config loading works from `aflow.toml` | `uv run pytest tests/test_aflow.py -q -k "WorkflowConfigTests or bundled_config"` |
| New schema uses `role`, `roles`, `teams`, and `extends` | `rg -n "role =|\\[roles\\]|\\[teams\\.|extends =" aflow/aflow.toml aflow/workflows.toml` |
| Legacy bundled `profile` workflow shape is gone | `rg -n "profile = " aflow/workflows.toml README.md ARCHITECTURE.md` must return no workflow-step hits |
| Workflow aliases only support alias plus team override | `uv run pytest tests/test_aflow.py -q -k "extends or alias or team"` |
| Team resolution precedence is CLI override -> workflow team -> global roles | `uv run pytest tests/test_aflow.py -q -k "team"` |
| Partial team override fallback works | `uv run pytest tests/test_aflow.py -q -k "team"` |
| `[aflow].max_turns` is supported and CLI overrides it | `uv run pytest tests/test_aflow.py -q -k "max_turns"` |
| Run output no longer depends on stale step profile storage | `uv run pytest tests/test_aflow.py -q -k "Banner or run_json or selector"` |
| Codex shares the agent skills destination | `uv run pytest tests/test_skill_install.py -q` and `rg -n "~/.codex/skills" aflow README.md ARCHITECTURE.md tests` |
| Bundled config comments explain first occurrences | Manual read of `aflow/aflow.toml` and `aflow/workflows.toml` plus `uv run pytest tests/test_aflow.py -q -k "SkillDocsTests"` |
| README and ARCHITECTURE match implemented behavior | `rg -n "profile|single-file|~/.codex/skills|--team|roles|teams|workflows.toml" README.md ARCHITECTURE.md` |

## Assumptions And Defaults

- This handoff intentionally drops old bundled single-file, step-`profile` config as the public documented format. Migration is by updating shipped config/docs/tests, not by carrying two public schemas indefinitely.
- Correct public role names are `architect`, `senior_architect`, `worker`, and `reviewer`. Misspelled aliases are not supported.
- Alias workflows are single-level in v1: they must extend a concrete base workflow that defines `steps`, and they may override only `team`.
- Team tables may omit some roles. Missing team role mappings fall back to the global `[roles]` table.
- `max_turns` belongs in `[aflow]` config, defaults to `15`, and is overridden only by the CLI flag. Define `DEFAULT_MAX_TURNS = 15` as a module-level constant in `config.py`, consistent with `DEFAULT_KEEP_RUNS` and `DEFAULT_MAX_SAME_STEP_TURNS`.
- The relevant docs to update are `README.md` and `ARCHITECTURE.md`. No `DEVLOG.md` or subdirectory `AGENTS.md` updates are required for this handoff.
- Existing user config files at `~/.config/aflow/aflow.toml` that still use the old single-file profile-based schema will fail to load with clear validation errors after this handoff. This is intentional: users must update their config. `bootstrap_config()` only writes the new split files on first run when no config exists, so existing configs are never silently overwritten.
