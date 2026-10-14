"""An agent, as declared in ``config/agents/<id>.yaml``."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AGENT_ID = r"^[a-z0-9][a-z0-9-]*$"


class ToolBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    server: str
    include: tuple[str, ...] = ("*",)
    approval: tuple[str, ...] = ()

    def includes(self, tool: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, g) for g in self.include)

    def needs_approval(self, tool: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, g) for g in self.approval)


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    to: str
    description: str


class KbRetrieval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    limit: int = Field(default=5, ge=1, le=20)
    types: tuple[str, ...] = ()
    query_rewrite: bool = False


class Preretrieval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kb: KbRetrieval = KbRetrieval()


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    id: str = Field(pattern=AGENT_ID)
    name: str
    description: str = ""
    model: str
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    allow_client_overrides: bool = True
    locked_parameters: tuple[str, ...] = ()
    allow_model_override: bool = True
    client_system: Literal["append", "prepend", "ignore"] = "append"
    client_tools: Literal["all", "none"] | tuple[str, ...] = "all"
    instructions: str
    tools: tuple[ToolBinding, ...] = ()
    interaction: tuple[Literal["ask_user"], ...] = ()
    preretrieval: Preretrieval = Preretrieval()
    handoffs: tuple[Handoff, ...] = ()
    activity: Literal["narrate", "quiet"] = "narrate"

    @field_validator("client_tools", mode="before")
    @classmethod
    def _list_to_tuple(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    def binding_for(self, server: str, tool: str) -> ToolBinding | None:
        for binding in self.tools:
            if binding.server == server and binding.includes(tool):
                return binding
        return None

    def allows_client_tool(self, name: str) -> bool:
        if self.client_tools == "all":
            return True
        if self.client_tools == "none":
            return False
        return name in self.client_tools


def validate_agent_set(agents: Mapping[str, AgentDefinition], servers: set[str]) -> list[str]:
    """Cross-agent problems: unknown handoff targets, unknown tool servers."""
    problems: list[str] = []
    for agent in agents.values():
        for handoff in agent.handoffs:
            if handoff.to not in agents:
                problems.append(f"agent {agent.id}: handoff target {handoff.to!r} is not a defined agent")
            if handoff.to == agent.id:
                problems.append(f"agent {agent.id}: hands off to itself")
        for binding in agent.tools:
            if binding.server not in servers:
                problems.append(f"agent {agent.id}: tool server {binding.server!r} is not in mcp-servers.yaml")
    return problems
