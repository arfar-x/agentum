"""Wiring: the one place that knows every concrete class."""

from __future__ import annotations

from dataclasses import dataclass
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
from agent_gateway.application.ports import Lock, RateLimiter
from agent_gateway.application.turn import Deps
from agent_gateway.config import EnvSettings
from agent_gateway.domain.interaction import ask_registry


class SystemClock:
    def now(self) -> float:
        import time

        return time.time()


@dataclass
class Container:
    config: GatewayConfig
    deps: Deps            # shared; the per-request sink is swapped in with ``dataclasses.replace``
    keys: Any             # KeyStore
    lock: Lock
    limiter: RateLimiter
    upstream: httpx.AsyncClient
    upstream_api_key: str
    driver: Any
    closers: tuple = ()
    context_window: int | None = None   # default window reported on /v1/models

    async def close(self) -> None:
        for closer in self.closers:
            await closer()


class _NullSink:
    async def emit(self, event) -> None:  # replaced per request
        raise RuntimeError("no sink attached")


async def build_container(env: EnvSettings) -> Container:
    config = load_config(env.config_dir)
    vault = Vault(env.creds_key)
    store = await PostgresStore.open(env.database_url, vault, env.migrations_dir)
    redis = Redis.from_url(env.redis_url)
    upstream = httpx.AsyncClient(base_url=env.upstream_base_url, timeout=httpx.Timeout(600.0, connect=10.0))
    tools = McpTools(config.server_urls, config.servers)
    retriever = None
    if config.retrieval.server in config.servers and any(
            a.preretrieval.kb.enabled for a in config.agents.values()):
        retriever = KbRetriever(tools, config.retrieval.server, config.retrieval.tool)
    deps = Deps(
        llm=HttpLlm(upstream, api_key=env.upstream_api_key, retries=config.limits.upstream_retries),
        tools=tools, store=store, creds=store, sink=_NullSink(), clock=SystemClock(),
        agents=config.agents, servers=config.servers, secret=env.approval_secret.encode(),
        settings=config.settings, retriever=retriever, aliases=config.aliases,
        cache=RedisTextCache(redis), ask_tools=ask_registry(config.ask_tools))
    return Container(config, deps, store, RedisLock(redis), RedisRateLimiter(redis), upstream,
                     env.upstream_api_key, LangGraphDriver(),
                     closers=(store.close, redis.aclose, upstream.aclose), context_window=env.context_window)
