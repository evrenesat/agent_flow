from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

from .api import (
    PreparedRun,
    StartupQuestion,
    StartupQuestionKind,
    StartupRequest,
    prepare_startup,
    prepare_startup_with_answer,
)
from .config import (
    ConfigError,
    bootstrap_config,
    _bootstrap_config_files,
    find_placeholders,
    load_workflow_config,
    validate_workflow_config,
    WorkflowStepConfig,
)
from .git_status import probe_worktree, classify_dirtiness_by_prefix
from .plan import PlanParseError, load_plan, load_plan_tolerant
from .skill_installer import InstallerError, install_skills
from .run_state import ControllerConfig, RetryContext, WorkflowEndReason, describe_end_reason
from .analyzer import (
    analyze_corpus,
    analyze_single_run,
    collect_run_dirs,
    find_latest_run_dir,
    is_noise_run,
    load_json,
    resolve_run_id,
)
from .workflow import (
    WorkflowError,
    _effective_retry_limit,
    generate_new_plan_path,
    move_completed_plan_to_done,
    render_step_prompts,
    resolve_role_selector,
    resolve_profile,
    run_workflow,
)

RUN_HELP = """\
Flags:
  --plan/-p PLAN_FILE       Path to the plan Markdown file.
  --workflow/-w WORKFLOW    Name of the workflow to run (default from config).
  --start-step/-ss STEP     Start from this step name or 1-based index (default: first).
  --team/-t TEAM_NAME       Override workflow team.
  --max-turns/-mt N         Maximum turns (default from config).

Positional arguments:
  [workflow_name] [plan_file]   Either form works:
                                  - One positional: treated as plan_file
                                  - Two positionals: first is workflow (if it matches a config name),
                                    second is plan_file
                                  If only one token matches a workflow name, the other is the plan.

Extra instructions:
  Append -- followed by free-form text to pass extra instructions to each step prompt.

Examples:
  aflow run path/to/plan.md
  aflow run ralph path/to/plan.md
  aflow run --workflow ralph --plan path/to/plan.md
  aflow run --plan path/to/plan.md --start-step my_step
  aflow run -mt 10 -ss 2 ralph plan.md
  aflow run plan.md -- keep edits small and update docs if behavior changes
"""

