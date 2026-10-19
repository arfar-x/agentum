# Covers: FR-CRD-05

import asyncio
import socket
import threading

import pytest
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from agent_gateway.adapters.outbound.mcp_tools import KbRetriever, McpTools
from agent_gateway.application.models import ServerInfo


def make_server():
    mcp = FastMCP("fake")

    @mcp.tool
    def whoami() -> str:
        """Report the credential header the call carried."""
        return get_http_headers(include_all=True).get("x-env-jira_user", "none")

    @mcp.tool
    def boom() -> str:
        raise ValueError("kaput")

    @mcp.tool
    def kb_search(queries: list[str], catalog_queries: list[str], limit: int = 5, types: list[str] | None = None) -> dict:
        return {"hits": [{"title": {"en": queries[0]}}], "queries": queries}

    return mcp


@pytest.fixture
def server_url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    app = make_server().http_app(path="/mcp")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        threading.Event().wait(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def tools(url):
    servers = {"skills": ServerInfo("skills", credentials=("JIRA_USER",), header_prefix="X-Env-")}
    return McpTools({"skills": (url, 10.0)}, servers)


async def test_tools_are_listed_without_credentials(server_url):
    specs = await tools(server_url).list_tools("skills")
    assert {s.name for s in specs} >= {"whoami", "boom", "kb_search"}


async def test_declared_credentials_travel_as_headers_on_the_call_and_undeclared_ones_do_not(server_url):
    t = tools(server_url)
    ok = await t.call("skills", "whoami", {}, {"JIRA_USER": "alice", "EVIL": "x"})
    assert ok.text == "alice" and not ok.is_error
    assert (await t.call("skills", "whoami", {}, {})).text == "none"


async def test_tool_failures_come_back_as_results_not_exceptions(server_url):
    res = await tools(server_url).call("skills", "boom", {}, {})
    assert res.is_error and "kaput" in res.text
    gone = await McpTools({"skills": ("http://127.0.0.1:1/mcp", 1.0)}, {"skills": ServerInfo("skills")}).call("skills", "x", {}, {})
    assert gone.is_error


async def test_a_down_server_lists_no_tools(server_url):
    t = McpTools({"skills": ("http://127.0.0.1:1/mcp", 1.0)}, {"skills": ServerInfo("skills")})
    assert await t.list_tools("skills") == []


async def test_the_catalog_retriever_returns_hits(server_url):
    servers = {"skills": ServerInfo("skills")}
    t = McpTools({"skills": (server_url, 10.0)}, servers)
    hits = await KbRetriever(t, "skills", "kb_search").search(["refunds"], 5, ())
    assert hits[0]["title"]["en"] == "refunds"
