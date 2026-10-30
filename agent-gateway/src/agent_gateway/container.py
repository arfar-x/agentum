"""Wiring: the one place that knows every concrete class."""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from redis.asyncio import Redis

from agent_gateway.adapters.outbound.config_files import GatewayConfig, load_config
from agent_gateway.adapters.outbound.crypto import Vault
from agent_gateway.adapters.outbound.langgraph_runtime.graph import LangGraphDriver
from agent_gateway.adapters.outbound.mcp_tools import KbRetriever, McpTools
from agent_gateway.adapters.outbound.postgres_store import PostgresStore
from agent_gateway.adapters.outbound.redis_locks import RedisLock, RedisRateLimiter, RedisTextCache
from agent_gateway.adapters.outbound.upstream_llm import HttpLlm
from agent_gateway.adapters.outbound.identity import keycloak as keycloak_provider
from agent_gateway.application.authenticate import Authenticator
from agent_gateway.application.directory_sync import DirectorySync
from agent_gateway.application.key_service import KeyManager
from agent_gateway.application.ports import Lock, RateLimiter
from agent_gateway.application.turn import Deps
from agent_gateway.application.upstream_credentials import UpstreamKeys
from agent_gateway.config import EnvSettings
from agent_gateway.domain.errors import ConfigError
from agent_gateway.domain.interaction import ask_registry


class SystemClock:
    def now(self) -> float:
        import time

        return time.time()


@dataclass
class Container:
    config: GatewayConfig
    deps: Deps            # shared; the per-request sink is swapped in with ``dataclasses.replace``
    keys: Any             # KeyService
    lock: Lock
    limiter: RateLimiter
    upstream: httpx.AsyncClient
    credentials: Any      # UpstreamCredentials
    driver: Any
    closers: tuple = ()
    context_window: int | None = None   # default window reported on /v1/models
    authenticator: Any = None           # Authenticator
    directory_sync: Any = None          # DirectorySync, or None when no provider is wired
    login: Any = None                   # PortalLogin
    audit: Any = None                   # AuditLog
    sync_seconds: int = 300
    audit_retention_days: int = 365
    portal: Any = None                  # PortalDeps when PORTAL_BASE_URL is set

    async def close(self) -> None:
        for closer in self.closers:
            await closer()


class _NullSink:
    async def emit(self, event) -> None:  # replaced per request
        raise RuntimeError("no sink attached")


PROVIDERS = {"keycloak": keycloak_provider}


def _provider_module(env: EnvSettings):
    module = PROVIDERS.get(env.identity_provider)
    if module is None:
        raise ConfigError(f"IDENTITY_PROVIDER must be one of: {', '.join(sorted(PROVIDERS))}")
    return module


def validate_identity(env: EnvSettings) -> None:
    """Startup validation of the identity settings: the provider's own, then the neutral portal rule."""
    _provider_module(env).validate(env.environ)
    raw: Mapping[str, str] = env.environ
    if (raw.get("PORTAL_BASE_URL") or "").strip() and len((raw.get("PORTAL_SESSION_SECRET") or "").encode()) < 32:
        raise ConfigError("PORTAL_SESSION_SECRET must be at least 32 bytes when PORTAL_BASE_URL is set")


async def build_container(env: EnvSettings, *, portal_login: Any = None) -> Container:
    validate_identity(env)
    config = load_config(env.config_dir)
    vault = Vault(env.creds_key)
    store = await PostgresStore.open(env.database_url, vault, env.migrations_dir)
    redis = Redis.from_url(env.redis_url)
    upstream = httpx.AsyncClient(base_url=env.upstream_base_url, timeout=httpx.Timeout(600.0, connect=10.0))
    for s in config.servers.values():
        if s.forward_identity and not env.kb_trust_secret:
            logging.getLogger(__name__).warning(
                "server %r has forward_identity but KB_TRUST_SECRET is empty: no identity is forwarded", s.name)
    tools = McpTools(config.server_urls, config.servers, trust_secret=env.kb_trust_secret)
    retriever = None
    if config.retrieval.server in config.servers and any(
            a.preretrieval.kb.enabled for a in config.agents.values()):
        retriever = KbRetriever(tools, config.retrieval.server, config.retrieval.tool)
    deps = Deps(
        llm=HttpLlm(upstream, retries=config.limits.upstream_retries),
        tools=tools, store=store, creds=store, sink=_NullSink(), clock=SystemClock(),
        agents=config.agents, servers=config.servers, secret=env.approval_secret.encode(),
        settings=config.settings, retriever=retriever, aliases=config.aliases,
        cache=RedisTextCache(redis), ask_tools=ask_registry(config.ask_tools))
    keys = KeyManager(store, vault, issue_keys=env.issue_keys, now=lambda: datetime.now(timezone.utc))
    credentials = UpstreamKeys(keys, store.audit, env.portal_base_url)
    deps = dataclasses.replace(deps, upstream=credentials)
    provider = _provider_module(env).build(env.environ)
    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    sync = DirectorySync(provider.directory, store.directory, store.audit, now=now)
    authenticator = Authenticator(keys, provider.tokens, store.directory, sync, store.audit,
                                  issue_keys=env.issue_keys,
                                  max_stale=timedelta(seconds=env.directory_max_stale_seconds), now=now,
                                  model_key_login=env.model_key_login)
    portal = None
    if env.portal_base_url:
        portal_login = portal_login or provider.login
        if portal_login is None:
            raise ConfigError("PORTAL_BASE_URL is set but no sign-in provider was given to the portal")
        from agent_gateway.adapters.inbound.portal import PortalDeps

        portal = PortalDeps(
            base_url=env.portal_base_url, session_secret=env.portal_session_secret, login=portal_login,
            directory=store.directory, keys=keys, audit=store.audit, creds=store, tools=tools,
            servers=config.servers, issue_keys=env.issue_keys, model_key_login=env.model_key_login,
            agent_ids=lambda entry: sorted(config.agents))
    return Container(config, deps, keys, RedisLock(redis), RedisRateLimiter(redis), upstream,
                     credentials, LangGraphDriver(),
                     closers=(store.close, redis.aclose, upstream.aclose, provider.close),
                     context_window=env.context_window, authenticator=authenticator, directory_sync=sync,
                     login=provider.login, audit=store.audit, sync_seconds=env.directory_sync_seconds,
                     audit_retention_days=env.audit_retention_days, portal=portal)


def build_directory_source(env: EnvSettings):
    """The provider's directory source and a closer, for operator commands that sync without serving."""
    validate_identity(env)
    provider = _provider_module(env).build(env.environ)
    return provider.directory, provider.close
