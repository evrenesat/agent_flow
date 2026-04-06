#!/usr/bin/env python3
"""Thin wrapper for aflow analyze CLI command.

This script is retained for compatibility with older documentation but
delegates all analysis to the aflow analyze CLI command.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_noise_run(run_json: dict) -> bool:
    return (
        run_json.get("workflow_name") == "other"
        and run_json.get("turns_completed") == 0
        and run_json.get("end_reason") == "already_complete"
    )


def find_latest_run_dir(
    run_dirs: list[Path],
    *,
    include_noise: bool,
) -> tuple[Path, int]:
    skipped_noise = 0
    for run_dir in reversed(run_dirs):
        run_json_path = run_dir / "run.json"
        if not run_json_path.is_file():
            continue
        run_json = load_json(run_json_path)
        if is_noise_run(run_json) and not include_noise:
            skipped_noise += 1
            continue
        return run_dir, skipped_noise
    if skipped_noise:
        raise SystemExit("No substantive runs found under the selected runs root; rerun with --include-noise if you want test-noise runs.")
    raise SystemExit("No runs with run.json were found under the selected runs root.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Wrapper for aflow analyze. This script is retained for compatibility "
            "but delegates to the aflow analyze CLI command."
        )
    )
    parser.add_argument("--repo-root", type=Path, help="Repository root containing .aflow/runs")
    parser.add_argument("--runs-root", type=Path, help="Path to a .aflow/runs directory")
    parser.add_argument("--run", type=Path, help="Analyze exactly one run directory by path")
    parser.add_argument("--run-id", help="Analyze exactly one run by run-id")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Analyze a run corpus instead of a single run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of run directories to include in corpus mode.",
    )
    parser.add_argument(
        "--include-noise",
        action="store_true",
        help="Include low-signal test noise runs instead of filtering them out.",
    )
    args = parser.parse_args()

    if args.run is not None and args.run_id is not None:
        parser.error("--run and --run-id cannot be combined")
    if args.run is not None and (args.repo_root is not None or args.runs_root is not None):
        parser.error("--run cannot be combined with --repo-root or --runs-root")
    if args.all and args.run is not None:
        parser.error("--all cannot be combined with --run")
    if args.all and args.run_id is not None:
        parser.error("--all cannot be combined with --run-id")

    return args


def main() -> int:
    args = parse_args()

    cmd = [sys.executable, "-m", "aflow", "analyze"]

    if args.repo_root is not None:
        cmd.extend(["--repo-root", str(args.repo_root)])
    elif args.runs_root is not None:
        cmd.extend(["--repo-root", str(args.runs_root.parent.parent)])

    if args.all:
        cmd.append("--all")

    if args.limit is not None and args.all:
        cmd.extend(["--limit", str(args.limit)])

    if args.include_noise:
        cmd.append("--include-noise")

    if args.run_id is not None:
        cmd.append(args.run_id)
    elif args.run is not None:
        cmd.append(args.run.name)
    elif not args.all:
        # For backward compatibility, find the latest run if no run ID is provided
        if args.runs_root is not None:
            runs_root = args.runs_root
        else:
            repo_root = args.repo_root or Path.cwd()
            runs_root = repo_root / ".aflow" / "runs"

        if runs_root.is_dir():
            run_dirs = sorted(path for path in runs_root.iterdir() if path.is_dir())
            latest_run_dir, _ = find_latest_run_dir(run_dirs, include_noise=args.include_noise)
            cmd.append(latest_run_dir.name)

    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