INSTALL_SKILLS_HELP = """\
Auto mode: omit DESTINATION to install the default bundled skills into each supported harness skill
directory for the harness CLIs found on PATH.

Manual mode: provide DESTINATION to install the eight default bundled skills into that root, one
subdirectory per skill.

Selection flags:
  --include-optional    Include optional bundled skills in the installation.
  --only SKILL          Install only the named skill(s). Can be repeated. Cannot be combined
                        with --include-optional.

Supported auto targets:
  claude -> ~/.claude/skills
  codex -> ~/.agents/skills
  copilot -> ~/.agents/skills
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


def _deduplicate_preserve_order(seq: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


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
        "--plan", "-p",
        type=str,
        default=None,
        metavar="PLAN_FILE",
        help="Path to the plan Markdown file.",
    )
    run_parser.add_argument(
        "--workflow", "-w",
        type=str,
        default=None,
        metavar="WORKFLOW_NAME",
        help="Name of the workflow to run.",
    )
    run_parser.add_argument(
        "--max-turns", "-mt",
        type=_positive_int,
        default=None,
        metavar="N",
        help="Maximum number of turns for this run. Defaults to [aflow].max_turns.",
    )
    run_parser.add_argument(
        "--team", "-t",
        type=str,
        default=None,
        metavar="TEAM_NAME",
        help="Override the workflow team for this run.",
    )
    run_parser.add_argument(
        "--start-step", "-ss",
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
            "Root directory that will receive the eight default bundled skill subdirectories. "
            "Omit it to auto-detect supported harness CLIs on PATH and install into each harness's "
            "global skill directory."
        ),
    )
    install_parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    install_parser.add_argument(
        "--include-optional",
        action="store_true",
        help="Include optional bundled skills in the installation.",
    )
    install_parser.add_argument(
        "--only",
        action="append",
        metavar="SKILL",
        help="Install only the named skill(s). Can be repeated. Cannot be combined with --include-optional.",
    )

    analyze_parser = subparsers.add_parser(
        "analyze",
        description="Analyze aflow run logs and extract high-signal debugging information.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyze_parser.add_argument(
        "run_id",
        nargs="?",
        help="Run ID to analyze. If not provided, uses AFLOW_LAST_RUN_ID environment variable or .aflow/last_run_id file.",
    )
    analyze_parser.add_argument(
        "--all",
        action="store_true",
        help="Analyze a corpus of runs instead of a single run.",
    )
    analyze_parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root containing .aflow/runs. Defaults to current directory.",
    )
    analyze_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of run directories to include in corpus mode (default: 20).",
    )
    analyze_parser.add_argument(
        "--include-noise",
        action="store_true",
        help="Include low-signal test noise runs instead of filtering them out.",
    )

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


def _resolve_run_arguments(
    plan_flag: str | None,
    workflow_flag: str | None,
    run_args: list[str],
    workflow_config: WorkflowConfig,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Resolve plan and workflow from explicit flags and positional args.

    Positional parsing:
      - Extract positionals before '--' (if present); everything after is extra instructions
      - 1 positional: treat as plan file
      - 2+ positionals: infer by checking if token resolves to existing file vs configured workflow name
      - extra positionals beyond 2 are appended to extra instructions

    Duplicate handling:
      - If plan comes from both flag and positional, they must resolve to the same value
      - If workflow comes from both flag and positional, they must resolve to the same value
      - Conflicting duplicates trigger a clear error
      - Ambiguous dual-positionals (both plan candidates, both workflow candidates, or neither) trigger a clear error

    Returns (workflow_name, plan_file, extra_instructions) where workflow_name and/or plan_file
    may be None if not determinable.
    """
    if "--" in run_args:
        sep = run_args.index("--")
        extra_instructions = tuple(run_args[sep + 1 :])
        positionals = run_args[:sep]
    else:
        extra_instructions = ()
        positionals = run_args

    known_workflows = set(workflow_config.workflows.keys())

    # Extract positional plan and workflow candidates
    positional_plan = None
    positional_workflow = None
    extra_positionals = []

    if len(positionals) == 0:
        pass
    elif len(positionals) == 1:
        # Single positional is always treated as plan, never as workflow
        positional_plan = positionals[0]
    else:
        # Two or more positionals: resolve by meaning
        first_token = positionals[0]
        second_token = positionals[1]

        # Check which token is a workflow and whether each file exists
        first_is_workflow = first_token in known_workflows
        second_is_workflow = second_token in known_workflows
        first_exists = Path(first_token).exists()
        second_exists = Path(second_token).exists()

        # Apply resolution rules in order:
        # 1. If exactly one is a workflow, treat the other as plan (even if it doesn't exist)
        #    Only accept this if the workflow token is not also an existing file (which would create ambiguity)
        if first_is_workflow and not second_is_workflow:
            if first_exists and second_exists:
                # Both tokens are existing files, and first is also a workflow -> ambiguous
                raise ValueError(
                    f"error: cannot determine which positional is the plan file: "
                    f"'{first_token}' is a configured workflow and also resolves to an existing file, "
                    f"and '{second_token}' resolves to an existing file. "
                    f"Only one plan file is allowed per run. Use --plan to specify which one."
                )
            positional_workflow = first_token
            positional_plan = second_token
        elif second_is_workflow and not first_is_workflow:
            if first_exists and second_exists:
                # Both tokens are existing files, and second is also a workflow -> ambiguous
                raise ValueError(
                    f"error: cannot determine which positional is the plan file: "
                    f"'{second_token}' is a configured workflow and also resolves to an existing file, "
                    f"and '{first_token}' resolves to an existing file. "
                    f"Only one plan file is allowed per run. Use --plan to specify which one."
                )
            positional_workflow = second_token
            positional_plan = first_token
        # 2. If both are workflows, both are workflow names -> ambiguous
        elif first_is_workflow and second_is_workflow:
            raise ValueError(
                f"error: cannot determine which positional is the plan file and which is the workflow: "
                f"'{first_token}' and '{second_token}'. "
                f"Both are configured workflow names. "
                f"Use --plan and --workflow flags to disambiguate."
            )
        # 3. If neither is a workflow, check file existence to distinguish plan from workflow intent
        else:
            # Neither is a workflow name
            if first_exists and second_exists:
                # Both are existing files -> can't choose which is plan
                raise ValueError(
                    f"error: cannot determine which positional is the plan file: "
                    f"both '{first_token}' and '{second_token}' resolve to existing files. "
                    f"Only one plan file is allowed per run. Use --plan to specify which one."
                )
            elif first_exists and not second_exists:
                # First exists, second doesn't -> first is plan, second is unclassified
                raise ValueError(
                    f"error: cannot determine which positional is the plan file and which is the workflow: "
                    f"'{first_token}' resolves to an existing file, but '{second_token}' is neither a "
                    f"configured workflow name nor an existing file. "
                    f"Use --plan and --workflow flags to specify them explicitly."
                )
            elif second_exists and not first_exists:
                # Second exists, first doesn't -> second is plan, first is unclassified
                raise ValueError(
                    f"error: cannot determine which positional is the plan file and which is the workflow: "
                    f"'{second_token}' resolves to an existing file, but '{first_token}' is neither a "
                    f"configured workflow name nor an existing file. "
                    f"Use --plan and --workflow flags to specify them explicitly."
                )
            else:
                # Neither exists and neither is a workflow -> can't determine
                raise ValueError(
                    f"error: cannot determine which positional is the plan file and which is the workflow: "
                    f"'{first_token}' and '{second_token}'. "
                    f"Neither resolves to an existing file, and neither is a configured workflow name. "
                    f"Use --plan and --workflow flags to specify them explicitly."
                )

        # Collect extra positionals beyond the first two
        if len(positionals) > 2:
            extra_positionals = positionals[2:]

    # Resolve final values from flags and positionals
    final_plan = None
    final_workflow = None

    # Handle plan resolution
    if plan_flag is not None and positional_plan is not None:
        # Canonicalize both paths for comparison
        flag_resolved = Path(plan_flag).expanduser().resolve()
        positional_resolved = Path(positional_plan).expanduser().resolve()
        if flag_resolved != positional_resolved:
            raise ValueError(
                f"error: conflicting plan specifications: --plan='{plan_flag}' but positional '{positional_plan}'. "
                f"These must resolve to the same file."
            )
        final_plan = plan_flag  # Use the user-provided spelling, not the resolved one
    elif plan_flag is not None:
        final_plan = plan_flag
    elif positional_plan is not None:
        final_plan = positional_plan

    # Handle workflow resolution
    if workflow_flag is not None and positional_workflow is not None:
        if workflow_flag != positional_workflow:
            raise ValueError(
                f"error: conflicting workflow specifications: --workflow='{workflow_flag}' but positional '{positional_workflow}'. "
                f"These must resolve to the same workflow name."
            )
        final_workflow = workflow_flag
    elif workflow_flag is not None:
        final_workflow = workflow_flag
    elif positional_workflow is not None:
        final_workflow = positional_workflow

    # Append any extra positionals to extra instructions
    all_extra = tuple(extra_positionals) + extra_instructions

    return final_workflow, final_plan, all_extra


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


