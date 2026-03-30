from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .config import ConfigError, load_user_config, resolve_launch_config
from .controller import ControllerError, run_controller
from .harnesses import ADAPTERS
from .run_state import ControllerConfig


DEFAULT_MAX_TURNS = 15
DEFAULT_STAGNATION_LIMIT = 5
DEFAULT_KEEP_RUNS = 20


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aflow")
    parser.add_argument("--harness", choices=sorted(ADAPTERS))
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--profile")
    parser.add_argument("--max-turns", type=_positive_int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--stagnation-limit", type=_positive_int, default=DEFAULT_STAGNATION_LIMIT)
    parser.add_argument("--keep-runs", type=_positive_int, default=DEFAULT_KEEP_RUNS)
    parser.add_argument("plan_file")
    parser.add_argument("extra_instructions", nargs=argparse.REMAINDER)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    plan_path = Path(args.plan_file).expanduser().resolve()
    extra_instructions = tuple(token for token in args.extra_instructions if token != "--")
    try:
        user_config = load_user_config()
        resolved = resolve_launch_config(
            user_config,
            harness=args.harness,
            model=args.model,
            effort=args.effort,
            profile=args.profile,
        )
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    adapter = ADAPTERS[resolved.harness]
    if resolved.effort is not None and not adapter.supports_effort:
        print(
            f"warning: harness '{adapter.name}' ignores --effort; continuing without an effort flag",
            file=sys.stderr,
        )
    config = ControllerConfig(
        repo_root=repo_root,
        plan_path=plan_path,
        harness=resolved.harness,
        model=resolved.model,
        max_turns=args.max_turns,
        stagnation_limit=args.stagnation_limit,
        keep_runs=args.keep_runs,
        extra_instructions=extra_instructions,
        effort=resolved.effort,
    )

    try:
        run_controller(config)
    except ControllerError as exc:
        print(exc.summary, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
