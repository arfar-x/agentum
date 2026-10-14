"""What a turn emits while it runs. Adapters render these onto the wire."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_gateway.domain.messages import ToolCall, Usage


@dataclass(frozen=True)
class ContentDelta:
    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    text: str


@dataclass(frozen=True)
class ToolCallsOut:
    """Calls the client must run; ends the turn with finish_reason ``tool_calls``."""

    calls: list[ToolCall]


@dataclass(frozen=True)
class Done:
    finish_reason: str
    usage: Usage = field(default_factory=Usage)


Event = ContentDelta | ReasoningDelta | ToolCallsOut | Done
