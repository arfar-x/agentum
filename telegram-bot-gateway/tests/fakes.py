"""In-memory stand-ins for the store, the endpoint and Telegram's bot object."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from typing import Any

from telegram_bot_gateway.application.model import Link, PairingRequest, Session


class FakeVault:
    def encrypt(self, plaintext: str) -> str:
        return "enc:" + plaintext

    def decrypt(self, ciphertext: str) -> str:
        return ciphertext.removeprefix("enc:")


class FakeStore:
    def __init__(self) -> None:
        self.links: dict[int, Link] = {}
        self.pairings: dict[str, PairingRequest] = {}
        self.sessions: dict[str, Session] = {}
        self.texts: dict[tuple[int, int], str] = {}

    async def get_link(self, uid): return self.links.get(uid)
    async def save_link(self, link): self.links[link.telegram_user_id] = link

    async def delete_links(self, *, telegram_user_id=None, username=None):
        gone = [u for u, l in self.links.items() if u == telegram_user_id or l.username == username]
        for u in gone:
            del self.links[u]
        return len(gone)

    async def list_links(self): return list(self.links.values())
    async def create_pairing(self, r): self.pairings[r.code] = r
    async def get_pairing(self, code): return self.pairings.get(code)
    async def pairings_of(self, uid): return [p for p in self.pairings.values() if p.telegram_user_id == uid]
    async def list_pairings(self, *, since): return [p for p in self.pairings.values() if p.created_at >= since]
    async def delete_pairing(self, code): self.pairings.pop(code, None)
    async def load_session(self, key): return copy.deepcopy(self.sessions.get(key))
    async def save_session(self, s): self.sessions[s.key] = copy.deepcopy(s)
    async def delete_session(self, key): self.sessions.pop(key, None)

    async def delete_sessions_of(self, uid):
        for k in [k for k, s in self.sessions.items() if s.telegram_user_id == uid]:
            del self.sessions[k]

    async def find_pending(self, chat_id, card_message_id):
        for s in self.sessions.values():
            p = s.pending
            if p and p.chat_id == chat_id and p.card_message_id == card_message_id:
                return copy.deepcopy(s)
        return None

    async def save_text(self, chat_id, message_id, text): self.texts[(chat_id, message_id)] = text
    async def get_text(self, chat_id, message_id): return self.texts.get((chat_id, message_id))


class ScriptedLlm:
    """Answers from a script: each item is a message dict, an Exception, or an async callable."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, api_key, model, messages, params, tools, on_reasoning=None):
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else None
        body = messages[1:] if system is not None else messages
        self.calls.append({"api_key": api_key, "model": model, "messages": copy.deepcopy(body), "system": system,
                           "params": params, "tools": tools})
        step = self.script.pop(0) if self.script else {"role": "assistant", "content": "ok"}
        if callable(step):
            step = await step()
        if isinstance(step, Exception):
            raise step
        return step

    async def models(self, api_key):
        return ["agent-a", "agent-b"]


def say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def call(name: str, arguments: str, id: str = "call_1") -> dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": [{"id": id, "type": "function", "function": {"name": name, "arguments": arguments}}]}


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.reactions: list[tuple[int, list]] = []
        self.deleted: list[tuple[int, int]] = []
        self.fail_next: list[Exception] = []
        self._ids = iter(range(100, 10_000))
        self.id = 999
        self.username = "agentbot"

    async def send_message(self, **kw):
        if self.fail_next:
            raise self.fail_next.pop(0)
        self.sent.append(kw)
        return SimpleNamespace(message_id=next(self._ids))

    async def edit_message_text(self, **kw):
        self.edits.append(kw)

    async def send_chat_action(self, **kw):
        self.actions.append(kw)

    async def set_message_reaction(self, chat_id, message_id, reaction):
        self.reactions.append((message_id, [r.emoji for r in reaction]))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


async def settle(predicate, timeout: float = 3.0) -> None:
    """Wait until ``predicate()`` is true."""
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached in time")
