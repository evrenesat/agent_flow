from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import (
    ConfigError,
    bootstrap_config,
    find_placeholders,
    load_workflow_config,
    validate_workflow_config,
)
from .skill_installer import InstallerError, install_skills
from .run_state import ControllerConfig, WorkflowEndReason, describe_end_reason
from .workflow import WorkflowError, run_workflow


DEFAULT_MAX_TURNS = 15
DEFAULT_KEEP_RUNS = 20
ROOT_HELP = """\
Commands:
  install-skills [DESTINATION] [--yes]
    Install the bundled aflow skills into harness skill directories.

Examples:
  aflow install-skills
  aflow install-skills ~/.claude/skills
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


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aflow",
        description="Run an aflow workflow from a plan file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=ROOT_HELP,
    )
    parser.add_argument("--workflow")
    parser.add_argument("--max-turns", type=_positive_int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--keep-runs", type=_positive_int, default=DEFAULT_KEEP_RUNS)
    parser.add_argument("plan_file")
    parser.add_argument("extra_instructions", nargs=argparse.REMAINDER)
    return parser


def build_install_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aflow install-skills",
        description="Install the bundled aflow skills into harness skill directories.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=INSTALL_SKILLS_HELP,
    )
    parser.add_argument(
        "destination",
        nargs="?",
        help=(
            "Root directory that will receive the six bundled skill subdirectories. "
            "Omit it to auto-detect supported harness CLIs on PATH and install into each harness's "
            "global skill directory."
        ),
    )
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _format_success_summary(workflow_name: str, turns_completed: int, end_reason: WorkflowEndReason) -> str:
    turn_label = "turn" if turns_completed == 1 else "turns"
    return (
        f"Workflow '{workflow_name}' completed after {turns_completed} {turn_label} "
        f"because {describe_end_reason(end_reason)}."
    )


def run_install_skills(argv: list[str] | None = None) -> int:
    args = build_install_parser().parse_args(argv)
    try:
        install_skills(destination=args.destination, yes=args.yes)
    except InstallerError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens and tokens[0] == "install-skills":
        return run_install_skills(tokens[1:])
    args = parse_args(tokens)
    repo_root = Path(__file__).resolve().parents[1]
    working_dir = Path.cwd()
    plan_path = Path(args.plan_file).expanduser().resolve()
    extra_instructions = tuple(token for token in args.extra_instructions if token != "--")
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

    workflow_name = args.workflow or workflow_config.aflow.default_workflow
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

    config = ControllerConfig(
        repo_root=repo_root,
        plan_path=plan_path,
        max_turns=args.max_turns,
        keep_runs=args.keep_runs,
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
