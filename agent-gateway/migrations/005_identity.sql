-- Identity and access (specs/003-identity-access).
--
-- Ownership moves from free-text user ids to the identity provider's account id (`sub`). Rows
-- written under the old ids cannot be mapped, so they are discarded and people register again
-- (FR-IDN-07). Nothing here preserves them on purpose.
TRUNCATE pending_calls, pending_turns, threads, responses;
DROP TABLE IF EXISTS api_keys;
DROP TABLE IF EXISTS user_credentials;

-- A secret a caller can present, owned by a person.
--   issued   an `agk_` key minted by the gateway (only while GATEWAY_ISSUE_KEYS is on)
--   upstream the person's own key for the model; stored reversibly because the gateway must
--            send it, plus a one-way hash used to recognise it as a bearer
CREATE TABLE api_keys (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner        text NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('issued', 'upstream')),
    label        text NOT NULL DEFAULT '',
    prefix       text NOT NULL,
    key_hash     text NOT NULL,
    secret_enc   bytea,
    scope        jsonb NOT NULL DEFAULT '{"agents": [], "write": true}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,
    revoked_at   timestamptz,
    last_used_at timestamptz,
    CHECK (
        (kind = 'issued' AND secret_enc IS NULL)
        OR (kind = 'upstream' AND expires_at IS NULL
            AND ((revoked_at IS NULL AND secret_enc IS NOT NULL)
              OR (revoked_at IS NOT NULL AND secret_enc IS NULL)))
    )
);
CREATE INDEX api_keys_owner ON api_keys (owner);
CREATE UNIQUE INDEX api_keys_active_hash ON api_keys (key_hash) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX api_keys_one_active_upstream ON api_keys (owner)
    WHERE kind = 'upstream' AND revoked_at IS NULL;

-- A person's own value for one variable of one tool server. Same shape as before; `user_id` now
-- holds the account id.
CREATE TABLE user_credentials (
    user_id     text NOT NULL,
    server      text NOT NULL,
    var         text NOT NULL,
    ciphertext  bytea NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, server, var)
);

-- The gateway's copy of the identity provider's user list (FR-IDN-03).
CREATE TABLE directory_snapshot (
    user_id    text PRIMARY KEY,
    username   text NOT NULL,
    kind       text NOT NULL DEFAULT 'user' CHECK (kind IN ('user', 'service')),
    groups     text[] NOT NULL DEFAULT '{}',
    roles      text[] NOT NULL DEFAULT '{}',
    enabled    boolean NOT NULL,
    synced_at  timestamptz NOT NULL
);
CREATE INDEX directory_snapshot_username ON directory_snapshot (username);

-- What happened (FR-AUD-01). Secret-free by construction (FR-AUD-03).
CREATE TABLE audit_log (
    id        bigserial PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    last_ts   timestamptz NOT NULL DEFAULT now(),
    actor     text NOT NULL,
    action    text NOT NULL,
    target    text NOT NULL DEFAULT '',
    detail    jsonb NOT NULL DEFAULT '{}',
    count     integer NOT NULL DEFAULT 1 CHECK (count >= 1)
);
CREATE INDEX audit_log_ts ON audit_log (ts DESC);
CREATE INDEX audit_log_actor ON audit_log (actor, ts DESC);
CREATE INDEX audit_log_action ON audit_log (action, ts DESC);