def _resolve_numeric_start_step(raw_value: str, workflow: WorkflowConfig) -> tuple[str, str | None]:
    """
    Resolve a raw start-step value (from --start-step/-ss) to a canonical step name.

    If raw_value is a plain ASCII base-10 integer (only ASCII decimal digits 0-9), treat it as a 1-based workflow step index.
    Otherwise, treat it as a step name.

    Returns (resolved_step_name, error_message).
    If successful, error_message is None.
    If parsing or validation fails, resolved_step_name is the raw_value and error_message describes the issue.
    """
    step_names = list(workflow.steps.keys())

    # Check if raw_value is a plain ASCII base-10 integer (only ASCII decimal digits, no signs or underscores)
    is_ascii_decimal = raw_value and all(c in '0123456789' for c in raw_value)
    if is_ascii_decimal:
        index = int(raw_value)

        # Validate numeric index
        if index < 1 or index > len(step_names):
            available = ", ".join(step_names)
            error = (
                f"error: start-step index {index} is out of range. "
                f"Valid indexes: 1 to {len(step_names)}. "
                f"Available steps: {available}"
            )
            return raw_value, error

        # Map 1-based index to step name
        resolved_name = step_names[index - 1]
        return resolved_name, None
    else:
        # Not a plain ASCII integer, treat as step name
        return raw_value, None


