# Covers: FR-CRD-08, FR-PRT-01
"""Credential declarations in config/mcp-servers.yaml, the portal settings, and the refusals."""

import io
from pathlib import Path

import pytest

from agent_gateway.adapters.inbound.cli import parser, run
from agent_gateway.adapters.outbound.config_files import load_config
from agent_gateway.application.portal import CredentialCenter, InvalidCredential, UndeclaredCredential
from agent_gateway.config import EnvSettings
from agent_gateway.domain.errors import ConfigError
from tests.application.fakes import FakeAudit, FakeCreds

CONFIG = Path(__file__).resolve().parents[2] / "config"
BASE = {"UPSTREAM_BASE_URL": "http://llm", "DATABASE_URL": "postgresql://x", "REDIS_URL": "redis://x",
        "CREDS_KEY": "k", "APPROVAL_SECRET": "s"}


def write_config(tmp_path: Path, servers_yaml: str) -> Path:
    (tmp_path / "agents").mkdir()
    (tmp_path / "mcp-servers.yaml").write_text(servers_yaml)
    return tmp_path


def test_plain_names_and_objects_are_both_valid_declarations(tmp_path):
    cfg = load_config(write_config(tmp_path, """
servers:
  s:
    url: http://s/mcp
    credentials:
      - TOKEN
      - {name: BASE_URL, label: Address, secret: false, required: true}
    test: {tool: whoami, args: {x: 1}}
"""))
    s = cfg.servers["s"]
    assert s.credentials == ("TOKEN", "BASE_URL")
    token, base = s.declared()
    assert (token.label, token.secret, token.required) == ("TOKEN", True, False)
    assert (base.label, base.secret, base.required) == ("Address", False, True)
    assert s.test.tool == "whoami" and s.test.args == {"x": 1}


@pytest.mark.parametrize("body", [
    "credentials: [{name: lower}]", "credentials: [{name: A, colour: red}]", "credentials: [A, A]",
    "test: {tool: ''}", "test: {args: {}}"])
def test_invalid_declarations_stop_loading_naming_the_file(tmp_path, body):
    with pytest.raises(ConfigError, match="mcp-servers.yaml"):
        load_config(write_config(tmp_path, f"servers:\n  s:\n    url: http://s/mcp\n    {body}\n"))


def test_the_shipped_servers_declare_labelled_credentials_and_a_test():
    jira = load_config(CONFIG, {"DEFAULT_MODEL": "m"}).servers["agent-skills"]
    password = next(d for d in jira.declared() if d.name == "JIRA_PASSWORD")
    assert password.secret and password.required and password.label
    assert next(d for d in jira.declared() if d.name == "JIRA_BASE_URL").secret is False
    assert jira.test is not None


async def test_the_credential_center_refuses_undeclared_and_unsafe_values():
    cfg = load_config(CONFIG, {"DEFAULT_MODEL": "m"})
    creds = FakeCreds(audit=FakeAudit())
    center = CredentialCenter(cfg.servers, creds, None, creds.audit)
    with pytest.raises(UndeclaredCredential):
        await center.set("u", "agent-skills", "NOT_DECLARED", "v")
    with pytest.raises(UndeclaredCredential):
        await center.set("u", "kb", "X", "v")
    with pytest.raises(InvalidCredential):
        await center.set("u", "agent-skills", "JIRA_PASSWORD", "line\nbreak")
    assert creds.data == {}


async def test_the_cli_refuses_a_variable_no_server_declares():
    cfg = load_config(CONFIG, {"DEFAULT_MODEL": "m"})

    async def opener():
        raise AssertionError("must not reach the store")

    args = parser().parse_args(["creds", "set", "--owner", "u", "agent-skills", "NOT_DECLARED"])
    out = io.StringIO()
    # The store is opened first by run(); a refusal must come before anything is written.
    class Store:
        async def close(self):
            pass

        box = None

    async def open_store():
        return Store()

    assert await run(args, open_store, out, stdin=io.StringIO("v\n"), servers=cfg.servers) == 1


def test_portal_settings_are_validated():
    ok = {**BASE, "PORTAL_BASE_URL": "https://gw.example.org/", "PORTAL_SESSION_SECRET": "x" * 32}
    assert EnvSettings.from_env(ok).portal_base_url == "https://gw.example.org"
    assert EnvSettings.from_env({**ok, "PORTAL_BASE_URL": "http://localhost:8080"}).portal_base_url == "http://localhost:8080"
    assert EnvSettings.from_env(BASE).portal_base_url == ""
    for bad in ({"PORTAL_BASE_URL": "http://gw.example.org"}, {"PORTAL_BASE_URL": "https://gw/portal"},
                {"PORTAL_BASE_URL": "gw.example.org"}):
        with pytest.raises(ConfigError, match="PORTAL_BASE_URL"):
            EnvSettings.from_env({**ok, **bad})
    with pytest.raises(ConfigError, match="PORTAL_SESSION_SECRET"):
        EnvSettings.from_env({**ok, "PORTAL_SESSION_SECRET": "short"})
