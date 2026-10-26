# Covers: FR-IDP-02, FR-IDP-04, FR-IDP-06, FR-IDN-03, FR-ATH-02, FR-ATH-03
"""The Keycloak adapter against the provider contract, over a fake Keycloak that speaks HTTP in-process."""

from __future__ import annotations

import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from agent_gateway.adapters.outbound.identity import keycloak
from agent_gateway.adapters.outbound.identity.keycloak.settings import KeycloakSettings
from agent_gateway.application.identity_ports import Account
from agent_gateway.domain.errors import ConfigError
from tests.providers.contract import (
    ALICE,
    SENTINEL_SECRET,
    IdentityProviderContract,
    ProviderUnderTest,
    ProviderWorld,
    pkce,
)

ISSUER = "https://kc.test/realms/agentflow"
GATEWAY_UUID = "uuid-agent-gateway"
PORTAL_CLIENT = "agent-gateway-portal"
PORTAL_SECRET = "SENTINEL-portal-secret-2b"


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


_STRANGER = _new_key()


def _jwk(kid: str, key) -> dict:
    data = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**data, "kid": kid, "use": "sig", "alg": "RS256"}


class KeycloakWorld(ProviderWorld):
    """Keycloak, as far as the adapter can tell."""

    def __init__(self, accounts: list[Account] | None = None) -> None:
        super().__init__()
        if accounts is not None:
            self.accounts = accounts
        self.keys = {"kid-0": _new_key()}
        self.current, self.previous = "kid-0", "kid-0"
        self.codes: dict[str, dict] = {}
        self.page = 100

    # ---- ProviderWorld

    def rotate_signing_key(self) -> None:
        self.previous = self.current
        self.current = f"kid-{len(self.keys)}"
        self.keys[self.current] = _new_key()

    def _claims(self, account: Account, audience, issuer, expired, groups, roles) -> dict:
        now = int(time.time())
        return {
            "iss": issuer or ISSUER, "aud": [audience or self.audience, "account"], "sub": account.sub,
            "azp": "telegram-bot", "preferred_username": account.username, "iat": now,
            "exp": now - 3600 if expired else now + 300,
            "groups": list(account.groups if groups is None else groups),
            "realm_access": {"roles": ["offline_access", "realm-only-role"]},
            "resource_access": {self.audience: {"roles": list(account.roles if roles is None else roles)},
                                "other-client": {"roles": ["not-ours"]}},
        }

    def mint_token(self, account, *, expired=False, audience=None, issuer=None, algorithm=None, groups=None,
                   roles=None, signing_key="current") -> str:
        claims = self._claims(account, audience, issuer, expired, groups, roles)
        if algorithm == "none":
            return jwt.encode(claims, None, algorithm="none", headers={"kid": self.current})
        if algorithm == "HS256":
            return jwt.encode(claims, "shared-secret-shared-secret-1234", algorithm="HS256",
                              headers={"kid": self.current})
        if signing_key == "stranger":
            return jwt.encode(claims, _STRANGER, algorithm="RS256", headers={"kid": "kid-stranger"})
        kid = self.previous if signing_key == "previous" else self.current
        return jwt.encode(claims, self.keys[kid], algorithm="RS256", headers={"kid": kid})

    def approve_login(self, account, authorize_url) -> str:
        query = {k: v[0] for k, v in parse_qs(urlparse(authorize_url).query).items()}
        assert query["code_challenge_method"] == "S256" and query["client_id"] == PORTAL_CLIENT
        code = uuid.uuid4().hex
        self.codes[code] = {"account": account, "challenge": query["code_challenge"],
                            "nonce": query["nonce"], "redirect": query["redirect_uri"]}
        return code

    # ---- the HTTP surface

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.offline:
            raise httpx.ConnectError("offline")
        path = request.url.path
        self.requests.append((request.method, path))
        prefix = "/realms/agentflow/protocol/openid-connect"
        if path == f"{prefix}/certs":
            return httpx.Response(200, json={"keys": [_jwk(k, v) for k, v in self.keys.items()]})
        if path == f"{prefix}/token" and request.method == "POST":
            return self._token(parse_qs(request.content.decode()))
        if path.startswith("/admin/realms/agentflow"):
            if request.headers.get("authorization") != "Bearer svc-token":
                return httpx.Response(401)
            if request.method != "GET":
                return httpx.Response(405)
            return self._admin(path.removeprefix("/admin/realms/agentflow"), request.url.params)
        return httpx.Response(404)

    def _token(self, form: dict) -> httpx.Response:
        f = {k: v[0] for k, v in form.items()}
        if f.get("grant_type") == "client_credentials":
            ok = f.get("client_id") == "agent-gateway-directory" and f.get("client_secret") == SENTINEL_SECRET
            return httpx.Response(200, json={"access_token": "svc-token", "expires_in": 300}) if ok \
                else httpx.Response(401, json={"error": "unauthorized_client"})
        if f.get("grant_type") == "authorization_code":
            record = self.codes.pop(f.get("code", ""), None)
            if (record is None or f.get("client_secret") != PORTAL_SECRET or f.get("redirect_uri") != record["redirect"]
                    or pkce(f.get("code_verifier", "")) != record["challenge"]):
                return httpx.Response(400, json={"error": "invalid_grant"})
            acct = record["account"]
            id_claims = {**self._claims(acct, PORTAL_CLIENT, None, False, [], []), "nonce": record["nonce"],
                         "azp": PORTAL_CLIENT}
            id_claims.pop("groups"), id_claims.pop("resource_access")
            return httpx.Response(200, json={
                "id_token": jwt.encode(id_claims, self.keys[self.current], algorithm="RS256",
                                       headers={"kid": self.current}),
                "access_token": self.mint_token(acct)})
        return httpx.Response(400)

    def _groups(self) -> dict[str, dict]:
        """name -> {id, parent}; 'leads' is nested under 'engineering' to prove leaf names are used."""
        names = sorted({g for a in self.accounts for g in a.groups})
        tree = {n: {"id": f"g-{n}", "parent": None} for n in names}
        if "leads" in tree:
            tree["engineering"] = {"id": "g-engineering", "parent": None}
            tree["leads"]["parent"] = "engineering"
        return tree

    def _page(self, rows: list, params) -> httpx.Response:
        first, size = int(params.get("first", 0)), int(params.get("max", 100))
        return httpx.Response(200, json=rows[first:first + min(size, self.page)])

    def _admin(self, path: str, params) -> httpx.Response:
        groups = self._groups()
        if path == "/clients":
            if params.get("clientId"):
                found = [{"id": GATEWAY_UUID, "clientId": self.audience}] if params.get("clientId") == self.audience else []
                return httpx.Response(200, json=found)
            return self._page([{"id": f"client-{a.sub}", "clientId": "automation", "serviceAccountsEnabled": True}
                               for a in self.accounts if a.kind == "service"]
                              + [{"id": "client-plain", "clientId": "plain"}], params)
        if path.endswith("/service-account-user"):
            a = next(a for a in self.accounts if f"client-{a.sub}" == path.split("/")[2])
            return httpx.Response(200, json={"id": a.sub, "username": a.username, "enabled": a.enabled})
        if path == "/users":      # like Keycloak, the user list leaves service accounts out
            return self._page([{"id": a.sub, "username": a.username, "enabled": a.enabled}
                               for a in self.accounts if a.kind != "service"], params)
        if path == "/groups":
            return self._page([{"id": g["id"], "name": n, "subGroupCount": 1 if n == "engineering" else 0}
                               for n, g in groups.items() if g["parent"] is None], params)
        if path.endswith("/children"):
            parent = path.split("/")[2]
            return self._page([{"id": g["id"], "name": n, "subGroupCount": 0} for n, g in groups.items()
                               if g["parent"] and groups[g["parent"]]["id"] == parent], params)
        if path.endswith("/members"):
            gid = path.split("/")[2]
            name = next(n for n, g in groups.items() if g["id"] == gid)
            return self._page([{"id": a.sub, "username": a.username} for a in self.accounts if name in a.groups], params)
        if "/role-mappings/clients/" in path:
            if self.break_directory_read and path.split("/")[2] == self.accounts[-1].sub:
                return httpx.Response(500)
            sub, _, rest = path.removeprefix("/users/").partition("/")
            assert rest == f"role-mappings/clients/{GATEWAY_UUID}/composite"
            account = next(a for a in self.accounts if a.sub == sub)
            return httpx.Response(200, json=[{"name": r} for r in account.roles])
        return httpx.Response(404)


