"""The module is self-contained, wired the way the stack's modules are, and its start-up behaves."""

import asyncio
import logging
import re
from pathlib import Path
from types import SimpleNamespace

from telegram.error import Conflict
from telegram.ext import Application

from telegram_bot_gateway.adapters.inbound.telegram import bot as handlers
from telegram_bot_gateway.adapters.inbound.telegram import serve as serve_mod
from telegram_bot_gateway.config import Settings

# Covers: NFR-ARC-02, NFR-ARC-03, NFR-ARC-04, FR-LNK-08, FR-LNK-09, FR-CFG-02, FR-OPS-02, FR-OPS-05, FR-OPS-07

MODULE = Path(__file__).resolve().parent.parent
ROOT = MODULE.parent


def test_the_module_carries_its_own_files():
    for name in ("compose.yaml", "module.mk", ".env.example", "Dockerfile", "docs/spec.md", "migrations/001_init.sql",
                 "scripts/prepare.sh", "scripts/setup-db.sh", "scripts/pair.sh", "AGENTS.md", "README.md", "docs/OPERATOR_GUIDE.md"):
        assert (MODULE / name).is_file(), name


def test_the_root_reaches_it_with_an_include_a_make_include_and_a_link():
    assert "path: telegram-bot-gateway/compose.yaml" in (ROOT / "docker-compose.yml").read_text()
    assert "include telegram-bot-gateway/module.mk" in (ROOT / "Makefile").read_text()
    assert "telegram-bot-gateway/" in (ROOT / "CLAUDE.md").read_text()


def test_the_service_joins_only_its_two_networks_and_publishes_nothing():
    compose = (MODULE / "compose.yaml").read_text()
    assert "networks: [telegram-gateway, gateway-data]" in compose
    assert "backend" not in re.sub(r"#.*", "", compose) and not re.search(r"^\s*ports:", compose, re.M)


def test_the_database_role_reaches_only_its_own_database():
    sql = (MODULE / "scripts" / "setup-db.sh").read_text()
    assert "REVOKE CONNECT ON DATABASE" in sql and "FROM PUBLIC" in sql
    assert "GRANT CONNECT ON DATABASE telegram_bot TO telegram_bot" in sql


def test_no_code_imports_the_gateway_and_linking_depends_on_ports_only():
    for path in (MODULE / "src").rglob("*.py"):
        assert not re.search(r"^\s*(import|from)\s+agent_gateway", path.read_text(), re.M), path
    linking = (MODULE / "src/telegram_bot_gateway/application/linking.py").read_text()
    assert "adapters" not in linking


def test_the_template_names_no_model_organization_or_user():
    values = dict(l.split("=", 1) for l in (MODULE / ".env.example").read_text().splitlines() if "=" in l and not l.startswith("#"))
    assert values["DEFAULT_MODEL"] == "" and values["BOT_TOKEN"] == "" and values["CREDS_KEY"] == ""


def test_a_second_poller_is_reported_plainly(caplog):
    context = SimpleNamespace(error=Conflict("terminated by other getUpdates request"))
    with caplog.at_level(logging.ERROR):
        asyncio.run(handlers.on_error(None, context))
    assert "another bot instance is polling" in caplog.text


def test_the_heartbeat_file_is_written(tmp_path):
    target = tmp_path / "healthy"

    async def go():
        task = asyncio.create_task(serve_mod._heartbeat(str(target)))
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(go())
    assert float(target.read_text()) > 0


def test_polling_drops_what_queued_while_offline_and_webhook_needs_no_extra_step(monkeypatch):
    seen = {}
    monkeypatch.setattr(Application, "run_polling", lambda self, **kw: seen.update(kw))
    monkeypatch.setattr(Application, "run_webhook", lambda self, **kw: seen.update(kw, webhook=True))
    cfg = Settings("123:abc", "http://x", "m", "d", "k")
    serve_mod.serve(cfg)
    assert seen["drop_pending_updates"] is True and "webhook" not in seen
    serve_mod.serve(Settings("123:abc", "http://x", "m", "d", "k", webhook_url="https://b/h", webhook_secret="s"))
    assert seen["secret_token"] == "s" and seen["webhook"] is True and seen["drop_pending_updates"] is True
