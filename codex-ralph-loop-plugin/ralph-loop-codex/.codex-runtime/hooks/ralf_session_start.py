#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import sys


HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from ralf_common import build_session_context, get_repo_root, load_state


def main() -> int:
    payload = json.load(sys.stdin)
    repo_root = get_repo_root(payload.get("cwd"))
    state = load_state(repo_root)
    if state is None:
        return 0

    context = build_session_context(state)
    if not context:
        return 0

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
