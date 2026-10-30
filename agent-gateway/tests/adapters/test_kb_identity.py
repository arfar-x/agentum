# Covers: FR-KBI-01, FR-KBI-02, FR-KBI-03, FR-KBI-04

import asyncio
import json
import logging
import socket
import threading

import pytest
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from agent_gateway.adapters.outbound.config_files import load_config
from agent_gateway.adapters.outbound.mcp_tools import KbRetriever, McpTools, forwarded_groups
from agent_gateway.application.models import ServerInfo
from agent_gateway.domain.errors import ConfigError
from agent_gateway.domain.identity import Principal

SECRET = "s3cret-trust-value"


def make_server():
    mcp = FastMCP("stub-kb")

    def headers() -> str:
        h = get_http_headers(include_all=True)
        return json.dumps({"groups": h.get("x-kb-groups"), "trust": h.get("x-kb-trust")})

    @mcp.tool
    def kb_search(queries: list[str], catalog_queries: list[str], limit: int = 5, types: list[str] | None = None) -> dict:
        return {"hits": [{"title": {"en": headers()}}]}

    @mcp.tool
    def seen() -> str:
        return headers()

    return mcp


@pytest.fixture
def url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(make_server().http_app(path="/mcp"), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        threading.Event().wait(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def tools(url, *, secret=SECRET):
    servers = {"kb": ServerInfo("kb", forward_identity=True), "other": ServerInfo("other")}
    return McpTools({"kb": (url, 10.0), "other": (url, 10.0)}, servers, trust_secret=secret)


def person(*groups, kind="user"):
    return Principal("u1", "alice", groups=tuple(groups), kind=kind)


async def seen(t, server="kb", identity=None):
    return json.loads((await t.call(server, "seen", {}, {}, identity=identity)).text)


NONE = {"groups": None, "trust": None}


async def test_a_flagged_server_gets_the_groups_and_the_secret(url):
    assert await seen(tools(url), identity=person("product", "engineering")) == {
        "groups": "product,engineering", "trust": SECRET}


async def test_a_service_principal_is_forwarded_like_a_person(url):
    assert await seen(tools(url), identity=person("bots", kind="service")) == {"groups": "bots", "trust": SECRET}


async def test_an_unflagged_server_gets_neither(url):
    assert await seen(tools(url), "other", person("product")) == NONE


async def test_no_principal_no_groups_or_no_secret_means_no_headers(url):
    assert await seen(tools(url)) == NONE
    assert await seen(tools(url), identity=person()) == NONE
    assert await seen(tools(url, secret=""), identity=person("product")) == NONE


async def test_the_pre_retrieval_lookup_carries_the_groups(url):
    retriever = KbRetriever(tools(url), "kb", "kb_search")
    hits = await retriever.search(["q"], 5, (), identity=person("product"))
    assert json.loads(hits[0]["title"]["en"]) == {"groups": "product", "trust": SECRET}
    hits = await retriever.search(["q"], 5, ())
    assert json.loads(hits[0]["title"]["en"]) == NONE


def test_names_with_commas_or_control_characters_are_dropped():
    assert forwarded_groups(["ok", "a,b", "bad\nname", "tab\t", "", "x" * 129, "ok", "fine"]) == "ok,fine"


def test_at_most_64_names_and_4_kib_are_sent(caplog):
    with caplog.at_level(logging.WARNING):
        assert len(forwarded_groups([f"g{i}" for i in range(100)]).split(",")) == 64
        big = forwarded_groups(["n" * 120 + str(i) for i in range(60)])
    assert len(big.encode()) <= 4096 and "left out" in caplog.text


def test_the_secret_never_reaches_a_log(caplog):
    with caplog.at_level(logging.DEBUG):
        headers = tools("http://x/mcp")._headers("kb", {}, person(*[f"g{i}" for i in range(100)]))
    assert headers["X-KB-Trust"] == SECRET and SECRET not in caplog.text


def test_the_flag_is_read_from_the_server_config(tmp_path):
    (tmp_path / "mcp-servers.yaml").write_text(
        "servers:\n  kb:\n    url: http://kb/mcp\n    forward_identity: true\n  b:\n    url: http://b/mcp\n")
    cfg = load_config(tmp_path, {})
    assert cfg.servers["kb"].forward_identity is True and cfg.servers["b"].forward_identity is False


def test_the_flag_must_be_a_boolean(tmp_path):
    (tmp_path / "mcp-servers.yaml").write_text("servers:\n  kb:\n    url: http://kb/mcp\n    forward_identity: [1]\n")
    with pytest.raises(ConfigError, match="forward_identity"):
        load_config(tmp_path, {})
