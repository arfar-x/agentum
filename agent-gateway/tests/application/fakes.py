"""In-memory fakes for every port, so the turn logic is provable with no services."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_gateway.application.events import ContentDelta, Done, Event, ReasoningDelta, ToolCallsOut
from agent_gateway.application.audit import (
    can_fold,
    clean_detail,
    key_added_event,
    key_revoked_event,
)
from agent_gateway.application.identity_ports import Account, Identity, NewKey
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
from agent_gateway.domain.identity import (
    AuditEvent,
    AuthError,
    DirectoryEntry,
    KeyInfo,
    Principal,
    clean_groups,
    clean_roles,
    is_active,
)
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
        self.keys: list[str | None] = []          # the api_key each call was made with

    async def complete(self, request: LlmRequest, api_key: str | None = None) -> Completion:
        self.requests.append(request)
        self.keys.append(api_key)
        assert self.script, "the model was called more times than scripted"
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def stream(self, request: LlmRequest, api_key: str | None = None) -> AsyncIterator[LlmDelta]:
        completion = await self.complete(request, api_key)
        text = completion.message.text()
        for i in range(0, len(text), 3):          # sub-word pieces, no whitespace added
            yield LlmDelta(content=text[i:i + 3])
        for i, c in enumerate(completion.message.tool_calls or []):
            yield LlmDelta(tool_calls=[{"index": i, "id": c.id, "function": {"name": c.function.name, "arguments": c.function.arguments}}])
        yield LlmDelta(finish_reason=completion.finish_reason, usage=completion.usage)


class FakeTools:
    def __init__(self, specs: dict[str, list[ToolSpec]], results: dict[str, str] | None = None) -> None:
        self.specs = specs
        self.results = results or {}
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, str]]] = []
        self.identities: list[Any] = []   # the identity passed with each call, in call order

    async def list_tools(self, server: str) -> list[ToolSpec]:
        return self.specs.get(server, [])

    async def call(self, server: str, tool: str, arguments: dict[str, Any], credentials: dict[str, str],
                   *, identity: Any = None) -> ToolResult:
        self.calls.append((server, tool, arguments, credentials))
        self.identities.append(identity)
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


    async def put_response(self, user_id, response_id, messages):
        self.responses = getattr(self, "responses", {})
        self.responses[(user_id, response_id)] = list(messages)

    async def get_response(self, user_id, response_id):
        return getattr(self, "responses", {}).get((user_id, response_id))


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.groups: dict[str, list[str]] = {}

    def _front(self, group, key):
        keys = self.groups.setdefault(group, [])
        if key in keys:
            keys.remove(key)
        keys.insert(0, key)
        return keys

    async def get(self, key, refresh_seconds, group=None):
        if key in self.data and group:
            self._front(group, key)
        return self.data.get(key)

    async def put(self, key, value, ttl_seconds, group=None, keep=1):
        self.data[key] = value
        if group:
            keys = self._front(group, key)
            for old in keys[keep:]:
                self.data.pop(old, None)
            del keys[keep:]


class FakeCreds:
    """Credential store in memory. With ``audit`` it writes the same entries the Postgres store does."""

    def __init__(self, data: dict[tuple[str, str], dict[str, str]] | None = None, audit=None) -> None:
        self.data = data or {}
        self.audit = audit

    async def get(self, user_id, server):
        return dict(self.data.get((user_id, server), {}))

    async def list_vars(self, user, server):
        return sorted(self.data.get((user, server), {}))

    async def delete_var(self, user, server, var, *, actor="cli"):
        gone = self.data.get((user, server), {}).pop(var, None) is not None
        if gone and self.audit:
            self.audit.write(actor, "cred.delete", f"{server}/{var}", {"owner": user})
        return gone

    async def set_credential(self, user_id, server, var, value, *, actor="cli"):
        self.data.setdefault((user_id, server), {})[var] = value
        if self.audit:
            self.audit.write(actor, "cred.set", f"{server}/{var}", {"owner": user_id})


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
                   stream: bool = False, params: dict | None = None, principal=None):
        self.sink = Sink()
        self.deps.sink = self.sink
        request = TurnRequest(principal=principal or Principal(user_id=user, username=user), agent_id=agent, messages=messages, client_tools=client_tools or [],
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
                secret=b"test-secret", settings=Settings(), retriever=retriever, cache=MemoryCache())
    return Rig(deps, llm, tools, store, clock)


# ---- Identity and access: fakes of every port in application/identity_ports.py


class FakeSecretBox:
    """Reversible and obviously not encryption: the tests look at what the store holds."""

    def encrypt(self, plaintext: bytes) -> bytes:
        return b"enc:" + plaintext[::-1]

    def decrypt(self, ciphertext: bytes) -> bytes:
        assert ciphertext.startswith(b"enc:")
        return ciphertext[4:][::-1]


class FakeNow:
    """A settable timezone-aware clock; calling it returns the current value."""

    def __init__(self, at: datetime | None = None) -> None:
        self.at = at or datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kw: float) -> None:
        self.at += timedelta(**kw)


class FakeAudit:
    """``AuditLog`` in memory, folding on the injected clock."""

    def __init__(self, now: FakeNow | None = None) -> None:
        self.now = now or FakeNow()
        self.events: list[AuditEvent] = []

    async def record(self, actor, action, target="", detail=None):
        self.write(actor, action, target, detail)

    def write(self, actor, action, target="", detail=None):
        """The synchronous core, also used by the fake stores to write in the same step as a change."""
        now = self.now()
        for i in range(len(self.events) - 1, -1, -1):
            earlier = self.events[i]
            if (earlier.actor, earlier.action, earlier.target) == (actor, action, target):
                if can_fold(earlier, actor, action, target, now):
                    self.events[i] = replace(earlier, count=earlier.count + 1, last_ts=now)
                    return
                break
        self.events.append(AuditEvent(len(self.events) + 1, now, now, actor, action, target,
                                      clean_detail(detail), 1))

    async def recent(self, limit=50, *, actor=None, action=None, since=None):
        found = [e for e in reversed(self.events)
                 if (actor is None or e.actor == actor) and (action is None or e.action == action)
                 and (since is None or e.last_ts >= since)]
        return found[:limit]

    async def prune(self, older_than):
        keep = [e for e in self.events if e.last_ts >= older_than]
        removed = len(self.events) - len(keep)
        self.events = keep
        return removed


class FakeKeyRecords:
    """``KeyRecords`` in memory; keeps the hash and the encrypted secret like the real table."""

    def __init__(self, audit: FakeAudit | None = None, now: FakeNow | None = None) -> None:
        self.now = now or FakeNow()
        self.audit = audit or FakeAudit(self.now)
        self.rows: dict[str, dict[str, Any]] = {}

    def _info(self, row: dict[str, Any]) -> KeyInfo:
        return row["info"]

    async def add_key(self, new: NewKey, *, actor: str):
        replaced = None
        if new.kind == "upstream":
            for row in self.rows.values():
                i = row["info"]
                if i.owner == new.owner and i.kind == "upstream" and i.revoked_at is None:
                    row["info"], row["secret_enc"] = replace(i, revoked_at=self.now()), None
                    replaced = i.id
        if new.kind == "issued" and any(r["key_hash"] == new.key_hash and r["info"].revoked_at is None
                                        and r["info"].kind == "issued" for r in self.rows.values()):
            raise AssertionError("an issued key's hash must stay unique")      # the real index refuses it
        info = KeyInfo(str(uuid.uuid4()), new.owner, new.kind, new.label, new.prefix, new.scope,
                       self.now(), new.expires_at, None, None)
        self.rows[info.id] = {"info": info, "key_hash": new.key_hash, "secret_enc": new.secret_enc}
        self.audit.write(actor, *key_added_event(info, replaced))
        return info, replaced

    async def find_active(self, key_hash, now):
        return [r["info"] for r in self.rows.values() if r["key_hash"] == key_hash and is_active(r["info"], now)]

    async def touch(self, key_id, now):
        row = self.rows[key_id]
        row["info"] = replace(row["info"], last_used_at=now)

    async def upstream_secret(self, owner):
        for row in self.rows.values():
            i = row["info"]
            if i.owner == owner and i.kind == "upstream" and i.revoked_at is None:
                return row["secret_enc"]
        return None

    async def list_for(self, owner):
        return [r["info"] for r in self.rows.values() if r["info"].owner == owner]

    async def revoke(self, owner, key_id, *, actor, by):
        row = self.rows.get(key_id)
        if row is None or row["info"].revoked_at is not None or (owner is not None and row["info"].owner != owner):
            return False
        row["info"] = replace(row["info"], revoked_at=self.now())
        if row["info"].kind == "upstream":
            row["secret_enc"] = None
        self.audit.write(actor, *key_revoked_event(row["info"], by))
        return True


class FakeDirectory:
    """``DirectoryStore`` in memory."""

    def __init__(self) -> None:
        self.rows: dict[str, DirectoryEntry] = {}

    async def lookup(self, user_id):
        return self.rows.get(user_id)

    async def replace_all(self, accounts: Sequence[Account], synced_at):
        self.rows = {a.sub: DirectoryEntry(a.sub, a.username, clean_groups(a.groups), clean_roles(a.roles),
                                           a.enabled, synced_at, a.kind) for a in accounts}

    async def newest_sync(self):
        return max((e.synced_at for e in self.rows.values()), default=None)

    async def list_all(self):
        return sorted(self.rows.values(), key=lambda e: e.username)


class FakeTokenVerifier:
    """``TokenVerifier`` over a table of known tokens; any other token is refused."""

    def __init__(self, known: dict[str, Identity] | None = None, *, failure: str = "bad_signature") -> None:
        self.known, self.failure = known or {}, failure

    async def verify(self, token):
        if token not in self.known:
            raise AuthError(self.failure)
        return self.known[token]


class FakeDirectorySource:
    """``DirectorySource`` returning a list, or raising to simulate an outage."""

    def __init__(self, accounts: list[Account] | None = None) -> None:
        self.accounts = accounts or []
        self.error: Exception | None = None
        self.calls = 0

    async def fetch_all(self):
        self.calls += 1
        if self.error:
            raise self.error
        return list(self.accounts)


class FakePortalLogin:
    """``PortalLogin`` where the code is the sub of the identity it completes to."""

    def __init__(self, identities: dict[str, Identity] | None = None) -> None:
        self.identities = identities or {}

    def authorize_url(self, state, nonce, code_challenge, redirect_uri):
        return f"https://idp.test/authorize?state={state}&nonce={nonce}&cc={code_challenge}&redirect_uri={redirect_uri}"

    async def complete(self, code, code_verifier, redirect_uri, nonce):
        if code not in self.identities:
            raise AuthError("malformed")
        return self.identities[code]
