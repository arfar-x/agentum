"""Loading and validating ``config/``: agents, tool servers and non-secret settings."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from agent_gateway.application.models import ServerInfo, Settings
from agent_gateway.domain.agent import AgentDefinition, validate_agent_set
from agent_gateway.domain.errors import ConfigError

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ServerFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    timeout: float = 60.0
    credentials: list[str] = []
    header_prefix: str = ""


class RetrievalFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: str = "kb"
    tool: str = "kb_search"


class LimitsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_steps: int = 25
    max_corrections: int = 4
    approval_ttl_seconds: int = 900
    pending_ttl_seconds: int = 86400
    thread_retention_days: int = 30
    tool_result_chars: int = 20000
    requests_per_minute: int = 120
    max_body_bytes: int = 4_000_000
    upstream_retries: int = 3


class GatewayFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limits: LimitsFile = LimitsFile()
    aliases: dict[str, str] = {}
    retrieval: RetrievalFile = RetrievalFile()


@dataclass(frozen=True)
class GatewayConfig:
    agents: dict[str, AgentDefinition]
    servers: dict[str, ServerInfo]
    server_urls: dict[str, tuple[str, float]]
    settings: Settings
    limits: LimitsFile
    aliases: dict[str, str]
    retrieval: RetrievalFile
    problems: list[str] = field(default_factory=list)


def _interpolate(value: Any, env: Mapping[str, str], where: str) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match[str]) -> str:
            if m.group(1) not in env:
                raise ConfigError(f"{where}: ${{{m.group(1)}}} is not set in the environment")
            return env[m.group(1)]

        return _ENV.sub(sub, value)
    if isinstance(value, list):
        return [_interpolate(v, env, where) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v, env, where) for k, v in value.items()}
    return value


def _read(path: Path, env: Mapping[str, str]) -> Any:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML ({exc})") from exc
    return _interpolate(data or {}, env, str(path))


def _validate(model: type[BaseModel], data: Any, path: Path) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"{path}: {details}") from exc


def load_config(config_dir: Path, env: Mapping[str, str] | None = None) -> GatewayConfig:
    env = os.environ if env is None else env
    if not config_dir.is_dir():
        raise ConfigError(f"{config_dir}: no such directory")

    gateway_path = config_dir / "gateway.yaml"
    gateway = _validate(GatewayFile, _read(gateway_path, env) if gateway_path.exists() else {}, gateway_path)

    servers: dict[str, ServerInfo] = {}
    urls: dict[str, tuple[str, float]] = {}
    servers_path = config_dir / "mcp-servers.yaml"
    raw_servers = (_read(servers_path, env) if servers_path.exists() else {}).get("servers", {})
    for name, body in raw_servers.items():
        parsed = _validate(ServerFile, body, servers_path)
        servers[name] = ServerInfo(name, tuple(parsed.credentials), parsed.header_prefix)
        urls[name] = (parsed.url, parsed.timeout)

    agents: dict[str, AgentDefinition] = {}
    for path in sorted((config_dir / "agents").glob("*.yaml")):
        agent = _validate(AgentDefinition, _read(path, env), path)
        if agent.id in agents:
            raise ConfigError(f"{path}: duplicate agent id {agent.id!r}")
        agents[agent.id] = agent
    problems = validate_agent_set(agents, set(servers))
    if problems:
        raise ConfigError("agents: " + "; ".join(problems))
    lim = gateway.limits
    settings = Settings(max_steps=lim.max_steps, max_corrections=lim.max_corrections,
                        approval_ttl_seconds=lim.approval_ttl_seconds, pending_ttl_seconds=lim.pending_ttl_seconds,
                        thread_retention_days=lim.thread_retention_days, tool_result_chars=lim.tool_result_chars)
    return GatewayConfig(agents, servers, urls, settings, lim, gateway.aliases, gateway.retrieval)
