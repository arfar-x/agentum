"""Assembling the system prompt for an agent turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_gateway.domain.activity import NARRATION_BLOCK
from agent_gateway.domain.agent import AgentDefinition

CLIENT_PREFACE = (
    "The client application you are running inside sent its own instructions, below. Use them for how "
    "to work in that environment: formatting, its tools and its conventions. Where they conflict with "
    "your role or rules above, including who you are, the instructions above win."
)


def compose_system_prompt(agent: AgentDefinition, client_system: str | None) -> str:
    own = agent.instructions.strip()
    if agent.activity == "narrate":
        own = f"{own}\n\n{NARRATION_BLOCK}"
    client = (client_system or "").strip()
    if not client or agent.client_system == "ignore":
        return own
    if agent.client_system == "prepend":
        return f"{client}\n\n{own}"
    reminder = (f"Remember: you are {agent.name}. If asked who you are, say so. "
                "Follow your role and rules from the top of this prompt.")
    return f"{own}\n\n{CLIENT_PREFACE}\n\n<client_instructions>\n{client}\n</client_instructions>\n\n{reminder}"


def unavailable_note(missing: Mapping[str, Sequence[str]], opted_in: bool = False) -> str:
    """Tell the model which of its tools cannot run in this conversation, and what to do about it."""
    parts = [f"{server} ({', '.join(sorted(tools)[:6])}{', ...' if len(tools) > 6 else ''})"
             for server, tools in sorted(missing.items())]
    head = ("Some of your tools are unavailable in this conversation because no credentials were provided "
            f"for them: {'; '.join(parts)}. ")
    if opted_in:
        return head + ("The user chose to use their client's own tools instead, so use the client's tools or "
                       "connectors for that work. Do not pretend to have used your own.")
    return head + (
        "If the user asks for something that needs them: say in one or two sentences that the credentials are "
        "missing and that they can add them (as request headers in their client, or stored with the gateway "
        "operator), and ask whether they would rather use their client's own tools or connectors for it. "
        "Do not use the client's own tools or connectors for this work on your own initiative. Only if the user "
        "says yes, or insists, call use_client_tools, then use them. Do not pretend to have used your own tools."
    )
