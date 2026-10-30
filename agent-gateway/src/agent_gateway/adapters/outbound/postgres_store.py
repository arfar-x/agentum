"""Postgres: keys, encrypted credentials, the directory copy, the audit log, threads and paused turns."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from agent_gateway.adapters.outbound.crypto import Vault
from agent_gateway.application.audit import clean_detail, key_added_event, key_revoked_event
from agent_gateway.application.identity_ports import Account, NewKey
from agent_gateway.application.models import PendingTurn, Thread
from agent_gateway.domain.identity import (
    AuditEvent,
    DirectoryEntry,
    KeyInfo,
    clean_groups,
    clean_roles,
    scope_from_json,
    scope_to_json,
)
from agent_gateway.domain.messages import ChatMessage, ToolCall


def _ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


_KEY_COLUMNS = "id::text, owner, kind, label, prefix, scope, created_at, expires_at, revoked_at, last_used_at"


def _key_info(row: tuple) -> KeyInfo:
    return KeyInfo(id=row[0], owner=row[1], kind=row[2], label=row[3], prefix=row[4],
                   scope=scope_from_json(row[5]), created_at=row[6], expires_at=row[7],
                   revoked_at=row[8], last_used_at=row[9])


def _cred_detail(actor: str, owner: str) -> dict[str, Any]:
    return {} if actor == owner else {"owner": owner}


async def _audit(conn: Any, actor: str, action: str, target: str = "", detail: dict | None = None) -> None:
    """Write one audit row on an open connection, so a change and its entry share a transaction.
    An identical event within a minute is folded into the earlier row."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("\x1f".join((actor, action, target)),))
    folded = await (await conn.execute(
        "UPDATE audit_log SET count = count + 1, last_ts = now() WHERE id = ("
        "  SELECT id FROM audit_log WHERE actor=%s AND action=%s AND target=%s "
        "  AND last_ts > now() - interval '60 seconds' ORDER BY id DESC LIMIT 1) RETURNING id",
        (actor, action, target))).fetchone()
    if folded is None:
        await conn.execute(
            "INSERT INTO audit_log (actor, action, target, detail) VALUES (%s,%s,%s,%s)",
            (actor, action, target, Jsonb(clean_detail(detail))))


class PostgresAudit:
    """Implements ``AuditLog``."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def record(self, actor: str, action: str, target: str = "", detail: dict | None = None) -> None:
        async with self._pool.connection() as conn:
            await _audit(conn, actor, action, target, detail)

    async def recent(self, limit: int = 50, *, actor: str | None = None, action: str | None = None,
                     since: datetime | None = None) -> list[AuditEvent]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT id, ts, last_ts, actor, action, target, detail, count FROM audit_log "
                "WHERE (%s::text IS NULL OR actor=%s) AND (%s::text IS NULL OR action=%s) "
                "AND (%s::timestamptz IS NULL OR last_ts >= %s) ORDER BY id DESC LIMIT %s",
                (actor, actor, action, action, since, since, limit))).fetchall()
        return [AuditEvent(*r) for r in rows]

    async def prune(self, older_than: datetime) -> int:
        async with self._pool.connection() as conn:
            return (await conn.execute("DELETE FROM audit_log WHERE last_ts < %s", (older_than,))).rowcount


class PostgresDirectory:
    """Implements ``DirectoryStore``."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def lookup(self, user_id: str) -> DirectoryEntry | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT user_id, username, groups, roles, enabled, synced_at, kind "
                "FROM directory_snapshot WHERE user_id=%s", (user_id,))).fetchone()
        if not row:
            return None
        return DirectoryEntry(row[0], row[1], tuple(row[2]), tuple(row[3]), row[4], row[5], row[6])

    async def replace_all(self, accounts: Sequence[Account], synced_at: datetime) -> None:
        async with self._pool.connection() as conn:
            for a in accounts:
                await conn.execute(
                    "INSERT INTO directory_snapshot (user_id, username, kind, groups, roles, enabled, synced_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET username=EXCLUDED.username, "
                    "kind=EXCLUDED.kind, groups=EXCLUDED.groups, roles=EXCLUDED.roles, enabled=EXCLUDED.enabled, "
                    "synced_at=EXCLUDED.synced_at",
                    (a.sub, a.username, a.kind, list(clean_groups(a.groups)), list(clean_roles(a.roles)),
                     a.enabled, synced_at))
            await conn.execute("DELETE FROM directory_snapshot WHERE user_id <> ALL(%s::text[])",
                               ([a.sub for a in accounts],))

    async def newest_sync(self) -> datetime | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute("SELECT max(synced_at) FROM directory_snapshot")).fetchone()
        return row[0]

    async def list_all(self) -> list[DirectoryEntry]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT user_id, username, groups, roles, enabled, synced_at, kind "
                "FROM directory_snapshot ORDER BY username")).fetchall()
        return [DirectoryEntry(r[0], r[1], tuple(r[2]), tuple(r[3]), r[4], r[5], r[6]) for r in rows]


