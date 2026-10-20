"""The bot's own database: links, pairing codes, sessions."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from telegram_bot_gateway.application.model import Link, PairingRequest, Pending, Session


class PostgresStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @classmethod
    async def open(cls, database_url: str, migrations_dir: Path | str) -> PostgresStore:
        pool = AsyncConnectionPool(database_url, min_size=1, max_size=8, open=False, kwargs={"row_factory": dict_row})
        await pool.open(wait=True, timeout=30)
        store = cls(pool)
        await store.migrate(Path(migrations_dir))
        return store

    async def close(self) -> None:
        await self._pool.close()

    async def migrate(self, directory: Path) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_lock(7466)")
            try:
                await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
                done = {r["name"] for r in await (await conn.execute("SELECT name FROM schema_migrations")).fetchall()}
                for path in sorted(directory.glob("*.sql")):
                    if path.name not in done:
                        await conn.execute(path.read_text(encoding="utf-8"))
                        await conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                        await conn.commit()
            finally:
                await conn.execute("SELECT pg_advisory_unlock(7466)")

    async def _rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        async with self._pool.connection() as conn:
            return await (await conn.execute(sql, args)).fetchall()

    async def _run(self, sql: str, args: tuple = ()) -> int:
        async with self._pool.connection() as conn:
            return (await conn.execute(sql, args)).rowcount

    # links
    async def get_link(self, telegram_user_id: int) -> Link | None:
        rows = await self._rows("SELECT * FROM links WHERE telegram_user_id = %s", (telegram_user_id,))
        return _link(rows[0]) if rows else None

    async def save_link(self, link: Link) -> None:
        await self._run(
            "INSERT INTO links (telegram_user_id, username, telegram_name, api_key_enc) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (telegram_user_id) DO UPDATE SET username = EXCLUDED.username, "
            "telegram_name = EXCLUDED.telegram_name, api_key_enc = EXCLUDED.api_key_enc",
            (link.telegram_user_id, link.username, link.telegram_name, link.api_key_enc),
        )

    async def delete_links(self, *, telegram_user_id: int | None = None, username: str | None = None) -> int:
        return await self._run(
            "DELETE FROM links WHERE telegram_user_id = %s OR username = %s", (telegram_user_id, username)
        )

    async def list_links(self) -> list[Link]:
        return [_link(r) for r in await self._rows("SELECT * FROM links ORDER BY created_at")]

    # pairing
    async def create_pairing(self, request: PairingRequest) -> None:
        await self._run(
            "INSERT INTO pairing_codes (code, telegram_user_id, username, telegram_name, chat_id, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (request.code, request.telegram_user_id, request.username, request.telegram_name, request.chat_id, request.created_at),
        )

    async def get_pairing(self, code: str) -> PairingRequest | None:
        rows = await self._rows("SELECT * FROM pairing_codes WHERE code = %s", (code,))
        return PairingRequest(**rows[0]) if rows else None

    async def pairings_of(self, telegram_user_id: int) -> list[PairingRequest]:
        rows = await self._rows("SELECT * FROM pairing_codes WHERE telegram_user_id = %s", (telegram_user_id,))
        return [PairingRequest(**r) for r in rows]

    async def list_pairings(self, *, since: float) -> list[PairingRequest]:
        rows = await self._rows("SELECT * FROM pairing_codes WHERE created_at >= %s ORDER BY created_at", (since,))
        return [PairingRequest(**r) for r in rows]

    async def delete_pairing(self, code: str) -> None:
        await self._run("DELETE FROM pairing_codes WHERE code = %s", (code,))

    # sessions
    async def load_session(self, key: str) -> Session | None:
        rows = await self._rows("SELECT * FROM sessions WHERE key = %s", (key,))
        return _session(rows[0]) if rows else None

    async def save_session(self, session: Session) -> None:
        pending = dataclasses.asdict(session.pending) if session.pending else None
        await self._run(
            "INSERT INTO sessions (key, telegram_user_id, model, params, messages, pending) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET model = EXCLUDED.model, params = EXCLUDED.params, "
            "messages = EXCLUDED.messages, pending = EXCLUDED.pending, updated_at = now()",
            (session.key, session.telegram_user_id, session.model, Jsonb(session.params), Jsonb(session.messages),
             Jsonb(pending) if pending else None),
        )

    async def delete_session(self, key: str) -> None:
        await self._run("DELETE FROM sessions WHERE key = %s", (key,))

    async def delete_sessions_of(self, telegram_user_id: int) -> None:
        await self._run("DELETE FROM sessions WHERE telegram_user_id = %s", (telegram_user_id,))

    async def find_pending(self, chat_id: int, card_message_id: int) -> Session | None:
        rows = await self._rows(
            "SELECT * FROM sessions WHERE (pending->>'chat_id')::bigint = %s AND (pending->>'card_message_id')::bigint = %s",
            (chat_id, card_message_id),
        )
        return _session(rows[0]) if rows else None

    # message texts
    async def save_text(self, chat_id: int, message_id: int, text: str) -> None:
        await self._run(
            "INSERT INTO message_texts (chat_id, message_id, text) VALUES (%s, %s, %s) "
            "ON CONFLICT (chat_id, message_id) DO UPDATE SET text = EXCLUDED.text",
            (chat_id, message_id, text),
        )

    async def get_text(self, chat_id: int, message_id: int) -> str | None:
        rows = await self._rows("SELECT text FROM message_texts WHERE chat_id = %s AND message_id = %s", (chat_id, message_id))
        return rows[0]["text"] if rows else None


def _link(row: dict[str, Any]) -> Link:
    return Link(row["telegram_user_id"], row["username"], row["telegram_name"], row["api_key_enc"])


def _session(row: dict[str, Any]) -> Session:
    return Session(
        key=row["key"], telegram_user_id=row["telegram_user_id"], model=row["model"],
        params=row["params"], messages=row["messages"],
        pending=Pending(**row["pending"]) if row["pending"] else None,
    )
