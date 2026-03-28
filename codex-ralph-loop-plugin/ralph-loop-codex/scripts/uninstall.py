#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
import json
import os
import re
from typing import Any


PLUGIN_NAME = "ralf-loop-codex"
PAYLOAD_LINK_NAME = "ralf-loop-codex"
MANAGED_COMMENT = "# ralf-loop-codex managed settings"


def load_hooks_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def remove_managed_entries(entries: list[dict[str, Any]], script_name: str) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        keep_entry = True
        for hook in entry.get("hooks", []):
            command = hook.get("command", "")
            if isinstance(command, str) and script_name in command:
                keep_entry = False
                break
        if keep_entry:
            filtered.append(entry)
    return filtered


def prune_hooks_file(path: Path) -> None:
    if not path.exists():
        return
    data = load_hooks_json(path)
    hooks = data.get("hooks", {})
    if "SessionStart" in hooks:
        hooks["SessionStart"] = remove_managed_entries(hooks["SessionStart"], "ralf_session_start.py")
    if "Stop" in hooks:
        hooks["Stop"] = remove_managed_entries(hooks["Stop"], "ralf_stop_continue.py")
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def prune_config_file(path: Path) -> None:
    if not path.exists():
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    filtered: list[str] = []
    skip_comment = False
    for line in lines:
        stripped = line.strip()
        if stripped == MANAGED_COMMENT:
            skip_comment = True
            continue
        if re.match(r"^\s*codex_hooks\s*=\s*true\s*$", line):
            continue
        filtered.append(line)

    path.write_text("\n".join(filtered).rstrip() + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Remove the Codex RALF runtime from live Codex locations.")
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--bin-dir", default=str(Path.home() / "bin"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    codex_home = Path(args.codex_home).expanduser().resolve()
    bin_dir = Path(args.bin_dir).expanduser().resolve()

    launcher = bin_dir / "ralf-codex"
    if launcher.is_symlink():
        launcher.unlink()

    payload_link = codex_home / PAYLOAD_LINK_NAME
    if payload_link.is_symlink():
        payload_link.unlink()

    prune_hooks_file(codex_home / "hooks.json")
    prune_config_file(codex_home / "config.toml")

    print(f"Uninstalled {PLUGIN_NAME} runtime from {codex_home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
