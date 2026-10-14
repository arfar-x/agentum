"""Assembling the system prompt for an agent turn."""

from __future__ import annotations

from agent_gateway.domain.activity import NARRATION_BLOCK
from agent_gateway.domain.agent import AgentDefinition


def compose_system_prompt(agent: AgentDefinition, client_system: str | None) -> str:
    own = agent.instructions.strip()
    if agent.activity == "narrate":
        own = f"{own}\n\n{NARRATION_BLOCK}"
    client = (client_system or "").strip()
    if not client or agent.client_system == "ignore":
        return own
    if agent.client_system == "prepend":
        return f"{client}\n\n{own}"
    return f"{own}\n\nContext from the client application:\n{client}"
