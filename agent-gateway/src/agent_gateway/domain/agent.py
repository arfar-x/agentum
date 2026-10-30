"""An agent, as declared in ``config/agents/<id>.yaml``."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_gateway.domain.identity import valid_name

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


class AgentAccess(BaseModel):
    """Who may use an agent and who may use its approval-gated (write) tools.

    A caller matches a rule when it has any listed group or any listed role. A list that is not
    given plays no part: with neither ``groups`` nor ``roles`` everyone may use the agent, and with
    neither write list anyone who may use the agent may use its write tools.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    groups: tuple[str, ...] | None = None
    roles: tuple[str, ...] | None = None
    write_groups: tuple[str, ...] | None = None
    write_roles: tuple[str, ...] | None = None

    @field_validator("groups", "roles", "write_groups", "write_roles", mode="before")
    @classmethod
    def _list_to_tuple(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("groups", "roles", "write_groups", "write_roles")
    @classmethod
    def _names(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("a list must not be empty; leave it out instead")
        bad = [n for n in value if not valid_name(n)]
        if bad:
            raise ValueError(f"invalid name {bad[0]!r}: 1 to 128 characters, no comma, no control character")
        if len(set(value)) != len(value):
            raise ValueError("names must be unique")
        return value

    @model_validator(mode="after")
    def _not_empty(self) -> AgentAccess:
        if self.groups is None and self.roles is None and self.write_groups is None and self.write_roles is None:
            raise ValueError("give groups, roles, write_groups, write_roles or remove the block")
        return self

    @property
    def restricts_use(self) -> bool:
        return self.groups is not None or self.roles is not None

    @property
    def restricts_write(self) -> bool:
        return self.write_groups is not None or self.write_roles is not None


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
    context_window: int | None = Field(default=None, gt=0)
    access: AgentAccess | None = None

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
