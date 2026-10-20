import argparse
import io

import pytest

from telegram_bot_gateway import cli
from telegram_bot_gateway.application.model import Link
from telegram_bot_gateway.config import ConfigError, Settings
from tests.fakes import FakeStore, FakeVault

# Covers: FR-CFG-01, FR-CFG-03, FR-OPS-01, FR-LNK-04, FR-LNK-06, FR-LNK-09, FR-OPS-06

ENV = {"BOT_TOKEN": "t", "OPENAI_BASE_URL": "http://x/v1", "DEFAULT_MODEL": "m", "DATABASE_URL": "postgresql://d", "CREDS_KEY": "k"}


def test_settings_read_the_documented_variables_with_defaults():
    s = Settings.from_env(ENV)
    assert (s.reactions, s.show_progress, s.request_timeout, s.webhook_url) == (True, False, 600.0, "")
    s = Settings.from_env({**ENV, "REACTIONS": "false", "SHOW_PROGRESS": "1", "REQUEST_TIMEOUT": "30", "OPENAI_API_KEY": "sk"})
    assert (s.reactions, s.show_progress, s.request_timeout, s.openai_api_key) == (False, True, 30.0, "sk")


def test_a_missing_variable_is_named():
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        Settings.from_env({k: v for k, v in ENV.items() if k != "BOT_TOKEN"})
    Settings.from_env({k: v for k, v in ENV.items() if k != "BOT_TOKEN"}, need_bot=False)  # the operator CLI needs no token


def test_a_webhook_requires_its_secret():
    with pytest.raises(ConfigError, match="WEBHOOK_SECRET"):
        Settings.from_env({**ENV, "WEBHOOK_URL": "https://bot.example/hook"})
    assert Settings.from_env({**ENV, "WEBHOOK_URL": "https://b/h", "WEBHOOK_SECRET": "s"}).webhook_url == "https://b/h"


def args(*argv):
    return cli.parser().parse_args(argv)


async def notified():
    sent = []

    async def notify(chat_id, text):
        sent.append((chat_id, text))

    return sent, notify


async def test_the_operator_approves_a_code_with_the_key_on_standard_input():
    from telegram_bot_gateway.application import linking
    import time

    store, vault = FakeStore(), FakeVault()
    paired = await linking.request(store, telegram_user_id=5, username="ann", telegram_name="Ann", chat_id=55, now=time.time())
    sent, notify = await notified()
    out = io.StringIO()
    assert await cli.run(args("pair", "list"), store, vault, notify, out=out) == 0
    assert paired.code in out.getvalue() and "ann" in out.getvalue()
    out = io.StringIO()
    await cli.run(args("pair", "show", paired.code, "--field", "username"), store, vault, notify, out=out)
    assert out.getvalue().strip() == "ann"
    assert await cli.run(args("pair", "approve", paired.code), store, vault, notify, stdin=io.StringIO("agw_key\n"), out=io.StringIO()) == 0
    assert store.links[5].api_key_enc == "enc:agw_key" and sent == [(55, "You are linked. Send me a message to begin.")]


async def test_approving_needs_a_key_and_a_live_code():
    store, vault = FakeStore(), FakeVault()
    _, notify = await notified()
    assert await cli.run(args("pair", "approve", "NOPE"), store, vault, notify, stdin=io.StringIO("k\n")) == 1
    assert await cli.run(args("pair", "approve", "NOPE"), store, vault, notify, stdin=io.StringIO("")) == 2
    assert await cli.run(args("pair", "show", "NOPE"), store, vault, notify) == 1


async def test_links_and_unlink():
    store, vault = FakeStore(), FakeVault()
    await store.save_link(Link(5, "ann", "Ann", "enc:k"))
    _, notify = await notified()
    out = io.StringIO()
    await cli.run(args("links"), store, vault, notify, out=out)
    assert "ann" in out.getvalue() and "enc:k" not in out.getvalue()  # never a key
    assert await cli.run(args("unlink", "--username", "ann"), store, vault, notify, out=io.StringIO()) == 0
    assert await cli.run(args("unlink", "--username", "ann"), store, vault, notify, out=io.StringIO()) == 1
    assert await cli.run(args("unlink"), store, vault, notify) == 2
