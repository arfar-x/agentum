# Covers: FR-CFG-04

import pytest

from agent_gateway.adapters.inbound.cli import parse_since, parser
from agent_gateway.config import EnvSettings
from agent_gateway.domain.errors import ConfigError
from tests.application.fakes import FakeNow

BASE = {"UPSTREAM_BASE_URL": "http://llm", "DATABASE_URL": "postgresql://x", "REDIS_URL": "redis://x",
        "CREDS_KEY": "k", "APPROVAL_SECRET": "s"}


def settings(**extra):
    return EnvSettings.from_env({**BASE, **extra})


def test_issuing_keys_is_off_by_default_and_audit_is_kept_a_year():
    s = settings()
    assert s.issue_keys is False and s.audit_retention_days == 365 and s.upstream_api_key == ""


def test_the_flag_and_the_retention_are_parsed_once_with_the_documented_defaults():
    assert settings(GATEWAY_ISSUE_KEYS="true").issue_keys is True
    assert settings(GATEWAY_ISSUE_KEYS="1").issue_keys is True
    assert settings(GATEWAY_ISSUE_KEYS="false").issue_keys is False
    assert settings(AUDIT_RETENTION_DAYS="30").audit_retention_days == 30
    assert settings(UPSTREAM_API_KEY="sk-shared").upstream_api_key == "sk-shared"


def test_a_bad_value_stops_startup_naming_the_variable_but_not_its_value():
    with pytest.raises(ConfigError, match="GATEWAY_ISSUE_KEYS"):
        settings(GATEWAY_ISSUE_KEYS="maybe-sk-secret")
    for bad in ("0", "-5", "soon"):
        with pytest.raises(ConfigError, match="AUDIT_RETENTION_DAYS"):
            settings(AUDIT_RETENTION_DAYS=bad)
    with pytest.raises(ConfigError) as err:
        settings(GATEWAY_ISSUE_KEYS="maybe-sk-secret")
    assert "sk-secret" not in str(err.value)


def test_the_cli_takes_secrets_from_standard_input_never_from_an_argument():
    args = parser().parse_args(["key", "register-llm", "--owner", "sub-1"])
    assert args.owner == "sub-1" and not hasattr(args, "secret") and not hasattr(args, "key")
    with pytest.raises(SystemExit):
        parser().parse_args(["key", "register-llm"])                 # the owner is required
    assert parser().parse_args(["key", "create", "--owner", "o", "--read-only"]).read_only is True
    assert parser().parse_args(["creds", "delete", "--owner", "o", "srv", "--var", "V"]).var == "V"


def test_since_accepts_an_age_or_an_iso_time():
    now = FakeNow()()
    assert (now - parse_since("2h", now)).total_seconds() == 7200
    assert (now - parse_since("7d", now)).days == 7
    assert parse_since("2026-01-01T00:00:00", now).tzinfo is not None
    with pytest.raises(ConfigError, match="--since"):
        parse_since("yesterday", now)
