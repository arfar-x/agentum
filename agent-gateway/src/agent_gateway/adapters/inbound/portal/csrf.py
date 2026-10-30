"""CSRF defences for state-changing portal requests (FR-PRT-05): the session's token and the origin."""

from __future__ import annotations

from urllib.parse import urlsplit

from agent_gateway.adapters.inbound.portal.session import same_secret


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower() if p.scheme and p.netloc else ""


def origin_allowed(headers, base_url: str) -> bool:
    """`Origin`, or when absent `Referer`, must be the origin of PORTAL_BASE_URL. Neither: refused."""
    given = headers.get("origin")
    if given is None:
        referer = headers.get("referer")
        if not referer:
            return False
        given = referer
    return origin_of(given) == origin_of(base_url)


def token_allowed(form_token: str | None, session_token: str) -> bool:
    return bool(form_token) and same_secret(form_token, session_token)
