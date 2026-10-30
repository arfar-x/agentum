# Covers: FR-KBI-01, FR-KBI-03

from agent_gateway.domain.identity import Principal
from tests.application.fakes import agent, call, rig, say, spec, user

KB = {"kb": {"enabled": True}}


class Spy:
    def __init__(self):
        self.seen = []

    async def search(self, queries, limit, types, *, identity=None):
        self.seen.append(identity)
        return []


async def test_the_callers_identity_goes_to_the_catalog_lookup_and_to_gateway_tool_calls():
    who = Principal("u1", "alice", groups=("product",))
    spy = Spy()
    r = rig(call(("t", {})), say("done"), specs={"kb": [spec("t", "kb")]},
            agents=[agent(tools=[{"server": "kb"}], preretrieval=KB)], retriever=spy)
    await r.turn([user("go")], principal=who)
    assert spy.seen == [who] and r.tools.identities == [who]


async def test_a_person_with_no_groups_is_still_passed_as_a_principal():
    spy = Spy()
    r = rig(call(("t", {})), say("done"), specs={"kb": [spec("t", "kb")]},
            agents=[agent(tools=[{"server": "kb"}], preretrieval=KB)], retriever=spy)
    await r.turn([user("go")])
    assert [p.groups for p in spy.seen] == [()] and [p.groups for p in r.tools.identities] == [()]
