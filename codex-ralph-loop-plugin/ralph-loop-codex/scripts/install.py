#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path
import json
import os
import re
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_DIR = PLUGIN_ROOT / ".codex-runtime"
LAUNCHER_SRC = PLUGIN_ROOT / "bin" / "ralf-codex"
PLUGIN_NAME = "ralf-loop-codex"
PAYLOAD_LINK_NAME = "ralf-loop-codex"
MANAGED_COMMENT = "# ralf-loop-codex managed settings"


def session_start_entry(script_path: Path) -> dict[str, Any]:
    return {
        "matcher": "startup|resume",
        "hooks": [
            {
                "type": "command",
                "command": f'python3 "{script_path}"',
                "statusMessage": "Loading RALF session state",
            }
        ],
    }


def stop_entry(script_path: Path) -> dict[str, Any]:
    return {
        "hooks": [
            {
                "type": "command",
                "command": f'python3 "{script_path}"',
                "statusMessage": "Evaluating RALF continuation",
                "timeout": 30,
            }
        ],
    }


def ensure_symlink(link_path: Path, target_path: Path, *, force: bool) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.exists() or link_path.is_symlink():
        if link_path.is_symlink() and link_path.resolve() == target_path.resolve():
            return
        if not force:
            raise RuntimeError(f"{link_path} already exists. Re-run with --force to replace it.")
        if link_path.is_dir() and not link_path.is_symlink():
            raise RuntimeError(f"{link_path} is a directory. Remove it manually before re-running install.")
        link_path.unlink()
    link_path.symlink_to(target_path)


def load_hooks_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"hooks": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def remove_managed_entries(entries: list[dict[str, Any]], script_name: str) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        command_blocks = entry.get("hooks", [])
        keep_entry = True
        for hook in command_blocks:
            command = hook.get("command", "")
            if isinstance(command, str) and script_name in command:
                keep_entry = False
                break
        if keep_entry:
            filtered.append(entry)
    return filtered


def merge_hooks_file(path: Path, session_script: Path, stop_script: Path) -> None:
    data = load_hooks_json(path)
    hooks = data.setdefault("hooks", {})

    session_entries = hooks.setdefault("SessionStart", [])
    stop_entries = hooks.setdefault("Stop", [])

    hooks["SessionStart"] = remove_managed_entries(session_entries, "ralf_session_start.py")
    hooks["SessionStart"].append(session_start_entry(session_script))

    hooks["Stop"] = remove_managed_entries(stop_entries, "ralf_stop_continue.py")
    hooks["Stop"].append(stop_entry(stop_script))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def merge_config_text(existing: str) -> str:
    if not existing.strip():
        return f"{MANAGED_COMMENT}\n[features]\ncodex_hooks = true\n"

    lines = existing.splitlines()
    features_start = None
    features_end = len(lines)

    for index, line in enumerate(lines):
        if line.strip() == "[features]":
            features_start = index
            for probe in range(index + 1, len(lines)):
                if re.match(r"^\[.+\]$", lines[probe].strip()):
                    features_end = probe
                    break
            break

    if features_start is None:
        suffix = "\n" if existing.endswith("\n") else "\n\n"
        return existing + suffix + f"{MANAGED_COMMENT}\n[features]\ncodex_hooks = true\n"

    for index in range(features_start + 1, features_end):
        if re.match(r"^\s*codex_hooks\s*=", lines[index]):
            lines[index] = "codex_hooks = true"
            return "\n".join(lines) + ("\n" if existing.endswith("\n") else "")

    insert_at = features_end
    lines.insert(insert_at, "codex_hooks = true")
    return "\n".join(lines) + ("\n" if existing.endswith("\n") else "")


def merge_config_file(path: Path) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    merged = merge_config_text(existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merged, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the Codex RALF runtime into live Codex locations.")
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--bin-dir", default=str(Path.home() / "bin"))
    parser.add_argument("--force", action="store_true", help="Replace existing symlinks managed by this installer.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    codex_home = Path(args.codex_home).expanduser().resolve()
    bin_dir = Path(args.bin_dir).expanduser().resolve()

    payload_link = codex_home / PAYLOAD_LINK_NAME
    ensure_symlink(payload_link, PAYLOAD_DIR, force=args.force)
    ensure_symlink(bin_dir / "ralf-codex", LAUNCHER_SRC, force=args.force)

    merge_hooks_file(
        codex_home / "hooks.json",
        payload_link / "hooks" / "ralf_session_start.py",
        payload_link / "hooks" / "ralf_stop_continue.py",
    )
    merge_config_file(codex_home / "config.toml")

    print(f"Installed {PLUGIN_NAME} runtime into {codex_home}")
    print(f"Launcher symlink: {bin_dir / 'ralf-codex'}")
    print(f"Payload symlink: {payload_link}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
