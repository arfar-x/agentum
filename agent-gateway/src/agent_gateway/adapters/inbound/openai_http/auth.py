"""Authentication of a request: one Principal per request, resolved by the application's Authenticator."""

from __future__ import annotations

from fastapi import Request

from agent_gateway.domain.errors import AuthenticationFailed, IdentityUnavailable, RateLimited
from agent_gateway.domain.identity import AuthError, Principal

Caller = Principal       # routes keep asking for a "caller"; it is the principal


MODEL_KEY_SHARED_MESSAGE = ("This key is shared by several people, so it cannot identify you. "
                           "Sign in with Keycloak or use a gateway key.")


def bearer_of(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def authenticate(request: Request) -> Principal:
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    container = request.app.state.container
    try:
        principal = await container.authenticator.authenticate(bearer_of(request), path=request.url.path)
    except AuthError as exc:
        if exc.status == 503:
            raise IdentityUnavailable("The identity service is unavailable. Retry shortly.") from None
        if exc.code == "model_key_shared":        # the one refusal that says why: the person can act on it
            raise AuthenticationFailed(MODEL_KEY_SHARED_MESSAGE, code="model_key_shared") from None
        raise AuthenticationFailed("Incorrect API key provided.") from None
    if not await container.limiter.allow(principal.user_id, container.config.limits.requests_per_minute):
        raise RateLimited("Rate limit reached. Slow down and retry shortly.")
    request.state.principal = principal
    return principal
