"""Linking a Telegram user to an API key, with the operator's approval."""

from __future__ import annotations

from dataclasses import dataclass

from telegram_bot_gateway.application.model import Link, PairingRequest
from telegram_bot_gateway.application.ports import Store, Vault
from telegram_bot_gateway.domain import pairing


@dataclass(frozen=True)
class Paired:
    code: str


@dataclass(frozen=True)
class Refused:
    reason: str  # "too_soon" | "too_many"


async def request(
    store: Store, *, telegram_user_id: int, username: str, telegram_name: str, chat_id: int, now: float
) -> Paired | Refused:
    live = [p for p in await store.pairings_of(telegram_user_id) if now - p.created_at < pairing.TTL_SECONDS]
    if live and now - max(p.created_at for p in live) < pairing.REQUEST_INTERVAL_SECONDS:
        return Refused("too_soon")
    if len(live) >= pairing.MAX_PENDING:
        return Refused("too_many")
    code = pairing.new_code()
    await store.create_pairing(PairingRequest(code, telegram_user_id, username, telegram_name, chat_id, now))
    return Paired(code)


async def approve(store: Store, vault: Vault, code: str, api_key: str, *, now: float) -> PairingRequest:
    """Link the user behind ``code`` to ``api_key``; raises LookupError for an unknown or expired code."""
    found = await store.get_pairing(pairing.normalise(code))
    if found is None or now - found.created_at >= pairing.TTL_SECONDS:
        raise LookupError("unknown or expired code")
    await store.save_link(Link(found.telegram_user_id, found.username, found.telegram_name, vault.encrypt(api_key)))
    await store.delete_pairing(found.code)
    return found


async def api_key_for(store: Store, vault: Vault, telegram_user_id: int) -> str | None:
    link = await store.get_link(telegram_user_id)
    return vault.decrypt(link.api_key_enc) if link else None


async def unlink(store: Store, *, telegram_user_id: int | None = None, username: str | None = None) -> int:
    """Remove links; returns how many. Their sessions go too, so nothing keeps working."""
    victims = [l for l in await store.list_links()
               if (telegram_user_id is not None and l.telegram_user_id == telegram_user_id)
               or (username is not None and l.username == username)]
    for link in victims:
        await store.delete_sessions_of(link.telegram_user_id)
    return await store.delete_links(telegram_user_id=telegram_user_id, username=username)
