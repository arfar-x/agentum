"""Which model name is an agent, and which is the upstream's."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from agent_gateway.domain.agent import AgentDefinition
from agent_gateway.domain.errors import ModelNotAllowed


@dataclass(frozen=True)
class Route:
    kind: Literal["agent", "passthrough"]
    agent_id: str | None = None
    upstream_model: str | None = None  # an explicit per-request override for the agent


def parse_route(model: str, agents: Mapping[str, AgentDefinition]) -> Route:
    """``<agent>`` selects the agent; ``<agent>:<model>`` overrides its upstream model.

    Upstream model names may themselves contain colons (``llama:8b``), so a prefix only counts
    when it names an agent. Anything else goes to the upstream untouched.
    """
    if model in agents:
        return Route("agent", agent_id=model)
    prefix, sep, rest = model.partition(":")
    if sep and prefix in agents and rest:
        if not agents[prefix].allow_model_override:
            raise ModelNotAllowed(f"agent {prefix!r} does not allow choosing its model", param="model")
        return Route("agent", agent_id=prefix, upstream_model=rest)
    return Route("passthrough")
