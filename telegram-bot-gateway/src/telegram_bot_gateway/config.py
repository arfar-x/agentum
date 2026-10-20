"""Settings, from the environment only."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    pass


def _flag(value: str | None, default: bool) -> bool:
    return default if value in (None, "") else value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    openai_base_url: str
    default_model: str
    database_url: str
    creds_key: str
    openai_api_key: str = ""
    reactions: bool = True
    show_progress: bool = False
    request_timeout: float = 600.0
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_port: int = 8443
    migrations_dir: str = "migrations"
    health_file: str = "/tmp/healthy"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, need_bot: bool = True) -> Settings:
        env = os.environ if env is None else env
        required = ["OPENAI_BASE_URL", "DEFAULT_MODEL", "DATABASE_URL", "CREDS_KEY"] + (["BOT_TOKEN"] if need_bot else [])
        missing = [n for n in required if not env.get(n)]
        if missing:
            raise ConfigError("missing required variable(s): " + ", ".join(missing))
        webhook_url = env.get("WEBHOOK_URL", "")
        if webhook_url and not env.get("WEBHOOK_SECRET"):
            raise ConfigError("WEBHOOK_URL is set, so WEBHOOK_SECRET is required")
        return cls(
            bot_token=env.get("BOT_TOKEN", ""),
            openai_base_url=env["OPENAI_BASE_URL"],
            default_model=env["DEFAULT_MODEL"],
            database_url=env["DATABASE_URL"],
            creds_key=env["CREDS_KEY"],
            openai_api_key=env.get("OPENAI_API_KEY", ""),
            reactions=_flag(env.get("REACTIONS"), True),
            show_progress=_flag(env.get("SHOW_PROGRESS"), False),
            request_timeout=float(env.get("REQUEST_TIMEOUT") or 600),
            webhook_url=webhook_url,
            webhook_secret=env.get("WEBHOOK_SECRET", ""),
            webhook_port=int(env.get("WEBHOOK_PORT") or 8443),
            migrations_dir=env.get("MIGRATIONS_DIR", "migrations"),
            health_file=env.get("HEALTH_FILE", "/tmp/healthy"),
        )
