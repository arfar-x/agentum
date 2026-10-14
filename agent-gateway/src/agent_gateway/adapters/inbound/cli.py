"""Operator commands: keys, stored credentials, config check, and the server itself."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from agent_gateway.adapters.outbound.config_files import load_config
from agent_gateway.adapters.outbound.crypto import Vault
from agent_gateway.adapters.outbound.postgres_store import PostgresStore
from agent_gateway.config import EnvSettings
from agent_gateway.domain.errors import ConfigError

StoreOpener = Callable[[], Awaitable[Any]]


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-gateway")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the HTTP server")
    sub.add_parser("check", help="validate config/ and the environment")
    key = sub.add_parser("key", help="manage API keys").add_subparsers(dest="action", required=True)
    create = key.add_parser("create")
    create.add_argument("user")
    create.add_argument("--label", default="")
    key.add_parser("list")
    revoke = key.add_parser("revoke")
    revoke.add_argument("key_id")
    creds = sub.add_parser("creds", help="manage a user's stored credentials").add_subparsers(dest="action", required=True)
    cset = creds.add_parser("set")
    cset.add_argument("user")
    cset.add_argument("server")
    cset.add_argument("var")
    cset.add_argument("--value-env", help="read the value from this environment variable instead of prompting")
    cdel = creds.add_parser("delete")
    cdel.add_argument("user")
    cdel.add_argument("server")
    return p


async def run(args: argparse.Namespace, opener: StoreOpener, out=sys.stdout) -> int:
    store = await opener()
    try:
        if args.command == "key":
            if args.action == "create":
                secret = await store.create_key(args.user, args.label)
                print(f"{secret}\n(shown once; store it now)", file=out)
            elif args.action == "list":
                for k in await store.list_keys():
                    state = "revoked" if k["revoked_at"] else "active"
                    print(f"{k['id']}  {k['user_id']:<24} {state:<8} {k['label']}", file=out)
            elif args.action == "revoke":
                ok = await store.revoke_key(args.key_id)
                print("revoked" if ok else "no active key with that id", file=out)
                return 0 if ok else 1
        elif args.command == "creds":
            if args.action == "set":
                value = os.environ[args.value_env] if args.value_env else getpass.getpass(f"{args.var}: ")
                await store.set_credential(args.user, args.server, args.var, value)
                print(f"stored {args.var} for {args.user}", file=out)
            elif args.action == "delete":
                print(f"deleted {await store.delete_credentials(args.user, args.server)} value(s)", file=out)
    finally:
        await store.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        env = EnvSettings.from_env()
        if args.command == "check":
            cfg = load_config(env.config_dir)
            print(f"ok: {len(cfg.agents)} agent(s), {len(cfg.servers)} tool server(s)")
            return 0
        if args.command == "serve":
            from agent_gateway.adapters.inbound.serve import serve

            asyncio.run(serve(env))
            return 0

        async def opener() -> PostgresStore:
            return await PostgresStore.open(env.database_url, Vault(env.creds_key), env.migrations_dir)

        return asyncio.run(run(args, opener))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
