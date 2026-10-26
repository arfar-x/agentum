"""The Keycloak identity provider: token verifier, directory source and portal login."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from agent_gateway.adapters.outbound.identity.keycloak.directory import KeycloakDirectory
from agent_gateway.adapters.outbound.identity.keycloak.portal_login import KeycloakPortalLogin
from agent_gateway.adapters.outbound.identity.keycloak.settings import KeycloakSettings
from agent_gateway.adapters.outbound.identity.keycloak.verifier import (
    JwksCache,
    KeycloakTokenVerifier,
    TokenDecoder,
)


@dataclass
class ProviderBundle:
    """What the composition root takes from any identity provider."""

    tokens: Any          # TokenVerifier
    directory: Any       # DirectorySource
    login: Any           # PortalLogin
    close: Callable[[], Any]


def validate(env: Mapping[str, str]) -> None:
    """Raise ConfigError naming the setting that is missing or inconsistent."""
    KeycloakSettings.from_env(env)


def build(env: Mapping[str, str], http: httpx.AsyncClient | None = None) -> ProviderBundle:
    settings = KeycloakSettings.from_env(env)
    client = http or httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    decoder = TokenDecoder(settings.issuer, JwksCache(settings.jwks_url, client, settings.jwks_ttl))
    return ProviderBundle(KeycloakTokenVerifier(decoder, settings.audience), KeycloakDirectory(settings, client),
                          KeycloakPortalLogin(settings, decoder, client), client.aclose)
