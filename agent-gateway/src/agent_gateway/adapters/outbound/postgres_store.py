"""Postgres: keys, encrypted credentials, threads and paused turns."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg_pool import AsyncConnectionPool

from agent_gateway.adapters.outbound.crypto import Vault, hash_key, new_api_key
from agent_gateway.application.models import PendingTurn, Thread
from agent_gateway.domain.messages import ChatMessage, ToolCall


def _ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


class PostgresStore:
    """Implements ``ConversationStore``, ``CredentialStore`` and ``KeyStore`` plus the operator API."""

    def __init__(self, pool: AsyncConnectionPool, vault: Vault) -> None:
        self._pool, self._vault = pool, vault

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

    # ---- KeyStore

    async def find_user(self, key_hash: str) -> tuple[str, str] | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "UPDATE api_keys SET last_used_at = now() WHERE key_hash=%s AND revoked_at IS NULL RETURNING id::text, user_id",
                (key_hash,))).fetchone()
        return (row[0], row[1]) if row else None

    async def create_key(self, user_id: str, label: str = "") -> str:
        secret = new_api_key()
        async with self._pool.connection() as conn:
            await conn.execute("INSERT INTO api_keys (user_id, label, key_hash) VALUES (%s,%s,%s)",
                               (user_id, label, hash_key(secret)))
        return secret

    async def list_keys(self) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT id::text, user_id, label, created_at, revoked_at, last_used_at FROM api_keys ORDER BY created_at")).fetchall()
        return [dict(zip(("id", "user_id", "label", "created_at", "revoked_at", "last_used_at"), r)) for r in rows]

    async def revoke_key(self, key_id: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute("UPDATE api_keys SET revoked_at = now() WHERE id::text=%s AND revoked_at IS NULL", (key_id,))
            return cur.rowcount > 0

    # ---- CredentialStore

    async def get(self, user_id: str, server: str) -> dict[str, str]:
        async with self._pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT var, ciphertext FROM user_credentials WHERE user_id=%s AND server=%s", (user_id, server))).fetchall()
        return {var: self._vault.decrypt(ct).decode() for var, ct in rows}

    async def set_credential(self, user_id: str, server: str, var: str, value: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO user_credentials (user_id, server, var, ciphertext) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (user_id, server, var) DO UPDATE SET ciphertext=EXCLUDED.ciphertext, updated_at=now()",
                (user_id, server, var, self._vault.encrypt(value.encode())))

    async def delete_credentials(self, user_id: str, server: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute("DELETE FROM user_credentials WHERE user_id=%s AND server=%s", (user_id, server))
            return cur.rowcount

    # ---- ConversationStore

    async def get_thread(self, user_id: str, history_hash: str) -> Thread | None:
        async with self._pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT active_agent, client_opt_in FROM threads WHERE user_id=%s AND history_hash=%s",
                (user_id, history_hash))).fetchone()
        return Thread(user_id, history_hash, row[0], row[1]) if row else None

    async def put_thread(self, thread: Thread) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO threads (user_id, history_hash, active_agent, client_opt_in) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (user_id, history_hash) DO UPDATE SET active_agent=EXCLUDED.active_agent, "
                "client_opt_in=EXCLUDED.client_opt_in, updated_at=now()",
                (thread.user_id, thread.history_hash, thread.active_agent, thread.client_opt_in))

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
