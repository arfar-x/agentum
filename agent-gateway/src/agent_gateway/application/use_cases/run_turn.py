"""Run one agent turn for a user: lock the conversation, then drive the nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_gateway.application.models import TurnRequest
from agent_gateway.application.ports import Lock
from agent_gateway.application.turn import Deps, TurnState, run_sequentially
from agent_gateway.domain.history import chain_hash, lookup_hash

Driver = Callable[[TurnState, Deps], Awaitable[None]]


def lock_key(request: TurnRequest) -> str:
    key = lookup_hash(request.user_id, request.messages)
    if key is None:
        key = chain_hash(request.user_id, request.messages[:1])
    return f"{request.user_id}:{key}"


async def run_turn(request: TurnRequest, deps: Deps, lock: Lock, driver: Driver = run_sequentially) -> TurnState:
    state = TurnState(request=request, agent_id=request.agent_id)
    async with lock.hold(lock_key(request)):
        await driver(state, deps)
    return state
