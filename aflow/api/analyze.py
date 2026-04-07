"""Public analysis helper for aflow run logs."""

from __future__ import annotations

from typing import Any

from aflow.analyzer import analyze_corpus, analyze_single_run, collect_run_dirs, resolve_run_id

from .models import AnalyzeRequest


def analyze_runs(request: AnalyzeRequest) -> dict[str, Any]:
    """Analyze one run or a corpus of runs using the same payload shape as the CLI."""

    repo_root = request.repo_root.resolve()
    runs_root = repo_root / ".aflow" / "runs"

    if request.all:
        if not runs_root.is_dir():
            raise ValueError(f"runs root does not exist: {runs_root}")
        run_dirs = collect_run_dirs(runs_root)
        if request.limit is not None and request.limit > 0:
            run_dirs = run_dirs[-request.limit :]
        return analyze_corpus(
            run_dirs=run_dirs,
            runs_root=runs_root,
            selection="corpus",
            include_noise=request.include_noise,
        )

    if request.run_id is not None:
        run_dir = runs_root / request.run_id
        selection = "explicit_run_id"
    else:
        resolved_run_dir, source = resolve_run_id(None, repo_root)
        if resolved_run_dir is None:
            raise ValueError(
                "no run ID specified and no last run ID found. "
                "Provide a run ID as an argument, use the current shell's last run, "
                "set AFLOW_LAST_RUN_ID environment variable, or ensure .aflow/last_run_id file exists."
            )
        selection = source or "unknown"
        run_dir = runs_root / resolved_run_dir.name

    if not (run_dir / "run.json").is_file():
        raise ValueError(f"run directory does not contain run.json: {run_dir}")

    return analyze_single_run(
        run_dir=run_dir,
        runs_root=runs_root,
        selection=selection,
        include_noise=request.include_noise,
    )
