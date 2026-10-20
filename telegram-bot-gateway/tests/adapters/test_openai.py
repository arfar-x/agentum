import json

import httpx
import pytest

from telegram_bot_gateway.adapters.outbound.openai import EndpointError, OpenAIChat

# Covers: FR-TRN-01, FR-TRN-02, FR-TRN-06, FR-TRN-07, FR-OUT-09, FR-CMD-06


def sse(*events) -> bytes:
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n").encode()


def delta(**d):
    return {"choices": [{"delta": d}]}


def client(handler):
    return OpenAIChat("http://llm/v1", 5, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_a_streamed_answer_is_assembled_and_the_request_is_plain_chat_completions():
    seen = {}

    def handler(request):
        seen["body"], seen["auth"], seen["url"] = json.loads(request.content), request.headers.get("authorization"), str(request.url)
        return httpx.Response(200, content=sse(delta(role="assistant"), delta(content="Hel"), delta(content="lo")))

    msg = await client(handler).chat("k1", "agent", [{"role": "user", "content": "hi"}], {"temperature": 0.1}, [{"type": "function"}])
    assert msg == {"role": "assistant", "content": "Hello"}
    assert seen["url"] == "http://llm/v1/chat/completions" and seen["auth"] == "Bearer k1"
    assert seen["body"]["model"] == "agent" and seen["body"]["stream"] is True and seen["body"]["temperature"] == 0.1
    assert seen["body"]["tools"] == [{"type": "function"}]


async def test_tool_calls_are_assembled_from_fragments():
    events = sse(
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "clarify", "arguments": '{"ques'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'tions":[]}'}}]),
    )
    msg = await client(lambda r: httpx.Response(200, content=events)).chat("k", "m", [], {}, [])
    assert msg["content"] is None
    assert msg["tool_calls"] == [{"id": "c1", "type": "function", "function": {"name": "clarify", "arguments": '{"questions":[]}'}}]


async def test_reasoning_is_reported_as_it_arrives():
    seen = []

    async def sink(text):
        seen.append(text)

    body = sse(delta(reasoning_content="a"), delta(reasoning_content="b"), delta(content="x"))
    msg = await client(lambda r: httpx.Response(200, content=body)).chat("k", "m", [], {}, [], sink)
    assert seen == ["a", "ab"] and msg["content"] == "x"


async def test_an_error_status_carries_the_endpoints_message():
    body = {"error": {"message": "temperature is locked"}}
    with pytest.raises(EndpointError, match="400.*temperature is locked"):
        await client(lambda r: httpx.Response(400, json=body)).chat("k", "m", [], {}, [])


async def test_a_mid_stream_error_and_a_timeout_are_reported():
    with pytest.raises(EndpointError, match="upstream died"):
        await client(lambda r: httpx.Response(200, content=sse({"error": {"message": "upstream died"}}))).chat("k", "m", [], {}, [])

    def slow(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(EndpointError, match="too long"):
        await client(slow).chat("k", "m", [], {}, [])


async def test_models_are_listed():
    assert await client(lambda r: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})).models("k") == ["a", "b"]