def env() -> dict[str, str]:
    return {"OIDC_ISSUER": ISSUER, "OIDC_AUDIENCE": "agent-gateway", "DIRECTORY_CLIENT_SECRET": SENTINEL_SECRET,
            "PORTAL_BASE_URL": "https://gw.test", "PORTAL_CLIENT_SECRET": PORTAL_SECRET}


def provider_over(world: KeycloakWorld) -> ProviderUnderTest:
    http = httpx.AsyncClient(transport=httpx.MockTransport(world.handler))
    bundle = keycloak.build(env(), http)
    return ProviderUnderTest(bundle.tokens, bundle.directory, bundle.login, (SENTINEL_SECRET, PORTAL_SECRET))


class TestKeycloak(IdentityProviderContract):
    def make_world(self) -> KeycloakWorld:
        return KeycloakWorld()

    def make_provider(self, world: KeycloakWorld) -> ProviderUnderTest:
        return provider_over(world)

    async def test_roles_come_only_from_the_gateway_clients_claim(self, world, provider):
        token = world.mint_token(ALICE)
        identity = await provider.verifier.verify(token)
        assert "not-ours" not in identity.roles and "offline_access" not in identity.roles
        assert "realm-only-role" not in identity.roles

    async def test_the_directory_pages_through_everyone(self):
        many = [Account(f"s{i}", f"user{i}", True, ("product",), ("agent-user",)) for i in range(250)]
        world = KeycloakWorld(many)
        found = await provider_over(world).directory.fetch_all()
        assert len(found) == 250 and all(a.groups == ("product",) and a.roles == ("agent-user",) for a in found)

    async def test_a_nested_group_is_reported_by_its_leaf_name(self, world, provider):
        accounts = {a.sub: a for a in await provider.directory.fetch_all()}
        assert "leads" in accounts[ALICE.sub].groups and "engineering" not in accounts[ALICE.sub].groups

    async def test_a_wrong_directory_secret_fails_the_whole_read(self, world):
        bundle = keycloak.build({**env(), "DIRECTORY_CLIENT_SECRET": "wrong"},
                                httpx.AsyncClient(transport=httpx.MockTransport(world.handler)))
        with pytest.raises(Exception) as err:
            await bundle.directory.fetch_all()
        assert "wrong" not in str(err.value)

    async def test_unknown_key_ids_are_refetched_at_most_once_per_thirty_seconds(self, world, provider):
        await provider.verifier.verify(world.mint_token(ALICE))
        fetches = lambda: sum(1 for _, p in world.requests if p.endswith("/certs"))   # noqa: E731
        before = fetches()
        for _ in range(5):
            with pytest.raises(Exception):
                await provider.verifier.verify(world.mint_token(ALICE, signing_key="stranger"))
        assert fetches() == before + 1


