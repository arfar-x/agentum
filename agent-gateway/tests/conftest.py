"""An in-process gateway over fakes and a stub upstream, driven through real HTTP."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from agent_gateway.adapters.inbound.openai_http.app import create_app
from agent_gateway.adapters.outbound.config_files import GatewayConfig, LimitsFile, RetrievalFile
from agent_gateway.adapters.outbound.langgraph_runtime.graph import LangGraphDriver
from agent_gateway.adapters.outbound.redis_locks import MemoryLock, MemoryRateLimiter
from agent_gateway.adapters.outbound.upstream_llm import HttpLlm
from agent_gateway.application.authenticate import Authenticator
from agent_gateway.application.directory_sync import DirectorySync
from agent_gateway.application.models import ServerInfo, Settings
from agent_gateway.application.turn import Deps
from agent_gateway.application.upstream_credentials import UpstreamKeys
from agent_gateway.container import Container
from agent_gateway.domain.identity import DirectoryEntry, KeyInfo, KeyScope, hash_key
from tests.application.fakes import (
    Clock,
    FakeAudit,
    FakeCreds,
    FakeDirectory,
    FakeDirectorySource,
    FakeNow,
    FakeStore,
    FakeTokenVerifier,
    FakeTools,
    Sink,
    agent,
)

KEY = "agk_test_key"
KEY2 = "agk_other_key"
SHARED_KEY = "sk-shared-deployment-key"


def _key(key_id: str, owner: str) -> KeyInfo:
    return KeyInfo(key_id, owner, "issued", "", "agk_", KeyScope(), datetime(2026, 1, 1, tzinfo=timezone.utc),
                   None, None, None)


class Keys:
    """A ``KeyService`` stand-in that recognises two keys and holds registered model keys."""

    def __init__(self) -> None:
        self.users = {hash_key(KEY): _key("k1", "alice"), hash_key(KEY2): _key("k2", "bob")}
        self.upstream: dict[str, str] = {}

    async def resolve(self, bearer):
        return self.users.get(hash_key(bearer))

    def register(self, owner: str, secret: str) -> None:
        """A person registers their own model key; it also works as their bearer."""
        self.upstream[owner] = secret
        self.users[hash_key(secret)] = KeyInfo(f"up-{owner}", owner, "upstream", "", secret[-4:], KeyScope(),
                                               datetime(2026, 1, 1, tzinfo=timezone.utc), None, None, None)

    async def upstream_key(self, owner):
        return self.upstream.get(owner)

    async def list_for(self, owner):
        return [i for i in self.users.values() if i.owner == owner and i.kind == "upstream"]


def streamed(status: int, payload, content_type: str = "application/json") -> httpx.Response:
    """A response whose body arrives as a stream, as a real upstream's does."""
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    async def body():
        yield data

    return httpx.Response(status, content=body(), headers={"content-type": content_type})


@dataclass
class Upstream:
    """A scriptable stub of the upstream model service."""

    replies: list = field(default_factory=list)       # chat completion JSON bodies, consumed in order
    seen: list = field(default_factory=list)          # (method, path, headers, body bytes)
    models: list = field(default_factory=lambda: [{"id": "base-model", "object": "model"}])
    down: bool = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        self.seen.append((request.method, request.url.path, dict(request.headers), request.content))
        if request.url.path.endswith("/models"):
            return streamed(200, {"object": "list", "data": self.models})
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            reply = self.replies.pop(0)
            if body.get("stream"):
                chunks = reply["chunks"] if "chunks" in reply else _as_chunks(reply)
                return httpx.Response(200, text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n",
                                      headers={"content-type": "text/event-stream"})
            return streamed(reply.get("status", 200), reply.get("json", reply))
        return streamed(200, {"echo": request.url.path})


def _as_chunks(reply):
    msg = reply["choices"][0]["message"]
    out = []
    text = msg.get("content") or ""
    for i in range(0, len(text), 3):          # sub-word pieces, as a real model streams them
        out.append({"choices": [{"delta": {"content": text[i:i + 3]}}]})
    for i, c in enumerate(msg.get("tool_calls") or []):
        out.append({"choices": [{"delta": {"tool_calls": [{"index": i, **c}]}}]})
    out.append({"choices": [{"delta": {}, "finish_reason": reply["choices"][0].get("finish_reason", "stop")}]})
    out.append({"choices": [], "usage": reply.get("usage", {})})
    return out


def completion(text: str | None = None, calls=None, usage=(10, 5)):
    msg = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                             for i, (n, a) in enumerate(calls)]
    return {"id": "up", "object": "chat.completion", "choices": [{"index": 0, "message": msg,
            "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)}}


@dataclass
class Gateway:
    client: httpx.AsyncClient
    upstream: Upstream
    container: Container
    tools: FakeTools
    directory: FakeDirectory
    audit: FakeAudit
    tokens: FakeTokenVerifier
    source: FakeDirectorySource
    now: FakeNow

    def auth(self, key=KEY, **extra):
        return {"Authorization": f"Bearer {key}", **extra}


@pytest.fixture
def make_gateway():
    clients = []

    async def make(agents=None, *, rpm=1000, max_body=4_000_000, specs=None, creds=None, servers=None,
                   shared_key=SHARED_KEY):
        upstream = Upstream()
        up_client = httpx.AsyncClient(base_url="http://upstream/v1", transport=httpx.MockTransport(upstream.handler))
        agents = {a.id: a for a in (agents or [agent("router")])}
        servers = servers or {"skills": ServerInfo("skills", credentials=("JIRA_USER",), header_prefix="X-Env-"),
                              "kb": ServerInfo("kb")}
        tools = FakeTools(specs or {})
        now = FakeNow()
        keys, audit = Keys(), FakeAudit(now)
        credentials = UpstreamKeys(keys, shared_key, audit, "https://portal.example")
        deps = Deps(llm=HttpLlm(up_client, retries=1, backoff=0), upstream=credentials, tools=tools, store=FakeStore(), creds=creds or FakeCreds(),
                    sink=Sink(), clock=Clock(), agents=agents, servers=servers, secret=b"s", settings=Settings())
        config = GatewayConfig(agents, servers, {}, Settings(), LimitsFile(requests_per_minute=rpm, max_body_bytes=max_body),
                               {}, RetrievalFile())
        directory, tokens, source = FakeDirectory(), FakeTokenVerifier(), FakeDirectorySource()
        for sub_id in ("alice", "bob"):
            directory.rows[sub_id] = DirectoryEntry(sub_id, sub_id, ("product",), ("agent-user",), True, now())
        sync = DirectorySync(source, directory, audit, now=now)
        authenticator = Authenticator(keys, tokens, directory, sync, audit, issue_keys=True,
                                      max_stale=timedelta(minutes=15), now=now)
        container = Container(config, deps, keys, MemoryLock(), MemoryRateLimiter(), up_client, credentials, LangGraphDriver(),
                              authenticator=authenticator, directory_sync=sync, audit=audit)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(container)), base_url="http://gw")
        clients.append((client, up_client))
        return Gateway(client, upstream, container, tools, directory, audit, tokens, source, now)

    yield make
