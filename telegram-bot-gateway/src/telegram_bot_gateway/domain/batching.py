"""How long to wait for the next part of a message."""

from __future__ import annotations

MEDIA_DELAY = 0.8


def text_delay(length: int) -> float:
    if length >= 4000:  # a client splits long messages near 4096: more is on its way
        return 1.0
    if length <= 320:
        return 0.18
    return 0.24 if length <= 1024 else 0.3