class TestSettings:
    def test_required_settings_are_named(self):
        with pytest.raises(ConfigError, match="OIDC_ISSUER"):
            KeycloakSettings.from_env({"DIRECTORY_CLIENT_SECRET": "x"})
        with pytest.raises(ConfigError, match="DIRECTORY_CLIENT_SECRET"):
            KeycloakSettings.from_env({"OIDC_ISSUER": ISSUER})

    def test_defaults_and_derived_urls(self):
        s = KeycloakSettings.from_env({"OIDC_ISSUER": ISSUER + "/", "DIRECTORY_CLIENT_SECRET": "x"})
        assert (s.audience, s.jwks_ttl, s.directory_client_id) == ("agent-gateway", 600, "agent-gateway-directory")
        assert s.admin_url == "https://kc.test/admin/realms/agentflow" and s.realm == "agentflow"

    def test_a_bad_issuer_or_ttl_is_rejected_without_echoing_secrets(self):
        with pytest.raises(ConfigError, match="OIDC_ISSUER"):
            KeycloakSettings.from_env({"OIDC_ISSUER": "https://kc.test", "DIRECTORY_CLIENT_SECRET": "hush"})
        with pytest.raises(ConfigError, match="OIDC_JWKS_TTL"):
            KeycloakSettings.from_env({**env(), "OIDC_JWKS_TTL": "soon"})

    def test_the_portal_needs_its_client_secret(self):
        with pytest.raises(ConfigError, match="PORTAL_CLIENT_SECRET"):
            KeycloakSettings.from_env({"OIDC_ISSUER": ISSUER, "DIRECTORY_CLIENT_SECRET": "x",
                                       "PORTAL_BASE_URL": "https://gw.test"})
