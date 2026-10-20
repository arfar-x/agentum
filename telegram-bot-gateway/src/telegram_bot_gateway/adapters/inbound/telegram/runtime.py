"""Running turns: batching what arrives, queueing while busy, and the typing/reaction chrome."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji

from telegram_bot_gateway.adapters.inbound.telegram.sender import Sender, Target
from telegram_bot_gateway.adapters.outbound.openai import EndpointError
from telegram_bot_gateway.application import linking, turn
from telegram_bot_gateway.application.model import Session
from telegram_bot_gateway.application.ports import Llm, Store, Vault
from telegram_bot_gateway.config import Settings
from telegram_bot_gateway.domain.content import Item, merge

log = logging.getLogger(__name__)

LINK_PROMPT = "You are not linked yet. Send /start <your username> here and I will give you a code for the operator."
PROGRESS_INTERVAL = 2.0
PROGRESS_TAIL = 600


@dataclass
class Job:
    target: Target
    user_id: int
    session_key: str
    item: Item
    delay: float


@dataclass
class _Bucket:
    jobs: list[Job] = field(default_factory=list)
    timer: asyncio.TimerHandle | None = None


@dataclass
class _Run:
    task: asyncio.Task | None = None
    queue: list[Job] = field(default_factory=list)


def card_markup(options: list[str], *, with_other: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(label, callback_data=f"a:{i}")] for i, label in enumerate(options)]
    if with_other:
        rows.append([InlineKeyboardButton("✏️ Other", callback_data="a:o")])
    return InlineKeyboardMarkup(rows)


class Runtime:
    def __init__(self, *, bot: Bot, store: Store, llm: Llm, vault: Vault, sender: Sender, config: Settings) -> None:
        self._bot, self._store, self._llm, self._vault = bot, store, llm, vault
        self._sender, self._cfg = sender, config
        self._buckets: dict[str, _Bucket] = {}
        self._runs: dict[str, _Run] = {}

    # -- intake --------------------------------------------------------------------------------
    def submit(self, job: Job) -> None:
        bucket = self._buckets.setdefault(job.session_key, _Bucket())
        bucket.jobs.append(job)
        if bucket.timer is not None:
            bucket.timer.cancel()
        loop = asyncio.get_running_loop()
        bucket.timer = loop.call_later(job.delay, self._flush, job.session_key)

    def _flush(self, key: str) -> None:
        bucket = self._buckets.pop(key, None)
        if bucket is None or not bucket.jobs:
            return
        run = self._runs.get(key)
        if run is not None:
            run.queue.extend(bucket.jobs)  # busy: merged into the next turn
            return
        run = self._runs[key] = _Run()
        run.task = asyncio.create_task(self._drain(key, run, bucket.jobs))

    async def _drain(self, key: str, run: _Run, jobs: list[Job]) -> None:
        try:
            while jobs:
                try:
                    await self._turn(key, jobs)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("turn failed for session %s", key)
                jobs, run.queue = run.queue, []
        finally:
            self._runs.pop(key, None)

    async def stop(self, key: str) -> bool:
        """Cancel the running request and drop what was queued."""
        bucket = self._buckets.pop(key, None)
        if bucket and bucket.timer:
            bucket.timer.cancel()
        run = self._runs.get(key)
        if run is None or run.task is None:
            return bool(bucket and bucket.jobs)
        run.queue.clear()
        run.task.cancel()
        try:
            await run.task
        except asyncio.CancelledError:
            pass
        return True

    def busy(self, key: str) -> bool:
        return key in self._runs

    # -- one turn ------------------------------------------------------------------------------
    async def _turn(self, key: str, jobs: list[Job]) -> None:
        last = jobs[-1]
        target = last.target
        api_key = await linking.api_key_for(self._store, self._vault, last.user_id)
        if api_key is None:
            await self._sender.send(target, LINK_PROMPT)
            return
        session = await self._store.load_session(key) or Session(key, last.user_id)
        work, stale = turn.begin(session, merge([j.item for j in jobs]), now=time.time())
        if stale is not None and stale.card_message_id is not None:
            await self._safely(self._sender.edit(stale.chat_id, stale.card_message_id, f"{stale.question}\n\n⌛ Expired"))
        typing = asyncio.create_task(self._typing(target))
        progress = _Progress(self._sender, target) if self._cfg.show_progress else None
        await self._react(target, "👀")
        try:
            outcome = await turn.run(
                work, self._llm, api_key, default_model=self._cfg.default_model, chat_id=target.chat_id,
                now=time.time(), on_reasoning=progress.update if progress else None,
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._react(target, None))
            raise
        except EndpointError as exc:
            await self._react(target, "👎")
            await self._sender.send(target, str(exc))
            return
        except Exception:
            log.exception("endpoint call failed")
            await self._react(target, "👎")
            await self._sender.send(target, "Something went wrong while answering. Please try again.")
            return
        finally:
            typing.cancel()
            if progress:
                await progress.finish()

        fresh = await self._store.load_session(key)  # settings changed during the turn win
        if fresh is not None:
            work.model, work.params = fresh.model, fresh.params
        await self._store.save_session(work)
        notices = "".join(f"⚠️ {n}\n" for n in outcome.notices)
        if isinstance(outcome, turn.Answer):
            if outcome.text or notices:
                await self._sender.send(target, notices + outcome.text)
        else:
            q = outcome.question
            ids = await self._sender.send(
                target, notices + q.text, reply_markup=card_markup(list(q.options), with_other=not q.is_approval)
            )
            work.pending.card_message_id = ids[0]
            await self._store.save_session(work)
        await self._react(target, "👍")

    # -- chrome --------------------------------------------------------------------------------
    async def _typing(self, target: Target) -> None:
        while True:
            try:
                kwargs: dict[str, Any] = {"chat_id": target.chat_id, "action": "typing"}
                if target.thread_id is not None:
                    kwargs["message_thread_id"] = target.thread_id
                await self._bot.send_chat_action(**kwargs)
            except Exception:  # a failed typing action must never fail the turn
                pass
            await asyncio.sleep(2)

    async def _react(self, target: Target, emoji: str | None) -> None:
        if not self._cfg.reactions or target.reply_to is None:
            return
        try:
            await self._bot.set_message_reaction(
                chat_id=target.chat_id, message_id=target.reply_to, reaction=[ReactionTypeEmoji(emoji)] if emoji else []
            )
        except Exception:
            pass

    @staticmethod
    async def _safely(coro: Any) -> None:
        try:
            await coro
        except Exception:
            log.debug("ignored a failed message edit", exc_info=True)


class _Progress:
    """The endpoint's reasoning as one silent message, edited in place and removed at the end."""

    def __init__(self, sender: Sender, target: Target) -> None:
        self._sender, self._target = sender, target
        self._message_id: int | None = None
        self._last = 0.0

    async def update(self, text: str) -> None:
        now = time.monotonic()
        if self._message_id is not None and now - self._last < PROGRESS_INTERVAL:
            return
        self._last = now
        shown = "💭 " + text[-PROGRESS_TAIL:]
        try:
            if self._message_id is None:
                quiet = Target(self._target.chat_id, self._target.chat_type, self._target.thread_id, None)
                self._message_id = (await self._sender.send(quiet, shown, notify=False))[0]
            else:
                await self._sender.edit(self._target.chat_id, self._message_id, shown)
        except Exception:
            pass

    async def finish(self) -> None:
        if self._message_id is not None:
            try:
                await self._sender.delete(self._target.chat_id, self._message_id)
            except Exception:
                pass
