"""The production driver: the turn nodes wired as a LangGraph graph.

Every decision lives in ``application.turn``. This module only owns the order they run in, so
the graph and the reference driver used in tests cannot drift apart.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from langgraph.graph import END as LG_END
from langgraph.graph import START, StateGraph
from langchain_core.runnables import RunnableConfig

from agent_gateway.application.turn import END, NODES, Deps, TurnState


def _node(fn):
    async def run(state: TurnState, config: RunnableConfig) -> dict[str, Any]:
        deps: Deps = config["configurable"]["deps"]
        await fn(state, deps)
        # LangGraph hands each node a fresh state, so everything the node changed is returned.
        return {f.name: getattr(state, f.name) for f in fields(state)}

    return run


def _route(state: TurnState) -> str:
    return LG_END if state.stage == END else state.stage


def build_graph():
    graph = StateGraph(TurnState)
    for name, fn in NODES.items():
        graph.add_node(name, _node(fn))
    graph.add_conditional_edges(START, _route)
    for name in NODES:
        graph.add_conditional_edges(name, _route)
    return graph.compile()


class LangGraphDriver:
    """Callable ``(state, deps) -> None`` compatible with ``run_turn``'s ``driver`` argument."""

    def __init__(self) -> None:
        self._graph = build_graph()

    async def __call__(self, state: TurnState, deps: Deps) -> None:
        limit = deps.settings.max_steps * 4 + 12
        result = await self._graph.ainvoke(state, config={"recursion_limit": limit, "configurable": {"deps": deps}})
        for f in fields(state):
            if f.name in result:
                setattr(state, f.name, result[f.name])
