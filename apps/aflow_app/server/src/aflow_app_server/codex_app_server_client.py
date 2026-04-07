"""Websocket JSON-RPC client for the official Codex app-server protocol."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from websockets.sync.client import connect

from .codex_thread_gateway import (
    CodexThreadGateway,
    CodexThreadGatewayError,
    CodexThreadPage,
    UserInput,
)
from .models import CodexThread, CodexThreadMutationResult, CodexTurn


JsonDict = dict[str, Any]
ConnectionFactory = Callable[..., AbstractContextManager[Any]]


@dataclass(frozen=True)
class CodexAppServerConnectionConfig:
    """Connection settings for the official Codex app-server."""

    url: str
    auth_token: str | None = None


class CodexAppServerClient(CodexThreadGateway):
    """Thread gateway backed by the official Codex app-server websocket API."""

    def __init__(
        self,
        server_url: str,
        auth_token: str | None = None,
        *,
        connection_factory: ConnectionFactory = connect,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.auth_token = auth_token
        self._connection_factory = connection_factory
        self._request_counter = 0

    def list_threads(
        self,
        *,
        cwd: str | None = None,
        search_term: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        source_kinds: list[str] | None = None,
        archived: bool | None = None,
    ) -> CodexThreadPage:
        result = self._request(
            "thread/list",
            self._compact_params(
                cwd=cwd,
                searchTerm=search_term,
                limit=limit,
                cursor=cursor,
                sourceKinds=source_kinds,
                archived=archived,
            ),
        )
        return CodexThreadPage(
            threads=[self._normalize_thread(item) for item in result.get("data", [])],
            next_cursor=result.get("nextCursor"),
        )

    def read_thread(self, thread_id: str, *, include_turns: bool = True) -> CodexThread:
        result = self._request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
        )
        return self._normalize_thread(result["thread"])

    def start_thread(
        self,
        *,
        cwd: str | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        service_tier: str | None = None,
        approval_policy: str | None = None,
        experimental_raw_events: bool = False,
        persist_extended_history: bool = True,
    ) -> CodexThreadMutationResult:
        result = self._request(
            "thread/start",
            self._compact_params(
                cwd=cwd,
                model=model,
                modelProvider=model_provider,
                serviceTier=service_tier,
                approvalPolicy=approval_policy,
                experimentalRawEvents=experimental_raw_events,
                persistExtendedHistory=persist_extended_history,
            ),
        )
        return self._normalize_thread_mutation_result(result)

    def resume_thread(
        self,
        thread_id: str,
        *,
        cwd: str | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        service_tier: str | None = None,
        approval_policy: str | None = None,
        persist_extended_history: bool = True,
    ) -> CodexThreadMutationResult:
        result = self._request(
            "thread/resume",
            self._compact_params(
                threadId=thread_id,
                cwd=cwd,
                model=model,
                modelProvider=model_provider,
                serviceTier=service_tier,
                approvalPolicy=approval_policy,
                persistExtendedHistory=persist_extended_history,
            ),
        )
        return self._normalize_thread_mutation_result(result)

    def fork_thread(
        self,
        thread_id: str,
        *,
        cwd: str | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        service_tier: str | None = None,
        approval_policy: str | None = None,
        persist_extended_history: bool = True,
    ) -> CodexThreadMutationResult:
        result = self._request(
            "thread/fork",
            self._compact_params(
                threadId=thread_id,
                cwd=cwd,
                model=model,
                modelProvider=model_provider,
                serviceTier=service_tier,
                approvalPolicy=approval_policy,
                persistExtendedHistory=persist_extended_history,
            ),
        )
        return self._normalize_thread_mutation_result(result)

    def set_thread_name(self, thread_id: str, name: str) -> None:
        self._request("thread/setName", {"threadId": thread_id, "name": name})

    def start_turn(
        self,
        thread_id: str,
        input: list[UserInput],
        *,
        cwd: str | None = None,
        approval_policy: str | None = None,
        model: str | None = None,
        service_tier: str | None = None,
        effort: str | None = None,
        summary: str | None = None,
        personality: str | None = None,
    ) -> CodexTurn:
        result = self._request(
            "turn/start",
            self._compact_params(
                threadId=thread_id,
                input=[item.model_dump(mode="json") for item in input],
                cwd=cwd,
                approvalPolicy=approval_policy,
                model=model,
                serviceTier=service_tier,
                effort=effort,
                summary=summary,
                personality=personality,
            ),
        )
        return self._normalize_turn(result["turn"])

    def _open_connection(self) -> AbstractContextManager[Any]:
        headers: dict[str, str] | None = None
        if self.auth_token:
            headers = {"Authorization": f"Bearer {self.auth_token}"}
        return self._connection_factory(self.server_url, additional_headers=headers)

    def _request(self, method: str, params: dict[str, Any] | None) -> JsonDict:
        self._request_counter += 1
        request_id = str(self._request_counter)
        request: JsonDict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params:
            request["params"] = params

        try:
            with self._open_connection() as websocket:
                websocket.send(json.dumps(request))
                while True:
                    payload = websocket.recv()
                    message = self._parse_payload(payload)
                    if message.get("id") != request_id:
                        continue
                    if "error" in message:
                        error = message["error"]
                        raise CodexThreadGatewayError(
                            f"{method} failed: {error.get('message', 'unknown error')}"
                        )
                    result = message.get("result")
                    if not isinstance(result, dict):
                        raise CodexThreadGatewayError(f"{method} returned a malformed response")
                    return result
        except CodexThreadGatewayError:
            raise
        except Exception as exc:  # pragma: no cover - defensive transport normalization
            raise CodexThreadGatewayError(f"{method} failed: {exc}") from exc

    def _parse_payload(self, payload: Any) -> JsonDict:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if not isinstance(payload, str):
            raise CodexThreadGatewayError("Codex app-server returned a non-text websocket frame")
        parsed = json.loads(payload)
        if not isinstance(parsed, dict):
            raise CodexThreadGatewayError("Codex app-server returned a non-object JSON-RPC frame")
        return parsed

    def _normalize_thread_mutation_result(self, result: JsonDict) -> CodexThreadMutationResult:
        thread = self._normalize_thread(result["thread"])
        return CodexThreadMutationResult(
            thread=thread,
            model=result.get("model"),
            model_provider=result.get("modelProvider"),
            service_tier=result.get("serviceTier"),
            cwd=str(result.get("cwd", thread.cwd)),
            approval_policy=result.get("approvalPolicy"),
            approvals_reviewer=self._coerce_dict(result.get("approvalsReviewer")),
            sandbox=self._coerce_dict(result.get("sandbox")),
            reasoning_effort=result.get("reasoningEffort"),
        )

    def _normalize_thread(self, data: JsonDict) -> CodexThread:
        turns = [self._normalize_turn(item) for item in data.get("turns", [])]
        return CodexThread(
            id=str(data["id"]),
            preview=str(data.get("preview", "")),
            ephemeral=bool(data.get("ephemeral", False)),
            model_provider=str(data.get("modelProvider", "")),
            created_at=self._parse_timestamp(data.get("createdAt")),
            updated_at=self._parse_timestamp(data.get("updatedAt")),
            status=data.get("status"),
            path=Path(data["path"]) if data.get("path") else None,
            cwd=str(data.get("cwd", "")),
            cli_version=str(data.get("cliVersion", "")),
            source=str(data.get("source", "")),
            agent_nickname=data.get("agentNickname"),
            agent_role=data.get("agentRole"),
            git_info=self._coerce_dict(data.get("gitInfo")),
            name=data.get("name"),
            turns=turns,
        )

    def _normalize_turn(self, data: JsonDict) -> CodexTurn:
        return CodexTurn(
            id=str(data.get("id", "")),
            status=str(data.get("status", "")),
            items=[self._coerce_dict(item) for item in data.get("items", [])],
            error=self._coerce_dict(data.get("error")),
        )

    def _parse_timestamp(self, value: Any) -> datetime:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    def _coerce_dict(self, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, dict):
            return value
        return {"value": value}

    def _compact_params(self, **kwargs: Any) -> dict[str, Any]:
        return {key: value for key, value in kwargs.items() if value is not None}
