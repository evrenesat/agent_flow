# Aflow Feature Branch + Worktree Lifecycle With Local Merge Handoff

## Summary

Add engine-managed git lifecycle support so workflows can declare deterministic setup/teardown behavior for feature branches and linked worktrees, while delegating the non-deterministic merge/rebase phase to a capable `team_lead` model via a new bundled `aflow-merge` skill.

Public interfaces and config changes:

- Add global `[aflow]` git-lifecycle settings:
  - `team_lead` as a required role name when any workflow uses `merge`
  - `worktree_prefix`
  - `branch_prefix`
  - `worktree_root`
- Reserve `[workflow]` in `workflows.toml` as workflow-lifecycle defaults, not as a concrete workflow.
- Add workflow-level properties on concrete workflows and aliases:
  - `setup`
  - `teardown`
  - `main_branch`
  - `merge_prompt`
- Keep `team_lead` global only. Resolve it through the selected team first, then global `[roles]`. If a workflow can reach `merge` teardown and its effective team cannot resolve `team_lead`, config loading must fail for config-defined teams and runtime must fail for a CLI `--team` override.
- Treat `merge_prompt` as an ordered array of prompt keys, same shape as step `prompts`. Default is empty.
- Add merge-only prompt placeholders:
  - `{MAIN_BRANCH}`
  - `{FEATURE_BRANCH}`
  - `{PRIMARY_REPO_ROOT}`
  - `{EXECUTION_REPO_ROOT}`
  - `{FEATURE_WORKTREE_PATH}`
- Add execution-context/runtime metadata so the engine can distinguish the primary checkout from the worktree checkout used for implementation.
- Clean up the stale `merge_prompt = ""` entry currently in bundled `aflow.toml` under `[aflow]`. `merge_prompt` is a workflow-level property only, not an `[aflow]` key.

## Git Tracking

- Plan Branch: `main`
- Pre-Handoff Base HEAD: `35113310d24f057927f10499c454301b057b9a12`
- Last Reviewed HEAD: (squashed)
- Review Log:
  - 2026-04-04: Reviewed full handoff cp1 v01 through cp4 v01, outcome: changes-requested. Two naming bugs found: unexpanded `{PLAN_NAME}` in `branch_prefix` config value, and unused `worktree_prefix` config key. Fix plan: `feature-branch-worktree-merge-20260403-v1-cp01-v01.md`.
  - 2026-04-04: Reviewed fix commit (cp4 v01 through HEAD). Both bugs fixed, 2 new tests added, 329 tests pass. Outcome: approved. Squashed into single handoff commit and moved plan to `plans/done/`.

## Done Means

- `load_workflow_config()` accepts the new schema and bundled config validates cleanly.
- Workflow defaults come from `[workflow]` and per-workflow overrides work for both concrete workflows and aliases without allowing aliases to redefine steps.
- Branch-only workflows create a local feature branch in the primary checkout, run the normal workflow there, invoke merge handoff locally, and finish back on the configured target branch.
- Worktree workflows create a linked worktree from the local target branch, create the feature branch inside that worktree, run normal workflow turns there, invoke merge handoff locally against the primary checkout, then remove the worktree only after merge succeeds.
- The merge phase is model-driven through a new bundled `aflow-merge` skill, uses only local refs and local worktrees, preserves commit history by default, and can consume per-workflow `merge_prompt` guidance.
- Failure cases preserve evidence and do not silently delete worktrees, branches, or unmerged changes.
- Bundled skill install/docs/tests are updated from six to seven skills and explicitly document the new workflow lifecycle behavior.

## Critical Invariants

- The engine must never fetch, pull, or otherwise depend on remote refs for setup or merge. All lifecycle operations are local-only.
- The primary checkout remains the control root for run artifacts, plan backups, and final merge verification, even when implementation runs in a linked worktree.
- When `setup` includes `worktree`, agent workflow steps must execute against the created worktree checkout, not the primary checkout.
- When `teardown` includes `merge`, merge ownership must resolve through the effective workflow team and the configured global `team_lead` role.
- Default merge behavior must preserve feature-branch commits. No implicit squash behavior may be introduced.
- `rm_worktree` may run only after a successful, verified merge. A failed or ambiguous merge must leave the worktree intact for inspection.
- Workflow lifecycle config must be validated before a run starts. Invalid combinations must fail fast with exact config paths or runtime context in the error.

