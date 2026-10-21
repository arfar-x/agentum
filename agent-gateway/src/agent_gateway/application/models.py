"""Values that move between the application core and its adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_gateway.domain.messages import ChatMessage, ToolCall, ToolDef, Usage


@dataclass(frozen=True)
class ServerInfo:
    """A tool server, as configured. ``credentials`` are the variables it declares."""

    name: str
    credentials: tuple[str, ...] = ()
    header_prefix: str = ""
    client_terms: tuple[str, ...] = ()   # words that mark a client tool as a stand-in for this server's tools

    @property
    def needs_credentials(self) -> bool:
        return bool(self.credentials)


@dataclass(frozen=True)
class ToolSpec:
    server: str
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class Settings:
    """Behavioural limits; defaults are documented in config/gateway.yaml."""

    max_steps: int = 25
    max_corrections: int = 4
    approval_ttl_seconds: int = 900
    pending_ttl_seconds: int = 86400
    thread_retention_days: int = 30
    tool_result_chars: int = 20000
    client_prompt_limit_chars: int = 6000
    client_prompt_target_chars: int = 2500
    client_prompt_cache_seconds: int = 21600
    client_prompt_cache_entries: int = 1


@dataclass
class TurnRequest:
    """A neutral request: nothing here knows about HTTP."""

    user_id: str
    agent_id: str
    messages: list[ChatMessage]
    client_tools: list[ToolDef] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    upstream_model: str | None = None
    stream: bool = False
    request_vars: dict[str, dict[str, str]] = field(default_factory=dict)  # server -> {VAR: value}


@dataclass
class LlmRequest:
    model: str
    messages: list[ChatMessage]
    tools: list[ToolDef]
    params: dict[str, Any]


@dataclass
class Completion:
    message: ChatMessage
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)
    reasoning: str = ""
    system_fingerprint: str | None = None


@dataclass
class LlmDelta:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # OpenAI delta.tool_calls entries
    finish_reason: str | None = None
    usage: Usage | None = None


@dataclass
class PendingTurn:
    """What a later request needs to resume a turn that paused for the user or the client."""

    user_id: str
    call_ids: list[str]
    kind: Literal["client_tool", "approval", "question"]
    agent_id: str
    hidden_messages: list[ChatMessage] = field(default_factory=list)
    queue: list[ToolCall] = field(default_factory=list)  # calls not yet handled, in order
    approval_call: ToolCall | None = None
    code_hash: str | None = None
    expires_at: float = 0.0


@dataclass
class Thread:
    user_id: str
    history_hash: str
    active_agent: str
    client_opt_in: bool = False   # the user chose to use the client's own tools for unavailable ones
    trace: list[dict[str, Any]] = field(default_factory=list)  # what the gateway ran in earlier turns (domain/trace.py)
