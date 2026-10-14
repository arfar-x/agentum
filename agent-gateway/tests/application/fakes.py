"""In-memory fakes for every port, so the turn logic is provable with no services."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from agent_gateway.application.events import ContentDelta, Done, Event, ReasoningDelta, ToolCallsOut
from agent_gateway.application.models import (
    Completion,
    LlmDelta,
    LlmRequest,
    PendingTurn,
    ServerInfo,
    Thread,
    ToolResult,
    ToolSpec,
    TurnRequest,
)
from agent_gateway.application.turn import Deps
from agent_gateway.application.use_cases.run_turn import run_turn
from agent_gateway.domain.agent import AgentDefinition
from agent_gateway.domain.messages import ChatMessage, FunctionCall, ToolCall, ToolDef, ToolFunction, Usage


def say(text: str) -> Completion:
    return Completion(message=ChatMessage(role="assistant", content=text), usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15))


def call(*calls: tuple[str, dict[str, Any]] | tuple[str, dict[str, Any], str], text: str = "") -> Completion:
    tool_calls = []
    for i, c in enumerate(calls):
        cid = c[2] if len(c) == 3 else f"call_{len(tool_calls)}_{c[0]}"
        tool_calls.append(ToolCall(id=cid, function=FunctionCall(name=c[0], arguments=json.dumps(c[1]))))
    return Completion(message=ChatMessage(role="assistant", content=text or None, tool_calls=tool_calls),
                      finish_reason="tool_calls", usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15))


def raw_call(name: str, arguments: str, cid: str = "call_raw") -> Completion:
    return Completion(message=ChatMessage(role="assistant", content=None, tool_calls=[
        ToolCall(id=cid, function=FunctionCall(name=name, arguments=arguments))]), finish_reason="tool_calls")


class FakeLlm:
    def __init__(self, *script: Completion) -> None:
        self.script = list(script)
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> Completion:
        self.requests.append(request)
        assert self.script, "the model was called more times than scripted"
        return self.script.pop(0)

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmDelta]:
        completion = await self.complete(request)
        text = completion.message.text()
        for word in text.split(" ") if text else []:
            yield LlmDelta(content=word + " ")
        for i, c in enumerate(completion.message.tool_calls or []):
            yield LlmDelta(tool_calls=[{"index": i, "id": c.id, "function": {"name": c.function.name, "arguments": c.function.arguments}}])
        yield LlmDelta(finish_reason=completion.finish_reason, usage=completion.usage)


class FakeTools:
    def __init__(self, specs: dict[str, list[ToolSpec]], results: dict[str, str] | None = None) -> None:
        self.specs = specs
        self.results = results or {}
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []

    async def list_tools(self, server: str) -> list[ToolSpec]:
        return self.specs.get(server, [])

    async def call(self, server: str, tool: str, arguments: dict[str, Any], credentials: dict[str, str]) -> ToolResult:
        self.calls.append((server, tool, arguments, credentials))
        return ToolResult(self.results.get(tool, f"{tool} ok"))


class FakeStore:
    def __init__(self) -> None:
        self.threads: dict[tuple[str, str], Thread] = {}
        self.pending: list[PendingTurn] = []

    async def get_thread(self, user_id, history_hash):
        return self.threads.get((user_id, history_hash))

    async def put_thread(self, thread):
        self.threads[(thread.user_id, thread.history_hash)] = thread

    async def put_pending(self, pending):
        self.pending.append(pending)

    async def take_pending(self, user_id, call_id, now):
        for p in self.pending:
            if p.user_id == user_id and call_id in p.call_ids and p.expires_at > now:
                self.pending.remove(p)
                return p
        return None

    async def take_pending_by_code(self, user_id, code_hash, now):
        for p in self.pending:
            if p.user_id == user_id and p.code_hash == code_hash and p.expires_at > now:
                self.pending.remove(p)
                return p
        return None


class FakeCreds:
    def __init__(self, data: dict[tuple[str, str], dict[str, str]] | None = None) -> None:
        self.data = data or {}

    async def get(self, user_id, server):
        return dict(self.data.get((user_id, server), {}))


class Sink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)

    @property
    def content(self) -> str:
        return "".join(e.text for e in self.events if isinstance(e, ContentDelta))

    @property
    def reasoning(self) -> str:
        return "".join(e.text for e in self.events if isinstance(e, ReasoningDelta))

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [c for e in self.events if isinstance(e, ToolCallsOut) for c in e.calls]

    @property
    def done(self) -> Done:
        return [e for e in self.events if isinstance(e, Done)][-1]


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t


class NullLock:
    @asynccontextmanager
    async def hold(self, key: str):
        yield


@dataclass
class Rig:
    deps: Deps
    llm: FakeLlm
    tools: FakeTools
    store: FakeStore
    clock: Clock
    sink: Sink = field(default_factory=Sink)
    driver: object = None

    async def turn(self, messages: list[ChatMessage], *, agent: str = "router", user: str = "u1",
                   client_tools: list[ToolDef] | None = None, request_vars: dict | None = None,
                   stream: bool = False, params: dict | None = None):
        self.sink = Sink()
        self.deps.sink = self.sink
        request = TurnRequest(user_id=user, agent_id=agent, messages=messages, client_tools=client_tools or [],
                              request_vars=request_vars or {}, stream=stream, params=params or {})
        if self.driver is not None:
            return await run_turn(request, self.deps, NullLock(), self.driver)
        return await run_turn(request, self.deps, NullLock())

    def visible_reply(self) -> ChatMessage:
        return ChatMessage(role="assistant", content=self.sink.content or None, tool_calls=self.sink.tool_calls or None)


def user(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=text)


def client_tool(name: str, **props: Any) -> ToolDef:
    return ToolDef(function=ToolFunction(name=name, description=name, parameters={"type": "object", "properties": props}))


def spec(name: str, server: str = "skills", **props: Any) -> ToolSpec:
    return ToolSpec(server=server, name=name, description=name, parameters={"type": "object", "properties": props})


def agent(id: str = "router", **kw: Any) -> AgentDefinition:
    base: dict[str, Any] = dict(id=id, name=id, model="base-model", instructions="Be helpful.", activity="narrate")
    base.update(kw)
    return AgentDefinition(**base)


def rig(*script: Completion, agents: list[AgentDefinition] | None = None, specs: dict | None = None,
        servers: dict | None = None, creds: FakeCreds | None = None, retriever=None, results: dict | None = None) -> Rig:
    from agent_gateway.application.models import Settings

    llm, tools, store, clock = FakeLlm(*script), FakeTools(specs or {}, results), FakeStore(), Clock()
    ags = {a.id: a for a in (agents or [agent()])}
    deps = Deps(llm=llm, tools=tools, store=store, creds=creds or FakeCreds(), sink=Sink(), clock=clock,
                agents=ags, servers=servers or {"skills": ServerInfo("skills", credentials=("JIRA_USER",)),
                                                "kb": ServerInfo("kb")},
                secret=b"test-secret", settings=Settings(), retriever=retriever)
    return Rig(deps, llm, tools, store, clock)
