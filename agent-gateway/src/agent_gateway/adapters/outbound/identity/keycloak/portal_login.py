"""Portal sign-in: authorization-code flow with PKCE, ID token verified (nonce included)."""

from __future__ import annotations

import hmac
from urllib.parse import urlencode

import httpx

from agent_gateway.adapters.outbound.identity.keycloak.settings import KeycloakSettings
from agent_gateway.adapters.outbound.identity.keycloak.verifier import (
    TokenDecoder,
    identity_from_claims,
)
from agent_gateway.application.identity_ports import Identity
from agent_gateway.domain.identity import AuthError


class KeycloakPortalLogin:
    """Implements ``PortalLogin``."""

    def __init__(self, settings: KeycloakSettings, decoder: TokenDecoder, http: httpx.AsyncClient) -> None:
        self._s, self._decoder, self._http = settings, decoder, http

    def authorize_url(self, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str:
        query = urlencode({
            "response_type": "code", "client_id": self._s.portal_client_id, "redirect_uri": redirect_uri,
            "scope": "openid profile", "state": state, "nonce": nonce,
            "code_challenge": code_challenge, "code_challenge_method": "S256"})
        return f"{self._s.authorize_url}?{query}"

    async def complete(self, code: str, code_verifier: str, redirect_uri: str, nonce: str) -> Identity:
        try:
            response = await self._http.post(self._s.token_url, data={
                "grant_type": "authorization_code", "code": code, "code_verifier": code_verifier,
                "redirect_uri": redirect_uri, "client_id": self._s.portal_client_id,
                "client_secret": self._s.portal_client_secret})
            body = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("id_token"), str):
            raise AuthError("malformed")
        claims = await self._decoder.decode(body["id_token"], self._s.portal_client_id)
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
            raise AuthError("malformed")
        identity = identity_from_claims(claims, self._s.audience)
        # Groups and roles ride on the access token; it is trusted only if it verifies for the gateway.
        access = body.get("access_token")
        if isinstance(access, str):
            try:
                full = identity_from_claims(await self._decoder.decode(access, self._s.audience), self._s.audience)
                if full.sub == identity.sub:
                    return Identity(identity.sub, full.username or identity.username, full.groups, full.roles,
                                    full.kind, self._s.portal_client_id)
            except AuthError:
                pass
        return Identity(identity.sub, identity.username, identity.groups, identity.roles, identity.kind,
                        self._s.portal_client_id)
