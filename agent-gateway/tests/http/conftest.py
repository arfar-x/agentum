"""An in-process gateway over fakes and a stub upstream, driven through real HTTP."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx
import pytest

from agent_gateway.adapters.inbound.openai_http.app import create_app
from agent_gateway.adapters.outbound.config_files import GatewayConfig, LimitsFile, RetrievalFile
from agent_gateway.adapters.outbound.crypto import hash_key
from agent_gateway.adapters.outbound.langgraph_runtime.graph import LangGraphDriver
from agent_gateway.adapters.outbound.redis_locks import MemoryLock, MemoryRateLimiter
from agent_gateway.adapters.outbound.upstream_llm import HttpLlm
from agent_gateway.application.models import ServerInfo, Settings
from agent_gateway.application.turn import Deps
from agent_gateway.container import Container
from tests.application.fakes import Clock, FakeCreds, FakeStore, FakeTools, Sink, agent

KEY = "agw_test_key"
KEY2 = "agw_other_key"


class Keys:
    def __init__(self) -> None:
        self.users = {hash_key(KEY): ("k1", "alice"), hash_key(KEY2): ("k2", "bob")}

    async def find_user(self, key_hash):
        return self.users.get(key_hash)


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
    if msg.get("content"):
        out.append({"choices": [{"delta": {"content": msg["content"]}}]})
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

    def auth(self, key=KEY, **extra):
        return {"Authorization": f"Bearer {key}", **extra}


@pytest.fixture
def make_gateway():
    clients = []

    async def make(agents=None, *, rpm=1000, max_body=4_000_000, specs=None, creds=None, servers=None):
        upstream = Upstream()
        up_client = httpx.AsyncClient(base_url="http://upstream/v1", transport=httpx.MockTransport(upstream.handler))
        agents = {a.id: a for a in (agents or [agent("router")])}
        servers = servers or {"skills": ServerInfo("skills", credentials=("JIRA_USER",), header_prefix="X-Env-"),
                              "kb": ServerInfo("kb")}
        tools = FakeTools(specs or {})
        deps = Deps(llm=HttpLlm(up_client, retries=1, backoff=0), tools=tools, store=FakeStore(), creds=creds or FakeCreds(),
                    sink=Sink(), clock=Clock(), agents=agents, servers=servers, secret=b"s", settings=Settings())
        config = GatewayConfig(agents, servers, {}, Settings(), LimitsFile(requests_per_minute=rpm, max_body_bytes=max_body),
                               {}, RetrievalFile())
        container = Container(config, deps, Keys(), MemoryLock(), MemoryRateLimiter(), up_client, "", LangGraphDriver())
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(container)), base_url="http://gw")
        clients.append((client, up_client))
        return Gateway(client, upstream, container, tools)

    yield make