def _confirm_startup_recovery(error_message: str) -> bool:
    print(error_message, file=sys.stderr)
    try:
        response = input("Recover using the existing retry flow? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return response in ("y", "yes")


def _print_bootstrap_paths(config_path: Path) -> None:
    workflows_path = config_path.with_name("workflows.toml")
    print(
        "Bootstrapped aflow config files. Edit these paths and rerun when ready:",
        file=sys.stderr,
    )
    print(f"  {config_path}", file=sys.stderr)
    print(f"  {workflows_path}", file=sys.stderr)


def _maybe_move_completed_plan_to_done(repo_root: Path, plan_path: Path, *, is_complete: bool) -> Path:
    if not is_complete or not plan_path.is_file():
        return plan_path
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if not is_tty:
        return plan_path

    in_progress_root = (repo_root / "plans" / "in-progress").resolve()
    try:
        plan_path.resolve().relative_to(in_progress_root)
    except ValueError:
        return plan_path

    try:
        response = input(
            f"Plan '{plan_path.name}' is complete and still in plans/in-progress. "
            "Move it to plans/done? [Y/n]: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return plan_path
    if response in ("", "y", "yes"):
        return move_completed_plan_to_done(repo_root, plan_path)
    return plan_path


def run_install_skills(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(["install-skills"] + ([] if argv is None else argv))
    try:
        only_skills = _deduplicate_preserve_order(tuple(args.only)) if args.only else None
        install_skills(
            destination=args.destination,
            yes=args.yes,
            only_skills=only_skills,
            include_optional=args.include_optional,
        )
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
            only_skills = _deduplicate_preserve_order(tuple(args.only)) if args.only else None
            install_skills(
                destination=args.destination,
                yes=args.yes,
                only_skills=only_skills,
                include_optional=args.include_optional,
            )
        except InstallerError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0

    if args.command == "analyze":
        import json

        if args.all:
            repo_root = (args.repo_root or Path.cwd()).resolve()
            runs_root = repo_root / ".aflow" / "runs"
            if not runs_root.is_dir():
                print(f"error: runs root does not exist: {runs_root}", file=sys.stderr)
                return 1
            run_dirs = collect_run_dirs(runs_root)
            if args.limit is not None and args.limit > 0:
                run_dirs = run_dirs[-args.limit :]
            payload = analyze_corpus(
                run_dirs=run_dirs,
                runs_root=runs_root,
                selection="corpus",
                include_noise=args.include_noise,
            )
        else:
            repo_root = (args.repo_root or Path.cwd()).resolve()
            runs_root = repo_root / ".aflow" / "runs"

            if args.run_id is not None:
                run_dir = runs_root / args.run_id
                selection = "explicit_run_id"
            else:
                resolved_run_id, source = resolve_run_id(None, repo_root)
                if resolved_run_id is None:
                    print(
                        "error: no run ID specified and no last run ID found. "
                        "Provide a run ID as an argument, set AFLOW_LAST_RUN_ID environment variable, "
                        "or ensure .aflow/last_run_id file exists.",
                        file=sys.stderr,
                    )
                    return 1
                if source == "env_var":
                    selection = "env_var"
                elif source == "last_run_id_file":
                    selection = "last_run_id_file"
                else:
                    selection = "unknown"
                run_dir = runs_root / resolved_run_id.name

            if not (run_dir / "run.json").is_file():
                print(f"error: run directory does not contain run.json: {run_dir}", file=sys.stderr)
                return 1

            payload = analyze_single_run(
                run_dir=run_dir,
                runs_root=runs_root,
                selection=selection,
                include_noise=args.include_noise,
            )

        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    config_path: Path | None = None
    if args.command in (None, "run"):
        config_path, created_paths = _bootstrap_config_files()
        if created_paths:
            _print_bootstrap_paths(config_path)
            return 0

    if args.command != "run":
        parser.print_help(sys.stderr)
        return 1

    repo_root = _resolve_repo_root()
    if repo_root is None:
        return 1
    working_dir = Path.cwd()

    if config_path is None:
        config_path = bootstrap_config()

    try:
        workflow_config = load_workflow_config(config_path)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        workflow_arg, plan_file_arg, extra_instructions = _resolve_run_arguments(
            args.plan, args.workflow, args.run_args, workflow_config
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    if plan_file_arg is None:
        print("error: plan_file is required", file=sys.stderr)
        return 1

    plan_path = Path(plan_file_arg).expanduser().resolve()

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

    startup_workflow_name = workflow_arg
    startup_start_step = args.start_step
    resolved_workflow_name = workflow_arg or workflow_config.aflow.default_workflow
    if startup_start_step is not None and resolved_workflow_name in workflow_config.workflows:
        resolved_step, error_msg = _resolve_numeric_start_step(
            startup_start_step,
            workflow_config.workflows[resolved_workflow_name],
        )
        if error_msg is not None:
            print(error_msg, file=sys.stderr)
            return 1
        startup_workflow_name = resolved_workflow_name
        startup_start_step = resolved_step

    startup_request = StartupRequest(
        repo_root=repo_root,
        plan_path=plan_path,
        config_path=config_path,
        workflow_config=workflow_config,
        workflow_name=startup_workflow_name,
        start_step=startup_start_step,
        max_turns=args.max_turns,
        team=args.team,
        extra_instructions=extra_instructions,
    )

    prepared_run = _handle_startup_questions(startup_request)
    if prepared_run is None:
        return 1

    config = ControllerConfig(
        repo_root=prepared_run.repo_root,
        plan_path=prepared_run.plan_path,
        max_turns=prepared_run.max_turns,
        keep_runs=workflow_config.aflow.keep_runs,
        team=prepared_run.team,
        extra_instructions=prepared_run.extra_instructions,
        start_step=prepared_run.start_step,
    )

    try:
        result = run_workflow(
            config,
            workflow_config,
            prepared_run.workflow_name,
            parsed_plan=prepared_run.parsed_plan,
            startup_retry=prepared_run.startup_retry,
            config_dir=config_path.parent,
            working_dir=working_dir,
        )
    except WorkflowError as exc:
        print(exc.summary, file=sys.stderr)
        return 1
    try:
        _maybe_move_completed_plan_to_done(
            prepared_run.repo_root,
            prepared_run.plan_path,
            is_complete=prepared_run.move_completed_plan_to_done,
        )
    except WorkflowError as exc:
        print(exc.summary, file=sys.stderr)
        return 1
    print(_format_success_summary(prepared_run.workflow_name, result.turns_completed, result.end_reason))
    return 0


def _handle_startup_questions(request: StartupRequest) -> PreparedRun | None:
    """Process startup questions interactively, returning PreparedRun or None on error."""
    from .api.startup import StartupError

    try:
        result = prepare_startup(request)
    except StartupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None

    while isinstance(result, StartupQuestion):
        answer = _answer_startup_question(result)
        if answer is None or (isinstance(answer, bool) and not answer):
            print("startup aborted", file=sys.stderr)
            return None

        try:
            result = prepare_startup_with_answer(result, request, answer)
            if isinstance(result, PreparedRun):
                break
        except StartupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return None

    return result


def _answer_startup_question(question: StartupQuestion) -> str | int | bool | None:
    """Interactively answer a startup question."""
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()

    if question.kind == StartupQuestionKind.CONFIRM_RECOVERY:
        if not is_tty:
            print(
                f"error: {question.message} "
                "Interactive confirmation is required.",
                file=sys.stderr,
            )
            return None
        print(question.message, file=sys.stderr)
        try:
            response = input("Recover using the existing retry flow? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return response in ("y", "yes")

    if question.kind == StartupQuestionKind.PICK_STEP:
        if not is_tty:
            step_names = question.choices
            print(
                f"error: {question.message} "
                f"Re-run with --start-step STEP_NAME. Available steps: {', '.join(step_names)}",
                file=sys.stderr,
            )
            return None
        step_names = question.choices
        step_index = _pick_workflow_step_interactive(step_names)
        if step_index is None:
            return None
        return step_index

    if question.kind == StartupQuestionKind.CONFIRM_WORKTREE_DIRTY:
        if not is_tty:
            print(
                f"error: {question.message} "
                "Interactive confirmation is required.",
                file=sys.stderr,
            )
            return None
        try:
            response = input(f"{question.message} [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        return response in ("y", "yes")

    return None


def _pick_workflow_step_interactive(step_names: list[str]) -> int | None:
    """Interactively pick a workflow step by index."""
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
        return choice - 1


if __name__ == "__main__":
    raise SystemExit(main())
