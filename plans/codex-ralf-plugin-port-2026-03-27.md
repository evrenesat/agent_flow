# Codex-Native RALF Plugin Port

## Objective

Port the existing Claude-oriented Ralph Loop work under `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-claude` into a standalone local Codex plugin that optimizes for hands-off RALF execution across checkpoints with fresh context boundaries. The end state is not a literal slash-command clone. The end state is a Codex plugin at `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex` that packages the repo’s existing RALF planning, execution, and review workflows in a self-contained plugin distribution.

Done means:

- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/.codex-plugin/plugin.json` exists and contains real plugin metadata, not scaffold placeholders.
- The plugin exposes Codex-facing skills for RALF planning, autonomous execution, checkpoint execution, checkpoint review, and final review.
- The plugin includes one front-door workflow skill that routes users into the packaged RALF flow without requiring them to know the internal skill names up front.
- The plugin README explains the Codex-native model, explicitly states that Claude’s same-session stop-hook loop is not being ported in v1, and maps the old Claude commands to the new Codex usage.
- The plugin is fully self-contained under the target folder. Do not add a repo or home marketplace entry in this pass.

## Target Files

Create or modify exactly these paths under `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex`:

- `.codex-plugin/plugin.json`
- `README.md`
- `skills/ralf/SKILL.md`
- `skills/ralf/agents/openai.yaml`
- `skills/ralf-handoff-plan/SKILL.md`
- `skills/ralf-handoff-plan/agents/openai.yaml`
- `skills/ralf-execute/SKILL.md`
- `skills/ralf-execute/agents/openai.yaml`
- `skills/ralf-cp-execute/SKILL.md`
- `skills/ralf-cp-execute/agents/openai.yaml`
- `skills/ralf-cp-review/SKILL.md`
- `skills/ralf-cp-review/agents/openai.yaml`
- `skills/ralf-review-squash/SKILL.md`
- `skills/ralf-review-squash/agents/openai.yaml`

Do not modify these source-of-truth paths during the port:

- `/Users/evren/code/agent_flow/skills/ralf-handoff-plan/**`
- `/Users/evren/code/agent_flow/skills/ralf-execute/**`
- `/Users/evren/code/agent_flow/skills/ralf-cp-execute/**`
- `/Users/evren/code/agent_flow/skills/ralf-cp-review/**`
- `/Users/evren/code/agent_flow/skills/ralf-review-squash/**`
- `/Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-claude/**`

The plugin should vendor copies of the current RALF skills into its own `skills/` tree. Do not use symlinks. The plugin must remain portable as a standalone local plugin folder.

## Public Interface Changes

### Plugin identity

- Plugin folder name: `ralph-loop-codex`
- Plugin manifest `name`: `ralph-loop-codex`
- Plugin display name: `RALF for Codex`
- Plugin category: `Productivity`

### User-facing skills

Ship these user-visible skills in the plugin:

- `ralf`
  - New front-door skill.
  - Purpose: choose the correct packaged RALF workflow based on user intent.
  - Behavior:
    - If the user needs a new checkpoint handoff plan, route to the bundled `$ralf-handoff-plan`.
    - If the user names an existing plan or asks to continue execution, route to `$ralf-execute`.
    - If the user explicitly asks for one checkpoint only, route to `$ralf-cp-execute`.
    - If the user asks to review checkpoint-sized work, route to `$ralf-cp-review`.
    - If the user asks for the final accumulated review and squash path, route to `$ralf-review-squash`.
    - If the intent is ambiguous, ask one targeted clarification question instead of guessing.
- `ralf-handoff-plan`
- `ralf-execute`
- `ralf-cp-execute`
- `ralf-cp-review`
- `ralf-review-squash`

Before porting, the only Ralph UX in this target folder is Claude slash-command markdown plus a stop hook. After porting, the Codex plugin UX is skill-first and checkpoint-oriented.

### Manifest contents

Set concrete manifest fields instead of `[TODO: ...]` placeholders:

- `version`: start at `0.1.0`
- `description`: short summary that this plugin packages Codex-native RALF planning and execution workflows
- `author`: Evren metadata
- `repository` and `homepage`: point to the repository or profile that is already authoritative for this work
- `skills`: `./skills/`
- Do not declare `hooks`, `mcpServers`, or `apps` in v1 unless an actual file exists and is needed
- `interface.defaultPrompt`: include 2-3 short prompts that steer users toward the front-door `ralf` skill or the autonomous executor

## Sequential Steps

1. Scaffold the plugin root.
   Before: `codex-ralph-loop-plugin` contains `ralph-loop-claude` only.
   After: `ralph-loop-codex/.codex-plugin/plugin.json` exists and the plugin has a `skills/` tree.
   Use the local plugin-creator scaffold only for structure creation. Do not keep placeholder manifest values after implementation.

2. Create the plugin manifest.
   Before: no Codex plugin manifest exists in the target directory.
   After: `plugin.json` contains real metadata for a standalone local plugin, points only to `./skills/`, and makes no marketplace assumptions.
   Do not add `.mcp.json`, `.app.json`, `hooks.json`, or asset references unless corresponding files are actually created.

3. Vendor the existing RALF skills into the plugin.
   Before: the authoritative RALF skills live only under `/Users/evren/code/agent_flow/skills/`.
   After: the plugin contains copies of the current `ralf-handoff-plan`, `ralf-execute`, `ralf-cp-execute`, `ralf-cp-review`, and `ralf-review-squash` skill folders, including their `agents/openai.yaml` files.
   Preserve the operational semantics of those skills. Only edit them inside the plugin copy when an instruction must change because the context is now “inside the plugin” rather than “inside the source repo”.

4. Add the front-door `ralf` skill.
   Before: users must know which specific RALF skill to invoke.
   After: `skills/ralf/SKILL.md` and `skills/ralf/agents/openai.yaml` provide a single starting point that routes to the correct bundled skill.
   Keep this skill thin. It should be a router and explainer, not a second implementation of the RALF logic.

5. Write plugin-local documentation.
   Before: the only documentation in the target directory describes Claude’s slash commands, stop hook, and same-session loop.
   After: `ralph-loop-codex/README.md` explains:
   - this is a Codex-native RALF workflow plugin
   - the primary abstraction is checkpoint-based fresh-context execution, not a stop-hook loop
   - the bundled skills and when to use each
   - how the old Claude commands map conceptually:
     - `/ralph-loop` maps to using `ralf` or `ralf-execute` after a plan exists
     - `/cancel-ralph` has no direct plugin-state equivalent in v1 because execution is plan-driven rather than state-file driven
     - `/help` maps to the README and skill descriptions

6. Tighten wording for v1 boundaries.
   Add explicit statements in the README and the front-door skill that v1 does not promise:
   - Claude-style stop-hook interception
   - same-session prompt reinjection
   - marketplace integration
   - external runner scripts that shell out to Codex CLI
   The implementation should favor correctness and portability over mimicking Claude behavior that Codex does not natively need for the chosen objective.

## Constraints

- Do not attempt to port `hooks/stop-hook.sh` or Claude `hooks/hooks.json` into the Codex plugin.
- Do not port Claude slash-command markdown files verbatim into the Codex plugin.
- Do not create a marketplace entry in `/Users/evren/code/agent_flow/.agents/plugins/marketplace.json`.
- Do not rely on symlinks back to `/Users/evren/code/agent_flow/skills`; the plugin must stand on its own.
- Do not silently change the semantics of the bundled RALF skills while copying them.
- Do not invent Codex hook, app, or MCP wiring that is not needed for the chosen execution model.
- Do not edit unrelated dirty files in the main repo worktree while doing this port.

## Edge Cases And Requirements

- If any bundled skill text references repo-root paths that only make sense in `/Users/evren/code/agent_flow`, update the plugin copy so the instructions still make sense when the plugin is installed elsewhere.
- If any bundled skill assumes persistence rules that conflict with plugin packaging, keep the runtime behavior and adjust only the explanatory text.
- If the front-door `ralf` skill cannot infer whether the user wants planning, execution, checkpoint execution, or review, it must ask a narrow clarification question instead of routing incorrectly.
- If the README references state files or stop hooks, it must clearly mark them as Claude-specific prior behavior, not Codex v1 behavior.
- If the plugin manifest includes default prompts, ensure they mention the packaged skill names exactly as they exist in the plugin.

## Verification

Run these commands after implementation:

1. Manifest validity:
   - `jq . /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/.codex-plugin/plugin.json >/dev/null`
2. Required file presence:
   - `test -f /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/.codex-plugin/plugin.json`
   - `test -f /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/README.md`
   - `test -f /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills/ralf/SKILL.md`
   - `test -f /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills/ralf-execute/SKILL.md`
3. Skill inventory:
   - `fd -a . /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills`
4. Placeholder check:
   - `rg -n '\[TODO:' /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/.codex-plugin/plugin.json /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/README.md /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex/skills`
   - Expect no matches in `plugin.json`. Matches in skill bodies are allowed only if they are part of copied instructional examples and not unresolved plugin packaging placeholders.
5. No forbidden Claude-port leftovers inside the Codex plugin:
   - `fd -a . /Users/evren/code/agent_flow/codex-ralph-loop-plugin/ralph-loop-codex | rg 'stop-hook\.sh|hooks\.json|/ralph-loop|/cancel-ralph'`
   - Expected result: README may mention the old commands for mapping purposes, but the plugin should not contain Claude hook files or slash-command implementation files.
6. Git review sanity:
   - `git -C /Users/evren/code/agent_flow status --short`
   - Confirm the changes are limited to the new plugin folder and the saved plan file if that file is included in the same working session.

## Acceptance Scenarios

- A user browsing the plugin can understand that this is the Codex distribution of RALF workflows, not a Claude hook clone.
- A user can invoke the front-door `ralf` skill and be steered to the correct bundled workflow without needing the old command names.
- A user who already has a RALF plan can use the bundled `ralf-execute` skill to continue from the first unchecked checkpoint with fresh checkpoint context.
- A user can access checkpoint-scoped execution and both review modes through the plugin without depending on repo-global skills.

## Assumptions And Defaults

- The correct v1 architecture is Codex-native RALF, not faithful same-session Ralph loop emulation.
- The plugin remains standalone local in `/Users/evren/code/agent_flow/codex-ralph-loop-plugin` and does not participate in marketplace discovery in this pass.
- `ralph-loop-codex` is the final plugin folder and manifest name for the Codex port.
- The host copy under `/Users/evren/code/agent_flow` is the working source of truth for planning because the OrbStack VM was offline during planning.
- No extra Codex CLI runner script is required in v1 because the user’s stated priority is the execution model, not the trigger mechanism.

## Final Checklist

- [ ] Plugin root exists at `ralph-loop-codex`
- [ ] `plugin.json` has concrete metadata and no unresolved scaffold placeholders
- [ ] Bundled RALF skills are present inside the plugin
- [ ] Front-door `ralf` skill exists and routes correctly
- [ ] README documents the Codex-native model and v1 boundaries
- [ ] No Claude stop-hook implementation files were copied into the Codex plugin
- [ ] No marketplace file was added or modified
- [ ] Verification commands have been run and checked
