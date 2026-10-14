# Covers: FR-API-01

"""The OpenAI SDK's Responses client against the gateway."""

import json

from openai import AsyncOpenAI

from tests.application.fakes import agent
from tests.conftest import KEY, completion


def sdk(gw) -> AsyncOpenAI:
    return AsyncOpenAI(base_url="http://gw/v1", api_key=KEY, http_client=gw.client, max_retries=0)


async def test_a_response_has_output_text_usage_and_can_be_continued(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies += [completion("First answer"), completion("Second answer")]
    r1 = await sdk(gw).responses.create(model="router", input="hello", instructions="Be brief.",
                                        reasoning={"effort": "low"}, max_output_tokens=50)
    assert r1.output_text == "First answer" and r1.status == "completed" and r1.usage.total_tokens == 15
    sent = json.loads(gw.upstream.seen[-1][3])
    assert sent["reasoning_effort"] == "low" and sent["max_completion_tokens"] == 50
    assert "Be brief." in sent["messages"][0]["content"]
    r2 = await sdk(gw).responses.create(model="router", input="and then?", previous_response_id=r1.id)
    assert r2.output_text == "Second answer"
    roles = [m["role"] for m in json.loads(gw.upstream.seen[-1][3])["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


async def test_streaming_events_assemble_in_the_sdk(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies.append(completion("streamed words here"))
    stream = await sdk(gw).responses.create(model="router", input="hi", stream=True)
    text, kinds, final = "", [], None
    async for event in stream:
        kinds.append(event.type)
        if event.type == "response.output_text.delta":
            text += event.delta
        if event.type == "response.completed":
            final = event.response
    assert text == "streamed words here" and kinds[0] == "response.created" and kinds[-1] == "response.completed"
    assert final.output_text == "streamed words here" and final.usage.total_tokens == 15


async def test_caller_executed_function_calls_round_trip(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies += [completion(calls=[("get_time", {"tz": "UTC"})]), completion("It is noon.")]
    tool = {"type": "function", "name": "get_time", "description": "time", "parameters": {"type": "object", "properties": {"tz": {"type": "string"}}}}
    r1 = await sdk(gw).responses.create(model="router", input="what time is it?", tools=[tool])
    call = [i for i in r1.output if i.type == "function_call"][0]
    assert call.name == "get_time" and json.loads(call.arguments) == {"tz": "UTC"}
    r2 = await sdk(gw).responses.create(model="router", tools=[tool], previous_response_id=r1.id,
                                        input=[{"type": "function_call_output", "call_id": call.call_id, "output": "12:00"}])
    assert r2.output_text == "It is noon."


async def test_other_models_go_straight_through(make_gateway):
    gw = await make_gateway()
    r = await gw.client.post("/v1/responses", headers=gw.auth(), json={"model": "raw", "input": "x"})
    assert r.json() == {"echo": "/v1/responses"}
