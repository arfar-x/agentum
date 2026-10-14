# Covers: FR-PRM-02, FR-PRM-03, FR-TLR-02, FR-TLR-03, FR-CRD-01, FR-CRD-05

import pytest

from agent_gateway.domain.errors import UnsupportedParameter
from agent_gateway.domain.params import check_supported, merge_parameters
from agent_gateway.domain.toolroute import base_name, choose_route, match_client_tool, resolve_vars

DEFAULTS = {"temperature": 0.5, "reasoning_effort": "low"}


def test_client_overrides_apply_when_allowed():
    merged = merge_parameters(DEFAULTS, {"reasoning_effort": "high", "stream": True}, allow_overrides=True)
    assert merged == {"temperature": 0.5, "reasoning_effort": "high"}


def test_overrides_are_ignored_when_not_allowed():
    assert merge_parameters(DEFAULTS, {"temperature": 2}, allow_overrides=False) == DEFAULTS


def test_locked_parameters_keep_the_agents_value():
    merged = merge_parameters(DEFAULTS, {"temperature": 2, "seed": 1}, allow_overrides=True, locked=("temperature",))
    assert merged == {"temperature": 0.5, "reasoning_effort": "low", "seed": 1}


def test_unsupported_parameters_name_themselves():
    for body, param in (({"n": 2}, "n"), ({"logprobs": True}, "logprobs")):
        with pytest.raises(UnsupportedParameter) as exc:
            check_supported(body)
        assert exc.value.param == param and exc.value.status == 400
    check_supported({"n": 1})


def test_normalization_strips_client_and_server_decoration():
    for name in ("jira_create_issue", "mcp__atlassian__jira_create_issue", "jira_create_issue_mcp_agent-skills",
                 "Jira-Create-Issue", "agent_skills_jira_create_issue"):
        assert base_name(name, servers=("agent-skills",)) == "jira_create_issue"


def test_client_tools_match_only_by_name_or_alias():
    tools = ["Read", "mcp__jira__jira_search", "jira_search_users"]
    assert match_client_tool("jira_search", tools) == "mcp__jira__jira_search"
    assert match_client_tool("jira", tools) is None
    assert match_client_tool("confluence_search", tools, aliases={"confluence_search": "Read"}) == "Read"


def test_route_order():
    kw = dict(tool="t", needs_credentials=True)
    assert choose_route(client_name="c", request_vars={"A": "1"}, stored_vars={"A": "2"}, **kw).kind == "client"
    r = choose_route(client_name=None, request_vars={"A": "1"}, stored_vars={"A": "2"}, **kw)
    assert (r.kind, r.credentials) == ("gateway", "request")
    r = choose_route(client_name=None, request_vars={}, stored_vars={"A": "2"}, **kw)
    assert (r.kind, r.credentials) == ("gateway", "stored")
    r = choose_route(client_name=None, request_vars={}, stored_vars={}, **kw)
    assert (r.kind, r.client_name) == ("delegate", "t")
    assert choose_route(tool="kb_search", client_name=None, needs_credentials=False,
                        request_vars={}, stored_vars={}).kind == "gateway"


def test_request_credentials_beat_stored_and_undeclared_vars_are_dropped():
    out = resolve_vars({"JIRA_USER": "req", "EVIL": "x"}, {"JIRA_USER": "st", "JIRA_PASS": "st"},
                       declared=("JIRA_USER", "JIRA_PASS"))
    assert out == {"JIRA_USER": "req", "JIRA_PASS": "st"}
