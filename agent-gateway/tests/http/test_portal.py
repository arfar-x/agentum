# Covers: FR-PRT-01, FR-PRT-02, FR-PRT-03, FR-PRT-04, FR-PRT-05, FR-PRT-06, FR-PRT-07, FR-PRT-08
# Covers: FR-PRT-09, FR-PRT-10, FR-PRT-11, FR-PRT-12, FR-CRD-08
"""The portal, driven over real HTTP (ASGI, no network) against in-memory fakes."""

from __future__ import annotations

import re
from contextlib import aclosing as closing
from dataclasses import dataclass, replace
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from agent_gateway.adapters.inbound.openai_http.app import create_app
from agent_gateway.adapters.inbound.portal import PortalDeps
from agent_gateway.adapters.inbound.portal.session import SESSION_COOKIE, SessionCodec
from agent_gateway.application.identity_ports import Identity, NewKey
from agent_gateway.application.key_service import KeyManager
from agent_gateway.application.models import CredentialDecl, ProbeTool, ServerInfo, ToolResult
from agent_gateway.domain.identity import DirectoryEntry
from tests.application.fakes import (
    FakeAudit,
    FakeCreds,
    FakeDirectory,
    FakeKeyRecords,
    FakeNow,
    FakePortalLogin,
    FakeSecretBox,
)

BASE = "https://gw.test"
SECRET = "s" * 40
SENTINEL = "SENTINEL-do-not-leak-0042"
SUB = "sub-1"


class Tools:
    def __init__(self) -> None:
        self.calls = []
        self.fail: str | None = None

    async def list_tools(self, server):
        return []

    async def call(self, server, tool, arguments, credentials):
        self.calls.append((server, tool, arguments, dict(credentials)))
        if self.fail:
            return ToolResult(self.fail.replace("PW", credentials.get("PASSWORD", "")), is_error=True)
        return ToolResult("fine")


@dataclass
class Rig:
    app: FastAPI
    deps: PortalDeps
    now: FakeNow
    audit: FakeAudit
    creds: FakeCreds
    directory: FakeDirectory
    tools: Tools
    records: FakeKeyRecords
    login: FakePortalLogin

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=BASE)


def make_rig(*, issue_keys: bool = False, base: str = BASE) -> Rig:
    now = FakeNow()
    audit = FakeAudit(now)
    records = FakeKeyRecords(audit, now)
    directory = FakeDirectory()
    directory.rows[SUB] = DirectoryEntry(SUB, "alice", ("eng",), (), True, now())
    creds = FakeCreds(audit=audit)
    tools = Tools()
    servers = {
        "jira": ServerInfo("jira", ("URL", "PASSWORD"), declarations=(
            CredentialDecl("URL", "Jira address", secret=False, required=True),
            CredentialDecl("PASSWORD", "Jira password", secret=True, required=True)),
            test=ProbeTool("jira_my_work", {})),
        "plain": ServerInfo("plain", ("TOKEN",)),
        "none": ServerInfo("none"),
    }
    login = FakePortalLogin({"code-alice": Identity(SUB, "alice", (), ()),
                             "code-gone": Identity("sub-gone", "gone", (), ())})
    keys = KeyManager(records, FakeSecretBox(), issue_keys=issue_keys, now=now)
    deps = PortalDeps(base_url=base, session_secret=SECRET, login=login, directory=directory, keys=keys,
                      audit=audit, creds=creds, tools=tools, servers=servers, issue_keys=issue_keys,
                      agent_ids=lambda entry: ["alpha", "beta"], now=now)

    async def close():
        return None

    app = create_app(SimpleNamespace(portal=deps, close=close))
    return Rig(app, deps, now, audit, creds, directory, tools, records, login)


@pytest.fixture
def rig() -> Rig:
    return make_rig()


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def post_headers(origin: str = BASE) -> dict[str, str]:
    return {"origin": origin, "content-type": "application/x-www-form-urlencoded"}


