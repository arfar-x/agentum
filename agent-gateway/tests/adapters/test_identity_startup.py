# Covers: FR-ATH-12, FR-IDP-03, FR-OPS-07
import io

import pytest

from agent_gateway.adapters.inbound.cli import parser, run
from agent_gateway.application.identity_ports import Account
from agent_gateway.config import EnvSettings
from agent_gateway.container import validate_identity
from agent_gateway.domain.errors import ConfigError
from tests.application.fakes import FakeAudit, FakeDirectory, FakeDirectorySource, FakeKeyRecords, FakeNow, FakeSecretBox

BASE = {"UPSTREAM_BASE_URL": "http://llm", "DATABASE_URL": "postgresql://x", "REDIS_URL": "redis://x",
        "CREDS_KEY": "k", "APPROVAL_SECRET": "s"}
GOOD = {**BASE, "OIDC_ISSUER": "https://kc.test/realms/r", "DIRECTORY_CLIENT_SECRET": "hush-hush"}


def env(**extra) -> EnvSettings:
    return EnvSettings.from_env({**GOOD, **extra})


def test_a_complete_environment_validates_and_has_the_documented_defaults():
    s = env()
    validate_identity(s)
    assert (s.identity_provider, s.directory_sync_seconds, s.directory_max_stale_seconds) == ("keycloak", 300, 900)


def test_each_missing_setting_stops_startup_naming_it_and_never_a_secret():
    for name in ("OIDC_ISSUER", "DIRECTORY_CLIENT_SECRET"):
        with pytest.raises(ConfigError, match=name) as err:
            validate_identity(EnvSettings.from_env({k: v for k, v in GOOD.items() if k != name}))
        assert "hush-hush" not in str(err.value)


def test_the_stale_limit_must_be_twice_the_interval_and_the_message_names_both():
    with pytest.raises(ConfigError, match="DIRECTORY_MAX_STALE_SECONDS.*DIRECTORY_SYNC_SECONDS"):
        env(DIRECTORY_SYNC_SECONDS="600", DIRECTORY_MAX_STALE_SECONDS="900")
    assert env(DIRECTORY_SYNC_SECONDS="60", DIRECTORY_MAX_STALE_SECONDS="120").directory_sync_seconds == 60


def test_an_unknown_provider_names_the_allowed_ones():
    with pytest.raises(ConfigError, match="IDENTITY_PROVIDER.*keycloak"):
        validate_identity(env(IDENTITY_PROVIDER="okta"))


def test_the_portal_needs_its_secrets_and_issuer():
    with pytest.raises(ConfigError, match="PORTAL_CLIENT_SECRET"):
        validate_identity(env(PORTAL_BASE_URL="https://gw.test", PORTAL_SESSION_SECRET="s" * 32))
    with pytest.raises(ConfigError, match="PORTAL_SESSION_SECRET"):
        env(PORTAL_BASE_URL="https://gw.test", PORTAL_CLIENT_SECRET="x", PORTAL_SESSION_SECRET="short")
    validate_identity(env(PORTAL_BASE_URL="https://gw.test", PORTAL_CLIENT_SECRET="x", PORTAL_SESSION_SECRET="s" * 32))


class Store:
    """The slice of the Postgres store the directory commands use, over fakes."""

    def __init__(self):
        self.now = FakeNow()
        self.audit = FakeAudit(self.now)
        self.directory = FakeDirectory()
        self.records = FakeKeyRecords(self.audit, self.now)
        self.box = FakeSecretBox()
        self.owners = ["sub-a", "sub-ghost"]

    async def stored_owners(self):
        return self.owners

    async def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self.records, name)


async def cli(argv, store, source=None):
    out = io.StringIO()

    async def opener():
        return store

    code = await run(parser().parse_args(argv), opener, out, source=source, now=store.now)
    return code, out.getvalue()


async def test_directory_sync_show_and_orphans():
    store = Store()
    source = FakeDirectorySource([Account("sub-a", "alice", True, ("product",), ("agent-user",)),
                                  Account("sub-b", "bob", False, (), ())])
    code, out = await cli(["directory", "sync"], store, source)
    assert code == 0 and "synced 2" in out
    store.now.advance(seconds=90)
    _, shown = await cli(["directory", "show"], store)
    assert "2 account(s), last synced 90s ago" in shown and "alice" in shown and "disabled" in shown
    _, orphans = await cli(["directory", "orphans"], store)
    assert orphans.split() == ["sub-ghost"]
    source.error = RuntimeError("down")
    code, _ = await cli(["directory", "sync"], store, source)
    assert code == 1


def test_the_audit_listing_filters_exist():
    args = parser().parse_args(["audit", "list", "--actor", "a", "--action", "auth.denied", "--since", "1d"])
    assert (args.actor, args.audit_action, args.since) == ("a", "auth.denied", "1d")
