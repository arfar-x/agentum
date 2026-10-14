
"""The official OpenAI SDK, unmodified, against the gateway (SC-001, SC-002)."""

import json

import openai
import pytest
from openai import AsyncOpenAI

from tests.application.fakes import agent, client_tool
from tests.conftest import KEY, completion


def sdk(gw) -> AsyncOpenAI:
    return AsyncOpenAI(base_url="http://gw/v1", api_key=KEY, http_client=gw.client, max_retries=0)


async def test_models_list_and_retrieve(make_gateway):
    gw = await make_gateway([agent("router")])
    models = await sdk(gw).models.list()
    assert {m.id for m in models.data} == {"router", "base-model"}
    assert (await sdk(gw).models.retrieve("router")).id == "router"


async def test_chat_completion_non_streaming_with_reasoning_effort_and_system_prompt(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies.append(completion("Hello!"))
    r = await sdk(gw).chat.completions.create(
        model="router", reasoning_effort="high", max_completion_tokens=100,
        messages=[{"role": "system", "content": "Be terse."}, {"role": "user", "content": "hi"}])
    assert r.choices[0].message.content == "Hello!" and r.choices[0].finish_reason == "stop"
    assert r.usage.total_tokens == 15 and r.model == "router"
    sent = json.loads(gw.upstream.seen[-1][3])
    assert sent["reasoning_effort"] == "high" and sent["max_completion_tokens"] == 100


async def test_chat_completion_streaming_assembles_through_the_sdk(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies.append(completion("streamed answer here"))
    stream = await sdk(gw).chat.completions.create(
        model="router", stream=True, stream_options={"include_usage": True},
        messages=[{"role": "user", "content": "hi"}])
    text, usage, finish = "", None, None
    async for chunk in stream:
        if chunk.choices:
            text += chunk.choices[0].delta.content or ""
            finish = chunk.choices[0].finish_reason or finish
        if chunk.usage:
            usage = chunk.usage
    assert text == "streamed answer here" and finish == "stop" and usage.total_tokens == 15


async def test_client_side_tool_calls_come_back_in_the_sdks_native_shape(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies.append(completion(calls=[("Read", {"path": "a.txt"})]))
    tool = {"type": "function", "function": {"name": "Read", "description": "read",
                                             "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}
    r = await sdk(gw).chat.completions.create(model="router", tools=[tool], messages=[{"role": "user", "content": "read a"}])
    call = r.choices[0].message.tool_calls[0]
    assert r.choices[0].finish_reason == "tool_calls" and call.function.name == "Read"
    assert json.loads(call.function.arguments) == {"path": "a.txt"}


async def test_errors_surface_as_the_sdks_own_exception_types(make_gateway):
    gw = await make_gateway()
    with pytest.raises(openai.AuthenticationError):
        await AsyncOpenAI(base_url="http://gw/v1", api_key="wrong", http_client=gw.client, max_retries=0).models.list()
    with pytest.raises(openai.BadRequestError) as exc:
        await sdk(gw).chat.completions.create(model="router", n=3, messages=[{"role": "user", "content": "x"}])
    assert exc.value.param == "n"


async def test_an_ordinary_model_behaves_exactly_as_the_upstream(make_gateway):
    gw = await make_gateway()
    gw.upstream.replies.append(completion("direct"))
    r = await sdk(gw).chat.completions.create(model="raw-model", messages=[{"role": "user", "content": "hi"}], temperature=0.1)
    assert r.choices[0].message.content == "direct"
    assert json.loads(gw.upstream.seen[-1][3])["model"] == "raw-model"