async def sign_in(c: httpx.AsyncClient, code: str = "code-alice", next_path: str | None = None):
    r = await c.get("/portal/login" + (f"?next={next_path}" if next_path else ""))
    assert r.status_code == 302
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return await c.get(f"/portal/callback?code={code}&state={state}")


async def signed_in(rig: Rig) -> tuple[httpx.AsyncClient, str]:
    c = rig.client()
    r = await sign_in(c)
    assert r.status_code == 303
    page = await c.get("/portal/key")
    return c, csrf_of(page.text)


async def test_the_portal_is_absent_unless_configured():
    app = create_app(SimpleNamespace(portal=None, close=lambda: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        assert (await c.get("/portal/login")).status_code == 404


async def test_signed_out_requests_are_sent_to_login_with_a_local_next(rig):
    async with rig.client() as c:
        r = await c.get("/portal/credentials")
        assert r.status_code == 303 and r.headers["location"] == "/portal/login?next=%2Fportal%2Fcredentials"
        assert (await c.post("/portal/key", headers=post_headers(), content="x=1")).status_code == 303
        assert (await c.get("/portal/healthz")).text == "ok"
        assert (await c.get("/portal/static/portal.css")).status_code == 200
        assert (await c.get("/portal/static/other.css")).status_code == 404


async def test_login_uses_state_nonce_and_pkce_and_the_callback_opens_a_session(rig):
    async with rig.client() as c:
        r = await c.get("/portal/login?next=/portal/credentials")
        q = parse_qs(urlsplit(r.headers["location"]).query)
        assert q["state"] and q["nonce"] and q["cc"] and q["redirect_uri"] == [f"{BASE}/portal/callback"]
        cookie = r.headers["set-cookie"].lower()
        assert "portal_login=" in cookie and "httponly" in cookie and "path=/portal" in cookie and "secure" in cookie
        done = await c.get(f"/portal/callback?code=code-alice&state={q['state'][0]}")
        assert done.status_code == 303 and done.headers["location"] == "/portal/credentials"
        raw = [h for h in done.headers.get_list("set-cookie") if h.startswith("portal_session=")][0].lower()
        assert "httponly" in raw and "samesite=lax" in raw and "path=/portal" in raw and "secure" in raw
        assert (await c.get("/portal/key")).status_code == 200


async def test_callback_refuses_wrong_state_missing_cookie_and_open_redirects(rig):
    async with rig.client() as c:
        await c.get("/portal/login")
        assert (await c.get("/portal/callback?code=code-alice&state=forged")).status_code == 400
    async with rig.client() as c:
        assert (await c.get("/portal/callback?code=code-alice&state=x")).status_code == 400
    async with rig.client() as c:
        r = await sign_in(c, next_path="//evil.example")
        assert r.headers["location"] == "/portal"
    async with rig.client() as c:
        r = await sign_in(c, code="bad-code")
        assert r.status_code == 403


async def test_a_person_missing_or_disabled_in_the_directory_cannot_sign_in(rig):
    async with rig.client() as c:
        assert (await sign_in(c, "code-gone")).status_code == 403
    rig.directory.rows[SUB] = replace(rig.directory.rows[SUB], enabled=False)
    async with rig.client() as c:
        assert (await sign_in(c)).status_code == 403


async def test_a_disabled_or_stale_person_is_signed_out_on_the_next_request(rig):
    c, _ = await signed_in(rig)
    async with closing(c):
        rig.directory.rows[SUB] = replace(rig.directory.rows[SUB], enabled=False)
        assert (await c.get("/portal/key")).status_code == 303
    rig.directory.rows[SUB] = replace(rig.directory.rows[SUB], enabled=True)
    c, _ = await signed_in(rig)
    async with closing(c):
        rig.now.advance(minutes=20)           # the directory row is older than the freshness limit
        assert (await c.get("/portal/key")).status_code == 303


async def test_session_has_an_idle_limit_an_absolute_limit_and_sliding_reissue(rig):
    c, _ = await signed_in(rig)
    async with closing(c):
        rig.now.advance(minutes=6)
        rig.directory.rows[SUB] = replace(rig.directory.rows[SUB], synced_at=rig.now())
        r = await c.get("/portal/key")
        assert r.status_code == 200 and SESSION_COOKIE in r.headers.get("set-cookie", "")   # reissued
        rig.now.advance(minutes=31)
        rig.directory.rows[SUB] = replace(rig.directory.rows[SUB], synced_at=rig.now())
        assert (await c.get("/portal/key")).status_code == 303                                # idle
    codec = SessionCodec(SECRET)
    cookie = codec.issue(SUB, "alice", rig.now())
    assert codec.read(cookie, rig.now()) is not None
    assert codec.read(cookie, rig.now() + timedelta(hours=8)) is None
    assert codec.read(cookie[:-3] + "AAA", rig.now()) is None
    assert SessionCodec("another-secret-another-secret-0000").read(cookie, rig.now()) is None


async def test_state_changes_need_the_csrf_token_and_the_origin(rig):
    c, token = await signed_in(rig)
    async with closing(c):
        body = f"label=x&secret=sk-abcdefgh1234&csrf={token}"
        assert (await c.post("/portal/key", headers=post_headers("https://evil.example"), content=body)).status_code == 403
        assert (await c.post("/portal/key", headers={"content-type": "application/x-www-form-urlencoded"},
                             content=body)).status_code == 403            # no Origin, no Referer
        assert (await c.post("/portal/key", headers=post_headers(), content="label=x&secret=sk-abcdefgh1234&csrf=nope")
                ).status_code == 403
        assert (await c.post("/portal/key", headers=post_headers(), content="label=x&secret=sk-abcdefgh1234")).status_code == 403
        assert await rig.records.list_for(SUB) == []                    # nothing happened
        ok = await c.post("/portal/key", headers={"referer": BASE + "/portal/key",
                                                  "content-type": "application/x-www-form-urlencoded"}, content=body)
        assert ok.status_code == 303


async def test_the_model_key_can_be_registered_replaced_and_revoked_and_is_never_shown(rig):
    c, token = await signed_in(rig)
    async with closing(c):
        def form(secret, label=""):
            return f"csrf={token}&label={label}&secret={secret}"

        r = await c.post("/portal/key", headers=post_headers(), content=form("sk-" + SENTINEL, "mine"))
        assert r.status_code == 303
        page = await c.get("/portal/key?m=key_saved")
        assert "ends in ...0042" in page.text and "mine" in page.text and SENTINEL not in page.text
        assert "Your model key is registered" in page.text
        r = await c.post("/portal/key", headers=post_headers(), content=form("sk-second-key-9999"))
        assert r.status_code == 303
        assert len([k for k in await rig.records.list_for(SUB) if k.revoked_at is None]) == 1
        await rig.deps.keys.register_upstream("someone-else", "", "sk-taken-by-another-1")
        dup = await c.post("/portal/key", headers=post_headers(), content=form("sk-taken-by-another-1"))
        assert dup.status_code == 409 and "someone" not in dup.text and "sk-taken" not in dup.text
        assert (await c.post("/portal/key", headers=post_headers(), content=form("short"))).status_code == 400
        assert (await c.post("/portal/key", headers=post_headers(), content=form("has space1234"))).status_code == 400
        assert (await c.post("/portal/key/revoke", headers=post_headers(), content=f"csrf={token}")).status_code == 303
        assert (await c.post("/portal/key/revoke", headers=post_headers(), content=f"csrf={token}")).status_code == 409
        actions = [e.action for e in rig.audit.events if e.actor == SUB]
        assert "key.register" in actions and "key.revoke" in actions


async def test_credentials_are_listed_by_declaration_and_only_declared_pairs_are_stored(rig):
    c, token = await signed_in(rig)
    async with closing(c):
        page = await c.get("/portal/credentials")
        assert "Jira address" in page.text and "Jira password" in page.text and "Model key: not set" in page.text
        assert "none</h2>" not in page.text and "TOKEN" in page.text       # plain-name server shows its name
        base = {"headers": post_headers()}
        assert (await c.post("/portal/credentials/jira/PASSWORD", content=f"csrf={token}&value={SENTINEL}", **base)).status_code == 303
        assert (await c.post("/portal/credentials/jira/URL", content=f"csrf={token}&value=https%3A%2F%2Fj.example", **base)).status_code == 303
        assert rig.creds.data[(SUB, "jira")]["PASSWORD"] == SENTINEL
        page = await c.get("/portal/credentials")
        assert SENTINEL not in page.text and "https://j.example" in page.text      # secret hidden, plain value shown
        assert page.text.count(">set</span>") == 2
        for path in ("/portal/credentials/jira/OTHER", "/portal/credentials/nope/X", "/portal/credentials/none/X"):
            assert (await c.post(path, content=f"csrf={token}&value=v", **base)).status_code == 404
        assert "OTHER" not in rig.creds.data.get((SUB, "jira"), {})
        assert (await c.post("/portal/credentials/jira/PASSWORD", content=f"csrf={token}&value=a%0Ab", **base)).status_code == 400
        assert (await c.post("/portal/credentials/jira/PASSWORD", content=f"csrf={token}&value=", **base)).status_code == 400
        gone = await c.post("/portal/credentials/jira/PASSWORD/delete", content=f"csrf={token}", **base)
        assert gone.status_code == 303 and "PASSWORD" not in rig.creds.data[(SUB, "jira")]
        assert "URL" in rig.creds.data[(SUB, "jira")]
        actions = [(e.action, e.target) for e in rig.audit.events]
        assert ("cred.set", "jira/PASSWORD") in actions and ("cred.delete", "jira/PASSWORD") in actions
        assert SENTINEL not in repr(rig.audit.events)


async def test_the_test_button_uses_the_persons_credentials_and_masks_errors(rig):
    c, token = await signed_in(rig)
    async with closing(c):
        base = {"headers": post_headers()}
        r = await c.post("/portal/credentials/jira/test", content=f"csrf={token}", **base)
        assert r.status_code == 200 and "Set URL, PASSWORD first" in r.text and rig.tools.calls == []
        await c.post("/portal/credentials/jira/PASSWORD", content=f"csrf={token}&value={SENTINEL}", **base)
        await c.post("/portal/credentials/jira/URL", content=f"csrf={token}&value=https%3A%2F%2Fj.example", **base)
        r = await c.post("/portal/credentials/jira/test", content=f"csrf={token}", **base)
        assert "The credentials work" in r.text
        assert rig.tools.calls[-1][:2] == ("jira", "jira_my_work") and rig.tools.calls[-1][3]["PASSWORD"] == SENTINEL
        rig.tools.fail = "401 for PW"
        r = await c.post("/portal/credentials/jira/test", content=f"csrf={token}", **base)
        assert "401 for ***" in r.text and SENTINEL not in r.text
        assert (await c.post("/portal/credentials/plain/test", content=f"csrf={token}", **base)).status_code == 404
        assert [e for e in rig.audit.events if e.action == "cred.test" and e.target == "jira"]


async def test_the_issued_keys_page_exists_only_when_the_flag_is_on(rig):
    c, _ = await signed_in(rig)
    async with closing(c):
        assert (await c.get("/portal/issued")).status_code == 404
        assert "Issued keys" not in (await c.get("/portal/key")).text
    on = make_rig(issue_keys=True)
    c, token = await signed_in(on)
    async with closing(c):
        assert "Issued keys" in (await c.get("/portal/key")).text
        r = await c.post("/portal/issued", headers=post_headers(),
                         content=f"csrf={token}&label=ci&expires_in_days=30&agents=alpha&write=1")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        secret = re.search(r"<code class=\"secret\">(agk_[^<]+)</code>", r.text).group(1)
        listing = await c.get("/portal/issued")
        assert secret not in listing.text and secret[:8] in listing.text            # shown once only
        info = (await on.records.list_for(SUB))[0]
        assert info.scope.agents == ("alpha",) and info.scope.write and info.kind == "issued"
        for bad in ("expires_in_days=0", "expires_in_days=366", "expires_in_days=abc", "agents=zzz"):
            body = f"csrf={token}&label=x&{bad}"
            assert (await c.post("/portal/issued", headers=post_headers(), content=body)).status_code == 400
        other = await on.records.add_key(NewKey("someone-else", "issued", "", "agk_zzzz", "h", None, info.scope, None),
                                         actor="x")
        assert (await c.post(f"/portal/issued/{other[0].id}/revoke", headers=post_headers(),
                             content=f"csrf={token}")).status_code == 404
        assert (await c.post(f"/portal/issued/{info.id}/revoke", headers=post_headers(),
                             content=f"csrf={token}")).status_code == 303
        assert [e for e in on.audit.events if e.action == "key.issue" and e.actor == SUB]


async def test_every_response_carries_the_security_headers_and_activity_is_own_only(rig):
    c, token = await signed_in(rig)
    async with closing(c):
        await c.post("/portal/key", headers=post_headers(), content=f"csrf={token}&secret=sk-abcdefgh1234")
        rig.audit.write("someone-else", "key.register", "k-other")
        for path in ("/portal/key", "/portal/credentials", "/portal/activity", "/portal/nothing", "/portal/healthz"):
            r = await c.get(path)
            assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"
            assert r.headers["referrer-policy"] == "same-origin" and r.headers["x-content-type-options"] == "nosniff"
            csp = r.headers["content-security-policy"]
            assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        page = (await c.get("/portal/activity")).text
        assert "key.register" in page and "k-other" not in page
        assert "<script" not in (await c.get("/portal/key")).text


async def test_values_are_escaped_and_secret_inputs_are_never_prefilled(rig):
    c, token = await signed_in(rig)
    async with closing(c):
        await c.post("/portal/key", headers=post_headers(), content=f"csrf={token}&label=%3Cb%3Ex%3C%2Fb%3E&secret=sk-abcdefgh1234")
        page = (await c.get("/portal/key")).text
        assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page
        creds = (await c.get("/portal/credentials")).text
        for m in re.finditer(r'<input[^>]*type="password"[^>]*>', creds):
            assert 'autocomplete="off"' in m.group(0) and "value=" not in m.group(0)


async def test_logout_ends_the_session(rig):
    c, token = await signed_in(rig)
    async with closing(c):
        r = await c.post("/portal/logout", headers=post_headers(), content=f"csrf={token}")
        assert r.status_code == 303 and r.headers["location"] == "/portal/signed-out"
        assert (await c.get("/portal/key")).status_code == 303


async def test_the_portal_does_not_accept_the_api_bearer(rig):
    async with rig.client() as c:
        r = await c.get("/portal/key", headers={"authorization": "Bearer agk_whatever"})
        assert r.status_code == 303


async def test_http_localhost_base_url_drops_only_the_secure_flag():
    r = make_rig(base="http://localhost:8080")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=r.app), base_url="http://localhost:8080") as c:
        resp = await c.get("/portal/login")
        assert "secure" not in resp.headers["set-cookie"].lower() and "httponly" in resp.headers["set-cookie"].lower()


async def test_the_directory_is_refreshed_once_for_an_unknown_person(rig):
    calls = []

    class Sync:
        async def refresh(self):
            pass

        async def refresh_if_due(self, gap):
            calls.append(gap)
            rig.directory.rows["sub-gone"] = DirectoryEntry("sub-gone", "gone", (), (), True, rig.now())

    rig.deps.sync = Sync()
    async with rig.client() as c:
        assert (await sign_in(c, "code-gone")).status_code == 303
    assert len(calls) == 1