## Forbidden Implementations

- Do not treat `[workflow]` as both defaults and a runnable workflow.
- Do not let aliases override `steps`.
- Do not run merge or rebase with remote-tracking branches, `git fetch`, or `git pull`.
- Do not hide actual branch/worktree names behind recomputation during teardown. Persist the resolved names/paths created during setup and reuse them.
- Do not store run logs only inside a removable worktree.
- Do not silently fall back from worktree mode to branch-only mode.
- Do not auto-delete the feature branch in v1.
- Do not accept arbitrary `setup` and `teardown` strings. Reject unsupported combinations explicitly.
- Do not place `merge_prompt` under `[aflow]`. It is a workflow-level property only.
- Do not create a feature branch or worktree if the derived branch name, derived worktree path, or git-registered worktree already exists. Fail with a clear collision error instead of overwriting.
- Do not allow `worktree_root` to resolve to a path inside the primary repo root. Nested worktrees create git state confusion.

## Checkpoints

### [x] Checkpoint 1: Parse And Materialize Workflow Lifecycle Config

**Goal:**

- Make the new config shape loadable, validated, and unambiguous.

**Context Bootstrapping:**

- Run these commands before editing:
- `pwd`
- `git branch --show-current`
- `git rev-parse HEAD`
- `rg --files -g 'AGENTS.md'`
- `bat --paging=never aflow/AGENTS.md`
- `sed -n '1,260p' aflow/config.py`
- `sed -n '1392,1585p' tests/test_aflow.py`

**Scope & Blast Radius:**

- May create/modify:
  - `aflow/config.py`
  - `aflow/aflow.toml`
  - `aflow/workflows.toml`
  - `tests/test_aflow.py`
- Must not touch:
  - `plans/**` except this plan file for progress tracking
  - `README.md`
  - `ARCHITECTURE.md`
  - `aflow/workflow.py`
  - bundled skills
- Constraints:
  - Before: `_parse_workflow_tables()` assumes every child of `[workflow]` is a workflow table and aliases may only override `team`.
  - After: `[workflow]` is a reserved defaults table; concrete workflows and aliases inherit lifecycle defaults and may override only `team`, `setup`, `teardown`, `main_branch`, and `merge_prompt` in addition to the existing concrete-only `retry_inconsistent_checkpoint_state`.
  - `team_lead`, `worktree_prefix`, `branch_prefix`, and `worktree_root` stay in `[aflow]`, not `workflows.toml`.
  - `merge_prompt` is an array of prompt keys, not inline free text.
  - Accept only these `(setup, teardown)` pair combinations in v1. Validate as tuples, not independent lists:
    - `([], [])` — no git lifecycle, engine behaves exactly as it does today
    - `(["branch"], ["merge"])` — branch-only
    - `(["worktree", "branch"], ["merge", "rm_worktree"])` — worktree flow
  - Array element ordering is significant. Reject `["branch", "worktree"]` (wrong order) and any other ordering that is not in the list above.
  - Reject `rm_worktree` unless `setup` includes `worktree`.
  - Reject `merge` unless `setup` includes `branch`.
  - Reject non-empty `teardown` with empty `setup` and non-empty `setup` with empty `teardown`.
  - If an effective workflow uses `merge`, require a non-empty `[aflow].team_lead`.
  - If a config-defined workflow team is known at load time and cannot resolve `team_lead` through team override or global roles, fail during config validation with the exact workflow path.

**Steps:**

