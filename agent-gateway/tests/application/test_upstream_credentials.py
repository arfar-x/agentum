# Covers: FR-UPS-01, FR-UPS-02, FR-UPS-04, FR-UPS-05, FR-UPS-06

import pytest

from agent_gateway.application.upstream_credentials import UpstreamKeys
from agent_gateway.domain.errors import UpstreamKeyRejected, UpstreamKeyRequired, UpstreamRejected
from agent_gateway.domain.identity import Principal
from agent_gateway.domain.messages import ChatMessage
from tests.application.fakes import FakeAudit, call, client_tool, rig, say, user
from tests.conftest import Keys

ALICE, BOB = Principal("alice", "alice"), Principal("bob", "bob")
SECRET = "sk-alice-personal-0001"
LONG = "You are Some Other Assistant. " + ("Always prefer the todo tool. " * 400)


def resolver(portal="https://portal.example"):
    keys, audit = Keys(), FakeAudit()
    return UpstreamKeys(keys, audit, portal), keys, audit


async def test_the_personal_key_is_used_and_without_one_there_is_an_error_naming_the_portal_and_no_fallback():
    creds, keys, _ = resolver()
    keys.register("alice", SECRET)
    assert await creds.for_principal(ALICE) == SECRET
    with pytest.raises(UpstreamKeyRequired) as err:
        await creds.for_principal(BOB)
    assert err.value.status == 403 and err.value.code == "upstream_key_required"
    assert "https://portal.example" in err.value.message and "register-llm" in err.value.message


async def test_a_refused_personal_key_is_audited_and_explained_without_the_key():
    creds, keys, audit = resolver()
    keys.register("alice", SECRET)
    with pytest.raises(UpstreamKeyRejected) as err:
        async with creds.use(ALICE):
            raise UpstreamRejected(401, {"error": {"message": "bad key"}})
    assert err.value.status == 502 and err.value.code == "upstream_key_rejected"
    assert SECRET not in err.value.message and "https://portal.example" in err.value.message
    [event] = audit.events
    assert (event.actor, event.action, event.target, event.detail) == ("alice", "upstream.key_rejected", "up-alice", {"status": 401})
    assert SECRET not in repr(event)


async def test_other_upstream_errors_pass_through_unaudited():
    creds, keys, audit = resolver()
    keys.register("bob", SECRET)
    with pytest.raises(UpstreamRejected):
        async with creds.use(BOB):
            raise UpstreamRejected(400, None)
    assert audit.events == []


async def test_every_model_call_of_a_turn_and_its_compaction_carries_the_callers_key():
    r = rig(say("- use todo"), say("done"))
    creds, keys, _ = resolver()
    keys.register("u1", SECRET)
    r.deps.upstream = creds
    await r.turn([ChatMessage(role="system", content=LONG), user("hi")])
    assert r.llm.keys == [SECRET, SECRET]            # the compaction call and the model step


async def test_nothing_is_sent_when_there_is_no_key():
    r = rig(say("never"))
    r.deps.upstream = resolver()[0]
    with pytest.raises(UpstreamKeyRequired):
        await r.turn([user("hi")])
    assert r.llm.requests == []


async def test_a_resumed_turn_uses_the_key_of_whoever_resumes_it_and_stores_none():
    r = rig(call(("lookup", {})), say("done"))
    creds, keys, _ = resolver()
    keys.register("u1", SECRET)
    r.deps.upstream = creds
    msgs = [user("find")]
    await r.turn(msgs, client_tools=[client_tool("lookup")])
    assert SECRET not in repr(r.store.pending) and SECRET not in repr(r.store.threads)
    asst = r.visible_reply()
    keys.register("u1", "sk-replaced-0002")                        # the person replaced their key meanwhile
    result = ChatMessage(role="tool", tool_call_id=asst.tool_calls[0].id, content="found")
    await r.turn([*msgs, asst, result], client_tools=[client_tool("lookup")])
    assert r.llm.keys == [SECRET, "sk-replaced-0002"]
