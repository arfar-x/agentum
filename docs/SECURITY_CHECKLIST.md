# Security checklist (identity and access)

What to check before exposing the agent gateway beyond a trusted network, and what to re-check after a
change to authentication, keys, the pass-through or the portal. Each line is a property the code or the
deployment must keep; the requirement ids point at
[`agent-gateway/docs/spec.md`](../agent-gateway/docs/spec.md). Operator steps are in
[`RUNBOOK_IDENTITY.md`](RUNBOOK_IDENTITY.md), the variables in [`CONFIGURATION.md`](CONFIGURATION.md).

## Who is the caller

- [ ] **H1. A model key is a login only when it is unique, and read-only by default.** A person's own model
      key presented as the bearer identifies them only if exactly one person has it registered
      (FR-ATH-14); a string registered by two or more people is a `401 model_key_shared` and identifies
      nobody. `GATEWAY_MODEL_KEY_LOGIN` is `read-only` (no approval-gated write tools, no raw model
      pass-through) unless the operator chose `full`, or `off` to turn the path off (FR-ATH-13). Registering a
      key somebody else holds is accepted and says nothing about it (FR-KEY-03), so there is nothing to probe.
      Know the consequence: when one of two holders revokes, the other's string becomes a valid login.
- [ ] Gateway-issued `agk_` keys exist only while `GATEWAY_ISSUE_KEYS=true`; one key is one person.
- [ ] Keycloak tokens are verified against the issuer's keys (RS256/ES256 only), with `iss`, `aud`, `exp`.
- [ ] No unauthenticated route besides `/healthz` and the portal's sign-in routes (FR-ATH-09).

## Which model key is used

- [ ] There is **no shared model key**: no `UPSTREAM_API_KEY` in `.env.agent-gateway` or compose. A person
      without a registered key gets `403 upstream_key_required` (FR-UPS-02).
- [ ] Stored tool credentials and model keys are read and written by the person's Keycloak account id only,
      never by the key presented (FR-UPS-08, FR-CRD-03). Two people with the same model-key string keep
      separate credentials.
- [ ] A model key never appears in a log, response, error, thread or the model's context (FR-UPS-04).

## The pass-through (`/v1/*` for models that are not agents)

- [ ] **M1.** A path with a `.` or `..` segment (raw, percent-encoded or double-encoded), a backslash or a control
      character is refused with a 404 before anything is sent upstream (FR-RTE-07).
- [ ] **M2.** A key limited to some agents, or to read-only use (issued keys and the read-only model-key login),
      gets `403 permission_denied` on the pass-through (FR-RTE-08).
- [ ] **M3.** Only an allow-list of request headers is forwarded upstream; the caller's `Authorization`, cookies,
      tool-credential headers (`X-Agent-Skills-Env-*`, `X-Env-*`) and `X-KB-*` never are (FR-RTE-09).

## Follow-ups still open

- [ ] **API clients need an identity.** Every API client must hold one of: a Keycloak token, a gateway-issued
      key, or a unique model key, according to `GATEWAY_MODEL_KEY_LOGIN`. Tell clients that cannot sign in
      (LibreChat with a pasted key, scripts) which of the three they use, and that a model key shared with
      colleagues stops identifying anyone.
- [ ] Rate-limit failed authentication before it reaches the database.
- [ ] Do not let a slow identity provider stall every token check.
- [ ] Restrict the base URLs a person may store for tool servers.
- [ ] Compare full group paths, not leaf names.
- [ ] Require a baseline role for every caller, not only per-agent rules.
- [ ] Give `gateway-db` only its own secrets and put a password on `gateway-redis`.
- [ ] Require `https` for the OIDC issuer outside local development.
