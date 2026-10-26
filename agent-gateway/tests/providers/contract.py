"""The identity-provider contract: what any adapter must do to be a provider.

Subclass ``IdentityProviderContract`` in a ``Test...`` class, implement ``make_world`` and
``make_provider``, and every case below runs against your adapter with no network.
Specified in specs/003-identity-access/contracts/provider-contract-tests.md.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import pytest

from agent_gateway.application.identity_ports import Account, DirectorySource, PortalLogin, TokenVerifier
from agent_gateway.domain.identity import AuthError

SENTINEL_SECRET = "SENTINEL-client-secret-9f3a"
SENTINEL_TOKEN = "SENTINEL-token-7c1d"

ALICE = Account("sub-alice", "alice", True, ("product", "leads"), ("agent-user", "agent-writer"))
DAVE = Account("sub-dave", "dave", False, ("product",), ("agent-user",))
SERVICE = Account("sub-svc", "service-account-automation", True, ("automation",), ("agent-user",), "service")
ROLES_ONLY = Account("sub-roles", "rolesonly", True, (), ("agent-writer",))
DEFAULT_ACCOUNTS = [ALICE, DAVE, SERVICE, ROLES_ONLY]


@dataclass
class ProviderWorld:
    """The provider's state, as a test describes it; a subclass turns it into HTTP and signed tokens."""

    audience: str = "agent-gateway"
    accounts: list[Account] = field(default_factory=lambda: list(DEFAULT_ACCOUNTS))
    offline: bool = False
    break_directory_read: bool = False
    requests: list[tuple[str, str]] = field(default_factory=list)      # (method, path) of every call received

    def mint_token(self, account: Account, *, expired: bool = False, audience: str | None = None,
                   issuer: str | None = None, algorithm: str | None = None, groups=None, roles=None,
                   signing_key: str = "current") -> str:
        raise NotImplementedError

    def approve_login(self, account: Account, authorize_url: str) -> str:
        """What the provider does when a person signs in at ``authorize_url``: returns the code."""
        raise NotImplementedError

    def rotate_signing_key(self) -> None:
        raise NotImplementedError

    def go_offline(self) -> None:
        self.offline = True


@dataclass
class ProviderUnderTest:
    verifier: TokenVerifier
    directory: DirectorySource
    login: PortalLogin
    secrets: tuple[str, ...] = (SENTINEL_SECRET,)       # configured secrets that must never leak


