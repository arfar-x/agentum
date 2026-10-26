# Covers: FR-ENT-01, FR-ENT-02, FR-ENT-04, FR-ENT-08, FR-ENT-10

import pytest
from pydantic import ValidationError

from agent_gateway.domain.access import access_warnings, can_hand_off, can_use, can_write, visible_agents
from agent_gateway.domain.agent import AgentAccess
from agent_gateway.domain.errors import ModelNotFound
from agent_gateway.domain.identity import KeyScope, Principal
from agent_gateway.domain.routing import parse_route
from tests.application.fakes import agent


def who(groups=(), roles=(), **scope):
    return Principal(user_id="u", username="u", groups=tuple(groups), roles=tuple(roles), scope=KeyScope(**scope))


def restricted(id="a", **access):
    return agent(id, access=access)


def test_no_block_or_no_use_list_means_everyone_may_use_and_write():
    for a in (agent("a"), restricted(write_groups=["leads"])):
        assert can_use(who(), a)
    assert can_write(who(), agent("a"))


def test_use_rule_is_any_of_groups_or_roles():
    a = restricted(groups=["product"], roles=["analyst"])
    assert can_use(who(groups=["product"]), a) and can_use(who(roles=["analyst"]), a)
    assert not can_use(who(groups=["other"], roles=["other"]), a) and not can_use(who(), a)
    assert not can_use(who(roles=["product"]), a)          # a role is not a group of the same name
    assert can_use(who(groups=["x"]), restricted(roles=["x"])) is False


def test_write_rule_is_separate_and_needs_the_use_rule_and_the_key_scope():
    a = restricted(groups=["product"], write_groups=["leads"], write_roles=["agent-writer"])
    assert can_write(who(groups=["product", "leads"]), a)
    assert can_write(who(groups=["product"], roles=["agent-writer"]), a)
    assert not can_write(who(groups=["product"]), a)
    assert not can_write(who(groups=["leads"]), a)          # cannot even use the agent
    assert not can_write(who(groups=["product", "leads"], write=False), a)
    assert not can_write(who(write=False), agent("a"))


def test_key_scope_only_narrows():
    agents = {"a": agent("a"), "b": restricted("b", groups=["g"]), "c": agent("c")}
    assert [x.id for x in visible_agents(who(groups=["g"], agents=("a", "b", "zzz")), agents)] == ["a", "b"]
    assert [x.id for x in visible_agents(who(agents=("b", "c")), agents)] == ["c"]
    assert [x.id for x in visible_agents(who(), agents)] == ["a", "c"]
    assert can_hand_off(who(), agents["c"]) and not can_hand_off(who(), agents["b"])


def test_a_refused_agent_is_indistinguishable_from_an_unknown_model_and_other_models_are_untouched():
    agents = {"a": restricted(groups=["g"])}
    with pytest.raises(ModelNotFound) as refused:
        parse_route("a", agents, who())
    with pytest.raises(ModelNotFound):
        parse_route("a:big", agents, who())
    err = refused.value
    assert (err.status, err.code, err.param) == (404, "model_not_found", "model")
    assert parse_route("a", agents, who(groups=["g"])).kind == "agent"
    assert parse_route("gpt-x", agents, who()).kind == "passthrough"


@pytest.mark.parametrize("bad", [
    {}, {"groups": []}, {"groups": ["a", "a"]}, {"roles": ["a,b"]}, {"write_roles": [""]}, {"groups": ["a\x00"]},
    {"groups": ["x" * 129]}, {"nope": ["a"]},
])
def test_invalid_access_blocks_are_rejected(bad):
    with pytest.raises(ValidationError):
        AgentAccess(**bad)
    with pytest.raises(ValidationError):
        agent("a", access=bad)


def test_valid_blocks_load_from_lists():
    a = agent("a", access={"groups": ["p", "q"], "write_roles": ["w"]})
    assert a.access.groups == ("p", "q") and a.access.roles is None and a.access.write_roles == ("w",)


def test_check_warnings():
    agents = [agent("a", access={"groups": ["p"], "roles": ["r"], "write_groups": ["lead"], "write_roles": ["r", "w"]}),
              agent("b")]
    text = "\n".join(access_warnings(agents, None, None))
    assert "write group 'lead'" in text and "write role 'w'" in text and "'r'" not in text.replace("'role'", "")
    assert "directory" not in text                          # no snapshot: typo check skipped
    text = "\n".join(access_warnings(agents, {"p", "lead"}, {"r"}))
    assert "role 'w' is in no directory entry" in text and "group 'p'" not in text