- [x] Step 1: Extend `AflowSection` and its parser/validation for `team_lead`, `worktree_prefix`, `branch_prefix`, and `worktree_root`, including exact-path errors and sane non-empty-string validation. Remove the stale `merge_prompt` key from the `[aflow]` allowed-keys set and bundled `aflow.toml`.
- [x] Step 2: Introduce a workflow-lifecycle config structure in `aflow/config.py` for defaults and per-workflow resolved values, and teach the parser that bare `[workflow]` is the defaults table.
- [x] Step 3: Materialize aliases by inheriting lifecycle defaults plus base workflow values, while still forbidding alias step redefinition.
- [x] Step 4: Add validation for allowed `(setup, teardown)` pair tuples (not independent lists), `main_branch` as non-empty string, prompt-key existence for every entry in `merge_prompt`, and `team_lead` resolution when merge is enabled. Reject unknown element ordering and mismatched setup/teardown sizes.
- [x] Step 5: Update bundled config fixtures in `aflow/aflow.toml` and `aflow/workflows.toml` to the new schema, including one branch-only example (`ralph_jr`) and one worktree+branch default.
- [x] Step 6: Add parsing/validation tests covering `[workflow]` defaults, alias overrides, invalid lifecycle combinations, invalid `team_lead` resolution for `7teen`, and bundled-config validation.

**Dependencies:**

