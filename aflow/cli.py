from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

from .config import (
    ConfigError,
    bootstrap_config,
    find_placeholders,
    load_workflow_config,
    validate_workflow_config,
)
from .git_status import probe_worktree
from .skill_installer import InstallerError, install_skills
from .run_state import ControllerConfig, WorkflowEndReason, describe_end_reason
from .workflow import WorkflowError, run_workflow


DEFAULT_MAX_TURNS = 15

RUN_HELP = """\
Positional arguments:
  [workflow_name]   Name of the workflow to run. Omit to use default_workflow from config.
  plan_file         Path to the plan Markdown file.

Extra instructions:
  Append -- followed by free-form text to pass extra instructions to each step prompt.

Examples:
  aflow run path/to/plan.md
  aflow run ralph path/to/plan.md
  aflow run -mt 10 path/to/plan.md
  aflow run path/to/plan.md -- keep edits small and update docs if behavior changes
"""

INSTALL_SKILLS_HELP = """\
Auto mode: omit DESTINATION to install the bundled skills into each supported harness skill directory
for the harness CLIs found on PATH.

Manual mode: provide DESTINATION to install the six bundled skills into that root, one subdirectory
per skill.

Supported auto targets:
  claude -> ~/.claude/skills
  codex -> ~/.codex/skills
  gemini -> ~/.agents/skills
  kiro -> ~/.kiro/skills
  opencode -> ~/.config/opencode/skills
  pi -> ~/.agents/skills
"""


def _resolve_repo_root() -> Path | None:
    """Resolve project root from cwd using git discovery.

    Returns the resolved root, or None when the run must be aborted due to an
    ambiguous root that cannot be resolved interactively.
    """
    working_dir = Path.cwd().resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(working_dir), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError):
        return working_dir

    if result.returncode != 0:
        return working_dir

    git_root = Path(result.stdout.strip()).resolve()
    if git_root == working_dir:
        return working_dir

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if not is_tty:
        print(
            f"error: current directory '{working_dir}' is inside a git repository "
            f"rooted at '{git_root}'.\n"
            f"Rerun from '{git_root}' to use the repository root, or rerun from a "
            f"directory that is its own git root.",
            file=sys.stderr,
        )
        return None

    try:
        response = input(
            f"Current directory '{working_dir}' is inside '{git_root}'.\n"
            f"Use git root '{git_root}' as project root? [Y/n]: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return working_dir

    if response in ("", "y", "yes"):
        return git_root
    return working_dir


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aflow",
        description="Run plan-driven coding workflows through existing agent CLIs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser(
        "run",
        description="Run an aflow workflow from a plan file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=RUN_HELP,
    )
    run_parser.add_argument(
        "--max-turns", "-mt",
        type=_positive_int,
        default=DEFAULT_MAX_TURNS,
        metavar="N",
        help=f"Maximum number of turns (default: {DEFAULT_MAX_TURNS}).",
    )
    run_parser.add_argument("run_args", nargs=argparse.REMAINDER)

    install_parser = subparsers.add_parser(
        "install-skills",
        description="Install the bundled aflow skills into harness skill directories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=INSTALL_SKILLS_HELP,
    )
    install_parser.add_argument(
        "destination",
        nargs="?",
        help=(
            "Root directory that will receive the six bundled skill subdirectories. "
            "Omit it to auto-detect supported harness CLIs on PATH and install into each harness's "
            "global skill directory."
        ),
    )
    install_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")

    return parser


def _parse_run_args(
    run_args: list[str],
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Split REMAINDER args into (workflow_name, plan_file, extra_instructions).

    With '--' present: everything before is positionals, everything after is extra.
    Without '--': all args are positionals, no extra instructions.

    Positional rules:
      1 positional  -> plan_file only, no workflow
      2+ positionals -> first is workflow, second is plan_file
    """
    if "--" in run_args:
        sep = run_args.index("--")
        extra = tuple(run_args[sep + 1 :])
        positionals = run_args[:sep]
    else:
        extra = ()
        positionals = run_args

    if not positionals:
        return None, None, extra

    if len(positionals) == 1:
        return None, positionals[0], extra

    workflow_name = positionals[0]
    plan_file = positionals[1]
    if len(positionals) > 2:
        extra = tuple(positionals[2:]) + extra
    return workflow_name, plan_file, extra


def _format_success_summary(workflow_name: str, turns_completed: int, end_reason: WorkflowEndReason) -> str:
    turn_label = "turn" if turns_completed == 1 else "turns"
    return (
        f"Workflow '{workflow_name}' completed after {turns_completed} {turn_label} "
        f"because {describe_end_reason(end_reason)}."
    )


def run_install_skills(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(["install-skills"] + ([] if argv is None else argv))
    try:
        install_skills(destination=args.destination, yes=args.yes)
    except InstallerError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(tokens)

    if args.command == "install-skills":
        try:
            install_skills(destination=args.destination, yes=args.yes)
        except InstallerError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0

    if args.command != "run":
        parser.print_help(sys.stderr)
        return 1

    workflow_arg, plan_file_arg, extra_instructions = _parse_run_args(args.run_args)

    if plan_file_arg is None:
        print("error: plan_file is required", file=sys.stderr)
        return 1

    repo_root = _resolve_repo_root()
    if repo_root is None:
        return 1
    working_dir = Path.cwd()
    plan_path = Path(plan_file_arg).expanduser().resolve()

    try:
        config_path = bootstrap_config()
        workflow_config = load_workflow_config(config_path)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    placeholders = find_placeholders(workflow_config)
    if placeholders:
        keys = "\n".join(f"  {k}" for k in placeholders)
        print(
            f"Config bootstrapped. Fill in the following model values before running:\n{keys}",
            file=sys.stderr,
        )
        return 1

    validation_errors = validate_workflow_config(workflow_config)
    if validation_errors:
        errors = "\n".join(f"  {e}" for e in validation_errors)
        print(
            f"Config validation errors:\n{errors}",
            file=sys.stderr,
        )
        return 1

    workflow_name = workflow_arg or workflow_config.aflow.default_workflow
    if workflow_name is None:
        print(
            "No workflow specified and no default_workflow set in config.",
            file=sys.stderr,
        )
        return 1

    if workflow_name not in workflow_config.workflows:
        print(
            f"Workflow '{workflow_name}' not found in config.",
            file=sys.stderr,
        )
        return 1

    probe = probe_worktree(repo_root)
    if probe is not None and probe.is_dirty:
        is_tty = sys.stdin.isatty() and sys.stdout.isatty()
        dirty_desc = f"M {probe.modified_count}, A {probe.added_count}, D {probe.removed_count}"
        if not is_tty:
            print(
                f"error: worktree is dirty ({dirty_desc}). "
                "Interactive confirmation is required to start with a dirty worktree.",
                file=sys.stderr,
            )
            return 1
        try:
            response = input(
                f"Worktree is dirty ({dirty_desc}). Start anyway? [y/N]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 1
        if response not in ("y", "yes"):
            return 1

    config = ControllerConfig(
        repo_root=repo_root,
        plan_path=plan_path,
        max_turns=args.max_turns,
        keep_runs=workflow_config.aflow.keep_runs,
        extra_instructions=extra_instructions,
    )

    try:
        result = run_workflow(
            config,
            workflow_config,
            workflow_name,
            config_dir=config_path.parent,
            working_dir=working_dir,
        )
    except WorkflowError as exc:
        print(exc.summary, file=sys.stderr)
        return 1
    print(_format_success_summary(workflow_name, result.turns_completed, result.end_reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
