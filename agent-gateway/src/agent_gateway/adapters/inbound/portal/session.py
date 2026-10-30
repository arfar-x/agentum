"""Signed cookies of the portal (data model, section 8): `portal_session` and `portal_login`.

Wire form: base64url(json) + "." + base64url(HMAC-SHA256(key, json)), with
key = SHA-256("portal-session:" + PORTAL_SESSION_SECRET). Nothing secret is stored in them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

SESSION_COOKIE = "portal_session"
LOGIN_COOKIE = "portal_login"
COOKIE_PATH = "/portal"
ABSOLUTE = timedelta(hours=8)
IDLE = timedelta(minutes=30)
REISSUE_AFTER = timedelta(minutes=5)
LOGIN_TTL = timedelta(minutes=10)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_token(nbytes: int = 32) -> str:
    return _b64(secrets.token_bytes(nbytes))


@dataclass(frozen=True)
class PortalSession:
    sub: str
    username: str
    csrf: str
    iat: float
    exp: float
    seen: float


class Signer:
    def __init__(self, secret: str) -> None:
        self._key = hashlib.sha256(b"portal-session:" + secret.encode()).digest()

    def sign(self, data: dict[str, Any]) -> str:
        body = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        return f"{_b64(body)}.{_b64(hmac.new(self._key, body, hashlib.sha256).digest())}"

    def unsign(self, value: str | None) -> dict[str, Any] | None:
        if not value or "." not in value:
            return None
        body, _, sig = value.partition(".")
        try:
            raw, given = _unb64(body), _unb64(sig)
            if not hmac.compare_digest(given, hmac.new(self._key, raw, hashlib.sha256).digest()):
                return None
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None


class SessionCodec:
    def __init__(self, secret: str) -> None:
        self._signer = Signer(secret)

    # -- the session
    def issue(self, sub: str, username: str, now: datetime, csrf: str | None = None) -> str:
        t = now.timestamp()
        return self._signer.sign({"v": 1, "sub": sub, "username": username, "csrf": csrf or new_token(),
                                  "iat": t, "exp": t + ABSOLUTE.total_seconds(), "seen": t})

    def read(self, cookie: str | None, now: datetime) -> PortalSession | None:
        """The session, or None if the cookie is missing, forged, expired or idle."""
        data = self._signer.unsign(cookie)
        try:
            if not data or data["v"] != 1:
                return None
            s = PortalSession(str(data["sub"]), str(data["username"]), str(data["csrf"]),
                              float(data["iat"]), float(data["exp"]), float(data["seen"]))
        except (KeyError, TypeError, ValueError):
            return None
        t = now.timestamp()
        if t >= s.exp or t - s.seen >= IDLE.total_seconds() or not s.sub or not s.csrf:
            return None
        return s

    def refreshed(self, s: PortalSession, now: datetime) -> str | None:
        """A reissued cookie when `seen` is older than five minutes, else None."""
        if now.timestamp() - s.seen < REISSUE_AFTER.total_seconds():
            return None
        return self._signer.sign({"v": 1, "sub": s.sub, "username": s.username, "csrf": s.csrf,
                                  "iat": s.iat, "exp": s.exp, "seen": now.timestamp()})

    # -- the sign-in in progress
    def issue_login(self, state: str, nonce: str, verifier: str, next_path: str, now: datetime) -> str:
        return self._signer.sign({"state": state, "nonce": nonce, "code_verifier": verifier, "next": next_path,
                                  "exp": now.timestamp() + LOGIN_TTL.total_seconds()})

    def read_login(self, cookie: str | None, now: datetime) -> dict[str, Any] | None:
        data = self._signer.unsign(cookie)
        try:
            if not data or float(data["exp"]) <= now.timestamp():
                return None
            return {k: str(data[k]) for k in ("state", "nonce", "code_verifier", "next")}
        except (KeyError, TypeError, ValueError):
            return None


def pkce_challenge(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def same_secret(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
