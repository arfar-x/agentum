"""One agent turn, as small nodes over a state.

The nodes here hold every decision; the order they run in is a graph (LangGraph in production,
``run_sequentially`` in tests). A node returns nothing and mutates the state it is given, and
``next_node`` says where to go next.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from agent_gateway.application.events import ContentDelta, Done, ReasoningDelta, ToolCallsOut
from agent_gateway.application.use_cases.compact_prompt import compact_client_prompt
from agent_gateway.application.models import (
    Completion,
    LlmRequest,
    PendingTurn,
    ServerInfo,
    Settings,
    Thread,
    ToolSpec,
    TurnRequest,
)
from agent_gateway.application.ports import (
    Clock,
    ConversationStore,
    CredentialStore,
    EventSink,
    Llm,
    Retriever,
    TextCache,
    ToolGateway,
    UpstreamCredentials,
)
from agent_gateway.domain import access, activity, corrections, retrieval
from agent_gateway.domain.agent import AgentDefinition, ToolBinding
from agent_gateway.domain.approval import arguments_hash, make_code, parse_decision, verify_code
from agent_gateway.domain.errors import LoopExhausted
from agent_gateway.domain.identity import Principal
from agent_gateway.domain.history import chain_hash, lookup_hash
from agent_gateway.domain.identity import Principal
from agent_gateway.domain.interaction import APPROVE, REJECT, AskTool, find_ask_tool, parse_answer
from agent_gateway.domain.messages import ChatMessage, FunctionCall, ToolCall, ToolDef, ToolFunction, Usage
from agent_gateway.domain.params import merge_parameters
from agent_gateway.domain import trace
from agent_gateway.domain.prompt import compose_system_prompt, unavailable_note
from agent_gateway.domain.toolroute import choose_route, match_client_tool, matching_client_tools, mentions_any, resolve_vars

ASK_USER = "ask_user"
USE_CLIENT_TOOLS = "use_client_tools"
END = "__end__"


@dataclass
class Deps:
    llm: Llm
    tools: ToolGateway
    store: ConversationStore
    creds: CredentialStore
    sink: EventSink
    clock: Clock
    agents: Mapping[str, AgentDefinition]
    servers: Mapping[str, ServerInfo]
    secret: bytes
    settings: Settings = field(default_factory=Settings)
    retriever: Retriever | None = None
    aliases: Mapping[str, str] = field(default_factory=dict)
    cache: TextCache | None = None
    ask_tools: Mapping[str, AskTool] | None = None   # None: the built-in defaults
    upstream: UpstreamCredentials | None = None      # None: model calls carry no key


@dataclass
class Exposed:
    """The tools the model is offered on one step, and what each one means."""

    defs: list[ToolDef] = field(default_factory=list)
    client: set[str] = field(default_factory=set)
    gateway: dict[str, tuple[ToolSpec, ToolBinding]] = field(default_factory=dict)
    handoffs: dict[str, str] = field(default_factory=dict)
    ask: bool = False
    opt_in_tool: bool = False
    unavailable: dict[str, list[str]] = field(default_factory=dict)  # server -> tool names

    def names(self) -> list[str]:
        return [d.function.name for d in self.defs]


@dataclass
class TurnState:
    request: TurnRequest
    agent_id: str
    stage: str = "prepare"
    messages: list[ChatMessage] = field(default_factory=list)
    client_system: str = ""
    context: list[str] = field(default_factory=list)
    step_start: int = 0
    step: int = 0
    corrections: int = 0
    usage: Usage = field(default_factory=Usage)
    queue: list[ToolCall] = field(default_factory=list)
    client_calls: list[ToolCall] = field(default_factory=list)
    decisions: dict[str, bool] = field(default_factory=dict)
    reply_text: str = ""
    resumed: bool = False
    stored_creds: dict[str, dict[str, str]] = field(default_factory=dict)
    finish_reason: str = "stop"
    exposed: Exposed | None = None
    step_started: bool = False
    client_opt_in: bool = False
    trace_prior: list[dict[str, Any]] = field(default_factory=list)   # earlier turns of this thread
    trace_new: list[dict[str, Any]] = field(default_factory=list)     # this turn's gateway-run calls


# --------------------------------------------------------------------------- helpers


def _agent(state: TurnState, deps: Deps) -> AgentDefinition:
    return deps.agents[state.agent_id]


def _principal(state: TurnState) -> Principal:
    """The caller. A request without one (internal callers, tests) is an unrestricted person."""
    request = state.request
    return request.principal or Principal(user_id=request.user_id, username=request.user_id)


def _narrate(state: TurnState, deps: Deps) -> bool:
    return _agent(state, deps).activity == "narrate"


async def _note(state: TurnState, deps: Deps, event: activity.ActivityEvent) -> None:
    if _narrate(state, deps):
        await deps.sink.emit(ReasoningDelta(event.line() + "\n"))


async def _say(state: TurnState, deps: Deps, text: str, *, block: bool = False) -> None:
    """Emit reply text.

    Streamed tokens are appended exactly as they arrive. A paragraph break is added only where
    separate pieces of the reply meet: at the start of a new model step's text, or before a
    ``block`` (a preview or notice the gateway writes itself).
    """
    if not state.reply_text:
        text = text.lstrip("\n")       # models often open with blank lines
    if not text:
        return
    joins = block or not state.step_started
    state.step_started = True
    if joins and state.reply_text:
        text = text.lstrip("\n")
        if not text:
            state.step_started = False      # only blank lines so far: join at the first real text
            return
        if not state.reply_text.endswith("\n\n"):
            text = ("\n" if state.reply_text.endswith("\n") else "\n\n") + text
    state.reply_text += text
    await deps.sink.emit(ContentDelta(text))


def _tool_message(call_id: str, text: str) -> ChatMessage:
    return ChatMessage(role="tool", tool_call_id=call_id, content=text)


def _split_visible(messages: list[ChatMessage]) -> tuple[list[ChatMessage], str]:
    """Drop system/developer messages from the body; they become the client system prompt."""
    body, system = [], []
    for m in messages:
        if m.role in {"system", "developer"}:
            system.append(m.text())
        else:
            body.append(m)
    return body, "\n\n".join(s for s in system if s)


def _last_assistant_index(messages: list[ChatMessage]) -> int | None:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "assistant":
            return i
    return None


def _preview(call: ToolCall, args: dict[str, Any]) -> str:
    shown = json.dumps(args, ensure_ascii=False, indent=2)
    return f"The assistant wants to run `{call.function.name}` with:\n{shown}"


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


# --------------------------------------------------------------------------- toolset


async def build_toolset(state: TurnState, deps: Deps) -> Exposed:
    agent = _agent(state, deps)
    ex = Exposed()
    reserved = {ASK_USER, USE_CLIENT_TOOLS}
    principal = _principal(state)
    can_write = access.can_write(principal, agent)
    for handoff in agent.handoffs:
        if not access.can_hand_off(principal, deps.agents[handoff.to]):
            continue
        name = "transfer_to_" + handoff.to.replace("-", "_")
        reserved.add(name)
        ex.handoffs[name] = handoff.to
        ex.defs.append(ToolDef(function=ToolFunction(name=name, description=handoff.description)))
    if "ask_user" in agent.interaction:
        ex.ask = True
        ex.defs.append(ToolDef(function=ToolFunction(
            name=ASK_USER,
            description="Ask the user a question and wait for the answer. Use only when you cannot proceed without it.",
            parameters={"type": "object", "required": ["question"], "properties": {
                "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}},
        )))
    client_defs = [d for d in state.request.client_tools
                   if agent.allows_client_tool(d.function.name) and d.function.name not in reserved]
    client_names = [d.function.name for d in client_defs]
    replaced: set[str] = set()      # client tools the agent's own version takes the place of
    held_back: set[str] = set()     # client equivalents withheld until the user opts in
    for binding in agent.tools:
        server = deps.servers[binding.server]
        stored = await _stored(state, deps, binding.server) if server.needs_credentials else {}
        sent = state.request.request_vars.get(binding.server, {})
        for spec in await deps.tools.list_tools(binding.server):
            if not binding.includes(spec.name) or spec.name in reserved:
                continue
            if binding.needs_approval(spec.name) and not can_write:
                continue      # nothing to approve: the caller may not use this agent's write tools
            match = match_client_tool(spec.name, client_names, servers=deps.servers, aliases=deps.aliases)
            route = choose_route(tool=spec.name, client_name=match if state.client_opt_in else None, needs_credentials=server.needs_credentials,
                                 request_vars=resolve_vars(sent, {}, server.credentials),
                                 stored_vars=resolve_vars({}, stored, server.credentials))
            if route.kind == "gateway":
                ex.gateway[spec.name] = (spec, binding)
                ex.defs.append(ToolDef(function=ToolFunction(
                    name=spec.name, description=spec.description, parameters=spec.parameters)))
                replaced.update(matching_client_tools(spec.name, client_names, servers=deps.servers, aliases=deps.aliases))
            elif route.kind == "unavailable":
                ex.unavailable.setdefault(spec.server, []).append(spec.name)
                held_back.update(matching_client_tools(spec.name, client_names, servers=deps.servers, aliases=deps.aliases))
                if not state.client_opt_in:
                    held_back.update(n for n in client_names if mentions_any(n, server.client_terms))
            # route "client" (the user opted in): the client's own version is offered below
    if ex.unavailable and not state.client_opt_in:
        ex.opt_in_tool = True
        ex.defs.append(ToolDef(function=ToolFunction(
            name=USE_CLIENT_TOOLS,
            description=("Call this only after the user has said they want to use their client's own tools or "
                         "connectors for the work your unavailable tools would do. It enables them for this conversation."))))
    for d in client_defs:
        if d.function.name in replaced or d.function.name in held_back:
            continue
        ex.client.add(d.function.name)
        ex.defs.append(d)
    return ex


# --------------------------------------------------------------------------- nodes


async def prepare(state: TurnState, deps: Deps) -> None:
    req = state.request
    visible = req.messages
    last = visible[-1] if visible else None
    now = deps.clock.now()
    thread_hash = lookup_hash(req.user_id, visible)
    thread = await deps.store.get_thread(req.user_id, thread_hash) if thread_hash else None
    principal = _principal(state)
    if thread and thread.active_agent in deps.agents and access.can_use(principal, deps.agents[thread.active_agent]):
        state.agent_id = thread.active_agent       # a specialist the caller may no longer use is not continued
    if thread:
        state.client_opt_in = thread.client_opt_in
        state.trace_prior = list(thread.trace)

    pending: PendingTurn | None = None
    code_decision: tuple[str, str] | None = None
    if last is not None and last.role == "user":
        code_decision = parse_decision(last.text())
        if code_decision:
            pending = await deps.store.take_pending_by_code(req.user_id, _code_hash(code_decision[1]), now)
            if pending is None or pending.approval_call is None or not _verify(deps, req.user_id, pending, code_decision[1]):
                await _say(state, deps, "That approval code is not valid or has expired. Nothing was changed.", block=True)
                state.stage = "finish"
                return
    elif last is not None and last.role == "tool" and last.tool_call_id:
        pending = await deps.store.take_pending(req.user_id, last.tool_call_id, now)

    if pending is not None:
        state.resumed = True
        if pending.agent_id in deps.agents:
            if not access.can_use(principal, deps.agents[pending.agent_id]):
                await _say(state, deps, "You can no longer use the agent this was waiting on. Nothing was changed.", block=True)
                state.stage = "finish"
                return
            state.agent_id = pending.agent_id
        if pending.kind == "approval" and pending.approval_call is not None:
            offered = await build_toolset(state, deps)
            if pending.approval_call.function.name not in offered.gateway:
                await _say(state, deps, f"You can no longer run `{pending.approval_call.function.name}`. Nothing was changed.", block=True)
                state.stage = "finish"
                return
        cut = _last_assistant_index(visible)
        base = visible[:cut] if cut is not None else []
        tail = [m for m in visible[(cut + 1) if cut is not None else 0:] if m.role == "tool"]
        body, raw_system = _split_visible(base)
        state.client_system = await _compact(state, deps, raw_system)
        state.messages = body + pending.hidden_messages
        state.step_start = len(body)
        state.queue = list(pending.queue)
        if pending.kind == "approval" and pending.approval_call is not None:
            if code_decision:
                approved: bool | None = code_decision[0] == "approve"
            else:
                approved = parse_answer(last.text()) if last else None
            state.decisions[pending.approval_call.id] = bool(approved)
        elif pending.kind in {"question", "client_tool"}:
            # the client's results (an answer, or tool output) follow the restored hidden work
            state.messages.extend(tail)
        state.stage = "dispatch" if state.queue else "model"
        return

    body, raw_system = _split_visible(visible)
    state.client_system = await _compact(state, deps, raw_system)
    state.messages = body
    state.step_start = len(body)
    agent = _agent(state, deps)
    kb = agent.preretrieval.kb
    if kb.enabled and deps.retriever is not None and last is not None and last.role == "user":
        try:
            hits = await deps.retriever.search([last.text()], kb.limit, kb.types,
                                                 identity=state.request.principal)
            state.context.append(retrieval.format_hits(hits))
            state.trace_new.append(trace.entry(state.agent_id, trace.CATALOG_LOOKUP, {"query": last.text()}, ok=True, found=len(hits)))
            await _note(state, deps, activity.retrieval(len(hits)))
        except Exception:  # noqa: BLE001 -- a catalog failure must never fail the turn
            state.context.append(retrieval.UNAVAILABLE)
            state.trace_new.append(trace.entry(state.agent_id, trace.CATALOG_LOOKUP, {"query": last.text()}, ok=False))
    state.stage = "model"


def _caller(state: TurnState) -> Principal:
    return Principal(user_id=state.request.user_id, username=state.request.user_id)


@asynccontextmanager
async def _model_key(state: TurnState, deps: Deps) -> AsyncIterator[str | None]:
    """The key for one upstream call, resolved now for whoever is making this request."""
    if deps.upstream is None:
        yield None
    else:
        async with deps.upstream.use(_caller(state)) as key:
            yield key


async def _compact(state: TurnState, deps: Deps, raw: str) -> str:
    key = await deps.upstream.for_principal(_caller(state)) if deps.upstream is not None else None
    return await compact_client_prompt(raw, user_id=state.request.user_id, model=_agent(state, deps).model,
                                       llm=deps.llm, cache=deps.cache, settings=deps.settings, api_key=key)


def _verify(deps: Deps, user_id: str, pending: PendingTurn, code: str) -> bool:
    call = pending.approval_call
    assert call is not None
    args, _ = corrections.parse_arguments(call.function.arguments)
    return verify_code(deps.secret, code, user_id=user_id, call_id=call.id, tool=call.function.name,
                       args_hash=arguments_hash(args or {}))


def _llm_request(state: TurnState, deps: Deps, ex: Exposed) -> LlmRequest:
    agent = _agent(state, deps)
    system = ChatMessage(role="system", content=compose_system_prompt(agent, state.client_system))
    notes = list(state.context)
    earlier = trace.note(state.trace_prior)
    if earlier:
        notes.append(earlier)
    if ex.unavailable:
        notes.append(unavailable_note(ex.unavailable, state.client_opt_in))
    context = [ChatMessage(role="system", content=c) for c in notes]
    params = merge_parameters(agent.model_parameters, state.request.params,
                              allow_overrides=agent.allow_client_overrides, locked=agent.locked_parameters)
    return LlmRequest(model=state.request.upstream_model or agent.model,
                      messages=[system, *context, *state.messages], tools=ex.defs, params=params)


async def model(state: TurnState, deps: Deps) -> None:
    state.step += 1
    state.step_started = False
    if state.step > deps.settings.max_steps:
        raise LoopExhausted("the agent took too many steps without finishing")
    ex = state.exposed = await build_toolset(state, deps)
    request = _llm_request(state, deps, ex)
    completion = await _call_model(state, deps, request)
    state.usage = state.usage + completion.usage
    message = completion.message
    state.step_start = len(state.messages)
    calls = message.tool_calls or []
    text = message.text()
    if not text and not calls:
        state.corrections += 1
        if state.corrections > deps.settings.max_corrections:
            raise LoopExhausted("the model kept returning empty replies")
        state.messages.append(ChatMessage(role="system", content=corrections.empty_reply()))
        state.stage = "model"
        return
    state.messages.append(ChatMessage(role="assistant", content=text or None, tool_calls=calls or None))
    if calls:
        state.queue = list(calls)
        state.stage = "dispatch"
    else:
        state.stage = "finish"
        state.finish_reason = completion.finish_reason if completion.finish_reason != "tool_calls" else "stop"


async def _call_model(state: TurnState, deps: Deps, request: LlmRequest) -> Completion:
    async with _model_key(state, deps) as key:
        return await _ask_model(state, deps, request, key)


async def _ask_model(state: TurnState, deps: Deps, request: LlmRequest, key: str | None) -> Completion:
    if not state.request.stream:
        completion = await deps.llm.complete(request, key)
        if completion.reasoning:
            await deps.sink.emit(ReasoningDelta(completion.reasoning))
        await _say(state, deps, completion.message.text())
        return completion
    text, reasoning = [], []
    calls: dict[int, dict[str, Any]] = {}
    finish: str | None = None
    usage = Usage()
    async for delta in deps.llm.stream(request, key):
        if delta.reasoning:
            reasoning.append(delta.reasoning)
            await deps.sink.emit(ReasoningDelta(delta.reasoning))
        if delta.content:
            text.append(delta.content)
            await _say(state, deps, delta.content)
        for part in delta.tool_calls:
            slot = calls.setdefault(part.get("index", 0), {"id": "", "name": "", "arguments": ""})
            slot["id"] = part.get("id") or slot["id"]
            fn = part.get("function") or {}
            slot["name"] += fn.get("name") or ""
            slot["arguments"] += fn.get("arguments") or ""
        finish = delta.finish_reason or finish
        if delta.usage:
            usage = delta.usage
    tool_calls = [
        ToolCall(id=c["id"] or f"call_{uuid.uuid4().hex[:12]}",
                 function=FunctionCall(name=c["name"], arguments=c["arguments"]))
        for _, c in sorted(calls.items())
    ]
    return Completion(
        message=ChatMessage(role="assistant", content="".join(text) or None, tool_calls=tool_calls or None),
        finish_reason=finish or "stop", usage=usage, reasoning="".join(reasoning))


async def _stored(state: TurnState, deps: Deps, server: str) -> dict[str, str]:
    if server not in state.stored_creds:
        state.stored_creds[server] = await deps.creds.get(state.request.user_id, server)
    return state.stored_creds[server]


def _correct(state: TurnState, deps: Deps, call: ToolCall, text: str) -> None:
    state.corrections += 1
    if state.corrections > deps.settings.max_corrections:
        raise LoopExhausted("the model kept making invalid tool calls")
    state.messages.append(_tool_message(call.id, text))


async def dispatch(state: TurnState, deps: Deps) -> None:
    ex = state.exposed or await build_toolset(state, deps)
    state.exposed = ex
    agent = _agent(state, deps)
    pending_calls, state.queue = state.queue, []
    reads: list[tuple[ToolCall, dict[str, Any]]] = []
    approvals: list[tuple[ToolCall, dict[str, Any]]] = []
    asks: list[tuple[ToolCall, dict[str, Any]]] = []
    for call in pending_calls:
        name = call.function.name
        args, err = corrections.parse_arguments(call.function.arguments)
        if name in ex.handoffs:
            await _handoff(state, deps, call, ex.handoffs[name], pending_calls)
            state.stage = "model"
            return
        if name not in ex.names():
            _correct(state, deps, call, corrections.unknown_tool(name, ex.names()))
            continue
        if err is not None or args is None:
            _correct(state, deps, call, corrections.invalid_arguments_json(name, err or "invalid"))
            continue
        definition = next(d for d in ex.defs if d.function.name == name)
        problems = corrections.schema_problems(definition.function.parameters, args)
        if problems:
            _correct(state, deps, call, corrections.schema_violation(name, problems))
            continue
        if name == USE_CLIENT_TOOLS:
            state.client_opt_in = True
            state.messages.append(_tool_message(call.id, "Enabled. The user's own client tools are now available."))
            continue
        if name in ex.client:
            state.client_calls.append(call)
        elif name == ASK_USER:
            asks.append((call, args))
        else:
            spec, binding = ex.gateway[name]
            server = deps.servers[spec.server]
            stored = await _stored(state, deps, spec.server) if server.needs_credentials else {}
            sent = state.request.request_vars.get(spec.server, {})
            route = choose_route(tool=name, client_name=None, needs_credentials=server.needs_credentials,
                                 request_vars=resolve_vars(sent, {}, server.credentials),
                                 stored_vars=resolve_vars({}, stored, server.credentials))
            if route.kind == "unavailable":
                _correct(state, deps, call, corrections.unknown_tool(name, ex.names()))
                continue
            elif binding.needs_approval(name):
                approvals.append((call, args))
            else:
                reads.append((call, args))
    for call, args in reads:
        await _run_gateway(state, deps, call, args, approved=False)
    for index, (call, args) in enumerate(approvals):
        if call.id in state.decisions:
            if state.decisions[call.id]:
                await _run_gateway(state, deps, call, args, approved=True)
            else:
                state.messages.append(_tool_message(call.id, corrections.declined(call.function.name)))
            continue
        rest = [c for c, _ in approvals[index + 1:]] + [c for c, _ in asks] + list(state.client_calls)
        await _request_approval(state, deps, call, args, rest)
        return
    if asks:
        call, args = asks[0]
        for other, _ in asks[1:]:
            state.messages.append(_tool_message(other.id, "Skipped: ask one question at a time."))
        await _ask_user(state, deps, call, args)
        return
    if state.client_calls:
        await _hand_to_client(state, deps, state.client_calls, kind="client_tool")
        return
    state.stage = "model"


async def _handoff(state: TurnState, deps: Deps, call: ToolCall, target: str, all_calls: list[ToolCall]) -> None:
    previous = state.agent_id
    answered = {m.tool_call_id for m in state.messages[state.step_start:] if m.role == "tool"}
    name = deps.agents[target].name
    state.messages.append(_tool_message(call.id, (
        f"Handoff complete: you are now {name} and you hold this conversation. Do the user's request "
        "yourself from here, as {name}; do not announce the handoff again or wait for anyone.").replace("{name}", name)))
    for other in all_calls:
        if other.id != call.id and other.id not in answered:
            state.messages.append(_tool_message(other.id, "Skipped: the conversation was handed off."))
    await _note(state, deps, activity.handoff(previous, target))
    state.agent_id = target
    state.corrections = 0


async def _run_gateway(state: TurnState, deps: Deps, call: ToolCall, args: dict[str, Any], *, approved: bool) -> None:
    ex = state.exposed
    assert ex is not None
    spec, binding = ex.gateway[call.function.name]
    server = deps.servers[spec.server]
    stored = await _stored(state, deps, spec.server) if server.needs_credentials else {}
    creds = resolve_vars(state.request.request_vars.get(spec.server, {}), stored, server.credentials)
    run_args = dict(args)
    if binding.needs_approval(spec.name):
        run_args.pop("confirm", None)
        if approved:
            run_args["confirm"] = True
    await _note(state, deps, activity.tool_started(spec.name))
    result = await deps.tools.call(spec.server, spec.name, run_args, creds, identity=state.request.principal)
    state.trace_new.append(trace.entry(state.agent_id, spec.name, args, ok=not result.is_error))
    text = corrections.redact(result.text, creds.values())[: deps.settings.tool_result_chars]
    state.messages.append(_tool_message(call.id, corrections.tool_failed(spec.name, text) if result.is_error else text))
    await _note(state, deps, activity.tool_finished(spec.name))


def _ask_tool(state: TurnState, deps: Deps):
    return find_ask_tool((d.function.name for d in state.request.client_tools), deps.ask_tools)


async def _request_approval(state: TurnState, deps: Deps, call: ToolCall, args: dict[str, Any], rest: list[ToolCall]) -> None:
    preview = _preview(call, args)
    hidden = state.messages[state.step_start:]
    expires = deps.clock.now() + deps.settings.approval_ttl_seconds
    ask = _ask_tool(state, deps)
    if ask is not None:
        ask_id = f"approve_{call.id}"
        out = ToolCall(id=ask_id, function=FunctionCall(
            name=ask.name, arguments=json.dumps(ask.build("Approval", preview + "\n\nRun it?", [APPROVE, REJECT]))))
        await deps.store.put_pending(PendingTurn(
            user_id=state.request.user_id, call_ids=[ask_id], kind="approval", agent_id=state.agent_id,
            hidden_messages=hidden, queue=[call, *rest], approval_call=call, expires_at=expires))
        state.client_calls = [out]
        state.stage = "finish"
        await deps.sink.emit(ToolCallsOut([out]))
        state.finish_reason = "tool_calls"
        return
    code = make_code(deps.secret, user_id=state.request.user_id, call_id=call.id,
                     tool=call.function.name, args_hash=arguments_hash(args))
    await deps.store.put_pending(PendingTurn(
        user_id=state.request.user_id, call_ids=[], kind="approval", agent_id=state.agent_id,
        hidden_messages=hidden, queue=[call, *rest], approval_call=call, code_hash=_code_hash(code),
        expires_at=expires))
    await _say(state, deps, f"{preview}\n\nReply `approve {code}` to run it or `reject {code}` to cancel.", block=True)
    state.client_calls = []
    state.stage = "finish"


async def _ask_user(state: TurnState, deps: Deps, call: ToolCall, args: dict[str, Any]) -> None:
    question = str(args.get("question", ""))
    options = args.get("options") or []
    ask = _ask_tool(state, deps)
    if ask is None:
        text = question + ("\n" + "\n".join(f"- {o}" for o in options) if options else "")
        await _say(state, deps, text, block=True)
        state.stage = "finish"
        return
    built = ask.build("Question", question, [str(o) for o in options])
    out = ToolCall(id=call.id, function=FunctionCall(name=ask.name, arguments=json.dumps(built)))
    await _hand_to_client(state, deps, [out], kind="question", hidden_calls=[call])


async def _hand_to_client(state: TurnState, deps: Deps, calls: list[ToolCall], *, kind: str,
                          hidden_calls: list[ToolCall] | None = None) -> None:
    step_calls = state.messages[state.step_start].tool_calls or [] if state.step_start < len(state.messages) else []
    extra_hidden = len(state.messages) - state.step_start > 1
    if extra_hidden or len(step_calls) != len(calls) or kind == "question":
        await deps.store.put_pending(PendingTurn(
            user_id=state.request.user_id, call_ids=[c.id for c in calls], kind=kind,  # type: ignore[arg-type]
            agent_id=state.agent_id, hidden_messages=state.messages[state.step_start:],
            expires_at=deps.clock.now() + deps.settings.pending_ttl_seconds))
    state.client_calls = calls
    state.finish_reason = "tool_calls"
    state.stage = "finish"
    await deps.sink.emit(ToolCallsOut(calls))


async def finish(state: TurnState, deps: Deps) -> None:
    visible = ChatMessage(
        role="assistant", content=state.reply_text or None, tool_calls=state.client_calls or None)
    key = chain_hash(state.request.user_id, [*state.request.messages, visible])
    await deps.store.put_thread(Thread(state.request.user_id, key, state.agent_id, state.client_opt_in,
                                       trace.merge(state.trace_prior, state.trace_new)))
    await deps.sink.emit(Done(state.finish_reason, state.usage))
    state.stage = END


NODES = {"prepare": prepare, "model": model, "dispatch": dispatch, "finish": finish}


def next_node(state: TurnState) -> str:
    return state.stage


async def run_sequentially(state: TurnState, deps: Deps) -> None:
    """The reference driver: tests use it, and the LangGraph runtime must behave the same."""
    while state.stage != END:
        await NODES[state.stage](state, deps)
