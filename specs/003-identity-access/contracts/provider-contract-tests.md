# Contract: identity-provider contract tests

A reusable test class that every identity-provider adapter must pass. It is what makes the provider
replaceable: the gateway's core only knows the three ports in [`ports.md`](ports.md) section 3.1, and
this suite is the definition of "behaves like a provider". Requirements: `FR-IDP-*` in
`agent-gateway/docs/spec.md`.

## Shape

`agent-gateway/tests/providers/contract.py` defines:

```python
class IdentityProviderContract:
    """Subclass, implement `make_provider`, and all tests run against your adapter."""

    def make_world(self) -> ProviderWorld: ...
    def make_provider(self, world: ProviderWorld) -> ProviderUnderTest: ...

@dataclass
class ProviderUnderTest:
    verifier: TokenVerifier
    directory: DirectorySource
    login: PortalLogin
    secrets: tuple[str, ...]      # configured secrets that must never appear in an error or log
```

`ProviderWorld` is a small in-process description of the provider's state that the subclass turns
into whatever its adapter talks to (for Keycloak: recorded or faked HTTP responses plus a test RSA
key pair):

```python
@dataclass
class ProviderWorld:
    audience: str
    accounts: list[Account]                 # includes a disabled one, a service one, one with roles only
    def mint_token(self, account, *, expired=False, audience=None, issuer=None, algorithm=None,
                   groups=None, roles=None) -> str: ...
    def approve_login(self, account, authorize_url) -> str: ...   # the code the provider issues for that URL (its PKCE challenge and nonce)
    def rotate_signing_key(self) -> None: ...
    def go_offline(self) -> None: ...
    break_directory_read: bool           # makes part of the directory read fail
```

`tests/providers/test_keycloak_contract.py` is the only subclass today
(`class TestKeycloak(IdentityProviderContract)`); a new provider adds one file like it.

## Cases every adapter must pass

| Case | Expectation | Requirement |
|---|---|---|
| good token | `verify` returns an `Identity` with the account's `sub`, username, groups, roles and kind | FR-IDP-02 |
| expired token | raises `AuthError` with code `expired` | FR-IDP-06 |
| wrong audience | raises `AuthError` with code `bad_audience` | FR-IDP-06 |
| wrong issuer | raises `AuthError` with code `bad_issuer` | FR-IDP-06 |
| unsigned token or a disallowed algorithm | raises `AuthError` (`bad_algorithm` or `bad_signature`) | FR-IDP-06 |
| token signed by an unknown key | raises `AuthError` (`bad_signature`) | FR-IDP-06 |
| key rotation | after `rotate_signing_key`, a token under the new key verifies | FR-IDP-06 |
| provider unreachable | a token under an already-known key still verifies; unknown key raises `AuthError` | FR-IDP-06 |
| invalid group or role names in a token | dropped from the `Identity`, not an error | FR-IDP-02 |
| `fetch_all` shape | returns every account, as `Account`, including the disabled one, the service one (`kind="service"`) and the one holding roles only; `groups` and `roles` are cleaned tuples | FR-IDP-02 |
| `fetch_all` is all or nothing | when part of the read fails it raises; it never returns a partial list | FR-IDP-06 |
| `fetch_all` is read only | no write request is made to the provider (the fake records requests and fails the test on any non-read) | FR-IDP-04 |
| login round trip | `authorize_url(...)` contains the given `state`, `nonce`, `code_challenge` and `redirect_uri`; `complete(code, verifier, ...)` returns the approved account's `Identity` | FR-IDP-06 |
| login with a wrong verifier, wrong nonce or a reused code | raises `AuthError`; nothing is returned | FR-IDP-06 |
| no secret in errors | a sentinel client secret and sentinel token never appear in any exception message or log record | FR-IDP-06 |

## Rules

- The suite uses no network and no containers. A provider adapter that needs a live service for these
  cases is out of contract; live checks belong to the end-to-end recipe.
- The suite lives in the gateway repository and may import the ports, never an adapter's internals.
- Adding a case is a change to this file and to FR-IDP-06 if it changes what "passes" means.
