"""Tests for the Codex thread gateway interface and normalized models."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aflow_app_server.codex_thread_gateway import (
    CodexThreadGateway,
    CodexThreadPage,
    UserInput,
    UserInputText,
)
from aflow_app_server.models import CodexThread, CodexThreadMutationResult, CodexTurn


class RecordingGateway(CodexThreadGateway):
    """Minimal gateway used to prove the interface is thread-centric."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.thread = CodexThread(
            id="thread-1",
            preview="hello",
            ephemeral=False,
            model_provider="openai",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            status={"type": "idle"},
            path=Path("/tmp/project/.codex/thread-1"),
            cwd="/tmp/project",
            cli_version="1.2.3",
            source="app-server",
            agent_nickname=None,
            agent_role=None,
            git_info={"branch": "main"},
            name="Example",
            turns=[],
        )

    def list_threads(self, **kwargs: Any) -> CodexThreadPage:
        self.calls.append(("list_threads", (), kwargs))
        return CodexThreadPage(threads=[self.thread], next_cursor=None)

    def read_thread(self, thread_id: str, *, include_turns: bool = True) -> CodexThread:
        self.calls.append(("read_thread", (thread_id,), {"include_turns": include_turns}))
        return self.thread

    def start_thread(self, **kwargs: Any) -> CodexThreadMutationResult:
        self.calls.append(("start_thread", (), kwargs))
        return self._mutation_result()

    def _mutation_result(self) -> CodexThreadMutationResult:
        return CodexThreadMutationResult(
            thread=self.thread,
            model="o3",
            model_provider="openai",
            service_tier=None,
            cwd=self.thread.cwd,
            approval_policy="never",
            approvals_reviewer={"mode": "default"},
            sandbox={"mode": "workspace-write"},
            reasoning_effort="medium",
        )

    def resume_thread(self, thread_id: str, **kwargs: Any) -> CodexThreadMutationResult:
        self.calls.append(("resume_thread", (thread_id,), kwargs))
        return self._mutation_result()

    def fork_thread(self, thread_id: str, **kwargs: Any) -> CodexThreadMutationResult:
        self.calls.append(("fork_thread", (thread_id,), kwargs))
        return self._mutation_result()

    def set_thread_name(self, thread_id: str, name: str) -> None:
        self.calls.append(("set_thread_name", (thread_id, name), {}))

    def start_turn(self, thread_id: str, input: list[UserInput], **kwargs: Any) -> CodexTurn:
        self.calls.append(("start_turn", (thread_id, input), kwargs))
        return CodexTurn(id="turn-1", status="inProgress", items=input, error=None)


def test_gateway_interface_uses_threads_not_sessions() -> None:
    gateway = RecordingGateway()

    page = gateway.list_threads(cwd="/tmp/project", search_term="hello")
    thread = gateway.read_thread("thread-1")
    start_result = gateway.start_thread(cwd="/tmp/project")
    resume_result = gateway.resume_thread("thread-1", cwd="/tmp/new-project")
    fork_result = gateway.fork_thread("thread-1", cwd="/tmp/new-project")
    gateway.set_thread_name("thread-1", "Renamed")
    turn = gateway.start_turn(
        "thread-1",
        [UserInputText(type="text", text="hello", text_elements=[])],
        cwd="/tmp/project",
    )

    assert page.threads[0].cwd == "/tmp/project"
    assert thread.id == "thread-1"
    assert start_result.thread.id == "thread-1"
    assert resume_result.thread.id == "thread-1"
    assert fork_result.thread.id == "thread-1"
    assert turn.id == "turn-1"
    assert [call[0] for call in gateway.calls] == [
        "list_threads",
        "read_thread",
        "start_thread",
        "resume_thread",
        "fork_thread",
        "set_thread_name",
        "start_turn",
    ]


def test_normalized_thread_models_are_json_serializable() -> None:
    turn = CodexTurn(id="turn-1", status="completed", items=[{"type": "userMessage"}], error=None)
    thread = CodexThread(
        id="thread-1",
        preview="hello",
        ephemeral=False,
        model_provider="openai",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        status={"type": "active", "activeFlags": []},
        path=Path("/tmp/project/.codex/thread-1"),
        cwd="/tmp/project",
        cli_version="1.2.3",
        source="app-server",
        agent_nickname=None,
        agent_role=None,
        git_info={"branch": "main"},
        name="Example",
        turns=[turn],
    )
    result = CodexThreadMutationResult(
        thread=thread,
        model="o3",
        model_provider="openai",
        service_tier=None,
        cwd="/tmp/project",
        approval_policy="never",
        approvals_reviewer={"mode": "default"},
        sandbox={"mode": "workspace-write"},
        reasoning_effort="medium",
    )

    thread_payload = thread.to_dict()
    turn_payload = turn.to_dict()
    result_payload = result.to_dict()

    assert thread_payload["cwd"] == "/tmp/project"
    assert thread_payload["status"] == {"type": "active", "activeFlags": []}
    assert turn_payload["status"] == "completed"
    assert result_payload["thread"]["id"] == "thread-1"
    assert result_payload["approval_policy"] == "never"
