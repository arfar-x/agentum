"""Operator commands: keys, stored credentials, audit, owner purge, config check, and the server itself.

Owners are identity-provider account ids (``sub``). Secrets are read from standard input (or the
environment), never from an argument.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, TextIO

from agent_gateway.adapters.outbound.config_files import load_config
from agent_gateway.adapters.outbound.crypto import Vault
from agent_gateway.adapters.outbound.postgres_store import PostgresStore, read_directory_names
from agent_gateway.application.directory_sync import DirectorySync
from agent_gateway.application.key_service import KeyManager
from agent_gateway.application.models import ServerInfo
from agent_gateway.application.portal import UndeclaredCredential, check_declared
from agent_gateway.config import EnvSettings
from agent_gateway.domain.access import access_warnings
from agent_gateway.domain.errors import ConfigError
from agent_gateway.domain.identity import DuplicateKey, KeyInfo, KeyIssuingDisabled, KeyScope

StoreOpener = Callable[[], Awaitable[Any]]

LLM_SERVER, LLM_VAR = "llm", "API_KEY"        # how a person's model key is shown beside tool credentials
DEFAULT_KEY_DAYS = 90


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-gateway")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the HTTP server")
    sub.add_parser("check", help="validate config/ and the environment")

    key = sub.add_parser("key", help="manage keys").add_subparsers(dest="action", required=True)
    reg = key.add_parser("register-llm", help="register a person's own model key (read from standard input)")
    reg.add_argument("--owner", required=True)
    reg.add_argument("--label", default="")
    create = key.add_parser("create", help="issue a gateway key (only when GATEWAY_ISSUE_KEYS is on)")
    create.add_argument("--owner", required=True)
    create.add_argument("--label", default="")
    create.add_argument("--expires-days", type=int, default=DEFAULT_KEY_DAYS)
    create.add_argument("--agents", default="", help="comma-separated agent ids the key may use (default: all)")
    create.add_argument("--read-only", action="store_true", help="the key cannot use approval-gated write tools")
    klist = key.add_parser("list", help="list a person's keys (never a secret)")
    klist.add_argument("--owner", required=True)
    revoke = key.add_parser("revoke", help="revoke any key")
    revoke.add_argument("key_id")

    creds = sub.add_parser("creds", help="manage a person's stored credentials").add_subparsers(dest="action", required=True)
    clist = creds.add_parser("list", help="list the names (never values) a person has stored")
    clist.add_argument("--owner", required=True)
    cset = creds.add_parser("set", help="store one variable; the value comes from standard input")
    cset.add_argument("--owner", required=True)
    cset.add_argument("server")
    cset.add_argument("var")
    cset.add_argument("--value-env", help="read the value from this environment variable instead of standard input")
    cdel = creds.add_parser("delete", help="delete one variable (--var) or every variable of a server")
    cdel.add_argument("--owner", required=True)
    cdel.add_argument("server")
    cdel.add_argument("--var")

    audit = sub.add_parser("audit", help="read the audit log").add_subparsers(dest="action", required=True)
    alist = audit.add_parser("list")
    alist.add_argument("--limit", type=int, default=50)
    alist.add_argument("--actor")
    alist.add_argument("--action", dest="audit_action")
    alist.add_argument("--since", help="an ISO 8601 time, or an age such as 30m, 12h, 7d")

    directory = sub.add_parser("directory", help="the copy of the identity provider's people").add_subparsers(
        dest="action", required=True)
    directory.add_parser("sync", help="refresh the copy now")
    directory.add_parser("show", help="show the copy and its age")
    directory.add_parser("orphans", help="owners with stored data but no directory entry")

    owner = sub.add_parser("owner", help="owner-wide operations").add_subparsers(dest="action", required=True)
    purge = owner.add_parser("purge", help="delete everything stored for one owner")
    purge.add_argument("--owner", required=True)
    purge.add_argument("--yes", action="store_true", help="confirm the deletion")
    return p


def parse_since(text: str, now: datetime) -> datetime:
    """An ISO 8601 time (UTC when it has no zone) or an age like ``90s``, ``30m``, ``12h``, ``7d``."""
    m = re.fullmatch(r"(\d+)([smhd])", text.strip())
    if m:
        unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
        return now - timedelta(**{unit: int(m.group(1))})
    try:
        when = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ConfigError(f"--since: {text!r} is neither an ISO 8601 time nor an age like 12h or 7d") from None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _read_secret(prompt: str, stdin: TextIO) -> str:
    if stdin.isatty():
        return getpass.getpass(prompt)
    return stdin.read().strip()


def _key_line(k: KeyInfo, now: datetime) -> str:
    if k.revoked_at:
        state = "revoked"
    elif k.expires_at and k.expires_at <= now:
        state = "expired"
    else:
        state = "active"
    scope = "all agents" if not k.scope.agents else ",".join(k.scope.agents)
    scope += "" if k.scope.write else " (read-only)"
    when = lambda d: d.strftime("%Y-%m-%d %H:%M") if d else "-"          # noqa: E731
    return (f"{k.id}  {k.kind:<8} {state:<8} ...{k.prefix:<8} {k.label or '-':<16} scope: {scope}  "
            f"created {when(k.created_at)}  expires {when(k.expires_at)}  last used {when(k.last_used_at)}")


async def run(args: argparse.Namespace, opener: StoreOpener, out=sys.stdout, *, issue_keys: bool = False,
              stdin: TextIO | None = None, now: Callable[[], datetime] | None = None,
              source: Any = None, servers: Mapping[str, ServerInfo] | None = None) -> int:
    stdin = stdin or sys.stdin
    clock = now or (lambda: datetime.now(timezone.utc))
    store = await opener()
    keys = KeyManager(store, store.box, issue_keys=issue_keys, now=clock)
    try:
        if args.command == "key":
            if args.action == "register-llm":
                secret = _read_secret("model key: ", stdin)
                if not secret:
                    print("error: no key on standard input", file=sys.stderr)
                    return 1
                try:
                    info = await keys.register_upstream(args.owner, args.label, secret, actor="cli")
                except DuplicateKey:
                    print("error: that key is already registered", file=sys.stderr)
                    return 1
                print(f"registered {info.id} (ends in {info.prefix}); any previous key of {args.owner} is revoked", file=out)
            elif args.action == "create":
                scope = KeyScope(tuple(a for a in (s.strip() for s in args.agents.split(",")) if a), not args.read_only)
                expires = clock() + timedelta(days=args.expires_days)
                try:
                    secret, info = await keys.issue(args.owner, args.label, scope, expires, actor="cli")
                except KeyIssuingDisabled:
                    print("error: issuing gateway keys is disabled; set GATEWAY_ISSUE_KEYS=true to allow it", file=sys.stderr)
                    return 1
                print(f"{secret}\n(shown once; store it now; id {info.id})", file=out)
            elif args.action == "list":
                for k in await keys.list_for(args.owner):
                    print(_key_line(k, clock()), file=out)
            elif args.action == "revoke":
                ok = await keys.revoke(None, args.key_id, actor="cli")
                print("revoked" if ok else "no active key with that id", file=out)
                return 0 if ok else 1
        elif args.command == "creds":
            if args.action == "list":
                if any(k.kind == "upstream" and k.revoked_at is None for k in await keys.list_for(args.owner)):
                    print(f"{LLM_SERVER}  {LLM_VAR}", file=out)
                for server, var in await store.list_credentials(args.owner):
                    print(f"{server}  {var}", file=out)
            elif args.action == "set":
                if servers is not None:
                    try:
                        check_declared(servers, args.server, args.var)
                    except UndeclaredCredential:
                        print(f"error: {args.server} does not declare {args.var} (see config/mcp-servers.yaml)",
                              file=sys.stderr)
                        return 1
                value = os.environ[args.value_env] if args.value_env else _read_secret(f"{args.var}: ", stdin)
                await store.set_credential(args.owner, args.server, args.var, value)
                print(f"stored {args.var} for {args.owner}", file=out)
            elif args.action == "delete":
                if args.var:
                    ok = await store.delete_var(args.owner, args.server, args.var)
                    print("deleted 1 value(s)" if ok else "deleted 0 value(s)", file=out)
                else:
                    print(f"deleted {await store.delete_credentials(args.owner, args.server)} value(s)", file=out)
        elif args.command == "audit":
            since = parse_since(args.since, clock()) if args.since else None
            for e in await store.audit.recent(args.limit, actor=args.actor, action=args.audit_action, since=since):
                again = f" x{e.count}" if e.count > 1 else ""
                print(f"{e.ts.strftime('%Y-%m-%d %H:%M:%S')}  {e.actor:<24} {e.action:<24} {e.target}{again}  {e.detail or ''}", file=out)
        elif args.command == "directory":
            if args.action == "sync":
                if source is None:
                    print("error: no identity provider is configured", file=sys.stderr)
                    return 1
                sync = DirectorySync(source, store.directory, store.audit, now=clock)
                try:
                    await sync.refresh()
                except Exception as exc:
                    print(f"error: directory sync failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                    return 1
                print(f"synced {len(await store.directory.list_all())} account(s)", file=out)
            elif args.action == "show":
                entries = await store.directory.list_all()
                newest = await store.directory.newest_sync()
                age = f"{int((clock() - newest).total_seconds())}s ago" if newest else "never"
                print(f"{len(entries)} account(s), last synced {age}", file=out)
                for e in entries:
                    state = "enabled" if e.enabled else "disabled"
                    print(f"{e.user_id}  {e.username:<24} {e.kind:<7} {state:<8} groups: {','.join(e.groups) or '-'}  "
                          f"roles: {','.join(e.roles) or '-'}", file=out)
            elif args.action == "orphans":
                known = {e.user_id for e in await store.directory.list_all()}
                for owner in await store.stored_owners():
                    if owner not in known:
                        print(owner, file=out)
        elif args.command == "owner":
            if not args.yes:
                print("error: this deletes every key, credential, thread, pending turn and response of the owner; "
                      "repeat with --yes", file=sys.stderr)
                return 1
            counts = await store.purge(args.owner)
            print("purged " + ", ".join(f"{n} {name}" for name, n in counts.items()), file=out)
    finally:
        await store.close()
    return 0


def _access_warnings(cfg, env) -> list[str]:
    """Access-rule warnings; the typo check runs only when the directory copy can be read and is not empty."""
    groups = roles = None
    try:
        names = asyncio.run(read_directory_names(env.database_url))
        if names[0] or names[1]:
            groups, roles = names
    except Exception:  # noqa: BLE001 -- no database from here: skip the typo check, keep the rest
        pass
    return access_warnings(cfg.agents.values(), groups, roles)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        env = EnvSettings.from_env()
        if args.command == "check":
            cfg = load_config(env.config_dir)
            from agent_gateway.container import validate_identity

            validate_identity(env)
            for s in cfg.servers.values():
                if s.forward_identity and not env.kb_trust_secret:
                    print(f"warning: server {s.name!r} has forward_identity but KB_TRUST_SECRET is empty: "
                          "no identity will be forwarded", file=sys.stderr)
            print(f"ok: {len(cfg.agents)} agent(s), {len(cfg.servers)} tool server(s)")
            for line in _access_warnings(cfg, env):
                print(f"warning: {line}")
            return 0
        if args.command == "serve":
            from agent_gateway.adapters.inbound.serve import serve

            asyncio.run(serve(env))
            return 0

        async def opener() -> PostgresStore:
            return await PostgresStore.open(env.database_url, Vault(env.creds_key), env.migrations_dir)

        source, close = None, None
        servers = None
        if args.command == "creds" and args.action == "set":
            servers = load_config(env.config_dir).servers      # a variable no server declares is refused
        if args.command == "directory" and args.action == "sync":
            from agent_gateway.container import build_directory_source

            source, close = build_directory_source(env)

        async def go() -> int:
            try:
                return await run(args, opener, issue_keys=env.issue_keys, source=source, servers=servers)
            finally:
                if close:
                    await close()

        return asyncio.run(go())
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
