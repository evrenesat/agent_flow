---
description: Start a Ralph Wiggum loop for iterative development
---

# Ralph Loop Command

You are starting a Ralph Wiggum loop. This is an iterative development technique where you work on the same task repeatedly, seeing your previous work in files and git history.

## Setup Instructions

Execute the following steps to initialize the Ralph loop:

1. Parse the arguments from: `$ARGUMENTS`

   Arguments format: `<PROMPT> [--max-iterations N] [--completion-promise TEXT]`

   - Extract the main prompt (everything that isn't a flag or flag value)
   - Extract `--max-iterations` value if provided (default: 0 for unlimited)
   - Extract `--completion-promise` value if provided (default: null)

2. Create the state file at `ralph-loop.local.md` (in the project root) with this exact format:

```markdown
---
active: true
iteration: 1
max_iterations: <MAX_ITERATIONS_VALUE>
completion_promise: <COMPLETION_PROMISE_VALUE_OR_null>
started_at: "<CURRENT_ISO_TIMESTAMP>"
---

<THE_PROMPT_TEXT>
```

3. Output the activation message:

```
Ralph loop activated!

Iteration: 1
Max iterations: <N or "unlimited">
Completion promise: <TEXT or "none (runs forever)">

The Ralph plugin will now monitor for session idle events. When you complete
your response, the same prompt will be fed back to continue the loop.

To stop the loop:
- Output <promise>YOUR_PROMISE</promise> if a completion promise is set
- Wait for max iterations to be reached
- Run /cancel-ralph to cancel manually
```

4. If a completion promise is set, display this critical warning:

```
CRITICAL - Ralph Loop Completion Promise

To complete this loop, output this EXACT text:
  <promise>YOUR_PROMISE_HERE</promise>

STRICT REQUIREMENTS:
  - Use <promise> XML tags EXACTLY as shown above
  - The statement MUST be completely and unequivocally TRUE
  - Do NOT output false statements to exit the loop
  - Do NOT lie even if you think you should exit

IMPORTANT: Even if you believe you're stuck or the task is impossible,
you MUST NOT output a false promise. The loop continues until the
promise is GENUINELY TRUE.
```

5. Now begin working on the task from the prompt. The Ralph plugin will automatically continue feeding you the same prompt when you complete your response.

## Example Usage

```
/ralph-loop Build a REST API for todos --completion-promise "DONE" --max-iterations 20
/ralph-loop Fix the auth bug --max-iterations 10
/ralph-loop Refactor the cache layer
```
