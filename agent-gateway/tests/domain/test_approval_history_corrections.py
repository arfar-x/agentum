from agent_gateway.domain import corrections
from agent_gateway.domain.approval import arguments_hash, make_code, parse_decision, verify_code
from agent_gateway.domain.history import chain_hash, lookup_hash
from agent_gateway.domain.interaction import find_ask_tool, parse_answer
from agent_gateway.domain.messages import ChatMessage

S = b"secret"
ARGS = {"summary": "x", "project": "P"}


def code(**over):
    kw = dict(user_id="u", call_id="c1", tool="jira_create_issue", args_hash=arguments_hash(ARGS))
    kw.update(over)
    return kw


def test_code_verifies_only_for_the_exact_call():
    c = make_code(S, **code())
    assert verify_code(S, c, **code())
    assert verify_code(S, c.upper(), **code())
    for over in ({"user_id": "v"}, {"call_id": "c2"}, {"tool": "other"},
                 {"args_hash": arguments_hash({**ARGS, "summary": "y"})}):
        assert not verify_code(S, c, **code(**over))
    assert not verify_code(b"other", c, **code())


def test_argument_hash_ignores_key_order():
    assert arguments_hash({"a": 1, "b": 2}) == arguments_hash({"b": 2, "a": 1})


def test_decisions_parse_strictly():
    assert parse_decision("Approve abc123") == ("approve", "abc123")
    assert parse_decision("reject abc123") == ("reject", "abc123")
    assert parse_decision("please approve it") is None
    assert parse_decision("approve") is None


def test_thread_lookup_uses_the_history_up_to_the_last_assistant_message():
    u1, a1, u2 = (ChatMessage(role="user", content="hi"), ChatMessage(role="assistant", content="hello "),
                  ChatMessage(role="user", content="more"))
    stored = chain_hash("u", [u1, ChatMessage(role="assistant", content="hello")])
    assert lookup_hash("u", [u1, a1, u2]) == stored
    assert lookup_hash("other", [u1, a1, u2]) != stored
    assert lookup_hash("u", [u1]) is None


def test_corrections_are_short_and_actionable():
    msg = corrections.unknown_tool("nope", ["b", "a"])
    assert "nope" in msg and "a, b" in msg
    obj, err = corrections.parse_arguments('{"a": ')
    assert obj is None and err
    assert corrections.parse_arguments("") == ({}, None)
    assert corrections.parse_arguments("[1]")[0] is None
    problems = corrections.schema_problems(
        {"properties": {"n": {"type": "integer"}}, "required": ["n", "m"]}, {"n": "x"})
    assert "missing required 'm'" in problems and "'n' must be integer" in problems


def test_ask_tools_are_found_by_name_and_answers_parsed():
    t = find_ask_tool(["Read", "mcp__x__AskUserQuestion"])
    assert t is not None and t.name == "mcp__x__AskUserQuestion"
    assert t.build("Approval", "Create ticket?")["questions"][0]["options"][0]["label"] == "Approve"
    assert find_ask_tool(["Read", "Bash"]) is None
    assert parse_answer("User selected: Approve") is True
    assert parse_answer("Reject") is False
    assert parse_answer("hmm") is None
