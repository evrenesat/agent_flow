"""Tests for the Codex app-server thread client."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

import pytest

from aflow_app_server.codex_app_server_client import CodexAppServerClient
from aflow_app_server.codex_thread_gateway import UserInputText


class FakeWebSocket(AbstractContextManager["FakeWebSocket"]):
    """Simple websocket double for JSON-RPC request/response tests."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.sent_messages: list[dict[str, Any]] = []
        self.uri: str | None = None
        self.additional_headers: dict[str, str] | None = None

    def send(self, payload: str) -> None:
        self.sent_messages.append(json.loads(payload))

    def recv(self) -> str:
        if not self.responses:
            raise AssertionError("Fake websocket received more reads than responses")
        return json.dumps(self.responses.pop(0))

    def __enter__(self) -> "FakeWebSocket":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def make_factory(fake_socket: FakeWebSocket):
    def factory(uri: str, **kwargs: Any) -> FakeWebSocket:
        fake_socket.uri = uri
        fake_socket.additional_headers = kwargs.get("additional_headers")
        return fake_socket

    return factory


def thread_payload(*, thread_id: str = "thread-1", include_turns: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": thread_id,
        "preview": "hello world",
        "ephemeral": False,
        "modelProvider": "openai",
        "createdAt": 1710000000,
        "updatedAt": 1710000300,
        "status": {"type": "active", "activeFlags": ["waitingOnUserInput"]},
        "path": "/tmp/project/.codex/thread-1",
        "cwd": "/tmp/project",
        "cliVersion": "1.2.3",
        "source": "app-server",
        "agentNickname": None,
        "agentRole": None,
        "gitInfo": {"branch": "main"},
        "name": "Example thread",
        "turns": [],
    }
    if include_turns:
        payload["turns"] = [
            {
                "id": "turn-1",
                "status": "completed",
                "items": [
                    {
                        "type": "userMessage",
                        "id": "item-1",
                        "content": [
                            {"type": "text", "text": "hello", "text_elements": []}
                        ],
                    },
                ],
                "error": None,
            }
        ]
    return payload


def test_list_threads_request_shape_and_normalization() -> None:
    fake = FakeWebSocket(
        responses=[
            {
            "jsonrpc": "2.0",
            "id": "1",
            "result": {
                "data": [thread_payload()],
                "nextCursor": "cursor-2",
                },
            }
        ]
    )
    client = CodexAppServerClient(
        "ws://codex.example",
        "secret-token",
        connection_factory=make_factory(fake),
    )

    page = client.list_threads(
        cwd="/tmp/project",
        search_term="thread",
        limit=25,
        cursor="cursor-1",
        source_kinds=["interactive"],
        archived=False,
    )

    assert fake.uri == "ws://codex.example"
    assert fake.additional_headers == {"Authorization": "Bearer secret-token"}
    assert fake.sent_messages == [
        {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "thread/list",
            "params": {
                "cwd": "/tmp/project",
                "searchTerm": "thread",
                "limit": 25,
                "cursor": "cursor-1",
                "sourceKinds": ["interactive"],
                "archived": False,
            },
        }
    ]
    assert page.next_cursor == "cursor-2"
    assert len(page.threads) == 1
    thread = page.threads[0]
    assert thread.id == "thread-1"
    assert thread.cwd == "/tmp/project"
    assert thread.status == {"type": "active", "activeFlags": ["waitingOnUserInput"]}
    assert thread.created_at == datetime.fromtimestamp(1710000000, tz=timezone.utc)
    assert thread.turns[0].id == "turn-1"


def test_read_thread_includes_turn_history() -> None:
    fake = FakeWebSocket(
        responses=[
            {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {"thread": thread_payload(include_turns=True)},
            }
        ]
    )
    client = CodexAppServerClient("ws://codex.example", connection_factory=make_factory(fake))

    thread = client.read_thread("thread-1")

    assert fake.sent_messages[0]["method"] == "thread/read"
    assert fake.sent_messages[0]["params"] == {
        "threadId": "thread-1",
        "includeTurns": True,
    }
    assert thread.id == "thread-1"
    assert thread.turns[0].items[0]["type"] == "userMessage"
    assert thread.turns[0].items[0]["content"][0]["type"] == "text"


@pytest.mark.parametrize(
    "method_name,call",
    [
        (
            "thread/resume",
            lambda client: client.resume_thread("thread-1", cwd="/tmp/new"),
        ),
        (
            "thread/fork",
            lambda client: client.fork_thread("thread-1", cwd="/tmp/new"),
        ),
    ],
)
def test_resume_and_fork_include_cwd_override(method_name: str, call) -> None:
    fake = FakeWebSocket(
        responses=[
            {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {
                    "thread": thread_payload(),
                    "model": "o3",
                    "modelProvider": "openai",
                    "serviceTier": None,
                    "cwd": "/tmp/new",
                    "approvalPolicy": "auto",
                    "approvalsReviewer": {"mode": "default"},
                    "sandbox": {"mode": "workspace-write"},
                    "reasoningEffort": "medium",
                },
            }
        ]
    )
    client = CodexAppServerClient("ws://codex.example", connection_factory=make_factory(fake))

    result = call(client)

    assert fake.sent_messages[0]["method"] == method_name
    assert fake.sent_messages[0]["params"]["threadId"] == "thread-1"
    assert fake.sent_messages[0]["params"]["cwd"] == "/tmp/new"
    assert fake.sent_messages[0]["params"]["persistExtendedHistory"] is True
    assert result.thread.cwd == "/tmp/project"
    assert result.cwd == "/tmp/new"
    assert result.model == "o3"


def test_start_turn_uses_turn_start_method() -> None:
    fake = FakeWebSocket(
        responses=[
            {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {
                        "turn": {
                            "id": "turn-2",
                        "status": "inProgress",
                        "items": [],
                        "error": None,
                    }
                },
            }
        ]
    )
    client = CodexAppServerClient("ws://codex.example", connection_factory=make_factory(fake))

    turn = client.start_turn(
        "thread-1",
        input=[UserInputText(type="text", text="hello", text_elements=[])],
        cwd="/tmp/project",
        approval_policy="never",
        model="o3",
        service_tier="default",
        effort="high",
        summary="short",
        personality="concise",
    )

    assert fake.sent_messages[0]["method"] == "turn/start"
    assert fake.sent_messages[0]["params"]["threadId"] == "thread-1"
    assert fake.sent_messages[0]["params"]["cwd"] == "/tmp/project"
    assert fake.sent_messages[0]["params"]["input"] == [
        {"type": "text", "text": "hello", "text_elements": []}
    ]
    assert "modelProvider" not in fake.sent_messages[0]["params"]
    assert turn.id == "turn-2"
    assert turn.status == "inProgress"


def test_set_thread_name_uses_set_name_method() -> None:
    fake = FakeWebSocket(
        responses=[
            {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {},
            }
        ]
    )
    client = CodexAppServerClient("ws://codex.example", connection_factory=make_factory(fake))

    client.set_thread_name("thread-1", "Renamed thread")

    assert fake.sent_messages[0]["method"] == "thread/setName"
    assert fake.sent_messages[0]["params"] == {
        "threadId": "thread-1",
        "name": "Renamed thread",
    }
