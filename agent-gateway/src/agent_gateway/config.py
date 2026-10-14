"""Environment-derived settings. Secrets come only from here, never from tracked files."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agent_gateway.domain.errors import ConfigError


@dataclass(frozen=True)
class EnvSettings:
    config_dir: Path
    upstream_base_url: str
    upstream_api_key: str
    database_url: str
    redis_url: str
    creds_key: str
    approval_secret: str
    migrations_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> EnvSettings:
        env = os.environ if env is None else env
        missing = [k for k in ("UPSTREAM_BASE_URL", "DATABASE_URL", "REDIS_URL", "CREDS_KEY", "APPROVAL_SECRET")
                   if not env.get(k)]
        if missing:
            raise ConfigError("missing environment: " + ", ".join(missing))
        here = Path(__file__).resolve()
        return cls(
            config_dir=Path(env.get("GATEWAY_CONFIG_DIR", "config")),
            upstream_base_url=env["UPSTREAM_BASE_URL"].rstrip("/"),
            upstream_api_key=env.get("UPSTREAM_API_KEY", ""),
            database_url=env["DATABASE_URL"],
            redis_url=env["REDIS_URL"],
            creds_key=env["CREDS_KEY"],
            approval_secret=env["APPROVAL_SECRET"],
            migrations_dir=Path(env.get("GATEWAY_MIGRATIONS_DIR", str(here.parents[2] / "migrations"))),
            host=env.get("GATEWAY_HOST", "0.0.0.0"),
            port=int(env.get("GATEWAY_PORT", "8080")),
        )
