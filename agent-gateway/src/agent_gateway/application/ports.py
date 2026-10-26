"""What the application core needs from the outside world."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from agent_gateway.application.events import Event
from agent_gateway.domain.errors import UpstreamKeyRejected
from agent_gateway.domain.identity import Principal
from agent_gateway.domain.messages import ChatMessage
from agent_gateway.application.models import (
    UpstreamKey,
    Completion,
    LlmDelta,
    LlmRequest,
    PendingTurn,
    Thread,
    ToolResult,
    ToolSpec,
)


class Llm(Protocol):
    """``api_key`` is the key of the person the call is made for; the client holds none of its own."""

    async def complete(self, request: LlmRequest, api_key: str | None = None) -> Completion: ...
    def stream(self, request: LlmRequest, api_key: str | None = None) -> AsyncIterator[LlmDelta]: ...


class UpstreamCredentials(Protocol):
    """Which model key a person's upstream calls are made with."""

    async def for_principal(self, principal: Principal) -> str:
        """The person's registered key; else the shared ``UPSTREAM_API_KEY``; else ``UpstreamKeyRequired``."""
        ...

    def use(self, principal: Principal) -> AbstractAsyncContextManager[str]:
        """Yields the key for one upstream call and turns a provider 401/403 into ``UpstreamKeyRejected``."""
        ...

    async def resolve(self, principal: Principal) -> UpstreamKey: ...

    async def rejected(self, principal: Principal, key: UpstreamKey, status: int) -> UpstreamKeyRejected:
        """Audit a refused personal key and return the error to raise."""
        ...


class ToolGateway(Protocol):
    async def list_tools(self, server: str) -> list[ToolSpec]: ...
    async def call(
        self, server: str, tool: str, arguments: dict[str, Any], credentials: dict[str, str],
        *, identity: Principal | None = None,
    ) -> ToolResult:
        """``identity`` is used only for servers configured with ``forward_identity``."""
        ...


class Retriever(Protocol):
    async def search(self, queries: list[str], limit: int, types: tuple[str, ...], *,
                     identity: Principal | None = None) -> list[dict[str, Any]]: ...


class ConversationStore(Protocol):
    async def get_thread(self, user_id: str, history_hash: str) -> Thread | None: ...
    async def put_thread(self, thread: Thread) -> None: ...
    async def put_pending(self, pending: PendingTurn) -> None: ...
    async def take_pending(self, user_id: str, call_id: str, now: float) -> PendingTurn | None: ...
    async def take_pending_by_code(self, user_id: str, code_hash: str, now: float) -> PendingTurn | None: ...
    async def put_response(self, user_id: str, response_id: str, messages: list[ChatMessage]) -> None: ...
    async def get_response(self, user_id: str, response_id: str) -> list[ChatMessage] | None: ...


class CredentialStore(Protocol):
    async def get(self, user_id: str, server: str) -> dict[str, str]: ...
    async def list_vars(self, user: str, server: str) -> list[str]:
        """Variable names only, never values."""
        ...
    async def delete_var(self, user: str, server: str, var: str) -> bool: ...


class EventSink(Protocol):
    async def emit(self, event: Event) -> None: ...


class Clock(Protocol):
    def now(self) -> float: ...


class Lock(Protocol):
    def hold(self, key: str) -> AbstractAsyncContextManager[None]: ...


class RateLimiter(Protocol):
    async def allow(self, key_id: str, limit_per_minute: int) -> bool: ...


class TextCache(Protocol):
    async def get(self, key: str, refresh_seconds: int, group: str | None = None) -> str | None:
        """The cached text; a hit extends its life to ``refresh_seconds`` and makes it the group's newest."""
        ...

    async def put(self, key: str, value: str, ttl_seconds: int, group: str | None = None, keep: int = 1) -> None:
        """Store ``value``. Within ``group`` only the ``keep`` newest keys survive; older ones are deleted."""
        ...
