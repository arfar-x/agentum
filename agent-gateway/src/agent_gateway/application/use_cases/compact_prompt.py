"""Compact a client's system prompt once, then reuse the result for a while."""

from __future__ import annotations

from agent_gateway.application.models import LlmRequest, Settings
from agent_gateway.application.ports import Llm, TextCache
from agent_gateway.domain import compaction
from agent_gateway.domain.messages import ChatMessage


async def compact_client_prompt(
    raw: str, *, user_id: str, model: str, llm: Llm, cache: TextCache | None, settings: Settings
) -> str:
    """The prompt to place in the agent's context: as sent if short, else condensed (cached)."""
    if not compaction.needs_compaction(raw, settings.client_prompt_limit_chars):
        return raw
    target = settings.client_prompt_target_chars
    key, group = compaction.cache_key(user_id, raw), compaction.cache_group(user_id)
    if cache is not None:
        hit = await cache.get(key, settings.client_prompt_cache_seconds, group)
        if hit:
            return hit
    try:
        completion = await llm.complete(LlmRequest(
            model=model, tools=[],
            messages=[ChatMessage(role="system", content=compaction.COMPACT_SYSTEM),
                      ChatMessage(role="user", content=compaction.compaction_user_message(raw, target))],
            params={"temperature": 0.1, "max_completion_tokens": target // 2 + 200}))
        condensed = completion.message.text().strip()
        result = condensed if compaction.usable(condensed, raw, target) else compaction.truncate_to(raw, target)
    except Exception:  # noqa: BLE001 -- a compaction problem must never fail the turn
        result = compaction.truncate_to(raw, target)
    if cache is not None:
        # a new client prompt pushes the user's older entries out
        await cache.put(key, result, settings.client_prompt_cache_seconds, group, settings.client_prompt_cache_entries)
    return result
