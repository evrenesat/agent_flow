#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import sys


HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from ralf_common import evaluate_stop, get_repo_root


def main() -> int:
    payload = json.load(sys.stdin)
    repo_root = get_repo_root(payload.get("cwd"))
    decision = evaluate_stop(
        repo_root,
        stop_hook_active=bool(payload.get("stop_hook_active")),
        last_assistant_message=payload.get("last_assistant_message"),
    )

    if decision.action == "noop":
        return 0

    if decision.action == "stop":
        response: dict[str, object] = {"continue": False}
        if decision.system_message:
            response["systemMessage"] = decision.system_message
        print(json.dumps(response))
        return 0

    if decision.action == "continue":
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": decision.reason,
                }
            )
        )
        return 0

    raise RuntimeError(f"Unexpected stop decision action: {decision.action}")


if __name__ == "__main__":
    raise SystemExit(main())