- None.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_aflow.py -k "WorkflowConfigTests"`
- Run non-regression tests: `uv run pytest -q tests/test_aflow.py -k "bundled_config or docs_and_configs_reflect_split_schema"`

**Done When:**

- Verification commands pass cleanly.
- `load_workflow_config(repo_root / "aflow" / "aflow.toml")` succeeds with the new packaged config.
- `workflow.ralph_jr` is branch-only and any merge-enabled workflow pointing at team `7teen` fails until `senior_architect` is provided by team override or global roles.
- A git commit is created with message starting with:
  ```text
  cp1 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp1 v02`, `cp1 v03`, and so on.

**Stop and Escalate If:**

- Python's TOML loader cannot represent a reserved `[workflow]` defaults table cleanly without a schema hack that would make error reporting ambiguous. Emit `AFLOW_STOP: workflow defaults table cannot be implemented without ambiguous TOML parsing`.

### [x] Checkpoint 2: Add Execution Context For Branch And Worktree Setup

**Goal:**

- Teach the workflow engine to create deterministic local branch/worktree execution contexts before normal workflow turns begin, and to route workflow turns into the right checkout.

**Context Bootstrapping:**

- Run these commands before editing:
- `sed -n '1,260p' aflow/run_state.py`
- `sed -n '1,240p' aflow/git_status.py`
- `sed -n '780,1160p' aflow/workflow.py`
- `sed -n '140,250p' aflow/runlog.py`
- `sed -n '1,220p' aflow/harnesses/base.py`
- `rg -n "build_invocation\\(|repo_root=|cwd=" aflow/harnesses aflow/workflow.py tests/test_aflow.py`

**Scope & Blast Radius:**

- May create/modify:
  - `aflow/run_state.py`
  - `aflow/workflow.py`
  - `aflow/runlog.py`
  - `aflow/harnesses/base.py`
  - `aflow/harnesses/claude.py`
  - `aflow/harnesses/codex.py`
  - `aflow/harnesses/copilot.py`
  - `aflow/harnesses/gemini.py`
  - `aflow/harnesses/kiro.py`
  - `aflow/harnesses/opencode.py`
  - `aflow/harnesses/pi.py`
  - `tests/test_aflow.py`
- Must not touch:
  - `README.md`
  - `ARCHITECTURE.md`
  - `aflow/skill_installer.py`
  - bundled skills
- Constraints:
  - Before: `config.repo_root` is both the control root and the harness execution root.
  - After: the engine distinguishes:
    - primary repo root: original checkout, run artifacts, plan backups, final merge root
    - execution repo root: primary checkout for branch-only workflows, created worktree path for worktree workflows
  - When `setup` is empty (the `([], [])` pair), skip all lifecycle logic entirely: no preflight checks, no branch creation, no execution-root remapping. The engine must behave exactly as it does today for these workflows.
  - Keep `.aflow/runs/` and plan-backup behavior anchored to the primary repo root.
  - Require the startup branch in the primary checkout to equal the effective `main_branch` for any workflow that uses `branch` or `merge`. Also verify that the `main_branch` ref exists locally; fail with a clear error if it does not.
  - Require the primary checkout to be clean (no uncommitted changes) before creating branches or worktrees. The existing dirty-worktree gate in `cli.py` already runs before `run_workflow()`, but the lifecycle preflight must re-verify because a test or future caller could bypass the CLI.
  - Require the plan path to live under the primary repo root when `setup` includes `worktree`; otherwise fail fast before creating anything.
  - When the execution root is a worktree, translate the prompt-visible plan paths `{ORIGINAL_PLAN_PATH}`, `{ACTIVE_PLAN_PATH}`, and `{NEW_PLAN_PATH}` for the agent: replace the primary repo root prefix with the worktree root prefix so the agent sees and edits the plan at the correct filesystem path inside the worktree. The engine must translate those paths back to primary-root-relative paths when reading post-turn state.
  - Expand `worktree_root` with `expanduser()`, require it to resolve to an absolute path, verify it does not resolve to a path inside the primary repo root (nested worktrees create git state confusion), and create worktree directories under it.
  - Derive actual branch/worktree names from the configured prefixes plus a sanitized plan stem and a timestamp suffix, for example `aflow-my-plan-20260404-154501`. Sanitization rules: lowercase the stem, replace non-alphanumeric characters (except `-`) with `-`, collapse consecutive `-`, strip leading/trailing `-`, truncate to 50 characters. If sanitization yields an empty stem, fall back to `plan`. Persist the actual resolved values in controller state and `run.json`; do not recompute them later.
  - Before creating a branch or worktree, verify the derived branch name does not already exist (`git show-ref --verify`), the derived worktree path does not already exist on disk, and the path is not already registered as a git worktree (`git worktree list --porcelain`). Fail with a collision error rather than overwriting.
  - The branch-only path must not create or remove any worktree.

**Steps:**

- [x] Step 1: Add an execution-context dataclass to runtime state for effective lifecycle config, target branch, feature branch, primary repo root, execution repo root, and optional worktree path.
- [x] Step 2: Add preflight helpers in `aflow/workflow.py` that validate lifecycle prerequisites before the first normal workflow turn. Preflight checks: (a) `main_branch` ref exists locally, (b) current branch equals `main_branch`, (c) working tree is clean, (d) derived branch name, derived worktree path, and git-registered worktree entries do not already exist, (e) `worktree_root` does not resolve inside the primary repo root.
- [x] Step 3: Implement branch-only setup by creating the feature branch from local `main_branch` in the primary checkout and making that checkout the execution root.
- [x] Step 4: Implement worktree setup by creating a linked worktree from local `main_branch`, creating the feature branch inside that worktree, and making the worktree the execution root for normal workflow turns.
- [x] Step 5: Thread `execution_repo_root` through `ControllerConfig`, `run.json`, turn artifacts, and every harness adapter invocation so normal steps run against the correct checkout. Specifically, both `_run_process()` (line ~624, `cwd=str(repo_root)`) and the test runner path (line ~1156, `cwd=str(config.repo_root)`) must use `execution_repo_root` instead of `repo_root` as the subprocess cwd. `{ORIGINAL_PLAN_PATH}`, `{ACTIVE_PLAN_PATH}`, and `{NEW_PLAN_PATH}` passed to prompt rendering must be translated to execution-root-relative paths for the agent, then translated back to primary-root-relative for post-turn plan reload.
- [x] Step 6: Add tests covering successful branch-only setup, successful worktree setup, startup-branch mismatch failure, out-of-repo plan failure for worktree workflows, and adapter invocation using the execution root instead of the primary root.

**Dependencies:**

- Depends on Checkpoint 1.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_aflow.py -k "WorkflowRuntimeTests or WorkflowArtifactTests or WorkflowEndToEndTests"`
- Run non-regression tests: `uv run pytest -q tests/test_aflow.py -k "build_invocation or bundled_config"`

**Done When:**

- Verification commands pass cleanly.
- A worktree-enabled workflow runs its normal step prompt in the created worktree checkout while still writing `.aflow/runs/` under the primary checkout.
- A branch-only workflow runs in the primary checkout on a created feature branch.
- `run.json` records the effective lifecycle context needed for later merge/cleanup.
- A git commit is created with message starting with:
  ```text
  cp2 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp2 v02`, `cp2 v03`, and so on.

**Stop and Escalate If:**

- Git worktree creation fails consistently in local test repos for reasons unrelated to the repo fixture itself. Emit `AFLOW_STOP: local git worktree creation is unreliable in the current environment`.

