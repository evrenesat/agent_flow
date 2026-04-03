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
    WorkflowStepConfig,
)
from .git_status import probe_worktree
from .plan import PlanParseError, load_plan, load_plan_tolerant
from .skill_installer import InstallerError, install_skills
from .run_state import ControllerConfig, RetryContext, WorkflowEndReason, describe_end_reason
from .workflow import (
    WorkflowError,
    _effective_retry_limit,
    generate_new_plan_path,
    render_step_prompts,
    resolve_role_selector,
    resolve_profile,
    run_workflow,
)

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
  codex -> ~/.agents/skills
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
        default=None,
        metavar="N",
        help="Maximum number of turns for this run. Defaults to [aflow].max_turns.",
    )
    run_parser.add_argument(
        "--team",
        type=str,
        default=None,
        metavar="TEAM_NAME",
        help="Override the workflow team for this run.",
    )
    run_parser.add_argument(
        "--start-step",
        type=str,
        default=None,
        metavar="STEP_NAME",
        help="Start the workflow from a specific step instead of the first step.",
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


def _pick_workflow_step(steps: dict[str, WorkflowStepConfig]) -> str | None:
    step_names = list(steps.keys())
    if not step_names:
        return None

    while True:
        print("Select the workflow step to start from:")
        for index, step_name in enumerate(step_names, start=1):
            print(f"  {index}. {step_name}")
        try:
            response = input(f"Enter a number between 1 and {len(step_names)}: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        try:
            choice = int(response)
        except ValueError:
            print(
                f"error: enter a number between 1 and {len(step_names)}",
                file=sys.stderr,
            )
            continue
        if choice < 1 or choice > len(step_names):
            print(
                f"error: enter a number between 1 and {len(step_names)}",
                file=sys.stderr,
            )
            continue
        return step_names[choice - 1]


def _confirm_startup_recovery(error_message: str) -> bool:
    print(error_message, file=sys.stderr)
    try:
        response = input("Recover using the existing retry flow? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return response in ("y", "yes")


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

    workflow = workflow_config.workflows[workflow_name]
    effective_team = args.team if args.team is not None else workflow.team
    if effective_team is not None and effective_team not in workflow_config.teams:
        known_teams = ", ".join(sorted(workflow_config.teams)) or "none"
        print(
            f"error: workflow '{workflow_name}' references unknown team '{effective_team}'. "
            f"Known teams: {known_teams}",
            file=sys.stderr,
        )
        return 1
    effective_max_turns = (
        args.max_turns if args.max_turns is not None else workflow_config.aflow.max_turns
    )
    if args.start_step is not None and args.start_step not in workflow.steps:
        print(
            f"error: step '{args.start_step}' not found in workflow '{workflow_name}'. "
            f"Available steps: {', '.join(workflow.steps.keys())}",
            file=sys.stderr,
        )
        return 1

    parsed_plan = None
    startup_retry: RetryContext | None = None
    selected_start_step = args.start_step
    try:
        parsed_plan = load_plan(plan_path)
    except PlanParseError as exc:
        if exc.error_kind != "inconsistent_checkpoint_state":
            print(exc, file=sys.stderr)
            return 1
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            print(
                "error: startup recovery for inconsistent checkpoint state requires an interactive terminal.",
                file=sys.stderr,
            )
            return 1
        if not _confirm_startup_recovery(str(exc)):
            print("startup aborted", file=sys.stderr)
            return 1
        try:
            tolerant_result = load_plan_tolerant(plan_path)
        except FileNotFoundError as exc2:
            print(exc2, file=sys.stderr)
            return 1
        parsed_plan = tolerant_result.parsed_plan
        startup_retry_error = str(tolerant_result.parse_error or exc)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    else:
        startup_retry_error = None

    assert parsed_plan is not None
    if parsed_plan.snapshot.is_complete:
        if args.start_step is not None:
            print("error: plan is already complete, --start-step has no effect", file=sys.stderr)
            return 1
    else:
        has_completed_checkpoint = any(section.heading_checked for section in parsed_plan.sections)
        if selected_start_step is None:
            if len(workflow.steps) > 1 and has_completed_checkpoint:
                if not (sys.stdin.isatty() and sys.stdout.isatty()):
                    print(
                        f"error: workflow '{workflow_name}' has multiple steps and interactive startup selection requires a terminal. "
                        "Re-run with --start-step STEP_NAME.",
                        file=sys.stderr,
                    )
                    return 1
                selected_start_step = _pick_workflow_step(workflow.steps)
                if selected_start_step is None:
                    print("startup aborted", file=sys.stderr)
                    return 1
            else:
                selected_start_step = workflow.first_step

        if startup_retry_error is not None:
            if selected_start_step is None:
                print(
                    f"workflow '{workflow_name}' has no steps",
                    file=sys.stderr,
                )
                return 1
            step = workflow.steps[selected_start_step]
            step_path = f"workflow.{workflow_name}.steps.{selected_start_step}"
            selector = resolve_role_selector(
                step.role,
                effective_team,
                workflow_config,
                step_path=step_path,
            )
            resolved = resolve_profile(selector, workflow_config, step_path=step_path)
            checkpoint_index = parsed_plan.snapshot.current_checkpoint_index or 1
            new_plan_path = generate_new_plan_path(plan_path, checkpoint_index=checkpoint_index)
            base_user_prompt = render_step_prompts(
                step,
                workflow_config,
                config_dir=config_path.parent,
                working_dir=working_dir,
                original_plan_path=plan_path,
                new_plan_path=new_plan_path,
                active_plan_path=plan_path,
            )
            startup_retry = RetryContext(
                step_name=selected_start_step,
                step_role=step.role,
                resolved_selector=selector,
                resolved_harness_name=resolved.harness_name,
                resolved_model=resolved.model,
                resolved_effort=resolved.effort,
                snapshot_before=parsed_plan.snapshot,
                active_plan_path=plan_path,
                new_plan_path=new_plan_path,
                base_user_prompt=base_user_prompt,
                parse_error_str=startup_retry_error,
                attempt=1,
                retry_limit=_effective_retry_limit(workflow, workflow_config.aflow),
            )

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
        max_turns=effective_max_turns,
        keep_runs=workflow_config.aflow.keep_runs,
        team=effective_team,
        extra_instructions=extra_instructions,
        start_step=selected_start_step,
    )

    try:
        result = run_workflow(
            config,
            workflow_config,
            workflow_name,
            parsed_plan=parsed_plan,
            startup_retry=startup_retry,
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