def pkce(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


REDIRECT = "https://gw.test/portal/callback"
VERIFIER = "v" * 64


class IdentityProviderContract:
    def make_world(self) -> ProviderWorld:
        raise NotImplementedError

    def make_provider(self, world: ProviderWorld) -> ProviderUnderTest:
        raise NotImplementedError

    @pytest.fixture
    def world(self) -> ProviderWorld:
        return self.make_world()

    @pytest.fixture
    def provider(self, world) -> ProviderUnderTest:
        return self.make_provider(world)

    async def _refused(self, awaitable, *codes: str) -> AuthError:
        with pytest.raises(AuthError) as err:
            await awaitable
        if codes:
            assert err.value.code in codes
        return err.value

    # ---- tokens

    async def test_a_good_token_gives_the_identity(self, world, provider):
        identity = await provider.verifier.verify(world.mint_token(ALICE))
        assert (identity.sub, identity.username) == (ALICE.sub, ALICE.username)
        assert identity.groups == ALICE.groups and identity.roles == ALICE.roles and identity.kind == "user"
        service = await provider.verifier.verify(world.mint_token(SERVICE))
        assert service.kind == "service" and service.groups == ("automation",)

    async def test_an_expired_token_is_refused(self, world, provider):
        await self._refused(provider.verifier.verify(world.mint_token(ALICE, expired=True)), "expired")

    async def test_a_token_for_another_audience_is_refused(self, world, provider):
        await self._refused(provider.verifier.verify(world.mint_token(ALICE, audience="someone-else")), "bad_audience")

    async def test_a_token_from_another_issuer_is_refused(self, world, provider):
        await self._refused(provider.verifier.verify(world.mint_token(ALICE, issuer="https://evil.test/realms/x")),
                            "bad_issuer")

    async def test_unsigned_and_shared_secret_tokens_are_refused(self, world, provider):
        await self._refused(provider.verifier.verify(world.mint_token(ALICE, algorithm="none")),
                            "bad_algorithm", "bad_signature")
        await self._refused(provider.verifier.verify(world.mint_token(ALICE, algorithm="HS256")),
                            "bad_algorithm", "bad_signature")

    async def test_a_token_signed_by_an_unknown_key_is_refused(self, world, provider):
        await self._refused(provider.verifier.verify(world.mint_token(ALICE, signing_key="stranger")), "bad_signature")

    async def test_garbage_is_refused(self, provider):
        await self._refused(provider.verifier.verify("not-a-token"))
        await self._refused(provider.verifier.verify("a" * 9000))

    async def test_a_rotated_signing_key_is_picked_up(self, world, provider):
        await provider.verifier.verify(world.mint_token(ALICE))
        world.rotate_signing_key()
        assert (await provider.verifier.verify(world.mint_token(ALICE))).sub == ALICE.sub

    async def test_an_outage_keeps_known_keys_working_and_refuses_unknown_ones(self, world, provider):
        await provider.verifier.verify(world.mint_token(ALICE))
        world.rotate_signing_key()
        newer = world.mint_token(ALICE)
        world.go_offline()
        older = world.mint_token(ALICE, signing_key="previous")
        assert (await provider.verifier.verify(older)).sub == ALICE.sub
        await self._refused(provider.verifier.verify(newer), "bad_signature")

    async def test_invalid_group_and_role_names_are_dropped(self, world, provider):
        token = world.mint_token(ALICE, groups=["ok", "a,b", "", "x" * 200, "ok", "tab\tname"],
                                 roles=["agent-user", "bad,role", " "])
        identity = await provider.verifier.verify(token)
        assert identity.groups == ("ok",) and identity.roles == ("agent-user",)

    # ---- the directory

    async def test_fetch_all_returns_every_account_cleaned(self, world, provider):
        accounts = {a.sub: a for a in await provider.directory.fetch_all()}
        assert set(accounts) == {a.sub for a in world.accounts}
        assert accounts[DAVE.sub].enabled is False
        assert accounts[SERVICE.sub].kind == "service" and accounts[ALICE.sub].kind == "user"
        assert accounts[ROLES_ONLY.sub].groups == () and accounts[ROLES_ONLY.sub].roles == ("agent-writer",)
        assert set(accounts[ALICE.sub].groups) == set(ALICE.groups) and set(accounts[ALICE.sub].roles) == set(ALICE.roles)
        assert all(isinstance(a.groups, tuple) and isinstance(a.roles, tuple) for a in accounts.values())

    async def test_fetch_all_is_all_or_nothing(self, world, provider):
        world.break_directory_read = True
        with pytest.raises(Exception):
            await provider.directory.fetch_all()

    async def test_fetch_all_only_reads(self, world, provider):
        await provider.directory.fetch_all()
        writes = [r for r in world.requests if r[0] not in ("GET",) and "/token" not in r[1]]
        assert world.requests and not writes

    # ---- portal login

    def _approved(self, world, provider, account=ALICE, nonce="n-1"):
        url = provider.login.authorize_url("state-1", nonce, pkce(VERIFIER), REDIRECT)
        return url, world.approve_login(account, url)

    async def test_the_login_round_trip(self, world, provider):
        url, code = self._approved(world, provider)
        query = parse_qs(urlparse(url).query)
        assert query["state"] == ["state-1"] and query["nonce"] == ["n-1"]
        assert query["code_challenge"] == [pkce(VERIFIER)] and query["redirect_uri"] == [REDIRECT]
        identity = await provider.login.complete(code, VERIFIER, REDIRECT, "n-1")
        assert (identity.sub, identity.username, identity.groups, identity.roles) == (
            ALICE.sub, ALICE.username, ALICE.groups, ALICE.roles)

    async def test_a_wrong_verifier_nonce_or_reused_code_is_refused(self, world, provider):
        _, code = self._approved(world, provider)
        await self._refused(provider.login.complete(code, "w" * 64, REDIRECT, "n-1"))
        _, code = self._approved(world, provider)
        await self._refused(provider.login.complete(code, VERIFIER, REDIRECT, "other-nonce"))
        _, code = self._approved(world, provider)
        await provider.login.complete(code, VERIFIER, REDIRECT, "n-1")
        await self._refused(provider.login.complete(code, VERIFIER, REDIRECT, "n-1"))

    # ---- secrets

    async def test_no_secret_appears_in_an_error_or_a_log(self, world, provider, caplog):
        caplog.set_level(logging.DEBUG)
        messages: list[str] = []
        for call in (lambda: provider.verifier.verify(SENTINEL_TOKEN),
                     lambda: provider.verifier.verify(world.mint_token(ALICE, expired=True) + SENTINEL_TOKEN),
                     lambda: provider.login.complete(SENTINEL_TOKEN, VERIFIER, REDIRECT, "n")):
            try:
                await call()
            except Exception as exc:
                messages.append(f"{exc!r} {exc}")
        world.break_directory_read = True
        try:
            await provider.directory.fetch_all()
        except Exception as exc:
            messages.append(f"{exc!r} {exc}")
        world.go_offline()
        for call in (provider.directory.fetch_all, lambda: provider.verifier.verify(world.mint_token(ALICE, signing_key="stranger"))):
            try:
                await call()
            except Exception as exc:
                messages.append(f"{exc!r} {exc}")
        text = "\n".join(messages + [r.getMessage() for r in caplog.records])
        for secret in (*provider.secrets, SENTINEL_TOKEN):
            assert secret not in text
