# RALF for Codex

This plugin packages the Codex Ralph source, hidden runtime payload, and install scripts.

## What this plugin does

The loop runtime comes from this plugin's hidden payload, but it must be installed into Codex's live locations:

- `~/.codex/hooks.json`
- `~/.codex/config.toml`
- a bin directory on your `PATH`

The source of truth stays in this repo. The installer wires the runtime into the locations Codex actually reads.

## Bundled skills

- `ralf`, the front door. Use this when you want the plugin to explain the Codex RALF flow and point you to the launcher.

## What you need

For the loop itself, you only need:

- a plan file with real `### [ ] Checkpoint ...` headings
- the installed `ralf-codex` launcher
- the installed Codex hook entries

No extra RALF skills are required for whole-plan loop execution.

## Install

Run:

```bash
python3 scripts/install.py
```

By default this:

- symlinks `.codex-runtime/` into `~/.codex/ralf-loop-codex`
- merges the managed Ralph hooks into `~/.codex/hooks.json`
- enables `codex_hooks = true` in `~/.codex/config.toml`
- symlinks `bin/ralf-codex` into `~/bin/ralf-codex`

Optional flags:

```bash
python3 scripts/install.py --codex-home ~/.codex --bin-dir ~/bin --force
```

Remove the live runtime with:

```bash
python3 scripts/uninstall.py
```

## Quick start

If you already have a plan and want Codex CLI to drive it:

```bash
ralf-codex path/to/plan.md
```

If you want to start the run from the Codex app:

```bash
ralf-codex --prepare-only path/to/plan.md
```

Then paste the printed prompt into a Codex app thread for the actual project you want to work in.

## Optional skills

The repo's RALF skills are optional helpers, not runtime dependencies.

- Use them if you want Codex to create a plan for you.
- Use them if you want checkpoint-only execution or review-specific behavior outside the full loop.
- Ignore them if you already have a good plan file and just want the loop manager.

## Boundaries

The plugin is the source bundle. The live runtime still has to be installed.

- Installing the plugin alone is not enough to activate hooks.
- Whole-plan Codex continuation depends on the installed hook entries and launcher.
- The loop does not depend on the extra RALF skills.

## Notes

- The hidden runtime payload lives under `.codex-runtime/`.
- The install scripts are the supported way to wire this plugin into live Codex paths.
- Cancel a run by removing `ralph-loop.local.md` or setting `active: false`.
