# Ralph Wiggum Plugin for OpenCode

Implementation of the Ralph Wiggum technique for iterative, self-referential AI development loops in OpenCode.

Ported from the [Claude Code Ralph Plugin](https://github.com/anthropics/claude-code/tree/main/plugins/ralph-wiggum).

## What is Ralph?

Ralph is a development methodology based on continuous AI agent loops. As Geoffrey Huntley describes it: **"Ralph is a Bash loop"** - a simple `while true` that repeatedly feeds an AI agent a prompt file, allowing it to iteratively improve its work until completion.

The technique is named after Ralph Wiggum from The Simpsons, embodying the philosophy of persistent iteration despite setbacks.

### Core Concept

This plugin implements Ralph using OpenCode's event system to intercept session idle states:

```bash
# You run ONCE:
/ralph-loop "Your task description" --completion-promise "DONE"

# Then OpenCode automatically:
# 1. Works on the task
# 2. Finishes responding
# 3. Plugin intercepts idle state
# 4. Plugin feeds the SAME prompt back
# 5. Repeat until completion
```

The loop happens **inside your current session** - you don't need external bash loops. The plugin creates the self-referential feedback loop by intercepting the session idle event.

This creates a **self-referential feedback loop** where:
- The prompt never changes between iterations
- The AI's previous work persists in files
- Each iteration sees modified files and git history
- The AI autonomously improves by reading its own past work in files

## Installation

Clone or copy this repo, then symlink or copy to your global OpenCode config:

```bash
# Clone the repo
git clone https://github.com/anthropics/opencode-ralph.git
cd opencode-ralph

# Symlink to global config (recommended - updates automatically)
ln -s "$(pwd)/plugin/ralph.ts" ~/.config/opencode/plugin/ralph.ts
ln -s "$(pwd)/command/ralph-loop.md" ~/.config/opencode/command/ralph-loop.md
ln -s "$(pwd)/command/cancel-ralph.md" ~/.config/opencode/command/cancel-ralph.md
ln -s "$(pwd)/command/ralph-help.md" ~/.config/opencode/command/ralph-help.md

# Or copy files directly
cp plugin/* ~/.config/opencode/plugin/
cp command/* ~/.config/opencode/command/
```

For project-level installation, copy to `.opencode/`:

```bash
cp plugin/* /path/to/your/project/.opencode/plugin/
cp command/* /path/to/your/project/.opencode/command/
```

## Quick Start

```bash
/ralph-loop "Build a REST API for todos. Requirements: CRUD operations, input validation, tests. Output <promise>COMPLETE</promise> when done." --completion-promise "COMPLETE" --max-iterations 50
```

The AI will:
- Implement the API iteratively
- Run tests and see failures
- Fix bugs based on test output
- Iterate until all requirements met
- Output the completion promise when done

## Commands

### /ralph-loop

Start a Ralph loop in your current session.

**Usage:**
```bash
/ralph-loop "<prompt>" --max-iterations <n> --completion-promise "<text>"
```

**Options:**
- `--max-iterations <n>` - Stop after N iterations (default: unlimited)
- `--completion-promise <text>` - Phrase that signals completion

### /cancel-ralph

Cancel the active Ralph loop.

**Usage:**
```bash
/cancel-ralph
```

### /ralph-help

Get detailed help about the Ralph technique and commands.

**Usage:**
```bash
/ralph-help
```

## Prompt Writing Best Practices

### 1. Clear Completion Criteria

Bad: "Build a todo API and make it good."

Good:
```markdown
Build a REST API for todos.

When complete:
- All CRUD endpoints working
- Input validation in place
- Tests passing (coverage > 80%)
- README with API docs
- Output: <promise>COMPLETE</promise>
```

### 2. Incremental Goals

Bad: "Create a complete e-commerce platform."

Good:
```markdown
Phase 1: User authentication (JWT, tests)
Phase 2: Product catalog (list/search, tests)
Phase 3: Shopping cart (add/remove, tests)

Output <promise>COMPLETE</promise> when all phases done.
```

### 3. Self-Correction

Bad: "Write code for feature X."

Good:
```markdown
Implement feature X following TDD:
1. Write failing tests
2. Implement feature
3. Run tests
4. If any fail, debug and fix
5. Refactor if needed
6. Repeat until all green
7. Output: <promise>COMPLETE</promise>
```

### 4. Escape Hatches

Always use `--max-iterations` as a safety net to prevent infinite loops:

```bash
# Recommended: Always set a reasonable iteration limit
/ralph-loop "Try to implement feature X" --max-iterations 20
```

## Philosophy

Ralph embodies several key principles:

### 1. Iteration > Perfection
Don't aim for perfect on first try. Let the loop refine the work.

### 2. Failures Are Data
"Deterministically bad" means failures are predictable and informative. Use them to tune prompts.

### 3. Operator Skill Matters
Success depends on writing good prompts, not just having a good model.

### 4. Persistence Wins
Keep trying until success. The loop handles retry logic automatically.

## When to Use Ralph

**Good for:**
- Well-defined tasks with clear success criteria
- Tasks requiring iteration and refinement (e.g., getting tests to pass)
- Greenfield projects where you can walk away
- Tasks with automatic verification (tests, linters)

**Not good for:**
- Tasks requiring human judgment or design decisions
- One-shot operations
- Tasks with unclear success criteria
- Production debugging (use targeted debugging instead)

## Files

- `plugin/ralph.ts` - Main plugin that handles the loop logic
- `command/ralph-loop.md` - Command to start a Ralph loop
- `command/cancel-ralph.md` - Command to cancel the loop
- `command/ralph-help.md` - Help documentation

## State File

The plugin stores loop state in `ralph-loop.local.md` in your project root:

```markdown
---
active: true
iteration: 5
max_iterations: 20
completion_promise: "DONE"
started_at: "2024-01-15T10:30:00Z"
---

Your prompt text here...
```

Add this file to `.gitignore` to avoid committing loop state.

## Learn More

- Original technique: https://ghuntley.com/ralph/
- Ralph Orchestrator: https://github.com/mikeyobrien/ralph-orchestrator
- Claude Code plugin: https://github.com/anthropics/claude-code/tree/main/plugins/ralph-wiggum
