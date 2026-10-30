"""The caller's principal, as the routes need it for access decisions."""

from __future__ import annotations

from fastapi import Request

from agent_gateway.domain.identity import Principal


def principal_of(request: Request, caller) -> Principal:
    """The principal the front door resolved for this request, or a plain person for the caller's id."""
    found = getattr(request.state, "principal", None)
    return found or Principal(user_id=caller.user_id, username=caller.user_id)