class PostgresStore:
    """Implements ``ConversationStore``, ``CredentialStore``, ``KeyRecords`` and ``OwnerPurge``;
    ``audit`` and ``directory`` are the ``AuditLog`` and ``DirectoryStore`` over the same pool."""

    def __init__(self, pool: AsyncConnectionPool, vault: Vault) -> None:
        self._pool, self._vault = pool, vault
        self.audit = PostgresAudit(pool)
        self.directory = PostgresDirectory(pool)

    @property
    def box(self) -> Vault:
        """The ``SecretBox`` this store encrypts with."""
        return self._vault

    @classmethod
    async def open(cls, dsn: str, vault: Vault, migrations: Path) -> PostgresStore:
        pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=10)
        await pool.open()
        store = cls(pool, vault)
        await store.migrate(migrations)
        return store

    async def close(self) -> None:
        await self._pool.close()

    async def migrate(self, directory: Path) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
            for path in sorted(directory.glob("*.sql")):
                done = await (await conn.execute("SELECT 1 FROM schema_migrations WHERE name=%s", (path.name,))).fetchone()
                if not done:
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))

    # ---- KeyRecords

    async def add_key(self, new: NewKey, *, actor: str) -> tuple[KeyInfo, str | None]:
        replaced: str | None = None
        async with self._pool.connection() as conn:
            if new.kind == "upstream":
                row = await (await conn.execute(
                    "UPDATE api_keys SET revoked_at = now(), secret_enc = NULL "
                    "WHERE owner=%s AND kind='upstream' AND revoked_at IS NULL RETURNING id::text",
                    (new.owner,))).fetchone()
                replaced = row[0] if row else None
            row = await (await conn.execute(
                "INSERT INTO api_keys (owner, kind, label, prefix, key_hash, secret_enc, scope, expires_at) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {_KEY_COLUMNS}",
                (new.owner, new.kind, new.label, new.prefix, new.key_hash, new.secret_enc,
                 Jsonb(scope_to_json(new.scope)), new.expires_at))).fetchone()
            info = _key_info(row)
            await _audit(conn, actor, *key_added_event(info, replaced))
        return info, replaced

    async def find_active(self, key_hash: str, now: datetime) -> list[KeyInfo]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                f"SELECT {_KEY_COLUMNS} FROM api_keys WHERE key_hash=%s AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > %s) ORDER BY created_at, id", (key_hash, now))).fetchall()
        return [_key_info(r) for r in rows]

    async def touch(self, key_id: str, now: datetime) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("UPDATE api_keys SET last_used_at=%s WHERE id=%s::uuid", (now, key_id))

    async def upstream_secret(self, owner: str) -> bytes | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT secret_enc FROM api_keys WHERE owner=%s AND kind='upstream' AND revoked_at IS NULL",
                (owner,))).fetchone()
        return bytes(row[0]) if row else None

    async def list_for(self, owner: str) -> list[KeyInfo]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                f"SELECT {_KEY_COLUMNS} FROM api_keys WHERE owner=%s ORDER BY created_at, id", (owner,))).fetchall()
        return [_key_info(r) for r in rows]

    async def revoke(self, owner: str | None, key_id: str, *, actor: str, by: str) -> bool:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "UPDATE api_keys SET revoked_at = now(), "
                "secret_enc = CASE WHEN kind='upstream' THEN NULL ELSE secret_enc END "
                f"WHERE id=%s::uuid AND revoked_at IS NULL AND (%s::text IS NULL OR owner=%s) RETURNING {_KEY_COLUMNS}",
                (key_id, owner, owner))).fetchone()
            if row:
                await _audit(conn, actor, *key_revoked_event(_key_info(row), by))
        return row is not None

    # ---- CredentialStore

    async def get(self, user_id: str, server: str) -> dict[str, str]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT var, ciphertext FROM user_credentials WHERE user_id=%s AND server=%s", (user_id, server))).fetchall()
        return {var: self._vault.decrypt(ct).decode() for var, ct in rows}

    async def list_vars(self, user: str, server: str) -> list[str]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT var FROM user_credentials WHERE user_id=%s AND server=%s ORDER BY var", (user, server))).fetchall()
        return [r[0] for r in rows]

    async def list_credentials(self, user: str) -> list[tuple[str, str]]:
        """(server, variable) for every stored value of one person; names only."""
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT server, var FROM user_credentials WHERE user_id=%s ORDER BY server, var", (user,))).fetchall()
        return [(r[0], r[1]) for r in rows]

    async def set_credential(self, user_id: str, server: str, var: str, value: str, *, actor: str = "cli") -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO user_credentials (user_id, server, var, ciphertext) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (user_id, server, var) DO UPDATE SET ciphertext=EXCLUDED.ciphertext, updated_at=now()",
                (user_id, server, var, self._vault.encrypt(value.encode())))
            await _audit(conn, actor, "cred.set", f"{server}/{var}", _cred_detail(actor, user_id))

    async def delete_var(self, user: str, server: str, var: str, *, actor: str = "cli") -> bool:
        return bool(await self._delete_creds(user, server, var, actor))

    async def delete_credentials(self, user_id: str, server: str, *, actor: str = "cli") -> int:
        """Every stored variable of one server; returns how many."""
        return len(await self._delete_creds(user_id, server, None, actor))

    async def _delete_creds(self, user: str, server: str, var: str | None, actor: str) -> list[str]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "DELETE FROM user_credentials WHERE user_id=%s AND server=%s AND (%s::text IS NULL OR var=%s) "
                "RETURNING var", (user, server, var, var))).fetchall()
            for (name,) in rows:
                await _audit(conn, actor, "cred.delete", f"{server}/{name}", _cred_detail(actor, user))
        return [r[0] for r in rows]

    async def stored_owners(self) -> list[str]:
        """Every owner id that has keys, credentials, threads, pending turns or responses."""
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT owner FROM api_keys UNION SELECT user_id FROM user_credentials "
                "UNION SELECT user_id FROM threads UNION SELECT user_id FROM pending_turns "
                "UNION SELECT user_id FROM responses ORDER BY 1")).fetchall()
        return [r[0] for r in rows]

    # ---- OwnerPurge

    async def purge(self, owner: str) -> dict[str, int]:
        statements = {
            "keys": "DELETE FROM api_keys WHERE owner=%s",
            "credentials": "DELETE FROM user_credentials WHERE user_id=%s",
            "threads": "DELETE FROM threads WHERE user_id=%s",
            "pending_turns": "DELETE FROM pending_turns WHERE user_id=%s",
            "responses": "DELETE FROM responses WHERE user_id=%s",
        }
        counts: dict[str, int] = {}
        async with self._pool.connection() as conn:
            for name, sql in statements.items():
                counts[name] = (await conn.execute(sql, (owner,))).rowcount
            await _audit(conn, "cli", "owner.purge", owner, {"counts": counts})
        return counts

    # ---- ConversationStore

    async def get_thread(self, user_id: str, history_hash: str) -> Thread | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT active_agent, client_opt_in, trace FROM threads WHERE user_id=%s AND history_hash=%s",
                (user_id, history_hash))).fetchone()
        if not row:
            return None
        trace = json.loads(self._vault.decrypt(row[2])) if row[2] else []
        return Thread(user_id, history_hash, row[0], row[1], trace)

    async def put_thread(self, thread: Thread) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO threads (user_id, history_hash, active_agent, client_opt_in, trace) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (user_id, history_hash) DO UPDATE SET active_agent=EXCLUDED.active_agent, "
                "client_opt_in=EXCLUDED.client_opt_in, trace=EXCLUDED.trace, updated_at=now()",
                (thread.user_id, thread.history_hash, thread.active_agent, thread.client_opt_in,
                 self._vault.encrypt(json.dumps(thread.trace, ensure_ascii=False).encode()) if thread.trace else None))

    def _pack(self, pending: PendingTurn) -> bytes:
        body = {
            "hidden": [m.model_dump(mode="json", exclude_none=True) for m in pending.hidden_messages],
            "queue": [c.model_dump(mode="json") for c in pending.queue],
            "approval_call": pending.approval_call.model_dump(mode="json") if pending.approval_call else None,
        }
        return self._vault.encrypt(json.dumps(body).encode())

    def _unpack(self, user_id: str, kind: str, agent_id: str, payload: bytes, code_hash: str | None) -> PendingTurn:
        body = json.loads(self._vault.decrypt(payload))
        return PendingTurn(
            user_id=user_id, call_ids=[], kind=kind, agent_id=agent_id,  # type: ignore[arg-type]
            hidden_messages=[ChatMessage.model_validate(m) for m in body["hidden"]],
            queue=[ToolCall.model_validate(c) for c in body["queue"]],
            approval_call=ToolCall.model_validate(body["approval_call"]) if body["approval_call"] else None,
            code_hash=code_hash)

    async def put_pending(self, pending: PendingTurn) -> None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "INSERT INTO pending_turns (user_id, kind, agent_id, payload, code_hash, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (pending.user_id, pending.kind, pending.agent_id, self._pack(pending), pending.code_hash,
                 _ts(pending.expires_at)))).fetchone()
            for call_id in pending.call_ids:
                await conn.execute("INSERT INTO pending_calls (call_id, pending_id) VALUES (%s,%s)", (call_id, row[0]))

    async def take_pending(self, user_id: str, call_id: str, now: float) -> PendingTurn | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "DELETE FROM pending_turns WHERE user_id=%s AND expires_at > %s "
                "AND id IN (SELECT pending_id FROM pending_calls WHERE call_id=%s) "
                "RETURNING kind, agent_id, payload, code_hash", (user_id, _ts(now), call_id))).fetchone()
        return self._unpack(user_id, *row) if row else None

    async def take_pending_by_code(self, user_id: str, code_hash: str, now: float) -> PendingTurn | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "DELETE FROM pending_turns WHERE user_id=%s AND code_hash=%s AND expires_at > %s "
                "RETURNING kind, agent_id, payload, code_hash", (user_id, code_hash, _ts(now)))).fetchone()
        return self._unpack(user_id, *row) if row else None

    async def put_response(self, user_id: str, response_id: str, messages: list[ChatMessage]) -> None:
        payload = self._vault.encrypt(json.dumps([m.model_dump(mode="json", exclude_none=True) for m in messages]).encode())
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO responses (user_id, id, payload) VALUES (%s,%s,%s) "
                "ON CONFLICT (user_id, id) DO UPDATE SET payload=EXCLUDED.payload", (user_id, response_id, payload))

    async def get_response(self, user_id: str, response_id: str) -> list[ChatMessage] | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT payload FROM responses WHERE user_id=%s AND id=%s", (user_id, response_id))).fetchone()
        if not row:
            return None
        return [ChatMessage.model_validate(m) for m in json.loads(self._vault.decrypt(row[0]))]

    async def prune(self, now: float, retention_days: int) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM pending_turns WHERE expires_at < %s", (_ts(now),))
            await conn.execute("DELETE FROM responses WHERE created_at < %s - make_interval(days => %s)", (_ts(now), retention_days))
            await conn.execute("DELETE FROM threads WHERE updated_at < %s - make_interval(days => %s)", (_ts(now), retention_days))


async def read_directory_names(dsn: str) -> tuple[set[str], set[str]]:
    """Every group and role in the directory copy. Read-only; used by ``check`` to catch typos."""
    pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=1)
    await pool.open(wait=True, timeout=5)
    try:
        async with pool.connection() as conn:
            rows = await (await conn.execute("SELECT groups, roles FROM directory_snapshot")).fetchall()
    finally:
        await pool.close()
    return {g for r in rows for g in r[0]}, {x for r in rows for x in r[1]}
