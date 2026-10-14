"""What the application core needs from the outside world."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from agent_gateway.application.events import Event
from agent_gateway.application.models import (
    Completion,
    LlmDelta,
    LlmRequest,
    PendingTurn,
    Thread,
    ToolResult,
    ToolSpec,
)


class Llm(Protocol):
    async def complete(self, request: LlmRequest) -> Completion: ...
    def stream(self, request: LlmRequest) -> AsyncIterator[LlmDelta]: ...


class ToolGateway(Protocol):
    async def list_tools(self, server: str) -> list[ToolSpec]: ...
    async def call(
        self, server: str, tool: str, arguments: dict[str, Any], credentials: dict[str, str]
    ) -> ToolResult: ...


class Retriever(Protocol):
    async def search(self, queries: list[str], limit: int, types: tuple[str, ...]) -> list[dict[str, Any]]: ...


class ConversationStore(Protocol):
    async def get_thread(self, user_id: str, history_hash: str) -> Thread | None: ...
    async def put_thread(self, thread: Thread) -> None: ...
    async def put_pending(self, pending: PendingTurn) -> None: ...
    async def take_pending(self, user_id: str, call_id: str, now: float) -> PendingTurn | None: ...
    async def take_pending_by_code(self, user_id: str, code_hash: str, now: float) -> PendingTurn | None: ...


class CredentialStore(Protocol):
    async def get(self, user_id: str, server: str) -> dict[str, str]: ...


class EventSink(Protocol):
    async def emit(self, event: Event) -> None: ...


class Clock(Protocol):
    def now(self) -> float: ...


class Lock(Protocol):
    def hold(self, key: str) -> AbstractAsyncContextManager[None]: ...
