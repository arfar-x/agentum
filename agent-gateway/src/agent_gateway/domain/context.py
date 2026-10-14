"""The context window a model entry reports, so clients can size their own budgets."""

from __future__ import annotations

from collections.abc import Mapping

from agent_gateway.domain.agent import AgentDefinition


def context_window_for(
    model_id: str, *, agent: AgentDefinition | None = None, per_model: Mapping[str, int] | None = None,
    default: int | None = None,
) -> int | None:
    """An agent's own setting, else the table entry for its upstream model, else the default."""
    if agent is not None and agent.context_window:
        return agent.context_window
    lookup = agent.model if agent is not None else model_id
    return (per_model or {}).get(lookup, default)


def context_fields(tokens: int | None) -> dict[str, int]:
    """The same number under the names different clients read (vLLM, OpenRouter, generic)."""
    if not tokens:
        return {}
    return {"context_window": tokens, "context_length": tokens, "max_model_len": tokens}
