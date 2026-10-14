"""Merging the agent's default parameters with what the client sent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_gateway.domain.errors import UnsupportedParameter

#: Request fields that tune generation and are forwarded to the upstream.
TUNABLE = (
    "temperature", "top_p", "max_tokens", "max_completion_tokens", "stop", "seed",
    "reasoning_effort", "response_format", "presence_penalty", "frequency_penalty",
    "logit_bias", "verbosity", "user",
)


def check_supported(request: Mapping[str, Any]) -> None:
    """Refuse what an agent turn cannot honour, naming the parameter."""
    n = request.get("n")
    if isinstance(n, int) and n > 1:
        raise UnsupportedParameter("agents return a single completion; 'n' must be 1", param="n")
    if request.get("logprobs"):
        raise UnsupportedParameter("'logprobs' is not supported for agents", param="logprobs")
    if request.get("top_logprobs"):
        raise UnsupportedParameter("'top_logprobs' is not supported for agents", param="top_logprobs")


def merge_parameters(
    defaults: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    allow_overrides: bool,
    locked: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(defaults)
    if not allow_overrides:
        return merged
    for name in TUNABLE:
        if name in request and request[name] is not None and name not in locked:
            merged[name] = request[name]
    return merged
