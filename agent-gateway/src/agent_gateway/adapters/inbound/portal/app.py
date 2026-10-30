"""The self-service portal (specs/003-identity-access/contracts/http-portal.md).

A small server-rendered sub-application mounted at ``/portal``. It has its own error handling and
its own cookie session; it never accepts the API bearer and imports nothing provider-specific:
sign-in goes through the ``PortalLogin`` port.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from agent_gateway.adapters.inbound.portal.csrf import origin_allowed, token_allowed
from agent_gateway.adapters.inbound.portal.session import (
    COOKIE_PATH,
    LOGIN_COOKIE,
    LOGIN_TTL,
    SESSION_COOKIE,
    PortalSession,
    SessionCodec,
    new_token,
    pkce_challenge,
    same_secret,
)
from agent_gateway.application.identity_ports import AuditLog, DirectoryStore, DirectorySync, KeyService, PortalLogin
from agent_gateway.application.models import ServerInfo
from agent_gateway.application.portal import (
    CredentialCenter,
    CredentialRecords,
    InvalidCredential,
    UndeclaredCredential,
    check_declared,
)
from agent_gateway.application.ports import ToolGateway
from agent_gateway.domain.identity import (
    AuthError,
    DirectoryEntry,
    KeyIssuingDisabled,
    KeyScope,
    is_active,
)

HERE = Path(__file__).parent
MAX_FORM_BYTES = 64 * 1024
SECRET_SHAPE = re.compile(r"^\S{8,512}$")
MAX_LABEL = 64
LLM_SERVER = "llm"

HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'",
}

# Flash messages are chosen by a fixed code in the query string, never by text a request supplies.
FLASH = {
    "key_saved": "Your model key is registered. Any earlier key is revoked.",
    "key_revoked": "Your model key is revoked.",
    "cred_saved": "Saved.",
    "cred_deleted": "Deleted.",
    "issued_revoked": "The key is revoked.",
}


@dataclass
class PortalDeps:
    base_url: str
    session_secret: str
    login: PortalLogin
    directory: DirectoryStore
    keys: KeyService
    audit: AuditLog
    creds: CredentialRecords
    tools: ToolGateway
    servers: Mapping[str, ServerInfo]
    issue_keys: bool = False
    model_key_login: str = "read-only"
    agent_ids: Callable[[DirectoryEntry], list[str]] = field(default=lambda entry: [])
    sync: DirectorySync | None = None
    max_stale: timedelta = timedelta(seconds=900)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))


class _Refused(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status, self.message = status, message


def create_portal_app(deps: PortalDeps) -> Starlette:
    codec = SessionCodec(deps.session_secret)
    center = CredentialCenter(deps.servers, deps.creds, deps.tools, deps.audit)
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]),
                      trim_blocks=True, lstrip_blocks=True)
    secure = not deps.base_url.startswith("http://localhost")
    redirect_uri = f"{deps.base_url}/portal/callback"

    # ---- helpers

    def page(request: Request, template: str, status: int = 200, **ctx) -> Response:
        session: PortalSession | None = getattr(request.state, "session", None)
        body = env.get_template(template).render(
            session=session, issue_keys=deps.issue_keys, flash=FLASH.get(request.query_params.get("m", "")),
            nav=request.url.path, **ctx)
        return Response(body, status_code=status, media_type="text/html")

    def safe_next(value: str | None) -> str:
        if not value or not value.startswith("/portal") or value.startswith("//") or "\\" in value \
                or any(ord(c) < 32 for c in value):
            return "/portal"
        parts = urlsplit(value)
        return value if not parts.scheme and not parts.netloc and (parts.path == "/portal" or parts.path.startswith("/portal/")) \
            else "/portal"

    def cookie(response: Response, name: str, value: str, max_age: int) -> None:
        response.set_cookie(name, value, max_age=max_age, path=COOKIE_PATH, httponly=True, secure=secure,
                            samesite="lax")

    def clear(response: Response, name: str) -> None:
        response.delete_cookie(name, path=COOKIE_PATH, httponly=True, secure=secure, samesite="lax")

    async def fresh_entry(sub: str) -> DirectoryEntry | None:
        """The person's directory row if it is fresh and enabled; refreshes once if it is not there."""
        for attempt in (0, 1):
            entry = await deps.directory.lookup(sub)
            usable = entry is not None and deps.now() - entry.synced_at <= deps.max_stale
            if usable or attempt or deps.sync is None:
                break
            try:
                await deps.sync.refresh_if_due(timedelta(seconds=30))
            except Exception:  # noqa: BLE001 -- an outage means the person stays signed out
                break
        if entry is None or deps.now() - entry.synced_at > deps.max_stale or not entry.enabled:
            return None
        return entry

    def signed_out(request: Request) -> Response:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        next_path = target if request.method == "GET" else "/portal"
        response = RedirectResponse(f"/portal/login?next={quote(safe_next(next_path), safe='')}", status_code=303)
        clear(response, SESSION_COOKIE)
        return response

    async def read_form(request: Request) -> dict[str, list[str]]:
        if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            raise _Refused(400, "The form was not understood.")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_FORM_BYTES:
                raise _Refused(400, "The form is too large.")
        return parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)

    def first(form: dict[str, list[str]], name: str) -> str:
        return (form.get(name) or [""])[0]

    def protected(handler, *, post: bool = False):
        """Signed-in (and still enabled) people only; POSTs also need the CSRF token and the origin."""

        async def wrapper(request: Request) -> Response:
            now = deps.now()
            session = codec.read(request.cookies.get(SESSION_COOKIE), now)
            entry = await fresh_entry(session.sub) if session else None
            if session is None or entry is None:
                return signed_out(request)
            request.state.session, request.state.entry = session, entry
            form: dict[str, list[str]] = {}
            if post:
                if not origin_allowed(request.headers, deps.base_url):
                    return page(request, "error.html", 403, message="The request came from another site and was refused.")
                try:
                    form = await read_form(request)
                except _Refused as exc:
                    return page(request, "error.html", exc.status, message=exc.message)
                if not token_allowed(first(form, "csrf"), session.csrf):
                    return page(request, "error.html", 403, message="The form expired. Go back, reload the page and try again.")
            response = await (handler(request, form) if post else handler(request))
            renewed = codec.refreshed(session, now)
            if renewed:
                cookie(response, SESSION_COOKIE, renewed, int((session.exp - now.timestamp())))
            return response

        return wrapper

    async def active_model_key(sub: str):
        return next((k for k in await deps.keys.list_for(sub) if k.kind == "upstream" and is_active(k, deps.now())), None)

    # ---- sign-in

    async def index(request: Request) -> Response:
        return RedirectResponse("/portal/key", status_code=303)

    async def login(request: Request) -> Response:
        state, nonce, verifier = new_token(), new_token(), secrets.token_urlsafe(48)
        next_path = safe_next(request.query_params.get("next"))
        url = deps.login.authorize_url(state, nonce, pkce_challenge(verifier), redirect_uri)
        response = RedirectResponse(url, status_code=302)
        cookie(response, LOGIN_COOKIE, codec.issue_login(state, nonce, verifier, next_path, deps.now()),
               int(LOGIN_TTL.total_seconds()))
        return response

    async def callback(request: Request) -> Response:
        pending = codec.read_login(request.cookies.get(LOGIN_COOKIE), deps.now())
        again = {"again": True}
        if pending is None or not same_secret(request.query_params.get("state", ""), pending["state"]):
            response = page(request, "error.html", 400, message="The sign-in could not be completed.", **again)
            clear(response, LOGIN_COOKIE)
            return response
        code = request.query_params.get("code", "")
        try:
            if not code:
                raise AuthError("malformed")
            who = await deps.login.complete(code, pending["code_verifier"], redirect_uri, pending["nonce"])
        except AuthError:
            response = page(request, "error.html", 403, message="Sign-in was refused.", **again)
            clear(response, LOGIN_COOKIE)
            return response
        if await fresh_entry(who.sub) is None:
            response = page(request, "error.html", 403, message="This account is not enabled for the portal.")
            clear(response, LOGIN_COOKIE)
            return response
        response = RedirectResponse(safe_next(pending["next"]), status_code=303)
        cookie(response, SESSION_COOKIE, codec.issue(who.sub, who.username, deps.now()), 8 * 3600)
        clear(response, LOGIN_COOKIE)
        return response

    async def logout(request: Request, form) -> Response:
        end = getattr(deps.login, "logout_url", None)         # optional on the port; a provider may offer it
        target = end(f"{deps.base_url}/portal/login") if callable(end) else "/portal/signed-out"
        response = RedirectResponse(target, status_code=303)
        clear(response, SESSION_COOKIE)
        return response

    async def signed_out_page(request: Request) -> Response:
        return page(request, "signed_out.html")

    # ---- my model key

    async def key_page(request: Request, status: int = 200, error: str | None = None) -> Response:
        return page(request, "key.html", status, key=await active_model_key(request.state.session.sub), error=error,
                    login_mode=deps.model_key_login)

    async def key_set(request: Request, form) -> Response:
        sub = request.state.session.sub
        label, secret = first(form, "label").strip(), first(form, "secret").strip()
        if len(label) > MAX_LABEL:
            return await key_page(request, 400, f"The label is longer than {MAX_LABEL} characters.")
        if not SECRET_SHAPE.match(secret):
            return await key_page(request, 400, "The key must be 8 to 512 characters without spaces.")
        await deps.keys.register_upstream(sub, label, secret, actor=sub)
        return RedirectResponse("/portal/key?m=key_saved", status_code=303)

    async def key_revoke(request: Request, form) -> Response:
        sub = request.state.session.sub
        current = await active_model_key(sub)
        if current is None or not await deps.keys.revoke(sub, current.id, actor=sub):
            return await key_page(request, 409, "You have no active model key.")
        return RedirectResponse("/portal/key?m=key_revoked", status_code=303)

    # ---- my tool credentials

    async def credentials_page(request: Request, status: int = 200, error: str | None = None,
                               probe: tuple[str, object] | None = None) -> Response:
        sub = request.state.session.sub
        return page(request, "credentials.html", status, cards=await center.cards(sub), error=error,
                    probe=probe, model_key=await active_model_key(sub))

    def declared_or_404(server: str, var: str | None = None) -> None:
        info = deps.servers.get(server)
        if var is None:
            if info is None or info.test is None:
                raise UndeclaredCredential(server)
        else:
            check_declared(deps.servers, server, var)

    async def cred_set(request: Request, form) -> Response:
        server, var = request.path_params["server"], request.path_params["var"]
        try:
            declared_or_404(server, var)
            await center.set(request.state.session.sub, server, var, first(form, "value"))
        except UndeclaredCredential:
            return not_found(request)
        except InvalidCredential as exc:
            return await credentials_page(request, 400, str(exc))
        return RedirectResponse("/portal/credentials?m=cred_saved", status_code=303)

    async def cred_delete(request: Request, form) -> Response:
        server, var = request.path_params["server"], request.path_params["var"]
        try:
            await center.delete(request.state.session.sub, server, var)
        except UndeclaredCredential:
            return not_found(request)
        return RedirectResponse("/portal/credentials?m=cred_deleted", status_code=303)

    async def cred_test(request: Request, form) -> Response:
        server = request.path_params["server"]
        try:
            declared_or_404(server)
            outcome = await center.test(request.state.session.sub, server)
        except UndeclaredCredential:
            return not_found(request)
        return await credentials_page(request, 200, probe=(server, outcome))

    # ---- issued keys

    def issued_enabled() -> None:
        if not deps.issue_keys:
            raise _Refused(404, "There is nothing here.")

    async def issued_page(request: Request, status: int = 200, error: str | None = None,
                          new_secret: str | None = None) -> Response:
        issued_enabled()
        sub = request.state.session.sub
        keys = [k for k in await deps.keys.list_for(sub) if k.kind == "issued"]
        return page(request, "issued.html", status, keys=keys, error=error, new_secret=new_secret, now=deps.now(),
                    agents=deps.agent_ids(request.state.entry), is_active=is_active)

    async def issued_get(request: Request) -> Response:
        try:
            return await issued_page(request)
        except _Refused as exc:
            return page(request, "error.html", exc.status, message=exc.message)

    async def issued_create(request: Request, form) -> Response:
        try:
            issued_enabled()
        except _Refused as exc:
            return page(request, "error.html", exc.status, message=exc.message)
        sub = request.state.session.sub
        label = first(form, "label").strip()
        days_raw = first(form, "expires_in_days").strip() or "90"
        known = set(deps.agent_ids(request.state.entry))
        agents = tuple(dict.fromkeys(a for a in form.get("agents", []) if a))
        if len(label) > MAX_LABEL:
            return await issued_page(request, 400, f"The label is longer than {MAX_LABEL} characters.")
        if not days_raw.isdigit() or not 1 <= int(days_raw) <= 365:
            return await issued_page(request, 400, "Days until expiry must be a whole number from 1 to 365.")
        if not set(agents) <= known:
            return await issued_page(request, 400, "Choose agents from the list.")
        scope = KeyScope(agents, "write" in form)
        try:
            secret, _ = await deps.keys.issue(sub, label, scope, deps.now() + timedelta(days=int(days_raw)), actor=sub)
        except KeyIssuingDisabled:
            return page(request, "error.html", 404, message="There is nothing here.")
        return await issued_page(request, 200, new_secret=secret)

    async def issued_revoke(request: Request, form) -> Response:
        try:
            issued_enabled()
        except _Refused as exc:
            return page(request, "error.html", exc.status, message=exc.message)
        sub, key_id = request.state.session.sub, request.path_params["key_id"]
        own = {k.id for k in await deps.keys.list_for(sub) if k.kind == "issued"}
        if key_id not in own or not await deps.keys.revoke(sub, key_id, actor=sub):
            return not_found(request)
        return RedirectResponse("/portal/issued?m=issued_revoked", status_code=303)

    # ---- activity, health, static

    async def activity(request: Request) -> Response:
        events = await deps.audit.recent(50, actor=request.state.session.sub)
        return page(request, "activity.html", events=events)

    async def healthz(request: Request) -> Response:
        return PlainTextResponse("ok")

    async def static(request: Request) -> Response:
        if request.path_params["name"] != "portal.css":
            return PlainTextResponse("Not found", status_code=404)
        return Response((HERE / "static" / "portal.css").read_bytes(), media_type="text/css")

    def not_found(request: Request) -> Response:
        return page(request, "error.html", 404, message="There is nothing here.")

    async def not_found_handler(request: Request, exc) -> Response:
        request.state.session = getattr(request.state, "session", None)
        return not_found(request)

    async def failure_handler(request: Request, exc: Exception) -> Response:
        request.state.session = None
        response = page(request, "error.html", 500, message="Something went wrong. Try again in a moment.")
        response.headers.update(HEADERS)      # the server-error layer sits outside the headers middleware
        return response

    P = lambda h: protected(h, post=True)   # noqa: E731
    routes = [
        Route("/", index),
        Route("/login", login),
        Route("/callback", callback),
        Route("/logout", P(logout), methods=["POST"]),
        Route("/signed-out", signed_out_page),
        Route("/key", protected(key_page)),
        Route("/key", P(key_set), methods=["POST"]),
        Route("/key/revoke", P(key_revoke), methods=["POST"]),
        Route("/credentials", protected(credentials_page)),
        Route("/credentials/{server}/test", P(cred_test), methods=["POST"]),
        Route("/credentials/{server}/{var}/delete", P(cred_delete), methods=["POST"]),
        Route("/credentials/{server}/{var}", P(cred_set), methods=["POST"]),
        Route("/issued", protected(issued_get)),
        Route("/issued", P(issued_create), methods=["POST"]),
        Route("/issued/{key_id}/revoke", P(issued_revoke), methods=["POST"]),
        Route("/activity", protected(activity)),
        Route("/healthz", healthz),
        Route("/static/{name}", static),
    ]
    app = Starlette(routes=routes, exception_handlers={404: not_found_handler, 405: not_found_handler,
                                                       Exception: failure_handler})

    app.add_middleware(_SecurityHeaders)
    return app


class _SecurityHeaders:
    """Adds the fixed response headers to every response, whatever produced it."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        async def with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.decode().title() not in HEADERS]
                headers += [(k.lower().encode(), v.encode()) for k, v in HEADERS.items()]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_headers)
