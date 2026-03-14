#!/bin/bash

set -euo pipefail

DRY_RUN=0
PLAN_FILE="IMPLEMENTATION_PLAN.md"
RUNTIME="${RALF_RUNTIME:-opencode}"
STATE_FILE="ralph-loop.local.md"
COMPLETION_PROMISE="PLAN_COMPLETE"
MAX_ITERATIONS=20
PYTHON_BIN="${PYTHON_BIN:-python3}"
INACTIVITY_TIMEOUT_SECONDS="${RALF_INACTIVITY_TIMEOUT_SECONDS:-90}"

usage() {
  cat <<'EOF'
Usage:
  ralf [--runtime opencode|gemini] [--dry-run] [path/to/plan.md]

Options:
  -r, --runtime   Agent runtime to use. Defaults to "opencode".
      --dry-run   Print the command without executing it.
  -h, --help      Show this help text.
EOF
}

run_opencode_json() {
  "$PYTHON_BIN" - "$STATE_FILE" "$INACTIVITY_TIMEOUT_SECONDS" "${COMMAND[@]}" <<'PY'
import json
import os
import selectors
import subprocess
import sys
import time

state_file = sys.argv[1]
timeout_seconds = float(sys.argv[2])
command = sys.argv[3:]

def emit(text: str, *, stream=sys.stdout) -> None:
    stream.write(text + "\n")
    stream.flush()

def summarize_state(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    iteration = None
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("iteration:"):
                iteration = line.split(":", 1)[1].strip()
                break
    return iteration

proc = subprocess.Popen(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)

selector = selectors.DefaultSelector()
assert proc.stdout is not None
assert proc.stderr is not None
selector.register(proc.stdout, selectors.EVENT_READ, ("stdout", proc.stdout))
selector.register(proc.stderr, selectors.EVENT_READ, ("stderr", proc.stderr))
last_activity = time.monotonic()

while selector.get_map():
    events = selector.select(timeout=1.0)
    if not events:
        if proc.poll() is not None:
            for key in list(selector.get_map().values()):
                selector.unregister(key.fileobj)
            break
        if timeout_seconds > 0 and time.monotonic() - last_activity > timeout_seconds:
            proc.kill()
            emit(
                f"Error: OpenCode produced no output for {int(timeout_seconds)} seconds; terminating Ralph run.",
                stream=sys.stderr,
            )
            sys.exit(124)
        continue

    for key, _mask in events:
        source, stream = key.data
        line = stream.readline()
        if line == "":
            selector.unregister(stream)
            continue

        last_activity = time.monotonic()
        text = line.strip()
        if not text:
            continue

        if source == "stderr":
            emit(text, stream=sys.stderr)
            continue

        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            emit(text, stream=sys.stderr)
            continue

        event_type = event.get("type")
        part = event.get("part") or {}

        if event_type == "text":
            payload = part.get("text")
            if payload:
                emit(payload)
            continue

        if event_type == "tool_use":
            state = part.get("state") or {}
            status = state.get("status")
            tool = part.get("tool") or "tool"
            title = state.get("title") or ""
            if status == "completed" and title:
                emit(f"[{tool}] {title}")
            elif status == "error":
                output = state.get("output") or "tool failed"
                emit(f"[{tool}] {title}: {output}".strip(), stream=sys.stderr)
            continue

        if event_type in {"error", "session.error"}:
            emit(text, stream=sys.stderr)

returncode = proc.wait()
if returncode != 0:
    sys.exit(returncode)

if os.path.exists(state_file):
    iteration = summarize_state(state_file)
    emit(
        f"Error: OpenCode exited but Ralph state is still active in {state_file}.",
        stream=sys.stderr,
    )
    if iteration:
        emit(f"Last recorded iteration: {iteration}", stream=sys.stderr)
    emit(
        "The run likely stopped before marking the checkpoint or emitting the completion promise.",
        stream=sys.stderr,
    )
    sys.exit(1)
PY
}

write_opencode_state_file() {
  local started_at="$1"

  cat > "$STATE_FILE" <<EOF
---
active: true
iteration: 1
max_iterations: ${MAX_ITERATIONS}
completion_promise: "${COMPLETION_PROMISE}"
started_at: "${started_at}"
---

${PLAN_PROMPT}
EOF
}

print_opencode_state_file() {
  local started_at="$1"

  cat <<EOF
---
active: true
iteration: 1
max_iterations: ${MAX_ITERATIONS}
completion_promise: "${COMPLETION_PROMISE}"
started_at: "${started_at}"
---

${PLAN_PROMPT}
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -r|--runtime)
      if [ "$#" -lt 2 ]; then
        echo "Error: --runtime requires a value." >&2
        usage >&2
        exit 1
      fi
      RUNTIME="$2"
      shift 2
      ;;
    --runtime=*)
      RUNTIME="${1#*=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Error: Unknown option '$1'." >&2
      usage >&2
      exit 1
      ;;
    *)
      PLAN_FILE="$1"
      shift
      ;;
  esac
done

case "$RUNTIME" in
  opencode|gemini)
    ;;
  *)
    echo "Error: Unsupported runtime '$RUNTIME'. Use 'opencode' or 'gemini'." >&2
    exit 1
    ;;
esac

if [ ! -f "$PLAN_FILE" ]; then
  echo "Error: Plan file '$PLAN_FILE' not found." >&2
  exit 1
fi

ABS_PLAN_PATH="$(cd "$(dirname "$PLAN_FILE")" && pwd)/$(basename "$PLAN_FILE")"
PLAN_PROMPT="Read the plan file at ${ABS_PLAN_PATH}. Review the overall progress to understand what has already been done, then continue from where the plan was left off by finding the first unchecked checkpoint (### [ ]). Read the context, scope, and steps for that checkpoint. Execute the work. Run the required verification commands. If verification passes, modify ${ABS_PLAN_PATH} to change that specific '### [ ]' to '### [x]'. Create a git commit. If there are no unchecked checkpoints remaining in the file, output exactly <promise>${COMPLETION_PROMISE}</promise>. Do not output that promise unless it is completely and unequivocally true."
STARTED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

case "$RUNTIME" in
  gemini)
    COMMAND=(gemini -y -s -p "/ralph:loop \"${PLAN_PROMPT}\" --completion-promise \"${COMPLETION_PROMISE}\" --max-iterations ${MAX_ITERATIONS}")
    ;;
  opencode)
    COMMAND=(opencode run --format json "$PLAN_PROMPT")
    ;;
esac

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run enabled. The following command would be executed:"
  echo ""
  if [ "$RUNTIME" = "opencode" ]; then
    echo "State file (${STATE_FILE}):"
    echo ""
    print_opencode_state_file "$STARTED_AT"
    echo ""
  fi
  printf '%q ' "${COMMAND[@]}"
  echo ""
  echo ""
  exit 0
fi

if ! command -v "${COMMAND[0]}" >/dev/null 2>&1; then
  echo "Error: Required command '${COMMAND[0]}' is not installed or not on PATH." >&2
  exit 1
fi

if [ "$RUNTIME" = "opencode" ]; then
  write_opencode_state_file "$STARTED_AT"
fi

if [ "$RUNTIME" = "opencode" ]; then
  run_opencode_json
  exit $?
fi

"${COMMAND[@]}"
