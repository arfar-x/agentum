"""Bearer-key authentication and per-key rate limiting."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from agent_gateway.adapters.outbound.crypto import hash_key
from agent_gateway.domain.errors import AuthenticationFailed, RateLimited


@dataclass(frozen=True)
class Caller:
    user_id: str
    key_id: str


async def authenticate(request: Request) -> Caller:
    container = request.app.state.container
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationFailed("You didn't provide an API key. Send it as 'Authorization: Bearer <key>'.")
    found = await container.keys.find_user(hash_key(token.strip()))
    if found is None:
        raise AuthenticationFailed("Incorrect API key provided.")
    key_id, user_id = found
    if not await container.limiter.allow(key_id, container.config.limits.requests_per_minute):
        raise RateLimited("Rate limit reached for this key. Slow down and retry shortly.")
    return Caller(user_id, key_id)
