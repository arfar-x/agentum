"""Loading and validating ``config/``: agents, tool servers and non-secret settings."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_gateway.application.models import CredentialDecl, ProbeTool, ServerInfo, Settings
from agent_gateway.domain.agent import AgentDefinition, validate_agent_set
from agent_gateway.domain.errors import ConfigError

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CredentialFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    label: str = ""
    secret: bool = True
    required: bool = False


class ProbeFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1)
    args: dict[str, Any] = {}


class ServerFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    timeout: float = 60.0
    credentials: list[str | CredentialFile] = []
    header_prefix: str = ""
    client_terms: list[str] = []
    forward_identity: bool = False
    test: ProbeFile | None = None


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
    client_prompt_limit_chars: int = 6000
    client_prompt_target_chars: int = 2500
    client_prompt_cache_seconds: int = 21600
    client_prompt_cache_entries: int = 1
    requests_per_minute: int = 120
    max_body_bytes: int = 4_000_000
    upstream_retries: int = 3


class ModelsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_windows: dict[str, int] = {}   # upstream model id -> tokens


class GatewayFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: ModelsFile = ModelsFile()
    limits: LimitsFile = LimitsFile()
    aliases: dict[str, str] = {}
    retrieval: RetrievalFile = RetrievalFile()
    ask_tools: dict[str, str] = {}   # client question tool name -> argument style


@dataclass(frozen=True)
class GatewayConfig:
    agents: dict[str, AgentDefinition]
    servers: dict[str, ServerInfo]
    server_urls: dict[str, tuple[str, float]]
    settings: Settings
    limits: LimitsFile
    aliases: dict[str, str]
    retrieval: RetrievalFile
    context_windows: dict[str, int] = field(default_factory=dict)
    ask_tools: dict[str, str] = field(default_factory=dict)
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
        decls = tuple(CredentialDecl(c, c) if isinstance(c, str)
                      else CredentialDecl(c.name, c.label or c.name, c.secret, c.required)
                      for c in parsed.credentials)
        names = [d.name for d in decls]
        if len(set(names)) != len(names):
            raise ConfigError(f"{servers_path}: servers.{name}.credentials: a variable is declared twice")
        probe = ProbeTool(parsed.test.tool, dict(parsed.test.args)) if parsed.test else None
        servers[name] = ServerInfo(name, tuple(names), parsed.header_prefix, tuple(parsed.client_terms),
                                   declarations=decls, test=probe, forward_identity=parsed.forward_identity)
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
    from agent_gateway.domain.interaction import STYLES

    unknown = sorted(set(gateway.ask_tools.values()) - set(STYLES))
    if unknown:
        raise ConfigError(f"{gateway_path}: ask_tools: unknown style(s) {', '.join(unknown)} (use one of {', '.join(STYLES)})")
    lim = gateway.limits
    settings = Settings(max_steps=lim.max_steps, max_corrections=lim.max_corrections,
                        approval_ttl_seconds=lim.approval_ttl_seconds, pending_ttl_seconds=lim.pending_ttl_seconds,
                        thread_retention_days=lim.thread_retention_days, tool_result_chars=lim.tool_result_chars,
                        client_prompt_limit_chars=lim.client_prompt_limit_chars,
                        client_prompt_target_chars=lim.client_prompt_target_chars,
                        client_prompt_cache_seconds=lim.client_prompt_cache_seconds,
                        client_prompt_cache_entries=lim.client_prompt_cache_entries)
    return GatewayConfig(agents, servers, urls, settings, lim, gateway.aliases, gateway.retrieval,
                         context_windows=gateway.models.context_windows, ask_tools=gateway.ask_tools)
