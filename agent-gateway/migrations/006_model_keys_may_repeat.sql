-- A person's own model key may be registered by several people (colleagues sharing one provider
-- key). Only a gateway-issued key must stay unique among active keys, because the presented
-- string is what identifies its owner. One active model key per person is unchanged
-- (api_keys_one_active_upstream).
DROP INDEX IF EXISTS api_keys_active_hash;
CREATE UNIQUE INDEX api_keys_active_hash ON api_keys (key_hash)
    WHERE revoked_at IS NULL AND kind = 'issued';
-- Lookup by hash for every kind (the unique index above only covers issued keys).
CREATE INDEX api_keys_active_lookup ON api_keys (key_hash) WHERE revoked_at IS NULL;
