# Covers: FR-AGT-02, FR-AGT-03, FR-AGT-04, FR-RTE-02

import pytest
from pydantic import ValidationError

from agent_gateway.domain.agent import AgentDefinition, validate_agent_set
from agent_gateway.domain.errors import ModelNotAllowed
from agent_gateway.domain.prompt import compose_system_prompt
from agent_gateway.domain.routing import parse_route


def agent(id="router", **kw):
    base = dict(id=id, name=id, model="m", instructions="Be helpful.")
    base.update(kw)
    return AgentDefinition(**base)


def test_ids_must_be_lowercase_slugs():
    for bad in ("Router", "-x", "a b", "a_b", ""):
        with pytest.raises(ValidationError):
            agent(id=bad)
    assert agent(id="prepare-for-dev").id == "prepare-for-dev"


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        agent(colour="red")


def test_handoff_targets_must_exist_and_not_be_self():
    a = agent("a", handoffs=[{"to": "ghost", "description": "x"}])
    b = agent("b", handoffs=[{"to": "b", "description": "x"}])
    problems = validate_agent_set({"a": a, "b": b}, servers=set())
    assert any("ghost" in p for p in problems)
    assert any("itself" in p for p in problems)
    ok = agent("a", handoffs=[{"to": "b", "description": "x"}])
    assert validate_agent_set({"a": ok, "b": agent("b")}, servers=set()) == []


def test_client_system_prompt_is_appended_by_default_prepended_or_ignored():
    a = agent(activity="quiet")
    assert compose_system_prompt(a, "CLIENT").startswith("Be helpful.")
    assert "CLIENT" in compose_system_prompt(a, "CLIENT")
    assert compose_system_prompt(agent(activity="quiet", client_system="prepend"), "CLIENT").startswith("CLIENT")
    assert "CLIENT" not in compose_system_prompt(agent(activity="quiet", client_system="ignore"), "CLIENT")
    assert compose_system_prompt(a, None) == "Be helpful."


def test_route_selects_agents_and_everything_else_is_passthrough():
    agents = {"router": agent("router"), "locked": agent("locked", allow_model_override=False)}
    assert parse_route("router", agents).kind == "agent"
    assert parse_route("gpt-x", agents).kind == "passthrough"
    assert parse_route("llama:8b", agents).kind == "passthrough"


def test_route_override_needs_the_agent_to_allow_it():
    agents = {"router": agent("router"), "locked": agent("locked", allow_model_override=False)}
    r = parse_route("router:big-model", agents)
    assert (r.kind, r.agent_id, r.upstream_model) == ("agent", "router", "big-model")
    with pytest.raises(ModelNotAllowed):
        parse_route("locked:big-model", agents)
