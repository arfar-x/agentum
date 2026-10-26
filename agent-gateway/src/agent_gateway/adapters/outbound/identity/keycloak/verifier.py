"""Token verification against Keycloak's published signing keys (FR-ATH-02, FR-ATH-03)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt

from agent_gateway.application.identity_ports import Identity
from agent_gateway.domain.identity import AuthError, clean_groups, clean_roles

log = logging.getLogger("agent_gateway.identity")

ALLOWED_ALGORITHMS = ("RS256", "ES256")
LEEWAY_SECONDS = 60
MAX_TOKEN_BYTES = 8 * 1024
REFETCH_FLOOR_SECONDS = 30
SERVICE_ACCOUNT_PREFIX = "service-account-"


class JwksCache:
    """The issuer's signing keys: cached for a TTL, refetched at most once per floor on an unknown
    key id, and kept in use when a refetch fails."""

    def __init__(self, url: str, http: httpx.AsyncClient, ttl: int,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._url, self._http, self._ttl, self._clock = url, http, ttl, clock
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._failed_at: float | None = None
        self._unknown_at: float | None = None
        self._lock = asyncio.Lock()

    async def key(self, kid: str) -> Any | None:
        async with self._lock:
            now = self._clock()
            expired = self._fetched_at is None or now - self._fetched_at >= self._ttl
            unknown = kid not in self._keys
            recently_failed = self._failed_at is not None and now - self._failed_at < REFETCH_FLOOR_SECONDS
            if (expired or unknown) and not recently_failed:
                if expired or self._unknown_at is None or now - self._unknown_at >= REFETCH_FLOOR_SECONDS:
                    if not expired:
                        self._unknown_at = now
                    await self._fetch(now)
            return self._keys.get(kid)

    async def _fetch(self, now: float) -> None:
        try:
            response = await self._http.get(self._url)
            response.raise_for_status()
            keys: dict[str, Any] = {}
            for jwk in response.json().get("keys", []):
                if jwk.get("use", "sig") != "sig" or not jwk.get("kid"):
                    continue
                try:
                    keys[jwk["kid"]] = jwt.PyJWK(jwk).key
                except jwt.PyJWTError:
                    continue
            self._keys, self._fetched_at, self._failed_at = keys, now, None
        except (httpx.HTTPError, ValueError, AttributeError):
            self._failed_at = now
            log.warning("could not fetch the issuer's signing keys; using the cached ones")


class TokenDecoder:
    """Signature, issuer, audience, expiry and algorithm checks; returns the claims."""

    def __init__(self, issuer: str, jwks: JwksCache) -> None:
        self._issuer, self._jwks = issuer, jwks

    async def decode(self, token: str, audience: str) -> dict[str, Any]:
        if len(token.encode()) > MAX_TOKEN_BYTES:
            raise AuthError("malformed")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise AuthError("malformed") from None
        alg = header.get("alg")
        if alg not in ALLOWED_ALGORITHMS:
            raise AuthError("bad_algorithm")
        kid = header.get("kid")
        key = await self._jwks.key(kid) if isinstance(kid, str) else None
        if key is None:
            raise AuthError("bad_signature")
        try:
            return jwt.decode(token, key, algorithms=[alg], audience=audience, issuer=self._issuer,
                              leeway=LEEWAY_SECONDS, options={"require": ["exp", "iss", "aud", "sub"]})
        except jwt.ExpiredSignatureError:
            raise AuthError("expired") from None
        except jwt.InvalidAudienceError:
            raise AuthError("bad_audience") from None
        except jwt.InvalidIssuerError:
            raise AuthError("bad_issuer") from None
        except jwt.InvalidSignatureError:
            raise AuthError("bad_signature") from None
        except jwt.PyJWTError:
            raise AuthError("malformed") from None


def identity_from_claims(claims: dict[str, Any], audience: str) -> Identity:
    """Groups from the ``groups`` claim; roles only from ``resource_access.<audience>.roles``."""
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("malformed")
    username = claims.get("preferred_username")
    username = username if isinstance(username, str) and username else sub
    raw_groups = claims.get("groups")
    raw_groups = raw_groups if isinstance(raw_groups, list) else []
    access = claims.get("resource_access")
    client = access.get(audience) if isinstance(access, dict) else None
    raw_roles = client.get("roles") if isinstance(client, dict) else None
    raw_roles = raw_roles if isinstance(raw_roles, list) else []
    groups, roles = clean_groups(raw_groups), clean_roles(raw_roles)
    if len(groups) != len(set(map(str, raw_groups))) or len(roles) != len(set(map(str, raw_roles))):
        log.warning("dropped invalid group or role names from a token")
    kind = "service" if username.startswith(SERVICE_ACCOUNT_PREFIX) else "user"
    azp = claims.get("azp")
    return Identity(sub, username, groups, roles, kind, azp if isinstance(azp, str) else "")


class KeycloakTokenVerifier:
    """Implements ``TokenVerifier``."""

    def __init__(self, decoder: TokenDecoder, audience: str) -> None:
        self._decoder, self._audience = decoder, audience

    async def verify(self, token: str) -> Identity:
        return identity_from_claims(await self._decoder.decode(token, self._audience), self._audience)
