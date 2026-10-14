"""Approval codes: bound to one user, call, tool and exact arguments."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

CODE_LENGTH = 10


def arguments_hash(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def make_code(secret: bytes, *, user_id: str, call_id: str, tool: str, args_hash: str) -> str:
    message = "\x1f".join((user_id, call_id, tool, args_hash)).encode()
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.b32encode(digest).decode().rstrip("=")[:CODE_LENGTH].lower()


def verify_code(
    secret: bytes, code: str, *, user_id: str, call_id: str, tool: str, args_hash: str
) -> bool:
    expected = make_code(secret, user_id=user_id, call_id=call_id, tool=tool, args_hash=args_hash)
    return hmac.compare_digest(expected, code.strip().lower())


def parse_decision(text: str) -> tuple[str, str] | None:
    """``approve <code>`` / ``reject <code>`` -> (decision, code); anything else -> None."""
    parts = text.strip().lower().split()
    if len(parts) == 2 and parts[0] in {"approve", "reject"}:
        return parts[0], parts[1]
    return None