### [x] Checkpoint 3: Add Local Merge Handoff And Bundled `aflow-merge` Skill

**Goal:**

- Add the post-workflow merge phase, route it to the configured `team_lead`, and ship a bundled merge skill that resolves local rebase/merge conflicts without losing data.

**Context Bootstrapping:**

- Run these commands before editing:
- `sed -n '1,260p' aflow/skill_installer.py`
- `sed -n '1,220p' aflow/bundled_skills/aflow-execute-plan/SKILL.md`
- `sed -n '1,220p' aflow/bundled_skills/aflow-review-checkpoint/SKILL.md`
- `sed -n '780,1160p' aflow/workflow.py`
- `rg -n "six bundled skills|aflow-review-final|install-skills" aflow README.md ARCHITECTURE.md tests`

**Scope & Blast Radius:**

- May create/modify:
  - `aflow/workflow.py`
  - `aflow/run_state.py`
  - `aflow/runlog.py`
  - `aflow/aflow.toml`
  - `aflow/skill_installer.py`
  - `aflow/bundled_skills/aflow-merge/SKILL.md`
  - `tests/test_aflow.py`
  - `tests/test_skill_install.py`
- Must not touch:
  - `README.md`
  - `ARCHITECTURE.md`
  - root `AGENTS.md`
- Constraints:
  - Merge is not a normal workflow step and must not consume the normal workflow step graph. It is a post-run teardown phase triggered only after the workflow reaches a successful end state.
  - The merge handoff must run from the primary checkout, not the feature worktree, because the target branch is expected to remain checked out there.
  - The merge agent must be resolved from `[aflow].team_lead` through the effective team, using the same role-resolution logic as normal steps.
  - Always prepend a built-in merge instruction that says to use `aflow-merge`; then append rendered `merge_prompt` templates if configured.
  - Default merge strategy is:
    - local-only
    - preserve commits (no squash)
    - if the feature branch is already based on the current local target branch tip, fast-forward merge directly
    - if the feature branch is not based on the current local target branch tip, rebase feature onto local target branch and resolve conflicts carefully, then fast-forward merge
    - if rebase produces irrecoverable conflicts (agent cannot determine correct resolution for both sides), the agent must abort the rebase (`git rebase --abort`), emit `AFLOW_STOP:`, and leave the feature branch intact — do not fall back to a merge commit silently
  - After the merge agent returns, the engine must verify:
    - no unmerged index entries remain (`git ls-files --unmerged` is empty)
    - the working tree in the primary checkout is clean (`git status --porcelain` is empty)
    - the primary checkout HEAD is on `main_branch` (`git symbolic-ref HEAD` matches)
    - the feature branch is an ancestor of the target branch (`git merge-base --is-ancestor <feature_branch> <main_branch>` exits 0). Note: after a rebase, the feature branch tip SHA may differ from the pre-rebase tip, so do not compare SHAs directly; use ancestry check.
  - If any verification check fails, stop and preserve the feature branch and any worktree. Log which specific check failed.
  - Only after all verification checks pass may `rm_worktree` run.

**Steps:**

- [x] Step 1: Add merge-context prompt rendering for `merge_prompt`, including the new merge-only placeholders and both plan paths.
- [x] Step 2: Add a post-success teardown pipeline in `aflow/workflow.py` that invokes the merge handoff with the resolved `team_lead`, primary checkout root, feature branch/worktree context, and configured merge prompts.
- [x] Step 3: Persist merge-phase status into run metadata so failures are visible in `run.json` and turn artifacts.
- [x] Step 4: Create bundled skill `aflow/bundled_skills/aflow-merge/SKILL.md` with explicit rules:
  - operate only on local refs/worktrees
  - do not fetch or pull
  - understand both implementation plan paths
  - resolve conflicts without dropping either side's intent
  - preserve commits by default
  - leave cleanup of worktree removal to the engine
  - emit `AFLOW_STOP:` for irrecoverable ambiguous states
- [x] Step 5: Update `aflow/skill_installer.py`, install preview/help text, and tests from six bundled skills to seven.
- [x] Step 6: Add end-to-end tests for:
  - branch-only merge handoff
  - worktree merge handoff
  - team-lead resolution through a team override
  - merge verification failure preserving the worktree
  - packaged skill discovery including `aflow-merge`

