"""Operator commands: serve the bot, approve links, list and remove them."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from telegram_bot_gateway.application import linking
from telegram_bot_gateway.config import ConfigError, Settings
from telegram_bot_gateway.domain import pairing


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="telegram-bot-gateway")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the bot")
    pair = sub.add_parser("pair", help="pairing codes").add_subparsers(dest="action", required=True)
    pair.add_parser("list", help="open codes and who asked for them")
    show = pair.add_parser("show", help="one code's details")
    show.add_argument("code")
    show.add_argument("--field", choices=["username", "telegram_name", "telegram_user_id"])
    approve = pair.add_parser("approve", help="link the user behind a code; the API key is read from standard input")
    approve.add_argument("code")
    sub.add_parser("links", help="linked users")
    unlink = sub.add_parser("unlink", help="remove a link and the user's sessions")
    unlink.add_argument("--telegram-id", type=int)
    unlink.add_argument("--username")
    return p


async def run(args: argparse.Namespace, store: Any, vault: Any, notify: Callable[[int, str], Awaitable[None]], *, stdin=sys.stdin, out=sys.stdout) -> int:
    now = time.time()
    if args.command == "pair":
        if args.action == "list":
            open_codes = await store.list_pairings(since=now - pairing.TTL_SECONDS)
            for r in open_codes:
                print(f"{r.code}  {r.username:<24} {r.telegram_name} (telegram id {r.telegram_user_id})", file=out)
            if not open_codes:
                print("no open codes", file=out)
        elif args.action == "show":
            found = await store.get_pairing(pairing.normalise(args.code))
            if found is None or now - found.created_at >= pairing.TTL_SECONDS:
                print("unknown or expired code", file=sys.stderr)
                return 1
            print(getattr(found, args.field) if args.field else
                  f"{found.username} / {found.telegram_name} / telegram id {found.telegram_user_id}", file=out)
        elif args.action == "approve":
            api_key = stdin.readline().strip()
            if not api_key:
                print("expected the API key on standard input", file=sys.stderr)
                return 2
            try:
                found = await linking.approve(store, vault, args.code, api_key, now=now)
            except LookupError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(f"linked {found.username} (telegram id {found.telegram_user_id})", file=out)
            try:
                await notify(found.chat_id, "You are linked. Send me a message to begin.")
            except Exception:
                print("linked, but the user could not be notified", file=sys.stderr)
    elif args.command == "links":
        for link in await store.list_links():
            print(f"{link.telegram_user_id:<14} {link.username:<24} {link.telegram_name}", file=out)
    elif args.command == "unlink":
        if args.telegram_id is None and not args.username:
            print("give --telegram-id or --username", file=sys.stderr)
            return 2
        removed = await linking.unlink(store, telegram_user_id=args.telegram_id, username=args.username)
        print(f"removed {removed} link(s)", file=out)
        return 0 if removed else 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            import logging

            from telegram_bot_gateway.adapters.inbound.telegram.serve import serve

            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
            for noisy in ("httpx", "httpcore"):  # their request lines contain the bot token
                logging.getLogger(noisy).setLevel(logging.WARNING)

            serve(Settings.from_env())
            return 0
        config = Settings.from_env(need_bot=False)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    async def go() -> int:
        import httpx

        from telegram_bot_gateway.adapters.outbound.crypto import Vault
        from telegram_bot_gateway.adapters.outbound.postgres import PostgresStore

        store = await PostgresStore.open(config.database_url, config.migrations_dir)

        async def notify(chat_id: int, text: str) -> None:
            if not config.bot_token:
                return
            async with httpx.AsyncClient(timeout=10) as client:
                (await client.post(f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
                                   json={"chat_id": chat_id, "text": text})).raise_for_status()

        try:
            return await run(args, store, Vault(config.creds_key), notify)
        finally:
            await store.close()

    return asyncio.run(go())


if __name__ == "__main__":
    raise SystemExit(main())
