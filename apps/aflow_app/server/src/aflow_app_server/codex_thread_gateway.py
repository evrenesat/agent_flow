"""Thread-centric Codex gateway interface and normalized response models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import CodexThread, CodexThreadMutationResult, CodexTurn


class CodexThreadGatewayError(Exception):
    """Error raised by Codex thread gateway implementations."""


@dataclass(frozen=True)
class CodexThreadPage:
    """A page of Codex threads."""

    threads: list[CodexThread]
    next_cursor: str | None


class UserInputText(BaseModel):
    """A text input item accepted by the official Codex turn protocol."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str
    text_elements: list[Any]


class UserInputImage(BaseModel):
    """An image input item accepted by the official Codex turn protocol."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["image"]
    url: str


class UserInputLocalImage(BaseModel):
    """A local image input item accepted by the official Codex turn protocol."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["localImage"]
    path: str


class UserInputSkill(BaseModel):
    """A skill reference input item accepted by the official Codex turn protocol."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["skill"]
    name: str
    path: str


class UserInputMention(BaseModel):
    """A mention input item accepted by the official Codex turn protocol."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["mention"]
    name: str
    path: str


UserInput = Annotated[
    UserInputText | UserInputImage | UserInputLocalImage | UserInputSkill | UserInputMention,
    Field(discriminator="type"),
]


class CodexThreadGateway(ABC):
    """Abstract thread gateway used by the remote app server."""

    @abstractmethod
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
        """List threads visible to the official Codex app-server protocol."""

    @abstractmethod
    def read_thread(self, thread_id: str, *, include_turns: bool = True) -> CodexThread:
        """Read a thread and optionally include turn history."""

    @abstractmethod
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
        """Start a new thread."""

    @abstractmethod
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
        """Resume an existing thread."""

    @abstractmethod
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
        """Fork an existing thread."""

    @abstractmethod
    def set_thread_name(self, thread_id: str, name: str) -> None:
        """Set a thread's user-facing name."""

    @abstractmethod
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
        """Send a user turn into an existing thread."""
