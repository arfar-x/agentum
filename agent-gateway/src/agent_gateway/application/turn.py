"""One agent turn, as small nodes over a state.

The nodes here hold every decision; the order they run in is a graph (LangGraph in production,
``run_sequentially`` in tests). A node returns nothing and mutates the state it is given, and
``next_node`` says where to go next.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_gateway.application.events import ContentDelta, Done, ReasoningDelta, ToolCallsOut
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
    ToolGateway,
)
from agent_gateway.domain import activity, corrections, retrieval
from agent_gateway.domain.agent import AgentDefinition, ToolBinding
from agent_gateway.domain.approval import arguments_hash, make_code, parse_decision, verify_code
from agent_gateway.domain.errors import LoopExhausted
from agent_gateway.domain.history import chain_hash, lookup_hash
from agent_gateway.domain.interaction import find_ask_tool, parse_answer
from agent_gateway.domain.messages import ChatMessage, FunctionCall, ToolCall, ToolDef, ToolFunction, Usage
from agent_gateway.domain.params import merge_parameters
from agent_gateway.domain.prompt import compose_system_prompt
from agent_gateway.domain.toolroute import choose_route, match_client_tool, resolve_vars

ASK_USER = "ask_user"
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


@dataclass
class Exposed:
    """The tools the model is offered on one step, and what each one means."""

    defs: list[ToolDef] = field(default_factory=list)
    client: set[str] = field(default_factory=set)
    gateway: dict[str, tuple[ToolSpec, ToolBinding]] = field(default_factory=dict)
    handoffs: dict[str, str] = field(default_factory=dict)
    ask: bool = False

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


# --------------------------------------------------------------------------- helpers


def _agent(state: TurnState, deps: Deps) -> AgentDefinition:
    return deps.agents[state.agent_id]


def _narrate(state: TurnState, deps: Deps) -> bool:
    return _agent(state, deps).activity == "narrate"


async def _note(state: TurnState, deps: Deps, event: activity.ActivityEvent) -> None:
    if _narrate(state, deps):
        await deps.sink.emit(ReasoningDelta(event.line() + "\n"))


async def _say(state: TurnState, deps: Deps, text: str) -> None:
    if not text:
        return
    if state.reply_text and not state.reply_text[-1].isspace() and not text[0].isspace():
        text = "\n\n" + text
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
    reserved = {ASK_USER}
    for handoff in agent.handoffs:
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
    for binding in agent.tools:
        for spec in await deps.tools.list_tools(binding.server):
            if not binding.includes(spec.name) or spec.name in reserved:
                continue
            if match_client_tool(spec.name, client_names, servers=deps.servers, aliases=deps.aliases):
                continue  # the user's own client has it; its version is offered instead
            ex.gateway[spec.name] = (spec, binding)
            ex.defs.append(ToolDef(function=ToolFunction(
                name=spec.name, description=spec.description, parameters=spec.parameters)))
    for d in client_defs:
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
    if thread and thread.active_agent in deps.agents:
        state.agent_id = thread.active_agent

    pending: PendingTurn | None = None
    code_decision: tuple[str, str] | None = None
    if last is not None and last.role == "user":
        code_decision = parse_decision(last.text())
        if code_decision:
            pending = await deps.store.take_pending_by_code(req.user_id, _code_hash(code_decision[1]), now)
            if pending is None or pending.approval_call is None or not _verify(deps, req.user_id, pending, code_decision[1]):
                await _say(state, deps, "That approval code is not valid or has expired. Nothing was changed.")
                state.stage = "finish"
                return
    elif last is not None and last.role == "tool" and last.tool_call_id:
        pending = await deps.store.take_pending(req.user_id, last.tool_call_id, now)

    if pending is not None:
        state.resumed = True
        state.agent_id = pending.agent_id if pending.agent_id in deps.agents else state.agent_id
        cut = _last_assistant_index(visible)
        base = visible[:cut] if cut is not None else []
        tail = [m for m in visible[(cut + 1) if cut is not None else 0:] if m.role == "tool"]
        body, state.client_system = _split_visible(base)
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

    body, state.client_system = _split_visible(visible)
    state.messages = body
    state.step_start = len(body)
    agent = _agent(state, deps)
    kb = agent.preretrieval.kb
    if kb.enabled and deps.retriever is not None and last is not None and last.role == "user":
        try:
            hits = await deps.retriever.search([last.text()], kb.limit, kb.types)
            state.context.append(retrieval.format_hits(hits))
            await _note(state, deps, activity.retrieval(len(hits)))
        except Exception:  # noqa: BLE001 -- a catalog failure must never fail the turn
            state.context.append(retrieval.UNAVAILABLE)
    state.stage = "model"


def _verify(deps: Deps, user_id: str, pending: PendingTurn, code: str) -> bool:
    call = pending.approval_call
    assert call is not None
    args, _ = corrections.parse_arguments(call.function.arguments)
    return verify_code(deps.secret, code, user_id=user_id, call_id=call.id, tool=call.function.name,
                       args_hash=arguments_hash(args or {}))


def _llm_request(state: TurnState, deps: Deps, ex: Exposed) -> LlmRequest:
    agent = _agent(state, deps)
    system = ChatMessage(role="system", content=compose_system_prompt(agent, state.client_system))
    context = [ChatMessage(role="system", content=c) for c in state.context]
    params = merge_parameters(agent.model_parameters, state.request.params,
                              allow_overrides=agent.allow_client_overrides, locked=agent.locked_parameters)
    return LlmRequest(model=state.request.upstream_model or agent.model,
                      messages=[system, *context, *state.messages], tools=ex.defs, params=params)


async def model(state: TurnState, deps: Deps) -> None:
    state.step += 1
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
    if not state.request.stream:
        completion = await deps.llm.complete(request)
        if completion.reasoning:
            await deps.sink.emit(ReasoningDelta(completion.reasoning))
        await _say(state, deps, completion.message.text())
        return completion
    text, reasoning = [], []
    calls: dict[int, dict[str, Any]] = {}
    finish: str | None = None
    usage = Usage()
    async for delta in deps.llm.stream(request):
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
            if route.kind == "delegate":
                state.client_calls.append(call)
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
    state.messages.append(_tool_message(call.id, f"Conversation handed to {target}."))
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
    result = await deps.tools.call(spec.server, spec.name, run_args, creds)
    text = result.text[: deps.settings.tool_result_chars]
    state.messages.append(_tool_message(call.id, corrections.tool_failed(spec.name, text) if result.is_error else text))
    await _note(state, deps, activity.tool_finished(spec.name))


def _ask_tool(state: TurnState):
    return find_ask_tool(d.function.name for d in state.request.client_tools)


async def _request_approval(state: TurnState, deps: Deps, call: ToolCall, args: dict[str, Any], rest: list[ToolCall]) -> None:
    preview = _preview(call, args)
    hidden = state.messages[state.step_start:]
    expires = deps.clock.now() + deps.settings.approval_ttl_seconds
    ask = _ask_tool(state)
    if ask is not None:
        ask_id = f"approve_{call.id}"
        out = ToolCall(id=ask_id, function=FunctionCall(
            name=ask.name, arguments=json.dumps(ask.build("Approval", preview + "\n\nRun it?"))))
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
    await _say(state, deps, f"{preview}\n\nReply `approve {code}` to run it or `reject {code}` to cancel.")
    state.client_calls = []
    state.stage = "finish"


async def _ask_user(state: TurnState, deps: Deps, call: ToolCall, args: dict[str, Any]) -> None:
    question = str(args.get("question", ""))
    options = args.get("options") or []
    ask = _ask_tool(state)
    if ask is None:
        text = question + ("\n" + "\n".join(f"- {o}" for o in options) if options else "")
        await _say(state, deps, text)
        state.stage = "finish"
        return
    built = ask.build("Question", question)
    if options and isinstance(built.get("questions"), list):
        built["questions"][0]["options"] = [{"label": str(o), "description": ""} for o in options]
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
    await deps.store.put_thread(Thread(state.request.user_id, key, state.agent_id))
    await deps.sink.emit(Done(state.finish_reason, state.usage))
    state.stage = END


NODES = {"prepare": prepare, "model": model, "dispatch": dispatch, "finish": finish}


def next_node(state: TurnState) -> str:
    return state.stage


async def run_sequentially(state: TurnState, deps: Deps) -> None:
    """The reference driver: tests use it, and the LangGraph runtime must behave the same."""
    while state.stage != END:
        await NODES[state.stage](state, deps)
