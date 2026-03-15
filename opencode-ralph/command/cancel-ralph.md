---
description: Cancel the active Ralph Wiggum loop
---

# Cancel Ralph Loop

To cancel the Ralph loop, perform these steps:

1. Check if the Ralph state file exists at `ralph-loop.local.md`

2. If the file does NOT exist:
   - Report: "No active Ralph loop found."

3. If the file EXISTS:
   - Read the file to get the current iteration number from the `iteration:` field in the frontmatter
   - Delete the file `ralph-loop.local.md`
   - Report: "Cancelled Ralph loop (was at iteration N)" where N is the iteration value

Execute:
```bash
if [ -f ralph-loop.local.md ]; then
  ITERATION=$(grep '^iteration:' ralph-loop.local.md | sed 's/iteration: *//')
  rm ralph-loop.local.md
  echo "Cancelled Ralph loop (was at iteration $ITERATION)"
else
  echo "No active Ralph loop found."
fi
```
