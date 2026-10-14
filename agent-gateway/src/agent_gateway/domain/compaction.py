"""Shrinking a client's system prompt so it does not crowd the agent's own context."""

from __future__ import annotations

import hashlib

COMPACT_SYSTEM = (
    "You condense another application's system prompt. The condensed text will be given to a different "
    "assistant that runs inside that application and must work correctly in it.\n"
    "Keep: how to format output; how tools are called and named; conventions for asking the user, "
    "showing progress or status, artifacts and files; safety and permission rules; environment facts "
    "(operating system, working directory, date) that affect the work.\n"
    "Drop: persona, name and identity statements; marketing; catalogs of skills, memories or examples; "
    "anything repeated.\n"
    "Write short, plain bullet points in the same language as the original. Do not add anything new. "
    "Reply with the condensed text only."
)


def needs_compaction(text: str, limit_chars: int) -> bool:
    return len(text) > limit_chars


def compaction_user_message(text: str, target_chars: int) -> str:
    return f"Condense this to at most {target_chars} characters:\n\n{text}"


def truncate_to(text: str, target_chars: int) -> str:
    """Head and tail with a marker: the opening states the rules, the end holds the latest additions."""
    if len(text) <= target_chars:
        return text
    marker = "\n[... shortened ...]\n"
    keep = max(target_chars - len(marker), 0)
    head = int(keep * 0.65)
    return text[:head].rstrip() + marker + text[len(text) - (keep - head):].lstrip()


def usable(compacted: str, original: str, target_chars: int) -> bool:
    return bool(compacted.strip()) and len(compacted) < len(original) and len(compacted) <= target_chars * 2


def cache_group(user_id: str) -> str:
    return "cpc-user:" + hashlib.sha256(user_id.encode()).hexdigest()


def cache_key(user_id: str, text: str) -> str:
    return "cpc:" + hashlib.sha256(f"{user_id}\x1f{text}".encode()).hexdigest()
