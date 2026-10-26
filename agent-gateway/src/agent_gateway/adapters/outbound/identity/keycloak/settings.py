"""The Keycloak adapter's own settings. Nothing else reads ``OIDC_*``, ``DIRECTORY_CLIENT_*`` or ``PORTAL_CLIENT_*``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agent_gateway.domain.errors import ConfigError


@dataclass(frozen=True)
class KeycloakSettings:
    issuer: str
    audience: str
    jwks_ttl: int
    directory_client_id: str
    directory_client_secret: str = field(repr=False)
    portal_client_id: str
    portal_client_secret: str = field(repr=False)

    @property
    def base_url(self) -> str:
        return self.issuer.rsplit("/realms/", 1)[0]

    @property
    def realm(self) -> str:
        return self.issuer.rsplit("/realms/", 1)[1]

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/certs"

    @property
    def token_url(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/token"

    @property
    def authorize_url(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/auth"

    @property
    def admin_url(self) -> str:
        return f"{self.base_url}/admin/realms/{self.realm}"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> KeycloakSettings:
        def get(name: str, default: str = "") -> str:
            return (env.get(name) or default).strip()

        missing = [n for n in ("OIDC_ISSUER", "DIRECTORY_CLIENT_SECRET") if not get(n)]
        audience = get("OIDC_AUDIENCE", "agent-gateway")
        if missing:
            raise ConfigError("missing environment: " + ", ".join(missing))
        issuer = get("OIDC_ISSUER").rstrip("/")
        if not issuer.startswith(("http://", "https://")) or "/realms/" not in issuer \
                or not issuer.rsplit("/realms/", 1)[1] or "/" in issuer.rsplit("/realms/", 1)[1]:
            raise ConfigError("OIDC_ISSUER must look like https://host/realms/<realm>")
        ttl_raw = get("OIDC_JWKS_TTL", "600")
        if not ttl_raw.isdigit() or int(ttl_raw) < 1:
            raise ConfigError("OIDC_JWKS_TTL must be a whole number of seconds, at least 1")
        portal_secret = get("PORTAL_CLIENT_SECRET")
        if get("PORTAL_BASE_URL") and not portal_secret:
            raise ConfigError("PORTAL_CLIENT_SECRET is required when PORTAL_BASE_URL is set")
        return cls(issuer, audience, int(ttl_raw), get("DIRECTORY_CLIENT_ID", "agent-gateway-directory"),
                   get("DIRECTORY_CLIENT_SECRET"), get("PORTAL_CLIENT_ID", "agent-gateway-portal"), portal_secret)
