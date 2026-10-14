"""OpenAI chat wire models, as the gateway sees them.

Unknown fields are kept (``extra="allow"``) so a message a client sends comes back unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FunctionCall(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    arguments: str = ""


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: str | list[Any] | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def text(self) -> str:
        return text_of(self.content)


class ToolFunction(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolDef(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["function"] = "function"
    function: ToolFunction


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


def text_of(content: str | list[Any] | None) -> str:
    """The plain text of a message content, whether a string or a list of parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(str(part.get("text", "")))
        elif isinstance(part, str):
            parts.append(part)
    return "".join(parts)