**Dependencies:**

- Depends on Checkpoint 2.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_aflow.py -k "merge or worktree or team_lead"`
- Run non-regression tests: `uv run pytest -q tests/test_skill_install.py`

**Done When:**

- Verification commands pass cleanly.
- Merge-enabled workflows invoke exactly one post-workflow merge handoff after normal completion and before final success is reported.
- The merge handoff runs locally against the primary checkout and can inspect the feature worktree path when one exists.
- Successful merge preserves feature-branch commit history on the target branch.
- Worktree removal happens only after verified merge success.
- `aflow install-skills` discovers and installs `aflow-merge`.
- A git commit is created with message starting with:
  ```text
  cp3 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp3 v02`, `cp3 v03`, and so on.

**Stop and Escalate If:**

- Local merge verification cannot distinguish a successful merge from an ambiguous detached-HEAD or multi-worktree target-branch state. Emit `AFLOW_STOP: merge success cannot be verified safely with the current git state model`.

### [x] Checkpoint 4: Document And Lock The New Workflow Model

**Goal:**

- Update shipped docs and config examples so the new lifecycle model is documented exactly as implemented.

**Context Bootstrapping:**

- Run these commands before editing:
- `sed -n '120,240p' README.md`
- `sed -n '340,430p' README.md`
- `sed -n '120,230p' ARCHITECTURE.md`
- `rg -n "six bundled skills|workflows.toml|Shipped Workflows|Included Skills|Workflow Configuration" README.md ARCHITECTURE.md tests/test_aflow.py`

**Scope & Blast Radius:**

- May create/modify:
  - `README.md`
  - `ARCHITECTURE.md`
  - `tests/test_aflow.py`
- Must not touch:
  - root `AGENTS.md`
  - unrelated docs
- Constraints:
  - Update only existing relevant sections. Do not add new README sections just to advertise the feature.
  - Document the exact supported lifecycle combinations from Checkpoint 1.
  - Document that merge is local-only and model-driven through `aflow-merge`.
  - Document that worktree execution uses a linked checkout while run artifacts remain under the primary checkout.
  - Update all “six bundled skills” references to seven.
  - Keep docs aligned to implemented behavior only.

**Steps:**

- [x] Step 1: Update README config tables and examples to show `[workflow]` defaults, per-workflow lifecycle overrides, `team_lead`, `worktree_root`, naming prefixes, and `merge_prompt`.
- [x] Step 2: Update README run-behavior and shipped-workflows sections to explain branch-only vs worktree-enabled flows, local merge handoff, and the new `aflow-merge` skill.
- [x] Step 3: Update `ARCHITECTURE.md` to describe the primary-vs-execution checkout split, lifecycle config parsing, post-run merge phase, and seven bundled skills.
- [x] Step 4: Extend docs-parity assertions in `tests/test_aflow.py` so future config/doc drift is caught.

**Dependencies:**

- Depends on Checkpoint 3.

**Verification:**

- Run scoped tests: `uv run pytest -q tests/test_aflow.py -k "docs_and_configs_reflect_split_schema or bundled_skills"`
- Run non-regression tests: `rg -n "aflow-merge|team_lead|worktree_root|merge_prompt|seven bundled skills" README.md ARCHITECTURE.md aflow`

**Done When:**

- Verification commands pass cleanly.
- README and ARCHITECTURE describe the implemented lifecycle model without contradicting the packaged config or bundled skills.
- A git commit is created with message starting with:
  ```text
  cp4 v01
  <rest of the commit message>
  ```
  The first commit for a checkpoint must use `v01`. Later fix passes for the same checkpoint increment the version number, for example `cp4 v02`, `cp4 v03`, and so on.

**Stop and Escalate If:**

- Docs need to describe behavior that could not be stabilized in Checkpoints 1-3. Emit `AFLOW_STOP: docs would have to describe behavior that is not actually implemented`.

## Behavioral Acceptance Tests

- Given the packaged config, `load_workflow_config(aflow/aflow.toml)` succeeds and the bundled `ralph_jr` workflow resolves as branch-only while default workflows resolve as worktree+branch.
- Given a merge-enabled workflow with `team = "7teen"` and no `senior_architect` resolution, config validation fails before any run starts.
- Given a branch-only workflow started from local `main`, the engine creates a feature branch in the primary checkout, runs workflow turns there, then invokes merge handoff and leaves `main` containing the feature tip.
- Given a worktree-enabled workflow started from local `main`, the engine creates a linked worktree under `worktree_root`, runs workflow turns there, then invokes merge handoff from the primary checkout and removes the worktree only after verified merge success.
- Given merge conflicts during rebase or merge, `aflow-merge` resolves them locally using both the feature branch and target branch context without fetching remotes and without silently dropping either side's changes.
- Given merge verification failure, the run fails clearly and preserves the feature branch and any created worktree for inspection.
- Given `aflow install-skills`, the preview and installation include `aflow-merge` as the seventh bundled skill.

## Plan-to-Verification Matrix

| Requirement | Verification |
| --- | --- |
| `[workflow]` is a defaults table, not a runnable workflow | `uv run pytest -q tests/test_aflow.py -k "WorkflowConfigTests"` |
| Alias workflows may override lifecycle config but not steps | `uv run pytest -q tests/test_aflow.py -k "WorkflowConfigTests"` |
| Merge-enabled workflows require resolvable `team_lead` | `uv run pytest -q tests/test_aflow.py -k "team_lead"` |
| Branch-only workflows do not create worktrees | `uv run pytest -q tests/test_aflow.py -k "branch_only"` |
| Worktree workflows run turns in the worktree checkout | `uv run pytest -q tests/test_aflow.py -k "worktree"` |
| Run artifacts stay under the primary checkout | `uv run pytest -q tests/test_aflow.py -k "WorkflowArtifactTests"` |
| Merge handoff is local-only and post-workflow | `uv run pytest -q tests/test_aflow.py -k "merge"` |
| Default merge preserves commit history | `uv run pytest -q tests/test_aflow.py -k "merge"` |
| Worktree removal happens only after successful merge | `uv run pytest -q tests/test_aflow.py -k "rm_worktree or merge"` |
| Bundled skills count increases from six to seven | `uv run pytest -q tests/test_skill_install.py` |
| Docs and packaged config stay in sync | `uv run pytest -q tests/test_aflow.py -k "docs_and_configs_reflect_split_schema"` |
| Final repo-wide safety | `uv run pytest -q` |

## Assumptions And Defaults

- `team_lead` remains a global `[aflow]` role key (a role name, not a selector). Resolution path: read `[aflow].team_lead` value (e.g., `"senior_architect"`), then resolve that role name through the effective team's role map (`[teams.<name>]`), falling back to global `[roles]` if the team does not override it. The result is a harness.profile selector.
- `merge_prompt` is optional and defaults to an empty ordered list. The engine always supplies a built-in merge instruction that tells the agent to use `aflow-merge`.
- Supported lifecycle combinations are intentionally narrow in v1 and validated as `(setup, teardown)` tuples:
  - no lifecycle: `([], [])` — engine behaves exactly as it does today, all lifecycle logic is skipped
  - branch-only: `(["branch"], ["merge"])`
  - worktree flow: `(["worktree", "branch"], ["merge", "rm_worktree"])`
  - Any other combination, including mismatched sizes or wrong element ordering, is rejected at config validation time
- Worktree workflows require the active plan file to live under the primary repo root so plan edits happen inside the feature branch checkout and merge back with the code. The engine translates `{ORIGINAL_PLAN_PATH}`, `{ACTIVE_PLAN_PATH}`, and `{NEW_PLAN_PATH}` from primary-root-relative to worktree-root-relative when rendering prompts for the agent, and translates them back when reading post-turn plan state. Both paths refer to the same git-tracked file but at different filesystem locations.
- Branch/worktree names are generated from sanitized plan names plus a timestamp suffix to avoid collisions across repeated runs of the same plan. If sanitization yields an empty name, the fallback stem is `plan`.
- Successful merge does not delete the feature branch in v1.
- `README.md` and `ARCHITECTURE.md` need updates because they already document workflow config, shipped workflows, and bundled skill counts. Root `AGENTS.md` does not need changes because the feature does not alter top-level agent instructions.
