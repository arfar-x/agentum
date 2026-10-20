"""Settings commands: what they change in a session and what they say back."""

from __future__ import annotations

from telegram_bot_gateway.application.model import Session
from telegram_bot_gateway.domain import settings


def set_param(session: Session, name: str, raw: str) -> str:
    value = settings.parse(name, raw)
    session.params[name] = value
    return f"{name} = {value}"


def unset_param(session: Session, name: str) -> str:
    if name not in settings.PARAMS and name != "reasoning_effort":
        raise ValueError(f"unknown setting '{name}'")
    session.params.pop(name, None)
    return f"{name} reset to the endpoint's default"


def set_reasoning(session: Session, raw: str) -> str:
    if raw.lower() == "default":
        session.params.pop("reasoning_effort", None)
        return "reasoning reset to the endpoint's default"
    session.params["reasoning_effort"] = settings.parse_reasoning(raw)
    return f"reasoning = {session.params['reasoning_effort']}"


def set_model(session: Session, model: str) -> str:
    session.model = model
    return f"model = {model}"


def reset_settings(session: Session) -> None:
    session.model = None
    session.params.clear()


def describe(session: Session, default_model: str) -> str:
    lines = [f"model: {session.model or default_model}" + ("" if session.model else " (default)")]
    for name in (*settings.PARAMS, "reasoning_effort"):
        if name in session.params:
            lines.append(f"{name}: {session.params[name]}")
    if len(lines) == 1:
        lines.append("no other settings; the endpoint's defaults apply")
    return "\n".join(lines)
