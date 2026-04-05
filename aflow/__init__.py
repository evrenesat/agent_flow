"""aflow workflow runner.

Public library API for workflow startup and execution:

    from aflow import StartupRequest, prepare_startup

    request = StartupRequest(...)
    result = prepare_startup(request)
    # result is either a PreparedRun (ready to execute) or a StartupQuestion (needs user input)
"""

from .api import (
    PreparedRun,
    StartupError,
    StartupQuestion,
    StartupQuestionKind,
    StartupRequest,
    prepare_startup,
    prepare_startup_with_answer,
)

__all__ = [
    "PreparedRun",
    "StartupError",
    "StartupQuestion",
    "StartupQuestionKind",
    "StartupRequest",
    "prepare_startup",
    "prepare_startup_with_answer",
]
