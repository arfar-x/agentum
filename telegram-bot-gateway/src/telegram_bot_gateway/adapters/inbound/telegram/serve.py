"""Starting the bot: wiring, polling or webhook, and the container's health file."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from telegram import Update
from telegram.ext import Application

from telegram_bot_gateway.adapters.inbound.telegram import bot
from telegram_bot_gateway.adapters.inbound.telegram.runtime import Runtime
from telegram_bot_gateway.adapters.inbound.telegram.sender import Sender
from telegram_bot_gateway.adapters.outbound.crypto import Vault
from telegram_bot_gateway.adapters.outbound.openai import OpenAIChat
from telegram_bot_gateway.adapters.outbound.postgres import PostgresStore
from telegram_bot_gateway.config import Settings

log = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 30


def serve(config: Settings) -> None:
    async def post_init(app: Application) -> None:
        store = await PostgresStore.open(config.database_url, config.migrations_dir)
        llm = OpenAIChat(config.openai_base_url, config.request_timeout)
        vault = Vault(config.creds_key)
        sender = Sender(app.bot, store)
        runtime = Runtime(bot=app.bot, store=store, llm=llm, vault=vault, sender=sender, config=config)
        app.bot_data["deps"] = bot.Deps(config, store, llm, vault, sender, runtime)
        await app.bot.set_my_commands(bot.MENU)
        app.bot_data["heartbeat"] = asyncio.create_task(_heartbeat(config.health_file))
        log.info("ready as @%s", app.bot.username)

    async def post_shutdown(app: Application) -> None:
        deps = app.bot_data.get("deps")
        if deps:
            await deps.llm.close()
            await deps.store.close()

    app = (
        Application.builder().token(config.bot_token).concurrent_updates(True)
        .post_init(post_init).post_shutdown(post_shutdown).build()
    )
    bot.register(app)
    allowed = [Update.MESSAGE, Update.CALLBACK_QUERY]
    if config.webhook_url:
        app.run_webhook(
            listen="0.0.0.0", port=config.webhook_port, webhook_url=config.webhook_url,
            secret_token=config.webhook_secret, drop_pending_updates=True, allowed_updates=allowed,
        )
    else:
        app.run_polling(drop_pending_updates=True, allowed_updates=allowed)


async def _heartbeat(path: str) -> None:
    while True:
        try:
            Path(path).write_text(str(time.time()))
        except OSError:
            log.warning("cannot write the health file %s", path)
        await asyncio.sleep(HEARTBEAT_SECONDS)
