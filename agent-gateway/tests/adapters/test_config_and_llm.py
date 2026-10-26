# Covers: FR-AGT-01, FR-AGT-05, FR-CFG-01, FR-CFG-02, FR-COR-05

import json
from pathlib import Path

import httpx
import pytest

from agent_gateway.adapters.outbound.config_files import load_config
from agent_gateway.adapters.outbound.upstream_llm import HttpLlm
from agent_gateway.application.models import LlmRequest
from agent_gateway.domain.errors import ConfigError, UpstreamRejected, UpstreamUnavailable
from agent_gateway.domain.messages import ChatMessage

AGENT = """version: 1
id: router
name: Router
model: ${MODEL}
instructions: hi
"""


def write(tmp_path: Path, agent: str = AGENT, servers: str = "servers: {}\n", gateway: str = ""):
    (tmp_path / "agents").mkdir(exist_ok=True)
    (tmp_path / "agents" / "router.yaml").write_text(agent)
    (tmp_path / "mcp-servers.yaml").write_text(servers)
    if gateway:
        (tmp_path / "gateway.yaml").write_text(gateway)
    return tmp_path


def test_a_valid_directory_loads_with_env_interpolation_and_default_limits(tmp_path):
    cfg = load_config(write(tmp_path), {"MODEL": "m1"})
    assert cfg.agents["router"].model == "m1"
    assert cfg.settings.max_steps == 25 and cfg.limits.requests_per_minute == 120


def test_limits_and_aliases_come_from_gateway_yaml(tmp_path):
    cfg = load_config(write(tmp_path, gateway="limits: {max_steps: 7}\naliases: {a: b}\n"), {"MODEL": "m"})
    assert cfg.settings.max_steps == 7 and cfg.aliases == {"a": "b"}


def test_an_unset_variable_stops_startup_naming_file_and_variable(tmp_path):
    with pytest.raises(ConfigError, match=r"router\.yaml.*MODEL"):
        load_config(write(tmp_path), {})


@pytest.mark.parametrize("agent, needle", [
    (AGENT + "colour: red\n", "colour"),
    (AGENT.replace("id: router", "id: Bad_Id"), "id"),
    (AGENT + "handoffs: [{to: ghost, description: x}]\n", "ghost"),
    (AGENT + "tools: [{server: nowhere}]\n", "nowhere"),
])
def test_invalid_agent_files_name_the_problem(tmp_path, agent, needle):
    with pytest.raises(ConfigError, match=needle):
        load_config(write(tmp_path, agent), {"MODEL": "m"})


def test_broken_yaml_names_the_file(tmp_path):
    with pytest.raises(ConfigError, match="router.yaml"):
        load_config(write(tmp_path, ": : :\n  - ["), {"MODEL": "m"})


def llm(handler, retries=2):
    client = httpx.AsyncClient(base_url="http://up/v1", transport=httpx.MockTransport(handler))
    return HttpLlm(client, retries=retries, backoff=0)


REQ = LlmRequest(model="m", messages=[ChatMessage(role="user", content="hi")], tools=[], params={"temperature": 0.1})


async def test_complete_parses_message_usage_and_reasoning():
    seen = {}

    def handler(request):
        seen["body"], seen["auth"] = json.loads(request.content), request.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "yo", "reasoning_content": "hmm"},
                                                     "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}})

    c = await llm(handler).complete(REQ, "k")
    assert (c.message.text(), c.reasoning, c.usage.total_tokens) == ("yo", "hmm", 4)
    assert seen["auth"] == "Bearer k" and seen["body"]["temperature"] == 0.1 and "tools" not in seen["body"]


async def test_transient_failures_are_retried_then_succeed():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    assert (await llm(handler).complete(REQ)).message.text() == "ok" and len(calls) == 3


async def test_persistent_transient_failure_surfaces_and_client_errors_are_passed_on():
    with pytest.raises(UpstreamUnavailable):
        await llm(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))).complete(REQ)
    err = {"error": {"message": "bad model", "type": "invalid_request_error", "code": "model_not_found", "param": "model"}}
    with pytest.raises(UpstreamRejected) as exc:
        await llm(lambda r: httpx.Response(404, json=err)).complete(REQ)
    assert exc.value.status == 404 and exc.value.code == "model_not_found" and "bad model" in exc.value.message


async def test_stream_yields_deltas_and_usage():
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in [
        {"choices": [{"delta": {"content": "he"}}]},
        {"choices": [{"delta": {"content": "llo"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}},
    ]) + "data: [DONE]\n\n"
    deltas = [d async for d in llm(lambda r: httpx.Response(200, text=sse)).stream(REQ)]
    assert "".join(d.content for d in deltas) == "hello"
    assert deltas[-1].usage.total_tokens == 3



async def test_reasoning_a_client_sends_back_in_history_is_not_forwarded_upstream():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    req = LlmRequest(model="m", tools=[], params={}, messages=[
        ChatMessage(role="user", content="hi"),
        ChatMessage(role="assistant", content="hello", reasoning_content="Looked up the knowledge base.", reasoning="x"),
        ChatMessage(role="user", content="again")])
    await llm(handler).complete(req)
    assert all("reasoning" not in m and "reasoning_content" not in m for m in seen["body"]["messages"])



def test_ask_tools_in_gateway_yaml_are_validated_against_the_known_styles(tmp_path):
    cfg = load_config(write(tmp_path, gateway="ask_tools: {prompt_user: questions_choices}\n"), {"MODEL": "m"})
    assert cfg.ask_tools == {"prompt_user": "questions_choices"}
    with pytest.raises(ConfigError, match="unknown style"):
        load_config(write(tmp_path, gateway="ask_tools: {prompt_user: nope}\n"), {"MODEL": "m"})
