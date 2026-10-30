"""Environment-derived settings. Secrets come only from here, never from tracked files."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from agent_gateway.domain.errors import ConfigError
from agent_gateway.domain.identity import MODEL_KEY_LOGIN_MODES


@dataclass(frozen=True)
class EnvSettings:
    config_dir: Path
    upstream_base_url: str
    database_url: str
    redis_url: str
    creds_key: str
    approval_secret: str
    migrations_dir: Path
    context_window: int | None = None
    host: str = "0.0.0.0"
    port: int = 8080
    issue_keys: bool = False
    model_key_login: str = "read-only"
    audit_retention_days: int = 365
    portal_base_url: str = ""
    portal_session_secret: str = ""
    identity_provider: str = "keycloak"
    directory_sync_seconds: int = 300
    directory_max_stale_seconds: int = 900
    environ: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)
    kb_trust_secret: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EnvSettings:
        env = os.environ if env is None else env
        missing = [k for k in ("UPSTREAM_BASE_URL", "DATABASE_URL", "REDIS_URL", "CREDS_KEY", "APPROVAL_SECRET")
                   if not env.get(k)]
        if missing:
            raise ConfigError("missing environment: " + ", ".join(missing))
        here = Path(__file__).resolve()
        interval = _positive_int(env, "DIRECTORY_SYNC_SECONDS", 300)
        stale = _positive_int(env, "DIRECTORY_MAX_STALE_SECONDS", 900)
        if stale < 2 * interval:
            raise ConfigError("DIRECTORY_MAX_STALE_SECONDS must be at least twice DIRECTORY_SYNC_SECONDS")
        return cls(
            config_dir=Path(env.get("GATEWAY_CONFIG_DIR", "config")),
            upstream_base_url=env["UPSTREAM_BASE_URL"].rstrip("/"),
            database_url=env["DATABASE_URL"],
            redis_url=env["REDIS_URL"],
            creds_key=env["CREDS_KEY"],
            approval_secret=env["APPROVAL_SECRET"],
            migrations_dir=Path(env.get("GATEWAY_MIGRATIONS_DIR", str(here.parents[2] / "migrations"))),
            context_window=int(env["MODEL_CONTEXT_TOKENS"]) if env.get("MODEL_CONTEXT_TOKENS") else None,
            host=env.get("GATEWAY_HOST", "0.0.0.0"),
            port=int(env.get("GATEWAY_PORT", "8080")),
            issue_keys=_flag(env, "GATEWAY_ISSUE_KEYS"),
            model_key_login=_model_key_login(env),
            audit_retention_days=_positive_int(env, "AUDIT_RETENTION_DAYS", 365),
            portal_base_url=_portal_base_url(env),
            portal_session_secret=_portal_secret(env),
            identity_provider=env.get("IDENTITY_PROVIDER", "").strip() or "keycloak",
            directory_sync_seconds=interval,
            directory_max_stale_seconds=stale,
            environ=dict(env),
            kb_trust_secret=env.get("KB_TRUST_SECRET", ""),
        )


def _model_key_login(env: Mapping[str, str]) -> str:
    raw = (env.get("GATEWAY_MODEL_KEY_LOGIN") or "").strip().lower() or "read-only"
    if raw not in MODEL_KEY_LOGIN_MODES:
        raise ConfigError("GATEWAY_MODEL_KEY_LOGIN must be off, read-only or full")
    return raw


def _flag(env: Mapping[str, str], name: str) -> bool:
    raw = env.get(name, "").strip().lower()
    if raw in ("", "0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    raise ConfigError(f"{name} must be true or false")


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise ConfigError(f"{name} must be a whole number of at least 1")
    return value


def _portal_base_url(env: Mapping[str, str]) -> str:
    """The public origin of the gateway, or empty (portal off). https, or http only on localhost."""
    raw = env.get("PORTAL_BASE_URL", "").strip().rstrip("/")
    if not raw:
        return ""
    parts = urlsplit(raw)
    local = parts.scheme == "http" and parts.hostname == "localhost"
    if not (parts.scheme == "https" or local) or not parts.netloc or parts.path or parts.query or parts.fragment:
        raise ConfigError("PORTAL_BASE_URL must be an origin such as https://gateway.example.org "
                          "(http is accepted only for localhost)")
    return raw


def _portal_secret(env: Mapping[str, str]) -> str:
    secret = env.get("PORTAL_SESSION_SECRET", "")
    if env.get("PORTAL_BASE_URL", "").strip() and len(secret.encode()) < 32:
        raise ConfigError("PORTAL_SESSION_SECRET must be at least 32 bytes when PORTAL_BASE_URL is set")
    return secret
