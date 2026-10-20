"""Pairing codes: how an unknown Telegram user asks the operator to link them."""

from __future__ import annotations

import secrets

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0 O 1 I
CODE_LENGTH = 8
TTL_SECONDS = 3600
REQUEST_INTERVAL_SECONDS = 600
MAX_PENDING = 3


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def normalise(code: str) -> str:
    return code.strip().upper().replace("-", "").replace(" ", "")
