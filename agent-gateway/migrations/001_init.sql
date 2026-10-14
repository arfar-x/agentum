CREATE TABLE IF NOT EXISTS api_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       text NOT NULL,
    label         text NOT NULL DEFAULT '',
    key_hash      text NOT NULL UNIQUE,
    created_at    timestamptz NOT NULL DEFAULT now(),
    revoked_at    timestamptz,
    last_used_at  timestamptz
);
CREATE INDEX IF NOT EXISTS api_keys_user ON api_keys (user_id);

CREATE TABLE IF NOT EXISTS user_credentials (
    user_id     text NOT NULL,
    server      text NOT NULL,
    var         text NOT NULL,
    ciphertext  bytea NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, server, var)
);

CREATE TABLE IF NOT EXISTS threads (
    user_id       text NOT NULL,
    history_hash  text NOT NULL,
    active_agent  text NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, history_hash)
);
CREATE INDEX IF NOT EXISTS threads_updated ON threads (updated_at);

CREATE TABLE IF NOT EXISTS pending_turns (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('client_tool', 'approval', 'question')),
    agent_id    text NOT NULL,
    payload     bytea NOT NULL,           -- encrypted JSON: hidden messages, queue, approval call
    code_hash   text,
    expires_at  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pending_user_code ON pending_turns (user_id, code_hash) WHERE code_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS pending_expires ON pending_turns (expires_at);

CREATE TABLE IF NOT EXISTS pending_calls (
    call_id     text NOT NULL,
    pending_id  uuid NOT NULL REFERENCES pending_turns (id) ON DELETE CASCADE,
    PRIMARY KEY (call_id, pending_id)
);
CREATE INDEX IF NOT EXISTS pending_calls_call ON pending_calls (call_id);
